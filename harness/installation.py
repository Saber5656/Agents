"""Inspect skill provenance and deploy explicitly selected files with preimages."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid
from .runner import load_dotenv


def digest(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def roots(env):
    result = {}
    for key in ('AGENTS_ROOT', 'SKILLS_ROOT', 'AGENTS_VAULT_ROOT'):
        if not env.get(key) or not Path(env[key]).is_dir():
            raise ValueError(f'{key}: configure an existing directory in the local .env')
        result[key] = Path(env[key]).resolve()
    return result


def inventory(source, installed=(), vendors=()):
    """One identity per name/owner; resolved directory aliases are counted once."""
    rows = {}
    for owner, locations in [('user', [source, *installed]), ('vendor', vendors)]:
        seen = set()
        for index, location in enumerate(locations):
            location = Path(location)
            if not location.is_dir():
                raise ValueError(f'Missing inventory root: {location}')
            for entry in sorted(location.iterdir()):
                skill = entry/'SKILL.md'
                if not skill.is_file():
                    continue
                identity = (owner, str(skill.resolve()))
                if identity in seen:
                    continue
                seen.add(identity)
                row = rows.setdefault((owner,entry.name), {'name':entry.name, 'ownership':owner,
                    'source':None, 'installed':[]})
                info = {'path':str(skill), 'resolved':str(skill.resolve()), 'digest':digest(skill)}
                if owner == 'user' and index == 0:
                    row['source'] = info
                else:
                    row['installed'].append(info)
    return sorted(rows.values(), key=lambda r:(r['ownership'], r['name']))


def contained(root, relative):
    root = Path(root).resolve(strict=True)
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts or not part.parts:
        raise ValueError('Select a relative file inside the skill')
    target = root/part
    cursor = root
    for component in part.parts:
        cursor /= component
        if cursor.is_symlink():
            raise ValueError('Selected symlinks require explicit source reconciliation')
    if not target.resolve().is_relative_to(root):
        raise ValueError('Selected path escapes the skill')
    if target.exists() and not target.is_file():
        raise ValueError('Selected target is not a regular file')
    return target


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.agents-write-', dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),mode)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(name,path)
        d = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(d)
        finally: os.close(d)
    finally:
        if os.path.exists(name): os.unlink(name)


@contextmanager
def locked(vault):
    root = Path(vault)
    if not root.is_dir(): raise ValueError('Existing Vault required')
    records = root/'01-Projects'/'skill-installations'
    records.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (records/'.lock').open('a') as lock:
        os.chmod(lock.name,0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield records


def deploy_file(source, target, relative, expected, vault):
    """CAS against an inspected preimage; never replace the whole installed tree."""
    src = contained(source,relative); dst = contained(target,relative)
    if src == dst:
        return {'status':'already_effective','effective_digest':digest(src),'path':str(dst)}
    if not src.is_file(): raise ValueError('Source file missing')
    with locked(vault) as records:
        if digest(dst) != expected: raise ValueError('Installed preimage changed; reconcile local edits')
        run = records/uuid.uuid4().hex; run.mkdir(mode=0o700)
        prior = dst.read_bytes() if dst.exists() else None
        mode = stat.S_IMODE(dst.stat().st_mode) if dst.exists() else stat.S_IMODE(src.stat().st_mode)
        if prior is not None: atomic_write(run/'preimage',prior)
        desired = src.read_bytes()
        receipt = {'status':'prepared','target_root':str(Path(target).resolve()),'relative':relative,
            'vault':str(Path(vault).resolve()),'before':expected,'effective_digest':hashlib.sha256(desired).hexdigest(),
            'mode':mode,'record':str(run/'receipt.json')}
        atomic_write(run/'receipt.json', json.dumps(receipt,indent=2).encode())
        # Require the parent to exist; no guessed installation directories.
        if not dst.parent.is_dir(): raise ValueError('Target parent missing')
        if digest(dst) != expected: raise ValueError('Installed preimage changed during preparation')
        atomic_write(dst,desired,mode)
        if digest(dst) != receipt['effective_digest']: raise ValueError('Deployment read-back mismatch')
        receipt['status']='installed'
        atomic_write(run/'receipt.json',json.dumps(receipt,indent=2).encode())
        return receipt


def rollback(record):
    record = Path(record)
    receipt = json.loads(record.read_text())
    with locked(receipt['vault']):
        dst = contained(receipt['target_root'],receipt['relative'])
        current = digest(dst)
        if current == receipt['before']:
            receipt['status']='rolled_back'
        elif current != receipt['effective_digest']:
            raise ValueError('Installed file changed after deployment; preserve local edits')
        elif receipt['before'] is None:
            dst.unlink(); receipt['status']='rolled_back'
        else:
            preimage = record.parent/'preimage'
            if digest(preimage) != receipt['before']: raise ValueError('Preimage integrity failure')
            atomic_write(dst,preimage.read_bytes(),receipt['mode']); receipt['status']='rolled_back'
        atomic_write(record,json.dumps(receipt,indent=2).encode())
        return receipt


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file',type=Path,default=Path(__file__).resolve().parents[1]/'.env')
    commands=p.add_subparsers(dest='command',required=True)
    i=commands.add_parser('inventory'); i.add_argument('--installed',type=Path,action='append',default=[])
    i.add_argument('--vendor',type=Path,action='append',default=[])
    d=commands.add_parser('deploy'); d.add_argument('--skill',required=True); d.add_argument('--target',type=Path,required=True)
    d.add_argument('--file',required=True); d.add_argument('--expected',required=True,help='Inspected sha256, or absent')
    r=commands.add_parser('rollback'); r.add_argument('record',type=Path)
    args=p.parse_args(argv)
    try:
        env=roots(load_dotenv(args.env_file,os.environ))
        if args.command=='inventory': result=inventory(env['SKILLS_ROOT'],args.installed,args.vendor)
        elif args.command=='rollback': result=rollback(args.record)
        else:
            if Path(args.skill).name != args.skill or args.skill in ('.','..'):
                raise ValueError('Select one skill name')
            result=deploy_file(env['SKILLS_ROOT']/args.skill,args.target/args.skill,args.file,
                None if args.expected=='absent' else args.expected,env['AGENTS_VAULT_ROOT'])
        print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
    except (ValueError,OSError,KeyError) as exc:
        print(json.dumps({'status':'incomplete','error':str(exc)})); return 2


if __name__=='__main__': raise SystemExit(main())
