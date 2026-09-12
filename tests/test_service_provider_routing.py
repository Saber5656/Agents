"""Ordinary worker and acceptance review default to Claude sonnet/low first.

Codex gpt-5.6-luna/low remains the automatic, quota-only fallback so the
separate Codex subscription is preserved for cases Claude cannot serve. An
explicitly selected Codex model keeps its original single-provider semantics,
and a legacy job (no persisted ``provider`` column) preserves its original
Codex-only routing across a restart/resume.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from harness.tasks import TaskStore
from harness.service import ServiceStore, default_executor, default_verifier, AuthError

# Verified against actual local CLI output (``claude auth status --json`` while
# logged into Claude Pro): a saved API key also reports ``loggedIn: true`` but
# not this authMethod/apiProvider/subscriptionType combination.
CLAUDE_PRO_LOGIN_JSON = json.dumps({
    "loggedIn": True, "authMethod": "claude.ai",
    "apiProvider": "firstParty", "subscriptionType": "pro",
})
CODEX_CHATGPT_LOGIN_TEXT = "Logged in using ChatGPT"


def _login_side_effect(argv, **kwargs):
    if argv[0] == "claude":
        return mock.Mock(returncode=0, stdout=CLAUDE_PRO_LOGIN_JSON, stderr="")
    return mock.Mock(returncode=0, stdout=CODEX_CHATGPT_LOGIN_TEXT, stderr="")


class ProviderRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"; self.vault.mkdir()
        self.workspace = self.root / "workspace"; self.workspace.mkdir()
        self.env = {"AGENTS_ROOT": str(self.root), "AGENTS_VAULT_ROOT": str(self.vault)}
        self.patcher = mock.patch.dict(os.environ, self.env, clear=False); self.patcher.start()
        self.addCleanup(self.patcher.stop); self.addCleanup(self.tmp.cleanup)
        self.tasks = TaskStore(self.root / "tasks.sqlite3")
        self.task = self.tasks.create_task(purpose="scheduled work", repository="org/repo")
        self.service = ServiceStore(self.root / "service.sqlite3", self.tasks)
        self.addCleanup(self.tasks.close); self.addCleanup(self.service.close)

    # -- ordinary worker (default_executor) ------------------------------

    def test_new_enrollment_defaults_to_claude_provider(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        self.assertEqual(job["provider"], "claude")
        self.assertEqual(job["model"], "gpt-5.6-luna")

    def test_run_once_routes_default_executor_spec_with_job_provider(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        seen = []
        def executor(spec):
            seen.append(spec)
            return {"status": "completed"}
        self.service.run_once(executor=executor)
        self.assertEqual(seen[0]["provider"], "claude")

    def test_default_executor_uses_claude_sonnet_low_first_with_codex_fallback_model(self):
        spec = {"workspace": str(self.workspace), "agents_root": str(self.root),
                "vault_root": str(self.vault), "run_dir": str(self.vault / "run"),
                "prompt": "prompt", "context": "context", "model": "gpt-5.6-luna",
                "effort": "low", "timeout": 1, "updates": [], "provider": "claude"}
        with mock.patch("harness.runner.run_job", return_value={"status": "completed"}) as run:
            default_executor(spec)
        job = run.call_args.args[0]
        self.assertEqual(job.provider, "claude")
        self.assertEqual(job.claude_model, "sonnet")
        self.assertEqual(job.codex_model, "gpt-5.6-luna")
        self.assertEqual(job.effort, "low")
        self.assertTrue(job.fallback)

    def test_explicit_codex_provider_selection_keeps_single_provider_no_fallback(self):
        spec = {"workspace": str(self.workspace), "agents_root": str(self.root),
                "vault_root": str(self.vault), "run_dir": str(self.vault / "run"),
                "prompt": "prompt", "context": "context", "model": "gpt-5.6-codex-explicit",
                "effort": "low", "timeout": 1, "updates": [], "provider": "codex"}
        with mock.patch("harness.runner.run_job", return_value={"status": "completed"}) as run:
            default_executor(spec)
        job = run.call_args.args[0]
        self.assertEqual(job.provider, "codex")
        self.assertEqual(job.codex_model, "gpt-5.6-codex-explicit")
        self.assertFalse(job.fallback)

    def test_direct_call_without_provider_key_preserves_legacy_codex_only_default(self):
        """A caller that never adopted the new field keeps the original behavior."""
        spec = {"workspace": str(self.workspace), "agents_root": str(self.root),
                "vault_root": str(self.vault), "run_dir": str(self.vault / "run"),
                "prompt": "prompt", "context": "context", "model": "gpt-5.6-luna",
                "effort": "low", "timeout": 1, "updates": []}
        with mock.patch("harness.runner.run_job", return_value={"status": "completed"}) as run:
            default_executor(spec)
        job = run.call_args.args[0]
        self.assertEqual(job.provider, "codex")
        self.assertFalse(job.fallback)

    def test_legacy_persisted_job_without_provider_column_keeps_codex_routing(self):
        """A restart migrates a pre-existing database without silently
        switching an already scheduled job onto the new default provider."""
        import sqlite3
        from harness.service import SCHEMA
        legacy_db = self.root / "legacy-service.sqlite3"
        conn = sqlite3.connect(legacy_db)
        conn.executescript(SCHEMA)
        stamp = "2020-01-01T00:00:00+00:00"
        run_dir = self.vault / "01-Projects" / "agent-runs" / "service-job_legacy"
        conn.execute(
            "INSERT INTO service_jobs (id,task_id,workspace,run_dir,resource,prompt,context,model,"
            "effort,timeout,retry_base,retry_max,state,created_at,updated_at) VALUES "
            "('job_legacy',?,?,?,'default','prompt','context','gpt-5.6-luna','low',300,30,3600,"
            "'pending',?,?)",
            (self.task["id"], str(self.workspace), str(run_dir), stamp, stamp))
        conn.commit(); conn.close()
        migrated_service = ServiceStore(legacy_db, self.tasks)
        self.addCleanup(migrated_service.close)
        migrated = migrated_service.get_job("job_legacy")
        self.assertEqual(migrated["provider"], "codex")
        seen = []
        migrated_service.run_once(executor=lambda spec: (seen.append(spec) or {"status": "completed"}))
        self.assertEqual(seen[0]["provider"], "codex")

    def test_worker_auth_preflight_checks_claude_login_for_claude_provider_job(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        with mock.patch("harness.service.subprocess.run", side_effect=_login_side_effect) as run, \
             mock.patch("harness.service.default_executor", return_value={"status": "completed"}):
            result = self.service.run_once()
        self.assertEqual(result["status"], "needs_verification")
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "claude")

    def test_worker_auth_preflight_does_not_fall_back_to_codex_on_claude_auth_failure(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        with mock.patch("harness.service.subprocess.run",
                        return_value=mock.Mock(returncode=1, stdout="", stderr="not logged in")), \
             mock.patch("harness.service.default_executor") as executor:
            result = self.service.run_once()
        self.assertEqual(result["status"], "retry")
        self.assertIn("not authenticated", self.service.get_job(job["id"])["last_error"])
        executor.assert_not_called()

    # -- acceptance review (default_verifier) ----------------------------

    def test_verifier_attempts_claude_sonnet_low_before_codex(self):
        task = self.tasks.create_task(purpose="verify", acceptance_evidence=["A check"])
        self.tasks.add_acceptance_evidence(task["id"], "A check", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        claude_verdict = json.dumps({
            "acceptance": True, "findings": [],
            "criteria": [{"criterion_id": __import__("hashlib").sha256(b"A check").hexdigest(),
                          "verified": True, "evidence": "observed"}],
            "evidence": "observed", "evidence_links": []})
        events = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "result", "subtype": "success", "is_error": False,
                       "result": claude_verdict, "usage": {"input_tokens": 1}}),
        ])
        from harness.runner import ProcessResult
        with mock.patch("harness.service.subprocess.run", side_effect=_login_side_effect), \
             mock.patch("harness.runner.execute", return_value=ProcessResult(0, events, "")) as run:
            value = default_verifier({"job": self.service.get_job(job["id"]),
                                      "task": self.tasks.get_task(task["id"]),
                                      "agents_root": str(self.root), "vault_root": str(self.vault)})
        self.assertTrue(value["acceptance"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "claude")

    def test_verifier_falls_back_to_codex_only_on_claude_quota(self):
        task = self.tasks.create_task(purpose="verify", acceptance_evidence=["A check"])
        self.tasks.add_acceptance_evidence(task["id"], "A check", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        criterion_id = __import__("hashlib").sha256(b"A check").hexdigest()
        codex_verdict = json.dumps({
            "acceptance": True, "findings": [],
            "criteria": [{"criterion_id": criterion_id, "verified": True, "evidence": "observed"}],
            "evidence": "observed", "evidence_links": []})
        claude_quota_events = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "result", "subtype": "error", "is_error": True,
                       "result": "You have hit your usage limit"}),
        ])
        codex_events = "\n".join([
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": codex_verdict}}),
            json.dumps({"type": "turn.completed", "status": "completed"}),
        ])
        from harness.runner import ProcessResult
        calls = [ProcessResult(0, claude_quota_events, ""), ProcessResult(0, codex_events, "")]
        with mock.patch("harness.service.subprocess.run", side_effect=_login_side_effect), \
             mock.patch("harness.runner.execute", side_effect=lambda *a, **k: calls.pop(0)) as run:
            value = default_verifier({"job": self.service.get_job(job["id"]),
                                      "task": self.tasks.get_task(task["id"]),
                                      "agents_root": str(self.root), "vault_root": str(self.vault)})
        self.assertTrue(value["acceptance"])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][0], "claude")
        self.assertEqual(run.call_args_list[1].args[0][0], "codex")

    def test_verifier_claude_auth_failure_does_not_fall_back_to_codex(self):
        task = self.tasks.create_task(purpose="verify", acceptance_evidence=["A check"])
        self.tasks.add_acceptance_evidence(task["id"], "A check", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        with mock.patch("harness.service.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout=json.dumps({"loggedIn": False}), stderr="")), \
             mock.patch("harness.runner.execute") as run:
            with self.assertRaises(AuthError):
                default_verifier({"job": self.service.get_job(job["id"]),
                                  "task": self.tasks.get_task(task["id"]),
                                  "agents_root": str(self.root), "vault_root": str(self.vault)})
        run.assert_not_called()

    def test_verifier_codex_fallback_requires_codex_login_before_inference(self):
        """A Claude quota status must not skip the Codex login preflight."""
        task = self.tasks.create_task(purpose="verify", acceptance_evidence=["A check"])
        self.tasks.add_acceptance_evidence(task["id"], "A check", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        claude_quota_events = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "result", "subtype": "error", "is_error": True,
                       "result": "You have hit your usage limit"}),
        ])
        from harness.runner import ProcessResult
        def login_side_effect(argv, **kwargs):
            if argv[0] == "claude":
                return mock.Mock(returncode=0, stdout=CLAUDE_PRO_LOGIN_JSON, stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="not logged in")
        with mock.patch("harness.service.subprocess.run", side_effect=login_side_effect), \
             mock.patch("harness.runner.execute",
                        return_value=ProcessResult(0, claude_quota_events, "")) as run:
            with self.assertRaises(AuthError):
                default_verifier({"job": self.service.get_job(job["id"]),
                                  "task": self.tasks.get_task(task["id"]),
                                  "agents_root": str(self.root), "vault_root": str(self.vault)})
        self.assertEqual(run.call_count, 1)

    def test_verifier_enforces_total_time_budget_across_providers(self):
        """The Claude+Codex review turns share one overall deadline, not one each."""
        task = self.tasks.create_task(purpose="verify", acceptance_evidence=["A check"])
        self.tasks.add_acceptance_evidence(task["id"], "A check", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context", timeout=10)
        claude_quota_events = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "result", "subtype": "error", "is_error": True,
                       "result": "You have hit your usage limit"}),
        ])
        from harness.runner import ProcessResult
        with mock.patch("harness.service.subprocess.run", side_effect=_login_side_effect), \
             mock.patch("harness.service.time.monotonic", side_effect=[100.0, 100.0, 500.0]), \
             mock.patch("harness.runner.execute",
                        return_value=ProcessResult(0, claude_quota_events, "")) as run:
            with self.assertRaises(AuthError) as ctx:
                default_verifier({"job": self.service.get_job(job["id"]),
                                  "task": self.tasks.get_task(task["id"]),
                                  "agents_root": str(self.root), "vault_root": str(self.vault)})
        self.assertIn("timeout", str(ctx.exception))
        self.assertEqual(run.call_count, 1)

    def test_verifier_records_provider_model_and_usage(self):
        task = self.tasks.create_task(purpose="verify", acceptance_evidence=["A check"])
        self.tasks.add_acceptance_evidence(task["id"], "A check", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        claude_verdict = json.dumps({
            "acceptance": True, "findings": [],
            "criteria": [{"criterion_id": __import__("hashlib").sha256(b"A check").hexdigest(),
                          "verified": True, "evidence": "observed"}],
            "evidence": "observed", "evidence_links": []})
        events = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "result", "subtype": "success", "is_error": False,
                       "result": claude_verdict, "usage": {"input_tokens": 7}}),
        ])
        from harness.runner import ProcessResult
        with mock.patch("harness.service.subprocess.run", side_effect=_login_side_effect), \
             mock.patch("harness.runner.execute", return_value=ProcessResult(0, events, "")):
            value = default_verifier({"job": self.service.get_job(job["id"]),
                                      "task": self.tasks.get_task(task["id"]),
                                      "agents_root": str(self.root), "vault_root": str(self.vault)})
        self.assertEqual(value["provider"], "claude")
        self.assertEqual(value["model"], "sonnet")
        self.assertEqual(value["usage"], {"input_tokens": 7})

    def test_worker_codex_fallback_requires_codex_login_before_inference(self):
        """default_executor's Claude-primary/Codex-fallback run must verify the
        Codex subscription before runner.run_job actually invokes Codex."""
        spec = {"workspace": str(self.workspace), "agents_root": str(self.root),
                "vault_root": str(self.vault), "run_dir": str(self.vault / "run"),
                "prompt": "prompt", "context": "context", "model": "gpt-5.6-luna",
                "effort": "low", "timeout": 60, "updates": [], "provider": "claude"}
        claude_quota_events = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "result", "subtype": "error", "is_error": True,
                       "result": "You have hit your usage limit"}),
        ])
        from harness.runner import ProcessResult
        def login_side_effect(argv, **kwargs):
            if argv[0] == "claude":
                return mock.Mock(returncode=0, stdout=CLAUDE_PRO_LOGIN_JSON, stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="not logged in")
        with mock.patch("harness.service.subprocess.run", side_effect=login_side_effect), \
             mock.patch("harness.runner.execute",
                        return_value=ProcessResult(0, claude_quota_events, "")) as run:
            with self.assertRaises(AuthError):
                default_executor(spec)
        self.assertEqual(run.call_count, 1)

    # -- claude/codex auth strictness --------------------------------------

    def test_claude_login_check_rejects_saved_api_key_auth(self):
        from harness.service import _claude_login_check
        api_key_login = json.dumps({"loggedIn": True, "authMethod": "api-key",
                                    "apiProvider": "firstParty", "subscriptionType": None})
        with mock.patch("harness.service.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout=api_key_login, stderr="")):
            self.assertFalse(_claude_login_check({}))
        with mock.patch("harness.service.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout=CLAUDE_PRO_LOGIN_JSON, stderr="")):
            self.assertTrue(_claude_login_check({}))

    def test_auth_guard_blocks_bedrock_vertex_foundry_paid_routes(self):
        for key in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                    "AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_VERTEX_PROJECT_ID",
                    "CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_FOUNDRY_API_KEY"):
            with self.assertRaises(AuthError):
                self.service.auth_guard({key: "1"}, login_check=lambda _: True)

    # -- interrupted/malformed recovery with per-provider records -----------

    def test_recover_interrupted_verification_scans_per_provider_process_state(self):
        task = self.tasks.create_task(purpose="verify", acceptance_evidence=["A check"])
        self.tasks.add_acceptance_evidence(task["id"], "A check", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        self.service.run_once(executor=lambda spec: {"status": "completed"})
        self.assertTrue(self.service._start_verification(job["id"]))
        record = Path(job["run_dir"]) / "verification-live"
        record.mkdir(parents=True)
        (record / "0-claude-process-state.json").write_text(json.dumps({"status": "running", "pid": os.getpid()}))
        recovered = self.service.recover_interrupted_verification()
        self.assertEqual(recovered, [])
        self.assertEqual(self.service.get_job(job["id"])["state"], "verifying")

    def test_recover_interrupted_verification_requeues_dead_per_provider_owner(self):
        task = self.tasks.create_task(purpose="verify", acceptance_evidence=["A check"])
        self.tasks.add_acceptance_evidence(task["id"], "A check", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        self.service.run_once(executor=lambda spec: {"status": "completed"})
        self.assertTrue(self.service._start_verification(job["id"]))
        with self.service.tx() as conn:
            conn.execute("UPDATE service_jobs SET verification_pid=999999999 WHERE id=?", (job["id"],))
        record = Path(job["run_dir"]) / "verification-dead"
        record.mkdir(parents=True)
        (record / "0-claude-process-state.json").write_text(json.dumps({"status": "completed", "pid": 999999999}))
        recovered = self.service.recover_interrupted_verification()
        self.assertEqual(recovered, [job["id"]])
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")

    def test_cached_malformed_verification_reused_from_latest_provider_attempt(self):
        task = self.tasks.create_task(purpose="verify", acceptance_evidence=["A check"])
        self.tasks.add_acceptance_evidence(task["id"], "A check", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        record = Path(job["run_dir"]) / "verification-malformed"
        record.mkdir(parents=True)
        (record / "0-claude-outcome.json").write_text(json.dumps({"exit_code": 0, "output_pending": False}))
        (record / "0-claude-process-state.json").write_text(json.dumps({"status": "completed"}))
        (record / "1-codex-outcome.json").write_text(json.dumps({"exit_code": 0, "output_pending": False}))
        (record / "1-codex-process-state.json").write_text(json.dumps({"status": "completed"}))
        found, cached = self.service._cached_verification(record)
        self.assertTrue(found)
        self.assertIn("_verification_error", cached)

    def test_cached_malformed_verification_not_reused_while_latest_attempt_running(self):
        task = self.tasks.create_task(purpose="verify", acceptance_evidence=["A check"])
        self.tasks.add_acceptance_evidence(task["id"], "A check", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        record = Path(job["run_dir"]) / "verification-running"
        record.mkdir(parents=True)
        (record / "0-claude-outcome.json").write_text(json.dumps({"exit_code": 0, "output_pending": False}))
        (record / "0-claude-process-state.json").write_text(json.dumps({"status": "completed"}))
        (record / "1-codex-outcome.json").write_text(json.dumps({"exit_code": None, "output_pending": True}))
        (record / "1-codex-process-state.json").write_text(json.dumps({"status": "running"}))
        found, cached = self.service._cached_verification(record)
        self.assertFalse(found)

    # -- launchd PATH ------------------------------------------------------

    def test_launchd_path_can_discover_both_providers(self):
        from harness.service import Launchd
        def fake_which(name, path=None):
            return f"/opt/{name}/bin/{name}"
        with mock.patch("harness.service.shutil.which", side_effect=fake_which):
            plist = Launchd(self.root, self.root / "service.sqlite3", self.vault).generate("com.example.agents")
        import plistlib
        payload = plistlib.loads(plist.encode())
        path_value = payload["EnvironmentVariables"]["PATH"]
        self.assertIn("/opt/claude/bin", path_value)
        self.assertIn("/opt/codex/bin", path_value)


if __name__ == "__main__":
    unittest.main()
