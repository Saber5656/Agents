import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from harness.delivery import public_text, prepare_worktree, sync_main, merge_ready, DeliveryError, issue_body, GitHub


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

    def test_sync_only_clean_main_and_contains_merge(self):
        remote=self.root/'remote.git';git(self.root,'init','--bare',str(remote))
        git(self.repo,'remote','add','origin',str(remote));git(self.repo,'push','origin','main')
        self.assertEqual(self.base,sync_main(self.repo,'main',self.base,remote=str(remote)))
        (self.repo/'a').write_text('local')
        with self.assertRaisesRegex(DeliveryError,'dirty'):
            sync_main(self.repo,'main',self.base,remote=str(remote))
        self.assertEqual('local',(self.repo/'a').read_text())

    def test_privacy_before_transmission(self):
        for text in ['fixture '+'/'.join(['','Users','example','private']), 'token '+('ghp_'+'x'*30), '日本語公開本文']:
            with self.assertRaises(DeliveryError):public_text(text,english=True)
        with self.assertRaises(DeliveryError):public_text('secret-value',env={'API_KEY':'secret-value'})
        self.assertEqual('Use $AGENTS_ROOT.',public_text('Use $AGENTS_ROOT.',english=True))

    def state(self):
        return {'number':1,'state':'OPEN','isDraft':False,'headRefOid':'a'*40,'baseRefOid':'b'*40,
            'reviewDecision':'','mergeable':'MERGEABLE','statusCheckRollup':[
                {'name':'test','status':'COMPLETED','conclusion':'SUCCESS'}]}

    def test_merge_checks_head_base_and_findings(self):
        s=self.state();merge_ready(s,'a'*40,'b'*40,['test'],[])
        for change in [{'headRefOid':'c'*40},{'baseRefOid':'c'*40},{'reviewDecision':'CHANGES_REQUESTED'},
                       {'statusCheckRollup':[]},{'statusCheckRollup':[{'name':'test','status':'COMPLETED','conclusion':'FAILURE'}]}]:
            with self.assertRaises(DeliveryError):merge_ready(s|change,'a'*40,'b'*40,['test'],[])
        with self.assertRaises(DeliveryError):merge_ready(s,'a'*40,'b'*40,['test'],[{'isResolved':False,'isOutdated':False}])
        with self.assertRaises(DeliveryError):merge_ready(s,'a'*40,'b'*40,None,[])

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


    def test_issue_marker_is_stable_and_english(self):
        body=issue_body('task-1','Expected result',['Run the fixture'])
        self.assertIn('<!-- agents-local-task:task-1 -->',body)
        with self.assertRaises(DeliveryError):issue_body('task-1','日本語',['Run'])


if __name__=='__main__':unittest.main()
