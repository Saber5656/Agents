import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from harness.lifecycle import (ChatLifecycle, CleanupError, GitWorktree,
                               LifecycleError, LifecycleStore)


class FakeChatBackend:
    def __init__(self):
        self.threads = []
        self.create_calls = 0
        self.archive_calls = 0
        self.send_calls = []
        self.fork_calls = []
        self.fail_create_once = False
        self.stale_listing_once = False

    def create_thread(self, **payload):
        self.create_calls += 1
        if self.fail_create_once:
            self.fail_create_once = False
            raise OSError("lost response")
        thread = {
            "threadId": f"thread-{self.create_calls}",
            "hostId": payload["host_id"],
            "work_unit_id": payload["work_unit_id"],
            "cwd": payload["worktree"],
            "base_oid": payload["base_oid"],
            "status": "ready",
            "archived": False,
            "prompt": payload["prompt"],
        }
        self.threads.append(thread)
        return dict(thread)

    def list_threads(self, *, cursor=None):
        if self.stale_listing_once:
            self.stale_listing_once = False
            return {"threads": [], "next_cursor": None}
        start = int(cursor or 0)
        page = self.threads[start:start + 1]
        return {"threads": [dict(row) for row in page],
                "next_cursor": str(start + 1) if start + 1 < len(self.threads) else None}

    def read_thread(self, thread_id, *, host_id=None):
        for thread in self.threads:
            if thread["threadId"] == thread_id:
                return dict(thread)
        raise KeyError(thread_id)

    def send_message(self, thread_id, prompt, *, host_id=None):
        self.send_calls.append((thread_id, prompt))
        return {"threadId": thread_id, "status": "ready"}

    def fork_thread(self, thread_id, *, follow_up, host_id=None):
        self.fork_calls.append((thread_id, follow_up))
        source = self.read_thread(thread_id)
        fork = dict(source)
        fork["threadId"] = f"fork-{len(self.fork_calls)}"
        fork["prompt"] = follow_up
        fork["status"] = "ready"
        self.threads.append(fork)
        return fork

    def archive_thread(self, thread_id, *, host_id=None):
        self.archive_calls += 1
        for thread in self.threads:
            if thread["threadId"] == thread_id:
                thread["archived"] = True
                thread["status"] = "archived"
                return dict(thread)
        raise KeyError(thread_id)

    def unarchive_thread(self, thread_id, *, host_id=None):
        for thread in self.threads:
            if thread["threadId"] == thread_id:
                thread["archived"] = False
                thread["status"] = "ready"
                return dict(thread)
        raise KeyError(thread_id)


class FakeGit:
    def __init__(self, repo, worktree, branch="feat/task", head="b" * 40):
        self.repo = str(repo)
        self.worktree = str(worktree)
        self.branch = branch
        self.head_oid = head
        self.removed = False
        self.deleted = False
        self.ahead = 0
        self.other_worktrees = []
        self.active_writer = False

    def worktrees(self, repository):
        return [{"path": self.repo, "branch": "main"},
                *self.other_worktrees,
                *([] if self.removed else [{"path": self.worktree, "branch": self.branch}])]

    def status(self, worktree):
        return ""

    def head(self, worktree):
        return self.head_oid

    def branch_head(self, repository, branch):
        if self.deleted:
            raise CleanupError("missing branch")
        return self.head_oid

    def branch_ahead(self, repository, branch):
        return self.ahead

    def remove_worktree(self, repository, worktree):
        if self.active_writer:
            raise CleanupError("active writer")
        self.removed = True

    def delete_branch(self, repository, branch):
        self.deleted = True

    def branch_exists(self, repository, branch):
        return not self.deleted


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"; self.vault.mkdir()
        self.worktree = self.root / "worktree"; self.worktree.mkdir()
        self.repo = self.root / "repo"; self.repo.mkdir()
        self.registry = LifecycleStore(self.vault / "lifecycle.json", vault_root=self.vault)
        self.base = "a" * 40
        self.unit = self.registry.register(
            "wu-1", purpose="Improve lifecycle", repository="org/repo",
            project_id="project-1", host_id="host-1", base_oid=self.base,
            worktree=self.worktree, repository_path=self.repo, branch="feat/task",
            task_ids=["task-1", "task-2"], issue_ids=["org/repo#14"],
            context_path=self.vault / "context.json")
        self.backend = FakeChatBackend()
        self.chat = ChatLifecycle(
            self.registry,
            writer_guard=lambda _worktree, git: "active" if getattr(git, "active_writer", False) else "inactive",
        )
        self.addCleanup(self.temp.cleanup)

    def test_registry_persists_immutable_work_unit_and_pending_ready_chat(self):
        self.assertEqual(self.unit["base_oid"], self.base)
        self.assertEqual(self.registry.get("wu-1")["task_ids"], ["task-1", "task-2"])
        self.assertEqual(self.registry.get("wu-1")["issue_ids"], ["org/repo#14"])
        with self.assertRaises(LifecycleError):
            self.registry.register("wu-2", purpose="bad", repository="org/repo",
                                   project_id="p", host_id="h", base_oid="short",
                                   worktree=self.worktree)

    def test_projectless_registration_preserves_none_project_without_guessing(self):
        unit = self.registry.register(
            "wu-projectless", purpose="Projectless task", repository="org/repo",
            project_id=None, host_id="host-1", base_oid=self.base,
            worktree=self.worktree)
        self.assertIsNone(unit["project_id"])

    def test_concurrent_first_open_preserves_registered_units(self):
        path = self.vault / "new-registry.json"
        observed = threading.Event(); first_finished = threading.Event()
        original = Path.exists
        errors = []
        def exists(candidate):
            result = original(candidate)
            if candidate == path and threading.current_thread().name == "delayed-init" and not result:
                observed.set()
                first_finished.wait(1)
            return result
        def open_and_register(name):
            try:
                store = LifecycleStore(path, vault_root=self.vault)
                store.register(name, purpose=name, repository="org/repo", project_id="p", host_id="h",
                               base_oid=self.base, worktree=self.worktree)
            except Exception as exc:
                errors.append(exc)
            finally:
                if name == "first": first_finished.set()
        with mock.patch.object(Path, "exists", exists):
            delayed = threading.Thread(target=open_and_register, args=("delayed",), name="delayed-init")
            delayed.start(); self.assertTrue(observed.wait(2))
            first = threading.Thread(target=open_and_register, args=("first",))
            first.start(); first.join(3); delayed.join(3)
        self.assertEqual(errors, [])
        self.assertEqual({row["id"] for row in LifecycleStore(path, vault_root=self.vault).list_units()}, {"first", "delayed"})

    def test_ready_readback_must_match_requested_thread_id(self):
        real = self.backend.create_thread(prompt="x", work_unit_id="wu-1", project_id="p", host_id="host-1",
                                          worktree=str(self.worktree), base_oid=self.base)
        real["threadId"] = "wrong-thread"
        with mock.patch.object(self.backend, "read_thread", return_value=real):
            result = self.chat.create("wu-1", "request", self.backend)
        self.assertEqual(result["state"], "ambiguous")

    def test_reconciliation_rejects_multiple_ready_matches(self):
        self.chat.create("wu-1", "request", self.backend)
        duplicate = dict(self.backend.threads[0]); duplicate["threadId"] = "duplicate"
        self.backend.threads.append(duplicate)
        with self.assertRaisesRegex(LifecycleError, "multiple"):
            self.chat.reconcile("wu-1", self.backend)

    def test_create_readback_records_ready_thread_and_cwd_base(self):
        result = self.chat.create("wu-1", "latest requirements", self.backend)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["ready_thread_id"], "thread-1")
        self.assertEqual(self.backend.create_calls, 1)
        persisted = self.registry.get("wu-1")
        self.assertEqual(persisted["chat"]["cwd"], str(self.worktree.resolve()))
        self.assertEqual(persisted["chat"]["base_oid"], self.base)

    def test_existing_ready_create_performs_remote_readback(self):
        self.chat.create("wu-1", "initial", self.backend)
        self.backend.threads[0]["cwd"] = str(self.root / "different")
        result = self.chat.create("wu-1", "initial", self.backend)
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(self.backend.create_calls, 1)

    def test_create_does_not_adopt_failed_remote_thread_as_ready(self):
        self.backend.create_thread = mock.Mock(return_value={
            "threadId": "failed-thread", "hostId": "host-1", "work_unit_id": "wu-1",
            "cwd": str(self.worktree), "base_oid": self.base, "status": "failed",
        })
        self.backend.threads.append(dict(self.backend.create_thread.return_value))
        result = self.chat.create("wu-1", "initial", self.backend)
        self.assertEqual(result["state"], "ambiguous")

    def test_lost_create_response_is_ambiguous_and_retry_never_blind_creates(self):
        self.backend.fail_create_once = True
        first = self.chat.create("wu-1", "latest requirements", self.backend)
        self.assertEqual(first["state"], "ambiguous")
        second = self.chat.create("wu-1", "latest requirements", self.backend)
        self.assertEqual(second["state"], "ambiguous")
        self.assertEqual(self.backend.create_calls, 1)
        self.backend.threads.append({
            "threadId": "thread-reconciled", "hostId": "host-1", "work_unit_id": "wu-1",
            "cwd": str(self.worktree.resolve()), "base_oid": self.base,
            "status": "ready", "archived": False,
        })
        third = self.chat.reconcile("wu-1", self.backend)
        self.assertEqual(third["state"], "ready")
        self.assertEqual(third["ready_thread_id"], "thread-reconciled")
        self.assertEqual(self.backend.create_calls, 1)

    def test_stale_remote_listing_keeps_ambiguous_and_later_readback_reconciles(self):
        self.backend.fail_create_once = True
        self.chat.create("wu-1", "prompt", self.backend)
        self.backend.threads.append({
            "threadId": "thread-later", "hostId": "host-1", "work_unit_id": "wu-1",
            "cwd": str(self.worktree.resolve()), "base_oid": self.base,
            "status": "ready", "archived": False,
        })
        self.backend.stale_listing_once = True
        self.assertEqual(self.chat.reconcile("wu-1", self.backend)["state"], "ambiguous")
        self.assertEqual(self.chat.reconcile("wu-1", self.backend)["state"], "ready")
        self.assertEqual(self.backend.create_calls, 1)

    def test_client_only_id_is_pending_and_never_sent_to_ready_api(self):
        self.backend.create_thread = mock.Mock(return_value={"clientThreadId": "client-1", "hostId": "host-1"})
        pending = self.chat.create("wu-1", "prompt", self.backend)
        self.assertEqual(pending["state"], "pending")
        with self.assertRaises(LifecycleError):
            self.chat.send("wu-1", "follow-up", self.backend)
        self.assertFalse(self.backend.send_calls)

    def test_unfinished_current_turn_forks_with_latest_scope_and_ready_readback(self):
        self.chat.create("wu-1", "initial", self.backend)
        self.backend.threads[0]["status"] = "running"
        result = self.chat.handoff("wu-1", "new acceptance requirement", self.backend)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["ready_thread_id"], "fork-1")
        self.assertIn("new acceptance requirement", self.backend.fork_calls[0][1])
        self.assertIn("task-1", self.backend.fork_calls[0][1])
        self.assertIn("org/repo#14", self.backend.fork_calls[0][1])

    def test_lost_handoff_response_reconciles_without_duplicate_fork(self):
        self.chat.create("wu-1", "initial", self.backend)
        self.backend.threads[0]["status"] = "running"
        original = self.backend.fork_thread
        calls = []
        def lost(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("lost response")
            return original(*args, **kwargs)
        self.backend.fork_thread = lost
        first = self.chat.handoff("wu-1", "latest", self.backend)
        self.assertEqual(first["chat"]["handoff"]["state"], "ambiguous")
        second = self.chat.handoff("wu-1", "latest", self.backend)
        self.assertEqual(second["chat"]["handoff"]["state"], "ambiguous")
        self.assertEqual(calls, [1])
        handoff = self.registry.get("wu-1")["chat"]["handoff"]
        self.backend.threads.append({
            "threadId": "fork-reconciled", "hostId": "host-1", "work_unit_id": "wu-1",
            "cwd": str(self.worktree.resolve()), "base_oid": self.base,
            "status": "ready", "archived": False,
            "prompt": handoff["marker"],
        })
        self.assertEqual(self.chat.reconcile_handoff("wu-1", self.backend)["ready_thread_id"], "fork-reconciled")

    def test_send_lost_response_records_marker_and_never_blindly_resends(self):
        self.chat.create("wu-1", "initial", self.backend)
        original = self.backend.send_message
        calls = []
        def lost(thread_id, prompt, *, host_id=None):
            calls.append(1)
            if len(calls) == 1:
                self.backend.threads[0]["prompt"] += "\n" + prompt
                raise OSError("lost response")
            return original(thread_id, prompt, host_id=host_id)
        self.backend.send_message = lost
        with self.assertRaises(LifecycleError):
            self.chat.send("wu-1", "follow-up", self.backend)
        state = self.registry.get("wu-1")
        self.assertEqual(state["chat"]["handoff"]["state"], "ambiguous")
        self.assertEqual(self.chat.send("wu-1", "follow-up", self.backend)["state"], "ready")
        self.assertEqual(calls, [1])

    def test_archive_requires_delivery_context_and_remote_identity_then_unarchive_restores(self):
        self.chat.create("wu-1", "initial", self.backend)
        with self.assertRaises(LifecycleError):
            self.chat.archive("wu-1", self.backend)
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                    context_saved=True, context_path=context)
        archived = self.chat.archive("wu-1", self.backend)
        self.assertEqual(archived["state"], "archived")
        self.assertEqual(self.backend.archive_calls, 1)
        restored = self.chat.unarchive("wu-1", self.backend)
        self.assertEqual(restored["state"], "ready")
        self.assertEqual(self.registry.get("wu-1")["chat"]["ready_thread_id"], "thread-1")

    def test_unarchive_lost_readback_is_durable_and_reconciles_without_duplicate(self):
        self.chat.create("wu-1", "initial", self.backend)
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                   context_saved=True, context_path=context)
        self.chat.archive("wu-1", self.backend)
        original = self.backend.read_thread
        calls = []
        def lost(thread_id, *, host_id=None):
            calls.append(len(calls) + 1)
            if len(calls) == 2:
                raise OSError("lost readback")
            return original(thread_id, host_id=host_id)
        self.backend.read_thread = lost
        with self.assertRaises(LifecycleError):
            self.chat.unarchive("wu-1", self.backend)
        self.assertEqual(self.registry.get("wu-1")["chat"]["unarchive_receipt"]["state"], "ambiguous")
        self.assertEqual(self.chat.unarchive("wu-1", self.backend)["state"], "ready")
        self.assertEqual(calls, [1, 2, 3])

    def test_archive_rejects_running_or_different_task_chat(self):
        self.chat.create("wu-1", "initial", self.backend)
        self.backend.threads[0]["status"] = "running"
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                    context_saved=True, context_path=context)
        with self.assertRaises(LifecycleError):
            self.chat.archive("wu-1", self.backend)
        self.backend.threads[0]["status"] = "ready"
        self.backend.threads[0]["task_ids"] = ["other-task"]
        with self.assertRaises(LifecycleError):
            self.chat.archive("wu-1", self.backend)

    def test_cleanup_is_staged_idempotent_and_preserves_dirty_or_active_worktree(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                    context_saved=True, context_path=context)
        git = FakeGit(self.repo, self.worktree)
        result = self.chat.cleanup("wu-1", git)
        self.assertEqual(result["state"], "branch_removed")
        self.assertTrue(git.removed); self.assertTrue(git.deleted)
        again = self.chat.cleanup("wu-1", git)
        self.assertEqual(again["state"], "branch_removed")

    def test_cleanup_rejects_local_only_commit_and_active_writer(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                    context_saved=True, context_path=context)
        git = FakeGit(self.repo, self.worktree); git.ahead = 1
        with self.assertRaises(CleanupError): self.chat.cleanup("wu-1", git)
        git.ahead = 0; git.active_writer = True
        with self.assertRaises(CleanupError): self.chat.cleanup("wu-1", git)
        self.assertFalse(git.removed)

    def test_cleanup_exclusive_lifecycle_lock_blocks_active_writer(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                    context_saved=True, context_path=context)
        git = FakeGit(self.repo, self.worktree)
        held = self.chat._open_worktree_lock(self.worktree)
        try:
            with self.assertRaises(CleanupError): self.chat.cleanup("wu-1", git)
        finally:
            import fcntl
            fcntl.flock(held.fileno(), fcntl.LOCK_UN); held.close()

    def test_cleanup_receipt_resumes_after_worktree_removal_before_branch_delete(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                    context_saved=True, context_path=context)
        git = FakeGit(self.repo, self.worktree)
        receipt = {"state": "worktree_removed", "unit_id": "wu-1",
                   "repository": str(self.repo.resolve()), "worktree": str(self.worktree.resolve()),
                   "branch": "feat/task", "head_oid": "b" * 40}
        self.chat._write_cleanup_receipt("wu-1", receipt)
        git.removed = True
        result = self.chat.cleanup("wu-1", git)
        self.assertEqual(result["state"], "branch_removed")
        self.assertTrue(git.deleted)

    def test_cleanup_recovers_when_branch_delete_succeeded_before_receipt_persist(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                    context_saved=True, context_path=context)
        git = FakeGit(self.repo, self.worktree)
        receipt = {"state": "worktree_removed", "unit_id": "wu-1",
                   "repository": str(self.repo.resolve()), "worktree": str(self.worktree.resolve()),
                   "branch": "feat/task", "head_oid": "b" * 40}
        self.chat._write_cleanup_receipt("wu-1", receipt)
        git.removed = True
        git.deleted = True
        result = self.chat.cleanup("wu-1", git)
        self.assertEqual(result["state"], "branch_removed")
        self.assertEqual(self.registry.get("wu-1")["cleanup"]["state"], "branch_removed")

    def test_cleanup_prepared_receipt_recovers_when_worktree_already_removed(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                   context_saved=True, context_path=context)
        git = FakeGit(self.repo, self.worktree)
        receipt = {"state": "prepared", "unit_id": "wu-1",
                   "repository": str(self.repo.resolve()), "worktree": str(self.worktree.resolve()),
                   "branch": "feat/task", "head_oid": "b" * 40}
        self.chat._write_cleanup_receipt("wu-1", receipt)
        git.removed = True
        result = self.chat.cleanup("wu-1", git)
        self.assertEqual(result["state"], "branch_removed")
        self.assertTrue(git.deleted)

    def test_cleanup_rejects_receipt_with_different_branch(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                   context_saved=True, context_path=context)
        git = FakeGit(self.repo, self.worktree)
        receipt = {"state": "worktree_removed", "unit_id": "wu-1",
                   "repository": str(self.repo.resolve()), "worktree": str(self.worktree.resolve()),
                   "branch": "other-branch", "head_oid": "b" * 40}
        self.chat._write_cleanup_receipt("wu-1", receipt)
        git.removed = True
        with self.assertRaises(CleanupError):
            self.chat.cleanup("wu-1", git)

    def test_cleanup_preserves_corrupt_receipt_without_deletion(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                   context_saved=True, context_path=context)
        path = self.chat._receipt_path("wu-1")
        path.write_text("{broken")
        git = FakeGit(self.repo, self.worktree)
        with self.assertRaises(CleanupError):
            self.chat.cleanup("wu-1", git)
        self.assertFalse(git.removed)

    def test_cleanup_preserves_prepared_state_when_receipt_is_missing(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                   context_saved=True, context_path=context)
        self.registry.update("wu-1", lambda row: row["cleanup"].update({"state": "prepared"}))
        git = FakeGit(self.repo, self.worktree)
        with self.assertRaises(CleanupError):
            self.chat.cleanup("wu-1", git)
        self.assertFalse(git.removed)

    def test_cleanup_rejects_branch_head_changed_after_removal_receipt(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                   context_saved=True, context_path=context)
        git = FakeGit(self.repo, self.worktree)
        receipt = {"state": "worktree_removed", "unit_id": "wu-1",
                   "repository": str(self.repo.resolve()), "worktree": str(self.worktree.resolve()),
                   "branch": "feat/task", "head_oid": "b" * 40}
        self.chat._write_cleanup_receipt("wu-1", receipt)
        git.removed = True; git.head_oid = "c" * 40
        with self.assertRaises(CleanupError):
            self.chat.cleanup("wu-1", git)
        self.assertFalse(git.deleted)

    def test_cleanup_without_writer_guard_never_assumes_inactive(self):
        chat = ChatLifecycle(self.registry)
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                   context_saved=True, context_path=context)
        with self.assertRaises(CleanupError):
            chat.cleanup("wu-1", FakeGit(self.repo, self.worktree))

    def test_cleanup_lock_can_share_explicit_service_lock_root_and_blocks_other_process(self):
        lock_root = self.root / "service-locks"
        chat = ChatLifecycle(self.registry, lock_root=lock_root,
                             writer_guard=lambda _worktree, _git: "inactive")
        held = chat._open_worktree_lock(self.worktree)
        try:
            lock_path = lock_root / "workspace" / (
                hashlib.sha256(str(self.worktree.resolve()).encode()).hexdigest() + ".lock"
            )
            self.assertTrue(lock_path.is_file())
            child = subprocess.run(
                [sys.executable, "-c", "import fcntl, os, sys; fd=os.open(sys.argv[1], os.O_RDWR); "
                 "\ntry: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                 "except OSError: raise SystemExit(1)\n"
                 "else: raise SystemExit(0)", str(lock_path)],
                check=False,
            )
            self.assertNotEqual(child.returncode, 0)
        finally:
            import fcntl
            fcntl.flock(held.fileno(), fcntl.LOCK_UN); held.close()
        child = subprocess.run(
            [sys.executable, "-c", "import fcntl, os, sys; fd=os.open(sys.argv[1], os.O_RDWR); "
             "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", str(lock_path)],
            check=False,
        )
        self.assertEqual(child.returncode, 0)

    def test_ready_thread_id_remains_ready_while_current_turn_runs(self):
        self.chat.create("wu-1", "initial", self.backend)
        self.backend.threads[0]["status"] = "running"
        result = self.chat.create("wu-1", "retry create", self.backend)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["ready_thread_id"], "thread-1")

    def test_cleanup_actual_isolated_git_fixture_requires_clean_merged_branch(self):
        remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True,
                       capture_output=True, text=True)
        canonical = self.root / "canonical"
        subprocess.run(["git", "init", "-b", "main", str(canonical)], check=True,
                       capture_output=True, text=True)
        def git(path, *args):
            return subprocess.run(["git", *args], cwd=path, check=True,
                                  capture_output=True, text=True).stdout.strip()
        git(canonical, "config", "user.email", "fixture@example.invalid")
        git(canonical, "config", "user.name", "Fixture")
        (canonical / "README").write_text("base\n")
        git(canonical, "add", "README"); git(canonical, "commit", "-m", "base")
        git(canonical, "remote", "add", "origin", str(remote))
        git(canonical, "push", "-u", "origin", "main")
        worktree = self.root / "fixture-worktree"
        git(canonical, "worktree", "add", "-b", "feat/fixture", str(worktree), "main")
        (worktree / "change").write_text("merged\n")
        git(worktree, "add", "change"); git(worktree, "commit", "-m", "change")
        git(canonical, "merge", "--no-ff", "feat/fixture", "-m", "merge")
        git(canonical, "push", "origin", "main")
        context = self.vault / "context.json"; context.write_text("saved context")
        unit = self.registry.register(
            "wu-fixture", purpose="fixture cleanup", repository="org/repo",
            project_id="p", host_id="h", base_oid=self.base,
            worktree=worktree, repository_path=canonical, branch="feat/fixture",
            context_path=context)
        self.registry.set_delivery("wu-fixture", merged=True, main_synced=True,
                                    context_saved=True, context_path=context)
        result = self.chat.cleanup("wu-fixture", GitWorktree(canonical))
        self.assertEqual(result["state"], "branch_removed")
        self.assertFalse(worktree.exists())
        self.assertEqual(git(canonical, "branch", "--list", "feat/fixture"), "")

    def test_cleanup_real_git_three_worktrees_preserves_unpushed_dirty_peer_and_resumes_missing_branch(self):
        remote = self.root / "three-worktree-remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True,
                       capture_output=True, text=True)
        canonical = self.root / "three-canonical"
        subprocess.run(["git", "init", "-b", "main", str(canonical)], check=True,
                       capture_output=True, text=True)

        def git(path, *args):
            return subprocess.run(["git", *args], cwd=path, check=True,
                                  capture_output=True, text=True).stdout.strip()

        git(canonical, "config", "user.email", "fixture@example.invalid")
        git(canonical, "config", "user.name", "Fixture")
        (canonical / "README").write_text("base\n")
        git(canonical, "add", "README"); git(canonical, "commit", "-m", "base")
        git(canonical, "remote", "add", "origin", str(remote)); git(canonical, "push", "-u", "origin", "main")
        target = self.root / "three-target"
        peer = self.root / "three-peer"
        git(canonical, "worktree", "add", "-b", "feat/kill-resume", str(target), "main")
        git(canonical, "worktree", "add", "-b", "feat/peer", str(peer), "main")
        git(target, "config", "user.email", "fixture@example.invalid")
        git(target, "config", "user.name", "Fixture")
        git(peer, "config", "user.email", "fixture@example.invalid")
        git(peer, "config", "user.name", "Fixture")
        (target / "merged").write_text("merged\n")
        git(target, "add", "merged"); git(target, "commit", "-m", "merged feature")
        target_head = git(target, "rev-parse", "HEAD")
        git(canonical, "merge", "--no-ff", "feat/kill-resume", "-m", "merge feature")
        git(canonical, "push", "origin", "main")
        (peer / "peer-local").write_text("unpublished\n")
        git(peer, "add", "peer-local"); git(peer, "commit", "-m", "unpublished peer")
        (peer / "peer-dirty").write_text("must remain\n")

        context = self.vault / "three-context.json"; context.write_text("saved context")
        unit = self.registry.register(
            "wu-three", purpose="three-worktree recovery", repository="org/repo",
            project_id="p", host_id="h", base_oid=self.base,
            worktree=target, repository_path=canonical, branch="feat/kill-resume",
            context_path=context)
        self.registry.set_delivery("wu-three", merged=True, main_synced=True,
                                   context_saved=True, context_path=context)
        git(canonical, "worktree", "remove", "--force", str(target))
        git(canonical, "branch", "-d", "feat/kill-resume")
        receipt = {"state": "worktree_removed", "unit_id": unit["id"],
                   "repository": str(canonical.resolve()), "worktree": str(target.resolve()),
                   "branch": "feat/kill-resume", "head_oid": target_head}
        lock_root = self.root / "three-service-locks"
        chat = ChatLifecycle(self.registry, lock_root=lock_root,
                             writer_guard=lambda _worktree, _git: "inactive")
        chat._write_cleanup_receipt("wu-three", receipt)
        self.assertFalse(target.exists())
        self.assertTrue(peer.exists())
        result = chat.cleanup("wu-three", GitWorktree(canonical))
        self.assertEqual(result["state"], "branch_removed")
        self.assertFalse(git(canonical, "branch", "--list", "feat/kill-resume"))
        self.assertIn("peer-dirty", git(peer, "status", "--porcelain"))
        self.assertNotEqual(git(peer, "rev-parse", "HEAD"), git(canonical, "rev-parse", "main"))

    def test_cleanup_rejects_other_worktree_use_and_dependent_registry_unit(self):
        context = self.vault / "context.json"; context.write_text("saved context")
        self.registry.set_delivery("wu-1", merged=True, main_synced=True,
                                    context_saved=True, context_path=context)
        git = FakeGit(self.repo, self.worktree)
        git.other_worktrees = [{"path": str(self.root / "other"), "branch": "feat/task"}]
        with self.assertRaises(CleanupError): self.chat.cleanup("wu-1", git)
        git.other_worktrees = []
        self.registry.register("wu-2", purpose="dependent", repository="org/repo",
                               project_id="p", host_id="h", base_oid=self.base,
                               worktree=self.worktree, repository_path=self.repo,
                               branch="feat/task")
        with self.assertRaises(CleanupError): self.chat.cleanup("wu-1", git)


if __name__ == "__main__":
    unittest.main()
