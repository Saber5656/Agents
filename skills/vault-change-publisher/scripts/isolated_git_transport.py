#!/usr/bin/env python3
"""Run bounded Git transports without reading a Vault repository config."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence


TRANSPORT_TIMEOUT_SECONDS = 90
LOCAL_COMMAND_TIMEOUT_SECONDS = 90
PROCESS_CLEANUP_TIMEOUT_SECONDS = 5
GIT_BIN = "/usr/bin/git"
SSH_COMMAND = (
    "/usr/bin/ssh -oBatchMode=yes -oConnectTimeout=20 "
    "-oServerAliveInterval=15 -oServerAliveCountMax=2"
)


class TransportError(RuntimeError):
    """Represent an isolated Git transport setup, timeout, or command failure."""


class ProcessCleanupError(subprocess.SubprocessError):
    """Represent a local subprocess group that could not be reaped safely."""


def kill_process_group(
    process: subprocess.Popen[Any],
    *,
    cleanup_timeout: int = PROCESS_CLEANUP_TIMEOUT_SECONDS,
) -> None:
    """Kill one private process group and reap its direct child within a bound."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=cleanup_timeout)
    except subprocess.TimeoutExpired as first_exc:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=cleanup_timeout)
        except subprocess.TimeoutExpired as final_exc:
            raise ProcessCleanupError(
                "local subprocess could not be reaped after bounded cleanup"
            ) from final_exc
        raise ProcessCleanupError(
            "local subprocess required a second bounded kill"
        ) from first_exc


def close_process_streams(process: subprocess.Popen[Any]) -> None:
    """Close every pipe owned by one completed or aborted subprocess."""
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def run_local_command(
    arguments: Sequence[str],
    *,
    timeout: int = LOCAL_COMMAND_TIMEOUT_SECONDS,
    check: bool = False,
    capture_output: bool = False,
    text: bool = False,
    input: str | bytes | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
    pass_fds: Sequence[int] = (),
) -> subprocess.CompletedProcess[Any]:
    """Run a local helper with a wall deadline and process-group cleanup."""
    if capture_output and (stdout is not None or stderr is not None):
        raise ValueError("stdout and stderr may not be used with capture_output")
    command = list(arguments)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_output else stdout,
        stderr=subprocess.PIPE if capture_output else stderr,
        cwd=cwd,
        env=env,
        text=text,
        start_new_session=True,
        pass_fds=tuple(pass_fds),
    )
    try:
        output, errors = process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            kill_process_group(process)
        finally:
            close_process_streams(process)
        raise subprocess.TimeoutExpired(
            command, timeout, output=exc.output, stderr=exc.stderr
        ) from exc
    except BaseException as original:
        cleanup_error = None
        try:
            kill_process_group(process)
        except ProcessCleanupError as exc:
            cleanup_error = exc
        finally:
            close_process_streams(process)
        if cleanup_error is not None:
            raise original from cleanup_error
        raise
    completed = subprocess.CompletedProcess(
        command, process.returncode, output, errors
    )
    if check:
        completed.check_returncode()
    return completed


def clean_transport_environment(object_directory: Path) -> dict[str, str]:
    """Build a fixed transport environment while preserving only normal auth state."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": SSH_COMMAND,
            "GIT_OBJECT_DIRECTORY": str(object_directory),
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.trustctime",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.checkStat",
            "GIT_CONFIG_VALUE_1": "minimal",
        }
    )
    return environment


def common_git_directory(git_dir: str) -> Path:
    """Resolve a worktree commondir through bounded, no-follow metadata reads."""
    root = Path(git_dir)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise TransportError("Git directory is not a real absolute directory")
    marker = root / "commondir"
    if not os.path.lexists(marker):
        common = root
    else:
        descriptor = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096:
                raise TransportError("Git commondir marker is invalid")
            content = os.read(descriptor, 4097)
            after = os.fstat(descriptor)
            if (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise TransportError("Git commondir marker changed while read")
        finally:
            os.close(descriptor)
        try:
            relative = content.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise TransportError("Git commondir marker is not UTF-8") from exc
        candidate = Path(relative)
        common = candidate if candidate.is_absolute() else root / candidate
        common = common.resolve()
    objects = common / "objects"
    if common.is_symlink() or not common.is_dir():
        raise TransportError("Git common directory is invalid")
    if objects.is_symlink() or not objects.is_dir():
        raise TransportError("Git object directory is invalid")
    return common


class IsolatedGitTransport:
    """One temporary bare control plane backed by an existing object store."""

    def __init__(self, git_dir: str, timeout: int = TRANSPORT_TIMEOUT_SECONDS):
        self._common = common_git_directory(git_dir)
        self._temporary = tempfile.TemporaryDirectory(prefix="it-news-git-transport-")
        self.git_dir = Path(self._temporary.name) / "transport.git"
        self.timeout = timeout
        (self.git_dir / "refs" / "heads").mkdir(parents=True)
        (self.git_dir / "refs" / "remotes").mkdir(parents=True)
        (self.git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
        (self.git_dir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n\tbare = true\n",
            encoding="ascii",
        )
        self.environment = clean_transport_environment(self._common / "objects")

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "IsolatedGitTransport":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def run(
        self,
        *arguments: str,
        check: bool = True,
        text: bool = True,
        input: str | bytes | None = None,
    ) -> subprocess.CompletedProcess[Any]:
        """Run one fixed transport command with a wall clock deadline."""
        command = [
            GIT_BIN,
            f"--git-dir={self.git_dir}",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.trustctime=false",
            "-c",
            "core.checkStat=minimal",
            "-c",
            f"core.sshCommand={SSH_COMMAND}",
            "-c",
            "credential.helper=",
            "-c",
            "http.proxy=",
            "-c",
            "https.proxy=",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "protocol.file.allow=always",
            *arguments,
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            env=self.environment,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(input=input, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                kill_process_group(process)
            except ProcessCleanupError as cleanup_exc:
                raise TransportError(
                    "Git transport process could not be reaped after timeout"
                ) from cleanup_exc
            finally:
                close_process_streams(process)
            raise TransportError(
                f"Git transport exceeded {self.timeout} second deadline"
            ) from exc
        except BaseException as original:
            cleanup_error = None
            try:
                kill_process_group(process)
            except ProcessCleanupError as exc:
                cleanup_error = exc
            finally:
                close_process_streams(process)
            if cleanup_error is not None:
                raise TransportError(
                    "Git transport process could not be reaped after failure"
                ) from cleanup_error
            raise
        close_process_streams(process)
        completed = subprocess.CompletedProcess(
            command, process.returncode, stdout, stderr
        )
        if check and completed.returncode != 0:
            raise subprocess.CalledProcessError(
                completed.returncode,
                command,
                output=completed.stdout,
                stderr=completed.stderr,
            )
        return completed


def run_transport(
    git_dir: str,
    *arguments: str,
    check: bool = True,
    text: bool = True,
    input: str | bytes | None = None,
    timeout: int = TRANSPORT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[Any]:
    """Run one isolated transport command in a disposable control plane."""
    with IsolatedGitTransport(git_dir, timeout=timeout) as transport:
        return transport.run(
            *arguments, check=check, text=text, input=input
        )
