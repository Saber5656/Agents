"""Durable local task store and standalone CLI.

The store deliberately has no GitHub client.  Workers capture work locally;
the separate issueization batch can claim a task, call its own agent, and
write back a verified Issue link through this module.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid


class ConfigurationError(ValueError):
    """Required environment roots are missing or invalid."""


class ConflictError(RuntimeError):
    """A caller tried to update a row using an old version or claim."""


class IssueizationError(RuntimeError):
    """Issueization state cannot perform the requested transition."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  purpose TEXT NOT NULL,
  source TEXT,
  source_task_id TEXT REFERENCES tasks(id),
  source_event_key TEXT,
  expected_result TEXT,
  repository TEXT,
  assignee TEXT,
  priority TEXT,
  execution_status TEXT NOT NULL DEFAULT 'planned',
  issueization_state TEXT NOT NULL DEFAULT 'unissued',
  issueization_attempts INTEGER NOT NULL DEFAULT 0,
  claim_token TEXT,
  claim_owner TEXT,
  claim_expires_at TEXT,
  issueization_error TEXT,
  version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS task_discovery_key
  ON tasks(source_task_id, source_event_key)
  WHERE source_task_id IS NOT NULL AND source_event_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS task_evidence (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  link TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'context',
  created_at TEXT NOT NULL,
  PRIMARY KEY(task_id, link, kind)
);
CREATE TABLE IF NOT EXISTS task_acceptance (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  evidence TEXT NOT NULL,
  verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY(task_id, evidence)
);
CREATE TABLE IF NOT EXISTS task_completion (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  evidence TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY(task_id, evidence)
);
CREATE TABLE IF NOT EXISTS task_revisions (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  field TEXT NOT NULL,
  revision INTEGER NOT NULL,
  value TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY(task_id, field, revision)
);
CREATE TABLE IF NOT EXISTS task_dependencies (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  depends_on TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY(task_id, depends_on)
);
CREATE TABLE IF NOT EXISTS issues (
  repository TEXT NOT NULL,
  issue_id INTEGER NOT NULL,
  issue_url TEXT NOT NULL,
  title TEXT,
  body TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(repository, issue_id)
);
CREATE TABLE IF NOT EXISTS task_issues (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  repository TEXT NOT NULL,
  issue_id INTEGER NOT NULL,
  linked_at TEXT NOT NULL,
  PRIMARY KEY(task_id, repository, issue_id),
  FOREIGN KEY(repository, issue_id) REFERENCES issues(repository, issue_id)
);
CREATE TABLE IF NOT EXISTS prs (
  repository TEXT NOT NULL,
  pr_id INTEGER NOT NULL,
  pr_url TEXT NOT NULL,
  title TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(repository, pr_id)
);
CREATE TABLE IF NOT EXISTS task_prs (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  repository TEXT NOT NULL,
  pr_id INTEGER NOT NULL,
  linked_at TEXT NOT NULL,
  PRIMARY KEY(task_id, repository, pr_id),
  FOREIGN KEY(repository, pr_id) REFERENCES prs(repository, pr_id)
);
CREATE TABLE IF NOT EXISTS work_units (
  id TEXT PRIMARY KEY,
  purpose TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_unit_tasks (
  work_unit_id TEXT NOT NULL REFERENCES work_units(id) ON DELETE CASCADE,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  PRIMARY KEY(work_unit_id, task_id)
);
CREATE TABLE IF NOT EXISTS work_unit_issues (
  work_unit_id TEXT NOT NULL REFERENCES work_units(id) ON DELETE CASCADE,
  repository TEXT NOT NULL,
  issue_id INTEGER NOT NULL,
  PRIMARY KEY(work_unit_id, repository, issue_id),
  FOREIGN KEY(repository, issue_id) REFERENCES issues(repository, issue_id)
);
CREATE TABLE IF NOT EXISTS work_unit_prs (
  work_unit_id TEXT NOT NULL REFERENCES work_units(id) ON DELETE CASCADE,
  repository TEXT NOT NULL,
  pr_id INTEGER NOT NULL,
  PRIMARY KEY(work_unit_id, repository, pr_id),
  FOREIGN KEY(repository, pr_id) REFERENCES prs(repository, pr_id)
);
CREATE TABLE IF NOT EXISTS requirements (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  source TEXT,
  acceptance TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'open',
  version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requirement_revisions (
  requirement_id TEXT NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(requirement_id, revision)
);
CREATE TABLE IF NOT EXISTS requirement_tasks (
  requirement_id TEXT NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  relationship TEXT NOT NULL DEFAULT 'implements',
  PRIMARY KEY(requirement_id, task_id)
);
"""


class TaskStore:
    """A small transactional SQLite store safe for threads and processes."""

    def __init__(self, db_path: str | os.PathLike | None = None, *, agents_root=None,
                 vault_root=None, timeout: float = 15.0):
        self.agents_root = Path(agents_root or os.environ.get("AGENTS_ROOT", ""))
        self.vault_root = Path(vault_root or os.environ.get("AGENTS_VAULT_ROOT", ""))
        if not str(self.agents_root) or str(self.agents_root) == ".":
            raise ConfigurationError("AGENTS_ROOT is required; no root is guessed")
        if not str(self.vault_root) or str(self.vault_root) == ".":
            raise ConfigurationError("AGENTS_VAULT_ROOT is required; no Vault is guessed")
        if not self.agents_root.is_dir():
            raise ConfigurationError(f"AGENTS_ROOT does not exist: {self.agents_root}")
        if not self.vault_root.is_dir():
            raise ConfigurationError(f"AGENTS_VAULT_ROOT does not exist: {self.vault_root}")
        if db_path is None:
            local = self.agents_root / ".local"
            local.mkdir(mode=0o700, exist_ok=True)
            db_path = local / "tasks.sqlite3"
        self._memory = str(db_path) == ":memory:"
        self.db_path = Path(db_path)
        if not self._memory:
            self.db_path.parent.mkdir(mode=0o700, exist_ok=True)
            # Create privately before SQLite can create a journal; preserve the
            # permissions of an explicitly selected shared parent directory.
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.db_path, flags, 0o600)
            try:
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:" if self._memory else self.db_path,
                                     timeout=timeout, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._harden_permissions()

    def close(self):
        with self._lock:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.commit()
                self._harden_permissions()
            except Exception:
                self._conn.rollback()
                raise

    def _harden_permissions(self):
        if self._memory:
            return
        # The database may be supplied in an already-existing permissive
        # directory. Keep the store and SQLite sidecars private regardless of
        # the process umask or the directory's previous mode.
        for path in (self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
            if path.exists():
                path.chmod(0o600)

    def _task(self, row):
        if row is None:
            return None
        result = dict(row)
        task_id = result["id"]
        result["evidence_links"] = [r[0] for r in self._conn.execute(
            "SELECT link FROM task_evidence WHERE task_id=? ORDER BY rowid", (task_id,))]
        result["acceptance_evidence"] = [r[0] for r in self._conn.execute(
            "SELECT evidence FROM task_acceptance WHERE task_id=? ORDER BY rowid", (task_id,))]
        result["completion_evidence"] = [r[0] for r in self._conn.execute(
            "SELECT evidence FROM task_completion WHERE task_id=? ORDER BY rowid", (task_id,))]
        result["acceptance_records"] = [{"evidence": r[0], "verified": bool(r[1])} for r in self._conn.execute(
            "SELECT evidence,verified FROM task_acceptance WHERE task_id=? ORDER BY rowid", (task_id,))]
        result["expected_result_history"] = [r[0] for r in self._conn.execute(
            "SELECT value FROM task_revisions WHERE task_id=? AND field='expected_result' ORDER BY revision", (task_id,))]
        result["dependencies"] = [r[0] for r in self._conn.execute(
            "SELECT depends_on FROM task_dependencies WHERE task_id=? ORDER BY depends_on", (task_id,))]
        result["work_units"] = [r[0] for r in self._conn.execute(
            "SELECT work_unit_id FROM work_unit_tasks WHERE task_id=? ORDER BY work_unit_id", (task_id,))]
        # Friendly aliases keep the record convenient for workers while the
        # explicit *_id names remain unambiguous in the schema.
        result["source_task"] = result["source_task_id"]
        result["issueization"] = result["issueization_state"]
        return result

    def _task_by_id(self, task_id):
        return self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def get_task(self, task_id):
        with self._lock:
            return self._task(self._task_by_id(task_id))

    def list_tasks(self, *, execution_status=None, issueization_state=None, repository=None):
        where, args = [], []
        for column, value in (("execution_status", execution_status),
                              ("issueization_state", issueization_state),
                              ("repository", repository)):
            if value is not None:
                where.append(f"{column}=?"); args.append(value)
        query = "SELECT * FROM tasks"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at, id"
        with self._lock:
            return [self._task(r) for r in self._conn.execute(query, args)]

    def create_task(self, *, purpose, source=None, source_task_id=None, source_event_key=None,
                    expected_result=None, evidence_links=None, repository=None, assignee=None,
                    priority=None, dependencies=None, execution_status="planned", work_unit=None,
                    acceptance_evidence=None, completion_evidence=None, task_id=None, _before_commit=None):
        if not purpose or not str(purpose).strip():
            raise ValueError("purpose is required")
        now, task_id = _now(), task_id or _id("task")
        evidence_links = list(dict.fromkeys(evidence_links or []))
        acceptance_evidence = list(dict.fromkeys(acceptance_evidence or []))
        completion_evidence = list(dict.fromkeys(completion_evidence or []))
        dependencies = list(dict.fromkeys(dependencies or []))
        with self._tx() as conn:
            conn.execute("""INSERT INTO tasks
              (id,purpose,source,source_task_id,source_event_key,expected_result,repository,
               assignee,priority,execution_status,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (task_id, str(purpose), source, source_task_id, source_event_key,
                          expected_result, repository, assignee, priority, execution_status, now, now))
            if expected_result is not None:
                self._record_task_revision(conn, task_id, "expected_result", expected_result, now)
            conn.executemany("INSERT INTO task_evidence VALUES (?,?,?,?)",
                             [(task_id, x, "context", now) for x in evidence_links])
            conn.executemany("INSERT INTO task_acceptance VALUES (?,?,0,?)",
                             [(task_id, x, now) for x in acceptance_evidence])
            conn.executemany("INSERT INTO task_completion VALUES (?,?,?)",
                             [(task_id, x, now) for x in completion_evidence])
            conn.executemany("INSERT INTO task_dependencies VALUES (?,?)",
                             [(task_id, x) for x in dependencies])
            if work_unit:
                self._ensure_work_unit(conn, work_unit)
                conn.execute("INSERT OR IGNORE INTO work_unit_tasks VALUES (?,?)", (work_unit, task_id))
            if _before_commit:
                _before_commit(conn)
        return self.get_task(task_id)

    def record_discovery(self, *, originating_task, discovery_key, purpose, expected_result=None,
                         evidence_links=None, repository=None, assignee=None, priority=None,
                         dependencies=None, acceptance_evidence=None):
        if not discovery_key:
            raise ValueError("discovery_key is required for idempotent capture")
        with self._tx() as conn:
            found = conn.execute("SELECT id FROM tasks WHERE source_task_id=? AND source_event_key=?",
                                 (originating_task, discovery_key)).fetchone()
            if found:
                task_id = found[0]
                now = _now()
                current = conn.execute("SELECT expected_result FROM tasks WHERE id=?", (task_id,)).fetchone()
                if expected_result is not None and expected_result != current[0]:
                    self._record_task_revision(conn, task_id, "expected_result", expected_result, now)
                for link in dict.fromkeys(evidence_links or []):
                    conn.execute("INSERT OR IGNORE INTO task_evidence VALUES (?,?,?,?)",
                                 (task_id, link, "context", now))
                for evidence in dict.fromkeys(acceptance_evidence or []):
                    conn.execute("INSERT OR IGNORE INTO task_acceptance VALUES (?,?,0,?)",
                                 (task_id, evidence, now))
                conn.execute("UPDATE tasks SET updated_at=?, expected_result=COALESCE(?,expected_result), version=version+1 WHERE id=?",
                             (now, expected_result, task_id))
            else:
                now, task_id = _now(), _id("task")
                conn.execute("""INSERT INTO tasks
                  (id,purpose,source,source_task_id,source_event_key,expected_result,repository,
                   assignee,priority,execution_status,issueization_state,created_at,updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?, 'unissued',?,?)""",
                             (task_id, purpose, "discovery", originating_task, discovery_key,
                              expected_result, repository, assignee, priority, "planned", now, now))
                if expected_result is not None:
                    self._record_task_revision(conn, task_id, "expected_result", expected_result, now)
                conn.executemany("INSERT INTO task_evidence VALUES (?,?,?,?)",
                                 [(task_id, x, "context", now) for x in dict.fromkeys(evidence_links or [])])
                conn.executemany("INSERT INTO task_acceptance VALUES (?,?,0,?)",
                                 [(task_id, x, now) for x in dict.fromkeys(acceptance_evidence or [])])
                for dependency in dict.fromkeys(dependencies or []):
                    conn.execute("INSERT INTO task_dependencies VALUES (?,?)", (task_id, dependency))
        return self.get_task(task_id)

    capture_discovery = record_discovery
    discover = record_discovery

    def add_acceptance_evidence(self, task_id, evidence, *, verified=False):
        with self._tx() as conn:
            self._require_task(conn, task_id)
            conn.execute("INSERT OR REPLACE INTO task_acceptance VALUES (?,?,?,?)",
                         (task_id, evidence, int(bool(verified)), _now()))
            conn.execute("UPDATE tasks SET version=version+1,updated_at=? WHERE id=?", (_now(), task_id))
        return self.get_task(task_id)

    def add_completion_evidence(self, task_id, evidence):
        with self._tx() as conn:
            self._require_task(conn, task_id)
            conn.execute("INSERT OR IGNORE INTO task_completion VALUES (?,?,?)",
                         (task_id, evidence, _now()))
            conn.execute("UPDATE tasks SET version=version+1,updated_at=? WHERE id=?", (_now(), task_id))
        return self.get_task(task_id)

    def add_evidence(self, task_id, links, *, kind="context"):
        with self._tx() as conn:
            self._require_task(conn, task_id)
            now = _now()
            conn.executemany("INSERT OR IGNORE INTO task_evidence VALUES (?,?,?,?)",
                             [(task_id, link, kind, now) for link in dict.fromkeys(links)])
            conn.execute("UPDATE tasks SET version=version+1,updated_at=? WHERE id=?", (now, task_id))
        return self.get_task(task_id)

    def update_task(self, task_id, *, expected_version, **fields):
        allowed = {"purpose", "source", "expected_result", "repository", "assignee", "priority",
                   "execution_status"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError("unknown task fields: " + ", ".join(sorted(unknown)))
        if not fields:
            return self.get_task(task_id)
        with self._tx() as conn:
            self._require_task(conn, task_id)
            values = [fields[key] for key in fields]
            values += [_now(), task_id, expected_version]
            query = "UPDATE tasks SET " + ", ".join(f"{key}=?" for key in fields)
            query += ", version=version+1, updated_at=? WHERE id=? AND version=?"
            changed = conn.execute(query, values).rowcount
            if not changed:
                raise ConflictError(f"stale task version for {task_id}; expected {expected_version}")
        return self.get_task(task_id)

    def _require_task(self, conn, task_id):
        if not conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
            raise KeyError(f"unknown task: {task_id}")

    def _record_task_revision(self, conn, task_id, field, value, recorded_at):
        row = conn.execute("SELECT COALESCE(MAX(revision), 0) FROM task_revisions WHERE task_id=? AND field=?",
                           (task_id, field)).fetchone()
        conn.execute("INSERT INTO task_revisions VALUES (?,?,?,?,?)",
                     (task_id, field, row[0] + 1, str(value), recorded_at))

    def _ensure_work_unit(self, conn, work_unit, purpose=None):
        conn.execute("INSERT OR IGNORE INTO work_units VALUES (?,?,?)", (work_unit, purpose, _now()))

    def _ensure_issue(self, conn, repository, issue_id, issue_url, title=None, body=None):
        conn.execute("""INSERT INTO issues(repository,issue_id,issue_url,title,body,created_at)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(repository,issue_id) DO UPDATE SET
                       issue_url=excluded.issue_url, title=COALESCE(excluded.title,issues.title),
                       body=COALESCE(excluded.body,issues.body)""",
                     (repository, int(issue_id), issue_url, title, body, _now()))

    def link_issue(self, task_id, repository, issue_id, issue_url, *, claim_token=None,
                   title=None, body=None, verified=False, readback=None):
        if not repository or not issue_url or int(issue_id) <= 0:
            raise ValueError("repository, positive issue_id, and issue_url are required")
        expected_url = f"https://github.com/{repository}/issues/{int(issue_id)}"
        if not verified or issue_url != expected_url:
            raise ValueError("link_issue requires verified=True and the exact GitHub Issue URL")
        if readback is not None:
            rb_repo = readback.get("repository")
            rb_id = readback.get("issue_id", readback.get("number"))
            rb_url = readback.get("url", readback.get("issue_url", readback.get("html_url")))
            if (rb_repo, int(rb_id) if rb_id is not None else None, rb_url) != (repository, int(issue_id), expected_url):
                raise ValueError("Issue readback does not match repository, ID, and URL")
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"unknown task: {task_id}")
            if claim_token is not None and task["claim_token"] != claim_token:
                raise ConflictError("issueization claim is missing or stale")
            self._ensure_issue(conn, repository, int(issue_id), issue_url, title, body)
            conn.execute("INSERT OR IGNORE INTO task_issues VALUES (?,?,?,?)",
                         (task_id, repository, int(issue_id), _now()))
            conn.execute("""UPDATE tasks SET issueization_state='issued',claim_token=NULL,
                         claim_owner=NULL,claim_expires_at=NULL,issueization_error=NULL,
                         version=version+1,updated_at=? WHERE id=?""", (_now(), task_id))
        return self.get_task(task_id)

    def list_issue_links(self, task_id=None):
        query = "SELECT * FROM task_issues"
        args = ()
        if task_id is not None:
            query += " WHERE task_id=?"; args = (task_id,)
        query += " ORDER BY linked_at"
        with self._lock:
            return [dict(r) for r in self._conn.execute(query, args)]

    def claim_issueization(self, task_id, owner, *, lease_seconds=300):
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=lease_seconds)
        token = _id("claim")
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"unknown task: {task_id}")
            if task["issueization_state"] == "issued" or task["issueization_state"] == "claimed":
                raise IssueizationError(f"task is not claimable: {task['issueization_state']}")
            conn.execute("""UPDATE tasks SET issueization_state='claimed',claim_token=?,claim_owner=?,
                         claim_expires_at=?,issueization_attempts=issueization_attempts+1,
                         version=version+1,updated_at=? WHERE id=?""",
                         (token, owner, expires.isoformat(timespec="seconds"), _now(), task_id))
        return self.get_task(task_id)

    def list_issueization_candidates(self, *, limit=None):
        """Return tasks a separate batch may consider, without claiming them."""
        tasks = (self.list_tasks(issueization_state="unissued") +
                 self.list_tasks(issueization_state="retry") +
                 self.list_tasks(issueization_state="ambiguous") +
                 self.list_tasks(issueization_state="claimed"))
        now = datetime.now(timezone.utc).isoformat()
        for task in tasks:
            expires = task.get("claim_expires_at")
            task["reconciliation_required"] = bool(
                task["issueization_state"] == "claimed" and expires and expires <= now)
        return tasks if limit is None else tasks[:limit]

    def claim_next_issueization(self, owner, *, lease_seconds=300):
        """Atomically claim the first currently eligible task, if any."""
        for task in self.list_issueization_candidates():
            try:
                return self.claim_issueization(task["id"], owner, lease_seconds=lease_seconds)
            except IssueizationError:
                continue
        return None

    def mark_issueization_ambiguous(self, task_id, claim_token, diagnostic):
        return self._issueization_result(task_id, claim_token, "ambiguous", diagnostic)

    def record_issueization_failure(self, task_id, claim_token, diagnostic):
        return self._issueization_result(task_id, claim_token, "retry", diagnostic)

    def _issueization_result(self, task_id, claim_token, state, diagnostic):
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"unknown task: {task_id}")
            if task["claim_token"] != claim_token or task["issueization_state"] != "claimed":
                raise ConflictError("issueization claim is missing or stale")
            conn.execute("""UPDATE tasks SET issueization_state=?,claim_token=NULL,claim_owner=NULL,
                         claim_expires_at=NULL,issueization_error=?,version=version+1,updated_at=? WHERE id=?""",
                         (state, diagnostic, _now(), task_id))
        return self.get_task(task_id)

    def reconcile_expired_claim(self, task_id, diagnostic=None):
        """Release an expired lease only after the batch has reconciled remote state."""
        now = datetime.now(timezone.utc).isoformat()
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"unknown task: {task_id}")
            if task["issueization_state"] != "claimed" or not task["claim_expires_at"] or task["claim_expires_at"] > now:
                raise IssueizationError("task does not have an expired issueization claim")
            conn.execute("""UPDATE tasks SET issueization_state='ambiguous',claim_token=NULL,
                         claim_owner=NULL,claim_expires_at=NULL,issueization_error=?,
                         version=version+1,updated_at=? WHERE id=?""",
                         (diagnostic or "claim expired; reconcile remote outcome before retry", _now(), task_id))
        return self.get_task(task_id)

    def retry_issueization(self, task_id, *, expected_version=None):
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"unknown task: {task_id}")
            if task["issueization_state"] not in ("ambiguous", "retry"):
                raise IssueizationError("only ambiguous or retry tasks can be retried")
            if expected_version is not None and task["version"] != expected_version:
                raise ConflictError("stale task version")
            conn.execute("UPDATE tasks SET issueization_state='retry',issueization_error=NULL,version=version+1,updated_at=? WHERE id=?",
                         (_now(), task_id))
        return self.get_task(task_id)

    claim_issue = claim_issueization
    record_issue = link_issue

    def import_existing_issue(self, *, repository, issue_id, issue_url, title=None, body=None,
                              purpose=None, source="github-import", acceptance_evidence=None):
        with self._tx() as conn:
            existing = conn.execute("""SELECT t.id FROM tasks t JOIN task_issues ti ON ti.task_id=t.id
                                      WHERE ti.repository=? AND ti.issue_id=? LIMIT 1""",
                                    (repository, int(issue_id))).fetchone()
            if existing:
                task_id = existing[0]
                self._ensure_issue(conn, repository, int(issue_id), issue_url, title, body)
            else:
                now, task_id = _now(), _id("task")
                purpose = purpose or title or f"GitHub Issue {repository}#{issue_id}"
                conn.execute("""INSERT INTO tasks(id,purpose,source,repository,execution_status,
                             issueization_state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)""",
                             (task_id, purpose, source, repository, "planned", "issued", now, now))
                self._ensure_issue(conn, repository, int(issue_id), issue_url, title, body)
                for evidence in dict.fromkeys(acceptance_evidence or []):
                    conn.execute("INSERT INTO task_acceptance VALUES (?,?,0,?)", (task_id, evidence, now))
            conn.execute("INSERT OR IGNORE INTO task_issues VALUES (?,?,?,?)",
                         (task_id, repository, int(issue_id), _now()))
        return self.get_task(task_id)

    def import_backlog(self, path, *, default_repository=None):
        data = json.loads(Path(path).read_text())
        if isinstance(data, dict):
            data = data.get("issues", data.get("items", []))
        result = []
        for item in data:
            number = item.get("number", item.get("issue_id"))
            repo = item.get("repository") or default_repository
            if number is None or not repo:
                continue
            url = item.get("html_url") or item.get("issue_url") or f"https://github.com/{repo}/issues/{number}"
            body = item.get("body") or ""
            acceptance = [line.strip()[6:].strip() for line in body.splitlines()
                          if line.strip().startswith("- [") and "]" in line]
            result.append(self.import_existing_issue(repository=repo, issue_id=number,
                              issue_url=url, title=item.get("title"), body=body,
                              acceptance_evidence=acceptance))
        return result

    def link_pr(self, task_id, repository, pr_id, pr_url, *, title=None):
        if not repository or not pr_url or int(pr_id) <= 0:
            raise ValueError("repository, positive pr_id, and pr_url are required")
        with self._tx() as conn:
            self._require_task(conn, task_id)
            conn.execute("""INSERT INTO prs VALUES (?,?,?,?,?)
              ON CONFLICT(repository,pr_id) DO UPDATE SET pr_url=excluded.pr_url,title=COALESCE(excluded.title,prs.title)""",
                         (repository, int(pr_id), pr_url, title, _now()))
            conn.execute("INSERT OR IGNORE INTO task_prs VALUES (?,?,?,?)", (task_id, repository, int(pr_id), _now()))
        return self.list_pr_links(task_id)

    def list_pr_links(self, task_id=None):
        query, args = "SELECT * FROM task_prs", ()
        if task_id is not None:
            query += " WHERE task_id=?"; args = (task_id,)
        with self._lock:
            return [dict(r) for r in self._conn.execute(query + " ORDER BY linked_at", args)]

    def link_work_unit(self, work_unit, *, task_ids=(), issue_ids=(), pr_ids=(), purpose=None):
        with self._tx() as conn:
            self._ensure_work_unit(conn, work_unit, purpose)
            for task_id in task_ids:
                self._require_task(conn, task_id)
                conn.execute("INSERT OR IGNORE INTO work_unit_tasks VALUES (?,?)", (work_unit, task_id))
            for repository, issue_id in issue_ids:
                if not conn.execute("SELECT 1 FROM issues WHERE repository=? AND issue_id=?", (repository, int(issue_id))).fetchone():
                    raise KeyError(f"unknown issue: {repository}#{issue_id}")
                conn.execute("INSERT OR IGNORE INTO work_unit_issues VALUES (?,?,?)", (work_unit, repository, int(issue_id)))
            for repository, pr_id in pr_ids:
                if not conn.execute("SELECT 1 FROM prs WHERE repository=? AND pr_id=?", (repository, int(pr_id))).fetchone():
                    raise KeyError(f"unknown PR: {repository}#{pr_id}")
                conn.execute("INSERT OR IGNORE INTO work_unit_prs VALUES (?,?,?)", (work_unit, repository, int(pr_id)))
        return self.get_work_unit(work_unit)

    def get_work_unit(self, work_unit):
        with self._lock:
            row = self._conn.execute("SELECT * FROM work_units WHERE id=?", (work_unit,)).fetchone()
            if row is None:
                return None
            tasks = [self._task(self._task_by_id(r[0])) for r in self._conn.execute("SELECT task_id FROM work_unit_tasks WHERE work_unit_id=?", (work_unit,))]
            issues = [dict(r) for r in self._conn.execute("SELECT i.* FROM issues i JOIN work_unit_issues w ON w.repository=i.repository AND w.issue_id=i.issue_id WHERE w.work_unit_id=?", (work_unit,))]
            prs = [dict(r) for r in self._conn.execute("SELECT p.* FROM prs p JOIN work_unit_prs w ON w.repository=p.repository AND w.pr_id=p.pr_id WHERE w.work_unit_id=?", (work_unit,))]
            return {"id": row["id"], "purpose": row["purpose"], "created_at": row["created_at"], "tasks": tasks, "issues": issues, "prs": prs}

    def create_requirement(self, text, *, source=None, acceptance=None, requirement_id=None):
        now, requirement_id = _now(), requirement_id or _id("req")
        with self._tx() as conn:
            conn.execute("INSERT INTO requirements VALUES (?,?,?,?,?,?,?,?)",
                         (requirement_id, text, source, json.dumps(list(acceptance or [])), "open", 0, now, now))
        return self.get_requirement(requirement_id)

    def get_requirement(self, requirement_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM requirements WHERE id=?", (requirement_id,)).fetchone()
            if row is None:
                return None
            result = dict(row); result["acceptance"] = _json(result["acceptance"], [])
            result["revisions"] = [r[0] for r in self._conn.execute("SELECT text FROM requirement_revisions WHERE requirement_id=? ORDER BY revision", (requirement_id,))]
            result["task_ids"] = [r[0] for r in self._conn.execute("SELECT task_id FROM requirement_tasks WHERE requirement_id=?", (requirement_id,))]
            return result

    def list_requirements(self):
        with self._lock:
            ids = [r[0] for r in self._conn.execute("SELECT id FROM requirements ORDER BY created_at, id")]
            return [self.get_requirement(x) for x in ids]

    def add_requirement_revision(self, requirement_id, text):
        with self._tx() as conn:
            row = conn.execute("SELECT version FROM requirements WHERE id=?", (requirement_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown requirement: {requirement_id}")
            conn.execute("INSERT INTO requirement_revisions VALUES (?,?,?,?)", (requirement_id, row[0] + 1, text, _now()))
            conn.execute("UPDATE requirements SET version=version+1,updated_at=? WHERE id=?", (_now(), requirement_id))
        return self.get_requirement(requirement_id)

    def link_requirement_task(self, requirement_id, task_id, relationship="implements"):
        with self._tx() as conn:
            if not conn.execute("SELECT 1 FROM requirements WHERE id=?", (requirement_id,)).fetchone():
                raise KeyError(f"unknown requirement: {requirement_id}")
            self._require_task(conn, task_id)
            conn.execute("INSERT OR REPLACE INTO requirement_tasks VALUES (?,?,?)", (requirement_id, task_id, relationship))
        return self.get_requirement(requirement_id)

    def completion_report(self):
        """Return a conservative all-complete result with missing evidence."""
        with self._lock:
            requirements = self.list_requirements()
            tasks = self.list_tasks()
            if requirements:
                required_task_ids = {task_id for requirement in requirements for task_id in requirement["task_ids"]}
                missing_requirements = [r["id"] for r in requirements if not r["task_ids"]]
            else:
                required_task_ids = {task["id"] for task in tasks}
                missing_requirements = []
            by_id = {task["id"]: task for task in tasks}
            missing_tasks = []
            for task_id in sorted(required_task_ids):
                task = by_id.get(task_id)
                if task is None or task["execution_status"] not in ("completed", "verified"):
                    missing_tasks.append({"task_id": task_id, "reason": "execution not complete"})
                elif not task["acceptance_records"]:
                    missing_tasks.append({"task_id": task_id, "reason": "acceptance evidence missing"})
                elif not all(record["verified"] for record in task["acceptance_records"]):
                    missing_tasks.append({"task_id": task_id, "reason": "acceptance evidence not verified"})
                elif not task["completion_evidence"]:
                    missing_tasks.append({"task_id": task_id, "reason": "completion evidence missing"})
            return {"complete": not missing_requirements and not missing_tasks,
                    "missing_requirements": missing_requirements, "missing_tasks": missing_tasks,
                    "requirements": requirements}


def _store_from(args):
    return TaskStore(args.db)


def _emit(value, as_json=False):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        return
    if isinstance(value, list):
        for row in value:
            print(f"{row['id']}  {row.get('execution_status','')}  {row.get('issueization_state','')}  {row.get('purpose','')}")
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (list, dict)):
                item = json.dumps(item, ensure_ascii=False)
            print(f"{key}: {item}")
    else:
        print(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Durable local Agents task store")
    parser.add_argument("--db", default=None, help="SQLite path (default: $AGENTS_ROOT/.local/tasks.sqlite3)")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create"); create.add_argument("--db", default=argparse.SUPPRESS); create.add_argument("--purpose", required=True); create.add_argument("--source"); create.add_argument("--repository"); create.add_argument("--assignee"); create.add_argument("--priority"); create.add_argument("--status", default="planned"); create.add_argument("--evidence", action="append", default=[]); create.add_argument("--acceptance", action="append", default=[]); create.add_argument("--json", action="store_true")
    listing = sub.add_parser("list"); listing.add_argument("--db", default=argparse.SUPPRESS); listing.add_argument("--status"); listing.add_argument("--issueization"); listing.add_argument("--repository"); listing.add_argument("--json", action="store_true")
    show = sub.add_parser("show"); show.add_argument("--db", default=argparse.SUPPRESS); show.add_argument("task_id"); show.add_argument("--json", action="store_true")
    imp = sub.add_parser("import"); imp.add_argument("--db", default=argparse.SUPPRESS); imp.add_argument("path"); imp.add_argument("--repository"); imp.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    store = _store_from(args)
    try:
        if args.command == "create":
            value = store.create_task(purpose=args.purpose, source=args.source, repository=args.repository,
                                      assignee=args.assignee, priority=args.priority, execution_status=args.status,
                                      evidence_links=args.evidence, acceptance_evidence=args.acceptance)
        elif args.command == "list":
            value = store.list_tasks(execution_status=args.status, issueization_state=args.issueization, repository=args.repository)
        elif args.command == "show":
            value = store.get_task(args.task_id)
            if value is None:
                parser.error(f"unknown task: {args.task_id}")
        else:
            value = store.import_backlog(args.path, default_repository=args.repository)
        _emit(value, getattr(args, "json", False))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
