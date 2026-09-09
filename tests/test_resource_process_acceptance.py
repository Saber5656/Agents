"""Actual OS-process acceptance for workspace and shared-resource ownership."""
import json
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from harness.service import WorkspaceLock


WORKER = r'''
import json, socket, sqlite3, sys
from pathlib import Path
from harness.service import WorkspaceLock
workspace, locks, resource, marker = sys.argv[1:]
lock = WorkspaceLock(Path(workspace), Path(locks), resource)
if not lock.acquire(blocking=False):
    print(json.dumps({"state": "queued"}), flush=True)
    sys.exit(0)
with socket.socket() as server:
    server.bind(("127.0.0.1", 0))
    server.listen()
    with sqlite3.connect(str(Path(workspace) / "worker.sqlite3")) as db:
        db.execute("CREATE TABLE observations (value TEXT)")
        db.execute("INSERT INTO observations VALUES (?)", (marker,))
        db.commit()
        print(json.dumps({"state": "running", "port": server.getsockname()[1]}), flush=True)
        sys.stdin.readline()
        db.execute("INSERT INTO observations VALUES (?)", (marker + "-finished",))
        db.commit()
lock.release()
'''


class ResourceProcessAcceptanceTests(unittest.TestCase):
    def test_independent_processes_keep_ports_databases_and_cancellation_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locks = root / "locks"
            left, right = root / "left", root / "right"
            left.mkdir(); right.mkdir()
            processes = []

            def start(workspace, resource, marker):
                process = subprocess.Popen(
                    [sys.executable, "-c", WORKER, str(workspace), str(locks), resource, marker],
                    cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                processes.append(process)
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(10), "worker did not report readiness")
                return process, json.loads(process.stdout.readline())

            try:
                first, first_state = start(left, "ref:left", "left")
                second, second_state = start(right, "ref:right", "right")
                self.assertEqual(first_state["state"], "running")
                self.assertEqual(second_state["state"], "running")
                self.assertNotEqual(first_state["port"], second_state["port"])
                self.assertIsNone(first.poll()); self.assertIsNone(second.poll())
                same_workspace, queued = start(left, "different-resource", "forbidden")
                self.assertEqual(queued, {"state": "queued"})
                self.assertEqual(same_workspace.wait(timeout=10), 0)
                other = root / "other"; other.mkdir()
                same_ref, queued = start(other, "ref:left", "forbidden")
                self.assertEqual(queued, {"state": "queued"})
                self.assertEqual(same_ref.wait(timeout=10), 0)
                first.kill(); first.wait(timeout=10)
                self.assertIsNone(second.poll(), "terminating one owner affected its peer")
                reclaimed = WorkspaceLock(left, locks, "ref:left")
                self.assertTrue(reclaimed.acquire(blocking=False))
                reclaimed.release()
                peer_lock = WorkspaceLock(right, locks, "ref:right")
                self.assertFalse(peer_lock.acquire(blocking=False))
                second.communicate("finish\n", timeout=10)
                self.assertEqual(second.returncode, 0)
                for workspace, expected in ((left, ["left"]), (right, ["right", "right-finished"])):
                    with sqlite3.connect(str(workspace / "worker.sqlite3")) as db:
                        self.assertEqual([row[0] for row in db.execute("SELECT value FROM observations")], expected)
                self.assertTrue(peer_lock.acquire(blocking=False))
                peer_lock.release()
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=10)
