#!/usr/bin/env python3
"""Resolve machine-local automation paths through the Saihai directory catalog."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shlex
import subprocess
import sys
import re
import tempfile
import time
from collections.abc import Callable
from typing import TypeVar
from urllib.parse import urlsplit
from pathlib import Path, PurePosixPath

from isolated_git_transport import run_local_command
from trusted_gitleaks import EXPECTED_CONFIG_SHA256, validated_config_path

ALLOWED_KEYS = {
    "SAIHAI_CHECKOUT_ROOT",
    "CODEX_BIN",
    "GITLEAKS_BIN",
    "IT_NEWS_ARCHIVE_RELATIVE",
    "ADVISORY_ARCHIVE_RELATIVE",
    "STANDING_TASK_ID",
    "STANDING_TASK_RELATIVE",
    "AUTHORIZATION_TASK_ID",
    "AUTHORIZATION_TASK_RELATIVE",
    "AUTHORIZATION_TASK_SHA256",
    "PUBLISHER_GIT_NAME",
    "PUBLISHER_GIT_EMAIL",
    "HERMES_BIN",
    "DISCORD_NEWS_TARGET",
}
REQUIRED_KEYS = ALLOWED_KEYS
CONTROL_COMMAND_TIMEOUT_SECONDS = 30
EXTERNAL_BINARY_TIMEOUT_SECONDS = 60
SUPPORTED_GITLEAKS_MAJOR = 8
MINIMUM_GITLEAKS_VERSION = (8, 19, 0)
# File Provider can transiently lock any existing Vault path used while the
# context is assembled, including catalog validation, pinned evidence, and Git
# metadata. Retry the complete read-only resolution attempt so a lock after the
# catalog load is covered by the same bounded window.
CONTEXT_RETRY_ATTEMPTS = 30
CONTEXT_RETRY_DELAY_SECONDS = 1.0
RETRYABLE_FILESYSTEM_ERRNOS = frozenset(
    errno_value
    for errno_value in (
        getattr(errno, "EDEADLK", None),
        getattr(errno, "EAGAIN", None),
        getattr(errno, "EBUSY", None),
    )
    if errno_value is not None
)
RETRYABLE_SUBPROCESS_MARKERS = frozenset(
    os.strerror(errno_value).casefold()
    for errno_value in RETRYABLE_FILESYSTEM_ERRNOS
)
T = TypeVar("T")


class ContextError(RuntimeError):
    """Represent invalid or unavailable runtime configuration."""


def subprocess_error_text(exc: subprocess.CalledProcessError) -> str:
    """Return bounded diagnostic text used only to classify transient locks."""
    values: list[str] = [str(exc)]
    for stream in (exc.stdout, exc.stderr):
        if isinstance(stream, bytes):
            values.append(stream.decode("utf-8", errors="replace"))
        elif isinstance(stream, str):
            values.append(stream)
    return "\n".join(values).casefold()


def is_retryable_file_provider_error(exc: BaseException) -> bool:
    """Recognize only the three approved transient File Provider failures."""
    if isinstance(exc, OSError):
        return exc.errno in RETRYABLE_FILESYSTEM_ERRNOS
    if isinstance(exc, subprocess.CalledProcessError):
        diagnostic = subprocess_error_text(exc)
        return any(marker in diagnostic for marker in RETRYABLE_SUBPROCESS_MARKERS)
    return False


def retry_read_only_context(operation: Callable[[], T]) -> T:
    """Retry a read-only context build without weakening permanent failures."""
    for attempt in range(CONTEXT_RETRY_ATTEMPTS):
        try:
            return operation()
        except (OSError, subprocess.CalledProcessError) as exc:
            if (
                not is_retryable_file_provider_error(exc)
                or attempt + 1 >= CONTEXT_RETRY_ATTEMPTS
            ):
                raise
            time.sleep(CONTEXT_RETRY_DELAY_SECONDS)
    raise ContextError("runtime context resolution exhausted retry attempts")


def load_directory_catalog(directory_paths: object, checkout_root: Path, catalog: dict[str, str]) -> dict[str, object]:
    """Load one fresh catalog snapshot within a context-resolution attempt."""
    loader = getattr(directory_paths, "load_environment")
    catalog.clear()
    diagnostics = loader(
        checkout_root=checkout_root,
        environ=catalog,
        require_catalog=True,
    )
    if not isinstance(diagnostics, dict):
        raise ContextError("directory catalog diagnostics are malformed")
    return diagnostics


def clean_git_environment() -> dict[str, str]:
    """Disable ambient Git control planes for every resolved repository."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.trustctime",
            "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "core.checkStat",
            "GIT_CONFIG_VALUE_2": "minimal",
        }
    )
    return environment


def parse_local_config(path: Path) -> dict[str, str]:
    """Parse a restricted KEY=VALUE file without evaluating shell code."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ContextError(f"invalid local config line:{line_number}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key not in ALLOWED_KEYS or key in values:
            raise ContextError(f"unsupported or duplicate local config key:{key}")
        parts = shlex.split(raw_value, posix=True)
        if len(parts) != 1:
            raise ContextError(f"invalid local config value:{key}")
        values[key] = parts[0]
    missing = sorted(REQUIRED_KEYS - values.keys())
    if missing:
        raise ContextError(f"missing local config keys:{','.join(missing)}")
    return values


def validated_relative(value: str, key: str) -> PurePosixPath:
    """Accept a normalized relative path without traversal."""
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
    ):
        raise ContextError(f"invalid relative path:{key}")
    return path


def validated_git_identity(name: str, email: str) -> tuple[str, str]:
    """Accept bounded Git identity fields without control or address delimiters."""
    if (
        not name
        or len(name) > 128
        or any(character in name for character in "\0\r\n<>")
    ):
        raise ContextError("invalid publisher Git name")
    if (
        not email
        or len(email) > 254
        or re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", email) is None
    ):
        raise ContextError("invalid publisher Git email")
    return name, email


def validated_discord_target(value: str) -> str:
    """Require an immutable Discord channel snowflake rather than a name."""
    if re.fullmatch(r"discord:[1-9][0-9]{16,19}", value) is None:
        raise ContextError("DISCORD_NEWS_TARGET must use discord:<channel-id>")
    return value


def validated_executable_path(value: str, key: str) -> str:
    """Resolve one absolute executable path for a machine-local integration."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ContextError(f"{key} must be an absolute path")
    resolved = candidate.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ContextError(f"{key} is not an executable regular file")
    return str(resolved)


def git_directory(repo_root: Path) -> str:
    """Return a direct or safely detached Git directory for one Vault."""
    top_level = run_local_command(
        [
            "git", "-C", str(repo_root), "-c", "core.fsmonitor=false",
            "-c", "core.trustctime=false", "-c", "core.checkStat=minimal",
            "rev-parse", "--show-toplevel",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=clean_git_environment(),
        timeout=CONTROL_COMMAND_TIMEOUT_SECONDS,
    ).stdout.strip()
    if Path(top_level).resolve() != repo_root.resolve():
        raise ContextError("catalog Vault root is not the repository top level")
    result = run_local_command(
        [
            "git", "-C", str(repo_root), "-c", "core.fsmonitor=false",
            "-c", "core.trustctime=false", "-c", "core.checkStat=minimal",
            "rev-parse", "--absolute-git-dir",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=clean_git_environment(),
        timeout=CONTROL_COMMAND_TIMEOUT_SECONDS,
    )
    git_dir = Path(result.stdout.strip())
    expected = repo_root / ".git"
    if expected.is_symlink():
        raise ContextError("Vault .git entry must not be a symlink")
    resolved_git_dir = git_dir.resolve()
    if expected.is_dir():
        if resolved_git_dir != expected.resolve():
            raise ContextError("Vault Git directory resolution mismatch")
        return str(resolved_git_dir)
    if not expected.is_file():
        raise ContextError("Vault .git entry is not a directory or regular file")
    try:
        lines = expected.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ContextError("Vault gitdir file is not valid UTF-8") from exc
    if len(lines) != 1 or not lines[0].startswith("gitdir: "):
        raise ContextError("Vault gitdir file is malformed")
    declared = Path(lines[0][len("gitdir: ") :])
    if (
        not declared.is_absolute()
        or declared.is_symlink()
        or not declared.is_dir()
        or resolved_git_dir != declared.resolve()
    ):
        raise ContextError("Vault detached Git directory is unsafe or mismatched")
    return str(resolved_git_dir)


def validate_git_control_config(repo_root: Path, git_dir: str) -> None:
    """Reject includes and repository config capable of redirecting Git I/O."""
    common_dir = Path(
        run_local_command(
            [
                "git", "-C", str(repo_root), "rev-parse",
                "--path-format=absolute", "--git-common-dir",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=clean_git_environment(),
            timeout=CONTROL_COMMAND_TIMEOUT_SECONDS,
        ).stdout.strip()
    )
    primary_config = common_dir / "config"
    optional_worktree_config = Path(git_dir) / "config.worktree"
    if primary_config.is_symlink() or not primary_config.is_file():
        raise ContextError("repository-local Git config must be a regular file")
    if os.path.lexists(optional_worktree_config) and (
        optional_worktree_config.is_symlink()
        or not optional_worktree_config.is_file()
    ):
        raise ContextError("worktree Git config must be a regular file")
    completed = run_local_command(
        [
            # Do not dereference repository-controlled include paths merely to
            # discover that includes are forbidden.  The directive itself is
            # visible with --no-includes and is rejected below.
            "git", "-C", str(repo_root), "config", "--local", "--no-includes",
            "--show-origin", "--null", "--list",
        ],
        check=True,
        capture_output=True,
        env=clean_git_environment(),
        timeout=CONTROL_COMMAND_TIMEOUT_SECONDS,
    )
    fields = completed.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ContextError("could not parse repository-local Git config")
    allowed_origin_paths = {
        primary_config.resolve(),
        optional_worktree_config.resolve(),
    }
    dangerous_exact = {
        "core.alternaterefscommand",
        "core.attributesfile",
        "core.askpass",
        "core.editor",
        "core.excludesfile",
        "core.fsmonitor",
        "core.gitproxy",
        "core.hookspath",
        "core.pager",
        "core.sshcommand",
        "gpg.program",
        "sequence.editor",
        "ssh.variant",
        "uploadpack.packobjectshook",
    }
    for index in range(0, len(fields), 2):
        try:
            origin = fields[index].decode("utf-8")
            key_value = fields[index + 1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContextError("repository-local Git config is not UTF-8") from exc
        key, separator, _value = key_value.partition("\n")
        normalized = key.lower()
        if not origin.startswith("file:"):
            raise ContextError("repository-local Git config origin is forbidden")
        origin_path = Path(origin.removeprefix("file:"))
        if not origin_path.is_absolute():
            origin_path = repo_root / origin_path
        if origin_path.resolve() not in allowed_origin_paths:
            raise ContextError("repository-local Git config include is forbidden")
        if not separator:
            raise ContextError("repository-local Git config value is missing")
        if (
            normalized in dangerous_exact
            or normalized.startswith("alias.")
            or normalized.startswith("include.")
            or normalized.startswith("includeif.")
            or normalized.startswith("url.")
            or normalized.startswith("credential.")
            or normalized.startswith("filter.")
            or normalized.startswith("http.")
            or normalized.startswith("protocol.")
            or (
                normalized.startswith("gpg.")
                and normalized.endswith(".program")
            )
            or (
                normalized.startswith("merge.")
                and normalized.rsplit(".", 1)[-1] in {"driver", "recursive"}
            )
            or (
                normalized.startswith("diff.")
                and normalized.rsplit(".", 1)[-1]
                in {"command", "textconv", "cachetextconv"}
            )
            or (
                normalized.startswith("remote.")
                and normalized.rsplit(".", 1)[-1]
                in {"proxy", "pushurl", "receivepack", "uploadpack"}
            )
        ):
            raise ContextError(f"unsafe repository-local Git config:{key}")


def remote_url(repo_root: Path) -> str:
    """Return a credential-free HTTPS or SSH origin URL."""
    result = run_local_command(
        [
            "git", "-C", str(repo_root), "config", "--local", "--no-includes",
            "--get", "remote.origin.url",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=clean_git_environment(),
        timeout=CONTROL_COMMAND_TIMEOUT_SECONDS,
    )
    value = result.stdout.strip()
    candidate = Path(value)
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if candidate.is_absolute():
        resolved_candidate = candidate.resolve()
        resolved_repo = repo_root.resolve()
        if (
            temporary_root in resolved_candidate.parents
            and temporary_root in resolved_repo.parents
        ):
            return str(resolved_candidate)
    if re.fullmatch(r"git@[A-Za-z0-9.-]+:[A-Za-z0-9._/-]+(?:\.git)?", value):
        return value
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "ssh"}
        or not parsed.hostname
        or parsed.username not in {None, "git"}
        or parsed.password is not None
        or not parsed.path.strip("/")
    ):
        raise ContextError("origin URL must be credential-free HTTPS or SSH")
    return value


def validated_gitleaks_version(value: str) -> str:
    """Require the reviewed v8 CLI with native git, dir, and stdin commands."""
    match = re.search(r"(?<![0-9])(\d+)\.(\d+)\.(\d+)(?![0-9])", value)
    version = tuple(int(group) for group in match.groups()) if match else None
    if (
        version is None
        or version[0] != SUPPORTED_GITLEAKS_MAJOR
        or version < MINIMUM_GITLEAKS_VERSION
    ):
        raise ContextError(
            "Gitleaks version 8.19.0 or later within major version 8 is required"
        )
    return value


def resolve_context_once(local_config: Path, workdir: Path) -> dict[str, str]:
    """Load one canonical env={} snapshot and derive runtime paths read-only."""
    values = parse_local_config(local_config)
    saihai_root = Path(values["SAIHAI_CHECKOUT_ROOT"]).expanduser().resolve()
    if not saihai_root.is_dir():
        raise ContextError("Saihai checkout is not a directory")
    sys.path.insert(0, str(saihai_root))
    import directory_paths  # type: ignore[import-not-found]

    catalog: dict[str, str] = {}
    diagnostics = load_directory_catalog(directory_paths, saihai_root, catalog)
    if diagnostics.get("status") != "loaded":
        raise ContextError("directory catalog status is not loaded")

    agents_root = Path(catalog["AGENTS_VAULT_ROOT"]).resolve()
    user_root = Path(catalog["USER_VAULT_ROOT"]).resolve()
    skills_root = Path(catalog["SKILLS_REPO_ROOT"]).resolve()
    for vault_root in (agents_root, user_root):
        if not vault_root.is_dir() or not os.access(vault_root, os.R_OK | os.W_OK):
            raise ContextError("catalog Vault root is not readable and writable")
    archive_relative = validated_relative(
        values["IT_NEWS_ARCHIVE_RELATIVE"], "IT_NEWS_ARCHIVE_RELATIVE"
    )
    advisory_relative = validated_relative(
        values["ADVISORY_ARCHIVE_RELATIVE"], "ADVISORY_ARCHIVE_RELATIVE"
    )
    standing_relative = validated_relative(
        values["STANDING_TASK_RELATIVE"], "STANDING_TASK_RELATIVE"
    )
    authorization_relative = validated_relative(
        values["AUTHORIZATION_TASK_RELATIVE"], "AUTHORIZATION_TASK_RELATIVE"
    )
    authorization_path = agents_root.joinpath(*authorization_relative.parts)
    if authorization_path.is_symlink() or not authorization_path.is_file():
        raise ContextError("authorization task is not a regular file")
    standing_path = agents_root.joinpath(*standing_relative.parts)
    if (
        standing_path.is_symlink()
        or not standing_path.is_file()
        or not os.access(standing_path, os.R_OK | os.W_OK)
    ):
        raise ContextError("standing task is not a readable and writable regular file")
    authorization_digest = hashlib.sha256(authorization_path.read_bytes()).hexdigest()
    if authorization_digest != values["AUTHORIZATION_TASK_SHA256"]:
        raise ContextError("authorization task digest does not match pinned evidence")
    publisher_name, publisher_email = validated_git_identity(
        values["PUBLISHER_GIT_NAME"], values["PUBLISHER_GIT_EMAIL"]
    )
    hermes_bin = validated_executable_path(values["HERMES_BIN"], "HERMES_BIN")
    discord_news_target = validated_discord_target(values["DISCORD_NEWS_TARGET"])

    agents_git_dir = git_directory(agents_root)
    user_git_dir = git_directory(user_root)
    validate_git_control_config(agents_root, agents_git_dir)
    validate_git_control_config(user_root, user_git_dir)

    gitleaks_version = run_local_command(
        [str(Path(values["GITLEAKS_BIN"]).expanduser().resolve()), "version"],
        check=True,
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in clean_git_environment().items()
            if not key.startswith("GITLEAKS_")
        },
        timeout=EXTERNAL_BINARY_TIMEOUT_SECONDS,
    ).stdout.strip()
    context = {
        "workdir": str(workdir.resolve()),
        "saihai_root": str(saihai_root),
        "codex_bin": str(Path(values["CODEX_BIN"]).expanduser().resolve()),
        "gitleaks_bin": str(
            Path(values["GITLEAKS_BIN"]).expanduser().resolve()
        ),
        "gitleaks_version": validated_gitleaks_version(gitleaks_version),
        "gitleaks_config": validated_config_path(),
        "gitleaks_config_sha256": EXPECTED_CONFIG_SHA256,
        "skills_root": str(skills_root),
        "agents_vault_root": str(agents_root),
        "user_vault_root": str(user_root),
        "agents_git_dir": agents_git_dir,
        "user_git_dir": user_git_dir,
        "agents_remote_url": remote_url(agents_root),
        "user_remote_url": remote_url(user_root),
        "it_news_archive_root": str(user_root.joinpath(*archive_relative.parts)),
        "it_news_archive_relative": str(archive_relative),
        "advisory_archive_root": str(
            agents_root.joinpath(*advisory_relative.parts)
        ),
        "advisory_archive_relative": str(advisory_relative),
        "standing_task_id": values["STANDING_TASK_ID"],
        "standing_task_path": str(standing_path),
        "authorization_task_id": values["AUTHORIZATION_TASK_ID"],
        "authorization_task_path": str(
            authorization_path
        ),
        "authorization_task_sha256": authorization_digest,
        "publisher_git_name": publisher_name,
        "publisher_git_email": publisher_email,
        "hermes_bin": hermes_bin,
        "discord_news_target": discord_news_target,
    }
    for key, value in context.items():
        if not value:
            raise ContextError(f"empty resolved context:{key}")
    if not os.access(context["codex_bin"], os.X_OK):
        raise ContextError("CODEX_BIN is not executable")
    if not os.access(context["gitleaks_bin"], os.X_OK):
        raise ContextError("GITLEAKS_BIN is not executable")
    return context


def resolve_context(local_config: Path, workdir: Path) -> dict[str, str]:
    """Resolve context with bounded retries across every existing Vault read."""
    return retry_read_only_context(
        lambda: resolve_context_once(local_config, workdir)
    )


def main(argv: list[str]) -> int:
    """Resolve context and print JSON without exporting path variables."""
    if len(argv) != 3:
        print("usage: resolve-runtime-context.py LOCAL_CONFIG WORKDIR", file=sys.stderr)
        return 64
    try:
        context = resolve_context(Path(argv[1]), Path(argv[2]))
    except (
        ContextError,
        OSError,
        ImportError,
        AttributeError,
        ValueError,
        subprocess.SubprocessError,
        KeyError,
    ) as exc:
        print(f"runtime context resolution failed:{exc}", file=sys.stderr)
        return 78
    print(json.dumps(context, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
