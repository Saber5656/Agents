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


def public_git_changes(repo, base, head, env=None):
    """Validate the complete unpublished commit messages and diff before export.

    This is deliberately a read-only check.  It binds both revisions to full
    commit IDs and scans the bytes that would be exposed, including commit
    messages that are not present in the rendered patch.
    """
    repo = Path(repo).resolve()
    oid(base); oid(head)
    git(repo, 'cat-file', '-e', base + '^{commit}')
    git(repo, 'cat-file', '-e', head + '^{commit}')
    history = git(repo, 'log', '--format=%H%n%B', base + '..' + head, '--')
    diff = git(repo, 'diff', '--no-ext-diff', '--binary', base + '..' + head, '--')
    public_text(history, env)
    public_text(diff, env)
    return history + ('\n' if history and diff else '') + diff


def prepare_worktree(repo,path,branch,base):
    """Reuse only a matching identity; do not reset, stash or clean other work."""
    repo=Path(repo).resolve();path=Path(path).absolute();oid(base)
    try:
        git(repo,'cat-file','-e',base+'^{commit}')
    except DeliveryError as exc:
        raise DeliveryError('Selected immutable base is unavailable') from exc
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
        try:
            branch_probe=subprocess.run(
                ['git','show-ref','--verify','--quiet','refs/heads/'+branch],
                cwd=repo, capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DeliveryError('Cannot determine whether task branch exists') from exc
        if branch_probe.returncode == 0:
            raise DeliveryError('Existing branch has no matching worktree; explicit reconciliation required')
        if branch_probe.returncode != 1:
            raise DeliveryError('Cannot determine whether task branch exists')
        try:
            git(repo,'worktree','add','-b',branch,str(path),base)
        except DeliveryError as exc:
            raise DeliveryError('Worktree creation failed; preserve existing repository state') from exc
    return {'worktree':str(path.resolve()),'branch':branch,'base':base,
            'head':git(path,'rev-parse','HEAD')}


def verify_remote(repo,expected):
    urls=[git(repo,'remote','get-url','origin'),git(repo,'remote','get-url','--push','origin')]
    if urls!=[expected,expected]:raise DeliveryError('Remote destination changed')


def sync_main(repo,branch,merge_sha,remote):
    oid(merge_sha);verify_remote(repo,remote)
    try:
        current_branch=git(repo,'symbolic-ref','--short','HEAD')
    except DeliveryError as exc:
        raise DeliveryError('Canonical checkout is detached or on another branch') from exc
    if current_branch!=branch:
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


def _check_successful(check):
    """Accept only an unambiguous completed success from a check object.

    Check runs report ``status=COMPLETED`` and ``conclusion=SUCCESS`` while
    status contexts report ``state=SUCCESS``.  A mixed or incomplete object is
    deliberately rejected so one field cannot mask a contradictory result.
    """
    conclusion = check.get('conclusion')
    state = check.get('state')
    status = check.get('status')
    if conclusion is not None:
        if str(conclusion).upper() != 'SUCCESS' or state is not None:
            return False
        return status is None or str(status).upper() == 'COMPLETED'
    return state is not None and str(state).upper() == 'SUCCESS' and status is None


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
    for requirement in required:
        name=requirement if isinstance(requirement,str) else requirement['context']
        app_id=None if isinstance(requirement,str) else requirement.get('app_id')
        if app_id not in (None,-1):
            matches=[c for c in state.get('checkRuns',[]) if c.get('name')==name
                     and (c.get('app') or {}).get('id')==app_id and c.get('head_sha')==head]
        else:
            matches=[c for c in checks if (c.get('name') or c.get('context'))==name]
        if not matches:raise DeliveryError('Missing required check: '+name)
        for check in matches:
            if not _check_successful(check):
                raise DeliveryError('Required check not successful: '+name)
    # A reported failed/pending check is not silently ignored even if unprotected.
    for check in checks:
        if not _check_successful(check):
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
            'number,url,state,isDraft,headRefName,headRefOid,baseRefOid,baseRefName,reviewDecision,mergeable,statusCheckRollup,mergeCommit,title,body,assignees,labels']))

    def list_prs(self, head, base):
        """List candidate PRs without treating an empty eventual page as proof.

        The caller uses a second read after a failed create.  A zero-result
        second read is returned as zero and is handled as an uncertain outcome,
        so it can never trigger a blind duplicate create.
        """
        try:
            value = json.loads(command(['gh', 'pr', 'list', '--repo', self.repo,
                '--state', 'all', '--head', head, '--base', base, '--json',
                'number,url,state,headRefName,headRefOid,baseRefName,baseRefOid,title,body,assignees,labels']))
        except (json.JSONDecodeError, DeliveryError) as exc:
            raise DeliveryError('PR listing incomplete') from exc
        if not isinstance(value, list):
            raise DeliveryError('PR listing incomplete')
        return value

    @staticmethod
    def _matching_prs(rows, head, base):
        owners = {head, head.split(':', 1)[-1]}
        return [row for row in rows
                if row.get('baseRefName') == base and row.get('headRefName') in owners]

    def push_branch(self, repo, branch, head, remote='origin', *, expected_remote,
                    base, allowed_paths=None, env=None):
        """Push one reviewed commit bound to its remote and immutable base."""
        repo = Path(repo).resolve(); oid(head)
        if branch in ('main', 'master') or branch.startswith('-'):
            raise DeliveryError('A task branch is required for PR publication')
        try:
            if git(repo, 'symbolic-ref', '--short', 'HEAD') != branch:
                raise DeliveryError('Selected task branch is not checked out')
        except DeliveryError as exc:
            if str(exc) == 'Selected task branch is not checked out':
                raise
            raise DeliveryError('Selected task branch is detached') from exc
        if git(repo, 'rev-parse', 'HEAD') != head:
            raise DeliveryError('Selected commit is not the current HEAD')
        dirty = git(repo, 'status', '--porcelain=v1', '-uall').splitlines()
        oid(base)
        try:
            if git(repo, 'merge-base', base, head) != base:
                raise DeliveryError('Selected commit does not descend from the reviewed base')
        except DeliveryError as exc:
            if str(exc) == 'Selected commit does not descend from the reviewed base':
                raise
            raise DeliveryError('Reviewed base is unavailable') from exc
        public_git_changes(repo, base, head, env)
        if allowed_paths is not None:
            dirty_names = [line[3:] for line in dirty if len(line) >= 4]
            allowed = tuple(str(path).rstrip('/') for path in allowed_paths)
            if any(name == path or name.startswith(path + '/')
                   for name in dirty_names for path in allowed):
                raise DeliveryError('Task-owned unpublished changes must be committed first')
            names = git(repo, 'diff', '--name-only', base + '..' + head, '--').splitlines()
            if any(not any(name == path or name.startswith(path + '/') for path in allowed)
                   for name in names):
                raise DeliveryError('Selected commit contains an out-of-scope path')
        urls = [git(repo, 'remote', 'get-url', remote),
                git(repo, 'remote', 'get-url', '--push', remote)]
        if urls[0] != urls[1]:
            raise DeliveryError('Remote fetch and push destinations differ')
        if urls[0] != expected_remote:
            raise DeliveryError('Remote destination changed')
        before = git(repo, 'ls-remote', '--heads', remote, branch)
        before_oid = before.split()[0] if before else None
        if before_oid and before_oid != head:
            raise DeliveryError('Remote branch diverged; preserve it')
        try:
            command(['git', 'push', remote,
                     f'refs/heads/{branch}:refs/heads/{branch}'], cwd=repo)
        except DeliveryError as exc:
            # A lost response is reconciled by the remote readback.  It is
            # never retried as a second push when the result is unknown.
            after = git(repo, 'ls-remote', '--heads', remote, branch)
            after_oid = after.split()[0] if after else None
            if after_oid != head:
                raise DeliveryError('Push outcome unknown; reconcile before retry') from exc
        after = git(repo, 'ls-remote', '--heads', remote, branch)
        remote_head = after.split()[0] if after else None
        if remote_head != head:
            raise DeliveryError('Remote branch readback mismatch')
        return {'remote': remote, 'branch': branch, 'remote_head': remote_head,
                'unrelated_dirty': dirty}

    def create_or_reuse_pr(self, head, base, title, body, *, assignees=(), labels=(),
                           head_oid, env=None):
        """Create/reconcile one PR whose readback is bound to reviewed ``head_oid``."""
        if not head or not base or head == base or head in ('main', 'master'):
            raise DeliveryError('A task branch and distinct base are required')
        oid(head_oid)
        public_text(title, env, english=True); public_text(body, env, english=True)
        rows = self.list_prs(head, base)
        matches = self._matching_prs(rows, head, base)
        if len(matches) > 1:
            raise DeliveryError('Multiple matching PRs require reconciliation')
        if matches:
            result = matches[0]
        else:
            argv = ['gh', 'pr', 'create', '--repo', self.repo, '--base', base,
                    '--head', head, '--title', title, '--body', body]
            for login in assignees: argv += ['--assignee', login]
            for label in labels: argv += ['--label', label]
            try:
                command(argv)
            except DeliveryError as exc:
                # A failed response is ambiguous.  Reconcile the same exact
                # branch/base pair; an empty page is not definitive.
                try:
                    rows = self.list_prs(head, base)
                except DeliveryError as read_exc:
                    raise DeliveryError('PR create outcome unknown; reconcile before retry') from read_exc
                matches = self._matching_prs(rows, head, base)
                if len(matches) != 1:
                    raise DeliveryError('PR create outcome unknown; reconcile before retry') from exc
                result = matches[0]
            else:
                rows = self.list_prs(head, base)
                matches = self._matching_prs(rows, head, base)
                if len(matches) != 1:
                    raise DeliveryError('PR create outcome unknown; reconcile before retry')
                result = matches[0]
        if str(result.get('state', '')).upper() != 'OPEN':
            raise DeliveryError('Matching PR is not open')
        if result.get('title') != title or result.get('body') != body:
            raise DeliveryError('PR readback public content differs from draft')
        if result.get('headRefOid') != head_oid:
            raise DeliveryError('PR head readback mismatch')
        if assignees:
            observed = {item.get('login') if isinstance(item, dict) else item
                        for item in (result.get('assignees') or [])}
            if observed != set(assignees):
                raise DeliveryError('PR assignees readback mismatch')
        return result

    def required_checks(self,branch):
        # Both modern rules and legacy branch protection must be observed.
        from urllib.parse import quote
        name=quote(branch,safe='');rules=self.api('rules/branches/'+name)
        detail=self.api('branches/'+name);requirements=[]
        for rule in rules:
            if rule['type']=='required_status_checks':
                requirements.extend((x['context'],x.get('integration_id'))
                    for x in rule['parameters']['required_status_checks'])
        if detail.get('protected'):
            # Unavailable discovery remains incomplete, never an empty policy.
            protection=self.api('branches/'+name+'/protection')
            required=protection.get('required_status_checks') or {}
            bound=required.get('checks',[])
            requirements.extend((x['context'],x.get('app_id')) for x in bound)
            requirements.extend((context,None) for context in required.get('contexts',[])
                                if context not in {x['context'] for x in bound})
        unique=set(requirements)
        return [{'context':context,'app_id':app} for context,app in
                sorted(unique,key=lambda item:(item[0],str(item[1])))]

    def check_runs(self,head):
        oid(head)
        pages=json.loads(command(['gh','api','--paginate','--slurp',
            f'repos/{self.repo}/commits/{head}/check-runs?per_page=100&filter=latest']))
        return [run for page in pages for run in page['check_runs']]

    def threads(self,number):
        owner,name=self.repo.split('/')
        query='''query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{id isResolved isOutdated} pageInfo{hasNextPage endCursor}}}}}'''
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
        if first.get('baseRefName')!=branch:raise DeliveryError('PR targets a different branch')
        if first['state']=='MERGED':
            if first['headRefOid']!=head:raise DeliveryError('Merged PR has different head')
            return first
        required=self.required_checks(branch)
        threads=self.threads(number)
        current=self.pr(number)
        if current.get('baseRefName')!=branch:raise DeliveryError('PR targets a different branch')
        if any(isinstance(r,dict) and r.get('app_id') not in (None,-1) for r in required):
            current['checkRuns']=self.check_runs(head)
        fresh_threads=self.threads(number)
        if sorted(threads,key=lambda r:r.get('id',''))!=sorted(fresh_threads,key=lambda r:r.get('id','')):
            raise DeliveryError('Review threads changed; reobserve before merge')
        merge_ready(current,head,base,required,fresh_threads)
        # GitHub enforces native protection and the expected head. The API has no
        # compare-and-swap for base; do not claim an atomic base pin/queue guarantee.
        try:
            command(['gh','pr','merge',str(number),'--repo',self.repo,'--merge','--match-head-commit',head])
        except DeliveryError as exc:
            try:
                result = self.pr(number)
            except DeliveryError as read_exc:
                raise DeliveryError('Merge outcome unknown; reconcile before retry') from read_exc
            if result.get('state') != 'MERGED' or result.get('headRefOid') != head:
                raise DeliveryError('Merge outcome unknown; reconcile before retry') from exc
            if result.get('baseRefName') != branch or not result.get('mergeCommit'):
                raise DeliveryError('Merge outcome unknown; reconcile before retry') from exc
            return result
        result=self.pr(number)
        if result['state']!='MERGED' or not result.get('mergeCommit') or result.get('baseRefName')!=branch or result.get('headRefOid')!=head:
            raise DeliveryError('Merge pending; reconcile before retry')
        return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    p=parser.add_subparsers(dest='command',required=True)
    check=p.add_parser('check-public');check.add_argument('path',type=Path);check.add_argument('--english',action='store_true')
    revision=p.add_parser('check-public-revision');revision.add_argument('--repo',type=Path,required=True)
    revision.add_argument('--base',required=True);revision.add_argument('--head',required=True)
    sync=p.add_parser('sync');sync.add_argument('--repo',type=Path,required=True);sync.add_argument('--remote',required=True)
    sync.add_argument('--branch',default='main');sync.add_argument('--merge-sha',required=True)
    merge=p.add_parser('merge');merge.add_argument('--repo',required=True);merge.add_argument('--pr',type=int,required=True)
    merge.add_argument('--head',required=True);merge.add_argument('--base',required=True);merge.add_argument('--branch',default='main')
    args=parser.parse_args(argv)
    try:
        if args.command=='check-public':public_text(args.path.read_text(),english=args.english);result={'status':'checked'}
        elif args.command=='check-public-revision':
            public_git_changes(args.repo,args.base,args.head);result={'status':'checked'}
        elif args.command=='sync':result={'main':sync_main(args.repo,args.branch,args.merge_sha,args.remote)}
        else:result=GitHub(args.repo).merge(args.pr,args.head,args.base,args.branch)
        print(json.dumps(result,indent=2));return 0
    except (DeliveryError,OSError,KeyError) as exc:
        print(json.dumps({'status':'incomplete','error':str(exc)}));return 2


if __name__=='__main__':raise SystemExit(main())
