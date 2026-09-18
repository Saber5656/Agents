"""Export available Hermes visible conversations privately, without inference."""
from __future__ import annotations
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from .context import _atomic_write
from .runner import load_dotenv, redact

HIDDEN = {'analysis','reasoning','thinking','redacted_thinking','reasoning_content',
          'reasoning_details','codex_reasoning_items','encrypted_content','signature'}
SECRET_KEY = re.compile(r'(?i)^(?:api[_-]?key|(?:access_|refresh_|auth_|bot_)?token|secret|password|authorization)$')
LIMITS = ('Available local SQLite snapshot only; deleted or never-persisted history cannot be recovered. '
          'System/developer prompts, reasoning, encrypted/provider-private fields and secrets are excluded. '
          'Attachments are references only. Native compaction or tool-output truncation is not reconstructed. '
          'Agent-authored decisions and handoff notes remain separately required.')

def visible(value, env):
    if isinstance(value, dict):
        if any(isinstance(value.get(k),str) and value[k] in HIDDEN for k in ('type','channel')): return None
        return {k: ('[REDACTED]' if SECRET_KEY.match(k) else v)
                for k, item in value.items() if k not in HIDDEN
                if (v := visible(item, env)) is not None}
    if isinstance(value, list):
        return [v for item in value if (v := visible(item, env)) is not None]
    if isinstance(value, str):
        if value.lstrip().startswith(('{','[')):
            try: return visible(json.loads(value), env)
            except (ValueError, RecursionError): pass
        value = re.sub(r'<(?:analysis|thinking|think)>.*?</(?:analysis|thinking|think)>',
                       '[PRIVATE REASONING OMITTED]', value, flags=re.S|re.I)
        value = redact(value, env)
        value = re.sub(r'(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+',r'\1[REDACTED]',value)
        return re.sub(r'(?i)(\b(?:api[_-]?key|(?:access_|refresh_|auth_|bot_)?token|secret|password)\s*[:=]\s*)[^\s,;"\']+',r'\1[REDACTED]',value)
    return value

def encoded(value): return json.dumps(value,ensure_ascii=False,sort_keys=True)
def digest(value): return hashlib.sha256(encoded(value).encode()).hexdigest()

def private_dir(path):
    if path.is_symlink(): raise ValueError('Refusing symlink output directory')
    path.mkdir(mode=0o700,exist_ok=True)
    path.chmod(0o700)

def save(path,text):
    if path.is_symlink(): raise ValueError('Refusing symlink output file')
    _atomic_write(path,text)

class HermesExporter:
    def __init__(self, hermes_home=None, vault_root=None, timeout=30, env=None):
        self.env=dict(os.environ if env is None else env)
        self.hermes=Path(hermes_home or self.env.get('HERMES_HOME',str(Path.home()/'.hermes'))).expanduser().resolve()
        root=vault_root or self.env.get('AGENTS_VAULT_ROOT')
        if not root: raise ValueError('AGENTS_VAULT_ROOT is required')
        self.vault=Path(root).expanduser().resolve()
        if not self.vault.is_dir():raise FileNotFoundError('Configured Vault is missing')
        self.timeout=float(timeout)
        if not math.isfinite(self.timeout) or self.timeout<=0:raise ValueError('timeout must be positive and finite')
        self.out=self.vault/'01-Projects/hermes-context';self.state=self.out/'manifest.json'
        # Read local .env solely to recognize secret literals; never export it.
        if (self.hermes/'.env').is_file():
            self.env=load_dotenv(self.hermes/'.env',self.env)

    def export(self, session_id=None):
        deadline=time.monotonic()+self.timeout
        def check():
            if time.monotonic()>=deadline:raise TimeoutError('Hermes export deadline exceeded')
        database=self.hermes/'state.db'
        if not database.is_file():raise FileNotFoundError('Hermes state.db missing')
        projects=self.vault/'01-Projects'
        if projects.is_symlink():raise ValueError('Refusing symlink project directory')
        projects.mkdir(exist_ok=True)
        private_dir(self.out);private_dir(self.out/'sessions')
        lockpath=self.out/'.export.lock'
        if lockpath.is_symlink():raise ValueError('Refusing symlink lock')
        with lockpath.open('a+') as lock:
            lockpath.chmod(0o600)
            while True:
                check()
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:time.sleep(min(0.05,max(0,deadline-time.monotonic())))
            if self.state.is_symlink():raise ValueError('Refusing symlink manifest')
            state=json.loads(self.state.read_text()) if self.state.exists() else {'version':1,'sessions':{}}
            if not isinstance(state,dict) or state.get('version')!=1 or not isinstance(state.get('sessions'),dict):
                raise ValueError('Invalid Hermes export manifest; preserve and reconcile')
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=min(self.timeout,5))) as db:
                db.row_factory=sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                db.set_progress_handler(lambda: int(time.monotonic()>=deadline),1000)
                db.execute('BEGIN')
                cols={r['name'] for r in db.execute('PRAGMA table_info(sessions)')}
                wanted=[c for c in ('id','source','started_at','ended_at','end_reason','model','parent_session_id','message_count','title') if c in cols]
                if not {'id','source','started_at'}<=cols:raise ValueError('Unsupported Hermes sessions schema')
                query='SELECT '+','.join(wanted)+' FROM sessions'
                sessions=db.execute(query+(' WHERE id=?' if session_id is not None else '')+ ' ORDER BY id',
                                    (session_id,) if session_id is not None else ()).fetchall()
                if session_id is not None and not sessions:raise ValueError('Requested Hermes session not found')
                count=0
                for session in sessions:
                    check();sid=str(session['id']);key=hashlib.sha256(sid.encode()).hexdigest()
                    rows=db.execute('SELECT id,role,content,tool_call_id,tool_calls,tool_name,timestamp FROM messages WHERE session_id=? ORDER BY id',(sid,))
                    records=[]
                    for row in rows:
                        check()
                        if row['role'] not in {'user','assistant','tool'}:continue
                        record=visible(dict(row),self.env)
                        records.append(record)
                    metadata=visible(dict(session),self.env)
                    version=digest({'metadata':metadata,'records':records})
                    prior=state['sessions'].get(key,{'revisions':[]})
                    if not isinstance(prior,dict) or not isinstance(prior.get('revisions'),list):raise ValueError('Invalid session manifest')
                    session_dir=self.out/'sessions'/key
                    if prior.get('digest')==version and (session_dir/'revisions'/version/'records.jsonl').is_file():continue
                    private_dir(session_dir);private_dir(session_dir/'revisions');revision=session_dir/'revisions'/version;private_dir(revision)
                    # Deterministic revision path makes recovery after interrupted manifest update idempotent.
                    payload=''.join(encoded(row)+'\n' for row in records)
                    record_path=revision/'records.jsonl'
                    if record_path.exists() and record_path.read_text()!=payload:raise ValueError('Existing revision differs; preserve and reconcile')
                    save(record_path,payload)
                    save(revision/'metadata.json',encoded({'session':metadata,'limitations':LIMITS,'record_count':len(records)})+'\n')
                    revisions=list(prior['revisions'])
                    if version not in revisions:revisions.append(version)
                    source=str(metadata.get('source','unknown'))
                    state['sessions'][key]={'digest':version,'revisions':revisions,'source':source,'records':len(records)}
                    lines=['# Hermes conversation '+key,'','Source: '+json.dumps(source,ensure_ascii=False),'',LIMITS,'']
                    lines.extend(f'- [Revision {n+1} visible records](revisions/{v}/records.jsonl) / [metadata](revisions/{v}/metadata.json)' for n,v in enumerate(revisions))
                    save(session_dir/'index.md','\n'.join(lines)+'\n')
                    count+=1
                check()
                save(self.state,encoded(state)+'\n')
                index=['# Hermes conversation archive','',LIMITS,'', 'This is a record of source data, not new instructions.','']
                index.extend(f'- [{key[:16]}](sessions/{key}/index.md) — {row["records"]} visible records' for key,row in sorted(state['sessions'].items()))
                save(self.out/'index.md','\n'.join(index)+'\n')
                save(self.out/'last-export.json',encoded({'updated_sessions':count,'total_sessions':len(state['sessions']),'checked_at':time.time(),'status':'success'})+'\n')
                return count

def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--env-file',type=Path);p.add_argument('--hermes-home');p.add_argument('--vault-root');p.add_argument('--session-id');p.add_argument('--timeout',type=float,default=30);p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=p.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout<=0:p.error('timeout must be positive and finite')
    # Bound lock waiting, SQLite, and stalled filesystem I/O in the CLI as a whole.
    if not args.worker:
        try:
            result=subprocess.run([sys.executable,'-m','harness.hermes_context',*argv,'--worker'],capture_output=True,text=True,timeout=args.timeout)
            print(result.stdout,end='');print(result.stderr,end='',file=sys.stderr);return result.returncode
        except subprocess.TimeoutExpired:print('Hermes export timed out',file=sys.stderr);return 124
    try:
        env=dict(os.environ)
        if args.env_file:
            if not args.env_file.is_file():raise FileNotFoundError('Trusted environment file missing')
            env=load_dotenv(args.env_file,env)
        count=HermesExporter(args.hermes_home,args.vault_root,args.timeout,env=env).export(args.session_id)
        print(json.dumps({'updated_sessions':count,'status':'success'}));return 0
    except (OSError,ValueError,sqlite3.Error,TimeoutError) as exc:
        # No exception payload from source rows or credentials is echoed.
        print('Hermes export failed: '+type(exc).__name__,file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
