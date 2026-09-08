import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FAKE_PROVIDER = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

prompt = sys.stdin.read()
Path(os.environ["FAKE_REVIEW_LOG"]).write_text(
    json.dumps({"argv": sys.argv[1:], "prompt": prompt}), encoding="utf-8"
)
review = os.environ.get("FAKE_REVIEW", json.dumps({
    "verdict": "request_changes",
    "findings": [{
        "severity": "high",
        "file": "fixture.py:3",
        "issue": "The fixture has a defect.",
        "evidence": ["fixture diff shows the failing branch"],
    }],
    "limitations": [],
}))
if os.environ.get("FAKE_MODE") == "malformed":
    review = '{"verdict":"approve","findings":[]}'
if Path(sys.argv[0]).name == "claude":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": review}))
else:
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": review}}))
    print(json.dumps({"type": "turn.completed", "model": "gpt-5.6-luna", "usage": {"input_tokens": 1}}))
'''


class ReviewEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.vault = self.root / "vault"
        self.bin = self.root / "bin"
        self.work.mkdir()
        self.vault.mkdir()
        self.bin.mkdir()
        for name in ("claude", "codex"):
            path = self.bin / name
            path.write_text(FAKE_PROVIDER, encoding="utf-8")
            path.chmod(0o700)
        self.log = self.root / "provider.json"
        self.env_file = self.root / ".env"
        self.env_file.write_text(
            f"AGENTS_ROOT={self.root}\nAGENTS_VAULT_ROOT={self.vault}\n",
            encoding="utf-8",
        )
        self.prompt = self.root / "review.md"
        self.prompt.write_text(
            "Review fixture.py read-only. Report actionable evidence; do not edit files or start agents.\n",
            encoding="utf-8",
        )

    def run_review(self, provider="claude", *, model=None, role="tech-reviewer", mode=None):
        run_dir = self.vault / f"run-{provider}-{mode or 'normal'}"
        environment = dict(os.environ)
        environment["PATH"] = f"{self.bin}{os.pathsep}{environment['PATH']}"
        environment["FAKE_REVIEW_LOG"] = str(self.log)
        if mode:
            environment["FAKE_MODE"] = mode
        command = [
            sys.executable, "-m", "harness.runner", "--environment", "current",
            "--env-file", str(self.env_file), "review", "--workspace", str(self.work),
            "--prompt-file", str(self.prompt), "--role", role, "--provider", provider,
            "--effort", "low", "--no-fallback", "--run-dir", str(run_dir),
        ]
        if provider == "claude":
            command.extend(["--claude-model", "sonnet"])
        else:
            command.extend(["--codex-model", model or "gpt-5.6-luna"])
        result = subprocess.run(command, cwd=ROOT, env=environment, text=True,
                                capture_output=True, timeout=30)
        return result, json.loads(result.stdout)

    def test_claude_cli_review_is_read_only_and_returns_evidence_finding(self):
        result, summary = self.run_review()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(summary["status"], "review_findings")
        invocation = json.loads(self.log.read_text(encoding="utf-8"))
        argv = invocation["argv"]
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Grep,Glob")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Grep,Glob")
        self.assertNotIn("Write", argv)
        self.assertNotIn("Edit", argv)
        self.assertNotIn("Bash", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertIn('"evidence"', invocation["prompt"])
        self.assertIn("追加エージェント", invocation["prompt"])

    def test_codex_cli_review_binds_model_readonly_and_disables_extra_agents(self):
        result, summary = self.run_review(provider="codex", model="gpt-5.6-luna")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(summary["status"], "review_findings")
        invocation = json.loads(self.log.read_text(encoding="utf-8"))
        argv = invocation["argv"]
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-5.6-luna")
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")
        disabled = {argv[i + 1] for i, item in enumerate(argv[:-1]) if item == "--disable"}
        self.assertIn("multi_agent", disabled)
        self.assertIn("apps", disabled)
        self.assertNotIn("--add-dir", argv)
        self.assertEqual(argv[-1], "-")

    def test_malformed_provider_result_is_incomplete_not_approval(self):
        result, summary = self.run_review(mode="malformed")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(summary["status"], "review_incomplete")

    def test_canonical_entrypoints_share_owner_scope_and_result_contract(self):
        for name in ("review", "review-bugbot", "review-security"):
            text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("python -m harness.runner review", text)
            self.assertIn("read-only", text)
            self.assertIn("verdict", text)
            self.assertIn("findings", text)
            self.assertIn("limitations", text)
            self.assertIn("evidence", text)
            self.assertNotIn("AskQuestion", text)
            self.assertNotIn("cursor_dialog", text)
            self.assertNotIn("SwitchMode", text)


if __name__ == "__main__":
    unittest.main()
