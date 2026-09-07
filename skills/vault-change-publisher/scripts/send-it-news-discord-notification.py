#!/usr/bin/env python3
"""Send one post-publication IT-news link to a fixed Discord channel."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit


MAX_INPUT_BYTES = 1024 * 1024
MAX_PROCESS_OUTPUT_BYTES = 256 * 1024
DELIVERY_TIMEOUT_SECONDS = 45
PROCESS_CLEANUP_SECONDS = 2
MAX_ATTEMPTS_PER_RUN = 3
RETRY_DELAYS_SECONDS = (0.1, 0.25)
SUMMARY_NAME = re.compile(
    r"^SUMMARY-IT-NEWS-(\d{4})-(\d{2})-(\d{2})(?:-\d+)?\.md$"
)
DISCORD_TARGET = re.compile(r"^discord:([1-9][0-9]{16,19})$")
DISCORD_SNOWFLAKE = re.compile(r"^[1-9][0-9]{16,19}$")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
STATE_ENTRY = re.compile(r"^attempt-([0-9]{6})-(intent|result)\.json$")
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
MAX_STATE_ATTEMPT = 999_999


class NotificationError(RuntimeError):
    """Represent a fail-closed notification condition with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreparedDelivery:
    """Validated, bounded inputs for one Discord delivery."""

    run_id: str
    hermes_bin: str
    target: str
    target_id: str
    target_sha256: str
    summary_commit: str
    summary_repo_path: str
    summary_date: str
    summary_url: str
    message: str
    message_sha256: str
    delivery_key_sha256: str
    workdir: Path


def canonical_json_bytes(value: object) -> bytes:
    """Return a stable UTF-8 representation for digests and receipts."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    """Return the lowercase SHA-256 digest of bytes."""
    return hashlib.sha256(content).hexdigest()


def read_regular_nofollow(path: Path, *, limit: int = MAX_INPUT_BYTES) -> bytes:
    """Read one stable, bounded regular file without following its final name."""
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise NotificationError("input_invalid", "input is not a bounded regular file")
        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(descriptor, min(65536, limit + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > limit:
                raise NotificationError("input_invalid", "input exceeds its size limit")
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            stat.S_IMODE(before.st_mode),
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            stat.S_IMODE(after.st_mode),
        )
        if identity_before != identity_after or consumed != before.st_size:
            raise NotificationError("input_changed", "input changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def load_json(path: Path) -> dict[str, object]:
    """Read one JSON object through the stable file reader."""
    try:
        value = json.loads(read_regular_nofollow(path).decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise NotificationError("input_invalid", "input is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise NotificationError("input_invalid", "input is not valid JSON") from exc
    if not isinstance(value, dict):
        raise NotificationError("input_invalid", "input JSON must be an object")
    return value


def write_exclusive_json(path: Path, payload: object) -> str:
    """Create one private JSON receipt without replacing an existing entry."""
    content = canonical_json_bytes(payload)
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise NotificationError("receipt_write_failed", "receipt write made no progress")
            offset += written
        os.fsync(descriptor)
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_size != len(content):
            raise NotificationError("receipt_write_failed", "receipt contract mismatch")
    finally:
        os.close(descriptor)
    return sha256_bytes(content)


def write_json_at(directory_fd: int, name: str, payload: object) -> str:
    """Create one private state receipt relative to a sealed directory."""
    content = canonical_json_bytes(payload)
    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise NotificationError("state_write_failed", "state write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(directory_fd)
    return sha256_bytes(content)


def read_json_at(directory_fd: int, name: str) -> tuple[dict[str, object], str]:
    """Read and digest one bounded state receipt relative to a directory."""
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        dir_fd=directory_fd,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_INPUT_BYTES:
            raise NotificationError("state_invalid", "state receipt is not a bounded regular file")
        content = b""
        while len(content) <= MAX_INPUT_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_INPUT_BYTES + 1 - len(content)))
            if not chunk:
                break
            content += chunk
        after = os.fstat(descriptor)
        if (
            len(content) > MAX_INPUT_BYTES
            or metadata.st_dev != after.st_dev
            or metadata.st_ino != after.st_ino
            or metadata.st_size != after.st_size
            or len(content) != metadata.st_size
        ):
            raise NotificationError("state_invalid", "state receipt changed while being read")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotificationError("state_invalid", "state receipt is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise NotificationError("state_invalid", "state receipt must be an object")
    return payload, sha256_bytes(content)


def ensure_private_directory(path: Path) -> int:
    """Create one durable owner-only child below a verified parent directory."""
    if path.name in {"", ".", ".."}:
        raise NotificationError("state_invalid", "notification state path is unsafe")
    parent = path.parent
    parent_observed = os.lstat(parent)
    if (
        not stat.S_ISDIR(parent_observed.st_mode)
        or parent_observed.st_uid != os.getuid()
        or stat.S_IMODE(parent_observed.st_mode) & 0o022
    ):
        raise NotificationError("state_invalid", "notification workdir is unsafe")
    parent_fd = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        parent_opened = os.fstat(parent_fd)
        if (parent_opened.st_dev, parent_opened.st_ino) != (
            parent_observed.st_dev,
            parent_observed.st_ino,
        ):
            raise NotificationError("state_invalid", "notification workdir changed")
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if opened.st_uid != os.getuid() or stat.S_IMODE(opened.st_mode) & 0o077:
            os.close(descriptor)
            raise NotificationError(
                "state_invalid", "notification state directory is unsafe"
            )
        return descriptor
    finally:
        os.close(parent_fd)


def ensure_private_child(directory_fd: int, name: str) -> int:
    """Create or open one owner-only child directory by descriptor."""
    try:
        os.mkdir(name, 0o700, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except FileExistsError:
        pass
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    observed = os.fstat(descriptor)
    if (
        observed.st_uid != os.getuid()
        or stat.S_IMODE(observed.st_mode) & 0o077
    ):
        os.close(descriptor)
        raise NotificationError("state_invalid", "notification key directory is unsafe")
    return descriptor


def validated_target(value: object) -> tuple[str, str]:
    """Accept only an explicit Discord snowflake, never a mutable channel name."""
    if not isinstance(value, str):
        raise NotificationError("input_invalid", "Discord target must be a string")
    match = DISCORD_TARGET.fullmatch(value)
    if match is None:
        raise NotificationError("input_invalid", "Discord target must use discord:<channel-id>")
    return value, match.group(1)


def github_repository(remote: object) -> tuple[str, str]:
    """Extract one credential-free GitHub owner/repository pair."""
    if not isinstance(remote, str) or len(remote) > 1024:
        raise NotificationError("input_invalid", "User Vault remote is invalid")
    if remote.startswith("git@github.com:"):
        repository_path = remote[len("git@github.com:") :]
    else:
        parsed = urlsplit(remote)
        if (
            parsed.scheme not in {"https", "ssh"}
            or (parsed.hostname or "").lower() != "github.com"
            or parsed.password is not None
            or parsed.username not in {None, "git"}
            or parsed.query
            or parsed.fragment
        ):
            raise NotificationError(
                "input_invalid", "User Vault remote must be credential-free GitHub"
            )
        repository_path = parsed.path.strip("/")
    if repository_path.endswith(".git"):
        repository_path = repository_path[:-4]
    parts = repository_path.split("/")
    if (
        len(parts) != 2
        or any(re.fullmatch(r"[A-Za-z0-9_.-]+", part) is None for part in parts)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise NotificationError("input_invalid", "GitHub repository path is invalid")
    return parts[0], parts[1]


def safe_output_path(workdir: Path, path: Path) -> None:
    """Require a new output below the canonical automation work directory."""
    if not path.is_absolute():
        raise NotificationError("input_invalid", "notification output must be absolute")
    resolved_parent = path.parent.resolve()
    try:
        resolved_parent.relative_to(workdir)
    except ValueError as exc:
        raise NotificationError("input_invalid", "notification output is outside workdir") from exc
    if os.path.lexists(path):
        raise NotificationError("input_invalid", "notification output already exists")


def prepare_delivery(
    runtime: dict[str, object], initial: dict[str, object], run_id: str
) -> PreparedDelivery:
    """Validate publication completion and build the immutable link message."""
    if RUN_ID.fullmatch(run_id) is None:
        raise NotificationError("input_invalid", "run id is invalid")
    workdir_value = runtime.get("workdir")
    if not isinstance(workdir_value, str):
        raise NotificationError("input_invalid", "runtime workdir is invalid")
    workdir = Path(workdir_value)
    if not workdir.is_absolute() or workdir.resolve() != workdir:
        raise NotificationError("input_invalid", "runtime workdir must be canonical")

    target, target_id = validated_target(runtime.get("discord_news_target"))
    hermes_value = runtime.get("hermes_bin")
    if not isinstance(hermes_value, str):
        raise NotificationError("input_invalid", "Hermes executable is invalid")
    hermes = Path(hermes_value)
    if (
        not hermes.is_absolute()
        or hermes.resolve() != hermes
        or not hermes.is_file()
        or not os.access(hermes, os.X_OK)
    ):
        raise NotificationError("input_invalid", "Hermes executable is unavailable")

    for key in ("agents_vault", "user_vault"):
        vault = initial.get(key)
        if not isinstance(vault, dict):
            raise NotificationError("publication_incomplete", "push result is incomplete")
        if (
            vault.get("push_status") not in {"complete", "not_required"}
            or vault.get("local_head") != vault.get("remote_head")
            or not isinstance(vault.get("local_head"), str)
            or HEX_40.fullmatch(str(vault.get("local_head"))) is None
        ):
            raise NotificationError("publication_incomplete", "both Vault pushes must be complete")
    if initial.get("daily_pipeline_status") != "complete":
        raise NotificationError("publication_incomplete", "daily pipeline is not complete")

    user_root_value = runtime.get("user_vault_root")
    summary_value = initial.get("summary_path")
    if not isinstance(user_root_value, str) or not isinstance(summary_value, str):
        raise NotificationError("input_invalid", "summary path is unavailable")
    user_root = Path(user_root_value)
    summary = Path(summary_value)
    if not user_root.is_absolute() or not summary.is_absolute():
        raise NotificationError("input_invalid", "summary path must be absolute")
    try:
        relative = summary.relative_to(user_root)
    except ValueError as exc:
        raise NotificationError("input_invalid", "summary is outside User Vault") from exc
    relative_posix = PurePosixPath(relative.as_posix())
    if (
        not relative_posix.parts
        or ".." in relative_posix.parts
        or any(part in {".git", ".obsidian"} for part in relative_posix.parts)
        or len(relative_posix.as_posix().encode("utf-8")) > 1024
    ):
        raise NotificationError("input_invalid", "summary repository path is unsafe")
    name_match = SUMMARY_NAME.fullmatch(relative_posix.name)
    if name_match is None:
        raise NotificationError("input_invalid", "summary filename is invalid")
    summary_date = "-".join(name_match.groups())
    try:
        date.fromisoformat(summary_date)
    except ValueError as exc:
        raise NotificationError("input_invalid", "summary date is invalid") from exc

    owner, repository = github_repository(runtime.get("user_remote_url"))
    summary_commit = str(initial["user_vault"]["remote_head"])
    encoded_path = quote(relative_posix.as_posix(), safe="/-._~")
    summary_url = (
        f"https://github.com/{owner}/{repository}/blob/{summary_commit}/{encoded_path}"
    )
    message = f"ITニュース（{summary_date}）が公開されました。\n{summary_url}"
    if len(message) > 1900 or "@everyone" in message or "@here" in message:
        raise NotificationError("input_invalid", "Discord message is unsafe or oversized")
    message_sha256 = sha256_bytes(message.encode("utf-8"))
    target_sha256 = sha256_bytes(target.encode("utf-8"))
    delivery_key_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "github_repository": f"{owner}/{repository}",
                "summary_commit": summary_commit,
                "summary_repo_path": relative_posix.as_posix(),
                "target_sha256": target_sha256,
            }
        )
    )
    return PreparedDelivery(
        run_id=run_id,
        hermes_bin=str(hermes),
        target=target,
        target_id=target_id,
        target_sha256=target_sha256,
        summary_commit=summary_commit,
        summary_repo_path=relative_posix.as_posix(),
        summary_date=summary_date,
        summary_url=summary_url,
        message=message,
        message_sha256=message_sha256,
        delivery_key_sha256=delivery_key_sha256,
        workdir=workdir,
    )


def hermes_environment() -> dict[str, str]:
    """Provide only the local account context Hermes needs for its own config."""
    home = os.environ.get("HOME")
    if not home or not Path(home).is_absolute():
        raise NotificationError("environment_invalid", "HOME is unavailable")
    environment = {
        "HOME": home,
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    if "TMPDIR" in os.environ:
        environment["TMPDIR"] = os.environ["TMPDIR"]
    return environment


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop the isolated Hermes process group within a fixed deadline."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=PROCESS_CLEANUP_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=PROCESS_CLEANUP_SECONDS)


def collect_bounded_output(
    process: subprocess.Popen[bytes],
) -> tuple[bytes, bytes, bool, bool]:
    """Collect both pipes without ever retaining more than the response limit."""
    if process.stdout is None or process.stderr is None:
        raise NotificationError("process_invalid", "Hermes output pipes are unavailable")
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + DELIVERY_TIMEOUT_SECONDS
    timed_out = False
    oversized = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _events in selector.select(timeout=min(0.25, remaining)):
                stream = key.fileobj
                buffer = streams[stream]
                try:
                    chunk = os.read(
                        stream.fileno(),
                        min(65536, MAX_PROCESS_OUTPUT_BYTES + 1 - len(buffer)),
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                buffer.extend(chunk)
                if len(buffer) > MAX_PROCESS_OUTPUT_BYTES:
                    oversized = True
                    break
            if oversized:
                break
        if not timed_out and not oversized and process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
            else:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
        if timed_out or oversized:
            for stream in streams:
                try:
                    selector.unregister(stream)
                except (KeyError, ValueError):
                    pass
                if not stream.closed:
                    stream.close()
            terminate_process_group(process)
        else:
            process.wait()
    finally:
        selector.close()
        for stream in streams:
            if not stream.closed:
                stream.close()
    return (
        bytes(streams[process.stdout]),
        bytes(streams[process.stderr]),
        timed_out,
        oversized,
    )


def invoke_hermes(prepared: PreparedDelivery) -> dict[str, object]:
    """Run the fixed Hermes command and classify its response without raw logs."""
    try:
        process = subprocess.Popen(
            [
                prepared.hermes_bin,
                "send",
                "--to",
                prepared.target,
                "--json",
                prepared.message,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=prepared.workdir,
            env=hermes_environment(),
            start_new_session=True,
        )
    except OSError:
        return {
            "classification": "definite_failure",
            "error_code": "spawn_failed",
            "returncode": None,
            "stdout_sha256": sha256_bytes(b""),
            "stderr_sha256": sha256_bytes(b""),
            "stdout_size": 0,
            "stderr_size": 0,
            "message_id": None,
        }
    stdout, stderr, timed_out, oversized = collect_bounded_output(process)
    observation: dict[str, object] = {
        "returncode": process.returncode,
        "stdout_sha256": sha256_bytes(stdout),
        "stderr_sha256": sha256_bytes(stderr),
        "stdout_size": len(stdout),
        "stderr_size": len(stderr),
        "message_id": None,
    }
    if timed_out:
        observation.update(
            {"classification": "ambiguous", "error_code": "delivery_timeout"}
        )
        return observation
    if oversized:
        observation.update(
            {"classification": "ambiguous", "error_code": "response_too_large"}
        )
        return observation
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        response = None
    if (
        process.returncode == 0
        and isinstance(response, dict)
        and response.get("success") is True
        and response.get("platform") == "discord"
        and str(response.get("chat_id")) == prepared.target_id
        and isinstance(response.get("message_id"), (str, int))
        and DISCORD_SNOWFLAKE.fullmatch(str(response.get("message_id"))) is not None
    ):
        observation.update(
            {
                "classification": "delivered",
                "error_code": None,
                "message_id": str(response["message_id"]),
            }
        )
        return observation
    if isinstance(response, dict) and response.get("success") is False:
        observation.update(
            {"classification": "definite_failure", "error_code": "backend_rejected"}
        )
        return observation
    observation.update(
        {"classification": "ambiguous", "error_code": "unverified_response"}
    )
    return observation


def validate_state_receipt(
    receipt: dict[str, object], prepared: PreparedDelivery, *, status: str
) -> None:
    """Bind a strict persistent receipt to one verified Discord delivery."""
    expected_keys = {
        "schema_version",
        "status",
        "delivery_key_sha256",
        "target_sha256",
        "summary_commit",
        "summary_repo_path",
        "summary_url",
        "message_sha256",
        "message_id",
        "attempt",
        "run_id",
    }
    if (
        set(receipt) != expected_keys
        or receipt.get("schema_version") != 1
        or receipt.get("status") != status
        or receipt.get("delivery_key_sha256") != prepared.delivery_key_sha256
        or receipt.get("target_sha256") != prepared.target_sha256
        or receipt.get("summary_commit") != prepared.summary_commit
        or receipt.get("summary_repo_path") != prepared.summary_repo_path
        or receipt.get("summary_url") != prepared.summary_url
        or receipt.get("message_sha256") != prepared.message_sha256
        or not isinstance(receipt.get("message_id"), str)
        or DISCORD_SNOWFLAKE.fullmatch(str(receipt.get("message_id"))) is None
        or type(receipt.get("attempt")) is not int
        or not 1 <= int(receipt["attempt"]) <= MAX_STATE_ATTEMPT
        or not isinstance(receipt.get("run_id"), str)
        or RUN_ID.fullmatch(str(receipt.get("run_id"))) is None
    ):
        raise NotificationError("state_invalid", "notification receipt identity mismatch")


def validate_intent(
    intent: dict[str, object], prepared: PreparedDelivery, attempt: int
) -> None:
    """Validate the exact durable authorization written before one send."""
    if (
        set(intent)
        != {
            "schema_version",
            "delivery_key_sha256",
            "message_sha256",
            "attempt",
            "run_id",
        }
        or intent.get("schema_version") != 1
        or intent.get("delivery_key_sha256") != prepared.delivery_key_sha256
        or intent.get("message_sha256") != prepared.message_sha256
        or intent.get("attempt") != attempt
        or not 1 <= attempt <= MAX_STATE_ATTEMPT
        or not isinstance(intent.get("run_id"), str)
        or RUN_ID.fullmatch(str(intent.get("run_id"))) is None
    ):
        raise NotificationError("state_invalid", "notification intent identity mismatch")


def validate_attempt_result(
    result: dict[str, object],
    prepared: PreparedDelivery,
    attempt: int,
    *,
    intent_run_id: object,
) -> None:
    """Reject corrupt or incomplete attempt results before any dedup decision."""
    expected_keys = {
        "schema_version",
        "delivery_key_sha256",
        "message_sha256",
        "attempt",
        "run_id",
        "returncode",
        "stdout_sha256",
        "stderr_sha256",
        "stdout_size",
        "stderr_size",
        "message_id",
        "classification",
        "error_code",
    }
    returncode = result.get("returncode")
    if (
        set(result) != expected_keys
        or result.get("schema_version") != 1
        or result.get("delivery_key_sha256") != prepared.delivery_key_sha256
        or result.get("message_sha256") != prepared.message_sha256
        or result.get("attempt") != attempt
        or not 1 <= attempt <= MAX_STATE_ATTEMPT
        or not isinstance(result.get("run_id"), str)
        or RUN_ID.fullmatch(str(result.get("run_id"))) is None
        or result.get("run_id") != intent_run_id
        or (returncode is not None and type(returncode) is not int)
        or not isinstance(result.get("stdout_sha256"), str)
        or HEX_64.fullmatch(str(result.get("stdout_sha256"))) is None
        or not isinstance(result.get("stderr_sha256"), str)
        or HEX_64.fullmatch(str(result.get("stderr_sha256"))) is None
        or type(result.get("stdout_size")) is not int
        or not 0 <= int(result["stdout_size"]) <= MAX_PROCESS_OUTPUT_BYTES + 1
        or type(result.get("stderr_size")) is not int
        or not 0 <= int(result["stderr_size"]) <= MAX_PROCESS_OUTPUT_BYTES + 1
    ):
        raise NotificationError("state_invalid", "notification attempt identity mismatch")
    classification = result.get("classification")
    error_code = result.get("error_code")
    message_id = result.get("message_id")
    if classification == "delivered":
        valid = (
            returncode == 0
            and error_code is None
            and isinstance(message_id, str)
            and DISCORD_SNOWFLAKE.fullmatch(message_id) is not None
        )
    elif classification == "definite_failure":
        valid = (
            message_id is None
            and error_code in {"backend_rejected", "spawn_failed"}
            and (error_code != "spawn_failed" or returncode is None)
        )
    elif classification == "ambiguous":
        valid = message_id is None and error_code in {
            "delivery_timeout",
            "response_too_large",
            "unverified_response",
        }
    else:
        valid = False
    if not valid:
        raise NotificationError("state_invalid", "notification result schema is invalid")


def delivery_receipt(
    prepared: PreparedDelivery,
    observation: dict[str, object],
    attempt: int,
    *,
    origin_run_id: str,
) -> dict[str, object]:
    """Build a durable delivered receipt from an authenticated Hermes response."""
    return {
        "schema_version": 1,
        "status": "delivered",
        "delivery_key_sha256": prepared.delivery_key_sha256,
        "target_sha256": prepared.target_sha256,
        "summary_commit": prepared.summary_commit,
        "summary_repo_path": prepared.summary_repo_path,
        "summary_url": prepared.summary_url,
        "message_sha256": prepared.message_sha256,
        "message_id": observation["message_id"],
        "attempt": attempt,
        "run_id": origin_run_id,
    }


def deliver_with_state(prepared: PreparedDelivery) -> dict[str, object]:
    """Deliver at most once across crashes and concurrent runner invocations."""
    state_root = prepared.workdir / "notification-state"
    root_fd = ensure_private_directory(state_root)
    key_fd: int | None = None
    lock_fd: int | None = None
    try:
        key_fd = ensure_private_child(root_fd, prepared.delivery_key_sha256)
        lock_fd = os.open(
            "delivery.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=key_fd,
        )
        lock_stat = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_uid != os.getuid()
            or stat.S_IMODE(lock_stat.st_mode) & 0o077
        ):
            raise NotificationError("state_invalid", "notification lock is unsafe")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise NotificationError("concurrent_delivery", "delivery is already active") from exc

        names = set(os.listdir(key_fd))
        if "delivery.json" in names:
            existing, receipt_sha256 = read_json_at(key_fd, "delivery.json")
            validate_state_receipt(existing, prepared, status="delivered")
            return {
                "status": "already_delivered",
                "error_code": None,
                "message_id": existing["message_id"],
                "attempts_this_run": 0,
                "state_receipt_sha256": receipt_sha256,
            }

        entries: dict[int, set[str]] = {}
        for name in names:
            match = STATE_ENTRY.fullmatch(name)
            if match is None:
                if name != "delivery.lock":
                    raise NotificationError("state_invalid", "unexpected notification state entry")
                continue
            entries.setdefault(int(match.group(1)), set()).add(match.group(2))
        for attempt in sorted(entries):
            kinds = entries[attempt]
            if "intent" not in kinds:
                raise NotificationError("state_invalid", "notification result lacks intent")
            intent, _ = read_json_at(key_fd, f"attempt-{attempt:06d}-intent.json")
            validate_intent(intent, prepared, attempt)
            if "result" not in kinds:
                return {
                    "status": "ambiguous",
                    "error_code": "unmatched_intent",
                    "message_id": None,
                    "attempts_this_run": 0,
                    "state_receipt_sha256": None,
                }
            result, result_sha256 = read_json_at(
                key_fd, f"attempt-{attempt:06d}-result.json"
            )
            validate_attempt_result(
                result,
                prepared,
                attempt,
                intent_run_id=intent["run_id"],
            )
            classification = result.get("classification")
            if classification == "delivered":
                receipt = delivery_receipt(
                    prepared,
                    result,
                    attempt,
                    origin_run_id=str(result["run_id"]),
                )
                try:
                    receipt_sha256 = write_json_at(key_fd, "delivery.json", receipt)
                except FileExistsError:
                    existing, receipt_sha256 = read_json_at(key_fd, "delivery.json")
                    validate_state_receipt(existing, prepared, status="delivered")
                return {
                    "status": "already_delivered",
                    "error_code": None,
                    "message_id": result["message_id"],
                    "attempts_this_run": 0,
                    "state_receipt_sha256": receipt_sha256,
                }
            if classification == "ambiguous":
                return {
                    "status": "ambiguous",
                    "error_code": str(result.get("error_code") or "ambiguous_result"),
                    "message_id": None,
                    "attempts_this_run": 0,
                    "state_receipt_sha256": result_sha256,
                }
            if classification != "definite_failure":
                raise NotificationError(
                    "state_invalid", "notification result classification is invalid"
                )
            error_code = result.get("error_code")
            if error_code == "spawn_failed" and result["run_id"] != prepared.run_id:
                continue
            if error_code != "backend_rejected":
                return {
                    "status": "failed",
                    "error_code": str(error_code or "delivery_failed"),
                    "message_id": None,
                    "attempts_this_run": 0,
                    "state_receipt_sha256": result_sha256,
                }

        next_attempt = max(entries, default=0) + 1
        if next_attempt + MAX_ATTEMPTS_PER_RUN - 1 > MAX_STATE_ATTEMPT:
            return {
                "status": "failed",
                "error_code": "state_exhausted",
                "message_id": None,
                "attempts_this_run": 0,
                "state_receipt_sha256": None,
            }
        last_result_sha256: str | None = None
        last_error_code = "backend_rejected"
        for attempt_offset in range(MAX_ATTEMPTS_PER_RUN):
            attempt = next_attempt + attempt_offset
            intent = {
                "schema_version": 1,
                "delivery_key_sha256": prepared.delivery_key_sha256,
                "message_sha256": prepared.message_sha256,
                "attempt": attempt,
                "run_id": prepared.run_id,
            }
            validate_intent(intent, prepared, attempt)
            write_json_at(key_fd, f"attempt-{attempt:06d}-intent.json", intent)
            observation = invoke_hermes(prepared)
            result = {
                "schema_version": 1,
                "delivery_key_sha256": prepared.delivery_key_sha256,
                "message_sha256": prepared.message_sha256,
                "attempt": attempt,
                "run_id": prepared.run_id,
                **observation,
            }
            validate_attempt_result(
                result,
                prepared,
                attempt,
                intent_run_id=intent["run_id"],
            )
            last_result_sha256 = write_json_at(
                key_fd, f"attempt-{attempt:06d}-result.json", result
            )
            classification = observation["classification"]
            if classification == "delivered":
                receipt = delivery_receipt(
                    prepared,
                    observation,
                    attempt,
                    origin_run_id=prepared.run_id,
                )
                receipt_sha256 = write_json_at(key_fd, "delivery.json", receipt)
                return {
                    "status": "delivered",
                    "error_code": None,
                    "message_id": observation["message_id"],
                    "attempts_this_run": attempt_offset + 1,
                    "state_receipt_sha256": receipt_sha256,
                }
            last_error_code = str(observation["error_code"])
            if classification == "ambiguous":
                return {
                    "status": "ambiguous",
                    "error_code": last_error_code,
                    "message_id": None,
                    "attempts_this_run": attempt_offset + 1,
                    "state_receipt_sha256": last_result_sha256,
                }
            if last_error_code != "backend_rejected":
                return {
                    "status": "failed",
                    "error_code": last_error_code,
                    "message_id": None,
                    "attempts_this_run": attempt_offset + 1,
                    "state_receipt_sha256": last_result_sha256,
                }
            if attempt_offset < len(RETRY_DELAYS_SECONDS):
                time.sleep(RETRY_DELAYS_SECONDS[attempt_offset])
        return {
            "status": "failed",
            "error_code": last_error_code,
            "message_id": None,
            "attempts_this_run": MAX_ATTEMPTS_PER_RUN,
            "state_receipt_sha256": last_result_sha256,
        }
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        if key_fd is not None:
            os.close(key_fd)
        os.close(root_fd)


def notification_summary(result: dict[str, object], prepared: PreparedDelivery) -> str:
    """Create the bounded string stored in canonical publication evidence."""
    fields = [
        f"discord:{result['status']}",
        f"summary_commit={prepared.summary_commit}",
        f"receipt_sha256={result.get('state_receipt_sha256') or 'none'}",
    ]
    if result.get("message_id") is not None:
        fields.append(f"message_id={result['message_id']}")
    if result.get("error_code") is not None:
        fields.append(f"error_code={result['error_code']}")
    return ";".join(fields)


def output_payload(
    result: dict[str, object], prepared: PreparedDelivery
) -> dict[str, object]:
    """Build a secret-free run receipt."""
    return {
        "schema_version": 1,
        "status": result["status"],
        "run_id": prepared.run_id,
        "delivery_key_sha256": prepared.delivery_key_sha256,
        "target_sha256": prepared.target_sha256,
        "summary_commit": prepared.summary_commit,
        "summary_repo_path": prepared.summary_repo_path,
        "summary_url": prepared.summary_url,
        "message_sha256": prepared.message_sha256,
        "message_id": result.get("message_id"),
        "attempts_this_run": result.get("attempts_this_run", 0),
        "state_receipt_sha256": result.get("state_receipt_sha256"),
        "error_code": result.get("error_code"),
        "notification_result": notification_summary(result, prepared),
    }


def main(argv: list[str]) -> int:
    """Send the notification and emit both receipt and effective push result."""
    if len(argv) != 6:
        print(
            "usage: send-it-news-discord-notification.py "
            "RUNTIME INITIAL_PUSH_RESULT RUN_ID RECEIPT EFFECTIVE_RESULT",
            file=sys.stderr,
        )
        return 64
    receipt_path = Path(argv[4])
    effective_path = Path(argv[5])
    try:
        runtime = load_json(Path(argv[1]))
        initial = load_json(Path(argv[2]))
        prepared = prepare_delivery(runtime, initial, argv[3])
        safe_output_path(prepared.workdir, receipt_path)
        safe_output_path(prepared.workdir, effective_path)
        result = deliver_with_state(prepared)
        receipt = output_payload(result, prepared)
        write_exclusive_json(receipt_path, receipt)
        effective = dict(initial)
        effective["notification_result"] = receipt["notification_result"]
        write_exclusive_json(effective_path, effective)
        if result["status"] in {"delivered", "already_delivered"}:
            return 0
        print(
            f"Discord notification failed closed:{result['error_code']}",
            file=sys.stderr,
        )
        return 75
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        NotificationError,
        subprocess.SubprocessError,
    ) as exc:
        code = exc.code if isinstance(exc, NotificationError) else "notification_error"
        print(f"Discord notification failed closed:{code}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
