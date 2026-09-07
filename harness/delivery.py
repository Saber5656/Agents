"""Preserving Git delivery primitives. No retired runtime or permission bypass."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from .runner import redact


class DeliveryError(ValueError):
    pass


def command(argv,cwd=None):
    try:
        p=subprocess.run(argv,cwd=cwd,capture_output=True,text=True,timeout=60)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise DeliveryError(f'Command incomplete: {argv[0]} ({type(exc).__name__})') from exc
    if p.returncode:
        raise DeliveryError(redact(p.stderr or p.stdout,os.environ))
    return p.stdout.strip()


def git(root,*args):return command(['git',*args],root)


def oid(value):
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',value):
        raise DeliveryError('An immutable full commit OID is required')
    return value


def public_text(text,env=None,english=False):
    if redact(text,env if env is not None else os.environ) != text:
        raise DeliveryError('Secret detected in selected public content')
    if re.search(r'/(?:Users|home)/[^/\s]+(?:/|(?=$|[\s]))|[A-Za-z]:\\Users\\[^\\\s]+(?:\\|(?=$|[\s]))',text):
        raise DeliveryError('Personal home path detected in selected public content')
    if english and re.search(r'[\u3040-\u30ff\u3400-\u9fff]',text):
        raise DeliveryError('Public title/body must be authored in English')
    return text


def prepare_worktree(repo,path,branch,base):
    """Reuse only a matching identity; do not reset, stash or clean other work."""
    repo=Path(repo).resolve();path=Path(path).absolute();oid(base)
    git(repo,'cat-file','-e',base+'^{commit}')
    if branch in ('main','master') or branch.startswith('-'):
        raise DeliveryError('Select a task branch')
    git(repo,'check-ref-format','--branch',branch)
    if path.exists():
        entries=git(repo,'worktree','list','--porcelain').split('\n\n')
        matching=next((e for e in entries if e.splitlines()[0]=='worktree '+str(path.resolve())),None)
        if not matching or 'branch refs/heads/'+branch not in matching.splitlines():
            raise DeliveryError('Existing workspace has conflicting ownership/branch')
        if git(path,'merge-base',base,'HEAD')!=base:
            raise DeliveryError('Existing workspace does not descend from selected base')
    else:
        # Existing refs are deliberately not repurposed without their workspace identity.
        git(repo,'worktree','add','-b',branch,str(path),base)
    return {'worktree':str(path.resolve()),'branch':branch,'base':base,
            'head':git(path,'rev-parse','HEAD')}


def verify_remote(repo,expected):
    urls=[git(repo,'remote','get-url','origin'),git(repo,'remote','get-url','--push','origin')]
    if urls!=[expected,expected]:raise DeliveryError('Remote destination changed')


def sync_main(repo,branch,merge_sha,remote):
    oid(merge_sha);verify_remote(repo,remote)
    if git(repo,'symbolic-ref','--short','HEAD')!=branch:
        raise DeliveryError('Canonical checkout is detached or on another branch')
    if git(repo,'status','--porcelain=v1','-uall'):
        raise DeliveryError('Canonical checkout is dirty; preserve local state')
    git(repo,'fetch','origin',branch)
    target=git(repo,'rev-parse','FETCH_HEAD')
    if git(repo,'merge-base',merge_sha,target)!=merge_sha:
        raise DeliveryError('Remote branch does not contain verified merge')
    if git(repo,'merge-base','HEAD',target)!=git(repo,'rev-parse','HEAD'):
        raise DeliveryError('Canonical main diverged; preserve local commits')
    git(repo,'merge','--ff-only',target)
    if git(repo,'rev-parse','HEAD')!=target:raise DeliveryError('Main read-back mismatch')
    return target


def merge_ready(state,head,base,required,threads):
    if required is None:raise DeliveryError('Required check discovery unavailable')
    if state['headRefOid']!=head or state['baseRefOid']!=base:
        raise DeliveryError('PR head/base changed since validation')
    if state['state']!='OPEN' or state.get('isDraft') or state.get('mergeable')!='MERGEABLE':
        raise DeliveryError('PR is not ready to merge')
    if state.get('reviewDecision')=='CHANGES_REQUESTED':raise DeliveryError('Blocking review')
    if any(not r.get('isResolved') and not r.get('isOutdated') for r in threads):
        raise DeliveryError('Unresolved current review findings')
    checks=state.get('statusCheckRollup') or []
    for name in required:
        matches=[c for c in checks if (c.get('name') or c.get('context'))==name]
        if not matches:raise DeliveryError('Missing required check: '+name)
        for check in matches:
            if check.get('conclusion') not in ('SUCCESS','NEUTRAL','SKIPPED') and check.get('state')!='SUCCESS':
                raise DeliveryError('Required check not successful: '+name)
    # A reported failed/pending check is not silently ignored even if unprotected.
    for check in checks:
        if check.get('conclusion') not in ('SUCCESS','NEUTRAL','SKIPPED') and check.get('state')!='SUCCESS':
            raise DeliveryError('Observed check not successful')


def issue_body(task_id,outcome,acceptance):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',task_id):raise DeliveryError('Invalid local task identity')
    if not acceptance:raise DeliveryError('Observable acceptance required')
    return public_text(f'<!-- agents-local-task:{task_id} -->\n\n## Outcome\n\n{outcome}\n\n## Acceptance\n\n'+
        '\n'.join('- [ ] '+item for item in acceptance)+'\n',english=True)


class GitHub:
    def __init__(self,repo):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):raise DeliveryError('Invalid repository')
        self.repo=repo

    def api(self,path):return json.loads(command(['gh','api',f'repos/{self.repo}/{path}']))

    def pr(self,number):
        return json.loads(command(['gh','pr','view',str(number),'--repo',self.repo,'--json',
            'number,state,isDraft,headRefOid,baseRefOid,reviewDecision,mergeable,statusCheckRollup,mergeCommit']))

    def required_checks(self,branch):
        # Both modern rules and legacy branch protection must be observed.
        from urllib.parse import quote
        name=quote(branch,safe='');rules=self.api('rules/branches/'+name)
        detail=self.api('branches/'+name);names=[]
        for rule in rules:
            if rule['type']=='required_status_checks':
                names.extend(x['context'] for x in rule['parameters']['required_status_checks'])
        if detail.get('protected'):
            # An unavailable endpoint is an incomplete discovery, never zero checks.
            protection=self.api('branches/'+name+'/protection')
            required=protection.get('required_status_checks') or {}
            names.extend(required.get('contexts',[]))
            names.extend(x['context'] for x in required.get('checks',[]))
        return sorted(set(names))

    def threads(self,number):
        owner,name=self.repo.split('/')
        query='''query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{isResolved isOutdated} pageInfo{hasNextPage endCursor}}}}}'''
        rows=[];cursor=None
        while True:
            args=['gh','api','graphql','-f','query='+query,'-f','owner='+owner,'-f','name='+name,'-F','number='+str(number)]
            if cursor:args+=['-f','cursor='+cursor]
            value=json.loads(command(args))
            if value.get('errors'):raise DeliveryError('Review thread discovery incomplete')
            data=value['data']['repository']['pullRequest']['reviewThreads'];rows+=data['nodes']
            if not data['pageInfo']['hasNextPage']:return rows
            cursor=data['pageInfo']['endCursor']

    def merge(self,number,head,base,branch='main'):
        oid(head);oid(base)
        first=self.pr(number)
        if first['state']=='MERGED':
            if first['headRefOid']!=head:raise DeliveryError('Merged PR has different head')
            return first
        required=self.required_checks(branch)
        threads=self.threads(number)
        current=self.pr(number)
        merge_ready(current,head,base,required,threads)
        # GitHub enforces native protection and the expected head. The API has no
        # compare-and-swap for base; do not claim an atomic base pin/queue guarantee.
        command(['gh','pr','merge',str(number),'--repo',self.repo,'--merge','--match-head-commit',head])
        result=self.pr(number)
        if result['state']!='MERGED' or not result.get('mergeCommit'):
            raise DeliveryError('Merge pending; reconcile before retry')
        return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    p=parser.add_subparsers(dest='command',required=True)
    check=p.add_parser('check-public');check.add_argument('path',type=Path);check.add_argument('--english',action='store_true')
    sync=p.add_parser('sync');sync.add_argument('--repo',type=Path,required=True);sync.add_argument('--remote',required=True)
    sync.add_argument('--branch',default='main');sync.add_argument('--merge-sha',required=True)
    merge=p.add_parser('merge');merge.add_argument('--repo',required=True);merge.add_argument('--pr',type=int,required=True)
    merge.add_argument('--head',required=True);merge.add_argument('--base',required=True);merge.add_argument('--branch',default='main')
    args=parser.parse_args(argv)
    try:
        if args.command=='check-public':public_text(args.path.read_text(),english=args.english);result={'status':'checked'}
        elif args.command=='sync':result={'main':sync_main(args.repo,args.branch,args.merge_sha,args.remote)}
        else:result=GitHub(args.repo).merge(args.pr,args.head,args.base,args.branch)
        print(json.dumps(result,indent=2));return 0
    except (DeliveryError,OSError,KeyError) as exc:
        print(json.dumps({'status':'incomplete','error':str(exc)}));return 2


if __name__=='__main__':raise SystemExit(main())
