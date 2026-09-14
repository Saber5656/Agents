import errno
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import daily_it_news_runtime as runtime


class RunCommandTests(unittest.TestCase):
    def test_success_returns_completed_process_with_captured_output(self):
        result = runtime.run_command([sys.executable, "-c", "print('hi')"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "hi")

    def test_passes_input_and_env_and_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = runtime.run_command(
                [sys.executable, "-c", "import sys, os; sys.stdout.write(sys.stdin.read() + os.getcwd())"],
                input="echoed-", text=True, capture_output=True, cwd=tmp, env={"PATH": "/usr/bin"})
            self.assertEqual(result.stdout, f"echoed-{Path(tmp).resolve()}")

    def test_timeout_kills_process_group_so_grandchild_stops_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            script = (
                "import subprocess, sys, time;"
                f"subprocess.Popen([sys.executable, '-c', "
                f"'import time\\nf=open(\"{marker}\",\"a\")\\n'"
                f"'\\nwhile True:\\n f.write(\"x\")\\n f.flush()\\n time.sleep(0.05)']);"
                "time.sleep(30)"
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                runtime.run_command([sys.executable, "-c", script], text=True, capture_output=True, timeout=0.5)
            size_after_kill = marker.stat().st_size if marker.exists() else 0
            time.sleep(1.0)
            size_later = marker.stat().st_size if marker.exists() else 0
            self.assertEqual(size_after_kill, size_later)

    def test_timeout_preserves_partial_stdout_and_stderr(self):
        script = (
            "import sys, time;"
            "sys.stdout.write('partial-out'); sys.stdout.flush();"
            "sys.stderr.write('partial-err'); sys.stderr.flush();"
            "time.sleep(30)"
        )
        try:
            runtime.run_command([sys.executable, "-c", script], text=True, capture_output=True, timeout=0.5)
            self.fail("expected TimeoutExpired")
        except subprocess.TimeoutExpired as exc:
            self.assertIn("partial-out", exc.output or "")
            self.assertIn("partial-err", exc.stderr or "")

    def test_timeout_kills_term_ignoring_child_after_parent_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp)/'late-write'
            pidfile = Path(tmp)/'pid'
            child = ("import signal,time;from pathlib import Path;"
                     "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                     f"time.sleep(1);Path({str(marker)!r}).write_text('orphan')")
            parent = ("import subprocess,sys,time;from pathlib import Path;"
                      f"p=subprocess.Popen([sys.executable,'-c',{child!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                      f"Path({str(pidfile)!r}).write_text(str(p.pid));time.sleep(30)")
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    runtime.run_command([sys.executable,'-c',parent],capture_output=True,text=True,timeout=.3)
                time.sleep(1)
                self.assertFalse(marker.exists(), 'grandchild survived after its parent exited')
            finally:
                if pidfile.is_file():
                    try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
                    except ProcessLookupError:pass


class ReadVerifiedTests(unittest.TestCase):
    def test_reads_matching_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.txt"
            path.write_bytes(b"hello world")
            expected = runtime.hashlib.sha256(b"hello world").hexdigest()
            self.assertEqual(runtime.read_verified(path, expected), b"hello world")
            self.assertEqual(runtime.read_verified(path), b"hello world")

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.txt"
            real.write_bytes(b"data")
            link = Path(tmp) / "link.txt"
            link.symlink_to(real)
            with self.assertRaises(OSError):
                runtime.read_verified(link, retries=1, _sleep=lambda s: None)

    def test_retries_transient_errno_then_succeeds(self):
        calls = []

        def flaky(path):
            calls.append(path)
            if len(calls) < 3:
                raise OSError(errno.EAGAIN, "again")
            return b"final-data"

        result = runtime.read_verified("ignored", _reader=flaky, _sleep=lambda s: None)
        self.assertEqual(result, b"final-data")
        self.assertEqual(len(calls), 3)

    def test_permanent_permission_error_fails_immediately(self):
        calls = []

        def denied(path):
            calls.append(path)
            raise PermissionError(errno.EACCES, "denied")

        with self.assertRaises(PermissionError):
            runtime.read_verified("ignored", _reader=denied, _sleep=lambda s: self.fail("should not sleep"))
        self.assertEqual(len(calls), 1)

    def test_sha_mismatch_retries_then_raises(self):
        def wrong(path):
            return b"unexpected"

        with self.assertRaises(RuntimeError):
            runtime.read_verified("ignored", "0" * 64, _reader=wrong, _sleep=lambda s: None)

    def test_empty_read_retries_then_raises(self):
        def empty(path):
            return b""

        with self.assertRaises(RuntimeError):
            runtime.read_verified("ignored", _reader=empty, _sleep=lambda s: None, retries=2)


if __name__ == "__main__":
    unittest.main()
