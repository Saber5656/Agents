#!/usr/bin/env python3
"""Install verified staged artifacts below catalog-derived Vault roots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path, PurePosixPath

from atomic_file_ops import (
    AtomicTransactionError,
    fsync_after_rename,
    link_no_replace_durable,
    mkdir_durable,
    open_absolute_directory_chain,
    read_named_entry_contract,
    rename_no_replace,
    verify_rename_no_replace,
)

SUMMARY_PATTERN = re.compile(r"^SUMMARY-IT-NEWS-(\d{4})-(\d{2})-(\d{2})(?:-\d+)?\.md$")
ADVISORY_PATTERN = re.compile(r"^Personal-Vulnerability-Advisory-\d{4}-\d{2}-\d{2}(?:-\d+)?\.md$")


class InstallError(RuntimeError):
    """Represent a fail-closed artifact installation error."""


def cleanup_failed_install(
    directory_fd: int,
    candidate: str,
    owned_descriptor: int,
    owned_identity: tuple[int, int],
    owned_size: int,
    owned_mode: int,
    owned_sha256: str,
    reservation_fd: int,
) -> None:
    """Detach only the named target while its owned inode remains reserved."""
    detached_name = "worktree"
    verify_rename_no_replace(reservation_fd)
    try:
        rename_no_replace(directory_fd, candidate, reservation_fd, detached_name)
    except FileNotFoundError:
        return
    fsync_after_rename(directory_fd, reservation_fd)
    metadata = os.stat(detached_name, dir_fd=reservation_fd, follow_symlinks=False)
    if (metadata.st_dev, metadata.st_ino) != owned_identity:
        try:
            rename_no_replace(reservation_fd, detached_name, directory_fd, candidate)
            fsync_after_rename(reservation_fd, directory_fd)
        except FileExistsError as exc:
            raise InstallError(
                "replacement artifact was preserved in failed-install reservation"
            ) from exc
        raise InstallError("destination artifact was replaced during failed cleanup")
    try:
        retained_content, retained_identity = read_named_entry_contract(
            reservation_fd, detached_name, max_bytes=owned_size
        )
    except AtomicTransactionError as exc:
        raise InstallError(
            "installer-owned artifact changed and was retained in failed-install reservation"
        ) from exc
    owned_metadata = os.fstat(owned_descriptor)
    if (
        retained_identity[:4]
        != [owned_identity[0], owned_identity[1], owned_size, owned_mode]
        or hashlib.sha256(retained_content).hexdigest() != owned_sha256
        or (owned_metadata.st_dev, owned_metadata.st_ino) != owned_identity
        or not stat.S_ISREG(owned_metadata.st_mode)
        or owned_metadata.st_size != owned_size
        or owned_metadata.st_mode != owned_mode
        or sha256_fd(owned_descriptor) != owned_sha256
    ):
        raise InstallError(
            "installer-owned artifact changed and was retained in failed-install reservation"
        )


def sha256_fd(descriptor: int) -> str:
    """Calculate SHA-256 from one already-open regular-file descriptor."""
    hasher = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        while chunk := os.read(descriptor, 1024 * 1024):
            hasher.update(chunk)
    finally:
        os.lseek(descriptor, 0, os.SEEK_SET)
    return hasher.hexdigest()


def validate_source(source: Path, expected_hash: str) -> None:
    """Validate one source through a single no-follow file descriptor."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise InstallError("source artifact validation failed") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or sha256_fd(descriptor) != expected_hash:
            raise InstallError("source artifact validation failed")
    finally:
        os.close(descriptor)


def open_directory(parent_fd: int, component: str) -> int:
    """Open or create one descriptor-relative directory without symlinks."""
    try:
        mkdir_durable(component, 0o755, parent_fd=parent_fd)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(component, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise InstallError("destination contains a symlink or non-directory") from exc


def directory_chain(
    vault_root: Path, relative_directory: PurePosixPath
) -> tuple[tuple[int, int], ...]:
    """Reopen the Vault root and target parents without following symlinks."""
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    opened: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        opened.append(os.open(vault_root, flags))
        metadata = os.fstat(opened[-1])
        identities.append((metadata.st_dev, metadata.st_ino))
        for component in relative_directory.parts:
            opened.append(os.open(component, flags, dir_fd=opened[-1]))
            metadata = os.fstat(opened[-1])
            identities.append((metadata.st_dev, metadata.st_ino))
        return tuple(identities)
    except OSError as exc:
        raise InstallError("destination parent chain changed") from exc
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def install(
    source: Path,
    expected_hash: str,
    vault_root: Path,
    relative_directory: PurePosixPath,
    filename: str,
    expected_target: Path,
    quarantine_root: Path,
) -> tuple[Path, dict[str, object]]:
    """Publish a reservation-backed artifact and return its stable receipt."""
    source_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    try:
        source_fd = os.open(source, source_flags)
    except OSError as exc:
        raise InstallError("source artifact validation failed") from exc
    source_stat = os.fstat(source_fd)
    if not stat.S_ISREG(source_stat.st_mode) or sha256_fd(source_fd) != expected_hash:
        os.close(source_fd)
        raise InstallError("source artifact validation failed")
    root_flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        root_flags |= os.O_NOFOLLOW
    root_fd = os.open(vault_root, root_flags)
    opened = [root_fd]
    identities: list[tuple[int, int]] = []
    root_metadata = os.fstat(root_fd)
    identities.append((root_metadata.st_dev, root_metadata.st_ino))
    quarantine_root_fd, quarantine_root_chain = open_absolute_directory_chain(
        quarantine_root
    )
    quarantine_root_metadata = os.fstat(quarantine_root_fd)
    if quarantine_root_metadata.st_dev != root_metadata.st_dev:
        os.close(quarantine_root_fd)
        os.close(source_fd)
        os.close(root_fd)
        raise InstallError("artifact quarantine is not on the Vault filesystem")
    directory_fd = root_fd
    reservation_fd = -1
    target_fd = -1
    reservation_name = f".vault-publisher-install-{secrets.token_hex(16)}"
    try:
        for component in relative_directory.parts:
            directory_fd = open_directory(directory_fd, component)
            opened.append(directory_fd)
            directory_metadata = os.fstat(directory_fd)
            identities.append(
                (directory_metadata.st_dev, directory_metadata.st_ino)
            )
        target_directory = vault_root.joinpath(*relative_directory.parts)
        if expected_target.parent != target_directory:
            raise InstallError("planned destination directory mismatch")
        candidate = expected_target.name
        mkdir_durable(reservation_name, 0o700, parent_fd=quarantine_root_fd)
        reservation_fd = os.open(
            reservation_name,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=quarantine_root_fd,
        )
        verify_rename_no_replace(reservation_fd)
        reservation_metadata = os.fstat(reservation_fd)
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            target_fd = os.open("artifact", flags, 0o644, dir_fd=reservation_fd)
        except OSError as exc:
            raise InstallError("could not create private artifact reservation") from exc
        created_stat = os.fstat(target_fd)
        created_identity = (created_stat.st_dev, created_stat.st_ino)
        # Keep cleanup metadata defined even when the first write/fsync fails.
        # The cleanup verifier will then retain any partial run-owned inode and
        # report the original installation error instead of raising an
        # unrelated UnboundLocalError.
        target_stat = created_stat
        try:
            while chunk := os.read(source_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    count = os.write(target_fd, view)
                    if count <= 0:
                        raise InstallError("could not write destination artifact")
                    view = view[count:]
            os.fsync(target_fd)
            os.fsync(reservation_fd)
            target_stat = os.fstat(target_fd)
            if (
                not stat.S_ISREG(target_stat.st_mode)
                or target_stat.st_size != source_stat.st_size
            ):
                raise InstallError("destination artifact identity is invalid")
            try:
                link_no_replace_durable(
                    reservation_fd,
                    "artifact",
                    directory_fd,
                    candidate,
                )
            except OSError as exc:
                raise InstallError("planned destination is no longer available") from exc
            named_fd = os.open(
                candidate,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            try:
                named_stat = os.fstat(named_fd)
                named_hash = sha256_fd(named_fd)
            finally:
                os.close(named_fd)
            if (
                (named_stat.st_dev, named_stat.st_ino) != created_identity
                or (named_stat.st_size, named_stat.st_mode)
                != (target_stat.st_size, target_stat.st_mode)
                or named_hash != expected_hash
                or directory_chain(vault_root, relative_directory)
                != tuple(identities)
            ):
                raise InstallError("destination artifact changed after installation")
            receipt = {
                "path": str(expected_target),
                "vault_root": str(vault_root),
                "vault_root_identity": list(identities[0]),
                "target_parent_chain": [list(identity) for identity in identities],
                "quarantine_root": str(quarantine_root),
                "quarantine_root_identity": [
                    quarantine_root_metadata.st_dev,
                    quarantine_root_metadata.st_ino,
                ],
                "quarantine_root_chain": [
                    list(identity) for identity in quarantine_root_chain
                ],
                "reservation_name": reservation_name,
                "reservation_identity": [
                    reservation_metadata.st_dev,
                    reservation_metadata.st_ino,
                ],
                "sha256": expected_hash,
                "identity": [
                    target_stat.st_dev,
                    target_stat.st_ino,
                ],
                "size": target_stat.st_size,
                "mode": target_stat.st_mode,
            }
        except Exception as original:
            cleanup_error: Exception | None = None
            try:
                try:
                    cleanup_failed_install(
                        directory_fd,
                        candidate,
                        target_fd,
                        created_identity,
                        target_stat.st_size,
                        target_stat.st_mode,
                        expected_hash,
                        reservation_fd,
                    )
                except Exception as exc:
                    cleanup_error = exc
            finally:
                os.close(target_fd)
                target_fd = -1
            if cleanup_error is not None:
                # Preserve the actual write/fsync/install failure as the
                # primary exception.  Cleanup diagnostics remain available as
                # its cause, and the partial run-owned inode stays reserved.
                raise original from cleanup_error
            raise
        else:
            os.close(target_fd)
            target_fd = -1
        return expected_target, receipt
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        if reservation_fd >= 0:
            os.close(reservation_fd)
        os.close(source_fd)
        os.close(quarantine_root_fd)
        for descriptor in reversed(opened):
            os.close(descriptor)


def safe_relative(value: str) -> PurePosixPath:
    """Validate a catalog-derived relative destination."""
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
    ):
        raise InstallError("invalid relative destination")
    return path


def planned_target(
    vault_root: Path, relative_directory: PurePosixPath, filename: str
) -> Path:
    """Choose the first currently unused target without mutating the Vault."""
    if vault_root.is_symlink() or not vault_root.is_dir():
        raise InstallError("Vault root must be a real directory")
    current = vault_root
    missing_parent = False
    for component in relative_directory.parts:
        current = current / component
        if missing_parent:
            continue
        if current.is_symlink():
            raise InstallError("destination contains a symlink")
        if current.exists():
            if not current.is_dir():
                raise InstallError("destination contains a non-directory")
        else:
            missing_parent = True
    stem, suffix = os.path.splitext(filename)
    for index in range(1, 10000):
        candidate = filename if index == 1 else f"{stem}-{index}{suffix}"
        target = current / candidate
        if missing_parent or not os.path.lexists(target):
            return target
    raise InstallError("artifact collision limit exceeded")


def target_from_bound_plan(
    value: object,
    vault_root: Path,
    relative_directory: PurePosixPath,
    filename: str,
) -> Path:
    """Validate a runner-generated target without probing a blocked Vault."""
    if not isinstance(value, str):
        raise InstallError("artifact plan target is not a string")
    target = Path(value)
    expected_parent = vault_root.joinpath(*relative_directory.parts)
    stem, suffix = os.path.splitext(filename)
    candidate_stem, candidate_suffix = os.path.splitext(target.name)
    expected_prefix = f"{stem}-"
    suffix_number = (
        candidate_stem[len(expected_prefix) :]
        if candidate_stem.startswith(expected_prefix)
        else ""
    )
    if (
        not target.is_absolute()
        or target.parent != expected_parent
        or candidate_suffix != suffix
        or (
            target.name != filename
            and (not suffix_number.isdigit() or int(suffix_number) < 2)
        )
    ):
        raise InstallError("artifact plan target is invalid")
    return target


def main(argv: list[str]) -> int:
    """Install summary and advisory artifacts and emit their final paths."""
    plan_only = len(argv) == 4 and argv[1] == "--plan"
    install_from_plan = len(argv) == 5 and argv[1] != "--plan"
    if not plan_only and not install_from_plan:
        print(
            "usage: install-verified-artifacts.py --plan CONTEXT COLLECTION | "
            "install-verified-artifacts.py CONTEXT COLLECTION PLAN ROLE",
            file=sys.stderr,
        )
        return 64
    try:
        offset = 1 if plan_only else 0
        context = json.loads(Path(argv[1 + offset]).read_text(encoding="utf-8"))
        collection = json.loads(Path(argv[2 + offset]).read_text(encoding="utf-8"))
        summary_source = Path(collection["summary_path"])
        advisory_source = Path(collection["advisory_path"])
        summary_match = SUMMARY_PATTERN.fullmatch(summary_source.name)
        if not summary_match or not ADVISORY_PATTERN.fullmatch(advisory_source.name):
            raise InstallError("artifact filename does not match its declared role")
        validate_source(summary_source, collection["summary_sha256"])
        validate_source(advisory_source, collection["advisory_sha256"])
        year, month, day = summary_match.groups()
        summary_relative = safe_relative(context["it_news_archive_relative"]).joinpath(
            year, month, day
        )
        advisory_relative = safe_relative(context["advisory_archive_relative"])
        user_root = Path(context["user_vault_root"])
        agents_root = Path(context["agents_vault_root"])
        if plan_only:
            summary_target = planned_target(
                user_root, summary_relative, summary_source.name
            )
            advisory_target = planned_target(
                agents_root, advisory_relative, advisory_source.name
            )
        else:
            selected = {value for value in argv[4].split(",") if value}
            if len(selected) != 1 or not selected <= {
                "user_it_news_summary",
                "agents_security_advisory",
            }:
                raise InstallError("exactly one artifact role must be selected")
            plan = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
            summary_target = target_from_bound_plan(
                plan.get("summary_target"),
                user_root,
                summary_relative,
                summary_source.name,
            )
            advisory_target = target_from_bound_plan(
                plan.get("advisory_target"),
                agents_root,
                advisory_relative,
                advisory_source.name,
            )
            if "user_it_news_summary" in selected:
                if planned_target(
                    user_root, summary_relative, summary_source.name
                ) != summary_target:
                    raise InstallError(
                        "selected summary target no longer matches the artifact plan"
                    )
                summary_target, installed_receipt = install(
                    summary_source,
                    collection["summary_sha256"],
                    user_root,
                    summary_relative,
                    summary_source.name,
                    summary_target,
                    Path(context["user_git_dir"]),
                )
            if "agents_security_advisory" in selected:
                if planned_target(
                    agents_root, advisory_relative, advisory_source.name
                ) != advisory_target:
                    raise InstallError(
                        "selected advisory target no longer matches the artifact plan"
                    )
                advisory_target, installed_receipt = install(
                    advisory_source,
                    collection["advisory_sha256"],
                    agents_root,
                    advisory_relative,
                    advisory_source.name,
                    advisory_target,
                    Path(context["agents_git_dir"]),
                )
        result = {
            "summary_target": str(summary_target),
            "advisory_target": str(advisory_target),
        }
        if not plan_only:
            result["installed_receipt"] = installed_receipt
    except (InstallError, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"artifact installation failed:{exc}", file=sys.stderr)
        return 75
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
