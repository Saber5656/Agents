#!/usr/bin/env python3
"""Provide descriptor-relative atomic filesystem operations used by publication."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import Callable


HEAD_INDEX_TRANSACTION_JOURNAL = ".vault-publisher-head-index-transaction.json"
HEAD_INDEX_CANDIDATE_PREFIX = ".vault-publisher-index-candidate-"
HEAD_INDEX_EXPECTED_PREFIX = ".vault-publisher-index-expected-"
HEAD_INDEX_EXCHANGE_PREFIX = ".vault-publisher-index-exchange-"
HEAD_INDEX_DISPLACED_PREFIX = ".vault-publisher-index-displaced-"
RETAINED_ENTRY_PREFIX = ".vault-publisher-retained-"
HEX_OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AtomicTransactionError(RuntimeError):
    """Represent a HEAD/index transaction that could not be reconciled safely."""


def _rename_function(operation: str = "no_replace") -> tuple[object, int]:
    """Return one platform atomic rename function and requested flag."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        name = "renameatx_np"
        flags = {"no_replace": 0x00000004, "exchange": 0x00000002}
    elif sys.platform.startswith("linux"):
        name = "renameat2"
        flags = {"no_replace": 0x00000001, "exchange": 0x00000002}
    else:
        raise OSError(errno.ENOTSUP, "atomic rename operation is unavailable")
    try:
        flag = flags[operation]
    except KeyError as exc:
        raise OSError(errno.EINVAL, "unknown atomic rename operation") from exc
    function = getattr(libc, name, None)
    if function is None:
        raise OSError(errno.ENOSYS, "atomic no-replace rename is unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    return function, flag


def rename_no_replace(
    source_directory_fd: int,
    source_name: str,
    destination_directory_fd: int,
    destination_name: str,
) -> None:
    """Atomically rename any entry type without replacing the destination."""
    function, flag = _rename_function("no_replace")
    result = function(
        source_directory_fd,
        os.fsencode(source_name),
        destination_directory_fd,
        os.fsencode(destination_name),
        flag,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            destination_name,
        )


def rename_exchange(
    first_directory_fd: int,
    first_name: str,
    second_directory_fd: int,
    second_name: str,
) -> None:
    """Atomically exchange two existing entries without an overwrite window."""
    function, flag = _rename_function("exchange")
    result = function(
        first_directory_fd,
        os.fsencode(first_name),
        second_directory_fd,
        os.fsencode(second_name),
        flag,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), second_name)


def _identity(metadata: os.stat_result) -> list[int]:
    return [
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mode,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    ]


def _read_descriptor_contract(
    descriptor: int, *, max_bytes: int | None = None
) -> tuple[bytes, list[int]]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise AtomicTransactionError("transaction entry is not a regular file")
    if max_bytes is not None and (max_bytes < 0 or before.st_size > max_bytes):
        raise AtomicTransactionError("transaction entry exceeds its size bound")
    content = bytearray()
    while chunk := os.read(descriptor, 1024 * 1024):
        content.extend(chunk)
        if max_bytes is not None and len(content) > max_bytes:
            raise AtomicTransactionError("transaction entry exceeds its size bound")
    after = os.fstat(descriptor)
    if _identity(before) != _identity(after) or len(content) != before.st_size:
        raise AtomicTransactionError("transaction entry changed while it was read")
    return bytes(content), _identity(after)


def _read_entry_contract(directory_fd: int, name: str) -> tuple[bytes, list[int]]:
    descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    try:
        return _read_descriptor_contract(descriptor)
    finally:
        os.close(descriptor)


def read_named_entry_contract(
    directory_fd: int, name: str, *, max_bytes: int | None = None
) -> tuple[bytes, list[int]]:
    """Seal one direct regular entry through its bound destination directory."""
    if (
        not name
        or "/" in name
        or "\\" in name
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in name)
    ):
        raise AtomicTransactionError("entry contract name is malformed")
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        return _read_descriptor_contract(descriptor, max_bytes=max_bytes)
    finally:
        os.close(descriptor)


def _entry_contract_or_none(
    directory_fd: int, name: str
) -> tuple[bytes, list[int]] | None:
    try:
        return _read_entry_contract(directory_fd, name)
    except FileNotFoundError:
        return None


def _matches_contract(
    contract: tuple[bytes, list[int]] | None,
    expected: dict[str, object],
    *,
    exact_metadata: bool = False,
) -> bool:
    if contract is None:
        return False
    content, identity = contract
    expected_identity = expected.get("identity")
    if not isinstance(expected_identity, list) or len(expected_identity) != 6:
        return False
    metadata_matches = (
        identity == expected_identity
        if exact_metadata
        else identity[:4] == expected_identity[:4]
    )
    return metadata_matches and hashlib.sha256(content).hexdigest() == expected.get("sha256")


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise AtomicTransactionError("could not write transaction entry")
        view = view[written:]


def _open_retention_directory(
    directory_fd: int, *, prefix: str = RETAINED_ENTRY_PREFIX
) -> tuple[int, str]:
    """Create and bind one private directory that is never auto-deleted."""
    if re.fullmatch(r"[.A-Za-z0-9_-]+", prefix) is None:
        raise AtomicTransactionError("retention prefix is unsafe")
    for _ in range(10):
        name = prefix + secrets.token_hex(12)
        try:
            os.mkdir(name, 0o700, dir_fd=directory_fd)
        except FileExistsError:
            continue
        os.fsync(directory_fd)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(descriptor)
            raise AtomicTransactionError("retention target is not a directory")
        return descriptor, name
    raise AtomicTransactionError("could not allocate a private retention directory")


def retain_named_entry_no_replace(
    directory_fd: int,
    name: str,
    expected_identity: tuple[int, int],
    *,
    label: str = "transaction entry",
    prefix: str = RETAINED_ENTRY_PREFIX,
) -> tuple[str, tuple[bytes, list[int]]]:
    """Atomically retain the current named inode and reject a replacement."""
    if (
        not name
        or "/" in name
        or "\x00" in name
        or len(expected_identity) != 2
        or not all(isinstance(item, int) and item >= 0 for item in expected_identity)
    ):
        raise AtomicTransactionError("retention input is malformed")
    retention_fd, retention_name = _open_retention_directory(
        directory_fd, prefix=prefix
    )
    try:
        try:
            rename_no_replace(
                directory_fd,
                name,
                retention_fd,
                "entry",
            )
        except FileNotFoundError as exc:
            raise AtomicTransactionError(
                f"{label} disappeared before private retention"
            ) from exc
        os.fsync(retention_fd)
        os.fsync(directory_fd)
        retained = _read_entry_contract(retention_fd, "entry")
        identity = retained[1]
        if tuple(identity[:2]) != expected_identity:
            raise AtomicTransactionError(
                f"{label} was replaced; third-party inode retained in {retention_name}"
            )
        return retention_name, retained
    finally:
        os.close(retention_fd)


def allocate_private_entry_path(
    parent: str | os.PathLike[str],
    *,
    prefix: str,
    entry_name: str = "entry",
) -> str:
    """Allocate a unique 0700 directory and return one absent direct child path."""
    if (
        re.fullmatch(r"[.A-Za-z0-9_-]+", prefix) is None
        or re.fullmatch(r"[A-Za-z0-9._-]+", entry_name) is None
        or entry_name in {".", ".."}
    ):
        raise AtomicTransactionError("private entry path input is unsafe")
    parent_path = Path(parent)
    parent_fd = os.open(
        parent_path,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        private_fd, private_name = _open_retention_directory(
            parent_fd, prefix=prefix
        )
        os.close(private_fd)
    finally:
        os.close(parent_fd)
    return str(parent_path / private_name / entry_name)


def retain_path_no_replace(
    path: str | os.PathLike[str],
    *,
    expected: dict[str, object] | None = None,
    label: str = "temporary entry",
    prefix: str = RETAINED_ENTRY_PREFIX,
    allow_missing: bool = False,
) -> str | None:
    """Retain a direct pathname and verify its sealed post-move contract."""
    candidate = Path(path)
    if candidate.name in {"", ".", ".."} or candidate.parent == candidate:
        raise AtomicTransactionError("temporary retention path is unsafe")
    directory_fd = os.open(
        candidate.parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        observed = _entry_contract_or_none(directory_fd, candidate.name)
        if observed is None:
            if allow_missing:
                return None
            raise AtomicTransactionError(f"{label} disappeared before private retention")
        contract = expected
        if contract is None:
            content, identity = observed
            contract = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "identity": identity,
            }
        if not _matches_contract(observed, contract):
            observed_identity = observed[1]
            _, retained = retain_named_entry_no_replace(
                directory_fd,
                candidate.name,
                (observed_identity[0], observed_identity[1]),
                label=f"unowned {label}",
                prefix=prefix,
            )
            if not _matches_contract(retained, {
                "sha256": hashlib.sha256(observed[0]).hexdigest(),
                "identity": observed_identity,
            }):
                raise AtomicTransactionError(
                    f"unowned {label} changed during private retention"
                )
            raise AtomicTransactionError(
                f"{label} was replaced; third-party inode retained"
            )
        expected_identity = contract.get("identity")
        if not isinstance(expected_identity, list) or len(expected_identity) != 6:
            raise AtomicTransactionError("temporary retention contract is malformed")
        retention_name, retained = retain_named_entry_no_replace(
            directory_fd,
            candidate.name,
            (expected_identity[0], expected_identity[1]),
            label=label,
            prefix=prefix,
        )
        if not _matches_contract(retained, contract):
            raise AtomicTransactionError(
                f"{label} changed during private retention"
            )
        return str(candidate.parent / retention_name / "entry")
    finally:
        os.close(directory_fd)


def _create_entry(
    directory_fd: int, name: str, content: bytes, mode: int
) -> tuple[int, list[int]]:
    descriptor = os.open(
        name,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
        dir_fd=directory_fd,
    )
    created = os.fstat(descriptor)
    created_identity = (created.st_dev, created.st_ino)
    try:
        _write_all(descriptor, content)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        _, identity = _read_descriptor_contract(descriptor)
        os.fsync(directory_fd)
        return descriptor, identity
    except Exception as original:
        os.close(descriptor)
        try:
            retain_named_entry_no_replace(
                directory_fd,
                name,
                created_identity,
                label="failed transaction entry",
            )
        except AtomicTransactionError as cleanup_error:
            raise cleanup_error from original
        raise


def _link_matching_entry(
    directory_fd: int,
    source_name: str,
    destination_name: str,
    expected: dict[str, object],
) -> list[int]:
    """Create a durable hardlink only from the sealed regular source inode."""
    if not _matches_contract(_entry_contract_or_none(directory_fd, source_name), expected):
        raise AtomicTransactionError("transaction hardlink source changed")
    os.link(
        source_name,
        destination_name,
        src_dir_fd=directory_fd,
        dst_dir_fd=directory_fd,
        follow_symlinks=False,
    )
    os.fsync(directory_fd)
    linked = _entry_contract_or_none(directory_fd, destination_name)
    if not _matches_contract(linked, expected):
        raise AtomicTransactionError("transaction hardlink identity changed")
    assert linked is not None
    return linked[1]


def _retain_matching_entry(
    directory_fd: int,
    name: str,
    expected: dict[str, object],
) -> bool:
    """Move only a sealed transaction inode into durable private retention."""
    contract = _entry_contract_or_none(directory_fd, name)
    if contract is None:
        return False
    if not _matches_contract(contract, expected):
        raise AtomicTransactionError(f"refusing to retain unrelated transaction entry: {name}")
    expected_identity = expected.get("identity")
    if not isinstance(expected_identity, list) or len(expected_identity) != 6:
        raise AtomicTransactionError("transaction retention contract is malformed")
    _, retained = retain_named_entry_no_replace(
        directory_fd,
        name,
        (expected_identity[0], expected_identity[1]),
        label=f"sealed transaction entry {name}",
    )
    if not _matches_contract(retained, expected):
        raise AtomicTransactionError(
            f"sealed transaction entry changed during private retention: {name}"
        )
    return True


def _journal_contract(content: bytes, identity: list[int]) -> dict[str, object]:
    return {"sha256": hashlib.sha256(content).hexdigest(), "identity": identity}


def _validate_journal(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "version",
        "base_head",
        "candidate_head",
        "expected_index",
        "candidate_index",
        "exchange_name",
        "displaced_name",
    }:
        raise AtomicTransactionError("HEAD/index recovery journal is malformed")
    if value.get("version") != 3:
        raise AtomicTransactionError("HEAD/index recovery journal version is unsupported")
    for key in ("base_head", "candidate_head"):
        item = value.get(key)
        if not isinstance(item, str) or HEX_OBJECT_ID.fullmatch(item) is None:
            raise AtomicTransactionError("HEAD/index recovery journal contains an invalid OID")
    expected = value.get("expected_index")
    candidate = value.get("candidate_index")
    if not isinstance(expected, dict) or set(expected) != {"name", "sha256", "identity"}:
        raise AtomicTransactionError("HEAD/index expected contract is malformed")
    if not isinstance(candidate, dict) or set(candidate) != {"name", "sha256", "identity"}:
        raise AtomicTransactionError("HEAD/index candidate contract is malformed")
    for contract in (expected, candidate):
        digest = contract.get("sha256")
        identity = contract.get("identity")
        if (
            not isinstance(digest, str)
            or HEX_SHA256.fullmatch(digest) is None
            or not isinstance(identity, list)
            or len(identity) != 6
            or not all(isinstance(item, int) and item >= 0 for item in identity)
        ):
            raise AtomicTransactionError("HEAD/index file contract is malformed")
    for contract, prefix, label in (
        (expected, HEAD_INDEX_EXPECTED_PREFIX, "expected"),
        (candidate, HEAD_INDEX_CANDIDATE_PREFIX, "candidate"),
    ):
        name = contract.get("name")
        if (
            not isinstance(name, str)
            or not name.startswith(prefix)
            or re.fullmatch(r"[.A-Za-z0-9_-]+", name) is None
        ):
            raise AtomicTransactionError(f"HEAD/index {label} name is unsafe")
    exchange_name = value.get("exchange_name")
    if (
        not isinstance(exchange_name, str)
        or not exchange_name.startswith(HEAD_INDEX_EXCHANGE_PREFIX)
        or re.fullmatch(r"[.A-Za-z0-9_-]+", exchange_name) is None
    ):
        raise AtomicTransactionError("HEAD/index exchange name is unsafe")
    displaced_name = value.get("displaced_name")
    if (
        not isinstance(displaced_name, str)
        or not displaced_name.startswith(HEAD_INDEX_DISPLACED_PREFIX)
        or re.fullmatch(r"[.A-Za-z0-9_-]+", displaced_name) is None
    ):
        raise AtomicTransactionError("HEAD/index displaced name is unsafe")
    return value


def _load_journal(directory_fd: int) -> tuple[dict[str, object], dict[str, object]]:
    content, identity = _read_entry_contract(directory_fd, HEAD_INDEX_TRANSACTION_JOURNAL)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AtomicTransactionError("HEAD/index recovery journal is unreadable") from exc
    return _validate_journal(value), _journal_contract(content, identity)


def _current_entry_kind(
    directory_fd: int,
    name: str,
    expected: dict[str, object],
    candidate: dict[str, object],
) -> str:
    contract = _entry_contract_or_none(directory_fd, name)
    if contract is None:
        return "missing"
    if _matches_contract(contract, candidate):
        return "candidate"
    if _matches_contract(contract, expected):
        return "expected"
    return "other"


def _preserve_private_entry(
    directory_fd: int,
    name: str,
    journal: dict[str, object],
) -> None:
    """Retain owned links or an unrelated raced inode under a private name."""
    expected = journal["expected_index"]
    candidate = journal["candidate_index"]
    if not isinstance(expected, dict) or not isinstance(candidate, dict):
        raise AtomicTransactionError("HEAD/index cleanup contracts are malformed")
    observed = _entry_contract_or_none(directory_fd, name)
    if observed is None:
        return
    if _matches_contract(observed, expected):
        _retain_matching_entry(directory_fd, name, expected)
        return
    if _matches_contract(observed, candidate):
        _retain_matching_entry(directory_fd, name, candidate)
        return
    displaced_name = str(journal["displaced_name"])
    observed_content, observed_identity = observed
    observed_contract = {
        "sha256": hashlib.sha256(observed_content).hexdigest(),
        "identity": observed_identity,
    }
    _, retained = retain_named_entry_no_replace(
        directory_fd,
        name,
        (observed_identity[0], observed_identity[1]),
        label=f"unrelated HEAD/index entry {name}",
        prefix=f"{displaced_name}-",
    )
    if not _matches_contract(retained, observed_contract):
        raise AtomicTransactionError(
            f"unrelated HEAD/index entry changed during private retention: {name}"
        )


def _cleanup_transaction_locations(
    directory_fd: int, journal: dict[str, object]
) -> None:
    expected = journal["expected_index"]
    candidate = journal["candidate_index"]
    if not isinstance(expected, dict) or not isinstance(candidate, dict):
        raise AtomicTransactionError("transaction cleanup contract is malformed")
    for name in (
        str(expected["name"]),
        str(candidate["name"]),
        str(journal["exchange_name"]),
        "index.lock",
    ):
        _preserve_private_entry(directory_fd, name, journal)


def recover_head_index_transaction(
    git_dir: str | os.PathLike[str],
    *,
    read_head: Callable[[], str],
    update_head: Callable[[str, str], None],
) -> str:
    """Reconcile one interrupted durable HEAD/index transaction deterministically."""
    directory_fd = os.open(
        git_dir, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        try:
            journal, journal_file = _load_journal(directory_fd)
        except FileNotFoundError:
            return "none"
        expected = journal["expected_index"]
        candidate = journal["candidate_index"]
        if not isinstance(expected, dict) or not isinstance(candidate, dict):
            raise AtomicTransactionError("HEAD/index recovery contracts are malformed")
        base_head = str(journal["base_head"])
        candidate_head = str(journal["candidate_head"])
        head = read_head()
        index_kind = _current_entry_kind(directory_fd, "index", expected, candidate)
        exchange_name = str(journal["exchange_name"])
        exchange_kind = _current_entry_kind(
            directory_fd, exchange_name, expected, candidate
        )

        if head == base_head:
            allow_unrelated_index = False
            if index_kind == "candidate":
                if exchange_kind in {"expected", "other"}:
                    # The private exchange name is the exact inode that was
                    # canonical immediately before our atomic swap.  Swapping
                    # it back distinguishes a pre-exchange index race (other)
                    # from a later lock-name race (expected).
                    allow_unrelated_index = exchange_kind == "other"
                    rename_exchange(
                        directory_fd, "index", directory_fd, exchange_name
                    )
                    os.fsync(directory_fd)
                else:
                    expected_name = str(expected["name"])
                    if _current_entry_kind(
                        directory_fd, expected_name, expected, candidate
                    ) != "expected":
                        raise AtomicTransactionError(
                            "recovery cannot restore the reviewed index"
                        )
                    rename_exchange(
                        directory_fd, "index", directory_fd, expected_name
                    )
                    os.fsync(directory_fd)
            elif index_kind == "other":
                if exchange_kind == "expected":
                    # The canonical index was replaced after our exchange.
                    # Restore the exact displaced reviewed inode and retain the
                    # unrelated replacement under the exchange name.
                    rename_exchange(
                        directory_fd, "index", directory_fd, exchange_name
                    )
                    os.fsync(directory_fd)
                elif exchange_kind == "candidate":
                    # A pre-exchange destination race was already swapped
                    # back.  The third-party index owns the canonical name and
                    # must remain there unchanged.
                    allow_unrelated_index = True
                else:
                    raise AtomicTransactionError(
                        "recovery cannot order an unrelated index mutation"
                    )
            elif index_kind == "missing":
                expected_name = str(expected["name"])
                if _current_entry_kind(
                    directory_fd, expected_name, expected, candidate
                ) != "expected":
                    raise AtomicTransactionError(
                        "recovery cannot recreate the reviewed index"
                    )
                _link_matching_entry(directory_fd, expected_name, "index", expected)
            elif index_kind != "expected":
                raise AtomicTransactionError("recovery cannot identify the current Git index")
            restored_kind = _current_entry_kind(directory_fd, "index", expected, candidate)
            if restored_kind != "expected" and not (
                allow_unrelated_index and restored_kind == "other"
            ):
                raise AtomicTransactionError("recovery did not restore the reviewed index")
            _cleanup_transaction_locations(directory_fd, journal)
            _retain_matching_entry(
                directory_fd, HEAD_INDEX_TRANSACTION_JOURNAL, journal_file
            )
            return "rolled_back"

        if head == candidate_head:
            if index_kind != "candidate":
                candidate_name = str(candidate["name"])
                if _current_entry_kind(
                    directory_fd, candidate_name, expected, candidate
                ) != "candidate":
                    raise AtomicTransactionError(
                        "recovery cannot find the committed index candidate"
                    )
                if index_kind == "missing":
                    _link_matching_entry(
                        directory_fd, candidate_name, "index", candidate
                    )
                else:
                    rename_exchange(
                        directory_fd, "index", directory_fd, candidate_name
                    )
                    os.fsync(directory_fd)
                if _current_entry_kind(directory_fd, "index", expected, candidate) != "candidate":
                    raise AtomicTransactionError("recovery could not install the committed index")
            _cleanup_transaction_locations(directory_fd, journal)
            _retain_matching_entry(
                directory_fd, HEAD_INDEX_TRANSACTION_JOURNAL, journal_file
            )
            return "rolled_forward"

        raise AtomicTransactionError("HEAD moved outside the interrupted transaction")
    finally:
        os.close(directory_fd)


def publish_head_index_transaction(
    git_dir: str | os.PathLike[str],
    *,
    base_head: str,
    candidate_head: str,
    expected_index_sha256: str,
    expected_index_identity: list[int],
    candidate_index_path: str | os.PathLike[str],
    read_head: Callable[[], str],
    update_head: Callable[[str, str], None],
    mutation_tracker: dict[str, object] | None = None,
    fault_injector: Callable[[str], None] | None = None,
    candidate_index_sha256: str | None = None,
    candidate_index_identity: list[int] | None = None,
) -> None:
    """Publish one reviewed HEAD/index pair with durable crash recovery."""
    if (
        HEX_OBJECT_ID.fullmatch(base_head) is None
        or HEX_OBJECT_ID.fullmatch(candidate_head) is None
        or HEX_SHA256.fullmatch(expected_index_sha256) is None
        or len(expected_index_identity) != 6
        or not all(isinstance(item, int) and item >= 0 for item in expected_index_identity)
        or ((candidate_index_sha256 is None) != (candidate_index_identity is None))
        or (
            candidate_index_sha256 is not None
            and HEX_SHA256.fullmatch(candidate_index_sha256) is None
        )
        or (
            candidate_index_identity is not None
            and (
                len(candidate_index_identity) != 6
                or not all(
                    isinstance(item, int) and item >= 0
                    for item in candidate_index_identity
                )
            )
        )
    ):
        raise AtomicTransactionError("HEAD/index transaction input is malformed")
    candidate_fd = os.open(
        candidate_index_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        candidate_content, source_identity = _read_descriptor_contract(candidate_fd)
    finally:
        os.close(candidate_fd)
    if candidate_index_sha256 is not None and (
        hashlib.sha256(candidate_content).hexdigest() != candidate_index_sha256
        or source_identity != candidate_index_identity
    ):
        raise AtomicTransactionError("shared index candidate raced after preparation")
    candidate_name = HEAD_INDEX_CANDIDATE_PREFIX + secrets.token_hex(12)
    transaction_token = candidate_name.removeprefix(HEAD_INDEX_CANDIDATE_PREFIX)
    expected_name = HEAD_INDEX_EXPECTED_PREFIX + transaction_token
    exchange_name = HEAD_INDEX_EXCHANGE_PREFIX + transaction_token
    displaced_name = HEAD_INDEX_DISPLACED_PREFIX + transaction_token
    directory_fd = os.open(
        git_dir, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    )
    owned_candidate_fd = -1
    journal_created = False
    candidate_contract: dict[str, object] | None = None
    expected_contract: dict[str, object] | None = None
    try:
        if _entry_contract_or_none(directory_fd, HEAD_INDEX_TRANSACTION_JOURNAL) is not None:
            raise AtomicTransactionError(
                "an interrupted HEAD/index transaction must be recovered first"
            )
        owned_candidate_fd, candidate_identity = _create_entry(
            directory_fd,
            candidate_name,
            candidate_content,
            stat.S_IMODE(source_identity[3]),
        )
        candidate_contract = {
            "name": candidate_name,
            "sha256": hashlib.sha256(candidate_content).hexdigest(),
            "identity": candidate_identity,
        }
        reviewed_expected = {
            "sha256": expected_index_sha256,
            "identity": expected_index_identity,
        }
        expected_source_fd = os.open(
            "index",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            current = _read_descriptor_contract(expected_source_fd)
            if not _matches_contract(current, reviewed_expected, exact_metadata=True):
                raise AtomicTransactionError(
                    "shared Git index raced after publication review"
                )
            if fault_injector is not None:
                fault_injector("before_expected_index_link")
            os.link(
                "index",
                expected_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            os.fsync(directory_fd)
            expected_content, expected_identity = _read_entry_contract(
                directory_fd, expected_name
            )
            bound_content, bound_identity = _read_descriptor_contract(
                expected_source_fd
            )
            canonical_content, canonical_identity = _read_entry_contract(
                directory_fd, "index"
            )
            if (
                expected_content != current[0]
                or bound_content != current[0]
                or canonical_content != current[0]
                or expected_identity[:2] != current[1][:2]
                or bound_identity[:2] != current[1][:2]
                or canonical_identity[:2] != current[1][:2]
                or expected_identity[:4] != bound_identity[:4]
                or expected_identity[:4] != canonical_identity[:4]
            ):
                raise AtomicTransactionError(
                    "reviewed Git index backup rebound to another inode"
                )
        finally:
            os.close(expected_source_fd)
        expected_contract = {
            "name": expected_name,
            "sha256": expected_index_sha256,
            "identity": expected_identity,
        }
        journal = {
            "version": 3,
            "base_head": base_head,
            "candidate_head": candidate_head,
            "expected_index": expected_contract,
            "candidate_index": candidate_contract,
            "exchange_name": exchange_name,
            "displaced_name": displaced_name,
        }
        journal_content = (
            json.dumps(journal, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        journal_fd, _ = _create_entry(
            directory_fd, HEAD_INDEX_TRANSACTION_JOURNAL, journal_content, 0o600
        )
        os.close(journal_fd)
        journal_created = True
        if fault_injector is not None:
            fault_injector("journal_durable")

        if read_head() != base_head:
            raise AtomicTransactionError("HEAD raced after publication review")
        current = _read_entry_contract(directory_fd, "index")
        expected = journal["expected_index"]
        candidate = journal["candidate_index"]
        if not isinstance(expected, dict) or not isinstance(candidate, dict):
            raise AtomicTransactionError("HEAD/index transaction contracts are malformed")
        if not _matches_contract(current, expected, exact_metadata=True):
            raise AtomicTransactionError("shared Git index raced after publication review")
        _link_matching_entry(directory_fd, candidate_name, "index.lock", candidate)
        if fault_injector is not None:
            fault_injector("index_lock_durable")
        _link_matching_entry(
            directory_fd, candidate_name, exchange_name, candidate
        )

        if read_head() != base_head:
            raise AtomicTransactionError("HEAD raced while the index candidate was locked")
        current = _read_entry_contract(directory_fd, "index")
        if not _matches_contract(current, expected, exact_metadata=True):
            raise AtomicTransactionError("shared Git index raced while candidate was locked")
        if fault_injector is not None:
            fault_injector("before_index_exchange")
        # Keep index.lock as the cooperative Git lock.  A distinct private
        # exchange name records the exact inode displaced from canonical
        # index, so an index race and an index.lock race remain distinguishable
        # during crash recovery.
        rename_exchange(directory_fd, exchange_name, directory_fd, "index")
        os.fsync(directory_fd)
        if mutation_tracker is not None:
            mutation_tracker["index_updated"] = True
        if fault_injector is not None:
            fault_injector("index_exchanged")

        installed = _read_entry_contract(directory_fd, "index")
        displaced = _read_entry_contract(directory_fd, exchange_name)
        locked = _read_entry_contract(directory_fd, "index.lock")
        if (
            not _matches_contract(installed, candidate)
            or not _matches_contract(displaced, expected)
            or not _matches_contract(locked, candidate)
        ):
            # Exchange back preserves whichever concurrent inode was displaced.
            if _matches_contract(installed, candidate) and displaced is not None:
                rename_exchange(directory_fd, "index", directory_fd, exchange_name)
                os.fsync(directory_fd)
                if mutation_tracker is not None:
                    mutation_tracker["index_updated"] = False
            raise AtomicTransactionError("shared Git index changed at the atomic exchange")
        update_head(candidate_head, base_head)
        if mutation_tracker is not None:
            mutation_tracker["head_updated"] = True
        if fault_injector is not None:
            fault_injector("head_updated")
        if (
            read_head() != candidate_head
            or _current_entry_kind(directory_fd, "index", expected, candidate)
            != "candidate"
            or _current_entry_kind(
                directory_fd, "index.lock", expected, candidate
            )
            != "candidate"
        ):
            raise AtomicTransactionError("published HEAD/index pair failed verification")
        _cleanup_transaction_locations(directory_fd, journal)
        _, journal_file = _load_journal(directory_fd)
        _retain_matching_entry(
            directory_fd, HEAD_INDEX_TRANSACTION_JOURNAL, journal_file
        )
        journal_created = False
    except Exception as original:
        if journal_created:
            try:
                recovery_status = recover_head_index_transaction(
                    git_dir, read_head=read_head, update_head=update_head
                )
                if mutation_tracker is not None:
                    rolled_forward = recovery_status == "rolled_forward"
                    mutation_tracker["head_updated"] = rolled_forward
                    mutation_tracker["index_updated"] = rolled_forward
                journal_created = False
            except Exception as recovery_error:
                raise AtomicTransactionError(
                    f"HEAD/index transaction requires recovery: {recovery_error}"
                ) from original
        else:
            for name, contract in (
                (candidate_name, candidate_contract),
                (expected_name, expected_contract),
            ):
                try:
                    if contract is None:
                        observed = _entry_contract_or_none(directory_fd, name)
                        if observed is None:
                            continue
                        observed_content, observed_identity = observed
                        retain_named_entry_no_replace(
                            directory_fd,
                            name,
                            (observed_identity[0], observed_identity[1]),
                            label=f"unverified transaction entry {name}",
                        )
                    else:
                        _retain_matching_entry(directory_fd, name, contract)
                except (FileNotFoundError, AtomicTransactionError, OSError):
                    pass
        raise
    finally:
        if owned_candidate_fd >= 0:
            os.close(owned_candidate_fd)
        os.close(directory_fd)


def fsync_after_rename(
    source_directory_fd: int, destination_directory_fd: int
) -> None:
    """Persist the new name before persisting removal of the source name."""
    os.fsync(destination_directory_fd)
    source = os.fstat(source_directory_fd)
    destination = os.fstat(destination_directory_fd)
    if (source.st_dev, source.st_ino) != (destination.st_dev, destination.st_ino):
        os.fsync(source_directory_fd)


def mkdir_durable(name: str, mode: int, *, parent_fd: int) -> None:
    """Create a directory and persist its parent dirent before it is used."""
    os.mkdir(name, mode, dir_fd=parent_fd)
    os.fsync(parent_fd)


def link_no_replace_durable(
    source_directory_fd: int,
    source_name: str,
    destination_directory_fd: int,
    destination_name: str,
) -> None:
    """Create a same-inode retained name without replacing another entry."""
    os.link(
        source_name,
        destination_name,
        src_dir_fd=source_directory_fd,
        dst_dir_fd=destination_directory_fd,
        follow_symlinks=False,
    )
    os.fsync(destination_directory_fd)


def open_absolute_directory_chain(
    value: str | os.PathLike[str],
) -> tuple[int, tuple[tuple[int, int], ...]]:
    """Open every component of an absolute directory without following symlinks."""
    path = Path(value)
    # macOS exposes two immutable system aliases for its real data-volume
    # directories. Canonicalize only those aliases; every task-controlled
    # component below them is still opened one-by-one with O_NOFOLLOW.
    if sys.platform == "darwin" and len(path.parts) > 1:
        if path.parts[1] == "var":
            path = Path("/private").joinpath(*path.parts[1:])
        elif path.parts[1] == "tmp":
            path = Path("/private/tmp").joinpath(*path.parts[2:])
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise OSError(errno.EINVAL, "directory path is not a normalized absolute path")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    opened: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        opened.append(os.open(os.sep, flags))
        root = os.fstat(opened[-1])
        identities.append((root.st_dev, root.st_ino))
        for component in path.parts[1:]:
            opened.append(os.open(component, flags, dir_fd=opened[-1]))
            metadata = os.fstat(opened[-1])
            identities.append((metadata.st_dev, metadata.st_ino))
        result = opened.pop()
        for descriptor in reversed(opened):
            os.close(descriptor)
        return result, tuple(identities)
    except Exception:
        for descriptor in reversed(opened):
            os.close(descriptor)
        raise


def verify_rename_no_replace(directory_fd: int) -> None:
    """Verify no-replace support while retaining the private probe inode."""
    token = secrets.token_hex(8)
    source_name = f".rename-no-replace-source-{token}"
    descriptor = os.open(
        source_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    metadata = os.fstat(descriptor)
    os.close(descriptor)
    retain_named_entry_no_replace(
        directory_fd,
        source_name,
        (metadata.st_dev, metadata.st_ino),
        label="rename capability probe",
        prefix=".rename-no-replace-retained-",
    )
