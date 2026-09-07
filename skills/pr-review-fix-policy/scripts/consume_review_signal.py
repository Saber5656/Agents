#!/usr/bin/env python3
"""Deprecated review-status reconciler retained only for fixture compatibility.

Runtime v1 does not authorize the commit-status signal channel. The CLI fails
closed; production review intake must use fresh Saihai ``github_observe``
operations.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

QUERY = r"""
query($owner:String!, $repo:String!, $number:Int!, $cursor:String) {
  repository(owner:$owner, name:$repo) {
    nameWithOwner
    pullRequest(number:$number) {
      headRefOid
      reviewThreads(first:100, after:$cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved isOutdated path line originalLine }
      }
    }
  }
}
"""
REVIEW_QUERY = r"""
query($owner:String!, $repo:String!, $number:Int!, $cursor:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      headRefOid
      reviews(first:100, after:$cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id state submittedAt updatedAt commit { oid } }
      }
    }
  }
}
"""
HEAD_QUERY = r"""
query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) { nameWithOwner pullRequest(number:$number) { headRefOid state } }
}
"""
COMMENT_QUERY = r"""
query($id:ID!, $cursor:String) {
  node(id:$id) {
    ... on PullRequestReviewThread {
      comments(first:100, after:$cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id updatedAt }
      }
    }
  }
}
"""


def gh_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(["gh", *command], check=False, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "gh failed")
    return json.loads(completed.stdout)


def compute_state(repository: str, pr_number: int) -> dict[str, Any]:
    owner, repo = repository.split("/", 1)
    cursor = None
    threads: list[dict[str, Any]] = []
    pr = None
    fixed_head = None
    canonical_repository = None
    while True:
        args = ["api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}", "-F", f"repo={repo}", "-F", f"number={pr_number}"]
        if cursor is not None:
            args.extend(["-F", f"cursor={cursor}"])
        payload = gh_json(args)
        repository_node = payload.get("data", {}).get("repository") or {}
        pr = repository_node.get("pullRequest")
        if pr is None:
            raise RuntimeError("PR not found or inaccessible")
        page_repository = str(repository_node.get("nameWithOwner") or repository).lower()
        if canonical_repository is None:
            canonical_repository = page_repository
        elif page_repository != canonical_repository:
            raise RuntimeError("repository_identity_changed_during_fetch")
        page_head = str(pr.get("headRefOid") or "").lower()
        if fixed_head is None:
            fixed_head = page_head
        elif page_head != fixed_head:
            raise RuntimeError("head_changed_during_fetch")
        threads.extend(pr["reviewThreads"]["nodes"])
        page = pr["reviewThreads"]["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]
        if not cursor:
            raise RuntimeError("review thread pagination cursor is missing")

    head = fixed_head
    reviews_all: list[dict[str, Any]] = []
    review_cursor = None
    while True:
        args = ["api", "graphql", "-f", f"query={REVIEW_QUERY}", "-F", f"owner={owner}", "-F", f"repo={repo}", "-F", f"number={pr_number}"]
        if review_cursor is not None:
            args.extend(["-F", f"cursor={review_cursor}"])
        review_pr = gh_json(args).get("data", {}).get("repository", {}).get("pullRequest")
        if review_pr is None or str(review_pr.get("headRefOid") or "").lower() != head:
            raise RuntimeError("head_changed_during_fetch")
        connection = review_pr.get("reviews")
        if connection is None:
            raise RuntimeError("reviews not found or inaccessible")
        reviews_all.extend(connection["nodes"])
        page = connection["pageInfo"]
        if not page["hasNextPage"]:
            break
        review_cursor = page["endCursor"]
        if not review_cursor:
            raise RuntimeError("review pagination cursor is missing")
    for thread in threads:
        thread["commentMetadata"] = []
        comment_cursor = None
        while True:
            args = ["api", "graphql", "-f", f"query={COMMENT_QUERY}", "-F", f"id={thread['id']}"]
            if comment_cursor is not None:
                args.extend(["-F", f"cursor={comment_cursor}"])
            connection = gh_json(args).get("data", {}).get("node", {}).get("comments")
            if connection is None:
                raise RuntimeError("thread comments not found or inaccessible")
            thread["commentMetadata"].extend(connection["nodes"])
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                break
            comment_cursor = page["endCursor"]
            if not comment_cursor:
                raise RuntimeError("thread comment pagination cursor is missing")
    final_args = ["api", "graphql", "-f", f"query={HEAD_QUERY}", "-F", f"owner={owner}", "-F", f"repo={repo}", "-F", f"number={pr_number}"]
    final_repository = gh_json(final_args).get("data", {}).get("repository") or {}
    final_pr = final_repository.get("pullRequest")
    if final_pr is None or str(final_pr.get("headRefOid") or "").lower() != head:
        raise RuntimeError("head_changed_during_fetch")
    if str(final_repository.get("nameWithOwner") or repository).lower() != canonical_repository:
        raise RuntimeError("repository_identity_changed_during_fetch")
    if final_pr.get("state") != "OPEN":
        raise RuntimeError(f"pr_state_{str(final_pr.get('state')).lower()}")
    actionable = sorted(item["id"] for item in threads if not item["isResolved"] and not item["isOutdated"])
    reviews = sorted(
        item["id"] for item in reviews_all
        if (item.get("commit") or {}).get("oid", "").lower() == head and item["state"] in {"COMMENTED", "CHANGES_REQUESTED"}
    )
    review_lines = sorted("|".join([
        item["id"], item["state"], (item.get("commit") or {}).get("oid", "").lower(),
        item.get("submittedAt") or "", item.get("updatedAt") or "",
    ]) for item in reviews_all)
    review_digest = hashlib.sha256("\n".join(review_lines).encode()).hexdigest()
    lines = sorted("|".join([
        item["id"], str(item["isResolved"]).lower(), str(item["isOutdated"]).lower(), item.get("path") or "",
        str(item.get("originalLine") or item.get("line") or ""),
        ",".join(sorted(f"{comment['id']}@{comment['updatedAt']}" for comment in item["commentMetadata"])),
    ]) for item in threads)
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    identity = "\n".join(["2", canonical_repository, str(pr_number), head, ",".join(reviews), review_digest, ",".join(actionable), digest])
    return {"repository": canonical_repository, "head_sha": head, "review_ids": reviews, "actionable_thread_ids": actionable,
            "review_state_digest": review_digest, "thread_state_digest": digest,
            "signal_id": hashlib.sha256(identity.encode()).hexdigest()}


def find_status(repository: str, head: str) -> dict[str, Any] | None:
    page = 1
    while True:
        payload = gh_json(["api", "--method", "GET", f"repos/{repository}/commits/{head}/status", "-f", "per_page=100", "-f", f"page={page}"])
        statuses = payload.get("statuses", [])
        match = next((item for item in statuses if item.get("context") == "review-intake/signal"), None)
        if match is not None:
            return match
        if len(statuses) < 100:
            return None
        page += 1


def find_failed_delivery(watch: dict[str, Any]) -> dict[str, Any] | None:
    payload = gh_json(["api", "--method", "GET", f"repos/{watch['repository']}/actions/runs", "-f", "per_page=100"])
    title = f"Review signal for PR #{watch['pr_number']}"
    matches = [run for run in payload.get("workflow_runs", [])
               if run.get("display_title") == title and run.get("conclusion") == "failure"
               and str(run.get("created_at", "")) >= str(watch["created_at"])]
    return matches[0] if matches else None


def reconcile(watch: dict[str, Any]) -> dict[str, Any]:
    required = {"watch_id", "repository", "pr_number", "expected_head_sha", "task_id", "created_at", "expires_at"}
    missing = sorted(required - watch.keys())
    if missing:
        return {"status": "invalid_watch", "blocker": f"missing: {', '.join(missing)}"}
    now = dt.datetime.now(dt.timezone.utc)
    try:
        expires = dt.datetime.fromisoformat(str(watch["expires_at"]).replace("Z", "+00:00"))
        if expires.tzinfo is None or expires.utcoffset() is None:
            raise ValueError("timezone is required")
    except (TypeError, ValueError):
        return {"status": "invalid_watch", "blocker": "expires_at is not an ISO timestamp"}
    if expires <= now:
        return {"status": "expired_watch", "watch_id": watch["watch_id"]}
    state = compute_state(watch["repository"], int(watch["pr_number"]))
    if state["head_sha"] != str(watch["expected_head_sha"]).lower():
        return {"status": "head_mismatch", "watch_id": watch["watch_id"], "current_head_sha": state["head_sha"]}
    status = find_status(watch["repository"], state["head_sha"])
    if status is None:
        failed = find_failed_delivery(watch)
        if failed:
            return {"status": "delivery_blocked_unreconciled", "watch_id": watch["watch_id"],
                    "head_sha": state["head_sha"], "workflow_url": failed.get("html_url")}
        return {"status": "no_signal", "watch_id": watch["watch_id"], "head_sha": state["head_sha"]}
    expected_description = f"{'ready' if state['actionable_thread_ids'] or state['review_ids'] else 'no_actionable_review'}:{state['signal_id'][:16]}"
    if status.get("description") != expected_description:
        return {"status": "stale_signal", "watch_id": watch["watch_id"], "head_sha": state["head_sha"],
                "expected_description": expected_description, "observed_description": status.get("description")}
    if watch.get("last_consumed_signal_id") == state["signal_id"]:
        return {"status": "duplicate_signal", "watch_id": watch["watch_id"], "head_sha": state["head_sha"],
                "signal_id": state["signal_id"]}
    return {
        "status": "ready" if state["actionable_thread_ids"] or state["review_ids"] else "no_actionable_review",
        "watch_id": watch["watch_id"], "task_id": watch["task_id"], "repository": state.get("repository", str(watch["repository"]).lower()),
        "pr_number": watch["pr_number"], **state, "workflow_url": status.get("target_url"),
        "reconciled_at": now.isoformat(),
    }


def acknowledge(path: Path, watch: dict[str, Any], result: dict[str, Any]) -> None:
    updated = dict(watch)
    updated["last_consumed_signal_id"] = result["signal_id"]
    updated["consumed_at"] = result["reconciled_at"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(updated, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def locked_reconcile(path: Path) -> dict[str, Any]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        watch = json.loads(path.read_text())
        result = reconcile(watch)
        if result["status"] == "ready":
            acknowledge(path, watch, result)
        return result


def main() -> int:
    result = {
        "status": "review_signal_status_unsupported",
        "blocker": "use fresh attested Saihai github_observe operations",
    }
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
