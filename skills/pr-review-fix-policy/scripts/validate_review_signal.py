#!/usr/bin/env python3
"""Deprecated signal validator retained only for fixture compatibility.

The CLI fails closed because runtime v1 does not authorize the commit-status
signal channel.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import sys

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SHA40 = re.compile(r"^[0-9a-fA-F]{40}$")


def signal_id(payload: dict) -> str:
    identity = "\n".join([
        payload["schema_version"], payload["repository"].lower(), str(payload["pr_number"]),
        payload["head_sha"].lower(), ",".join(sorted(payload["review_ids"])),
        payload["review_state_digest"], ",".join(sorted(payload["actionable_thread_ids"])), payload["thread_state_digest"],
    ])
    return hashlib.sha256(identity.encode()).hexdigest()


def is_aware_datetime(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["envelope must be a JSON object"]
    required = {"schema_version", "signal_id", "repository", "pr_number", "head_sha", "observed_at", "settled_at", "source_event", "review_ids", "review_state_digest", "actionable_thread_ids", "thread_state_digest", "workflow_url", "delivery"}
    errors = [f"missing field: {key}" for key in sorted(required - payload.keys())]
    if errors:
        return errors
    extras = sorted(set(payload) - required)
    if extras:
        errors.append(f"unexpected fields: {', '.join(extras)}")
    if payload["schema_version"] != "2": errors.append("schema_version must be 2")
    if not HEX64.fullmatch(str(payload["signal_id"])): errors.append("signal_id must be lowercase sha256")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", str(payload["repository"])): errors.append("repository must be owner/repo")
    if not isinstance(payload["pr_number"], int) or isinstance(payload["pr_number"], bool) or payload["pr_number"] < 1: errors.append("pr_number must be a positive integer")
    if not SHA40.fullmatch(str(payload["head_sha"])): errors.append("head_sha must be 40 hex characters")
    if not is_aware_datetime(payload["observed_at"]): errors.append("observed_at must be a timezone-aware ISO date-time")
    if not is_aware_datetime(payload["settled_at"]): errors.append("settled_at must be a timezone-aware ISO date-time")
    if not isinstance(payload["source_event"], str) or not payload["source_event"]: errors.append("source_event must be a non-empty string")
    if not HEX64.fullmatch(str(payload["thread_state_digest"])): errors.append("thread_state_digest must be lowercase sha256")
    if not HEX64.fullmatch(str(payload["review_state_digest"])): errors.append("review_state_digest must be lowercase sha256")
    review_ids = payload["review_ids"]
    if not isinstance(review_ids, list) or not all(isinstance(item, str) and item for item in review_ids) or len(review_ids) != len(set(review_ids)):
        errors.append("review_ids must be a unique string list")
    thread_ids = payload["actionable_thread_ids"]
    if not isinstance(thread_ids, list) or not all(isinstance(item, str) and item for item in thread_ids) or len(thread_ids) != len(set(thread_ids)):
        errors.append("actionable_thread_ids must be a unique string list")
    if not isinstance(payload["delivery"], str) or payload["delivery"] not in {"ready", "no_actionable_review", "duplicate_suppressed", "signal_delivery_blocked"}:
        errors.append("invalid delivery")
    if not str(payload["workflow_url"]).startswith("https://github.com/"): errors.append("workflow_url must be a github.com URL")
    if not errors and payload["signal_id"] != signal_id(payload): errors.append("signal_id mismatch")
    return errors


def main() -> int:
    print(
        "review_signal_status_unsupported: use fresh attested Saihai github_observe operations",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
