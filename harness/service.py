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
import inspect
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sqlite3
import stat
import string
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

from .tasks import ConflictError, TaskStore
from .service_review import decide_findings


class AuthError(RuntimeError):
    """Subscription authentication is absent or an API route was requested."""

    def __init__(self, message, *, hold=False, action=None, source=None):
        super().__init__(message)
        self.hold = bool(hold)
        self.action = action
        self.source = source


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value)


def retry_delay(base, maximum, attempt):
    import math
    if base <= 0 or maximum <= 0:
        return 0.0
    exponent = min(max(0, attempt - 1), max(0, math.ceil(math.log2(maximum / base))))
    return min(maximum, base * 2 ** exponent)


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


def _reject_symlink_components(path):
    """Reject a target or explicitly selected parent symlink."""
    path = Path(path)
    try:
        if path.is_symlink():
            raise ValueError(f"path component must not be a symlink: {path}")
    except OSError as exc:
        raise ValueError(f"cannot inspect path component: {path}") from exc


def _read_nofollow(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "rb") as stream:
            fd = None
            return stream.read()
    finally:
        if fd is not None:
            os.close(fd)


def _atomic_write_bytes(path, content):
    """Write private bytes and atomically replace the destination."""
    path = Path(path)
    _reject_symlink_components(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


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
        current = self.db_path.parent.resolve()
        self.db_path = current / self.db_path.name
        immediate_parent = current
        while True:
            info = current.stat()
            mode = stat.S_IMODE(info.st_mode)
            trusted_owner = not hasattr(os, "geteuid") or info.st_uid in (0, os.geteuid())
            sticky_ancestor = current != immediate_parent and bool(mode & stat.S_ISVTX) and trusted_owner
            if not trusted_owner or (mode & 0o022 and not sticky_ancestor):
                raise ValueError(f"database parent must not be writable by other users: {current}")
            if current == current.parent:
                break
            current = current.parent
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.db_path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            opened = os.fstat(fd)
            actual = os.stat(self.db_path, follow_symlinks=False)
            if (opened.st_dev, opened.st_ino) != (actual.st_dev, actual.st_ino):
                raise ValueError("database path changed while opening")
        finally:
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
        for column, kind in (("verification_pid", "INTEGER"), ("verification_identity", "TEXT"), ("verification_count", "INTEGER NOT NULL DEFAULT 0")):
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
            pid = int(pid)
            if pid <= 0:
                return "unknown"
            os.kill(pid, 0)
            if recorded_identity:
                current = self._process_identity(pid)
                try:
                    recorded = json.loads(recorded_identity) if isinstance(recorded_identity, str) else recorded_identity
                except (TypeError, ValueError):
                    return "unknown"
                if not isinstance(recorded, dict):
                    return "unknown"
                for key in ("start_ticks", "start_time"):
                    if recorded.get(key) and current.get(key) and recorded[key] != current[key]:
                        return "dead"
                if recorded.get("command") and current.get("command") and recorded["command"] != current["command"]:
                    return "unknown"
                if not any(recorded.get(key) and current.get(key) for key in ("start_ticks", "start_time")):
                    return "unknown"
            return "alive"
        except (TypeError, ValueError):
            return "unknown"
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
            collectors = record.get("collectors", [])
            if collectors is None or not isinstance(collectors, list):
                return "unknown"
            for collector in collectors:
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
                    current = conn.execute("""SELECT j.*,a.id AS attempt_id,a.pid,a.identity,
                        a.run_dir AS attempt_run_dir,a.attempt_number,a.status AS attempt_status
                        FROM service_jobs j JOIN service_attempts a ON a.job_id=j.id
                        WHERE j.id=? AND j.state IN ('running','reconciling')
                          AND a.id=? AND a.status IN ('running','reconciling')""",
                        (row["id"], row["attempt_id"])).fetchone()
                    if current is None:
                        continue
                    if self._attempt_state(current) != "dead":
                        continue
                    stamp = now()
                    conn.execute("UPDATE service_jobs SET state='reconciling',last_error=?,updated_at=? WHERE id=?", ("worker died; read-only reconciliation in progress", stamp, row["id"]))
                    conn.execute("UPDATE service_attempts SET status='reconciling',ended_at=?,error=? WHERE id=? AND status IN ('running','reconciling')", (stamp, "worker process is definitely dead", row["attempt_id"]))
                try:
                    result = (reconciler or default_reconciler)(self._job(current), dict(current))
                except Exception as exc:
                    result = {"safe_to_resume": False, "reason": "receipt reconciliation failed", "error": str(exc)}
                safe = isinstance(result, dict) and result.get("safe_to_resume") is True
                state = "retry" if safe else "needs_verification"
                with self.tx() as conn:
                    conn.execute("UPDATE service_jobs SET state=?,next_attempt_at=?,last_error=?,updated_at=? WHERE id=?", (state, now() if safe else None, result.get("reason") if isinstance(result, dict) else "reconciliation requires verification", now(), row["id"]))
                    conn.execute("UPDATE service_attempts SET status=?,result_json=? WHERE id=?", (state, json.dumps(result or {}, ensure_ascii=False), row["attempt_id"]))
                task = self.tasks.get_task(current["task_id"])
                if task is not None:
                    try:
                        self.tasks.update_task(task["id"], expected_version=task["version"],
                                               execution_status="running" if state == "retry" else "needs_verification")
                    except Exception:
                        pass
                recovered.append({"job_id": current["id"], "state": state})
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
                surviving = False
                for path in Path(row["run_dir"]).glob("verification-*/process-state.json"):
                    try:
                        record = json.loads(path.read_text())
                        processes = [record] + record.get("collectors", [])
                        if any(self._pid_state(item.get("pid"), item.get("identity")) != "dead" for item in processes):
                            surviving = True
                    except (OSError, ValueError, TypeError, AttributeError):
                        surviving = True
                if surviving:
                    continue
                conn.execute("UPDATE service_jobs SET state='needs_verification',verification_pid=NULL,verification_identity=NULL,last_error=?,updated_at=? WHERE id=?", ("verification interrupted; retrying independent read-only review", now(), row["id"]))
                conn.execute("UPDATE service_attempts SET status='needs_verification' WHERE job_id=? AND status='verifying'", (row["id"],))
                recovered.append(row["id"])
        return recovered

    def _start_verification(self, job_id):
        with self.tx() as conn:
            changed = conn.execute("UPDATE service_jobs SET state='verifying',verification_count=verification_count+1,verification_pid=?,verification_identity=?,updated_at=? WHERE id=? AND state='needs_verification' AND (next_attempt_at IS NULL OR next_attempt_at<=?)", (os.getpid(), json.dumps(self._process_identity(os.getpid())), now(), job_id, now())).rowcount
            if changed:
                conn.execute("UPDATE service_attempts SET status='verifying' WHERE job_id=? AND status='needs_verification'", (job_id,))
            return changed == 1

    def _reset_verification(self, job_id, diagnostic):
        with self.tx() as conn:
            job = conn.execute("SELECT * FROM service_jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            # Cap the exponent as well as the delay for indefinitely retried work.
            delay = retry_delay(job["retry_base"], job["retry_max"], job["verification_count"])
            next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
            conn.execute("UPDATE service_jobs SET state='needs_verification',next_attempt_at=?,last_error=?,updated_at=? WHERE id=? AND state='verifying'", (next_at, diagnostic, now(), job_id))
            conn.execute("UPDATE service_attempts SET status='needs_verification',error=? WHERE job_id=? AND status='verifying'", (diagnostic, job_id))

    def _schedule_publication_repair(self, job_id, diagnostic):
        """Return a publication race to the original worker for repair."""
        job = self.get_job(job_id)
        next_generation = max(1, int(job.get("attempts_count") or 0) + 1) if job else 1
        receipt_name = "publication.json" if next_generation == 1 else f"publication-{next_generation}.json"
        diagnostic = (f"{diagnostic}; rebase the task branch onto current canonical main, recompute "
                      f"immutable_base and selected digests, and use new receipt generation {receipt_name}; "
                      "do not reuse the failed publication receipt")
        stamp = now()
        with self.tx() as conn:
            changed = conn.execute("UPDATE service_jobs SET state='retry',next_attempt_at=NULL,last_error=?,verification_count=0,updated_at=? WHERE id=? AND state='verifying'", (diagnostic, stamp, job_id)).rowcount
            if changed != 1:
                return
            conn.execute("UPDATE service_attempts SET status='repair_required',error=? WHERE job_id=? AND status='verifying'", (diagnostic, job_id))
            row = conn.execute("SELECT COALESCE(MAX(sequence),0) FROM service_updates WHERE job_id=?", (job_id,)).fetchone()
            conn.execute("INSERT INTO service_updates VALUES (?,?,?,?,?)", (job_id, row[0] + 1, diagnostic, json.dumps(["vault://publication-repair"]), stamp))
        task = self.tasks.get_task(job["task_id"]) if job else None
        if task is not None:
            try:
                self.tasks.update_task(task["id"], expected_version=task["version"], execution_status="running")
            except ConflictError:
                pass

    @staticmethod
    def _publication_conflict(error):
        text = str(error).lower()
        return any(marker in text for marker in (
            "canonical main moved", "not an ancestor", "remote readback",
            "main advanced", "receipt commit is not",
        ))

    @staticmethod
    def _publication_receipt_path(job):
        run_dir = Path(job["run_dir"])
        generation = max(1, int(job.get("attempts_count") or 1))
        return run_dir / ("publication.json" if generation == 1 else f"publication-{generation}.json")

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
            conn.execute("UPDATE service_jobs SET next_attempt_at=NULL,verification_count=0,updated_at=? WHERE id=?", (now(), job_id))
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
                if not any(self._structured_completion_receipt(item, dependency)
                           for item in dependency.get("completion_evidence", [])):
                    return False
        return True

    @staticmethod
    def _structured_completion_receipt(value, task):
        """Recognize the service's persisted acceptance/publication receipt."""
        try:
            path = Path(value)
            if not path.is_file():
                return False
            receipt = json.loads(path.read_text())
            review = receipt.get("review")
            publication = receipt.get("publication_readback")
            if (not isinstance(receipt, dict) or not isinstance(review, dict)
                    or review.get("acceptance") is not True or review.get("findings")
                    or not isinstance(review.get("criteria"), list) or not review["criteria"]
                    or not isinstance(publication, dict)):
                return False
            if not re.fullmatch(r"[0-9a-fA-F]{40}", str(publication.get("commit", ""))):
                return False
            same_main = publication.get("main") == publication.get("commit")
            ancestor_main = (publication.get("commit_is_ancestor") is True
                             and publication.get("merge_base") == publication.get("commit"))
            if not same_main and not ancestor_main:
                return False
            criteria = review["criteria"]
            expected = {item["evidence"] for item in task.get("acceptance_records", [])}
            observed = {item.get("criterion") for item in criteria if isinstance(item, dict)}
            if observed != expected or publication.get("repository") != task.get("repository"):
                return False
            return all(isinstance(item, dict) and item.get("verified") is True
                       and isinstance(item.get("criterion"), str)
                       and isinstance(item.get("evidence"), str)
                       and item["evidence"].strip() for item in criteria)
        except (OSError, TypeError, ValueError, AttributeError):
            return False

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
            try:
                claimed = self._claim_job(row["id"])
            except Exception:
                lock.release()
                raise
            if claimed is not None:
                try:
                    current = self.tasks.get_task(claimed["task_id"])
                    self.tasks.update_task(claimed["task_id"], expected_version=current["version"], execution_status="running")
                except ConflictError:
                    pass
                except Exception as exc:
                    try:
                        result = self._finish_attempt(
                            claimed, result={"status": "failed", "text": str(exc)}, error=str(exc))
                    except Exception as finish_error:
                        result = {"status": "retry", "job_id": claimed["id"],
                                  "error": str(finish_error)}
                    lock.release()
                    return {"_claim_error": result}
                claimed["_lock"] = lock
                return claimed
            lock.release()
        return None

    def _finish_attempt(self, job, result=None, error=None):
        status = result.get("status") if isinstance(result, dict) else "failed"
        held = status == "held"
        succeeded = status in ("completed", "success")
        terminal = "needs_verification" if succeeded else ("held" if held else "retry")
        attempt_status = "needs_verification" if succeeded else ("held" if held else "retry")
        attempt_no = job["attempts_count"]
        if succeeded or held:
            next_at = None
        else:
            delay = retry_delay(job["retry_base"], job["retry_max"], attempt_no)
            next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
        stamp = now()
        with self.tx() as conn:
            conn.execute("UPDATE service_jobs SET state=?,next_attempt_at=?,last_error=?,updated_at=? WHERE id=?", (terminal, next_at, error or (result.get("text") if not succeeded and isinstance(result, dict) else None), stamp, job["id"]))
            conn.execute("UPDATE service_attempts SET status=?,ended_at=?,result_json=?,error=? WHERE job_id=? AND attempt_number=?", (attempt_status, stamp, json.dumps(result or {}, ensure_ascii=False), error, job["id"], attempt_no))
        task = self.tasks.get_task(job["task_id"])
        if task is not None:
            try:
                current = self.tasks.get_task(job["task_id"])
                self.tasks.update_task(job["task_id"], expected_version=current["version"],
                                       execution_status="needs_verification" if succeeded else ("held" if held else "running"))
            except ConflictError:
                pass
            except Exception as exc:
                try:
                    with self.tx() as conn:
                        conn.execute("UPDATE service_jobs SET last_error=?,updated_at=? WHERE id=?",
                                     (f"task status update failed: {exc}", now(), job["id"]))
                except Exception:
                    pass
        if held:
            action = result.get("action") if isinstance(result, dict) else None
            source = result.get("source") if isinstance(result, dict) else None
            diagnostic = error or (result.get("text") if isinstance(result, dict) else None) or "operation held"
            evidence = f"local://cost-security/{source}/{action}" if source and action else "local://cost-security/hold"
            self.record_update(job["id"],
                               f"Cost/security hold: {diagnostic}",
                               [evidence])
            try:
                self.tasks.add_evidence(job["task_id"], [evidence], kind="cost_security_hold")
            except Exception:
                # The service/job receipt above remains the source of truth if
                # a concurrent task update cannot be appended here.
                pass
        return {"status": terminal, "job_id": job["id"], "attempt": attempt_no}

    def run_once(self, executor: Callable | None = None, verifier: Callable | None = None):
        job = self._claim_next()
        if job is None:
            blocked = any(self.tasks.get_task(row["task_id"]) is not None and not self._dependencies_ready(self.tasks.get_task(row["task_id"])) for row in self._ready_rows())
            return {"status": "blocked" if blocked else "idle", **({"reason": "dependencies"} if blocked else {})}
        if "_claim_error" in job:
            return job["_claim_error"]
        lock = job.pop("_lock")
        try:
            if executor is None:
                try:
                    self.auth_guard(load_agents_env(self.tasks.agents_root / ".env"))
                except Exception as exc:
                    held = isinstance(exc, AuthError) and exc.hold
                    diagnostic = str(exc)
                    if held:
                        diagnostic += f" [source={exc.source or 'unknown'}; action={exc.action or 'unknown'}]"
                    result = {"status": "held" if held else "failed", "text": diagnostic}
                    if held:
                        result.update({"action": exc.action, "source": exc.source,
                                       "hold_category": "cost_or_security"})
                    return self._finish_attempt(job, result=result, error=diagnostic)
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
        from .runner import save
        import uuid
        job = self.get_job(job_id)
        if job is None: raise KeyError(job_id)
        if job["state"] not in ("needs_verification", "verifying"):
            raise ValueError(f"job state {job['state']} cannot be verified")
        task = self.tasks.get_task(job["task_id"])
        if not isinstance(evidence, dict) or evidence.get("acceptance") is not True or evidence.get("findings"):
            raise ValueError("a structured acceptance review with no unresolved findings is required")
        expected = {row["evidence"] for row in task["acceptance_records"]}
        criteria = evidence.get("criteria", [])
        if not expected or not isinstance(criteria, list) or len(criteria) != len(expected):
            raise ValueError("every acceptance criterion must be independently observed")
        observed = set()
        for item in criteria:
            if (not isinstance(item, dict) or item.get("criterion") not in expected
                    or item["criterion"] in observed or item.get("verified") is not True
                    or not isinstance(item.get("evidence"), str) or not item["evidence"].strip()):
                raise ValueError("acceptance evidence must match each exact criterion")
            observed.add(item["criterion"])
        publication = observe_publication(job, task, evidence.get("publication"))
        receipt = Path(job["run_dir"]) / ("acceptance-" + uuid.uuid4().hex + ".json")
        receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        save(receipt, {"review": evidence, "publication_readback": publication,
                       "task_version": task["version"], "observed_at": now()}, os.environ)
        for item in criteria:
            self.tasks.add_acceptance_evidence(task["id"], item["criterion"], verified=True)
        task = self.tasks.add_completion_evidence(task["id"], str(receipt))
        task = self.tasks.update_task(task["id"], expected_version=task["version"], execution_status="verified")
        with self.tx() as conn:
            conn.execute("UPDATE service_jobs SET state='completed',next_attempt_at=NULL,updated_at=? WHERE id=?", (now(), job_id))
        return self.get_job(job_id)

    def verify_with_agent(self, job_id, verifier):
        job = self.get_job(job_id)
        task = self.tasks.get_task(job["task_id"])
        proposal = self._latest_publication_proposal(job)
        resumed_proof = None
        if proposal is not None:
            try:
                resumed = self._resume_pending_publication(job, task, proposal)
            except Exception as exc:
                if self._publication_conflict(exc):
                    self._schedule_publication_repair(job_id, "publication race requires worker repair: " + str(exc))
                    return {"status": "retry", "job_id": job_id,
                            "repair_required": True, "verification_error": str(exc)}
                self._reset_verification(job_id, "publication receipt reconciliation failed: " + str(exc))
                return {"status": "needs_verification", "job_id": job_id,
                        "verification_error": str(exc)}
            if resumed is not None:
                if resumed.get("status") in {"failed", "incomplete"}:
                    sha = resumed.get("published_sha", "unknown")
                    diagnostic = (f"publication receipt {resumed.get('status')} for published SHA {sha}; "
                                  f"receipt generation {self._publication_receipt_path(job).name} requires repair")
                    self._schedule_publication_repair(job_id, diagnostic)
                    return {"status": "retry", "job_id": job_id,
                            "repair_required": True, "publication": resumed,
                            "verification_error": diagnostic}
                if resumed.get("status") != "published":
                    self._reset_verification(job_id, "publication CI or remote readback remains incomplete")
                    return {"status": "needs_verification", "job_id": job_id,
                            "publication": resumed,
                            "verification_error": resumed.get("reason", "publication remains incomplete")}
                resumed_proof = {"commit": resumed["published_sha"],
                                 "mode": proposal.get("mode", "direct_main"),
                                 "files": list(resumed.get("files", proposal.get("files", [])))}
                # The code publication is already durably completed.  Only a
                # fresh final acceptance review may still be needed.
                proposal = None
        proposal_snapshot = None
        if proposal is not None:
            try:
                proposal_snapshot = self._publication_snapshot(job, proposal)
            except Exception as exc:
                proposal_snapshot = {"error": str(exc)}
        try:
            verifier_spec = {"job": job, "task": task,
                            "agents_root": str(self.tasks.agents_root),
                            "vault_root": str(self.tasks.vault_root)}
            if proposal is not None:
                verifier_spec["publication_snapshot"] = proposal_snapshot
            if resumed_proof is not None:
                # The sandboxed reviewer need not repeat a network request.
                # Supply the host's actual readback before final review, then
                # recheck it again at the completion boundary below.
                verifier_spec["publication_readback"] = observe_publication(job, task, resumed_proof)
            result = verifier(verifier_spec)
        except Exception as exc:
            self._reset_verification(job_id, str(exc))
            return {"status": "needs_verification", "job_id": job_id, "verification_error": str(exc)}
        findings = result.get("findings", []) if isinstance(result, dict) else []
        if findings:
            links = tuple(item for item in (result.get("evidence_links", []) if isinstance(result, dict) else []) if isinstance(item, str))
            review_spec = {"task": self.tasks.get_task(job["task_id"]), "job": job,
                           "agents_root": str(self.tasks.agents_root),
                           "vault_root": str(self.tasks.vault_root)}
            try:
                disposition = decide_findings(review_spec, result)
            except Exception as exc:
                self._reset_verification(job_id, "review finding disposition failed: " + str(exc))
                return {"status": "needs_verification", "job_id": job_id, "verification": result,
                        "review_disposition": {"status": "incomplete", "reason": str(exc)}}
            if not isinstance(disposition, dict) or disposition.get("status") != "complete":
                reason = disposition.get("reason", "finding disposition is incomplete") if isinstance(disposition, dict) else "finding disposition is malformed"
                self._reset_verification(job_id, "review finding disposition incomplete: " + str(reason))
                return {"status": "needs_verification", "job_id": job_id, "verification": result,
                        "review_disposition": disposition}
            decisions = disposition.get("decisions", [])
            adopted = disposition.get("adopted_findings", [])
            rejected = disposition.get("rejected_findings", [])
            separated = disposition.get("separated_findings", [item for item in decisions if isinstance(item, dict) and item.get("decision") == "separate"])
            if any(not isinstance(items, list) for items in (decisions, adopted, rejected, separated)):
                self._reset_verification(job_id, "review finding disposition has invalid decision lists")
                return {"status": "needs_verification", "job_id": job_id, "verification": result,
                        "review_disposition": {"status": "incomplete", "reason": "invalid decision lists"}}
            for label, items in (("adopted", adopted), ("rejected", rejected), ("separated", separated)):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    finding = item.get("finding", item)
                    message = finding if isinstance(finding, str) else json.dumps(finding, ensure_ascii=False, sort_keys=True)
                    reason = str(item.get("reason", "disposition recorded"))
                    item_links = tuple(link for link in item.get("evidence", []) if isinstance(link, str)) if isinstance(item.get("evidence", []), list) else ()
                    evidence = tuple(dict.fromkeys(links + item_links))
                    follow_up = ""
                    if label == "separated" and disposition.get("separate_task_ids"):
                        follow_up = "; local follow-up: " + ", ".join(map(str, disposition["separate_task_ids"]))
                    self.record_update(job_id, f"Verification finding {label}: {message}; rationale: {reason}{follow_up}", evidence)
            if adopted:
                with self.tx() as conn:
                    conn.execute("UPDATE service_jobs SET state='retry',next_attempt_at=?,last_error=?,updated_at=? WHERE id=?", (now(), "verification findings require repair", now(), job_id))
                    conn.execute("UPDATE service_attempts SET status='repair_required' WHERE job_id=? AND attempt_number=(SELECT MAX(attempt_number) FROM service_attempts WHERE job_id=?)", (job_id, job_id))
                task = self.tasks.get_task(job["task_id"])
                if task is not None:
                    try:
                        self.tasks.update_task(task["id"], expected_version=task["version"], execution_status="running")
                    except ConflictError:
                        pass
                return {"status": "retry", "job_id": job_id, "verification": result,
                        "review_disposition": disposition, "repair_required": True}
            accepted_result = dict(result)
            accepted_result["findings"] = []
            accepted_result["review_disposition"] = disposition
            result = accepted_result
        if resumed_proof is not None:
            result = dict(result) if isinstance(result, dict) else {}
            result["publication"] = resumed_proof
            try:
                result["publication_readback"] = observe_publication(job, task, resumed_proof)
            except Exception as exc:
                self._reset_verification(job_id, "publication readback remains incomplete: " + str(exc))
                return {"status": "needs_verification", "job_id": job_id,
                        "verification": result, "verification_error": str(exc)}
        if proposal is not None:
            # Publication readiness covers the code/test evidence needed to
            # safely publish.  Final acceptance may still depend on the
            # post-publication remote and CI readback, so it is evaluated only
            # after the host has published and observed that state.
            if not isinstance(result, dict) or result.get("publication_readiness") is not True:
                self._reset_verification(job_id, "publication readiness remains incomplete")
                return {"status": "needs_verification", "job_id": job_id,
                        "verification": result or {},
                        "verification_error": "publication readiness is incomplete"}
            review = result.get("publication_review") if isinstance(result, dict) else None
            if not isinstance(review, dict):
                self._reset_verification(job_id, "publication proposal requires structured publication review")
                return {"status": "needs_verification", "job_id": job_id,
                        "verification": result or {},
                        "verification_error": "publication review is incomplete"}
            # The verifier reports findings beside publication_review. Bind
            # that observed list into the host publication review so an empty
            # decisions list is accepted only for an explicit zero-finding
            # review; hidden or unresolved findings cannot be erased by an
            # empty decisions array.
            review = dict(review)
            if "findings" not in review and isinstance(result.get("findings"), list):
                review["findings"] = result["findings"]
            if review.get("findings_complete") is not True:
                self._reset_verification(job_id, "publication review findings remain incomplete")
                return {"status": "needs_verification", "job_id": job_id,
                        "verification": result, "verification_error": "publication review is incomplete"}
            try:
                proof = self._publish_proposal(job, task, proposal, review,
                                               snapshot=proposal_snapshot)
            except Exception as exc:
                if self._publication_conflict(exc):
                    self._schedule_publication_repair(job_id, "publication race requires worker repair: " + str(exc))
                    return {"status": "retry", "job_id": job_id,
                            "repair_required": True, "verification": result,
                            "verification_error": str(exc)}
                self._reset_verification(job_id, "publication proposal was not published: " + str(exc))
                return {"status": "needs_verification", "job_id": job_id,
                        "verification": result or {}, "verification_error": str(exc)}
            result = dict(result)
            result["publication"] = proof
            try:
                result["publication_readback"] = observe_publication(
                    job, self.tasks.get_task(job["task_id"]), proof)
            except Exception as exc:
                self._reset_verification(job_id, "publication readback remains incomplete: " + str(exc))
                return {"status": "needs_verification", "job_id": job_id,
                        "verification": result, "verification_error": str(exc)}
        if not isinstance(result, dict) or result.get("acceptance") is not True:
            self._reset_verification(job_id, "verification criteria remain incomplete")
            return {"status": "needs_verification", "job_id": job_id, "verification": result or {}}
        try:
            verified = self.verify(job_id, result)
        except Exception as exc:
            self._reset_verification(job_id, str(exc))
            return {"status": "needs_verification", "job_id": job_id, "verification": result,
                    "verification_error": str(exc)}
        return {"status": "verified", "job_id": job_id, "job": verified, "verification": result}

    @staticmethod
    def _latest_publication_proposal(job):
        attempts = job.get("attempts", []) if isinstance(job, dict) else []
        if not attempts:
            return None
        result = attempts[-1].get("result") if isinstance(attempts[-1], dict) else None
        if not isinstance(result, dict):
            return None
        return _worker_publication_proposal(result)

    def _publication_snapshot(self, job, proposal):
        """Capture the host's pre-review publication inputs for later binding."""
        from .delivery import git
        from .publication import selected_diff_digest, selected_tree_digest
        canonical = Path(self.tasks.agents_root).resolve()
        worktree = Path(job["workspace"]).resolve()
        if (Path(proposal.get("canonical_repo", "")).resolve() != canonical
                or Path(proposal.get("task_worktree", "")).resolve() != worktree):
            raise ValueError("publication proposal checkouts do not match the host job")
        files = proposal.get("files")
        base = proposal.get("immutable_base")
        if not isinstance(files, list) or not files or not isinstance(base, str):
            raise ValueError("publication proposal snapshot inputs are invalid")
        return {"head": git(worktree, "rev-parse", "HEAD"),
                "preimage": selected_tree_digest(canonical, files),
                "diff": selected_diff_digest(worktree, base, files)}

    def _resume_pending_publication(self, job, task, proposal):
        """Reconcile a host receipt without spending another verifier turn."""
        from .publication import _read_json, publish_scoped
        receipt = self._publication_receipt_path(job)
        if receipt.is_symlink() or not receipt.is_file():
            return None
        state = _read_json(receipt)
        if state.get("status") not in {"pending", "failed", "incomplete", "published", "success"} or not state.get("published_sha"):
            return None
        files = state.get("files") or proposal.get("files")
        base = state.get("base") or proposal.get("immutable_base")
        preimage = state.get("preimage_digest") or proposal.get("preimage_digest")
        diff = state.get("diff_digest") or proposal.get("diff_digest")
        review = state.get("review")
        if (not isinstance(files, list) or not isinstance(base, str)
                or not isinstance(preimage, str) or not isinstance(diff, str)
                or not isinstance(review, dict)):
            raise ValueError("publication receipt lacks host resume inputs")
        from .publication import _authorized_github_remote
        if not _authorized_github_remote(task.get("repository", ""), proposal.get("remote", "")):
            raise ValueError("publication receipt remote is not the authorized GitHub remote")
        spec = dict(proposal)
        spec.update({"repository": task.get("repository"),
                     "canonical_repo": str(Path(self.tasks.agents_root).resolve()),
                     "task_worktree": str(Path(job["workspace"]).resolve()),
                     "files": files, "immutable_base": base,
                     "preimage_digest": preimage, "diff_digest": diff,
                     "review": review, "vault_receipt": str(receipt)})
        spec.pop("ci", None)
        spec.pop("ci_observer", None)
        return publish_scoped(spec)

    def _publish_proposal(self, job, task, proposal, review, *, snapshot=None):
        """Publish a worker proposal only after host-side CAS and review checks."""
        if not isinstance(proposal, dict) or not isinstance(task, dict):
            raise ValueError("publication proposal is malformed")
        from .delivery import git
        from .publication import publish_scoped, selected_diff_digest, selected_tree_digest
        required = ("canonical_repo", "task_worktree", "files", "immutable_base",
                    "commit_message", "remote")
        if any(key not in proposal for key in required):
            raise ValueError("publication proposal is incomplete")
        if proposal.get("repository", task.get("repository")) != task.get("repository"):
            raise ValueError("publication proposal repository does not match the task")
        from .publication import _authorized_github_remote
        if not _authorized_github_remote(task.get("repository", ""), proposal.get("remote", "")):
            raise ValueError("publication proposal remote is not the authorized GitHub remote")
        canonical = Path(self.tasks.agents_root).resolve()
        worktree = Path(job["workspace"]).resolve()
        proposed_canonical = Path(proposal["canonical_repo"])
        proposed_worktree = Path(proposal["task_worktree"])
        if (not proposed_canonical.is_absolute() or not proposed_worktree.is_absolute()
                or proposed_canonical.resolve() != canonical
                or proposed_worktree.resolve() != worktree):
            raise ValueError("publication proposal checkouts do not match the host job")
        if not canonical.is_dir() or not worktree.is_dir() or canonical == worktree:
            raise ValueError("publication proposal checkouts are unavailable")
        files = proposal["files"]
        if not isinstance(files, list) or not files:
            raise ValueError("publication proposal files are invalid")
        actual_head = git(worktree, "rev-parse", "HEAD")
        actual_preimage = selected_tree_digest(canonical, files)
        actual_diff = selected_diff_digest(worktree, proposal["immutable_base"], files)
        if snapshot is not None and (not isinstance(snapshot, dict) or snapshot.get("error")):
            raise ValueError("publication review host snapshot is unavailable")
        if snapshot is not None and {
                "head": actual_head, "preimage": actual_preimage, "diff": actual_diff
        } != {"head": snapshot.get("head"), "preimage": snapshot.get("preimage"),
              "diff": snapshot.get("diff")}:
            raise ValueError("publication proposal changed after host review snapshot")
        if proposal.get("reviewed_head") not in (None, actual_head):
            raise ValueError("publication proposal head changed before host review")
        if proposal.get("preimage_digest") not in (None, actual_preimage):
            raise ValueError("publication proposal preimage changed before host review")
        if proposal.get("diff_digest") not in (None, actual_diff):
            raise ValueError("publication proposal diff changed before host review")
        host_review = json.loads(json.dumps(review))
        expected_head = snapshot.get("head") if snapshot is not None else actual_head
        expected_diff = snapshot.get("diff") if snapshot is not None else actual_diff
        if host_review.get("reviewed_head") != expected_head:
            raise ValueError("publication review head does not match host observation")
        if host_review.get("reviewed_diff_digest") != expected_diff:
            raise ValueError("publication review diff does not match host observation")
        # The worker cannot choose where the durable publication receipt lives.
        # Bind it to this job's Vault run directory before calling the publisher.
        raw_run_dir = Path(job["run_dir"])
        if not raw_run_dir.is_absolute() or raw_run_dir.is_symlink():
            raise ValueError("job Vault run directory is unavailable")
        run_dir = raw_run_dir.resolve()
        try:
            run_dir.relative_to(Path(self.tasks.vault_root).resolve())
        except ValueError as exc:
            raise ValueError("job Vault run directory is outside the configured Vault") from exc
        receipt = self._publication_receipt_path(job)
        spec = dict(proposal)
        spec.update({"repository": task.get("repository"), "canonical_repo": str(canonical),
                     "task_worktree": str(worktree), "preimage_digest": actual_preimage,
                     "diff_digest": actual_diff, "review": host_review,
                     "vault_receipt": str(receipt)})
        # CI is a host observation.  Discard worker supplied metadata and
        # adapters so production publication always selects the repository's
        # required-check observer.
        spec.pop("ci", None)
        spec.pop("ci_observer", None)
        outcome = publish_scoped(spec)
        if not isinstance(outcome, dict) or outcome.get("status") != "published":
            raise ValueError("host publication did not reach a published state")
        commit = outcome.get("published_sha")
        if not isinstance(commit, str):
            raise ValueError("host publication omitted the published commit")
        return {"commit": commit, "mode": proposal.get("mode", "direct_main"),
                "files": list(files),
                "canonical_dirty_before": outcome.get("canonical_dirty_before", []),
                "canonical_dirty_after": outcome.get("canonical_dirty_after", [])}

    def verify_pending(self, verifier):
        outcomes = []
        for job in self.list_jobs("needs_verification"):
            if self._start_verification(job["id"]):
                outcomes.append(self.verify_with_agent(job["id"], verifier))
        return outcomes

    @staticmethod
    def auth_guard(env, login_check=None):
        blocked = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CODEX_API_KEY")
        configured = next((key for key in blocked if env.get(key)), None)
        if configured:
            raise AuthError(
                f"API inference routes are forbidden; use ChatGPT subscription login "
                f"(extra billing blocked: source={configured}; action=inference_api_route)",
                hold=True, action="inference_api_route", source=configured)
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


def _worker_publication_proposal(result):
    """Recover the final proposal from persisted multi-message CLI output.

    Progress messages are preserved by the runner before its final JSON. This
    extracts data only; host snapshot, scope and review checks still authorize
    every publication independently.
    """
    if "publication_proposal" in result:
        return result["publication_proposal"]
    text = result.get("text")
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    proposal = None
    offset = 0
    while offset < len(text):
        start = text.find("{", offset)
        if start < 0:
            break
        try:
            value, consumed = decoder.raw_decode(text, start)
        except ValueError:
            offset = start + 1
            continue
        offset = consumed
        if isinstance(value, dict) and "publication_proposal" in value:
            proposal = value["publication_proposal"]
    return proposal


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
    for policy, label in ((agents_root / "COMMON-AGENTS.md", "Current common policy"),
                          (agents_root / "policies" / "repository-delivery.md", "Repository delivery policy")):
        if policy.is_file():
            prompt += f"\n\n{label}:\n" + policy.read_text()
    prompt += (
        "\n\nIf the task produces a reviewed, selected-file change that needs host delivery, "
        "return a structured JSON object containing publication_proposal with canonical_repo, "
        "task_worktree, files, immutable_base, commit_message, remote, and any reviewed digests. "
        "The host binds canonical_repo to AGENTS_ROOT, task_worktree to this job workspace, and "
        "the receipt to the job Vault run directory; worker paths, receipt locations, and CI metadata "
        "cannot authorize publication. Do not claim publication or completion; the host independently "
        "checks the diff, review, remote, and required CI checks."
    )
    job_args = dict(workspace=Path(spec["workspace"]), vault=vault, prompt=prompt,
                    mode="run", provider="codex", codex_model=spec["model"], effort=spec["effort"],
                    timeout=spec["timeout"], fallback=False, run_dir=Path(spec["run_dir"]))
    # Newer runners persist task identity in their Job record.  Keep this
    # compatible with the older local runner while passing it whenever the
    # runner exposes the field.
    if "task_id" in inspect.signature(Job).parameters:
        job_args["task_id"] = spec.get("task_id")
    job = Job(**job_args)
    if "task_id" in spec and not hasattr(job, "task_id"):
        job.task_id = spec["task_id"]
    run_dir = Path(spec["run_dir"])
    if run_dir.exists():
        result = resume_job(job, run_dir, env)
    else:
        result = run_job(job, env, run_dir=run_dir)
    if isinstance(result, dict):
        proposal = _worker_publication_proposal(result)
        if proposal is not None:
            if not isinstance(proposal, dict):
                invalid = dict(result)
                invalid.pop("publication_proposal", None)
                return {**invalid, "status": "failed", "text": "publication proposal is malformed"}
            return {**result, "publication_proposal": proposal}
    return result


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


def observe_publication(job, task, proof):
    """Read actual Git/GitHub state; review prose is not publication evidence."""
    from .delivery import git, oid, GitHub
    if not isinstance(proof, dict):
        raise ValueError("publication commit and delivery mode are required")
    commit = oid(proof.get("commit", ""))
    repository = task.get("repository")
    remote = GitHub(repository or "")
    workspace = job["workspace"]
    accepted_urls = {f"git@github.com:{repository}.git", f"https://github.com/{repository}.git",
                     f"https://github.com/{repository}"}
    if git(workspace, "remote", "get-url", "origin") not in accepted_urls:
        raise ValueError("workspace remote does not match the assigned repository")
    checkouts = git(workspace, "worktree", "list", "--porcelain").split("\n\n")
    main = [entry.splitlines()[0].removeprefix("worktree ") for entry in checkouts
            if "branch refs/heads/main" in entry.splitlines()]
    if len(main) != 1:
        raise ValueError("one canonical main checkout is required for synchronization readback")
    canonical = main[0]
    dirty = git(canonical, "status", "--porcelain=v1", "-z", "-uall")
    selected_files = proof.get("files") if isinstance(proof.get("files"), list) else None
    if selected_files is None:
        if dirty:
            raise ValueError("canonical main contains uncommitted work")
    else:
        if _status_overlaps_selected(dirty, selected_files):
            raise ValueError("canonical main overlaps selected files")
    local = git(canonical, "rev-parse", "HEAD")
    published = remote.api("commits/main")["sha"]
    if local != published or git(canonical, "merge-base", commit, local) != commit:
        raise ValueError("published commit and synchronized main do not match")
    if proof.get("mode") == "direct_main":
        if repository != "Saber5656/Agents":
            raise ValueError("direct main policy is scoped to Saber5656/Agents")
    elif proof.get("mode") == "pull_request":
        number = proof.get("pr_number")
        if type(number) is not int or number <= 0:
            raise ValueError("a positive PR number is required")
        pr = remote.pr(number)
        if pr["state"] != "MERGED" or pr["baseRefName"] != "main" or (pr.get("mergeCommit") or {}).get("oid") != commit:
            raise ValueError("PR merge readback does not match the accepted commit")
        if any(not row.get("isResolved") and not row.get("isOutdated") for row in remote.threads(number)):
            raise ValueError("PR has unresolved current review findings")
    else:
        raise ValueError("unknown delivery mode")
    # Re-read after the multi-step observations; never certify a stale main.
    if remote.api("commits/main")["sha"] != local or git(canonical, "rev-parse", "HEAD") != local:
        raise ValueError("main moved during publication verification")
    return {"repository": repository, "commit": commit, "main": local,
            "commit_is_ancestor": True, "merge_base": commit,
            "canonical_workspace": canonical, "mode": proof["mode"],
            **({"files": selected_files} if selected_files is not None else {})}


def _status_overlaps_selected(raw_status, files):
    selected = set(files)
    rename_source = False
    for entry in raw_status.split("\0"):
        if len(entry) >= 4 and entry[3:] in selected:
            return True
        if rename_source and entry in selected:
            return True
        rename_source = len(entry) >= 2 and entry[:2] in {"R ", " R", "C ", " C"}
    return False


def _acceptance_catalog(task):
    """Content identities avoid asking reviewers to retype long requirements."""
    import hashlib
    return [{"id": hashlib.sha256(row["evidence"].encode("utf-8")).hexdigest(),
             "criterion": row["evidence"]} for row in task["acceptance_records"]]


def _bind_acceptance_ids(task, verdict):
    """Resolve exact identities only; never fuzzy-match a changed requirement."""
    catalog = {row["id"]: row["criterion"] for row in _acceptance_catalog(task)}
    result = dict(verdict)
    criteria = result.get("criteria")
    if not isinstance(criteria, list):
        return result  # The completion boundary rejects absent/malformed criteria.
    bound = []
    for item in criteria:
        if not isinstance(item, dict) or "criterion_id" not in item:
            bound.append(item)  # Preserve support for existing exact-text verdicts.
            continue
        identifier = item["criterion_id"]
        if not isinstance(identifier, str) or identifier not in catalog:
            raise ValueError("unknown or stale acceptance criterion identity")
        criterion = catalog[identifier]
        if "criterion" in item and item["criterion"] != criterion:
            raise ValueError("acceptance criterion identity and text disagree")
        bound.append({**item, "criterion": criterion})
    result["criteria"] = bound
    return result


def default_verifier(spec):
    """Use an actual read-only subscription Codex turn for acceptance review."""
    task = spec["task"]
    env = load_agents_env(Path(spec["agents_root"]) / ".env")
    ServiceStore.auth_guard(env)
    phase = "pre_publication" if spec.get("publication_snapshot") is not None else "final_acceptance"
    phase_instructions = (
        "Review only source readiness for host publication now. An uncommitted diff, pending CI, "
        "and not-yet-published main are expected at this phase, not a source defect or a separate task. "
        "Do not require remote publication to set publication_readiness=true. Do not attempt network "
        "or GitHub checks: the host performs them after your source review. Keep acceptance=false "
        "and publication-dependent criteria unverified until the later final acceptance phase; "
        "this must not create findings when source checks pass. Return explicit empty findings "
        "and decisions when the code/docs, validation and scope are correct."
        if phase == "pre_publication" else
        "Review final acceptance against actual artifacts and the supplied host publication_readback. "
        "That readback was obtained by the host from Git/GitHub immediately before this review and "
        "will be rechecked before completion. Use its exact commit/main identity as external evidence; "
        "do not repeat network requests from the read-only sandbox or treat its network unavailability "
        "as a product defect. Missing host readback remains unverified."
    )
    prompt = json.dumps({
        "phase": phase,
        "acceptance_catalog": _acceptance_catalog(task),
        "phase_instructions": phase_instructions,
        "task": task,
        "job": spec["job"],
        "publication_snapshot": spec.get("publication_snapshot"),
        "publication_readback": spec.get("publication_readback"),
        "instructions": (
            "Act as an independent read-only verifier. Inspect the workspace, saved execution result and artifacts, "
            "the task acceptance criteria and current git status/log. Follow phase_instructions: "
            "public commit/merge/main evidence belongs only to final_acceptance and comes from host readback. "
            "For a publication proposal, separately verify the code/test change and return publication_readiness=true "
            "only when those pre-publication checks are complete. Bind publication_review to the observed worktree "
            "head and selected diff digest supplied in publication_snapshot (the host computed these from the "
            "selected paths and immutable base); copy those values exactly. The host will recompute both and does "
            "not treat this schema as evidence. "
            "Do not edit files, run write commands, or infer completion from words alone. Return JSON only: "
            "{acceptance:boolean, publication_readiness:boolean, findings:[objects], evidence:string, criteria:[{criterion_id:string, verified:boolean, evidence:string}], publication:{commit:string, mode:direct_main|pull_request, pr_number:integer}, publication_review:{status:complete, reviewed:true, reviewed_head:string, reviewed_diff_digest:string, findings:[objects], findings_complete:true, decisions:[{finding_id:string, decision:adopt|reject|separate, reason:string, evidence:[string], applied:boolean, applied_evidence:[string]}]}, "
            "evidence_links:[strings]}. Return every acceptance_catalog id exactly once as criterion_id and cite observed evidence for each. Do not retype or paraphrase the criterion text; the host binds each id to its exact requirement. Saber5656/Agents uses direct main publication; other repositories require their PR delivery policy. "
            "Use findings for any missing or incorrect implementation and explain the repair required. "
            "When no findings exist, return findings:[] and publication_review.findings:[] with decisions:[]; "
            "when findings exist, return exactly one explicit adopt/reject/separate decision for every finding."
        ),
    }, ensure_ascii=False)
    from .runner import execute, save
    import uuid
    record = Path(spec["job"]["run_dir"]) / ("verification-" + uuid.uuid4().hex)
    record.mkdir(mode=0o700, parents=True)
    save(record / "prompt.json", prompt, env)
    argv = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--json",
            "--skip-git-repo-check", "-m", "gpt-5.6-luna", "-s", "read-only",
            "-c", 'approval_policy="never"', "-c", 'model_reasoning_effort="low"',
            "-c", 'web_search="disabled"', "-c", "skills.max_context_tokens=1"]
    for feature in ("apps", "plugins", "browser_use", "computer_use", "image_generation", "multi_agent"):
        argv.extend(["--disable", feature])
    argv.append("-")
    save(record / "command.json", {"argv": argv, "model": "gpt-5.6-luna", "effort": "low"}, env)
    result = execute(argv, env, spec["job"]["workspace"], prompt,
                     min(float(spec["job"].get("timeout", 300)), 300),
                     stdout_path=record / "stdout.jsonl", stderr_path=record / "stderr.txt",
                     state_path=record / "process-state.json", redaction_env=env)
    save(record / "outcome.json", {"exit_code": result.code, "output_pending": result.output_pending}, env)
    if result.code or result.output_pending:
        raise AuthError("verification agent did not complete; preserved records: " + str(record))
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
    save(record / "usage.json", completed.get("usage"), env)
    text = messages[-1].strip() if messages else ""
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        value = json.loads(candidate)
    except (TypeError, ValueError) as exc:
        raise ValueError("verification agent returned malformed JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("acceptance"), bool):
        raise ValueError("verification agent omitted boolean acceptance verdict")
    if not isinstance(value.get("findings", []), list):
        raise ValueError("verification agent findings must be a list")
    save(record / "verdict.json", value, env)
    value = _bind_acceptance_ids(task, value)
    save(record / "bound-verdict.json", value, env)
    value.setdefault("evidence_links", []).append(str(record))
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
                    verification_job_id = None
                    for job_id, verifier_future in list(verifiers.items()):
                        if verifier_future is future:
                            verification_job_id = job_id
                            verifiers.pop(job_id, None)
                            break
                    try:
                        completed_results.append(future.result())
                    except Exception as exc:
                        if verification_job_id is not None:
                            try:
                                self.store._reset_verification(
                                    verification_job_id, f"verification future failed: {exc}")
                            except Exception as reset_error:
                                raise RuntimeError("verification cleanup failed") from reset_error
                            completed_results.append({"status": "needs_verification",
                                                      "job_id": verification_job_id})
                        else:
                            # Without a job id there is no safe way to mark a
                            # possibly claimed attempt.  Let launchd restart
                            # the owner so its PID becomes definitely dead
                            # and normal reconciliation can inspect the receipt.
                            raise RuntimeError("worker future failed") from exc
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
        _reject_symlink_components(target)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _reject_symlink_components(target)
        content = self.generate(label)
        desired = content.encode()
        backup = None
        changed = False
        old_content = _read_nofollow(target) if target.exists() else None
        if old_content is not None and old_content != desired:
            suffix = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = target.with_name(target.name + ".bak-" + suffix)
            serial = 1
            while os.path.lexists(backup):
                backup = target.with_name(target.name + f".bak-{suffix}-{serial}")
                serial += 1
            _atomic_write_bytes(backup, old_content)
        if old_content != desired:
            try:
                _atomic_write_bytes(target, desired)
                if _read_nofollow(target) != desired:
                    raise OSError(f"launchd plist readback failed: {target}")
            except Exception:
                try:
                    if old_content is None:
                        _reject_symlink_components(target)
                        if target.exists():
                            target.unlink()
                    else:
                        _atomic_write_bytes(target, old_content)
                except Exception:
                    # Preserve the original failure while leaving the backup
                    # available for manual recovery.
                    pass
                raise
            changed = True
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
    ver = sub.add_parser("verify"); ver.add_argument("--db", default=argparse.SUPPRESS); ver.add_argument("job_id"); ver.add_argument("--evidence", required=True, help="path to structured acceptance review JSON"); ver.add_argument("--json", action="store_true")
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
        elif args.command == "verify": value = store.verify(args.job_id, json.loads(Path(args.evidence).read_text()))
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
