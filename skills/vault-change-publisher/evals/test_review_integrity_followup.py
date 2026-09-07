#!/usr/bin/env python3
"""Regression tests for bounded-review identity and execution integrity."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REVIEW = load_module(
    "followup_validate_publication_review",
    SCRIPTS / "validate-publication-review.py",
)
PINNED = load_module("followup_run_pinned_review", SCRIPTS / "run-pinned-review.py")
FINALIZER = load_module(
    "followup_commit_push_publication_evidence",
    SCRIPTS / "commit-push-publication-evidence.py",
)


class ReviewIntegrityFollowupTests(unittest.TestCase):
    """Cover the four review findings without requiring real Vaults."""

    def test_sealed_history_identity_is_restored_for_bounded_block(self) -> None:
        """Large local history can be validated without model-side copying."""
        changed_paths = [f"history/{index:04d}.md" for index in range(200)]
        commit = {
            "commit": "a" * 40,
            "parents": ["b" * 40],
            "tree": "c" * 40,
            "message": "large local history",
            "changed_paths": changed_paths,
        }
        materialized = {**commit, "patch_sha256": "d" * 64}
        review = {
            "agents_vault": {
                "publication_mode": "blocked",
                "approved_existing_commits": [],
            },
            "user_vault": {
                "publication_mode": "sweep",
                "approved_existing_commits": [],
            },
        }
        normalized, receipt = REVIEW.normalize_sealed_local_history(
            review,
            {
                "agents_vault": {"local_commits": [commit]},
                "user_vault": {"local_commits": []},
            },
            {
                "local_commits": {
                    "agents_vault": [materialized],
                    "user_vault": [],
                }
            },
            {"agents_vault": "blocked", "user_vault": "sweep"},
        )
        self.assertEqual(
            normalized["agents_vault"]["approved_existing_commits"],
            [materialized],
        )
        self.assertTrue(receipt["agents_vault"]["normalized"])
        self.assertEqual(receipt["agents_vault"]["restored_count"], 1)

    def test_blocked_residual_identity_is_restored_from_sealed_entries(self) -> None:
        """A blocked large residual still satisfies the exact path contract."""
        paths = [f"residual/{index:04d}.md" for index in range(200)]
        review = {
            "agents_vault": {
                "publication_mode": "blocked",
                "core_review_status": "quality_ok",
                "review_or_validation_status": "quality_ok",
                "residual_review_status": "blocked",
                "excluded_paths": [],
                "unrelated_dirty_paths": [],
                "deferred_cleanup": [],
            },
            "user_vault": {
                "publication_mode": "sweep",
                "core_review_status": "quality_ok",
                "review_or_validation_status": "quality_ok",
                "residual_review_status": "quality_ok",
                "excluded_paths": [],
                "unrelated_dirty_paths": [],
                "deferred_cleanup": [],
            },
        }
        normalized, receipt = REVIEW.normalize_own_only_residuals(
            review,
            {},
            {
                "agents_vault": {"dirty_paths": paths},
                "user_vault": {"dirty_paths": []},
            },
            {
                "vaults": {
                    "agents_vault": [
                        {"path": path, "materialization_reason": "guard"}
                        for path in paths
                    ],
                    "user_vault": [],
                }
            },
            {"agents_vault": "blocked", "user_vault": "sweep"},
        )
        self.assertEqual(normalized["agents_vault"]["excluded_paths"], paths)
        self.assertEqual(normalized["agents_vault"]["unrelated_dirty_paths"], paths)
        self.assertEqual(
            [item["path"] for item in normalized["agents_vault"]["deferred_cleanup"]],
            paths,
        )
        self.assertEqual(receipt["agents_vault"]["mode"], "blocked")

    def test_pinned_runner_passes_exact_metrics_bound_request(self) -> None:
        """The child receives the bytes whose digest was prepared and audited."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "request.txt"
            metrics = root / "metrics.json"
            sink = root / "received.bin"
            content = "review\nRuntime context JSON:\n{}\n".encode("utf-8")
            request.write_bytes(content)
            metrics_value = {
                "status": "ready",
                "publication_context_projection": "review_bounded_v2",
                "request_bytes": len(content),
                "request_chars": len(content.decode()),
                "request_sha256": hashlib.sha256(content).hexdigest(),
            }
            metrics.write_text(json.dumps(metrics_value), encoding="utf-8")
            receiver = root / "receiver.py"
            receiver.write_text(
                "import pathlib,sys\n"
                f"pathlib.Path({str(sink)!r}).write_bytes(sys.stdin.buffer.read())\n",
                encoding="utf-8",
            )
            status = PINNED.main(
                [
                    "run-pinned-review.py",
                    str(request),
                    str(metrics),
                    hashlib.sha256(metrics.read_bytes()).hexdigest(),
                    "--",
                    sys.executable,
                    str(receiver),
                ]
            )
            self.assertEqual(status, 0)
            self.assertEqual(sink.read_bytes(), content)

    def test_pinned_runner_rejects_request_digest_mismatch(self) -> None:
        """A replaced request cannot be consumed under stale metrics."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "request.txt"
            metrics = root / "metrics.json"
            request.write_bytes(b"actual")
            metrics_value = {
                "status": "ready",
                "publication_context_projection": "review_bounded_v2",
                "request_bytes": 8,
                "request_chars": 8,
                "request_sha256": hashlib.sha256(b"expected").hexdigest(),
            }
            metrics.write_text(json.dumps(metrics_value), encoding="utf-8")
            status = PINNED.main(
                [
                    "run-pinned-review.py",
                    str(request),
                    str(metrics),
                    hashlib.sha256(metrics.read_bytes()).hexdigest(),
                    "--",
                    "/usr/bin/true",
                ]
            )
            self.assertEqual(status, 75)

    def test_pinned_runner_rejects_fifo_without_blocking(self) -> None:
        """A FIFO replacement is rejected before any blocking read."""
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "request.fifo"
            os.mkfifo(fifo)
            with self.assertRaises(PINNED.PinnedReviewError):
                PINNED.read_stable(fifo, PINNED.MAX_REQUEST_BYTES)

    def test_finalizer_reads_structured_evidence_diagnostic(self) -> None:
        """Only bounded diagnostic fields, never raw stderr, cross the finalizer boundary."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic.json"
            path.write_text(json.dumps({
                "process_status": 17,
                "status": 75,
                "reason_code": "input_too_large",
                "result_present": False,
                "stderr_sha256": "a" * 64,
                "result_sha256": None,
            }), encoding="utf-8")
            diagnostic = FINALIZER.read_evidence_review_diagnostic(path, 75)
            self.assertEqual(diagnostic["reason_code"], "input_too_large")
            self.assertNotIn("stderr", diagnostic)

    def test_finalizer_keeps_evidence_review_failure_reason(self) -> None:
        """Missing evidence output carries the bounded-input cause to recovery."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_value = {
                "publisher_git_name": "Fixture Publisher",
                "publisher_git_email": "publisher@example.invalid",
            }
            pre_value = {"sealed": True}
            context_value = {"runtime": runtime_value, "pre_collection_state": pre_value}
            runtime = root / "runtime.json"
            pre = root / "pre.json"
            initial = root / "initial.json"
            plan = root / "plan.json"
            context = root / "context.json"
            review = root / "missing-review.json"
            output = root / "result.json"
            runtime.write_text(json.dumps(runtime_value), encoding="utf-8")
            pre.write_text(json.dumps(pre_value), encoding="utf-8")
            initial.write_text(json.dumps({"initial": True}), encoding="utf-8")
            context.write_text(json.dumps(context_value), encoding="utf-8")
            plan.write_text(
                json.dumps({
                    "publication_context_sha256": hashlib.sha256(context.read_bytes()).hexdigest()
                }),
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), mock.patch.object(
                FINALIZER, "partial_result", return_value={"next_action": "reason"}
            ):
                status = FINALIZER.main(
                    [
                        "commit-push-publication-evidence.py",
                        str(runtime),
                        str(pre),
                        str(initial),
                        str(plan),
                        str(review),
                        str(output),
                        "75",
                        str(context),
                        "evidence review process rejected bounded input: max length",
                    ]
                )
            self.assertEqual(status, 75)
            self.assertIn("max length", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
