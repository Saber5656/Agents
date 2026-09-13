import json
import sys
import tempfile
import unittest
import errno
import hashlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import daily_it_news_batch as batch

class BatchTests(unittest.TestCase):
    def test_advisory_input_retries_cloud_placeholder_and_is_digest_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            summary = root/'SUMMARY-IT-NEWS-2026-09-14.md'
            content = b'verified news'
            expected = hashlib.sha256(content).hexdigest()
            with patch.object(Path, 'read_bytes', side_effect=[b'', OSError(errno.EDEADLK, 'cloud busy'), content]):
                path = batch.snapshot_advisory_input(summary, root/'run', expected)
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.name, summary.name)
            self.assertFalse(path.stat().st_mode & 0o222)

    def test_advisory_input_rejects_persistent_mismatch_without_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            summary = root/'SUMMARY-IT-NEWS-2026-09-14.md'
            summary.write_text('different news')
            with self.assertRaisesRegex(batch.news.RunnerError, 'verified summary'):
                batch.snapshot_advisory_input(summary, root/'run', '0'*64)
            self.assertFalse((root/'run/advisory-input'/summary.name).exists())

    def test_generated_advice_reads_verified_local_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = batch.news.Config(root,root,root,root,root,root,'codex')
            cloud = root/'cloud';cloud.mkdir()
            summary = cloud/'SUMMARY-IT-NEWS-2026-09-14.md';summary.write_text('verified news')
            expected = batch.news.digest(summary)
            run = root/'run';run.mkdir()
            def generate(runner, command, run_root, name, **kwargs):
                prompt = kwargs['input']
                snapshot = run/'advisory-input'/summary.name
                self.assertIn(str(snapshot), prompt)
                self.assertNotIn(str(summary), prompt)
                self.assertEqual(batch.news.digest(snapshot), expected)
                stage = kwargs['cwd']
                output = stage/'Personal-Vulnerability-Advisory.md'
                output.write_text(f'{summary.name} (same-run SHA-256: {expected})\n棚卸し日時\n'
                                  '## 結論\n## 推奨対応\n## 対象外 / 情報不足\n## Audit Notes\n')
                (stage/'result.json').write_text(json.dumps({'status':'created','advisory_path':str(output)}))
            with patch.object(batch.news, 'logged_command', side_effect=generate):
                advice = batch.generate_advisory(cfg, summary, run, expected)
            self.assertTrue(advice.is_file())
            self.assertEqual(json.loads((run/'advisory-input.json').read_text())['sha256'], expected)

    def test_delivery_is_required_for_batch_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = batch.news.Config(root,root,root,root,root,root,'codex')
            summary = root/'SUMMARY-IT-NEWS-2026-09-10.md';summary.write_text('news')
            advisory = root/'advisory.md';advisory.write_text('advice')
            with patch.object(batch.news,'run',return_value={'status':'already_complete','summary_path':str(summary),'summary_sha256':batch.news.digest(summary)}), patch.object(batch,'generate_advisory',return_value=advisory), patch.object(batch,'complete_delivery',return_value={'delivery_status':'unknown'}):
                result=batch.run_batch(cfg,{})
            self.assertEqual(result['status'],'blocked')
            self.assertEqual(result['phase'],'discord')

    def test_published_and_delivered_batch_is_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cfg=batch.news.Config(root,root,root,root,root,root,'codex')
            summary=root/'SUMMARY-IT-NEWS-2026-09-10.md';summary.write_text('news')
            advisory=root/'advisory.md';advisory.write_text('advice')
            with patch.object(batch.news,'run',return_value={'status':'already_complete','summary_path':str(summary),'summary_sha256':batch.news.digest(summary)}), patch.object(batch,'generate_advisory',return_value=advisory), patch.object(batch,'complete_delivery',return_value={'delivery_status':'delivered','message_id':'1234567890123456789'}):
                result=batch.run_batch(cfg,{})
            self.assertEqual(result['status'],'complete')
            self.assertEqual(result['delivery']['message_id'],'1234567890123456789')
            self.assertEqual(json.loads((root/'last-status.json').read_text())['status'],'complete')

if __name__=='__main__':unittest.main()
