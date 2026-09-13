import json
import os
import subprocess
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
