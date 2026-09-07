#!/usr/bin/env python3
"""Regression tests for deterministic public-source acquisition."""

from __future__ import annotations

import importlib.util
import gzip
import hashlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).parents[1]
REPO_ROOT = SKILL_ROOT.parent
CATALOG = SKILL_ROOT / "references" / "it-news-sources.json"
SCRIPT = SKILL_ROOT / "scripts" / "collect-public-sources.py"
SPEC = importlib.util.spec_from_file_location("collect_public_sources", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
VALIDATOR_SCRIPT = (
    REPO_ROOT / "vault-change-publisher/scripts/validate-collection-result.py"
)
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_collection_result_for_collector", VALIDATOR_SCRIPT
)
assert VALIDATOR_SPEC and VALIDATOR_SPEC.loader
VALIDATOR = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR)


def write_bound_manifest(
    path: Path,
    statuses: dict[str, str],
    catalog: Path = CATALOG,
) -> None:
    """Write the complete catalog-bound manifest shape used by the collector."""
    sources = MODULE.load_catalog(catalog)
    rows = [
        {
            "name": source["name"],
            "status": statuses.get(source["name"], "access_constraint"),
        }
        for source in sources
    ]
    path.write_text(
        json.dumps(
            {
                "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                "source_count": len(rows),
                "fetched_count": sum(row["status"] == "fetched" for row in rows),
                "needs_search_fallback_count": sum(
                    row["status"] == "needs_search_fallback" for row in rows
                ),
                "access_constraint_count": sum(
                    row["status"] == "access_constraint" for row in rows
                ),
                "sources": rows,
            }
        ),
        encoding="utf-8",
    )


def create_check_runtime(
    root: Path,
    statuses: dict[str, str],
) -> tuple[Path, Path, Path, Path]:
    """Create the flattened immutable layout required by resolution preflight."""
    root = root.resolve(strict=True)
    runtime = root / "runtime"
    runtime.mkdir()
    executable = runtime / "collect-public-sources.py"
    executable.write_text("# fixture runtime executable\n", encoding="utf-8")
    catalog = runtime / "it-news-sources.json"
    catalog.write_bytes(CATALOG.read_bytes())
    run_root = (
        runtime
        / "logs"
        / "2026-08-23"
        / "20260823T050000+0900-123-456"
    )
    staging = run_root / "staging"
    source_inputs = run_root / "source-inputs"
    staging.mkdir(parents=True)
    source_inputs.mkdir()
    manifest = source_inputs / "source-manifest.json"
    write_bound_manifest(manifest, statuses, catalog)
    request = staging / "source-resolutions.json"
    return executable, catalog, manifest, request


class FakeHeaders:
    """Provide the response header subset used by the collector."""

    def __init__(
        self,
        content_type: str,
        content_encoding: str = "",
        content_length=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self.content_type = content_type
        self.content_encoding = content_encoding
        self.content_length = content_length

    def get(self, name: str, default=None):  # type: ignore[no-untyped-def]
        if name == "Content-Encoding":
            return self.content_encoding
        if name == "Content-Length":
            return self.content_length
        return default

    def get_content_type(self) -> str:
        return self.content_type


class FakeResponse:
    """Act as a bounded urllib response context manager."""

    def __init__(self, content: bytes, content_type: str, url: str, content_encoding: str = "") -> None:
        self.content = content
        self.headers = FakeHeaders(content_type, content_encoding)
        self.url = url
        self.status = 200

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_args):  # type: ignore[no-untyped-def]
        return False

    def read(self, _limit: int) -> bytes:
        return self.content

    def geturl(self) -> str:
        return self.url


class PublicSourceCollectorTests(unittest.TestCase):
    """Exercise catalog, URL, MIME, fallback, and staging boundaries."""

    def test_catalog_has_unique_reviewed_sources(self) -> None:
        """Keep all configured Tier 1 and Tier 2 sources machine-readable."""
        sources = MODULE.load_catalog(CATALOG)
        self.assertEqual(len(sources), 26)
        self.assertEqual(len({source["name"] for source in sources}), 26)
        self.assertEqual(sum(source["tier"] == 1 for source in sources), 11)
        self.assertEqual(sum(source["tier"] == 2 for source in sources), 15)
        by_name = {source["name"]: source for source in sources}
        self.assertEqual(
            by_name["The New Stack"]["feed_url"],
            "https://thenewstack.io/feed/",
        )

    def test_the_new_stack_official_feed_precedes_html_fallback(self) -> None:
        """Resolve the publisher from its reviewed feed without touching HTML."""
        source = next(
            source
            for source in MODULE.load_catalog(CATALOG)
            if source["name"] == "The New Stack"
        )
        hosts = MODULE.source_hosts(source)
        feed = (
            b"<rss><channel><item><title>Official feed story</title>"
            b"<link>https://thenewstack.io/official-feed-story/</link>"
            b"<pubDate>Sat, 22 Aug 2026 17:00:00 +0000</pubDate>"
            b"</item></channel></rss>"
        )
        fetch = mock.Mock(return_value={
            "content": feed,
            "content_type": "application/rss+xml",
            "final_url": source["feed_url"],
            "http_status": 200,
        })
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", fetch
        ):
            result = MODULE.collect_source(
                16, source, hosts, Path(temporary), MODULE.date(2026, 8, 23)
            )
        fetch.assert_called_once_with("https://thenewstack.io/feed/", hosts)
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["method"], "rss")
        self.assertEqual(result["jst_window_item_count"], 1)

    def test_url_allowlist_rejects_unsafe_targets(self) -> None:
        """Do not turn the collector into an arbitrary URL or credential client."""
        hosts = MODULE.allowed_hosts(MODULE.load_catalog(CATALOG))
        for value in (
            "http://techcrunch.com/feed/",
            "https://user:secret@techcrunch.com/feed/",
            "https://127.0.0.1/feed/",
            "https://example.com/feed/",
        ):
            with self.subTest(value=value), self.assertRaises(MODULE.CollectionError):
                MODULE.validate_url(value, hosts)

    def test_source_host_boundary_rejects_cross_publisher_redirect(self) -> None:
        """Do not attribute another catalog publisher's response to this source."""
        sources = MODULE.load_catalog(CATALOG)
        first_hosts = MODULE.source_hosts(sources[0])
        with self.assertRaises(MODULE.CollectionError):
            MODULE.validate_url(sources[1]["page_url"], first_hosts)

    def test_fetch_accepts_public_rss_mime(self) -> None:
        """Accept RSS/XML content instead of reproducing the browser MIME failure."""
        sources = MODULE.load_catalog(CATALOG)
        hosts = MODULE.allowed_hosts(sources)
        url = sources[0]["feed_url"]
        response = FakeResponse(b"<?xml version='1.0'?><rss/>", "application/rss+xml", url)
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(
            MODULE,
            "robots_policy",
            return_value={"allowed": True, "robots_url": "https://example/robots.txt", "robots_sha256": None},
        ), mock.patch.object(
            MODULE.urllib.request, "build_opener", return_value=opener
        ):
            result = MODULE.fetch_url(url, hosts)
        self.assertEqual(result["content_type"], "application/rss+xml")
        self.assertEqual(result["content"], b"<?xml version='1.0'?><rss/>")

    def test_feed_failure_falls_back_to_public_page(self) -> None:
        """Retain the failed feed audit and save the successful page bytes."""
        source = MODULE.load_catalog(CATALOG)[0]
        hosts = MODULE.allowed_hosts([source])
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE,
            "fetch_url",
            side_effect=[
                MODULE.CollectionError("content-type fixture"),
                {
                    "content": b'<html><article><a href="/article">Public article</a><time datetime="2026-08-10">Today</time></article></html>',
                    "content_type": "text/html",
                    "final_url": source["page_url"],
                    "http_status": 200,
                    "attempt": 1,
                },
            ],
        ):
            result = MODULE.collect_source(
                1, source, hosts, Path(temporary), MODULE.date(2026, 8, 10)
            )
            extract = json.loads((Path(temporary) / result["extract_file"]).read_text())
            self.assertEqual(extract["format"], "html_links")
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["method"], "public_page")
        self.assertEqual(result["jst_window_start"], "2026-08-04")
        self.assertEqual(result["jst_window_end"], "2026-08-10")
        self.assertEqual(result["jst_window_item_count"], 1)
        self.assertEqual([item["status"] for item in result["attempts"]], ["failed", "fetched"])

    def test_undated_html_requires_search_fallback(self) -> None:
        """Do not seal an HTML candidate set that cannot support a date audit."""
        source = MODULE.load_catalog(CATALOG)[0]
        undated = b'<html><article><a href="/article">Public article</a></article></html>'
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE,
            "fetch_url",
            side_effect=[
                MODULE.CollectionError("feed unavailable"),
                {
                    "content": undated,
                    "content_type": "text/html",
                    "final_url": source["page_url"],
                    "http_status": 200,
                },
            ],
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "needs_search_fallback")
        self.assertEqual(
            result["attempts"][-1]["reason"],
            "html_extract_lacks_publication_date_evidence",
        )

    def test_unresolved_direct_attempts_require_search_fallback(self) -> None:
        """Never present two direct failures as successful coverage."""
        source = MODULE.load_catalog(CATALOG)[0]
        hosts = MODULE.allowed_hosts([source])
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", side_effect=MODULE.CollectionError("fixture failure")
        ):
            result = MODULE.collect_source(1, source, hosts, Path(temporary))
        self.assertEqual(result["status"], "needs_search_fallback")
        self.assertEqual(len(result["attempts"]), 2)

    def test_extracts_compact_feed_entries(self) -> None:
        """Give the model bounded entry metadata instead of raw untrusted XML."""
        content = b"<rss><channel><item><title>Release</title><link>https://example.test/a</link><pubDate>2026-08-05</pubDate></item></channel></rss>"
        extracted = MODULE.extract_content(content, "application/rss+xml", "https://example.test/feed")
        self.assertEqual(extracted["format"], "feed")
        self.assertEqual(extracted["entry_count"], 1)
        self.assertEqual(extracted["entries"][0]["title"], "Release")

    def test_feed_extract_drops_invalid_urls_without_losing_valid_peers(self) -> None:
        """Apply the public URL gate to every RSS item independently."""
        items = (
            ("bad-port", "https://example.test:bad/a"),
            ("out-of-range-port", "https://example.test:65536/b"),
            ("credentials", "https://user:pass@example.test/c"),
            ("invalid-host", "https://bad_host.test/d"),
            ("foreign-host", "https://other.test/e"),
            ("valid", "https://example.test/release#details"),
        )
        content = (
            "<rss><channel>"
            + "".join(
                "<item><title>"
                + title
                + "</title><link>"
                + url
                + "</link><pubDate>2026-08-05</pubDate></item>"
                for title, url in items
            )
            + "</channel></rss>"
        ).encode()
        extracted = MODULE.extract_content(
            content,
            "application/rss+xml",
            "https://example.test/feed",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(extracted["format"], "feed")
        self.assertEqual(extracted["entry_count"], 1)
        self.assertEqual(extracted["entries"][0]["title"], "valid")
        self.assertEqual(
            extracted["entries"][0]["url"], "https://example.test/release"
        )

    def test_source_bound_extract_drops_foreign_html_navigation(self) -> None:
        """Do not seal cross-host navigation from an otherwise valid source page."""
        content = (
            b'<a href="https://other.test/about">Foreign navigation</a>'
            b'<article><time datetime="2026-08-18"></time>'
            b'<h2><a href="/story">Local story</a></h2></article>'
        )
        extracted = MODULE.extract_content(
            content,
            "text/html",
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(extracted["entry_count"], 1)
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(
            extracted["entries"][0]["url"], "https://example.test/story"
        )

    def test_source_bound_foreign_records_do_not_consume_html_caps(self) -> None:
        """Count accepted source-local records, not earlier foreign records."""
        foreign_anchors = "".join(
            f'<a href="https://other.test/nav/{index}">Foreign {index}</a>'
            for index in range(400)
        )
        local_article = (
            '<article><time datetime="2026-08-18"></time>'
            '<h2><a href="/story">Local story</a></h2></article>'
        )
        extracted = MODULE.extract_content(
            (foreign_anchors + local_article).encode(),
            "text/html",
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(extracted["entries"][0]["title"], "Local story")

        foreign_records = [
            {
                "@context": "https://schema.org",
                "@type": "NewsArticle",
                "headline": f"Foreign {index}",
                "url": f"https://other.test/story/{index}",
                "datePublished": "2026-08-17",
            }
            for index in range(200)
        ]
        local_record = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": "Local JSON story",
            "url": "/json-story",
            "datePublished": "2026-08-18",
        }
        graph = json.dumps({"@graph": [*foreign_records, local_record]}).encode()
        content = (
            b'<script type="application/ld+json">'
            + graph
            + b"</script>"
        )
        extracted = MODULE.extract_content(
            content,
            "text/html",
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(extracted["entries"][0]["title"], "Local JSON story")

        foreign_blocks = b"".join(
            b'<script type="application/ld+json">'
            + json.dumps(record).encode()
            + b"</script>"
            for record in foreign_records[:50]
        )
        local_block = (
            b'<script type="application/ld+json">'
            + json.dumps(local_record).encode()
            + b"</script>"
        )
        extracted = MODULE.extract_content(
            foreign_blocks + local_block,
            "text/html",
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["entries"][0]["title"], "Local JSON story")

    def test_rss_text_url_is_validated_before_any_length_transform(self) -> None:
        """Preserve bounded raw RSS URL identity and reject overlong input."""
        prefix = "https://example.test/"
        for length in (1000, 1001, 4096, 4097):
            url = prefix + "a" * (length - len(prefix))
            content = (
                "<rss><channel><item><title>Length probe</title><link>\n  "
                + url
                + "  \n</link><pubDate>2026-08-05</pubDate></item>"
                "</channel></rss>"
            ).encode()
            extracted = MODULE.extract_content(
                content,
                "application/rss+xml",
                "https://example.test/feed",
                {"example.test", "www.example.test"},
            )
            with self.subTest(length=length):
                if length <= 4096:
                    self.assertEqual(extracted["entry_count"], 1)
                    self.assertEqual(extracted["entries"][0]["url"], url)
                else:
                    self.assertEqual(extracted["entry_count"], 0)

    def test_duplicate_records_do_not_consume_unique_entry_caps(self) -> None:
        """Deduplicate accepted identities before applying per-channel caps."""
        duplicate_items = "".join(
            "<item><title>Duplicate</title>"
            "<link>https://example.test/duplicate</link>"
            "<pubDate>2026-08-17</pubDate></item>"
            for _ in range(200)
        )
        unique_item = (
            "<item><title>Unique</title>"
            "<link>https://example.test/unique</link>"
            "<pubDate>2026-08-18</pubDate></item>"
        )
        extracted = MODULE.extract_content(
            ("<rss><channel>" + duplicate_items + unique_item + "</channel></rss>").encode(),
            "application/rss+xml",
            "https://example.test/feed",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(extracted["entry_count"], 2)
        self.assertEqual(
            [entry["url"] for entry in extracted["entries"]],
            ["https://example.test/duplicate", "https://example.test/unique"],
        )

        duplicate_json = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": "Duplicate JSON",
            "url": "/duplicate",
            "datePublished": "2026-08-17",
        }
        unique_json = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": "Unique JSON",
            "url": "/unique",
            "datePublished": "2026-08-18",
        }
        content = (
            b'<script type="application/ld+json">'
            + json.dumps({"@graph": [duplicate_json] * 200 + [unique_json]}).encode()
            + b"</script>"
        )
        extracted = MODULE.extract_content(
            content,
            "text/html",
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(extracted["candidate_entry_count"], 2)
        self.assertEqual(
            [entry["url"] for entry in extracted["entries"]],
            ["https://example.test/duplicate", "https://example.test/unique"],
        )

        duplicate_embedded = "".join(
            '<p class="title"><a href="/duplicate">Duplicate embedded</a></p>'
            '<p class="date">(2026/8/17)</p>'
            for _ in range(200)
        )
        unique_embedded = (
            '<p class="title"><a href="/unique">Unique embedded</a></p>'
            '<p class="date">(2026/8/18)</p>'
        )
        entries = MODULE.extract_embedded_article_metadata(
            (duplicate_embedded + unique_embedded).encode(),
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            [entry["url"] for entry in entries],
            ["https://example.test/duplicate", "https://example.test/unique"],
        )

    def test_json_ld_titleless_duplicates_complete_existing_metadata(self) -> None:
        """Merge validated metadata by identity before title admission or caps."""
        first_block = json.dumps(
            {
                "@context": "https://schema.org",
                "@graph": [
                {
                    "@type": "NewsArticle",
                    "headline": "Target",
                    "url": "/target",
                },
                *[
                    {
                        "@type": "NewsArticle",
                        "headline": f"Filler {index}",
                        "url": f"/filler/{index}",
                    }
                    for index in range(199)
                ],
                ],
            }
        )
        titleless_complement = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "NewsArticle",
                "url": "https://example.test:443/target/",
                "datePublished": "2026-08-18",
                "description": "Validated later metadata",
            }
        )
        non_article = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "url": "/target",
                "datePublished": "2026-08-19",
                "description": "Must not replace article metadata",
            }
        )
        content = (
            f'<script type="application/ld+json">{first_block}</script>'
            f'<script type="application/ld+json">{titleless_complement}</script>'
            f'<script type="application/ld+json">{non_article}</script>'
        ).encode()
        entries = MODULE.extract_json_ld(
            content,
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(len(entries), 200)
        self.assertEqual(entries[0]["title"], "Target")
        self.assertEqual(entries[0]["published"], "2026-08-18")
        self.assertEqual(entries[0]["summary"], "Validated later metadata")

        before_cap = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"NewsArticle",'
                '"headline":"Before cap","url":"/before"}'
                '</script><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"NewsArticle",'
                '"url":"/before/",'
                '"datePublished":"2026-08-17","description":"Completed"}'
                "</script>"
            ).encode(),
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(before_cap[0]["published"], "2026-08-17")
        self.assertEqual(before_cap[0]["summary"], "Completed")

    def test_json_ld_budgets_isolate_bad_blocks_and_keep_valid_peers(self) -> None:
        """Reject one abnormal block transactionally and continue later peers."""
        decode_too_deep = "[" * 1200 + "0" + "]" * 1200
        depth_too_deep = json.dumps(
            {"child": {"child": {"child": "seed"}}}
        )
        for _ in range(MODULE.JSON_LD_MAX_DEPTH + 1):
            depth_too_deep = '{"child":' + depth_too_deep + "}"
        node_too_large = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": "Must roll back",
            "url": "/partial",
            "@graph": [0] * MODULE.JSON_LD_MAX_NODES,
        }
        node_too_large = json.dumps(node_too_large)
        valid_json = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "NewsArticle",
                "headline": "Valid JSON peer",
                "url": "/valid-json",
                "datePublished": "2026-08-18",
            }
        )
        content = (
            f'<script type="application/ld+json">{decode_too_deep}</script>'
            f'<script type="application/ld+json">{depth_too_deep}</script>'
            f'<script type="application/ld+json">{node_too_large}</script>'
            f'<script type="application/ld+json">{valid_json}</script>'
            '<article><time datetime="2026-08-17"></time>'
            '<h2><a href="/valid-html">Valid HTML peer</a></h2></article>'
        ).encode()
        extracted = MODULE.extract_content(
            content,
            "text/html",
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        by_url = {entry["url"]: entry for entry in extracted["entries"]}
        self.assertNotIn("https://example.test/partial", by_url)
        self.assertIn("https://example.test/valid-json", by_url)
        self.assertIn("https://example.test/valid-html", by_url)
        self.assertEqual(extracted["candidate_entry_count"], 2)
        self.assertEqual(extracted["date_evidence_count"], 2)

    def test_json_ld_accepts_only_canonical_schema_article_types(self) -> None:
        """Use exact Schema.org Article identities, never arbitrary suffixes."""
        allowed_records = [
            {
                "@type": article_type,
                "headline": article_type,
                "url": f"/allowed/{index}",
                "datePublished": "2026-08-18",
            }
            for index, article_type in enumerate(
                sorted(MODULE.JSON_LD_ARTICLE_TYPES)
            )
        ]
        allowed_records.extend(
            [
                {
                    "@type": "https://schema.org/NewsArticle",
                    "headline": "HTTPS canonical IRI",
                    "url": "/allowed/https",
                    "datePublished": "2026-08-18",
                },
                {
                    "@type": "http://schema.org:80/BlogPosting",
                    "headline": "HTTP canonical IRI",
                    "url": "/allowed/http",
                    "datePublished": "2026-08-18",
                },
                {
                    "@type": ["WebPage", "TechArticle"],
                    "headline": "Multiple canonical types",
                    "url": "/allowed/multiple",
                    "datePublished": "2026-08-18",
                },
            ]
        )
        allowed = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                + json.dumps(
                    {
                        "@context": "https://schema.org",
                        "@graph": allowed_records,
                    }
                )
                + "</script>"
            ).encode(),
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(len(allowed), len(allowed_records))
        self.assertTrue(all(entry["published"] for entry in allowed))

        context_list = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                + json.dumps(
                    {
                        "@context": [
                            "https://schema.org",
                            {"@vocab": "http://schema.org/"},
                        ],
                        "@type": "NewsArticle",
                        "headline": "Context list",
                        "url": "/allowed/context-list",
                        "datePublished": "2026-08-18",
                    }
                )
                + "</script>"
            ).encode(),
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(len(context_list), 1)

        absolute_without_trusted_context = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                + json.dumps(
                    {
                        "@graph": [
                            {
                                "@type": "https://schema.org/NewsArticle",
                                "headline": "Absolute without context",
                                "url": "/allowed/absolute-no-context",
                            },
                            {
                                "@context": "https://other.test/context",
                                "@type": "http://schema.org:80/BlogPosting",
                                "headline": "Absolute under foreign context",
                                "url": "/allowed/absolute-foreign-context",
                            },
                        ]
                    }
                )
                + "</script>"
            ).encode(),
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(len(absolute_without_trusted_context), 2)

        invalid_types = [
            "NotAnArticle",
            "SyntheticPosting",
            "schema:NewsArticle",
            "https://schema.org/NewsArticle?variant=Article",
            "https://schema.org/NewsArticle?",
            "https://schema.org/NewsArticle#Article",
            "https://schema.org/NewsArticle#",
            "https://schema.org/path/NewsArticle",
            "https://schema.org/NewsArticle/",
            "https://www.schema.org/NewsArticle",
            "https://pending.schema.org/NewsArticle",
            "https://other.test/NewsArticle",
            "https://schema.org:444/NewsArticle",
            "https://schema.org/WebPage?kind=Article",
            ["WebPage", 42],
        ]
        for invalid_type in invalid_types:
            initial = {
                "@type": "NewsArticle",
                "headline": "Existing",
                "url": "/existing",
            }
            invalid_complement = {
                "@type": invalid_type,
                "url": "/existing/",
                "datePublished": "2026-08-19",
                "description": "Must not merge",
            }
            entries = MODULE.extract_json_ld(
                (
                    '<script type="application/ld+json">'
                    + json.dumps(
                        {
                            "@context": "https://schema.org",
                            "@graph": [initial, invalid_complement],
                        }
                    )
                    + "</script>"
                ).encode(),
                "https://example.test/news",
                {"example.test", "www.example.test"},
            )
            with self.subTest(invalid_type=invalid_type):
                self.assertEqual(len(entries), 1)
                self.assertIsNone(entries[0]["published"])
                self.assertIsNone(entries[0]["summary"])

        for untrusted_context in (
            None,
            "https://other.test/context",
            {"@vocab": "https://other.test/"},
            {"NewsArticle": "https://other.test/Type"},
            {
                "@vocab": "https://schema.org/",
                "NewsArticle": "https://other.test/Type",
            },
            {
                "@vocab": "https://schema.org/",
                "@language": "en",
            },
            {
                "@vocab": "https://schema.org/",
                "@import": "https://other.test/context",
            },
            {
                "@vocab": "https://schema.org/",
                "@propagate": False,
            },
            ["https://schema.org", {"unrelated": "https://example.test/"}],
            ["https://schema.org", "https://other.test/context"],
            ["https://other.test/context", "https://schema.org"],
            [
                {
                    "@vocab": "https://schema.org/",
                    "NewsArticle": "https://other.test/NotArticle",
                },
                "https://schema.org",
            ],
            [
                "https://schema.org",
                {
                    "@vocab": "https://schema.org/",
                    "NewsArticle": "https://other.test/NotArticle",
                },
                "https://schema.org",
            ],
            ["https://schema.org", ["https://schema.org"]],
            ["https://schema.org"] * (MODULE.JSON_LD_MAX_CONTEXT_ITEMS + 1),
        ):
            context_override = {
                "@context": "https://schema.org",
                "@graph": [
                    {
                        "@type": "NewsArticle",
                        "headline": "Existing",
                        "url": "/existing",
                    },
                    {
                        "@context": untrusted_context,
                        "@type": "NewsArticle",
                        "url": "/existing/",
                        "datePublished": "2026-08-19",
                        "description": "Must not merge",
                    },
                ],
            }
            entries = MODULE.extract_json_ld(
                (
                    '<script type="application/ld+json">'
                    + json.dumps(context_override)
                    + "</script>"
                ).encode(),
                "https://example.test/news",
                {"example.test", "www.example.test"},
            )
            with self.subTest(untrusted_context=untrusted_context):
                self.assertEqual(len(entries), 1)
                self.assertIsNone(entries[0]["published"])
                self.assertIsNone(entries[0]["summary"])

        no_context_short_name = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                + json.dumps(
                    {
                        "@graph": [
                            {
                                "@type": "https://schema.org/NewsArticle",
                                "headline": "Existing",
                                "url": "/existing",
                            },
                            {
                                "@type": "NewsArticle",
                                "url": "/existing/",
                                "datePublished": "2026-08-19",
                                "description": "Must not merge",
                            },
                        ]
                    }
                )
                + "</script>"
            ).encode(),
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(len(no_context_short_name), 1)
        self.assertIsNone(no_context_short_name[0]["published"])
        self.assertIsNone(no_context_short_name[0]["summary"])

        context_declaration_is_not_a_record = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                + json.dumps(
                    {
                        "@context": {
                            "@vocab": "https://schema.org/",
                            "payload": {
                                "@id": "https://example.test/payload",
                                "@type": "NewsArticle",
                                "headline": "Context injection",
                                "url": "/context-injection",
                            },
                        },
                        "@type": "WebPage",
                        "url": "/listing",
                    }
                )
                + "</script>"
            ).encode(),
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(context_declaration_is_not_a_record, [])

        canonical_complement = [
            {
                "@type": "NewsArticle",
                "headline": "Existing",
                "url": "/existing",
            },
            {
                "@type": "https://schema.org/NewsArticle",
                "url": "/existing/",
                "datePublished": "2026-08-18",
                "description": "Canonical complement",
            },
        ]
        entries = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                + json.dumps(
                    {
                        "@context": {"@vocab": "https://schema.org/"},
                        "@graph": canonical_complement,
                    }
                )
                + "</script>"
            ).encode(),
            "https://example.test/news",
            {"example.test", "www.example.test"},
        )
        self.assertEqual(entries[0]["published"], "2026-08-18")
        self.assertEqual(entries[0]["summary"], "Canonical complement")

    def test_json_ld_context_taint_is_sticky_across_arrays_and_scopes(self) -> None:
        """Never let partially understood contexts authorize short article types."""
        base = "https://example.test/news"
        hosts = {"example.test", "www.example.test"}
        initial = {
            "@type": "https://schema.org/NewsArticle",
            "headline": "Existing",
            "url": "/existing",
        }
        tainted_contexts = [
            [
                {
                    "@vocab": "https://schema.org/",
                    "NewsArticle": "https://other.test/NotArticle",
                },
                "https://schema.org",
            ],
            [
                "https://schema.org",
                {
                    "@vocab": "https://schema.org/",
                    "NewsArticle": "https://other.test/NotArticle",
                },
                "https://schema.org",
            ],
            ["https://other.test/context", "https://schema.org"],
        ]
        for tainted_context in tainted_contexts:
            entries = MODULE.extract_json_ld(
                (
                    '<script type="application/ld+json">'
                    + json.dumps(
                        {
                            "@graph": [
                                initial,
                                {
                                    "@context": tainted_context,
                                    "@type": "NewsArticle",
                                    "url": "/existing/",
                                    "datePublished": "2026-08-19",
                                    "description": "Must not merge",
                                },
                            ]
                        }
                    )
                    + "</script>"
                ).encode(),
                base,
                hosts,
            )
            with self.subTest(tainted_context=tainted_context):
                self.assertEqual(len(entries), 1)
                self.assertIsNone(entries[0]["published"])
                self.assertIsNone(entries[0]["summary"])

        sibling_isolation = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                + json.dumps(
                    {
                        "@context": "https://schema.org",
                        "@graph": [
                            {
                                "@context": [
                                    "https://other.test/context",
                                    "https://schema.org",
                                ],
                                "@type": "NewsArticle",
                                "headline": "Tainted child",
                                "url": "/tainted-child",
                            },
                            {
                                "@type": "TechArticle",
                                "headline": "Trusted sibling",
                                "url": "/trusted-sibling",
                            },
                        ],
                    }
                )
                + "</script>"
            ).encode(),
            base,
            hosts,
        )
        self.assertEqual(
            [entry["url"] for entry in sibling_isolation],
            ["https://example.test/trusted-sibling"],
        )

        scoped = {
            "@context": {
                "@vocab": "https://schema.org/",
                "payload": {
                    "@id": "https://example.test/payload",
                    "@context": {"@vocab": "https://other.test/"},
                },
            },
            "@graph": [
                initial,
                {
                    "payload": {
                        "@context": "https://schema.org",
                        "@type": "NewsArticle",
                        "url": "/existing/",
                        "datePublished": "2026-08-20",
                        "description": "Must remain tainted",
                    }
                },
                {
                    "payload": {
                        "@type": "https://schema.org/NewsArticle",
                        "headline": "Absolute peer",
                        "url": "/absolute-peer",
                        "datePublished": "2026-08-20",
                    }
                },
            ],
        }
        scoped_entries = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                + json.dumps(scoped)
                + "</script>"
            ).encode(),
            base,
            hosts,
        )
        scoped_by_url = {entry["url"]: entry for entry in scoped_entries}
        self.assertEqual(len(scoped_entries), 2)
        self.assertIsNone(scoped_by_url["https://example.test/existing"]["published"])
        self.assertIsNone(scoped_by_url["https://example.test/existing"]["summary"])
        self.assertEqual(
            scoped_by_url["https://example.test/absolute-peer"]["published"],
            "2026-08-20",
        )

        accepted = [
            {
                "@context": "https://schema.org",
                "@type": "NewsArticle",
                "headline": "Short target",
                "url": "/cap-short",
            },
            {
                "@context": "https://schema.org",
                "@type": "NewsArticle",
                "headline": "Absolute target",
                "url": "/cap-absolute",
            },
            *[
                {
                    "@context": "https://schema.org",
                    "@type": "NewsArticle",
                    "headline": f"Filler {index}",
                    "url": f"/cap-filler/{index}",
                }
                for index in range(198)
            ],
        ]
        cap_content = (
            '<script type="application/ld+json">'
            + json.dumps(accepted)
            + "</script>"
            + '<script type="application/ld+json">'
            + json.dumps(
                {
                    "@context": {
                        "@vocab": "https://schema.org/",
                        "payload": {
                            "@id": "https://example.test/payload",
                            "@context": {"@vocab": "https://other.test/"},
                        },
                    },
                    "payload": [
                        {
                            "@type": "NewsArticle",
                            "url": "/cap-short/",
                            "datePublished": "2026-08-21",
                            "description": "Must not merge after cap",
                        },
                        {
                            "@type": "https://schema.org/NewsArticle",
                            "url": "/cap-absolute/",
                            "datePublished": "2026-08-21",
                            "description": "Absolute complement",
                        },
                    ],
                }
            )
            + "</script>"
        ).encode()
        cap_entries = MODULE.extract_json_ld(cap_content, base, hosts)
        cap_by_url = {entry["url"]: entry for entry in cap_entries}
        self.assertEqual(len(cap_entries), 200)
        self.assertIsNone(cap_by_url["https://example.test/cap-short"]["published"])
        self.assertIsNone(cap_by_url["https://example.test/cap-short"]["summary"])
        self.assertEqual(
            cap_by_url["https://example.test/cap-absolute"]["published"],
            "2026-08-21",
        )
        self.assertEqual(
            cap_by_url["https://example.test/cap-absolute"]["summary"],
            "Absolute complement",
        )

    def test_json_ld_raw_text_does_not_decode_html_character_references(self) -> None:
        """Keep HTML entities literal inside script raw text before JSON parsing."""
        base = "https://example.test/news"
        hosts = {"example.test", "www.example.test"}
        initial = {
            "@type": "https://schema.org/NewsArticle",
            "headline": "Existing",
            "url": "/existing",
        }
        encoded_type_forms = [
            {
                "@context": "https://schema.org&#47;",
                "@type": "NewsArticle",
            },
            {
                "@context": "https://schema.org",
                "@type": "News&#65;rticle",
            },
            {
                "@type": "https://schema.org/News&#65;rticle",
            },
        ]
        for encoded_type_form in encoded_type_forms:
            complement = {
                **encoded_type_form,
                "url": "/existing/",
                "datePublished": "2026-08-22",
                "description": "Must not merge",
            }
            entries = MODULE.extract_json_ld(
                (
                    '<script type="application/ld+json">'
                    + json.dumps({"@graph": [initial, complement]})
                    + "</script>"
                ).encode(),
                base,
                hosts,
            )
            with self.subTest(encoded_type_form=encoded_type_form):
                self.assertEqual(len(entries), 1)
                self.assertIsNone(entries[0]["published"])
                self.assertIsNone(entries[0]["summary"])

        encoded_date = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                + json.dumps(
                    {
                        "@context": "https://schema.org",
                        "@type": "NewsArticle",
                        "headline": "Encoded date",
                        "url": "/encoded-date",
                        "datePublished": "2026-08-&#50;2",
                    }
                )
                + "</script>"
            ).encode(),
            base,
            hosts,
        )
        self.assertEqual(len(encoded_date), 1)
        self.assertIsNone(encoded_date[0]["published"])

        accepted = [
            {
                "@context": "https://schema.org",
                "@type": "NewsArticle",
                "headline": f"Target {index}",
                "url": f"/cap-target/{index}",
            }
            for index in range(4)
        ] + [
            {
                "@context": "https://schema.org",
                "@type": "NewsArticle",
                "headline": f"Filler {index}",
                "url": f"/cap-entity-filler/{index}",
            }
            for index in range(196)
        ]
        complements = [
            {
                **encoded_type_form,
                "url": f"/cap-target/{index}/",
                "datePublished": "2026-08-22",
                "description": "Must not merge after cap",
            }
            for index, encoded_type_form in enumerate(encoded_type_forms)
        ]
        complements.append(
            {
                "@context": "https://schema.org",
                "@type": "NewsArticle",
                "url": "/cap-target/3/",
                "datePublished": "2026-08-&#50;2",
                "description": "Encoded date must not merge after cap",
            }
        )
        cap_entries = MODULE.extract_json_ld(
            (
                '<script type="application/ld+json">'
                + json.dumps(accepted)
                + "</script>"
                + '<script type="application/ld+json">'
                + json.dumps(complements)
                + "</script>"
            ).encode(),
            base,
            hosts,
        )
        cap_by_url = {entry["url"]: entry for entry in cap_entries}
        self.assertEqual(len(cap_entries), 200)
        for index in range(4):
            target = cap_by_url[f"https://example.test/cap-target/{index}"]
            self.assertIsNone(target["published"])
            if index < 3:
                self.assertIsNone(target["summary"])
        self.assertEqual(
            cap_by_url["https://example.test/cap-target/3"]["summary"],
            "Encoded date must not merge after cap",
        )

        json_escape = (
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"News\\u0041rticle",'
            '"headline":"JSON escape","url":"/json-escape",'
            '"datePublished":"2026-08-\\u0032\\u0032"}'
            "</script>"
        )
        encoded_syntax = (
            '<script type="application/ld+json">'
            '{&quot;@context&quot;:&quot;https://schema.org&quot;,'
            '&quot;@type&quot;:&quot;NewsArticle&quot;,'
            '&quot;headline&quot;:&quot;Encoded syntax&quot;,'
            '&quot;url&quot;:&quot;/encoded-syntax&quot;}'
            "</script>"
        )
        valid_peer = (
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"NewsArticle",'
            '"headline":"Valid peer","url":"/valid-peer"}'
            "</script>"
        )
        peers = MODULE.extract_json_ld(
            (encoded_syntax + json_escape + valid_peer).encode(), base, hosts
        )
        self.assertEqual(
            [entry["url"] for entry in peers],
            [
                "https://example.test/json-escape",
                "https://example.test/valid-peer",
            ],
        )
        self.assertEqual(peers[0]["published"], "2026-08-22")

    def test_json_ld_requires_actual_inline_script_type_attribute(self) -> None:
        """Ignore lookalike attributes, duplicate types, and external scripts."""
        base = "https://example.test/news"
        hosts = {"example.test", "www.example.test"}

        def payload(path: str) -> str:
            return json.dumps(
                {
                    "@context": "https://schema.org",
                    "@type": "NewsArticle",
                    "headline": path,
                    "url": f"/{path}",
                    "datePublished": "2026-08-23",
                }
            )

        rejected = [
            f'<script data-type="application/ld+json">{payload("data-type")}</script>',
            f'<script notatype="application/ld+json">{payload("notatype")}</script>',
            (
                "<script data-note='type=\"application/ld+json\"'>"
                f'{payload("embedded-type")}</script>'
            ),
            (
                '<script type="application/ld+json" '
                f'type="text/plain">{payload("duplicate-type")}</script>'
            ),
            (
                '<script type="application/ld+json" src="/metadata.json">'
                f'{payload("external-script")}</script>'
            ),
            (
                '<script type="application/ld&#43;json">'
                f'{payload("attribute-entity")}</script>'
            ),
            (
                '<script type="application/ld+json"/>'
                f'{payload("self-closing")}</script>'
            ),
            f'<script type="application/ld+json">{payload("unfinished")}',
        ]
        for markup in rejected:
            with self.subTest(markup=markup):
                self.assertEqual(
                    MODULE.extract_json_ld(markup.encode(), base, hosts), []
                )

        accepted = (
            "<SCRIPT TYPE=APPLICATION/LD+JSON data-purpose=jsonld>"
            f'{payload("uppercase-unquoted")}</SCRIPT>'
            '<script type="application/ld+json" '
            "data-note='type=\"application/ld+json\"'>"
            f'{payload("quoted-peer")}</script>'
        )
        entries = MODULE.extract_json_ld(accepted.encode(), base, hosts)
        self.assertEqual(
            [entry["url"] for entry in entries],
            [
                "https://example.test/uppercase-unquoted",
                "https://example.test/quoted-peer",
            ],
        )

        lookalike_then_valid = (
            f'<script data-type="application/ld+json">{payload("lookalike")}</script>'
            '<script type="text/plain">not JSON-LD</script>'
            '<script type="application/ld+json">'
            f'{payload("valid-peer")}</script>'
        )
        peers = MODULE.extract_json_ld(
            lookalike_then_valid.encode(), base, hosts
        )
        self.assertEqual(
            [entry["url"] for entry in peers],
            ["https://example.test/valid-peer"],
        )

    def test_json_ld_uses_html5_safe_script_token_boundaries(self) -> None:
        """Fail closed on HTMLParser/HTML5 whitespace and closer divergence."""
        base = "https://example.test/news"
        hosts = {"example.test", "www.example.test"}

        def payload(path: str) -> str:
            return json.dumps(
                {
                    "@context": "https://schema.org",
                    "@type": "NewsArticle",
                    "headline": path,
                    "url": f"/{path}",
                    "datePublished": "2026-08-23",
                }
            )

        for closer in ("</ script>", "</\tscript>", "</\nscript>"):
            markup = (
                '<script type="application/ld+json">'
                f'{payload("bad-closer")}{closer}'
            )
            with self.subTest(closer=closer):
                self.assertEqual(
                    MODULE.extract_json_ld(markup.encode(), base, hosts), []
                )

        unicode_separators = ("\v", "\u0085", "\u00a0", "\u1680", "\u2003", "\u202f")
        for separator in unicode_separators:
            markup = (
                f'<script x{separator}type="application/ld+json">'
                f'{payload("unicode-separator")}</script>'
            )
            with self.subTest(separator=repr(separator)):
                self.assertEqual(
                    MODULE.extract_json_ld(markup.encode(), base, hosts), []
                )

        for index, separator in enumerate((" ", "\t", "\n", "\f", "\r")):
            markup = (
                f'<script data-id={index}{separator}type=application/ld+json>'
                f'{payload(f"ascii-separator-{index}")}</script>'
            )
            entries = MODULE.extract_json_ld(markup.encode(), base, hosts)
            with self.subTest(separator=repr(separator)):
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["published"], "2026-08-23")

        for index, closer in enumerate(("</script>", "</SCRIPT >", "</script\t>")):
            markup = (
                '<script type="application/ld+json">'
                f'{payload(f"safe-closer-{index}")}{closer}'
            )
            entries = MODULE.extract_json_ld(markup.encode(), base, hosts)
            with self.subTest(closer=closer):
                self.assertEqual(len(entries), 1)

        valid_before = (
            '<script type="application/ld+json">'
            f'{payload("valid-before")}</script>'
        )
        malformed = (
            '<script type="application/ld+json">'
            f'{payload("malformed")}</ script>'
        )
        valid_after = (
            '<script type="application/ld+json">'
            f'{payload("valid-after")}</script>'
        )
        self.assertEqual(
            MODULE.extract_json_ld(
                (valid_before + malformed + valid_after).encode(), base, hosts
            ),
            [],
        )

        opaque_containers = (
            "iframe",
            "noembed",
            "noframes",
            "noscript",
            "style",
            "textarea",
            "title",
            "xmp",
            "math",
            "svg",
            "template",
        )
        for container in opaque_containers:
            markup = (
                f'<{container}><script type="application/ld+json">'
                f'{payload("hidden")}</script></{container}>'
                '<script type="application/ld+json">'
                f'{payload("visible-peer")}</script>'
            )
            entries = MODULE.extract_json_ld(markup.encode(), base, hosts)
            with self.subTest(container=container):
                self.assertEqual(
                    [entry["url"] for entry in entries],
                    ["https://example.test/visible-peer"],
                )

        plaintext = (
            '<plaintext><script type="application/ld+json">'
            f'{payload("plaintext-hidden")}</script>'
        )
        self.assertEqual(
            MODULE.extract_json_ld(plaintext.encode(), base, hosts), []
        )

        frameset_documents = (
            '<frameset><script type="application/ld+json">'
            f'{payload("in-frameset")}</script></frameset>',
            '<frameset></frameset><script type="application/ld+json">'
            f'{payload("after-frameset")}</script>',
            '<frameset></frameset></html><script type="application/ld+json">'
            f'{payload("after-after-frameset")}</script>',
            '<script type="application/ld+json">'
            f'{payload("valid-before-frameset")}</script><frameset></frameset>',
        )
        for markup in frameset_documents:
            with self.subTest(frameset_markup=markup):
                self.assertEqual(
                    MODULE.extract_json_ld(markup.encode(), base, hosts), []
                )

    def test_html_extract_preserves_article_date_evidence(self) -> None:
        """Associate article-card and JSON-LD publication dates with official URLs."""
        content = b'''<html><article><a href="/card">Card story</a><time datetime="2026-08-05">Today</time></article><script type="application/ld+json">{"@context":"https://schema.org","@type":"NewsArticle","headline":"JSON story","url":"/json","datePublished":"2026-08-04"}</script></html>'''
        extracted = MODULE.extract_content(content, "text/html", "https://example.test/news")
        by_url = {entry["url"]: entry for entry in extracted["entries"]}
        self.assertEqual(by_url["https://example.test/card"]["published"], "2026-08-05")
        self.assertEqual(by_url["https://example.test/json"]["published"], "2026-08-04")
        self.assertEqual(extracted["date_evidence_count"], 2)

    def test_html_extract_binds_visible_card_date_to_article(self) -> None:
        """Normalize a visible YYYY/M/D date inside one article card."""
        content = (
            b'<article><h2><a href="/card">Card story</a></h2>'
            b'<span class="date">2026/8/4</span></article>'
        )
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        self.assertEqual(extracted["entries"][0]["published"], "2026-08-04")
        self.assertEqual(extracted["date_evidence_count"], 1)

    def test_html_extract_binds_semantically_labeled_english_date(self) -> None:
        """Bind a publisher's explicit publication label without guessing body dates."""
        content = (
            b'<article><span alt="Date of publication">Aug 18, 2026</span>'
            b'<h2><a href="/card">Card story</a></h2></article>'
        )
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        self.assertEqual(extracted["entries"][0]["published"], "2026-08-18")
        self.assertEqual(extracted["date_evidence_count"], 1)

    def test_html_extract_prefers_same_url_article_headline_over_share_label(self) -> None:
        """Keep the real card headline when copy/share controls reuse its URL."""
        content = (
            b'<article><span alt="Date of publication">Aug 18, 2026</span>'
            b'<a href="/story">Copy the url to clipboard</a>'
            b'<a href="/story">Share this article</a>'
            b'<h2><a href="/story">New benchmark ranks search APIs</a></h2>'
            b'<a href="/story">Read full article</a></article>'
        )
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "New benchmark ranks search APIs")
        self.assertEqual(candidates[0]["published"], "2026-08-18")

        heading_first = (
            b'<article><h2><a href="/story">Actual headline</a></h2>'
            b'<a href="/story">Copy link</a>'
            b'<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            heading_first, "text/html", "https://example.test/news"
        )
        self.assertEqual(extracted["entries"][0]["title"], "Actual headline")

        canonical_variants = (
            b'<article><a href="/story/?ref=home#copy">Copy the url to clipboard</a>'
            b'<h2><a href="https://example.test:443/story?ref=home#headline">'
            b'Canonical headline</a></h2>'
            b'<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            canonical_variants, "text/html", "https://example.test/news"
        )
        self.assertEqual(extracted["entry_count"], 1)
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["entries"][0]["title"], "Canonical headline")
        self.assertEqual(extracted["entries"][0]["published"], "2026-08-18")

    def test_html_extract_preserves_headline_across_reused_url_article_scopes(self) -> None:
        """Do not let a later control label overwrite an earlier headline."""
        content = (
            b'<article><h2><a href="/story">Canonical headline</a></h2>'
            b'<time datetime="2026-08-18"></time></article>'
            b'<article><a href="/story">Copy link</a>'
            b'<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "Canonical headline")

    def test_publisher_javascript_scan_bounds_untrusted_blocks(self) -> None:
        """Skip oversized publisher blocks while retaining bounded valid records."""
        parser = MODULE.LinkExtractor(
            "https://example.test/news", {"example.test"}, ""
        )
        oversized = (
            "{'url':'https://example.test/ignored','title':'"
            + "x" * MODULE.PUBLISHER_JAVASCRIPT_MAX_BYTES
            + "','date':'2026/8/18'}"
        )
        parser.embedded_script_blocks = [("publisher_javascript", oversized)]
        MODULE.populate_embedded_script_entries(parser)
        self.assertEqual(parser.embedded_entries, [])

        valid = "{'url':'https://example.test/valid','title':'Valid','date':'2026/8/18'}"
        parser.embedded_script_blocks = [("publisher_javascript", valid)]
        MODULE.populate_embedded_script_entries(parser)
        self.assertEqual(len(parser.embedded_entries), 1)
        self.assertEqual(parser.embedded_entries[0]["title"], "Valid")

    def test_html_extract_upgrades_existing_headline_at_entry_cap(self) -> None:
        """Keep title arbitration active when the bounded entry set reaches 400."""
        navigation = "".join(
            f'<a href="/nav/{index}">Navigation {index}</a>'
            for index in range(399)
        )
        article = (
            '<article><a href="/story">Copy link</a>'
            '<h2><a href="/story">Boundary headline</a></h2>'
            '<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            (navigation + article).encode(),
            "text/html",
            "https://example.test/news",
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(extracted["entry_count"], 400)
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(candidates[0]["title"], "Boundary headline")
        self.assertEqual(candidates[0]["published"], "2026-08-18")

        full_navigation = navigation + '<a href="/nav/399">Navigation 399</a>'
        extracted = MODULE.extract_content(
            (full_navigation + article).encode(),
            "text/html",
            "https://example.test/news",
        )
        self.assertEqual(extracted["entry_count"], 400)
        self.assertEqual(extracted["candidate_entry_count"], 0)

    def test_html_extract_disables_single_link_fallback_when_article_is_capped(self) -> None:
        """Do not promote a control when the real article URL was cap-rejected."""
        navigation = '<a href="/story?view=share">Global share</a>' + "".join(
            f'<a href="/nav/{index}">Navigation {index}</a>'
            for index in range(399)
        )
        truncated_article = (
            '<article><a href="/story?view=share">Copy link</a>'
            '<h2><a href="/story?view=article">Actual headline</a></h2>'
            '<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            (navigation + truncated_article).encode(),
            "text/html",
            "https://example.test/news",
        )
        self.assertEqual(extracted["entry_count"], 400)
        self.assertEqual(extracted["candidate_entry_count"], 0)
        self.assertEqual(extracted["date_evidence_count"], 0)

        known_headline = (
            '<article><h2><a href="/story?view=share">Known headline</a></h2>'
            '<a href="/new-ancillary">Rejected ancillary</a>'
            '<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            (navigation + known_headline).encode(),
            "text/html",
            "https://example.test/news",
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(candidates[0]["title"], "Known headline")

    def test_html_date_capture_ignores_void_depth_and_resets_between_articles(self) -> None:
        """Do not let void or malformed nested tags poison later card dates."""
        content = (
            b'<article><h2><a href="/one">One</a></h2>'
            b'<span class="date"><br/>2026/8/4</span></article>'
            b'<article><h2><a href="/broken">Broken</a></h2>'
            b'<span class="date"><em>malformed</article>'
            b'<article><h2><a href="/two">Two</a></h2>'
            b'<span class="date"><img src="x"/>2026/8/5</span></article>'
            b'<article><h2><a href="/three">Three</a></h2>'
            b'<span class="date"><br>2026/8/6</span></article>'
        )
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        by_url = {entry["url"]: entry for entry in extracted["entries"]}
        self.assertEqual(by_url["https://example.test/one"]["published"], "2026-08-04")
        self.assertEqual(by_url["https://example.test/two"]["published"], "2026-08-05")
        self.assertEqual(by_url["https://example.test/three"]["published"], "2026-08-06")

    def test_html_heading_scope_resets_between_articles(self) -> None:
        """Do not let an unclosed heading turn a later article link into a headline."""
        content = (
            b'<article><h2><a href="/first">First headline</a>'
            b'<time datetime="2026-08-17"></time></article>'
            b'<article><a href="/author">Alice</a>'
            b'<a href="/story">Actual story</a>'
            b'<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(candidates[0]["url"], "https://example.test/first")
        self.assertEqual(candidates[0]["title"], "First headline")

    def test_unclosed_anchor_is_discarded_at_article_boundary(self) -> None:
        """Do not complete one article's unfinished anchor inside a later article."""
        content = (
            b'<article><a href="/leaked">Leaked anchor'
            b'<time datetime="2026-08-16"></time></article>'
            b'<article>Later text</a>'
            b'<time datetime="2026-08-17"></time></article>'
            b'<article><h2><a href="/valid">Valid headline</a></h2>'
            b'<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(candidates[0]["url"], "https://example.test/valid")
        self.assertEqual(candidates[0]["title"], "Valid headline")
        self.assertEqual(candidates[0]["published"], "2026-08-18")

    def test_nested_article_heading_and_date_do_not_replace_outer_candidate(self) -> None:
        """Keep nested article metadata out of the enclosing listing candidate."""
        content = (
            b'<article><article><h2><a href="/nested">Nested headline</a></h2>'
            b'<time datetime="2026-08-17"></time></article>'
            b'<h2><a href="/outer">Outer headline</a></h2>'
            b'<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(candidates[0]["url"], "https://example.test/outer")
        self.assertEqual(candidates[0]["title"], "Outer headline")
        self.assertEqual(candidates[0]["published"], "2026-08-18")

        malformed_nested = (
            b'<article><article><h2><a href="/nested">Nested</a></article>'
            b'<a href="/author">Alice</a><a href="/story">Story</a>'
            b'<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            malformed_nested, "text/html", "https://example.test/news"
        )
        self.assertEqual(extracted["candidate_entry_count"], 0)
        self.assertEqual(extracted["date_evidence_count"], 0)

        for nested_start in (
            b'<article><a href="/nested">Nested text',
            b'<article><h2><a href="/nested">Nested headline',
        ):
            with self.subTest(nested_start=nested_start):
                nested_anchor_closed_by_outer = (
                    b'<article>' + nested_start + b'</article>'
                    b'Outer tail</a><time datetime="2026-08-18"></time></article>'
                )
                extracted = MODULE.extract_content(
                    nested_anchor_closed_by_outer,
                    "text/html",
                    "https://example.test/news",
                )
                self.assertEqual(extracted["candidate_entry_count"], 0)
                self.assertEqual(extracted["date_evidence_count"], 0)

        for malformed_outer_date in (
            b'<time><article>Aug 17, 2026</article></time>',
            b'<time datetime="2026-08-17"><article>Nested</article></time>',
            b'<span alt="Date of publication"><article>Aug 17, 2026</article></span>',
        ):
            with self.subTest(malformed_outer_date=malformed_outer_date):
                nested_text_inside_outer_date_capture = (
                    b'<article>' + malformed_outer_date
                    + b'<h2><a href="/outer">Outer headline</a></h2></article>'
                )
                extracted = MODULE.extract_content(
                    nested_text_inside_outer_date_capture,
                    "text/html",
                    "https://example.test/news",
                )
                self.assertEqual(extracted["candidate_entry_count"], 1)
                self.assertEqual(extracted["date_evidence_count"], 0)
                candidate = next(
                    entry for entry in extracted["entries"]
                    if entry.get("candidate_provenance") == "article"
                )
                self.assertIsNone(candidate["published"])

        outer_heading_crosses_nested_boundary = (
            b'<article><h2>Outer prefix'
            b'<article><a href="/nested">Nested</a></article>'
            b'<a href="/author">Alice</a><a href="/story">Actual story</a>'
            b'<time datetime="2026-08-18"></time></article>'
        )
        extracted = MODULE.extract_content(
            outer_heading_crosses_nested_boundary,
            "text/html",
            "https://example.test/news",
        )
        self.assertEqual(extracted["candidate_entry_count"], 0)
        self.assertEqual(extracted["date_evidence_count"], 0)

        completed_outer_metadata_before_nested = (
            b'<article><h2><a href="/outer">Outer headline</a></h2>'
            b'<time datetime="2026-08-18"></time>'
            b'<article><h2><a href="/nested">Nested headline</a></h2>'
            b'<time datetime="2026-08-17"></time></article></article>'
        )
        extracted = MODULE.extract_content(
            completed_outer_metadata_before_nested,
            "text/html",
            "https://example.test/news",
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(candidates[0]["url"], "https://example.test/outer")
        self.assertEqual(candidates[0]["title"], "Outer headline")
        self.assertEqual(candidates[0]["published"], "2026-08-18")

    def test_completed_outer_date_survives_later_unfinished_capture(self) -> None:
        """Discard only pending date state while retaining completed evidence."""
        completed_dates = (
            b'<time datetime="2026-08-18"></time>',
            b'<span alt="Date of publication">Aug 18, 2026</span>',
        )
        unfinished_dates = (
            b'<time datetime="2026-08-17">discard me',
            b'<span alt="Date of publication">Aug 17, 2026',
        )
        for completed_date in completed_dates:
            for unfinished_date in unfinished_dates:
                with self.subTest(
                    completed_date=completed_date,
                    unfinished_date=unfinished_date,
                ):
                    content = (
                        b'<article>' + completed_date + unfinished_date
                        + b'<article><a href="http://[bad">Nested</a></article>'
                        + b'<h2><a href="/outer">Outer headline</a></h2>'
                        + b'</article>'
                    )
                    extracted = MODULE.extract_content(
                        content, "text/html", "https://example.test/news"
                    )
                    candidates = [
                        entry for entry in extracted["entries"]
                        if entry.get("candidate_provenance") == "article"
                    ]
                    self.assertEqual(extracted["candidate_entry_count"], 1)
                    self.assertEqual(extracted["date_evidence_count"], 1)
                    self.assertEqual(candidates[0]["url"], "https://example.test/outer")
                    self.assertEqual(candidates[0]["published"], "2026-08-18")

        completed_then_nested_then_unfinished = (
            b'<article><time datetime="2026-08-18"></time>'
            b'<article><a href="/nested">Nested</a></article>'
            b'<time datetime="2026-08-17">discard me'
            b'<h2><a href="/outer">Outer headline</a></h2></article>'
        )
        extracted = MODULE.extract_content(
            completed_then_nested_then_unfinished,
            "text/html",
            "https://example.test/news",
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(candidates[0]["published"], "2026-08-18")

    def test_malformed_href_does_not_discard_valid_article_candidate(self) -> None:
        """Skip one invalid URL, including nested metadata, without aborting HTML."""
        for malformed_anchor in (
            b'<a href="http://[bad">Global malformed</a>',
            b'<article><a href="http://[bad">Nested malformed</a></article>',
        ):
            with self.subTest(malformed_anchor=malformed_anchor):
                content = (
                    malformed_anchor
                    + b'<article><time datetime="2026-08-18"></time>'
                    + b'<h2><a href="/valid">Valid headline</a></h2></article>'
                )
                extracted = MODULE.extract_content(
                    content, "text/html", "https://example.test/news"
                )
                candidates = [
                    entry for entry in extracted["entries"]
                    if entry.get("candidate_provenance") == "article"
                ]
                self.assertEqual(extracted["candidate_entry_count"], 1)
                self.assertEqual(extracted["date_evidence_count"], 1)
                self.assertEqual(candidates[0]["url"], "https://example.test/valid")
                self.assertEqual(candidates[0]["published"], "2026-08-18")

        malformed_after_unfinished_anchor = (
            b'<article><a href="/discarded">Discarded unfinished anchor'
            b'<a href="http://[bad">Malformed replacement</a>'
            b'<time datetime="2026-08-18"></time>'
            b'<h2><a href="/valid">Valid headline</a></h2></article>'
        )
        extracted = MODULE.extract_content(
            malformed_after_unfinished_anchor,
            "text/html",
            "https://example.test/news",
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(candidates[0]["url"], "https://example.test/valid")
        self.assertEqual(candidates[0]["published"], "2026-08-18")

    def test_candidate_url_grammar_rejects_unusable_authorities(self) -> None:
        """Validate the complete URL authority before sealing a candidate."""
        accepted = (
            "/story",
            "https://example.test/story",
            "https://example.test:443/story",
            "https://example.test:65535/story",
            "//EXAMPLE.TEST/story",
            "HTTPS://EXAMPLE.TEST/story",
        )
        rejected = (
            "http://[bad",
            "https://example.test:bad/story",
            "https://example.test:%20/story",
            "https://example.test:-1/story",
            "https://example.test:/story",
            "https://example.test:0/story",
            "https://example.test:65536/story",
            "https://user@example.test/story",
            "https://user:pass@example.test/story",
            "https://@example.test/story",
            "https://exa mple.test/story",
            "https://%zz/story",
            "https://[fe80::1%25eth0]/story",
            "https://./story",
            "https://bad_host.test/story",
            "https://-bad.example/story",
            "https://bad-.example/story",
            "https://bad..example/story",
            " https://example.test/story",
            "https://example.test/story ",
            "https://user\\@example.test/story",
            "javascript:alert(1)",
        )
        for value in accepted:
            with self.subTest(value=value, expected="accepted"):
                self.assertIsNotNone(
                    MODULE.parsed_public_candidate_url(
                        "https://example.test/news", value
                    )
                )
        for value in rejected:
            with self.subTest(value=value, expected="rejected"):
                self.assertIsNone(
                    MODULE.parsed_public_candidate_url(
                        "https://example.test/news", value
                    )
                )
                content = (
                    f'<article><time datetime="2026-08-18"></time>'
                    f'<h2><a href="{value}">Invalid headline</a></h2></article>'
                ).encode()
                extracted = MODULE.extract_content(
                    content, "text/html", "https://example.test/news"
                )
                self.assertEqual(extracted["candidate_entry_count"], 0)
                self.assertEqual(extracted["date_evidence_count"], 0)

    def test_malformed_structured_url_isolated_from_valid_peer(self) -> None:
        """Skip malformed JSON-LD or embedded records without aborting peers."""
        json_ld = (
            b'<script type="application/ld+json">'
            b'{"@type":"NewsArticle","headline":"Bad","url":"http://[bad",'
            b'"datePublished":"2026-08-17"}</script>'
            b'<article><time datetime="2026-08-18"></time>'
            b'<h2><a href="/valid">Valid headline</a></h2></article>'
        )
        extracted = MODULE.extract_content(
            json_ld, "text/html", "https://example.test/news"
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") in {"article", "json_ld"}
        ]
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["date_evidence_count"], 1)
        self.assertEqual(candidates[0]["url"], "https://example.test/valid")

        embedded = (
            b'<p class="title"><a href="http://[bad">Bad</a></p>'
            b'<p class="date">(2026/8/17)</p>'
            b'<p class="title"><a href="/valid">Valid</a></p>'
            b'<p class="date">(2026/8/18)</p>'
        )
        entries = MODULE.extract_embedded_article_metadata(
            embedded, "https://example.test/news"
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["url"], "https://example.test/valid")
        self.assertEqual(entries[0]["published"], "2026-08-18")

    def test_nested_article_cap_rejection_does_not_truncate_outer_candidate(self) -> None:
        """Keep nested anchors out of enclosing state and its bounded entry set."""
        nav = b"".join(
            f'<a href="/nav-{index}">Nav {index}</a>'.encode()
            for index in range(399)
        )
        for article_body in (
            b'<a href="/outer">Outer story</a>'
            b'<article><a href="/nested-over-cap">Nested story</a></article>',
            b'<article><a href="/nested-at-cap">Nested story</a></article>'
            b'<a href="/outer">Outer story</a>',
        ):
            with self.subTest(article_body=article_body):
                content = (
                    nav + b'<article>' + article_body
                    + b'<time datetime="2026-08-18"></time></article>'
                )
                extracted = MODULE.extract_content(
                    content, "text/html", "https://example.test/news"
                )
                candidates = [
                    entry for entry in extracted["entries"]
                    if entry.get("candidate_provenance") == "article"
                ]
                self.assertEqual(extracted["entry_count"], 400)
                self.assertEqual(extracted["candidate_entry_count"], 1)
                self.assertEqual(extracted["date_evidence_count"], 1)
                self.assertEqual(candidates[0]["url"], "https://example.test/outer")
                self.assertEqual(candidates[0]["title"], "Outer story")
                self.assertEqual(candidates[0]["published"], "2026-08-18")

    def test_html_extract_rejects_arbitrary_visible_and_malformed_dates(self) -> None:
        """Do not promote body dates or malformed metadata to publication evidence."""
        content = b'''<article><span alt="Date of publication">Aug 32, 2026</span><h2><a href="/card">Support ends 2026-08-04</a></h2></article><script type="application/ld+json">{"@context":"https://schema.org","@type":"NewsArticle","headline":"Broken","url":"/broken","datePublished":"not-a-date"}</script>'''
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") in {"article", "json_ld"}
        ]
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(entry["published"] is None for entry in candidates))
        self.assertEqual(extracted["date_evidence_count"], 0)

    def test_non_article_json_ld_does_not_supply_date_evidence(self) -> None:
        """Ignore dated WebPage metadata when sealing article candidates."""
        content = b'''<script type="application/ld+json">{"@type":"WebPage","name":"Archive","url":"/news","datePublished":"2026-08-04","dateModified":"2026-08-05"}</script>'''
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        self.assertEqual(extracted["candidate_entry_count"], 0)
        self.assertEqual(extracted["date_evidence_count"], 0)

    def test_lowercase_z_uses_the_shared_jst_timestamp_semantics(self) -> None:
        """Treat lowercase and uppercase UTC suffixes identically end to end."""
        value = "2026-08-22T16:34:56z"
        expected = MODULE.date(2026, 8, 23)
        self.assertEqual(MODULE.validated_publication_date(value), value)
        self.assertEqual(MODULE.publication_date_in_jst(value), expected)
        self.assertEqual(VALIDATOR.parse_publication_date(value), expected)

    def test_collector_and_validator_share_jst_timestamp_semantics(self) -> None:
        """Keep RFC, offset, Z, and naive timestamps identical at window edges."""
        fixtures = {
            "Mon, 03 Aug 2026 15:00:00 GMT": MODULE.date(2026, 8, 4),
            "2026-08-03T15:00:00Z": MODULE.date(2026, 8, 4),
            "2026-08-03T23:59:59+09:00": MODULE.date(2026, 8, 3),
            "2026-08-03T16:00:00": MODULE.date(2026, 8, 4),
            "2026-08-10T14:59:59Z": MODULE.date(2026, 8, 10),
            "2026-08-10T15:00:00Z": MODULE.date(2026, 8, 11),
        }
        for value, expected in fixtures.items():
            with self.subTest(value=value):
                self.assertEqual(MODULE.validated_publication_date(value), value)
                self.assertEqual(MODULE.publication_date_in_jst(value), expected)
                self.assertEqual(VALIDATOR.parse_publication_date(value), expected)
        rejected = (
            "Sun, 23 Aug 2026 12:00:00 +0900 trailing prose",
            " Sun, 23 Aug 2026 12:00:00 +0900",
            "Sun, 23 Aug 2026 12:00:00 +0900 ",
            " 2026-08-22T16:34:56z",
            "2026-08-22T16:34:56z\n",
            "<time>2026-08-22T16:34:56Z</time>",
            "2026-08-22T16:34:56&#90;",
        )
        for value in rejected:
            with self.subTest(rejected=value):
                self.assertIsNone(MODULE.validated_publication_date(value))
                self.assertIsNone(MODULE.publication_date_in_jst(value))
                self.assertIsNone(VALIDATOR.parse_publication_date(value))
        for wrapped in ("( 2026/8/21)", "(2026/8/21 )"):
            with self.subTest(wrapped=wrapped):
                self.assertIsNone(MODULE.validated_publication_date(wrapped))
        extract = {
            "format": "feed",
            "entries": [
                {"published": "2026-08-03T14:59:59Z"},
                {"published": "2026-08-03T15:00:00Z"},
                {"published": "2026-08-10T14:59:59Z"},
                {"published": "2026-08-10T15:00:00Z"},
            ],
        }
        self.assertEqual(
            MODULE.jst_window_item_count(extract, MODULE.date(2026, 8, 10)), 2
        )

    def test_extracts_dated_publisher_list_metadata(self) -> None:
        """Bind supported public list metadata to its official article URL."""
        fixtures = [
            (
                b'<p class="title"><a href="/docs/1.html">Forest story</a></p>'
                b'<p class="date">(2026/8/7)</p>',
                "https://forest.watch.impress.co.jp/category/genai/",
                "https://forest.watch.impress.co.jp/docs/1.html",
            ),
            (
                b"<script>{'url':'/ait/articles/2608/07/news008.html',"
                b"'title':'AIT story','subtitle':'x','date':'2026/08/07',}"
                b"</script>",
                "https://atmarkit.itmedia.co.jp/",
                "https://atmarkit.itmedia.co.jp/ait/articles/2608/07/news008.html",
            ),
            (
                b'<script id="__NEXT_DATA__" type="application/json">'
                b'{"metadata":{"datePublished":"2026-08-07T16:00:00.000Z"},'
                b'"articleMetadata":{"title":"Cyber story"},'
                b'"data":{"issueId":"daily-briefing","issueVolume":15,'
                b'"issueNumber":150}}</script>',
                "https://thecyberwire.com/newsletters/daily-briefing",
                "https://thecyberwire.com/newsletters/daily-briefing/15/150",
            ),
        ]
        for content, base, expected_url in fixtures:
            with self.subTest(base=base):
                entries = MODULE.extract_embedded_article_metadata(content, base)
                self.assertEqual(entries[0]["url"], expected_url)
                self.assertTrue(entries[0]["published"].startswith("2026-08-07"))

    def test_embedded_list_date_cannot_cross_into_the_next_record(self) -> None:
        """Keep an undated title from consuming a later record's date."""
        content = (
            b'<p class="title"><a href="/first">First</a></p>'
            b'<p class="summary">No publication date</p>'
            b'<p class="title"><a href="/second">Second</a></p>'
            b'<p class="date">(2026/8/7)</p>'
        )
        entries = MODULE.extract_embedded_article_metadata(
            content, "https://example.test/news"
        )
        self.assertEqual([entry["url"] for entry in entries], ["https://example.test/second"])

    def test_embedded_list_date_requires_a_complete_calendar_field(self) -> None:
        """Reject prose containing a calendar substring while keeping exact wrappers."""
        content = (
            b'<p class="title"><a href="/bad">Bad</a></p>'
            b'<p class="date">Updated on 2026/8/22</p>'
            b'<p class="title"><a href="/good">Good</a></p>'
            b'<p class="date">(2026/8/21)</p>'
        )
        entries = MODULE.extract_embedded_article_metadata(
            content, "https://example.test/news"
        )
        self.assertEqual(
            [(entry["url"], entry["published"]) for entry in entries],
            [("https://example.test/good", "2026-08-21")],
        )
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        self.assertEqual(extracted["date_evidence_count"], 1)

    def test_embedded_and_end_to_end_paths_fail_closed_on_bad_script_boundary(self) -> None:
        """Discard completed peers when a later trusted script never closes safely."""
        valid = (
            b'<p class="title"><a href="/valid">Valid</a></p>'
            b'<p class="date">(2026/8/22)</p>'
        )
        malformed_scripts = (
            b"<script>{'url':'/bad','title':'Bad','date':'2026/08/22'}",
            b"<script>{'url':'/bad','title':'Bad','date':'2026/08/22'}</ script>",
            b"<script>{'url':'/bad','title':'Bad','date':'2026/08/22'}</\tscript>",
            b"<script>{'url':'/bad','title':'Bad','date':'2026/08/22'}</\nscript>",
        )
        for malformed in malformed_scripts:
            content = valid + malformed
            with self.subTest(malformed=malformed):
                self.assertEqual(
                    MODULE.extract_embedded_article_metadata(
                        content, "https://example.test/news"
                    ),
                    [],
                )
                extracted = MODULE.extract_content(
                    content, "text/html", "https://example.test/news"
                )
                self.assertEqual(extracted["entries"], [])
                self.assertEqual(extracted["date_evidence_count"], 0)

    def test_primary_date_evidence_fails_closed_on_unfinished_trust_containers(self) -> None:
        """Apply the same document-wide terminal gate to the primary-meta path."""
        base = "https://example.test/article"
        meta = b'<meta property="article:published_time" content="2026-08-22">'
        malformed = (
            b"<script>{}",
            b"<script>{}</ script>",
            b"<script>{}</\tscript></script>",
            b"<select>",
            b"<template>",
            b"<style>",
        )
        for suffix in malformed:
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    MODULE.extract_primary_publication_evidence(
                        meta + suffix, base, {"example.test"}
                    ),
                    (None, None),
                )

    def test_verified_date_request_rejects_primary_meta_before_bad_script(self) -> None:
        """Exercise the actual supplemental date-evidence path, not just its parser."""
        source = MODULE.load_catalog(CATALOG)[0]
        article_url = source["page_url"]
        content = (
            b'<meta property="article:published_time" content="2026-08-22">'
            b"<script>{'date':'2026-08-22'}</ script>"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "source-manifest.json"
            request = root / "source-resolutions.json"
            output = root / "verified-source-resolutions.json"
            write_bound_manifest(manifest, {source["name"]: "fetched"})
            request.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "resolutions": [],
                        "date_evidence": [
                            {"name": source["name"], "url": article_url}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
            ), mock.patch.object(
                MODULE,
                "fetch_url",
                return_value={
                    "content": content,
                    "content_type": "text/html",
                    "final_url": article_url,
                    "http_status": 200,
                },
            ):
                with self.assertRaisesRegex(
                    MODULE.CollectionError,
                    "lacks publication-date evidence",
                ):
                    MODULE.verify_resolutions(
                        CATALOG, manifest, request, output
                    )
            self.assertFalse(output.exists())

    def test_extracts_legacy_list_items_beneath_one_outer_article(self) -> None:
        """Match the public Forest listing without crossing list-item bounds."""
        content = (
            b'<article><ul><li><div><p class="title">'
            b'<a href="/docs/first.html">First</a></p>'
            b'<p class="outline">Summary</p>'
            b'<p class="date">(2026/8/22)</p></div></li>'
            b'<li><p class="title"><a href="/docs/undated.html">'
            b'Undated</a></p></li>'
            b'<li><p class="date">(2026/8/21)</p></li></ul></article>'
        )
        entries = MODULE.extract_embedded_article_metadata(
            content,
            "https://forest.watch.impress.co.jp/category/genai/",
        )
        self.assertEqual(
            [(entry["url"], entry["published"]) for entry in entries],
            [("https://forest.watch.impress.co.jp/docs/first.html", "2026-08-22")],
        )

    def test_all_article_channels_share_the_html_trust_boundary(self) -> None:
        """Reject fake cards and publisher data from every suppressed context."""
        base = "https://example.test/news"
        article = (
            '<article><time datetime="2026-08-22"></time>'
            '<h2><a href="/fake">Fake story</a></h2></article>'
        )
        legacy = (
            '<p class="title"><a href="/fake">Fake legacy</a></p>'
            '<p class="date">(2026/8/22)</p>'
        )
        javascript = (
            "<script>{'url':'/fake','title':'Fake JS',"
            "'date':'2026/08/22'}</script>"
        )
        next_data = (
            '<script id="__NEXT_DATA__" type="application/json">'
            '{"metadata":{"datePublished":"2026-08-22"},'
            '"articleMetadata":{"title":"Fake Next"},'
            '"data":{"issueId":"daily","issueVolume":1,"issueNumber":2}}'
            '</script>'
        )
        wrappers = (
            lambda payload: f"<!--{payload}-->",
            lambda payload: f'<script type="text/plain">{payload}</script>',
            lambda payload: f"<template>{payload}</template>",
            lambda payload: f"<select>{payload}</select>",
            lambda payload: f"<svg>{payload}</svg>",
            lambda payload: f"<math>{payload}</math>",
            lambda payload: f"<frameset>{payload}</frameset>",
        )
        for payload in (article, legacy, javascript, next_data):
            for wrapper in wrappers:
                markup = wrapper(payload)
                with self.subTest(payload=payload[:20], markup=markup[:40]):
                    extracted = MODULE.extract_content(
                        markup.encode(), "text/html", base
                    )
                    self.assertEqual(extracted["candidate_entry_count"], 0)
                    self.assertEqual(extracted["date_evidence_count"], 0)
                    self.assertEqual(
                        MODULE.extract_embedded_article_metadata(
                            markup.encode(), base
                        ),
                        [],
                    )

        frameset_after = article + "<frameset></frameset>"
        extracted = MODULE.extract_content(
            frameset_after.encode(), "text/html", base
        )
        self.assertEqual(extracted["candidate_entry_count"], 0)
        self.assertEqual(extracted["entries"], [])

    def test_rejects_gzip_expansion_over_limit(self) -> None:
        """Bound decompressed bytes as well as compressed transport bytes."""
        compressed = gzip.compress(b"x" * (MODULE.MAX_BYTES + 1))
        response = FakeResponse(compressed, "text/plain", "https://example.test", "gzip")
        with self.assertRaises(MODULE.CollectionError):
            MODULE.read_bounded(response)

    def test_ignores_malformed_content_length_and_reads_bounded_body(self) -> None:
        """Do not let a malformed upstream header abort fallback verification."""
        for declared in ("not-a-number", "-1", "+7", "1_0", "７", " 7"):
            with self.subTest(declared=declared):
                response = FakeResponse(
                    b"bounded", "text/plain", "https://example.test"
                )
                response.headers = FakeHeaders(
                    "text/plain", content_length=declared
                )
                self.assertEqual(MODULE.read_bounded(response), b"bounded")

        response = FakeResponse(b"bounded", "text/plain", "https://example.test")
        response.headers = FakeHeaders("text/plain", content_length="0007")
        self.assertEqual(MODULE.read_bounded(response), b"bounded")

    def test_malformed_content_length_cannot_clip_http_error_body(self) -> None:
        """Undo CPython's permissive parsed length before bounded inspection."""

        class ResponseSocket:
            def __init__(self, payload: bytes) -> None:
                self.payload = io.BytesIO(payload)

            def makefile(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
                return self.payload

        prefix = b"<title>Vercel Security Checkpoint</title>"
        poison = b"<title>Duplicate title</title>"
        raw_response = MODULE.http.client.HTTPResponse(
            ResponseSocket(
                b"HTTP/1.1 429 Too Many Requests\r\n"
                b"Content-Type: text/html\r\n"
                b"Content-Length: +41\r\n\r\n"
                + prefix
                + poison
            )
        )
        raw_response.begin()
        self.assertEqual(raw_response.length, len(prefix))
        error = MODULE.urllib.error.HTTPError(
            "https://example.test/checkpoint",
            429,
            "Too Many Requests",
            raw_response.headers,
            raw_response,
        )

        with error:
            content = MODULE.read_bounded(error)

        self.assertEqual(content, prefix + poison)
        self.assertIsNone(MODULE.detect_access_constraint(content))

    def test_duplicate_content_length_fields_are_rejected_before_read(self) -> None:
        """Never trust the first of multiple conflicting transport lengths."""

        class ResponseSocket:
            def __init__(self, payload: bytes) -> None:
                self.payload = io.BytesIO(payload)

            def makefile(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
                return self.payload

        prefix = b"<title>Vercel Security Checkpoint</title>"
        poison = b"<title>Duplicate title</title>"
        raw_response = MODULE.http.client.HTTPResponse(
            ResponseSocket(
                b"HTTP/1.1 429 Too Many Requests\r\n"
                b"Content-Type: text/html\r\n"
                b"Content-Length: 41\r\n"
                + f"Content-Length: {len(prefix) + len(poison)}\r\n\r\n".encode()
                + prefix
                + poison
            )
        )
        raw_response.begin()
        self.assertEqual(raw_response.length, len(prefix))
        self.assertEqual(raw_response.headers.get_all("Content-Length"), [
            "41",
            str(len(prefix) + len(poison)),
        ])
        error = MODULE.urllib.error.HTTPError(
            "https://example.test/checkpoint",
            429,
            "Too Many Requests",
            raw_response.headers,
            raw_response,
        )

        with error, self.assertRaisesRegex(
            MODULE.CollectionError,
            "^response has duplicate Content-Length fields$",
        ):
            MODULE.read_bounded(error)

    def test_rejects_decimal_content_length_beyond_conversion_bound(self) -> None:
        """Reject an oversized decimal field instead of treating it as absent."""
        response = FakeResponse(
            b"<title>Vercel Security Checkpoint</title>",
            "text/html",
            "https://example.test/checkpoint",
        )
        response.headers = FakeHeaders(
            "text/html",
            content_length="0" * 4_301,
        )

        with self.assertRaisesRegex(
            MODULE.CollectionError,
            "^response Content-Length field is too long$",
        ):
            MODULE.read_bounded(response)

    def test_rejects_body_that_does_not_match_valid_content_length(self) -> None:
        """Do not parse a transport-truncated body as complete evidence."""
        body = b"bounded"
        response = FakeResponse(body, "text/plain", "https://example.test")
        response.headers = FakeHeaders(
            "text/plain", content_length=str(len(body) + 1)
        )
        with self.assertRaisesRegex(
            MODULE.CollectionError,
            "^response length does not match Content-Length$",
        ):
            MODULE.read_bounded(response)

    def test_reads_explicit_gate_body_from_http_error(self) -> None:
        """Recognize bounded login markup returned with an HTTP gate status."""
        source = MODULE.load_catalog(CATALOG)[0]
        body = b'<form><input type="password"><button>Sign in</button></form>'
        error = MODULE.urllib.error.HTTPError(
            source["page_url"],
            403,
            "Forbidden",
            FakeHeaders("text/html", content_length=str(len(body))),
            io.BytesIO(body),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(
            MODULE,
            "robots_policy",
            return_value={"allowed": True, "robots_url": "https://example/robots.txt", "robots_sha256": None},
        ), mock.patch.object(MODULE.urllib.request, "build_opener", return_value=opener):
            fetched = MODULE.fetch_url(source["page_url"], MODULE.source_hosts(source))
        self.assertEqual(fetched["http_status"], 403)
        self.assertEqual(MODULE.detect_access_constraint(fetched["content"]), "login")

    def test_reads_vercel_security_checkpoint_from_http_429(self) -> None:
        """Treat an explicit Vercel challenge as a gate, not a generic rate limit."""
        source = MODULE.load_catalog(CATALOG)[0]
        body = (
            b"<!doctype html><html><head>"
            b"<title>Vercel Security Checkpoint</title>"
            b"</head><body><main>Security verification</main></body></html>"
        )
        error = MODULE.urllib.error.HTTPError(
            source["page_url"],
            429,
            "Too Many Requests",
            FakeHeaders("text/html", content_length=str(len(body))),
            io.BytesIO(body),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(
            MODULE,
            "robots_policy",
            return_value={"allowed": True, "robots_url": "https://example/robots.txt", "robots_sha256": None},
        ), mock.patch.object(MODULE.urllib.request, "build_opener", return_value=opener):
            fetched = MODULE.fetch_url(source["page_url"], MODULE.source_hosts(source))
        self.assertEqual(fetched["http_status"], 429)
        self.assertEqual(MODULE.detect_access_constraint(fetched["content"]), "captcha")

    def test_malformed_length_does_not_hide_valid_checkpoint_evidence(self) -> None:
        """Keep bounded structural inspection when no valid length is declared."""
        source = MODULE.load_catalog(CATALOG)[0]
        body = (
            b"<!doctype html><html><head><title>\t Vercel Security Checkpoint\n"
            b"</title></head><body></body></html>"
        )
        error = MODULE.urllib.error.HTTPError(
            source["page_url"],
            429,
            "Too Many Requests",
            FakeHeaders("text/html", content_length="not-a-number"),
            io.BytesIO(body),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(
            MODULE,
            "robots_policy",
            return_value={
                "allowed": True,
                "robots_url": "https://example/robots.txt",
                "robots_sha256": None,
            },
        ), mock.patch.object(
            MODULE.urllib.request, "build_opener", return_value=opener
        ):
            fetched = MODULE.fetch_url(
                source["page_url"], MODULE.source_hosts(source)
            )
        self.assertEqual(fetched["http_status"], 429)
        self.assertEqual(MODULE.detect_access_constraint(fetched["content"]), "captcha")

    def test_generic_http_429_remains_fail_closed(self) -> None:
        """Do not infer a CAPTCHA or login gate from status 429 alone."""
        source = MODULE.load_catalog(CATALOG)[0]
        body = b"<html><head><title>Too Many Requests</title></head></html>"
        error = MODULE.urllib.error.HTTPError(
            source["page_url"],
            429,
            "Too Many Requests",
            FakeHeaders("text/html", content_length=str(len(body))),
            io.BytesIO(body),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(
            MODULE,
            "robots_policy",
            return_value={"allowed": True, "robots_url": "https://example/robots.txt", "robots_sha256": None},
        ), mock.patch.object(MODULE.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(MODULE.CollectionError, "^http_429$"):
                MODULE.fetch_url(source["page_url"], MODULE.source_hosts(source))

    def test_http_429_accepts_only_captcha_constraint_evidence(self) -> None:
        """Do not seal login/paywall text from a rate-limit response as a gate."""
        source = MODULE.load_catalog(CATALOG)[0]
        bodies = (
            b'<html><body><input type="password">Sign in</body></html>',
            b"<html><body><p>Subscription required</p></body></html>",
        )
        for body in bodies:
            with self.subTest(body=body):
                error = MODULE.urllib.error.HTTPError(
                    source["page_url"],
                    429,
                    "Too Many Requests",
                    FakeHeaders("text/html", content_length=str(len(body))),
                    io.BytesIO(body),
                )
                opener = mock.Mock()
                opener.open.side_effect = error
                with mock.patch.object(
                    MODULE,
                    "robots_policy",
                    return_value={
                        "allowed": True,
                        "robots_url": "https://example/robots.txt",
                        "robots_sha256": None,
                    },
                ), mock.patch.object(
                    MODULE.urllib.request, "build_opener", return_value=opener
                ):
                    with self.assertRaisesRegex(MODULE.CollectionError, "^http_429$"):
                        MODULE.fetch_url(
                            source["page_url"], MODULE.source_hosts(source)
                        )

    def test_unreadable_http_429_body_remains_fail_closed(self) -> None:
        """Normalize bounded-body read failures back to the original 429 error."""
        source = MODULE.load_catalog(CATALOG)[0]
        error = MODULE.urllib.error.HTTPError(
            source["page_url"],
            429,
            "Too Many Requests",
            FakeHeaders("text/html"),
            io.BytesIO(b"unreadable"),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(
            MODULE,
            "robots_policy",
            return_value={"allowed": True, "robots_url": "https://example/robots.txt", "robots_sha256": None},
        ), mock.patch.object(
            MODULE.urllib.request, "build_opener", return_value=opener
        ), mock.patch.object(
            MODULE, "read_bounded", side_effect=OSError("body unavailable")
        ):
            with self.assertRaisesRegex(MODULE.CollectionError, "^http_429$"):
                MODULE.fetch_url(source["page_url"], MODULE.source_hosts(source))

    def test_incomplete_http_429_body_remains_fail_closed(self) -> None:
        """Normalize a truncated HTTP error body back to the original 429 error."""
        source = MODULE.load_catalog(CATALOG)[0]
        error = MODULE.urllib.error.HTTPError(
            source["page_url"],
            429,
            "Too Many Requests",
            FakeHeaders("text/html"),
            io.BytesIO(b"partial"),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(
            MODULE,
            "robots_policy",
            return_value={"allowed": True, "robots_url": "https://example/robots.txt", "robots_sha256": None},
        ), mock.patch.object(
            MODULE.urllib.request, "build_opener", return_value=opener
        ), mock.patch.object(
            MODULE,
            "read_bounded",
            side_effect=MODULE.http.client.IncompleteRead(b"partial"),
        ):
            with self.assertRaisesRegex(MODULE.CollectionError, "^http_429$"):
                MODULE.fetch_url(source["page_url"], MODULE.source_hosts(source))

    def test_declared_truncated_http_429_checkpoint_remains_fail_closed(self) -> None:
        """Reject a complete-looking checkpoint when transport bytes are missing."""
        source = MODULE.load_catalog(CATALOG)[0]
        body = (
            b"<html><head><title>Vercel Security Checkpoint</title>"
            b"</head><body></body></html>"
        )
        error = MODULE.urllib.error.HTTPError(
            source["page_url"],
            429,
            "Too Many Requests",
            FakeHeaders("text/html", content_length=str(len(body) + 64)),
            io.BytesIO(body),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(
            MODULE,
            "robots_policy",
            return_value={
                "allowed": True,
                "robots_url": "https://example/robots.txt",
                "robots_sha256": None,
            },
        ), mock.patch.object(
            MODULE.urllib.request, "build_opener", return_value=opener
        ):
            with self.assertRaisesRegex(MODULE.CollectionError, "^http_429$"):
                MODULE.fetch_url(source["page_url"], MODULE.source_hosts(source))

    def test_vercel_checkpoint_prose_mention_is_not_a_gate(self) -> None:
        """Require explicit checkpoint markup instead of a prose phrase match."""
        body = (
            b"<html><head><title>Cloud security news</title></head>"
            b"<body><article>Vercel Security Checkpoint behavior changed.</article></body></html>"
        )
        self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_vercel_checkpoint_title_in_comment_is_not_a_gate(self) -> None:
        """Do not recognize title-looking bytes inside an HTML comment."""
        body = (
            b"<html><head><!-- <title>Vercel Security Checkpoint</title> -->"
            b"<title>Ordinary article</title></head><body></body></html>"
        )
        self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_vercel_checkpoint_title_in_script_is_not_a_gate(self) -> None:
        """Do not recognize title-looking text inside a script raw-text element."""
        body = (
            b"<html><head><script>"
            b"const sample = '<title>Vercel Security Checkpoint</title>';"
            b"</script><title>Ordinary article</title></head><body></body></html>"
        )
        self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_challenge_script_double_escape_does_not_expose_widget(self) -> None:
        """Reject a script end callback that HTML5 keeps double escaped."""
        double_escaped = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<script><!--<script></script>"
            b"<div class='g-recaptcha'></div></body></html>"
        )
        parsed = MODULE.parse_access_constraint_markup(double_escaped.decode())
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.structure_invalid)
        self.assertTrue(parsed.captcha_widget)
        self.assertFalse(parsed.has_captcha_evidence)
        self.assertIsNone(MODULE.detect_access_constraint(double_escaped))

        escaped_then_closed = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<script><!-- fixture --></script>"
            b"<div class='g-recaptcha'></div></body></html>"
        )
        self.assertEqual(
            MODULE.detect_access_constraint(escaped_then_closed),
            "captcha",
        )

    def test_challenge_script_double_escape_search_is_segment_bounded(self) -> None:
        """Do not rescan the remaining body for every closed escape segment."""
        pattern = MODULE.SCRIPT_DOUBLE_ESCAPE_START

        class SearchRecorder:
            def __init__(self) -> None:
                self.ranges: list[tuple[int, int]] = []

            def search(
                self,
                data: str,
                start: int,
                end: int,
            ) -> object:
                self.ranges.append((start, end))
                return pattern.search(data, start, end)

        recorder = SearchRecorder()
        data = "<!-- -->" * 4096 + "<script>"
        with mock.patch.object(MODULE, "SCRIPT_DOUBLE_ESCAPE_START", recorder):
            self.assertFalse(MODULE.script_data_enters_double_escaped_state(data))

        self.assertEqual(len(recorder.ranges), 4096)
        self.assertLessEqual(
            sum(end - start for start, end in recorder.ranges),
            len(data),
        )

    def test_vercel_checkpoint_requires_plain_document_title_in_head(self) -> None:
        """Reject title attributes, body placement, and template-contained markup."""
        bodies = (
            b"<html><head><title data-test='fixture'>Vercel Security Checkpoint</title></head></html>",
            b"<html><head></head><body><title>Vercel Security Checkpoint</title></body></html>",
            b"<html><head><template><title>Vercel Security Checkpoint</title></template></head></html>",
            b"<html><head><title>Vercel Security Checkpoint</title></head><body/></html>",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_vercel_checkpoint_rejects_head_started_after_body(self) -> None:
        """Do not let a late malformed head re-enable document-title capture."""
        bodies = (
            b"<html><body><head><title>Vercel Security Checkpoint</title></head></body></html>",
            b"<html><head><title>Ordinary article</title></head><body></body>"
            b"<head><title>Vercel Security Checkpoint</title></head></html>",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_vercel_checkpoint_rejects_mismatched_opaque_closers(self) -> None:
        """Keep an opaque container active until its own matching end tag."""
        bodies = (
            b"<html><head><template></noscript>"
            b"<title>Vercel Security Checkpoint</title></template></head></html>",
            b"<html><head><noscript></template>"
            b"<title>Vercel Security Checkpoint</title></noscript></head></html>",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_access_constraint_requires_exact_trust_boundary_end_tags(self) -> None:
        """Reject Python callbacks synthesized from malformed closing tokens."""
        bodies = (
            b"<html><head><template></template fixture>"
            b"<title>Vercel Security Checkpoint</title></head></html>",
            b"<html><head><title>Vercel Security Checkpoint</title foo>"
            b"</head></html>",
            b"<html><head><title>Vercel Security Checkpoint</title>"
            b"</head fixture><body></body></html>",
            b"<html><head><title>Rate limited</title></head><body>"
            b"<div class='g-recaptcha'></div></body fixture></html>",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_vercel_checkpoint_requires_one_document_title(self) -> None:
        """Reject duplicate titles regardless of which one names the checkpoint."""
        bodies = (
            b"<html><head><title>Ordinary article</title>"
            b"<title>Vercel Security Checkpoint</title></head></html>",
            b"<html><head><title>Vercel Security Checkpoint</title>"
            b"<title>Ordinary article</title></head></html>",
            b"<html><head><title>Vercel Security Checkpoint</title>"
            b"<title>Vercel Security Checkpoint</title></head></html>",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_vercel_checkpoint_title_uses_ascii_only_case_folding(self) -> None:
        """Reject Unicode lookalikes while accepting ordinary ASCII case."""
        lookalike = (
            "<html><head><title>Vercel Security Chec\u212apoint</title>"
            "</head><body></body></html>"
        ).encode()
        self.assertIsNone(MODULE.detect_access_constraint(lookalike))
        ascii_case = (
            b"<html><head><title>vErCeL sEcUrItY cHeCkPoInT</title>"
            b"</head><body></body></html>"
        )
        self.assertEqual(MODULE.detect_access_constraint(ascii_case), "captcha")
        ascii_whitespace = (
            b"<html><head><title>\t Vercel Security Checkpoint\r\n </title>"
            b"</head><body></body></html>"
        )
        self.assertEqual(
            MODULE.detect_access_constraint(ascii_whitespace), "captcha"
        )

    def test_generic_captcha_markers_require_structural_widget_markup(self) -> None:
        """Ignore marker strings in comments, scripts, prose, and unrelated attrs."""
        bodies = (
            b"<html><head><title>Rate limited</title><!-- g-recaptcha hcaptcha "
            b"cf-turnstile captcha challenge --></head><body></body></html>",
            b"<html><head><title>Rate limited</title><script>"
            b"const fixture = 'g-recaptcha hcaptcha cf-turnstile captcha challenge';"
            b"</script></head><body></body></html>",
            b"<html><head><title>Rate limited</title></head><body><p>"
            b"g-recaptcha hcaptcha cf-turnstile captcha challenge"
            b"</p></body></html>",
            b"<html><head><title>Rate limited</title></head><body>"
            b"<div data-fixture='hcaptcha'>ordinary content</div></body></html>",
            b"<html><head><title>Rate limited</title></head><body>"
            b"<div class='g-recaptcha'/></body></html>",
            b"<html><head><title>Rate limited</title></head><body>"
            b"<div class='ordinary' class='h-captcha'></div></body></html>",
            b"<html><head><title>Rate limited</title></head><body><template>"
            b"<div class='cf-turnstile'></div></template></body></html>",
            b"<html><head><title>Rate limited</title></head><body><select>"
            b"<div class='g-recaptcha'></div></select></body></html>",
            b"<html><head><title>Rate limited</title></head><body><select>"
            b"<div class='g-recaptcha'></div></select fixture></body></html>",
            b"<html><head><title>Rate limited</title></head><body></body>"
            b"<div class='g-recaptcha'></div></html>",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

        widgets = (
            b"<div class='g-recaptcha'></div>",
            b"<div class='h-captcha'></div>",
            b"<section id='cf-turnstile'></section>",
            b"<iframe aria-label='Captcha Challenge'></iframe>",
        )
        for widget in widgets:
            with self.subTest(widget=widget):
                body = (
                    b"<html><head><title>Rate limited</title></head><body>"
                    + widget
                    + b"</body></html>"
                )
                self.assertEqual(MODULE.detect_access_constraint(body), "captcha")

    def test_captcha_widget_tokens_use_ascii_only_case_folding(self) -> None:
        """Reject Unicode lookalikes while accepting ordinary ASCII token case."""
        lookalike = (
            "<html><head><title>Rate limited</title></head><body>"
            "<section id='cf-turn\u017ftile'></section></body></html>"
        ).encode()
        self.assertIsNone(MODULE.detect_access_constraint(lookalike))
        ascii_case = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<section id='CF-TURNSTILE'></section></body></html>"
        )
        self.assertEqual(MODULE.detect_access_constraint(ascii_case), "captcha")

    def test_captcha_widget_checks_both_semantic_label_attributes(self) -> None:
        """Accept either supported label even when the other is unrelated."""
        widgets = (
            b"<iframe aria-label='security widget' "
            b"title='Captcha Challenge'></iframe>",
            b"<iframe aria-label='Captcha Challenge' "
            b"title='security widget'></iframe>",
        )
        for widget in widgets:
            with self.subTest(widget=widget):
                body = (
                    b"<html><head><title>Rate limited</title></head><body>"
                    + widget
                    + b"</body></html>"
                )
                self.assertEqual(
                    MODULE.detect_access_constraint(body),
                    "captcha",
                )

    def test_captcha_widget_attribute_tokenization_is_bounded(self) -> None:
        """Reject oversized candidate attributes before creating token objects."""
        limit = MODULE.AccessConstraintMarkupParser.MAX_WIDGET_ATTRIBUTE_CHARS
        oversized = "x " * (limit // 2) + "g-recaptcha"
        for name in ("class", "id", "aria-label", "title"):
            with self.subTest(name=name):
                body = (
                    "<html><head><title>Rate limited</title></head><body>"
                    f"<div {name}='{oversized}'></div></body></html>"
                ).encode()
                self.assertIsNone(MODULE.detect_access_constraint(body))

        bounded = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<div class='ordinary g-recaptcha'></div></body></html>"
        )
        self.assertEqual(MODULE.detect_access_constraint(bounded), "captcha")

    def test_challenge_raw_start_tag_is_bounded_before_attribute_parsing(self) -> None:
        """Skip an oversized token before HTMLParser builds its attribute list."""
        limit = MODULE.AccessConstraintMarkupParser.MAX_RAW_START_TAG_CHARS
        oversized = "<div " + "a=x " * (limit // 4 + 1) + "class=g-recaptcha>"
        self.assertGreater(len(oversized), limit)

        with mock.patch.object(
            MODULE.HTMLParser,
            "parse_starttag",
            side_effect=AssertionError("base attribute parser invoked"),
        ):
            parsed = MODULE.parse_access_constraint_markup(oversized)

        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.structure_invalid)
        self.assertFalse(parsed.captcha_widget)
        self.assertFalse(parsed.has_captcha_evidence)

        bounded = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<div class='ordinary g-recaptcha'></div></body></html>"
        )
        self.assertEqual(MODULE.detect_access_constraint(bounded), "captcha")

    def test_invalid_document_title_poisons_widget_evidence(self) -> None:
        """Do not let a valid widget override ambiguous document-title structure."""
        widget = b"<div class='g-recaptcha'></div>"
        bodies = (
            b"<html><head><title data-fixture='1'>Rate limited</title></head><body>"
            + widget
            + b"</body></html>",
            b"<html><head><title>Rate limited</title><title>Second</title></head><body>"
            + widget
            + b"</body></html>",
            b"<html><head><title>Rate limited</title></head><body>"
            b"<title>Fixture title</title>"
            + widget
            + b"</body></html>",
            b"<html><body><head><title>Rate limited</title></head>"
            + widget
            + b"</body></html>",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_access_constraint_parser_inspects_entire_bounded_body(self) -> None:
        """Do not trust prefix evidence before later bounded structure poisons it."""
        body = (
            b"<html><head><title>Vercel Security Checkpoint</title>"
            b"</head><body>"
            + b" " * (1024 * 1024)
            + b"<title>Late fixture title</title></body></html>"
        )
        self.assertLess(len(body), MODULE.MAX_BYTES)
        self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_access_constraint_tracks_implicit_body_tokens(self) -> None:
        """Reject late heads and recognize widgets when the body tag is omitted."""
        late_heads = (
            b"<html><div>body content</div><head>"
            b"<title>Vercel Security Checkpoint</title></head></html>",
            b"<html>body text<head>"
            b"<title>Vercel Security Checkpoint</title></head></html>",
        )
        for body in late_heads:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

        implicit_body_widgets = (
            b"<html><head><title>Rate limited</title></head>"
            b"<div class='g-recaptcha'></div></html>",
            b"<html><head><title>Rate limited</title></head>body text"
            b"<section id='cf-turnstile'></section></html>",
        )
        for body in implicit_body_widgets:
            with self.subTest(body=body):
                self.assertEqual(MODULE.detect_access_constraint(body), "captcha")

        post_document_script = (
            b"<html><head><title>Vercel Security Checkpoint</title></head>"
            b"<body></body></html><script type='module'>challenge()</script>"
        )
        self.assertEqual(
            MODULE.detect_access_constraint(post_document_script), "captcha"
        )

    def test_after_head_opaque_tokens_do_not_start_the_body(self) -> None:
        """Apply head rules to script, style, and template before explicit body."""
        after_head_tokens = (
            b"<script>const fixture = 'g-recaptcha';</script>",
            b"<style>.g-recaptcha { display: block; }</style>",
            b"<template><div class='g-recaptcha'></div></template>",
        )
        for token in after_head_tokens:
            with self.subTest(token=token):
                body = (
                    b"<html><head><title>Rate limited</title></head>"
                    + token
                    + b"<body><div class='g-recaptcha'></div></body></html>"
                )
                self.assertEqual(MODULE.detect_access_constraint(body), "captcha")

        marker_only = (
            b"<html><head><title>Rate limited</title></head>"
            b"<template><div class='g-recaptcha'></div></template>"
            b"<body></body></html>"
        )
        self.assertIsNone(MODULE.detect_access_constraint(marker_only))

    def test_foreign_content_titles_do_not_poison_document_evidence(self) -> None:
        """Ignore local SVG/MathML titles without trusting nested widgets."""
        foreign_titles = (
            b"<svg><title>Lock icon</title></svg>",
            b"<math><title>Formula description</title></math>",
        )
        for foreign_title in foreign_titles:
            with self.subTest(foreign_title=foreign_title):
                body = (
                    b"<html><head><title>Rate limited</title></head><body>"
                    + foreign_title
                    + b"<div class='g-recaptcha'></div></body></html>"
                )
                self.assertEqual(MODULE.detect_access_constraint(body), "captcha")

        foreign_widget = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<svg><foreignObject><div class='g-recaptcha'></div>"
            b"</foreignObject></svg></body></html>"
        )
        self.assertIsNone(MODULE.detect_access_constraint(foreign_widget))

    def test_frameset_documents_cannot_supply_captcha_evidence(self) -> None:
        """Fail closed for actual framesets without trusting iframe callbacks."""
        frameset_documents = (
            b"<html><head><title>Rate limited</title></head>"
            b"<frameset><iframe aria-label='Captcha Challenge'></iframe></frameset>"
            b"</html>",
            b"<html><head><title>Rate limited</title></head><body>"
            b"<div class='g-recaptcha'></div></body><frameset></frameset></html>",
            b"<html><head><title>Rate limited</title></head><body>"
            b"<div class='g-recaptcha'></div></body></frameset></html>",
        )
        for body in frameset_documents:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

        suppressed_frameset = (
            b"<html><head><title>Rate limited</title></head><body><template>"
            b"<frameset><iframe aria-label='Captcha Challenge'></iframe></frameset>"
            b"</template><div class='g-recaptcha'></div></body></html>"
        )
        self.assertEqual(
            MODULE.detect_access_constraint(suppressed_frameset), "captcha"
        )

    def test_access_constraint_ignores_only_a_leading_utf8_bom(self) -> None:
        """Allow the encoding signature without ignoring later body data."""
        checkpoint = (
            b"<html><head><title>Vercel Security Checkpoint</title>"
            b"</head><body></body></html>"
        )
        self.assertEqual(
            MODULE.detect_access_constraint(b"\xef\xbb\xbf" + checkpoint),
            "captcha",
        )
        self.assertIsNone(
            MODULE.detect_access_constraint(b" \xef\xbb\xbf" + checkpoint)
        )

    def test_access_constraint_infers_an_optional_initial_head(self) -> None:
        """Recognize head-compatible tokens before body without an explicit head."""
        implied_heads = (
            b"<!doctype html><title>Vercel Security Checkpoint</title><body></body>",
            b"<!doctype html><meta charset='utf-8'>"
            b"<title>Vercel Security Checkpoint</title><body></body>",
            b"<!doctype html><meta charset='utf-8'/>"
            b"<title>Vercel Security Checkpoint</title><body></body>",
            b"<!doctype html><script>const fixture = '<body>';</script>"
            b"<title>Vercel Security Checkpoint</title><body></body>",
        )
        for body in implied_heads:
            with self.subTest(body=body):
                self.assertEqual(MODULE.detect_access_constraint(body), "captcha")

        late_title = (
            b"<!doctype html><div>body content</div>"
            b"<title>Vercel Security Checkpoint</title>"
        )
        self.assertIsNone(MODULE.detect_access_constraint(late_title))

    def test_access_constraint_uses_constant_space_raw_tag_tracking(self) -> None:
        """Do not retain one Python offset object for every response newline."""
        body = (
            b"<html><head><title>Rate limited</title></head><body>"
            + b"\n" * (512 * 1024)
            + b"<div class='g-recaptcha'></div></body></html>"
        )
        parsed = MODULE.parse_access_constraint_markup(body.decode())
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.has_captcha_evidence)
        self.assertNotIn("line_offsets", vars(parsed))
        self.assertNotIn("source_text", vars(parsed))
        self.assertIsNone(parsed.raw_end_tag_start)

    def test_access_constraint_bounds_opaque_container_nesting(self) -> None:
        """Poison challenge evidence instead of retaining unbounded nesting."""
        body = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<div class='g-recaptcha'></div>"
            + b"<template>" * (MODULE.AccessConstraintMarkupParser.MAX_OPAQUE_DEPTH + 1)
            + b"</body></html>"
        )
        parsed = MODULE.parse_access_constraint_markup(body.decode())
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.structure_invalid)
        self.assertTrue(parsed.captcha_widget)
        self.assertLessEqual(
            len(parsed.opaque_containers),
            MODULE.AccessConstraintMarkupParser.MAX_OPAQUE_DEPTH,
        )
        self.assertFalse(parsed.has_captcha_evidence)
        self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_self_closing_raw_text_start_suppresses_following_widgets(self) -> None:
        """Treat a slash on non-void raw/RCDATA starts as non-closing."""
        containers = (
            b"iframe",
            b"noembed",
            b"noframes",
            b"plaintext",
            b"script",
            b"style",
            b"textarea",
            b"xmp",
        )
        for container in containers:
            with self.subTest(container=container):
                body = (
                    b"<html><head><title>Rate limited</title></head><body><"
                    + container
                    + b"/><div class='g-recaptcha'></div></body></html>"
                )
                self.assertIsNone(MODULE.detect_access_constraint(body))

        closed_iframe = (
            b"<html><head><title>Rate limited</title></head><body><iframe/>"
            b"<div class='g-recaptcha'></div></iframe>"
            b"<section id='cf-turnstile'></section></body></html>"
        )
        self.assertEqual(MODULE.detect_access_constraint(closed_iframe), "captcha")

    def test_noframes_remains_head_compatible_in_challenge_documents(self) -> None:
        """Process noframes with head rules before the document body begins."""
        documents = (
            b"<html><head><noframes>fallback</noframes>"
            b"<title>Vercel Security Checkpoint</title></head></html>",
            b"<noframes>fallback</noframes>"
            b"<title>Vercel Security Checkpoint</title>",
            b"<html><head><title>Rate limited</title></head>"
            b"<noframes><div class='g-recaptcha'></div></noframes>"
            b"<body><section id='cf-turnstile'></section></body></html>",
        )
        for body in documents:
            with self.subTest(body=body):
                parsed = MODULE.parse_access_constraint_markup(body.decode())
                self.assertIsNotNone(parsed)
                self.assertFalse(parsed.structure_invalid)
                self.assertEqual(MODULE.detect_access_constraint(body), "captcha")

    def test_self_closing_foreign_content_closes_immediately(self) -> None:
        """Honor the self-closing flag on SVG and MathML roots."""
        for foreign_root in (b"<svg/>", b"<math/>"):
            with self.subTest(foreign_root=foreign_root):
                body = (
                    b"<html><head><title>Rate limited</title></head><body>"
                    + foreign_root
                    + b"<div class='g-recaptcha'></div></body></html>"
                )
                self.assertEqual(MODULE.detect_access_constraint(body), "captcha")

        marker_attribute = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<svg class='g-recaptcha'/><math id='cf-turnstile'/>"
            b"</body></html>"
        )
        self.assertIsNone(MODULE.detect_access_constraint(marker_attribute))

    def test_nested_form_start_cannot_supply_captcha_evidence(self) -> None:
        """Ignore a nested form token while the HTML form pointer is occupied."""
        rejected = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<form><form class='g-recaptcha'></form></form></body></html>",
            b"<html><head><title>Rate limited</title></head><body>"
            b"<form/><form class='g-recaptcha'></form></body></html>",
            b"<html><head><title>Rate limited</title></head><body>"
            b"<form></form fixture><form class='g-recaptcha'></form>"
            b"</body></html>",
        )
        for body in rejected:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

        later_form = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<form><form></form><form class='g-recaptcha'></form>"
            b"</body></html>"
        )
        self.assertEqual(MODULE.detect_access_constraint(later_form), "captcha")

        direct_form = (
            b"<html><head><title>Rate limited</title></head><body>"
            b"<form class='h-captcha'></form></body></html>"
        )
        self.assertEqual(MODULE.detect_access_constraint(direct_form), "captcha")

    def test_access_constraint_finalizes_a_valid_head_at_eof(self) -> None:
        """Apply the implied head transition when a minimal document ends."""
        valid = (
            b"<title>Vercel Security Checkpoint</title>",
            b"<html><head><title>Vercel Security Checkpoint</title>",
            b"<meta charset='utf-8'><title>Vercel Security Checkpoint</title>",
        )
        for body in valid:
            with self.subTest(body=body):
                parsed = MODULE.parse_access_constraint_markup(body.decode())
                self.assertIsNotNone(parsed)
                self.assertFalse(parsed.in_head)
                self.assertTrue(parsed.head_closed)
                self.assertTrue(parsed.vercel_checkpoint)
                self.assertEqual(MODULE.detect_access_constraint(body), "captcha")

        incomplete = (
            b"<title>Vercel Security Checkpoint",
            b"<script><title>Vercel Security Checkpoint</title>",
        )
        for body in incomplete:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_body_transition_end_tags_close_the_challenge_head(self) -> None:
        """Do not accept a document title after in-head br/html end tokens."""
        for transition in (b"</br>", b"</html>"):
            with self.subTest(transition=transition):
                body = (
                    b"<html><head>"
                    + transition
                    + b"<title>Vercel Security Checkpoint</title></head>"
                )
                parsed = MODULE.parse_access_constraint_markup(body.decode())
                self.assertIsNotNone(parsed)
                self.assertFalse(parsed.in_head)
                self.assertTrue(parsed.head_closed)
                self.assertTrue(parsed.body_started)
                self.assertIsNone(MODULE.detect_access_constraint(body))

        malformed = (
            b"<html><head></br fixture>"
            b"<title>Vercel Security Checkpoint</title></head>"
        )
        self.assertIsNone(MODULE.detect_access_constraint(malformed))

    def test_body_end_tag_can_close_an_implied_empty_body(self) -> None:
        """Honor a safe body closer when the optional start tag was omitted."""
        valid = (
            b"<html><head><title>Vercel Security Checkpoint</title></head>"
            b"</body></html>",
            b"<title>Vercel Security Checkpoint</title></body></html>",
        )
        for body in valid:
            with self.subTest(body=body):
                parsed = MODULE.parse_access_constraint_markup(body.decode())
                self.assertIsNotNone(parsed)
                self.assertTrue(parsed.body_started)
                self.assertTrue(parsed.body_closed)
                self.assertTrue(parsed.head_closed)
                self.assertEqual(MODULE.detect_access_constraint(body), "captcha")

        invalid = (
            b"<html><head><title>Vercel Security Checkpoint</title></head>"
            b"</body></body></html>",
            b"<html><head><title>Vercel Security Checkpoint</title></head>"
            b"</body fixture></html>",
        )
        for body in invalid:
            with self.subTest(body=body):
                self.assertIsNone(MODULE.detect_access_constraint(body))

    def test_legacy_login_and_paywall_matching_keeps_its_prefix_bound(self) -> None:
        """Use the full body only for structural CAPTCHA verification."""
        suffix_only = (
            b"<html><head><title>Ordinary page</title></head><body>"
            + b"x" * MODULE.LEGACY_CONSTRAINT_TEXT_BYTES
            + b"<input type='password'>Sign in. Subscription required."
            + b"</body></html>"
        )
        self.assertIsNone(MODULE.detect_access_constraint(suffix_only))

        prefix_login = b"<input type='password'>Sign in"
        prefix_paywall = b"Subscription required"
        self.assertEqual(MODULE.detect_access_constraint(prefix_login), "login")
        self.assertEqual(MODULE.detect_access_constraint(prefix_paywall), "paywall")

        late_structural_widget = (
            b"<html><head><title>Rate limited</title></head><body>"
            + b" " * MODULE.LEGACY_CONSTRAINT_TEXT_BYTES
            + b"<div class='g-recaptcha'></div></body></html>"
        )
        self.assertEqual(
            MODULE.detect_access_constraint(late_structural_widget), "captcha"
        )

    def test_after_head_metadata_does_not_start_the_body(self) -> None:
        """Apply head rules to supported metadata before an explicit body."""
        after_head_metadata = (
            b"<meta charset='utf-8'>",
            b"<link rel='stylesheet' href='/checkpoint.css'>",
            b"<base href='https://example.test/'>",
            b"<meta charset='utf-8'/>",
        )
        for token in after_head_metadata:
            with self.subTest(token=token):
                body = (
                    b"<html><head><title>Rate limited</title></head>"
                    + token
                    + b"<body><div class='g-recaptcha'></div></body></html>"
                )
                self.assertEqual(MODULE.detect_access_constraint(body), "captcha")

        metadata_marker = (
            b"<html><head><title>Rate limited</title></head>"
            b"<meta class='g-recaptcha'><body></body></html>"
        )
        self.assertIsNone(MODULE.detect_access_constraint(metadata_marker))

    def test_rejects_escape_before_creating_output(self) -> None:
        """Do not leave directories outside the caller-bound staging root."""
        with tempfile.TemporaryDirectory() as temporary:
            trusted = Path(temporary) / "trusted"
            trusted.mkdir()
            escaped = Path(temporary) / "escaped"
            with mock.patch.dict(os.environ, {"COLLECTION_OUTPUT_ROOT": str(trusted)}):
                with self.assertRaises(MODULE.CollectionError):
                    MODULE.resolve_output_root(str(escaped))
            self.assertFalse(escaped.exists())

    def test_independently_verifies_search_fallback_on_source_host(self) -> None:
        """Convert a model-discovered URL into trusted evidence only after fetch."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            candidate = source["page_url"]
            write_bound_manifest(
                manifest, {source["name"]: "needs_search_fallback"}
            )
            request.write_text(json.dumps({"version": 1, "resolutions": [{
                "name": source["name"], "method": "site_search", "url": candidate
            }], "date_evidence": []}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}), mock.patch.object(
                MODULE, "fetch_url", return_value={
                    "final_url": candidate,
                    "http_status": 200,
                    "content_type": "text/html",
                    "content": (
                        b'<article><time datetime="2026-08-08"></time>'
                        b'<h2><a href="/article">Public article</a></h2>'
                        b'<a href="/author">Alice</a><a href="/tag">AI</a></article>'
                        b'<nav><a href="/about">About</a></nav>'
                    ),
                }
            ) as fetch:
                MODULE.verify_resolutions(CATALOG, manifest, request, output)
            fetch.assert_called_once()
            verified = json.loads(output.read_text(encoding="utf-8"))["resolutions"][0]
            self.assertEqual(verified["status"], "verified_fallback")
            self.assertEqual(verified["final_url"], candidate)
            self.assertEqual(verified["extracted_entry_count"], 4)
            self.assertEqual(verified["candidate_entry_count"], 1)
            self.assertEqual(verified["date_evidence_count"], 1)
            self.assertEqual(verified["published_dates"], ["2026-08-08"])
            self.assertEqual(
                verified["candidate_evidence"][0]["url"],
                MODULE.urllib.parse.urljoin(candidate, "/article"),
            )

    def test_check_resolutions_cli_is_read_only(self) -> None:
        """Let the collection agent preflight the exact request without evidence writes."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, _catalog, _manifest, request = create_check_runtime(
                root, {source["name"]: "needs_search_fallback"}
            )
            candidate = MODULE.urllib.parse.urljoin(
                source["page_url"], "/dated-article"
            )
            request.write_text(
                json.dumps({
                    "version": 1,
                    "resolutions": [{
                        "name": source["name"],
                        "method": "site_search",
                        "url": candidate,
                    }],
                    "date_evidence": [],
                }),
                encoding="utf-8",
            )
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            with mock.patch.object(
                MODULE, "__file__", str(executable)
            ), mock.patch.object(
                MODULE, "canonical_runtime_root", return_value=executable.parent
            ), mock.patch.object(MODULE, "fetch_url", return_value={
                "final_url": candidate,
                "http_status": 200,
                "content_type": "text/html",
                "content": (
                    b'<meta property="article:published_time" '
                    b'content="2026-08-22">'
                    b'<article><h2><a href="/dated-article">'
                    b'Dated article</a></h2></article>'
                ),
            }):
                status = MODULE.main([
                    str(SCRIPT),
                    "--check-resolutions",
                    str(request),
                ])
            self.assertEqual(status, 0)
            self.assertEqual(
                {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                },
                before,
            )

    def test_check_resolutions_requires_every_unresolved_source(self) -> None:
        """Do not let an empty request pass preflight while a fallback is unresolved."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, _catalog, _manifest, request = create_check_runtime(
                root, {source["name"]: "needs_search_fallback"}
            )
            request.write_text(
                json.dumps({"version": 1, "resolutions": [], "date_evidence": []}),
                encoding="utf-8",
            )
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            with mock.patch.object(
                MODULE, "__file__", str(executable)
            ), mock.patch.object(
                MODULE, "canonical_runtime_root", return_value=executable.parent
            ), mock.patch.object(MODULE, "fetch_url") as fetch, mock.patch(
                "sys.stderr", new=io.StringIO()
            ):
                status = MODULE.main([
                    str(SCRIPT),
                    "--check-resolutions",
                    str(request),
                ])
            self.assertEqual(status, 75)
            fetch.assert_not_called()
            self.assertEqual(
                {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                },
                before,
            )

    def test_check_resolutions_rejects_agent_supplied_catalog_arguments(self) -> None:
        """Never let staging content replace the executable-bound host allowlist."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, _catalog, manifest, request = create_check_runtime(
                root, {source["name"]: "needs_search_fallback"}
            )
            alternate_catalog = request.parent / "alternate-catalog.json"
            alternate_catalog.write_text(
                json.dumps({
                    "version": 1,
                    "sources": [{
                        "name": source["name"],
                        "tier": 1,
                        "feed_url": None,
                        "page_url": "https://agent-controlled.example/news",
                    }],
                }),
                encoding="utf-8",
            )
            request.write_text(
                json.dumps({"version": 1, "resolutions": [], "date_evidence": []}),
                encoding="utf-8",
            )
            with mock.patch.object(
                MODULE, "__file__", str(executable)
            ), mock.patch.object(
                MODULE, "canonical_runtime_root", return_value=executable.parent
            ), mock.patch.object(MODULE, "fetch_url") as fetch, mock.patch(
                "sys.stderr", new=io.StringIO()
            ):
                status = MODULE.main([
                    str(SCRIPT),
                    "--check-resolutions",
                    str(alternate_catalog),
                    str(manifest),
                    str(request),
                ])
            self.assertEqual(status, 64)
            fetch.assert_not_called()

    def test_check_resolutions_rejects_manifest_catalog_digest_mismatch(self) -> None:
        """Fail before fetch when the canonical manifest is not catalog-bound."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, _catalog, manifest, request = create_check_runtime(
                root, {source["name"]: "needs_search_fallback"}
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["catalog_sha256"] = "0" * 64
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            request.write_text(
                json.dumps({"version": 1, "resolutions": [], "date_evidence": []}),
                encoding="utf-8",
            )
            with mock.patch.object(
                MODULE, "__file__", str(executable)
            ), mock.patch.object(
                MODULE, "canonical_runtime_root", return_value=executable.parent
            ), mock.patch.object(MODULE, "fetch_url") as fetch, mock.patch(
                "sys.stderr", new=io.StringIO()
            ):
                status = MODULE.main([
                    str(SCRIPT),
                    "--check-resolutions",
                    str(request),
                ])
            self.assertEqual(status, 75)
            fetch.assert_not_called()

    def test_check_resolutions_rejects_staging_verifier_copy_and_fake_runtime(
        self,
    ) -> None:
        """Do not let a copied verifier redefine the externally fixed runtime root."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, _catalog, _manifest, request = create_check_runtime(
                root, {source["name"]: "needs_search_fallback"}
            )
            copied_runtime = request.parent / "copied-runtime"
            copied_executable = copied_runtime / "collect-public-sources.py"
            copied_executable.parent.mkdir()
            copied_executable.write_bytes(executable.read_bytes())
            copied_catalog = copied_runtime / "it-news-sources.json"
            copied_catalog.write_text(
                json.dumps({
                    "version": 1,
                    "sources": [{
                        "name": source["name"],
                        "tier": 1,
                        "feed_url": None,
                        "page_url": "https://agent-controlled.example/news",
                    }],
                }),
                encoding="utf-8",
            )
            fake_run = (
                copied_runtime
                / "logs"
                / "2026-08-23"
                / "20260823T050000+0900-999-888"
            )
            fake_request = fake_run / "staging/source-resolutions.json"
            fake_manifest = fake_run / "source-inputs/source-manifest.json"
            fake_request.parent.mkdir(parents=True)
            fake_manifest.parent.mkdir()
            write_bound_manifest(
                fake_manifest,
                {source["name"]: "needs_search_fallback"},
                copied_catalog,
            )
            fake_request.write_text(
                json.dumps({"version": 1, "resolutions": [], "date_evidence": []}),
                encoding="utf-8",
            )
            with mock.patch.object(
                MODULE, "__file__", str(copied_executable)
            ), mock.patch.object(
                MODULE, "canonical_runtime_root", return_value=executable.parent
            ), mock.patch.object(MODULE, "fetch_url") as fetch, mock.patch(
                "sys.stderr", new=io.StringIO()
            ):
                status = MODULE.main([
                    str(copied_executable),
                    "--check-resolutions",
                    str(fake_request),
                ])
            self.assertEqual(status, 75)
            fetch.assert_not_called()

    def test_check_resolutions_rejects_intermediate_symlink_alias(self) -> None:
        """Require the lexical direct staging path, not a resolved alias of it."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, _catalog, _manifest, request = create_check_runtime(
                root, {source["name"]: "needs_search_fallback"}
            )
            request.write_text(
                json.dumps({"version": 1, "resolutions": [], "date_evidence": []}),
                encoding="utf-8",
            )
            alias = request.parent / "alias"
            alias.symlink_to(".", target_is_directory=True)
            with mock.patch.object(
                MODULE, "__file__", str(executable)
            ), mock.patch.object(
                MODULE, "canonical_runtime_root", return_value=executable.parent
            ), mock.patch.object(MODULE, "fetch_url") as fetch, mock.patch(
                "sys.stderr", new=io.StringIO()
            ):
                status = MODULE.main([
                    str(executable),
                    "--check-resolutions",
                    str(alias / request.name),
                ])
            self.assertEqual(status, 75)
            fetch.assert_not_called()

    def test_verify_resolutions_rejects_legacy_arbitrary_path_arguments(self) -> None:
        """Do not expose a second agent-callable catalog or manifest selector."""
        with mock.patch.object(MODULE, "fetch_url") as fetch, mock.patch(
            "sys.stderr", new=io.StringIO()
        ):
            status = MODULE.main([
                str(SCRIPT),
                "--verify-resolutions",
                "/alternate/catalog.json",
                "/alternate/manifest.json",
                "/alternate/request.json",
                "/alternate/output.json",
            ])
        self.assertEqual(status, 64)
        fetch.assert_not_called()

    def test_verify_resolutions_cli_uses_canonical_request_and_output(self) -> None:
        """Let only the runner's exact run-local evidence path receive output."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, _catalog, _manifest, request = create_check_runtime(
                root, {source["name"]: "needs_search_fallback"}
            )
            candidate = MODULE.urllib.parse.urljoin(
                source["page_url"], "/dated-article"
            )
            request.write_text(
                json.dumps({
                    "version": 1,
                    "resolutions": [{
                        "name": source["name"],
                        "method": "site_search",
                        "url": candidate,
                    }],
                    "date_evidence": [],
                }),
                encoding="utf-8",
            )
            output = request.parent.parent / "verified-source-resolutions.json"
            with mock.patch.object(
                MODULE, "__file__", str(executable)
            ), mock.patch.object(
                MODULE, "canonical_runtime_root", return_value=executable.parent
            ), mock.patch.object(MODULE, "fetch_url", return_value={
                "final_url": candidate,
                "http_status": 200,
                "content_type": "text/html",
                "content": (
                    b'<meta property="article:published_time" content="2026-08-22">'
                    b'<article><h2><a href="/dated-article">Dated</a></h2></article>'
                ),
            }):
                status = MODULE.main([
                    str(executable),
                    "--verify-resolutions",
                    str(request),
                    str(output),
                ])
            self.assertEqual(status, 0)
            self.assertTrue(output.is_file())

    def test_verify_resolutions_cli_rejects_noncanonical_output_before_fetch(
        self,
    ) -> None:
        """Prevent the collection agent from selecting a writable evidence target."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, _catalog, _manifest, request = create_check_runtime(
                root, {source["name"]: "needs_search_fallback"}
            )
            request.write_text(
                json.dumps({"version": 1, "resolutions": [], "date_evidence": []}),
                encoding="utf-8",
            )
            output = request.parent / "agent-selected-output.json"
            with mock.patch.object(
                MODULE, "__file__", str(executable)
            ), mock.patch.object(
                MODULE, "canonical_runtime_root", return_value=executable.parent
            ), mock.patch.object(MODULE, "fetch_url") as fetch, mock.patch(
                "sys.stderr", new=io.StringIO()
            ):
                status = MODULE.main([
                    str(executable),
                    "--verify-resolutions",
                    str(request),
                    str(output),
                ])
            self.assertEqual(status, 75)
            fetch.assert_not_called()
            self.assertFalse(output.exists())

    def test_fallback_source_cannot_also_submit_supplemental_date_evidence(
        self,
    ) -> None:
        """Keep a fallback row bound to its one independently sealed resolution."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            candidate = source["page_url"]
            write_bound_manifest(
                manifest, {source["name"]: "needs_search_fallback"}
            )
            request.write_text(
                json.dumps({
                    "version": 1,
                    "resolutions": [{
                        "name": source["name"],
                        "method": "site_search",
                        "url": candidate,
                    }],
                    "date_evidence": [{
                        "name": source["name"],
                        "url": MODULE.urllib.parse.urljoin(candidate, "/second"),
                    }],
                }),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
            ), mock.patch.object(MODULE, "fetch_url", return_value={
                "final_url": candidate,
                "http_status": 200,
                "content_type": "text/html",
                "content": (
                    b'<article><time datetime="2026-08-08"></time>'
                    b'<h2><a href="/article">Public article</a></h2></article>'
                ),
            }):
                with self.assertRaisesRegex(
                    MODULE.CollectionError, "outside catalog scope"
                ):
                    MODULE.verify_resolutions(CATALOG, manifest, request, output)
            self.assertFalse(output.exists())

    def test_fallback_rejects_partially_dated_article_candidates(self) -> None:
        """Require dates for every sealed article candidate while ignoring navigation."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            candidate = source["page_url"]
            write_bound_manifest(
                manifest, {source["name"]: "needs_search_fallback"}
            )
            request.write_text(json.dumps({"version": 1, "resolutions": [{
                "name": source["name"], "method": "site_search", "url": candidate
            }], "date_evidence": []}), encoding="utf-8")
            content = (
                b'<article><time datetime="2026-08-08"></time>'
                b'<h2><a href="/article">Public article</a></h2></article>'
                b'<article><h2><a href="/article-2">Undated article</a></h2></article>'
                b'<nav><a href="/about">About</a></nav>'
            )
            with mock.patch.dict(
                os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
            ), mock.patch.object(MODULE, "fetch_url", return_value={
                "final_url": candidate,
                "http_status": 200,
                "content_type": "text/html",
                "content": content,
            }):
                with self.assertRaisesRegex(MODULE.CollectionError, "complete"):
                    MODULE.verify_resolutions(CATALOG, manifest, request, output)

    def test_specific_article_fallback_seals_only_its_primary_date(self) -> None:
        """Ignore undated related cards when the requested page is itself an article."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            candidate = MODULE.urllib.parse.urljoin(source["page_url"], "/article")
            write_bound_manifest(
                manifest, {source["name"]: "needs_search_fallback"}
            )
            request.write_text(json.dumps({"version": 1, "resolutions": [{
                "name": source["name"], "method": "site_search", "url": candidate
            }], "date_evidence": []}), encoding="utf-8")
            content = (
                b'<meta property="article:published_time" content="2026-08-08">'
                b'<article><h2><a href="/related">Undated related story</a></h2></article>'
            )
            with mock.patch.dict(
                os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
            ), mock.patch.object(MODULE, "fetch_url", return_value={
                "final_url": candidate,
                "http_status": 200,
                "content_type": "text/html",
                "content": content,
            }):
                MODULE.verify_resolutions(CATALOG, manifest, request, output)
            verified = json.loads(output.read_text())["resolutions"][0]
            self.assertEqual(verified["candidate_entry_count"], 1)
            self.assertEqual(verified["candidate_evidence"][0]["url"], candidate)
            self.assertEqual(
                verified["candidate_evidence"][0]["provenance"], "html_meta"
            )
            self.assertEqual(verified["published_dates"], ["2026-08-08"])

    def test_html_meta_fallback_passes_the_release_validator(self) -> None:
        """Keep the trusted producer and downstream provenance contract aligned."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "catalog.json"
            source_manifest = root / "source-manifest.json"
            request = root / "request.json"
            verified = root / "verified.json"
            candidate = "https://example.test/article"
            source = {
                "name": "Fixture News",
                "tier": 1,
                "feed_url": None,
                "page_url": candidate,
            }
            catalog.write_text(
                json.dumps({"version": 1, "sources": [source]}),
                encoding="utf-8",
            )
            source_manifest.write_text(
                json.dumps({
                    "catalog_sha256": hashlib.sha256(
                        catalog.read_bytes()
                    ).hexdigest(),
                    "source_count": 1,
                    "fetched_count": 0,
                    "needs_search_fallback_count": 1,
                    "access_constraint_count": 0,
                    "sources": [{
                        "name": source["name"],
                        "tier": source["tier"],
                        "status": "needs_search_fallback",
                        "method": None,
                        "requested_url": None,
                        "final_url": None,
                        "content_file": None,
                        "extract_file": None,
                        "extracted_entry_count": 0,
                        "attempts": [{
                            "method": "public_page",
                            "url": candidate,
                            "status": "failed",
                            "reason": "fixture",
                        }],
                    }],
                }),
                encoding="utf-8",
            )
            request.write_text(
                json.dumps({
                    "version": 1,
                    "resolutions": [{
                        "name": source["name"],
                        "method": "site_search",
                        "url": candidate,
                    }],
                    "date_evidence": [],
                }),
                encoding="utf-8",
            )
            content = (
                b'<meta property="article:published_time" content="2026-08-22">'
                b'<nav><a href="/about">About</a></nav>'
            )
            with mock.patch.dict(
                os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
            ), mock.patch.object(MODULE, "fetch_url", return_value={
                "final_url": candidate,
                "http_status": 200,
                "content_type": "text/html",
                "content": content,
            }):
                MODULE.verify_resolutions(
                    catalog, source_manifest, request, verified
                )
            resolution = json.loads(
                verified.read_text(encoding="utf-8")
            )["resolutions"][0]
            self.assertEqual(
                resolution["candidate_evidence"][0]["provenance"],
                "html_meta",
            )
            summary = (
                "## 確認済みサイト一覧\n\n"
                "| サイト | Tier | 状態 | 取得方法 | 確認URL | 期間内件数 | 理由 |\n"
                "|---|---:|---|---|---|---:|---|\n"
                f"| {source['name']} | 1 | 取得済み | サイト限定検索 | "
                f"{candidate} | 1 | - |\n"
            )
            VALIDATOR.validate_source_coverage(
                summary,
                catalog,
                source_manifest,
                verified,
                date(2026, 8, 22),
            )
            payload = json.loads(verified.read_text(encoding="utf-8"))
            payload["resolutions"][0]["candidate_evidence"][0][
                "provenance"
            ] = "lookalike_meta"
            verified.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError,
                "candidate evidence is invalid",
            ):
                VALIDATOR.validate_source_coverage(
                    summary,
                    catalog,
                    source_manifest,
                    verified,
                    date(2026, 8, 22),
                )

    def test_primary_publication_meta_uses_structural_safe_subset(self) -> None:
        """Accept only unambiguous actual meta outside ignored HTML contexts."""
        base = "https://example.test/article"
        hosts = {"example.test"}
        positives = (
            '<meta property="article:published_time" content="2026-08-22">',
            '<META CONTENT=2026-08-22 NAME=datePublished>',
            '<meta data-purpose=date content="2026-08-22" '
            'property="ARTICLE:PUBLISHED_TIME">',
        )
        for markup in positives:
            with self.subTest(positive=markup):
                self.assertEqual(
                    MODULE.extract_primary_publication_evidence(
                        markup.encode(), base, hosts
                    ),
                    ("2026-08-22", "html_meta"),
                )

        meta = '<meta property="article:published_time" content="2026-08-22">'
        negatives = (
            f'<!--{meta}-->',
            f'<script type="text/plain">{meta}</script>',
            f'<style>{meta}</style>',
            f'<textarea>{meta}</textarea>',
            f'<title>{meta}</title>',
            f'<iframe>{meta}</iframe>',
            f'<noembed>{meta}</noembed>',
            f'<noframes>{meta}</noframes>',
            f'<noscript>{meta}</noscript>',
            f'<xmp>{meta}</xmp>',
            f'<plaintext>{meta}',
            f'<template>{meta}</template>',
            f'<svg>{meta}</svg>',
            f'<math>{meta}</math>',
            f'<select>{meta}</select>',
            f'<frameset>{meta}</frameset>',
            f'<frameset></frameset>{meta}',
            '<div data-note=\'<meta property="article:published_time" '
            'content="2026-08-22">\'></div>',
            '<meta data-property="article:published_time" '
            'data-content="2026-08-22">',
            '<meta notname="datePublished" data-content="2026-08-22">',
            '<meta property="article:published_time"\u00a0content="2026-08-22">',
            '<meta property="not-a-date" property="article:published_time" '
            'content="2026-08-22">',
            '<meta property="article:published_time" content="not-a-date" '
            'content="2026-08-22">',
            '<meta property="article:published_time" name="datePublished" '
            'content="2026-08-22">',
            '<meta property="article:published_time" '
            'content="2026-08-&#50;2">',
            '<meta property="article:published_time" content="2026-08-21">'
            '<meta name="datePublished" content="2026-08-22">',
        )
        for markup in negatives:
            with self.subTest(negative=markup):
                self.assertEqual(
                    MODULE.extract_primary_publication_evidence(
                        markup.encode(), base, hosts
                    ),
                    (None, None),
                )

    def test_select_shortcuts_cannot_expose_primary_meta(self) -> None:
        """Reject Python-only select self-close and malformed end callbacks."""
        base = "https://example.test/article"
        hosts = {"example.test"}
        meta = '<meta property="article:published_time" content="2026-08-22">'
        malformed = (
            f"<select/>{meta}",
            f"<select />{meta}",
            f"<select name=x />{meta}",
            f"<select></ select>{meta}</select>",
            *(
                f"<select></{separator}select>{meta}</select>"
                for separator in ("\t", "\n", "\f", "\r")
            ),
        )
        for markup in malformed:
            with self.subTest(markup=markup):
                self.assertEqual(
                    MODULE.extract_primary_publication_evidence(
                        markup.encode(), base, hosts
                    ),
                    (None, None),
                )
        self.assertEqual(
            MODULE.extract_primary_publication_evidence(
                f"<select></select>{meta}".encode(), base, hosts
            ),
            ("2026-08-22", "html_meta"),
        )

    def test_nonvoid_self_closing_flag_does_not_end_trust_suppression(self) -> None:
        """Treat XHTML-style flags on every non-void trust container as open."""
        base = "https://example.test/article"
        hosts = {"example.test"}
        meta = '<meta property="article:published_time" content="2026-08-22">'
        for tag in sorted(MODULE.JSON_LD_NONVOID_TRUST_CONTAINERS):
            with self.subTest(tag=tag):
                self.assertEqual(
                    MODULE.extract_primary_publication_evidence(
                        f"<{tag}/>{meta}".encode(), base, hosts
                    ),
                    (None, None),
                )
        self.assertEqual(
            MODULE.extract_primary_publication_evidence(
                f"<br/>{meta}".encode(), base, hosts
            ),
            ("2026-08-22", "html_meta"),
        )

    def test_untrusted_primary_metadata_cannot_seal_resolution_evidence(self) -> None:
        """Reject fake meta/frameset dates in fallback and supplemental paths."""
        source = MODULE.load_catalog(CATALOG)[0]
        candidate = MODULE.urllib.parse.urljoin(source["page_url"], "/article")
        json_ld = json.dumps({
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": "Ignored",
            "url": candidate,
            "datePublished": "2026-08-22",
        })
        fixtures = (
            b'<nav><a href="/about">About</a></nav><!--<meta '
            b'property="article:published_time" content="2026-08-22">-->',
            b'<nav><a href="/about">About</a></nav><meta '
            b'data-property="article:published_time" data-content="2026-08-22">',
            (
                '<nav><a href="/about">About</a></nav><frameset>'
                '<script type="application/ld+json">'
                f'{json_ld}</script></frameset>'
            ).encode(),
            (
                '<template><article><time datetime="2026-08-22"></time>'
                f'<h2><a href="{candidate}">Fake card</a></h2></article>'
                '</template>'
            ).encode(),
            (
                '<script type="text/plain">'
                f"{{'url':'{candidate}','title':'Fake JS',"
                "'date':'2026/08/22'}</script>"
            ).encode(),
            (
                '<select><script id="__NEXT_DATA__" type="application/json">'
                '{"metadata":{"datePublished":"2026-08-22"},'
                '"articleMetadata":{"title":"Fake Next"},'
                '"data":{"issueId":"daily","issueVolume":1,'
                '"issueNumber":2}}</script></select>'
            ).encode(),
        )
        for index, content in enumerate(fixtures):
            with self.subTest(path="fallback", index=index), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest = root / "manifest.json"
                request = root / "request.json"
                output = root / "verified.json"
                write_bound_manifest(
                    manifest, {source["name"]: "needs_search_fallback"}
                )
                request.write_text(json.dumps({
                    "version": 1,
                    "resolutions": [{
                        "name": source["name"],
                        "method": "site_search",
                        "url": candidate,
                    }],
                    "date_evidence": [],
                }), encoding="utf-8")
                with mock.patch.dict(
                    os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
                ), mock.patch.object(MODULE, "fetch_url", return_value={
                    "final_url": candidate,
                    "http_status": 200,
                    "content_type": "text/html",
                    "content": content,
                }), self.assertRaisesRegex(MODULE.CollectionError, "complete|empty"):
                    MODULE.verify_resolutions(CATALOG, manifest, request, output)

            with self.subTest(path="date_evidence", index=index), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest = root / "manifest.json"
                request = root / "request.json"
                output = root / "verified.json"
                write_bound_manifest(manifest, {source["name"]: "fetched"})
                request.write_text(json.dumps({
                    "version": 1,
                    "resolutions": [],
                    "date_evidence": [{
                        "name": source["name"], "url": candidate
                    }],
                }), encoding="utf-8")
                with mock.patch.dict(
                    os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
                ), mock.patch.object(MODULE, "fetch_url", return_value={
                    "final_url": candidate,
                    "http_status": 200,
                    "content_type": "text/html",
                    "content": content,
                }), self.assertRaisesRegex(MODULE.CollectionError, "lacks"):
                    MODULE.verify_resolutions(CATALOG, manifest, request, output)

    def test_generic_page_date_does_not_collapse_listing_candidates(self) -> None:
        """Do not treat generic category metadata as the listing's publication date."""
        content = (
            b'<meta name="date" content="2026-08-08">'
            b'<article><h2><a href="/undated">Undated story</a></h2></article>'
        )
        self.assertIsNone(
            MODULE.extract_primary_publication_date(
                content, "https://example.test/news"
            )
        )

    def test_json_ld_seals_undated_article_and_merges_same_url_date(self) -> None:
        """Keep undated article-like JSON-LD and merge matching card evidence."""
        base = "https://example.test/"
        undated = b'''<script type="application/ld+json">{"@context":"https://schema.org","@type":"NewsArticle","headline":"A","url":"/a"}</script>'''
        extracted = MODULE.extract_content(undated, "text/html", base)
        self.assertEqual(extracted["entries"][0]["candidate_provenance"], "json_ld")
        self.assertIsNone(extracted["entries"][0]["published"])

        merged = b'''<script type="application/ld+json">{"@context":"https://schema.org","@type":"NewsArticle","headline":"A","url":"https://example.test:443/a/"}</script><article><time datetime="2026-08-08"></time><h2><a href="/a">A</a></h2></article>'''
        extracted = MODULE.extract_content(merged, "text/html", base)
        self.assertEqual(len(extracted["entries"]), 1)
        self.assertEqual(extracted["entries"][0]["published"], "2026-08-08")

        distinct_queries = b'''<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"NewsArticle","headline":"A1","url":"/a?id=1","datePublished":"2026-08-08"},{"@type":"NewsArticle","headline":"A2","url":"/a?id=2","datePublished":"2026-08-08"}]}</script>'''
        extracted = MODULE.extract_content(distinct_queries, "text/html", base)
        self.assertEqual(len(extracted["entries"]), 2)

    def test_article_without_headline_is_candidate_only_when_unambiguous(self) -> None:
        """Do not guess that an author/tag link is the article permalink."""
        base = "https://example.test/"
        ambiguous = (
            b'<article><time datetime="2026-08-08"></time>'
            b'<a href="/author">Alice</a><a href="/story">Story</a></article>'
        )
        extracted = MODULE.extract_content(ambiguous, "text/html", base)
        self.assertFalse(any(entry.get("candidate_provenance") for entry in extracted["entries"]))

        single = (
            b'<article><time datetime="2026-08-08"></time>'
            b'<a href="/story">Story</a></article>'
        )
        extracted = MODULE.extract_content(single, "text/html", base)
        self.assertEqual(extracted["entries"][0]["candidate_provenance"], "article")

        preseen = (
            b'<nav><a href="/story">Navigation label</a></nav>'
            b'<article><time datetime="2026-08-08"></time>'
            b'<a href="/story">Article-local title</a></article>'
        )
        extracted = MODULE.extract_content(preseen, "text/html", base)
        self.assertEqual(extracted["candidate_entry_count"], 1)
        self.assertEqual(extracted["entries"][0]["title"], "Article-local title")

        repeated_controls = (
            b'<article><time datetime="2026-08-08"></time>'
            b'<a href="/story">Copy link</a>'
            b'<a href="/story">Share link</a></article>'
        )
        extracted = MODULE.extract_content(repeated_controls, "text/html", base)
        self.assertEqual(extracted["candidate_entry_count"], 0)
        self.assertEqual(extracted["date_evidence_count"], 0)

    def test_detects_explicit_login_gate_but_not_generic_forbidden_text(self) -> None:
        """Resolve login exceptions from gate markup, not a bare status/error word."""
        login = b'<form><input type="password"><button>Sign in</button></form>'
        self.assertEqual(MODULE.detect_access_constraint(login), "login")
        self.assertIsNone(MODULE.detect_access_constraint(b"HTTP 403 forbidden"))

    def test_rss_gate_falls_back_to_public_page(self) -> None:
        """Do not classify a whole publisher from a constrained feed endpoint."""
        source = MODULE.load_catalog(CATALOG)[0]
        gate = b'<form><input type="password"><button>Sign in</button></form>'
        page = b'<html><article><a href="/article">Public article</a><time datetime="2026-08-10">Today</time></article></html>'
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", side_effect=[
                {"content": gate, "content_type": "text/html", "final_url": source["feed_url"], "http_status": 200},
                {"content": page, "content_type": "text/html", "final_url": source["page_url"], "http_status": 200},
            ]
        ):
            result = MODULE.collect_source(1, source, MODULE.source_hosts(source), Path(temporary))
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["method"], "public_page")

    def test_direct_html_requires_dates_for_every_article_candidate(self) -> None:
        """Send partially dated listings to fallback instead of sealing a subset."""
        source = MODULE.load_catalog(CATALOG)[0]
        page = (
            b'<article><h2><a href="/dated">Dated</a></h2>'
            b'<time datetime="2026-08-10"></time></article>'
            b'<article><h2><a href="/undated">Undated</a></h2></article>'
        )
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE,
            "fetch_url",
            side_effect=[
                MODULE.CollectionError("feed unavailable"),
                {
                    "content": page,
                    "content_type": "text/html",
                    "final_url": source["page_url"],
                    "http_status": 200,
                },
            ],
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "needs_search_fallback")
        self.assertIn("lacks_publication", result["attempts"][-1]["reason"])

    def test_mixed_constraint_and_failure_requires_fallback(self) -> None:
        """Do not close a publisher when another direct endpoint failed generically."""
        source = MODULE.load_catalog(CATALOG)[0]
        gate = b'<form><input type="password"><button>Sign in</button></form>'
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE,
            "fetch_url",
            side_effect=[
                {"content": gate, "content_type": "text/html", "final_url": source["feed_url"], "http_status": 403},
                MODULE.CollectionError("transient failure"),
            ],
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "needs_search_fallback")

    def test_verified_http_429_captcha_ignores_link_count_heuristic(self) -> None:
        """Seal a verified 429 gate even when its page exposes many links."""
        source = MODULE.load_catalog(CATALOG)[0]
        links = b"".join(
            (
                f"<a href='{source['page_url']}checkpoint-{index}'>"
                f"Checkpoint link {index}</a>"
            ).encode()
            for index in range(12)
        )
        body = (
            b"<html><head><title>Vercel Security Checkpoint</title></head>"
            b"<body>" + links + b"</body></html>"
        )
        extracted = MODULE.extract_content(
            body, "text/html", source["page_url"], MODULE.source_hosts(source)
        )
        self.assertGreaterEqual(extracted["entry_count"], 10)
        fetched = {
            "content": body,
            "content_type": "text/html",
            "final_url": source["page_url"],
            "http_status": 429,
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", return_value=fetched
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "access_constraint")
        self.assertEqual(result["constraint"], "captcha")
        self.assertTrue(
            all(item["status"] == "access_constraint" for item in result["attempts"])
        )

    def test_verified_http_429_captcha_bypasses_article_extraction(self) -> None:
        """Do not send a bounded gate body through higher-memory article parsers."""
        source = MODULE.load_catalog(CATALOG)[0]
        body = (
            b"<html><head><title>Vercel Security Checkpoint</title></head><body>"
            + b"\n" * 10000
            + b"</body></html>"
        )
        fetched = {
            "content": body,
            "content_type": "text/html",
            "final_url": source["page_url"],
            "http_status": 429,
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", return_value=fetched
        ), mock.patch.object(
            MODULE,
            "extract_content",
            side_effect=AssertionError("verified 429 gate reached article extraction"),
        ) as extract:
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )

        extract.assert_not_called()
        self.assertEqual(result["status"], "access_constraint")
        self.assertEqual(result["constraint"], "captcha")

    def test_resolution_verifier_preserves_verified_http_429_captcha(self) -> None:
        """Keep a verified gate without link heuristics or article extraction."""
        source = MODULE.load_catalog(CATALOG)[0]
        links = b"".join(
            f"<a href='/checkpoint-{index}'>link</a>".encode()
            for index in range(12)
        )
        body = (
            b"<html><head><title>Vercel Security Checkpoint</title></head><body>"
            + links
            + b"</body></html>"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            write_bound_manifest(
                manifest, {source["name"]: "needs_search_fallback"}
            )
            request.write_text(
                json.dumps({
                    "version": 1,
                    "resolutions": [{
                        "name": source["name"],
                        "method": "access_constraint",
                        "url": source["page_url"],
                        "constraint": "captcha",
                    }],
                    "date_evidence": [],
                }),
                encoding="utf-8",
            )
            fetched = {
                "content": body,
                "content_type": "text/html",
                "final_url": source["page_url"],
                "http_status": 429,
            }
            with mock.patch.dict(
                os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
            ), mock.patch.object(
                MODULE, "fetch_url", return_value=fetched
            ), mock.patch.object(
                MODULE,
                "extract_content",
                side_effect=AssertionError("verified gate reached article extraction"),
            ) as extract, mock.patch.object(
                MODULE,
                "extract_primary_publication_evidence",
                side_effect=AssertionError("verified gate reached date extraction"),
            ) as primary:
                MODULE.verify_resolutions(CATALOG, manifest, request, output)

            extract.assert_not_called()
            primary.assert_not_called()
            verified = json.loads(output.read_text())["resolutions"][0]
            self.assertEqual(verified["status"], "verified_access_constraint")
            self.assertEqual(verified["constraint"], "captcha")
            self.assertEqual(verified["extracted_entry_count"], 0)
            self.assertEqual(verified["candidate_entry_count"], 0)

    def test_verified_robots_constraint_requires_all_direct_endpoints(self) -> None:
        """Seal robots evidence only when every direct endpoint is disallowed."""
        source = MODULE.load_catalog(CATALOG)[0]
        digest = "a" * 64
        errors = [
            MODULE.RobotsDisallowed(source["feed_url"], "https://techcrunch.com/robots.txt", digest),
            MODULE.RobotsDisallowed(source["page_url"], "https://techcrunch.com/robots.txt", digest),
        ]
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", side_effect=errors
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "access_constraint")
        self.assertEqual(result["constraint"], "robots")
        self.assertEqual(result["robots_sha256"], digest)
        self.assertTrue(all(item["constraint"] == "robots" for item in result["attempts"]))
        self.assertTrue(all(item["robots_sha256"] == digest for item in result["attempts"]))

    def test_date_evidence_rejects_redirect_to_another_article(self) -> None:
        """Do not assign a redirect target's date to the requested sealed article."""
        source = MODULE.load_catalog(CATALOG)[0]
        requested = source["page_url"] + "article-a"
        redirected = source["page_url"] + "article-b"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            write_bound_manifest(manifest, {source["name"]: "fetched"})
            request.write_text(json.dumps({
                "version": 1,
                "resolutions": [],
                "date_evidence": [{"name": source["name"], "url": requested}],
            }), encoding="utf-8")
            with mock.patch.dict(
                os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
            ), mock.patch.object(MODULE, "fetch_url", return_value={
                "final_url": redirected,
                "http_status": 200,
                "content_type": "text/html",
                "content": b'<meta property="article:published_time" content="2026-08-08">',
            }):
                with self.assertRaisesRegex(MODULE.CollectionError, "redirected"):
                    MODULE.verify_resolutions(CATALOG, manifest, request, output)

    def test_empty_direct_extract_requires_fallback(self) -> None:
        """Treat unparseable direct pages as unresolved instead of article-free."""
        source = MODULE.load_catalog(CATALOG)[0]
        empty = {
            "content": b"<html></html>",
            "content_type": "text/html",
            "final_url": source["page_url"],
            "http_status": 200,
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", side_effect=[empty, empty]
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "needs_search_fallback")
        self.assertTrue(all(item["reason"] == "empty_extract" for item in result["attempts"]))

    def test_resolution_request_rejects_symlink(self) -> None:
        """Do not follow a model-created symlink in the trusted verification phase."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            target = root / "target.json"
            request = root / "request.json"
            output = root / "verified.json"
            write_bound_manifest(
                manifest, {source["name"]: "needs_search_fallback"}
            )
            target.write_text(json.dumps({"version": 1, "resolutions": [], "date_evidence": []}))
            request.symlink_to(target)
            with mock.patch.dict(os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}), self.assertRaises((MODULE.CollectionError, OSError)):
                MODULE.verify_resolutions(CATALOG, manifest, request, output)

    def test_verifies_date_evidence_for_directly_fetched_source(self) -> None:
        """Allow a fetched HTML source to supplement missing dates through trusted fetch."""
        source = MODULE.load_catalog(CATALOG)[0]
        article = source["page_url"]
        html = (
            '<script type="application/ld+json">'
            f'{{"@context":"https://schema.org","@type":"NewsArticle",'
            f'"headline":"Story","url":"{article}",'
            '"datePublished":"2026-08-05"}'
            '</script>'
        ).encode()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            write_bound_manifest(manifest, {source["name"]: "fetched"})
            request.write_text(json.dumps({
                "version": 1, "resolutions": [],
                "date_evidence": [{"name": source["name"], "url": article}],
            }))
            with mock.patch.dict(os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}), mock.patch.object(
                MODULE, "fetch_url", return_value={
                    "final_url": article, "http_status": 200,
                    "content_type": "text/html", "content": html,
                },
            ):
                MODULE.verify_resolutions(CATALOG, manifest, request, output)
            verified = json.loads(output.read_text())["date_evidence"][0]
            self.assertEqual(verified["published_date"], "2026-08-05")

    def test_article_channels_reject_ambiguous_raw_attributes(self) -> None:
        """Never trust HTMLParser-normalized duplicate or non-HTML5 attributes."""
        malformed_anchors = (
            b'<a href="/wrong" href="/duplicate">Duplicate href</a>',
            b'<a\x0bhref="/vertical-tab">Vertical tab href</a>',
            b'<a href="&#x2f;entity">Entity-built href</a>',
        )
        for malformed in malformed_anchors:
            with self.subTest(malformed=malformed):
                extracted = MODULE.extract_content(
                    b'<article><time datetime="2026-08-22"></time>'
                    + malformed
                    + b'<h2><a href="/valid">Valid peer</a></h2></article>',
                    "text/html",
                    "https://example.test/news",
                )
                candidates = [
                    entry
                    for entry in extracted["entries"]
                    if entry.get("candidate_provenance") == "article"
                ]
                self.assertEqual(
                    [entry["url"] for entry in candidates],
                    ["https://example.test/valid"],
                )
                self.assertEqual(candidates[0]["published"], "2026-08-22")

        legacy = MODULE.extract_content(
            b'<p class="title" class="date"><a href="/ambiguous">Bad</a></p>'
            b'<p class="date">(2026/8/21)</p>'
            b'<p class="title"><a href="/legacy-valid">Valid</a></p>'
            b'<p class="date">(2026/8/22)</p>',
            "text/html",
            "https://example.test/news",
        )
        embedded = [
            entry
            for entry in legacy["entries"]
            if entry.get("candidate_provenance") == "article"
        ]
        self.assertEqual(
            [entry["url"] for entry in embedded],
            ["https://example.test/legacy-valid"],
        )

    def test_suppression_container_control_separator_invalidates_document(self) -> None:
        """Reject callback tags whose raw container name used a control separator."""
        extracted = MODULE.extract_content(
            b'<select\x0b><meta property="article:published_time" '
            b'content="2026-08-22"></select>'
            b'<article><time datetime="2026-08-22"></time>'
            b'<h2><a href="/must-not-survive">Invalid peer</a></h2></article>',
            "text/html",
            "https://example.test/news",
        )
        self.assertEqual(extracted["entries"], [])


if __name__ == "__main__":
    unittest.main()
