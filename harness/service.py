"""Durable, App-independent task execution service.

This module owns scheduling state and launchd integration.  GitHub issue
creation is intentionally outside this process.  A worker may execute a task
but reports ``needs_verification`` until explicit evidence is recorded.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sqlite3
import string
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

from .tasks import ConflictError, TaskStore


class AuthError(RuntimeError):
    """Subscription authentication is absent or an API route was requested."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value)


def load_agents_env(path: str | os.PathLike, env=None):
    """Load a simple .env without shell expansion or command execution."""
    result = dict(os.environ if env is None else env)
    path = Path(path)
    if not path.is_file():
        return result
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", line) or "$(" in line or "`" in line:
            raise ValueError(f"unsupported .env syntax at line {line_number}")
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        try:
            result[key] = string.Template(value).substitute(result)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"unresolved .env variable at line {line_number}") from exc
    return result


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS service_jobs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  run_dir TEXT NOT NULL,
  resource TEXT NOT NULL DEFAULT 'default',
  prompt TEXT NOT NULL,
  context TEXT NOT NULL,
  model TEXT NOT NULL,
  effort TEXT NOT NULL,
  timeout REAL NOT NULL,
  retry_base REAL NOT NULL,
  retry_max REAL NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  attempts_count INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS service_jobs_ready ON service_jobs(state, next_attempt_at);
CREATE TABLE IF NOT EXISTS service_updates (
  job_id TEXT NOT NULL REFERENCES service_jobs(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  message TEXT NOT NULL,
  evidence_links TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  PRIMARY KEY(job_id, sequence)
);
CREATE TABLE IF NOT EXISTS service_attempts (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES service_jobs(id) ON DELETE CASCADE,
  attempt_number INTEGER NOT NULL,
  pid INTEGER,
  identity TEXT,
  run_dir TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  result_json TEXT,
  error TEXT
);
"""


class WorkspaceLock:
    """Process-lifetime lock for one workspace/resource pair."""

    def __init__(self, workspace, lock_root, resource="default"):
        self.workspace = Path(workspace).resolve()
        self.resource = resource
        self.lock_root = Path(lock_root)
        self.lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace_key = hashlib.sha256(str(self.workspace).encode()).hexdigest()
        resource_key = hashlib.sha256(str(resource).encode()).hexdigest()
        self.paths = [self.lock_root / "workspace" / f"{workspace_key}.lock"]
        if resource != "default":
            self.paths.append(self.lock_root / "resource" / f"{resource_key}.lock")
        self.path = self.paths[0]
        self._files = []

    def acquire(self, blocking=True):
        if self._files:
            return True
        opened = []
        current_file = None
        try:
            for path in self.paths:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                file = path.open("a+"); current_file = file; path.chmod(0o600)
                if os.name == "nt":
                    import msvcrt
                    mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                    msvcrt.locking(file.fileno(), mode, 1)
                else:
                    import fcntl
                    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                    fcntl.flock(file.fileno(), flags)
                opened.append(file)
            self._files = opened
            return True
        except (BlockingIOError, OSError):
            if current_file is not None and current_file not in opened:
                current_file.close()
            for file in reversed(opened):
                try:
                    if os.name != "nt":
                        import fcntl
                        fcntl.flock(file.fileno(), fcntl.LOCK_UN)
                finally:
                    file.close()
            return False

    def release(self):
        if not self._files:
            return
        for file in reversed(self._files):
            try:
                if os.name == "nt":
                    import msvcrt
                    file.seek(0); msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)
            finally:
                file.close()
        self._files = []

    def __enter__(self):
        if not self.acquire():
            raise BlockingIOError(f"workspace resource is locked: {self.workspace}")
        return self

    def __exit__(self, *_):
        self.release()


class ServiceStore:
    def __init__(self, db_path=None, task_store=None, *, agents_root=None,
                 vault_root=None, timeout=15.0):
        self.tasks = task_store or TaskStore(agents_root=agents_root, vault_root=vault_root)
        if db_path is None:
            path = self.tasks.agents_root / ".local" / "service.sqlite3"
        else:
            path = Path(db_path)
        self.db_path = Path(path)
        if self.db_path.is_symlink():
            raise ValueError(f"database path must not be a symlink: {self.db_path}")
        if self.db_path.parent.is_symlink():
            raise ValueError(f"database parent must not be a symlink: {self.db_path.parent}")
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.db_path.exists():
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.db_path, flags, 0o600)
            os.close(fd)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, timeout=timeout, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        busy_ms = max(1, int(float(timeout) * 1000))
        self._conn.execute(f"PRAGMA busy_timeout={busy_ms}")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate_schema()
        self._conn.commit(); self._harden()

    def _migrate_schema(self):
        """Add durable identity columns to databases from the first service release."""
        job_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(service_jobs)")}
        if "run_dir" not in job_columns:
            self._conn.execute("ALTER TABLE service_jobs ADD COLUMN run_dir TEXT")
            prefix = str(self.tasks.vault_root / "01-Projects" / "agent-runs" / "service-")
            rows = self._conn.execute("SELECT id FROM service_jobs WHERE run_dir IS NULL").fetchall()
            self._conn.executemany("UPDATE service_jobs SET run_dir=? WHERE id=?",
                                  [(prefix + row[0], row[0]) for row in rows])
        for column, kind in (("verification_pid", "INTEGER"), ("verification_identity", "TEXT")):
            if column not in job_columns:
                self._conn.execute(f"ALTER TABLE service_jobs ADD COLUMN {column} {kind}")
        attempt_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(service_attempts)")}
        if "identity" not in attempt_columns:
            self._conn.execute("ALTER TABLE service_attempts ADD COLUMN identity TEXT")
        if "run_dir" not in attempt_columns:
            self._conn.execute("ALTER TABLE service_attempts ADD COLUMN run_dir TEXT")

    def _harden(self):
        for path in (self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
            if path.exists():
                path.chmod(0o600)

    @contextmanager
    def tx(self):
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.commit(); self._harden()
            except Exception:
                self._conn.rollback(); raise

    def close(self):
        with self._lock:
            self._conn.close()

    @staticmethod
    def _process_identity(pid):
        identity = {"pid": int(pid)}
        try:
            fields = Path(f"/proc/{int(pid)}/stat").read_text().split()
            if len(fields) > 21:
                identity["start_ticks"] = fields[21]
        except (OSError, ValueError):
            pass
        if "start_ticks" not in identity:
            try:
                observed = subprocess.run(
                    ["ps", "-p", str(int(pid)), "-o", "lstart=,command="],
                    capture_output=True, text=True, timeout=2, check=False)
                fields = observed.stdout.strip().split(None, 5)
                if observed.returncode == 0 and len(fields) >= 6:
                    identity["start_time"] = " ".join(fields[:5])
                    identity["command"] = fields[5]
            except (OSError, subprocess.TimeoutExpired):
                pass
        return identity

    def _pid_state(self, pid, recorded_identity=None):
        if not pid:
            return "unknown"
        try:
            os.kill(int(pid), 0)
            if recorded_identity:
                current = self._process_identity(pid)
                recorded = json.loads(recorded_identity) if isinstance(recorded_identity, str) else recorded_identity
                for key in ("start_ticks", "start_time"):
                    if recorded.get(key) and current.get(key) and recorded[key] != current[key]:
                        return "dead"
                if recorded.get("command") and current.get("command") and recorded["command"] != current["command"]:
                    return "unknown"
                if not any(recorded.get(key) and current.get(key) for key in ("start_ticks", "start_time")):
                    return "unknown"
            return "alive"
        except ProcessLookupError:
            return "dead"
        except PermissionError:
            return "unknown"
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ESRCH:
                return "dead"
            return "unknown"

    def _attempt_state(self, row):
        """Include a surviving runner/provider before reclaiming a service attempt."""
        worker_state = self._pid_state(row["pid"], row["identity"])
        if worker_state != "dead":
            return worker_state
        run_dir = row["attempt_run_dir"] or str(Path(row["run_dir"]) / f"attempt-{row['attempt_number']}")
        for path in Path(run_dir).glob("*-state.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError, TypeError):
                return "unknown"
            if not isinstance(record, dict):
                return "unknown"
            for collector in record.get("collectors", []):
                if not isinstance(collector, dict):
                    return "unknown"
                collector_state = self._pid_state(collector.get("pid"), collector.get("identity"))
                if collector_state != "dead":
                    return collector_state
            if record.get("status") not in ("starting", "running", "collecting"):
                continue
            provider_state = self._pid_state(record.get("pid"), record.get("identity"))
            if provider_state in ("alive", "unknown"):
                return provider_state
        return "dead"

    def recover_stale_jobs(self, reconciler=None):
        """Reconcile only jobs whose recorded worker is definitely dead.

        A live or uninspectable PID remains occupied.  The read-only
        reconciler runs while both resource locks are held before retrying.
        """
        with self._lock:
            rows = list(self._conn.execute("SELECT j.*,a.id AS attempt_id,a.pid,a.identity,a.run_dir AS attempt_run_dir,a.attempt_number,a.status AS attempt_status FROM service_jobs j JOIN service_attempts a ON a.job_id=j.id AND a.status IN ('running','reconciling') WHERE j.state IN ('running','reconciling')"))
        recovered = []
        for row in rows:
            if self._attempt_state(row) != "dead":
                continue
            lock = WorkspaceLock(row["workspace"], self.tasks.agents_root / ".local" / "service-locks", row["resource"])
            if not lock.acquire(blocking=False):
                continue
            try:
                with self.tx() as conn:
                    current = conn.execute("SELECT * FROM service_jobs WHERE id=? AND state IN ('running','reconciling')", (row["id"],)).fetchone()
                    if current is None:
                        continue
                    stamp = now()
                    conn.execute("UPDATE service_jobs SET state='reconciling',last_error=?,updated_at=? WHERE id=?", ("worker died; read-only reconciliation in progress", stamp, row["id"]))
                    conn.execute("UPDATE service_attempts SET status='reconciling',ended_at=?,error=? WHERE id=? AND status IN ('running','reconciling')", (stamp, "worker process is definitely dead", row["attempt_id"]))
                try:
                    result = (reconciler or default_reconciler)(self._job(current), dict(row))
                except Exception as exc:
                    result = {"safe_to_resume": False, "reason": "receipt reconciliation failed", "error": str(exc)}
                safe = isinstance(result, dict) and result.get("safe_to_resume") is True
                state = "retry" if safe else "needs_verification"
                with self.tx() as conn:
                    conn.execute("UPDATE service_jobs SET state=?,next_attempt_at=?,last_error=?,updated_at=? WHERE id=?", (state, now() if safe else None, result.get("reason") if isinstance(result, dict) else "reconciliation requires verification", now(), row["id"]))
                    conn.execute("UPDATE service_attempts SET status=?,result_json=? WHERE id=?", (state, json.dumps(result or {}, ensure_ascii=False), row["attempt_id"]))
                task = self.tasks.get_task(row["task_id"])
                if task is not None:
                    try:
                        self.tasks.update_task(task["id"], expected_version=task["version"],
                                               execution_status="running" if state == "retry" else "needs_verification")
                    except ConflictError:
                        pass
                recovered.append({"job_id": row["id"], "state": state})
            finally:
                lock.release()
        return recovered

    def recover_interrupted_verification(self):
        """Requeue only verification whose recorded owner is definitely dead."""
        recovered = []
        with self.tx() as conn:
            rows = list(conn.execute("SELECT * FROM service_jobs WHERE state='verifying'"))
            for row in rows:
                if self._pid_state(row["verification_pid"], row["verification_identity"]) != "dead":
                    continue
                conn.execute("UPDATE service_jobs SET state='needs_verification',verification_pid=NULL,verification_identity=NULL,last_error=?,updated_at=? WHERE id=?", ("verification interrupted; retrying independent read-only review", now(), row["id"]))
                conn.execute("UPDATE service_attempts SET status='needs_verification' WHERE job_id=? AND status='verifying'", (row["id"],))
                recovered.append(row["id"])
        return recovered

    def _start_verification(self, job_id):
        with self.tx() as conn:
            changed = conn.execute("UPDATE service_jobs SET state='verifying',verification_pid=?,verification_identity=?,updated_at=? WHERE id=? AND state='needs_verification'", (os.getpid(), json.dumps(self._process_identity(os.getpid())), now(), job_id)).rowcount
            if changed:
                conn.execute("UPDATE service_attempts SET status='verifying' WHERE job_id=? AND status='needs_verification'", (job_id,))
            return changed == 1

    def _reset_verification(self, job_id, diagnostic):
        with self.tx() as conn:
            conn.execute("UPDATE service_jobs SET state='needs_verification',last_error=?,updated_at=? WHERE id=? AND state='verifying'", (diagnostic, now(), job_id))
            conn.execute("UPDATE service_attempts SET status='needs_verification',error=? WHERE job_id=? AND status='verifying'", (diagnostic, job_id))

    def _job(self, row):
        if row is None:
            return None
        result = dict(row)
        result["updates"] = [{"message": x[0], "evidence_links": json.loads(x[1])} for x in self._conn.execute("SELECT message,evidence_links FROM service_updates WHERE job_id=? ORDER BY sequence", (row["id"],))]
        result["attempts"] = [dict(x) for x in self._conn.execute("SELECT * FROM service_attempts WHERE job_id=? ORDER BY attempt_number", (row["id"],))]
        for item in result["attempts"]:
            if item.get("result_json"):
                try: item["result"] = json.loads(item.pop("result_json"))
                except ValueError: item["result"] = item.pop("result_json")
        return result

    def get_job(self, job_id):
        with self._lock:
            return self._job(self._conn.execute("SELECT * FROM service_jobs WHERE id=?", (job_id,)).fetchone())

    def list_jobs(self, state=None):
        with self._lock:
            if state:
                rows = self._conn.execute("SELECT * FROM service_jobs WHERE state=? ORDER BY created_at", (state,))
            else:
                rows = self._conn.execute("SELECT * FROM service_jobs ORDER BY created_at")
            return [self._job(row) for row in rows]

    def list_attempts(self, job_id):
        with self._lock:
            rows = self._conn.execute("SELECT * FROM service_attempts WHERE job_id=? ORDER BY attempt_number", (job_id,))
            return [dict(row) for row in rows]

    def enroll(self, task_id, workspace, prompt, context, *, model="gpt-5.6-luna", effort="low",
               timeout=300.0, retry_base=30.0, retry_max=3600.0, resource="default"):
        workspace = Path(workspace).resolve()
        if not workspace.is_dir(): raise ValueError(f"workspace does not exist: {workspace}")
        if not prompt or not context: raise ValueError("prompt and context are required")
        task = self.tasks.get_task(task_id)
        if task is None: raise KeyError(f"unknown task: {task_id}")
        with self.tx() as conn:
            existing = conn.execute("SELECT id FROM service_jobs WHERE task_id=? AND state NOT IN ('completed', 'cancelled') LIMIT 1", (task_id,)).fetchone()
            if existing:
                return self.get_job(existing[0])
            jid = "job_" + hashlib.sha256(f"{task_id}\0{time.time_ns()}".encode()).hexdigest()[:24]
            run_dir = self.tasks.vault_root / "01-Projects" / "agent-runs" / f"service-{jid}"
            stamp = now()
            conn.execute("""INSERT INTO service_jobs
              (id,task_id,workspace,run_dir,resource,prompt,context,model,effort,timeout,retry_base,retry_max,state,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                         (jid, task_id, str(workspace), str(run_dir), resource, prompt, context, model, effort, float(timeout), float(retry_base), float(retry_max), stamp, stamp))
        return self.get_job(jid)

    def record_update(self, job_id, message, evidence_links=()):
        with self.tx() as conn:
            if not conn.execute("SELECT 1 FROM service_jobs WHERE id=?", (job_id,)).fetchone(): raise KeyError(job_id)
            row = conn.execute("SELECT COALESCE(MAX(sequence),0) FROM service_updates WHERE job_id=?", (job_id,)).fetchone()
            conn.execute("INSERT INTO service_updates VALUES (?,?,?,?,?)", (job_id, row[0] + 1, message, json.dumps(list(dict.fromkeys(evidence_links))), now()))
            conn.execute("UPDATE service_jobs SET updated_at=? WHERE id=?", (now(), job_id))
        return self.get_job(job_id)

    def _dependencies_ready(self, task):
        for dependency_id in task.get("dependencies", []):
            dependency = self.tasks.get_task(dependency_id)
            if dependency is None or dependency["execution_status"] not in ("completed", "verified"):
                return False
            if not dependency.get("acceptance_records") or not all(x["verified"] for x in dependency["acceptance_records"]):
                return False
            if not dependency.get("completion_evidence"):
                return False
            evidence = [str(item).lower() for item in dependency.get("completion_evidence", []) + dependency.get("evidence_links", [])]
            if not any(("main" in item and "sync" in item) or "merged" in item for item in evidence):
                return False
        return True

    def _ready_rows(self):
        with self._lock:
            rows = list(self._conn.execute("SELECT * FROM service_jobs WHERE state IN ('pending','retry') AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at", (now(),)))
        return rows

    def _claim_job(self, job_id):
        with self.tx() as conn:
            row = conn.execute("SELECT * FROM service_jobs WHERE id=? AND state IN ('pending','retry') AND (next_attempt_at IS NULL OR next_attempt_at<=?)", (job_id, now())).fetchone()
            if row is None: return None
            task = self.tasks.get_task(row["task_id"])
            if task is None or not self._dependencies_ready(task): return None
            previous = conn.execute(
                "SELECT * FROM service_attempts WHERE job_id=? ORDER BY attempt_number DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            resume = False
            if previous is not None and previous["status"] == "retry" and previous["result_json"]:
                try:
                    resume = json.loads(previous["result_json"]).get("safe_to_resume") is True
                except (TypeError, ValueError, AttributeError):
                    resume = False
            attempt = row["attempts_count"] if resume else row["attempts_count"] + 1; stamp = now()
            conn.execute("UPDATE service_jobs SET state='running',attempts_count=?,updated_at=?,last_error=NULL WHERE id=?", (attempt, stamp, job_id))
            if resume:
                aid = previous["id"]
                attempt_run_dir = previous["run_dir"] or str(Path(row["run_dir"]) / f"attempt-{attempt}")
                conn.execute("""UPDATE service_attempts
                    SET pid=?,identity=?,status='running',started_at=?,ended_at=NULL,error=NULL
                    WHERE id=?""", (os.getpid(), json.dumps(self._process_identity(os.getpid())), stamp, aid))
            else:
                aid = f"attempt_{hashlib.sha256(f'{job_id}:{attempt}'.encode()).hexdigest()[:24]}"
                attempt_run_dir = str(Path(row["run_dir"]) / f"attempt-{attempt}")
                conn.execute("""INSERT INTO service_attempts
                    (id,job_id,attempt_number,pid,identity,run_dir,status,started_at,ended_at,result_json,error)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (aid, job_id, attempt, os.getpid(),
                    json.dumps(self._process_identity(os.getpid())), attempt_run_dir, "running", stamp,
                    None, None, None))
            claimed = dict(conn.execute("SELECT * FROM service_jobs WHERE id=?", (job_id,)).fetchone())
            claimed["attempt_id"] = aid
            claimed["attempt_run_dir"] = attempt_run_dir
            return claimed

    def _claim_next(self):
        for row in self._ready_rows():
            task = self.tasks.get_task(row["task_id"])
            if task is None or not self._dependencies_ready(task):
                continue
            lock = WorkspaceLock(row["workspace"], self.tasks.agents_root / ".local" / "service-locks", row["resource"])
            if not lock.acquire(blocking=False):
                continue
            claimed = self._claim_job(row["id"])
            if claimed is not None:
                try:
                    current = self.tasks.get_task(claimed["task_id"])
                    self.tasks.update_task(claimed["task_id"], expected_version=current["version"], execution_status="running")
                except ConflictError:
                    pass
                claimed["_lock"] = lock
                return claimed
            lock.release()
        return None

    def _finish_attempt(self, job, result=None, error=None):
        status = result.get("status") if isinstance(result, dict) else "failed"
        succeeded = status in ("completed", "success")
        terminal = "needs_verification" if succeeded else "retry"
        attempt_status = "needs_verification" if succeeded else "retry"
        attempt_no = job["attempts_count"]
        if succeeded:
            next_at = None
        else:
            delay = min(job["retry_max"], job["retry_base"] * (2 ** max(0, attempt_no - 1)))
            next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
        stamp = now()
        with self.tx() as conn:
            conn.execute("UPDATE service_jobs SET state=?,next_attempt_at=?,last_error=?,updated_at=? WHERE id=?", (terminal, next_at, error or (result.get("text") if isinstance(result, dict) else None), stamp, job["id"]))
            conn.execute("UPDATE service_attempts SET status=?,ended_at=?,result_json=?,error=? WHERE job_id=? AND attempt_number=?", (attempt_status, stamp, json.dumps(result or {}, ensure_ascii=False), error, job["id"], attempt_no))
        task = self.tasks.get_task(job["task_id"])
        if task is not None:
            try:
                current = self.tasks.get_task(job["task_id"])
                self.tasks.update_task(job["task_id"], expected_version=current["version"], execution_status="needs_verification" if succeeded else "running")
            except ConflictError:
                pass
        return {"status": terminal, "job_id": job["id"], "attempt": attempt_no}

    def run_once(self, executor: Callable | None = None, verifier: Callable | None = None):
        job = self._claim_next()
        if job is None:
            blocked = any(self.tasks.get_task(row["task_id"]) is not None and not self._dependencies_ready(self.tasks.get_task(row["task_id"])) for row in self._ready_rows())
            return {"status": "blocked" if blocked else "idle", **({"reason": "dependencies"} if blocked else {})}
        lock = job.pop("_lock")
        try:
            if executor is None:
                try:
                    self.auth_guard(load_agents_env(self.tasks.agents_root / ".env"))
                except Exception as exc:
                    return self._finish_attempt(job, result={"status": "failed", "text": str(exc)}, error=str(exc))
                executor = default_executor
            try:
                latest = self.get_job(job["id"])
                result = executor({"job_id": job["id"], "task_id": job["task_id"], "workspace": job["workspace"], "run_dir": job.get("attempt_run_dir") or str(Path(job["run_dir"]) / f"attempt-{job['attempts_count']}"), "attempt_id": job["attempt_id"], "attempt": job["attempts_count"], "agents_root": str(self.tasks.agents_root), "vault_root": str(self.tasks.vault_root), "prompt": job["prompt"], "context": job["context"], "model": job["model"], "effort": job["effort"], "timeout": job["timeout"], "updates": latest["updates"]})
            except Exception as exc:
                return self._finish_attempt(job, result={"status": "failed", "text": str(exc)}, error=str(exc))
            outcome = self._finish_attempt(job, result=result)
            if outcome["status"] == "needs_verification" and verifier is not None:
                started = self._start_verification(job["id"])
                if not started:
                    return outcome
                try:
                    return self.verify_with_agent(job["id"], verifier)
                except Exception as exc:
                    self._reset_verification(job["id"], str(exc))
                    return {"status": "needs_verification", "job_id": job["id"], "verification_error": str(exc)}
            return outcome
        finally:
            lock.release()

    def verify(self, job_id, evidence):
        job = self.get_job(job_id)
        if job is None: raise KeyError(job_id)
        task = self.tasks.get_task(job["task_id"])
        if not task.get("acceptance_records") or not all(x["verified"] for x in task["acceptance_records"]):
            raise ValueError("all acceptance evidence must be verified first")
        evidence_text = [str(item).lower() for item in task.get("completion_evidence", [])]
        evidence_text += [str(item).lower() for item in task.get("evidence_links", [])]
        evidence_text.append(str(evidence).lower())
        merged = any(re.search(r"\bmerge(?:d)?\b", item) or "pull request" in item or "/pull/" in item for item in evidence_text)
        main_sync = any(("main" in item and "sync" in item) or "merged" in item for item in evidence_text)
        if not merged or not main_sync:
            raise ValueError("merge and main-sync evidence are required")
        task = self.tasks.add_completion_evidence(task["id"], evidence)
        task = self.tasks.update_task(task["id"], expected_version=task["version"], execution_status="verified")
        with self.tx() as conn:
            conn.execute("UPDATE service_jobs SET state='completed',next_attempt_at=NULL,updated_at=? WHERE id=?", (now(), job_id))
        return self.get_job(job_id)

    def verify_with_agent(self, job_id, verifier):
        job = self.get_job(job_id)
        try:
            result = verifier({"job": job, "task": self.tasks.get_task(job["task_id"]), "agents_root": str(self.tasks.agents_root), "vault_root": str(self.tasks.vault_root)})
        except Exception as exc:
            self._reset_verification(job_id, str(exc))
            return {"status": "needs_verification", "job_id": job_id, "verification_error": str(exc)}
        findings = result.get("findings", []) if isinstance(result, dict) else []
        if findings:
            links = tuple(item for item in (result.get("evidence_links", []) if isinstance(result, dict) else []) if isinstance(item, str))
            for finding in findings:
                message = finding if isinstance(finding, str) else json.dumps(finding, ensure_ascii=False, sort_keys=True)
                self.record_update(job_id, "Verification finding adopted: " + message, links)
            with self.tx() as conn:
                conn.execute("UPDATE service_jobs SET state='retry',next_attempt_at=?,last_error=?,updated_at=? WHERE id=?", (now(), "verification findings require repair", now(), job_id))
                conn.execute("UPDATE service_attempts SET status='repair_required' WHERE job_id=? AND attempt_number=(SELECT MAX(attempt_number) FROM service_attempts WHERE job_id=?)", (job_id, job_id))
            task = self.tasks.get_task(job["task_id"])
            if task is not None:
                try:
                    self.tasks.update_task(task["id"], expected_version=task["version"], execution_status="running")
                except ConflictError:
                    pass
            return {"status": "retry", "job_id": job_id, "verification": result, "repair_required": True}
        if not isinstance(result, dict) or not result.get("acceptance") or not result.get("merge") or not result.get("main_sync"):
            self._reset_verification(job_id, "verification criteria remain incomplete")
            return {"status": "needs_verification", "job_id": job_id, "verification": result or {}}
        evidence = result.get("evidence")
        if not evidence:
            self._reset_verification(job_id, "verification returned no evidence")
            return {"status": "needs_verification", "job_id": job_id, "verification": result}
        proof = f"{evidence};merge;main-sync"
        try:
            verified = self.verify(job_id, proof)
        except Exception as exc:
            self._reset_verification(job_id, str(exc))
            return {"status": "needs_verification", "job_id": job_id, "verification": result,
                    "verification_error": str(exc)}
        return {"status": "verified", "job_id": job_id, "job": verified, "verification": result}

    def verify_pending(self, verifier):
        outcomes = []
        for job in self.list_jobs("needs_verification"):
            if self._start_verification(job["id"]):
                outcomes.append(self.verify_with_agent(job["id"], verifier))
        return outcomes

    @staticmethod
    def auth_guard(env, login_check=None):
        blocked = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CODEX_API_KEY")
        if any(env.get(key) for key in blocked):
            raise AuthError("API inference routes are forbidden; use ChatGPT subscription login")
        if login_check is None:
            def login_check(actual):
                try:
                    result = subprocess.run(["codex", "login", "status"], env=actual, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
                    text = (result.stdout + "\n" + result.stderr).lower()
                    return result.returncode == 0 and "chatgpt" in text and any(marker in text for marker in ("logged in", "authenticated", "signed in"))
                except (OSError, subprocess.TimeoutExpired): return False
        if not login_check(env):
            raise AuthError("codex login status is not authenticated")
        return True


def default_executor(spec):
    from .runner import Job, run_job, resume_job
    agents_root = Path(spec["agents_root"]).resolve()
    vault = Path(spec["vault_root"]).resolve()
    if not agents_root.is_dir() or not vault.is_dir():
        raise ValueError("explicit AGENTS_ROOT and AGENTS_VAULT_ROOT directories are required")
    env = load_agents_env(agents_root / ".env")
    prompt = spec["prompt"] + "\n\nContext:\n" + spec["context"]
    for update in spec.get("updates", []):
        prompt += f"\n\nUpdate: {update['message']}\nEvidence: {', '.join(update['evidence_links'])}"
    common = agents_root / "COMMON-AGENTS.md"
    if common.is_file():
        prompt += "\n\nCurrent common policy:\n" + common.read_text()
    job = Job(workspace=Path(spec["workspace"]), vault=vault, prompt=prompt,
              mode="run", provider="codex", codex_model=spec["model"], effort=spec["effort"],
              timeout=spec["timeout"], fallback=False, run_dir=Path(spec["run_dir"]))
    run_dir = Path(spec["run_dir"])
    if run_dir.exists():
        return resume_job(job, run_dir, env)
    return run_job(job, env, run_dir=run_dir)


def default_reconciler(job, attempt):
    """Read only receipt reconciliation after a definitely dead worker."""
    attempt = dict(attempt)
    run_dir = Path(attempt.get("attempt_run_dir") or attempt.get("run_dir") or job["run_dir"])
    result_file = run_dir / "result.json"
    if not result_file.is_file():
        return {"safe_to_resume": False, "reason": "no durable result receipt; reconciliation required"}
    try:
        result = json.loads(result_file.read_text())
    except (OSError, ValueError):
        return {"safe_to_resume": False, "reason": "unreadable result receipt"}
    if result.get("status") in ("completed", "review_findings", "review_incomplete"):
        return {"safe_to_resume": False, "reason": "receipt requires acceptance verification", "receipt": result}
    return {"safe_to_resume": True, "reason": "receipt is resumable", "receipt": result}


def default_verifier(spec):
    """Use an actual read-only subscription Codex turn for acceptance review."""
    task = spec["task"]
    env = load_agents_env(Path(spec["agents_root"]) / ".env")
    ServiceStore.auth_guard(env)
    prompt = json.dumps({
        "task": task,
        "job": spec["job"],
        "instructions": (
            "Act as an independent read-only verifier. Inspect the workspace, saved execution result and artifacts, "
            "the task acceptance criteria, current git status/log, and the public commit/merge/main synchronization. "
            "Do not edit files, run write commands, or infer completion from words alone. Return JSON only: "
            "{acceptance:boolean, merge:boolean, main_sync:boolean, findings:[objects], evidence:string, "
            "evidence_links:[strings]}. Every acceptance claim must cite observed evidence. "
            "Use findings for any missing or incorrect implementation and explain the repair required."
        ),
    }, ensure_ascii=False)
    argv = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--json",
            "--skip-git-repo-check", "-m", "gpt-5.6-luna", "-s", "read-only",
            "-c", 'approval_policy="never"', "-c", 'model_reasoning_effort="low"',
            "--disable", "multi_agent", "-"]
    try:
        result = subprocess.run(argv, input=prompt, env=env, cwd=spec["job"]["workspace"],
                                capture_output=True, text=True, timeout=min(float(spec["job"].get("timeout", 300)), 300))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuthError(f"verification agent unavailable: {type(exc).__name__}") from exc
    if result.returncode:
        raise AuthError("verification agent did not complete")
    messages = []
    completed = None
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            if isinstance(item.get("text"), str): messages.append(item["text"])
        if event.get("type") == "turn.completed": completed = event
    # Current subscription CLI emits a completed event with no status field;
    # returncode=0 plus that terminal event is its success signal. Explicit
    # failure statuses remain rejected.
    if completed is None or (completed.get("status") not in (None, "completed", "success", "succeeded")):
        raise AuthError("verification agent turn did not complete successfully")
    text = "\n".join(messages).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        value = json.loads(candidate)
    except (TypeError, ValueError) as exc:
        match = re.search(r"\{.*\}", candidate, flags=re.S)
        if not match:
            raise ValueError("verification agent returned malformed JSON") from exc
        try:
            value = json.loads(match.group(0))
        except (TypeError, ValueError) as nested:
            raise ValueError("verification agent returned malformed JSON") from nested
    if not isinstance(value, dict) or not all(isinstance(value.get(key), bool) for key in ("acceptance", "merge", "main_sync")):
        raise ValueError("verification agent omitted boolean acceptance verdict")
    if not isinstance(value.get("findings", []), list):
        raise ValueError("verification agent findings must be a list")
    return value


class Scheduler:
    def __init__(self, store, *, poll_interval=30.0, stop_event=None, max_workers=2,
                 coordinator_reserved=1, verification_executor=None, reconciler=None):
        self.store = store; self.poll_interval = poll_interval; self.stop_event = stop_event or threading.Event(); self._wait = self.stop_event.wait
        self.max_workers = max(1, int(max_workers)); self.coordinator_reserved = max(0, int(coordinator_reserved))
        self.worker_capacity = max(1, self.max_workers - self.coordinator_reserved)
        self.verification_executor = verification_executor; self.reconciler = reconciler
        self._prepared = False; self._prepare_lock = threading.Lock()

    def _prepare(self):
        # A surviving provider can finish after scheduler startup. Revisit
        # persisted ownership on each scheduling pass, without model polling.
        with self._prepare_lock:
            self.store.recover_stale_jobs(self.reconciler)
            self.store.recover_interrupted_verification()

    def run_once(self, executor=None):
        self._prepare()
        result = self.store.run_once(executor=executor, verifier=None)
        if result.get("status") == "needs_verification" and self.verification_executor is not None:
            if not self.store._start_verification(result["job_id"]):
                return result
            try:
                return self.store.verify_with_agent(result["job_id"], self.verification_executor)
            except Exception as exc:
                self.store._reset_verification(result["job_id"], str(exc))
                return {"status": "needs_verification", "job_id": result["job_id"], "verification_error": str(exc)}
        return result

    def run_forever(self, executor=None):
        worker_pool = ThreadPoolExecutor(max_workers=self.worker_capacity, thread_name_prefix="agents-worker")
        verification_pool = (ThreadPoolExecutor(max_workers=max(1, self.coordinator_reserved), thread_name_prefix="agents-verifier")
                             if self.verification_executor is not None else None)
        workers = {}
        verifiers = {}
        try:
            while not self.stop_event.is_set():
                self._prepare()
                while len(workers) < self.worker_capacity:
                    future = worker_pool.submit(self.store.run_once, executor, None)
                    workers[future] = True
                if verification_pool is not None:
                    for job in self.store.list_jobs("needs_verification"):
                        if job["id"] not in verifiers and len(verifiers) < max(1, self.coordinator_reserved):
                            if self.store._start_verification(job["id"]):
                                verifiers[job["id"]] = verification_pool.submit(self.store.verify_with_agent, job["id"], self.verification_executor)
                all_futures = list(workers) + list(verifiers.values())
                if not all_futures:
                    self._wait(self.poll_interval)
                    continue
                done, _ = wait(all_futures, timeout=self.poll_interval, return_when=FIRST_COMPLETED)
                completed_results = []
                for future in done:
                    workers.pop(future, None)
                    for job_id, verifier_future in list(verifiers.items()):
                        if verifier_future is future:
                            verifiers.pop(job_id, None)
                            break
                    try:
                        completed_results.append(future.result())
                    except Exception:
                        completed_results.append({"status": "retry"})
                if completed_results and all(result.get("status") in ("idle", "blocked", "retry", "needs_verification")
                                             for result in completed_results if isinstance(result, dict)):
                    self._wait(self.poll_interval)
        finally:
            worker_pool.shutdown(wait=True)
            if verification_pool is not None:
                verification_pool.shutdown(wait=True)
        return "stopped"


class Launchd:
    def __init__(self, agents_root, db_path, vault_root=None):
        self.agents_root = Path(agents_root).resolve(); self.db_path = Path(db_path).resolve()
        self.vault_root = Path(vault_root).resolve() if vault_root else None

    def generate(self, label="com.agents.service"):
        environment = {"AGENTS_ROOT": str(self.agents_root)}
        vault_root = self.vault_root or (Path(os.environ["AGENTS_VAULT_ROOT"]).resolve() if os.environ.get("AGENTS_VAULT_ROOT") else None)
        if vault_root:
            environment["AGENTS_VAULT_ROOT"] = str(vault_root)
        codex = shutil.which("codex")
        environment["PATH"] = (str(Path(codex).parent) + ":/usr/bin:/bin") if codex else "/usr/bin:/bin"
        payload = {"Label": label, "ProgramArguments": [sys.executable, "-m", "harness.service", "--db", str(self.db_path), "run"], "WorkingDirectory": str(self.agents_root), "EnvironmentVariables": environment, "RunAtLoad": True, "KeepAlive": True, "StandardOutPath": str(self.agents_root / ".local" / "service.log"), "StandardErrorPath": str(self.agents_root / ".local" / "service.error.log")}
        return plistlib.dumps(payload, fmt=plistlib.FMT_XML).decode()

    def install(self, label="com.agents.service", path=None):
        if sys.platform != "darwin": raise OSError("launchd installation is only supported on macOS")
        target = Path(path or Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        content = self.generate(label)
        backup = None
        changed = False
        old_content = target.read_text() if target.exists() else None
        if old_content is not None and old_content != content:
            suffix = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = target.with_name(target.name + ".bak-" + suffix)
            serial = 1
            while backup.exists():
                backup = target.with_name(target.name + f".bak-{suffix}-{serial}")
                serial += 1
            backup.write_text(old_content); backup.chmod(0o600)
        if old_content != content:
            target.write_text(content); target.chmod(0o600); changed = True
            if target.read_text() != content:
                raise OSError(f"launchd plist readback failed: {target}")
        return {"path": str(target), "backup": str(backup) if backup else None, "changed": changed}

    def start(self, label="com.agents.service", path=None):
        target = path or Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if sys.platform != "darwin": raise OSError("launchd is only supported on macOS")
        existing = self.status(label)
        if existing.returncode == 0:
            return existing
        return subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)], text=True, capture_output=True, check=False)

    def status(self, label="com.agents.service"):
        if sys.platform != "darwin": raise OSError("launchd is only supported on macOS")
        return subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], text=True, capture_output=True, check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Durable App-independent Agents service")
    parser.add_argument("--db", default=None); sub = parser.add_subparsers(dest="command", required=True)
    en = sub.add_parser("enroll"); en.add_argument("--task", required=True); en.add_argument("--workspace", required=True); prompt_group = en.add_mutually_exclusive_group(required=True); prompt_group.add_argument("--prompt"); prompt_group.add_argument("--prompt-file"); en.add_argument("--context", required=True); en.add_argument("--model", default="gpt-5.6-luna"); en.add_argument("--effort", default="low"); en.add_argument("--timeout", type=float, default=300); en.add_argument("--json", action="store_true")
    ls = sub.add_parser("list"); ls.add_argument("--db", default=argparse.SUPPRESS); ls.add_argument("--state"); ls.add_argument("--json", action="store_true")
    sh = sub.add_parser("show"); sh.add_argument("--db", default=argparse.SUPPRESS); sh.add_argument("job_id"); sh.add_argument("--json", action="store_true")
    run = sub.add_parser("run"); run.add_argument("--db", default=argparse.SUPPRESS); run.add_argument("--poll", type=float, default=30)
    once = sub.add_parser("run-once"); once.add_argument("--db", default=argparse.SUPPRESS); once.add_argument("--json", action="store_true")
    ver = sub.add_parser("verify"); ver.add_argument("--db", default=argparse.SUPPRESS); ver.add_argument("job_id"); ver.add_argument("--evidence", required=True); ver.add_argument("--json", action="store_true")
    ld = sub.add_parser("launchd"); ld.add_argument("--db", default=argparse.SUPPRESS); ld.add_argument("action", choices=["generate", "install", "start", "status"]); ld.add_argument("--label", default="com.agents.service"); ld.add_argument("--path"); ld.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root_hint = Path(os.environ.get("AGENTS_ROOT", Path(__file__).resolve().parents[1])).resolve()
    selected_env = load_agents_env(root_hint / ".env")
    if not selected_env.get("AGENTS_ROOT") or not selected_env.get("AGENTS_VAULT_ROOT"):
        raise ValueError("AGENTS_ROOT and AGENTS_VAULT_ROOT must be configured in the existing .env or environment")
    store = ServiceStore(args.db, agents_root=selected_env["AGENTS_ROOT"], vault_root=selected_env["AGENTS_VAULT_ROOT"])
    try:
        if args.command == "enroll":
            prompt = Path(args.prompt_file).read_text() if args.prompt_file else args.prompt
            value = store.enroll(args.task, args.workspace, prompt, args.context, model=args.model, effort=args.effort, timeout=args.timeout)
        elif args.command == "list": value = store.list_jobs(args.state)
        elif args.command == "show": value = store.get_job(args.job_id)
        elif args.command == "verify": value = store.verify(args.job_id, args.evidence)
        elif args.command == "run-once": value = Scheduler(store, verification_executor=default_verifier).run_once()
        elif args.command == "run": value = Scheduler(store, poll_interval=args.poll, verification_executor=default_verifier).run_forever()
        else:
            launchd = Launchd(store.tasks.agents_root, store.db_path, store.tasks.vault_root)
            value = launchd.generate(args.label) if args.action == "generate" else getattr(launchd, args.action)(args.label, args.path) if args.action in ("install", "start") else launchd.status(args.label)
        if getattr(args, "json", False): print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        elif isinstance(value, str): print(value)
        elif isinstance(value, list):
            for row in value: print(f"{row['id']} {row['state']} task={row['task_id']}")
        elif hasattr(value, "stdout"): print(value.stdout or value.stderr)
        else: print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        return 0
    finally: store.close()


if __name__ == "__main__": raise SystemExit(main())
