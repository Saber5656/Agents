"""Durable local work-unit, chat, and merged-worktree lifecycle primitives.

The module keeps local provenance separate from the Codex App backend.  A
backend adapter is deliberately injected by callers; this module never treats
a client-only setup identifier as a ready thread identifier and never guesses
that a local record proves a remote operation succeeded.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Callable


class LifecycleError(RuntimeError):
    """A lifecycle transition is unavailable or could not be verified."""


class CleanupError(LifecycleError):
    """A merged worktree is not safe to remove yet."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _full_oid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
        raise LifecycleError("immutable full commit OID is required")
    return value.lower()


def _copy(value):
    return deepcopy(value)


def _thread_id(value):
    if not isinstance(value, dict):
        return None
    return value.get("threadId") or value.get("thread_id") or value.get("ready_thread_id")


def _client_id(value):
    if not isinstance(value, dict):
        return None
    return value.get("clientThreadId") or value.get("client_thread_id")


class LifecycleStore:
    """Atomic/private JSON registry for one or more linked work units."""

    def __init__(self, path, *, vault_root=None):
        self.path = Path(path).absolute()
        self.vault_root = Path(vault_root or self.path.parent).resolve()
        if not self.vault_root.is_dir():
            raise LifecycleError("an existing Vault directory is required")
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise LifecycleError("lifecycle registry file and immediate parent must not be symlinks")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        current = self.path.parent.resolve()
        immediate_parent = current
        while True:
            info = current.stat()
            owner_ok = not hasattr(os, "geteuid") or info.st_uid in (0, os.geteuid())
            mode = stat.S_IMODE(info.st_mode)
            sticky = current != immediate_parent and bool(mode & stat.S_ISVTX) and owner_ok
            if not owner_ok or (mode & 0o022 and not sticky):
                raise LifecycleError(f"lifecycle registry parent must not be writable by other users: {current}")
            if current == current.parent:
                break
            current = current.parent
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        if self.lock_path.is_symlink():
            raise LifecycleError("lifecycle lock must not be a symlink")
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self._locked():
            if not self.path.exists():
                self._write({"version": 1, "work_units": {}})

    @contextmanager
    def _locked(self):
        if self.lock_path.is_symlink():
            raise LifecycleError("lifecycle lock must not be a symlink")
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        lock = os.fdopen(fd, "a+")
        try:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()

    def _read(self):
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags)
            try:
                with os.fdopen(fd, "r") as stream:
                    fd = None
                    value = json.load(stream)
            finally:
                if fd is not None:
                    os.close(fd)
        except (OSError, ValueError, TypeError) as exc:
            raise LifecycleError("lifecycle registry is unreadable; preserve it for reconciliation") from exc
        if not isinstance(value, dict) or not isinstance(value.get("work_units"), dict):
            raise LifecycleError("lifecycle registry has an invalid shape")
        return value

    def _write(self, value):
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode()
        fd, temporary = tempfile.mkstemp(prefix=".lifecycle-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.path.chmod(0o600)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, unit_id):
        with self._locked():
            value = self._read()["work_units"].get(unit_id)
            return _copy(value) if value is not None else None

    def list_units(self):
        with self._locked():
            return [_copy(value) for value in self._read()["work_units"].values()]

    def update(self, unit_id, mutator: Callable[[dict], None]):
        with self._locked():
            data = self._read()
            unit = data["work_units"].get(unit_id)
            if unit is None:
                raise KeyError(unit_id)
            mutator(unit)
            unit["updated_at"] = _now()
            self._write(data)
            return _copy(unit)

    def claim_chat_create(self, unit_id, prompt):
        """Atomically become the one creator for a not-yet-created chat."""
        with self._locked():
            data = self._read(); unit = data["work_units"].get(unit_id)
            if unit is None:
                raise KeyError(unit_id)
            if unit["chat"].get("state") != "not_created":
                return None
            unit["chat"].update({"state": "creating", "requested_prompt": str(prompt), "create_started_at": _now()})
            unit["updated_at"] = _now(); self._write(data)
            return _copy(unit)

    def claim_handoff(self, unit_id, handoff):
        """Atomically reserve one current-turn handoff for a work unit."""
        with self._locked():
            data = self._read(); unit = data["work_units"].get(unit_id)
            if unit is None:
                raise KeyError(unit_id)
            prior = unit["chat"].get("handoff") or {}
            if prior.get("state") in ("creating", "ambiguous", "pending"):
                return None
            unit["chat"]["handoff"] = _copy(handoff)
            unit["updated_at"] = _now(); self._write(data)
            return _copy(unit)

    def register(self, unit_id, *, purpose, repository, project_id, host_id,
                 base_oid, worktree, repository_path=None, branch=None,
                 task_ids=(), issue_ids=(), pr_ids=(), context_path=None,
                 published_commit=None):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", str(unit_id)):
            raise LifecycleError("invalid work-unit identity")
        if not purpose or not repository or not host_id:
            raise LifecycleError("purpose, repository, and host are required")
        base_oid = _full_oid(base_oid)
        worktree = Path(worktree).absolute()
        if not worktree.is_dir():
            raise LifecycleError("worktree must be an existing directory")
        if branch in ("main", "master") or (branch and branch.startswith("-")):
            raise LifecycleError("cleanup cannot target the canonical branch")
        repository_path = Path(repository_path).resolve() if repository_path else None
        context_path = str(Path(context_path).resolve()) if context_path else None
        immutable = {
            "purpose": str(purpose), "repository": str(repository),
            "project_id": str(project_id) if project_id is not None else None,
            "host_id": str(host_id),
            "base_oid": base_oid, "worktree": str(worktree.resolve()),
            "repository_path": str(repository_path) if repository_path else None,
            "branch": branch, "task_ids": list(dict.fromkeys(map(str, task_ids))),
            "issue_ids": list(dict.fromkeys(map(str, issue_ids))),
            "pr_ids": list(dict.fromkeys(map(str, pr_ids))),
            "published_commit": _full_oid(published_commit) if published_commit else None,
        }
        with self._locked():
            data = self._read()
            existing = data["work_units"].get(str(unit_id))
            if existing is not None:
                for key, value in immutable.items():
                    if existing.get(key) != value:
                        raise LifecycleError(f"work-unit identity is immutable: {key}")
                return _copy(existing)
            stamp = _now()
            unit = {
                "id": str(unit_id), **immutable,
                "context_path": context_path,
                "delivery": {"merged": False, "main_synced": False,
                              "context_saved": False},
                "chat": {"state": "not_created", "marker": f"<!-- agents-work-unit:{unit_id} -->",
                         "ready_thread_id": None, "client_thread_id": None,
                         "host_id": str(host_id), "cwd": None, "base_oid": None,
                         "handoff": None, "archive_receipt": None, "unarchive_receipt": None},
                "cleanup": {"state": "pending", "receipt": None},
                "created_at": stamp, "updated_at": stamp,
            }
            data["work_units"][str(unit_id)] = unit
            self._write(data)
            return _copy(unit)

    def set_delivery(self, unit_id, *, merged, main_synced, context_saved,
                     context_path=None, merge_commit=None, main_sync_ref=None):
        def mutate(unit):
            if context_path is not None:
                unit["context_path"] = str(Path(context_path).resolve())
            unit["delivery"] = {"merged": bool(merged), "main_synced": bool(main_synced),
                                "context_saved": bool(context_saved),
                                "merge_commit": _full_oid(merge_commit) if merge_commit else None,
                                "main_sync_ref": main_sync_ref}
        return self.update(unit_id, mutate)


class ChatLifecycle:
    """Reconcile injected App backend operations against a local work unit."""

    def __init__(self, store: LifecycleStore, *, writer_guard=None):
        self.store = store
        self.writer_guard = writer_guard

    @staticmethod
    def _payload(unit, prompt):
        return f"{unit['chat']['marker']}\n\n{prompt}"

    def _unit(self, unit_id):
        unit = self.store.get(unit_id)
        if unit is None:
            raise KeyError(unit_id)
        return unit

    @staticmethod
    def _thread_marker(unit, thread):
        if not isinstance(thread, dict):
            return False
        if thread.get("work_unit_id") == unit["id"]:
            return True
        text = " ".join(str(thread.get(key, "")) for key in ("title", "summary", "prompt", "description"))
        return unit["chat"]["marker"] in text

    def _verify_thread(self, unit, thread, *, allow_archived=True, require_ready=False):
        if not self._thread_marker(unit, thread):
            raise LifecycleError("remote thread does not identify this work unit")
        thread_cwd = thread.get("cwd") or thread.get("worktree") or thread.get("workspace")
        thread_base = thread.get("base_oid") or thread.get("base") or thread.get("immutable_base")
        if thread_cwd is None or Path(thread_cwd).resolve() != Path(unit["worktree"]).resolve():
            raise LifecycleError("remote thread cwd does not match the recorded worktree")
        if thread_base is None or str(thread_base).lower() != unit["base_oid"]:
            raise LifecycleError("remote thread base does not match the immutable base")
        if thread.get("hostId", thread.get("host_id", unit["host_id"])) != unit["host_id"]:
            raise LifecycleError("remote thread host does not match the recorded host")
        remote_tasks = thread.get("task_ids")
        if remote_tasks is None and thread.get("task_id") is not None:
            remote_tasks = [thread["task_id"]]
        if remote_tasks is not None and set(map(str, remote_tasks)) != set(unit["task_ids"]):
            raise LifecycleError("remote thread task scope does not match the work unit")
        if not allow_archived and thread.get("archived"):
            raise LifecycleError("remote thread is archived")
        if require_ready:
            status = str(thread.get("status", "")).lower()
            if status in ("running", "in_progress", "active", "failed", "incomplete", "needs_verification", "pending"):
                raise LifecycleError("remote thread is not ready")
            if status not in ("ready", "completed", "succeeded", "success", "archived") and not thread.get("archived"):
                raise LifecycleError("remote thread readiness is unverified")
        return thread

    @staticmethod
    def _pages(backend):
        cursor = None
        seen = set()
        for _ in range(1000):
            response = backend.list_threads(cursor=cursor)
            if isinstance(response, list):
                rows, next_cursor = response, None
            elif isinstance(response, dict):
                rows = response.get("threads", response.get("items", []))
                next_cursor = response.get("next_cursor", response.get("nextCursor"))
            else:
                raise LifecycleError("thread listing response is malformed")
            if not isinstance(rows, list):
                raise LifecycleError("thread listing page is malformed")
            yield from rows
            if not next_cursor:
                return
            if str(next_cursor) in seen:
                raise LifecycleError("thread listing cursor repeated")
            seen.add(str(next_cursor)); cursor = next_cursor
        raise LifecycleError("thread listing pagination did not terminate")

    def _save_chat(self, unit_id, **fields):
        def mutate(unit):
            unit["chat"].update(fields)
        return self._public(self.store.update(unit_id, mutate))

    @staticmethod
    def _public(unit):
        result = _copy(unit)
        result["state"] = result["chat"]["state"]
        result["ready_thread_id"] = result["chat"].get("ready_thread_id")
        result["client_thread_id"] = result["chat"].get("client_thread_id")
        return result

    def _readback(self, unit, backend, thread_id, *, require_ready=False):
        try:
            thread = backend.read_thread(thread_id, host_id=unit["host_id"])
        except Exception as exc:
            raise LifecycleError("ready thread readback is incomplete") from exc
        if str(_thread_id(thread)) != str(thread_id):
            raise LifecycleError("remote readback returned a different thread identity")
        return self._verify_thread(unit, thread, require_ready=require_ready)

    def _adopt_ready(self, unit, backend, response, *, handoff=None):
        thread_id = _thread_id(response)
        if not thread_id:
            raise LifecycleError("remote response has no ready thread ID")
        thread = self._readback(unit, backend, thread_id, require_ready=True)
        fields = {"state": "archived" if thread.get("archived") else "ready",
                  "ready_thread_id": str(thread_id), "client_thread_id": None,
                  "host_id": thread.get("hostId", thread.get("host_id", unit["host_id"])),
                  "cwd": str(Path(thread.get("cwd") or thread.get("worktree") or thread.get("workspace")).resolve()),
                  "base_oid": str(thread.get("base_oid") or thread.get("base") or thread.get("immutable_base")).lower()}
        if handoff is not None:
            fields["handoff"] = handoff
        return self._save_chat(unit["id"], **fields)

    def reconcile(self, unit_id, backend):
        unit = self._unit(unit_id)
        chat = unit["chat"]
        candidates = []
        for thread in self._pages(backend):
            if self._thread_marker(unit, thread):
                candidates.append(thread)
            elif chat.get("client_thread_id") and _client_id(thread) == chat["client_thread_id"]:
                candidates.append(thread)
        if not candidates:
            return self._save_chat(unit_id, state="ambiguous" if chat["state"] in ("creating", "ambiguous") else chat["state"])
        identities = {_thread_id(row) for row in candidates if _thread_id(row)}
        if len(identities) > 1:
            raise LifecycleError("multiple ready threads match this operation; reconcile their identities")
        ready = next((row for row in candidates if _thread_id(row)), None)
        if ready is not None:
            return self._adopt_ready(unit, backend, ready)
        pending = next((row for row in candidates if _client_id(row)), None)
        if pending is not None:
            return self._save_chat(unit_id, state="pending", client_thread_id=_client_id(pending))
        return self._save_chat(unit_id, state="ambiguous")

    def create(self, unit_id, prompt, backend):
        unit = self._unit(unit_id); chat = unit["chat"]
        if chat.get("ready_thread_id"):
            try:
                self._readback(unit, backend, chat["ready_thread_id"], require_ready=True)
            except LifecycleError as exc:
                return self._save_chat(unit_id, state="ambiguous", last_error=str(exc), uncertain_at=_now())
            return self._public(unit)
        if chat["state"] in ("creating", "ambiguous", "pending"):
            return self.reconcile(unit_id, backend)
        unit = self.store.claim_chat_create(unit_id, prompt)
        if unit is None:
            return self.reconcile(unit_id, backend)
        try:
            response = backend.create_thread(
                prompt=self._payload(unit, str(prompt)), work_unit_id=unit["id"],
                project_id=unit["project_id"], host_id=unit["host_id"],
                worktree=unit["worktree"], base_oid=unit["base_oid"])
        except Exception as exc:
            return self._save_chat(unit_id, state="ambiguous", last_error=str(exc),
                                   uncertain_at=_now())
        thread_id = _thread_id(response)
        client_id = _client_id(response)
        if thread_id:
            try:
                return self._adopt_ready(unit, backend, response)
            except LifecycleError as exc:
                return self._save_chat(unit_id, state="ambiguous", last_error=str(exc), uncertain_at=_now())
        if client_id:
            return self._save_chat(unit_id, state="pending", client_thread_id=str(client_id),
                                   ready_thread_id=None)
        return self._save_chat(unit_id, state="ambiguous", last_error="remote response has no thread identity",
                               uncertain_at=_now())

    def _require_ready(self, unit_id, backend):
        unit = self._unit(unit_id)
        thread_id = unit["chat"].get("ready_thread_id")
        if not thread_id or unit["chat"].get("state") not in ("ready", "archived"):
            raise LifecycleError("a ready remote thread ID is required")
        thread = self._readback(unit, backend, thread_id)
        return unit, thread_id, thread

    def send(self, unit_id, prompt, backend):
        unit, thread_id, thread = self._require_ready(unit_id, backend)
        if thread.get("archived"):
            raise LifecycleError("archived chat cannot receive a message")
        handoff_id = hashlib.sha256(f"{unit_id}\0{thread_id}\0{prompt}".encode()).hexdigest()[:24]
        marker = f"<!-- agents-handoff:{handoff_id} -->"
        claim = self.store.claim_handoff(unit_id, {
            "state": "creating", "id": handoff_id, "marker": marker,
            "kind": "message", "prompt": str(prompt), "at": _now(),
        })
        if claim is None:
            return self.reconcile_handoff(unit_id, backend)
        payload = f"{self._payload(unit, str(prompt))}\n{marker}"
        try:
            response = backend.send_message(thread_id, payload, host_id=unit["host_id"])
            if _thread_id(response) and str(_thread_id(response)) != str(thread_id):
                raise LifecycleError("message response changed the ready thread identity")
            readback = self._readback(unit, backend, thread_id)
            return self._save_chat(unit_id, handoff={"state": "ready", "id": handoff_id,
                                      "marker": marker, "kind": "message", "prompt": str(prompt), "at": _now()},
                                   last_handoff={"kind": "message", "prompt": str(prompt), "at": _now()},
                                   state="archived" if readback.get("archived") else "ready")
        except Exception as exc:
            self._save_chat(unit_id, handoff={"state": "ambiguous", "id": handoff_id,
                                "marker": marker, "kind": "message", "prompt": str(prompt),
                                "error": str(exc), "at": _now()})
            raise LifecycleError("message requires remote readback") from exc

    def handoff(self, unit_id, prompt, backend):
        unit, thread_id, thread = self._require_ready(unit_id, backend)
        prior = unit["chat"].get("handoff") or {}
        if prior.get("state") in ("creating", "ambiguous", "pending"):
            return self.reconcile_handoff(unit_id, backend)
        handoff_id = hashlib.sha256(f"{unit_id}\0{thread_id}\0{prompt}".encode()).hexdigest()[:24]
        handoff_marker = f"<!-- agents-handoff:{handoff_id} -->"
        current = thread.get("current_turn")
        running = str(thread.get("status", "")).lower() in ("running", "in_progress", "active")
        running = running or (isinstance(current, dict) and str(current.get("status", "")).lower() in ("running", "in_progress", "active"))
        running = running or (isinstance(current, dict) and current.get("completed") is False)
        scope = (f"{self._payload(unit, str(prompt))}\n{handoff_marker}\n\nLatest requirements: {prompt}\n"
                 f"Existing task IDs: {', '.join(unit['task_ids']) or '(none)'}\n"
                 f"Existing Issue references: {', '.join(unit['issue_ids']) or '(none)'}\n"
                 f"Repository: {unit['repository']}\nImmutable base: {unit['base_oid']}\nWorktree: {unit['worktree']}")
        claimed = self.store.claim_handoff(unit_id, {"state": "creating", "id": handoff_id,
                                                       "marker": handoff_marker, "prompt": str(prompt), "at": _now()})
        if claimed is None:
            return self.reconcile_handoff(unit_id, backend)
        unit = claimed
        try:
            if running:
                response = backend.fork_thread(thread_id, follow_up=scope, host_id=unit["host_id"])
                kind = "fork"
            else:
                response = backend.send_message(thread_id, scope, host_id=unit["host_id"])
                kind = "message"
        except Exception as exc:
            return self._save_chat(unit_id, handoff={**unit["chat"].get("handoff", {}), "state": "ambiguous", "kind": kind if "kind" in locals() else "unknown", "prompt": str(prompt), "error": str(exc), "at": _now()})
        new_id = _thread_id(response)
        if not new_id:
            client = _client_id(response)
            if client:
                return self._save_chat(unit_id, handoff={**unit["chat"].get("handoff", {}), "state": "pending", "kind": kind, "client_thread_id": client, "prompt": str(prompt), "at": _now()})
            return self._save_chat(unit_id, handoff={**unit["chat"].get("handoff", {}), "state": "ambiguous", "kind": kind, "prompt": str(prompt), "error": "missing remote identity", "at": _now()})
        try:
            return self._adopt_ready(unit, backend, response,
                                     handoff={"state": "ready", "kind": kind, "prompt": str(prompt), "at": _now()})
        except LifecycleError as exc:
            return self._save_chat(unit_id, handoff={**unit["chat"].get("handoff", {}), "state": "ambiguous", "kind": kind, "prompt": str(prompt), "error": str(exc), "at": _now()})

    def reconcile_handoff(self, unit_id, backend):
        unit = self._unit(unit_id)
        handoff = unit["chat"].get("handoff") or {}
        marker = handoff.get("marker")
        if not marker:
            return self._public(unit)
        candidates = []
        for thread in self._pages(backend):
            text = " ".join(str(thread.get(key, "")) for key in ("title", "summary", "prompt", "description"))
            if marker in text or (handoff.get("client_thread_id") and _client_id(thread) == handoff["client_thread_id"]):
                candidates.append(thread)
        identities = {_thread_id(row) for row in candidates if _thread_id(row)}
        if len(identities) > 1:
            raise LifecycleError("multiple ready threads match this operation; reconcile their identities")
        ready = next((row for row in candidates if _thread_id(row)), None)
        if ready is not None:
            try:
                return self._adopt_ready(unit, backend, ready,
                                         handoff={**handoff, "state": "ready", "at": _now()})
            except LifecycleError as exc:
                return self._save_chat(unit_id, handoff={**handoff, "state": "ambiguous", "error": str(exc), "at": _now()})
        pending = next((row for row in candidates if _client_id(row)), None)
        if pending is not None:
            return self._save_chat(unit_id, handoff={**handoff, "state": "pending", "client_thread_id": _client_id(pending), "at": _now()})
        return self._save_chat(unit_id, handoff={**handoff, "state": "ambiguous", "at": _now()})

    def _require_delivery(self, unit):
        delivery = unit["delivery"]
        if not all(delivery.get(key) for key in ("merged", "main_synced", "context_saved")):
            raise LifecycleError("merge, main synchronization, and saved context are required")
        context_path = unit.get("context_path")
        if not context_path:
            raise LifecycleError("saved context readback is unavailable")
        try:
            Path(context_path).resolve().relative_to(self.store.vault_root.resolve())
        except ValueError as exc:
            raise LifecycleError("saved context must remain inside the configured Vault") from exc
        if not Path(context_path).is_file():
            raise LifecycleError("saved context readback is unavailable")

    def archive(self, unit_id, backend):
        unit, thread_id, thread = self._require_ready(unit_id, backend)
        self._require_delivery(unit)
        status = str(thread.get("status", "")).lower()
        current = thread.get("current_turn")
        if status in ("running", "in_progress", "active") or (isinstance(current, dict) and str(current.get("status", "")).lower() in ("running", "in_progress", "active")):
            raise LifecycleError("running chat cannot be archived")
        if isinstance(current, dict) and current.get("completed") is False:
            raise LifecycleError("running chat cannot be archived")
        if status in ("failed", "incomplete", "needs_verification", "pending"):
            raise LifecycleError("incomplete chat cannot be archived")
        if thread.get("archived"):
            return self._save_chat(unit_id, state="archived", archive_receipt={"state": "archived", "at": _now()})
        try:
            backend.archive_thread(thread_id, host_id=unit["host_id"])
            readback = self._readback(unit, backend, thread_id)
            if not readback.get("archived") and str(readback.get("status", "")).lower() != "archived":
                raise LifecycleError("archive readback did not report archived")
        except Exception as exc:
            self._save_chat(unit_id, archive_receipt={"state": "ambiguous", "error": str(exc), "at": _now()})
            raise LifecycleError("archive requires remote readback") from exc
        return self._save_chat(unit_id, state="archived", archive_receipt={"state": "archived", "at": _now()})

    def unarchive(self, unit_id, backend):
        unit = self._unit(unit_id)
        thread_id = unit["chat"].get("ready_thread_id")
        if not thread_id:
            raise LifecycleError("archived chat has no ready thread reference")
        receipt = unit["chat"].get("unarchive_receipt") or {}
        try:
            thread = self._readback(unit, backend, thread_id)
        except Exception as exc:
            self._save_chat(unit_id, unarchive_receipt={"state": "ambiguous", "thread_id": str(thread_id),
                                                        "error": str(exc), "at": _now()})
            raise LifecycleError("unarchive requires remote readback") from exc
        if not thread.get("archived") and str(thread.get("status", "")).lower() != "archived":
            return self._save_chat(unit_id, state="ready", unarchive_receipt={"state": "ready", "thread_id": str(thread_id), "at": _now()})
        if receipt.get("state") == "ambiguous":
            # The prior attempt may have reached the backend.  Readback above
            # is authoritative; retrying the idempotent transition is safe only
            # when the remote still proves archived.
            pass
        self._save_chat(unit_id, unarchive_receipt={"state": "creating", "thread_id": str(thread_id), "at": _now()})
        try:
            backend.unarchive_thread(thread_id, host_id=unit["host_id"])
            readback = self._readback(unit, backend, thread_id)
            if readback.get("archived") or str(readback.get("status", "")).lower() == "archived":
                raise LifecycleError("unarchive readback remained archived")
        except Exception as exc:
            self._save_chat(unit_id, unarchive_receipt={"state": "ambiguous", "thread_id": str(thread_id),
                                                        "error": str(exc), "at": _now()})
            raise LifecycleError("unarchive requires remote readback") from exc
        return self._save_chat(unit_id, state="ready", unarchive_receipt={"state": "ready", "thread_id": str(thread_id), "at": _now()})

    def cleanup(self, unit_id, git):
        unit = self._unit(unit_id)
        try:
            lock = self._open_worktree_lock(unit["worktree"])
        except OSError as exc:
            raise CleanupError("active writer owns the worktree") from exc
        try:
            return self._cleanup_locked(unit_id, git)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def _open_worktree_lock(self, worktree):
        root = self.store.vault_root / "01-Projects" / "task-lifecycle" / "locks"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = hashlib.sha256(str(Path(worktree).resolve()).encode()).hexdigest()
        path = root / f"{key}.lock"
        if path.is_symlink():
            raise CleanupError("worktree lock must not be a symlink")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        lock = os.fdopen(fd, "a+")
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise
        return lock

    def _writer_state(self, worktree, git):
        if self.writer_guard is None:
            return "unknown"
        try:
            value = self.writer_guard(worktree, git)
        except Exception:
            return "unknown"
        if value is True:
            return "active"
        if value is False:
            return "inactive"
        return value if value in ("active", "inactive", "unknown") else "unknown"

    def _validate_cleanup_receipt(self, unit, receipt):
        if not isinstance(receipt, dict):
            raise CleanupError("cleanup receipt is malformed; preserve data")
        required = ("unit_id", "repository", "worktree", "branch", "head_oid")
        if receipt.get("unit_id") != unit["id"]:
            raise CleanupError("cleanup receipt belongs to a different work unit")
        if receipt.get("state") not in ("prepared", "worktree_removed", "branch_removed"):
            raise CleanupError("cleanup receipt has an unknown state; preserve data")
        for key in required[1:]:
            if key not in receipt:
                raise CleanupError("cleanup receipt lacks immutable identity")
        if Path(receipt["repository"]).resolve() != Path(unit["repository_path"]).resolve():
            raise CleanupError("cleanup receipt repository does not match the work unit")
        if Path(receipt["worktree"]).resolve() != Path(unit["worktree"]).resolve():
            raise CleanupError("cleanup receipt worktree does not match the work unit")
        if receipt["branch"] != unit["branch"]:
            raise CleanupError("cleanup receipt branch does not match the work unit")
        try:
            if _full_oid(receipt["head_oid"]) != _full_oid(unit.get("published_commit") or receipt["head_oid"]):
                # A work unit has no immutable head until cleanup preparation;
                # compare against the persisted receipt on later retries.
                if unit.get("cleanup", {}).get("receipt", {}).get("head_oid") != receipt["head_oid"]:
                    raise CleanupError("cleanup receipt HEAD does not match recorded identity")
        except LifecycleError as exc:
            raise CleanupError("cleanup receipt HEAD is not a full commit OID") from exc
        return receipt

    def _cleanup_locked(self, unit_id, git):
        unit = self._unit(unit_id)
        cleanup = unit["cleanup"]
        receipt = self._read_cleanup_receipt(unit_id)
        if receipt:
            receipt = self._validate_cleanup_receipt(unit, receipt)
        if cleanup.get("state") in ("prepared", "worktree_removed", "branch_removed") and not receipt:
            raise CleanupError("cleanup receipt is missing; preserve data for reconciliation")
        if receipt and receipt.get("state") == "branch_removed" and cleanup.get("state") != "branch_removed":
            result = self.store.update(unit_id, lambda row: row["cleanup"].update({"state": "branch_removed", "receipt": receipt}))
            result["state"] = "branch_removed"
            return result
        if receipt and receipt.get("state") == "prepared" and cleanup.get("state") == "pending":
            cleanup = dict(cleanup); cleanup["state"] = "prepared"
            self.store.update(unit_id, lambda row: row["cleanup"].update({"state": "prepared", "receipt": receipt}))
        if receipt and receipt.get("state") == "worktree_removed" and cleanup.get("state") != "worktree_removed":
            cleanup = dict(cleanup); cleanup["state"] = "worktree_removed"
            self.store.update(unit_id, lambda row: row["cleanup"].update({"state": "worktree_removed", "receipt": receipt}))
        if cleanup.get("state") == "branch_removed":
            result = self._public(unit)
            result["state"] = "branch_removed"
            return result
        self._require_delivery(unit)
        repository = unit.get("repository_path")
        worktree = unit.get("worktree")
        branch = unit.get("branch")
        if not repository or not branch or not worktree:
            raise CleanupError("repository path, branch, and worktree are required")
        if branch in ("main", "master"):
            raise CleanupError("canonical branch cannot be removed")
        for other in self.store.list_units():
            if other["id"] == unit_id or other["cleanup"].get("state") == "branch_removed":
                continue
            if other.get("worktree") == worktree or other.get("branch") == branch:
                raise CleanupError("another work unit still references the worktree or branch")
        if self._writer_state(worktree, git) != "inactive":
            raise CleanupError("worktree writer state is not proven inactive")
        rows = git.worktrees(repository)
        same_path = lambda left, right: left is not None and right is not None and Path(left).resolve() == Path(right).resolve()
        canonical = [row for row in rows if same_path(row.get("path"), repository)]
        if len(canonical) != 1 or canonical[0].get("branch") not in (None, "main", "master"):
            raise CleanupError("canonical repository identity is ambiguous")
        target_rows = [row for row in rows if same_path(row.get("path"), worktree)]
        if len(target_rows) > 1 or (target_rows and target_rows[0].get("branch") != branch):
            raise CleanupError("task worktree identity does not match the recorded branch")
        same_branch_elsewhere = [row for row in rows if row.get("branch") == branch and not same_path(row.get("path"), worktree)]
        if same_branch_elsewhere:
            raise CleanupError("branch is used by another worktree")
        worktree_present = any(same_path(row.get("path"), worktree) for row in rows)
        if cleanup.get("state") == "worktree_removed" and worktree_present:
            raise CleanupError("cleanup receipt says worktree was removed, but it is present again")
        if not worktree_present and cleanup.get("state") == "prepared":
            receipt = dict(receipt or {})
            receipt["state"] = "worktree_removed"; receipt["at"] = _now()
            self._write_cleanup_receipt(unit_id, receipt)
            self.store.update(unit_id, lambda row: row["cleanup"].update({"state": "worktree_removed", "receipt": receipt}))
            cleanup = dict(cleanup); cleanup["state"] = "worktree_removed"
        if not worktree_present and cleanup.get("state") != "worktree_removed":
            raise CleanupError("recorded worktree is not present in Git worktree registry")
        if cleanup.get("state") != "worktree_removed":
            if git.status(worktree):
                raise CleanupError("worktree has dirty or ignored files")
            if git.branch_ahead(repository, branch):
                raise CleanupError("branch has local-only commits")
            current_head = _full_oid(git.head(worktree))
            branch_head = getattr(git, "branch_head", None)
            if branch_head is None or _full_oid(branch_head(repository, branch)) != current_head:
                raise CleanupError("worktree and branch HEAD identities do not match")
            receipt = {"state": "prepared", "unit_id": unit_id, "repository": repository,
                       "worktree": worktree, "branch": branch, "head_oid": current_head,
                       "at": _now()}
            self._write_cleanup_receipt(unit_id, receipt)
            self.store.update(unit_id, lambda row: row["cleanup"].update({"state": "prepared", "receipt": receipt}))
            try:
                git.remove_worktree(repository, worktree)
            except Exception as exc:
                raise CleanupError("worktree removal was not completed; preserve data") from exc
            receipt["state"] = "worktree_removed"; receipt["at"] = _now()
            self._write_cleanup_receipt(unit_id, receipt)
            self.store.update(unit_id, lambda row: row["cleanup"].update({"state": "worktree_removed", "receipt": receipt}))
        try:
            if receipt and receipt.get("state") == "worktree_removed":
                branch_head = getattr(git, "branch_head", None)
                if branch_head is None or _full_oid(branch_head(repository, branch)) != _full_oid(receipt["head_oid"]):
                    raise CleanupError("branch HEAD identity changed after worktree removal")
            if getattr(git, "branch_exists", lambda *_: True)(repository, branch):
                git.delete_branch(repository, branch)
        except Exception as exc:
            raise CleanupError("worktree removed; branch cleanup remains pending") from exc
        receipt = self._read_cleanup_receipt(unit_id) or {}
        receipt.update({"state": "branch_removed", "at": _now()})
        self._write_cleanup_receipt(unit_id, receipt)
        result = self.store.update(unit_id, lambda row: row["cleanup"].update({"state": "branch_removed", "receipt": receipt}))
        result["state"] = "branch_removed"
        return result

    def _receipt_path(self, unit_id):
        root = self.store.vault_root / "01-Projects" / "task-lifecycle" / "cleanup"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return root / f"{unit_id}.json"

    def _write_cleanup_receipt(self, unit_id, receipt):
        path = self._receipt_path(unit_id)
        if path.is_symlink() or path.parent.is_symlink():
            raise CleanupError("cleanup receipt path must not be a symlink")
        fd, temporary = tempfile.mkstemp(prefix=".cleanup-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(receipt, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path); path.chmod(0o600)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def _read_cleanup_receipt(self, unit_id):
        path = self._receipt_path(unit_id)
        if not path.is_file():
            return None
        if path.is_symlink():
            raise CleanupError("cleanup receipt path must not be a symlink")
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                with os.fdopen(fd, "r") as stream:
                    fd = None
                    value = json.load(stream)
            finally:
                if fd is not None:
                    os.close(fd)
        except (OSError, ValueError) as exc:
            raise CleanupError("cleanup receipt is corrupt; preserve data") from exc
        return value if isinstance(value, dict) else None

    # Explicit names make the lifecycle boundaries easy to call from either a
    # supported App adapter or a local coordinator without changing semantics.
    create_thread = create
    reconcile_thread = reconcile
    handoff_current_turn = handoff
    archive_chat = archive
    unarchive_chat = unarchive
    cleanup_merged_worktree = cleanup


class GitWorktree:
    """Small injectable Git adapter used by cleanup and isolated fixtures."""

    def __init__(self, repository):
        self.repository = str(Path(repository).resolve())

    def _run(self, *args):
        result = subprocess.run(["git", *args], cwd=self.repository,
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise CleanupError((result.stderr or result.stdout).strip() or "git command failed")
        return result.stdout.strip()

    def worktrees(self, repository):
        output = self._run("worktree", "list", "--porcelain")
        rows = []; current = None
        for line in output.splitlines() + [""]:
            if line.startswith("worktree "):
                if current: rows.append(current)
                current = {"path": line[9:]}
            elif current and line.startswith("branch "):
                current["branch"] = line[7:].removeprefix("refs/heads/")
            elif not line and current:
                rows.append(current); current = None
        return rows

    def status(self, worktree):
        result = subprocess.run(["git", "status", "--porcelain=v1", "--ignored", "--untracked-files=all"],
                                cwd=worktree, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise CleanupError(result.stderr.strip() or "git status failed")
        return result.stdout.strip()

    def head(self, worktree):
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree,
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise CleanupError("cannot read worktree HEAD")
        return _full_oid(result.stdout.strip())

    def branch_head(self, repository, branch):
        result = subprocess.run(["git", "rev-parse", f"refs/heads/{branch}"], cwd=self.repository,
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise CleanupError("cannot read branch HEAD")
        return _full_oid(result.stdout.strip())

    def branch_ahead(self, repository, branch):
        # Read the remote advertisement at cleanup time.  A cached
        # origin/main ref can lag behind the actual publication and would
        # incorrectly permit a branch with local-only commits.
        advertised = self._run("ls-remote", "origin", "refs/heads/main")
        remote_oid = (advertised.split()[0] if advertised else "")
        try:
            remote_oid = _full_oid(remote_oid)
        except LifecycleError as exc:
            raise CleanupError("cannot verify the remote main commit") from exc
        reachable = subprocess.run(["git", "cat-file", "-e", f"{remote_oid}^{{commit}}"],
                                   cwd=self.repository, capture_output=True, text=True, timeout=30)
        if reachable.returncode:
            raise CleanupError("remote main commit is not locally reachable")
        result = subprocess.run(["git", "merge-base", "--is-ancestor", branch, remote_oid],
                                cwd=self.repository, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return 0
        if result.returncode == 1:
            return 1
        raise CleanupError("cannot determine local-only commits")

    def remove_worktree(self, repository, worktree):
        self._run("worktree", "remove", "--", worktree)

    def delete_branch(self, repository, branch):
        self._run("branch", "-d", "--", branch)

    def branch_exists(self, repository, branch):
        result = subprocess.run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                                cwd=self.repository, capture_output=True, text=True, timeout=30)
        if result.returncode not in (0, 1):
            raise CleanupError("cannot determine branch state")
        return result.returncode == 0
