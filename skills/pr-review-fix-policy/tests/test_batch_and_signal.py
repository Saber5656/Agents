import importlib.util
import unittest
import tempfile
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BATCH = load("batch", "scripts/fetch_review_batch.py")
SIGNAL = load("signal", "scripts/validate_review_signal.py")
CONSUMER = load("consumer", "scripts/consume_review_signal.py")


class BatchTests(unittest.TestCase):
    @patch.object(BATCH, "run_graphql")
    def test_invalid_thread_cursor_never_certifies_complete_fetch(self, graphql):
        for cursor, blocker in [(None, "pagination_cursor_missing"),
                                ("repeat", "pagination_cursor_repeated")]:
            with self.subTest(cursor=cursor):
                payload = {"data": {"repository": {"pullRequest": {
                    "url": "u", "state": "OPEN", "baseRefName": "main", "headRefName": "b",
                    "headRefOid": "a" * 40,
                    "reviewThreads": {"nodes": [], "pageInfo": {
                        "hasNextPage": True, "endCursor": cursor}},
                    "reviews": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                }}}}
                graphql.reset_mock()
                graphql.side_effect = [payload, payload, RuntimeError("Unexpected repeated request")]
                result = BATCH.fetch_one("owner", "repo", 1)
                self.assertEqual(blocker, result["blocker"])
                self.assertFalse(result["pagination_complete"])
                self.assertLessEqual(graphql.call_count, 2)

    def test_requires_explicit_owner_repo_number(self):
        self.assertEqual(("owner", "repo", 12), BATCH.parse_ref("owner/repo#12"))
        self.assertEqual(("Owner", "Repo", 12), BATCH.parse_ref("https://github.com/Owner/Repo/pull/12"))
        with self.assertRaises(ValueError):
            BATCH.parse_ref("#12")

    @patch.object(BATCH, "run_comment_graphql")
    def test_paginates_all_thread_comments(self, graphql):
        thread = {"id": "T1", "comments": {
            "nodes": [{"id": "C1", "body": "first"}],
            "pageInfo": {"hasNextPage": True, "endCursor": "next"},
        }}
        graphql.return_value = {"data": {"node": {"comments": {
            "nodes": [{"id": "C2", "body": "latest"}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}}}
        self.assertEqual(["C1", "C2"], [item["id"] for item in BATCH.complete_comments(thread)])

    @patch.object(BATCH, "run_graphql")
    def test_paginates_and_filters_actionable_threads(self, graphql):
        graphql.side_effect = [
            {"data": {"repository": {"pullRequest": {
                "url": "u", "state": "OPEN", "baseRefName": "main", "headRefName": "b", "headRefOid": "a" * 40,
                "reviewThreads": {"nodes": [
                    {"id": "T1", "isResolved": False, "isOutdated": False, "path": "a.py", "line": 2, "originalLine": 2,
                     "comments": {"nodes": [{"author": {"login": "bot"}, "body": "fix me"}]}},
                    {"id": "T2", "isResolved": True, "isOutdated": False, "path": "b.py", "line": 3, "originalLine": 3,
                     "comments": {"nodes": []}},
                ], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}
            }}}},
            {"data": {"repository": {"pullRequest": {
                "url": "u", "state": "OPEN", "baseRefName": "main", "headRefName": "b", "headRefOid": "a" * 40,
                "reviewThreads": {"nodes": [
                    {"id": "T3", "isResolved": False, "isOutdated": True, "path": "c.py", "line": 4, "originalLine": 4,
                     "comments": {"nodes": []}},
                ], "pageInfo": {"hasNextPage": False, "endCursor": None}}
            }}}},
            {"data": {"repository": {"pullRequest": {
                "url": "u", "state": "OPEN", "baseRefName": "main", "headRefName": "b", "headRefOid": "a" * 40,
                "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
            }}}},
        ]
        result = BATCH.fetch_one("owner", "repo", 1)
        self.assertTrue(result["pagination_complete"])
        self.assertEqual(["T1"], [item["thread_node_id"] for item in result["actionable_threads"]])
        self.assertEqual("untrusted_review_content", result["actionable_threads"][0]["content_trust"])
        self.assertEqual({"resolved": 1, "outdated": 1}, result["ignored"])
        self.assertEqual(3, graphql.call_count)

    @patch.object(BATCH, "run_graphql")
    def test_rejects_head_rollover_during_pagination(self, graphql):
        def page(head, has_next, cursor):
            return {"data": {"repository": {"pullRequest": {
                "url": "u", "state": "OPEN", "baseRefName": "main", "headRefName": "b", "headRefOid": head,
                "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}},
            }}}}
        graphql.side_effect = [page("a" * 40, True, "next"), page("b" * 40, False, None)]
        result = BATCH.fetch_one("owner", "repo", 1)
        self.assertEqual("head_changed_during_fetch", result["blocker"])
        self.assertEqual([], result["actionable_threads"])

    @patch.object(BATCH, "run_graphql", side_effect=RuntimeError("denied"))
    def test_per_pr_fetch_failure_becomes_blocker(self, _graphql):
        self.assertIn("fetch_failed", BATCH.fetch_one("owner", "repo", 1)["blocker"])

    @patch.object(BATCH, "run_graphql")
    def test_final_pr_state_controls_blocker(self, graphql):
        def payload(state):
            return {"data": {"repository": {"pullRequest": {
                "url": "u", "state": state, "baseRefName": "main", "headRefName": "b", "headRefOid": "a" * 40,
                "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
            }}}}
        graphql.side_effect = [payload("OPEN"), payload("CLOSED")]
        self.assertEqual("pr_state_closed", BATCH.fetch_one("owner", "repo", 1)["blocker"])

    @patch.object(BATCH, "run_graphql")
    def test_fetches_submitted_reviews_bound_to_current_head(self, graphql):
        def payload(state="OPEN"):
            return {"data": {"repository": {"pullRequest": {
                "url": "u", "state": state, "baseRefName": "main", "headRefName": "b",
                "headRefOid": "a" * 40,
                "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                "reviews": {"nodes": [{
                    "id": "R1", "state": "CHANGES_REQUESTED", "body": "blocking rationale",
                    "submittedAt": "2026-07-16T00:00:00Z", "updatedAt": "2026-07-16T00:01:00Z",
                    "commit": {"oid": "a" * 40}, "author": {"login": "reviewer"},
                }], "pageInfo": {"hasNextPage": False, "endCursor": None}},
            }}}}
        graphql.side_effect = [payload(), payload()]
        result = BATCH.fetch_one("owner", "repo", 1)
        self.assertEqual("R1", result["reviews"][0]["id"])
        self.assertEqual("CHANGES_REQUESTED", result["reviews"][0]["state"])
        self.assertEqual("a" * 40, result["reviews"][0]["commit_oid"])

    @patch.object(BATCH, "run_review_graphql")
    @patch.object(BATCH, "run_graphql")
    def test_paginates_submitted_reviews_and_retains_old_head_provenance(self, graphql, review_graphql):
        def payload(reviews, state="OPEN", has_next=False):
            return {"data": {"repository": {"pullRequest": {
                "url": "u", "state": state, "baseRefName": "main", "headRefName": "b",
                "headRefOid": "a" * 40,
                "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                "reviews": {"nodes": reviews, "pageInfo": {"hasNextPage": has_next, "endCursor": "next" if has_next else None}},
            }}}}
        first = {"id": "R1", "state": "COMMENTED", "body": "one", "commit": {"oid": "a" * 40}}
        old = {"id": "OLD", "state": "CHANGES_REQUESTED", "body": "old", "commit": {"oid": "b" * 40}}
        graphql.side_effect = [payload([first], has_next=True), payload([first])]
        review_graphql.return_value = {"data": {"repository": {"pullRequest": {
            "headRefOid": "a" * 40,
            "reviews": {"nodes": [old, {"id": "R2", "state": "APPROVED", "body": "two", "commit": {"oid": "a" * 40}}], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        }}}}
        result = BATCH.fetch_one("owner", "repo", 1)
        self.assertEqual(["OLD", "R1", "R2"], [item["id"] for item in result["reviews"]])
        self.assertFalse(result["reviews"][0]["head_match"])
        self.assertTrue(all(item["head_match"] for item in result["reviews"][1:]))
        review_graphql.assert_called_once_with("owner", "repo", 1, "next")

    @patch.object(BATCH, "run_review_graphql")
    @patch.object(BATCH, "run_graphql")
    def test_missing_submitted_review_cursor_is_incomplete(self, graphql, review_graphql):
        def payload(has_next=True, cursor="next"):
            return {"data": {"repository": {"pullRequest": {
                "url": "u", "state": "OPEN", "baseRefName": "main", "headRefName": "b",
                "headRefOid": "a" * 40,
                "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                "reviews": {"nodes": [], "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}},
            }}}}
        graphql.return_value = payload()
        review_graphql.return_value = {"data": {"repository": {"pullRequest": {
            "headRefOid": "a" * 40,
            "reviews": {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": None}},
        }}}}
        result = BATCH.fetch_one("owner", "repo", 1)
        self.assertEqual("review_pagination_cursor_missing", result["blocker"])
        self.assertFalse(result["pagination_complete"])

    @patch.object(BATCH, "run_review_graphql")
    @patch.object(BATCH, "run_graphql")
    def test_repeated_submitted_review_cursor_is_incomplete(self, graphql, review_graphql):
        payload = {"data": {"repository": {"pullRequest": {
            "url": "u", "state": "OPEN", "baseRefName": "main", "headRefName": "b",
            "headRefOid": "a" * 40,
            "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
            "reviews": {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "next"}},
        }}}}
        graphql.return_value = payload
        review_graphql.return_value = {"data": {"repository": {"pullRequest": {
            "headRefOid": "a" * 40,
            "reviews": {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "next"}},
        }}}}
        result = BATCH.fetch_one("owner", "repo", 1)
        self.assertEqual("review_pagination_cursor_repeated", result["blocker"])
        self.assertFalse(result["pagination_complete"])

    @patch.object(BATCH, "run_graphql")
    def test_submitted_review_keeps_full_body_alongside_summary(self, graphql):
        body = "initial context\n" + ("detail " * 60) + "FINDING_AT_END"
        payload = {"data": {"repository": {"pullRequest": {
            "url": "u", "state": "OPEN", "baseRefName": "main", "headRefName": "b",
            "headRefOid": "a" * 40,
            "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
            "reviews": {"nodes": [{
                "id": "R1", "state": "CHANGES_REQUESTED", "body": body,
                "commit": {"oid": "a" * 40}, "author": {"login": "reviewer"},
            }], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        }}}}
        graphql.side_effect = [payload, payload]
        result = BATCH.fetch_one("owner", "repo", 1)
        review = result["reviews"][0]
        self.assertEqual(body, review["body"])
        self.assertIn("FINDING_AT_END", review["body"])
        self.assertLess(len(review["summary"]), len(review["body"]))
        self.assertNotIn("FINDING_AT_END", review["summary"])


class SignalTests(unittest.TestCase):
    def envelope(self):
        payload = {
            "schema_version": "2", "signal_id": "", "repository": "owner/repo", "pr_number": 1,
            "head_sha": "a" * 40, "observed_at": "2026-07-16T00:00:00Z", "settled_at": "2026-07-16T00:01:30Z",
            "source_event": "pull_request_review", "review_ids": ["R1"], "review_state_digest": "d" * 64,
            "actionable_thread_ids": ["T1"],
            "thread_state_digest": "b" * 64, "workflow_url": "https://github.com/owner/repo/actions/runs/1",
            "delivery": "ready",
        }
        payload["signal_id"] = SIGNAL.signal_id(payload)
        return payload

    def test_valid_signal(self):
        self.assertEqual([], SIGNAL.validate(self.envelope()))

    def test_rejects_tampered_identity(self):
        payload = self.envelope()
        payload["head_sha"] = "c" * 40
        self.assertIn("signal_id mismatch", SIGNAL.validate(payload))

    def test_rejects_duplicate_thread_ids(self):
        payload = self.envelope()
        payload["actionable_thread_ids"] = ["T1", "T1"]
        self.assertIn("actionable_thread_ids must be a unique string list", SIGNAL.validate(payload))

    def test_rejects_malformed_envelope_shapes(self):
        self.assertEqual(["envelope must be a JSON object"], SIGNAL.validate([]))
        payload = self.envelope()
        payload["review_ids"] = [["unhashable"]]
        self.assertIn("review_ids must be a unique string list", SIGNAL.validate(payload))

    def test_rejects_invalid_provenance_fields(self):
        payload = self.envelope()
        payload.update(observed_at=0, settled_at="2026-07-16T00:01:30", source_event={})
        errors = SIGNAL.validate(payload)
        self.assertTrue(any("observed_at" in item for item in errors))
        self.assertTrue(any("settled_at" in item for item in errors))
        self.assertTrue(any("source_event" in item for item in errors))

    def test_rejects_unknown_fields_and_empty_ids(self):
        payload = self.envelope()
        payload["extra"] = True
        payload["review_ids"] = [""]
        errors = SIGNAL.validate(payload)
        self.assertTrue(any("unexpected fields" in item for item in errors))
        self.assertIn("review_ids must be a unique string list", errors)


class ConsumerTests(unittest.TestCase):
    def watch(self):
        return {
            "watch_id": "w1", "repository": "owner/repo", "pr_number": 1,
            "expected_head_sha": "a" * 40, "task_id": "private-task",
            "created_at": "2026-07-16T00:00:00Z", "expires_at": "2099-07-16T00:00:00Z",
        }

    @patch.object(CONSUMER, "find_status")
    @patch.object(CONSUMER, "compute_state")
    def test_ready_requires_matching_fresh_state(self, state, status):
        state.return_value = {
            "head_sha": "a" * 40, "review_ids": ["R1"], "actionable_thread_ids": ["T1"],
            "review_state_digest": "d" * 64, "thread_state_digest": "b" * 64, "signal_id": "c" * 64,
        }
        status.return_value = {"description": "ready:" + "c" * 16, "target_url": "https://github.com/o/r/actions/runs/1"}
        self.assertEqual("ready", CONSUMER.reconcile(self.watch())["status"])

    @patch.object(CONSUMER, "find_status")
    @patch.object(CONSUMER, "compute_state")
    def test_rejects_changed_head(self, state, status):
        state.return_value = {"head_sha": "d" * 40}
        self.assertEqual("head_mismatch", CONSUMER.reconcile(self.watch())["status"])
        status.assert_not_called()

    @patch.object(CONSUMER, "find_status")
    @patch.object(CONSUMER, "compute_state")
    def test_rejects_stale_status_digest(self, state, status):
        state.return_value = {
            "head_sha": "a" * 40, "review_ids": ["R1"], "actionable_thread_ids": ["T1"],
            "review_state_digest": "d" * 64, "thread_state_digest": "b" * 64, "signal_id": "c" * 64,
        }
        status.return_value = {"description": "ready:" + "d" * 16}
        self.assertEqual("stale_signal", CONSUMER.reconcile(self.watch())["status"])

    @patch.object(CONSUMER, "find_status")
    @patch.object(CONSUMER, "compute_state")
    def test_consumed_signal_is_not_woken_again(self, state, status):
        watch = self.watch()
        watch["last_consumed_signal_id"] = "c" * 64
        state.return_value = {
            "head_sha": "a" * 40, "review_ids": ["R1"], "actionable_thread_ids": ["T1"],
            "review_state_digest": "d" * 64, "thread_state_digest": "b" * 64, "signal_id": "c" * 64,
        }
        status.return_value = {"description": "ready:" + "c" * 16}
        self.assertEqual("duplicate_signal", CONSUMER.reconcile(watch)["status"])

    @patch.object(CONSUMER, "find_failed_delivery")
    @patch.object(CONSUMER, "find_status", return_value=None)
    @patch.object(CONSUMER, "compute_state")
    def test_failed_workflow_is_not_reported_as_no_signal(self, state, _status, failed):
        state.return_value = {"head_sha": "a" * 40}
        failed.return_value = {"html_url": "https://github.com/o/r/actions/runs/2"}
        result = CONSUMER.reconcile(self.watch())
        self.assertEqual("delivery_blocked_unreconciled", result["status"])

    def test_ack_is_persisted_atomically_to_private_watch(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "watch.json"
            watch = self.watch()
            path.write_text(json.dumps(watch))
            CONSUMER.acknowledge(path, watch, {"signal_id": "c" * 64, "reconciled_at": "2026-07-16T00:02:00Z"})
            saved = json.loads(path.read_text())
            self.assertEqual("c" * 64, saved["last_consumed_signal_id"])

    def state_payloads(self, review_updated="2026-07-16T00:00:00Z", comment_updated="2026-07-16T00:00:00Z"):
        return [
            {"data": {"repository": {"pullRequest": {
                "headRefOid": "a" * 40,
                "reviewThreads": {"nodes": [{"id": "T1", "isResolved": False, "isOutdated": False, "path": "a.py", "line": 1, "originalLine": 1}], "pageInfo": {"hasNextPage": False, "endCursor": None}},
            }, "nameWithOwner": "Owner/Repo"}}},
            {"data": {"repository": {"pullRequest": {"reviews": {
                "nodes": [{"id": "R1", "state": "COMMENTED", "submittedAt": "2026-07-16T00:00:00Z", "updatedAt": review_updated, "commit": {"oid": "a" * 40}}],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }, "headRefOid": "a" * 40}}}},
            {"data": {"node": {"comments": {"nodes": [{"id": "C1", "updatedAt": comment_updated}], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}},
            {"data": {"repository": {"nameWithOwner": "Owner/Repo", "pullRequest": {"headRefOid": "a" * 40, "state": "OPEN"}}}},
        ]

    @patch.object(CONSUMER, "gh_json")
    def test_comment_edit_changes_signal_identity(self, gh):
        gh.side_effect = self.state_payloads()
        first = CONSUMER.compute_state("owner/repo", 1)["signal_id"]
        gh.side_effect = self.state_payloads(comment_updated="2026-07-16T00:03:00Z")
        second = CONSUMER.compute_state("owner/repo", 1)["signal_id"]
        self.assertNotEqual(first, second)

    @patch.object(CONSUMER, "gh_json")
    def test_review_edit_changes_signal_identity(self, gh):
        gh.side_effect = self.state_payloads()
        first = CONSUMER.compute_state("owner/repo", 1)["signal_id"]
        gh.side_effect = self.state_payloads(review_updated="2026-07-16T00:04:00Z")
        second = CONSUMER.compute_state("owner/repo", 1)["signal_id"]
        self.assertNotEqual(first, second)

    @patch.object(CONSUMER, "gh_json")
    def test_second_review_page_changes_signal_identity(self, gh):
        first_payloads = self.state_payloads()
        gh.side_effect = first_payloads
        first = CONSUMER.compute_state("owner/repo", 1)["signal_id"]
        paged = self.state_payloads()
        paged[1]["data"]["repository"]["pullRequest"]["reviews"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next"}
        second_page = {"data": {"repository": {"pullRequest": {"reviews": {
            "nodes": [{"id": "R2", "state": "CHANGES_REQUESTED", "submittedAt": "2026-07-16T00:01:00Z", "updatedAt": "2026-07-16T00:02:00Z", "commit": {"oid": "a" * 40}}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }, "headRefOid": "a" * 40}}}}
        gh.side_effect = [paged[0], paged[1], second_page, paged[2], paged[3]]
        second = CONSUMER.compute_state("owner/repo", 1)["signal_id"]
        self.assertNotEqual(first, second)

    @patch.object(CONSUMER, "gh_json")
    def test_consumer_rejects_head_rollover_before_return(self, gh):
        payloads = self.state_payloads()
        payloads[-1] = {"data": {"repository": {"nameWithOwner": "Owner/Repo", "pullRequest": {"headRefOid": "b" * 40, "state": "OPEN"}}}}
        gh.side_effect = payloads
        with self.assertRaisesRegex(RuntimeError, "head_changed_during_fetch"):
            CONSUMER.compute_state("owner/repo", 1)

    @patch.object(CONSUMER, "gh_json")
    def test_consumer_uses_final_pr_state(self, gh):
        payloads = self.state_payloads()
        payloads[-1]["data"]["repository"]["pullRequest"]["state"] = "CLOSED"
        gh.side_effect = payloads
        with self.assertRaisesRegex(RuntimeError, "pr_state_closed"):
            CONSUMER.compute_state("owner/repo", 1)

    @patch.object(CONSUMER, "gh_json")
    def test_repository_case_uses_canonical_lowercase_identity(self, gh):
        gh.side_effect = self.state_payloads()
        state = CONSUMER.compute_state("OWNER/REPO", 1)
        self.assertEqual("owner/repo", state["repository"])

    @patch.object(CONSUMER, "gh_json")
    def test_find_status_paginates(self, gh):
        gh.side_effect = [
            {"statuses": [{"context": f"other-{index}"} for index in range(100)]},
            {"statuses": [{"context": "review-intake/signal", "description": "ready:abc"}]},
        ]
        self.assertEqual("ready:abc", CONSUMER.find_status("owner/repo", "a" * 40)["description"])
        self.assertEqual(2, gh.call_count)

    def test_rejects_timezone_less_expiry(self):
        watch = self.watch()
        watch["expires_at"] = "2099-07-16T00:00:00"
        self.assertEqual("invalid_watch", CONSUMER.reconcile(watch)["status"])

    @patch.object(CONSUMER, "reconcile")
    def test_concurrent_consumers_claim_one_ready_signal(self, reconcile):
        def result_for(watch):
            time.sleep(0.02)
            if watch.get("last_consumed_signal_id") == "c" * 64:
                return {"status": "duplicate_signal", "signal_id": "c" * 64}
            return {"status": "ready", "signal_id": "c" * 64, "reconciled_at": "2026-07-16T00:02:00Z"}
        reconcile.side_effect = result_for
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "watch.json"
            path.write_text(json.dumps(self.watch()))
            results = []
            threads = [threading.Thread(target=lambda: results.append(CONSUMER.locked_reconcile(path))) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(["duplicate_signal", "ready"], sorted(item["status"] for item in results))


class WorkflowSafetyTests(unittest.TestCase):
    def test_legacy_receiver_is_fail_closed_and_read_only(self):
        workflow = (ROOT / "assets/review-signal.yml").read_text()
        self.assertIn("Deprecated fail-closed reference", workflow)
        self.assertIn("statuses: read", workflow)
        self.assertIn("if: ${{ false }}", workflow)
        self.assertNotIn("statuses: write", workflow)
        self.assertNotIn("issue_comment:", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("actions/checkout", workflow)

    def test_skill_deprecates_commit_status_signal_as_evidence(self):
        skill = (ROOT / "SKILL.md").read_text()
        readme = (ROOT / "README.md").read_text()
        for phrase in [
            "commit-status方式",
            "review-intake/signal",
            "statusを書かないでください",
            "review到着・clean・merge-ready",
        ]:
            self.assertIn(phrase, skill + "\n" + readme)


if __name__ == "__main__":
    unittest.main()
