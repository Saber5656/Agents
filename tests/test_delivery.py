import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from harness.delivery import (public_text, public_git_changes, prepare_worktree,
                              sync_main, merge_ready, DeliveryError, issue_body,
                              GitHub)


def git(cwd,*args):
    return subprocess.check_output(['git',*args],cwd=cwd,text=True).strip()


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        git(self.repo,'init','-b','main');git(self.repo,'config','user.name','Fixture')
        git(self.repo,'config','user.email','fixture@example.invalid')
        (self.repo/'a').write_text('base');git(self.repo,'add','a');git(self.repo,'commit','-m','base')
        self.base=git(self.repo,'rev-parse','HEAD')

    def test_worktree_preserves_dirty_primary_and_reuses_same_identity(self):
        (self.repo/'a').write_text('keep me')
        git(self.repo,'add','a');before=git(self.repo,'diff','--cached')
        path=self.root/'task'
        first=prepare_worktree(self.repo,path,'task/demo',self.base)
        self.assertEqual(self.base,git(path,'rev-parse','HEAD'))
        self.assertEqual(first,prepare_worktree(self.repo,path,'task/demo',self.base))
        self.assertEqual(before,git(self.repo,'diff','--cached'))
        self.assertEqual('keep me',(self.repo/'a').read_text())
        with self.assertRaises(DeliveryError):prepare_worktree(self.repo,path,'task/other',self.base)

    def test_mutable_base_rejected(self):
        with self.assertRaisesRegex(DeliveryError,'immutable'):
            prepare_worktree(self.repo,self.root/'task','task/demo','main')

    def test_existing_branch_without_matching_worktree_requires_reconciliation(self):
        git(self.repo, 'branch', 'task/demo')
        with self.assertRaisesRegex(DeliveryError, 'explicit reconciliation'):
            prepare_worktree(self.repo, self.root/'task', 'task/demo', self.base)

    def test_detached_or_unrelated_existing_directory_is_not_adopted(self):
        path = self.root/'task'; path.mkdir()
        with self.assertRaisesRegex(DeliveryError, 'conflicting ownership'):
            prepare_worktree(self.repo, path, 'task/demo', self.base)
        detached = self.root/'detached'
        git(self.repo, 'worktree', 'add', '--detach', str(detached), self.base)
        self.addCleanup(lambda: git(self.repo, 'worktree', 'remove', str(detached)))
        with self.assertRaisesRegex(DeliveryError, 'conflicting ownership'):
            prepare_worktree(self.repo, detached, 'task/detached', self.base)

    def test_missing_immutable_base_has_explicit_incomplete_state(self):
        with self.assertRaisesRegex(DeliveryError, 'base is unavailable'):
            prepare_worktree(self.repo, self.root/'task', 'task/demo', 'f'*40)

    def test_worker_readback_matches_actual_cwd_branch_and_base(self):
        path = self.root/'task'
        prepared = prepare_worktree(self.repo, path, 'task/demo', self.base)
        observed = {
            'cwd': subprocess.check_output(['pwd'], cwd=path, text=True).strip(),
            'branch': git(path, 'symbolic-ref', '--short', 'HEAD'),
            'base': git(path, 'merge-base', self.base, 'HEAD'),
        }
        self.assertEqual(prepared['worktree'], observed['cwd'])
        self.assertEqual('task/demo', observed['branch'])
        self.assertEqual(self.base, observed['base'])

    def test_sync_only_clean_main_and_contains_merge(self):
        remote=self.root/'remote.git';git(self.root,'init','--bare',str(remote))
        git(self.repo,'remote','add','origin',str(remote));git(self.repo,'push','origin','main')
        self.assertEqual(self.base,sync_main(self.repo,'main',self.base,remote=str(remote)))
        (self.repo/'a').write_text('local')
        with self.assertRaisesRegex(DeliveryError,'dirty'):
            sync_main(self.repo,'main',self.base,remote=str(remote))
        self.assertEqual('local',(self.repo/'a').read_text())

    def test_sync_remote_failure_preserves_primary_state(self):
        (self.repo/'a').write_text('keep dirty')
        with self.assertRaises(DeliveryError):
            sync_main(self.repo, 'main', self.base, remote=str(self.root/'missing-remote'))
        self.assertEqual('keep dirty', (self.repo/'a').read_text())

    def test_privacy_before_transmission(self):
        for text in ['fixture '+'/'.join(['','Users','example','private']), 'token '+('ghp_'+'x'*30), '日本語公開本文']:
            with self.assertRaises(DeliveryError):public_text(text,english=True)
        with self.assertRaises(DeliveryError):public_text('secret-value',env={'API_KEY':'secret-value'})
        self.assertEqual('Use $AGENTS_ROOT.',public_text('Use $AGENTS_ROOT.',english=True))

    def state(self):
        return {'number':1,'baseRefName':'main','state':'OPEN','isDraft':False,'headRefOid':'a'*40,'baseRefOid':'b'*40,
            'reviewDecision':'','mergeable':'MERGEABLE','statusCheckRollup':[
                {'name':'test','status':'COMPLETED','conclusion':'SUCCESS'}]}

    def test_merge_checks_head_base_and_findings(self):
        s=self.state();merge_ready(s,'a'*40,'b'*40,['test'],[])
        for change in [{'headRefOid':'c'*40},{'baseRefOid':'c'*40},{'reviewDecision':'CHANGES_REQUESTED'},
                       {'statusCheckRollup':[]},{'statusCheckRollup':[{'name':'test','status':'COMPLETED','conclusion':'FAILURE'}]}]:
            with self.assertRaises(DeliveryError):merge_ready(s|change,'a'*40,'b'*40,['test'],[])
        with self.assertRaises(DeliveryError):merge_ready(s,'a'*40,'b'*40,['test'],[{'isResolved':False,'isOutdated':False}])
        with self.assertRaises(DeliveryError):merge_ready(s,'a'*40,'b'*40,None,[])

    def test_merge_requires_actual_success_and_rejects_contradictions(self):
        for conclusion in ['NEUTRAL', 'SKIPPED', 'FAILURE', None]:
            check={'name':'test','status':'COMPLETED','conclusion':conclusion}
            if conclusion is None:
                check.pop('conclusion')
            with self.subTest(check=check), self.assertRaises(DeliveryError):
                merge_ready(self.state() | {'statusCheckRollup':[check]},
                            'a'*40, 'b'*40, ['test'], [])
        contradictory=self.state() | {
            'statusCheckRollup':[{'name':'test','state':'SUCCESS','conclusion':'FAILURE'}]
        }
        with self.assertRaises(DeliveryError):
            merge_ready(contradictory, 'a'*40, 'b'*40, ['test'], [])
        status_context=self.state() | {
            'statusCheckRollup':[{'context':'test','state':'SUCCESS'}]
        }
        merge_ready(status_context, 'a'*40, 'b'*40, ['test'], [])

    def test_duplicate_check_name_cannot_hide_failure(self):
        s=self.state();s['statusCheckRollup'].append({'name':'test','status':'COMPLETED','conclusion':'FAILURE'})
        with self.assertRaises(DeliveryError):merge_ready(s,'a'*40,'b'*40,['test'],[])

    def test_merge_mutation_never_sent_for_changed_head(self):
        client=GitHub('fixture/repository')
        first=self.state(); second=first|{'headRefOid':'c'*40}
        with patch.object(client,'required_checks',return_value=['test']), patch.object(client,'threads',return_value=[]), \
             patch.object(client,'pr',side_effect=[first,second]), patch('harness.delivery.command') as sent:
            with self.assertRaises(DeliveryError):client.merge(1,'a'*40,'b'*40)
            sent.assert_not_called()

    def test_existing_merged_result_reused_without_mutation(self):
        client=GitHub('fixture/repository')
        state=self.state()|{'state':'MERGED','mergeCommit':{'oid':'d'*40}}
        with patch.object(client,'pr',return_value=state), patch('harness.delivery.command') as sent:
            self.assertEqual(state,client.merge(1,'a'*40,'b'*40))
            sent.assert_not_called()


    def test_home_directory_without_child_is_private(self):
        for home in ['/'.join(['', 'home', 'fixture']), '/'.join(['', 'Users', 'fixture']),
                     chr(92).join(['C:', 'Users', 'fixture'])]:
            with self.subTest(home=home), self.assertRaises(DeliveryError):
                public_text('Home: ' + home)

    def test_sync_uses_fresh_fetch_without_tracking_refspec(self):
        remote=self.root/'remote.git';git(self.root,'init','--bare',str(remote))
        git(self.repo,'remote','add','origin',str(remote));git(self.repo,'push','origin','main')
        git(self.repo,'config','--unset-all','remote.origin.fetch')
        other=self.root/'other';git(self.root,'clone','--branch','main',str(remote),str(other))
        git(other,'config','user.name','Fixture');git(other,'config','user.email','fixture@example.invalid')
        (other/'b').write_text('new');git(other,'add','b');git(other,'commit','-m','new');git(other,'push','origin','main')
        latest=git(other,'rev-parse','HEAD')
        self.assertEqual(latest,sync_main(self.repo,'main',self.base,str(remote)))
        self.assertEqual(latest,sync_main(self.repo,'main',latest,str(remote)))
        self.assertEqual(latest,git(self.repo,'rev-parse','HEAD'))

    def test_sync_blocks_divergent_and_detached_primary_without_mutation(self):
        remote=self.root/'remote.git';git(self.root,'init','--bare',str(remote))
        git(self.repo,'remote','add','origin',str(remote));git(self.repo,'push','origin','main')
        other=self.root/'other';git(self.root,'clone','--branch','main',str(remote),str(other))
        git(other,'config','user.name','Fixture');git(other,'config','user.email','fixture@example.invalid')
        (other/'remote').write_text('remote');git(other,'add','remote');git(other,'commit','-m','remote');git(other,'push','origin','main')
        (self.repo/'local').write_text('local');git(self.repo,'add','local');git(self.repo,'commit','-m','local')
        local_head=git(self.repo,'rev-parse','HEAD')
        with self.assertRaisesRegex(DeliveryError,'diverged'):
            sync_main(self.repo,'main',git(other,'rev-parse','HEAD'),str(remote))
        self.assertEqual(local_head,git(self.repo,'rev-parse','HEAD'))
        git(self.repo,'switch','--detach','HEAD')
        with self.assertRaisesRegex(DeliveryError,'detached'):
            sync_main(self.repo,'main',git(other,'rev-parse','HEAD'),str(remote))

    def test_other_base_branch_never_mutates_even_with_same_oid(self):
        client=GitHub('fixture/repository')
        for status in ['OPEN','MERGED']:
            state=self.state()|{'state':status,'baseRefName':'release','mergeCommit':{'oid':'d'*40}}
            with patch.object(client,'pr',return_value=state), patch.object(client,'required_checks',return_value=[]), \
                 patch.object(client,'threads',return_value=[]), patch('harness.delivery.command') as sent:
                with self.assertRaises(DeliveryError):client.merge(1,'a'*40,'b'*40)
                sent.assert_not_called()

    def test_required_producer_cannot_be_impersonated_by_name(self):
        state=self.state()
        requirement={'context':'test','app_id':42}
        state['checkRuns']=[{'name':'test','head_sha':'a'*40,'app':{'id':7},'conclusion':'SUCCESS'}]
        with self.assertRaises(DeliveryError):merge_ready(state,'a'*40,'b'*40,[requirement],[])
        state['checkRuns'][0]['app']['id']=42
        merge_ready(state,'a'*40,'b'*40,[requirement],[])
        state['checkRuns'][0]['head_sha']='c'*40
        with self.assertRaises(DeliveryError):merge_ready(state,'a'*40,'b'*40,[requirement],[])

    def test_required_check_discovery_preserves_producer_identity(self):
        client=GitHub('fixture/repository')
        rules=[{'type':'required_status_checks','parameters':{'required_status_checks':[{'context':'test','integration_id':42}]}}]
        protection={'required_status_checks':{'contexts':['build'],'checks':[{'context':'build','app_id':7}]}}
        with patch.object(client,'api',side_effect=[rules,{'protected':True},protection]):
            self.assertEqual([{'context':'build','app_id':7},{'context':'test','app_id':42}],client.required_checks('main'))

    def test_findings_added_after_final_pr_read_block_merge(self):
        client=GitHub('fixture/repository');state=self.state()
        with patch.object(client,'pr',return_value=state), patch.object(client,'required_checks',return_value=[]), \
             patch.object(client,'threads',side_effect=[[],[{'id':'new','isResolved':False,'isOutdated':False}]]), \
             patch('harness.delivery.command') as sent:
            with self.assertRaises(DeliveryError):client.merge(1,'a'*40,'b'*40)
            sent.assert_not_called()

    def test_current_check_or_policy_discovery_failure_never_mutates(self):
        client = GitHub('fixture/repository'); state = self.state()
        with patch.object(client, 'pr', return_value=state), \
             patch.object(client, 'required_checks', side_effect=DeliveryError('discovery unavailable')), \
             patch('harness.delivery.command') as sent:
            with self.assertRaisesRegex(DeliveryError, 'discovery unavailable'):
                client.merge(1, 'a' * 40, 'b' * 40)
            sent.assert_not_called()

        failed = state | {'statusCheckRollup': [{'name': 'test', 'conclusion': 'FAILURE'}]}
        with patch.object(client, 'pr', return_value=failed), \
             patch.object(client, 'required_checks', return_value=['test']), \
             patch.object(client, 'threads', return_value=[]), \
             patch('harness.delivery.command') as sent:
            with self.assertRaises(DeliveryError): client.merge(1, 'a' * 40, 'b' * 40)
            sent.assert_not_called()

    def test_issue_marker_is_stable_and_english(self):
        body=issue_body('task-1','Expected result',['Run the fixture'])
        self.assertIn('<!-- agents-local-task:task-1 -->',body)
        with self.assertRaises(DeliveryError):issue_body('task-1','日本語',['Run'])

    def test_public_git_changes_checks_unpublished_commit_and_diff(self):
        branch = self.root / 'task'
        git(self.root, 'clone', str(self.repo), str(branch))
        git(branch, 'config', 'user.name', 'Fixture')
        git(branch, 'config', 'user.email', 'fixture@example.invalid')
        (branch / 'public').write_text('relative fixture')
        git(branch, 'add', 'public'); git(branch, 'commit', '-m', 'Document the fixture')
        head = git(branch, 'rev-parse', 'HEAD')
        self.assertIn('relative fixture', public_git_changes(branch, self.base, head))
        (branch / 'leak').write_text('/home/alice')
        git(branch, 'add', 'leak'); git(branch, 'commit', '-m', 'Add private path')
        leaked = git(branch, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(DeliveryError, 'home path'):
            public_git_changes(branch, self.base, leaked)
        (branch / 'clean').write_text('relative')
        git(branch, 'add', 'clean'); git(branch, 'commit', '-m', 'Token ghp_' + 'x' * 30)
        history_leak = git(branch, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(DeliveryError, 'Secret'):
            public_git_changes(branch, self.base, history_leak)

    def test_public_git_changes_checks_leaks_removed_in_later_commits(self):
        leak = self.repo / 'leak'
        leak.write_text('/' + 'home' + '/fixture-user/private')
        git(self.repo, 'add', 'leak')
        git(self.repo, 'commit', '-m', 'First revision')
        git(self.repo, 'rm', 'leak')
        git(self.repo, 'commit', '-m', 'Remove temporary file')
        head = git(self.repo, 'rev-parse', 'HEAD')
        self.assertEqual('', git(self.repo, 'diff', self.base, head))
        with self.assertRaisesRegex(DeliveryError, 'home path'):
            public_git_changes(self.repo, self.base, head)

    def test_push_branch_uses_exact_oid_and_rejects_default_branch(self):
        remote = self.root / 'remote.git'; git(self.root, 'init', '--bare', str(remote))
        git(self.repo, 'remote', 'add', 'origin', str(remote))
        branch = self.root / 'task'; git(self.repo, 'switch', '-c', 'task/publish')
        (self.repo / 'published').write_text('ready')
        git(self.repo, 'add', 'published'); git(self.repo, 'commit', '-m', 'Publish fixture')
        head = git(self.repo, 'rev-parse', 'HEAD')
        result = GitHub('fixture/repository').push_branch(
            self.repo, 'task/publish', head, expected_remote=str(remote), base=self.base)
        self.assertEqual(head, result['remote_head'])
        self.assertEqual(head, git(self.repo, 'ls-remote', '--heads', 'origin', 'task/publish').split()[0])
        with self.assertRaisesRegex(DeliveryError, 'task branch'):
            GitHub('fixture/repository').push_branch(
                self.repo, 'main', self.base, expected_remote=str(remote), base=self.base)

    def test_push_branch_preserves_unrelated_dirty_files(self):
        remote = self.root / 'remote.git'; git(self.root, 'init', '--bare', str(remote))
        git(self.repo, 'remote', 'add', 'origin', str(remote)); git(self.repo, 'switch', '-c', 'task/dirty')
        (self.repo / 'published').write_text('ready')
        git(self.repo, 'add', 'published'); git(self.repo, 'commit', '-m', 'Publish fixture')
        (self.repo / 'other-task').write_text('keep')
        head = git(self.repo, 'rev-parse', 'HEAD')
        result = GitHub('fixture/repository').push_branch(
            self.repo, 'task/dirty', head, expected_remote=str(remote), base=self.base)
        self.assertEqual(['?? other-task'], result['unrelated_dirty'])
        self.assertEqual('keep', (self.repo / 'other-task').read_text())

    def test_push_branch_rejects_committed_out_of_scope_path(self):
        remote = self.root / 'remote.git'; git(self.root, 'init', '--bare', str(remote))
        git(self.repo, 'remote', 'add', 'origin', str(remote)); git(self.repo, 'switch', '-c', 'task/scope')
        (self.repo / 'unexpected').write_text('outside')
        git(self.repo, 'add', 'unexpected'); git(self.repo, 'commit', '-m', 'Outside selected scope')
        head = git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(DeliveryError, 'out-of-scope'):
            GitHub('fixture/repository').push_branch(self.repo, 'task/scope', head,
                                                     expected_remote=str(remote), base=self.base,
                                                     allowed_paths=['harness'])

    def test_push_branch_rejects_changed_remote_before_mutation(self):
        remote = self.root / 'remote.git'; other = self.root / 'other.git'
        git(self.root, 'init', '--bare', str(remote)); git(self.root, 'init', '--bare', str(other))
        git(self.repo, 'remote', 'add', 'origin', str(remote)); git(self.repo, 'switch', '-c', 'task/remote')
        (self.repo / 'published').write_text('ready')
        git(self.repo, 'add', 'published'); git(self.repo, 'commit', '-m', 'Publish fixture')
        head = git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(DeliveryError, 'destination changed'):
            GitHub('fixture/repository').push_branch(self.repo, 'task/remote', head,
                                                     expected_remote=str(other), base=self.base)
        self.assertEqual('', git(self.repo, 'ls-remote', '--heads', 'origin', 'task/remote'))

    def test_create_or_reuse_pr_reconciles_lost_create_without_duplicate(self):
        client = GitHub('fixture/repository')
        pr = {'number': 9, 'url': 'https://example.invalid/pr/9', 'state': 'OPEN',
              'baseRefName': 'main', 'headRefName': 'task/publish',
              'headRefOid': 'a' * 40, 'title': 'English title', 'body': 'English body'}
        calls = []
        def fake(argv, cwd=None):
            calls.append(argv)
            if argv[:3] == ['gh', 'pr', 'list']:
                return '[]' if len([x for x in calls if x[:3] == ['gh', 'pr', 'list']]) == 1 else json.dumps([pr])
            if argv[:3] == ['gh', 'pr', 'create']:
                raise DeliveryError('transport response lost')
            raise AssertionError(argv)
        with patch('harness.delivery.command', side_effect=fake):
            result = client.create_or_reuse_pr(
                'task/publish', 'main', 'English title', 'English body', head_oid='a' * 40)
        self.assertEqual(pr, result)
        self.assertEqual(1, len([x for x in calls if x[:3] == ['gh', 'pr', 'create']]))

    def test_stale_empty_listing_after_create_is_uncertain_and_never_retried(self):
        client = GitHub('fixture/repository'); calls = []
        def fake(argv, cwd=None):
            calls.append(argv)
            if argv[:3] == ['gh', 'pr', 'list']: return '[]'
            if argv[:3] == ['gh', 'pr', 'create']: return 'https://example.invalid/pr/10'
            raise AssertionError(argv)
        with patch('harness.delivery.command', side_effect=fake), \
             self.assertRaisesRegex(DeliveryError, 'outcome unknown'):
            client.create_or_reuse_pr(
                'task/publish', 'main', 'English title', 'English body', head_oid='a' * 40)
        self.assertEqual(1, len([x for x in calls if x[:3] == ['gh', 'pr', 'create']]))

    def test_create_or_reuse_pr_rejects_stale_head(self):
        client = GitHub('fixture/repository')
        pr = {'number': 9, 'url': 'https://example.invalid/pr/9', 'state': 'OPEN',
              'baseRefName': 'main', 'headRefName': 'task/publish',
              'headRefOid': 'c' * 40, 'title': 'English title', 'body': 'English body'}
        with patch.object(client, 'list_prs', return_value=[pr]), \
             self.assertRaisesRegex(DeliveryError, 'head readback mismatch'):
            client.create_or_reuse_pr('task/publish', 'main', 'English title', 'English body',
                                      head_oid='a' * 40)

    def test_merge_lost_response_reads_back_merged_pr(self):
        client = GitHub('fixture/repository'); state = self.state()
        merged = state | {'state': 'MERGED', 'mergeCommit': {'oid': 'd' * 40}}
        with patch.object(client, 'pr', side_effect=[state, state, merged]), \
             patch.object(client, 'required_checks', return_value=[]), \
             patch.object(client, 'threads', side_effect=[[], []]), \
             patch('harness.delivery.command', side_effect=[DeliveryError('response lost')]):
            self.assertEqual(merged, client.merge(1, 'a' * 40, 'b' * 40))


if __name__=='__main__':unittest.main()
