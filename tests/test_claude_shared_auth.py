import fcntl
import io
import json
import os
import pty
import signal
import stat
import sys
import tempfile
import termios
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import claude_shared_auth as auth

OLD = "sk-ant-oat01-" + "A1b2" * 20
NEW = "sk-ant-oat01-" + "Z9y8" * 20
MARK = "Long-lived authentication token created successfully!"
URL = "https://claude.ai/oauth/authorize?code=true&client_id=x&state=st"

FAKE = textwrap.dedent('''\
    #!%s
    import json, os, sys, time
    mode = os.environ.get("FAKE_MODE", "ok")
    tok = os.environ.get("FAKE_TOKEN", "")
    log = os.environ.get("FAKE_LOG")
    args = sys.argv[1:]
    def record(extra=None):
        if not log:
            return
        d = {"args": args, "env_ok": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == tok,
             "has_token": "CLAUDE_CODE_OAUTH_TOKEN" in os.environ,
             "disable": [os.environ.get("DISABLE_LOGIN_COMMAND"), os.environ.get("DISABLE_LOGOUT_COMMAND")]}
        d.update(extra or {})
        with open(log, "a") as f:
            f.write(json.dumps(d) + "\\n")
    def emit(s):
        for i in range(0, len(s), 7):
            sys.stdout.write(s[i:i + 7]); sys.stdout.flush(); time.sleep(0.002)
    if "-p" in args:
        record()
        v = os.environ.get("FAKE_VALIDATE")
        if v == "hang":
            time.sleep(60)
        body = {"type": "result", "subtype": "success", "is_error": False, "result": "OK"}
        if v == "fail":
            body.update(subtype="error_during_execution", is_error=True)
        if v == "empty":
            body = {}
        if v == "ambig":
            body = {"is_error": False, "result": "OK"}
        if v == "subtype":
            body["subtype"] = "error_max_turns"
        if v == "list":
            body = [body]
        sys.stdout.write(json.dumps(body))
        sys.exit(0 if os.environ.get("FAKE_VALIDATE") != "crash" else 3)
    if args[:1] == ["setup-token"]:
        record()
        if mode == "hang":
            time.sleep(60)
        if mode == "fail":
            emit("\\x1b[31merror\\x1b[0m\\r\\n"); sys.exit(1)
        if mode == "code":
            line = sys.stdin.readline()
            open(os.environ["FAKE_CODE_OUT"], "w").write(line)
        emit("\\x1b[2K\\x1b[1mOpen: " + os.environ["FAKE_URL"] + "\\x1b[0m\\r\\nhttps://evil.example/authorize\\r\\n")
        if mode == "fragment":
            emit("Long-lived authentication token created successfully!\\r\\nsk-ant-oat01-abc\\r\\n"); sys.exit(0)
        if mode == "two":
            emit("Long-lived authentication token created successfully!\\r\\n" + tok + "\\r\\nsk-ant-oat01-" + "Q" * 70 + "\\r\\n"); sys.exit(0)
        if mode == "eof":
            emit("Long-lived authentication token created successfully!\\r\\n" + tok); sys.exit(0)
        if mode == "oldmarker":
            emit("Long-lived token created successfully!\\r\\n" + tok + "\\r\\n"); sys.exit(0)
        if mode == "nomarker":
            emit(tok + "\\r\\n"); sys.exit(0)
        emit("\\x1b[32mLong-lived authentication token created successfully!\\x1b[0m\\r\\n\\x1b[1m" + tok + "\\x1b[0m\\r\\n")
        sys.exit(1 if mode == "exit1" else 0)
    record()
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
''' % sys.executable)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        git_roots = mock.patch.object(auth, "_git_setting_roots", return_value=[])
        git_roots.start()
        self.addCleanup(git_roots.stop)
        self.home = Path(self.tmp.name).resolve()
        (self.home / ".local/bin").mkdir(parents=True)
        self.native = self.home / ".local/bin/claude"
        self.native.write_text(FAKE)
        self.native.chmod(0o755)
        self.log = self.home / "fake.log"
        self.env = {"PATH": os.environ.get("PATH", ""), "FAKE_TOKEN": NEW, "FAKE_URL": URL,
                    "FAKE_LOG": str(self.log)}
        self.out, self.err = io.StringIO(), io.StringIO()
        self.exec_calls = []

    def run_main(self, argv, env=None, **kw):
        e = dict(self.env)
        e.update(env or {})
        kw.setdefault("timeout", 20)
        return auth.main(argv, environ=e, home=self.home, out=self.out, err=self.err,
                         execve=lambda p, a, en: self.exec_calls.append((p, a, en)), **kw)

    def seed(self, token=OLD):
        auth.save_credential(self.home, token)
        auth.write_marker(self.home)

    def cred_bytes(self):
        return auth.credential_path(self.home).read_bytes()

    def logs(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text().splitlines()]

    def assert_no_leak(self, *tokens):
        shown = self.out.getvalue() + self.err.getvalue()
        for t in tokens:
            self.assertNotIn(t, shown)
            for p in self.home.rglob("*"):
                if p.is_file() and p != auth.credential_path(self.home) and p != self.native:
                    self.assertNotIn(t.encode(), p.read_bytes(), p)


class StoreTests(Base):
    def test_save_and_load_format_and_modes(self):
        self.seed()
        d = auth.credential_path(self.home)
        self.assertEqual(stat.S_IMODE(d.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o600)
        data = json.loads(d.read_text())
        self.assertEqual(set(data), {"token", "created_at", "source"})
        self.assertEqual(data["source"], "claude setup-token")
        self.assertEqual(auth.load_token(self.home), OLD)

    def test_rejects_unsafe_store(self):
        self.seed()
        p = auth.credential_path(self.home)
        os.chmod(p, 0o644)
        with self.assertRaises(auth.AuthError):
            auth.load_token(self.home)
        os.chmod(p, 0o600)
        os.chmod(p.parent, 0o755)
        with self.assertRaises(auth.AuthError):
            auth.load_token(self.home)
        os.chmod(p.parent, 0o700)
        with mock.patch.object(auth, "_euid", return_value=os.geteuid() + 1):
            with self.assertRaises(auth.AuthError):
                auth.load_token(self.home)
        self.assertEqual(auth.load_token(self.home), OLD)

    def test_rejects_symlink_and_fifo(self):
        self.seed()
        p = auth.credential_path(self.home)
        real = p.parent / "real.json"
        os.rename(p, real)
        os.symlink(real, p)
        with self.assertRaises(auth.AuthError):
            auth.load_token(self.home)
        os.unlink(p)
        os.mkfifo(p, 0o600)
        with self.assertRaises(auth.AuthError):
            auth.load_token(self.home)

    def test_rejects_bad_content(self):
        self.seed()
        p = auth.credential_path(self.home)
        good = json.loads(p.read_text())
        for bad in ({**good, "token": "sk-ant-oat01-short"}, {**good, "source": "x"},
                    {**good, "created_at": "yesterday"}, {**good, "extra": 1}):
            p.write_text(json.dumps(bad))
            with self.assertRaises(auth.AuthError):
                auth.load_token(self.home)

    def test_reader_never_sees_intermediate_state(self):
        self.seed()
        stop, seen, errors = threading.Event(), set(), []

        def reader():
            while not stop.is_set():
                try:
                    seen.add(auth.load_token(self.home))
                except Exception as e:  # noqa
                    errors.append(e)
        t = threading.Thread(target=reader)
        t.start()
        for i in range(150):
            auth.save_credential(self.home, NEW if i % 2 else OLD)
        stop.set()
        t.join()
        self.assertEqual(errors, [])
        self.assertTrue(seen <= {OLD, NEW})


class MarkerTests(Base):
    def marker(self):
        return auth.credential_path(self.home).parent / "enabled"

    def test_launch_requires_marker_fail_closed(self):
        auth.save_credential(self.home, OLD)
        self.assertEqual(self.run_main(["x"]), 1)
        self.assertEqual(self.exec_calls, [])
        auth.write_marker(self.home)
        self.assertEqual(self.run_main(["x"]), 0)

    def test_marker_mode_and_unsafe_rejected(self):
        self.seed()
        m = self.marker()
        self.assertEqual(stat.S_IMODE(m.stat().st_mode), 0o600)
        os.chmod(m, 0o644)
        self.assertEqual(self.run_main(["x"]), 1)
        os.chmod(m, 0o600)
        os.unlink(m)
        other = m.parent / "other"
        other.write_text("")
        os.chmod(other, 0o600)
        os.symlink(other, m)
        self.assertEqual(self.run_main(["x"]), 1)
        os.unlink(m)
        os.mkfifo(m, 0o600)
        self.assertEqual(self.run_main(["x"]), 1)
        os.unlink(m)
        os.mkdir(m, 0o700)
        self.assertEqual(self.run_main(["x"]), 1)
        os.rmdir(m)
        auth.write_marker(self.home)
        with mock.patch.object(auth, "_euid", return_value=os.geteuid() + 1):
            self.assertEqual(self.run_main(["x"]), 1)
        self.assertEqual(self.exec_calls, [])

    def test_write_marker_refuses_unsafe_existing(self):
        self.seed()
        m = self.marker()
        os.unlink(m)
        os.symlink(m.parent / "credential.json", m)
        with self.assertRaises(auth.AuthError):
            auth.write_marker(self.home)

    def test_login_creates_marker_after_save_and_logout_keeps_it(self):
        self.assertEqual(self.run_main(["setup-token"]), 0, self.err.getvalue())
        self.assertTrue(self.marker().exists())
        self.assertEqual(stat.S_IMODE(self.marker().stat().st_mode), 0o600)
        self.assertEqual(self.run_main(["auth", "logout"]), 0)
        self.assertTrue(self.marker().exists())
        self.assertEqual(self.run_main(["x"]), 1)

    def test_failed_login_does_not_create_marker(self):
        self.assertNotEqual(self.run_main(["setup-token"], env={"FAKE_MODE": "fail"}), 0)
        self.assertFalse(self.marker().exists())
        self.assertNotEqual(self.run_main(["setup-token"], env={"FAKE_VALIDATE": "empty"}), 0)
        self.assertFalse(self.marker().exists())


class SaveRecheckTests(Base):
    def test_save_rejects_unsafe_existing_target(self):
        self.seed()
        p = auth.credential_path(self.home)
        before = p.read_bytes()
        os.chmod(p, 0o644)
        with self.assertRaises(auth.AuthError):
            auth.save_credential(self.home, NEW)
        os.chmod(p, 0o600)
        with mock.patch.object(auth, "_euid", return_value=os.geteuid() + 1):
            with self.assertRaises(auth.AuthError):
                auth.save_credential(self.home, NEW)
        self.assertEqual(p.read_bytes(), before)
        real = p.parent / "real.json"
        os.rename(p, real)
        os.symlink(real, p)
        with self.assertRaises(auth.AuthError):
            auth.save_credential(self.home, NEW)
        self.assertTrue(p.is_symlink())
        os.unlink(p)
        os.mkdir(p, 0o700)
        with self.assertRaises(auth.AuthError):
            auth.save_credential(self.home, NEW)
        self.assertEqual([x.name for x in p.parent.iterdir() if x.name.endswith(".tmp")], [])


class LaunchTests(Base):
    def test_help_only_bypasses_when_sole_info_invocation(self):
        for args in (["-p", "--help"], ["x", "-h"], ["--version", "x"], ["--help", "--help"]):
            self.assertEqual(self.run_main(args), 1, args)
        self.assertEqual(self.exec_calls, [])
        for args in (["--version"], ["-v"], ["--help"], ["-h"]):
            self.assertEqual(self.run_main(args), 0, args)

    def test_fail_closed_without_credential(self):
        rc = self.run_main(["-p", "hi"])
        self.assertEqual(rc, 1)
        self.assertIn("claude auth login", self.err.getvalue())
        self.assertEqual(self.exec_calls, [])

    def test_exec_sets_child_env_only(self):
        self.seed()
        env = {"CLAUDE_CODE_OAUTH_TOKEN": OLD, "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR": "9",
               "CLAUDE_CODE_OAUTH_REFRESH_TOKEN": "r", "CLAUDE_CODE_OAUTH_SCOPES": "s"}
        rc = self.run_main(["-p", "hi"], env=env)
        self.assertEqual(rc, 0)
        path, args, child = self.exec_calls[0]
        self.assertEqual(path, str(self.native))
        self.assertEqual(args, [str(self.native), "-p", "hi"])
        self.assertEqual(child["CLAUDE_CODE_OAUTH_TOKEN"], OLD)
        self.assertEqual(child["DISABLE_LOGIN_COMMAND"], "1")
        self.assertEqual(child["DISABLE_LOGOUT_COMMAND"], "1")
        for k in ("CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
                  "CLAUDE_CODE_OAUTH_SCOPES"):
            self.assertNotIn(k, child)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", os.environ)

    def test_new_launch_reads_rotated_token(self):
        self.seed()
        self.run_main(["x"])
        self.seed(NEW)
        self.run_main(["x"])
        self.assertEqual([c[2]["CLAUDE_CODE_OAUTH_TOKEN"] for c in self.exec_calls], [OLD, NEW])

    def test_conflicting_env_rejected_without_value(self):
        self.seed()
        for k, v in (("ANTHROPIC_API_KEY", "sekret-1"), ("ANTHROPIC_AUTH_TOKEN", "sekret-2"),
                     ("ANTHROPIC_BASE_URL", "https://sekret-3.example"),
                     ("CLAUDE_CODE_USE_BEDROCK", "1"), ("CLAUDE_CODE_USE_VERTEX", "true")):
            self.err.truncate(0)
            self.err.seek(0)
            self.assertEqual(self.run_main(["x"], env={k: v}), 1, k)
            self.assertNotIn(v, self.err.getvalue())
            self.assertIn(k, self.err.getvalue())
        self.assertEqual(self.exec_calls, [])

    def test_info_commands_work_without_credential_or_token(self):
        for flag in ("--version", "--help"):
            self.assertEqual(self.run_main([flag], env={"CLAUDE_CODE_OAUTH_TOKEN": OLD}), 0)
        for _, _, child in self.exec_calls:
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", child)

    def test_refuses_native_that_is_bridge(self):
        self.seed()
        self.native.unlink()
        os.symlink(auth.__file__, self.native)
        self.assertEqual(self.run_main(["x"]), 1)
        self.native.unlink()
        self.native.write_text("#!/bin/sh\nexec python3 /x/claude_shared_auth.py \"$@\"\n")
        self.native.chmod(0o755)
        self.assertEqual(self.run_main(["x"]), 1)
        self.assertEqual(self.exec_calls, [])

    def test_missing_native(self):
        self.seed()
        self.native.unlink()
        self.assertEqual(self.run_main(["x"]), 1)

    def test_real_exec_preserves_exit_status(self):
        self.seed()
        import subprocess
        drv = ("import sys;sys.path.insert(0,%r);import claude_shared_auth as a;"
               "from pathlib import Path;sys.exit(a.main(['x'],home=Path(%r)))"
               % (str(Path(auth.__file__).parent), str(self.home)))
        env = dict(self.env, FAKE_EXIT="7")
        r = subprocess.run([sys.executable, "-I", "-c", drv], env=env)
        self.assertEqual(r.returncode, 7)
        self.assertTrue(self.logs()[-1]["env_ok"] is False and self.logs()[-1]["has_token"])


class ScannerTests(unittest.TestCase):
    def test_oauth_url_path_and_token_restrictions(self):
        def urls(*us):
            sc = auth.LoginScanner()
            got = sc.feed(("\n".join(us) + "\n").encode())
            return got + sc.finish_urls()
        good = "https://claude.ai/oauth/authorize?code=true&client_id=x"
        self.assertEqual(urls(good), [good])
        self.assertEqual(urls("https://claude.ai/"), [])
        self.assertEqual(urls("https://claude.ai/login"), [])
        self.assertEqual(urls("https://claude.ai/oauth/authorize/../x"), [])
        self.assertEqual(urls("https://claude.ai/foo/oauth/authorize"), [])
        self.assertEqual(urls("https://platform.claude.com/other/authorize"), [])
        self.assertEqual(urls(good + "&access_token=abc"), [])
        self.assertEqual(urls(good + "&state=" + NEW), [])
        self.assertEqual(urls(good + "#" + NEW), [])

    def test_chunked_ansi_and_url_filtering(self):
        text = ("\x1b[2K\x1b[1mgo: " + URL + "\x1b[0m\r\nhttps://claude.ai.evil.com/x\r\n"
                "https://platform.claude.com/oauth/authorize?a=b\r\nhttps://evil.example/authorize\r\n"
                "http://claude.ai/x\r\nhttps://platform.claude.com/other\r\n")
        sc = auth.LoginScanner()
        urls = []
        raw = text.encode()
        for i in range(0, len(raw), 3):
            urls += sc.feed(raw[i:i + 3])
        urls += sc.finish_urls()
        self.assertEqual(urls, [URL, "https://platform.claude.com/oauth/authorize?a=b"])

    def test_token_extraction(self):
        def scan(s):
            sc = auth.LoginScanner()
            for i in range(0, len(s), 5):
                sc.feed(s[i:i + 5].encode())
            return sc.result()
        ok = scan("\x1b[1m"+MARK+"\x1b[0m\r\n\x1b[1m%s\x1b[0m\r\n" % NEW)
        self.assertEqual(ok, NEW)
        self.assertIsNone(scan(MARK + " sk-ant-oat01-abc\r\n"))
        self.assertIsNone(scan(NEW + "\r\n"))
        self.assertIsNone(scan("Long-lived token created successfully!\r\n" + NEW + "\r\n"))
        self.assertIsNone(scan("created successfully\r\n" + NEW + "\r\n"))
        self.assertIsNone(scan(MARK + "\r\n" + NEW))  # 終了区切りなし EOF
        self.assertEqual(scan(MARK + "\r\n" + NEW + "\r\n"), NEW)
        self.assertIsNone(scan(MARK + " %s %s\r\n" % (NEW, OLD)))
        self.assertIsNone(scan(MARK + " " + NEW[:-1].replace("-", "-", 1)[:40]))


class LoginTests(Base):
    login = ["auth", "login", "--claudeai"]

    def test_success_stores_token_and_shows_only_safe_output(self):
        self.seed()
        rc = self.run_main(self.login, env={"FAKE_MODE": "ok"})
        self.assertEqual(rc, 0, self.err.getvalue())
        self.assertEqual(auth.load_token(self.home), NEW)
        self.assertIn(URL, self.out.getvalue())
        self.assertNotIn("evil.example", self.out.getvalue() + self.err.getvalue())
        self.assertNotIn("\x1b", self.out.getvalue())
        self.assert_no_leak(NEW, OLD)
        validate = [l for l in self.logs() if "-p" in l["args"]][0]
        self.assertTrue(validate["env_ok"])
        for flag in ("--model", "sonnet", "--effort", "low", "--safe-mode", "--tools", "",
                     "--no-session-persistence", "--output-format", "json", "--max-turns", "1"):
            self.assertIn(flag, validate["args"])
        self.assertEqual(self.exec_calls, [])
        self.assertEqual([p.name for p in auth.credential_path(self.home).parent.iterdir()
                          if p.name not in ("credential.json", "credential.lock", "enabled")], [])

    def test_setup_token_alias_and_fresh_install(self):
        self.assertEqual(self.run_main(["setup-token"]), 0, self.err.getvalue())
        self.assertEqual(auth.load_token(self.home), NEW)

    def test_failures_keep_old_credential(self):
        self.seed()
        before = self.cred_bytes()
        cases = [({"FAKE_MODE": "fail"}, 20), ({"FAKE_MODE": "exit1"}, 20),
                 ({"FAKE_MODE": "fragment"}, 20), ({"FAKE_MODE": "two"}, 20),
                 ({"FAKE_MODE": "nomarker"}, 20), ({"FAKE_MODE": "eof"}, 20),
                 ({"FAKE_MODE": "oldmarker"}, 20),
                 ({"FAKE_MODE": "ok", "FAKE_VALIDATE": "empty"}, 20),
                 ({"FAKE_MODE": "ok", "FAKE_VALIDATE": "ambig"}, 20),
                 ({"FAKE_MODE": "ok", "FAKE_VALIDATE": "subtype"}, 20),
                 ({"FAKE_MODE": "ok", "FAKE_VALIDATE": "list"}, 20), ({"FAKE_MODE": "hang"}, 1),
                 ({"FAKE_MODE": "ok", "FAKE_VALIDATE": "fail"}, 20),
                 ({"FAKE_MODE": "ok", "FAKE_VALIDATE": "crash"}, 20)]
        for env, timeout in cases:
            self.out.truncate(0), self.err.truncate(0)
            rc = self.run_main(self.login, env=env, timeout=timeout)
            self.assertNotEqual(rc, 0, env)
            self.assertEqual(self.cred_bytes(), before, env)
            self.assert_no_leak(NEW)
            self.assertNotIn("Traceback", self.err.getvalue())

    def test_conflicting_env_blocks_login(self):
        self.assertEqual(self.run_main(self.login, env={"ANTHROPIC_API_KEY": "sekret"}), 1)
        self.assertNotIn("sekret", self.err.getvalue())
        self.assertEqual(self.logs(), [])

    def test_unknown_login_option_rejected(self):
        self.assertEqual(self.run_main(["auth", "login", "--email", "a@b"]), 1)
        self.assertEqual(self.logs(), [])

    def test_concurrent_login_rejected(self):
        self.seed()
        before = self.cred_bytes()
        with auth.login_lock(self.home):
            self.assertEqual(self.run_main(self.login), 1)
        self.assertEqual(self.cred_bytes(), before)
        self.assertEqual(self.logs(), [])

    def test_ctrl_c_terminates_child(self):
        self.seed()
        before = self.cred_bytes()
        with mock.patch.object(auth.select, "select", side_effect=KeyboardInterrupt):
            rc = self.run_main(self.login, env={"FAKE_MODE": "hang"})
        self.assertEqual(rc, 130)
        self.assertEqual(self.cred_bytes(), before)

    def _spy_kill(self):
        procs = []
        real = auth._kill_group

        def spy(proc):
            procs.append(proc)
            real(proc)
        return procs, mock.patch.object(auth, "_kill_group", spy)

    def _assert_group_gone(self, procs):
        self.assertTrue(procs)
        for p in procs:
            self.assertIsNotNone(p.poll())
            with self.assertRaises(ProcessLookupError):
                os.killpg(p.pid, 0)

    def test_sigterm_sighup_cleanup_child_and_restore_handlers(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            self.seed()
            before = self.cred_bytes()
            old = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
            self.out.truncate(0), self.err.truncate(0)
            procs, spy = self._spy_kill()
            real_select = auth.select.select

            def fire(*a, **k):
                os.kill(os.getpid(), sig)
                return real_select(*a, **k)
            with spy, mock.patch.object(auth.select, "select", side_effect=fire):
                rc = self.run_main(self.login, env={"FAKE_MODE": "hang"})
            self.assertEqual(rc, 128 + sig)
            self.assertIn("中断", self.err.getvalue())
            self.assertNotIn("Traceback", self.err.getvalue())
            self.assertEqual(self.cred_bytes(), before)
            self._assert_group_gone(procs)
            self.assertEqual(old, (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)))

    def test_validate_ctrl_c_cleans_child(self):
        self.seed()
        before = self.cred_bytes()
        procs, spy = self._spy_kill()
        with spy, mock.patch.object(auth.subprocess.Popen, "communicate", side_effect=KeyboardInterrupt):
            rc = self.run_main(self.login)
        self.assertEqual(rc, 130)
        self.assertNotIn("Traceback", self.err.getvalue())
        self.assertEqual(self.cred_bytes(), before)
        self._assert_group_gone(procs)

    def test_validate_timeout_kills_group_keeps_credential(self):
        self.seed()
        before = self.cred_bytes()
        procs, spy = self._spy_kill()
        with spy, mock.patch.object(auth, "VALIDATE_TIMEOUT", 1):
            rc = self.run_main(self.login, env={"FAKE_VALIDATE": "hang"})
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.cred_bytes(), before)
        self.assertEqual(auth.load_token(self.home), OLD)
        self.assertEqual(len(procs), 2)  # setup-token と検証の両 group
        self._assert_group_gone(procs)

    def test_tty_code_forwarded_and_echo_restored(self):
        m, s = pty.openpty()
        self.addCleanup(os.close, m)
        self.addCleanup(os.close, s)
        before = termios.tcgetattr(s)[3]
        codeout = self.home / "code.txt"
        os.write(m, b"FAKECODE123\n")
        rc = self.run_main(self.login, env={"FAKE_MODE": "code", "FAKE_CODE_OUT": str(codeout)},
                           stdin_fd=s)
        self.assertEqual(rc, 0, self.err.getvalue())
        self.assertEqual(codeout.read_text().strip(), "FAKECODE123")
        self.assertEqual(termios.tcgetattr(s)[3], before)
        self.assertNotIn("FAKECODE123", self.out.getvalue() + self.err.getvalue())


class LogoutTests(Base):
    def test_auth_mutations_and_bare_cannot_bypass_shared_entry(self):
        self.seed()
        before = self.cred_bytes()
        for args in (["auth", "logout", "--help"], ["--verbose", "auth", "login"],
                     ["--verbose", "auth", "logout"], ["--verbose", "setup-token"],
                     ["--bare", "-p", "Hello"], ["--bare=true", "-p", "Hello"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_main(args), 1)
                self.assertEqual(self.exec_calls, [])
                self.assertEqual(self.cred_bytes(), before)

    def test_logout_removes_only_shared_file_and_states_local(self):
        self.seed()
        other = self.home / ".claude/.credentials.json"
        other.parent.mkdir()
        other.write_text("native")
        rc = self.run_main(["auth", "logout"])
        self.assertEqual(rc, 0)
        self.assertFalse(auth.credential_path(self.home).exists())
        self.assertEqual(other.read_text(), "native")
        self.assertIn("ローカル", self.out.getvalue())
        self.assertIn("失効", self.out.getvalue())
        self.assertEqual(self.logs(), [])

    def test_logout_without_credential(self):
        self.assertEqual(self.run_main(["auth", "logout"]), 0)

    def test_logout_refuses_symlink(self):
        self.seed()
        p = auth.credential_path(self.home)
        real = p.parent / "r.json"
        os.rename(p, real)
        os.symlink(real, p)
        self.assertEqual(self.run_main(["auth", "logout"]), 1)
        self.assertTrue(real.exists())


if __name__ == "__main__":
    unittest.main()
