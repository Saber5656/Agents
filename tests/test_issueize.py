import json
import os
from pathlib import Path
import threading
import time
import tempfile
import unittest
from unittest import mock

from harness.issueize import (
    AmbiguousRemoteError,
    AuthorizationError,
    CodexDraftAgent,
    DraftError,
    GitHubIssueAdapter,
    IssueDraft,
    IssueizationBatch,
    RemoteMalformedError,
    SubscriptionBoundaryError,
    parse_draft,
    render_issue_body,
)
from harness.tasks import TaskStore


class FakeAgent:
    def __init__(self, *, delay=0, malformed=False):
        self.delay = delay
        self.malformed = malformed
        self.calls = []

    def draft(self, task):
        self.calls.append(task["id"])
        if self.delay:
            time.sleep(self.delay)
        if self.malformed:
            return "not json"
        return {
            "title": f"Improve {task['purpose']}",
            "body": "Describe the observable implementation and its reason.",
            "acceptance": ["The behavior is covered by a reproducible check."],
        }


class FakeGitHub:
    def __init__(self):
        self.issues = []
        self.creates = 0
        self.lock = threading.Lock()
        self.fail_create = None
        self.hide_from_listing = False

    def list_issues(self, repository):
        with self.lock:
            if self.hide_from_listing:
                return []
            return [dict(issue) for issue in self.issues]

    def create_issue(self, repository, title, body):
        with self.lock:
            self.creates += 1
            number = len(self.issues) + 1
            issue = {"number": number, "title": title, "body": body,
                     "html_url": f"https://github.com/{repository}/issues/{number}"}
            self.issues.append(issue)
            if self.fail_create:
                error = self.fail_create
                self.fail_create = None
                raise error
            return dict(issue)

    def read_issue(self, repository, number):
        with self.lock:
            for issue in self.issues:
                if issue["number"] == number:
                    return dict(issue)
        raise KeyError(number)


class IssueizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.db = self.root / "tasks.sqlite3"
        self.env = {"AGENTS_ROOT": str(self.root), "AGENTS_VAULT_ROOT": str(self.vault)}
        self.env_patch = mock.patch.dict(os.environ, self.env, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.store = TaskStore(self.db)
        self.addCleanup(self.store.close)
        self.addCleanup(self.temp.cleanup)

    def make_tasks(self, count=3):
        return [self.store.create_task(purpose=f"follow-up-{i}", repository="org/repo")
                for i in range(count)]

    def test_three_unissued_tasks_and_one_issued_are_processed(self):
        tasks = self.make_tasks()
        issued = self.store.create_task(purpose="already", repository="org/repo")
        self.store.link_issue(issued["id"], "org/repo", 99,
                              "https://github.com/org/repo/issues/99", verified=True,
                              readback={"repository": "org/repo", "number": 99,
                                        "url": "https://github.com/org/repo/issues/99"})
        remote, agent = FakeGitHub(), FakeAgent()
        result = IssueizationBatch(self.store, remote, agent, owner="batch-a").run()
        self.assertEqual(result["issued"], 3)
        self.assertEqual(remote.creates, 3)
        self.assertEqual(set(agent.calls), {task["id"] for task in tasks})
        for task in tasks:
            current = self.store.get_task(task["id"])
            self.assertEqual(current["issueization_state"], "issued")
            issue = next(item for item in remote.issues if task["id"] in item["body"])
            self.assertIn(f"<!-- agents-local-task:{task['id']} -->", issue["body"])

    def test_overlapping_batches_use_one_lifetime_lock_and_one_issue(self):
        task = self.make_tasks(1)[0]
        remote, agent = FakeGitHub(), FakeAgent(delay=0.05)
        outputs = []

        def run(owner):
            with TaskStore(self.db) as store:
                outputs.append(IssueizationBatch(store, remote, agent, owner=owner).run())

        first = threading.Thread(target=run, args=("one",))
        second = threading.Thread(target=run, args=("two",))
        first.start(); second.start(); first.join(); second.join()
        self.assertEqual(remote.creates, 1)
        self.assertEqual(self.store.get_task(task["id"])["issueization_state"], "issued")

    def test_kill_window_reconciles_remote_marker_without_duplicate(self):
        task = self.make_tasks(1)[0]
        remote, agent = FakeGitHub(), FakeAgent()
        remote.fail_create = AmbiguousRemoteError("connection lost after create")
        first = IssueizationBatch(self.store, remote, agent, owner="batch-a").run()
        self.assertEqual(first["ambiguous"], 1)
        self.assertEqual(self.store.get_task(task["id"])["issueization_state"], "ambiguous")
        receipt = self.vault / "01-Projects" / "issueization" / "receipts" / f"{task['id']}.json"
        self.assertEqual(json.loads(receipt.read_text())["status"], "ambiguous")
        remote.hide_from_listing = True
        second = IssueizationBatch(self.store, remote, agent, owner="batch-b").run()
        self.assertEqual(second["ambiguous"], 1)
        self.assertEqual(remote.creates, 1)
        remote.hide_from_listing = False
        third = IssueizationBatch(self.store, remote, agent, owner="batch-c").run()
        self.assertEqual(third["issued"], 1)
        self.assertEqual(remote.creates, 1)

    def test_corrupt_receipt_is_preserved_and_never_creates(self):
        task = self.make_tasks(1)[0]
        receipt = self.vault / "01-Projects" / "issueization" / "receipts" / f"{task['id']}.json"
        receipt.parent.mkdir(parents=True)
        original = "{corrupt receipt"
        receipt.write_text(original)
        remote, agent = FakeGitHub(), FakeAgent()
        result = IssueizationBatch(self.store, remote, agent, owner="batch-a").run()
        self.assertEqual(result["incomplete"], 1)
        self.assertEqual(remote.creates, 0)
        self.assertEqual(agent.calls, [])
        self.assertEqual(receipt.read_text(), original)
        self.assertEqual(self.store.get_task(task["id"])["issueization_state"], "ambiguous")

    def test_remote_readback_body_mismatch_stays_incomplete(self):
        task = self.make_tasks(1)[0]
        remote, agent = FakeGitHub(), FakeAgent()
        remote.create_issue = mock.Mock(side_effect=lambda repository, title, body: {
            "number": 1, "title": title, "body": body,
            "html_url": f"https://github.com/{repository}/issues/1"})
        remote.read_issue = mock.Mock(return_value={
            "number": 1, "title": "altered", "body": "<!-- agents-local-task:%s --> altered" % task["id"],
            "html_url": "https://github.com/org/repo/issues/1"})
        result = IssueizationBatch(self.store, remote, agent, owner="batch-a").run()
        self.assertEqual(result["incomplete"], 1)
        self.assertEqual(self.store.get_task(task["id"])["issueization_state"], "ambiguous")

    def test_codex_jsonl_requires_successful_turn_completion(self):
        agent = CodexDraftAgent(env={})
        task = {"id": "task_1", "purpose": "test"}
        with mock.patch("harness.issueize.subprocess.run") as run:
            run.side_effect = [mock.Mock(returncode=0, stdout="Logged in using ChatGPT", stderr=""),
                               mock.Mock(returncode=0, stdout=json.dumps({
                                   "type": "item.completed", "item": {"type": "agent_message", "text": "{}"}}),
                                          stderr="")]
            with self.assertRaises(DraftError):
                agent.draft(task)

    def test_codex_observation_keeps_redacted_prompt_streams_and_usage(self):
        agent = CodexDraftAgent(env={"SECRET_TOKEN": "supersecret"})
        task = {"id": "task_1", "purpose": "test"}
        artifact_dir = self.root / "vault" / "private-agent-runs"
        events = "\n".join([
            json.dumps({"type": "item.completed", "item": {"type": "agent_message",
                        "text": json.dumps({"title": "Fix parser", "body": "Explain behavior.",
                                             "acceptance": ["A test passes."]})}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 3}}),
        ])
        with mock.patch("harness.issueize.subprocess.run") as run:
            run.side_effect = [mock.Mock(returncode=0, stdout="Logged in using ChatGPT", stderr=""),
                               mock.Mock(returncode=0, stdout=events, stderr="supersecret")]
            result = agent.draft(task, artifact_dir=artifact_dir)
        self.assertIn('"title": "Fix parser"', result)
        files = list(artifact_dir.glob("*.json"))
        self.assertEqual(len(files), 1)
        observation = json.loads(files[0].read_text())
        self.assertEqual(observation["usage"]["output_tokens"], 3)
        self.assertNotIn("supersecret", observation["stderr"])
        self.assertNotIn("actual_model", observation)
        with mock.patch("harness.issueize.subprocess.run") as run:
            run.side_effect = [mock.Mock(returncode=0, stdout="Logged in using ChatGPT", stderr=""),
                               mock.Mock(returncode=0, stdout=json.dumps({
                                   "type": "turn.completed", "status": "failed"}), stderr="")]
            with self.assertRaises(DraftError):
                agent.draft(task)

    def test_codex_api_key_is_rejected(self):
        with self.assertRaises(SubscriptionBoundaryError):
            CodexDraftAgent(env={"CODEX_API_KEY": "secret"})

    def test_auth_failure_and_malformed_agent_output_remain_retryable(self):
        task = self.make_tasks(1)[0]
        result = IssueizationBatch(self.store, FakeGitHub(), FakeAgent(malformed=True), owner="batch-a").run()
        self.assertEqual(result["retry"], 1)
        self.assertEqual(self.store.get_task(task["id"])["issueization_state"], "retry")
        remote = FakeGitHub(); remote.fail_create = AuthorizationError("login required")
        result = IssueizationBatch(self.store, remote, FakeAgent(), owner="batch-b").run()
        self.assertEqual(result["retry"], 1)
        self.assertEqual(remote.creates, 1)
        self.assertEqual(self.store.get_task(task["id"])["issueization_state"], "retry")

    def test_expired_claim_is_reconciled_before_reclaim(self):
        task = self.make_tasks(1)[0]
        claim = self.store.claim_issueization(task["id"], "dead-batch", lease_seconds=-1)
        remote, agent = FakeGitHub(), FakeAgent()
        result = IssueizationBatch(self.store, remote, agent, owner="live-batch").run()
        self.assertEqual(result["issued"], 1)
        self.assertEqual(remote.creates, 1)

    def test_public_draft_requires_english_and_stable_marker(self):
        draft = parse_draft({"title": "Fix parser", "body": "Explain behavior.",
                             "acceptance": ["A test passes."]})
        body = render_issue_body("task_abc", draft)
        self.assertTrue(body.startswith("<!-- agents-local-task:task_abc -->"))
        with self.assertRaises(DraftError):
            parse_draft({"title": "日本語", "body": "Explain.", "acceptance": ["A test."]})
        with self.assertRaises(DraftError):
            render_issue_body("task_abc", IssueDraft("Fix", "/Users/alice/private", ["A test."]))

    def test_subscription_agent_rejects_paid_api_routes_and_high_model(self):
        with self.assertRaises(SubscriptionBoundaryError):
            CodexDraftAgent(env={"OPENAI_API_KEY": "secret"})
        with self.assertRaises(SubscriptionBoundaryError):
            CodexDraftAgent(model="gpt-5.6-astra")
        with self.assertRaises(SubscriptionBoundaryError):
            CodexDraftAgent(effort="high")

    def test_github_listing_is_paginated_and_does_not_use_search(self):
        calls = []

        def command(argv):
            calls.append(argv)
            return json.dumps([{"number": 1}, {"number": 2}]) if "page=1" in argv[2] else json.dumps([{"number": 3}])

        adapter = GitHubIssueAdapter("org/repo", page_size=2, command_runner=command)
        self.assertEqual([item["number"] for item in adapter.list_issues()], [1, 2, 3])
        self.assertTrue(all("search/issues" not in " ".join(call) for call in calls))
        self.assertIn("page=2", calls[1][2])

    def test_malformed_create_response_is_ambiguous(self):
        adapter = GitHubIssueAdapter("org/repo", command_runner=lambda argv: "not json")
        with self.assertRaises(RemoteMalformedError):
            adapter.create_issue("org/repo", "Title", "Body")


if __name__ == "__main__":
    unittest.main()
