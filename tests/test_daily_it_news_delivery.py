import json
import os
import subprocess
import sys
import tempfile
import unittest
import hashlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import daily_it_news_delivery as delivery


def git(cwd, *args):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        work = root / "seed"
        subprocess.run(["git", "clone", str(self.remote), str(work)], check=True, capture_output=True)
        git(work, "switch", "-c", "main")
        git(work, "config", "user.name", "Fixture")
        git(work, "config", "user.email", "fixture@example.invalid")
        (work / "README").write_text("seed")
        git(work, "add", "README"); git(work, "commit", "-m", "seed"); git(work, "push", "origin", "main")
        self.artifact = root / "summary.md"; self.artifact.write_text("news")
        self.run = root / "run"
        self.gitleaks = root / "gitleaks"
        self.gitleaks.write_text("#!/bin/sh\nexit 0\n")
        self.gitleaks.chmod(0o755)

    def test_publish_and_idempotent_blob(self):
        first = delivery.publish_artifact(str(self.remote), "main", self.artifact, "daily/summary.md", self.run, "Fixture", "fixture@example.invalid", str(self.gitleaks))
        self.assertEqual("published", first["status"])
        second = delivery.publish_artifact(str(self.remote), "main", self.artifact, "daily/summary.md", self.run, "Fixture", "fixture@example.invalid", str(self.gitleaks))
        self.assertEqual("already_present", second["status"])
        self.assertEqual(first["artifact_sha256"], second["artifact_sha256"])

    def test_publish_preserves_markdown_with_blank_line_at_eof(self):
        content = b"# Daily news\n\nVerified article.\n\n"
        self.artifact.write_bytes(content)
        result = delivery.publish_artifact(str(self.remote), "main", self.artifact,
            "daily/summary.md", self.run, "Fixture", "fixture@example.invalid", str(self.gitleaks))
        self.assertEqual("published", result["status"])
        published = subprocess.check_output(["git", "show", "main:daily/summary.md"], cwd=self.remote)
        self.assertEqual(content, published)
        self.assertEqual(content, self.artifact.read_bytes())

    def test_trailing_whitespace_still_blocks_with_stdout_diagnostic(self):
        self.artifact.write_text("# News\n\nUnintended whitespace \n")
        before = git(self.remote, "rev-parse", "main")
        with self.assertRaisesRegex(delivery.DeliveryError, "trailing whitespace"):
            delivery.publish_artifact(str(self.remote), "main", self.artifact,
                "daily/summary.md", self.run, "Fixture", "fixture@example.invalid", str(self.gitleaks))
        self.assertEqual(before, git(self.remote, "rev-parse", "main"))
        failure = json.loads((self.run / "publish-failure.json").read_text())
        self.assertIn("trailing whitespace", failure["error"])

    def test_custom_whitespace_checks_are_preserved(self):
        config = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.whitespace",
                  "GIT_CONFIG_VALUE_0": "blank-at-eof,tab-in-indent"}
        with patch.dict(os.environ, config):
            self.artifact.write_text("# News\n\n")
            result = delivery.publish_artifact(str(self.remote), "main", self.artifact,
                "daily/summary.md", self.run, "Fixture", "fixture@example.invalid", str(self.gitleaks))
            self.assertEqual("published", result["status"])
            before = git(self.remote, "rev-parse", "main")
            self.artifact.write_text("# News\n\tIndented line\n")
            with self.assertRaisesRegex(delivery.DeliveryError, "tab in indent"):
                delivery.publish_artifact(str(self.remote), "main", self.artifact,
                    "daily/summary.md", self.run, "Fixture", "fixture@example.invalid", str(self.gitleaks))
            self.assertEqual(before, git(self.remote, "rev-parse", "main"))
            self.assertEqual("blank-at-eof,tab-in-indent", git(self.remote, "config", "--get", "core.whitespace"))

    def test_notification_records_intent_and_parses_bridge(self):
        bridge = Path(self.tmp.name) / "bridge.py"
        bridge.write_text("import json; print(json.dumps({'success':True,'platform':'discord','chat_id':'12345678901234567','message_id':'22345678901234567'}))")
        out = delivery.notify_discord("2026-09-10", "https://news", "https://advice", "a" * 64, self.run, sys.executable, "discord:12345678901234567", "req-1", bridge)
        self.assertEqual({"delivery_status": "delivered", "message_id": "22345678901234567"}, out)
        self.assertTrue((self.run / "discord-bridge").glob("intent-*.json").__iter__().__next__())

    def test_multiline_wrapper_and_uncertain_response(self):
        bridge = Path(self.tmp.name) / "wrapped.py"
        bridge.write_text("import json; print(json.dumps({'status':'completed','stdout':json.dumps({'success':True,'platform':'discord','chat_id':'12345678901234567','message_id':'22345678901234567'})},indent=2))")
        out=delivery.notify_discord("2026-09-10","https://news","https://advice","b"*64,self.run,sys.executable,"discord:12345678901234567","req-2",bridge)
        self.assertEqual(out['delivery_status'],'delivered')
        bridge.write_text("print('unexpected response')")
        out=delivery.notify_discord("2026-09-10","https://news","https://advice","c"*64,self.run,sys.executable,"discord:12345678901234567","req-3",bridge)
        self.assertEqual(out['delivery_status'],'unknown')
        bridge.write_text("raise RuntimeError('must not resend')")
        retry=delivery.notify_discord("2026-09-10","https://news","https://advice","c"*64,self.run,sys.executable,"discord:12345678901234567","req-3",bridge)
        self.assertEqual(retry['error'],'existing intent requires reconciliation')

    def test_unsafe_path_rejected(self):
        with self.assertRaises(delivery.DeliveryError):
            delivery.publish_artifact(str(self.remote), "main", self.artifact, "../secret", self.run, "x", "x@y", "")

    def test_publication_uses_the_same_bytes_that_were_verified(self):
        content = self.artifact.read_bytes()
        original = delivery.shutil.copyfile
        def cloud_read(source, destination, *args, **kwargs):
            if Path(source) == self.artifact:
                Path(destination).write_bytes(b'')
                return str(destination)
            return original(source, destination, *args, **kwargs)
        with patch.object(delivery.shutil, 'copyfile', side_effect=cloud_read):
            result = delivery.publish_artifact(str(self.remote), 'main', self.artifact,
                'daily/summary.md', self.run, 'Fixture', 'fixture@example.invalid', str(self.gitleaks))
        self.assertEqual(result['artifact_sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(content, subprocess.check_output(['git', 'show', 'main:daily/summary.md'], cwd=self.remote))

    def test_transient_fetch_failure_retries_without_changing_payload(self):
        original = delivery._git
        failed = []
        def flaky(args, *a, **kw):
            if args[0] == 'fetch' and not failed:
                failed.append(True)
                raise delivery.RetryableDeliveryError('temporary connection failure')
            return original(args, *a, **kw)
        with patch.object(delivery, '_git', side_effect=flaky):
            result = delivery.publish_artifact(str(self.remote), 'main', self.artifact,
                'daily/summary.md', self.run, 'Fixture', 'fixture@example.invalid', str(self.gitleaks))
        self.assertEqual('published', result['status'])
        self.assertEqual(1, len(failed))

    def test_unrelated_remote_advance_after_push_does_not_block_delivery(self):
        original = delivery._git
        def advance(args, *a, **kw):
            value = original(args, *a, **kw)
            if args[0] == 'push':
                seed = Path(self.tmp.name)/'seed'
                git(seed, 'fetch', 'origin', 'main');git(seed, 'reset', '--hard', 'origin/main')
                (seed/'README').write_text('independent update')
                git(seed, 'add', 'README');git(seed, 'commit', '-m', 'independent update');git(seed, 'push', 'origin', 'main')
            return value
        with patch.object(delivery, '_git', side_effect=advance):
            result = delivery.publish_artifact(str(self.remote), 'main', self.artifact,
                'daily/summary.md', self.run, 'Fixture', 'fixture@example.invalid', str(self.gitleaks))
        self.assertEqual('published', result['status'])
        self.assertEqual('independent update', git(self.remote, 'show', 'main:README'))

    def test_process_start_failure_is_retryable_without_an_unknown_intent(self):
        args = ('2026-09-14', 'https://news', 'https://advice', 'e'*64, self.run)
        bridge = Path(self.tmp.name)/'send.py'
        bridge.write_text("import json;print(json.dumps({'success':True,'platform':'discord','chat_id':'12345678901234567','message_id':'22345678901234567'}))")
        first = delivery.notify_discord(*args, '/does/not/exist', 'discord:12345678901234567', 'req-start', bridge)
        self.assertEqual('failed', first['delivery_status'])
        second = delivery.notify_discord(*args, sys.executable, 'discord:12345678901234567', 'req-start', bridge)
        self.assertEqual('delivered', second['delivery_status'])

    def test_confirmed_bridge_receipt_recovers_without_resending(self):
        target = 'discord:12345678901234567';artifact = 'f'*64
        key = hashlib.sha256(f'{artifact}:{target}'.encode()).hexdigest()
        base = self.run/'discord-bridge';base.mkdir(parents=True)
        message = 'ITニュース 2026-09-14\nニュース: https://news\n助言: https://advice'
        (base/f'intent-{key}.json').write_text(json.dumps({'artifact_sha256':artifact,'target':target,'message':message}))
        message_file = base/f'message-{key}.txt';message_file.write_text(message)
        attempt = base/'req-recover/attempt-test';attempt.mkdir(parents=True)
        (attempt/'request.json').write_text(json.dumps({'request_id':'req-recover','command':['hermes','send','--to',target,'--json','--file',str(message_file)]}))
        payload = {'success':True,'platform':'discord','chat_id':'12345678901234567','message_id':'22345678901234567'}
        (attempt/'result.json').write_text(json.dumps({'request_id':'req-recover','returncode':0,'status':'completed','stdout':json.dumps(payload)}))
        with patch.object(delivery, 'run_command', side_effect=AssertionError('must not resend')):
            result = delivery.notify_discord('2026-09-14','https://news','https://advice',artifact,self.run,sys.executable,target,'req-recover','bridge.py')
        self.assertEqual('delivered', result['delivery_status'])
        self.assertEqual(payload['message_id'], result['message_id'])
        self.assertTrue((base/f'sent-{key}.json').is_file())

    def test_invalid_sent_receipt_is_not_success(self):
        target = 'discord:12345678901234567';artifact = '9'*64
        key = hashlib.sha256(f'{artifact}:{target}'.encode()).hexdigest()
        base = self.run/'discord-bridge';base.mkdir(parents=True)
        (base/f'sent-{key}.json').write_text('{}')
        result = delivery.notify_discord('2026-09-14','https://news','https://advice',artifact,self.run,sys.executable,target,'req-invalid','bridge.py')
        self.assertEqual('unknown', result['delivery_status'])


if __name__ == "__main__":
    unittest.main()
