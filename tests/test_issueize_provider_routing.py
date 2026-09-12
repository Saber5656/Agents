import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from harness.issueize import (
    AuthorizationError,
    ClaudeDraftAgent,
    CodexDraftAgent,
    ProviderRoutingDraftAgent,
    QuotaExceededError,
    RemoteError,
    RemoteNetworkError,
    SubscriptionBoundaryError,
)
from harness.runner import ProcessResult


def claude_stream(text, *, model="sonnet"):
    return "\n".join([
        json.dumps({"type": "system", "model": model}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text}),
    ])


def claude_usage_limit_stream():
    return json.dumps({"type": "result", "subtype": "error", "is_error": True,
                        "result": "Claude AI usage limit reached, please try again later"})


def claude_auth_stream():
    return json.dumps({"type": "result", "subtype": "error", "is_error": True,
                        "result": "Invalid API key · Please run /login"})


class ClaudeDraftAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        login = mock.patch.object(ClaudeDraftAgent, '_verify_subscription_login', create=True)
        login.start()
        self.addCleanup(login.stop)

    def test_rejects_non_default_model_or_effort(self):
        with self.assertRaises(SubscriptionBoundaryError):
            ClaudeDraftAgent(model="opus", env=self.env)
        with self.assertRaises(SubscriptionBoundaryError):
            ClaudeDraftAgent(effort="high", env=self.env)

    def test_rejects_paid_api_routes(self):
        with self.assertRaises(SubscriptionBoundaryError):
            ClaudeDraftAgent(env={**self.env, "ANTHROPIC_API_KEY": "secret"})

    def test_drafting_is_tool_less(self):
        agent = ClaudeDraftAgent(env=self.env)
        with mock.patch("harness.issueize.execute") as execute:
            execute.return_value = ProcessResult(0, claude_stream(json.dumps({
                "title": "Fix parser", "body": "Explain behavior.",
                "acceptance": ["A test passes."]})))
            agent.draft({"id": "task_1", "purpose": "test"})
        argv = execute.call_args.args[0]
        tools_index = argv.index("--tools")
        self.assertEqual(argv[tools_index + 1], "")
        self.assertNotIn("--allowedTools", argv)

    def test_completed_draft_returns_text_and_saves_redacted_artifact(self):
        agent = ClaudeDraftAgent(env={**self.env, "SECRET_TOKEN": "supersecret"})
        artifact_dir = Path(self.temp.name) / "agent-runs"
        draft_text = json.dumps({"title": "Fix parser", "body": "Explain behavior.",
                                  "acceptance": ["A test passes."]})
        with mock.patch("harness.issueize.execute") as execute:
            execute.return_value = ProcessResult(0, claude_stream(draft_text), "supersecret")
            result = agent.draft({"id": "task_1", "purpose": "test"}, artifact_dir=artifact_dir)
        self.assertIn('"title": "Fix parser"', result)
        files = list(artifact_dir.glob("*.json"))
        self.assertEqual(len(files), 1)
        observation = json.loads(files[0].read_text())
        self.assertEqual(observation["provider"], "claude")
        self.assertNotIn("supersecret", observation["stderr"])

    def test_usage_limit_raises_quota_exceeded(self):
        agent = ClaudeDraftAgent(env=self.env)
        with mock.patch("harness.issueize.execute") as execute:
            execute.return_value = ProcessResult(1, claude_usage_limit_stream())
            with self.assertRaises(QuotaExceededError):
                agent.draft({"id": "task_1", "purpose": "test"})

    def test_auth_failure_raises_authorization_error_not_quota(self):
        agent = ClaudeDraftAgent(env=self.env)
        with mock.patch("harness.issueize.execute") as execute:
            execute.return_value = ProcessResult(1, claude_auth_stream())
            with self.assertRaises(AuthorizationError):
                agent.draft({"id": "task_1", "purpose": "test"})

    def test_timeout_raises_remote_network_error_not_quota(self):
        agent = ClaudeDraftAgent(env=self.env)
        with mock.patch("harness.issueize.execute") as execute:
            execute.return_value = ProcessResult(124, "", "", timed_out=True)
            with self.assertRaises(RemoteNetworkError):
                agent.draft({"id": "task_1", "purpose": "test"})

    def test_malformed_output_raises_remote_error(self):
        agent = ClaudeDraftAgent(env=self.env)
        with mock.patch("harness.issueize.execute") as execute:
            execute.return_value = ProcessResult(0, "not json at all")
            with self.assertRaises(RemoteError):
                agent.draft({"id": "task_1", "purpose": "test"})


class ProviderRoutingDraftAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        login = mock.patch.object(ClaudeDraftAgent, '_verify_subscription_login', create=True)
        login.start()
        self.addCleanup(login.stop)

    def test_default_uses_claude_sonnet_low_and_codex_luna_low(self):
        agent = ProviderRoutingDraftAgent(env=self.env)
        self.assertEqual(agent.claude.model, "sonnet")
        self.assertEqual(agent.claude.effort, "low")
        self.assertEqual(agent.codex.model, "gpt-5.6-luna")
        self.assertEqual(agent.codex.effort, "low")

    def test_claude_success_never_calls_codex(self):
        agent = ProviderRoutingDraftAgent(env=self.env)
        draft_text = json.dumps({"title": "Fix parser", "body": "Explain behavior.",
                                  "acceptance": ["A test passes."]})
        with mock.patch("harness.issueize.execute") as execute, \
             mock.patch.object(agent.codex, "draft") as codex_draft:
            execute.return_value = ProcessResult(0, claude_stream(draft_text))
            result = agent.draft({"id": "task_1", "purpose": "test"})
        self.assertIn("Fix parser", result)
        codex_draft.assert_not_called()

    def test_quota_exceeded_falls_back_to_codex(self):
        agent = ProviderRoutingDraftAgent(env=self.env)
        with mock.patch("harness.issueize.execute") as execute, \
             mock.patch.object(agent.codex, "draft", return_value="codex-draft") as codex_draft:
            execute.return_value = ProcessResult(1, claude_usage_limit_stream())
            result = agent.draft({"id": "task_1", "purpose": "test"})
        self.assertEqual(result, "codex-draft")
        codex_draft.assert_called_once()

    def test_non_quota_failure_does_not_fall_back(self):
        agent = ProviderRoutingDraftAgent(env=self.env)
        with mock.patch("harness.issueize.execute") as execute, \
             mock.patch.object(agent.codex, "draft") as codex_draft:
            execute.return_value = ProcessResult(1, claude_auth_stream())
            with self.assertRaises(AuthorizationError):
                agent.draft({"id": "task_1", "purpose": "test"})
        codex_draft.assert_not_called()

    def test_fallback_receives_only_remaining_timeout(self):
        agent = ProviderRoutingDraftAgent(timeout=30, env=self.env)
        with mock.patch.object(agent.claude, 'draft', side_effect=QuotaExceededError('limit')), \
             mock.patch.object(agent.codex, 'draft', return_value='draft') as fallback, \
             mock.patch('harness.issueize.time.monotonic', side_effect=[100, 122]):
            agent.draft({'id': 'task_1'})
        fallback.assert_called_once()
        self.assertEqual(agent.codex.timeout, 8)

    def test_no_fallback_after_total_timeout(self):
        agent = ProviderRoutingDraftAgent(timeout=30, env=self.env)
        with mock.patch.object(agent.claude, 'draft', side_effect=QuotaExceededError('limit')), \
             mock.patch.object(agent.codex, 'draft') as fallback, \
             mock.patch('harness.issueize.time.monotonic', side_effect=[100, 131]):
            with self.assertRaises(RemoteNetworkError):
                agent.draft({'id': 'task_1'})
        fallback.assert_not_called()

    def test_cloud_billing_route_is_rejected(self):
        for key in ('CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY'):
            with self.assertRaises(SubscriptionBoundaryError):
                ProviderRoutingDraftAgent(env={**self.env, key: '1'})

    def test_explicit_codex_agent_remains_usable_alone(self):
        agent = CodexDraftAgent(env=self.env)
        terminal = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}}) \
            + "\n" + json.dumps({"type": "turn.completed"})
        with mock.patch.object(agent, "_verify_subscription_login"), \
             mock.patch("harness.issueize.subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout=terminal, stderr="")):
            agent.draft({"id": "task_fixture"})


if __name__ == "__main__":
    unittest.main()

class ClaudeSubscriptionLoginTests(unittest.TestCase):
    def test_stored_api_login_is_rejected_before_drafting(self):
        agent = ClaudeDraftAgent(env={'PATH': '/usr/bin'})
        auth = mock.Mock(returncode=0, stdout=json.dumps({
            'loggedIn': True, 'authMethod': 'api_key', 'apiProvider': 'firstParty'}))
        with mock.patch('harness.issueize.subprocess.run', return_value=auth), \
             mock.patch('harness.issueize.execute') as inference:
            with self.assertRaises(AuthorizationError):
                agent.draft({'id': 'task_1'})
        inference.assert_not_called()
