"""Durable, App-independent task execution service.

This module owns scheduling state and launchd integration.  GitHub issue
creation is intentionally outside this process.  A worker may execute a task
but reports ``needs_verification`` until explicit evidence is recorded.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import string
import subprocess
import sys
import threading
import time
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
        key = hashlib.sha256(f"{self.workspace}\0{resource}".encode()).hexdigest()
        self.path = self.lock_root / f"{key}.lock"
        self._file = None

    def acquire(self, blocking=True):
        if self._file is not None:
            return True
        self._file = self.path.open("a+")
        self.path.chmod(0o600)
        try:
            if os.name == "nt":
                import msvcrt
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(self._file.fileno(), mode, 1)
            else:
                import fcntl
                flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                fcntl.flock(self._file.fileno(), flags)
            return True
        except (BlockingIOError, OSError):
            self._file.close(); self._file = None
            return False

    def release(self):
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0); msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close(); self._file = None

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
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db_path.parent.chmod(0o700)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, timeout=timeout, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA); self._conn.commit(); self._harden()
        self._recover_running()

    def _harden(self):
        self.db_path.parent.chmod(0o700)
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

    def _recover_running(self):
        with self.tx() as conn:
            stamp = now()
            conn.execute("UPDATE service_attempts SET status='needs_verification',ended_at=?,error=COALESCE(error,'service restarted while attempt was running') WHERE status='running'", (stamp,))
            conn.execute("UPDATE service_jobs SET state='needs_verification',last_error=COALESCE(last_error,'service restarted while job was running'),updated_at=? WHERE state='running'", (stamp,))
        for task in self.tasks.list_tasks(execution_status="running"):
            try:
                current = self.tasks.get_task(task["id"])
                self.tasks.update_task(task["id"], expected_version=current["version"], execution_status="needs_verification")
            except ConflictError:
                pass

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
            stamp = now()
            conn.execute("""INSERT INTO service_jobs
              (id,task_id,workspace,resource,prompt,context,model,effort,timeout,retry_base,retry_max,state,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                         (jid, task_id, str(workspace), resource, prompt, context, model, effort, float(timeout), float(retry_base), float(retry_max), stamp, stamp))
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
            attempt = row["attempts_count"] + 1; stamp = now()
            conn.execute("UPDATE service_jobs SET state='running',attempts_count=?,updated_at=?,last_error=NULL WHERE id=?", (attempt, stamp, job_id))
            aid = f"attempt_{hashlib.sha256(f'{job_id}:{attempt}'.encode()).hexdigest()[:24]}"
            conn.execute("INSERT INTO service_attempts VALUES (?,?,?,?,?,?,?,?,?)", (aid, job_id, attempt, os.getpid(), "running", stamp, None, None, None))
            return dict(conn.execute("SELECT * FROM service_jobs WHERE id=?", (job_id,)).fetchone())

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
                self.tasks.update_task(job["task_id"], expected_version=current["version"], execution_status="needs_verification")
            except ConflictError:
                pass
        return {"status": terminal, "job_id": job["id"], "attempt": attempt_no}

    def run_once(self, executor: Callable | None = None):
        if executor is None:
            self.auth_guard(load_agents_env(self.tasks.agents_root / ".env"))
        job = self._claim_next()
        if job is None:
            blocked = any(self.tasks.get_task(row["task_id"]) is not None and not self._dependencies_ready(self.tasks.get_task(row["task_id"])) for row in self._ready_rows())
            return {"status": "blocked" if blocked else "idle", **({"reason": "dependencies"} if blocked else {})}
        lock = job.pop("_lock")
        try:
            if executor is None: executor = default_executor
            result = executor({"job_id": job["id"], "task_id": job["task_id"], "workspace": job["workspace"], "prompt": job["prompt"], "context": job["context"], "model": job["model"], "effort": job["effort"], "timeout": job["timeout"], "updates": self.get_job(job["id"])["updates"]})
            return self._finish_attempt(job, result=result)
        except Exception as exc:
            return self._finish_attempt(job, result={"status": "failed", "text": str(exc)}, error=str(exc))
        finally:
            lock.release()

    def verify(self, job_id, evidence):
        job = self.get_job(job_id)
        if job is None: raise KeyError(job_id)
        task = self.tasks.get_task(job["task_id"])
        if not task.get("acceptance_records") or not all(x["verified"] for x in task["acceptance_records"]):
            raise ValueError("all acceptance evidence must be verified first")
        task = self.tasks.add_completion_evidence(task["id"], evidence)
        task = self.tasks.update_task(task["id"], expected_version=task["version"], execution_status="verified")
        with self.tx() as conn:
            conn.execute("UPDATE service_jobs SET state='completed',next_attempt_at=NULL,updated_at=? WHERE id=?", (now(), job_id))
        return self.get_job(job_id)

    @staticmethod
    def auth_guard(env, login_check=None):
        blocked = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CODEX_API_KEY")
        if any(env.get(key) for key in blocked):
            raise AuthError("API inference routes are forbidden; use ChatGPT subscription login")
        if login_check is None:
            def login_check(actual):
                try: return subprocess.run(["codex", "login", "status"], env=actual, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20).returncode == 0
                except (OSError, subprocess.TimeoutExpired): return False
        if not login_check(env):
            raise AuthError("codex login status is not authenticated")
        return True


def default_executor(spec):
    from .runner import Job, run_job
    agents_root = Path(os.environ.get("AGENTS_ROOT", Path(spec["workspace"]).parent)).resolve()
    env = load_agents_env(agents_root / ".env")
    vault = Path(env.get("AGENTS_VAULT_ROOT", ""))
    if not vault.is_dir():
        raise ValueError("AGENTS_VAULT_ROOT must point to an existing Vault")
    prompt = spec["prompt"] + "\n\nContext:\n" + spec["context"]
    for update in spec.get("updates", []):
        prompt += f"\n\nUpdate: {update['message']}\nEvidence: {', '.join(update['evidence_links'])}"
    result = run_job(Job(Path(spec["workspace"]), vault, prompt, provider="codex", codex_model=spec["model"], effort=spec["effort"], timeout=spec["timeout"]), env)
    return result


class Scheduler:
    def __init__(self, store, *, poll_interval=30.0, stop_event=None):
        self.store = store; self.poll_interval = poll_interval; self.stop_event = stop_event or threading.Event(); self._wait = self.stop_event.wait

    def run_once(self, executor=None): return self.store.run_once(executor=executor)

    def run_forever(self, executor=None):
        while not self.stop_event.is_set():
            result = self.run_once(executor=executor)
            if result["status"] in ("idle", "blocked"):
                self._wait(self.poll_interval)
        return "stopped"


class Launchd:
    def __init__(self, agents_root, db_path):
        self.agents_root = Path(agents_root).resolve(); self.db_path = Path(db_path).resolve()

    def generate(self, label="com.agents.service"):
        environment = {"AGENTS_ROOT": str(self.agents_root)}
        vault_root = os.environ.get("AGENTS_VAULT_ROOT")
        if vault_root:
            environment["AGENTS_VAULT_ROOT"] = str(Path(vault_root).resolve())
        payload = {"Label": label, "ProgramArguments": [sys.executable, "-m", "harness.service", "--db", str(self.db_path), "run"], "WorkingDirectory": str(self.agents_root), "EnvironmentVariables": environment, "RunAtLoad": True, "KeepAlive": True, "StandardOutPath": str(self.agents_root / ".local" / "service.log"), "StandardErrorPath": str(self.agents_root / ".local" / "service.error.log")}
        return plistlib.dumps(payload, fmt=plistlib.FMT_XML).decode()

    def install(self, label="com.agents.service", path=None):
        if sys.platform != "darwin": raise OSError("launchd installation is only supported on macOS")
        target = Path(path or Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True); target.write_text(self.generate(label)); target.chmod(0o600); return target

    def start(self, label="com.agents.service", path=None):
        target = path or Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if sys.platform != "darwin": raise OSError("launchd is only supported on macOS")
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
    args = parser.parse_args(argv); store = ServiceStore(args.db)
    try:
        if args.command == "enroll":
            prompt = Path(args.prompt_file).read_text() if args.prompt_file else args.prompt
            value = store.enroll(args.task, args.workspace, prompt, args.context, model=args.model, effort=args.effort, timeout=args.timeout)
        elif args.command == "list": value = store.list_jobs(args.state)
        elif args.command == "show": value = store.get_job(args.job_id)
        elif args.command == "verify": value = store.verify(args.job_id, args.evidence)
        elif args.command == "run-once": value = store.run_once()
        elif args.command == "run": value = Scheduler(store, poll_interval=args.poll).run_forever()
        else:
            launchd = Launchd(store.tasks.agents_root, store.db_path)
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
