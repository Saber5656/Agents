#!/usr/bin/env python3
"""Claude Code 共有認証ブリッジ。

単一正本 ~/.config/agents/claude-auth/credential.json の setup-token を、
native claude (~/.local/bin/claude) の子プロセス環境だけへ渡す。
token・Keychain・生出力は表示も永続化もしない。
"""
import contextlib
import datetime
import fcntl
import json
import os
import pty
import re
import select
import signal
import stat
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

SOURCE = "claude setup-token"
TOKEN_RE = re.compile(r"sk-ant-oat\d{2}-[A-Za-z0-9_-]*")
MIN_TOKEN_LEN = 64
SUCCESS_MARKER = "Long-lived authentication token created successfully!"
MARKER_NAME = "enabled"
DEFAULT_LOGIN_TIMEOUT = 15 * 60
VALIDATE_TIMEOUT = 120
MAX_SCAN_BYTES = 1 << 20
INFO_INVOCATIONS = (["--version"], ["-v"], ["--help"], ["-h"])
CLEAR_ENV = (
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR", "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR",
    "DISABLE_LOGIN_COMMAND", "DISABLE_LOGOUT_COMMAND",
)
CONFLICT_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL", "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_FOUNDRY_RESOURCE", "AWS_BEARER_TOKEN_BEDROCK",
    "ANTHROPIC_PROFILE",
)
CONFLICT_FLAGS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
LOGIN_HINT = "claude auth login を実行して共有認証情報を作成してください。"


class AuthError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def _euid():
    return os.geteuid()


# ---- 正本ストア -------------------------------------------------------------

def store_dir(home):
    return Path(home) / ".config" / "agents" / "claude-auth"


def credential_path(home):
    return store_dir(home) / "credential.json"


def _check_dir(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise AuthError("共有認証情報がありません。" + LOGIN_HINT)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != _euid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise AuthError("共有認証ディレクトリが安全ではありません(owner/0700/非symlinkが必要)。")


def _check_file_stat(st):
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != _euid()
            or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink > 1):
        raise AuthError("共有認証ファイルが安全ではありません(owner/0600/通常ファイルが必要)。")


def _ensure_dir(home):
    d = store_dir(home)
    os.makedirs(d.parent, exist_ok=True)
    try:
        os.mkdir(d, 0o700)
        os.chmod(d, 0o700)
    except FileExistsError:
        pass
    _check_dir(d)
    return d


def _valid_token(token):
    return (isinstance(token, str) and len(token) >= MIN_TOKEN_LEN
            and TOKEN_RE.fullmatch(token) is not None)


def load_token(home):
    _check_dir(store_dir(home))
    path = credential_path(home)
    try:
        _check_file_stat(os.lstat(path))
    except FileNotFoundError:
        raise AuthError("共有認証情報がありません。" + LOGIN_HINT)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise AuthError("共有認証ファイルを開けません。")
    try:
        _check_file_stat(os.fstat(fd))
        raw = os.read(fd, 65536)
    finally:
        os.close(fd)
    try:
        data = json.loads(raw)
        created = datetime.datetime.fromisoformat(data["created_at"])
        ok = (set(data) == {"token", "created_at", "source"} and data["source"] == SOURCE
              and _valid_token(data["token"]) and created.tzinfo is not None
              and created <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
    except (ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        raise AuthError("共有認証ファイルの内容が不正です。" + LOGIN_HINT)
    return data["token"]


def _recheck_target(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    _check_file_stat(st)


def marker_path(home):
    return store_dir(home) / MARKER_NAME


def check_marker(home):
    _check_dir(store_dir(home))
    try:
        st = os.lstat(marker_path(home))
    except FileNotFoundError:
        raise AuthError("共有認証が有効化されていません。" + LOGIN_HINT)
    _check_file_stat(st)


def write_marker(home):
    d = _ensure_dir(home)
    _recheck_target(marker_path(home))
    tmp = d / (".enabled.%d.%s.tmp" % (os.getpid(), os.urandom(6).hex()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, b"enabled\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        _recheck_target(marker_path(home))
        os.replace(tmp, marker_path(home))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def save_credential(home, token, now=None):
    if not _valid_token(token):
        raise AuthError("token の形式が不正です。")
    d = _ensure_dir(home)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    payload = json.dumps({"token": token, "created_at": now.replace(microsecond=0).isoformat(),
                          "source": SOURCE}).encode()
    _recheck_target(credential_path(home))
    tmp = d / (".credential.%d.%s.tmp" % (os.getpid(), os.urandom(6).hex()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        _recheck_target(credential_path(home))
        os.replace(tmp, credential_path(home))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


@contextlib.contextmanager
def login_lock(home):
    d = _ensure_dir(home)
    try:
        fd = os.open(d / "credential.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise AuthError("ロックファイルを開けません。")
    try:
        _check_file_stat(os.fstat(fd))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AuthError("別のログイン/ログアウトが進行中です。")
        yield
    finally:
        os.close(fd)


def delete_credential(home):
    """正本のみ削除する。戻り値: 削除したか。"""
    d = store_dir(home)
    try:
        os.lstat(d)
    except FileNotFoundError:
        return False
    with login_lock(home):
        path = credential_path(home)
        try:
            _check_file_stat(os.lstat(path))
        except FileNotFoundError:
            return False
        os.unlink(path)
        return True


# ---- 環境/native ------------------------------------------------------------

def check_conflicts(environ):
    bad = [k for k in CONFLICT_ENV if environ.get(k)]
    bad += [k for k in CONFLICT_FLAGS
            if environ.get(k, "").strip().lower() not in ("", "0", "false", "no", "off")]
    if bad:
        raise AuthError("共有認証と競合する環境変数があります(値は表示しません): " + ", ".join(bad))


def clean_env(environ):
    return {k: v for k, v in environ.items() if k not in CLEAR_ENV}


def _git_setting_roots(cwd):
    try:
        r = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--path-format=absolute",
                            "--show-toplevel", "--git-common-dir"], capture_output=True,
                           text=True, timeout=3)
        lines = r.stdout.splitlines()
        if r.returncode == 0 and len(lines) == 2:
            # Worktrees inherit local settings from the main checkout.
            return [Path(lines[0]), Path(lines[1]).parent]
    except (OSError, subprocess.SubprocessError):
        pass
    return []


def check_settings(home, environ, args, *, cwd=None):
    """Reject auth overrides without changing normal skills, MCP or permissions."""
    cwd = Path.cwd() if cwd is None else Path(cwd)
    config = Path(environ.get("CLAUDE_CONFIG_DIR") or Path(home) / ".claude").expanduser()
    paths = [config / name for name in ("settings.json", "settings.local.json", "remote-settings.json")]
    paths += [cwd / ".claude" / name for name in ("settings.json", "settings.local.json")]
    paths += [root / ".claude/settings.local.json" for root in _git_setting_roots(cwd)]
    managed = Path("/Library/Application Support/ClaudeCode")
    paths += [managed / "managed-settings.json"] + sorted((managed / "managed-settings.d").glob("*.json"))

    def inspect(data):
        if not isinstance(data, dict):
            raise AuthError("Claudeの設定を安全に確認できません(JSON objectが必要)。")
        if data.get("apiKeyHelper") or data.get("forceLoginMethod") in ("console", "gateway"):
            raise AuthError("Claudeの設定ファイルに共有OAuth認証と競合する認証方式があります。")
        env = data.get("env", {})
        if not isinstance(env, dict):
            raise AuthError("Claudeの設定ファイルのenvが不正です。")
        check_conflicts(env)
        if set(env) & (set(CLEAR_ENV) | {"CLAUDE_CONFIG_DIR"}):
            raise AuthError("Claudeの設定ファイルから共有認証の環境変数を上書きできません。")
        if "settings" in data:  # Cached server-managed envelope, if present.
            inspect(data["settings"])

    def read(path, required=False):
        try:
            with open(path, "rb") as f:
                raw = f.read((1 << 20) + 1)
            if len(raw) > 1 << 20:
                raise ValueError
            inspect(json.loads(raw))
        except FileNotFoundError:
            if required:
                raise AuthError("指定されたClaude設定ファイルがありません。")
        except (OSError, ValueError, TypeError):
            raise AuthError("Claudeの設定ファイルを安全に解析できません。内容や秘密値は表示しません。")

    for path in dict.fromkeys(paths):
        read(path)
    for i, arg in enumerate(args):
        if arg == "--settings":
            if i + 1 == len(args):
                raise AuthError("--settings の値がありません。")
            value = args[i + 1]
        elif arg.startswith("--settings="):
            value = arg.partition("=")[2]
        else:
            continue
        if value.lstrip().startswith("{"):
            try:
                inspect(json.loads(value))
            except (ValueError, TypeError):
                raise AuthError("追加のClaude設定JSONを安全に解析できません。")
        else:
            read(Path(value).expanduser(), required=True)


def find_native(home):
    native = Path(home) / ".local" / "bin" / "claude"
    if not native.is_file() or not os.access(native, os.X_OK):
        raise AuthError("native claude が見つかりません(~/.local/bin/claude)。")
    real = os.path.realpath(native)
    me = {os.path.realpath(__file__), os.path.realpath(sys.argv[0] or "")}
    if real in me:
        raise AuthError("native claude がこのブリッジ自身です。再帰起動を拒否しました。")
    try:
        if os.path.getsize(real) <= (1 << 20) and b"claude_shared_auth" in Path(real).read_bytes():
            raise AuthError("native claude がこのブリッジを参照しています。再帰起動を拒否しました。")
    except OSError:
        pass
    return str(native)


def token_env(environ, token):
    env = clean_env(environ)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


# ---- ログイン出力の走査 -----------------------------------------------------

_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
_CTRL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_URL = re.compile(r"https?://[^\s\x00-\x1f\x7f]+")


def _allowed_url(url):
    url = url.rstrip(".,;)'\"")
    try:
        p = urlparse(url)
        host, port = p.hostname, p.port
    except ValueError:
        return None
    if p.scheme != "https" or p.username or p.password or port not in (None, 443):
        return None
    if host not in ("claude.ai", "platform.claude.com") or p.path != "/oauth/authorize" or p.fragment:
        return None
    if "sk-ant-" in url or any(re.search(r"token|secret|key", k, re.I)
                               for k, _ in parse_qsl(p.query, keep_blank_values=True)):
        return None
    return url


class LoginScanner:
    """PTY 出力をメモリ上でだけ走査する。生出力は保持のみで外へ出さない。"""

    def __init__(self):
        self._raw = b""
        self._text = ""
        self._seen = []
        self._overflow = False

    def _update(self, data):
        if len(self._raw) + len(data) > MAX_SCAN_BYTES:
            self._overflow = True
            return
        self._raw += data
        text = _ANSI.sub("", self._raw.decode("utf-8", "replace"))
        self._text = _CTRL.sub("\n", text.replace("\r\n", "\n"))

    def _urls(self, final):
        out = []
        for m in _URL.finditer(self._text):
            if not final and m.end() >= len(self._text):
                continue
            url = _allowed_url(m.group(0))
            if url and url not in self._seen:
                self._seen.append(url)
                out.append(url)
        return out

    def feed(self, data):
        self._update(data)
        return self._urls(False)

    def finish_urls(self):
        return self._urls(True)

    def result(self):
        if self._overflow:
            return None
        idx = self._text.find(SUCCESS_MARKER)
        if idx < 0:
            return None
        rest = self._text[idx + len(SUCCESS_MARKER):]
        found = set(m.group(0) for m in re.finditer(TOKEN_RE.pattern + r"(?=\s)", rest))
        if len(found) != 1:
            return None
        token = found.pop()
        return token if _valid_token(token) else None


# ---- PTY ログイン/検証 ------------------------------------------------------

def _kill_group(proc):
    for sig, wait in ((signal.SIGTERM, 3), (signal.SIGKILL, 5)):
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, sig)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(wait)


def run_setup_token(native, environ, timeout, stdin_fd, out):
    env = clean_env(environ)
    master, slave = pty.openpty()
    saved = None
    proc = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 4096, 0, 0))
        forward = stdin_fd is not None and os.isatty(stdin_fd)
        if forward:
            saved = termios.tcgetattr(stdin_fd)
            noecho = termios.tcgetattr(stdin_fd)
            noecho[3] &= ~termios.ECHO
            termios.tcsetattr(stdin_fd, termios.TCSANOW, noecho)
        proc = subprocess.Popen(
            [native, "setup-token"], stdin=slave, stdout=slave, stderr=slave, env=env,
            start_new_session=True, close_fds=True,
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        os.close(slave)
        slave = None
        scanner = LoginScanner()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthError("ログインがタイムアウトしました。")
            fds = [master] + ([stdin_fd] if forward else [])
            ready, _, _ = select.select(fds, [], [], min(remaining, 0.2))
            eof = False
            for fd in ready:
                if fd == master:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        data = b""
                    if not data:
                        eof = True
                    for url in scanner.feed(data):
                        out.write("認証URL: %s\n" % url)
                        out.flush()
                else:
                    data = os.read(stdin_fd, 1024)
                    if data:
                        os.write(master, data)
                    else:
                        forward = False
            if eof or (not ready and proc.poll() is not None):
                break
        try:
            code = proc.wait(max(1, min(10, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            raise AuthError("ログインがタイムアウトしました。")
        for url in scanner.finish_urls():
            out.write("認証URL: %s\n" % url)
        token = scanner.result()
        if code != 0 or token is None:
            raise AuthError("ログインに失敗しました(完全な token を確認できませんでした)。")
        return token
    except KeyboardInterrupt:
        raise AuthError("ログインを中断しました。", code=130)
    finally:
        if proc is not None:
            _kill_group(proc)
        if slave is not None:
            os.close(slave)
        os.close(master)
        if saved is not None:
            termios.tcsetattr(stdin_fd, termios.TCSANOW, saved)


def validate_token(native, environ, token):
    cmd = [native, "--model", "sonnet", "--effort", "low", "--safe-mode", "-p", "Reply with OK.",
           "--tools", "", "--no-session-persistence", "--output-format", "json", "--max-turns", "1"]
    proc = None
    try:
        proc = subprocess.Popen(cmd, env=token_env(environ, token), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True, close_fds=True)
        stdout, _ = proc.communicate(timeout=VALIDATE_TIMEOUT)
        data = json.loads(stdout)
        ok = (proc.returncode == 0 and isinstance(data, dict) and data.get("type") == "result"
              and data.get("subtype") == "success" and data.get("is_error") is False)
    except KeyboardInterrupt:
        raise AuthError("検証を中断しました。既存の共有認証情報は変更していません。", code=130)
    except (OSError, ValueError, subprocess.SubprocessError):
        ok = False
    finally:
        if proc is not None:
            _kill_group(proc)
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
    if not ok:
        raise AuthError("新しい token の検証に失敗しました。既存の共有認証情報は変更していません。")


# ---- コマンド ---------------------------------------------------------------

def _default_stdin():
    try:
        fd = sys.stdin.fileno()
        return fd if os.isatty(fd) else None
    except (OSError, ValueError, AttributeError):
        return None


class _Signalled(BaseException):
    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


def _raise_signalled(signum, frame):
    raise _Signalled(signum)


def _login(home, environ, timeout, stdin_fd, out):
    check_conflicts(environ)
    check_settings(home, environ, [])
    native = find_native(home)
    sigs = (signal.SIGTERM, signal.SIGHUP)
    old = {}
    try:
        for sig in sigs:
            old[sig] = signal.signal(sig, _raise_signalled)
        with login_lock(home):
            out.write("ブラウザで次のURLを開き、表示されたコードをこの端末に入力してください。\n")
            out.flush()
            token = run_setup_token(native, environ, timeout, stdin_fd, out)
            validate_token(native, environ, token)
            save_credential(home, token)
            write_marker(home)
    except _Signalled as e:
        raise AuthError("ログインを中断しました。必要なら再度 claude auth login を実行してください。",
                        code=128 + e.signum)
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
    out.write("ログインに成功し、共有認証情報を更新しました。\n")


def _logout(home, out):
    if delete_credential(home):
        out.write("ローカルの共有認証情報を削除しました。サーバー側の token 失効(revoke)は行っていません。"
                  "必要なら Claude の設定画面で別途失効してください。native の Keychain/.credentials は変更していません。\n")
    else:
        out.write("ローカルの共有認証情報はありません(サーバー側の失効は行っていません)。\n")


def main(argv=None, *, environ=None, home=None, out=None, err=None, execve=os.execve,
         timeout=DEFAULT_LOGIN_TIMEOUT, stdin_fd=False):
    args = list(sys.argv[1:] if argv is None else argv)
    environ = dict(os.environ if environ is None else environ)
    home = Path(home) if home else Path.home()
    out = out or sys.stdout
    err = err or sys.stderr
    if stdin_fd is False:
        stdin_fd = _default_stdin()
    try:
        if args in (["auth", "login"], ["auth", "login", "--claudeai"], ["setup-token"]):
            _login(home, environ, timeout, stdin_fd, out)
            return 0
        if args[:2] == ["auth", "login"] or args[:1] == ["setup-token"]:
            raise AuthError("未対応のログインオプションです(--claudeai のみ)。")
        if args == ["auth", "logout"]:
            _logout(home, out)
            return 0
        if "setup-token" in args or any(
                args[i:i + 2] in (["auth", "login"], ["auth", "logout"])
                for i in range(len(args) - 1)):
            raise AuthError("認証の更新は追加オプションなしの claude auth login / logout を使ってください。")
        if any(a.split("=", 1)[0] == "--bare" for a in args):
            raise AuthError("--bare は共有OAuth認証を使用しないため、この入口では利用できません。")
        if args in INFO_INVOCATIONS:
            native = find_native(home)
            env = clean_env(environ)
        else:
            check_conflicts(environ)
            check_settings(home, environ, args)
            native = find_native(home)
            check_marker(home)
            env = token_env(environ, load_token(home))
            env["DISABLE_LOGIN_COMMAND"] = "1"
            env["DISABLE_LOGOUT_COMMAND"] = "1"
        try:
            execve(native, [native] + args, env)
        except OSError:
            raise AuthError("native claude を起動できません。")
        return 0
    except AuthError as e:
        err.write("claude-shared-auth: %s\n" % e)
        return e.code
    except Exception:
        err.write("claude-shared-auth: 内部エラーが発生しました(詳細は秘匿)。\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
