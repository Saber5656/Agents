#!/usr/bin/env python3
"""Validate collection output paths and hashes before publication privileges exist."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
import sys
import urllib.parse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional, Tuple

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
SUMMARY_NAME = re.compile(r"^SUMMARY-IT-NEWS-(\d{4})-(\d{2})-(\d{2})(?:-\d+)?\.md$")
ADVISORY_NAME = re.compile(r"^Personal-Vulnerability-Advisory-(\d{4})-(\d{2})-(\d{2})(?:-\d+)?\.md$")
ALLOWED_COVERAGE_STATUS = {"取得済み", "対象期間記事なし", "アクセス制約"}
ACCESS_CONTROL_REASON = re.compile(
    r"login|log-in|paywall|subscription|robots|captcha|ログイン|購読|有料|ロボット",
    re.IGNORECASE,
)
CONSTRAINT_REASON = {
    "login": re.compile(r"login|log-in|sign-in|ログイン", re.IGNORECASE),
    "paywall": re.compile(r"paywall|subscription|subscribe|購読|有料|会員限定", re.IGNORECASE),
    "captcha": re.compile(r"captcha|キャプチャ", re.IGNORECASE),
    "robots": re.compile(r"robots|ロボット", re.IGNORECASE),
}
PUBLIC_HOST_LABEL = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.IGNORECASE
)
ISO_PUBLICATION_DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?"
    r"(?:[Zz]|[+-]\d{2}:?\d{2})?)?\Z"
)
RFC_PUBLICATION_DATE = re.compile(
    r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),[ ]+)?"
    r"\d{1,2}[ ]+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[ ]+"
    r"\d{4}[ ]+\d{2}:\d{2}(?::\d{2})?[ ]+(?:[+-]\d{4}|[A-Z]{1,5})\Z",
    re.IGNORECASE,
)

class ValidationError(RuntimeError):
    """Represent a collection result that must block publication."""


def file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    """Return the metadata fields that must remain stable across one read."""
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def digest_fd(
    descriptor: int, maximum_bytes: int = MAX_ARTIFACT_BYTES
) -> tuple[str, bytes]:
    """Hash and retain bounded bytes from one already-open descriptor."""
    hasher = hashlib.sha256()
    content = bytearray()
    while chunk := os.read(descriptor, 1024 * 1024):
        content.extend(chunk)
        if len(content) > maximum_bytes:
            raise ValidationError("artifact grew beyond the allowed size")
        hasher.update(chunk)
    return hasher.hexdigest(), bytes(content)


def validate_artifact(
    path_value: str,
    expected_hash: str,
    staging_root: Path,
    earliest_mtime: int,
    expected_date: str,
    role: str,
) -> str:
    """Require a same-run regular non-symlink file below staging root."""
    path = Path(path_value)
    if not path.is_absolute():
        raise ValidationError("artifact is not an absolute regular non-symlink file")
    root = Path(os.path.abspath(staging_root))
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValidationError("artifact escapes run staging root") from exc
    if not relative.parts or ".." in relative.parts:
        raise ValidationError("artifact escapes run staging root")
    expected_pattern = SUMMARY_NAME if role == "summary" else ADVISORY_NAME
    match = expected_pattern.fullmatch(path.name)
    if not match or "-".join(match.groups()) != expected_date:
        raise ValidationError("artifact filename does not match current JST run date")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    opened = []
    directory_fd = os.open(root, directory_flags)
    opened.append(directory_fd)
    try:
        for component in relative.parts[:-1]:
            directory_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            opened.append(directory_fd)
        descriptor = os.open(relative.name, flags, dir_fd=directory_fd)
        opened.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValidationError("artifact is not a regular file")
        if before.st_size <= 0 or before.st_size > MAX_ARTIFACT_BYTES:
            raise ValidationError("artifact size is outside the allowed range")
        if before.st_mtime < earliest_mtime:
            raise ValidationError("artifact predates this collection run")
        actual_hash, content = digest_fd(descriptor)
        after = os.fstat(descriptor)
        if file_identity(before) != file_identity(after) or len(content) != before.st_size:
            raise ValidationError("artifact changed while it was read")
        if actual_hash != expected_hash:
            raise ValidationError("artifact SHA-256 mismatch")
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("artifact is not UTF-8 text") from exc
    finally:
        for opened_descriptor in reversed(opened):
            os.close(opened_descriptor)
    return decoded


def validate_advisory_reference(
    advisory: str,
    summary_name: str,
    summary_sha256: str,
    staging_root: Path,
) -> None:
    """Require one exact path-free reference and reject private runtime paths."""
    expected = (
        f"- 入力ニュース: {summary_name} "
        f"(same-run SHA-256: {summary_sha256})"
    )
    references = [
        line for line in advisory.splitlines() if line.startswith("- 入力ニュース:")
    ]
    if references != [expected]:
        raise ValidationError("advisory summary reference is missing or inconsistent")
    private_paths = {str(Path(os.path.abspath(staging_root))), str(Path.home())}
    if any(private_path in advisory for private_path in private_paths):
        raise ValidationError("advisory contains a machine-specific path")


def read_regular_nofollow(path: Path, label: str) -> bytes:
    """Read one stable bounded regular file without following its final component."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_ARTIFACT_BYTES
        ):
            raise ValidationError(f"{label} is not a bounded regular file")
        content = bytearray()
        while chunk := os.read(descriptor, 65536):
            content.extend(chunk)
            if len(content) > MAX_ARTIFACT_BYTES:
                raise ValidationError(f"{label} exceeds its size bound")
        after = os.fstat(descriptor)
        if file_identity(before) != file_identity(after) or len(content) != before.st_size:
            raise ValidationError(f"{label} changed while it was read")
        return bytes(content)
    finally:
        os.close(descriptor)


def write_exclusive_bytes(
    directory_fd: int, name: str, content: bytes, label: str
) -> None:
    """Create one private immutable-by-contract output in a bound directory."""
    if (
        not name
        or "/" in name
        or "\\" in name
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in name)
    ):
        raise ValidationError(f"{label} filename is invalid")
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ValidationError(f"could not write {label}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def canonical_json_bytes(value: object) -> bytes:
    """Encode one deterministic JSON audit artifact."""
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sealed_coverage_authority(
    catalog_path: Path, manifest_path: Path, resolutions_path: Path
) -> tuple[dict[str, str], set[str], dict[str, dict[str, str]]]:
    """Return sealed row classes and the exact evidence bytes consumed."""
    catalog, catalog_bytes = load_source_catalog_snapshot(catalog_path)
    evidence, manifest_bytes = load_source_manifest_snapshot(
        manifest_path, catalog_bytes
    )
    resolutions, _, resolutions_bytes = load_verified_resolutions_snapshot(
        resolutions_path
    )
    if set(evidence) != set(catalog):
        raise ValidationError("source manifest does not match the catalog")
    unresolved = {
        name
        for name, item in evidence.items()
        if item.get("status") == "needs_search_fallback"
    }
    if not set(resolutions).issubset(unresolved):
        raise ValidationError("verified fallback evidence is outside unresolved scope")
    constraints: dict[str, str] = {}
    retrieved: set[str] = set()
    for name, item in evidence.items():
        source_status = item.get("status")
        if source_status == "fetched":
            retrieved.add(name)
            continue
        if source_status == "access_constraint":
            constraint = item.get("constraint")
            if constraint not in CONSTRAINT_REASON:
                raise ValidationError("source constraint evidence is invalid")
            constraints[name] = str(constraint)
            continue
        resolution = resolutions.get(name)
        if source_status != "needs_search_fallback" or not resolution:
            raise ValidationError("source lacks a verified coverage resolution")
        if resolution.get("status") == "verified_access_constraint":
            constraint = resolution.get("constraint")
            if constraint not in CONSTRAINT_REASON:
                raise ValidationError("verified fallback constraint is invalid")
            constraints[name] = str(constraint)
        elif resolution.get("status") == "verified_fallback":
            retrieved.add(name)
        else:
            raise ValidationError("verified fallback status is invalid")
    if set(constraints) & retrieved or set(constraints) | retrieved != set(catalog):
        raise ValidationError("sealed source coverage authority is incomplete")
    bindings = {
        "catalog": {
            "path": str(catalog_path),
            "sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        },
        "manifest": {
            "path": str(manifest_path),
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
        "verified_resolutions": {
            "path": str(resolutions_path),
            "sha256": hashlib.sha256(resolutions_bytes).hexdigest(),
        },
    }
    return constraints, retrieved, bindings


def sealed_constraint_reasons(
    catalog_path: Path, manifest_path: Path, resolutions_path: Path
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Compatibility wrapper returning only sealed constraint reasons."""
    constraints, _, bindings = sealed_coverage_authority(
        catalog_path, manifest_path, resolutions_path
    )
    return constraints, bindings


def project_table_cell(body: str, cell_index: int, replacement: str) -> str:
    """Replace one Markdown table cell while preserving its surrounding spacing."""
    delimiters = [
        position for position, character in enumerate(body) if character == "|"
    ]
    if len(delimiters) != 8 or not 0 <= cell_index < 7:
        raise ValidationError("source coverage row is malformed")
    cell_start = delimiters[cell_index] + 1
    cell_end = delimiters[cell_index + 1]
    raw_cell = body[cell_start:cell_end]
    leading_length = len(raw_cell) - len(raw_cell.lstrip())
    trailing_length = len(raw_cell) - len(raw_cell.rstrip())
    leading = raw_cell[:leading_length]
    trailing = raw_cell[len(raw_cell) - trailing_length:] if trailing_length else ""
    projected = leading + replacement + trailing
    return body[:cell_start] + projected + body[cell_end:]


def canonicalize_summary_coverage(
    summary: str, constraints: dict[str, str], retrieved: set[str]
) -> tuple[str, list[dict[str, str]], list[dict[str, object]]]:
    """Project only sealed constraint reasons and count-implied normal statuses."""
    heading = "## 確認済みサイト一覧"
    lines = summary.splitlines(keepends=True)
    heading_indexes = [
        index for index, line in enumerate(lines) if line.rstrip("\r\n") == heading
    ]
    if len(heading_indexes) != 1:
        raise ValidationError("summary source coverage section is missing or duplicated")
    start = heading_indexes[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].rstrip("\r\n").startswith("## "):
            end = index
            break
    authority = set(constraints) | retrieved
    if set(constraints) & retrieved:
        raise ValidationError("source coverage projection authority overlaps")
    seen: set[str] = set()
    reason_corrections: list[dict[str, str]] = []
    status_corrections: list[dict[str, object]] = []
    for index in range(start, end):
        body = lines[index].rstrip("\r\n")
        line_ending = lines[index][len(body):]
        if not body.startswith("|") or not body.endswith("|"):
            continue
        cells = [cell.strip() for cell in body.strip("|").split("|")]
        if len(cells) != 7:
            continue
        name = cells[0]
        if name not in authority:
            continue
        if name in seen:
            raise ValidationError("summary contains a duplicate authorized source row")
        seen.add(name)
        if name in constraints:
            if cells[2] != "アクセス制約":
                raise ValidationError("constrained source row has a non-constraint status")
            sealed_reason = constraints[name]
            supplied_reason = cells[6]
            if supplied_reason != sealed_reason:
                body = project_table_cell(body, 6, sealed_reason)
                lines[index] = body + line_ending
                reason_corrections.append(
                    {
                        "source": name,
                        "supplied": supplied_reason,
                        "sealed": sealed_reason,
                    }
                )
            continue
        supplied_status = cells[2]
        if supplied_status not in {"取得済み", "対象期間記事なし"}:
            raise ValidationError("retrieved source row has an unauthorized status")
        try:
            item_count = int(cells[5])
        except ValueError as exc:
            raise ValidationError("retrieved source row has an invalid item count") from exc
        if item_count < 0:
            raise ValidationError("retrieved source row has an invalid item count")
        sealed_status = "取得済み" if item_count > 0 else "対象期間記事なし"
        if supplied_status != sealed_status:
            body = project_table_cell(body, 2, sealed_status)
            lines[index] = body + line_ending
            status_corrections.append(
                {
                    "source": name,
                    "supplied": supplied_status,
                    "sealed": sealed_status,
                    "item_count": item_count,
                }
            )
    if seen != authority:
        raise ValidationError("summary lacks an authorized source coverage row")
    return "".join(lines), reason_corrections, status_corrections


def canonicalize_summary_constraint_reasons(
    summary: str, constraints: dict[str, str]
) -> tuple[str, list[dict[str, str]]]:
    """Compatibility wrapper for the original constraint-only projection."""
    projected, corrections, _ = canonicalize_summary_coverage(
        summary, constraints, set()
    )
    return projected, corrections


def update_advisory_summary_reference(
    advisory: str,
    summary_name: str,
    raw_summary_sha256: str,
    canonical_summary_sha256: str,
) -> str:
    """Update exactly one already-validated same-run summary digest reference."""
    old_reference = (
        f"- 入力ニュース: {summary_name} "
        f"(same-run SHA-256: {raw_summary_sha256})"
    )
    new_reference = (
        f"- 入力ニュース: {summary_name} "
        f"(same-run SHA-256: {canonical_summary_sha256})"
    )
    lines = advisory.splitlines(keepends=True)
    matches = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n") == old_reference
    ]
    if len(matches) != 1:
        raise ValidationError("advisory summary reference cannot be canonicalized")
    index = matches[0]
    line_ending = lines[index][len(lines[index].rstrip("\r\n")):]
    lines[index] = new_reference + line_ending
    return "".join(lines)


def load_source_catalog_snapshot(
    path: Path,
) -> tuple[dict[str, dict[str, object]], bytes]:
    """Load one stable catalog snapshot and retain the exact consumed bytes."""
    content = read_regular_nofollow(path, "source catalog")
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValidationError("source catalog is invalid")
    sources = payload.get("sources")
    if payload.get("version") != 1 or not isinstance(sources, list) or not sources:
        raise ValidationError("source catalog is invalid")
    expected: dict[str, dict[str, object]] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ValidationError("source catalog entry is invalid")
        name = source.get("name")
        tier = source.get("tier")
        if not isinstance(name, str) or not name or name in expected or tier not in (1, 2):
            raise ValidationError("source catalog entry is invalid")
        expected[name] = source
    return expected, content


def load_source_catalog(path: Path) -> dict[str, dict[str, object]]:
    """Load the reviewed source names and tiers used by this runtime."""
    return load_source_catalog_snapshot(path)[0]


def load_source_manifest_snapshot(
    path: Path, catalog_bytes: bytes
) -> tuple[dict[str, dict[str, object]], bytes]:
    """Bind one stable manifest snapshot to exact consumed catalog bytes."""
    content = read_regular_nofollow(path, "source manifest")
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValidationError("source manifest is invalid")
    if payload.get("catalog_sha256") != hashlib.sha256(catalog_bytes).hexdigest():
        raise ValidationError("source manifest catalog digest mismatch")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ValidationError("source manifest is invalid")
    evidence: dict[str, dict[str, object]] = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("name"), str):
            raise ValidationError("source manifest entry is invalid")
        name = source["name"]
        if name in evidence:
            raise ValidationError("source manifest contains a duplicate source")
        evidence[name] = source
    return evidence, content


def load_source_manifest(path: Path, catalog_path: Path) -> dict[str, dict[str, object]]:
    """Bind source evidence to one stable catalog and manifest snapshot."""
    _, catalog_bytes = load_source_catalog_snapshot(catalog_path)
    return load_source_manifest_snapshot(path, catalog_bytes)[0]


def load_verified_resolutions_snapshot(
    path: Path,
) -> tuple[dict[str, dict[str, object]], dict[str, object], bytes]:
    """Load one stable verified-resolution snapshot and its parsed payload."""
    content = read_regular_nofollow(path, "verified fallback evidence")
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValidationError("verified fallback evidence is invalid")
    resolutions = payload.get("resolutions")
    if payload.get("version") != 1 or not isinstance(resolutions, list):
        raise ValidationError("verified fallback evidence is invalid")
    result: dict[str, dict[str, object]] = {}
    for item in resolutions:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValidationError("verified fallback entry is invalid")
        if item["name"] in result:
            raise ValidationError("verified fallback evidence contains a duplicate source")
        result[item["name"]] = item
    return result, payload, content


def load_verified_resolutions(path: Path) -> dict[str, dict[str, object]]:
    """Load fallback URLs that the trusted helper independently fetched."""
    return load_verified_resolutions_snapshot(path)[0]


def load_verified_date_evidence_payload(
    payload: dict[str, object],
) -> dict[str, list[dict[str, object]]]:
    """Group trusted article-date evidence from an already-bound snapshot."""
    items = payload.get("date_evidence")
    if not isinstance(items, list):
        raise ValidationError("verified publication-date evidence is invalid")
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValidationError("verified publication-date entry is invalid")
        published = item.get("published_date")
        if not isinstance(published, str) or not published:
            raise ValidationError("verified publication-date entry lacks dates")
        grouped.setdefault(str(item["name"]), []).append(item)
    return grouped


def load_verified_date_evidence(path: Path) -> dict[str, list[dict[str, object]]]:
    """Group trusted article-date evidence by catalog source."""
    _, payload, _ = load_verified_resolutions_snapshot(path)
    return load_verified_date_evidence_payload(payload)


def parse_publication_date(value: object) -> Optional[date]:
    """Parse only complete collector-approved publication date strings."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 200 or cleaned != value:
        return None
    try:
        if ISO_PUBLICATION_DATE.fullmatch(cleaned):
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00").replace("z", "+00:00"))
        elif RFC_PUBLICATION_DATE.fullmatch(cleaned):
            parsed = parsedate_to_datetime(cleaned)
        else:
            numeric = re.fullmatch(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", cleaned)
            if numeric is None:
                return None
            return date(*(int(part) for part in numeric.groups()))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone(timedelta(hours=9))).date()


def parsed_public_url(value: object) -> Optional[urllib.parse.SplitResult]:
    """Strictly parse one absolute public HTTP(S) URL without network access."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(
            character.isspace()
            or ord(character) < 0x20
            or ord(character) == 0x7F
            or character == "\\"
            for character in value
        )
    ):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
        or (port is None and parsed.netloc.endswith(":"))
        or "%" in hostname
    ):
        return None
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        labels = ascii_hostname.split(".")
        if (
            len(ascii_hostname) > 253
            or any(not PUBLIC_HOST_LABEL.fullmatch(label) for label in labels)
        ):
            return None
    return parsed


def canonical_url(value: str) -> str:
    """Normalize one independently validated article URL for deduplication."""
    parsed = parsed_public_url(value)
    if parsed is None:
        raise ValidationError("article URL is invalid")
    path = parsed.path.rstrip("/") or "/"
    host = (parsed.hostname or "").lower()
    host = f"[{host}]" if ":" in host else host
    port = parsed.port
    default_port = (parsed.scheme.lower() == "https" and port == 443) or (
        parsed.scheme.lower() == "http" and port == 80
    )
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), netloc, path, parsed.query, "")
    )


def source_hosts(source: dict[str, object]) -> set[str]:
    """Mirror the collector's reviewed www/non-www host aliases."""
    hosts: set[str] = set()
    for value in (source.get("feed_url"), source.get("page_url")):
        if not isinstance(value, str) or not value:
            continue
        host = urllib.parse.urlsplit(value).hostname
        if not host:
            continue
        hosts.add(host)
        hosts.add(host[4:] if host.startswith("www.") else f"www.{host}")
    return hosts


def is_allowed_source_url(value: object, hosts: set[str]) -> bool:
    """Apply the collector's transport restrictions to sealed final URLs."""
    parsed = parsed_public_url(value)
    if parsed is None:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname in hosts
        and parsed.port in (None, 443)
        and not parsed.fragment
    )


def is_allowed_extract_url(value: object, hosts: set[str]) -> bool:
    """Bind sealed feed/HTML candidates to a catalog source host."""
    parsed = parsed_public_url(value)
    if parsed is None or parsed.hostname not in hosts or parsed.fragment:
        return False
    return (
        parsed.scheme.lower() == "https" and parsed.port in (None, 443)
    ) or (
        parsed.scheme.lower() == "http" and parsed.port in (None, 80)
    )


def sealed_extract_entries(
    manifest_path: Path, source_evidence: dict[str, object], hosts: set[str]
) -> tuple[str, dict[str, Optional[date]]]:
    """Load every canonical article entry and optional date from a sealed extract."""
    filename = source_evidence.get("extract_file")
    if not isinstance(filename, str):
        raise ValidationError("source extract filename is invalid")
    if Path(filename).name != filename:
        raise ValidationError("source extract filename is invalid")
    path = manifest_path.parent / filename
    content = read_regular_nofollow(path, "source extract")
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValidationError("source extract format is invalid")
    extract_format = payload.get("format")
    if extract_format not in {"feed", "html_links"}:
        raise ValidationError("source extract format is invalid")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValidationError("source extract entries are invalid")
    sealed: dict[str, Optional[date]] = {}
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValidationError("source extract entry is invalid")
        published = parse_publication_date(item.get("published"))
        url = item.get("url")
        if isinstance(url, str):
            if not is_allowed_extract_url(url, hosts):
                raise ValidationError("source extract entry URL is invalid")
            key = canonical_url(url)
        else:
            key = f"undirected:{index}"
        if key in sealed:
            raise ValidationError("source extract contains a duplicate article URL")
        sealed[key] = published
    return str(extract_format), sealed


def validate_source_coverage(
    summary: str, catalog_path: Path, manifest_path: Path, resolutions_path: Path,
    run_date: date,
) -> None:
    """Require every audit row to match deterministic collector evidence."""
    expected, catalog_bytes = load_source_catalog_snapshot(catalog_path)
    evidence, _ = load_source_manifest_snapshot(manifest_path, catalog_bytes)
    resolutions, resolutions_payload, _ = load_verified_resolutions_snapshot(
        resolutions_path
    )
    supplemental_dates = load_verified_date_evidence_payload(resolutions_payload)
    if set(evidence) != set(expected):
        raise ValidationError("source manifest does not match the catalog")
    unresolved = {
        name for name, item in evidence.items()
        if item.get("status") == "needs_search_fallback"
    }
    if not set(resolutions).issubset(unresolved):
        raise ValidationError("verified fallback evidence is outside unresolved scope")
    if not set(supplemental_dates).issubset(expected):
        raise ValidationError("verified publication-date evidence is outside catalog scope")
    heading = "## 確認済みサイト一覧"
    if summary.count(heading) != 1:
        raise ValidationError("summary source coverage section is missing or duplicated")
    section = summary.split(heading, 1)[1]
    section = re.split(r"\n## ", section, maxsplit=1)[0]
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 7 or cells[0] in {"サイト", "---"} or set(cells[0]) == {"-"}:
            continue
        name = cells[0]
        if name in rows:
            raise ValidationError("summary contains a duplicate source coverage row")
        rows[name] = cells
    if set(rows) != set(expected):
        raise ValidationError("summary source coverage does not match the catalog")
    for name, cells in rows.items():
        _, tier, status, method, confirmed_url, recent_items, reason = cells
        source = expected[name]
        source_evidence = evidence[name]
        if tier != str(source["tier"]) or source_evidence.get("tier") != source["tier"] or status not in ALLOWED_COVERAGE_STATUS:
            raise ValidationError("summary source coverage row is invalid")
        if method not in {"RSS", "公開ページ", "サイト限定検索", "公式代替URL"}:
            raise ValidationError("summary source acquisition method is invalid")
        hosts = source_hosts(source)
        if not is_allowed_source_url(confirmed_url, hosts):
            raise ValidationError("summary source confirmation URL is invalid")
        try:
            item_count = int(recent_items)
        except ValueError as exc:
            raise ValidationError("summary source item count is invalid") from exc
        if source_evidence.get("status") == "fetched":
            expected_method = {"rss": "RSS", "public_page": "公開ページ"}.get(
                source_evidence.get("method")
            )
            if method != expected_method or confirmed_url != source_evidence.get("final_url"):
                raise ValidationError("summary source row does not match fetch evidence")
            if status == "アクセス制約":
                raise ValidationError("fetched source cannot be an access constraint")
            extract_format, sealed_entries = sealed_extract_entries(
                manifest_path, source_evidence, hosts
            )
            extracted_count = source_evidence.get("extracted_entry_count")
            if (
                not isinstance(extracted_count, int)
                or extracted_count <= 0
                or extracted_count != len(sealed_entries)
            ):
                raise ValidationError("fetched source has an empty or inconsistent extract")
            for item in supplemental_dates.get(name, []):
                requested_url = item.get("requested_url")
                final_url = item.get("final_url")
                published = parse_publication_date(item.get("published_date"))
                if (
                    not isinstance(requested_url, str)
                    or not isinstance(final_url, str)
                    or not published
                    or not is_allowed_source_url(requested_url, hosts)
                    or not is_allowed_source_url(final_url, hosts)
                ):
                    raise ValidationError("verified publication-date entry is invalid")
                key = canonical_url(requested_url)
                if key != canonical_url(final_url):
                    raise ValidationError(
                        "publication-date evidence redirected to another article"
                    )
                if key not in sealed_entries or sealed_entries[key] is not None:
                    raise ValidationError(
                        "publication-date evidence is not bound to an undated extract entry"
                    )
                sealed_entries[key] = published
            dated_entries = [value for value in sealed_entries.values() if value is not None]
            if not dated_entries:
                raise ValidationError("source lacks publication-date evidence")
            if extract_format == "feed" and len(dated_entries) != len(sealed_entries):
                raise ValidationError("feed source lacks publication-date evidence")
            start = run_date - timedelta(days=6)
            evidence_count = sum(
                start <= value <= run_date
                for value in dated_entries
            )
            manifest_count = source_evidence.get("jst_window_item_count")
            if (
                type(manifest_count) is not int
                or manifest_count != evidence_count
                or source_evidence.get("jst_window_start") != start.isoformat()
                or source_evidence.get("jst_window_end") != run_date.isoformat()
            ):
                raise ValidationError(
                    f"source manifest JST window count is invalid for {name}"
                )
            if item_count != evidence_count:
                raise ValidationError(
                    f"summary source item count does not match dated extract evidence for {name}"
                )
        elif source_evidence.get("status") == "access_constraint":
            expected_method = {"rss": "RSS", "public_page": "公開ページ"}.get(
                source_evidence.get("method")
            )
            constraint = source_evidence.get("constraint")
            if (
                method != expected_method
                or confirmed_url != source_evidence.get("final_url")
                or status != "アクセス制約"
                or constraint not in CONSTRAINT_REASON
                or not CONSTRAINT_REASON[str(constraint)].search(reason)
            ):
                raise ValidationError("summary access constraint does not match fetch evidence")
            attempts = source_evidence.get("attempts")
            expected_attempts = [
                (attempt_method, attempt_url)
                for attempt_method, attempt_url in (
                    ("rss", source.get("feed_url")),
                    ("public_page", source.get("page_url")),
                )
                if isinstance(attempt_url, str) and attempt_url
            ]
            if (
                not isinstance(attempts, list)
                or len(attempts) != len(expected_attempts)
            ):
                raise ValidationError("source access constraint does not cover every direct endpoint")
            for attempt, (attempt_method, attempt_url) in zip(attempts, expected_attempts):
                if (
                    not isinstance(attempt, dict)
                    or attempt.get("method") != attempt_method
                    or attempt.get("url") != attempt_url
                    or attempt.get("requested_url") != attempt_url
                    or attempt.get("status") != "access_constraint"
                    or attempt.get("constraint") not in CONSTRAINT_REASON
                ):
                    raise ValidationError(
                        "source access constraint does not cover every direct endpoint"
                    )
                attempt_constraint = attempt.get("constraint")
                if attempt_constraint == "robots":
                    if (
                        attempt.get("final_url") != attempt_url
                        or attempt.get("http_status") is not None
                    ):
                        raise ValidationError(
                            "direct endpoint lacks sealed robots.txt evidence"
                        )
                    attempt_robots_url = attempt.get("robots_url")
                    attempt_robots_sha256 = attempt.get("robots_sha256")
                    parsed_attempt_robots = urllib.parse.urlsplit(str(attempt_robots_url))
                    parsed_attempt_source = urllib.parse.urlsplit(attempt_url)
                    if (
                        parsed_attempt_robots.scheme != "https"
                        or parsed_attempt_robots.hostname != parsed_attempt_source.hostname
                        or parsed_attempt_robots.path != "/robots.txt"
                        or not isinstance(attempt_robots_sha256, str)
                        or re.fullmatch(r"[0-9a-f]{64}", attempt_robots_sha256) is None
                    ):
                        raise ValidationError(
                            "direct endpoint lacks sealed robots.txt evidence"
                        )
                else:
                    attempt_final_url = attempt.get("final_url")
                    attempt_http_status = attempt.get("http_status")
                    if (
                        not is_allowed_source_url(attempt_final_url, source_hosts(source))
                        or not isinstance(attempt_http_status, int)
                        or not 200 <= attempt_http_status <= 599
                    ):
                        raise ValidationError(
                            "direct endpoint lacks sealed access-constraint evidence"
                        )
            if constraint == "robots":
                robots_url = source_evidence.get("robots_url")
                robots_sha256 = source_evidence.get("robots_sha256")
                parsed_robots = urllib.parse.urlsplit(str(robots_url))
                parsed_source = urllib.parse.urlsplit(str(confirmed_url))
                if (
                    parsed_robots.scheme != "https"
                    or parsed_robots.hostname != parsed_source.hostname
                    or parsed_robots.path != "/robots.txt"
                    or not isinstance(robots_sha256, str)
                    or re.fullmatch(r"[0-9a-f]{64}", robots_sha256) is None
                ):
                    raise ValidationError("robots constraint lacks sealed robots.txt evidence")
        elif source_evidence.get("status") == "needs_search_fallback":
            fallback = resolutions.get(name)
            if fallback:
                if fallback.get("status") == "verified_access_constraint":
                    constraint = fallback.get("constraint")
                    if (
                        method != "公開ページ"
                        or confirmed_url != fallback.get("requested_url")
                        or status != "アクセス制約"
                        or item_count != 0
                        or constraint not in CONSTRAINT_REASON
                        or not CONSTRAINT_REASON[str(constraint)].search(reason)
                    ):
                        raise ValidationError("summary access constraint does not match verified fallback")
                    continue
                expected_method = {
                    "site_search": "サイト限定検索",
                    "official_alternate": "公式代替URL",
                }.get(fallback.get("method"))
                if (
                    fallback.get("status") != "verified_fallback"
                    or method != expected_method
                    or confirmed_url != fallback.get("requested_url")
                    or status == "アクセス制約"
                ):
                    raise ValidationError("summary source row does not match verified fallback")
                published_dates = fallback.get("published_dates")
                candidate_evidence = fallback.get("candidate_evidence")
                if not isinstance(published_dates, list) or not isinstance(candidate_evidence, list):
                    raise ValidationError("verified fallback lacks publication-date evidence")
                parsed_dates = [parse_publication_date(value) for value in published_dates]
                extracted_count = fallback.get("extracted_entry_count")
                candidate_count = fallback.get("candidate_entry_count")
                date_evidence_count = fallback.get("date_evidence_count")
                if (
                    not isinstance(extracted_count, int)
                    or extracted_count <= 0
                    or not isinstance(candidate_count, int)
                    or candidate_count <= 0
                    or candidate_count > extracted_count
                    or not isinstance(date_evidence_count, int)
                    or date_evidence_count <= 0
                    or date_evidence_count != len(parsed_dates)
                    or date_evidence_count != candidate_count
                    or any(value is None for value in parsed_dates)
                    or len(candidate_evidence) != candidate_count
                ):
                    raise ValidationError("verified fallback lacks complete publication-date evidence")
                candidate_urls: set[str] = set()
                for index, item in enumerate(candidate_evidence):
                    candidate_url = item.get("url") if isinstance(item, dict) else None
                    if (
                        not isinstance(item, dict)
                        or not is_allowed_extract_url(candidate_url, hosts)
                        or item.get("provenance") not in {
                            "feed_entry",
                            "article",
                            "json_ld",
                            "html_meta",
                        }
                        or parse_publication_date(item.get("published")) != parsed_dates[index]
                    ):
                        raise ValidationError(
                            "verified fallback candidate evidence is invalid"
                        )
                    candidate_key = canonical_url(str(candidate_url))
                    if candidate_key in candidate_urls:
                        raise ValidationError(
                            "verified fallback candidate evidence is invalid"
                        )
                    candidate_urls.add(candidate_key)
                dated = [value for value in parsed_dates if value is not None]
                start = run_date - timedelta(days=6)
                evidence_count = sum(start <= value <= run_date for value in dated)
                if item_count != evidence_count:
                    raise ValidationError("fallback item count does not match date evidence")
                if status == "対象期間記事なし" and item_count != 0:
                    raise ValidationError("no-recent-article status must have zero items")
                if status == "取得済み" and item_count <= 0:
                    raise ValidationError("summary source item count contradicts status")
                continue
            attempts = source_evidence.get("attempts")
            if not isinstance(attempts, list):
                raise ValidationError("unresolved source lacks attempt evidence")
            raise ValidationError("source requires a verified fallback resolution")
        else:
            raise ValidationError("source manifest status is invalid")
        if status == "アクセス制約":
            if item_count != 0 or not ACCESS_CONTROL_REASON.search(reason):
                raise ValidationError("access-control status lacks a permitted reason")
        elif status == "対象期間記事なし":
            if item_count != 0:
                raise ValidationError("no-recent-article status must have zero items")
        elif item_count <= 0:
            raise ValidationError("summary source item count contradicts status")


def canonicalize_constraints_main(argv: list[str]) -> int:
    """Preserve raw artifacts and write a minimal sealed coverage-cell projection."""
    if len(argv) != 11:
        print(
            "usage: validate-collection-result.py --canonicalize-constraints "
            "RAW_RESULT STAGING_ROOT RUN_ID START_EPOCH SOURCE_CATALOG "
            "SOURCE_MANIFEST VERIFIED_RESOLUTIONS OUTPUT RECEIPT",
            file=sys.stderr,
        )
        return 64
    try:
        raw_result_path = Path(argv[2])
        staging_root = Path(os.path.abspath(argv[3]))
        output_path = Path(argv[9])
        receipt_path = Path(argv[10])
        if not (
            raw_result_path.is_absolute()
            and staging_root.is_absolute()
            and output_path.is_absolute()
            and receipt_path.is_absolute()
            and raw_result_path.parent == output_path.parent == receipt_path.parent
            and staging_root.parent == raw_result_path.parent
            and len(
                {raw_result_path.name, output_path.name, receipt_path.name}
            ) == 3
        ):
            raise ValidationError("collection normalization path layout is invalid")
        raw_result_bytes = read_regular_nofollow(
            raw_result_path, "raw collection result"
        )
        raw_result = json.loads(raw_result_bytes.decode("utf-8"))
        if raw_result.get("daily_pipeline_status") != "complete":
            raise ValidationError("daily pipeline is not complete")
        if raw_result.get("vault_artifacts_complete") is not True:
            raise ValidationError("artifact set is incomplete")
        if raw_result.get("run_id") != argv[4]:
            raise ValidationError("run ID mismatch")
        run_match = re.match(r"^(\d{4})(\d{2})(\d{2})T", argv[4])
        if not run_match:
            raise ValidationError("run ID does not contain a JST date")
        expected_date = "-".join(run_match.groups())
        earliest_mtime = int(argv[5])
        raw_summary = validate_artifact(
            raw_result["summary_path"],
            raw_result["summary_sha256"],
            staging_root,
            earliest_mtime,
            expected_date,
            "summary",
        )
        raw_advisory = validate_artifact(
            raw_result["advisory_path"],
            raw_result["advisory_sha256"],
            staging_root,
            earliest_mtime,
            expected_date,
            "advisory",
        )
        private_paths = {str(staging_root), str(Path.home())}
        if any(private_path in raw_summary for private_path in private_paths):
            raise ValidationError("summary contains a machine-specific path")
        raw_summary_path = Path(raw_result["summary_path"])
        validate_advisory_reference(
            raw_advisory,
            raw_summary_path.name,
            raw_result["summary_sha256"],
            staging_root,
        )
        constraints, retrieved, source_evidence_bindings = sealed_coverage_authority(
            Path(argv[6]), Path(argv[7]), Path(argv[8])
        )
        canonical_summary, reason_corrections, status_corrections = (
            canonicalize_summary_coverage(raw_summary, constraints, retrieved)
        )
        canonical_summary_bytes = canonical_summary.encode("utf-8")
        canonical_summary_sha256 = hashlib.sha256(
            canonical_summary_bytes
        ).hexdigest()
        canonical_advisory = update_advisory_summary_reference(
            raw_advisory,
            raw_summary_path.name,
            raw_result["summary_sha256"],
            canonical_summary_sha256,
        )
        canonical_advisory_bytes = canonical_advisory.encode("utf-8")
        canonical_advisory_sha256 = hashlib.sha256(
            canonical_advisory_bytes
        ).hexdigest()

        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        staging_fd = os.open(staging_root, directory_flags)
        canonical_fd = -1
        canonical_directory_name = "canonical-artifacts"
        try:
            previous_umask = os.umask(0)
            try:
                os.mkdir(canonical_directory_name, 0o700, dir_fd=staging_fd)
            finally:
                os.umask(previous_umask)
            os.fsync(staging_fd)
            canonical_fd = os.open(
                canonical_directory_name, directory_flags, dir_fd=staging_fd
            )
            os.fchmod(canonical_fd, 0o700)
            write_exclusive_bytes(
                canonical_fd,
                raw_summary_path.name,
                canonical_summary_bytes,
                "canonical summary",
            )
            raw_advisory_path = Path(raw_result["advisory_path"])
            write_exclusive_bytes(
                canonical_fd,
                raw_advisory_path.name,
                canonical_advisory_bytes,
                "canonical advisory",
            )
            os.fsync(canonical_fd)
        finally:
            if canonical_fd >= 0:
                os.close(canonical_fd)
            os.close(staging_fd)

        canonical_root = staging_root / canonical_directory_name
        canonical_result = deepcopy(raw_result)
        canonical_result.update(
            {
                "summary_path": str(canonical_root / raw_summary_path.name),
                "summary_sha256": canonical_summary_sha256,
                "advisory_path": str(
                    canonical_root / Path(raw_result["advisory_path"]).name
                ),
                "advisory_sha256": canonical_advisory_sha256,
            }
        )
        canonical_result_bytes = canonical_json_bytes(canonical_result)
        receipt = {
            "version": 1,
            "projection": "sealed_source_coverage_cells_v1",
            "raw_collection_result_sha256": hashlib.sha256(
                raw_result_bytes
            ).hexdigest(),
            "canonical_collection_result_sha256": hashlib.sha256(
                canonical_result_bytes
            ).hexdigest(),
            "raw_summary": {
                "path": str(raw_summary_path),
                "sha256": raw_result["summary_sha256"],
            },
            "canonical_summary": {
                "path": canonical_result["summary_path"],
                "sha256": canonical_summary_sha256,
            },
            "raw_advisory": {
                "path": raw_result["advisory_path"],
                "sha256": raw_result["advisory_sha256"],
            },
            "canonical_advisory": {
                "path": canonical_result["advisory_path"],
                "sha256": canonical_advisory_sha256,
            },
            "source_evidence": source_evidence_bindings,
            "corrected_reason_count": len(reason_corrections),
            "corrections": reason_corrections,
            "corrected_status_count": len(status_corrections),
            "status_corrections": status_corrections,
        }
        output_directory_fd = os.open(output_path.parent, directory_flags)
        try:
            write_exclusive_bytes(
                output_directory_fd,
                output_path.name,
                canonical_result_bytes,
                "canonical collection result",
            )
            write_exclusive_bytes(
                output_directory_fd,
                receipt_path.name,
                canonical_json_bytes(receipt),
                "collection normalization receipt",
            )
            os.fsync(output_directory_fd)
        finally:
            os.close(output_directory_fd)
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        print(f"collection normalization failed:{exc}", file=sys.stderr)
        return 75
    return 0


def main(argv: list[str]) -> int:
    """Validate one collection result and return a fail-closed status."""
    if len(argv) > 1 and argv[1] == "--canonicalize-constraints":
        return canonicalize_constraints_main(argv)
    if len(argv) != 8:
        print(
            "usage: validate-collection-result.py RESULT STAGING_ROOT RUN_ID START_EPOCH SOURCE_CATALOG SOURCE_MANIFEST VERIFIED_RESOLUTIONS",
            file=sys.stderr,
        )
        return 64
    try:
        result = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        if result.get("daily_pipeline_status") != "complete":
            raise ValidationError("daily pipeline is not complete")
        if result.get("vault_artifacts_complete") is not True:
            raise ValidationError("artifact set is incomplete")
        if result.get("run_id") != argv[3]:
            raise ValidationError("run ID mismatch")
        run_match = re.match(r"^(\d{4})(\d{2})(\d{2})T", argv[3])
        if not run_match:
            raise ValidationError("run ID does not contain a JST date")
        expected_date = "-".join(run_match.groups())
        earliest_mtime = int(argv[4])
        summary_content = validate_artifact(
            result["summary_path"],
            result["summary_sha256"],
            Path(argv[2]),
            earliest_mtime,
            expected_date,
            "summary",
        )
        advisory_content = validate_artifact(
            result["advisory_path"],
            result["advisory_sha256"],
            Path(argv[2]),
            earliest_mtime,
            expected_date,
            "advisory",
        )
        private_paths = {str(Path(os.path.abspath(argv[2]))), str(Path.home())}
        if any(private_path in summary_content for private_path in private_paths):
            raise ValidationError("summary contains a machine-specific path")
        validate_source_coverage(
            summary_content, Path(argv[5]), Path(argv[6]), Path(argv[7]),
            date.fromisoformat(expected_date),
        )
        validate_advisory_reference(
            advisory_content,
            Path(result["summary_path"]).name,
            result["summary_sha256"],
            Path(argv[2]),
        )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        print(f"collection validation failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
