"""Read-only aggregation of local task, service, and execution state."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.parse import quote


def _now():
    return datetime.now(timezone.utc)


def _parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


class _SnapshotConnection(sqlite3.Connection):
    def close(self):
        try:
            super().close()
        finally:
            self.snapshot_directory.cleanup()


def _file_identity(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_only(path):
    """Inspect a stable private snapshot, never opening source SQLite for writes.

    SQLite mode=ro can still create WAL/SHM siblings. Copy a stable observed
    DB/WAL pair and let SQLite build any reader sidecars only in temporary
    storage. A concurrent source change makes this observation uncertain.
    """
    path = Path(path) if path else None
    if path is None:
        return None, {"state": "not_configured", "path": None}
    if not path.is_file():
        return None, {"state": "missing", "path": str(path)}
    snapshot = None
    try:
        source_files = [path, Path(str(path) + "-wal")]
        before = [_file_identity(source) for source in source_files]
        content = [source.read_bytes() if identity is not None else None
                   for source, identity in zip(source_files, before)]
        if before != [_file_identity(source) for source in source_files]:
            return None, {"state": "uncertain", "path": str(path),
                          "reason": "database changed during snapshot; read again"}
        snapshot = tempfile.TemporaryDirectory(prefix="agents-status-")
        copy = Path(snapshot.name) / "snapshot.sqlite3"
        for target, data in zip((copy, Path(str(copy) + "-wal")), content):
            if data is not None:
                with target.open("xb") as stream:
                    target.chmod(0o600)
                    stream.write(data)
        connection = sqlite3.connect(
            f"file:{quote(str(copy))}?mode=ro", uri=True,
            timeout=1.0, factory=_SnapshotConnection,
        )
        connection.snapshot_directory = snapshot
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection, {"state": "available", "path": str(path)}
    except (OSError, sqlite3.DatabaseError) as error:
        if snapshot is not None:
            snapshot.cleanup()
        return None, {"state": "corrupt", "path": str(path), "reason": str(error)}


def _tables(connection):
    return {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}


def _json(value, default):
    if value is None:
        return default
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _row_values(connection, query, args=()):
    return [dict(row) for row in connection.execute(query, args)]


def _issueization(task, now):
    state = task.get("issueization_state") or "unknown"
    raw_expires = task.get("claim_expires_at")
    expires = _parse_time(raw_expires)
    if state == "claimed" and (not raw_expires or expires is None):
        return {
            "state": "unknown",
            "raw_state": state,
            "claim_owner": task.get("claim_owner"),
            "claim_expires_at": raw_expires,
            "attempts": task.get("issueization_attempts", 0),
            "error": task.get("issueization_error"),
            "last_batch_result": "claim_expiry_unknown",
            "timestamp_state": "unknown",
        }
    if state == "claimed" and expires and expires <= now:
        return {
            "state": "reconcile_needed",
            "raw_state": state,
            "claim_owner": task.get("claim_owner"),
            "claim_expires_at": task.get("claim_expires_at"),
            "attempts": task.get("issueization_attempts", 0),
            "error": task.get("issueization_error"),
            "last_batch_result": task.get("issueization_error") or "claim_expired",
            "timestamp_state": "valid",
        }
    return {
        "state": state,
        "raw_state": state,
        "claim_owner": task.get("claim_owner"),
        "claim_expires_at": task.get("claim_expires_at"),
        "attempts": task.get("issueization_attempts", 0),
        "error": task.get("issueization_error"),
        "last_batch_result": task.get("issueization_error") or ("issued" if state == "issued" else None),
        "timestamp_state": "valid" if raw_expires else None,
    }


def _publication_evidence(task, completion):
    """Classify publication only from a readable, explicit receipt."""
    repository = task.get("repository")
    candidates = []
    for value in completion:
        if not isinstance(value, str):
            continue
        path = Path(value)
        if not path.is_file():
            continue
        try:
            receipt = json.loads(path.read_text())
        except (OSError, UnicodeError, TypeError, ValueError):
            continue
        if not isinstance(receipt, dict):
            continue
        readback = receipt.get("publication_readback")
        if not isinstance(readback, dict):
            continue
        candidates.append((path, readback))

    for path, readback in candidates:
        commit = readback.get("commit")
        if (not repository
                or (readback.get("repository") is not None
                    and readback.get("repository") != repository)
                or not isinstance(commit, str)
                or not re.fullmatch(r"[0-9a-fA-F]{40}", commit)):
            continue
        main = readback.get("main")
        main_is_sha = isinstance(main, str) and re.fullmatch(r"[0-9a-fA-F]{40}", main)
        ancestor_proof = (readback.get("commit_is_ancestor") is True
                          and readback.get("merge_base") == commit)
        if main == commit or (main_is_sha and ancestor_proof):
            # The service wrapper's ancestor readback includes canonical main;
            # it proves synchronization even when main advanced past commit.
            state = "main_synced"
        elif main is None and readback.get("merged") is True:
            state = "merged"
        else:
            continue
        return {
            "state": state,
            "receipt": str(path),
            "publication_readback": readback,
            "reason": "explicit publication receipt readback",
        }

    # The scoped publisher records its own durable receipt rather than the
    # service acceptance wrapper.  Bind it to the task's repository and only
    # accept stages that contain a full commit identity.
    for value in completion:
        if not isinstance(value, str):
            continue
        path = Path(value)
        if not path.is_file():
            continue
        try:
            receipt = json.loads(path.read_text())
        except (OSError, UnicodeError, TypeError, ValueError):
            continue
        if (not isinstance(receipt, dict) or not repository
                or receipt.get("repository") != repository):
            continue
        published = receipt.get("published_sha")
        main_sha = receipt.get("main_sha")
        stage = receipt.get("stage")
        status = receipt.get("status")
        if stage == "main_synced" and isinstance(main_sha, str) and re.fullmatch(r"[0-9a-fA-F]{40}", main_sha):
            return {"state": "main_synced", "receipt": str(path),
                    "publication_readback": receipt,
                    "reason": "explicit publication receipt main sync"}
        if (stage == "pushed" and status in {"published", "success"}
                and isinstance(published, str)
                and re.fullmatch(r"[0-9a-fA-F]{40}", published)):
            return {"state": "published", "receipt": str(path),
                    "publication_readback": receipt,
                    "reason": "explicit publication receipt remote readback"}
    return {
        "state": "unknown",
        "receipt": None,
        "publication_readback": None,
        "reason": "no valid publication receipt readback was found",
    }


def _lifecycle(task, acceptance, completion, publication_state="unknown"):
    raw = (task.get("execution_status") or "unknown").lower()
    if raw in {"running", "collecting", "reviewing", "publishing"}:
        return "running"
    if raw in {"auth_error", "auth_failed", "authentication_failed", "permission_denied"}:
        return "auth_failed"
    if raw in {"quality_failed", "review_findings", "failed", "quality_error"}:
        return "quality_failed"
    if raw in {"awaiting_user", "needs_user", "pending_user", "user_action"}:
        return "awaiting_user"
    if raw in {"merged_unsynced", "merged-but-unsynced"}:
        return "merged_unsynced"
    if raw in {"published", "merged", "usable", "verified", "completed", "complete"}:
        verified = bool(acceptance) and all(item["verified"] for item in acceptance)
        if verified and completion and publication_state != "unknown":
            return "usable" if raw == "usable" else raw if raw in {"verified", "published", "merged"} else "complete"
        return "awaiting_verification"
    if raw in {"planned", "assigned", "queued", "pending"}:
        return "planned"
    return raw or "unknown"


def _issueization_next_action(issueization):
    if issueization["state"] == "reconcile_needed":
        return "reconcile the remote Issue outcome before retrying issueization"
    if issueization["state"] == "unknown" and issueization.get("raw_state") == "claimed":
        return "inspect the claimed Issue outcome and expiry before resuming issueization"
    if issueization["state"] in {"unissued", "retry"}:
        return "issueization batch may claim this task"
    return None


def _next_action(lifecycle, issueization):
    if lifecycle == "running":
        return "wait for the worker update or inspect the persisted attempt"
    if lifecycle == "auth_failed":
        return "restore the subscription provider login, then resume the task"
    if lifecycle == "quality_failed":
        return "repair the recorded findings and rerun verification"
    if lifecycle == "awaiting_user":
        return "complete the pending user operation and record its evidence"
    if lifecycle == "merged_unsynced":
        return "sync main and verify the merged result"
    if lifecycle == "awaiting_verification":
        return "record verified acceptance and completion evidence"
    if lifecycle == "planned":
        return "start or resume the planned task before issueization"
    if lifecycle in {"complete", "published", "merged", "usable"}:
        return _issueization_next_action(issueization)
    return "inspect the task evidence and determine the next recovery action"


def _task_views(connection):
    required = {"tasks", "task_acceptance", "task_completion", "task_evidence"}
    if not required.issubset(_tables(connection)):
        missing = sorted(required - _tables(connection))
        raise sqlite3.DatabaseError("missing task tables: " + ", ".join(missing))
    tasks = _row_values(connection, "SELECT * FROM tasks ORDER BY created_at, id")
    tables = _tables(connection)
    evidence = _row_values(connection, "SELECT * FROM task_evidence ORDER BY created_at")
    acceptance = _row_values(connection, "SELECT * FROM task_acceptance ORDER BY created_at")
    completion = _row_values(connection, "SELECT * FROM task_completion ORDER BY recorded_at")
    issues = _row_values(connection, """SELECT ti.task_id,i.repository,i.issue_id,i.issue_url,i.title
                                      FROM task_issues ti JOIN issues i
                                      ON i.repository=ti.repository AND i.issue_id=ti.issue_id
                                      ORDER BY ti.linked_at""") if {"task_issues", "issues"}.issubset(tables) else []
    prs = _row_values(connection, """SELECT tp.task_id,p.repository,p.pr_id,p.pr_url,p.title
                                   FROM task_prs tp JOIN prs p
                                   ON p.repository=tp.repository AND p.pr_id=tp.pr_id
                                   ORDER BY tp.linked_at""") if {"task_prs", "prs"}.issubset(tables) else []
    units = _row_values(connection, """SELECT wut.task_id,wut.work_unit_id
                                     FROM work_unit_tasks wut ORDER BY wut.work_unit_id""") if "work_unit_tasks" in tables else []
    by_task = {}
    for item in evidence:
        by_task.setdefault(item["task_id"], {}).setdefault("evidence_links", []).append(item["link"])
    for item in acceptance:
        by_task.setdefault(item["task_id"], {}).setdefault("acceptance", []).append({
            "evidence": item["evidence"], "verified": bool(item["verified"]),
        })
    for item in completion:
        by_task.setdefault(item["task_id"], {}).setdefault("completion", []).append(item["evidence"])
    for item in issues:
        by_task.setdefault(item["task_id"], {}).setdefault("issues", []).append({
            "repository": item["repository"], "id": item["issue_id"], "url": item["issue_url"],
            "title": item["title"],
        })
    for item in prs:
        by_task.setdefault(item["task_id"], {}).setdefault("prs", []).append({
            "repository": item["repository"], "id": item["pr_id"], "url": item["pr_url"],
            "title": item["title"],
        })
    for item in units:
        by_task.setdefault(item["task_id"], {}).setdefault("work_units", []).append(item["work_unit_id"])

    now = _now()
    views = []
    for task in tasks:
        related = by_task.get(task["id"], {})
        acceptance_records = related.get("acceptance", [])
        completion_records = related.get("completion", [])
        issueization = _issueization(task, now)
        publication = _publication_evidence(task, completion_records)
        lifecycle = _lifecycle(task, acceptance_records, completion_records, publication["state"])
        acceptance_state = (
            "verified" if acceptance_records and all(item["verified"] for item in acceptance_records)
            else "unverified" if acceptance_records else "missing"
        )
        view = {
            "task_id": task["id"], "purpose": task["purpose"], "source": task.get("source"),
            "repository": task.get("repository"), "assignee": task.get("assignee"),
            "priority": task.get("priority"), "execution_status": task.get("execution_status"),
            "lifecycle": lifecycle, "issueization": issueization,
            "stages": {
                "execution": task.get("execution_status"),
                "acceptance": acceptance_state,
                "issueization": issueization["state"],
                "publication": publication["state"],
                "usable": lifecycle == "usable",
            },
            "work_units": related.get("work_units", []), "issues": related.get("issues", []),
            "prs": related.get("prs", []), "evidence_links": related.get("evidence_links", []),
            "acceptance": acceptance_records, "completion_evidence": completion_records,
            "publication_evidence": publication,
            "attempts": task.get("issueization_attempts", 0),
            "last_error": task.get("issueization_error"),
            "next_action": _next_action(lifecycle, issueization),
            "issueization_next_action": _issueization_next_action(issueization),
            "version": task.get("version"), "updated_at": task.get("updated_at"),
        }
        views.append(view)
    return views


def _requirements(connection, tasks):
    tables = _tables(connection)
    if "requirements" not in tables:
        return []
    links = _row_values(connection, "SELECT * FROM requirement_tasks") if "requirement_tasks" in tables else []
    by_requirement = {}
    for link in links:
        by_requirement.setdefault(link["requirement_id"], []).append(link["task_id"])
    by_task = {task["task_id"]: task for task in tasks}
    result = []
    for requirement in _row_values(connection, "SELECT * FROM requirements ORDER BY created_at, id"):
        task_ids = by_requirement.get(requirement["id"], [])
        acceptance = _json(requirement.get("acceptance"), [])
        verified = {
            record["evidence"]
            for task_id in task_ids
            for record in by_task.get(task_id, {}).get("acceptance", [])
            if record["verified"]
        }
        result.append({
            "id": requirement["id"], "text": requirement["text"], "status": requirement["status"],
            "task_ids": task_ids, "acceptance": acceptance,
            "acceptance_state": "verified" if acceptance and all(item in verified for item in acceptance)
            else "unverified" if acceptance else "missing",
        })
    return result


def _service_view(connection):
    required = {"service_jobs", "service_updates", "service_attempts"}
    if not required.issubset(_tables(connection)):
        missing = sorted(required - _tables(connection))
        raise sqlite3.DatabaseError("missing service tables: " + ", ".join(missing))
    jobs = _row_values(connection, "SELECT * FROM service_jobs ORDER BY created_at, id")
    updates = _row_values(connection, "SELECT * FROM service_updates ORDER BY job_id, sequence")
    attempts = _row_values(connection, "SELECT * FROM service_attempts ORDER BY job_id, attempt_number")
    updates_by_job = {}
    attempts_by_job = {}
    usage = {}
    for update in updates:
        update["evidence_links"] = _json(update.get("evidence_links"), [])
        updates_by_job.setdefault(update["job_id"], []).append(update)
    for attempt in attempts:
        attempt["result"] = _json(attempt.pop("result_json", None), {})
        attempts_by_job.setdefault(attempt["job_id"], []).append(attempt)
        reported = attempt["result"].get("usage", {}) if isinstance(attempt["result"], dict) else {}
        if isinstance(reported, dict):
            for key, value in reported.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0) + value
    result = []
    retries, holds, queued = [], [], []
    for job in jobs:
        item = dict(job)
        item["updates"] = updates_by_job.get(job["id"], [])
        item["attempts"] = attempts_by_job.get(job["id"], [])
        item["error_history"] = [x.get("error") for x in item["attempts"] if x.get("error")]
        state = (job.get("state") or "unknown").lower()
        error = (job.get("last_error") or "").lower()
        item["wait_kind"] = "held_cost_or_security" if state in {"held", "blocked"} or any(
            token in error for token in ("extra-billing", "security", "paid", "fee")) else (
                "technical_retry" if state in {"retry", "pending", "waiting"} else None)
        if item["wait_kind"] == "held_cost_or_security":
            holds.append(item)
        elif item["wait_kind"] == "technical_retry":
            retries.append(item)
        if state in {"pending", "retry", "waiting"}:
            queued.append({"job_id": job["id"], "task_id": job.get("task_id"),
                           "resource": job.get("resource"), "next_attempt_at": job.get("next_attempt_at")})
        result.append(item)
    return {"state": "available", "jobs": result, "queued_resources": queued,
            "retries": retries, "holds": holds, "usage": usage,
            "updates": updates, "attempts": attempts}


def _merge_service_progress(tasks, service):
    """Overlay persisted worker progress without allowing it to fake acceptance."""
    by_task = {}
    for job in service.get("jobs", []):
        by_task.setdefault(job.get("task_id"), []).append(job)
    for task in tasks:
        jobs = by_task.get(task["task_id"], [])
        task["service_jobs"] = [job["id"] for job in jobs]
        task["execution_history"] = [
            {"job_id": job["id"], "state": attempt.get("status"), "error": attempt.get("error"),
             "result": attempt.get("result", {})}
            for job in jobs for attempt in job.get("attempts", [])
        ]
        if not jobs:
            continue
        latest = jobs[-1]
        state = (latest.get("state") or "").lower()
        error = (latest.get("last_error") or "").lower()
        auth_failure = any(token in error for token in ("auth", "login", "unauthorized", "not logged"))
        if task["lifecycle"] in {"planned", "running"} and auth_failure:
            task["lifecycle"] = "auth_failed"
            task["stages"]["execution"] = state or "auth_error"
            task["next_action"] = _next_action("auth_failed", task["issueization"])
        elif task["lifecycle"] == "planned" and state in {"running", "reconciling"}:
            task["lifecycle"] = "running"
            task["stages"]["execution"] = state
            task["next_action"] = _next_action("running", task["issueization"])
        elif task["lifecycle"] == "planned" and state in {"failed", "needs_verification"}:
            task["lifecycle"] = "quality_failed" if state == "failed" else "awaiting_verification"
            task["stages"]["execution"] = state
            task["next_action"] = _next_action(task["lifecycle"], task["issueization"])
        task["last_error"] = task["last_error"] or latest.get("last_error")


def build_status(*, db_path=None, service_db_path=None, agents_root=None):
    """Build a status snapshot using only read-only file and SQLite operations."""
    if db_path is None and agents_root:
        db_path = Path(agents_root) / ".local" / "tasks.sqlite3"
    task_connection, task_source = _read_only(db_path)
    service_connection, service_source = _read_only(service_db_path)
    uncertainties = []
    tasks = []
    requirements = []
    if task_connection is not None:
        try:
            tasks = _task_views(task_connection)
            requirements = _requirements(task_connection, tasks)
        except sqlite3.DatabaseError as error:
            task_source = {**task_source, "state": "corrupt", "reason": str(error)}
            uncertainties.append("task store schema or content is unreadable")
        finally:
            task_connection.close()
    elif task_source["state"] in {"missing", "corrupt", "not_configured", "uncertain"}:
        uncertainties.append("task store is unavailable")
    service = {"state": service_source["state"], "jobs": [], "queued_resources": [],
               "retries": [], "holds": [], "usage": {}, "updates": [], "attempts": []}
    if service_connection is not None:
        try:
            service = _service_view(service_connection)
        except sqlite3.DatabaseError as error:
            service_source = {**service_source, "state": "corrupt", "reason": str(error)}
            service["state"] = "corrupt"
            uncertainties.append("service store schema or content is unreadable")
        finally:
            service_connection.close()
    elif service_source["state"] in {"corrupt", "uncertain"}:
        uncertainties.append("service store is unreadable")

    _merge_service_progress(tasks, service)

    next_actions = []
    counts = {}
    for task in tasks:
        counts[task["lifecycle"]] = counts.get(task["lifecycle"], 0) + 1
        if task["next_action"]:
            next_actions.append({"task_id": task["task_id"], "action": task["next_action"]})
    for item in service["holds"]:
        next_actions.append({"job_id": item["id"], "action": "held for explicit cost/security review"})
    for item in service["retries"]:
        next_actions.append({"job_id": item["id"], "action": "technical retry is scheduled; no user approval is implied"})
    complete = bool(tasks) and not uncertainties and all(task["lifecycle"] in {
        "complete", "verified", "published", "merged", "usable"
    } for task in tasks) and all(
        requirement["acceptance_state"] == "verified" for requirement in requirements
    )
    return {
        "read_only": True, "generated_at": _now().isoformat(), "complete": complete,
        "uncertain": bool(uncertainties), "uncertainties": uncertainties,
        "sources": {"tasks": task_source, "service": service_source},
        "tasks": tasks, "requirements": requirements, "counts": counts, "service": service,
        "diagnostics": {
            "drift": {
                "state": "unknown",
                "reason": "no persisted workspace snapshot was available to compare",
            },
        },
        "next_actions": next_actions,
    }


def emit_status(report, *, as_json=False):
    if as_json:
        return json.dumps(report, ensure_ascii=False, indent=2, default=str)
    lines = [f"status: {'uncertain' if report['uncertain'] else 'available'}"]
    for task in report["tasks"]:
        lines.append(f"{task['task_id']}  {task['lifecycle']}  {task['purpose']}")
    for action in report["next_actions"]:
        lines.append(f"next: {action.get('task_id', action.get('job_id'))}  {action['action']}")
    return "\n".join(lines)
