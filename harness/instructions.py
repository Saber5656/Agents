"""Connect native agent instruction entrypoints to one local Agents policy.

No provider settings, credentials, permissions or model selections are changed.
Explicit --agents selection lets a new installation be provisioned intentionally.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
from .context import _atomic_write
from .runner import load_dotenv

BEGIN = '<!-- agents:begin -->'
END = '<!-- agents:end -->'

class InstructionError(RuntimeError):
    pass

def bootstrap():
    return '''## Agents shared workflow

Communicate in clear English, even when the user writes in Japanese. Apply `$AGENTS_ROOT/policies/communication.md` to progress, questions, reports, handoffs and new titles. Preserve explicitly requested artifact languages and original evidence; do not add bilingual text unless requested. The native agent receiving the request is the lead and owns the goal, decisions and results. Use this app's own models and capabilities; Devin, Hermes and Cursor do not need to start Codex.

At startup and resume, read the trusted local `~/.config/agents/environment.env` to resolve `AGENTS_ROOT`, `SKILLS_ROOT` and `AGENTS_VAULT_ROOT`. This points to the canonical `.env`. Do not display values or secrets. In a shell, `set -a; . "$HOME/.config/agents/environment.env"; set +a` explicitly passes them to that process.

Read `$AGENTS_ROOT/COMMON-AGENTS.md`, `$SKILLS_ROOT/CURRENT-WORKFLOW.md` and the communication policy in full, then apply the target project's instructions and relevant policies/skills. Retired Saihai intake, fixed roles and approval gates remain retired. If shared files are unavailable, report what is missing rather than guessing replacements.

When documentation and existing code disagree, follow established code conventions and report each inconsistency in the final reply. Check relevant code, tests and observed behavior; this concerns descriptive technical guidance, not permission to ignore explicit requirements, security rules, invariants or requested migrations. Existing code can have defects. Record evidence and disposition, fix task-relevant stale documentation and register unrelated mismatches locally.

Before work, consult related records in the existing Vault. Save original requests, additions, references, alternatives, decision reasons, execution, validation, failures/recovery, changes and unresolved items progressively in `AGENTS_VAULT_ROOT`. Preserve full available user/assistant/tool visible records with an index. Exclude secrets and private analysis/thinking, and state unavailable or truncated history. Raw logs do not replace decision and handoff records. Link the record and actual verification state in the final reply.

Complete agreed implementation, repairs, validation, review and commits autonomously. Normally delegate independent work and a separate review execution through the existing `python3 -m harness` to Claude sonnet/low. Only actual quota exhaustion triggers the automatic Codex Luna/low fallback. Keep the native lead and app capabilities. Record unavailable delegation paths and continue independent work. Preserve existing work and settings; handle extra charges, unauthorized publication, destructive operations and user authentication separately.

These are model instructions, not OS enforcement. Verify shared files and Vault access separately in remote environments; local configuration is not automatically transferred.'''


def merge_block(original, body):
    block=BEGIN+'\n'+body.strip()+'\n'+END
    if BEGIN in original or END in original:
        if original.count(BEGIN)!=1 or original.count(END)!=1 or original.index(BEGIN)>original.index(END):
            raise InstructionError('Invalid or duplicate Agents managed block; preserve file and reconcile')
        start=original.index(BEGIN);end=original.index(END)+len(END)
        return original[:start]+block+original[end:]
    return original+ ('\n\n' if original else '')+block+'\n'

def targets(home, agents):
    paths=[]
    mapping={'codex':[('.codex/AGENTS.md',None)],'claude':[('.claude/CLAUDE.md',None)],
             'devin':[('.config/devin/AGENTS.md',None),('.codeium/windsurf/memories/global_rules.md',6000)],
             'hermes':[('.hermes/SOUL.md',20000)],'cursor':[('.cursor/rules/agents.mdc',None)]}
    for agent in agents:
        if agent not in mapping:raise InstructionError('Unknown agent: '+agent)
        for relative,limit in mapping[agent]:paths.append((agent,home/relative,limit))
    return paths

def configure(env_file, home, agents, *, apply=False):
    env_file=Path(env_file).resolve();home=Path(home).expanduser().resolve()
    if not env_file.is_file():raise InstructionError('Trusted environment file missing')
    env=load_dotenv(env_file,dict(os.environ,HOME=str(home)))
    for key in ('AGENTS_ROOT','SKILLS_ROOT','AGENTS_VAULT_ROOT'):
        if not env.get(key) or not Path(env[key]).is_dir():raise InstructionError('Missing configured directory: '+key)
    root=Path(env['AGENTS_ROOT']);common=root/'COMMON-AGENTS.md'
    if not common.is_file():raise InstructionError('COMMON-AGENTS.md missing')
    link=home/'.config/agents/environment.env'
    if os.path.lexists(link) and not (link.is_symlink() and link.resolve()==env_file):
        raise InstructionError('Existing environment entrypoint differs; preserve and reconcile')
    changes=[];results=[]
    for agent,path,limit in targets(home,agents):
        if path.is_symlink():
            if agent in ('codex','claude') and path.resolve()==common.resolve():
                results.append({'agent':agent,'path':str(path),'state':'shared_policy_link'});continue
            raise InstructionError('Unexpected instruction symlink: '+str(path))
        original=path.read_text() if path.exists() else ''
        after=merge_block(original,bootstrap())
        if agent=='cursor' and not original:
            after='---\ndescription: Agents shared operating policy\nalwaysApply: true\n---\n\n'+after
        if limit and len(after)>limit:raise InstructionError('Native instruction size limit exceeded: '+str(path))
        results.append({'agent':agent,'path':str(path),'state':'current' if after==original else 'change_required'})
        if after!=original:changes.append((path,original if path.exists() else None,after))
    if apply:
        state=root/'.local/agent-instructions';state.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (state/'install.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            # Recheck all preimages before changing any shared native file.
            for path,before,_ in changes:
                if path.is_symlink() or (path.read_text() if path.exists() else None)!=before:
                    raise InstructionError('Concurrent instruction edit: '+str(path))
            link.parent.mkdir(parents=True,exist_ok=True)
            if not os.path.lexists(link):link.symlink_to(env_file)
            elif not(link.is_symlink() and link.resolve()==env_file):raise InstructionError('Concurrent environment change')
            backups=state/'backups';backups.mkdir(exist_ok=True,mode=0o700);backups.chmod(0o700)
            for path,before,after in changes:
                if before is not None:
                    digest=hashlib.sha256((str(path)+'\0'+before).encode()).hexdigest()
                    backup=backups/digest
                    if not backup.exists():_atomic_write(backup,before)
                path.parent.mkdir(parents=True,exist_ok=True)
                mode=path.stat().st_mode & 0o777 if path.exists() else 0o600
                _atomic_write(path,after)
                path.chmod(mode)
            for row in results:
                if row['state']=='change_required':row['state']='installed'
            _atomic_write(state/'last-install.json',json.dumps({'env_file':str(env_file),'results':results},ensure_ascii=False,indent=2)+'\n')
    return {'applied':apply,'environment':str(link),'results':results}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--env-file',required=True,type=Path)
    p.add_argument('--home',type=Path,default=Path.home());p.add_argument('--agents',nargs='+',required=True,choices=['codex','claude','devin','hermes','cursor'])
    p.add_argument('--apply',action='store_true')
    args=p.parse_args(argv)
    try:result=configure(args.env_file,args.home,args.agents,apply=args.apply)
    except (InstructionError,OSError,ValueError) as e:print(str(e),file=sys.stderr);return 2
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':sys.exit(main())
