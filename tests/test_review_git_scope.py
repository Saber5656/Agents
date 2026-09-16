import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness import runner as h


def event(**data):
    return json.dumps(data) + '\n'


class ReviewGitTests(unittest.TestCase):
    def test_vault_review_skips_snapshots_and_disables_claude_git_prefetch_only_for_child(self):
        with tempfile.TemporaryDirectory() as temp:
            vault=Path(temp); work=vault/'notes'; work.mkdir()
            env={'PATH':os.environ['PATH']}
            response=event(type='result', subtype='success',
                           structured_output={'verdict':'approve','findings':[],'limitations':[]})
            observed=[]
            def execute(argv, child, cwd, prompt, timeout):
                observed.append(child)
                return h.ProcessResult(0,response)
            with patch.object(h,'snapshot',side_effect=AssertionError('must not scan Vault')):
                result=h.run_job(h.Job(work,vault,'Review'),env,execute)
            self.assertEqual(result['status'],'completed')
            self.assertEqual(observed[0]['CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS'],'1')
            self.assertNotIn('CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS', env)
            before=json.loads((Path(result['run_dir'])/'before.json').read_text())
            self.assertEqual(before['reason'], 'read_only_vault_review')

    def test_snapshot_scopes_status_and_diff_to_workspace(self):
        calls=[]
        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, 'ok', '')
        with patch.object(h.subprocess,'run',side_effect=run):
            h.snapshot(Path('/example/repo/subdir'))
        for argv in calls:
            self.assertIn('--no-optional-locks', argv)
            if 'status' in argv or 'diff' in argv:
                self.assertEqual(argv[-2:], ['--','.'])
