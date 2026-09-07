#!/usr/bin/env python3
"""Fetch the configured public IT-news feeds/pages into run-local staging."""

from __future__ import annotations

import concurrent.futures
import gzip
import html
import hashlib
import http.client
import ipaddress
import io
import json
import os
import pwd
import re
import socket
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
import zlib
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

MAX_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_CONTENT_LENGTH_DIGITS = 20
LEGACY_CONSTRAINT_TEXT_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 20
JSON_LD_MAX_DEPTH = 64
JSON_LD_MAX_NODES = 10_000
JSON_LD_MAX_CONTEXT_ITEMS = 64
JSON_LD_ARTICLE_TYPES = frozenset({
    "Article",
    "AdvertiserContentArticle",
    "NewsArticle",
    "AnalysisNewsArticle",
    "AskPublicNewsArticle",
    "BackgroundNewsArticle",
    "OpinionNewsArticle",
    "ReportageNewsArticle",
    "ReviewNewsArticle",
    "Report",
    "SatiricalArticle",
    "ScholarlyArticle",
    "MedicalScholarlyArticle",
    "SocialMediaPosting",
    "BlogPosting",
    "LiveBlogPosting",
    "DiscussionForumPosting",
    "TechArticle",
    "APIReference",
})
SCHEMA_JSON_LD_CONTEXTS = frozenset({
    "http://schema.org",
    "http://schema.org/",
    "https://schema.org",
    "https://schema.org/",
})
JSON_LD_CONTEXT_UNBOUND = "unbound"
JSON_LD_CONTEXT_TRUSTED = "trusted"
JSON_LD_CONTEXT_TAINTED = "tainted"
USER_AGENT = "CodexITNewsCollector/1.0 (+public-news-research)"
SOURCE_KEYS = {"name", "tier", "feed_url", "page_url"}
RUNTIME_DATE_DIRECTORY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
RUNTIME_RUN_DIRECTORY = re.compile(
    r"\d{8}T\d{6}[+-]\d{4}-\d{1,10}-\d{1,10}\Z"
)
CANONICAL_RUNTIME_RELATIVE = Path(
    "AutomationWorkspaces/codex/daily-it-news-vulnerability-check"
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
AMBIGUOUS_SCRIPT_CLOSER = re.compile(
    r"</[\t\n\f\r ]+script(?:[\t\n\f\r />])", re.IGNORECASE
)
SCRIPT_DOUBLE_ESCAPE_START = re.compile(
    r"<script(?=[\t\n\f\r />])", re.IGNORECASE | re.ASCII
)
ENGLISH_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            (),
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        )
    )
    for name in names
}


class CollectionError(RuntimeError):
    """Represent a source collection error that should fail closed."""


class RobotsDisallowed(CollectionError):
    """Carry sealed robots.txt evidence for one disallowed direct URL."""

    def __init__(self, requested_url: str, robots_url: str, robots_sha256: str) -> None:
        super().__init__("robots_disallowed")
        self.requested_url = requested_url
        self.robots_url = robots_url
        self.robots_sha256 = robots_sha256


def stable_regular_bytes(path: Path, limit: int = MAX_JSON_BYTES) -> bytes:
    """Read one bounded regular file without following a final symlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > limit
        ):
            raise CollectionError("input is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if len(content) > limit or before_identity != after_identity:
            raise CollectionError("input changed while it was read")
        return content
    finally:
        os.close(descriptor)


def load_catalog_with_digest(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Load one stable catalog and bind its parsed sources to the same bytes."""
    content = stable_regular_bytes(path)
    payload = json.loads(content.decode("utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("sources"), list):
        raise CollectionError("unsupported source catalog")
    sources = payload["sources"]
    names: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
            raise CollectionError("invalid source catalog entry")
        if (
            not isinstance(source["name"], str)
            or not source["name"].strip()
            or source["name"] in names
            or source["tier"] not in (1, 2)
            or not isinstance(source["page_url"], str)
            or source["feed_url"] is not None
            and not isinstance(source["feed_url"], str)
        ):
            raise CollectionError("invalid source catalog value")
        names.add(source["name"])
    if not sources:
        raise CollectionError("source catalog is empty")
    return sources, hashlib.sha256(content).hexdigest()


def load_catalog(path: Path) -> list[dict[str, Any]]:
    """Load and strictly validate the tracked source catalog."""
    return load_catalog_with_digest(path)[0]


def allowed_hosts(sources: list[dict[str, Any]]) -> set[str]:
    """Derive an exact hostname allowlist from reviewed catalog URLs."""
    hosts: set[str] = set()
    for source in sources:
        for value in (source["feed_url"], source["page_url"]):
            if value:
                host = urllib.parse.urlsplit(value).hostname
                if not host:
                    raise CollectionError("catalog URL has no hostname")
                hosts.add(host.lower())
                if host.startswith("www."):
                    hosts.add(host[4:].lower())
                else:
                    hosts.add(f"www.{host.lower()}")
    return hosts


def validate_url(value: str, hosts: set[str]) -> str:
    """Allow only HTTPS catalog hosts without credentials or fragments."""
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise CollectionError("URL is outside the public source allowlist")
    return value


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects outside the reviewed public source hosts."""

    def __init__(self, hosts: set[str]) -> None:
        super().__init__()
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        validate_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    def http_error_308(self, req, fp, code, msg, headers):  # type: ignore[no-untyped-def]
        """Follow permanent redirects on Python versions that omit HTTP 308."""
        return self.http_error_302(req, fp, code, msg, headers)


def robots_policy(url: str, hosts: set[str]) -> dict[str, object]:
    """Return a reachable robots.txt decision and its exact evidence digest."""
    parsed = urllib.parse.urlsplit(url)
    robots_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
    )
    validate_url(robots_url, hosts)
    request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(SafeRedirectHandler(hosts))
    try:
        with opener.open(request, timeout=8) as response:
            content = response.read(512 * 1024 + 1)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, socket.timeout):
        return {"allowed": True, "robots_url": robots_url, "robots_sha256": None}
    if len(content) > 512 * 1024:
        raise CollectionError("robots.txt exceeds size limit")
    data = content.decode("utf-8", errors="replace")
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(data.splitlines())
    return {
        "allowed": parser.can_fetch(USER_AGENT, url),
        "robots_url": robots_url,
        "robots_sha256": hashlib.sha256(content).hexdigest(),
    }


def read_bounded(response) -> bytes:  # type: ignore[no-untyped-def]
    """Read a response with a hard size cap and optional gzip decoding."""
    get_all = getattr(response.headers, "get_all", None)
    if callable(get_all):
        declared_values = get_all("Content-Length") or []
        if len(declared_values) > 1:
            raise CollectionError("response has duplicate Content-Length fields")
        declared = declared_values[0] if declared_values else None
    else:
        declared = response.headers.get("Content-Length")
    declared_size = None
    valid_declared_length = bool(
        isinstance(declared, str)
        and re.fullmatch(r"[0-9]+", declared, re.ASCII)
    )
    if valid_declared_length:
        if len(declared) > MAX_CONTENT_LENGTH_DIGITS:
            raise CollectionError("response Content-Length field is too long")
        try:
            declared_size = int(declared)
        except (TypeError, ValueError) as exc:
            raise CollectionError("response Content-Length is invalid") from exc
        if declared_size is not None and declared_size > MAX_BYTES:
            raise CollectionError("response exceeds size limit")
    elif declared is not None:
        # CPython accepts forms such as ``+41`` when it initializes an
        # HTTPResponse and then silently clips read(amt) to that parsed length.
        # The collector treats non-ASCII-decimal field values as absent, so
        # clear only that implementation cache before the bounded read.
        reader = response
        seen_readers: set[int] = set()
        for _depth in range(3):
            if isinstance(reader, http.client.HTTPResponse):
                reader.length = None
                break
            reader_id = id(reader)
            if reader_id in seen_readers:
                break
            seen_readers.add(reader_id)
            reader = getattr(reader, "fp", None)
            if reader is None:
                break
    content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise CollectionError("response exceeds size limit")
    if declared_size is not None and len(content) != declared_size:
        raise CollectionError("response length does not match Content-Length")
    if response.headers.get("Content-Encoding", "").lower() == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as decompressor:
            content = decompressor.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise CollectionError("decompressed response exceeds size limit")
    if not content:
        raise CollectionError("response is empty")
    return content


def fetch_url(url: str, hosts: set[str]) -> dict[str, Any]:
    """Fetch one reviewed URL, accepting public XML/HTML regardless of MIME quirks."""
    validate_url(url, hosts)
    policy = robots_policy(url, hosts)
    if not policy["allowed"]:
        raise RobotsDisallowed(
            url, str(policy["robots_url"]), str(policy["robots_sha256"])
        )
    opener = urllib.request.build_opener(SafeRedirectHandler(hosts))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html, text/plain;q=0.9, */*;q=0.1",
            "Accept-Encoding": "gzip",
        },
    )
    last_error = "unknown fetch failure"
    for attempt in range(1, 3):
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                content = read_bounded(response)
                content_type = response.headers.get_content_type().lower()
                prefix = content.lstrip()[:32].lower()
                if not (
                    content_type.startswith("text/")
                    or content_type
                    in {
                        "application/rss+xml",
                        "application/atom+xml",
                        "application/xml",
                        "application/xhtml+xml",
                        "application/octet-stream",
                    }
                    and prefix.startswith((b"<", b"<?xml"))
                ):
                    raise CollectionError(f"unsupported response type: {content_type}")
                return {
                    "content": content,
                    "content_type": content_type,
                    "final_url": validate_url(response.geturl(), hosts),
                    "http_status": response.status,
                    "attempt": attempt,
                }
        except urllib.error.HTTPError as exc:
            last_error = f"http_{exc.code}"
            if exc.code in (307, 308) and exc.headers.get("Location"):
                redirected = urllib.parse.urljoin(exc.geturl(), exc.headers["Location"])
                validate_url(redirected, hosts)
                request = urllib.request.Request(redirected, headers=dict(request.header_items()))
                continue
            if exc.code in (401, 402, 403, 407, 429):
                try:
                    content = read_bounded(exc)
                except (
                    CollectionError,
                    OSError,
                    EOFError,
                    http.client.HTTPException,
                    zlib.error,
                ):
                    if exc.code == 429:
                        break
                    raise
                constraint = detect_access_constraint(content)
                if constraint and (exc.code != 429 or constraint == "captcha"):
                    return {
                        "content": content,
                        "content_type": exc.headers.get_content_type().lower(),
                        "final_url": validate_url(exc.geturl(), hosts),
                        "http_status": exc.code,
                        "attempt": attempt,
                    }
                break
        except (CollectionError, OSError, urllib.error.URLError, socket.timeout) as exc:
            last_error = str(exc)
        if attempt == 1:
            time.sleep(0.5)
    raise CollectionError(last_error)


def safe_filename(index: int, name: str, method: str) -> str:
    """Create a stable non-sensitive filename for one fetched source."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "source"
    return f"{index:02d}-{slug}-{method}.data"


def clean_text(value: Optional[str], limit: int = 800) -> Optional[str]:
    """Normalize bounded untrusted markup text for compact model input."""
    if not value:
        return None
    without_tags = re.sub(r"<[^>]+>", " ", value)
    normalized = re.sub(r"\s+", " ", html.unescape(without_tags)).strip()
    return normalized[:limit] or None


def validated_publication_date(value: Optional[str]) -> Optional[str]:
    """Return bounded date evidence only when it is a parseable timestamp."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or value.strip() != value
        or "<" in value
        or ">" in value
        or html.unescape(value) != value
    ):
        return None
    cleaned = value
    try:
        if ISO_PUBLICATION_DATE.fullmatch(cleaned) is None:
            raise ValueError
        datetime.fromisoformat(
            cleaned.replace("Z", "+00:00").replace("z", "+00:00")
        )
        return cleaned
    except ValueError:
        pass
    try:
        if RFC_PUBLICATION_DATE.fullmatch(cleaned) is None:
            raise ValueError
        parsedate_to_datetime(cleaned)
        return cleaned
    except (TypeError, ValueError, OverflowError):
        pass
    # Some publisher lists wrap the complete calendar field in parentheses.
    # Accept that exact wrapper, but never search a prose field for a date-like
    # substring (the downstream validator cannot recover the original field).
    calendar_value = cleaned
    wrapped = re.fullmatch(r"\(([^()]*)\)", cleaned)
    if wrapped is not None:
        calendar_value = wrapped.group(1)
        if not calendar_value or calendar_value.strip() != calendar_value:
            return None
    match = re.fullmatch(
        r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", calendar_value
    )
    if match:
        try:
            return date(*(int(part) for part in match.groups())).isoformat()
        except ValueError:
            pass
    match = re.fullmatch(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(20\d{2})", cleaned)
    if match and (month := ENGLISH_MONTHS.get(match.group(1).lower())):
        try:
            return date(int(match.group(3)), month, int(match.group(2))).isoformat()
        except ValueError:
            pass
    return None


def validated_json_ld_publication_date(value: Optional[str]) -> Optional[str]:
    """Validate JSON-LD dates without applying HTML text transformations."""
    if (
        not value
        or len(value) > 200
        or "<" in value
        or ">" in value
        or html.unescape(value) != value
    ):
        return None
    return validated_publication_date(value)


def publication_date_in_jst(value: Optional[str]) -> Optional[date]:
    """Normalize validated RSS/HTML evidence to its JST calendar date."""
    validated = validated_publication_date(value)
    if not validated:
        return None
    try:
        parsed = datetime.fromisoformat(
            validated.replace("Z", "+00:00").replace("z", "+00:00")
        )
    except ValueError:
        try:
            parsed = parsedate_to_datetime(validated)
        except (TypeError, ValueError, OverflowError):
            return None
    jst = ZoneInfo("Asia/Tokyo")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(jst).date()


def jst_window_item_count(extract: dict[str, Any], run_date: date) -> int:
    """Count sealed article candidates in the inclusive seven-day JST window."""
    entries = extract.get("entries", [])
    if extract.get("format") == "html_links":
        entries = [
            entry for entry in entries
            if entry.get("candidate_provenance") in {"article", "json_ld"}
        ]
    start = run_date - timedelta(days=6)
    return sum(
        start <= published <= run_date
        for published in (
            publication_date_in_jst(entry.get("published")) for entry in entries
        )
        if published is not None
    )


def local_name(tag: str) -> str:
    """Drop an XML namespace from one element tag."""
    return tag.rsplit("}", 1)[-1].lower()


PUBLIC_HOST_LABEL = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.IGNORECASE
)


def parsed_public_candidate_url(
    base_url: str, value: str
) -> Optional[tuple[str, urllib.parse.SplitResult]]:
    """Join and strictly validate one inert public HTTP(S) candidate URL."""
    if (
        not value
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
        absolute = urllib.parse.urljoin(base_url, value)
        parsed = urllib.parse.urlsplit(absolute)
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
        or len(absolute) > 4096
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
    parsed = parsed._replace(fragment="")
    return parsed.geturl(), parsed


def candidate_matches_source_hosts(
    candidate: tuple[str, urllib.parse.SplitResult],
    hosts: Optional[set[str]],
) -> bool:
    """Bind a sealed candidate to reviewed source hosts and default ports."""
    if hosts is None:
        return True
    parsed = candidate[1]
    if parsed.hostname not in hosts:
        return False
    return (
        parsed.scheme.lower() == "https" and parsed.port in (None, 443)
    ) or (
        parsed.scheme.lower() == "http" and parsed.port in (None, 80)
    )


def extract_xml(
    content: bytes,
    base_url: str,
    hosts: Optional[set[str]] = None,
) -> list[dict[str, Optional[str]]]:
    """Extract bounded RSS/Atom entry metadata without executing markup."""
    root = ET.fromstring(content)
    entries: list[dict[str, Optional[str]]] = []
    entries_by_url: dict[str, dict[str, Optional[str]]] = {}
    for item in root.iter():
        if local_name(item.tag) not in {"item", "entry"}:
            continue
        fields: dict[str, Optional[str]] = {
            "title": None,
            "url": None,
            "published": None,
            "summary": None,
            "candidate_provenance": "feed_entry",
        }
        for child in list(item):
            name = local_name(child.tag)
            if name == "title" and not fields["title"]:
                fields["title"] = clean_text("".join(child.itertext()), 300)
            elif name == "link" and not fields["url"]:
                href = child.attrib.get("href")
                if href is not None:
                    fields["url"] = href
                elif isinstance(child.text, str):
                    fields["url"] = child.text.strip()
            elif name in {"pubdate", "published", "updated", "date"} and not fields["published"]:
                fields["published"] = validated_publication_date(child.text)
            elif name in {"description", "summary", "content"} and not fields["summary"]:
                fields["summary"] = clean_text("".join(child.itertext()), 800)
        if fields["url"]:
            validated_url = parsed_public_candidate_url(base_url, fields["url"])
            if validated_url is None or not candidate_matches_source_hosts(
                validated_url, hosts
            ):
                continue
            fields["url"] = validated_url[0]
            entry_key = canonical_url(validated_url[0])
            existing = entries_by_url.get(entry_key)
            if existing is not None:
                existing["title"] = existing["title"] or fields["title"]
                existing["published"] = (
                    existing["published"] or fields["published"]
                )
                existing["summary"] = existing["summary"] or fields["summary"]
                continue
        if len(entries) >= 200:
            continue
        if fields["title"] or fields["url"]:
            entries.append(fields)
            if fields["url"]:
                entries_by_url[canonical_url(fields["url"])] = fields
    return entries


HTML_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def schema_json_ld_context_state(value: object, inherited: str) -> str:
    """Trust only exact Schema.org contexts and keep unknown semantics sticky."""
    if inherited == JSON_LD_CONTEXT_TAINTED:
        return JSON_LD_CONTEXT_TAINTED

    def is_exact_schema_context(item: object) -> bool:
        if isinstance(item, str):
            return item in SCHEMA_JSON_LD_CONTEXTS
        return (
            isinstance(item, dict)
            and len(item) == 1
            and "@vocab" in item
            and item.get("@vocab") in SCHEMA_JSON_LD_CONTEXTS
        )

    if isinstance(value, list):
        if not value:
            return inherited
        if len(value) > JSON_LD_MAX_CONTEXT_ITEMS:
            return JSON_LD_CONTEXT_TAINTED
        return (
            JSON_LD_CONTEXT_TRUSTED
            if all(is_exact_schema_context(item) for item in value)
            else JSON_LD_CONTEXT_TAINTED
        )
    return (
        JSON_LD_CONTEXT_TRUSTED
        if is_exact_schema_context(value)
        else JSON_LD_CONTEXT_TAINTED
    )


def is_schema_article_type(value: object, short_names_allowed: bool) -> bool:
    """Accept only exact Schema.org Article types or their canonical IRIs."""
    if not isinstance(value, str) or not value or len(value) > 256:
        return False
    if any(
        character.isspace()
        or ord(character) < 0x20
        or ord(character) == 0x7F
        or character == "\\"
        for character in value
    ) or "?" in value or "#" in value:
        return False
    if short_names_allowed and value in JSON_LD_ARTICLE_TYPES:
        return True
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        return False
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or hostname != "schema.org"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme.lower() == "https" and port not in (None, 443))
        or (parsed.scheme.lower() == "http" and port not in (None, 80))
        or (port is None and parsed.netloc.endswith(":"))
        or not parsed.path.startswith("/")
        or parsed.path.count("/") != 1
    ):
        return False
    return parsed.path[1:] in JSON_LD_ARTICLE_TYPES


HTML5_ASCII_WHITESPACE = "\t\n\f\r "
STRICT_HTML_ATTRIBUTE_NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_.:-]*")
STRICT_HTML_TAG_NAME = re.compile(r"[A-Za-z][A-Za-z0-9:-]*\Z")
HTML_TAG_NAME_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9:-]*")
HTML_CHARACTER_REFERENCE = re.compile(
    r"&(?:#[xX][0-9A-Fa-f]+;?|#[0-9]+;?|[A-Za-z][A-Za-z0-9]+;)"
)
JSON_LD_RAW_TEXT_CONTAINERS = frozenset({
    "iframe",
    "noembed",
    "noframes",
    "noscript",
    "plaintext",
    "style",
    "textarea",
    "title",
    "xmp",
})
JSON_LD_OPAQUE_CONTAINERS = frozenset({"math", "svg", "template"})
JSON_LD_NONVOID_TRUST_CONTAINERS = (
    JSON_LD_RAW_TEXT_CONTAINERS
    | JSON_LD_OPAQUE_CONTAINERS
    | {"frameset", "script", "select"}
)
HTML_NONVOID_ARTICLE_TRUST_ELEMENTS = JSON_LD_NONVOID_TRUST_CONTAINERS | {
    "a",
    "article",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "time",
}
EMBEDDED_SCRIPT_MAX_BYTES = 4 * 1024 * 1024
EMBEDDED_SCRIPT_MAX_BLOCKS = 32
PUBLISHER_JAVASCRIPT_MAX_BYTES = 512 * 1024


def strict_html_attributes(
    raw_tag: str, expected_tag: str
) -> Optional[list[tuple[str, Optional[str]]]]:
    """Parse one conservative HTML5-compatible start-tag subset."""
    prefix = f"<{expected_tag}"
    prefix_length = len(prefix)
    if (
        len(raw_tag) < prefix_length + 1
        or raw_tag[:prefix_length].lower() != prefix
        or raw_tag[prefix_length] not in HTML5_ASCII_WHITESPACE + ">"
        or not raw_tag.endswith(">")
    ):
        return None
    index = prefix_length
    end = len(raw_tag) - 1
    attributes: list[tuple[str, Optional[str]]] = []
    while True:
        while index < end and raw_tag[index] in HTML5_ASCII_WHITESPACE:
            index += 1
        if index == end:
            return attributes
        if raw_tag[index] in "/<>":
            return None
        match = STRICT_HTML_ATTRIBUTE_NAME.match(raw_tag, index, end)
        if match is None:
            return None
        name = match.group(0).lower()
        index = match.end()
        if (
            index < end
            and raw_tag[index] not in HTML5_ASCII_WHITESPACE + "="
        ):
            return None
        while index < end and raw_tag[index] in HTML5_ASCII_WHITESPACE:
            index += 1
        value: Optional[str] = None
        if index < end and raw_tag[index] == "=":
            index += 1
            while index < end and raw_tag[index] in HTML5_ASCII_WHITESPACE:
                index += 1
            if index == end:
                return None
            if raw_tag[index] in {'"', "'"}:
                quote = raw_tag[index]
                index += 1
                value_start = index
                index = raw_tag.find(quote, index, end)
                if index == -1:
                    return None
                value = raw_tag[value_start:index]
                index += 1
                if (
                    index < end
                    and raw_tag[index] not in HTML5_ASCII_WHITESPACE
                ):
                    return None
            else:
                value_start = index
                while index < end and raw_tag[index] not in HTML5_ASCII_WHITESPACE:
                    if raw_tag[index] in {'"', "'", "=", "<", ">", "`"}:
                        return None
                    index += 1
                if value_start == index:
                    return None
                value = raw_tag[value_start:index]
            # HTMLParser resolves character references in attributes even
            # with convert_charrefs=False.  Reject them at the raw boundary so
            # a callback value cannot acquire URL syntax that was not present
            # literally in the reviewed token.
            if HTML_CHARACTER_REFERENCE.search(value):
                return None
        attributes.append((name, value))


def strict_script_attributes(
    raw_tag: str,
) -> Optional[list[tuple[str, Optional[str]]]]:
    """Parse one conservative HTML5-compatible script start-tag subset."""
    return strict_html_attributes(raw_tag, "script")


class JsonLdBlockExtractor(HTMLParser):
    """Collect publication metadata across an HTML5-safe syntax subset."""

    def __init__(self, source_text: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source_text = source_text
        self.line_offsets = [0]
        self.line_offsets.extend(
            match.end() for match in re.finditer("\n", source_text)
        )
        self.capture = False
        self.script_open = False
        self.current: list[str] = []
        self.blocks: list[str] = []
        self.meta_dates: list[str] = []
        self.raw_text_container: Optional[str] = None
        self.opaque_containers: list[str] = []
        self.select_depth = 0
        self.invalid = False
        if AMBIGUOUS_SCRIPT_CLOSER.search(source_text):
            self.invalidate()

    def invalidate(self) -> None:
        """Discard all document JSON-LD after a tokenizer ambiguity."""
        self.invalid = True
        self.capture = False
        self.script_open = False
        self.current = []
        self.blocks = []
        self.meta_dates = []

    def current_raw_end_tag(self) -> Optional[str]:
        """Recover the raw end-tag lexeme at the current parser position."""
        line, column = self.getpos()
        if line <= 0 or line > len(self.line_offsets):
            return None
        start = self.line_offsets[line - 1] + column
        end = self.source_text.find(">", start)
        if end == -1:
            return None
        return self.source_text[start : end + 1]

    def has_safe_end_tag(self, tag: str) -> bool:
        """Allow only an exact name plus HTML5 ASCII whitespace before `>`."""
        raw_tag = self.current_raw_end_tag()
        return bool(
            raw_tag
            and re.fullmatch(
                rf"</{re.escape(tag)}[\t\n\f\r ]*>",
                raw_tag,
                re.IGNORECASE,
            )
        )

    def handle_starttag(
        self, tag: str, _attrs: list[tuple[str, Optional[str]]]
    ) -> None:
        if self.invalid:
            return
        if STRICT_HTML_TAG_NAME.fullmatch(tag) is None:
            prefix = HTML_TAG_NAME_PREFIX.match(tag)
            if (
                prefix is not None
                and prefix.group(0).lower()
                in JSON_LD_NONVOID_TRUST_CONTAINERS | {"meta"}
            ):
                self.invalidate()
            return
        lowered = tag.lower()
        if self.script_open:
            self.invalidate()
            return
        if self.raw_text_container is not None:
            # HTMLParser is not an HTML5 tree builder and may emit tag events
            # from RCDATA/raw-text containers.  Keep them entirely opaque.
            return
        if self.opaque_containers:
            if lowered in JSON_LD_OPAQUE_CONTAINERS:
                self.opaque_containers.append(lowered)
            elif lowered in JSON_LD_RAW_TEXT_CONTAINERS:
                self.raw_text_container = lowered
            elif lowered == "script":
                self.script_open = True
                self.capture = False
                self.current = []
            return
        if self.select_depth:
            if lowered == "select":
                self.select_depth += 1
            elif lowered in JSON_LD_RAW_TEXT_CONTAINERS:
                self.raw_text_container = lowered
            elif lowered in JSON_LD_OPAQUE_CONTAINERS:
                self.opaque_containers.append(lowered)
            elif lowered == "script":
                self.script_open = True
                self.capture = False
                self.current = []
            return
        if lowered in JSON_LD_RAW_TEXT_CONTAINERS:
            self.raw_text_container = lowered
            return
        if lowered in JSON_LD_OPAQUE_CONTAINERS:
            self.opaque_containers.append(lowered)
            return
        # Python's callback parser does not implement the HTML5 frameset,
        # after-frameset, or after-after-frameset insertion modes.  Once a
        # frameset token is present, fail the whole document closed rather
        # than accepting script/meta tokens that a tree builder would ignore.
        if lowered == "frameset":
            self.invalidate()
            return
        if lowered == "select":
            self.select_depth = 1
            return
        if lowered == "meta":
            if self.select_depth:
                return
            attributes = strict_html_attributes(
                self.get_starttag_text() or "", "meta"
            )
            if attributes is None:
                return
            names = [name for name, _value in attributes]
            if len(names) != len(set(names)):
                return
            selectors = [
                value
                for name, value in attributes
                if name in {"property", "name"}
            ]
            contents = [
                value for name, value in attributes if name == "content"
            ]
            if (
                len(selectors) != 1
                or selectors[0] is None
                or selectors[0].lower()
                not in {"article:published_time", "datepublished"}
                or len(contents) != 1
                or contents[0] is None
            ):
                return
            published = validated_json_ld_publication_date(contents[0])
            if published:
                self.meta_dates.append(published)
            return
        if lowered != "script":
            return

        self.script_open = True
        raw_tag = self.get_starttag_text() or ""
        attributes = strict_script_attributes(raw_tag)
        type_values = (
            [value for name, value in attributes if name == "type"]
            if attributes is not None
            else []
        )
        has_src = bool(
            attributes is not None
            and any(name == "src" for name, _value in attributes)
        )
        self.capture = (
            len(type_values) == 1
            and type_values[0] is not None
            and type_values[0].lower() == "application/ld+json"
            and not has_src
        )
        self.current = []

    def handle_data(self, data: str) -> None:
        if not self.invalid and self.script_open and self.capture:
            self.current.append(data)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, Optional[str]]]
    ) -> None:
        """Do not let Python's XHTML shortcut close an HTML5 non-void element."""
        if tag.lower() in JSON_LD_NONVOID_TRUST_CONTAINERS:
            self.handle_starttag(tag, attrs)
            return
        super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self.invalid:
            return
        if STRICT_HTML_TAG_NAME.fullmatch(tag) is None:
            prefix = HTML_TAG_NAME_PREFIX.match(tag)
            if (
                prefix is not None
                and prefix.group(0).lower()
                in JSON_LD_NONVOID_TRUST_CONTAINERS
            ):
                self.invalidate()
            return
        lowered = tag.lower()
        if self.script_open:
            if lowered != "script" or not self.has_safe_end_tag("script"):
                self.invalidate()
                return
            if self.capture:
                self.blocks.append("".join(self.current))
            self.capture = False
            self.script_open = False
            self.current = []
            return
        if self.raw_text_container is not None:
            if lowered != self.raw_text_container:
                return
            if not self.has_safe_end_tag(self.raw_text_container):
                self.invalidate()
                return
            if self.raw_text_container != "plaintext":
                self.raw_text_container = None
            return
        if self.opaque_containers and lowered in JSON_LD_OPAQUE_CONTAINERS:
            if (
                lowered != self.opaque_containers[-1]
                or not self.has_safe_end_tag(lowered)
            ):
                self.invalidate()
                return
            self.opaque_containers.pop()
            return
        if lowered == "select" and self.select_depth:
            if not self.has_safe_end_tag("select"):
                self.invalidate()
                return
            self.select_depth -= 1


def embedded_script_kind(raw_tag: str) -> Optional[str]:
    """Classify one actual inline script that may contain publisher records."""
    attributes = strict_script_attributes(raw_tag)
    if attributes is None:
        return None
    names = [name for name, _value in attributes]
    if len(names) != len(set(names)) or "src" in names:
        return None
    by_name = {name: value for name, value in attributes}
    raw_type = by_name.get("type")
    script_type = raw_type.casefold() if raw_type is not None else None
    script_id = by_name.get("id")
    if script_id == "__NEXT_DATA__" and script_type == "application/json":
        return "next_data"
    if script_id == "__NEXT_DATA__":
        return None
    if script_type in {None, "text/javascript", "application/javascript"}:
        return "publisher_javascript"
    return None


class LinkExtractor(HTMLParser):
    """Collect links and publisher records through one HTML trust boundary."""

    def __init__(
        self,
        base_url: str,
        hosts: Optional[set[str]],
        source_text: str,
    ) -> None:
        super().__init__(convert_charrefs=False)
        self.base_url = base_url
        self.hosts = hosts
        self.source_text = source_text
        self.line_offsets = [0]
        self.line_offsets.extend(
            match.end() for match in re.finditer("\n", source_text)
        )
        self.invalid = False
        self.raw_text_container: Optional[str] = None
        self.opaque_containers: list[str] = []
        self.select_depth = 0
        self.script_open = False
        self.script_kind: Optional[str] = None
        self.script_text: list[str] = []
        self.embedded_script_blocks: list[tuple[str, str]] = []
        self.current_href: Optional[str] = None
        self.current_text: list[str] = []
        self.current_legacy_title_link = False
        self.entries: list[dict[str, Optional[str]]] = []
        self.entries_by_url: dict[str, dict[str, Optional[str]]] = {}
        self.embedded_entries: list[dict[str, Optional[str]]] = []
        self.embedded_entries_by_url: dict[str, dict[str, Optional[str]]] = {}
        self.article_depth = 0
        self.article_link_occurrences: list[
            tuple[dict[str, Optional[str]], str, bool]
        ] = []
        self.article_entry_rejected_at_cap = False
        self.article_published: Optional[str] = None
        self.article_text: list[str] = []
        self.article_heading_depths: list[int] = []
        self.current_article_headline = False
        self.in_time = False
        self.time_datetime: Optional[str] = None
        self.time_text: list[str] = []
        self.date_element_depth = 0
        self.date_element_text: list[str] = []
        self.legacy_title_depth = 0
        self.legacy_title_link: Optional[tuple[str, str]] = None
        self.legacy_pending_title: Optional[tuple[str, str]] = None
        self.legacy_date_depth = 0
        self.legacy_date_text: list[str] = []
        self.legacy_list_item_depth = 0
        if AMBIGUOUS_SCRIPT_CLOSER.search(source_text):
            self.invalidate()

    def invalidate(self) -> None:
        """Discard every article channel after an HTML tokenizer ambiguity."""
        self.invalid = True
        self.raw_text_container = None
        self.opaque_containers = []
        self.select_depth = 0
        self.script_open = False
        self.script_kind = None
        self.script_text = []
        self.embedded_script_blocks = []
        self.entries = []
        self.entries_by_url = {}
        self.embedded_entries = []
        self.embedded_entries_by_url = {}
        self.discard_open_article_markup()
        self.article_depth = 0
        self.article_link_occurrences = []
        self.article_heading_depths = []
        self.legacy_title_depth = 0
        self.legacy_title_link = None
        self.legacy_pending_title = None
        self.legacy_date_depth = 0
        self.legacy_date_text = []
        self.legacy_list_item_depth = 0

    def current_raw_end_tag(self) -> Optional[str]:
        """Recover the end-tag lexeme at the callback's source position."""
        line, column = self.getpos()
        if line <= 0 or line > len(self.line_offsets):
            return None
        start = self.line_offsets[line - 1] + column
        end = self.source_text.find(">", start)
        if end == -1:
            return None
        return self.source_text[start : end + 1]

    def has_safe_end_tag(self, tag: str) -> bool:
        raw_tag = self.current_raw_end_tag()
        return bool(
            raw_tag
            and re.fullmatch(
                rf"</{re.escape(tag)}[\t\n\f\r ]*>",
                raw_tag,
                re.IGNORECASE,
            )
        )

    def discard_open_article_markup(self) -> None:
        """Discard unfinished parser state at one article nesting boundary."""
        self.current_href = None
        self.current_text = []
        self.current_article_headline = False
        self.current_legacy_title_link = False
        self.in_time = False
        self.time_datetime = None
        self.time_text = []
        self.date_element_depth = 0
        self.date_element_text = []
        if self.article_heading_depths:
            self.article_heading_depths[-1] = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if self.invalid:
            return
        if STRICT_HTML_TAG_NAME.fullmatch(tag) is None:
            prefix = HTML_TAG_NAME_PREFIX.match(tag)
            leading = prefix.group(0).lower() if prefix is not None else ""
            if leading in HTML_NONVOID_ARTICLE_TRUST_ELEMENTS - {"a"}:
                self.invalidate()
            elif leading == "a":
                self.current_href = None
                self.current_text = []
                self.current_article_headline = False
                self.current_legacy_title_link = False
            return
        lowered = tag.lower()
        if self.script_open:
            self.invalidate()
            return
        if self.raw_text_container is not None:
            return
        if self.opaque_containers:
            if lowered in JSON_LD_OPAQUE_CONTAINERS:
                self.opaque_containers.append(lowered)
            elif lowered in JSON_LD_RAW_TEXT_CONTAINERS:
                self.raw_text_container = lowered
            elif lowered == "script":
                self.script_open = True
                self.script_kind = None
                self.script_text = []
            return
        if self.select_depth:
            if lowered == "select":
                self.select_depth += 1
            elif lowered in JSON_LD_RAW_TEXT_CONTAINERS:
                self.raw_text_container = lowered
            elif lowered in JSON_LD_OPAQUE_CONTAINERS:
                self.opaque_containers.append(lowered)
            elif lowered == "script":
                self.script_open = True
                self.script_kind = None
                self.script_text = []
            return
        if lowered in JSON_LD_RAW_TEXT_CONTAINERS:
            self.raw_text_container = lowered
            return
        if lowered in JSON_LD_OPAQUE_CONTAINERS:
            self.opaque_containers.append(lowered)
            return
        if lowered == "frameset":
            self.invalidate()
            return
        if lowered == "select":
            self.select_depth = 1
            return
        if lowered == "script":
            self.script_open = True
            self.script_kind = embedded_script_kind(
                self.get_starttag_text() or ""
            )
            self.script_text = []
            return

        strict_attributes = strict_html_attributes(
            self.get_starttag_text() or "", lowered
        )
        attribute_names = (
            [name for name, _value in strict_attributes]
            if strict_attributes is not None
            else []
        )
        attributes_valid = (
            strict_attributes is not None
            and len(attribute_names) == len(set(attribute_names))
        )
        attributes = dict(strict_attributes) if attributes_valid else {}
        if not attributes_valid and lowered in {"article", "li"}:
            # These tokens define cross-record scope. Once their raw syntax is
            # ambiguous, a callback parser cannot safely pair later metadata.
            self.invalidate()
            return
        if not attributes_valid and lowered == "a":
            self.current_href = None
            self.current_text = []
            self.current_article_headline = False
            self.current_legacy_title_link = False
            return

        if lowered == "li":
            # The supported legacy publisher wraps each record in one list
            # item, sometimes beneath a single outer ``article`` element. A
            # list-item boundary must never inherit an unfinished record.
            if self.legacy_list_item_depth == 0:
                self.legacy_title_depth = 0
                self.legacy_title_link = None
                self.legacy_pending_title = None
                self.legacy_date_depth = 0
                self.legacy_date_text = []
            self.legacy_list_item_depth += 1

        legacy_paragraph_scope = self.article_depth == 0 or (
            self.article_depth == 1 and self.legacy_list_item_depth == 1
        )
        if lowered == "p" and legacy_paragraph_scope:
            # A new paragraph is an implied boundary for the supported legacy
            # publisher record. Unfinished state never crosses into it.
            self.legacy_title_depth = 0
            self.legacy_title_link = None
            self.legacy_date_depth = 0
            self.legacy_date_text = []
            class_tokens = set((attributes.get("class") or "").casefold().split())
            if "title" in class_tokens:
                self.legacy_pending_title = None
                self.legacy_title_depth = 1
            elif "date" in class_tokens and self.legacy_pending_title is not None:
                self.legacy_date_depth = 1
        if lowered == "article":
            if self.article_depth == 0:
                self.article_link_occurrences = []
                self.article_entry_rejected_at_cap = False
                self.article_published = None
                self.article_text = []
                self.article_heading_depths = []
            # No unfinished heading, anchor, or date capture may cross into a
            # child article. Completed outer occurrences and dates remain.
            self.discard_open_article_markup()
            self.article_depth += 1
            self.article_heading_depths.append(0)
        if lowered == "time" and attributes_valid and self.article_depth == 1:
            self.in_time = True
            self.time_text = []
            self.time_datetime = clean_text(attributes.get("datetime"), 200)
        if self.date_element_depth and lowered not in HTML_VOID_ELEMENTS:
            self.date_element_depth += 1
        if (
            self.legacy_title_depth
            and lowered not in HTML_VOID_ELEMENTS
            and lowered != "p"
        ):
            self.legacy_title_depth += 1
        if (
            self.legacy_date_depth
            and lowered not in HTML_VOID_ELEMENTS
            and lowered != "p"
        ):
            self.legacy_date_depth += 1
        class_tokens = set((attributes.get("class") or "").lower().split())
        semantic_labels = {
            label.casefold()
            for key in ("alt", "aria-label")
            if (label := clean_text(attributes.get(key), 100))
        }
        itemprop_tokens = set((attributes.get("itemprop") or "").casefold().split())
        if (
            lowered not in HTML_VOID_ELEMENTS
            and not self.date_element_depth
            and self.article_depth == 1
            and (
                class_tokens.intersection(
                    {"date", "published", "publication-date", "pubdate"}
                )
                or semantic_labels.intersection(
                    {"date of publication", "publication date", "published date", "公開日"}
                )
                or "datepublished" in itemprop_tokens
            )
        ):
            self.date_element_depth = 1
            self.date_element_text = []
        if (
            lowered in {"h1", "h2", "h3", "h4", "h5", "h6"}
            and self.article_heading_depths
        ):
            self.article_heading_depths[-1] += 1
        if lowered != "a":
            return
        # A new anchor starts a new parser occurrence even when the previous
        # malformed anchor omitted its end tag.  Invalid/no-href anchors must
        # not complete or contaminate that abandoned occurrence later.
        self.current_href = None
        self.current_text = []
        self.current_article_headline = False
        self.current_legacy_title_link = False
        href = attributes.get("href")
        if href:
            # Nested article metadata is outside the enclosing listing card;
            # reject it before even parsing attacker-controlled URL syntax.
            if self.article_depth > 1:
                return
            validated = parsed_public_candidate_url(self.base_url, href)
            if validated is None or not candidate_matches_source_hosts(
                validated, self.hosts
            ):
                return
            absolute, _parsed = validated
            # Stop growing the bounded entry set at 400, but keep observing
            # existing URLs so a later scoped headline can replace an earlier
            # copy/share label at the boundary.
            if (
                len(self.entries) >= 400
                and canonical_url(absolute) not in self.entries_by_url
            ):
                if self.article_depth == 1:
                    self.article_entry_rejected_at_cap = True
                return
            self.current_href = absolute
            self.current_text = []
            self.current_article_headline = (
                self.article_depth == 1
                and bool(self.article_heading_depths)
                and self.article_heading_depths[-1] > 0
            )
            self.current_legacy_title_link = bool(self.legacy_title_depth)

    def handle_data(self, data: str) -> None:
        if self.invalid:
            return
        if self.script_open:
            if self.script_kind is not None:
                self.script_text.append(data)
            return
        if (
            self.raw_text_container is not None
            or self.opaque_containers
            or self.select_depth
        ):
            return
        if self.current_href:
            self.current_text.append(data)
        if self.in_time:
            self.time_text.append(data)
        if self.date_element_depth:
            self.date_element_text.append(data)
        if self.legacy_date_depth:
            self.legacy_date_text.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.script_open:
            self.handle_data(f"&{name};")
        else:
            self.handle_data(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        if self.script_open:
            self.handle_data(f"&#{name};")
        else:
            self.handle_data(html.unescape(f"&#{name};"))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, Optional[str]]]
    ) -> None:
        """Treat an XHTML slash as ignored on every HTML non-void element."""
        if tag.lower() not in HTML_VOID_ELEMENTS:
            self.handle_starttag(tag, attrs)
            return
        super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self.invalid:
            return
        if STRICT_HTML_TAG_NAME.fullmatch(tag) is None:
            prefix = HTML_TAG_NAME_PREFIX.match(tag)
            if (
                prefix is not None
                and prefix.group(0).lower()
                in HTML_NONVOID_ARTICLE_TRUST_ELEMENTS
            ):
                self.invalidate()
            return
        lowered = tag.lower()
        if self.script_open:
            if lowered != "script" or not self.has_safe_end_tag("script"):
                self.invalidate()
                return
            if self.script_kind is not None:
                block = "".join(self.script_text)
                if (
                    len(self.embedded_script_blocks) < EMBEDDED_SCRIPT_MAX_BLOCKS
                    and len(block.encode("utf-8")) <= EMBEDDED_SCRIPT_MAX_BYTES
                ):
                    self.embedded_script_blocks.append((self.script_kind, block))
            self.script_open = False
            self.script_kind = None
            self.script_text = []
            return
        if self.raw_text_container is not None:
            if lowered != self.raw_text_container:
                return
            if not self.has_safe_end_tag(self.raw_text_container):
                self.invalidate()
                return
            if self.raw_text_container != "plaintext":
                self.raw_text_container = None
            return
        if self.opaque_containers:
            if lowered not in JSON_LD_OPAQUE_CONTAINERS:
                return
            if (
                lowered != self.opaque_containers[-1]
                or not self.has_safe_end_tag(lowered)
            ):
                self.invalidate()
                return
            self.opaque_containers.pop()
            return
        if self.select_depth:
            if lowered != "select":
                return
            if not self.has_safe_end_tag("select"):
                self.invalidate()
                return
            self.select_depth -= 1
            return
        if (
            lowered in HTML_NONVOID_ARTICLE_TRUST_ELEMENTS
            and not self.has_safe_end_tag(lowered)
        ):
            self.invalidate()
            return
        if lowered == "article" and self.article_depth:
            self.discard_open_article_markup()
        if lowered == "time" and self.in_time:
            completed_time = validated_publication_date(
                self.time_datetime
            ) or validated_publication_date(
                " ".join(self.time_text)
            )
            self.article_published = self.article_published or completed_time
            self.in_time = False
            self.time_datetime = None
            self.time_text = []
        if self.date_element_depth and lowered not in HTML_VOID_ELEMENTS:
            self.date_element_depth -= 1
            if self.date_element_depth == 0:
                self.article_published = self.article_published or validated_publication_date(
                    " ".join(self.date_element_text)
                )
                self.date_element_text = []
        if lowered == "article" and self.article_depth:
            self.article_depth -= 1
            if self.article_heading_depths:
                self.article_heading_depths.pop()
            if self.article_heading_depths:
                self.article_heading_depths[-1] = 0
            if self.article_depth == 0:
                self.article_published = validated_publication_date(
                    self.article_published
                )
                selected = next(
                    (
                        occurrence for occurrence in self.article_link_occurrences
                        if occurrence[2]
                    ),
                    (
                        self.article_link_occurrences[0]
                        if len(self.article_link_occurrences) == 1
                        and not self.article_entry_rejected_at_cap
                        else None
                    ),
                )
                if selected is not None:
                    candidate, article_title, is_headline = selected
                    if is_headline or not candidate["title"]:
                        candidate["title"] = article_title
                    candidate["candidate_provenance"] = "article"
                    candidate["published"] = (
                        candidate["published"] or self.article_published
                    )
                self.article_text = []
                self.article_link_occurrences = []
                self.article_entry_rejected_at_cap = False
                self.article_heading_depths = []
        if (
            lowered in {"h1", "h2", "h3", "h4", "h5", "h6"}
            and self.article_heading_depths
            and self.article_heading_depths[-1]
        ):
            self.article_heading_depths[-1] -= 1
        if self.legacy_title_depth and lowered not in HTML_VOID_ELEMENTS:
            self.legacy_title_depth -= 1
            if self.legacy_title_depth == 0:
                self.legacy_pending_title = self.legacy_title_link
                self.legacy_title_link = None
        if self.legacy_date_depth and lowered not in HTML_VOID_ELEMENTS:
            self.legacy_date_depth -= 1
            if self.legacy_date_depth == 0:
                if self.legacy_pending_title is not None:
                    title, url = self.legacy_pending_title
                    self.append_embedded_entry(
                        title,
                        url,
                        " ".join(self.legacy_date_text),
                    )
                self.legacy_pending_title = None
                self.legacy_date_text = []
        if lowered == "li" and self.legacy_list_item_depth:
            self.legacy_list_item_depth -= 1
            if self.legacy_list_item_depth == 0:
                self.legacy_title_depth = 0
                self.legacy_title_link = None
                self.legacy_pending_title = None
                self.legacy_date_depth = 0
                self.legacy_date_text = []
        if lowered != "a" or not self.current_href:
            return
        parsed = urllib.parse.urlsplit(self.current_href)
        title = clean_text(" ".join(self.current_text), 300)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and title
        ):
            entry_key = canonical_url(self.current_href)
            entry = self.entries_by_url.get(entry_key)
            if entry is None:
                entry = {
                    "title": title,
                    "url": self.current_href,
                    "published": None,
                    "summary": None,
                    "candidate_provenance": None,
                }
                self.entries.append(entry)
                self.entries_by_url[entry_key] = entry
            elif self.current_article_headline:
                # Listing cards often expose copy/share controls before the real
                # same-URL heading.  Keep URL deduplication, but let the scoped
                # article headline replace that control label deterministically.
                entry["title"] = title
            if self.article_depth == 1:
                if (
                    not self.current_article_headline
                    and entry["candidate_provenance"] is None
                ):
                    # A URL first seen in generic navigation has no trusted
                    # article title.  Clear that label so the unambiguous
                    # article occurrence can fill it at the article boundary.
                    entry["title"] = None
                self.article_link_occurrences.append(
                    (entry, title, self.current_article_headline)
                )
            if self.current_legacy_title_link:
                self.legacy_title_link = (title, self.current_href)
        self.current_href = None
        self.current_text = []
        self.current_article_headline = False
        self.current_legacy_title_link = False

    def append_embedded_entry(self, title: str, url: str, published: str) -> None:
        """Append one structurally scoped legacy publisher-list record."""
        validated_url = parsed_public_candidate_url(
            self.base_url, html.unescape(url)
        )
        if validated_url is None:
            return
        absolute, _parsed = validated_url
        cleaned_title = clean_text(title, 300)
        clean_published = validated_publication_date(published)
        if (
            not candidate_matches_source_hosts(validated_url, self.hosts)
            or not cleaned_title
            or not clean_published
        ):
            return
        key = canonical_url(absolute)
        existing = self.embedded_entries_by_url.get(key)
        if existing is not None:
            existing["published"] = existing["published"] or clean_published
            return
        if len(self.embedded_entries) >= 200:
            return
        entry = {
            "title": cleaned_title,
            "url": absolute,
            "published": clean_published,
            "summary": None,
            "candidate_provenance": "article",
        }
        self.embedded_entries.append(entry)
        self.embedded_entries_by_url[key] = entry


def html_trust_state_is_safe(parser: Any) -> bool:
    """Require every tokenizer-sensitive trust container to close safely."""
    return not (
        parser.invalid
        or parser.script_open
        or parser.raw_text_container is not None
        or parser.opaque_containers
        or parser.select_depth
    )


def extract_json_ld(
    content: bytes,
    base_url: str,
    hosts: Optional[set[str]] = None,
) -> list[dict[str, Optional[str]]]:
    """Extract article metadata exposed by public JSON-LD blocks."""
    text = content.decode("utf-8", errors="replace")
    # HTMLParser can absorb a whitespace-split pseudo closer into script raw
    # text and later close that script at an unrelated safe `</script>`.  That
    # makes already-collected peer blocks look trustworthy even though the
    # HTML5 tokenizer boundary is ambiguous, so reject the whole document.
    if AMBIGUOUS_SCRIPT_CLOSER.search(text):
        return []
    parser = JsonLdBlockExtractor(text)
    try:
        parser.feed(text)
        parser.close()
    except (AssertionError, ValueError):
        # Malformed markup must not turn public-page extraction into a crash.
        return []
    if not html_trust_state_is_safe(parser):
        return []
    entries: list[dict[str, Optional[str]]] = []
    entries_by_url: dict[str, dict[str, Optional[str]]] = {}

    for block in parser.blocks:
        block_entries: list[dict[str, Optional[str]]] = []
        block_entries_by_url: dict[str, dict[str, Optional[str]]] = {}
        try:
            # Script elements contain raw text: HTML character references are
            # literal JSON string content and must not be decoded before the
            # JSON parser applies JSON's own escape rules.
            root = json.loads(block)
            pending: list[tuple[Any, int, str]] = [
                (root, 0, JSON_LD_CONTEXT_UNBOUND)
            ]
            visited_nodes = 0
            while pending:
                value, depth, inherited_context_state = pending.pop()
                visited_nodes += 1
                if (
                    depth > JSON_LD_MAX_DEPTH
                    or visited_nodes > JSON_LD_MAX_NODES
                ):
                    raise ValueError("JSON-LD traversal budget exceeded")
                if isinstance(value, list):
                    if (
                        visited_nodes + len(pending) + len(value)
                        > JSON_LD_MAX_NODES
                    ):
                        raise ValueError("JSON-LD traversal budget exceeded")
                    pending.extend(
                        (item, depth + 1, inherited_context_state)
                        for item in reversed(value)
                    )
                    continue
                if not isinstance(value, dict):
                    continue

                context_state = inherited_context_state
                if "@context" in value:
                    context_state = schema_json_ld_context_state(
                        value.get("@context"), inherited_context_state
                    )

                raw_title = value.get("headline") or value.get("name")
                title = (
                    clean_text(raw_title, 300)
                    if isinstance(raw_title, str)
                    else None
                )
                raw_url = value.get("url") or value.get("mainEntityOfPage")
                if isinstance(raw_url, dict):
                    raw_url = raw_url.get("@id") or raw_url.get("url")
                raw_published = value.get("datePublished")
                published = (
                    validated_json_ld_publication_date(raw_published)
                    if isinstance(raw_published, str)
                    else None
                )
                raw_summary = value.get("description")
                summary = (
                    clean_text(raw_summary, 800)
                    if isinstance(raw_summary, str)
                    else None
                )
                raw_type = value.get("@type")
                types = raw_type if isinstance(raw_type, list) else [raw_type]
                article_like = any(
                    is_schema_article_type(
                        item, context_state == JSON_LD_CONTEXT_TRUSTED
                    )
                    for item in types
                )
                validated_url = (
                    parsed_public_candidate_url(base_url, raw_url)
                    if isinstance(raw_url, str)
                    else None
                )
                if (
                    article_like
                    and validated_url is not None
                    and candidate_matches_source_hosts(validated_url, hosts)
                ):
                    entry_key = canonical_url(validated_url[0])
                    existing = block_entries_by_url.get(entry_key)
                    if existing is not None:
                        existing["title"] = existing["title"] or title
                        existing["published"] = (
                            existing["published"] or published
                        )
                        existing["summary"] = existing["summary"] or summary
                    else:
                        entry = {
                            "title": title,
                            "url": validated_url[0],
                            "published": published,
                            "summary": summary,
                            "candidate_provenance": "json_ld",
                        }
                        block_entries.append(entry)
                        block_entries_by_url[entry_key] = entry

                # A JSON-LD context declaration defines how sibling/descendant
                # data is interpreted; it is not itself a graph node.  Never
                # traverse context mappings as article records, even when a
                # malicious mapping contains record-like keys.
                nested_values = [
                    nested
                    for key, nested in value.items()
                    if key != "@context"
                ]
                if (
                    visited_nodes + len(pending) + len(nested_values)
                    > JSON_LD_MAX_NODES
                ):
                    raise ValueError("JSON-LD traversal budget exceeded")
                pending.extend(
                    (nested, depth + 1, context_state)
                    for nested in reversed(nested_values)
                )
        except (ValueError, TypeError, RecursionError, OverflowError):
            # A malformed or pathologically structured script block must not
            # leak partial entries or prevent later JSON-LD/HTML peers.
            continue

        for entry in block_entries:
            entry_key = canonical_url(str(entry["url"]))
            existing = entries_by_url.get(entry_key)
            if existing is not None:
                existing["published"] = (
                    existing["published"] or entry["published"]
                )
                existing["summary"] = existing["summary"] or entry["summary"]
            elif entry["title"] and len(entries) < 200:
                entries.append(entry)
                entries_by_url[entry_key] = entry
    return entries[:200]


def extract_embedded_article_metadata(
    content: bytes,
    base_url: str,
    hosts: Optional[set[str]] = None,
) -> list[dict[str, Optional[str]]]:
    """Extract records only from structurally trusted markup/script blocks."""
    text = content.decode("utf-8", errors="replace")
    parser = LinkExtractor(base_url, hosts, text)
    try:
        parser.feed(text)
        parser.close()
    except (AssertionError, ValueError):
        return []
    if not html_trust_state_is_safe(parser):
        parser.invalidate()
    populate_embedded_script_entries(parser)
    return [] if parser.invalid else parser.embedded_entries[:200]


def populate_embedded_script_entries(parser: LinkExtractor) -> None:
    """Parse bounded publisher data captured by the trusted HTML parser."""
    if not html_trust_state_is_safe(parser):
        return
    for kind, block in parser.embedded_script_blocks:
        if kind == "publisher_javascript":
            if len(block.encode("utf-8")) > PUBLISHER_JAVASCRIPT_MAX_BYTES:
                continue
            for match in re.finditer(
                r"\{'url':'([^']+)','title':'([^']*)'[^{]{0,5000}?'date':'"
                r"(20\d{2})/(\d{1,2})/(\d{1,2})'",
                block,
                re.DOTALL,
            ):
                year, month, day = map(int, match.groups()[2:])
                try:
                    published = date(year, month, day).isoformat()
                except ValueError:
                    continue
                parser.append_embedded_entry(
                    match.group(2) or match.group(1),
                    match.group(1),
                    published,
                )
            continue
        if kind != "next_data":
            continue
        try:
            root = json.loads(block)
            pending: list[tuple[Any, int]] = [(root, 0)]
            visited = 0
            while pending:
                value, depth = pending.pop()
                visited += 1
                if depth > JSON_LD_MAX_DEPTH or visited > JSON_LD_MAX_NODES:
                    raise ValueError("embedded JSON traversal budget exceeded")
                if isinstance(value, list):
                    if visited + len(pending) + len(value) > JSON_LD_MAX_NODES:
                        raise ValueError("embedded JSON traversal budget exceeded")
                    pending.extend((item, depth + 1) for item in reversed(value))
                    continue
                if not isinstance(value, dict):
                    continue
                metadata = value.get("metadata")
                metadata = metadata if isinstance(metadata, dict) else {}
                article_metadata = value.get("articleMetadata")
                article_metadata = (
                    article_metadata if isinstance(article_metadata, dict) else {}
                )
                data = value.get("data")
                data = data if isinstance(data, dict) else {}
                title = next(
                    (
                        candidate
                        for candidate in (
                            article_metadata.get("title"),
                            metadata.get("title"),
                            value.get("title"),
                            value.get("headline"),
                        )
                        if isinstance(candidate, str)
                    ),
                    None,
                )
                published = next(
                    (
                        candidate
                        for candidate in (
                            metadata.get("datePublished"),
                            value.get("datePublished"),
                            data.get("date"),
                        )
                        if isinstance(candidate, str)
                    ),
                    None,
                )
                issue_id = data.get("issueId", value.get("issueId"))
                volume = data.get("issueVolume", value.get("issueVolume"))
                number = data.get("issueNumber", value.get("issueNumber"))
                if (
                    title is not None
                    and published is not None
                    and isinstance(issue_id, str)
                    and isinstance(volume, int)
                    and not isinstance(volume, bool)
                    and isinstance(number, int)
                    and not isinstance(number, bool)
                ):
                    parser.append_embedded_entry(
                        title,
                        f"/newsletters/{issue_id}/{volume}/{number}",
                        published,
                    )
                nested = list(value.values())
                if visited + len(pending) + len(nested) > JSON_LD_MAX_NODES:
                    raise ValueError("embedded JSON traversal budget exceeded")
                pending.extend((item, depth + 1) for item in reversed(nested))
        except (ValueError, TypeError, RecursionError, OverflowError):
            continue


def canonical_url(value: str) -> str:
    """Normalize an HTTP URL for same-article evidence comparison."""
    parsed = urllib.parse.urlsplit(value)
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


def extract_primary_publication_date(
    content: bytes,
    final_url: str,
    hosts: Optional[set[str]] = None,
) -> Optional[str]:
    """Return only the publication date belonging to the fetched article itself."""
    published, _provenance = extract_primary_publication_evidence(
        content, final_url, hosts
    )
    return published


def extract_primary_publication_evidence(
    content: bytes,
    final_url: str,
    hosts: Optional[set[str]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Return one structurally verified primary date and its actual channel."""
    target = canonical_url(final_url)
    for entry in extract_json_ld(content, final_url, hosts):
        if entry.get("url") and canonical_url(str(entry["url"])) == target:
            published = validated_publication_date(entry.get("published"))
            if published:
                return published, "json_ld"
    text = content.decode("utf-8", errors="replace")
    parser = JsonLdBlockExtractor(text)
    try:
        parser.feed(text)
        parser.close()
    except (AssertionError, ValueError):
        return None, None
    if not html_trust_state_is_safe(parser):
        return None, None
    dates = set(parser.meta_dates)
    if len(dates) != 1:
        return None, None
    return dates.pop(), "html_meta"


def extract_content(
    content: bytes,
    content_type: str,
    final_url: str,
    hosts: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Create a compact, inert index while retaining raw evidence separately."""
    prefix = content.lstrip()[:32].lower()
    if "xml" in content_type or prefix.startswith((b"<rss", b"<feed", b"<?xml")):
        try:
            entries = extract_xml(content, final_url, hosts)
            return {"format": "feed", "entry_count": len(entries), "entries": entries}
        except ET.ParseError:
            pass
    text = content.decode("utf-8", errors="replace")
    parser = LinkExtractor(final_url, hosts, text)
    try:
        parser.feed(text)
        parser.close()
    except (AssertionError, ValueError):
        parser.invalidate()
    if not html_trust_state_is_safe(parser):
        parser.invalidate()
    populate_embedded_script_entries(parser)
    combined: list[dict[str, Optional[str]]] = []
    by_url: dict[str, dict[str, Optional[str]]] = {}
    trusted_entries = [] if parser.invalid else [
        *extract_json_ld(content, final_url, hosts),
        *parser.embedded_entries,
        *parser.entries,
    ]
    for entry in trusted_entries:
        url = entry.get("url")
        if not isinstance(url, str):
            combined.append(entry)
            continue
        validated_url = parsed_public_candidate_url(final_url, url)
        if validated_url is None or not candidate_matches_source_hosts(
            validated_url, hosts
        ):
            continue
        entry["url"] = validated_url[0]
        key = canonical_url(validated_url[0])
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = entry
            combined.append(entry)
            continue
        existing["published"] = validated_publication_date(
            existing.get("published") or entry.get("published")
        )
        existing["candidate_provenance"] = (
            existing.get("candidate_provenance") or entry.get("candidate_provenance")
        )
    combined = combined[:400]
    candidates = [
        entry for entry in combined
        if entry.get("candidate_provenance") in {"article", "json_ld"}
    ]
    return {
        "format": "html_links",
        "entry_count": len(combined),
        "candidate_entry_count": len(candidates),
        "date_evidence_count": sum(
            bool(validated_publication_date(entry.get("published")))
            for entry in candidates
        ),
        "entries": combined,
    }


def script_data_enters_double_escaped_state(data: str) -> bool:
    """Detect a script end tag that HTMLParser would close too early."""
    position = 0
    while True:
        escape_start = data.find("<!--", position)
        if escape_start < 0:
            return False
        escaped_position = escape_start + 4
        escape_end = data.find("-->", escaped_position)
        scan_end = escape_end if escape_end >= 0 else len(data)
        nested_script = SCRIPT_DOUBLE_ESCAPE_START.search(
            data,
            escaped_position,
            scan_end,
        )
        if nested_script is not None:
            return True
        if escape_end < 0:
            return False
        position = escape_end + 3


class AccessConstraintMarkupParser(HTMLParser):
    """Extract explicit challenge evidence from a conservative HTML subset."""

    MAX_OPAQUE_DEPTH = 128
    MAX_RAW_START_TAG_CHARS = 16 * 1024
    MAX_WIDGET_ATTRIBUTE_CHARS = 4096
    FOREIGN_CONTENT_CONTAINERS = frozenset({"svg", "math"})
    RAW_TEXT_CONTAINERS = frozenset({
        "iframe",
        "noembed",
        "noframes",
        "plaintext",
        "script",
        "style",
        "textarea",
        "xmp",
    })
    OPAQUE_CONTAINERS = frozenset(
        {"template", "noscript", "select"}
    ) | FOREIGN_CONTENT_CONTAINERS | RAW_TEXT_CONTAINERS
    HEAD_OPAQUE_CONTAINERS = frozenset(
        {"script", "style", "template", "noscript", "noframes"}
    )
    AFTER_HEAD_OPAQUE_CONTAINERS = frozenset(
        {"script", "style", "template", "noframes"}
    )
    HEAD_METADATA_TAGS = frozenset(
        {"base", "basefont", "bgsound", "link", "meta"}
    )
    IMPLIED_HEAD_START_TAGS = (
        HEAD_METADATA_TAGS | HEAD_OPAQUE_CONTAINERS | {"title"}
    )
    CAPTCHA_WIDGET_TAGS = frozenset({"div", "form", "iframe", "section"})
    CAPTCHA_WIDGET_TOKENS = frozenset({
        "cf-turnstile",
        "g-recaptcha",
        "h-captcha",
        "hcaptcha",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raw_end_tag_start: Optional[int] = None
        self.head_seen = False
        self.head_closed = False
        self.in_head = False
        self.body_started = False
        self.body_closed = False
        self.form_open = False
        self.opaque_containers: list[str] = []
        self.capture_title = False
        self.invalid_title = False
        self.title_elements = 0
        self.title_parts: list[str] = []
        self.document_title: Optional[str] = None
        self.title_structure_invalid = False
        self.structure_invalid = False
        self.captcha_widget = False

    def current_raw_end_tag(self) -> Optional[str]:
        """Recover the raw end-tag token at the current parser position."""
        start = self.raw_end_tag_start
        if start is None or start < 0 or start >= len(self.rawdata):
            return None
        end = self.rawdata.find(">", start)
        if end == -1:
            return None
        return self.rawdata[start : end + 1]

    def parse_endtag(self, index: int) -> int:
        """Expose the parser's bounded raw token index only during its callback."""
        self.raw_end_tag_start = index
        try:
            return super().parse_endtag(index)
        finally:
            self.raw_end_tag_start = None

    def parse_starttag(self, index: int) -> int:
        """Reject oversized raw tags before HTMLParser materializes attributes."""
        end = self.check_for_whole_start_tag(index)
        if end < 0:
            return end
        if end - index > self.MAX_RAW_START_TAG_CHARS:
            self.structure_invalid = True
            return end
        return super().parse_starttag(index)

    def close(self) -> None:
        """Finalize a structurally complete optional head at end of input."""
        super().close()
        if (
            self.in_head
            and not self.capture_title
            and not self.opaque_containers
            and not self.structure_invalid
            and not self.title_structure_invalid
            and not self.body_started
            and not self.body_closed
        ):
            self.in_head = False
            self.head_closed = True

    def has_safe_end_tag(self, tag: str) -> bool:
        """Require an exact end-tag name plus HTML5 ASCII whitespace only."""
        raw_tag = self.current_raw_end_tag()
        return bool(
            raw_tag
            and re.fullmatch(
                rf"</{re.escape(tag)}[\t\n\f\r ]*>",
                raw_tag,
                re.IGNORECASE,
            )
        )

    def strict_callback_attributes(
        self, tag: str
    ) -> Optional[dict[str, Optional[str]]]:
        """Reparse the raw tag and reject ambiguous or duplicate attributes."""
        raw_tag = self.get_starttag_text() or ""
        parsed = strict_html_attributes(raw_tag, tag)
        if parsed is None:
            return None
        attributes: dict[str, Optional[str]] = {}
        for name, value in parsed:
            if name in attributes:
                return None
            attributes[name] = value
        return attributes

    def start_implicit_body(self) -> None:
        """Advance monotonically when a token belongs to the document body."""
        if self.body_closed:
            return
        if self.capture_title:
            self.invalid_title = True
        if self.in_head:
            self.in_head = False
            self.head_closed = True
        self.body_started = True

    def start_implied_head(self) -> None:
        """Infer the optional head start for an initial head-compatible token."""
        if (
            not self.head_seen
            and not self.head_closed
            and not self.in_head
            and not self.body_started
            and not self.body_closed
        ):
            self.head_seen = True
            self.in_head = True

    def is_after_head_compatible(self, tag: str) -> bool:
        """Recognize supported tokens processed with head rules after </head>."""
        return bool(
            self.head_seen
            and self.head_closed
            and not self.body_started
            and not self.body_closed
            and tag in self.AFTER_HEAD_OPAQUE_CONTAINERS | self.HEAD_METADATA_TAGS
        )

    def record_captcha_widget(self, tag: str) -> None:
        """Accept known widget tokens only from strict structural attributes."""
        if tag not in self.CAPTCHA_WIDGET_TAGS:
            return
        attributes = self.strict_callback_attributes(tag)
        if attributes is None:
            return
        relevant_values = [
            attributes.get(name) for name in ("class", "id", "aria-label", "title")
        ]
        if any(
            isinstance(value, str)
            and len(value) > self.MAX_WIDGET_ATTRIBUTE_CHARS
            for value in relevant_values
        ):
            return
        widget_token = False
        for name in ("class", "id"):
            value = attributes.get(name)
            if not value or not value.isascii():
                continue
            if any(
                match.group(0).lower() in self.CAPTCHA_WIDGET_TOKENS
                for match in re.finditer(r"[^\t\n\f\r ]+", value, re.ASCII)
            ):
                widget_token = True
                break
        semantic_labels = (
            attributes.get("aria-label"),
            attributes.get("title"),
        )
        if widget_token or (
            any(
                isinstance(semantic_label, str)
                and semantic_label.isascii()
                and re.fullmatch(
                    r"[\t\n\f\r ]*captcha challenge[\t\n\f\r ]*",
                    semantic_label,
                    re.IGNORECASE | re.ASCII,
                )
                for semantic_label in semantic_labels
            )
        ):
            self.captcha_widget = True

    def push_opaque_container(self, tag: str) -> None:
        """Bound parser state and poison evidence when nesting is excessive."""
        if len(self.opaque_containers) >= self.MAX_OPAQUE_DEPTH:
            self.structure_invalid = True
            return
        self.opaque_containers.append(tag)

    def handle_starttag(
        self, tag: str, _attrs: list[tuple[str, Optional[str]]]
    ) -> None:
        normalized = tag.lower()
        if self.opaque_containers:
            if self.opaque_containers[-1] == "plaintext":
                return
            if normalized in self.OPAQUE_CONTAINERS:
                self.push_opaque_container(normalized)
            return
        if normalized == "frameset":
            self.structure_invalid = True
            return
        if normalized in self.IMPLIED_HEAD_START_TAGS:
            self.start_implied_head()
        if normalized == "body":
            if self.body_started or self.body_closed:
                self.structure_invalid = True
                return
            if self.capture_title:
                self.invalid_title = True
            if self.in_head:
                self.in_head = False
                self.head_closed = True
            self.body_started = True
            return
        if normalized == "head":
            if self.head_seen or self.head_closed or self.body_started or self.in_head:
                self.structure_invalid = True
                self.title_structure_invalid = True
                return
            self.head_seen = True
            self.in_head = True
            return
        if normalized in self.OPAQUE_CONTAINERS:
            after_head_opaque = bool(
                normalized in self.AFTER_HEAD_OPAQUE_CONTAINERS
                and self.is_after_head_compatible(normalized)
            )
            if not after_head_opaque and (
                normalized not in self.HEAD_OPAQUE_CONTAINERS or not self.in_head
            ):
                self.start_implicit_body()
            if self.capture_title:
                self.invalid_title = True
            if (
                normalized == "iframe"
                and self.body_started
                and not self.body_closed
            ):
                self.record_captcha_widget(normalized)
            self.push_opaque_container(normalized)
            return
        if normalized == "title":
            if not self.in_head or self.body_started:
                self.start_implicit_body()
                self.title_structure_invalid = True
                return
            self.title_elements += 1
            attributes = self.strict_callback_attributes(normalized)
            if (
                self.title_elements == 1
                and attributes == {}
                and not self.capture_title
            ):
                self.capture_title = True
                self.invalid_title = False
                self.title_parts = []
            else:
                self.title_structure_invalid = True
                if self.capture_title:
                    self.invalid_title = True
            return
        if self.capture_title:
            self.invalid_title = True
            return
        head_metadata = bool(
            normalized in self.HEAD_METADATA_TAGS
            and (self.in_head or self.is_after_head_compatible(normalized))
        )
        if normalized != "html" and not head_metadata:
            self.start_implicit_body()
        if self.body_started and not self.body_closed:
            if normalized == "form":
                if self.form_open:
                    return
                self.form_open = True
            self.record_captcha_widget(normalized)

    def handle_startendtag(
        self, tag: str, _attrs: list[tuple[str, Optional[str]]]
    ) -> None:
        normalized = tag.lower()
        if self.capture_title:
            self.invalid_title = True
        if self.opaque_containers:
            if self.opaque_containers[-1] == "plaintext":
                return
            if normalized in self.RAW_TEXT_CONTAINERS:
                self.push_opaque_container(normalized)
            return
        if normalized == "frameset":
            self.structure_invalid = True
            return
        if normalized in self.IMPLIED_HEAD_START_TAGS:
            self.start_implied_head()
        if normalized in self.FOREIGN_CONTENT_CONTAINERS:
            self.start_implicit_body()
            return
        if normalized in self.RAW_TEXT_CONTAINERS:
            after_head_opaque = bool(
                normalized in self.AFTER_HEAD_OPAQUE_CONTAINERS
                and self.is_after_head_compatible(normalized)
            )
            if not after_head_opaque and (
                normalized not in self.HEAD_OPAQUE_CONTAINERS or not self.in_head
            ):
                self.start_implicit_body()
            self.push_opaque_container(normalized)
            return
        if normalized in self.OPAQUE_CONTAINERS or normalized in {"body", "head", "title"}:
            self.structure_invalid = True
            self.title_structure_invalid = True
            return
        if normalized == "form":
            self.start_implicit_body()
            if self.body_started and not self.body_closed and not self.form_open:
                # HTML ignores the slash for this non-void element.  Retain the
                # form pointer, but keep the conservative no-widget policy for
                # a self-closing callback.
                self.form_open = True
            return
        head_metadata = bool(
            normalized in self.HEAD_METADATA_TAGS
            and (self.in_head or self.is_after_head_compatible(normalized))
        )
        if normalized != "html" and not head_metadata:
            self.start_implicit_body()
        # Every supported widget element is non-void in HTML.  A callback for
        # self-closing syntax does not prove a stable widget node, so it is
        # never accepted as challenge evidence.

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if self.opaque_containers and self.opaque_containers[-1] == "plaintext":
            return
        if not self.opaque_containers and normalized == "frameset":
            self.structure_invalid = True
            return
        if (
            normalized in self.OPAQUE_CONTAINERS | {"body", "br", "head", "html", "title"}
            and not self.has_safe_end_tag(normalized)
        ):
            self.structure_invalid = True
            if normalized in {"head", "title"}:
                self.title_structure_invalid = True
            if self.capture_title:
                self.invalid_title = True
            return
        if self.opaque_containers:
            if normalized in self.OPAQUE_CONTAINERS:
                if self.opaque_containers[-1] != normalized:
                    self.structure_invalid = True
                    return
                self.opaque_containers.pop()
            return
        if normalized in self.OPAQUE_CONTAINERS:
            self.structure_invalid = True
            return
        if normalized in {"br", "html"} and self.in_head:
            self.start_implicit_body()
            return
        if normalized == "form":
            if self.form_open:
                if not self.has_safe_end_tag(normalized):
                    self.structure_invalid = True
                    return
                self.form_open = False
            return
        if normalized == "title":
            if not self.capture_title:
                if self.in_head:
                    self.title_structure_invalid = True
                return
            if self.invalid_title:
                self.title_structure_invalid = True
            else:
                self.document_title = "".join(self.title_parts)
            self.capture_title = False
            self.invalid_title = False
            self.title_parts = []
            return
        if self.capture_title:
            self.invalid_title = True
        if normalized == "head":
            if not self.in_head:
                self.structure_invalid = True
                self.title_structure_invalid = True
                return
            self.in_head = False
            self.head_closed = True
        elif normalized == "body":
            if not self.body_started and self.head_seen and (
                self.in_head or self.head_closed
            ):
                self.start_implicit_body()
            if not self.body_started or self.body_closed:
                self.structure_invalid = True
                return
            self.body_closed = True

    def handle_data(self, data: str) -> None:
        if (
            self.opaque_containers
            and self.opaque_containers[-1] == "script"
            and script_data_enters_double_escaped_state(data)
        ):
            self.structure_invalid = True
        if self.capture_title:
            self.title_parts.append(data)
            return
        if self.opaque_containers:
            return
        if re.search(r"[^\t\n\f\r ]", data):
            self.start_implicit_body()

    def handle_comment(self, _data: str) -> None:
        if self.capture_title:
            self.invalid_title = True

    @property
    def vercel_checkpoint(self) -> bool:
        """Return true only for one completed, unambiguous document title."""
        return bool(
            not self.structure_invalid
            and not self.title_structure_invalid
            and not self.capture_title
            and not self.in_head
            and not self.opaque_containers
            and self.head_seen
            and self.head_closed
            and self.title_elements == 1
            and isinstance(self.document_title, str)
            and re.fullmatch(
                r"[\t\n\f\r ]*vercel security checkpoint[\t\n\f\r ]*",
                self.document_title,
                re.IGNORECASE | re.ASCII,
            )
        )

    @property
    def has_captcha_evidence(self) -> bool:
        """Reject evidence when opaque-container structure remains ambiguous."""
        return bool(
            not self.structure_invalid
            and not self.title_structure_invalid
            and not self.capture_title
            and not self.opaque_containers
            and (self.vercel_checkpoint or self.captcha_widget)
        )


def parse_access_constraint_markup(
    text: str,
) -> Optional[AccessConstraintMarkupParser]:
    """Parse challenge markup once and return no evidence on parser failure."""
    parser = AccessConstraintMarkupParser()
    try:
        parser.feed(text)
        parser.close()
    except (AssertionError, ValueError):
        return None
    return parser


def has_vercel_security_checkpoint_title(text: str) -> bool:
    """Require a parsed title element instead of a matching raw byte sequence."""
    parser = parse_access_constraint_markup(text)
    return bool(parser and parser.vercel_checkpoint)


def detect_access_constraint(content: bytes) -> Optional[str]:
    """Recognize explicit gate markup without treating generic HTTP errors as proof."""
    decoded = content.decode("utf-8", errors="replace")
    if decoded.startswith("\ufeff"):
        decoded = decoded[1:]
    parsed_constraint = parse_access_constraint_markup(decoded)
    if parsed_constraint and parsed_constraint.has_captcha_evidence:
        return "captcha"
    text = content[:LEGACY_CONSTRAINT_TEXT_BYTES].decode(
        "utf-8", errors="replace"
    ).lower()
    if re.search(r"<input[^>]+type=[\"']password[\"']", text) and re.search(
        r"sign[ -]?in|log[ -]?in|ログイン", text
    ):
        return "login"
    if re.search(
        r"subscribe (?:to|in order to) (?:continue|read)|\bsubscription required\b|"
        r"購読.{0,30}(?:続きを読む|必要)|会員限定",
        text,
    ):
        return "paywall"
    return None


def collect_source(
    index: int,
    source: dict[str, Any],
    hosts: set[str],
    output_root: Path,
    run_date: Optional[date] = None,
) -> dict[str, Any]:
    """Try feed first, then the public page, retaining an audit of both."""
    attempts: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    candidates = (("rss", source["feed_url"]), ("public_page", source["page_url"]))
    for method, url in candidates:
        if not url:
            continue
        try:
            fetched = fetch_url(url, hosts)
            constraint = detect_access_constraint(fetched["content"])
            verified_http_429_captcha = (
                fetched["http_status"] == 429 and constraint == "captcha"
            )
            if verified_http_429_captcha:
                constraint_record = {
                    "method": method, "requested_url": url,
                    "final_url": fetched["final_url"], "constraint": constraint,
                    "http_status": fetched["http_status"],
                }
                attempts.append({
                    "method": method, "url": url, "status": "access_constraint",
                    **constraint_record,
                })
                constraints.append(constraint_record)
                continue
            extract = extract_content(
                fetched["content"],
                fetched["content_type"],
                fetched["final_url"],
                hosts,
            )
            if constraint and extract["entry_count"] < 10:
                constraint_record = {
                    "method": method, "requested_url": url,
                    "final_url": fetched["final_url"], "constraint": constraint,
                    "http_status": fetched["http_status"],
                }
                attempts.append({
                    "method": method, "url": url, "status": "access_constraint",
                    **constraint_record,
                })
                constraints.append(constraint_record)
                continue
            if extract["entry_count"] == 0:
                raise CollectionError("empty_extract")
            if (
                extract["format"] == "html_links"
                and (
                    extract.get("candidate_entry_count", 0) == 0
                    or extract.get("date_evidence_count", 0)
                    != extract.get("candidate_entry_count", 0)
                )
            ):
                raise CollectionError("html_extract_lacks_publication_date_evidence")
            filename = safe_filename(index, source["name"], method)
            destination = output_root / filename
            content = fetched.pop("content")
            with destination.open("xb") as handle:
                handle.write(content)
            digest = hashlib.sha256(content).hexdigest()
            extract_filename = filename.replace(".data", ".extract.json")
            (output_root / extract_filename).write_text(
                json.dumps(extract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            attempts.append({"method": method, "url": url, "status": "fetched"})
            result = {
                "name": source["name"],
                "tier": source["tier"],
                "status": "fetched",
                "method": method,
                "requested_url": url,
                "final_url": fetched["final_url"],
                "content_type": fetched["content_type"],
                "http_status": fetched["http_status"],
                "bytes": destination.stat().st_size,
                "sha256": digest,
                "content_file": filename,
                "extract_file": extract_filename,
                "extracted_entry_count": extract["entry_count"],
                "attempts": attempts,
            }
            if run_date is not None:
                result.update({
                    "jst_window_start": (run_date - timedelta(days=6)).isoformat(),
                    "jst_window_end": run_date.isoformat(),
                    "jst_window_item_count": jst_window_item_count(extract, run_date),
                })
            return result
        except RobotsDisallowed as exc:
            constraint_record = {
                "method": method,
                "requested_url": exc.requested_url,
                "final_url": exc.requested_url,
                "constraint": "robots",
                "http_status": None,
                "robots_url": exc.robots_url,
                "robots_sha256": exc.robots_sha256,
            }
            attempts.append({
                "method": method, "url": url, "status": "access_constraint",
                "reason": str(exc), **constraint_record,
            })
            constraints.append(constraint_record)
        except (CollectionError, OSError, ValueError) as exc:
            attempts.append(
                {"method": method, "url": url, "status": "failed", "reason": str(exc)}
            )
    if attempts and all(item.get("status") == "access_constraint" for item in attempts):
        resolved = constraints[-1]
        return {
            "name": source["name"], "tier": source["tier"],
            "status": "access_constraint", **resolved,
            "content_file": None, "extract_file": None,
            "extracted_entry_count": 0, "attempts": attempts,
        }
    return {
        "name": source["name"],
        "tier": source["tier"],
        "status": "needs_search_fallback",
        "method": None,
        "requested_url": None,
        "final_url": None,
        "content_type": None,
        "http_status": None,
        "bytes": 0,
        "sha256": None,
        "content_file": None,
        "extract_file": None,
        "extracted_entry_count": 0,
        "attempts": attempts,
    }


def resolve_output_root(value: str) -> Path:
    """Keep fetched untrusted bytes inside the caller-bound collection root."""
    trusted_value = os.environ.get("COLLECTION_OUTPUT_ROOT")
    if not trusted_value:
        raise CollectionError("COLLECTION_OUTPUT_ROOT is required")
    trusted = Path(trusted_value).resolve(strict=True)
    output = Path(value)
    if output.is_symlink():
        raise CollectionError("output root escapes the collection staging root")
    parent = output.parent.resolve(strict=True)
    if trusted != parent and trusted not in parent.parents:
        raise CollectionError("output root escapes the collection staging root")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    resolved = output.resolve(strict=True)
    if trusted == resolved or trusted not in resolved.parents:
        raise CollectionError("output root escapes the collection staging root")
    return resolved


def source_hosts(source: dict[str, Any]) -> set[str]:
    """Return only the reviewed hosts belonging to one catalog source."""
    return allowed_hosts([source])


def load_bounded_run_json(path: Path, trusted: Path) -> dict[str, Any]:
    """Read one regular non-symlink JSON file already contained by the run root."""
    parent = path.parent.resolve(strict=True)
    if parent != trusted and trusted not in parent.parents:
        raise CollectionError("JSON input escapes the run root")
    content = stable_regular_bytes(path)
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise CollectionError("JSON input root is invalid")
    return payload


def require_real_directory(path: Path) -> None:
    """Reject symlinked or non-directory components in the runtime layout."""
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode):
        raise CollectionError("resolution preflight runtime layout is invalid")


def canonical_runtime_root() -> Path:
    """Resolve the production runtime independently of agent-controlled input."""
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    if not account_home.is_absolute():
        raise CollectionError("OS account home is not absolute")
    return account_home / CANONICAL_RUNTIME_RELATIVE


def checked_runtime_inputs(request_path: Path) -> tuple[Path, Path, Path, Path]:
    """Derive reviewed check inputs from the immutable flattened runtime layout."""
    runtime = canonical_runtime_root()
    executable = Path(__file__)
    expected_executable = runtime / "collect-public-sources.py"
    if (
        not executable.is_absolute()
        or executable != expected_executable
        or runtime.resolve(strict=True) != runtime
    ):
        raise CollectionError("resolution verifier is outside the canonical runtime")
    executable_metadata = os.lstat(executable)
    if (
        not stat.S_ISREG(executable_metadata.st_mode)
        or executable.resolve(strict=True) != executable
    ):
        raise CollectionError("resolution verifier is not a regular runtime file")
    if not request_path.is_absolute():
        raise CollectionError("resolution request path is not absolute")
    try:
        relative = request_path.relative_to(runtime)
    except ValueError as exc:
        raise CollectionError("resolution request is outside the runtime run layout") from exc
    parts = relative.parts
    if (
        len(parts) != 5
        or parts[0] != "logs"
        or RUNTIME_DATE_DIRECTORY.fullmatch(parts[1]) is None
        or RUNTIME_RUN_DIRECTORY.fullmatch(parts[2]) is None
        or parts[2][:8] != parts[1].replace("-", "")
        or parts[3:] != ("staging", "source-resolutions.json")
    ):
        raise CollectionError("resolution request is outside the canonical run layout")
    run_root = runtime / "logs" / parts[1] / parts[2]
    staging = run_root / "staging"
    source_inputs = run_root / "source-inputs"
    for directory in (
        runtime,
        runtime / "logs",
        runtime / "logs" / parts[1],
        run_root,
        staging,
        source_inputs,
    ):
        require_real_directory(directory)
    expected_request = staging / "source-resolutions.json"
    if (
        request_path != expected_request
        or request_path.is_symlink()
        or request_path.resolve(strict=True) != request_path
    ):
        raise CollectionError("resolution request differs from the canonical run path")
    catalog = runtime / "it-news-sources.json"
    manifest = source_inputs / "source-manifest.json"
    return catalog, manifest, request_path, run_root


def checked_verification_output(output_path: Path, run_root: Path) -> Path:
    """Allow the authoritative runner to create only its exact run-local evidence."""
    expected = run_root / "verified-source-resolutions.json"
    if (
        not output_path.is_absolute()
        or output_path != expected
        or output_path.is_symlink()
        or output_path.exists()
        or output_path.parent.resolve(strict=True) != run_root
    ):
        raise CollectionError("verified fallback output differs from the canonical run path")
    return output_path


def validate_manifest_catalog_binding(
    manifest: dict[str, Any],
    sources: list[dict[str, Any]],
    catalog_sha256: str,
) -> None:
    """Bind a sealed source manifest to the exact reviewed catalog bytes."""
    rows = manifest.get("sources")
    if not isinstance(rows, list) or len(rows) != len(sources):
        raise CollectionError("source manifest does not match the reviewed catalog")
    statuses: list[str] = []
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise CollectionError("source manifest entry is invalid")
        name = row.get("name")
        status = row.get("status")
        if not isinstance(name, str) or status not in {
            "fetched",
            "needs_search_fallback",
            "access_constraint",
        }:
            raise CollectionError("source manifest entry is invalid")
        names.append(name)
        statuses.append(status)
    expected_names = [source["name"] for source in sources]
    if (
        manifest.get("catalog_sha256") != catalog_sha256
        or manifest.get("source_count") != len(sources)
        or names != expected_names
        or manifest.get("fetched_count") != statuses.count("fetched")
        or manifest.get("needs_search_fallback_count")
        != statuses.count("needs_search_fallback")
        or manifest.get("access_constraint_count")
        != statuses.count("access_constraint")
    ):
        raise CollectionError("source manifest does not match the reviewed catalog")


def verify_resolutions(
    catalog_path: Path,
    source_manifest_path: Path,
    request_path: Path,
    output_path: Optional[Path],
    *,
    trusted_root: Optional[Path] = None,
) -> None:
    """Independently verify model-discovered fallback URLs on reviewed hosts."""
    if trusted_root is None:
        trusted_value = os.environ.get("COLLECTION_OUTPUT_ROOT")
        if not trusted_value:
            raise CollectionError("COLLECTION_OUTPUT_ROOT is required")
        trusted = Path(trusted_value).resolve(strict=True)
    else:
        trusted = trusted_root.resolve(strict=True)
    sources, catalog_sha256 = load_catalog_with_digest(catalog_path)
    by_name = {source["name"]: source for source in sources}
    base = load_bounded_run_json(source_manifest_path, trusted)
    validate_manifest_catalog_binding(base, sources, catalog_sha256)
    unresolved = {
        item["name"] for item in base.get("sources", [])
        if isinstance(item, dict) and item.get("status") == "needs_search_fallback"
    }
    direct_date_sources = {
        item["name"] for item in base.get("sources", [])
        if isinstance(item, dict) and item.get("status") == "fetched"
    }
    request = load_bounded_run_json(request_path, trusted)
    resolutions = request.get("resolutions")
    date_requests = request.get("date_evidence")
    if (
        request.get("version") != 1
        or not isinstance(resolutions, list)
        or not isinstance(date_requests, list)
        or len(date_requests) > 500
    ):
        raise CollectionError("invalid fallback resolution request")
    verified = []
    seen: set[str] = set()
    for item in resolutions:
        if not isinstance(item, dict):
            raise CollectionError("invalid fallback resolution entry")
        name, method, url = item["name"], item["method"], item["url"]
        expected_keys = {"name", "method", "url", "constraint"} if method == "access_constraint" else {"name", "method", "url"}
        if set(item) != expected_keys:
            raise CollectionError("invalid fallback resolution entry")
        if (
            name not in unresolved
            or name in seen
            or method not in {"site_search", "official_alternate", "access_constraint"}
            or not isinstance(url, str)
        ):
            raise CollectionError("fallback resolution is outside unresolved scope")
        hosts = source_hosts(by_name[name])
        fetched = fetch_url(validate_url(url, hosts), hosts)
        constraint = detect_access_constraint(fetched["content"])
        verified_http_429_captcha = (
            fetched["http_status"] == 429 and constraint == "captcha"
        )
        if verified_http_429_captcha:
            if method != "access_constraint" or item["constraint"] != "captcha":
                raise CollectionError("fallback URL is access constrained")
            status = "verified_access_constraint"
            extracted_entry_count = 0
            candidates: list[dict[str, Any]] = []
            published_dates: list[Optional[str]] = []
        else:
            extract = extract_content(
                fetched["content"], fetched["content_type"], fetched["final_url"], hosts
            )
            if extract["entry_count"] >= 10:
                constraint = None
            if method == "access_constraint":
                if item["constraint"] not in {"login", "paywall", "captcha"} or constraint != item["constraint"]:
                    raise CollectionError("access constraint lacks verified gate evidence")
                status = "verified_access_constraint"
            elif constraint:
                raise CollectionError("fallback URL is access constrained")
            else:
                status = "verified_fallback"
                if extract["entry_count"] == 0:
                    raise CollectionError("verified fallback extract is empty")
            primary_published, primary_provenance = extract_primary_publication_evidence(
                fetched["content"], fetched["final_url"], hosts
            )
            if extract["format"] == "html_links" and primary_published:
                candidates = [{
                    "url": fetched["final_url"],
                    "candidate_provenance": primary_provenance,
                    "published": primary_published,
                }]
            else:
                candidates = (
                    extract["entries"]
                    if extract["format"] == "feed"
                    else [
                        entry for entry in extract["entries"]
                        if entry.get("candidate_provenance") in {"article", "json_ld"}
                    ]
                )
            published_dates = [
                validated_publication_date(entry.get("published")) for entry in candidates
            ]
            extracted_entry_count = extract["entry_count"]
        if status == "verified_fallback" and (
            not published_dates or any(not value for value in published_dates)
        ):
            raise CollectionError("verified fallback lacks complete publication-date evidence")
        verified.append({
            "name": name,
            "status": status,
            "method": method,
            "requested_url": url,
            "final_url": fetched["final_url"],
            "http_status": fetched["http_status"],
            "constraint": constraint,
            "extracted_entry_count": extracted_entry_count,
            "candidate_entry_count": len(candidates),
            "date_evidence_count": sum(bool(value) for value in published_dates),
            "published_dates": published_dates,
            "candidate_evidence": [
                {
                    "url": entry.get("url"),
                    "provenance": entry.get("candidate_provenance"),
                    "published": entry.get("published"),
                }
                for entry in candidates
            ],
        })
        seen.add(name)
    if seen != unresolved:
        raise CollectionError("fallback resolution set does not match unresolved sources")
    verified_dates = []
    seen_date_urls: set[tuple[str, str]] = set()
    for item in date_requests:
        if not isinstance(item, dict) or set(item) != {"name", "url"}:
            raise CollectionError("invalid publication-date evidence request")
        name, url = item["name"], item["url"]
        if (
            name not in by_name
            or name not in direct_date_sources
            or not isinstance(url, str)
            or (name, url) in seen_date_urls
        ):
            raise CollectionError("publication-date evidence is outside catalog scope")
        hosts = source_hosts(by_name[name])
        fetched = fetch_url(validate_url(url, hosts), hosts)
        if canonical_url(url) != canonical_url(fetched["final_url"]):
            raise CollectionError("publication-date evidence redirected to another article")
        extract = extract_content(
            fetched["content"], fetched["content_type"], fetched["final_url"], hosts
        )
        published_date = extract_primary_publication_date(
            fetched["content"], fetched["final_url"], hosts
        )
        if not published_date:
            raise CollectionError("article URL lacks publication-date evidence")
        verified_dates.append({
            "name": name, "requested_url": url, "final_url": fetched["final_url"],
            "published_date": published_date,
        })
        seen_date_urls.add((name, url))
    if output_path is not None:
        parent = output_path.parent.resolve(strict=True)
        if parent != trusted or output_path.is_symlink() or output_path.exists():
            raise CollectionError("verified fallback output escapes the run root")
        with output_path.open("x", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 1,
                    "resolutions": verified,
                    "date_evidence": verified_dates,
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")


def main(argv: list[str]) -> int:
    """Collect all configured sources and emit a run-local manifest."""
    try:
        if len(argv) == 3 and argv[1] == "--check-resolutions":
            catalog, manifest, request, run_root = checked_runtime_inputs(Path(argv[2]))
            verify_resolutions(
                catalog,
                manifest,
                request,
                None,
                trusted_root=run_root,
            )
            return 0
        if len(argv) == 4 and argv[1] == "--verify-resolutions":
            catalog, manifest, request, run_root = checked_runtime_inputs(Path(argv[2]))
            output = checked_verification_output(Path(argv[3]), run_root)
            verify_resolutions(
                catalog,
                manifest,
                request,
                output,
                trusted_root=run_root,
            )
            return 0
        if len(argv) != 4:
            print(
                "usage: collect_public_sources.py CATALOG OUTPUT_ROOT STARTED_AT",
                file=sys.stderr,
            )
            return 64
        catalog_path = Path(argv[1])
        sources, catalog_sha256 = load_catalog_with_digest(catalog_path)
        hosts = allowed_hosts(sources)
        for source in sources:
            validate_url(source["page_url"], hosts)
            if source["feed_url"]:
                validate_url(source["feed_url"], hosts)
        output_root = resolve_output_root(argv[2])
        run_date = publication_date_in_jst(argv[3])
        if run_date is None:
            raise CollectionError("started-at timestamp is invalid")
        results: list[Optional[dict[str, Any]]] = [None] * len(sources)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(
                    collect_source,
                    i,
                    source,
                    source_hosts(source),
                    output_root,
                    run_date,
                ): i
                for i, source in enumerate(sources, start=1)
            }
            for future in concurrent.futures.as_completed(futures):
                results[futures[future] - 1] = future.result()
        manifest = {
            "catalog_sha256": catalog_sha256,
            "source_count": len(sources),
            "fetched_count": sum(item["status"] == "fetched" for item in results if item),
            "needs_search_fallback_count": sum(
                item["status"] == "needs_search_fallback" for item in results if item
            ),
            "access_constraint_count": sum(
                item["status"] == "access_constraint" for item in results if item
            ),
            "sources": results,
        }
        manifest_path = output_root / "source-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({**manifest, "manifest_path": str(manifest_path)}, ensure_ascii=False))
    except (CollectionError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"source collection failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
