import json
from pathlib import Path
import tempfile
import unittest

from harness.usage import build_usage_report, emit_usage


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


class UsageReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.runs = self.vault / "01-Projects" / "agent-runs"
        self.runs.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_aggregates_by_actual_provider_and_model_preserving_requested_alias(self):
        run_dir = self.runs / "run-a"
        _write(run_dir / "result.json", {
            "run_dir": str(run_dir), "status": "completed", "attempts": [
                {"attempt_id": "run-a:0", "attempt_number": 0, "provider": "claude",
                 "requested_model": "sonnet", "actual_model": "claude-sonnet-5-20260101",
                 "status": "completed", "usage_info": {"available": True, "reason": "provider_reported",
                                                        "values": {"input_tokens": 100, "output_tokens": 20}},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        report = build_usage_report(self.vault)
        self.assertEqual(len(report["usage_by_provider_model"]), 1)
        group = report["usage_by_provider_model"][0]
        self.assertEqual(group["provider"], "claude")
        self.assertEqual(group["actual_model"], "claude-sonnet-5-20260101")
        self.assertIn("sonnet", group["requested_models"])
        self.assertEqual(group["token_totals"]["input_tokens"], 100)
        self.assertEqual(group["status_counts"]["completed"], 1)
        self.assertEqual(group["usage_reported_attempts"], 1)
        self.assertEqual(group["usage_missing_attempts"], 0)

    def test_missing_usage_is_not_treated_as_zero_or_quota(self):
        run_dir = self.runs / "run-b"
        _write(run_dir / "result.json", {
            "run_dir": str(run_dir), "status": "completed", "attempts": [
                {"attempt_id": "run-b:0", "attempt_number": 0, "provider": "codex",
                 "requested_model": "gpt-5.6-luna", "actual_model": "gpt-5.6-luna",
                 "status": "completed", "usage_info": {"available": False, "reason": "provider_did_not_report"},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        report = build_usage_report(self.vault)
        group = report["usage_by_provider_model"][0]
        self.assertEqual(group["usage_missing_attempts"], 1)
        self.assertEqual(group["usage_reported_attempts"], 0)
        self.assertEqual(group["token_totals"], {})
        self.assertEqual(group["status_counts"]["completed"], 1)
        self.assertNotIn("quota", group["status_counts"])

    def test_distinguishes_completed_quota_auth_failed_running(self):
        statuses = {
            "completed": "completed", "usage_limit": "quota", "auth_error": "auth",
            "permission_denied": "permission", "timeout": "failed", "running": "running",
        }
        run_dir = self.runs / "run-c"
        attempts = []
        for index, (raw, _bucket) in enumerate(statuses.items()):
            attempts.append({
                "attempt_id": f"run-c:{index}", "attempt_number": index, "provider": "claude",
                "requested_model": "sonnet", "actual_model": None, "status": raw,
                "usage_info": {"available": False, "reason": "provider_did_not_report"},
                "started_at": "2026-09-01T00:00:00+00:00",
            })
        _write(run_dir / "result.json", {"run_dir": str(run_dir), "status": "running", "attempts": attempts})
        report = build_usage_report(self.vault)
        group = next(g for g in report["usage_by_provider_model"] if g["actual_model"] is None)
        counts = group["status_counts"]
        for raw, bucket in statuses.items():
            self.assertGreaterEqual(counts.get(bucket, 0), 1, f"{raw} did not map to {bucket}: {counts}")

    def test_unknown_usage_never_disables_claude_or_is_treated_as_quota_exhaustion(self):
        run_dir = self.runs / "run-d"
        _write(run_dir / "result.json", {
            "run_dir": str(run_dir), "status": "completed", "attempts": [
                {"attempt_id": "run-d:0", "attempt_number": 0, "provider": "claude",
                 "requested_model": "sonnet", "actual_model": "sonnet", "status": "completed",
                 "usage_info": {"available": False, "reason": "provider_did_not_report"},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        report = build_usage_report(self.vault)
        group = next(g for g in report["usage_by_provider_model"] if g["provider"] == "claude")
        self.assertEqual(group["status_counts"].get("quota", 0), 0)
        self.assertNotIn("disabled", report)
        self.assertNotIn("claude_disabled", report)

    def test_deduplicates_by_attempt_id_across_repeated_records(self):
        run_dir = self.runs / "run-e"
        attempt = {"attempt_id": "run-e:0", "attempt_number": 0, "provider": "claude",
                   "requested_model": "sonnet", "actual_model": "sonnet", "status": "completed",
                   "usage_info": {"available": True, "reason": "provider_reported",
                                  "values": {"input_tokens": 5}},
                   "started_at": "2026-09-01T00:00:00+00:00"}
        _write(run_dir / "result.json", {"run_dir": str(run_dir), "status": "completed",
                                         "attempts": [attempt, dict(attempt)]})
        report = build_usage_report(self.vault)
        group = report["usage_by_provider_model"][0]
        self.assertEqual(group["attempts"], 1)
        self.assertEqual(report["attempts_deduplicated"], 1)

    def test_finite_nonnegative_numeric_usage_only_no_invented_percentages(self):
        run_dir = self.runs / "run-f"
        _write(run_dir / "result.json", {
            "run_dir": str(run_dir), "status": "completed", "attempts": [
                {"attempt_id": "run-f:0", "attempt_number": 0, "provider": "claude",
                 "requested_model": "sonnet", "actual_model": "sonnet", "status": "completed",
                 "usage_info": {"available": True, "reason": "provider_reported",
                                "values": {"input_tokens": 10, "bad_inf": float("inf"),
                                           "bad_neg": -1, "subscription_remaining_pct": 42}},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        report = build_usage_report(self.vault)
        group = report["usage_by_provider_model"][0]
        self.assertEqual(group["token_totals"].get("input_tokens"), 10)
        self.assertNotIn("bad_inf", group["token_totals"])
        self.assertNotIn("bad_neg", group["token_totals"])
        # subscription_remaining_pct is a quota-remaining percentage, not a
        # token metric: only whitelisted token counters are ever aggregated,
        # so an arbitrary numeric percentage must never be summed as usage.
        self.assertNotIn("subscription_remaining_pct", group["token_totals"])

    def test_raw_usage_preferred_over_usage_info_derived_summary(self):
        run_dir = self.runs / "run-raw"
        _write(run_dir / "result.json", {
            "run_dir": str(run_dir), "status": "completed", "attempts": [
                {"attempt_id": "run-raw:0", "attempt_number": 0, "provider": "claude",
                 "requested_model": "sonnet", "actual_model": "sonnet", "status": "completed",
                 "usage": {"input_tokens": 11, "output_tokens": 2},
                 "usage_info": {"available": True, "reason": "provider_reported",
                                "values": {"input_tokens": 999}},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        report = build_usage_report(self.vault)
        group = report["usage_by_provider_model"][0]
        self.assertEqual(group["token_totals"]["input_tokens"], 11)
        self.assertEqual(group["token_totals"]["output_tokens"], 2)

    def test_usage_info_used_as_fallback_for_historical_records_without_raw_usage(self):
        run_dir = self.runs / "run-historical"
        _write(run_dir / "result.json", {
            "run_dir": str(run_dir), "status": "completed", "attempts": [
                {"attempt_id": "run-historical:0", "attempt_number": 0, "provider": "claude",
                 "requested_model": "sonnet", "actual_model": "sonnet", "status": "completed",
                 "usage_info": {"available": True, "reason": "provider_reported",
                                "values": {"input_tokens": 40}},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        report = build_usage_report(self.vault)
        group = report["usage_by_provider_model"][0]
        self.assertEqual(group["token_totals"]["input_tokens"], 40)

    def test_invalid_since_raises_value_error_instead_of_silently_ignoring_filter(self):
        with self.assertRaises(ValueError):
            build_usage_report(self.vault, since="not-a-timestamp")
        with self.assertRaises(ValueError):
            build_usage_report(self.vault, since="2026-09-01T00:00:00")  # missing tz

    def test_duplicate_identity_keeps_richer_terminal_record_not_first_running_snapshot(self):
        run_dir = self.runs / "run-dup"
        running = {"attempt_id": "run-dup:0", "attempt_number": 0, "provider": "claude",
                   "requested_model": "sonnet", "actual_model": None, "status": "running",
                   "usage_info": {"available": False, "reason": "provider_did_not_report"},
                   "started_at": "2026-09-01T00:00:00+00:00"}
        completed = {"attempt_id": "run-dup:0", "attempt_number": 0, "provider": "claude",
                     "requested_model": "sonnet", "actual_model": "claude-sonnet-5", "status": "completed",
                     "usage_info": {"available": True, "reason": "provider_reported",
                                    "values": {"input_tokens": 30}},
                     "started_at": "2026-09-01T00:00:00+00:00", "finished_at": "2026-09-01T00:05:00+00:00"}
        _write(run_dir / "result.json", {"run_dir": str(run_dir), "status": "completed",
                                         "attempts": [running, completed]})
        report = build_usage_report(self.vault)
        self.assertEqual(len(report["usage_by_provider_model"]), 1)
        group = report["usage_by_provider_model"][0]
        self.assertEqual(group["actual_model"], "claude-sonnet-5")
        self.assertEqual(group["status_counts"], {"completed": 1})
        self.assertEqual(group["token_totals"]["input_tokens"], 30)

    def test_immutable_identity_fallback_uses_run_dir_and_ordinal_not_object_id(self):
        run_dir = self.runs / "run-noid"
        attempts = [
            {"attempt_number": None, "provider": "claude", "requested_model": "sonnet",
             "actual_model": "sonnet", "status": "completed",
             "usage_info": {"available": False, "reason": "provider_did_not_report"},
             "started_at": "2026-09-01T00:00:00+00:00"},
            {"attempt_number": None, "provider": "codex", "requested_model": "gpt",
             "actual_model": "gpt", "status": "completed",
             "usage_info": {"available": False, "reason": "provider_did_not_report"},
             "started_at": "2026-09-01T00:00:00+00:00"},
        ]
        _write(run_dir / "result.json", {"run_dir": str(run_dir), "status": "completed", "attempts": attempts})
        report_a = build_usage_report(self.vault)
        report_b = build_usage_report(self.vault)
        self.assertEqual(report_a["attempts_deduplicated"], 2)
        self.assertEqual(report_b["attempts_deduplicated"], 2)

    def test_rate_limit_events_only_inspect_claude_never_devin_stdout(self):
        run_dir = self.runs / "run-devin"
        _write(run_dir / "result.json", {
            "run_dir": str(run_dir), "status": "completed", "attempts": [
                {"attempt_id": "run-devin:0", "attempt_number": 0, "provider": "devin",
                 "requested_model": "devin", "actual_model": "devin", "status": "completed",
                 "usage_info": {"available": False, "reason": "provider_did_not_report"},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        devin_stdout = run_dir / "0-devin-stdout.jsonl"
        devin_stdout.write_text(
            json.dumps({"type": "rate_limit_event", "timestamp": "2026-09-01T00:00:05+00:00",
                        "rate_limit_info": {"status": "ok"}}) + "\n")
        report = build_usage_report(self.vault)
        self.assertEqual(report["rate_limit_events"], [])

    def test_rate_limit_stdout_path_rejects_path_traversal_attempt_number(self):
        from harness.usage import _stdout_path
        run_dir = self.runs / "run-traversal"
        run_dir.mkdir(parents=True)
        attempt = {"provider": "claude", "attempt_number": "../../etc/passwd"}
        self.assertIsNone(_stdout_path(run_dir, attempt))
        attempt_negative = {"provider": "claude", "attempt_number": -1}
        self.assertIsNone(_stdout_path(run_dir, attempt_negative))

    def test_run_dir_argument_narrows_scan_to_that_directory(self):
        inner = self.runs / "run-scoped"
        outer = self.runs / "run-other"
        for d in (inner, outer):
            _write(d / "result.json", {
                "run_dir": str(d), "status": "completed", "attempts": [
                    {"attempt_id": f"{d.name}:0", "attempt_number": 0, "provider": "claude",
                     "requested_model": "sonnet", "actual_model": "sonnet", "status": "completed",
                     "usage_info": {"available": False, "reason": "provider_did_not_report"},
                     "started_at": "2026-09-01T00:00:00+00:00"},
                ],
            })
        report = build_usage_report(self.vault, run_dir=inner)
        self.assertEqual(report["runs_scanned"], 1)
        self.assertEqual(report["attempts_deduplicated"], 1)

    def test_run_dir_argument_outside_agent_runs_root_is_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        with self.assertRaises(ValueError):
            build_usage_report(self.vault, run_dir=outside)

    def test_reports_provider_rate_limit_event_with_timestamp_and_source(self):
        run_dir = self.runs / "run-g"
        _write(run_dir / "result.json", {
            "run_dir": str(run_dir), "status": "completed", "attempts": [
                {"attempt_id": "run-g:0", "attempt_number": 0, "provider": "claude",
                 "requested_model": "sonnet", "actual_model": "sonnet", "status": "completed",
                 "usage_info": {"available": False, "reason": "provider_did_not_report"},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        stdout = run_dir / "0-claude-stdout.jsonl"
        stdout.write_text(
            json.dumps({"type": "system", "subtype": "init"}) + "\n" +
            json.dumps({"type": "rate_limit_event", "timestamp": "2026-09-01T00:00:05+00:00",
                        "rate_limit_info": {"status": "ok", "remaining": 12}}) + "\n")
        report = build_usage_report(self.vault)
        self.assertEqual(len(report["rate_limit_events"]), 1)
        event = report["rate_limit_events"][0]
        self.assertEqual(event["timestamp"], "2026-09-01T00:00:05+00:00")
        self.assertEqual(event["source"], "stdout_rate_limit_event")
        self.assertEqual(event["rate_limit_info"], {"status": "ok", "remaining": 12})
        self.assertNotIn("estimated_from_tokens", event)

    def test_since_filter_uses_timezone_aware_iso8601(self):
        old = self.runs / "run-old"
        new = self.runs / "run-new"
        for run_dir, started in ((old, "2026-01-01T00:00:00+00:00"), (new, "2026-09-10T00:00:00+00:00")):
            _write(run_dir / "result.json", {
                "run_dir": str(run_dir), "status": "completed", "attempts": [
                    {"attempt_id": f"{run_dir.name}:0", "attempt_number": 0, "provider": "claude",
                     "requested_model": "sonnet", "actual_model": "sonnet", "status": "completed",
                     "usage_info": {"available": False, "reason": "provider_did_not_report"},
                     "started_at": started},
                ],
            })
        report = build_usage_report(self.vault, since="2026-09-01T00:00:00+00:00")
        group = report["usage_by_provider_model"][0]
        self.assertEqual(group["attempts"], 1)

    def test_malformed_result_record_is_reported_without_breaking_whole_report(self):
        (self.runs / "run-bad").mkdir()
        (self.runs / "run-bad" / "result.json").write_text("{not valid json")
        good = self.runs / "run-good"
        _write(good / "result.json", {
            "run_dir": str(good), "status": "completed", "attempts": [
                {"attempt_id": "run-good:0", "attempt_number": 0, "provider": "claude",
                 "requested_model": "sonnet", "actual_model": "sonnet", "status": "completed",
                 "usage_info": {"available": False, "reason": "provider_did_not_report"},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        report = build_usage_report(self.vault)
        self.assertEqual(len(report["runs_skipped"]), 1)
        self.assertIn("run-bad", report["runs_skipped"][0]["path"])
        self.assertEqual(len(report["usage_by_provider_model"]), 1)

    def test_missing_vault_projects_directory_returns_empty_report_not_error(self):
        empty_vault = Path(self.temp.name) / "empty-vault"
        empty_vault.mkdir()
        report = build_usage_report(empty_vault)
        self.assertEqual(report["usage_by_provider_model"], [])
        self.assertEqual(report["runs_scanned"], 0)

    def test_recursively_finds_nested_service_attempt_result_records(self):
        nested = self.runs / "service-job-1" / "attempt-2"
        _write(nested / "result.json", {
            "run_dir": str(nested), "status": "completed", "attempts": [
                {"attempt_id": "nested:0", "attempt_number": 0, "provider": "codex",
                 "requested_model": "gpt-5.6-luna", "actual_model": "gpt-5.6-luna", "status": "completed",
                 "usage_info": {"available": True, "reason": "provider_reported",
                                "values": {"input_tokens": 7}},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        report = build_usage_report(self.vault)
        self.assertEqual(report["runs_scanned"], 1)
        group = report["usage_by_provider_model"][0]
        self.assertEqual(group["token_totals"]["input_tokens"], 7)

    def test_emit_usage_json_and_text(self):
        run_dir = self.runs / "run-h"
        _write(run_dir / "result.json", {
            "run_dir": str(run_dir), "status": "completed", "attempts": [
                {"attempt_id": "run-h:0", "attempt_number": 0, "provider": "claude",
                 "requested_model": "sonnet", "actual_model": "sonnet", "status": "completed",
                 "usage_info": {"available": True, "reason": "provider_reported",
                                "values": {"input_tokens": 3}},
                 "started_at": "2026-09-01T00:00:00+00:00"},
            ],
        })
        report = build_usage_report(self.vault)
        as_json = emit_usage(report, as_json=True)
        parsed = json.loads(as_json)
        self.assertEqual(parsed["usage_by_provider_model"][0]["provider"], "claude")
        text = emit_usage(report, as_json=False)
        self.assertIn("claude", text)
        self.assertIn("sonnet", text)


if __name__ == "__main__":
    unittest.main()

class UsageDisplayAccuracyTests(unittest.TestCase):
    def test_permissions_and_incomplete_reviews_are_distinct_from_auth_and_complete(self):
        from harness.usage import _status_bucket
        self.assertEqual(_status_bucket('permission_denied'), 'permission')
        self.assertEqual(_status_bucket('review_incomplete'), 'incomplete')

    def test_since_counts_only_selected_attempts_in_display(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); runs = root/'01-Projects'/'agent-runs'
            _write(runs/'r'/'result.json', {'attempts': [
                {'provider': 'claude', 'status': 'completed', 'attempt_number': 0,
                 'started_at': '2026-09-01T00:00:00+00:00'},
                {'provider': 'claude', 'status': 'completed', 'attempt_number': 1,
                 'started_at': '2026-09-15T00:00:00+00:00'}]})
            report = build_usage_report(root, since='2026-09-15T00:00:00+00:00')
            self.assertEqual(report['attempts_selected'], 1)
            self.assertIn('1 attempt(s)', emit_usage(report))
            self.assertIn('completed', emit_usage(report))

class ServiceAttemptIdentityTests(unittest.TestCase):
    def test_repeated_service_attempt_basename_is_not_the_same_execution(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d); runs = vault/'01-Projects'/'agent-runs'
            for job in ('service-a', 'service-b'):
                directory = runs/job/'attempt-1'
                _write(directory/'result.json', {'run_dir': str(directory), 'attempts': [
                    {'attempt_id': 'attempt-1:0', 'provider': 'claude', 'status': 'completed',
                     'usage': {'input_tokens': 7}}]})
            report = build_usage_report(vault)
            self.assertEqual(report['attempts_selected'], 2)
            self.assertEqual(report['usage_by_provider_model'][0]['token_totals']['input_tokens'], 14)

    def test_copy_of_record_with_same_canonical_run_is_counted_once(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d); runs = vault/'01-Projects'/'agent-runs'; source=runs/'original'
            record={'run_dir': str(source), 'attempts': [{'attempt_id': 'original:0', 'provider':'claude',
                     'status':'completed', 'usage': {'input_tokens': 8}}]}
            _write(source/'result.json',record);_write(runs/'copy'/'result.json',record)
            self.assertEqual(build_usage_report(vault)['attempts_selected'],1)
