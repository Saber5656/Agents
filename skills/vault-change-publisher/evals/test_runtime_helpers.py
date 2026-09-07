#!/usr/bin/env python3
"""Integration tests for runtime context, collection validation, and artifact install."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Optional
from unittest import mock

SKILL_ROOT = Path(__file__).parents[1]
REPO_ROOT = SKILL_ROOT.parent
SOURCE_CATALOG = REPO_ROOT / "summarize-it-news" / "references" / "it-news-sources.json"
SCRIPTS = SKILL_ROOT / "scripts"
RUNTIME_RELEASE_VERIFIER = SCRIPTS / "verify-runtime-release.py"
EXPECTED_RUNTIME_RELEASE_FILES = frozenset(
    {
        "atomic_file_ops.py",
        "automation-result.schema.json",
        "capture-vault-state.py",
        "collect-public-sources.py",
        "collection-result.schema.json",
        "commit-push-publication-evidence.py",
        "commit-reviewed-publication.py",
        "daily-it-news.collect.prompt.md",
        "daily-it-news.evidence-review.prompt.md",
        "daily-it-news.review.prompt.md",
        "determine-publication-modes.py",
        "evidence-review-result.schema.json",
        "evidence_hunk.py",
        "fetch-vault-main.py",
        "git_diff_digest.py",
        "gitleaks-default.toml",
        "install-verified-artifacts.py",
        "interpret-automation-result.sh",
        "isolated_git_transport.py",
        "it-news-sources.json",
        "prepare-codex-output-schema.py",
        "prepare-publication-evidence.py",
        "prepare-publication-review-context.py",
        "publication-commit-result.schema.json",
        "publication-review-result.schema.json",
        "push-committed-heads.py",
        "resolve-runtime-context.py",
        "run-daily-it-news-vulnerability-check.sh",
        "run-pinned-review.py",
        "send-it-news-discord-notification.py",
        "stage-dirty-review-inputs.py",
        "stage-standing-task.py",
        "trusted_gitleaks.py",
        "validate-canonical-result.py",
        "validate-collection-result.py",
        "validate-publication-review.py",
        "verify-runtime-release.py",
    }
)
sys.path.insert(0, str(SCRIPTS))
PUSH_SPEC = importlib.util.spec_from_file_location(
    "push_committed_heads", SCRIPTS / "push-committed-heads.py"
)
assert PUSH_SPEC and PUSH_SPEC.loader
PUSH_MODULE = importlib.util.module_from_spec(PUSH_SPEC)
PUSH_SPEC.loader.exec_module(PUSH_MODULE)
REVIEW_SPEC = importlib.util.spec_from_file_location(
    "validate_publication_review", SCRIPTS / "validate-publication-review.py"
)
assert REVIEW_SPEC and REVIEW_SPEC.loader
REVIEW_MODULE = importlib.util.module_from_spec(REVIEW_SPEC)
REVIEW_SPEC.loader.exec_module(REVIEW_MODULE)
FINALIZER_SPEC = importlib.util.spec_from_file_location(
    "commit_push_publication_evidence",
    SCRIPTS / "commit-push-publication-evidence.py",
)
assert FINALIZER_SPEC and FINALIZER_SPEC.loader
FINALIZER_MODULE = importlib.util.module_from_spec(FINALIZER_SPEC)
FINALIZER_SPEC.loader.exec_module(FINALIZER_MODULE)
FETCH_SPEC = importlib.util.spec_from_file_location(
    "fetch_vault_main", SCRIPTS / "fetch-vault-main.py"
)
assert FETCH_SPEC and FETCH_SPEC.loader
FETCH_MODULE = importlib.util.module_from_spec(FETCH_SPEC)
FETCH_SPEC.loader.exec_module(FETCH_MODULE)
SCHEMA_SPEC = importlib.util.spec_from_file_location(
    "prepare_codex_output_schema", SCRIPTS / "prepare-codex-output-schema.py"
)
assert SCHEMA_SPEC and SCHEMA_SPEC.loader
SCHEMA_MODULE = importlib.util.module_from_spec(SCHEMA_SPEC)
SCHEMA_SPEC.loader.exec_module(SCHEMA_MODULE)
CANONICAL_SPEC = importlib.util.spec_from_file_location(
    "validate_canonical_result", SCRIPTS / "validate-canonical-result.py"
)
assert CANONICAL_SPEC and CANONICAL_SPEC.loader
CANONICAL_MODULE = importlib.util.module_from_spec(CANONICAL_SPEC)
CANONICAL_SPEC.loader.exec_module(CANONICAL_MODULE)
DIRTY_STAGER_SPEC = importlib.util.spec_from_file_location(
    "stage_dirty_review_inputs", SCRIPTS / "stage-dirty-review-inputs.py"
)
assert DIRTY_STAGER_SPEC and DIRTY_STAGER_SPEC.loader
DIRTY_STAGER_MODULE = importlib.util.module_from_spec(DIRTY_STAGER_SPEC)
DIRTY_STAGER_SPEC.loader.exec_module(DIRTY_STAGER_MODULE)
COMMITTER_SPEC = importlib.util.spec_from_file_location(
    "commit_reviewed_publication", SCRIPTS / "commit-reviewed-publication.py"
)
assert COMMITTER_SPEC and COMMITTER_SPEC.loader
COMMITTER_MODULE = importlib.util.module_from_spec(COMMITTER_SPEC)
COMMITTER_SPEC.loader.exec_module(COMMITTER_MODULE)
INSTALLER_SPEC = importlib.util.spec_from_file_location(
    "install_verified_artifacts", SCRIPTS / "install-verified-artifacts.py"
)
assert INSTALLER_SPEC and INSTALLER_SPEC.loader
INSTALLER_MODULE = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(INSTALLER_MODULE)
EVIDENCE_SPEC = importlib.util.spec_from_file_location(
    "prepare_publication_evidence", SCRIPTS / "prepare-publication-evidence.py"
)
assert EVIDENCE_SPEC and EVIDENCE_SPEC.loader
EVIDENCE_MODULE = importlib.util.module_from_spec(EVIDENCE_SPEC)
EVIDENCE_SPEC.loader.exec_module(EVIDENCE_MODULE)
NOTIFICATION_SPEC = importlib.util.spec_from_file_location(
    "send_it_news_discord_notification",
    SCRIPTS / "send-it-news-discord-notification.py",
)
assert NOTIFICATION_SPEC and NOTIFICATION_SPEC.loader
NOTIFICATION_MODULE = importlib.util.module_from_spec(NOTIFICATION_SPEC)
sys.modules[NOTIFICATION_SPEC.name] = NOTIFICATION_MODULE
NOTIFICATION_SPEC.loader.exec_module(NOTIFICATION_MODULE)
CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "capture_vault_state", SCRIPTS / "capture-vault-state.py"
)
assert CAPTURE_SPEC and CAPTURE_SPEC.loader
CAPTURE_MODULE = importlib.util.module_from_spec(CAPTURE_SPEC)
CAPTURE_SPEC.loader.exec_module(CAPTURE_MODULE)
MODE_SPEC = importlib.util.spec_from_file_location(
    "determine_publication_modes", SCRIPTS / "determine-publication-modes.py"
)
assert MODE_SPEC and MODE_SPEC.loader
MODE_MODULE = importlib.util.module_from_spec(MODE_SPEC)
MODE_SPEC.loader.exec_module(MODE_MODULE)
REVIEW_CONTEXT_SPEC = importlib.util.spec_from_file_location(
    "prepare_publication_review_context",
    SCRIPTS / "prepare-publication-review-context.py",
)
assert REVIEW_CONTEXT_SPEC and REVIEW_CONTEXT_SPEC.loader
REVIEW_CONTEXT_MODULE = importlib.util.module_from_spec(REVIEW_CONTEXT_SPEC)
REVIEW_CONTEXT_SPEC.loader.exec_module(REVIEW_CONTEXT_MODULE)
COLLECTION_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_collection_result", SCRIPTS / "validate-collection-result.py"
)
assert COLLECTION_VALIDATOR_SPEC and COLLECTION_VALIDATOR_SPEC.loader
COLLECTION_VALIDATOR_MODULE = importlib.util.module_from_spec(COLLECTION_VALIDATOR_SPEC)
COLLECTION_VALIDATOR_SPEC.loader.exec_module(COLLECTION_VALIDATOR_MODULE)
RESOLVER_SPEC = importlib.util.spec_from_file_location(
    "resolve_runtime_context", SCRIPTS / "resolve-runtime-context.py"
)
assert RESOLVER_SPEC and RESOLVER_SPEC.loader
RESOLVER_MODULE = importlib.util.module_from_spec(RESOLVER_SPEC)
RESOLVER_SPEC.loader.exec_module(RESOLVER_MODULE)
STANDING_STAGER_SPEC = importlib.util.spec_from_file_location(
    "stage_standing_task", SCRIPTS / "stage-standing-task.py"
)
assert STANDING_STAGER_SPEC and STANDING_STAGER_SPEC.loader
STANDING_STAGER_MODULE = importlib.util.module_from_spec(STANDING_STAGER_SPEC)
STANDING_STAGER_SPEC.loader.exec_module(STANDING_STAGER_MODULE)
import isolated_git_transport as TRANSPORT_MODULE
import git_diff_digest as DIFF_MODULE
import trusted_gitleaks as TRUSTED_GITLEAKS_MODULE
import atomic_file_ops as ATOMIC_FILE_OPS_MODULE


def source_coverage_markdown() -> str:
    """Build a valid complete source-audit table from the tracked catalog."""
    catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
    lines = [
        "## 確認済みサイト一覧",
        "",
        "| サイト | Tier | 状態 | 取得方法 | 確認URL | 期間内件数 | 理由 |",
        "|---|---:|---|---|---|---:|---|",
    ]
    for index, source in enumerate(catalog["sources"]):
        url = source["feed_url"] or source["page_url"]
        method = "RSS" if source["feed_url"] else "公開ページ"
        if index == 0:
            status, count, reason = "取得済み", 1, "fixture記事確認"
        elif index == len(catalog["sources"]) - 1:
            status, count, reason = "アクセス制約", 0, "robotsで取得禁止"
        else:
            status, count, reason = "対象期間記事なし", 0, "fixture確認"
        lines.append(
            f"| {source['name']} | {source['tier']} | {status} | "
            f"{method} | {url} | {count} | {reason} |"
        )
    return "\n".join(lines) + "\n"


def create_empty_base(repo: Path) -> str:
    """Create one valid HEAD for residual-guard fixtures."""
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.invalid",
            "commit", "-q", "--allow-empty", "-m", "base",
        ],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


def write_source_manifest(root: Path) -> Path:
    """Create deterministic collector evidence matching the positive fixture."""
    catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
    source_root = root / "source-inputs"
    source_root.mkdir()
    sources = []
    for index, source in enumerate(catalog["sources"]):
        url = source["feed_url"] or source["page_url"]
        method = "rss" if source["feed_url"] else "public_page"
        if index == len(catalog["sources"]) - 1:
            sources.append({
                "name": source["name"], "tier": source["tier"],
                "status": "access_constraint",
                "method": method,
                "requested_url": url,
                "final_url": url,
                "constraint": "robots",
                "http_status": None,
                "robots_url": f"https://{url.split('/')[2]}/robots.txt",
                "robots_sha256": "a" * 64,
                "attempts": [
                    {
                        "method": attempt_method,
                        "url": attempt_url,
                        "status": "access_constraint",
                        "reason": "robots_disallowed",
                        "requested_url": attempt_url,
                        "final_url": attempt_url,
                        "constraint": "robots",
                        "http_status": None,
                        "robots_url": f"https://{attempt_url.split('/')[2]}/robots.txt",
                        "robots_sha256": "a" * 64,
                    }
                    for attempt_method, attempt_url in (
                        ("rss", source["feed_url"]),
                        ("public_page", source["page_url"]),
                    )
                    if attempt_url
                ],
            })
        else:
            extract_file = f"source-{index}.extract.json"
            published = "2026-07-31T00:00:00Z" if index == 0 else "2026-07-01T00:00:00Z"
            (source_root / extract_file).write_text(
                json.dumps({
                    "format": "feed" if method == "rss" else "html_links",
                    "entries": [{"url": f"{url}?fixture={index}", "published": published}],
                }),
                encoding="utf-8",
            )
            evidence = {
                "name": source["name"], "tier": source["tier"], "status": "fetched",
                "method": method, "final_url": url, "attempts": [],
                "extract_file": extract_file, "extracted_entry_count": 1,
                "jst_window_start": "2026-07-25",
                "jst_window_end": "2026-07-31",
                "jst_window_item_count": 1 if index == 0 else 0,
            }
            sources.append(evidence)
    path = source_root / "source-manifest.json"
    path.write_text(json.dumps({
        "catalog_sha256": hashlib.sha256(SOURCE_CATALOG.read_bytes()).hexdigest(),
        "sources": sources,
    }), encoding="utf-8")
    return path


def write_verified_resolutions(root: Path) -> Path:
    """Create an empty verified fallback set for the robots-only fixture."""
    path = root / "verified-source-resolutions.json"
    path.write_text(json.dumps({"version": 1, "resolutions": [], "date_evidence": []}), encoding="utf-8")
    return path


def write_minimal_coverage_fixture(
    root: Path,
    *,
    extract_entries: list[dict[str, object]],
    evidence_updates: Optional[dict[str, object]] = None,
    resolutions: Optional[list[dict[str, object]]] = None,
    date_evidence: Optional[list[dict[str, object]]] = None,
    status: str = "取得済み",
    method: str = "RSS",
    count: int = 1,
    confirmed_url: Optional[str] = None,
    reason: str = "fixture",
    run_date: date = date(2026, 7, 31),
) -> tuple[str, Path, Path, Path]:
    """Create one-source sealed coverage evidence for focused validator tests."""
    catalog = root / "catalog.json"
    manifest = root / "source-manifest.json"
    verified = root / "verified.json"
    extract = root / "source.extract.json"
    source_url = "https://example.test/feed"
    catalog.write_text(
        json.dumps({
            "version": 1,
            "sources": [{
                "name": "Fixture News",
                "tier": 1,
                "feed_url": source_url,
                "page_url": "https://example.test/news",
            }],
        }),
        encoding="utf-8",
    )
    extract.write_text(
        json.dumps({
            "format": "feed" if method == "RSS" else "html_links",
            "entries": extract_entries,
        }),
        encoding="utf-8",
    )
    evidence: dict[str, object] = {
        "name": "Fixture News",
        "tier": 1,
        "status": "fetched",
        "method": "rss",
        "final_url": source_url,
        "extract_file": extract.name,
        "extracted_entry_count": len(extract_entries),
        "attempts": [],
        "jst_window_start": (run_date - timedelta(days=6)).isoformat(),
        "jst_window_end": run_date.isoformat(),
        "jst_window_item_count": sum(
            run_date - timedelta(days=6) <= published <= run_date
            for published in (
                COLLECTION_VALIDATOR_MODULE.parse_publication_date(item.get("published"))
                for item in extract_entries
            )
            if published is not None
        ),
    }
    evidence.update(evidence_updates or {})
    manifest.write_text(
        json.dumps({
            "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
            "sources": [evidence],
        }),
        encoding="utf-8",
    )
    verified.write_text(
        json.dumps({
            "version": 1,
            "resolutions": resolutions or [],
            "date_evidence": date_evidence or [],
        }),
        encoding="utf-8",
    )
    summary = (
        "## 確認済みサイト一覧\n\n"
        "| サイト | Tier | 状態 | 取得方法 | 確認URL | 期間内件数 | 理由 |\n"
        "|---|---:|---|---|---|---:|---|\n"
        f"| Fixture News | 1 | {status} | {method} | {confirmed_url or source_url} | {count} | {reason} |\n"
    )
    return summary, catalog, manifest, verified


class RuntimeHelperTests(unittest.TestCase):
    """Exercise helper boundaries without machine-specific paths."""

    def setUp(self) -> None:
        """Create isolated fake catalog, Vaults, workdir, and staging."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.saihai = self.root / "saihai"
        self.agents = self.root / "agents"
        self.user = self.root / "user"
        self.skills = self.root / "skills"
        self.workdir = self.root / "automation"
        for directory in (
            self.saihai,
            self.agents,
            self.user,
            self.skills,
            self.workdir,
        ):
            directory.mkdir()
        for repo in (self.agents, self.user):
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
        self.origins = {}
        for repo, key in ((self.agents, "agents"), (self.user, "user")):
            origin = self.root / f"{key}-origin.git"
            subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "remote", "add", "origin", str(origin)],
                check=True,
            )
            self.origins[key] = origin
        module = f'''
def load_environment(*, checkout_root, environ, require_catalog):
    environ.update({{
        "AGENTS_VAULT_ROOT": {str(self.agents)!r},
        "USER_VAULT_ROOT": {str(self.user)!r},
        "SKILLS_REPO_ROOT": {str(self.skills)!r},
    }})
    return {{"status": "loaded"}}
'''
        (self.saihai / "directory_paths.py").write_text(module, encoding="utf-8")
        self.config = self.workdir / "automation.local.env"
        (self.agents / "tasks").mkdir()
        (self.agents / "tasks" / "standing.md").write_text(
            "# Standing\n\n### Vault Publication Evidence\n\n"
            "| Run ID | Push status |\n|---|---|\n\n## Reviews\n\nstanding\n",
            encoding="utf-8",
        )
        authorization = self.agents / "tasks" / "auth.md"
        authorization.write_text("approved\n", encoding="utf-8")
        authorization_hash = hashlib.sha256(authorization.read_bytes()).hexdigest()
        self.fake_gitleaks = self.workdir / "fake-gitleaks"
        self.fake_gitleaks.write_text(
            "#!/bin/sh\n[ \"$1\" = version ] && echo 'fixture-gitleaks 8.30.1'\nexit 0\n",
            encoding="utf-8",
        )
        self.fake_gitleaks.chmod(0o755)
        self.fake_hermes = self.workdir / "fake-hermes"
        self.fake_hermes.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' "
            "'{\"success\":true,\"platform\":\"discord\","
            "\"chat_id\":\"1234567890123456789\",\"message_id\":\"fixture-message\"}'\n",
            encoding="utf-8",
        )
        self.fake_hermes.chmod(0o755)
        self.config.write_text(
            "\n".join(
                (
                    f"SAIHAI_CHECKOUT_ROOT={self.saihai}",
                    "CODEX_BIN=/usr/bin/true",
                    f"GITLEAKS_BIN={self.fake_gitleaks}",
                    f"HERMES_BIN={self.fake_hermes}",
                    "DISCORD_NEWS_TARGET=discord:1234567890123456789",
                    "IT_NEWS_ARCHIVE_RELATIVE=10_Prompt",
                    "ADVISORY_ARCHIVE_RELATIVE=03-Contexts/Reports/Security",
                    "STANDING_TASK_ID=TSK-STANDING",
                    "STANDING_TASK_RELATIVE=tasks/standing.md",
                    "AUTHORIZATION_TASK_ID=TSK-AUTH",
                    "AUTHORIZATION_TASK_RELATIVE=tasks/auth.md",
                    f"AUTHORIZATION_TASK_SHA256={authorization_hash}",
                    "PUBLISHER_GIT_NAME='Fixture Publisher'",
                    "PUBLISHER_GIT_EMAIL=publisher@example.invalid",
                )
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        """Remove the isolated fixture."""
        self.temp_dir.cleanup()

    def write_runtime_release_manifest(
        self, release_root: Path, source_commit: str
    ) -> Path:
        """Seal the independently enumerated complete runtime fixture."""
        entries = []
        for name in sorted(EXPECTED_RUNTIME_RELEASE_FILES):
            target = release_root / name
            mode = stat.S_IMODE(target.lstat().st_mode)
            self.assertIn(mode, {0o644, 0o755}, name)
            entries.append(
                {
                    "path": name,
                    "mode": f"100{mode:03o}",
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )
        manifest = release_root / "runtime-release-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_commit": source_commit,
                    "files": entries,
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def create_runtime_release_fixture(
        self, release_root: Path, source_commit: str
    ) -> Path:
        """Create deterministic files for the complete runtime contract."""
        release_root.mkdir()
        for name in EXPECTED_RUNTIME_RELEASE_FILES:
            target = release_root / name
            target.write_text(f"fixture:{name}\n", encoding="utf-8")
            target.chmod(0o755 if name.endswith((".py", ".sh")) else 0o644)
        return self.write_runtime_release_manifest(release_root, source_commit)

    def test_runtime_release_verifier_accepts_exact_manifest(self) -> None:
        """Accept the independently enumerated complete runtime release."""
        release_root = self.workdir / "release"
        manifest = self.create_runtime_release_fixture(release_root, "a" * 40)
        result = subprocess.run(
            [str(RUNTIME_RELEASE_VERIFIER), str(manifest), str(release_root)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source_commit=" + "a" * 40, result.stdout)
        self.assertIn(f"files={len(EXPECTED_RUNTIME_RELEASE_FILES)}", result.stdout)

    def test_runtime_release_verifier_rejects_incomplete_manifest(self) -> None:
        """A stale required runtime file cannot be omitted from the manifest."""
        release_root = self.workdir / "release-incomplete"
        manifest = self.create_runtime_release_fixture(release_root, "b" * 40)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["files"] = [
            entry
            for entry in payload["files"]
            if entry["path"] != "send-it-news-discord-notification.py"
        ]
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [str(RUNTIME_RELEASE_VERIFIER), str(manifest), str(release_root)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("manifest_runtime_file_set_mismatch", result.stderr)

    def test_runtime_release_verifier_rejects_tampering_and_symlink_escape(self) -> None:
        """A changed file or symlink cannot silently replace the reviewed runtime."""
        release_root = self.workdir / "release-tamper"
        manifest = self.create_runtime_release_fixture(release_root, "c" * 40)
        target = release_root / "send-it-news-discord-notification.py"
        target.write_text("tampered\n", encoding="utf-8")
        result = subprocess.run(
            [str(RUNTIME_RELEASE_VERIFIER), str(manifest), str(release_root)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("runtime_file_digest_mismatch", result.stderr)

        outside = self.root / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        target.unlink()
        target.symlink_to(outside)
        result = subprocess.run(
            [str(RUNTIME_RELEASE_VERIFIER), str(manifest), str(release_root)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("runtime_file_symlink_escape", result.stderr)

    def notification_fixture(
        self, stdout: str, returncode: int
    ) -> tuple[Path, Path, Path, Path, Path]:
        """Create one schema-valid fixed-push result and a controllable Hermes."""
        notification_workdir = self.root / "notification-workdir"
        notification_workdir.mkdir()
        summary = (
            self.user
            / "10 Prompt"
            / "2026"
            / "08"
            / "31"
            / "SUMMARY-IT-NEWS-2026-08-31.md"
        )
        summary.parent.mkdir(parents=True)
        summary.write_text("private summary body must not be sent\n", encoding="utf-8")
        counter = notification_workdir / "hermes-calls.txt"
        arguments = notification_workdir / "hermes-arguments.json"
        hermes = notification_workdir / "hermes"
        hermes.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"counter = Path({str(counter)!r})\n"
            "with counter.open('a', encoding='utf-8') as stream:\n"
            "    stream.write('called\\n')\n"
            f"Path({str(arguments)!r}).write_text("
            "json.dumps(sys.argv[1:], ensure_ascii=False), encoding='utf-8')\n"
            f"sys.stdout.write({stdout!r})\n"
            f"raise SystemExit({returncode})\n",
            encoding="utf-8",
        )
        hermes.chmod(0o755)
        runtime = {
            "workdir": str(notification_workdir.resolve()),
            "hermes_bin": str(hermes.resolve()),
            "discord_news_target": "discord:1234567890123456789",
            "user_vault_root": str(self.user.resolve()),
            "user_remote_url": "git@github.com:fixture-owner/fixture-repo.git",
        }
        head = "a" * 40
        published_vault = {
            "commit_status": "complete",
            "commit_hashes": [head],
            "push_status": "complete",
            "local_head": head,
            "remote_head": head,
            "clean": True,
            "publication_mode": "sweep",
            "deferred_cleanup": [],
        }
        initial = {
            "outcome": "partial_publication",
            "phase": "initial_fixed_push",
            "daily_pipeline_status": "complete",
            "summary_path": str(summary.resolve()),
            "advisory_path": str((self.agents / "advisory.md").resolve()),
            "notification_result": "none",
            "agents_vault": dict(published_vault),
            "user_vault": dict(published_vault),
            "publication_mode": {
                "agents_vault": "sweep",
                "user_vault": "sweep",
            },
            "deferred_cleanup": {"agents_vault": [], "user_vault": []},
            "evidence_finalization_commit": None,
            "next_action": "Finalize publication evidence.",
        }
        runtime_path = notification_workdir / "runtime.json"
        initial_path = notification_workdir / "initial.json"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        initial_path.write_text(json.dumps(initial), encoding="utf-8")
        return notification_workdir, runtime_path, initial_path, counter, arguments

    def test_collection_date_parser_rejects_embedded_date_substrings(self) -> None:
        """Accept complete sealed dates, never arbitrary text containing a date."""
        parser = COLLECTION_VALIDATOR_MODULE.parse_publication_date
        self.assertEqual(parser("2026-08-18"), date(2026, 8, 18))
        self.assertEqual(
            parser("Tue, 18 Aug 2026 12:00:00 GMT"), date(2026, 8, 18)
        )
        self.assertEqual(parser("2026/8/18"), date(2026, 8, 18))
        for value in (
            "not-a-date 2026-08-18",
            "2026-08-18 trailing",
            "prefix Tue, 18 Aug 2026 12:00:00 GMT",
            " 2026-08-18",
            "2026/8/18 ",
            "2026-02-30",
        ):
            with self.subTest(value=value):
                self.assertIsNone(parser(value))

    def test_semantic_state_ignores_only_index_stat_cache_serialization(self) -> None:
        """A Git read may refresh raw index bytes without changing staged meaning."""
        initial = {
            "capture_status": "available",
            "branch": "main",
            "upstream": "origin/main",
            "local_head": "a" * 40,
            "remote_head": "a" * 40,
            "history_relation": "equal",
            "history_capture_status": "available",
            "operation_in_progress": False,
            "git_control_sha256": "b" * 64,
            "dirty_worktree_sha256": "c" * 64,
            "dirty_digest": "d" * 64,
            "diff_snapshot_sha256": "e" * 64,
            "dirty_paths": [],
            "staged_paths": [],
            "index_entries": [],
            "index_sha256": "1" * 64,
            "index_identity": [1, 2, 3, 33188, 4, 5],
        }
        refreshed = json.loads(json.dumps(initial))
        refreshed["index_sha256"] = "2" * 64
        refreshed["index_identity"] = [1, 6, 3, 33188, 7, 8]

        self.assertTrue(
            REVIEW_MODULE.same_semantic_vault_state(initial, refreshed)
        )
        self.assertTrue(
            COMMITTER_MODULE.same_semantic_vault_state(initial, refreshed)
        )
        mode = MODE_MODULE.vault_mode(initial, refreshed, "reports/news.md")
        self.assertFalse(mode["state_changed"])
        self.assertEqual(mode["required_mode"], "sweep")
        self.assertEqual(
            mode["initial_state_sha256"], mode["review_state_sha256"]
        )

        semantic_drift = json.loads(json.dumps(refreshed))
        semantic_drift["index_entries"] = [
            {
                "path": "parallel.md",
                "mode": "100644",
                "git_blob_oid": "f" * 40,
                "stage": 0,
            }
        ]
        self.assertFalse(
            REVIEW_MODULE.same_semantic_vault_state(initial, semantic_drift)
        )
        self.assertFalse(
            COMMITTER_MODULE.same_semantic_vault_state(initial, semantic_drift)
        )
        drift_mode = MODE_MODULE.vault_mode(
            initial, semantic_drift, "reports/news.md"
        )
        self.assertTrue(drift_mode["state_changed"])
        self.assertIn("index_entries", drift_mode["changed_fields"])

    def test_normal_publication_rejects_raw_index_churn_but_resume_accepts_it(
        self,
    ) -> None:
        """Limit the raw stat-cache exception to resumable progress comparison."""
        reviewed = {
            "local_head": "a" * 40,
            "dirty_digest": "b" * 64,
            "staged_paths": [],
            "index_entries": [],
            "index_sha256": "1" * 64,
            "index_identity": [1, 2, 3, 33188, 4, 5],
        }
        refreshed = json.loads(json.dumps(reviewed))
        refreshed["index_sha256"] = "2" * 64
        refreshed["index_identity"] = [1, 6, 3, 33188, 7, 8]
        with mock.patch.object(
            COMMITTER_MODULE, "capture_one", return_value=refreshed
        ):
            with self.assertRaisesRegex(
                COMMITTER_MODULE.CommitError, "state changed after approved review"
            ):
                COMMITTER_MODULE.capture_one_exact(
                    "/capture", "/runtime", "user_vault", reviewed
                )
            COMMITTER_MODULE.capture_one_semantic(
                "/capture", "/runtime", "user_vault", reviewed
            )

        before_install = {
            **reviewed,
            "dirty_lines": [],
            "dirty_paths": [],
            "dirty_entries": [],
            "dirty_metadata": [],
        }
        after_install = json.loads(json.dumps(before_install))
        after_install.update({
            "dirty_lines": ["?? news.md"],
            "dirty_paths": ["news.md"],
            "dirty_entries": [{
                "path": "news.md",
                "mode": "100644",
                "git_blob_oid": "c" * 40,
            }],
            "index_sha256": "2" * 64,
            "index_identity": [1, 6, 3, 33188, 7, 8],
        })
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError,
            "control state changed during artifact installation",
        ):
            COMMITTER_MODULE.validate_installed_vault(
                before_install, after_install, "news.md"
            )
        COMMITTER_MODULE.validate_installed_vault(
            before_install,
            after_install,
            "news.md",
            allow_volatile_index=True,
        )

    def test_carried_review_accepts_stat_cache_churn_and_failed_peer_replan(self) -> None:
        """Retain one completed Vault while its failed peer receives a new review."""
        summary_target = self.user / "summary.md"
        advisory_target = self.agents / "advisory.md"
        commit = "a" * 40
        old_user = {
            "local_head": commit,
            "remote_head": "b" * 40,
            "history_relation": "local_ahead",
            "history_capture_status": "available",
            "history_snapshot_sha256": "c" * 64,
            "dirty_digest": "d" * 64,
            "dirty_paths": [],
            "staged_paths": [],
            "index_entries": [],
            "index_sha256": "1" * 64,
            "index_identity": [1, 2, 3, 33188, 4, 5],
            "local_commits": [
                {"commit": commit, "changed_paths": ["summary.md"]}
            ],
        }
        current_user = json.loads(json.dumps(old_user))
        current_user["index_sha256"] = "2" * 64
        current_user["index_identity"] = [1, 6, 3, 33188, 7, 8]
        old_agents = {
            "local_head": "e" * 40,
            "dirty_digest": "f" * 64,
            "dirty_paths": [],
            "staged_paths": [],
            "index_entries": [],
            "index_sha256": "3" * 64,
            "index_identity": [9, 10, 3, 33188, 11, 12],
            "local_commits": [],
        }
        replanned_agents = json.loads(json.dumps(old_agents))
        replanned_agents["dirty_digest"] = "0" * 64
        replanned_agents["dirty_paths"] = ["parallel.md"]
        carried = {
            "outcome": "partial_publication",
            "phase": "local_commit",
            "daily_pipeline_status": "complete",
            "summary_path": str(summary_target),
            "advisory_path": None,
            "agents_vault": {
                "commit_status": "not_started",
                "commit_hashes": [],
                "local_head": old_agents["local_head"],
                "post_dirty_digest": old_agents["dirty_digest"],
            },
            "user_vault": {
                "commit_status": "complete",
                "commit_hashes": [commit],
                "local_head": commit,
                "post_dirty_digest": old_user["dirty_digest"],
            },
            "resumable_state": {
                "agents_vault": old_agents,
                "user_vault": old_user,
            },
        }
        carry_path = self.workdir / "carried-result.json"
        carry_path.write_text(json.dumps(carried), encoding="utf-8")
        context = {
            "carried_commit_result_file": str(carry_path),
            "carried_commit_result": carried,
            "carried_commit_result_sha256": hashlib.sha256(
                carry_path.read_bytes()
            ).hexdigest(),
            "publication_mode_hint": {
                "agents_vault": {"artifact_already_committed": False},
                "user_vault": {"artifact_already_committed": True},
            },
            "runtime": {
                "agents_vault_root": str(self.agents),
                "user_vault_root": str(self.user),
            },
        }
        pre = {
            "agents_vault": replanned_agents,
            "user_vault": current_user,
        }
        plan = {
            "advisory_target": str(advisory_target),
            "summary_target": str(summary_target),
        }

        self.assertEqual(
            REVIEW_MODULE.validate_carried_commit_result(context, pre, plan),
            {"user_vault"},
        )

        changed_index = json.loads(json.dumps(pre))
        changed_index["user_vault"]["index_entries"] = [
            {
                "path": "parallel.md",
                "mode": "100644",
                "git_blob_oid": "1" * 40,
                "stage": 0,
            }
        ]
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "no longer matches its resumable state"
        ):
            REVIEW_MODULE.validate_carried_commit_result(
                context, changed_index, plan
            )

    def test_cross_context_committer_carries_completed_vault_after_index_refresh(
        self,
    ) -> None:
        """Commit a replanned peer without rejecting raw index stat-cache churn."""
        summary = self.user / "summary.md"
        advisory = self.agents / "advisory.md"
        summary.write_text("summary\n", encoding="utf-8")
        advisory.write_text("advisory\n", encoding="utf-8")
        user_head = "a" * 40
        agents_old_head = "b" * 40
        agents_current_head = "c" * 40
        user_old = {
            "local_head": user_head,
            "dirty_digest": "d" * 64,
            "dirty_lines": [],
            "dirty_paths": [],
            "staged_paths": [],
            "index_entries": [],
            "index_sha256": "1" * 64,
            "index_identity": [1, 2, 3, 33188, 4, 5],
        }
        user_current = json.loads(json.dumps(user_old))
        user_current["index_sha256"] = "2" * 64
        user_current["index_identity"] = [1, 6, 3, 33188, 7, 8]
        agents_old = {
            "local_head": agents_old_head,
            "dirty_digest": "e" * 64,
            "dirty_lines": [],
            "dirty_paths": [],
            "staged_paths": [],
            "index_entries": [],
            "index_sha256": "3" * 64,
            "index_identity": [9, 10, 3, 33188, 11, 12],
        }
        agents_current = json.loads(json.dumps(agents_old))
        agents_current["local_head"] = agents_current_head
        agents_current["dirty_digest"] = "f" * 64
        agents_current["dirty_lines"] = ["?? parallel.md"]
        agents_current["dirty_paths"] = ["parallel.md"]
        pre = {
            "agents_vault": agents_current,
            "user_vault": user_current,
        }
        runtime = {
            "agents_vault_root": str(self.agents),
            "agents_git_dir": str(self.agents / ".git"),
            "user_vault_root": str(self.user),
            "user_git_dir": str(self.user / ".git"),
            "gitleaks_bin": str(self.fake_gitleaks),
            "publisher_git_name": "Fixture Publisher",
            "publisher_git_email": "publisher@example.invalid",
        }
        collection = {
            "daily_pipeline_status": "complete",
            "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
            "advisory_sha256": hashlib.sha256(advisory.read_bytes()).hexdigest(),
            "notification_result": "none",
        }
        plan = {
            "summary_target": str(summary),
            "advisory_target": str(advisory),
        }
        carried = {
            "outcome": "partial_publication",
            "phase": "local_commit",
            "daily_pipeline_status": "complete",
            "summary_path": str(summary),
            "advisory_path": None,
            "agents_vault": {
                "commit_status": "not_started",
                "commit_hashes": [],
                "local_head": agents_old_head,
                "post_dirty_digest": agents_old["dirty_digest"],
            },
            "user_vault": {
                "commit_status": "complete",
                "commit_hashes": [user_head],
                "local_head": user_head,
                "post_dirty_digest": user_old["dirty_digest"],
                "pre_local_head": "0" * 40,
                "pre_dirty_digest": "0" * 64,
                "clean": True,
            },
            "resumable_state": {
                "agents_vault": agents_old,
                "user_vault": user_old,
            },
        }
        carried_path = self.workdir / "cross-context-carried.json"
        carried_path.write_text(json.dumps(carried), encoding="utf-8")
        context = {
            "runtime": runtime,
            "pre_collection_state": pre,
            "verified_collection": collection,
            "artifact_plan": plan,
            "carried_commit_result": carried,
            "carried_commit_result_sha256": hashlib.sha256(
                carried_path.read_bytes()
            ).hexdigest(),
        }
        context_path = self.workdir / "cross-context.json"
        context_path.write_text(json.dumps(context), encoding="utf-8")
        review = {
            "outcome": "approved",
            "publication_context_sha256": hashlib.sha256(
                context_path.read_bytes()
            ).hexdigest(),
            "agents_vault": {
                "publication_mode": "own_only",
                "deferred_cleanup": [{"path": "parallel.md", "reason": "deferred"}],
                "commit_required": True,
                "commit_groups": [{"message": "advisory", "paths": ["advisory.md"]}],
            },
            "user_vault": {
                "publication_mode": "own_only",
                "deferred_cleanup": [],
                "commit_required": False,
                "commit_groups": [],
            },
        }
        review_path = self.workdir / "cross-context-review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        inputs = []
        for name, value in (
            ("cross-runtime.json", runtime),
            ("cross-pre.json", pre),
            ("cross-collection.json", collection),
            ("cross-plan.json", plan),
        ):
            path = self.workdir / name
            path.write_text(json.dumps(value), encoding="utf-8")
            inputs.append(path)
        output = self.workdir / "cross-context-commit-result.json"
        agents_result = {
            "commit_status": "complete",
            "commit_hashes": ["4" * 40],
            "pre_local_head": agents_current_head,
            "local_head": "4" * 40,
            "pre_dirty_digest": agents_current["dirty_digest"],
            "post_dirty_digest": agents_current["dirty_digest"],
            "clean": False,
            "publication_mode": "own_only",
            "deferred_cleanup": review["agents_vault"]["deferred_cleanup"],
        }

        with mock.patch.object(
            COMMITTER_MODULE, "capture_state", return_value=pre
        ), mock.patch.object(
            COMMITTER_MODULE, "capture_one", return_value=user_current
        ), mock.patch.object(
            COMMITTER_MODULE, "verify_carried_artifact"
        ) as verified, mock.patch.object(
            COMMITTER_MODULE,
            "publish_one_vault",
            return_value=(agents_result, True, False, None, None),
        ) as published:
            status = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in inputs),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                    str(carried_path),
                ]
            )

        self.assertEqual(status, 0)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "ready_to_push")
        self.assertEqual(result["user_vault"]["commit_hashes"], [user_head])
        self.assertEqual(result["agents_vault"], agents_result)
        verified.assert_called_once()
        published.assert_called_once()

    def test_durable_directory_operations_persist_destination_first(self) -> None:
        """Persist new dirents before removal and persist newly created parents."""
        source = self.root / "durable-source"
        destination = self.root / "durable-destination"
        source.mkdir()
        destination.mkdir()
        source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
        destination_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        calls: list[int] = []
        real_fsync = os.fsync

        def record_fsync(descriptor: int) -> None:
            calls.append(descriptor)
            real_fsync(descriptor)

        try:
            with mock.patch.object(
                ATOMIC_FILE_OPS_MODULE.os,
                "fsync",
                side_effect=record_fsync,
            ):
                ATOMIC_FILE_OPS_MODULE.fsync_after_rename(
                    source_fd, destination_fd
                )
            self.assertEqual(calls, [destination_fd, source_fd])
            calls.clear()
            with mock.patch.object(
                ATOMIC_FILE_OPS_MODULE.os,
                "fsync",
                side_effect=record_fsync,
            ):
                ATOMIC_FILE_OPS_MODULE.mkdir_durable(
                    "child", 0o700, parent_fd=destination_fd
                )
            self.assertEqual(calls, [destination_fd])
            self.assertTrue((destination / "child").is_dir())
        finally:
            os.close(source_fd)
            os.close(destination_fd)

    def test_transaction_cleanup_retains_a_post_check_replacement(self) -> None:
        """Move, retain, and reject an inode swapped in after the final check."""
        git_dir = self.root / "cleanup-retention-git"
        git_dir.mkdir()
        target = git_dir / "owned-private-entry"
        target.write_bytes(b"owned\n")
        directory_fd = os.open(git_dir, os.O_RDONLY | os.O_DIRECTORY)
        content, identity = ATOMIC_FILE_OPS_MODULE._read_entry_contract(
            directory_fd,
            target.name,
        )
        expected = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "identity": identity,
        }
        replacement = git_dir / "replacement"
        replacement.write_bytes(b"third-party\n")
        real_rename = ATOMIC_FILE_OPS_MODULE.rename_no_replace

        def replace_after_check(*arguments: object, **kwargs: object) -> None:
            os.replace(replacement, target)
            real_rename(*arguments, **kwargs)

        try:
            with mock.patch.object(
                ATOMIC_FILE_OPS_MODULE,
                "rename_no_replace",
                side_effect=replace_after_check,
            ), self.assertRaisesRegex(
                ATOMIC_FILE_OPS_MODULE.AtomicTransactionError,
                "third-party inode retained",
            ):
                ATOMIC_FILE_OPS_MODULE._retain_matching_entry(
                    directory_fd, target.name, expected
                )
        finally:
            os.close(directory_fd)
        retained = list(
            git_dir.glob(ATOMIC_FILE_OPS_MODULE.RETAINED_ENTRY_PREFIX + "*")
        )
        self.assertEqual(len(retained), 1)
        self.assertEqual((retained[0] / "entry").read_bytes(), b"third-party\n")
        self.assertFalse(target.exists())

    def test_rename_capability_probe_is_retained_without_unlink(self) -> None:
        """Exercise no-replace support and keep the exact probe inode private."""
        directory = self.root / "rename-probe-retention"
        directory.mkdir()
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            ATOMIC_FILE_OPS_MODULE.verify_rename_no_replace(directory_fd)
        finally:
            os.close(directory_fd)
        retained = list(directory.glob(".rename-no-replace-retained-*"))
        self.assertEqual(len(retained), 1)
        self.assertEqual((retained[0] / "entry").read_bytes(), b"")
        self.assertEqual(list(directory.glob(".rename-no-replace-source-*")), [])

    def test_failed_entry_creation_retains_a_cleanup_replacement(self) -> None:
        """Never unlink a replacement inserted during failed-entry cleanup."""
        git_dir = self.root / "failed-entry-retention-git"
        git_dir.mkdir()
        target = git_dir / "candidate"
        replacement = git_dir / "replacement"
        replacement.write_bytes(b"third-party\n")
        directory_fd = os.open(git_dir, os.O_RDONLY | os.O_DIRECTORY)

        def fail_after_replacement(_descriptor: int, _content: bytes) -> None:
            os.replace(replacement, target)
            raise OSError("injected write failure")

        try:
            with mock.patch.object(
                ATOMIC_FILE_OPS_MODULE,
                "_write_all",
                side_effect=fail_after_replacement,
            ), self.assertRaisesRegex(
                ATOMIC_FILE_OPS_MODULE.AtomicTransactionError,
                "third-party inode retained",
            ):
                ATOMIC_FILE_OPS_MODULE._create_entry(
                    directory_fd, target.name, b"owned\n", 0o600
                )
        finally:
            os.close(directory_fd)
        retained = list(
            git_dir.glob(ATOMIC_FILE_OPS_MODULE.RETAINED_ENTRY_PREFIX + "*")
        )
        self.assertEqual(len(retained), 1)
        self.assertEqual((retained[0] / "entry").read_bytes(), b"third-party\n")
        self.assertFalse(target.exists())

    def test_head_index_exchange_preserves_a_racing_destination(self) -> None:
        """Never overwrite a new shared index inserted at the final CAS boundary."""
        git_dir = self.root / "atomic-race-git"
        git_dir.mkdir()
        index = git_dir / "index"
        index.write_bytes(b"reviewed-index")
        metadata = index.stat()
        expected_identity = [
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mode,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ]
        candidate = self.workdir / "atomic-race-candidate"
        candidate.write_bytes(b"publication-index")
        base_head = "1" * 40
        candidate_head = "2" * 40
        head = {"value": base_head}

        def update_head(new: str, old: str) -> None:
            self.assertEqual(head["value"], old)
            head["value"] = new

        def replace_at_boundary(phase: str) -> None:
            if phase != "before_index_exchange":
                return
            replacement = git_dir / "third-party-index"
            replacement.write_bytes(b"third-party-staged-index")
            os.replace(replacement, index)

        with self.assertRaisesRegex(
            ATOMIC_FILE_OPS_MODULE.AtomicTransactionError,
            "atomic exchange",
        ):
            ATOMIC_FILE_OPS_MODULE.publish_head_index_transaction(
                git_dir,
                base_head=base_head,
                candidate_head=candidate_head,
                expected_index_sha256=hashlib.sha256(b"reviewed-index").hexdigest(),
                expected_index_identity=expected_identity,
                candidate_index_path=candidate,
                read_head=lambda: head["value"],
                update_head=update_head,
                fault_injector=replace_at_boundary,
            )
        self.assertEqual(head["value"], base_head)
        self.assertEqual(index.read_bytes(), b"third-party-staged-index")
        self.assertFalse((git_dir / "index.lock").exists())
        self.assertFalse(
            (git_dir / ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_TRANSACTION_JOURNAL).exists()
        )

    def test_expected_index_link_rejects_same_content_inode_rebind(self) -> None:
        """Bind the reviewed index descriptor across hard-link creation."""
        git_dir = self.root / "atomic-expected-index-rebind"
        git_dir.mkdir()
        index = git_dir / "index"
        index.write_bytes(b"reviewed-index")
        reviewed = index.stat()
        expected_identity = [
            reviewed.st_dev,
            reviewed.st_ino,
            reviewed.st_size,
            reviewed.st_mode,
            reviewed.st_mtime_ns,
            reviewed.st_ctime_ns,
        ]
        candidate = self.workdir / "atomic-expected-index-rebind-candidate"
        candidate.write_bytes(b"publication-index")

        def replace_before_link(phase: str) -> None:
            if phase == "before_expected_index_link":
                replacement = git_dir / "same-content-different-inode"
                replacement.write_bytes(b"reviewed-index")
                os.replace(replacement, index)

        with self.assertRaisesRegex(
            ATOMIC_FILE_OPS_MODULE.AtomicTransactionError,
            "rebound to another inode",
        ):
            ATOMIC_FILE_OPS_MODULE.publish_head_index_transaction(
                git_dir,
                base_head="1" * 40,
                candidate_head="2" * 40,
                expected_index_sha256=hashlib.sha256(b"reviewed-index").hexdigest(),
                expected_index_identity=expected_identity,
                candidate_index_path=candidate,
                read_head=lambda: "1" * 40,
                update_head=lambda _new, _old: self.fail("HEAD must not change"),
                fault_injector=replace_before_link,
            )
        self.assertEqual(index.read_bytes(), b"reviewed-index")
        self.assertNotEqual(index.stat().st_ino, reviewed.st_ino)
        retained = list(
            git_dir.glob(ATOMIC_FILE_OPS_MODULE.RETAINED_ENTRY_PREFIX + "*")
        )
        self.assertGreaterEqual(len(retained), 1)

    def test_other_cleanup_retains_check_to_move_replacement(self) -> None:
        """Reject a second unrelated inode inserted after cleanup classification."""
        git_dir = self.root / "atomic-other-cleanup-race"
        git_dir.mkdir()
        target = git_dir / "index.lock"
        target.write_bytes(b"first-third-party\n")
        replacement = git_dir / "replacement"
        replacement.write_bytes(b"second-third-party\n")
        expected_file = git_dir / "expected"
        candidate_file = git_dir / "candidate"
        expected_file.write_bytes(b"expected\n")
        candidate_file.write_bytes(b"candidate\n")
        directory_fd = os.open(git_dir, os.O_RDONLY | os.O_DIRECTORY)
        expected_content, expected_identity = ATOMIC_FILE_OPS_MODULE._read_entry_contract(
            directory_fd, expected_file.name
        )
        candidate_content, candidate_identity = ATOMIC_FILE_OPS_MODULE._read_entry_contract(
            directory_fd, candidate_file.name
        )
        journal = {
            "expected_index": {
                "sha256": hashlib.sha256(expected_content).hexdigest(),
                "identity": expected_identity,
            },
            "candidate_index": {
                "sha256": hashlib.sha256(candidate_content).hexdigest(),
                "identity": candidate_identity,
            },
            "displaced_name": ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_DISPLACED_PREFIX
            + "fixture",
        }
        real_retain = ATOMIC_FILE_OPS_MODULE.retain_named_entry_no_replace

        def replace_before_move(*args: object, **kwargs: object):
            os.replace(replacement, target)
            return real_retain(*args, **kwargs)

        try:
            with mock.patch.object(
                ATOMIC_FILE_OPS_MODULE,
                "retain_named_entry_no_replace",
                side_effect=replace_before_move,
            ), self.assertRaisesRegex(
                ATOMIC_FILE_OPS_MODULE.AtomicTransactionError,
                "third-party inode retained",
            ):
                ATOMIC_FILE_OPS_MODULE._preserve_private_entry(
                    directory_fd, target.name, journal
                )
        finally:
            os.close(directory_fd)
        retained = list(
            git_dir.glob(ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_DISPLACED_PREFIX + "*")
        )
        self.assertEqual(len(retained), 1)
        self.assertEqual((retained[0] / "entry").read_bytes(), b"second-third-party\n")
        self.assertFalse(target.exists())

    def test_head_index_journal_recovers_both_crash_boundaries(self) -> None:
        """Roll back a pre-HEAD crash and roll forward a committed HEAD crash."""

        class SimulatedCrash(BaseException):
            pass

        for crash_phase, expected_status, expected_head, expected_bytes in (
            ("index_exchanged", "rolled_back", "1" * 40, b"reviewed-index"),
            ("head_updated", "rolled_forward", "2" * 40, b"publication-index"),
        ):
            with self.subTest(crash_phase=crash_phase):
                git_dir = self.root / f"atomic-crash-{crash_phase}"
                git_dir.mkdir()
                index = git_dir / "index"
                index.write_bytes(b"reviewed-index")
                metadata = index.stat()
                expected_identity = [
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mode,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                ]
                candidate = self.workdir / f"candidate-{crash_phase}"
                candidate.write_bytes(b"publication-index")
                head = {"value": "1" * 40}

                def update_head(new: str, old: str) -> None:
                    if head["value"] != old:
                        raise RuntimeError("fixture HEAD CAS failed")
                    head["value"] = new

                def crash(phase: str) -> None:
                    if phase == crash_phase:
                        raise SimulatedCrash()

                with self.assertRaises(SimulatedCrash):
                    ATOMIC_FILE_OPS_MODULE.publish_head_index_transaction(
                        git_dir,
                        base_head="1" * 40,
                        candidate_head="2" * 40,
                        expected_index_sha256=hashlib.sha256(
                            b"reviewed-index"
                        ).hexdigest(),
                        expected_index_identity=expected_identity,
                        candidate_index_path=candidate,
                        read_head=lambda: head["value"],
                        update_head=update_head,
                        fault_injector=crash,
                    )
                self.assertTrue(
                    (
                        git_dir
                        / ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_TRANSACTION_JOURNAL
                    ).is_file()
                )
                status = ATOMIC_FILE_OPS_MODULE.recover_head_index_transaction(
                    git_dir,
                    read_head=lambda: head["value"],
                    update_head=update_head,
                )
                self.assertEqual(status, expected_status)
                self.assertEqual(head["value"], expected_head)
                self.assertEqual(index.read_bytes(), expected_bytes)
                self.assertFalse((git_dir / "index.lock").exists())
                self.assertFalse(
                    (
                        git_dir
                        / ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_TRANSACTION_JOURNAL
                    ).exists()
                )

    def test_head_index_recovery_survives_destination_replacement_after_exchange(self) -> None:
        """Restore the reviewed index while retaining a post-exchange racing inode."""
        git_dir = self.root / "atomic-post-exchange-race"
        git_dir.mkdir()
        index = git_dir / "index"
        index.write_bytes(b"reviewed-index")
        metadata = index.stat()
        expected_identity = [
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mode,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ]
        candidate = self.workdir / "atomic-post-exchange-candidate"
        candidate.write_bytes(b"publication-index")
        head = {"value": "1" * 40}

        def update_head(new: str, old: str) -> None:
            self.assertEqual(head["value"], old)
            head["value"] = new

        def replace_after_exchange(phase: str) -> None:
            if phase == "index_exchanged":
                replacement = git_dir / "third-party-index"
                replacement.write_bytes(b"post-exchange-third-party-index")
                os.replace(replacement, index)

        with self.assertRaisesRegex(
            ATOMIC_FILE_OPS_MODULE.AtomicTransactionError,
            "atomic exchange",
        ):
            ATOMIC_FILE_OPS_MODULE.publish_head_index_transaction(
                git_dir,
                base_head="1" * 40,
                candidate_head="2" * 40,
                expected_index_sha256=hashlib.sha256(b"reviewed-index").hexdigest(),
                expected_index_identity=expected_identity,
                candidate_index_path=candidate,
                read_head=lambda: head["value"],
                update_head=update_head,
                fault_injector=replace_after_exchange,
            )
        self.assertEqual(head["value"], "1" * 40)
        self.assertEqual(index.read_bytes(), b"reviewed-index")
        displaced = list(
            git_dir.glob(ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_DISPLACED_PREFIX + "*")
        )
        self.assertEqual(len(displaced), 1)
        self.assertEqual(
            (displaced[0] / "entry").read_bytes(),
            b"post-exchange-third-party-index",
        )
        self.assertFalse((git_dir / "index.lock").exists())
        self.assertFalse(
            (git_dir / ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_TRANSACTION_JOURNAL).exists()
        )
        self.assertFalse(
            list(git_dir.glob(ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_CANDIDATE_PREFIX + "*"))
        )
        self.assertFalse(
            list(git_dir.glob(ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_EXPECTED_PREFIX + "*"))
        )

    def test_head_index_recovery_survives_lock_replacement_after_exchange(self) -> None:
        """Restore reviewed staged state when the displaced lock name is replaced."""
        git_dir = self.root / "atomic-post-exchange-lock-race"
        git_dir.mkdir()
        index = git_dir / "index"
        index.write_bytes(b"reviewed-index")
        metadata = index.stat()
        expected_identity = [
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mode,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ]
        candidate = self.workdir / "atomic-post-exchange-lock-candidate"
        candidate.write_bytes(b"publication-index")
        head = {"value": "1" * 40}

        def update_head(new: str, old: str) -> None:
            self.assertEqual(head["value"], old)
            head["value"] = new

        def replace_lock_after_exchange(phase: str) -> None:
            if phase == "index_exchanged":
                replacement = git_dir / "third-party-lock"
                replacement.write_bytes(b"post-exchange-third-party-lock")
                os.replace(replacement, git_dir / "index.lock")

        with self.assertRaisesRegex(
            ATOMIC_FILE_OPS_MODULE.AtomicTransactionError,
            "atomic exchange",
        ):
            ATOMIC_FILE_OPS_MODULE.publish_head_index_transaction(
                git_dir,
                base_head="1" * 40,
                candidate_head="2" * 40,
                expected_index_sha256=hashlib.sha256(b"reviewed-index").hexdigest(),
                expected_index_identity=expected_identity,
                candidate_index_path=candidate,
                read_head=lambda: head["value"],
                update_head=update_head,
                fault_injector=replace_lock_after_exchange,
            )
        self.assertEqual(head["value"], "1" * 40)
        self.assertEqual(index.read_bytes(), b"reviewed-index")
        displaced = list(
            git_dir.glob(ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_DISPLACED_PREFIX + "*")
        )
        self.assertEqual(len(displaced), 1)
        self.assertEqual(
            (displaced[0] / "entry").read_bytes(),
            b"post-exchange-third-party-lock",
        )
        self.assertFalse((git_dir / "index.lock").exists())
        self.assertFalse(
            (git_dir / ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_TRANSACTION_JOURNAL).exists()
        )
        self.assertFalse(
            list(git_dir.glob(ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_CANDIDATE_PREFIX + "*"))
        )
        self.assertFalse(
            list(git_dir.glob(ATOMIC_FILE_OPS_MODULE.HEAD_INDEX_EXPECTED_PREFIX + "*"))
        )

    def test_codex_schema_projection_preserves_canonical_contract(self) -> None:
        source_path = SKILL_ROOT / "references" / "publication-review-result.schema.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        projected = SCHEMA_MODULE.compatible_schema(source)
        encoded = json.dumps(projected, sort_keys=True)

        self.assertIn("allOf", source)
        self.assertNotIn('"allOf"', encoded)
        self.assertNotIn('"if"', encoded)
        self.assertNotIn('"then"', encoded)
        self.assertNotIn('"else"', encoded)
        self.assertNotIn('"uniqueItems"', encoded)
        self.assertNotIn('"oneOf"', encoded)
        self.assertIn('"anyOf"', encoded)
        self.assertNotIn("approvedManifest", projected["$defs"])
        self.assertEqual(
            projected["$defs"]["validationEvidence"]["properties"]
            ["file_guard"]["enum"],
            ["passed", "blocked"],
        )
        self.assertEqual(
            json.loads(source_path.read_text(encoding="utf-8")), source
        )

    def test_codex_schema_projection_rejects_open_root(self) -> None:
        with self.assertRaises(SCHEMA_MODULE.SchemaProjectionError):
            SCHEMA_MODULE.compatible_schema(
                {"type": "object", "additionalProperties": True}
            )
        with self.assertRaises(SCHEMA_MODULE.SchemaProjectionError):
            SCHEMA_MODULE.compatible_schema(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "not": {"type": "null"},
                }
            )
        with self.assertRaises(SCHEMA_MODULE.SchemaProjectionError):
            SCHEMA_MODULE.compatible_schema(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "allOf": [{"not": {"type": "null"}}],
                }
            )
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            invalid_schema = {
                "type": "object",
                "additionalProperties": False,
                "allOf": [{"not": {"type": "object"}}],
            }
            CANONICAL_MODULE.validate({}, invalid_schema, invalid_schema)

    def test_canonical_validator_rejects_projected_only_result(self) -> None:
        schema = json.loads(
            (SKILL_ROOT / "references" / "collection-result.schema.json")
            .read_text(encoding="utf-8")
        )
        result = {
            "daily_pipeline_status": "complete",
            "run_id": "fixture",
            "summary_path": "summary.md",
            "summary_sha256": "0" * 64,
            "advisory_path": "advisory.md",
            "advisory_sha256": "1" * 64,
            "notification_result": None,
            "vault_artifacts_complete": True,
            "next_action": None,
        }
        CANONICAL_MODULE.validate(result, schema, schema)
        result["next_action"] = "canonical contract requires null"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(result, schema, schema)
        result["next_action"] = None
        result["notification_result"] = 17
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(result, schema, schema)

    def test_canonical_validator_rejects_short_terminal_status_argv(self) -> None:
        """Treat a partial terminal-mode invocation as usage error, not a file error."""
        self.assertEqual(
            CANONICAL_MODULE.main(
                ["validate-canonical-result.py", "--terminal-status", "schema.json"]
            ),
            64,
        )

    def test_publication_review_root_next_action_tracks_blocked_mode(self) -> None:
        """Deferred cleanup alone keeps an approved review terminally actionable."""
        review = {
            "outcome": "approved",
            "agents_vault": {"publication_mode": "sweep"},
            "user_vault": {"publication_mode": "own_only"},
            "next_action": None,
        }
        schema = json.loads(
            (SKILL_ROOT / "references" / "publication-review-result.schema.json")
            .read_text(encoding="utf-8")
        )
        mode_contract = schema["allOf"][1]
        CANONICAL_MODULE.validate(review, mode_contract, schema)
        REVIEW_MODULE.validate_root_contract(review)

        review["next_action"] = "remediate deferred residual later"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(review, mode_contract, schema)
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "unexpected next action"
        ):
            REVIEW_MODULE.validate_root_contract(review)

        review["user_vault"]["publication_mode"] = "blocked"
        CANONICAL_MODULE.validate(review, mode_contract, schema)
        REVIEW_MODULE.validate_root_contract(review)
        review["next_action"] = None
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(review, mode_contract, schema)
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "requires a concrete next action"
        ):
            REVIEW_MODULE.validate_root_contract(review)

    def test_own_only_normalization_completes_large_sealed_residual_set(self) -> None:
        """A model omission cannot make a large own-only path set block core work."""
        agents_paths = [f"parallel/{index:03d}.md" for index in range(128)]
        user_paths = [".codex-handoff/unsafe.md"]

        def manifest(
            excluded: list[str], deferred: list[dict[str, str]]
        ) -> dict[str, object]:
            return {
                "publication_mode": "own_only",
                "core_review_status": "quality_ok",
                "review_or_validation_status": "quality_ok",
                "residual_review_status": "deferred",
                "excluded_paths": excluded,
                "unrelated_dirty_paths": list(excluded),
                "deferred_cleanup": deferred,
                "owned_paths": ["artifact.md"],
                "commit_groups": [
                    {"message": "publish artifact", "paths": ["artifact.md"]}
                ],
            }

        review = {
            "outcome": "approved",
            "agents_vault": manifest([], []),
            "user_vault": manifest(
                user_paths,
                [{"path": user_paths[0], "reason": "reviewed unsafe handoff"}],
            ),
            "next_action": None,
        }
        pre = {
            "agents_vault": {"dirty_paths": agents_paths},
            "user_vault": {"dirty_paths": user_paths},
        }
        materialization = {
            "vaults": {
                "agents_vault": [
                    {
                        "path": path,
                        "materialization_status": (
                            "deferred" if index == 17 else "available"
                        ),
                        "materialization_reason": (
                            "dirty_entry_secret_scan_rejected"
                            if index == 17
                            else None
                        ),
                    }
                    for index, path in enumerate(agents_paths)
                ],
                "user_vault": [
                    {
                        "path": user_paths[0],
                        "materialization_status": "deferred",
                        "materialization_reason": "dirty_entry_added_machine_home_path",
                    }
                ],
            }
        }
        normalized, receipt = REVIEW_MODULE.normalize_own_only_residuals(
            review, {}, pre, materialization
        )

        self.assertEqual(review["agents_vault"]["excluded_paths"], [])
        self.assertEqual(
            normalized["agents_vault"]["excluded_paths"], sorted(agents_paths)
        )
        self.assertEqual(
            normalized["agents_vault"]["unrelated_dirty_paths"],
            sorted(agents_paths),
        )
        agents_reasons = {
            entry["path"]: entry["reason"]
            for entry in normalized["agents_vault"]["deferred_cleanup"]
        }
        self.assertEqual(
            agents_reasons[agents_paths[17]], "dirty_entry_secret_scan_rejected"
        )
        self.assertIn("publication_mode_hint", agents_reasons[agents_paths[0]])
        self.assertEqual(
            normalized["user_vault"]["deferred_cleanup"],
            [{"path": user_paths[0], "reason": "reviewed unsafe handoff"}],
        )
        self.assertEqual(
            normalized["agents_vault"]["commit_groups"],
            review["agents_vault"]["commit_groups"],
        )
        self.assertEqual(receipt["agents_vault"]["dirty_path_count"], 128)
        self.assertEqual(receipt["agents_vault"]["filled_reason_count"], 128)
        self.assertEqual(receipt["user_vault"]["filled_reason_count"], 0)

    def test_own_only_normalization_rejects_foreign_or_duplicate_model_paths(self) -> None:
        """Structural completion cannot launder a path outside the sealed state."""
        base = {
            "publication_mode": "own_only",
            "core_review_status": "quality_ok",
            "review_or_validation_status": "quality_ok",
            "residual_review_status": "deferred",
            "excluded_paths": ["foreign.md"],
            "unrelated_dirty_paths": [],
            "deferred_cleanup": [],
        }
        review = {
            "agents_vault": base,
            "user_vault": {
                **base,
                "excluded_paths": [],
            },
        }
        pre = {
            "agents_vault": {"dirty_paths": ["captured.md"]},
            "user_vault": {"dirty_paths": []},
        }
        materialization = {
            "vaults": {
                "agents_vault": [
                    {"path": "captured.md", "materialization_reason": None}
                ],
                "user_vault": [],
            }
        }
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "duplicate or foreign paths"
        ):
            REVIEW_MODULE.normalize_own_only_residuals(
                review, {}, pre, materialization
            )

        review["agents_vault"]["excluded_paths"] = []
        review["agents_vault"]["deferred_cleanup"] = [
            {"path": "captured.md", "reason": "one"},
            {"path": "captured.md", "reason": "two"},
        ]
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "duplicate or foreign paths"
        ):
            REVIEW_MODULE.normalize_own_only_residuals(
                review, {}, pre, materialization
            )

    def test_validation_evidence_normalization_repairs_only_sealed_identity(self) -> None:
        """A mode-hint digest copy error cannot replace reviewer judgments."""
        agents_diff = "a" * 64
        agents_history = "b" * 64
        user_diff = "c" * 64
        user_history = "d" * 64
        review_state = "e" * 64
        review = {
            "outcome": "approved",
            "agents_vault": {
                "publication_mode": "own_only",
                "commit_groups": [{"message": "publish", "paths": ["artifact.md"]}],
                "validation_evidence": {
                    "file_guard": "blocked",
                    "secret_scan": "passed",
                    "secret_scan_tool": "wrong-tool",
                    "secret_scan_tool_version": "wrong-version",
                    "reviewed_snapshot_sha256": review_state,
                    "reviewed_history_sha256": "f" * 64,
                },
            },
            "user_vault": {
                "publication_mode": "sweep",
                "commit_groups": [{"message": "publish", "paths": ["summary.md"]}],
                "validation_evidence": {
                    "file_guard": "not-reviewed",
                    "secret_scan": "blocked",
                    "secret_scan_tool": "gitleaks",
                    "secret_scan_tool_version": "8.30.1",
                    "reviewed_snapshot_sha256": user_diff,
                    "reviewed_history_sha256": user_history,
                },
            },
            "next_action": None,
        }
        pre = {
            "agents_vault": {
                "diff_snapshot_sha256": agents_diff,
                "history_snapshot_sha256": agents_history,
            },
            "user_vault": {
                "diff_snapshot_sha256": user_diff,
                "history_snapshot_sha256": user_history,
            },
        }

        normalized, receipt = REVIEW_MODULE.normalize_sealed_validation_evidence(
            review, {"runtime": {"gitleaks_version": "8.30.1"}}, pre
        )

        self.assertEqual(
            review["agents_vault"]["validation_evidence"][
                "reviewed_snapshot_sha256"
            ],
            review_state,
        )
        agents_evidence = normalized["agents_vault"]["validation_evidence"]
        self.assertEqual(agents_evidence["reviewed_snapshot_sha256"], agents_diff)
        self.assertEqual(
            agents_evidence["reviewed_history_sha256"], agents_history
        )
        self.assertEqual(agents_evidence["secret_scan_tool"], "gitleaks")
        self.assertEqual(
            agents_evidence["secret_scan_tool_version"], "8.30.1"
        )
        self.assertEqual(agents_evidence["file_guard"], "blocked")
        self.assertEqual(agents_evidence["secret_scan"], "passed")
        self.assertEqual(
            normalized["agents_vault"]["commit_groups"],
            review["agents_vault"]["commit_groups"],
        )
        self.assertEqual(
            normalized["user_vault"]["validation_evidence"]["file_guard"],
            "not-reviewed",
        )
        self.assertFalse(receipt["user_vault"]["normalized"])
        self.assertEqual(receipt["agents_vault"]["corrected_field_count"], 4)
        self.assertEqual(
            receipt["agents_vault"]["corrected_fields"],
            [
                "reviewed_history_sha256",
                "reviewed_snapshot_sha256",
                "secret_scan_tool",
                "secret_scan_tool_version",
            ],
        )

    def test_validation_evidence_normalization_rejects_malformed_evidence(self) -> None:
        """Missing evidence structure remains a fail-closed review error."""
        pre = {
            key: {
                "diff_snapshot_sha256": "a" * 64,
                "history_snapshot_sha256": "b" * 64,
            }
            for key in ("agents_vault", "user_vault")
        }
        review = {
            "agents_vault": {"validation_evidence": []},
            "user_vault": {"validation_evidence": {}},
        }
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "validation evidence is not an object"
        ):
            REVIEW_MODULE.normalize_sealed_validation_evidence(
                review, {"runtime": {"gitleaks_version": "8.30.1"}}, pre
            )

    def test_canonical_publication_schemas_enforce_terminal_state(self) -> None:
        """Reject success/ready labels that do not contain publishable commits."""
        automation_schema = json.loads(
            (SKILL_ROOT / "references" / "automation-result.schema.json")
            .read_text(encoding="utf-8")
        )
        deferred = {"agents_vault": [], "user_vault": []}
        published_vault = {
            "commit_status": "complete",
            "commit_hashes": ["a" * 40],
            "push_status": "complete",
            "local_head": "a" * 40,
            "remote_head": "a" * 40,
            "clean": False,
            "publication_mode": "own_only",
            "deferred_cleanup": [],
        }
        success = {
            "outcome": "success",
            "phase": "evidence_finalization",
            "daily_pipeline_status": "complete",
            "summary_path": "summary.md",
            "advisory_path": "advisory.md",
            "notification_result": None,
            "agents_vault": published_vault,
            "user_vault": published_vault,
            "publication_mode": {
                "agents_vault": "own_only", "user_vault": "own_only"
            },
            "deferred_cleanup": deferred,
            "evidence_finalization_commit": "b" * 40,
            "evidence_recovery": {
                "target_path": "tasks/standing.md",
                "quarantine_scope": "agents_git_dir",
                "quarantine_root_identity": [1, 1],
                "base_head": "a" * 40,
                "candidate_head": "b" * 40,
                "original_restored": False,
                "original_tombstone": {
                    "directory": ".publication-evidence-original-fixture",
                    "directory_identity": [1, 4],
                    "entry": "artifact",
                    "identity": [1, 2],
                    "sha256": "1" * 64,
                    "size": 10,
                    "mode": 0o100644,
                },
                "candidate": {
                    "identity": [1, 3],
                    "sha256": "2" * 64,
                    "size": 20,
                    "mode": 0o100644,
                },
                "head_updated": True,
                "index_updated": True,
            },
            "next_action": None,
        }
        CANONICAL_MODULE.validate(success, automation_schema, automation_schema)
        for unsafe_target in (
            "/private/task.md",
            "../task.md",
            "tasks/standing\nsecret.md",
        ):
            with self.subTest(unsafe_recovery_target=repr(unsafe_target)):
                unsafe_recovery = json.loads(json.dumps(success))
                unsafe_recovery["evidence_recovery"]["target_path"] = (
                    unsafe_target
                )
                with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
                    CANONICAL_MODULE.validate(
                        unsafe_recovery, automation_schema, automation_schema
                    )
        for unsafe_directory in (
            ".publication-evidence-original/bad",
            ".publication-evidence-original\\bad",
            ".publication-evidence-original\nbad",
            ".publication-evidence-original\x7fbad",
        ):
            with self.subTest(unsafe_tombstone_directory=repr(unsafe_directory)):
                unsafe_recovery = json.loads(json.dumps(success))
                unsafe_recovery["evidence_recovery"]["original_tombstone"][
                    "directory"
                ] = unsafe_directory
                with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
                    CANONICAL_MODULE.validate(
                        unsafe_recovery, automation_schema, automation_schema
                    )
        missing_tombstone = json.loads(json.dumps(success))
        del missing_tombstone["evidence_recovery"]["original_tombstone"]
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(
                missing_tombstone, automation_schema, automation_schema
            )
        invalid_recovery_head = json.loads(json.dumps(success))
        invalid_recovery_head["evidence_recovery"]["candidate_head"] = "short"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(
                invalid_recovery_head, automation_schema, automation_schema
            )
        invalid_success = json.loads(json.dumps(success))
        invalid_success["user_vault"]["commit_hashes"] = []
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(
                invalid_success, automation_schema, automation_schema
            )

        commit_schema = json.loads(
            (SKILL_ROOT / "references" / "publication-commit-result.schema.json")
            .read_text(encoding="utf-8")
        )
        ready_vault = {
            "commit_status": "complete",
            "commit_hashes": ["c" * 40],
            "pre_local_head": "d" * 40,
            "local_head": "c" * 40,
            "pre_dirty_digest": "e" * 64,
            "post_dirty_digest": "f" * 64,
            "clean": False,
            "publication_mode": "own_only",
            "deferred_cleanup": [],
        }
        blocked_vault = {
            "commit_status": "not_started",
            "commit_hashes": [],
            "pre_local_head": "1" * 40,
            "local_head": "1" * 40,
            "pre_dirty_digest": "2" * 64,
            "post_dirty_digest": "2" * 64,
            "clean": False,
            "publication_mode": "blocked",
            "deferred_cleanup": [],
        }
        ready = {
            "outcome": "ready_to_push",
            "phase": "local_commit",
            "daily_pipeline_status": "complete",
            "summary_path": None,
            "advisory_path": "advisory.md",
            "notification_result": None,
            "agents_vault": ready_vault,
            "user_vault": blocked_vault,
            "publication_mode": {
                "agents_vault": "own_only", "user_vault": "blocked"
            },
            "deferred_cleanup": deferred,
            "evidence_finalization_commit": None,
            "next_action": "repair blocked User Vault",
        }
        CANONICAL_MODULE.validate(ready, commit_schema, commit_schema)
        invalid_retry = json.loads(json.dumps(ready))
        invalid_retry["retry_disposition"] = "none"
        invalid_retry["replan_vaults"] = ["agents_vault"]
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(
                invalid_retry, commit_schema, commit_schema
            )
        invalid_ready_retry = json.loads(json.dumps(invalid_retry))
        invalid_ready_retry["retry_disposition"] = "replan"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(
                invalid_ready_retry, commit_schema, commit_schema
            )
        blocked_retry = json.loads(json.dumps(ready))
        blocked_retry.update(
            {
                "outcome": "blocked",
                "summary_path": None,
                "advisory_path": None,
                "agents_vault": blocked_vault,
                "user_vault": blocked_vault,
                "publication_mode": {
                    "agents_vault": "blocked",
                    "user_vault": "blocked",
                },
                "retry_disposition": "replan",
                "replan_vaults": ["user_vault"],
                "next_action": "retry collision-safe User target",
            }
        )
        CANONICAL_MODULE.validate(blocked_retry, commit_schema, commit_schema)
        empty_replan = json.loads(json.dumps(blocked_retry))
        empty_replan["replan_vaults"] = []
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(empty_replan, commit_schema, commit_schema)
        invalid_ready = json.loads(json.dumps(ready))
        invalid_ready["agents_vault"] = blocked_vault
        invalid_ready["publication_mode"]["agents_vault"] = "blocked"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(invalid_ready, commit_schema, commit_schema)

        no_op_ready = json.loads(json.dumps(ready))
        for key in ("agents_vault", "user_vault"):
            no_op_ready[key] = {
                **blocked_vault,
                "commit_status": "not_required",
                "publication_mode": "own_only",
            }
            no_op_ready["publication_mode"][key] = "own_only"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(no_op_ready, commit_schema, commit_schema)

        no_op_success = json.loads(json.dumps(success))
        for key in ("agents_vault", "user_vault"):
            no_op_success[key]["commit_status"] = "not_required"
            no_op_success[key]["commit_hashes"] = []
            no_op_success[key]["push_status"] = "not_required"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(
                no_op_success, automation_schema, automation_schema
            )
    def test_standing_task_snapshot_is_exclusive_and_nofollow(self) -> None:
        staging = self.workdir / "snapshot-staging"
        staging.mkdir()
        source = self.agents / "standing-source.md"
        source.write_text("# Standing task\n", encoding="utf-8")
        runtime = self.workdir / "snapshot-runtime.json"
        runtime.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "standing_task_path": str(source),
                }
            ),
            encoding="utf-8",
        )
        destination = staging / "standing-task.md"
        command = [
            str(SCRIPTS / "stage-standing-task.py"),
            str(runtime),
            str(staging),
        ]
        self.assertEqual(subprocess.run(command, check=False).returncode, 0)
        self.assertEqual(destination.read_text(encoding="utf-8"), "# Standing task\n")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(subprocess.run(command, check=False).returncode, 75)

        destination.unlink()
        linked = self.agents / "standing-link.md"
        linked.symlink_to(source)
        runtime.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "standing_task_path": str(linked),
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(subprocess.run(command, check=False).returncode, 75)
        self.assertFalse(destination.exists())

        outside = self.workdir / "outside-standing"
        outside.mkdir()
        (outside / "task.md").write_text("outside\n", encoding="utf-8")
        linked_parent = self.agents / "linked-parent"
        linked_parent.symlink_to(outside, target_is_directory=True)
        runtime.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "standing_task_path": str(linked_parent / "task.md"),
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(subprocess.run(command, check=False).returncode, 75)
        self.assertFalse(destination.exists())

    def test_authorization_task_snapshot_uses_distinct_destination(self) -> None:
        review_input = self.workdir / "review-input"
        review_input.mkdir()
        source = self.agents / "authorization-source.md"
        source.write_text("# Approved authorization\n", encoding="utf-8")
        pinned_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        runtime = self.workdir / "authorization-runtime.json"
        runtime.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "authorization_task_path": str(source),
                    "authorization_task_sha256": pinned_digest,
                }
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                str(SCRIPTS / "stage-standing-task.py"),
                str(runtime),
                str(review_input),
                "authorization",
            ],
            check=False,
        )
        destination = review_input / "authorization-task.md"
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(destination.read_text(encoding="utf-8"), "# Approved authorization\n")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

        destination.unlink()
        source.write_text("# Changed after resolver\n", encoding="utf-8")
        self.assertEqual(
            subprocess.run(
                [
                    str(SCRIPTS / "stage-standing-task.py"),
                    str(runtime),
                    str(review_input),
                    "authorization",
                ],
                check=False,
            ).returncode,
            75,
        )
        self.assertFalse(destination.exists())

    def test_standing_task_snapshot_retries_only_transient_file_provider_locks(self) -> None:
        content = b"# Approved authorization\n"
        transient = OSError(
            STANDING_STAGER_MODULE.errno.EDEADLK,
            "Resource deadlock avoided",
        )
        with (
            mock.patch.object(
                STANDING_STAGER_MODULE,
                "read_regular_beneath",
                side_effect=[transient, transient, content],
            ) as reader,
            mock.patch.object(STANDING_STAGER_MODULE.time, "sleep") as sleep,
        ):
            observed = STANDING_STAGER_MODULE.read_regular_beneath_with_retry(
                self.agents,
                Path("authorization-source.md"),
            )
        self.assertEqual(observed, content)
        self.assertEqual(reader.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

        permanent = OSError(STANDING_STAGER_MODULE.errno.EACCES, "denied")
        with (
            mock.patch.object(
                STANDING_STAGER_MODULE,
                "read_regular_beneath",
                side_effect=permanent,
            ) as reader,
            mock.patch.object(STANDING_STAGER_MODULE.time, "sleep") as sleep,
            self.assertRaises(OSError),
        ):
            STANDING_STAGER_MODULE.read_regular_beneath_with_retry(
                self.agents,
                Path("authorization-source.md"),
            )
        self.assertEqual(reader.call_count, 1)
        sleep.assert_not_called()

    def test_standing_task_snapshot_retries_within_read_identity_churn(self) -> None:
        """A provider-only identity refresh gets a fresh stable descriptor read."""
        content = b"# Stable standing task\n"
        transient = STANDING_STAGER_MODULE.SnapshotError(
            "standing task changed while being read"
        )
        with (
            mock.patch.object(
                STANDING_STAGER_MODULE,
                "read_regular_beneath",
                side_effect=[transient, transient, content],
            ) as reader,
            mock.patch.object(STANDING_STAGER_MODULE.time, "sleep") as sleep,
        ):
            observed = STANDING_STAGER_MODULE.read_regular_beneath_with_retry(
                self.agents,
                Path("standing-source.md"),
            )
        self.assertEqual(observed, content)
        self.assertEqual(reader.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

        permanent = STANDING_STAGER_MODULE.SnapshotError(
            "standing task is not UTF-8"
        )
        with (
            mock.patch.object(
                STANDING_STAGER_MODULE,
                "read_regular_beneath",
                side_effect=permanent,
            ) as reader,
            mock.patch.object(STANDING_STAGER_MODULE.time, "sleep") as sleep,
            self.assertRaises(STANDING_STAGER_MODULE.SnapshotError),
        ):
            STANDING_STAGER_MODULE.read_regular_beneath_with_retry(
                self.agents,
                Path("standing-source.md"),
            )
        self.assertEqual(reader.call_count, 1)
        sleep.assert_not_called()

    def test_standing_task_snapshot_identity_churn_exhausts_closed(self) -> None:
        """Continuous mutation still fails closed after the bounded retry window."""
        transient = STANDING_STAGER_MODULE.SnapshotError(
            "standing task changed while being read"
        )
        with (
            mock.patch.object(
                STANDING_STAGER_MODULE,
                "read_regular_beneath",
                side_effect=transient,
            ) as reader,
            mock.patch.object(STANDING_STAGER_MODULE.time, "sleep") as sleep,
            self.assertRaisesRegex(
                STANDING_STAGER_MODULE.SnapshotError,
                "standing task changed while being read",
            ),
        ):
            STANDING_STAGER_MODULE.read_regular_beneath_with_retry(
                self.agents,
                Path("standing-source.md"),
            )
        self.assertEqual(reader.call_count, STANDING_STAGER_MODULE.SNAPSHOT_RETRY_ATTEMPTS)
        self.assertEqual(
            sleep.call_count,
            STANDING_STAGER_MODULE.SNAPSHOT_RETRY_ATTEMPTS - 1,
        )

    def test_publication_validator_hashes_authorization_snapshot_nofollow(self) -> None:
        snapshot = self.workdir / "authorization-task.md"
        snapshot.write_text("approved\n", encoding="utf-8")
        expected = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        self.assertEqual(REVIEW_MODULE.sha256_regular_nofollow(snapshot), expected)

        link = self.workdir / "authorization-link.md"
        link.symlink_to(snapshot)
        with self.assertRaises(OSError):
            REVIEW_MODULE.sha256_regular_nofollow(link)

    def test_dirty_review_inputs_are_bound_to_captured_git_blobs(self) -> None:
        git_environment = DIRTY_STAGER_MODULE.clean_git_environment()
        self.assertEqual(git_environment["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(git_environment["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(git_environment["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(git_environment["LC_ALL"], "C")
        self.assertEqual(
            DIFF_MODULE.clean_git_environment()["GIT_LITERAL_PATHSPECS"], "1"
        )
        for environment in (
            PUSH_MODULE.clean_git_environment(),
            EVIDENCE_MODULE.clean_git_environment(),
        ):
            self.assertEqual(environment["GIT_CONFIG_COUNT"], "3")
            self.assertEqual(environment["GIT_CONFIG_KEY_0"], "core.fsmonitor")
            self.assertEqual(environment["GIT_CONFIG_VALUE_0"], "false")
            self.assertEqual(environment["GIT_CONFIG_KEY_1"], "core.trustctime")
            self.assertEqual(environment["GIT_CONFIG_VALUE_1"], "false")
            self.assertEqual(environment["GIT_CONFIG_KEY_2"], "core.checkStat")
            self.assertEqual(environment["GIT_CONFIG_VALUE_2"], "minimal")
        heads = {
            "agents": create_empty_base(self.agents),
            "user": create_empty_base(self.user),
        }
        content = b"captured dirty bytes\n"
        oid = subprocess.run(
            ["git", "-C", str(self.agents), "hash-object", "--stdin"],
            input=content,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        agents_git_dir = subprocess.check_output(
            ["git", "-C", str(self.agents), "rev-parse", "--absolute-git-dir"],
            text=True,
        ).strip()
        user_git_dir = subprocess.check_output(
            ["git", "-C", str(self.user), "rev-parse", "--absolute-git-dir"],
            text=True,
        ).strip()
        runtime = self.workdir / "dirty-runtime.json"
        runtime.write_text(
            json.dumps(
                {
                    "agents_git_dir": agents_git_dir,
                    "user_git_dir": user_git_dir,
                    "agents_vault_root": str(self.agents),
                    "user_vault_root": str(self.user),
                    "gitleaks_bin": str(self.fake_gitleaks),
                }
            ),
            encoding="utf-8",
        )
        pre = self.workdir / "dirty-pre.json"
        pre.write_text(
            json.dumps(
                {
                    "agents_vault": {
                        "local_head": heads["agents"],
                        "local_commits": [],
                        "dirty_entries": [
                            {
                                "path": "tasks/dirty.md",
                                "git_blob_oid": oid,
                                "mode": "100644",
                            }
                        ]
                    },
                    "user_vault": {
                        "local_head": heads["user"],
                        "local_commits": [],
                        "dirty_entries": [],
                    },
                }
            ),
            encoding="utf-8",
        )
        review_input = self.workdir / "dirty-review-input"
        review_input.mkdir()
        (self.agents / "tasks" / "dirty.md").write_bytes(content)
        command = [
            str(SCRIPTS / "stage-dirty-review-inputs.py"),
            str(runtime),
            str(pre),
            str(review_input),
        ]
        self.assertEqual(subprocess.run(command, check=False).returncode, 0)
        self.assertEqual(review_input.stat().st_mode & 0o777, 0o000)
        review_input.chmod(0o700)
        snapshot = review_input / "dirty-snapshots" / "agents" / "0000.blob"
        self.assertEqual(snapshot.read_bytes(), content)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        manifest_path = review_input / "dirty-snapshots.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["vaults"]["agents_vault"][0]["git_blob_oid"], oid)
        context = {
            "dirty_snapshot_manifest_file": str(manifest_path),
            "dirty_snapshot_manifest_sha256": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
        }
        REVIEW_MODULE.validate_dirty_snapshots(context, json.loads(pre.read_text()))

        snapshot.write_text("substituted\n", encoding="utf-8")
        with self.assertRaises(REVIEW_MODULE.ReviewError):
            REVIEW_MODULE.validate_dirty_snapshots(context, json.loads(pre.read_text()))

        snapshot.write_bytes(content)
        agents_directory = snapshot.parent
        real_agents_directory = agents_directory.with_name("real-agents")
        agents_directory.rename(real_agents_directory)
        agents_directory.symlink_to(real_agents_directory, target_is_directory=True)
        with self.assertRaises(OSError):
            REVIEW_MODULE.validate_dirty_snapshots(context, json.loads(pre.read_text()))

    def test_residual_guard_defers_added_home_path_but_allows_historical_path(
        self,
    ) -> None:
        """Constrain unsafe residuals before review without flagging unchanged history."""
        for repo in (self.agents, self.user):
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Fixture"],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(repo), "config", "user.email",
                    "fixture@example.invalid",
                ],
                check=True,
            )
        historical = self.agents / "historical.md"
        historical.write_text(
            f"historical evidence: {Path.home()}/old.log\n"
            f"historical CR evidence:\r{Path.home()}/old-cr.log\n",
            encoding="utf-8",
        )
        peer = self.user / "peer.md"
        peer.write_text("peer base\n", encoding="utf-8")
        for repo, path in ((self.agents, historical), (self.user, peer)):
            subprocess.run(["git", "-C", str(repo), "add", path.name], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "base"],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
                check=True,
            )

        historical.write_bytes(historical.read_bytes() + b"safe reviewed update\n")
        unsafe = self.agents / "unsafe.md"
        unsafe.write_text(
            f"++ new machine evidence:\r{Path.home()}/new.log\n", encoding="utf-8"
        )
        unsafe_binary = self.agents / "unsafe.bin"
        unsafe_binary.write_bytes(b"\0++" + os.fsencode(str(Path.home())) + b"/binary")
        peer.write_text("peer base\npeer safe update\n", encoding="utf-8")
        pre = {
            "agents_vault": CAPTURE_MODULE.capture(str(self.agents)),
            "user_vault": CAPTURE_MODULE.capture(str(self.user)),
        }
        runtime = {
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(self.user / ".git"),
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(self.user),
            "gitleaks_bin": str(self.fake_gitleaks),
        }
        destination = self.workdir / "guarded-dirty-review"
        destination.mkdir()
        manifest = DIRTY_STAGER_MODULE.materialize(runtime, pre, destination)
        agents_entries = {
            entry["path"]: entry for entry in manifest["vaults"]["agents_vault"]
        }
        self.assertEqual(
            agents_entries["historical.md"]["materialization_status"], "available"
        )
        self.assertEqual(
            agents_entries["unsafe.md"]["materialization_status"], "deferred"
        )
        self.assertEqual(
            agents_entries["unsafe.md"]["materialization_reason"],
            "dirty_entry_added_machine_home_path",
        )
        self.assertIsNone(agents_entries["unsafe.md"]["snapshot"])
        self.assertEqual(
            agents_entries["unsafe.bin"]["materialization_status"], "deferred"
        )
        self.assertEqual(
            agents_entries["unsafe.bin"]["materialization_reason"],
            "dirty_entry_added_machine_home_path",
        )
        self.assertIsNone(agents_entries["unsafe.bin"]["snapshot"])
        self.assertEqual(
            manifest["vaults"]["user_vault"][0]["materialization_status"],
            "available",
        )
        manifest_path = destination / "dirty-snapshots.json"
        REVIEW_MODULE.validate_dirty_snapshots(
            {
                "dirty_snapshot_manifest_file": str(manifest_path),
                "dirty_snapshot_manifest_sha256": hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest(),
            },
            pre,
        )

    def test_residual_guard_defers_gitleaks_rejection(self) -> None:
        """Convert a deterministic residual secret rejection into deferred input."""
        content = b"++prefix\rnew residual secret fixture\n"
        oid = subprocess.run(
            ["git", "-C", str(self.agents), "hash-object", "--stdin"],
            input=content,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        path = self.agents / "secret.md"
        path.write_bytes(content)
        head = subprocess.run(
            ["git", "-C", str(self.agents), "mktree"],
            input="",
            text=True,
            check=True,
            capture_output=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "-C", str(self.agents), "commit-tree", head],
            input="empty\n",
            text=True,
            check=True,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "Fixture",
                "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "Fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            },
        ).stdout.strip()
        pre = {
            "agents_vault": {
                "local_head": commit,
                "dirty_entries": [
                    {"path": "secret.md", "git_blob_oid": oid, "mode": "100644"}
                ],
                "local_commits": [],
            },
            "user_vault": {"dirty_entries": [], "local_commits": []},
        }
        runtime = {
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(self.user / ".git"),
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(self.user),
            "gitleaks_bin": str(self.fake_gitleaks),
        }
        destination = self.workdir / "secret-guard-review"
        destination.mkdir()
        with mock.patch.object(
            DIRTY_STAGER_MODULE, "gitleaks_rejects", return_value=True
        ) as scanner:
            manifest = DIRTY_STAGER_MODULE.materialize(runtime, pre, destination)
        self.assertIn(
            b"++prefix\rnew residual secret fixture", scanner.call_args.args[1]
        )
        entry = manifest["vaults"]["agents_vault"][0]
        self.assertEqual(entry["materialization_status"], "deferred")
        self.assertEqual(
            entry["materialization_reason"], "dirty_entry_secret_scan_rejected"
        )

    def test_gitleaks_v8_invocation_contracts_are_exact(self) -> None:
        """Pin the reviewed stdin, staged-index, and commit-range CLI shapes."""
        completed_bytes = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

        def assert_bound_config(arguments: list[str], **kwargs: object):
            self.assertIn("--config", arguments)
            pass_fds = kwargs.get("pass_fds")
            self.assertIsInstance(pass_fds, tuple)
            self.assertEqual(len(pass_fds), 1)
            descriptor = pass_fds[0]
            config_index = arguments.index("--config") + 1
            self.assertEqual(arguments[config_index], f"/dev/fd/{descriptor}")
            self.assertEqual(
                hashlib.sha256(os.pread(descriptor, 4096, 0)).hexdigest(),
                TRUSTED_GITLEAKS_MODULE.EXPECTED_CONFIG_SHA256,
            )
            return completed_bytes

        with mock.patch.object(
            DIRTY_STAGER_MODULE, "run_local_command", side_effect=assert_bound_config
        ) as stdin_run:
            self.assertFalse(
                DIRTY_STAGER_MODULE.gitleaks_rejects(
                    str(self.fake_gitleaks), b"candidate additions\n"
                )
            )
        stdin_arguments = stdin_run.call_args.args[0]
        self.assertEqual(stdin_arguments[0], str(self.fake_gitleaks))
        self.assertEqual(stdin_arguments[-1], "stdin")
        self.assertEqual(stdin_run.call_args.kwargs["cwd"], "/")
        self.assertEqual(
            stdin_run.call_args.kwargs["input"], b"candidate additions\n"
        )

        with mock.patch.object(
            COMMITTER_MODULE, "run_local_command", side_effect=assert_bound_config
        ) as staged_run, mock.patch.object(
            COMMITTER_MODULE,
            "git_bytes",
            return_value=subprocess.CompletedProcess([], 0, stdout=b"", stderr=b""),
        ), mock.patch.object(
            COMMITTER_MODULE,
            "git",
            return_value=subprocess.CompletedProcess([], 0, stdout=b"", stderr=b""),
        ):
            COMMITTER_MODULE.scan_staged(
                str(self.fake_gitleaks),
                str(self.user),
                str(self.user / ".git"),
                ["summary.md"],
                str(self.user / ".git" / "review-index"),
            )
        self.assertEqual(
            staged_run.call_args_list[0].args[0][-3:],
            ["git", "--staged", str(self.user)],
        )
        self.assertEqual(staged_run.call_args_list[0].kwargs["cwd"], "/")

        def assert_bound_config_text(arguments: list[str], **kwargs: object):
            assert_bound_config(arguments, **kwargs)
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        with mock.patch.object(
            PUSH_MODULE, "run_local_command", side_effect=assert_bound_config_text
        ) as history_run:
            PUSH_MODULE.scan_commits(
                str(self.fake_gitleaks), str(self.user), "a" * 40, "b" * 40
            )
        self.assertEqual(
            history_run.call_args.args[0][-4:],
            [
                "git",
                "--log-opts",
                f"{'a' * 40}..{'b' * 40}",
                str(self.user),
            ],
        )
        self.assertEqual(history_run.call_args.kwargs["cwd"], "/")

        with mock.patch.object(
            FINALIZER_MODULE,
            "run_local_command",
            side_effect=assert_bound_config_text,
        ) as evidence_run:
            FINALIZER_MODULE.scan_staged(
                str(self.fake_gitleaks),
                str(self.agents),
                str(self.agents / ".git" / "review-index"),
            )
        self.assertEqual(
            evidence_run.call_args.args[0][-3:],
            ["git", "--staged", str(self.agents)],
        )

    @unittest.skipUnless(shutil.which("gitleaks"), "gitleaks is not installed")
    def test_trusted_gitleaks_config_overrides_vault_local_rules(self) -> None:
        """Detect a fixture leak even when the Vault config disables defaults."""
        repo = self.workdir / "gitleaks-local-config"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True
        )
        subprocess.run(
            [
                "git", "-C", str(repo), "config", "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        (repo / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True
        )
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        (repo / ".gitleaks.toml").write_text(
            "title = \"untrusted weak rules\"\n\n"
            "[[rules]]\n"
            "id = \"never-match\"\n"
            "regex = '''THIS_PATTERN_DOES_NOT_MATCH'''\n",
            encoding="utf-8",
        )
        fixture_token = "xoxb-" + "123456789012-123456789012-abcdefghijklmnopqrstuvwx"
        (repo / "secret.md").write_text(
            f"token = {fixture_token}\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "fixture"],
            check=True,
        )
        after = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        with self.assertRaisesRegex(PUSH_MODULE.PushError, "gitleaks rejected"):
            PUSH_MODULE.scan_commits(
                str(shutil.which("gitleaks")), str(repo), before, after
            )
        self.assertEqual(
            hashlib.sha256(
                (SCRIPTS / "gitleaks-default.toml").read_bytes()
            ).hexdigest(),
            TRUSTED_GITLEAKS_MODULE.EXPECTED_CONFIG_SHA256,
        )

    @unittest.skipUnless(shutil.which("gitleaks"), "gitleaks is not installed")
    def test_trusted_gitleaks_config_swap_cannot_weaken_active_scan(self) -> None:
        """Keep the validated config on an inherited fd across pathname replacement."""
        trusted_root = self.workdir / "trusted-config-swap"
        trusted_root.mkdir()
        trusted_module = trusted_root / "trusted_gitleaks.py"
        trusted_module.write_text("fixture module path\n", encoding="utf-8")
        trusted_config = trusted_root / "gitleaks-default.toml"
        shutil.copy2(SCRIPTS / "gitleaks-default.toml", trusted_config)
        weak_config = trusted_root / "weak.toml"
        weak_config.write_text(
            "title = \"weak replacement\"\n\n"
            "[[rules]]\n"
            "id = \"never-match\"\n"
            "regex = '''THIS_PATTERN_DOES_NOT_MATCH'''\n",
            encoding="utf-8",
        )
        real_run = DIRTY_STAGER_MODULE.run_local_command

        def replace_then_run(arguments: list[str], **kwargs: object):
            os.replace(weak_config, trusted_config)
            return real_run(arguments, **kwargs)

        fixture_token = "xoxb-" + "123456789012-123456789012-abcdefghijklmnopqrstuvwx"
        with mock.patch.object(
            TRUSTED_GITLEAKS_MODULE, "__file__", str(trusted_module)
        ), mock.patch.object(
            DIRTY_STAGER_MODULE,
            "run_local_command",
            side_effect=replace_then_run,
        ):
            self.assertTrue(
                DIRTY_STAGER_MODULE.gitleaks_rejects(
                    str(shutil.which("gitleaks")),
                    f"token = {fixture_token}\n".encode(),
                )
            )
        self.assertIn("weak replacement", trusted_config.read_text(encoding="utf-8"))

    def test_network_git_keeps_process_cwd_outside_vault(self) -> None:
        """Use an isolated Git directory rather than the Vault control plane."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="a" * 40 + "\trefs/heads/main\n", stderr=""
        )
        for module in (PUSH_MODULE, FINALIZER_MODULE):
            with mock.patch.object(
                module, "run_transport", return_value=completed
            ) as run:
                remote = module.remote_head(
                    "/vault/worktree", "ssh://git@example.invalid/repo", "/local/gitdir"
                )
            self.assertEqual(remote, "a" * 40)
            command = run.call_args.args
            self.assertEqual(
                command[:2],
                (
                    "/local/gitdir",
                    "ls-remote",
                ),
            )
            self.assertNotIn("/vault/worktree", command)

    def test_transport_timeout_kills_the_process_group(self) -> None:
        """Bound every network Git call and reap its complete process group."""
        transport = object.__new__(TRANSPORT_MODULE.IsolatedGitTransport)
        transport.git_dir = Path("/isolated/transport.git")
        transport.environment = {}
        transport.timeout = 1
        process = mock.MagicMock()
        process.pid = 4242
        process.returncode = -9
        process.communicate.side_effect = subprocess.TimeoutExpired(["git", "push"], 1)
        process.wait.return_value = -9
        with mock.patch.object(
            TRANSPORT_MODULE.subprocess, "Popen", return_value=process
        ) as popen, mock.patch.object(
            TRANSPORT_MODULE.os, "killpg"
        ) as killpg:
            with self.assertRaisesRegex(
                TRANSPORT_MODULE.TransportError, "exceeded 1 second"
            ):
                transport.run("push", "remote", "a" * 40 + ":refs/heads/main")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertIn("core.fsmonitor=false", popen.call_args.args[0])
        self.assertIn("credential.helper=", popen.call_args.args[0])
        killpg.assert_called_once_with(4242, TRANSPORT_MODULE.signal.SIGKILL)
        process.wait.assert_called_once_with(
            timeout=TRANSPORT_MODULE.PROCESS_CLEANUP_TIMEOUT_SECONDS
        )

    def test_transport_timeout_has_a_bounded_reap_failure(self) -> None:
        """Never block forever when a killed transport cannot be reaped."""
        transport = object.__new__(TRANSPORT_MODULE.IsolatedGitTransport)
        transport.git_dir = Path("/isolated/transport.git")
        transport.environment = {}
        transport.timeout = 1
        process = mock.MagicMock()
        process.pid = 4242
        process.communicate.side_effect = subprocess.TimeoutExpired(["git"], 1)
        process.wait.side_effect = [
            subprocess.TimeoutExpired(["git"], 5),
            subprocess.TimeoutExpired(["git"], 5),
        ]
        with mock.patch.object(
            TRANSPORT_MODULE.subprocess, "Popen", return_value=process
        ), mock.patch.object(TRANSPORT_MODULE.os, "killpg"):
            with self.assertRaisesRegex(
                TRANSPORT_MODULE.TransportError, "could not be reaped"
            ):
                transport.run("push", "remote", "a" * 40 + ":refs/heads/main")
        self.assertEqual(process.wait.call_count, 2)
        process.kill.assert_called_once()

    def test_transport_unexpected_exception_kills_reaps_and_closes(self) -> None:
        """Clean up transport children on every communicate failure, not just timeout."""
        transport = object.__new__(TRANSPORT_MODULE.IsolatedGitTransport)
        transport.git_dir = Path("/isolated/transport.git")
        transport.environment = {}
        transport.timeout = 1
        process = mock.MagicMock()
        process.pid = 4242
        process.stdin = mock.MagicMock()
        process.stdout = mock.MagicMock()
        process.stderr = mock.MagicMock()
        process.communicate.side_effect = RuntimeError("fixture transport failure")
        with mock.patch.object(
            TRANSPORT_MODULE.subprocess, "Popen", return_value=process
        ), mock.patch.object(TRANSPORT_MODULE.os, "killpg") as killpg:
            with self.assertRaisesRegex(RuntimeError, "fixture transport failure"):
                transport.run("ls-remote", "ssh://example.invalid/repo")
        killpg.assert_called_once_with(4242, TRANSPORT_MODULE.signal.SIGKILL)
        process.wait.assert_called_once_with(
            timeout=TRANSPORT_MODULE.PROCESS_CLEANUP_TIMEOUT_SECONDS
        )
        process.stdin.close.assert_called_once_with()
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()

    def test_local_runner_timeout_kills_descendant_process_group(self) -> None:
        """Kill a spawned descendant, not only the timed-out direct helper."""
        pid_file = self.workdir / "local-runner-descendant.pid"
        program = (
            "import subprocess,time; from pathlib import Path; "
            "child=subprocess.Popen(['/bin/sleep','30']); "
            f"Path({str(pid_file)!r}).write_text(str(child.pid)); "
            "time.sleep(30)"
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            TRANSPORT_MODULE.run_local_command(
                [sys.executable, "-c", program], timeout=1, capture_output=True
            )
        child_pid = int(pid_file.read_text())
        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("timed-out local helper descendant remained alive")

    def test_local_runner_unexpected_exception_kills_reaps_and_closes(self) -> None:
        """Clean up the private process group before propagating helper errors."""
        process = mock.MagicMock()
        process.pid = 4242
        process.stdin = mock.MagicMock()
        process.stdout = mock.MagicMock()
        process.stderr = mock.MagicMock()
        process.communicate.side_effect = RuntimeError("fixture communication failure")
        with mock.patch.object(
            TRANSPORT_MODULE.subprocess, "Popen", return_value=process
        ), mock.patch.object(TRANSPORT_MODULE.os, "killpg") as killpg:
            with self.assertRaisesRegex(RuntimeError, "fixture communication failure"):
                TRANSPORT_MODULE.run_local_command(
                    ["/fixture/helper"], capture_output=True
                )
        killpg.assert_called_once_with(4242, TRANSPORT_MODULE.signal.SIGKILL)
        process.wait.assert_called_once_with(
            timeout=TRANSPORT_MODULE.PROCESS_CLEANUP_TIMEOUT_SECONDS
        )
        process.stdin.close.assert_called_once_with()
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()

    def test_materializer_bounded_read_kills_descendant_on_deadline(self) -> None:
        """Apply the same wall deadline and process-group cleanup to review input."""
        pid_file = self.workdir / "materializer-descendant.pid"
        program = (
            "import subprocess,time; from pathlib import Path; "
            "child=subprocess.Popen(['/bin/sleep','30']); "
            f"Path({str(pid_file)!r}).write_text(str(child.pid)); "
            "time.sleep(30)"
        )
        with mock.patch.object(
            DIRTY_STAGER_MODULE, "LOCAL_COMMAND_TIMEOUT_SECONDS", 1
        ):
            with self.assertRaisesRegex(
                DIRTY_STAGER_MODULE.DirtySnapshotError, "deadline"
            ):
                DIRTY_STAGER_MODULE.run_bounded(
                    [sys.executable, "-c", program], 1024
                )
        child_pid = int(pid_file.read_text())
        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("timed-out review materializer descendant remained alive")

    def test_evidence_reader_kills_group_after_leader_exits(self) -> None:
        """Kill pipe-holding descendants even when the direct process already exited."""
        pid_file = self.workdir / "evidence-descendant.pid"
        program = (
            "import subprocess; from pathlib import Path; "
            "child=subprocess.Popen(['/bin/sleep','30']); "
            f"Path({str(pid_file)!r}).write_text(str(child.pid))"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", program],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        with mock.patch.object(
            EVIDENCE_MODULE.subprocess, "Popen", return_value=process
        ), mock.patch.object(EVIDENCE_MODULE, "LOCAL_COMMAND_TIMEOUT_SECONDS", 1):
            with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "deadline"):
                EVIDENCE_MODULE.git_bytes("/vault", "/vault/.git", "show", "HEAD:x")
        child_pid = int(pid_file.read_text())
        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("pipe-holding evidence descendant remained alive")

    def test_local_git_helpers_disable_repo_fsmonitor(self) -> None:
        """Do not execute repo-local fsmonitor through Git or pinned gitleaks."""
        tracked = self.user / "initial.md"
        tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.user), "add", "initial.md"], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.user),
                "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid",
                "commit", "-q", "-m", "base",
            ],
            check=True,
        )
        marker = self.workdir / "fsmonitor-executed"
        monitor = self.workdir / "malicious-fsmonitor.sh"
        monitor.write_text(
            f"#!/bin/sh\n/usr/bin/touch '{marker}'\nexit 1\n",
            encoding="utf-8",
        )
        monitor.chmod(0o755)
        subprocess.run(
            [
                "git", "-C", str(self.user), "config", "--local",
                "core.fsmonitor", str(monitor),
            ],
            check=True,
        )
        PUSH_MODULE.dirty_digest(str(self.user))
        tracked.write_text("changed\n", encoding="utf-8")
        FINALIZER_MODULE.diff_digest(str(self.user), "initial.md")

        gitleaks_git = self.workdir / "gitleaks-that-invokes-git"
        gitleaks_git.write_text(
            "#!/bin/sh\n"
            "[ -z \"${GITLEAKS_CONFIG+x}\" ] || exit 99\n"
            "repo=\n"
            "for argument in \"$@\"; do repo=$argument; done\n"
            "/usr/bin/git -C \"$repo\" status --porcelain >/dev/null\n",
            encoding="utf-8",
        )
        gitleaks_git.chmod(0o755)
        index_file = str(self.user / ".git" / "index")
        with mock.patch.dict(
            os.environ, {"GITLEAKS_CONFIG": "/attacker/gitleaks.toml"}
        ):
            COMMITTER_MODULE.scan_staged(
                str(gitleaks_git), str(self.user), str(self.user / ".git"),
                ["initial.md"], index_file,
            )
            FINALIZER_MODULE.scan_staged(
                str(gitleaks_git), str(self.user), index_file
            )
            head = subprocess.check_output(
                ["git", "-C", str(self.user), "rev-parse", "HEAD"], text=True
            ).strip()
            PUSH_MODULE.scan_commits(str(gitleaks_git), str(self.user), head, head)
        self.assertFalse(marker.exists())

    def test_unmaterializable_dirty_entry_defers_only_its_vault(self) -> None:
        """Keep a symlink residual inert while another Vault remains reviewable."""
        agents_head = create_empty_base(self.agents)
        user_head = create_empty_base(self.user)
        agents_link = self.agents / "unsafe-link"
        agents_link.symlink_to("outside-target")
        user_file = self.user / "reviewable.md"
        user_file.write_text("reviewable\n", encoding="utf-8")
        link_oid = subprocess.run(
            ["git", "-C", str(self.agents), "hash-object", "--stdin"],
            input=b"outside-target",
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        user_oid = subprocess.run(
            ["git", "-C", str(self.user), "hash-object", "--stdin"],
            input=user_file.read_bytes(),
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        runtime = self.workdir / "deferred-runtime.json"
        runtime.write_text(
            json.dumps(
                {
                    "agents_git_dir": str(self.agents / ".git"),
                    "user_git_dir": str(self.user / ".git"),
                    "agents_vault_root": str(self.agents),
                    "user_vault_root": str(self.user),
                    "gitleaks_bin": str(self.fake_gitleaks),
                }
            ),
            encoding="utf-8",
        )
        state_digest = hashlib.sha256(b"").hexdigest()
        pre_value = {
            "agents_vault": {
                "local_head": agents_head,
                "repo_root": str(self.agents),
                "history_relation": "equal",
                "local_commits": [],
                "dirty_paths": ["unsafe-link"],
                "dirty_entries": [
                    {"path": "unsafe-link", "git_blob_oid": link_oid, "mode": "120000"}
                ],
                "diff_snapshot_sha256": state_digest,
                "history_snapshot_sha256": state_digest,
            },
            "user_vault": {
                "local_head": user_head,
                "repo_root": str(self.user),
                "history_relation": "equal",
                "local_commits": [],
                "dirty_paths": ["reviewable.md"],
                "dirty_entries": [
                    {"path": "reviewable.md", "git_blob_oid": user_oid, "mode": "100644"}
                ],
                "diff_snapshot_sha256": state_digest,
                "history_snapshot_sha256": state_digest,
            },
        }
        pre = self.workdir / "deferred-pre.json"
        pre.write_text(json.dumps(pre_value), encoding="utf-8")
        destination = self.workdir / "deferred-review"
        destination.mkdir()
        self.assertEqual(
            subprocess.run(
                [
                    str(SCRIPTS / "stage-dirty-review-inputs.py"),
                    str(runtime), str(pre), str(destination),
                ],
                check=False,
            ).returncode,
            0,
        )
        destination.chmod(0o700)
        materialized = json.loads(
            (destination / "dirty-snapshots.json").read_text(encoding="utf-8")
        )
        agents_materialized = materialized["vaults"]["agents_vault"]
        user_materialized = materialized["vaults"]["user_vault"]
        self.assertEqual(agents_materialized[0]["materialization_status"], "deferred")
        self.assertEqual(user_materialized[0]["materialization_status"], "available")

        artifact = self.agents / "artifact.md"
        common = {
            "repo_root": str(self.agents),
            "task_id": "TSK-AUTH",
            "core_review_status": "quality_ok",
            "approved_diff_snapshot_sha256": state_digest,
            "approved_existing_commits": [],
            "reviewed_artifacts": [
                {"role": "agents_security_advisory", "source_sha256": "a" * 64, "target_path": "artifact.md"}
            ],
            "validation_evidence": {
                "file_guard": "passed",
                "secret_scan": "passed",
                "secret_scan_tool": "gitleaks",
                "secret_scan_tool_version": "fixture",
                "reviewed_snapshot_sha256": state_digest,
                "reviewed_history_sha256": state_digest,
            },
            "review_or_validation_status": "quality_ok",
            "evidence_finalization": None,
        }
        own_only = {
            **common,
            "publication_mode": "own_only",
            "residual_review_status": "deferred",
            "owned_paths": ["artifact.md"],
            "excluded_paths": ["unsafe-link"],
            "deferred_cleanup": [
                {"path": "unsafe-link", "reason": "snapshot unavailable"}
            ],
            "approved_dirty_entries": [],
            "commit_required": True,
            "unrelated_dirty_paths": ["unsafe-link"],
            "commit_groups": [{"message": "publish", "paths": ["artifact.md"]}],
        }
        REVIEW_MODULE.validate_manifest(
            own_only,
            pre_value["agents_vault"],
            str(self.agents),
            "TSK-AUTH",
            {
                "role": "agents_security_advisory",
                "source_sha256": "a" * 64,
                "target_path": str(artifact),
            },
            None,
            "fixture",
            {"required_mode": "sweep"},
            agents_materialized,
            [],
        )
        residual_blocked = {
            **own_only,
            "publication_mode": "blocked",
            "residual_review_status": "blocked",
            "commit_required": False,
            "commit_groups": [],
        }
        REVIEW_MODULE.validate_manifest(
            residual_blocked,
            pre_value["agents_vault"],
            str(self.agents),
            "TSK-AUTH",
            {
                "role": "agents_security_advisory",
                "source_sha256": "a" * 64,
                "target_path": str(artifact),
            },
            None,
            "fixture",
            {"required_mode": "sweep"},
            agents_materialized,
            [],
        )
        invalid_history_deferred = json.loads(json.dumps(residual_blocked))
        invalid_history_deferred["deferred_cleanup"].append(
            {
                "path": "local-ahead-only.md",
                "reason": "unsafe local history belongs in next_action",
            }
        )
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError,
            "blocked manifest does not preserve the exact non-actionable scope",
        ):
            REVIEW_MODULE.validate_manifest(
                invalid_history_deferred,
                pre_value["agents_vault"],
                str(self.agents),
                "TSK-AUTH",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "a" * 64,
                    "target_path": str(artifact),
                },
                None,
                "fixture",
                {"required_mode": "sweep"},
                agents_materialized,
                [],
            )
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "core review status fields disagree"
        ):
            REVIEW_MODULE.validate_manifest(
                {**residual_blocked, "review_or_validation_status": "blocked"},
                pre_value["agents_vault"],
                str(self.agents),
                "TSK-AUTH",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "a" * 64,
                    "target_path": str(artifact),
                },
                None,
                "fixture",
                {"required_mode": "sweep"},
                agents_materialized,
                [],
            )
        sweep = {
            **own_only,
            "publication_mode": "sweep",
            "residual_review_status": "quality_ok",
            "owned_paths": ["artifact.md", "unsafe-link"],
            "excluded_paths": [],
            "deferred_cleanup": [],
            "approved_dirty_entries": pre_value["agents_vault"]["dirty_entries"],
            "unrelated_dirty_paths": [],
            "commit_groups": [
                {"message": "publish", "paths": ["artifact.md", "unsafe-link"]}
            ],
        }
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "unreviewable dirty residual"
        ):
            REVIEW_MODULE.validate_manifest(
                sweep,
                pre_value["agents_vault"],
                str(self.agents),
                "TSK-AUTH",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "a" * 64,
                    "target_path": str(artifact),
                },
                None,
                "fixture",
                {"required_mode": "sweep"},
                agents_materialized,
                [],
            )

    def test_oversize_dirty_entry_is_deferred_without_suppressing_peer(self) -> None:
        """Apply snapshot size limits per Vault instead of aborting both reviews."""
        agents_head = create_empty_base(self.agents)
        user_head = create_empty_base(self.user)
        large = self.agents / "large.md"
        small = self.user / "small.md"
        large.write_bytes(b"12345")
        small.write_bytes(b"ok")
        oid = lambda repo, content: subprocess.run(
            ["git", "-C", str(repo), "hash-object", "--stdin"],
            input=content,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        pre = {
            "agents_vault": {
                "local_head": agents_head,
                "dirty_entries": [
                    {"path": "large.md", "git_blob_oid": oid(self.agents, b"12345"), "mode": "100644"}
                ],
                "local_commits": [],
            },
            "user_vault": {
                "local_head": user_head,
                "dirty_entries": [
                    {"path": "small.md", "git_blob_oid": oid(self.user, b"ok"), "mode": "100644"}
                ],
                "local_commits": [],
            },
        }
        runtime = {
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(self.user / ".git"),
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(self.user),
            "gitleaks_bin": str(self.fake_gitleaks),
        }
        destination = self.workdir / "oversize-review"
        destination.mkdir()
        with mock.patch.object(DIRTY_STAGER_MODULE, "MAX_BLOB_BYTES", 4):
            manifest = DIRTY_STAGER_MODULE.materialize(runtime, pre, destination)
        self.assertEqual(
            manifest["vaults"]["agents_vault"][0]["materialization_status"],
            "deferred",
        )
        self.assertEqual(
            manifest["vaults"]["user_vault"][0]["materialization_status"],
            "available",
        )

    def test_capture_and_review_snapshot_local_ahead_then_diverged(self) -> None:
        """Allow reviewable local-ahead history but classify divergence separately."""
        for repo in (self.agents, self.user):
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
                check=True,
            )
            (repo / "base.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)

        base = subprocess.check_output(
            ["git", "-C", str(self.user), "rev-parse", "HEAD"], text=True
        ).strip()
        (self.user / "local.md").write_text("local\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.user), "add", "local.md"], check=True)
        subprocess.run(
            ["git", "-C", str(self.user), "commit", "-q", "-m", "local only"],
            check=True,
        )
        local_head = subprocess.check_output(
            ["git", "-C", str(self.user), "rev-parse", "HEAD"], text=True
        ).strip()
        runtime = self.workdir / "history-runtime.json"
        runtime.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "agents_git_dir": str(self.agents / ".git"),
                    "user_vault_root": str(self.user),
                    "user_git_dir": str(self.user / ".git"),
                }
            ),
            encoding="utf-8",
        )
        pre = self.workdir / "history-pre.json"
        with pre.open("w", encoding="utf-8") as output:
            subprocess.run(
                [
                    str(SCRIPTS / "capture-vault-state.py"),
                    "--include-local-history",
                    str(runtime),
                ],
                check=True,
                stdout=output,
                text=True,
            )
        state = json.loads(pre.read_text())
        user_state = state["user_vault"]
        self.assertEqual(user_state["history_relation"], "local_ahead")
        self.assertEqual(user_state["remote_head"], base)
        self.assertEqual(user_state["local_head"], local_head)
        self.assertEqual(
            [item["commit"] for item in user_state["local_commits"]], [local_head]
        )

        review_input = self.workdir / "history-review-input"
        review_input.mkdir()
        self.assertEqual(
            subprocess.run(
                [
                    str(SCRIPTS / "stage-dirty-review-inputs.py"),
                    str(runtime),
                    str(pre),
                    str(review_input),
                ],
                check=False,
            ).returncode,
            0,
        )
        review_input.chmod(0o700)
        manifest_path = review_input / "dirty-snapshots.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["version"], 4)
        self.assertEqual(
            manifest["local_commits"]["user_vault"][0]["commit"], local_head
        )
        REVIEW_MODULE.validate_dirty_snapshots(
            {
                "dirty_snapshot_manifest_file": str(manifest_path),
                "dirty_snapshot_manifest_sha256": hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest(),
            },
            state,
        )
        patch_path = review_input / "commit-snapshots" / "user" / "0000.patch"
        patch_path.write_bytes(patch_path.read_bytes() + b"tampered")
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "local commit patch digest mismatch"
        ):
            REVIEW_MODULE.validate_dirty_snapshots(
                {
                    "dirty_snapshot_manifest_file": str(manifest_path),
                    "dirty_snapshot_manifest_sha256": hashlib.sha256(
                        manifest_path.read_bytes()
                    ).hexdigest(),
                },
                state,
            )

        peer = self.root / "history-peer"
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            [  # noqa: S607 -- controlled Git fixture command
                "git", "clone", "-q", "--branch", "main",
                str(self.origins["user"]), str(peer),
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(peer), "config", "user.name", "Peer"], check=True)
        subprocess.run(
            ["git", "-C", str(peer), "config", "user.email", "peer@example.invalid"],
            check=True,
        )
        (peer / "remote.md").write_text("remote\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(peer), "add", "remote.md"], check=True)
        subprocess.run(
            ["git", "-C", str(peer), "commit", "-q", "-m", "remote only"],
            check=True,
        )
        subprocess.run(["git", "-C", str(peer), "push", "-q", "origin", "main"], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.user), "fetch", "origin",
                "refs/heads/main:refs/remotes/origin/main",
            ],
            check=True,
        )
        with pre.open("w", encoding="utf-8") as output:
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(runtime)],
                check=True,
                stdout=output,
                text=True,
            )
        diverged = json.loads(pre.read_text())["user_vault"]
        self.assertEqual(diverged["history_relation"], "diverged")
        self.assertEqual(diverged["local_commits"], [])

    def test_lightweight_capture_does_not_materialize_local_commit_patches(self) -> None:
        """Keep expensive local-only history work behind publication preflight."""
        for repo in (self.agents, self.user):
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
                check=True,
            )
            (repo / "base.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)
        (self.user / "huge-history.bin").write_bytes(b"local history")
        subprocess.run(["git", "-C", str(self.user), "add", "huge-history.bin"], check=True)
        subprocess.run(["git", "-C", str(self.user), "commit", "-q", "-m", "local ahead"], check=True)

        with mock.patch.object(
            CAPTURE_MODULE,
            "local_commit_metadata",
            side_effect=AssertionError("history materialization ran before collection"),
        ) as history:
            state = CAPTURE_MODULE.capture(str(self.user))
        history.assert_not_called()
        self.assertEqual(state["history_relation"], "local_ahead")
        self.assertEqual(state["local_commits"], [])

        with mock.patch.object(
            CAPTURE_MODULE,
            "commit_patch",
            side_effect=AssertionError("capture materialized a local commit patch"),
        ) as patch_reader:
            reviewed = CAPTURE_MODULE.capture(
                str(self.user), include_local_history=True
            )
        patch_reader.assert_not_called()
        self.assertEqual(reviewed["history_capture_status"], "available")
        self.assertEqual(len(reviewed["local_commits"]), 1)

    def test_binary_dirty_content_does_not_break_lightweight_capture(self) -> None:
        """Hash invalid-UTF-8 worktree bytes without decoding the file content."""
        subprocess.run(["git", "-C", str(self.user), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            ["git", "-C", str(self.user), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        (self.user / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.user), "add", "base.md"], check=True)
        subprocess.run(["git", "-C", str(self.user), "commit", "-q", "-m", "base"], check=True)
        subprocess.run(["git", "-C", str(self.user), "branch", "-M", "main"], check=True)
        subprocess.run(["git", "-C", str(self.user), "push", "-q", "-u", "origin", "main"], check=True)
        binary = self.user / "binary-dirty.bin"
        binary.write_bytes(b"changed-\xff\n")
        state = CAPTURE_MODULE.capture(str(self.user))
        self.assertIn("binary-dirty.bin", state["dirty_paths"])
        entry = next(
            item for item in state["dirty_entries"]
            if item["path"] == "binary-dirty.bin"
        )
        self.assertIsNotNone(entry["git_blob_oid"])
        self.assertEqual(state["capture_status"], "available")

    def test_index_only_capture_never_walks_existing_worktree(self) -> None:
        """Bind HEAD/index/control state without hydrating unrelated cloud files."""
        repo = self.user
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Fixture"],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(repo), "config", "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        tracked = repo / "tracked.md"
        tracked.write_text("head\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", tracked.name], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"],
            check=True,
        )
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
            check=True,
        )

        staged = repo / "staged-cloud.md"
        staged.write_text("sealed in index\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", staged.name], check=True)
        staged.unlink()
        tracked.write_text("unstaged cloud bytes\n", encoding="utf-8")
        (repo / "untracked-cloud.md").write_text(
            "untracked cloud bytes\n", encoding="utf-8"
        )

        real_runner = CAPTURE_MODULE.run_local_command
        commands: list[list[str]] = []

        def record_command(arguments: list[str], **kwargs: object):
            commands.append(arguments)
            return real_runner(arguments, **kwargs)

        with mock.patch.object(
            CAPTURE_MODULE, "run_local_command", side_effect=record_command
        ):
            state = CAPTURE_MODULE.capture(
                str(repo), include_local_history=True, worktree_scope="index_only"
            )

        flattened = [argument for command in commands for argument in command]
        self.assertNotIn("status", flattened)
        self.assertFalse(
            any("--untracked-files" in argument for argument in flattened)
        )
        self.assertFalse(
            any(
                "ls-files" in command and "--others" in command
                for command in commands
            )
        )
        self.assertFalse(
            any(
                "diff" in command and "--cached" not in command
                for command in commands
            )
        )
        self.assertFalse(any("hash-object" in command for command in commands))
        self.assertEqual(state["worktree_capture_scope"], "index_only")
        self.assertEqual(state["dirty_paths"], [staged.name])
        self.assertEqual(state["staged_paths"], [staged.name])
        self.assertEqual(state["dirty_entries"][0]["mode"], "100644")
        self.assertIsNotNone(state["dirty_entries"][0]["git_blob_oid"])
        self.assertTrue(
            all(value is None for value in state["dirty_metadata"][0].values() if value != staged.name)
        )
        self.assertEqual(
            state["dirty_materialization"],
            {
                "status": "deferred",
                "deferred_paths": [],
                "reason": "worktree_scan_intentionally_omitted",
            },
        )

    def test_index_only_capture_forces_own_only_publication(self) -> None:
        """Never infer sweep cleanliness from an intentionally omitted worktree."""
        base = {
            "capture_status": "available",
            "branch": "main",
            "upstream": "origin/main",
            "local_head": "a" * 40,
            "remote_head": "a" * 40,
            "history_relation": "equal",
            "history_capture_status": "available",
            "operation_in_progress": False,
            "git_control_sha256": "b" * 64,
            "worktree_capture_scope": "index_only",
            "dirty_lines": [],
            "dirty_paths": [],
            "dirty_entries": [],
            "dirty_metadata": [],
            "dirty_materialization": {
                "status": "deferred",
                "deferred_paths": [],
                "reason": "worktree_scan_intentionally_omitted",
            },
            "staged_paths": [],
            "index_entries": [],
            "index_sha256": "c" * 64,
            "index_identity": [1, 2, 3, 33188, 4, 5],
            "dirty_worktree_sha256": "d" * 64,
            "dirty_digest": "e" * 64,
            "diff_snapshot_sha256": "f" * 64,
        }
        mode = MODE_MODULE.vault_mode(base, dict(base), "reports/news.md")
        self.assertEqual(mode["required_mode"], "own_only")
        self.assertFalse(mode["state_changed"])
        self.assertIn("worktree_scan_intentionally_omitted", mode["reasons"])

    def test_every_publication_recapture_requests_index_only_scope(self) -> None:
        """Keep later commit, push, and evidence gates off full worktree status."""
        payload = json.dumps({"agents_vault": {}, "user_vault": {}})
        completed = subprocess.CompletedProcess([], 0, payload, "")
        calls = (
            (COMMITTER_MODULE, lambda: COMMITTER_MODULE.capture_state("/capture", "/runtime")),
            (PUSH_MODULE, lambda: PUSH_MODULE.capture_complete("/runtime")),
            (EVIDENCE_MODULE, lambda: EVIDENCE_MODULE.capture_complete("/runtime")),
            (FINALIZER_MODULE, lambda: FINALIZER_MODULE.capture_complete("/runtime")),
        )
        for module, callback in calls:
            with self.subTest(module=module.__name__), mock.patch.object(
                module, "run_local_command", return_value=completed
            ) as runner:
                callback()
                arguments = runner.call_args.args[0]
                self.assertIn("--index-only", arguments)
                self.assertIn("--include-local-history", arguments)

    def test_index_only_install_validation_rejects_shared_index_drift(self) -> None:
        """Trust the artifact receipt only while reviewed index/control state holds."""
        before = {
            "worktree_capture_scope": "index_only",
            "local_head": "a" * 40,
            "git_control_sha256": "b" * 64,
            "dirty_lines": [],
            "dirty_paths": [],
            "dirty_entries": [],
            "dirty_metadata": [],
            "staged_paths": [],
            "index_entries": [],
            "index_sha256": "c" * 64,
            "index_identity": [1, 2, 3, 33188, 4, 5],
            "dirty_worktree_sha256": "d" * 64,
            "dirty_digest": "e" * 64,
            "diff_snapshot_sha256": "f" * 64,
        }
        volatile = json.loads(json.dumps(before))
        volatile["index_sha256"] = "1" * 64
        volatile["index_identity"] = [1, 6, 3, 33188, 7, 8]
        COMMITTER_MODULE.validate_installed_vault(
            before, volatile, "reports/news.md"
        )
        COMMITTER_MODULE.validate_installed_scope(
            {"agents_vault": before, "user_vault": before},
            {"agents_vault": volatile, "user_vault": volatile},
            {
                "agents_vault": "reports/advisory.md",
                "user_vault": "reports/news.md",
            },
        )

        drifted = json.loads(json.dumps(volatile))
        drifted["index_entries"] = [
            {
                "path": "third-party.md",
                "mode": "100644",
                "git_blob_oid": "2" * 40,
                "stage": 0,
            }
        ]
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "control/index state changed"
        ):
            COMMITTER_MODULE.validate_installed_vault(
                before, drifted, "reports/news.md"
            )
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "control/index state changed"
        ):
            COMMITTER_MODULE.validate_installed_scope(
                {"agents_vault": before, "user_vault": before},
                {"agents_vault": drifted, "user_vault": volatile},
                {
                    "agents_vault": "reports/advisory.md",
                    "user_vault": "reports/news.md",
                },
            )

    def test_capture_defers_file_provider_hash_failure_without_blocking_vault(self) -> None:
        """Keep the Git path/metadata contract when one residual cannot hydrate."""
        for repo in (self.agents, self.user):
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Fixture"],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(repo), "config", "user.email",
                    "fixture@example.invalid",
                ],
                check=True,
            )
            (repo / "base.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "base"],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
                check=True,
            )
        (self.agents / "first.md").write_text("first\n", encoding="utf-8")
        (self.agents / "second.md").write_text("second\n", encoding="utf-8")
        real_runner = CAPTURE_MODULE.run_local_command
        hash_attempts: list[list[str]] = []

        def fail_file_provider_hash(arguments: list[str], **kwargs: object):
            if "hash-object" in arguments and "--no-filters" in arguments:
                hash_attempts.append(arguments)
                raise subprocess.TimeoutExpired(arguments, CAPTURE_MODULE.DIRTY_ENTRY_TIMEOUT_SECONDS)
            return real_runner(arguments, **kwargs)

        context = self.workdir / "provider-capture-context.json"
        context.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "user_vault_root": str(self.user),
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(
            CAPTURE_MODULE, "run_local_command", side_effect=fail_file_provider_hash
        ):
            result = CAPTURE_MODULE.capture(str(self.agents))
        self.assertEqual(len(hash_attempts), 1)
        self.assertEqual(result["capture_status"], "available")
        self.assertTrue(
            {"first.md", "second.md"}.issubset(set(result["dirty_paths"]))
        )
        self.assertEqual(result["dirty_materialization"]["status"], "deferred")
        self.assertEqual(
            result["dirty_materialization"]["deferred_paths"], result["dirty_paths"]
        )
        self.assertEqual(
            result["dirty_materialization"]["reason"],
            "dirty_entry_snapshot_unavailable",
        )
        self.assertTrue(
            all(
                entry["mode"] == CAPTURE_MODULE.UNAVAILABLE_DIRTY_MODE
                and entry["git_blob_oid"] is None
                for entry in result["dirty_entries"]
            )
        )

    def test_capture_keeps_index_only_entry_materialized_after_provider_failure(self) -> None:
        """An index-only residual needs no cloud bytes and stays exact."""
        repo = self.agents
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        (repo / "tracked.md").write_text("head\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True
        )
        tracked = repo / "tracked.md"
        tracked.write_text("staged\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.md"], check=True)
        tracked.write_text("head\n", encoding="utf-8")
        (repo / "provider.md").write_text("provider\n", encoding="utf-8")
        real_runner = CAPTURE_MODULE.run_local_command

        def fail_file_provider_hash(arguments: list[str], **kwargs: object):
            if "hash-object" in arguments and "--no-filters" in arguments:
                raise subprocess.TimeoutExpired(arguments, CAPTURE_MODULE.DIRTY_ENTRY_TIMEOUT_SECONDS)
            return real_runner(arguments, **kwargs)

        with mock.patch.object(
            CAPTURE_MODULE, "run_local_command", side_effect=fail_file_provider_hash
        ):
            result = CAPTURE_MODULE.capture(str(repo))
        entries = {entry["path"]: entry for entry in result["dirty_entries"]}
        self.assertEqual(entries["tracked.md"]["mode"], "100644")
        self.assertIsNotNone(entries["tracked.md"]["git_blob_oid"])
        self.assertEqual(entries["provider.md"]["mode"], CAPTURE_MODULE.UNAVAILABLE_DIRTY_MODE)
        self.assertIsNone(entries["provider.md"]["git_blob_oid"])

    def test_oversize_local_commit_patch_blocks_only_residual_sweep(self) -> None:
        """Materialize local history under a hard bound after collection only."""
        repo = self.user
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        (repo / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)
        (repo / "large.md").write_text("12345\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "large.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "large"], check=True)
        state = CAPTURE_MODULE.capture(str(repo), include_local_history=True)
        destination = self.workdir / "bounded-local-history"
        destination.mkdir()
        runtime = {
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(repo / ".git"),
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(repo),
        }
        pre = {
            "agents_vault": {"dirty_entries": [], "local_commits": []},
            "user_vault": state,
        }
        with mock.patch.object(DIRTY_STAGER_MODULE, "MAX_BLOB_BYTES", 4):
            manifest = DIRTY_STAGER_MODULE.materialize(runtime, pre, destination)
        self.assertEqual(
            manifest["local_commits"]["user_vault"][0]["materialization_status"],
            "blocked",
        )
        self.assertIsNone(
            manifest["local_commits"]["user_vault"][0]["patch_sha256"]
        )

    def test_local_commit_patch_helpers_disable_repository_textconv(self) -> None:
        """Do not execute repository-configured textconv while hashing reviewed patches."""
        repo = self.user
        marker = self.root / "textconv-executed"
        helper = self.root / "textconv.sh"
        helper.write_text(
            f"#!/bin/sh\ntouch {marker}\ncat \"$1\"\n",
            encoding="utf-8",
        )
        helper.chmod(0o755)
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "config", "user.name", "Fixture"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "config", "diff.fixture.textconv", str(helper)],  # noqa: S607
            check=True,
        )
        (repo / ".gitattributes").write_text("*.txt diff=fixture\n", encoding="utf-8")
        target = repo / "reviewed.txt"
        target.write_text("before\n", encoding="utf-8")
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "add", ".gitattributes", "reviewed.txt"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"],  # noqa: S607
            check=True,
        )
        parent = subprocess.check_output(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True  # noqa: S607
        ).strip()
        target.write_text("after\n", encoding="utf-8")
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "add", "reviewed.txt"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "commit", "-q", "-m", "change"],  # noqa: S607
            check=True,
        )
        commit = subprocess.check_output(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True  # noqa: S607
        ).strip()

        patch = CAPTURE_MODULE.commit_patch(str(repo), commit, [parent])
        DIRTY_STAGER_MODULE.read_commit_patch(
            str(repo / ".git"), commit, [parent], hashlib.sha256(patch).hexdigest()
        )
        self.assertEqual(
            PUSH_MODULE.commit_patch_sha256(str(repo), commit, [parent]),
            hashlib.sha256(patch).hexdigest(),
        )
        self.assertFalse(marker.exists())

    def test_empty_existing_commit_message_is_captured_and_schema_valid(self) -> None:
        """Represent valid local-only commits whose Git message is empty."""
        repo = self.user
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "config", "user.name", "Fixture"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],  # noqa: S607
            check=True,
        )
        (repo / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "add", "base.md"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"],  # noqa: S607
            check=True,
        )
        base = subprocess.check_output(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True  # noqa: S607
        ).strip()
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            [  # noqa: S607 -- controlled Git fixture command
                "git", "-C", str(repo), "commit", "-q", "--allow-empty",
                "--allow-empty-message", "-m", "",
            ],
            check=True,
        )
        head = subprocess.check_output(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True  # noqa: S607
        ).strip()
        commits = CAPTURE_MODULE.local_commit_metadata(str(repo), base, head)
        self.assertEqual(commits[0]["message"], "")
        schema = json.loads(
            (SKILL_ROOT / "references" / "publication-review-result.schema.json").read_text()
        )
        self.assertNotIn("minLength", schema["$defs"]["existingCommit"]["properties"]["message"])

    def test_local_committer_uses_explicit_git_paths_and_no_network_overrides(self) -> None:
        """Keep local mutation independent of a Vault process cwd or ambient Git config."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="main\n", stderr=""
        )
        with mock.patch.dict(
            os.environ,
            {"GIT_DIR": "/attacker/git", "GIT_ASKPASS": "/attacker/askpass"},
        ), mock.patch.object(COMMITTER_MODULE, "run_local_command", return_value=completed) as run:
            COMMITTER_MODULE.git(
                "/vault/worktree",
                "/vault/gitdir",
                "branch",
                "--show-current",
                publisher_identity=("Fixture Publisher", "publisher@example.invalid"),
            )
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(
            command[:3],
            ["git", "--git-dir=/vault/gitdir", "--work-tree=/vault/worktree"],
        )
        self.assertEqual(run.call_args.kwargs["cwd"], "/")
        self.assertNotIn("GIT_DIR", environment)
        self.assertNotIn("GIT_ASKPASS", environment)
        self.assertEqual(environment["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(environment["GIT_AUTHOR_NAME"], "Fixture Publisher")
        self.assertEqual(
            environment["GIT_AUTHOR_EMAIL"],
            "publisher@example.invalid",
        )
        self.assertEqual(environment["GIT_COMMITTER_NAME"], "Fixture Publisher")
        self.assertEqual(
            environment["GIT_COMMITTER_EMAIL"],
            "publisher@example.invalid",
        )

    def test_evidence_finalizer_uses_same_verified_publisher_identity(self) -> None:
        """Attribute the final evidence commit to the same GitHub account."""
        with mock.patch.dict(
            os.environ,
            {
                "GIT_AUTHOR_NAME": "Attacker",
                "GIT_AUTHOR_EMAIL": "attacker@example.invalid",
                "GIT_COMMITTER_NAME": "Attacker",
                "GIT_COMMITTER_EMAIL": "attacker@example.invalid",
            },
        ):
            environment = FINALIZER_MODULE.clean_environment(
                ("Fixture Publisher", "publisher@example.invalid")
            )
        self.assertEqual(environment["GIT_AUTHOR_NAME"], "Fixture Publisher")
        self.assertEqual(
            environment["GIT_AUTHOR_EMAIL"],
            "publisher@example.invalid",
        )
        self.assertEqual(environment["GIT_COMMITTER_NAME"], "Fixture Publisher")
        self.assertEqual(
            environment["GIT_COMMITTER_EMAIL"],
            "publisher@example.invalid",
        )

    def test_committers_reject_tampered_runtime_identity(self) -> None:
        """Apply the resolver's email grammar again immediately before mutation."""
        tampered = {
            "publisher_git_name": "Fixture Publisher",
            "publisher_git_email": "publisher@@example.invalid",
        }
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.validated_publisher_identity(tampered)
        with self.assertRaises(FINALIZER_MODULE.FinalizationError):
            FINALIZER_MODULE.validated_publisher_identity(tampered)

    def test_finalizer_rejects_valid_identity_substitution_after_review(self) -> None:
        """Bind finalization identity to the reviewed publication context digest."""
        runtime = {
            "publisher_git_name": "Fixture Publisher",
            "publisher_git_email": "publisher@example.invalid",
        }
        pre = {"agents_vault": {"local_head": "a" * 40}}
        context_bytes = json.dumps(
            {"runtime": runtime, "pre_collection_state": pre},
            sort_keys=True,
        ).encode("utf-8")
        substituted = dict(runtime)
        substituted["publisher_git_email"] = "other@example.invalid"
        with self.assertRaises(FINALIZER_MODULE.FinalizationError):
            FINALIZER_MODULE.context_bound_inputs(
                substituted,
                pre,
                context_bytes,
                hashlib.sha256(context_bytes).hexdigest(),
            )

    def test_local_committer_creates_github_attributed_commit_metadata(self) -> None:
        """Write the configured identity into an actual isolated commit object."""
        repo = self.workdir / "identity-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "artifact.md").write_text("reviewed artifact\n", encoding="utf-8")
        COMMITTER_MODULE.git(str(repo), str(repo / ".git"), "add", "artifact.md")
        COMMITTER_MODULE.git(
            str(repo),
            str(repo / ".git"),
            "commit",
            "-q",
            "-m",
            "fixture",
            publisher_identity=("Fixture Publisher", "publisher@example.invalid"),
        )
        identity = COMMITTER_MODULE.git(
            str(repo),
            str(repo / ".git"),
            "show",
            "-s",
            "--format=%an%x00%ae%x00%cn%x00%ce",
            "HEAD",
        ).stdout.strip().split("\0")
        self.assertEqual(
            identity,
            [
                "Fixture Publisher",
                "publisher@example.invalid",
                "Fixture Publisher",
                "publisher@example.invalid",
            ],
        )

    def test_evidence_finalizer_creates_github_attributed_commit_metadata(self) -> None:
        """Write the final evidence identity into an actual isolated commit object."""
        repo = self.workdir / "evidence-identity-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "evidence.md").write_text("reviewed evidence\n", encoding="utf-8")
        FINALIZER_MODULE.git(str(repo), "add", "evidence.md")
        FINALIZER_MODULE.git(
            str(repo),
            "commit",
            "-q",
            "-m",
            "fixture evidence",
            publisher_identity=("Fixture Publisher", "publisher@example.invalid"),
        )
        identity = FINALIZER_MODULE.git(
            str(repo),
            "show",
            "-s",
            "--format=%an%x00%ae%x00%cn%x00%ce",
            "HEAD",
        ).stdout.strip().split("\0")
        self.assertEqual(
            identity,
            [
                "Fixture Publisher",
                "publisher@example.invalid",
                "Fixture Publisher",
                "publisher@example.invalid",
            ],
        )

    def test_evidence_commit_ignores_replace_refs_and_matches_raw_parent(self) -> None:
        """Prevent a replace ref from hiding extra paths in the committed tree."""
        repo = self.workdir / "replace-ref-evidence-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        (repo / "base.md").write_text("base\n", encoding="utf-8")
        target = repo / "evidence.md"
        target.write_text(
            "# Task\n\n### Vault Publication Evidence\n\n## Reviews\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(repo), "add", "base.md", "evidence.md"],
            check=True,
        )
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
        base = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        (repo / "hidden.md").write_text("hidden\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "hidden.md"], check=True)
        replacement_tree = subprocess.check_output(
            ["git", "-C", str(repo), "write-tree"], text=True
        ).strip()
        replacement = subprocess.check_output(
            ["git", "-C", str(repo), "commit-tree", replacement_tree, "-p", base],
            input="replacement\n",
            text=True,
        ).strip()
        subprocess.run(
            ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", "hidden.md"],
            check=True,
        )
        (repo / "hidden.md").unlink()
        subprocess.run(["git", "-C", str(repo), "replace", base, replacement], check=True)
        before = target.read_bytes()
        candidate = before.replace(
            b"## Reviews\n", b"#### Daily evidence\n\n## Reviews\n"
        )
        head_candidate = self.workdir / "replace-head-candidate"
        index_candidate = self.workdir / "replace-index-candidate"
        patch_path = self.workdir / "replace-evidence.patch"
        head_candidate.write_bytes(candidate)
        index_candidate.write_bytes(candidate)
        patch = FINALIZER_MODULE.canonical_patch("evidence.md", before, candidate)
        patch_path.write_bytes(patch)
        runtime = {
            "agents_vault_root": str(repo),
            "agents_git_dir": str(repo / ".git"),
            "gitleaks_bin": str(self.fake_gitleaks),
        }
        plan = {
            "target_path": "evidence.md",
            "base_head": base,
            "head_source_sha256": hashlib.sha256(before).hexdigest(),
            "head_candidate_path": str(head_candidate),
            "head_candidate_sha256": hashlib.sha256(candidate).hexdigest(),
            "index_candidate_path": str(index_candidate),
            "index_candidate_sha256": hashlib.sha256(candidate).hexdigest(),
            "review_patch_path": str(patch_path),
            "evidence_diff_sha256": hashlib.sha256(patch).hexdigest(),
        }
        commit, _head_blob, _index_blob, _index_bytes = FINALIZER_MODULE.isolated_evidence_commit(
            runtime,
            plan,
            ("Fixture", "fixture@example.invalid"),
            self.workdir,
        )
        environment = FINALIZER_MODULE.clean_environment()
        raw_paths = subprocess.check_output(
            [
                "git", "-C", str(repo), "diff", "--name-only", "--no-renames",
                base, commit,
            ],
            text=True,
            env=environment,
        ).splitlines()
        self.assertEqual(raw_paths, ["evidence.md"])

    def test_local_committer_binds_inputs_and_complete_installed_scope(self) -> None:
        """Reject standalone JSON substitution and manifest-external dirty paths."""
        runtime = {"runtime": "reviewed"}
        pre = {"pre": "reviewed"}
        collection = {"collection": "reviewed"}
        plan = {"plan": "reviewed"}
        context = {
            "runtime": runtime,
            "pre_collection_state": pre,
            "verified_collection": collection,
            "artifact_plan": plan,
        }
        self.assertEqual(
            COMMITTER_MODULE.reviewed_inputs(
                context, runtime, pre, collection, plan
            ),
            (runtime, pre, collection, plan),
        )
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.reviewed_inputs(
                context, runtime, pre, {"collection": "substituted"}, plan
            )

        fixed = {
            "repo_root": "vault",
            "branch": "main",
            "upstream": "origin/main",
            "local_head": "a" * 40,
            "remote_head": "a" * 40,
            "operation_in_progress": False,
            "git_control_sha256": "b" * 64,
        }
        empty = {
            **fixed,
            "dirty_lines": [],
            "dirty_paths": [],
            "dirty_entries": [],
            "dirty_metadata": [],
            "staged_paths": [],
            "index_entries": [],
            "index_sha256": "0" * 64,
            "dirty_digest": hashlib.sha256(b"").hexdigest(),
            "diff_snapshot_sha256": hashlib.sha256(b"").hexdigest(),
        }
        current = {
            **empty,
            "dirty_lines": ["?? artifact.md"],
            "dirty_paths": ["artifact.md"],
            "dirty_entries": [
                {"path": "artifact.md", "git_blob_oid": "c" * 40, "mode": "100644"}
            ],
            "dirty_metadata": [
                {
                    "path": "artifact.md", "exists": True, "size": 1,
                    "mtime_ns": 1, "st_mode": 0o100644,
                }
            ],
        }
        COMMITTER_MODULE.validate_installed_scope(
            {"agents_vault": empty, "user_vault": empty},
            {"agents_vault": current, "user_vault": current},
            {"agents_vault": "artifact.md", "user_vault": "artifact.md"},
        )
        external = json.loads(json.dumps(current))
        external["dirty_paths"].append("external.md")
        external["dirty_entries"].append(
            {"path": "external.md", "git_blob_oid": "d" * 40, "mode": "100644"}
        )
        external["dirty_metadata"].append(
            {
                "path": "external.md", "exists": True, "size": 1,
                "mtime_ns": 1, "st_mode": 0o100644,
            }
        )
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.validate_installed_scope(
                {"agents_vault": empty, "user_vault": empty},
                {"agents_vault": external, "user_vault": current},
                {"agents_vault": "artifact.md", "user_vault": "artifact.md"},
            )

    def test_local_committer_rejects_unsafe_paths(self) -> None:
        """Reject absolute, traversal, and Obsidian-control paths."""
        for path in (
            "/absolute.md",
            "../escape.md",
            "a/../escape.md",
            ".obsidian/config",
        ):
            with self.assertRaises(COMMITTER_MODULE.CommitError):
                COMMITTER_MODULE.safe_path(path)
        self.assertEqual(
            COMMITTER_MODULE.safe_path("reports/advisory.md"),
            "reports/advisory.md",
        )

    def test_owned_artifact_rollback_is_exact_and_refuses_drift(self) -> None:
        """Undo only an unchanged O_EXCL artifact after a pre-commit failure."""
        target = self.user / "owned-artifact.md"
        target.write_text("verified\n", encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        receipt = COMMITTER_MODULE.installed_artifact_receipt(
            target, digest, self.user, self.user / ".git"
        )
        COMMITTER_MODULE.rollback_owned_artifact(receipt)
        self.assertFalse(target.exists())
        reservation = self.user / ".git" / str(receipt["reservation_name"])
        self.assertEqual((reservation / "artifact").read_bytes(), b"verified\n")
        self.assertEqual(
            (reservation / "rollback-worktree").read_bytes(), b"verified\n"
        )

        target.write_text("verified\n", encoding="utf-8")
        receipt = COMMITTER_MODULE.installed_artifact_receipt(
            target, digest, self.user, self.user / ".git"
        )
        target.write_text("other task\n", encoding="utf-8")
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "rollback refused"
        ):
            COMMITTER_MODULE.rollback_owned_artifact(receipt)
        self.assertEqual(target.read_text(encoding="utf-8"), "other task\n")

        target.write_text("verified\n", encoding="utf-8")
        receipt = COMMITTER_MODULE.installed_artifact_receipt(
            target, digest, self.user, self.user / ".git"
        )
        real_rename = COMMITTER_MODULE.rename_no_replace
        rename_calls = 0

        def replace_before_quarantine(*args: object, **kwargs: object) -> None:
            nonlocal rename_calls
            rename_calls += 1
            if rename_calls == 1:
                target.unlink()
                target.write_text("third party\n", encoding="utf-8")
            real_rename(*args, **kwargs)

        with mock.patch.object(
            COMMITTER_MODULE,
            "rename_no_replace",
            side_effect=replace_before_quarantine,
        ):
            with self.assertRaisesRegex(
                COMMITTER_MODULE.CommitError, "replacement was restored"
            ):
                COMMITTER_MODULE.rollback_owned_artifact(receipt)
        self.assertEqual(target.read_text(encoding="utf-8"), "third party\n")

        target.write_text("verified\n", encoding="utf-8")
        real_read = os.read
        replaced = False

        def replace_between_read_and_identity(
            descriptor: int, count: int
        ) -> bytes:
            nonlocal replaced
            chunk = real_read(descriptor, count)
            if chunk and not replaced:
                replaced = True
                target.unlink()
                target.write_text("verified\n", encoding="utf-8")
            return chunk

        with mock.patch.object(
            COMMITTER_MODULE.os,
            "read",
            side_effect=replace_between_read_and_identity,
        ), self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "changed before publication"
        ):
            COMMITTER_MODULE.installed_artifact_receipt(
                target, digest, self.user, self.user / ".git"
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "verified\n")

    def test_owned_artifact_receipt_allows_timestamp_only_file_provider_drift(self) -> None:
        """Treat File Provider timestamp normalization as non-identity metadata."""
        target = self.user / "file-provider-artifact.md"
        target.write_text("verified\n", encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        receipt = COMMITTER_MODULE.installed_artifact_receipt(
            target, digest, self.user, self.user / ".git"
        )
        before = target.stat()
        normalized_mtime = max(1, before.st_mtime_ns - 1_000_000_000)
        os.utime(target, ns=(normalized_mtime, normalized_mtime))
        after = target.stat()
        self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
        self.assertNotEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertNotEqual(after.st_ctime_ns, before.st_ctime_ns)
        COMMITTER_MODULE.require_owned_artifact(receipt)
        COMMITTER_MODULE.rollback_owned_artifact(receipt)
        self.assertFalse(target.exists())

    def test_owned_artifact_receipt_rejects_mode_drift(self) -> None:
        """Keep mode changes fail-closed even when inode and bytes are unchanged."""
        target = self.user / "mode-drift-artifact.md"
        target.write_text("verified\n", encoding="utf-8")
        os.chmod(target, 0o644)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        receipt = COMMITTER_MODULE.installed_artifact_receipt(
            target, digest, self.user, self.user / ".git"
        )
        os.chmod(target, 0o600)
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "changed before publication"
        ):
            COMMITTER_MODULE.require_owned_artifact(receipt)
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "rollback refused"
        ):
            COMMITTER_MODULE.rollback_owned_artifact(receipt)
        self.assertEqual(target.read_text(encoding="utf-8"), "verified\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_committer_rejects_same_bytes_different_artifact_inode(self) -> None:
        """Bind blob construction and HEAD update to the installer reservation."""
        base = self.user / "base.md"
        base.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.user), "add", base.name], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.user),
                "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid",
                "commit", "-q", "-m", "base",
            ],
            check=True,
        )
        target = self.user / "same-bytes-replacement.md"
        target.write_text("verified\n", encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        receipt = COMMITTER_MODULE.installed_artifact_receipt(
            target, digest, self.user, self.user / ".git"
        )
        original_identity = tuple(receipt["identity"])
        target.unlink()
        target.write_text("verified\n", encoding="utf-8")
        self.assertNotEqual(
            (target.stat().st_dev, target.stat().st_ino), original_identity
        )
        head = subprocess.check_output(
            ["git", "-C", str(self.user), "rev-parse", "HEAD"], text=True
        ).strip()
        manifest = {
            "approved_dirty_entries": [],
            "commit_groups": [
                {"message": "publish exact artifact", "paths": [target.name]}
            ],
        }
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "changed before publication"
        ):
            COMMITTER_MODULE.commit_groups(
                str(self.user),
                str(self.user / ".git"),
                str(self.fake_gitleaks),
                {"local_head": head, "dirty_digest": hashlib.sha256(b"").hexdigest()},
                manifest,
                target.name,
                digest,
                self.workdir,
                artifact_receipt=receipt,
            )
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(self.user), "rev-parse", "HEAD"], text=True
            ).strip(),
            head,
        )

    def test_owned_artifact_rollback_revalidates_after_quarantine(self) -> None:
        """Restore same-inode content, size, or mode drift instead of deleting it."""
        target = self.user / "quarantine-revalidation.md"
        digest = hashlib.sha256(b"verified\n").hexdigest()

        for drift in ("content", "size", "mode"):
            with self.subTest(drift=drift):
                target.write_text("verified\n", encoding="utf-8")
                os.chmod(target, 0o644)
                receipt = COMMITTER_MODULE.installed_artifact_receipt(
                    target, digest, self.user, self.user / ".git"
                )
                original_identity = (target.stat().st_dev, target.stat().st_ino)
                real_rename = COMMITTER_MODULE.rename_no_replace
                rename_calls = 0

                def drift_before_quarantine(
                    *args: object, **kwargs: object
                ) -> None:
                    nonlocal rename_calls
                    rename_calls += 1
                    if rename_calls == 1:
                        if drift == "content":
                            target.write_text("tampered\n", encoding="utf-8")
                        elif drift == "size":
                            target.write_text("verified plus drift\n", encoding="utf-8")
                        else:
                            os.chmod(target, 0o600)
                    real_rename(*args, **kwargs)

                with mock.patch.object(
                    COMMITTER_MODULE,
                    "rename_no_replace",
                    side_effect=drift_before_quarantine,
                ), self.assertRaisesRegex(
                    COMMITTER_MODULE.CommitError,
                    "changed during rollback; rollback refused",
                ):
                    COMMITTER_MODULE.rollback_owned_artifact(receipt)
                self.assertTrue(target.is_file())
                self.assertEqual(
                    (target.stat().st_dev, target.stat().st_ino),
                    original_identity,
                )
                if drift == "content":
                    self.assertEqual(target.read_text(encoding="utf-8"), "tampered\n")
                elif drift == "size":
                    self.assertEqual(
                        target.read_text(encoding="utf-8"), "verified plus drift\n"
                    )
                else:
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
                target.unlink()

        target.write_text("verified\n", encoding="utf-8")
        os.chmod(target, 0o644)
        receipt = COMMITTER_MODULE.installed_artifact_receipt(
            target, digest, self.user, self.user / ".git"
        )
        owned_identity = (target.stat().st_dev, target.stat().st_ino)
        real_rename = COMMITTER_MODULE.rename_no_replace
        rename_calls = 0

        def drift_and_reoccupy(*args: object, **kwargs: object) -> None:
            nonlocal rename_calls
            rename_calls += 1
            if rename_calls == 1:
                os.chmod(target, 0o600)
            real_rename(*args, **kwargs)
            if rename_calls == 1:
                target.write_text("new occupant\n", encoding="utf-8")

        with mock.patch.object(
            COMMITTER_MODULE,
            "rename_no_replace",
            side_effect=drift_and_reoccupy,
        ), self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError,
            "changed during rollback; rollback quarantined it",
        ):
            COMMITTER_MODULE.rollback_owned_artifact(receipt)
        self.assertEqual(target.read_text(encoding="utf-8"), "new occupant\n")
        held = (
            self.user
            / ".git"
            / str(receipt["reservation_name"])
            / "artifact"
        )
        self.assertEqual((held.stat().st_dev, held.stat().st_ino), owned_identity)
        self.assertEqual(held.read_text(encoding="utf-8"), "verified\n")
        self.assertEqual(stat.S_IMODE(held.stat().st_mode), 0o600)

    def test_owned_artifact_rollback_reopens_post_move_retention(self) -> None:
        """Seal rollback-worktree through its destination after the no-replace move."""
        target = self.user / "post-move-rollback-revalidation.md"
        digest = hashlib.sha256(b"verified\n").hexdigest()

        for drift in ("content", "size", "mode"):
            with self.subTest(drift=drift):
                target.write_bytes(b"verified\n")
                target.chmod(0o644)
                receipt = COMMITTER_MODULE.installed_artifact_receipt(
                    target, digest, self.user, self.user / ".git"
                )
                original_identity = (target.stat().st_dev, target.stat().st_ino)
                real_rename = COMMITTER_MODULE.rename_no_replace
                rename_calls = 0

                def drift_after_quarantine(
                    source_fd: int,
                    source_name: str,
                    destination_fd: int,
                    destination_name: str,
                ) -> None:
                    nonlocal rename_calls
                    rename_calls += 1
                    real_rename(
                        source_fd, source_name, destination_fd, destination_name
                    )
                    if rename_calls != 1:
                        return
                    retained_fd = os.open(
                        destination_name,
                        os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=destination_fd,
                    )
                    try:
                        if drift == "content":
                            os.write(retained_fd, b"tampered\n")
                            os.ftruncate(retained_fd, len(b"tampered\n"))
                        elif drift == "size":
                            os.write(retained_fd, b"verified plus drift\n")
                            os.ftruncate(retained_fd, len(b"verified plus drift\n"))
                        else:
                            os.fchmod(retained_fd, 0o600)
                        os.fsync(retained_fd)
                    finally:
                        os.close(retained_fd)

                with mock.patch.object(
                    COMMITTER_MODULE,
                    "rename_no_replace",
                    side_effect=drift_after_quarantine,
                ), self.assertRaisesRegex(
                    COMMITTER_MODULE.CommitError,
                    "changed during rollback; rollback refused",
                ):
                    COMMITTER_MODULE.rollback_owned_artifact(receipt)
                self.assertEqual(
                    (target.stat().st_dev, target.stat().st_ino), original_identity
                )
                if drift == "content":
                    self.assertEqual(target.read_bytes(), b"tampered\n")
                elif drift == "size":
                    self.assertEqual(target.read_bytes(), b"verified plus drift\n")
                else:
                    self.assertEqual(target.read_bytes(), b"verified\n")
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
                target.unlink()

    def test_owned_artifact_rollback_restores_non_hardlinkable_replacements(self) -> None:
        """Restore directory and symlink replacements without changing Git state."""
        staged = self.user / "unrelated-staged.md"
        staged.write_text("staged\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.user), "add", staged.name], check=True)
        fixed_mtime = 1_700_000_000_000_000_000

        for replacement_type in ("directory", "symlink"):
            with self.subTest(replacement_type=replacement_type):
                target = self.user / f"rollback-{replacement_type}.md"
                target.write_text("verified\n", encoding="utf-8")
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                receipt = COMMITTER_MODULE.installed_artifact_receipt(
                    target, digest, self.user, self.user / ".git"
                )
                symlink_source = self.user / f"rollback-{replacement_type}-source"
                if replacement_type == "symlink":
                    symlink_source.write_text("symlink target\n", encoding="utf-8")
                replacement_fingerprint: tuple[int, int, int, int, int] | None = None
                status_before = b""
                index_before = b""
                real_rename = COMMITTER_MODULE.rename_no_replace
                rename_calls = 0

                def replace_before_quarantine(*args: object, **kwargs: object) -> None:
                    nonlocal replacement_fingerprint, status_before, index_before, rename_calls
                    rename_calls += 1
                    if rename_calls == 1:
                        target.unlink()
                        if replacement_type == "directory":
                            target.mkdir(mode=0o750)
                            (target / "preserved.txt").write_text(
                                "directory content\n", encoding="utf-8"
                            )
                            os.chmod(target, 0o750)
                        else:
                            target.symlink_to(symlink_source.name)
                        os.utime(
                            target,
                            ns=(fixed_mtime, fixed_mtime),
                            follow_symlinks=False,
                        )
                        metadata = os.lstat(target)
                        replacement_fingerprint = (
                            metadata.st_dev,
                            metadata.st_ino,
                            metadata.st_mode,
                            metadata.st_size,
                            metadata.st_mtime_ns,
                        )
                        status_before = subprocess.check_output(
                            [
                                "git", "-C", str(self.user), "status",
                                "--porcelain=v2", "-z", "--untracked-files=all",
                            ]
                        )
                        index_before = (self.user / ".git" / "index").read_bytes()
                    real_rename(*args, **kwargs)

                with mock.patch.object(
                    COMMITTER_MODULE,
                    "rename_no_replace",
                    side_effect=replace_before_quarantine,
                ):
                    with self.assertRaisesRegex(
                        COMMITTER_MODULE.CommitError, "replacement was restored"
                    ):
                        COMMITTER_MODULE.rollback_owned_artifact(receipt)
                metadata = os.lstat(target)
                self.assertEqual(
                    (
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_mode,
                        metadata.st_size,
                        metadata.st_mtime_ns,
                    ),
                    replacement_fingerprint,
                )
                if replacement_type == "directory":
                    self.assertEqual(
                        (target / "preserved.txt").read_text(encoding="utf-8"),
                        "directory content\n",
                    )
                else:
                    self.assertEqual(os.readlink(target), symlink_source.name)
                self.assertEqual(
                    subprocess.check_output(
                        [
                            "git", "-C", str(self.user), "status",
                            "--porcelain=v2", "-z", "--untracked-files=all",
                        ]
                    ),
                    status_before,
                )
                self.assertEqual(
                    (self.user / ".git" / "index").read_bytes(), index_before
                )
                self.assertEqual(
                    list(self.user.glob(".vault-publisher-rollback-*")), []
                )

    def test_partial_resume_never_rebinds_path_into_rollback_ownership(self) -> None:
        """Leave an unowned resumed artifact intact when publication fails."""
        target = self.user / "summary.md"
        digest = hashlib.sha256(b"verified\n").hexdigest()
        snapshot = {
            "local_head": "a" * 40,
            "dirty_digest": "b" * 64,
            "dirty_lines": ["? summary.md"],
        }
        manifest = {"publication_mode": "own_only", "deferred_cleanup": []}
        runtime = {
            "user_vault_root": str(self.user),
            "user_git_dir": str(self.user / ".git"),
            "gitleaks_bin": str(self.fake_gitleaks),
        }
        plan = {
            "summary_target": str(target),
            "advisory_target": str(self.agents / "advisory.md"),
        }
        collection = {"summary_sha256": digest}
        with mock.patch.object(
            COMMITTER_MODULE, "capture_one_semantic"
        ), mock.patch.object(
            COMMITTER_MODULE, "validate_installed_vault"
        ), mock.patch.object(
            COMMITTER_MODULE,
            "validate_final_worktree",
            side_effect=COMMITTER_MODULE.CommitError("fixture failure"),
        ), mock.patch.object(
            COMMITTER_MODULE, "capture_one", return_value=snapshot
        ), mock.patch.object(
            COMMITTER_MODULE, "installed_artifact_receipt"
        ) as rebind, mock.patch.object(
            COMMITTER_MODULE, "rollback_uncommitted_artifact"
        ) as rollback:
            result, succeeded, retry_safe, reason, handle = (
                COMMITTER_MODULE.publish_one_vault(
                    key="user_vault",
                    prefix="user",
                    role="user_it_news_summary",
                    artifact_plan_key="summary_target",
                    collection_sha_key="summary_sha256",
                    runtime=runtime,
                    pre={"user_vault": snapshot},
                    collection=collection,
                    plan=plan,
                    manifest=manifest,
                    installer="/unused-installer",
                    capture="/unused-capture",
                    runtime_file="/runtime.json",
                    collection_file="/collection.json",
                    plan_file="/plan.json",
                    output_directory=self.workdir,
                    publisher_identity=("Fixture", "fixture@example.invalid"),
                    resume_state=snapshot,
                )
            )
        rebind.assert_not_called()
        rollback.assert_not_called()
        self.assertFalse(succeeded)
        self.assertTrue(retry_safe)
        self.assertIn("fixture failure", reason)
        self.assertEqual(result["commit_hashes"], [])
        self.assertIsNone(handle)

    def test_blocked_mode_is_rejected_before_local_git_mutation(self) -> None:
        """Validate publication mode before writing blobs or commit objects."""
        with mock.patch.object(COMMITTER_MODULE, "git") as git:
            with self.assertRaisesRegex(
                COMMITTER_MODULE.CommitError, "blocked Vault"
            ):
                COMMITTER_MODULE.commit_groups(
                    "/vault",
                    "/vault/.git",
                    "/gitleaks",
                    {"local_head": "a" * 40, "dirty_digest": "b" * 64},
                    {"commit_groups": [{"message": "x", "paths": ["x.md"]}]},
                    "x.md",
                    "c" * 64,
                    self.workdir,
                    publication_mode="blocked",
                )
        git.assert_not_called()

    def test_local_committer_scans_binary_blobs_and_allows_deletion(self) -> None:
        """Reject a home path in raw bytes without treating deletion as a blob."""
        repo = self.agents
        git_dir = str(repo / ".git")
        (repo / "delete.md").write_text("delete me\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "delete.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-q",
                "-m",
                "base",
            ],
            check=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            binary_index = str(Path(temporary) / "binary.index")
            COMMITTER_MODULE.git(
                str(repo), git_dir, "read-tree", "HEAD", index_file=binary_index
            )
            binary_oid = COMMITTER_MODULE.write_blob(
                str(repo), git_dir, b"\x00" + os.fsencode(str(Path.home()))
            )
            COMMITTER_MODULE.update_index_entry(
                str(repo),
                git_dir,
                "binary.dat",
                ("100644", binary_oid),
                index_file=binary_index,
            )
            with self.assertRaises(COMMITTER_MODULE.CommitError):
                COMMITTER_MODULE.scan_staged(
                    str(self.fake_gitleaks),
                    str(repo),
                    git_dir,
                    ["binary.dat"],
                    binary_index,
                )

            deletion_index = str(Path(temporary) / "deletion.index")
            COMMITTER_MODULE.git(
                str(repo), git_dir, "read-tree", "HEAD", index_file=deletion_index
            )
            COMMITTER_MODULE.update_index_entry(
                str(repo),
                git_dir,
                "delete.md",
                None,
                index_file=deletion_index,
            )
            COMMITTER_MODULE.scan_staged(
                str(self.fake_gitleaks),
                str(repo),
                git_dir,
                ["delete.md"],
                deletion_index,
            )

    def test_local_committer_allows_historical_home_path_but_rejects_new_text_path(
        self,
    ) -> None:
        """Only newly added home paths block reviewed text candidates."""
        repo = self.agents
        git_dir = str(repo / ".git")
        historical = repo / "historical.md"
        historical.write_text(
            f"historical evidence: {Path.home()}/old.log\n"
            f"historical CR evidence:\r{Path.home()}/old-cr.log\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(repo), "add", "historical.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-q",
                "-m",
                "historical path fixture",
            ],
            check=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            historical_index = str(Path(temporary) / "historical.index")
            COMMITTER_MODULE.git(
                str(repo), git_dir, "read-tree", "HEAD", index_file=historical_index
            )
            historical.write_bytes(historical.read_bytes() + b"reviewed update\n")
            COMMITTER_MODULE.git(
                str(repo),
                git_dir,
                "add",
                "--",
                "historical.md",
                index_file=historical_index,
            )
            COMMITTER_MODULE.scan_staged(
                str(self.fake_gitleaks),
                str(repo),
                git_dir,
                ["historical.md"],
                historical_index,
            )

            safe_candidate = historical.read_bytes()
            for index, prefix in enumerate(("", "+", "++", "+++")):
                with self.subTest(prefix=prefix):
                    added_index = str(Path(temporary) / f"added-{index}.index")
                    COMMITTER_MODULE.git(
                        str(repo), git_dir, "read-tree", "HEAD", index_file=added_index
                    )
                    historical.write_bytes(
                        safe_candidate
                        + f"{prefix}new evidence: {Path.home()}/new.log\n".encode(
                            "utf-8"
                        )
                    )
                    COMMITTER_MODULE.git(
                        str(repo),
                        git_dir,
                        "add",
                        "--",
                        "historical.md",
                        index_file=added_index,
                    )
                    with self.assertRaises(COMMITTER_MODULE.CommitError):
                        COMMITTER_MODULE.scan_staged(
                            str(self.fake_gitleaks),
                            str(repo),
                            git_dir,
                            ["historical.md"],
                            added_index,
                        )
            cr_index = str(Path(temporary) / "added-cr.index")
            COMMITTER_MODULE.git(
                str(repo), git_dir, "read-tree", "HEAD", index_file=cr_index
            )
            historical.write_bytes(
                safe_candidate
                + f"new CR evidence:\r{Path.home()}/new-cr.log\n".encode("utf-8")
            )
            COMMITTER_MODULE.git(
                str(repo),
                git_dir,
                "add",
                "--",
                "historical.md",
                index_file=cr_index,
            )
            with self.assertRaises(COMMITTER_MODULE.CommitError):
                COMMITTER_MODULE.scan_staged(
                    str(self.fake_gitleaks),
                    str(repo),
                    git_dir,
                    ["historical.md"],
                    cr_index,
                )

    def test_unified_diff_parser_uses_only_lf_as_line_boundary(self) -> None:
        """Preserve standalone CR and plus-prefixed content inside one Git hunk."""
        patch_bytes = (
            b"diff --git a/file b/file\n"
            b"--- a/file\n"
            b"+++ b/file\n"
            b"@@ -0,0 +1 @@\n"
            b"+++prefix\rsecret-after-cr\n"
        )
        self.assertEqual(
            DIRTY_STAGER_MODULE.unified_diff_added_content(patch_bytes),
            b"++prefix\rsecret-after-cr\n",
        )
        patch_text = patch_bytes.decode("utf-8")
        self.assertEqual(
            DIRTY_STAGER_MODULE.unified_diff_added_content(patch_text),
            "++prefix\rsecret-after-cr\n",
        )

    def test_local_committer_preserves_reviewed_markdown_whitespace(self) -> None:
        """Reviewed Vault content is committed byte-for-byte, including hard breaks."""
        repo = self.agents
        git_dir = str(repo / ".git")
        base = repo / "base.md"
        base.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-q",
                "-m",
                "base",
            ],
            check=True,
        )
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        reviewed = repo / "reviewed.md"
        reviewed.write_text("reviewed line  \n", encoding="utf-8")
        reviewed_oid = COMMITTER_MODULE.write_blob(
            str(repo), git_dir, reviewed.read_bytes()
        )
        artifact = repo / "artifact.md"
        artifact.write_text("artifact\n", encoding="utf-8")
        manifest = {
            "approved_dirty_entries": [
                {
                    "path": "reviewed.md",
                    "git_blob_oid": reviewed_oid,
                    "mode": "100644",
                }
            ],
            "commit_groups": [
                {"message": "approved content", "paths": ["reviewed.md"]},
                {"message": "publish artifact", "paths": ["artifact.md"]},
            ],
        }

        final_state = {
            "commit_status": "complete",
            "commit_hashes": ["1" * 40, "2" * 40],
            "pre_local_head": before,
            "local_head": "2" * 40,
            "pre_dirty_digest": hashlib.sha256(b"").hexdigest(),
            "post_dirty_digest": hashlib.sha256(b"").hexdigest(),
            "clean": True,
        }
        with mock.patch.object(
            COMMITTER_MODULE, "current_state", return_value=final_state
        ):
            index_bytes, index_identity = COMMITTER_MODULE.index_file_contract(
                Path(git_dir) / "index"
            )
            result = COMMITTER_MODULE.commit_groups(
                str(repo),
                git_dir,
                str(self.fake_gitleaks),
                {
                    "local_head": before,
                    "dirty_digest": hashlib.sha256(b"").hexdigest(),
                    "index_sha256": hashlib.sha256(index_bytes).hexdigest(),
                    "index_identity": index_identity,
                },
                manifest,
                "artifact.md",
                hashlib.sha256(b"artifact\n").hexdigest(),
                self.workdir,
            )

        self.assertEqual(result["commit_status"], "complete")
        self.assertTrue(result["clean"])
        self.assertEqual(reviewed.read_bytes(), b"reviewed line  \n")

    def test_fixed_pusher_validates_unquoted_unicode_paths(self) -> None:
        """Tree validation handles spaces and non-ASCII paths without Git quoting."""
        repo = self.agents
        relative = "日本語 フォルダ/要約.md"
        target = repo / relative
        target.parent.mkdir(parents=True)
        target.write_text("reviewed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", relative], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-q",
                "-m",
                "unicode path",
            ],
            check=True,
        )
        head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        oid = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", f"{head}:{relative}"], text=True
        ).strip()

        PUSH_MODULE.validate_dirty_entry(
            str(repo),
            head,
            {"path": relative, "mode": "100644", "git_blob_oid": oid},
        )
        PUSH_MODULE.validate_blob_mode(str(repo), head, relative)

    def test_local_committer_rejects_reviewed_conflict_markers(self) -> None:
        """Whitespace exceptions must not disable Git's conflict-marker guard."""
        repo = self.agents
        git_dir = str(repo / ".git")
        base = repo / "base.md"
        base.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-q",
                "-m",
                "base",
            ],
            check=True,
        )
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        reviewed = repo / "reviewed.md"
        reviewed.write_text("<<<<<<< ours\nvalue\n=======\nother\n>>>>>>> theirs\n")
        reviewed_oid = COMMITTER_MODULE.write_blob(
            str(repo), git_dir, reviewed.read_bytes()
        )
        artifact = repo / "artifact.md"
        artifact.write_text("artifact\n", encoding="utf-8")
        manifest = {
            "approved_dirty_entries": [
                {
                    "path": "reviewed.md",
                    "git_blob_oid": reviewed_oid,
                    "mode": "100644",
                }
            ],
            "commit_groups": [
                {
                    "message": "approved content",
                    "paths": ["reviewed.md", "artifact.md"],
                }
            ],
        }

        with self.assertRaises(subprocess.CalledProcessError):
            COMMITTER_MODULE.commit_groups(
                str(repo),
                git_dir,
                str(self.fake_gitleaks),
                {
                    "local_head": before,
                    "dirty_digest": hashlib.sha256(b"").hexdigest(),
                },
                manifest,
                "artifact.md",
                hashlib.sha256(b"artifact\n").hexdigest(),
                self.workdir,
            )
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip(),
            before,
        )

    def test_local_committer_main_resumes_after_agents_commit(self) -> None:
        """A sealed partial result resumes the uncommitted User Vault only."""
        resume_root = self.workdir / "resume-main"
        resume_root.mkdir()
        agents_root = resume_root / "agents"
        user_root = resume_root / "user"
        agents_root.mkdir()
        user_root.mkdir()
        advisory = agents_root / "advisory.md"
        summary = user_root / "summary.md"
        advisory.write_text("advisory\n", encoding="utf-8")
        summary.write_text("summary\n", encoding="utf-8")
        runtime = {
            "agents_vault_root": str(agents_root),
            "agents_git_dir": str(agents_root / ".git"),
            "user_vault_root": str(user_root),
            "user_git_dir": str(user_root / ".git"),
            "gitleaks_bin": str(self.fake_gitleaks),
            "publisher_git_name": "Fixture",
            "publisher_git_email": "fixture@example.invalid",
        }
        agents_before = "a" * 40
        agents_after = "b" * 40
        user_before = "c" * 40
        clean_digest = hashlib.sha256(b"").hexdigest()
        pre = {
            "agents_vault": {
                "local_head": agents_before,
                "dirty_digest": "d" * 64,
                "dirty_paths": [],
                "dirty_entries": [],
                "dirty_metadata": [],
                "staged_paths": [],
                "index_entries": [],
                "index_sha256": "0" * 64,
            },
            "user_vault": {
                "local_head": user_before,
                "dirty_digest": "e" * 64,
                "dirty_paths": [],
                "dirty_entries": [],
                "dirty_metadata": [],
                "staged_paths": [],
                "index_entries": [],
                "index_sha256": "0" * 64,
            },
        }
        collection = {
            "daily_pipeline_status": "complete",
            "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
            "advisory_sha256": hashlib.sha256(advisory.read_bytes()).hexdigest(),
            "notification_result": "none",
        }
        plan = {
            "summary_target": str(summary),
            "advisory_target": str(advisory),
        }
        context = {
            "runtime": runtime,
            "pre_collection_state": pre,
            "verified_collection": collection,
            "artifact_plan": plan,
        }
        review = {
            "outcome": "approved",
            "agents_vault": {"publication_mode": "sweep", "deferred_cleanup": []},
            "user_vault": {"publication_mode": "sweep", "deferred_cleanup": []},
            "next_action": None,
        }
        context_path = resume_root / "context.json"
        context_path.write_text(json.dumps(context), encoding="utf-8")
        review["publication_context_sha256"] = hashlib.sha256(
            context_path.read_bytes()
        ).hexdigest()
        review_path = resume_root / "review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        input_paths = []
        for name, value in (
            ("runtime.json", runtime),
            ("pre.json", pre),
            ("collection.json", collection),
            ("plan.json", plan),
        ):
            path = resume_root / name
            path.write_text(json.dumps(value), encoding="utf-8")
            input_paths.append(path)
        output = resume_root / "commit-result.json"
        partial_result = {
            "outcome": "partial_publication",
            "phase": "local_commit",
            "publication_context_sha256": review["publication_context_sha256"],
            "agents_vault": {
                "commit_status": "failed",
                "commit_hashes": [agents_after],
                "pre_local_head": agents_before,
                "local_head": agents_after,
                "pre_dirty_digest": "d" * 64,
                "post_dirty_digest": clean_digest,
                "clean": True,
                "publication_mode": "sweep",
                "deferred_cleanup": [],
            },
            "user_vault": {
                "commit_status": "not_started",
                "commit_hashes": [],
                "pre_local_head": user_before,
                "local_head": user_before,
                "pre_dirty_digest": "e" * 64,
                "post_dirty_digest": "f" * 64,
                "clean": False,
                "publication_mode": "sweep",
                "deferred_cleanup": [],
            },
            "publication_mode": {"agents_vault": "sweep", "user_vault": "sweep"},
            "deferred_cleanup": {"agents_vault": [], "user_vault": []},
            "evidence_finalization_commit": None,
        }
        actual_agents = {
            "commit_status": "complete",
            "commit_hashes": [agents_after],
            "pre_local_head": agents_before,
            "local_head": agents_after,
            "pre_dirty_digest": "d" * 64,
            "post_dirty_digest": clean_digest,
            "clean": True,
            "publication_mode": "sweep",
            "deferred_cleanup": [],
        }
        user_result = {
            "commit_status": "complete",
            "commit_hashes": ["1" * 40],
            "pre_local_head": user_before,
            "local_head": "1" * 40,
            "pre_dirty_digest": "e" * 64,
            "post_dirty_digest": clean_digest,
            "clean": True,
            "publication_mode": "sweep",
            "deferred_cleanup": [],
        }
        captured = {
            "agents_vault": {
                "dirty_paths": [],
                "dirty_entries": [],
                "dirty_metadata": [],
                "staged_paths": [],
                "index_entries": [],
                "index_sha256": "0" * 64,
                "local_head": agents_after,
            },
            "user_vault": {
                "dirty_paths": ["summary.md"],
                "dirty_entries": [
                    {
                        "path": "summary.md",
                        "mode": "100644",
                        "git_blob_oid": "2" * 40,
                    }
                ],
                "dirty_metadata": [
                    {
                        "path": "summary.md",
                        "exists": True,
                        "size": summary.stat().st_size,
                        "mtime_ns": summary.stat().st_mtime_ns,
                        "st_mode": summary.stat().st_mode,
                    }
                ],
                "staged_paths": [],
                "index_entries": [],
                "index_sha256": "0" * 64,
                "local_head": user_before,
                "dirty_digest": "f" * 64,
            },
        }
        partial_result["resumable_state"] = captured
        completed_capture = {
            "agents_vault": captured["agents_vault"],
            "user_vault": {"dirty_paths": [], "local_head": "1" * 40},
        }
        output.write_text(json.dumps(partial_result), encoding="utf-8")

        with mock.patch.object(
            COMMITTER_MODULE, "validate_final_worktree"
        ), mock.patch.object(
            COMMITTER_MODULE, "current_state", return_value=actual_agents
        ), mock.patch.object(
            COMMITTER_MODULE, "capture_state",
            side_effect=[captured, captured, completed_capture]
        ), mock.patch.object(
            COMMITTER_MODULE, "validate_post_commit_state"
        ), mock.patch.object(
            COMMITTER_MODULE, "commit_groups", return_value=user_result
        ) as resumed_commit:
            status = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )

        self.assertEqual(status, 0)
        result = json.loads(output.read_text())
        self.assertEqual(result["outcome"], "ready_to_push")
        self.assertEqual(result["agents_vault"]["commit_status"], "complete")
        self.assertEqual(result["agents_vault"]["commit_hashes"], [agents_after])
        self.assertEqual(result["user_vault"], user_result)
        resumed_commit.assert_called_once()

        complete_partial = json.loads(json.dumps(partial_result))
        complete_partial["agents_vault"]["commit_status"] = "complete"
        output.write_text(json.dumps(complete_partial), encoding="utf-8")
        with mock.patch.object(
            COMMITTER_MODULE, "validate_final_worktree"
        ), mock.patch.object(
            COMMITTER_MODULE, "current_state", return_value=actual_agents
        ), mock.patch.object(
            COMMITTER_MODULE, "capture_state",
            side_effect=[captured, captured, completed_capture]
        ), mock.patch.object(
            COMMITTER_MODULE, "validate_post_commit_state"
        ), mock.patch.object(
            COMMITTER_MODULE, "commit_groups", return_value=user_result
        ) as complete_resume_commit:
            complete_status = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )
        self.assertEqual(complete_status, 0)
        complete_result = json.loads(output.read_text())
        self.assertEqual(
            complete_result["agents_vault"]["commit_hashes"], [agents_after]
        )
        complete_resume_commit.assert_called_once()

        output.write_text(json.dumps(partial_result), encoding="utf-8")
        drifted_capture = json.loads(json.dumps(captured))
        drifted_capture["user_vault"]["dirty_digest"] = "9" * 64
        with mock.patch.object(
            COMMITTER_MODULE, "capture_state", return_value=drifted_capture
        ), mock.patch.object(
            COMMITTER_MODULE, "commit_groups"
        ) as user_drift_commit:
            user_drift = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )
        self.assertEqual(user_drift, 75)
        self.assertIn("Vaults no longer match", json.loads(output.read_text())["next_action"])
        user_drift_commit.assert_not_called()

        wrong_context = json.loads(json.dumps(partial_result))
        wrong_context["publication_context_sha256"] = "0" * 64
        output.write_text(json.dumps(wrong_context), encoding="utf-8")
        with mock.patch.object(COMMITTER_MODULE, "commit_groups") as context_commit:
            wrong_context_status = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )
        self.assertEqual(wrong_context_status, 75)
        self.assertIn("not a resumable", json.loads(output.read_text())["next_action"])
        context_commit.assert_not_called()

        missing_state = json.loads(json.dumps(partial_result))
        missing_state.pop("resumable_state")
        output.write_text(json.dumps(missing_state), encoding="utf-8")
        with mock.patch.object(COMMITTER_MODULE, "commit_groups") as state_commit:
            missing_state_status = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )
        self.assertEqual(missing_state_status, 75)
        self.assertIn("not a resumable", json.loads(output.read_text())["next_action"])
        state_commit.assert_not_called()

        output.write_text(json.dumps(partial_result), encoding="utf-8")
        drifted_agents = dict(actual_agents, local_head="9" * 40)
        with mock.patch.object(
            COMMITTER_MODULE, "validate_final_worktree"
        ), mock.patch.object(
            COMMITTER_MODULE, "current_state", return_value=drifted_agents
        ), mock.patch.object(
            COMMITTER_MODULE, "capture_state", return_value=captured
        ), mock.patch.object(
            COMMITTER_MODULE, "commit_groups"
        ) as rejected_commit:
            rejected = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )
        self.assertEqual(rejected, 75)
        self.assertIn(
            "Agents Vault no longer matches",
            json.loads(output.read_text())["next_action"],
        )
        rejected_commit.assert_not_called()

        output.unlink()
        unchanged_user = {
            "commit_status": "not_started",
            "commit_hashes": [],
            "pre_local_head": user_before,
            "local_head": user_before,
            "pre_dirty_digest": "e" * 64,
            "post_dirty_digest": "e" * 64,
            "clean": False,
        }
        with mock.patch.object(
            COMMITTER_MODULE,
            "current_state",
            side_effect=[actual_agents, unchanged_user],
        ):
            fatal_status = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    "0" * 64,
                    str(output),
                ]
            )
        self.assertEqual(fatal_status, 75)
        fatal_result = json.loads(output.read_text())
        self.assertEqual(fatal_result["outcome"], "partial_publication")
        self.assertEqual(fatal_result["agents_vault"]["local_head"], agents_after)
        self.assertEqual(
            fatal_result["agents_vault"]["commit_hashes"], [agents_after]
        )

    def test_local_committer_malformed_input_emits_blocked_result(self) -> None:
        """Convert malformed early input into status 75 and structured JSON."""
        invalid = self.workdir / "invalid-committer-runtime.json"
        invalid.write_bytes(b"\xff")
        placeholder = self.workdir / "committer-placeholder.json"
        placeholder.write_text("{}", encoding="utf-8")
        output = self.workdir / "committer-result.json"
        result = subprocess.run(
            [
                str(SCRIPTS / "commit-reviewed-publication.py"),
                str(invalid),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                "/missing-installer",
                "/missing-capture",
                "review-digest",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertEqual(json.loads(output.read_text())["outcome"], "blocked")
        self.assertNotIn("Traceback", result.stderr)

        runtime = {
            "agents_vault_root": str(self.agents),
            "agents_git_dir": str(self.agents / ".git"),
            "user_vault_root": str(self.user),
            "user_git_dir": str(self.user / ".git"),
        }
        invalid.write_text(json.dumps(runtime), encoding="utf-8")
        malformed_pre = self.workdir / "committer-list-pre.json"
        malformed_pre.write_text("[]", encoding="utf-8")
        result = subprocess.run(
            [
                str(SCRIPTS / "commit-reviewed-publication.py"),
                str(invalid),
                str(malformed_pre),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                "/missing-installer",
                "/missing-capture",
                hashlib.sha256(placeholder.read_bytes()).hexdigest(),
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertEqual(json.loads(output.read_text())["outcome"], "blocked")
        self.assertNotIn("Traceback", result.stderr)

    def test_partial_failure_without_context_is_valid_but_not_resumable(self) -> None:
        """Preserve early local progress without fabricating resume bindings."""
        before = {
            "agents_vault": {"local_head": "a" * 40, "dirty_digest": "d" * 64},
            "user_vault": {"local_head": "b" * 40, "dirty_digest": "e" * 64},
        }
        progressed = {
            "commit_status": "failed",
            "commit_hashes": ["c" * 40],
            "pre_local_head": "a" * 40,
            "local_head": "c" * 40,
            "pre_dirty_digest": "d" * 64,
            "post_dirty_digest": hashlib.sha256(b"").hexdigest(),
            "clean": True,
        }
        unchanged = {
            "commit_status": "not_started",
            "commit_hashes": [],
            "pre_local_head": "b" * 40,
            "local_head": "b" * 40,
            "pre_dirty_digest": "e" * 64,
            "post_dirty_digest": "e" * 64,
            "clean": False,
        }
        with mock.patch.object(
            COMMITTER_MODULE, "current_state", side_effect=[progressed, unchanged]
        ):
            partial = COMMITTER_MODULE.result_after_failure(
                {
                    "agents_vault_root": "/agents",
                    "agents_git_dir": "/agents/.git",
                    "user_vault_root": "/user",
                    "user_git_dir": "/user/.git",
                },
                before,
                {"daily_pipeline_status": "complete"},
                {"summary_target": "/user/summary.md", "advisory_target": "/agents/advisory.md"},
                "early failure",
            )
        self.assertEqual(partial["outcome"], "partial_publication")
        self.assertNotIn("publication_context_sha256", partial)
        self.assertNotIn("resumable_state", partial)
        schema = json.loads(
            (SKILL_ROOT / "references" / "publication-commit-result.schema.json").read_text()
        )
        CANONICAL_MODULE.validate(partial, schema, schema)
        incomplete_binding = dict(
            partial, publication_context_sha256="f" * 64
        )
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(incomplete_binding, schema, schema)

        resumable_snapshot = {
            "agents_vault": {"local_head": "c" * 40},
            "user_vault": {"local_head": "b" * 40},
        }
        deferred = {
            "agents_vault": [],
            "user_vault": [
                {"path": ".codex-handoff/unsafe.md", "reason": "guard rejection"}
            ],
        }
        with mock.patch.object(
            COMMITTER_MODULE, "current_state", side_effect=[progressed, unchanged]
        ), mock.patch.object(
            COMMITTER_MODULE, "capture_state", return_value=resumable_snapshot
        ):
            resumable = COMMITTER_MODULE.result_after_failure(
                {
                    "agents_vault_root": "/agents",
                    "agents_git_dir": "/agents/.git",
                    "user_vault_root": "/user",
                    "user_git_dir": "/user/.git",
                },
                before,
                {"daily_pipeline_status": "complete"},
                {
                    "summary_target": "/user/summary.md",
                    "advisory_target": "/agents/advisory.md",
                },
                "user CAS race",
                publication_context_sha256="f" * 64,
                capture="/capture",
                runtime_file="/runtime",
                publication_modes={
                    "agents_vault": "sweep",
                    "user_vault": "own_only",
                },
                deferred_cleanup=deferred,
            )
        self.assertEqual(
            resumable["publication_mode"],
            {"agents_vault": "sweep", "user_vault": "own_only"},
        )
        self.assertEqual(resumable["deferred_cleanup"], deferred)
        self.assertEqual(resumable["resumable_state"], resumable_snapshot)
        self.assertEqual(
            resumable["agents_vault"]["publication_mode"], "sweep"
        )
        self.assertEqual(
            resumable["user_vault"]["publication_mode"], "own_only"
        )

    def test_failure_state_timeout_never_fabricates_unchanged_vaults(self) -> None:
        """Use explicit unknown sentinels when post-failure recapture times out."""
        before = {
            "agents_vault": {
                "local_head": "a" * 40,
                "dirty_digest": "d" * 64,
                "dirty_lines": [],
            },
            "user_vault": {
                "local_head": "b" * 40,
                "dirty_digest": "e" * 64,
                "dirty_lines": [],
            },
        }
        with mock.patch.object(
            COMMITTER_MODULE,
            "current_state",
            side_effect=subprocess.TimeoutExpired(["git"], 90),
        ):
            result = COMMITTER_MODULE.result_after_failure(
                {
                    "agents_vault_root": "/agents",
                    "agents_git_dir": "/agents/.git",
                    "user_vault_root": "/user",
                    "user_git_dir": "/user/.git",
                },
                before,
                {"daily_pipeline_status": "complete"},
                {
                    "summary_target": "/user/summary.md",
                    "advisory_target": "/agents/advisory.md",
                },
                "state recapture timed out",
            )

        self.assertEqual(result["outcome"], "partial_publication")
        for key in ("agents_vault", "user_vault"):
            vault = result[key]
            self.assertEqual(vault["commit_status"], "failed")
            self.assertEqual(vault["local_head"], "0" * 40)
            self.assertEqual(vault["post_dirty_digest"], "0" * 64)
            self.assertFalse(vault["clean"])
            self.assertNotEqual(vault["local_head"], vault["pre_local_head"])
            self.assertNotEqual(
                vault["post_dirty_digest"], vault["pre_dirty_digest"]
            )

    def test_local_committer_rejects_staged_only_before_head_changes(self) -> None:
        """Fail before commit when reviewed index bytes differ from the worktree."""
        repo = self.agents
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        staged = repo / "staged.md"
        staged.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "staged.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True
        )
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        staged.write_text("reviewed staged bytes\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "staged.md"], check=True)
        reviewed_oid = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", ":staged.md"], text=True
        ).strip()
        staged.write_text("base\n", encoding="utf-8")
        artifact = repo / "artifact.md"
        artifact.write_text("artifact\n", encoding="utf-8")
        manifest = {
            "approved_dirty_entries": [
                {
                    "path": "staged.md",
                    "git_blob_oid": reviewed_oid,
                    "mode": "100644",
                }
            ],
            "commit_groups": [
                {
                    "message": "approved staged",
                    "paths": ["staged.md", "artifact.md"],
                }
            ],
        }
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.validate_final_worktree(
                str(repo),
                str(repo / ".git"),
                manifest,
                "artifact.md",
                hashlib.sha256(b"artifact\n").hexdigest(),
            )
        after = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        self.assertEqual(after, before)

        deleted = repo / "deleted.md"
        deleted.write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "deleted.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "tracked deletion"],
            check=True,
        )
        deletion_head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(["git", "-C", str(repo), "rm", "-q", "deleted.md"], check=True)
        deleted.write_text("tracked\n", encoding="utf-8")
        deletion_manifest = {
            "approved_dirty_entries": [
                {"path": "deleted.md", "git_blob_oid": None, "mode": None}
            ],
            "commit_groups": [
                {
                    "message": "approved deletion",
                    "paths": ["deleted.md", "artifact.md"],
                }
            ],
        }
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.validate_final_worktree(
                str(repo),
                str(repo / ".git"),
                deletion_manifest,
                "artifact.md",
                hashlib.sha256(b"artifact\n").hexdigest(),
            )
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip(),
            deletion_head,
        )

    def test_local_committer_barrier_failure_keeps_head_unchanged(self) -> None:
        """Build commit objects first but never advance HEAD after a scope race."""
        repo = self.agents
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        base = repo / "base.md"
        base.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True
        )
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        artifact = repo / "artifact.md"
        artifact.write_text("artifact\n", encoding="utf-8")
        manifest = {
            "approved_dirty_entries": [],
            "commit_groups": [
                {"message": "publish artifact", "paths": ["artifact.md"]}
            ],
        }

        def reject_race() -> None:
            raise COMMITTER_MODULE.CommitError("fixture scope race")

        index_bytes, index_identity = COMMITTER_MODULE.index_file_contract(
            repo / ".git" / "index"
        )
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.commit_groups(
                str(repo),
                str(repo / ".git"),
                str(self.fake_gitleaks),
                {
                    "local_head": before,
                    "dirty_digest": hashlib.sha256(b"").hexdigest(),
                    "index_sha256": hashlib.sha256(index_bytes).hexdigest(),
                    "index_identity": index_identity,
                },
                manifest,
                "artifact.md",
                hashlib.sha256(b"artifact\n").hexdigest(),
                self.workdir,
                before_update=reject_race,
            )
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip(),
            before,
        )

    def test_local_committer_index_cas_preserves_concurrent_staging(self) -> None:
        """Reject an index race without overwriting the third-party staged entry."""
        repo = self.user
        (repo / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo),
                "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid",
                "commit", "-q", "-m", "base",
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
            check=True,
        )
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        concurrent = repo / "concurrent.md"
        concurrent.write_text("third party staged\n", encoding="utf-8")
        pre = CAPTURE_MODULE.capture(str(repo), include_local_history=True)
        artifact = repo / "summary.md"
        artifact.write_text("verified summary\n", encoding="utf-8")

        def stage_concurrently() -> None:
            subprocess.run(
                ["git", "-C", str(repo), "add", "concurrent.md"], check=True
            )

        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "shared Git index raced"
        ):
            COMMITTER_MODULE.commit_groups(
                str(repo),
                str(repo / ".git"),
                str(self.fake_gitleaks),
                pre,
                {
                    "approved_dirty_entries": [],
                    "commit_groups": [
                        {"message": "docs: publish summary", "paths": ["summary.md"]}
                    ],
                },
                "summary.md",
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
                self.workdir,
                before_update=stage_concurrently,
                publication_mode="own_only",
            )
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip(),
            before,
        )
        self.assertTrue(
            subprocess.check_output(
                ["git", "-C", str(repo), "ls-files", "--stage", "concurrent.md"],
                text=True,
            ).strip()
        )

    def test_evidence_finalizer_index_cas_preserves_concurrent_staging(self) -> None:
        """Apply the same expected inode/digest CAS to evidence finalization."""
        repo = self.agents
        target = "tasks/standing.md"
        subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo),
                "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid",
                "commit", "-q", "-m", "base",
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
            check=True,
        )
        baseline = CAPTURE_MODULE.capture(str(repo), include_local_history=True)
        candidate_blob = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
            input=b"candidate standing task\n",
            check=True,
            capture_output=True,
        ).stdout.decode("ascii").strip()
        candidate_index, candidate_contract = FINALIZER_MODULE.prepare_shared_index_candidate(
            str(repo),
            str(repo / ".git"),
            baseline,
            target,
            candidate_blob,
            self.workdir,
        )
        concurrent = repo / "concurrent-evidence.md"
        concurrent.write_text("third party staged\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", concurrent.name], check=True
        )
        progress = {"head_updated": False, "index_updated": False}
        base_head = str(baseline["local_head"])
        try:
            with self.assertRaisesRegex(
                FINALIZER_MODULE.FinalizationError, "shared Git index raced"
            ):
                FINALIZER_MODULE.publish_evidence_head_and_index(
                    str(repo),
                    str(repo / ".git"),
                    baseline,
                    base_head,
                    base_head,
                    candidate_index,
                    candidate_contract,
                    progress,
                )
        finally:
            ATOMIC_FILE_OPS_MODULE.retain_path_no_replace(
                candidate_index,
                expected=candidate_contract,
                label="test evidence shared-index candidate",
                prefix=".test-evidence-index-retained-",
            )
        self.assertEqual(progress, {"head_updated": False, "index_updated": False})
        self.assertTrue(
            subprocess.check_output(
                ["git", "-C", str(repo), "ls-files", "--stage", concurrent.name],
                text=True,
            ).strip()
        )

    def test_fixed_fetch_pins_remote_refspec_and_environment(self) -> None:
        """Reject mutable remote/config behavior in the preflight transport."""
        oid = "a" * 40
        transport = mock.MagicMock()
        transport.__enter__.return_value = transport
        transport.__exit__.return_value = None
        transport.run.side_effect = [
            subprocess.CompletedProcess([], 0, f"{oid}\trefs/heads/main\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, oid + "\n", ""),
            subprocess.CompletedProcess([], 0, f"{oid}\trefs/heads/main\n", ""),
        ]
        local_results = [
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        with mock.patch.dict(os.environ, {"GIT_DIR": "/attacker/git"}), mock.patch.object(
            FETCH_MODULE, "IsolatedGitTransport", return_value=transport
        ) as transport_factory, mock.patch.object(
            FETCH_MODULE, "run_local_command", side_effect=local_results
        ) as local_run:
            FETCH_MODULE.fetch_main(
                "/vault/worktree", "/local/gitdir", "ssh://git@example.invalid/repo"
            )
        transport_factory.assert_called_once_with("/local/gitdir")
        fetch_call = transport.run.call_args_list[1]
        self.assertEqual(
            fetch_call.args[-2:],
            (
                "ssh://git@example.invalid/repo",
                "refs/heads/main:refs/remotes/origin/main",
            ),
        )
        update_command = local_run.call_args_list[-1].args[0]
        self.assertIn("core.fsmonitor=false", update_command)
        self.assertNotIn("origin", update_command[-2:])
        self.assertNotIn("GIT_DIR", local_run.call_args_list[-1].kwargs["env"])
        self.assertEqual(
            local_run.call_args_list[-1].kwargs["env"]["GIT_CONFIG_GLOBAL"],
            os.devnull,
        )

    def test_fixed_fetch_retries_transient_transport_exceptions(self) -> None:
        """Retry bounded ls-remote and candidate-resolution transport failures."""
        oid = "a" * 40
        before = subprocess.CompletedProcess(
            [], 0, f"{oid}\trefs/heads/main\n", ""
        )
        fetched = subprocess.CompletedProcess([], 0, "", "")
        candidate = subprocess.CompletedProcess([], 0, oid + "\n", "")
        local_results = [
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        scenarios = {
            "initial_ls_remote": [
                TRANSPORT_MODULE.TransportError("transient ls-remote"),
                before,
                fetched,
                candidate,
                before,
            ],
            "candidate_rev_parse": [
                before,
                fetched,
                TRANSPORT_MODULE.TransportError("transient rev-parse"),
                before,
                fetched,
                candidate,
                before,
            ],
        }
        for label, side_effects in scenarios.items():
            with self.subTest(label=label):
                transport = mock.MagicMock()
                transport.__enter__.return_value = transport
                transport.__exit__.return_value = None
                transport.run.side_effect = side_effects
                with mock.patch.object(
                    FETCH_MODULE, "IsolatedGitTransport", return_value=transport
                ), mock.patch.object(
                    FETCH_MODULE,
                    "run_local_command",
                    side_effect=list(local_results),
                ), mock.patch.object(FETCH_MODULE.time, "sleep") as sleep:
                    FETCH_MODULE.fetch_main(
                        "/vault/worktree",
                        "/local/gitdir",
                        "ssh://git@example.invalid/repo",
                    )
                sleep.assert_called_once_with(0.2)

    def test_fixed_fetch_invalid_utf8_uses_standard_failure_contract(self) -> None:
        """Convert malformed runtime bytes into status 75 without a traceback."""
        invalid = self.workdir / "invalid-fetch-runtime.json"
        invalid.write_bytes(b"\xff")
        result = subprocess.run(
            [str(SCRIPTS / "fetch-vault-main.py"), str(invalid)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("fixed fetch blocked:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_discord_notification_uses_immutable_link_and_deduplicates(self) -> None:
        """Send one fixed-channel link and reuse its durable delivery receipt."""
        response = json.dumps(
            {
                "success": True,
                "platform": "discord",
                "chat_id": "1234567890123456789",
                "message_id": "2234567890123456789",
            }
        )
        workdir, runtime, initial, counter, arguments = self.notification_fixture(
            response + "\n", 0
        )
        receipt = workdir / "receipt.json"
        effective = workdir / "effective.json"
        status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-1",
                str(receipt),
                str(effective),
            ]
        )
        self.assertEqual(status, 0)
        observed = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(observed["status"], "delivered")
        self.assertEqual(observed["message_id"], "2234567890123456789")
        self.assertEqual(observed["summary_commit"], "a" * 40)
        self.assertIn("/blob/" + "a" * 40 + "/", observed["summary_url"])
        self.assertIn("10%20Prompt", observed["summary_url"])
        sent_arguments = json.loads(arguments.read_text(encoding="utf-8"))
        self.assertEqual(
            sent_arguments[:4],
            ["send", "--to", "discord:1234567890123456789", "--json"],
        )
        self.assertIn(observed["summary_url"], sent_arguments[4])
        self.assertNotIn("private summary body", sent_arguments[4])
        effective_result = json.loads(effective.read_text(encoding="utf-8"))
        self.assertIn("discord:delivered", effective_result["notification_result"])

        second_receipt = workdir / "receipt-2.json"
        second_effective = workdir / "effective-2.json"
        second_status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-2",
                str(second_receipt),
                str(second_effective),
            ]
        )
        self.assertEqual(second_status, 0)
        self.assertEqual(
            json.loads(second_receipt.read_text(encoding="utf-8"))["status"],
            "already_delivered",
        )
        self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["called"])

    def test_discord_notification_rejects_corrupt_delivery_receipt(self) -> None:
        """Never suppress delivery from an invalid persistent success receipt."""
        response = json.dumps(
            {
                "success": True,
                "platform": "discord",
                "chat_id": "1234567890123456789",
                "message_id": "2234567890123456789",
            }
        )
        workdir, runtime, initial, counter, _arguments = self.notification_fixture(
            response + "\n", 0
        )
        first_status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-receipt-valid",
                str(workdir / "receipt-valid.json"),
                str(workdir / "effective-valid.json"),
            ]
        )
        self.assertEqual(first_status, 0)
        delivery = next((workdir / "notification-state").rglob("delivery.json"))
        corrupt = json.loads(delivery.read_text(encoding="utf-8"))
        corrupt["message_id"] = "not-a-snowflake"
        delivery.write_text(json.dumps(corrupt), encoding="utf-8")

        second_status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-receipt-corrupt",
                str(workdir / "receipt-corrupt.json"),
                str(workdir / "effective-corrupt.json"),
            ]
        )
        self.assertEqual(second_status, 75)
        self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["called"])
        self.assertFalse((workdir / "receipt-corrupt.json").exists())

    def test_discord_notification_recovery_preserves_originating_run_id(self) -> None:
        """Bind a crash-recovered durable receipt to the run that sent it."""
        response = json.dumps(
            {
                "success": True,
                "platform": "discord",
                "chat_id": "1234567890123456789",
                "message_id": "2234567890123456789",
            }
        )
        workdir, runtime, initial, counter, _arguments = self.notification_fixture(
            response + "\n", 0
        )
        first_status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-origin",
                str(workdir / "origin-receipt.json"),
                str(workdir / "origin-effective.json"),
            ]
        )
        self.assertEqual(first_status, 0)
        state_root = workdir / "notification-state"
        delivery_path = next(state_root.rglob("delivery.json"))
        delivery_path.unlink()

        recovery_receipt = workdir / "recovery-receipt.json"
        recovery_status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-recovery",
                str(recovery_receipt),
                str(workdir / "recovery-effective.json"),
            ]
        )
        self.assertEqual(recovery_status, 0)
        recovered_delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
        self.assertEqual(recovered_delivery["run_id"], "run-origin")
        self.assertEqual(recovered_delivery["attempt"], 1)
        self.assertEqual(
            json.loads(recovery_receipt.read_text(encoding="utf-8"))["run_id"],
            "run-recovery",
        )
        self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["called"])

    def test_discord_notification_rejects_corrupt_delivered_result(self) -> None:
        """Require a strict result schema before recovering delivery.json."""
        response = json.dumps(
            {
                "success": True,
                "platform": "discord",
                "chat_id": "1234567890123456789",
                "message_id": "2234567890123456789",
            }
        )
        workdir, runtime, initial, counter, _arguments = self.notification_fixture(
            response + "\n", 0
        )
        first_status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-result-valid",
                str(workdir / "result-receipt-valid.json"),
                str(workdir / "result-effective-valid.json"),
            ]
        )
        self.assertEqual(first_status, 0)
        state_root = workdir / "notification-state"
        next(state_root.rglob("delivery.json")).unlink()
        result_path = next(state_root.rglob("attempt-000001-result.json"))
        corrupt = json.loads(result_path.read_text(encoding="utf-8"))
        corrupt["message_id"] = "1"
        result_path.write_text(json.dumps(corrupt), encoding="utf-8")

        second_status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-result-corrupt",
                str(workdir / "result-receipt-corrupt.json"),
                str(workdir / "result-effective-corrupt.json"),
            ]
        )
        self.assertEqual(second_status, 75)
        self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["called"])

    def test_discord_notification_state_root_creation_fsyncs_workdir(self) -> None:
        """Make the first notification-state directory entry durable before send."""
        workdir = self.root / "notification-state-fsync-workdir"
        workdir.mkdir()
        workdir_identity = (workdir.stat().st_dev, workdir.stat().st_ino)
        fsynced_workdir = False
        real_fsync = os.fsync

        def recording_fsync(descriptor: int) -> None:
            nonlocal fsynced_workdir
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) == workdir_identity:
                fsynced_workdir = True
            real_fsync(descriptor)

        with mock.patch.object(
            NOTIFICATION_MODULE.os, "fsync", side_effect=recording_fsync
        ):
            descriptor = NOTIFICATION_MODULE.ensure_private_directory(
                workdir / "notification-state"
            )
            os.close(descriptor)
        self.assertTrue(fsynced_workdir)

    def test_evidence_review_contract_allows_only_sanitized_notification(self) -> None:
        """Authorize bounded notification states while rejecting raw backend text."""
        prompt = (
            SKILL_ROOT / "assets" / "daily-it-news.evidence-review.prompt.md"
        ).read_text(encoding="utf-8")
        for value in ("delivered", "already_delivered", "failed", "ambiguous"):
            self.assertIn(f"`{value}`", prompt)
        self.assertIn("raw Hermes", prompt)
        self.assertIn("summary body", prompt)
        self.assertIn("model text", prompt)

    def test_discord_notification_retries_only_definite_backend_failure(self) -> None:
        """Retry explicit rejection without persisting raw backend details."""
        response = json.dumps(
            {
                "success": False,
                "platform": "discord",
                "error": "sensitive backend detail",
            }
        )
        workdir, runtime, initial, counter, _arguments = self.notification_fixture(
            response + "\n", 1
        )
        receipt = workdir / "failed-receipt.json"
        effective = workdir / "failed-effective.json"
        status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-failed",
                str(receipt),
                str(effective),
            ]
        )
        self.assertEqual(status, 75)
        observed_text = receipt.read_text(encoding="utf-8")
        observed = json.loads(observed_text)
        self.assertEqual(observed["status"], "failed")
        self.assertEqual(observed["error_code"], "backend_rejected")
        self.assertNotIn("sensitive backend detail", observed_text)
        self.assertEqual(len(counter.read_text(encoding="utf-8").splitlines()), 3)
        self.assertTrue(effective.is_file())

    def test_discord_notification_retries_spawn_failure_only_in_later_run(self) -> None:
        """Retry a pre-delivery failure later, never twice in the same run."""
        response = json.dumps(
            {
                "success": True,
                "platform": "discord",
                "chat_id": "1234567890123456789",
                "message_id": "2234567890123456789",
            }
        )
        workdir, runtime, initial, counter, _arguments = self.notification_fixture(
            response + "\n", 0
        )
        runtime_payload = json.loads(runtime.read_text(encoding="utf-8"))
        hermes = Path(runtime_payload["hermes_bin"])
        working_hermes = hermes.read_bytes()
        hermes.write_text("#!/definitely/missing/interpreter\n", encoding="utf-8")
        hermes.chmod(0o755)
        receipt = workdir / "spawn-failed-receipt.json"
        effective = workdir / "spawn-failed-effective.json"
        status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-spawn-failed",
                str(receipt),
                str(effective),
            ]
        )
        self.assertEqual(status, 75)
        observed = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(observed["status"], "failed")
        self.assertEqual(observed["error_code"], "spawn_failed")
        self.assertEqual(observed["attempts_this_run"], 1)
        self.assertFalse(counter.exists())

        second_receipt = workdir / "spawn-failed-receipt-2.json"
        second_status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-spawn-failed",
                str(second_receipt),
                str(workdir / "spawn-failed-effective-2.json"),
            ]
        )
        self.assertEqual(second_status, 75)
        self.assertEqual(
            json.loads(second_receipt.read_text(encoding="utf-8"))[
                "attempts_this_run"
            ],
            0,
        )
        state_entries = list((workdir / "notification-state").rglob("attempt-*.json"))
        self.assertEqual(len(state_entries), 2)

        hermes.write_bytes(working_hermes)
        hermes.chmod(0o755)
        recovered_receipt = workdir / "spawn-recovered-receipt.json"
        recovered_status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-spawn-recovered",
                str(recovered_receipt),
                str(workdir / "spawn-recovered-effective.json"),
            ]
        )
        self.assertEqual(recovered_status, 0)
        recovered = json.loads(recovered_receipt.read_text(encoding="utf-8"))
        self.assertEqual(recovered["status"], "delivered")
        self.assertEqual(recovered["message_id"], "2234567890123456789")
        self.assertEqual(recovered["attempts_this_run"], 1)
        self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["called"])
        state_entries = list((workdir / "notification-state").rglob("attempt-*.json"))
        self.assertEqual(len(state_entries), 4)

    def test_discord_notification_bounds_oversized_response_without_retry(self) -> None:
        """Stop and classify an oversized post-send response as ambiguous."""
        workdir, runtime, initial, counter, _arguments = self.notification_fixture(
            "x" * (NOTIFICATION_MODULE.MAX_PROCESS_OUTPUT_BYTES + 1), 0
        )
        receipt = workdir / "oversized-receipt.json"
        status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-oversized",
                str(receipt),
                str(workdir / "oversized-effective.json"),
            ]
        )
        self.assertEqual(status, 75)
        observed = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(observed["status"], "ambiguous")
        self.assertEqual(observed["error_code"], "response_too_large")
        self.assertEqual(observed["attempts_this_run"], 1)
        self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["called"])

    def test_discord_notification_does_not_retry_ambiguous_response(self) -> None:
        """Treat an unverifiable post-send response as at-most-once ambiguity."""
        workdir, runtime, initial, counter, _arguments = self.notification_fixture(
            "not-json\n", 1
        )
        receipt = workdir / "ambiguous-receipt.json"
        effective = workdir / "ambiguous-effective.json"
        status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-ambiguous",
                str(receipt),
                str(effective),
            ]
        )
        self.assertEqual(status, 75)
        self.assertEqual(
            json.loads(receipt.read_text(encoding="utf-8"))["status"], "ambiguous"
        )
        second_status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-ambiguous-2",
                str(workdir / "ambiguous-receipt-2.json"),
                str(workdir / "ambiguous-effective-2.json"),
            ]
        )
        self.assertEqual(second_status, 75)
        self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["called"])

    def test_discord_notification_requires_complete_two_vault_push(self) -> None:
        """Never call Hermes before both Vaults are durably published."""
        response = json.dumps(
            {
                "success": True,
                "platform": "discord",
                "chat_id": "1234567890123456789",
                "message_id": "2234567890123456789",
            }
        )
        workdir, runtime, initial, counter, _arguments = self.notification_fixture(
            response + "\n", 0
        )
        initial_result = json.loads(initial.read_text(encoding="utf-8"))
        initial_result["agents_vault"]["push_status"] = "failed"
        initial.write_text(json.dumps(initial_result), encoding="utf-8")
        status = NOTIFICATION_MODULE.main(
            [
                "send-it-news-discord-notification.py",
                str(runtime),
                str(initial),
                "run-blocked",
                str(workdir / "blocked-receipt.json"),
                str(workdir / "blocked-effective.json"),
            ]
        )
        self.assertEqual(status, 75)
        self.assertFalse(counter.exists())

    def test_resolver_uses_catalog_and_relative_config(self) -> None:
        """Resolve roots and Git directories without tracked personal paths."""
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        context = json.loads(result.stdout)
        self.assertEqual(context["agents_vault_root"], str(self.agents.resolve()))
        self.assertEqual(context["standing_task_id"], "TSK-STANDING")
        self.assertEqual(context["publisher_git_name"], "Fixture Publisher")
        self.assertEqual(context["publisher_git_email"], "publisher@example.invalid")
        self.assertEqual(context["hermes_bin"], str(self.fake_hermes.resolve()))
        self.assertEqual(
            context["discord_news_target"], "discord:1234567890123456789"
        )
        self.assertTrue(Path(context["agents_git_dir"]).is_absolute())

    def test_resolver_rejects_mutable_discord_channel_name(self) -> None:
        """Bind notifications to one snowflake rather than a mutable channel name."""
        invalid = self.workdir / "invalid-discord-target.local.env"
        invalid.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "DISCORD_NEWS_TARGET=discord:1234567890123456789",
                "DISCORD_NEWS_TARGET=discord:it-news",
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(invalid),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("DISCORD_NEWS_TARGET", result.stderr)

    def test_resolver_rejects_invalid_private_publisher_identity(self) -> None:
        """Fail closed before runtime context creation for malformed identity."""
        invalid = self.workdir / "invalid-publisher.local.env"
        invalid.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "PUBLISHER_GIT_EMAIL=publisher@example.invalid",
                "PUBLISHER_GIT_EMAIL=not-an-email",
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(invalid),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("invalid publisher Git email", result.stderr)

    def test_resolver_pins_gitleaks_major_and_uses_distinct_deadlines(self) -> None:
        """Keep scanner compatibility separate from short Git control probes."""
        self.assertEqual(
            RESOLVER_MODULE.validated_gitleaks_version("gitleaks version 8.19.0"),
            "gitleaks version 8.19.0",
        )
        self.assertEqual(
            RESOLVER_MODULE.validated_gitleaks_version("gitleaks version 8.30.1"),
            "gitleaks version 8.30.1",
        )
        with self.assertRaisesRegex(RESOLVER_MODULE.ContextError, "8.19.0"):
            RESOLVER_MODULE.validated_gitleaks_version("gitleaks version 8.18.1")
        with self.assertRaisesRegex(RESOLVER_MODULE.ContextError, "major version 8"):
            RESOLVER_MODULE.validated_gitleaks_version("gitleaks version 9.0.0")
        self.assertGreater(
            RESOLVER_MODULE.EXTERNAL_BINARY_TIMEOUT_SECONDS,
            RESOLVER_MODULE.CONTROL_COMMAND_TIMEOUT_SECONDS,
        )

    def test_resolver_retries_transient_file_provider_error_across_context(self) -> None:
        """Retry a transient lock even when it happens after catalog loading."""
        attempts = 0

        def resolve_once(_local_config, _workdir):
            nonlocal attempts
            attempts += 1
            if attempts < 5:
                raise OSError(
                    RESOLVER_MODULE.errno.EDEADLK,
                    "Resource deadlock avoided",
                )
            return {"status": "resolved"}

        with mock.patch.object(RESOLVER_MODULE.time, "sleep") as sleep:
            with mock.patch.object(
                RESOLVER_MODULE,
                "resolve_context_once",
                side_effect=resolve_once,
            ):
                context = RESOLVER_MODULE.resolve_context(self.config, self.workdir)
        self.assertEqual(context, {"status": "resolved"})
        self.assertEqual(attempts, 5)
        self.assertEqual(sleep.call_count, 4)

    def test_resolver_retries_git_wrapped_file_provider_error(self) -> None:
        """Recognize the same transient errno when Git reports it via stderr."""
        attempts = 0

        def operation():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise subprocess.CalledProcessError(
                    128,
                    ["git", "rev-parse"],
                    stderr="fatal: Resource deadlock avoided\n",
                )
            return "resolved"

        with mock.patch.object(RESOLVER_MODULE.time, "sleep") as sleep:
            observed = RESOLVER_MODULE.retry_read_only_context(operation)
        self.assertEqual(observed, "resolved")
        self.assertEqual(attempts, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_resolver_does_not_retry_permanent_context_failure(self) -> None:
        """Keep permanent Git and validation failures immediately fail-closed."""
        permanent = subprocess.CalledProcessError(
            128,
            ["git", "rev-parse"],
            stderr="fatal: not a git repository\n",
        )
        with mock.patch.object(RESOLVER_MODULE.time, "sleep") as sleep:
            with self.assertRaises(subprocess.CalledProcessError):
                RESOLVER_MODULE.retry_read_only_context(
                    mock.Mock(side_effect=permanent)
                )
        sleep.assert_not_called()

    def test_resolver_sanitizes_gitleaks_version_probe_environment(self) -> None:
        """Do not let ambient Git or Gitleaks variables control the version probe."""
        self.fake_gitleaks.write_text(
            "#!/bin/sh\n"
            "if [ \"${GIT_DIR+x}\" = x ] || [ \"${GITLEAKS_CONFIG+x}\" = x ]; then\n"
            "  exit 97\n"
            "fi\n"
            "[ \"$1\" = version ] && echo 'fixture-gitleaks 8.30.1'\n"
            "exit 0\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GIT_DIR": "/attacker/gitdir",
                "GITLEAKS_CONFIG": "/attacker/gitleaks.toml",
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_resolver_rejects_git_transport_rewrite_and_include(self) -> None:
        """Reject repository-local config that can redirect fixed Git transport."""
        subprocess.run(
            [
                "git", "-C", str(self.agents), "config", "--local",
                "url.ssh://attacker.invalid/.insteadOf", "ssh://git@example.invalid/",
            ],
            check=True,
        )
        rewritten = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config), str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rewritten.returncode, 78)
        self.assertIn("unsafe repository-local Git config", rewritten.stderr)
        subprocess.run(
            [
                "git", "-C", str(self.agents), "config", "--local", "--unset-all",
                "url.ssh://attacker.invalid/.insteadOf",
            ],
            check=True,
        )
        included = self.workdir / "attacker-git-config"
        included.write_text("[core]\n\tsshCommand = attacker\n", encoding="utf-8")
        subprocess.run(
            [
                "git", "-C", str(self.agents), "config", "--local",
                "include.path", str(included),
            ],
            check=True,
        )
        include_result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config), str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(include_result.returncode, 78)
        self.assertIn("unsafe repository-local Git config", include_result.stderr)

    def test_resolver_rejects_git_execution_config(self) -> None:
        """Reject filters, attributes indirection, and fsmonitor before collection."""
        cases = (
            ("filter.fixture.clean", "sh -c 'exit 1'"),
            ("core.attributesFile", str(self.workdir / "attributes")),
            ("core.fsmonitor", "sh -c 'exit 1'"),
            ("alias.fixture", "!sh -c 'exit 1'"),
            ("gpg.ssh.program", "sh -c 'exit 1'"),
            ("merge.fixture.driver", "sh -c 'exit 1'"),
            ("uploadpack.packObjectsHook", "sh -c 'exit 1'"),
        )
        for key, value in cases:
            with self.subTest(key=key):
                subprocess.run(
                    [
                        "git", "-C", str(self.agents), "config", "--local",
                        key, value,
                    ],
                    check=True,
                )
                result = subprocess.run(
                    [
                        str(SCRIPTS / "resolve-runtime-context.py"),
                        str(self.config), str(self.workdir),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 78)
                self.assertIn("unsafe repository-local Git config", result.stderr)
                subprocess.run(
                    [
                        "git", "-C", str(self.agents), "config", "--local",
                        "--unset-all", key,
                    ],
                    check=True,
                )

    def test_resolver_rejects_valueless_local_git_config(self) -> None:
        """Report a missing value distinctly instead of accepting an empty command key."""
        config_path = self.agents / ".git" / "config"
        original = config_path.read_text(encoding="utf-8")
        config_path.write_text(original + "\n[alias]\n\tfixture\n", encoding="utf-8")
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("config value is missing", result.stderr)

    def test_resolver_accepts_bound_absolute_gitdir_file(self) -> None:
        """Support the Vault layout while binding its indirection file."""
        marker = self.agents / ".git"
        detached = self.root / "agents-detached.git"
        marker.rename(detached)
        marker.write_text(f"gitdir: {detached}\n", encoding="utf-8")
        marker.chmod(0o644)
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        context = json.loads(result.stdout)
        self.assertEqual(context["agents_git_dir"], str(detached.resolve()))
        push_before = PUSH_MODULE.git_control_digest(str(self.agents))
        final_before = FINALIZER_MODULE.control_digest(str(self.agents))
        self.assertEqual(push_before, final_before)
        marker.chmod(0o600)
        push_after = PUSH_MODULE.git_control_digest(str(self.agents))
        final_after = FINALIZER_MODULE.control_digest(str(self.agents))
        self.assertEqual(push_after, final_after)
        self.assertNotEqual(push_before, push_after)

        marker.unlink()
        marker.symlink_to(detached, target_is_directory=True)
        rejected = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 78)
        self.assertIn("must not be a symlink", rejected.stderr)

        marker.unlink()
        for invalid_content in ("gitdir: relative.git\n", "not-a-gitdir\n"):
            marker.write_text(invalid_content, encoding="utf-8")
            rejected = subprocess.run(
                [
                    str(SCRIPTS / "resolve-runtime-context.py"),
                    str(self.config),
                    str(self.workdir),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rejected.returncode, 78)

    def test_resolver_rejects_symlink_standing_task(self) -> None:
        """Reject a deferred evidence target that cannot be safely updated."""
        standing = self.agents / "tasks" / "standing.md"
        standing.unlink()
        standing.symlink_to("auth.md")
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("standing task", result.stderr)

    def test_resolver_import_failure_returns_standard_status(self) -> None:
        """Convert missing catalog loader modules into the status-78 contract."""
        module = self.saihai / "directory_paths.py"
        module.rename(self.saihai / "directory_paths.missing")
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("runtime context resolution failed", result.stderr)

    def test_resolver_malformed_config_returns_standard_status(self) -> None:
        """Convert shlex parse failures into the status-78 contract."""
        malformed = self.workdir / "malformed.local.env"
        malformed.write_text("SAIHAI_CHECKOUT_ROOT='unterminated\n", encoding="utf-8")
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(malformed),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("runtime context resolution failed", result.stderr)

    def test_capture_includes_staged_only_path_and_index_blob(self) -> None:
        """Bind an index-only change even when the worktree matches HEAD."""
        for repo in (self.agents, self.user):
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Fixture"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "config",
                    "user.email",
                    "fixture@example.invalid",
                ],
                check=True,
            )
            (repo / "tracked.md").write_text("head\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "initial"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "branch", "-M", "main"], check=True
            )
            subprocess.run(
                ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
                check=True,
            )
        tracked = self.agents / "tracked.md"
        tracked.write_text("staged\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.agents), "add", "tracked.md"], check=True
        )
        tracked.write_text("head\n", encoding="utf-8")
        context = self.workdir / "capture-context.json"
        context.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "user_vault_root": str(self.user),
                }
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(SCRIPTS / "capture-vault-state.py"), str(context)],
            check=True,
            capture_output=True,
            text=True,
        )
        state = json.loads(result.stdout)["agents_vault"]
        expected_oid = subprocess.check_output(
            ["git", "-C", str(self.agents), "rev-parse", ":tracked.md"],
            text=True,
        ).strip()
        self.assertEqual(state["dirty_paths"], ["tracked.md"])
        self.assertEqual(state["dirty_entries"][0]["git_blob_oid"], expected_oid)
        permissions = self.agents / "permissions.md"
        permissions.write_text("same bytes\n", encoding="utf-8")
        permissions.chmod(0o640)
        before_permissions_all = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        before_permissions = before_permissions_all["agents_vault"]
        before_metadata = next(
            item for item in before_permissions["dirty_metadata"]
            if item["path"] == "permissions.md"
        )
        permissions.chmod(0o600)
        after_permissions_all = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        after_permissions = after_permissions_all["agents_vault"]
        after_metadata = next(
            item for item in after_permissions["dirty_metadata"]
            if item["path"] == "permissions.md"
        )
        self.assertNotEqual(before_metadata["st_mode"], after_metadata["st_mode"])
        self.assertNotEqual(
            before_permissions["dirty_worktree_sha256"],
            after_permissions["dirty_worktree_sha256"],
        )
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "dirty metadata changed"
        ):
            COMMITTER_MODULE.validate_installed_scope(
                before_permissions_all, after_permissions_all, {}
            )
        dangling = self.agents / "dangling-link"
        dangling.symlink_to("missing-target")
        result = subprocess.run(
            [str(SCRIPTS / "capture-vault-state.py"), str(context)],
            check=True,
            capture_output=True,
            text=True,
        )
        state = json.loads(result.stdout)["agents_vault"]
        link_entry = next(
            entry for entry in state["dirty_entries"] if entry["path"] == "dangling-link"
        )
        expected_link_oid = subprocess.run(
            ["git", "-C", str(self.agents), "hash-object", "--stdin"],
            input=b"missing-target",
            check=True,
            capture_output=True,
        ).stdout.decode("ascii").strip()
        self.assertEqual(link_entry["mode"], "120000")
        self.assertEqual(link_entry["git_blob_oid"], expected_link_oid)

        hook = self.agents / ".git" / "hooks" / "fixture"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o644)
        before_mode = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )["agents_vault"]["git_control_sha256"]
        hook.chmod(0o755)
        after_mode = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )["agents_vault"]["git_control_sha256"]
        self.assertNotEqual(before_mode, after_mode)

        hook_directory = self.agents / ".git" / "hooks" / "nested"
        hook_directory.mkdir()
        after_directory = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )["agents_vault"]["git_control_sha256"]
        self.assertNotEqual(after_mode, after_directory)

        first_target = self.workdir / "external-hooks-one"
        second_target = self.workdir / "external-hooks-two"
        first_target.mkdir()
        second_target.mkdir()
        linked_directory = self.agents / ".git" / "hooks" / "linked"
        linked_directory.symlink_to(first_target, target_is_directory=True)
        first_link = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )["agents_vault"]["git_control_sha256"]
        linked_directory.unlink()
        linked_directory.symlink_to(second_target, target_is_directory=True)
        second_link = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )["agents_vault"]["git_control_sha256"]
        self.assertNotEqual(after_directory, first_link)
        self.assertNotEqual(first_link, second_link)
        self.assertEqual(PUSH_MODULE.git_control_digest(str(self.agents)), second_link)
        self.assertEqual(FINALIZER_MODULE.control_digest(str(self.agents)), second_link)

        hooks_path = self.agents / ".git" / "hooks"
        shutil.rmtree(hooks_path)
        top_level_target = self.workdir / "top-level-hooks"
        top_level_target.mkdir()
        top_level_hook = top_level_target / "pre-push"
        top_level_hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        top_level_hook.chmod(0o755)
        hooks_path.symlink_to(top_level_target, target_is_directory=True)
        top_level_before = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )["agents_vault"]["git_control_sha256"]
        self.assertEqual(
            PUSH_MODULE.git_control_digest(str(self.agents)), top_level_before
        )
        self.assertEqual(
            FINALIZER_MODULE.control_digest(str(self.agents)), top_level_before
        )
        top_level_hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        top_level_after = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )["agents_vault"]["git_control_sha256"]
        self.assertNotEqual(top_level_before, top_level_after)
        self.assertEqual(
            PUSH_MODULE.git_control_digest(str(self.agents)), top_level_after
        )
        self.assertEqual(
            FINALIZER_MODULE.control_digest(str(self.agents)), top_level_after
        )
        top_level_hook.chmod(0o700)
        top_level_mode_after = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )["agents_vault"]["git_control_sha256"]
        self.assertNotEqual(top_level_after, top_level_mode_after)
        self.assertEqual(
            PUSH_MODULE.git_control_digest(str(self.agents)), top_level_mode_after
        )
        self.assertEqual(
            FINALIZER_MODULE.control_digest(str(self.agents)), top_level_mode_after
        )

    def test_capture_rejects_staged_deletion_with_worktree_replacement(self) -> None:
        """Fail closed when a staged path exists only as an untracked replacement."""
        for repo in (self.agents, self.user):
            tracked = repo / "tracked.md"
            tracked.write_text("head\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.md"], check=True)
            subprocess.run(
                [
                    "git", "-C", str(repo),
                    "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid",
                    "commit", "-q", "-m", "initial",
                ],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
                check=True,
            )
        subprocess.run(
            ["git", "-C", str(self.agents), "rm", "--cached", "-q", "tracked.md"],
            check=True,
        )
        with self.assertRaisesRegex(ValueError, "index entry is unavailable"):
            CAPTURE_MODULE.capture(str(self.agents))

    def test_mode_determiner_constrains_each_vault_independently(self) -> None:
        """Convert collection-time drift and staged state into own_only, not failure."""
        base = {
            "repo_root": "/vault",
            "branch": "main",
            "upstream": "origin/main",
            "local_head": "a" * 40,
            "history_relation": "equal",
            "operation_in_progress": False,
            "git_control_sha256": "1" * 64,
            "index_sha256": "2" * 64,
            "dirty_worktree_sha256": "3" * 64,
            "dirty_digest": "4" * 64,
            "diff_snapshot_sha256": "5" * 64,
            "dirty_paths": [],
            "staged_paths": [],
        }
        changed = json.loads(json.dumps(base))
        changed["dirty_worktree_sha256"] = "6" * 64
        changed["dirty_paths"] = ["other-task.md"]
        result = MODE_MODULE.vault_mode(base, changed, "summary.md")
        self.assertEqual(result["required_mode"], "own_only")
        self.assertTrue(result["state_changed"])
        stable = json.loads(json.dumps(base))
        stable["staged_paths"] = ["staged.md"]
        staged = MODE_MODULE.vault_mode(stable, stable, "advisory.md")
        self.assertEqual(staged["required_mode"], "own_only")
        self.assertIn("existing_staged_changes", staged["reasons"])
        conflicted = MODE_MODULE.vault_mode(
            base, base, "summary.md", target_conflict=True
        )
        self.assertEqual(conflicted["required_mode"], "blocked")
        self.assertIn(
            "planned_target_changed_before_publication", conflicted["reasons"]
        )
        self.assertEqual(conflicted["retry_disposition"], "replan")
        active = json.loads(json.dumps(base))
        active["operation_in_progress"] = True
        blocked_active = MODE_MODULE.vault_mode(base, active, "summary.md")
        self.assertEqual(blocked_active["required_mode"], "blocked")
        self.assertEqual(blocked_active["retry_disposition"], "none")
        self.assertIn("active_git_operation", blocked_active["reasons"])
        control_changed = json.loads(json.dumps(base))
        control_changed["git_control_sha256"] = "9" * 64
        blocked_control = MODE_MODULE.vault_mode(
            base, control_changed, "summary.md"
        )
        self.assertEqual(blocked_control["required_mode"], "blocked")
        self.assertIn("git_control_plane_changed", blocked_control["reasons"])

    def test_sealed_residual_guard_raises_only_affected_mode_floor(self) -> None:
        """Turn a deterministic dirty deferral into an own_only mode hint."""
        hint = {
            "version": 1,
            "agents_vault": {
                "required_mode": "sweep",
                "reasons": ["stable_sweep_candidate"],
            },
            "user_vault": {
                "required_mode": "sweep",
                "reasons": ["stable_sweep_candidate"],
            },
        }
        manifest = {
            "version": 4,
            "vaults": {
                "agents_vault": [
                    {
                        "path": "unsafe.md",
                        "materialization_status": "deferred",
                    }
                ],
                "user_vault": [
                    {"path": "safe.md", "materialization_status": "available"}
                ],
            },
            "local_commits": {
                "agents_vault": [],
                "user_vault": [],
            },
        }
        guarded = MODE_MODULE.apply_residual_guards(hint, manifest)
        self.assertEqual(guarded["agents_vault"]["required_mode"], "own_only")
        self.assertEqual(
            guarded["agents_vault"]["guard_deferred_paths"], ["unsafe.md"]
        )
        self.assertIn(
            "sealed_residual_guard_deferred", guarded["agents_vault"]["reasons"]
        )
        self.assertNotIn(
            "stable_sweep_candidate", guarded["agents_vault"]["reasons"]
        )
        self.assertEqual(guarded["user_vault"]["required_mode"], "sweep")
        self.assertEqual(guarded["user_vault"]["guard_deferred_paths"], [])

    def test_residual_guard_rejects_malformed_snapshot_entries(self) -> None:
        """Fail closed instead of silently ignoring malformed review inputs."""
        hint = {
            "version": 1,
            "agents_vault": {"required_mode": "sweep", "reasons": []},
            "user_vault": {"required_mode": "sweep", "reasons": []},
        }
        for entry in (
            "not-an-object",
            {"path": "dirty.md"},
            {"path": "dirty.md", "materialization_status": "unexpected"},
            {"materialization_status": "available"},
        ):
            with self.subTest(dirty_entry=entry):
                manifest = {
                    "version": 4,
                    "vaults": {"agents_vault": [entry], "user_vault": []},
                    "local_commits": {"agents_vault": [], "user_vault": []},
                }
                with self.assertRaises(MODE_MODULE.ModeError):
                    MODE_MODULE.apply_residual_guards(hint, manifest)
        for entry in (
            "not-an-object",
            {"commit": "a" * 40},
            {"commit": "a" * 40, "materialization_status": "unexpected"},
            {"materialization_status": "available"},
        ):
            with self.subTest(local_commit_entry=entry):
                manifest = {
                    "version": 4,
                    "vaults": {"agents_vault": [], "user_vault": []},
                    "local_commits": {
                        "agents_vault": [entry],
                        "user_vault": [],
                    },
                }
                with self.assertRaises(MODE_MODULE.ModeError):
                    MODE_MODULE.apply_residual_guards(hint, manifest)

    def test_unavailable_local_history_blocks_only_its_vault(self) -> None:
        """Do not publish an unreviewable local-ahead commit as an ancestor."""
        hint = {
            "version": 1,
            "agents_vault": {
                "required_mode": "sweep",
                "retry_disposition": "none",
                "reasons": ["stable_sweep_candidate"],
            },
            "user_vault": {
                "required_mode": "sweep",
                "retry_disposition": "none",
                "reasons": ["stable_sweep_candidate"],
            },
        }
        manifest = {
            "version": 4,
            "vaults": {"agents_vault": [], "user_vault": []},
            "local_commits": {
                "agents_vault": [
                    {
                        "commit": "a" * 40,
                        "materialization_status": "blocked",
                    }
                ],
                "user_vault": [],
            },
        }
        guarded = MODE_MODULE.apply_residual_guards(hint, manifest)
        self.assertEqual(guarded["agents_vault"]["required_mode"], "blocked")
        self.assertEqual(guarded["agents_vault"]["retry_disposition"], "none")
        self.assertEqual(guarded["agents_vault"]["guard_blocked_commits"], ["a" * 40])
        self.assertIn(
            "sealed_local_history_guard_blocked",
            guarded["agents_vault"]["reasons"],
        )
        self.assertEqual(guarded["user_vault"]["required_mode"], "sweep")

    def test_missing_residual_guard_inputs_fail_closed(self) -> None:
        """Never treat an unscanned dirty entry as reviewable."""
        content = b"safe-looking bytes\n"
        self.assertEqual(
            DIRTY_STAGER_MODULE.residual_guard_reason(
                {}, {}, "agents_git_dir", "dirty.md", content
            ),
            "dirty_entry_residual_guard_unavailable",
        )

    def test_own_only_commit_preserves_unrelated_staged_and_untracked_state(self) -> None:
        """Commit only the artifact while preserving unsafe residual bytes and metadata."""
        repo = self.user
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        (repo / "staged.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "staged.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)
        (repo / "staged.md").write_text("other task staged\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "staged.md"], check=True)
        handoff = repo / ".codex-handoff" / "unsafe.md"
        handoff.parent.mkdir()
        handoff.write_text(f"unsafe existing path {Path.home()}\n", encoding="utf-8")
        handoff.chmod(0o640)
        before_stat = handoff.stat()
        before_bytes = handoff.read_bytes()
        staged_oid = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", ":staged.md"], text=True
        ).strip()
        pre = CAPTURE_MODULE.capture(str(repo), include_local_history=True)
        artifact = repo / "summary.md"
        artifact.write_text("verified artifact\n", encoding="utf-8")
        manifest = {
            "approved_dirty_entries": [],
            "commit_groups": [{"message": "docs: publish summary", "paths": ["summary.md"]}],
            "deferred_cleanup": [
                {"path": path, "reason": "existing unrelated change"}
                for path in pre["dirty_paths"]
            ],
        }
        result = COMMITTER_MODULE.commit_groups(
            str(repo), str(repo / ".git"), str(self.fake_gitleaks), pre,
            manifest, "summary.md", hashlib.sha256(artifact.read_bytes()).hexdigest(),
            self.workdir, ("Fixture Publisher", "publisher@example.invalid"),
            publication_mode="own_only",
        )
        after = CAPTURE_MODULE.capture(str(repo), include_local_history=True)
        COMMITTER_MODULE.validate_post_commit_state(pre, after, "summary.md", "own_only")
        self.assertEqual(result["publication_mode"], "own_only")
        self.assertFalse(result["clean"])
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", ":staged.md"], text=True
            ).strip(),
            staged_oid,
        )
        self.assertEqual(handoff.read_bytes(), before_bytes)
        after_stat = handoff.stat()
        self.assertEqual(after_stat.st_mode, before_stat.st_mode)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        committed_paths = subprocess.check_output(
            ["git", "-C", str(repo), "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            text=True,
        ).splitlines()
        self.assertEqual(committed_paths, ["summary.md"])

    def test_capture_invalid_utf8_context_fails_closed(self) -> None:
        """Return status 75 instead of leaking a decode traceback."""
        context = self.workdir / "invalid-context.json"
        context.write_bytes(b"\xff")
        result = subprocess.run(
            [str(SCRIPTS / "capture-vault-state.py"), str(context)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("Vault state capture failed", result.stderr)

    def test_collection_validator_checks_hash_and_staging(self) -> None:
        """Accept verified staged files and reject a hash mismatch."""
        staging = self.workdir / "staging"
        staging.mkdir()
        source_manifest = write_source_manifest(staging)
        verified_resolutions = write_verified_resolutions(staging)
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        summary.write_text(source_coverage_markdown(), encoding="utf-8")
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        advisory.write_text(
            f"- 入力ニュース: {summary.name} "
            f"(same-run SHA-256: {digest(summary)})\n",
            encoding="utf-8",
        )
        result_path = self.workdir / "collection.json"
        payload = {
            "daily_pipeline_status": "complete",
            "run_id": "20260731T040000+0900",
            "summary_path": str(summary),
            "summary_sha256": digest(summary),
            "advisory_path": str(advisory),
            "advisory_sha256": digest(advisory),
            "notification_result": "none",
            "vault_artifacts_complete": True,
            "next_action": None,
        }
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        command = [
            str(SCRIPTS / "validate-collection-result.py"),
            str(result_path),
            str(staging),
            "20260731T040000+0900",
            "0",
            str(SOURCE_CATALOG),
            str(source_manifest),
            str(verified_resolutions),
        ]
        self.assertEqual(subprocess.run(command, check=False).returncode, 0)
        payload["summary_sha256"] = "0" * 64
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(subprocess.run(command, check=False).returncode, 75)

    def test_collection_constraint_canonicalizer_preserves_raw_evidence(self) -> None:
        """Correct only authorized coverage cells and retain raw agent artifacts."""
        staging = self.workdir / "staging"
        staging.mkdir()
        source_manifest = write_source_manifest(staging)
        verified_resolutions = write_verified_resolutions(staging)
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        raw_summary = (
            source_coverage_markdown()
            .replace("robotsで取得禁止", "paywall")
            .replace(
                "| InfoQ | 1 | 対象期間記事なし |",
                "| InfoQ | 1 | 取得済み |",
                1,
            )
            .replace(
                "| 窓の杜 Internet | 2 |",
                "|   窓の杜 Internet   |  2  |",
            )
        )
        summary.write_text(raw_summary, encoding="utf-8")
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        advisory.write_text(
            f"- 入力ニュース: {summary.name} "
            f"(same-run SHA-256: {digest(summary)})\n",
            encoding="utf-8",
        )
        raw_result_path = self.workdir / "collection-agent-result.json"
        canonical_result_path = self.workdir / "collection-result.json"
        receipt_path = self.workdir / "collection-normalization.json"
        payload = {
            "daily_pipeline_status": "complete",
            "run_id": "20260731T040000+0900",
            "summary_path": str(summary),
            "summary_sha256": digest(summary),
            "advisory_path": str(advisory),
            "advisory_sha256": digest(advisory),
            "notification_result": "none",
            "vault_artifacts_complete": True,
            "next_action": None,
        }
        raw_result_path.write_text(json.dumps(payload), encoding="utf-8")
        raw_files = {
            path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
            for path in (summary, advisory, raw_result_path)
        }
        command = [
            str(SCRIPTS / "validate-collection-result.py"),
            "--canonicalize-constraints",
            str(raw_result_path),
            str(staging),
            payload["run_id"],
            "0",
            str(SOURCE_CATALOG),
            str(source_manifest),
            str(verified_resolutions),
            str(canonical_result_path),
            str(receipt_path),
        ]
        previous_umask = os.umask(0o777)
        try:
            normalization = subprocess.run(
                command, check=False, capture_output=True, text=True
            )
        finally:
            os.umask(previous_umask)
        self.assertEqual(normalization.returncode, 0, normalization.stderr)
        for path, (content, mode, mtime_ns) in raw_files.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mode, mode)
            self.assertEqual(path.stat().st_mtime_ns, mtime_ns)

        canonical = json.loads(canonical_result_path.read_text(encoding="utf-8"))
        canonical_summary = Path(canonical["summary_path"])
        canonical_advisory = Path(canonical["advisory_path"])
        self.assertEqual(canonical_summary.parent.name, "canonical-artifacts")
        self.assertEqual(
            stat.S_IMODE(canonical_summary.parent.stat().st_mode), 0o700
        )
        for private_file in (
            canonical_summary,
            canonical_advisory,
            canonical_result_path,
            receipt_path,
        ):
            self.assertEqual(stat.S_IMODE(private_file.stat().st_mode), 0o600)
        expected_canonical_summary = raw_summary.replace(
            "paywall", "robots"
        ).replace(
            "| InfoQ | 1 | 取得済み |",
            "| InfoQ | 1 | 対象期間記事なし |",
            1,
        )
        self.assertEqual(
            canonical_summary.read_text(encoding="utf-8"),
            expected_canonical_summary,
        )
        self.assertIn("paywall", summary.read_text(encoding="utf-8"))
        self.assertNotIn("paywall", canonical_summary.read_text(encoding="utf-8"))
        self.assertIn(
            "| InfoQ | 1 | 取得済み |",
            summary.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "| InfoQ | 1 | 対象期間記事なし |",
            canonical_summary.read_text(encoding="utf-8"),
        )
        self.assertEqual(canonical["summary_sha256"], digest(canonical_summary))
        self.assertEqual(canonical["advisory_sha256"], digest(canonical_advisory))
        self.assertIn(
            f"same-run SHA-256: {canonical['summary_sha256']}",
            canonical_advisory.read_text(encoding="utf-8"),
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["projection"], "sealed_source_coverage_cells_v1")
        self.assertEqual(receipt["corrected_reason_count"], 1)
        self.assertEqual(receipt["corrected_status_count"], 1)
        self.assertEqual(
            receipt["status_corrections"],
            [
                {
                    "source": "InfoQ",
                    "supplied": "取得済み",
                    "sealed": "対象期間記事なし",
                    "item_count": 0,
                }
            ],
        )
        self.assertEqual(
            receipt["raw_collection_result_sha256"],
            hashlib.sha256(raw_result_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            receipt["canonical_collection_result_sha256"],
            hashlib.sha256(canonical_result_path.read_bytes()).hexdigest(),
        )
        expected_evidence = {
            "catalog": SOURCE_CATALOG,
            "manifest": source_manifest,
            "verified_resolutions": verified_resolutions,
        }
        self.assertEqual(set(receipt["source_evidence"]), set(expected_evidence))
        for label, path in expected_evidence.items():
            self.assertEqual(receipt["source_evidence"][label]["path"], str(path))
            self.assertEqual(
                receipt["source_evidence"][label]["sha256"],
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        validation = subprocess.run(
            [
                str(SCRIPTS / "validate-collection-result.py"),
                str(canonical_result_path),
                str(staging),
                payload["run_id"],
                "0",
                str(SOURCE_CATALOG),
                str(source_manifest),
                str(verified_resolutions),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_collection_constraint_canonicalizer_rejects_existing_or_symlink_targets(
        self,
    ) -> None:
        """Never replace pre-existing canonical directories or audit outputs."""
        for collision in (
            "canonical_symlink",
            "result_file",
            "receipt_symlink",
        ):
            with self.subTest(collision=collision):
                root = self.workdir / collision
                root.mkdir()
                staging = root / "staging"
                staging.mkdir()
                source_manifest = write_source_manifest(staging)
                verified_resolutions = write_verified_resolutions(staging)
                summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
                advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
                summary.write_text(
                    source_coverage_markdown().replace(
                        "robotsで取得禁止", "paywall"
                    ),
                    encoding="utf-8",
                )
                summary_sha256 = hashlib.sha256(summary.read_bytes()).hexdigest()
                advisory.write_text(
                    f"- 入力ニュース: {summary.name} "
                    f"(same-run SHA-256: {summary_sha256})\n",
                    encoding="utf-8",
                )
                raw_result_path = root / "collection-agent-result.json"
                canonical_result_path = root / "collection-result.json"
                receipt_path = root / "collection-normalization.json"
                raw_result_path.write_text(
                    json.dumps(
                        {
                            "daily_pipeline_status": "complete",
                            "run_id": "20260731T040000+0900",
                            "summary_path": str(summary),
                            "summary_sha256": summary_sha256,
                            "advisory_path": str(advisory),
                            "advisory_sha256": hashlib.sha256(
                                advisory.read_bytes()
                            ).hexdigest(),
                            "notification_result": "none",
                            "vault_artifacts_complete": True,
                            "next_action": None,
                        }
                    ),
                    encoding="utf-8",
                )
                raw_before = {
                    path: (
                        path.read_bytes(),
                        stat.S_IMODE(path.stat().st_mode),
                        path.stat().st_mtime_ns,
                    )
                    for path in (summary, advisory, raw_result_path)
                }
                sentinel = root / "sentinel"
                sentinel.write_bytes(b"third-party\n")
                if collision == "canonical_symlink":
                    outside = root / "outside"
                    outside.mkdir()
                    (staging / "canonical-artifacts").symlink_to(
                        outside, target_is_directory=True
                    )
                elif collision == "result_file":
                    canonical_result_path.write_bytes(b"third-party\n")
                else:
                    receipt_path.symlink_to(sentinel)
                command = [
                    str(SCRIPTS / "validate-collection-result.py"),
                    "--canonicalize-constraints",
                    str(raw_result_path),
                    str(staging),
                    "20260731T040000+0900",
                    "0",
                    str(SOURCE_CATALOG),
                    str(source_manifest),
                    str(verified_resolutions),
                    str(canonical_result_path),
                    str(receipt_path),
                ]
                normalization = subprocess.run(
                    command, check=False, capture_output=True, text=True
                )
                self.assertEqual(normalization.returncode, 75)
                self.assertEqual(sentinel.read_bytes(), b"third-party\n")
                if collision == "result_file":
                    self.assertEqual(
                        canonical_result_path.read_bytes(), b"third-party\n"
                    )
                for path, expected in raw_before.items():
                    self.assertEqual(
                        (
                            path.read_bytes(),
                            stat.S_IMODE(path.stat().st_mode),
                            path.stat().st_mtime_ns,
                        ),
                        expected,
                    )

    def test_collection_artifact_reads_are_bounded_and_stable(self) -> None:
        """Reject descriptor growth and metadata drift during raw artifact reads."""
        descriptor = os.open(__file__, os.O_RDONLY)
        try:
            with mock.patch.object(
                COLLECTION_VALIDATOR_MODULE.os,
                "read",
                side_effect=[
                    b"x" * (COLLECTION_VALIDATOR_MODULE.MAX_ARTIFACT_BYTES + 1)
                ],
            ), self.assertRaisesRegex(
                COLLECTION_VALIDATOR_MODULE.ValidationError, "grew"
            ):
                COLLECTION_VALIDATOR_MODULE.digest_fd(descriptor)
        finally:
            os.close(descriptor)

        staging = self.workdir / "artifact-drift"
        staging.mkdir()
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        summary.write_bytes(b"stable\n")
        os.chmod(summary, 0o640)
        expected_hash = hashlib.sha256(summary.read_bytes()).hexdigest()
        real_digest = COLLECTION_VALIDATOR_MODULE.digest_fd

        def drift_mode(open_descriptor: int) -> tuple[str, bytes]:
            result = real_digest(open_descriptor)
            os.fchmod(open_descriptor, 0o600)
            return result

        with mock.patch.object(
            COLLECTION_VALIDATOR_MODULE,
            "digest_fd",
            side_effect=drift_mode,
        ), self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError, "changed while"
        ):
            COLLECTION_VALIDATOR_MODULE.validate_artifact(
                str(summary),
                expected_hash,
                staging,
                0,
                "2026-07-31",
                "summary",
            )

        evidence = staging / "source-evidence.json"
        evidence.write_bytes(b'{"version":1}\n')
        os.chmod(evidence, 0o640)
        real_read = os.read
        first_read = True

        def drift_evidence_mode(open_descriptor: int, count: int) -> bytes:
            nonlocal first_read
            chunk = real_read(open_descriptor, count)
            if first_read:
                first_read = False
                os.fchmod(open_descriptor, 0o600)
            return chunk

        with mock.patch.object(
            COLLECTION_VALIDATOR_MODULE.os,
            "read",
            side_effect=drift_evidence_mode,
        ), self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError, "changed while"
        ):
            COLLECTION_VALIDATOR_MODULE.read_regular_nofollow(
                evidence, "source evidence"
            )

    def test_collection_constraint_canonicalizer_rejects_ambiguous_rows(self) -> None:
        """Refuse to project missing, duplicate, or non-constraint source rows."""
        valid = source_coverage_markdown()
        constrained_row = valid.splitlines()[-1]
        cases = {
            "missing": "\n".join(valid.splitlines()[:-1]) + "\n",
            "duplicate": valid + constrained_row + "\n",
            "wrong status": valid.replace(
                "アクセス制約", "対象期間記事なし", 1
            ),
        }
        for label, summary in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(COLLECTION_VALIDATOR_MODULE.ValidationError):
                    COLLECTION_VALIDATOR_MODULE.canonicalize_summary_constraint_reasons(
                        summary,
                        {
                            json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))[
                                "sources"
                            ][-1]["name"]: "robots"
                        },
                    )

    def test_collection_coverage_projection_normalizes_count_implied_status(self) -> None:
        """Normalize only normal status cells implied by the unchanged row count."""
        root = self.workdir / "coverage-status-projection"
        root.mkdir()
        manifest = write_source_manifest(root)
        resolutions = write_verified_resolutions(root)
        constraints, retrieved, _ = (
            COLLECTION_VALIDATOR_MODULE.sealed_coverage_authority(
                SOURCE_CATALOG, manifest, resolutions
            )
        )
        raw = (
            source_coverage_markdown()
            .replace(
                "| TechCrunch | 1 | 取得済み |",
                "| TechCrunch | 1 | 対象期間記事なし |",
                1,
            )
            .replace(
                "| InfoQ | 1 | 対象期間記事なし |",
                "| InfoQ | 1 | 取得済み |",
                1,
            )
        )
        projected, reason_corrections, status_corrections = (
            COLLECTION_VALIDATOR_MODULE.canonicalize_summary_coverage(
                raw, constraints, retrieved
            )
        )
        self.assertEqual(len(reason_corrections), 1)
        self.assertEqual(
            status_corrections,
            [
                {
                    "source": "TechCrunch",
                    "supplied": "対象期間記事なし",
                    "sealed": "取得済み",
                    "item_count": 1,
                },
                {
                    "source": "InfoQ",
                    "supplied": "取得済み",
                    "sealed": "対象期間記事なし",
                    "item_count": 0,
                },
            ],
        )
        self.assertIn("| TechCrunch | 1 | 取得済み |", projected)
        self.assertIn("| InfoQ | 1 | 対象期間記事なし |", projected)
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            projected, SOURCE_CATALOG, manifest, resolutions, date(2026, 7, 31)
        )

        fallback_root = self.workdir / "coverage-status-fallback"
        fallback_root.mkdir()
        fallback_summary, fallback_catalog, fallback_manifest, fallback_verified = (
            write_minimal_coverage_fixture(
                fallback_root,
                extract_entries=[],
                evidence_updates={
                    "status": "needs_search_fallback",
                    "method": None,
                    "final_url": None,
                    "extract_file": None,
                    "extracted_entry_count": 0,
                    "attempts": [
                        {"method": "rss", "status": "failed", "reason": "fixture"}
                    ],
                },
                resolutions=[
                    {
                        "name": "Fixture News",
                        "status": "verified_fallback",
                        "method": "official_alternate",
                    }
                ],
                status="取得済み",
                count=0,
            )
        )
        fallback_constraints, fallback_retrieved, _ = (
            COLLECTION_VALIDATOR_MODULE.sealed_coverage_authority(
                fallback_catalog, fallback_manifest, fallback_verified
            )
        )
        self.assertEqual(fallback_constraints, {})
        self.assertEqual(fallback_retrieved, {"Fixture News"})
        fallback_projected, _, fallback_status_corrections = (
            COLLECTION_VALIDATOR_MODULE.canonicalize_summary_coverage(
                fallback_summary, fallback_constraints, fallback_retrieved
            )
        )
        self.assertIn("| Fixture News | 1 | 対象期間記事なし |", fallback_projected)
        self.assertEqual(fallback_status_corrections[0]["item_count"], 0)

    def test_collection_coverage_projection_does_not_mask_wrong_count(self) -> None:
        """Keep sealed date/count validation authoritative after status projection."""
        root = self.workdir / "coverage-wrong-count"
        root.mkdir()
        manifest = write_source_manifest(root)
        resolutions = write_verified_resolutions(root)
        constraints, retrieved, _ = (
            COLLECTION_VALIDATOR_MODULE.sealed_coverage_authority(
                SOURCE_CATALOG, manifest, resolutions
            )
        )
        raw = source_coverage_markdown().replace(
            "| TechCrunch | 1 | 取得済み | RSS | https://techcrunch.com/feed/ | 1 |",
            "| TechCrunch | 1 | 取得済み | RSS | https://techcrunch.com/feed/ | 0 |",
            1,
        )
        projected, _, status_corrections = (
            COLLECTION_VALIDATOR_MODULE.canonicalize_summary_coverage(
                raw, constraints, retrieved
            )
        )
        self.assertEqual(status_corrections[0]["source"], "TechCrunch")
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "does not match dated extract evidence",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                projected,
                SOURCE_CATALOG,
                manifest,
                resolutions,
                date(2026, 7, 31),
            )

        unauthorized = source_coverage_markdown().replace(
            "| InfoQ | 1 | 対象期間記事なし |",
            "| InfoQ | 1 | アクセス制約 |",
            1,
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "unauthorized status",
        ):
            COLLECTION_VALIDATOR_MODULE.canonicalize_summary_coverage(
                unauthorized, constraints, retrieved
            )

    def test_collection_constraint_projection_does_not_authorize_foreign_rows(
        self,
    ) -> None:
        """Leave foreign rows outside projection and reject them semantically."""
        root = self.workdir / "foreign-coverage-row"
        root.mkdir()
        manifest = write_source_manifest(root)
        resolutions = write_verified_resolutions(root)
        catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
        constrained_name = catalog["sources"][-1]["name"]
        summary = (
            source_coverage_markdown().replace("robotsで取得禁止", "paywall")
            + "| Foreign Source | 2 | アクセス制約 | 公開ページ | "
            "https://foreign.example.invalid/ | 0 | robots |\n"
        )
        projected, corrections = (
            COLLECTION_VALIDATOR_MODULE.canonicalize_summary_constraint_reasons(
                summary, {constrained_name: "robots"}
            )
        )
        self.assertEqual(len(corrections), 1)
        self.assertIn("Foreign Source", projected)
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "does not match the catalog",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                projected,
                SOURCE_CATALOG,
                manifest,
                resolutions,
                date(2026, 7, 31),
            )

    def test_collection_validator_preserves_query_article_identity(self) -> None:
        """Count distinct query-addressed articles instead of collapsing their evidence."""
        root = self.workdir / "query-coverage"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/news?id=1", "published": "2026-07-31"},
                {"url": "https://example.test/news?id=2", "published": "2026-07-31"},
            ],
            count=2,
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 7, 31)
        )

    def test_collection_validator_rejects_unsafe_sealed_entry_urls(self) -> None:
        """Fail closed on malformed, credentialed, or foreign extract URLs."""
        invalid_urls = (
            "https://example.test:bad/story",
            "https://example.test:65536/story",
            "https://user:pass@example.test/story",
            "https://bad_host.test/story",
            "https://other.test/story",
        )
        for index, invalid_url in enumerate(invalid_urls):
            with self.subTest(url=invalid_url):
                root = self.workdir / f"unsafe-sealed-url-{index}"
                root.mkdir()
                summary, catalog, manifest, verified = write_minimal_coverage_fixture(
                    root,
                    extract_entries=[
                        {"url": invalid_url, "published": "2026-07-31"}
                    ],
                )
                with self.assertRaisesRegex(
                    COLLECTION_VALIDATOR_MODULE.ValidationError,
                    "source extract entry URL is invalid",
                ):
                    COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                        summary, catalog, manifest, verified, date(2026, 7, 31)
                    )

    def test_collection_validator_uses_inclusive_seven_jst_calendar_dates(self) -> None:
        """Include run_date-6 through run_date after timezone normalization."""
        root = self.workdir / "jst-calendar-window"
        root.mkdir()
        entries = [
            {
                "url": "https://example.test/too-old",
                "published": "2026-08-03T23:59:59+09:00",
            },
            {
                "url": "https://example.test/first-second",
                "published": "Mon, 03 Aug 2026 15:00:00 GMT",
            },
            {
                "url": "https://example.test/last-second",
                "published": "2026-08-10T23:59:59+09:00",
            },
            {
                "url": "https://example.test/too-new",
                "published": "2026-08-10T15:00:00Z",
            },
        ]
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=entries,
            count=2,
            run_date=date(2026, 8, 10),
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 8, 10)
        )

        wrong_summary = summary.replace("| 2 | fixture |", "| 3 | fixture |")
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "item count does not match dated extract evidence",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                wrong_summary, catalog, manifest, verified, date(2026, 8, 10)
            )

    def test_collection_validator_rederives_manifest_window_count(self) -> None:
        """Accept the trusted count hint only when sealed dates reproduce it."""
        root = self.workdir / "manifest-window-count"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/in", "published": "2026-08-10"},
                {"url": "https://example.test/out", "published": "2026-08-03"},
            ],
            evidence_updates={
                "jst_window_start": "2026-08-04",
                "jst_window_end": "2026-08-10",
                "jst_window_item_count": 1,
            },
            count=1,
            run_date=date(2026, 8, 10),
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 8, 10)
        )
        payload = json.loads(manifest.read_text())
        payload["sources"][0]["jst_window_item_count"] = 0
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "manifest JST window count is invalid",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 8, 10)
            )
        for invalid in (None, True):
            payload = json.loads(manifest.read_text())
            if invalid is None:
                payload["sources"][0].pop("jst_window_item_count", None)
            else:
                payload["sources"][0]["jst_window_item_count"] = invalid
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                COLLECTION_VALIDATOR_MODULE.ValidationError,
                "manifest JST window count is invalid",
            ):
                COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                    summary, catalog, manifest, verified, date(2026, 8, 10)
                )
        payload = json.loads(manifest.read_text())
        payload["sources"][0]["jst_window_item_count"] = 1
        payload["sources"][0].pop("jst_window_start", None)
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "manifest JST window count is invalid",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 8, 10)
            )

    def test_collection_validator_binds_supplemental_date_to_extract_url(self) -> None:
        """Reject same-host dates for articles absent from the sealed extract."""
        root = self.workdir / "unbound-date"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/news?id=1", "published": None}
            ],
            date_evidence=[{
                "name": "Fixture News",
                "requested_url": "https://example.test/news?id=2",
                "final_url": "https://example.test/news?id=2",
                "published_date": "2026-07-31",
            }],
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "not bound to an undated extract entry",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_rejects_supplemental_date_redirect(self) -> None:
        """Require verified date evidence to finish on the sealed article URL."""
        root = self.workdir / "redirected-date"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/news?id=1", "published": None}
            ],
            date_evidence=[{
                "name": "Fixture News",
                "requested_url": "https://example.test/news?id=1",
                "final_url": "https://example.test/news?id=2",
                "published_date": "2026-07-31",
            }],
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError, "redirected"
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_ignores_undated_navigation_entries(self) -> None:
        """Count sealed dated articles without treating undated navigation as articles."""
        root = self.workdir / "mixed-html-dates"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/article", "published": "2026-07-31"},
                {"url": "https://example.test/about", "published": None},
            ],
            evidence_updates={
                "method": "public_page",
                "final_url": "https://example.test/news",
            },
            method="公開ページ",
            confirmed_url="https://example.test/news",
            count=1,
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 7, 31)
        )

    def test_collection_validator_requires_dates_for_every_feed_entry(self) -> None:
        """Do not treat undated RSS entries as HTML navigation links."""
        root = self.workdir / "undated-feed-entry"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/article", "published": "2026-07-31"},
                {"url": "https://example.test/undated", "published": None},
            ],
            count=1,
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "feed source lacks publication-date evidence",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_sealed_extract_requires_a_filename(self) -> None:
        """Raise the validator's typed error instead of an unpacking ValueError."""
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "source extract filename is invalid",
        ):
            COLLECTION_VALIDATOR_MODULE.sealed_extract_entries(
                self.workdir / "source-manifest.json", {}, set()
            )

    def test_collection_validator_requires_gate_attempt_transport_evidence(self) -> None:
        """Reject non-robots constraints without final URL and HTTP status evidence."""
        root = self.workdir / "incomplete-gate-attempt"
        root.mkdir()
        feed_url = "https://example.test/feed"
        page_url = "https://example.test/news"
        attempts = [
            {
                "method": attempt_method,
                "url": attempt_url,
                "requested_url": attempt_url,
                "status": "access_constraint",
                "constraint": "login",
            }
            for attempt_method, attempt_url in (
                ("rss", feed_url),
                ("public_page", page_url),
            )
        ]
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            evidence_updates={
                "status": "access_constraint",
                "method": "public_page",
                "final_url": page_url,
                "constraint": "login",
                "attempts": attempts,
            },
            status="アクセス制約",
            method="公開ページ",
            count=0,
            confirmed_url=page_url,
            reason="login required",
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "sealed access-constraint evidence",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_accepts_reviewed_www_gate_redirect(self) -> None:
        """Use the same reviewed www/non-www aliases as the collector."""
        root = self.workdir / "www-gate-redirect"
        root.mkdir()
        feed_url = "https://example.test/feed"
        page_url = "https://example.test/news"
        attempts = [
            {
                "method": attempt_method,
                "url": attempt_url,
                "requested_url": attempt_url,
                "final_url": attempt_url.replace("example.test", "www.example.test"),
                "http_status": 403,
                "status": "access_constraint",
                "constraint": "login",
            }
            for attempt_method, attempt_url in (
                ("rss", feed_url),
                ("public_page", page_url),
            )
        ]
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            evidence_updates={
                "status": "access_constraint",
                "method": "public_page",
                "final_url": page_url.replace("example.test", "www.example.test"),
                "constraint": "login",
                "attempts": attempts,
            },
            status="アクセス制約",
            method="公開ページ",
            count=0,
            confirmed_url=page_url.replace("example.test", "www.example.test"),
            reason="login required",
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 7, 31)
        )

    def test_collection_validator_rejects_empty_fetched_extract(self) -> None:
        """Do not infer no recent articles from an empty parser result."""
        root = self.workdir / "empty-extract"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            status="対象期間記事なし",
            count=0,
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "empty or inconsistent extract",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_requires_complete_fallback_dates(self) -> None:
        """Reject fallback counts when any extracted entry has no parseable date."""
        root = self.workdir / "fallback-dates"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            evidence_updates={
                "status": "needs_search_fallback",
                "method": None,
                "final_url": None,
                "extract_file": None,
                "extracted_entry_count": 0,
                "attempts": [{"method": "rss", "status": "failed", "reason": "fixture"}],
            },
            resolutions=[{
                "name": "Fixture News",
                "status": "verified_fallback",
                "method": "site_search",
                "requested_url": "https://example.test/feed",
                "final_url": "https://example.test/feed",
                "extracted_entry_count": 1,
                "candidate_entry_count": 1,
                "date_evidence_count": 1,
                "published_dates": [None],
                "candidate_evidence": [{
                    "url": "https://example.test/article",
                    "provenance": "feed_entry",
                    "published": None,
                }],
            }],
            status="対象期間記事なし",
            method="サイト限定検索",
            count=0,
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "complete publication-date evidence",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_accepts_http_fallback_candidate(self) -> None:
        """Use the collector's HTTP-or-HTTPS candidate policy for fallback evidence."""
        root = self.workdir / "fallback-http-candidate"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            evidence_updates={
                "status": "needs_search_fallback",
                "method": None,
                "final_url": None,
                "extract_file": None,
                "extracted_entry_count": 0,
                "attempts": [{"method": "rss", "status": "failed", "reason": "fixture"}],
            },
            resolutions=[{
                "name": "Fixture News",
                "status": "verified_fallback",
                "method": "site_search",
                "requested_url": "https://example.test/search",
                "final_url": "https://example.test/search",
                "extracted_entry_count": 1,
                "candidate_entry_count": 1,
                "date_evidence_count": 1,
                "published_dates": ["2026-07-31"],
                "candidate_evidence": [{
                    "url": "http://example.test/article",
                    "provenance": "article",
                    "published": "2026-07-31",
                }],
            }],
            status="取得済み",
            method="サイト限定検索",
            count=1,
            confirmed_url="https://example.test/search",
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 7, 31)
        )

    def test_collection_validator_rejects_mixed_robots_attempts_without_fallback(self) -> None:
        """One robots denial must not hide a generic failure on another endpoint."""
        root = self.workdir / "mixed-robots"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            evidence_updates={
                "status": "needs_search_fallback",
                "method": None,
                "final_url": None,
                "extract_file": None,
                "extracted_entry_count": 0,
                "attempts": [
                    {"method": "rss", "status": "access_constraint", "reason": "robots_disallowed"},
                    {"method": "public_page", "status": "failed", "reason": "transient"},
                ],
            },
            status="アクセス制約",
            count=0,
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "verified fallback resolution",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_rejects_unsafe_advisory_references(self) -> None:
        """Reject private paths, inconsistent hashes/names, and duplicate fields."""
        staging = self.workdir / "staging"
        staging.mkdir()
        source_manifest = write_source_manifest(staging)
        verified_resolutions = write_verified_resolutions(staging)
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        summary.write_text(source_coverage_markdown(), encoding="utf-8")
        summary_hash = hashlib.sha256(summary.read_bytes()).hexdigest()
        result_path = self.workdir / "collection.json"
        payload = {
            "daily_pipeline_status": "complete",
            "run_id": "20260731T040000+0900",
            "summary_path": str(summary),
            "summary_sha256": summary_hash,
            "advisory_path": str(advisory),
            "advisory_sha256": "",
            "vault_artifacts_complete": True,
        }
        command = [
            str(SCRIPTS / "validate-collection-result.py"),
            str(result_path),
            str(staging),
            payload["run_id"],
            "0",
            str(SOURCE_CATALOG),
            str(source_manifest),
            str(verified_resolutions),
        ]

        unsafe_references = {
            "private home": f"- 入力ニュース: {Path.home()}/news.md\n",
            "staging path": f"- 入力ニュース: {summary}\n",
            "wrong basename": (
                "- 入力ニュース: SUMMARY-IT-NEWS-2026-07-30.md "
                f"(same-run SHA-256: {summary_hash})\n"
            ),
            "wrong digest": (
                f"- 入力ニュース: {summary.name} "
                f"(same-run SHA-256: {'0' * 64})\n"
            ),
            "duplicate field": (
                f"- 入力ニュース: {summary.name} "
                f"(same-run SHA-256: {summary_hash})\n"
                f"- 入力ニュース: {summary.name} "
                f"(same-run SHA-256: {summary_hash})\n"
            ),
        }
        for label, content in unsafe_references.items():
            with self.subTest(label=label):
                advisory.write_text(content, encoding="utf-8")
                payload["advisory_sha256"] = hashlib.sha256(
                    advisory.read_bytes()
                ).hexdigest()
                result_path.write_text(json.dumps(payload), encoding="utf-8")
                validation = subprocess.run(
                    command, check=False, capture_output=True, text=True
                )
                self.assertEqual(validation.returncode, 75)
                self.assertIn("collection validation failed", validation.stderr)

    def test_collection_validator_rejects_unresolved_or_missing_sources(self) -> None:
        """Block complete publication when any catalog source lacks a resolved row."""
        staging = self.workdir / "staging"
        staging.mkdir()
        source_manifest = write_source_manifest(staging)
        verified_resolutions = write_verified_resolutions(staging)
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        result_path = self.workdir / "collection.json"
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        valid = source_coverage_markdown()
        fabricated_rows = []
        for line in valid.splitlines():
            if line.startswith("|") and "https://" in line:
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                cells[2:7] = [
                    "対象期間記事なし", "公式代替URL",
                    "https://example.invalid/fake", "42", "根拠なし",
                ]
                line = "| " + " | ".join(cells) + " |"
            fabricated_rows.append(line)
        invalid_summaries = {
            "unresolved status": valid.replace("対象期間記事なし", "取得不可", 1),
            "missing source": "\n".join(valid.splitlines()[:-1]) + "\n",
            "generic access error": valid.replace(
                "対象期間記事なし | RSS",
                "アクセス制約 | RSS",
                1,
            ).replace("fixture確認", "HTTP 403", 1),
            "fabricated complete table": "\n".join(fabricated_rows) + "\n",
        }
        for label, content in invalid_summaries.items():
            with self.subTest(label=label):
                summary.write_text(content, encoding="utf-8")
                advisory.write_text(
                    f"- 入力ニュース: {summary.name} "
                    f"(same-run SHA-256: {digest(summary)})\n",
                    encoding="utf-8",
                )
                payload = {
                    "daily_pipeline_status": "complete",
                    "run_id": "20260731T040000+0900",
                    "summary_path": str(summary),
                    "summary_sha256": digest(summary),
                    "advisory_path": str(advisory),
                    "advisory_sha256": digest(advisory),
                    "notification_result": "none",
                    "vault_artifacts_complete": True,
                    "next_action": None,
                }
                result_path.write_text(json.dumps(payload), encoding="utf-8")
                validation = subprocess.run(
                    [
                        str(SCRIPTS / "validate-collection-result.py"),
                        str(result_path),
                        str(staging),
                        payload["run_id"],
                        "0",
                        str(SOURCE_CATALOG),
                        str(source_manifest),
                        str(verified_resolutions),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(validation.returncode, 75)
                self.assertRegex(validation.stderr, r"source|access-control")

    def test_installer_places_only_declared_roles(self) -> None:
        """Install summary and advisory below their configured Vault roots."""
        staging = self.workdir / "staging"
        staging.mkdir()
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        summary.write_text("summary", encoding="utf-8")
        advisory.write_text("advisory", encoding="utf-8")
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        context = {
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(self.user),
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(self.user / ".git"),
            "it_news_archive_relative": "10_Prompt",
            "advisory_archive_relative": "03-Contexts/Reports/Security",
        }
        collection = {
            "summary_path": str(summary),
            "summary_sha256": digest(summary),
            "advisory_path": str(advisory),
            "advisory_sha256": digest(advisory),
        }
        context_path = self.workdir / "context.json"
        collection_path = self.workdir / "collection.json"
        context_path.write_text(json.dumps(context), encoding="utf-8")
        collection_path.write_text(json.dumps(collection), encoding="utf-8")
        existing_summary = (
            self.user
            / "10_Prompt"
            / "2026"
            / "07"
            / "31"
            / summary.name
        )
        existing_summary.parent.mkdir(parents=True)
        existing_summary.write_text("existing summary\n", encoding="utf-8")
        plan_path = self.workdir / "plan.json"
        with plan_path.open("w", encoding="utf-8") as output:
            subprocess.run(
                [
                    str(SCRIPTS / "install-verified-artifacts.py"),
                    "--plan",
                    str(context_path),
                    str(collection_path),
                ],
                check=True,
                stdout=output,
                text=True,
            )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(Path(plan["summary_target"]).name, "SUMMARY-IT-NEWS-2026-07-31-2.md")
        blocked_summary = Path(plan["summary_target"])
        blocked_summary.parent.mkdir(parents=True, exist_ok=True)
        blocked_summary.write_text("concurrent user target\n", encoding="utf-8")
        agents_only = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                str(context_path),
                str(collection_path),
                str(plan_path),
                "agents_security_advisory",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        agents_only_result = json.loads(agents_only.stdout)
        installed_advisory = Path(agents_only_result["advisory_target"])
        advisory_receipt = agents_only_result["installed_receipt"]
        advisory_stat = installed_advisory.lstat()
        self.assertEqual(advisory_receipt["path"], str(installed_advisory))
        self.assertEqual(advisory_receipt["sha256"], digest(advisory))
        self.assertEqual(
            advisory_receipt["identity"],
            [advisory_stat.st_dev, advisory_stat.st_ino],
        )
        self.assertEqual(advisory_receipt["size"], advisory_stat.st_size)
        self.assertEqual(advisory_receipt["mode"], advisory_stat.st_mode)
        normalized_mtime = max(1, advisory_stat.st_mtime_ns - 1_000_000_000)
        os.utime(installed_advisory, ns=(normalized_mtime, normalized_mtime))
        validated_receipt = COMMITTER_MODULE.validated_installer_receipt(
            advisory_receipt,
            installed_advisory,
            digest(advisory),
            str(self.agents),
            str(self.agents / ".git"),
        )
        self.assertEqual(validated_receipt["identity"], tuple(advisory_receipt["identity"]))
        self.assertEqual(
            blocked_summary.read_text(encoding="utf-8"),
            "concurrent user target\n",
        )
        self.assertTrue(installed_advisory.is_file())
        blocked_summary.unlink()
        installed_advisory.unlink()
        installed_advisory.write_bytes(advisory.read_bytes())
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "changed before publication"
        ):
            COMMITTER_MODULE.validated_installer_receipt(
                advisory_receipt,
                installed_advisory,
                digest(advisory),
                str(self.agents),
                str(self.agents / ".git"),
            )
        installed_advisory.unlink()

        summary_result = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                str(context_path),
                str(collection_path),
                str(plan_path),
                "user_it_news_summary",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        advisory_result = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                str(context_path),
                str(collection_path),
                str(plan_path),
                "agents_security_advisory",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        summary_output = json.loads(summary_result.stdout)
        advisory_output = json.loads(advisory_result.stdout)
        self.assertEqual(
            summary_output["installed_receipt"]["sha256"], digest(summary)
        )
        self.assertEqual(
            advisory_output["installed_receipt"]["sha256"], digest(advisory)
        )
        installed = {
            **summary_output,
            **advisory_output,
        }
        self.assertTrue(Path(installed["summary_target"]).is_file())
        self.assertTrue(Path(installed["advisory_target"]).is_file())
        self.assertEqual(existing_summary.read_text(encoding="utf-8"), "existing summary\n")

        unrelated_plan = dict(plan)
        unrelated_plan["summary_target"] = str(
            Path(plan["summary_target"]).with_name(
                "SUMMARY-IT-NEWS-2026-07-31junk-2.md"
            )
        )
        plan_path.write_text(json.dumps(unrelated_plan), encoding="utf-8")
        unrelated = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                str(context_path),
                str(collection_path),
                str(plan_path),
                "user_it_news_summary",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(unrelated.returncode, 75)
        self.assertIn("artifact plan target is invalid", unrelated.stderr)

        context["it_news_archive_relative"] = ""
        context_path.write_text(json.dumps(context), encoding="utf-8")
        rejected = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                "--plan",
                str(context_path),
                str(collection_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 75)

        invalid_context = self.workdir / "invalid-install-context.json"
        invalid_context.write_bytes(b"\xff")
        rejected = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                "--plan",
                str(invalid_context),
                str(collection_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 75)

    def test_installer_failure_cleanup_preserves_replaced_inode(self) -> None:
        """Never unlink a third-party entry that replaces a failed O_EXCL target."""
        source = self.workdir / "staged-summary.md"
        source.write_text("verified artifact\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        relative = PurePosixPath("installed")
        target = self.user / "installed" / "summary.md"
        before_reservations = set(
            (self.user / ".git").glob(".vault-publisher-install-*")
        )

        real_link = INSTALLER_MODULE.link_no_replace_durable

        def replace_then_fail(*arguments: object) -> None:
            real_link(*arguments)
            target.unlink()
            target.write_text("third-party replacement\n", encoding="utf-8")
            raise OSError("fixture write failure")

        with mock.patch.object(
            INSTALLER_MODULE,
            "link_no_replace_durable",
            side_effect=replace_then_fail,
        ), self.assertRaisesRegex(
            INSTALLER_MODULE.InstallError, "planned destination is no longer available"
        ) as raised:
            INSTALLER_MODULE.install(
                source,
                digest,
                self.user,
                relative,
                target.name,
                target,
                self.user / ".git",
            )
        self.assertIsInstance(raised.exception.__cause__, INSTALLER_MODULE.InstallError)
        self.assertIn("replaced during failed cleanup", str(raised.exception.__cause__))
        self.assertEqual(
            target.read_text(encoding="utf-8"), "third-party replacement\n"
        )
        reservations = set(
            (self.user / ".git").glob(".vault-publisher-install-*")
        ) - before_reservations
        self.assertEqual(len(reservations), 1)
        self.assertEqual(
            (next(iter(reservations)) / "artifact").read_bytes(),
            source.read_bytes(),
        )
        self.assertEqual(
            list(target.parent.glob(".vault-publisher-install-*")), []
        )

    def test_installer_first_write_failure_keeps_partial_private_inode(self) -> None:
        """Do not mask an early write failure with uninitialized cleanup metadata."""
        source = self.workdir / "early-write-source.md"
        source.write_bytes(b"verified artifact\n")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        relative = PurePosixPath("early-write")
        target = self.user / "early-write" / "summary.md"
        before_reservations = set(
            (self.user / ".git").glob(".vault-publisher-install-*")
        )
        real_write = INSTALLER_MODULE.os.write
        calls = 0

        def fail_after_partial_write(descriptor: int, content: bytes) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(descriptor, content[:3])
            raise OSError("fixture early write failure")

        with mock.patch.object(
            INSTALLER_MODULE.os,
            "write",
            side_effect=fail_after_partial_write,
        ), self.assertRaisesRegex(OSError, "fixture early write failure"):
            INSTALLER_MODULE.install(
                source,
                digest,
                self.user,
                relative,
                target.name,
                target,
                self.user / ".git",
            )
        self.assertFalse(target.exists())
        reservations = set(
            (self.user / ".git").glob(".vault-publisher-install-*")
        ) - before_reservations
        self.assertEqual(len(reservations), 1)
        self.assertEqual((next(iter(reservations)) / "artifact").read_bytes(), b"ver")

    def test_installer_failure_cleanup_restores_non_hardlinkable_replacements(
        self,
    ) -> None:
        """Restore directory and symlink entries with metadata and index intact."""
        source = self.workdir / "staged-type-generic-summary.md"
        source.write_text("verified artifact\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        staged = self.user / "installer-unrelated-staged.md"
        staged.write_text("staged\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.user), "add", staged.name], check=True)
        fixed_mtime = 1_700_000_100_000_000_000

        for replacement_type in ("directory", "symlink"):
            with self.subTest(replacement_type=replacement_type):
                relative = PurePosixPath(f"installed-{replacement_type}")
                target = self.user / str(relative) / "summary.md"
                symlink_source = self.user / f"installer-{replacement_type}-source"
                if replacement_type == "symlink":
                    symlink_source.write_text("symlink target\n", encoding="utf-8")
                replacement_fingerprint: tuple[int, int, int, int, int] | None = None
                status_before = b""
                index_before = b""

                real_link = INSTALLER_MODULE.link_no_replace_durable

                def replace_then_fail(*arguments: object) -> None:
                    nonlocal replacement_fingerprint, status_before, index_before
                    real_link(*arguments)
                    target.unlink()
                    if replacement_type == "directory":
                        target.mkdir(mode=0o750)
                        (target / "preserved.txt").write_text(
                            "directory content\n", encoding="utf-8"
                        )
                        os.chmod(target, 0o750)
                    else:
                        target.symlink_to(symlink_source.name)
                    os.utime(
                        target,
                        ns=(fixed_mtime, fixed_mtime),
                        follow_symlinks=False,
                    )
                    metadata = os.lstat(target)
                    replacement_fingerprint = (
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_mode,
                        metadata.st_size,
                        metadata.st_mtime_ns,
                    )
                    status_before = subprocess.check_output(
                        [
                            "git", "-C", str(self.user), "status",
                            "--porcelain=v2", "-z", "--untracked-files=all",
                        ]
                    )
                    index_before = (self.user / ".git" / "index").read_bytes()
                    raise OSError("fixture write failure")

                with mock.patch.object(
                    INSTALLER_MODULE,
                    "link_no_replace_durable",
                    side_effect=replace_then_fail,
                ), self.assertRaisesRegex(
                    INSTALLER_MODULE.InstallError,
                    "planned destination is no longer available",
                ) as raised:
                    INSTALLER_MODULE.install(
                        source,
                        digest,
                        self.user,
                        relative,
                        target.name,
                        target,
                        self.user / ".git",
                    )
                self.assertIsInstance(
                    raised.exception.__cause__, INSTALLER_MODULE.InstallError
                )
                self.assertIn(
                    "replaced during failed cleanup", str(raised.exception.__cause__)
                )
                metadata = os.lstat(target)
                self.assertEqual(
                    (
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_mode,
                        metadata.st_size,
                        metadata.st_mtime_ns,
                    ),
                    replacement_fingerprint,
                )
                if replacement_type == "directory":
                    self.assertEqual(
                        (target / "preserved.txt").read_text(encoding="utf-8"),
                        "directory content\n",
                    )
                else:
                    self.assertEqual(os.readlink(target), symlink_source.name)
                self.assertEqual(
                    subprocess.check_output(
                        [
                            "git", "-C", str(self.user), "status",
                            "--porcelain=v2", "-z", "--untracked-files=all",
                        ]
                    ),
                    status_before,
                )
                self.assertEqual(
                    (self.user / ".git" / "index").read_bytes(), index_before
                )

    def test_installer_failure_retains_same_inode_drift(self) -> None:
        """Never unlink an installer inode after content, size, or mode drift."""
        source = self.workdir / "staged-drift-summary.md"
        source.write_text("verified artifact\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        real_link = INSTALLER_MODULE.link_no_replace_durable
        for drift in ("content", "size", "mode"):
            with self.subTest(drift=drift):
                relative = PurePosixPath(f"installer-drift-{drift}")
                target = self.user / str(relative) / "summary.md"
                before_quarantines = set(
                    (self.user / ".git").glob(".vault-publisher-install-*")
                )

                def drift_then_fail(*arguments: object) -> None:
                    real_link(*arguments)
                    if drift == "content":
                        target.write_text("tampered\n", encoding="utf-8")
                    elif drift == "size":
                        target.write_text("tampered with extra bytes\n", encoding="utf-8")
                    else:
                        os.chmod(target, 0o640)
                    raise OSError("fixture write failure")

                with mock.patch.object(
                    INSTALLER_MODULE,
                    "link_no_replace_durable",
                    side_effect=drift_then_fail,
                ), self.assertRaisesRegex(
                    INSTALLER_MODULE.InstallError,
                    "planned destination is no longer available",
                ) as raised:
                    INSTALLER_MODULE.install(
                        source,
                        digest,
                        self.user,
                        relative,
                        target.name,
                        target,
                        self.user / ".git",
                    )
                self.assertIsInstance(
                    raised.exception.__cause__, INSTALLER_MODULE.InstallError
                )
                self.assertIn(
                    "changed and was retained", str(raised.exception.__cause__)
                )
                self.assertFalse(target.exists())
                quarantines = set(
                    (self.user / ".git").glob(".vault-publisher-install-*")
                ) - before_quarantines
                self.assertEqual(len(quarantines), 1)
                held = next(iter(quarantines)) / "artifact"
                self.assertTrue(held.is_file())
                if drift == "content":
                    self.assertEqual(held.read_text(encoding="utf-8"), "tampered\n")
                elif drift == "size":
                    self.assertEqual(
                        held.read_text(encoding="utf-8"),
                        "tampered with extra bytes\n",
                    )
                else:
                    self.assertEqual(stat.S_IMODE(held.stat().st_mode), 0o640)
                self.assertEqual(
                    list(target.parent.glob(".vault-publisher-install-*")), []
                )

    def test_installer_hash_rewinds_a_post_write_descriptor(self) -> None:
        """Hash all owned bytes even when cleanup begins with the fd at EOF."""
        artifact = self.workdir / "installer-post-write-hash.md"
        content = b"non-empty installer artifact\n"
        artifact.write_bytes(content)
        descriptor = os.open(artifact, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.lseek(descriptor, 0, os.SEEK_END)
            self.assertEqual(
                INSTALLER_MODULE.sha256_fd(descriptor),
                hashlib.sha256(content).hexdigest(),
            )
            self.assertEqual(os.lseek(descriptor, 0, os.SEEK_CUR), 0)
        finally:
            os.close(descriptor)

    def test_installer_failure_reopens_post_move_retention(self) -> None:
        """Read failed-install worktree from its bound reservation after movement."""
        source = self.workdir / "post-move-installer-summary.md"
        source.write_bytes(b"verified artifact\n")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()

        for drift in ("content", "size", "mode"):
            with self.subTest(drift=drift):
                relative = PurePosixPath(f"installer-post-move-{drift}")
                target = self.user / str(relative) / "summary.md"
                before_reservations = set(
                    (self.user / ".git").glob(".vault-publisher-install-*")
                )
                real_link = INSTALLER_MODULE.link_no_replace_durable
                real_rename = INSTALLER_MODULE.rename_no_replace

                def install_then_fail(*arguments: object) -> None:
                    real_link(*arguments)
                    raise OSError("fixture post-install failure")

                def drift_after_cleanup_move(
                    source_fd: int,
                    source_name: str,
                    destination_fd: int,
                    destination_name: str,
                ) -> None:
                    real_rename(
                        source_fd, source_name, destination_fd, destination_name
                    )
                    retained_fd = os.open(
                        destination_name,
                        os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=destination_fd,
                    )
                    try:
                        if drift == "content":
                            os.write(retained_fd, b"tampered artifact\n")
                            os.ftruncate(retained_fd, len(b"tampered artifact\n"))
                        elif drift == "size":
                            os.write(retained_fd, b"verified artifact extended\n")
                            os.ftruncate(
                                retained_fd, len(b"verified artifact extended\n")
                            )
                        else:
                            os.fchmod(retained_fd, 0o640)
                        os.fsync(retained_fd)
                    finally:
                        os.close(retained_fd)

                with mock.patch.object(
                    INSTALLER_MODULE,
                    "link_no_replace_durable",
                    side_effect=install_then_fail,
                ), mock.patch.object(
                    INSTALLER_MODULE,
                    "rename_no_replace",
                    side_effect=drift_after_cleanup_move,
                ), self.assertRaisesRegex(
                    INSTALLER_MODULE.InstallError,
                    "planned destination is no longer available",
                ) as raised:
                    INSTALLER_MODULE.install(
                        source,
                        digest,
                        self.user,
                        relative,
                        target.name,
                        target,
                        self.user / ".git",
                    )
                self.assertIsInstance(
                    raised.exception.__cause__, INSTALLER_MODULE.InstallError
                )
                self.assertIn(
                    "changed and was retained", str(raised.exception.__cause__)
                )
                self.assertFalse(target.exists())
                reservations = set(
                    (self.user / ".git").glob(".vault-publisher-install-*")
                ) - before_reservations
                self.assertEqual(len(reservations), 1)
                reservation = next(iter(reservations))
                held = reservation / "worktree"
                self.assertTrue(held.is_file())
                if drift == "content":
                    self.assertEqual(held.read_bytes(), b"tampered artifact\n")
                elif drift == "size":
                    self.assertEqual(
                        held.read_bytes(), b"verified artifact extended\n"
                    )
                else:
                    self.assertEqual(held.read_bytes(), b"verified artifact\n")
                    self.assertEqual(stat.S_IMODE(held.stat().st_mode), 0o640)

    def test_evidence_preparer_invalid_utf8_fails_closed(self) -> None:
        """Convert malformed runtime JSON into the status-75 contract."""
        invalid = self.workdir / "invalid-evidence-runtime.json"
        invalid.write_bytes(b"\xff")
        placeholder = self.workdir / "placeholder.json"
        placeholder.write_text("{}", encoding="utf-8")
        output = self.workdir / "evidence-plan.json"
        result = subprocess.run(
            [
                str(SCRIPTS / "prepare-publication-evidence.py"),
                str(invalid),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                "run-id",
                "2026-07-31T04:00:00+09:00",
                hashlib.sha256(placeholder.read_bytes()).hexdigest(),
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("evidence preparation failed", result.stderr)

    def test_evidence_preparer_rejects_changed_review_bytes(self) -> None:
        """Bind evidence generation to the exact review already used for push."""
        placeholder = self.workdir / "evidence-placeholder.json"
        placeholder.write_text("{}", encoding="utf-8")
        output = self.workdir / "changed-review-plan.json"
        result = subprocess.run(
            [
                str(SCRIPTS / "prepare-publication-evidence.py"),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                "run-id",
                "2026-07-31T04:00:00+09:00",
                hashlib.sha256(b"different review").hexdigest(),
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("approved review digest mismatch", result.stderr)

    def test_evidence_preparer_rejects_correlated_context_substitution(self) -> None:
        """Keep a valid alternate runtime+context pair outside the approved run."""
        approved_context = json.dumps(
            {
                "runtime": {
                    "publisher_git_name": "Fixture Publisher",
                    "publisher_git_email": "publisher@example.invalid",
                }
            },
            sort_keys=True,
        ).encode("utf-8")
        review = {
            "publication_context_sha256": hashlib.sha256(
                approved_context
            ).hexdigest()
        }
        substituted_context = approved_context.replace(
            b"publisher@example.invalid", b"other-pub@example.invalid"
        )
        with self.assertRaises(EVIDENCE_MODULE.EvidenceError):
            EVIDENCE_MODULE.approved_context_digest(review, substituted_context)

    def test_evidence_block_is_inserted_inside_canonical_section(self) -> None:
        """Place evidence before the next peer heading instead of at task EOF."""
        task = self.agents / "tasks" / "standing.md"
        marker = b"<!-- vault-change-publisher:fixture -->"
        block = "\n#### Daily publication evidence — fixture\n".encode() + marker + b"\n"
        EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
            self.agents, Path("tasks/standing.md"), block
        )
        text = task.read_text(encoding="utf-8")
        self.assertLess(text.index("Daily publication evidence"), text.index("## Reviews"))
        self.assertEqual(text.count("vault-change-publisher:fixture"), 1)

    def test_evidence_block_requires_one_canonical_section(self) -> None:
        """Fail closed when the insertion anchor is absent or ambiguous."""
        task = self.agents / "tasks" / "standing.md"
        marker = b"<!-- vault-change-publisher:fixture -->"
        block = "\n#### Daily publication evidence — fixture\n".encode() + marker + b"\n"
        for content in (
            "# Standing\n",
            "### Vault Publication Evidence\n### Vault Publication Evidence\n",
        ):
            with self.subTest(content=content):
                task.write_text(content, encoding="utf-8")
                with self.assertRaises(EVIDENCE_MODULE.EvidenceError):
                    EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                        self.agents, Path("tasks/standing.md"), block
                    )

    def test_evidence_block_ignores_headings_inside_fences(self) -> None:
        """Treat ATX-like lines in backtick and tilde fences as content."""
        task = self.agents / "tasks" / "standing.md"
        task.write_text(
            "# Standing\n\n### Vault Publication Evidence\n\n"
            "```md\n## example inside fence\n```\n"
            "~~~\n### another example\n~~~\n"
            "section tail\n\n## Reviews\n",
            encoding="utf-8",
        )
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()
        EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
            self.agents, Path("tasks/standing.md"), block
        )
        text = task.read_text(encoding="utf-8")
        self.assertLess(text.index("~~~\nsection tail"), text.index("Daily publication"))
        self.assertLess(text.index("Daily publication"), text.index("## Reviews"))

    def test_evidence_block_rejects_unterminated_fences_without_mutation(self) -> None:
        """Do not insert evidence into an unterminated backtick or tilde fence."""
        task = self.agents / "tasks" / "standing.md"
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()
        for fence in ("```md", "~~~"):
            with self.subTest(fence=fence):
                original = (
                    "# Standing\n\n### Vault Publication Evidence\n\n"
                    f"{fence}\nunclosed example\n## Reviews\n"
                )
                task.write_text(original, encoding="utf-8")
                with self.assertRaisesRegex(
                    EVIDENCE_MODULE.EvidenceError, "unterminated fenced block"
                ):
                    EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                        self.agents, Path("tasks/standing.md"), block
                    )
                self.assertEqual(task.read_text(encoding="utf-8"), original)

        with self.assertRaisesRegex(ValueError, "before evidence heading"):
            EVIDENCE_MODULE.insert_evidence_block(
                b"```md\n### Vault Publication Evidence\n",
                block,
                b"vault-change-publisher:fixture",
            )

    def test_evidence_patch_marks_missing_terminal_newlines(self) -> None:
        """Render a reviewable canonical diff when either side lacks LF."""
        cases = (
            (b"before", b"after", b"-before\n", b"+after\n"),
            (b"before\r", b"after\r", b"-before\r\n", b"+after\r\n"),
        )
        for before, after, removed, added in cases:
            with self.subTest(before=before, after=after):
                patch = FINALIZER_MODULE.canonical_patch(
                    "tasks/standing.md", before, after
                )
                self.assertEqual(
                    patch.count(b"\\ No newline at end of file\n"), 2
                )
                self.assertIn(removed + b"\\ No newline", patch)
                self.assertIn(added + b"\\ No newline", patch)

        crlf_patch = FINALIZER_MODULE.canonical_patch(
            "tasks/standing.md", b"before\r\n", b"after\r\n"
        )
        self.assertNotIn(b"\\ No newline at end of file", crlf_patch)

    def test_evidence_target_requires_one_nul_terminated_tree_record(self) -> None:
        """Reject ambiguous or truncated ls-tree output before evidence mutation."""
        record = b"100644 blob " + (b"a" * 40) + b"\ttasks/standing.md\0"
        runtime = {
            "agents_vault_root": str(self.agents),
            "agents_git_dir": str(self.agents / ".git"),
        }
        baseline = {"agents_vault": {"index_entries": []}}
        for raw in (record.rstrip(b"\0"), record + record):
            with self.subTest(raw=raw):
                with mock.patch.object(EVIDENCE_MODULE, "git_bytes", return_value=raw):
                    with self.assertRaisesRegex(
                        EVIDENCE_MODULE.EvidenceError, "missing or ambiguous"
                    ):
                        EVIDENCE_MODULE.target_entries(
                            runtime, baseline, "tasks/standing.md"
                        )

    def test_evidence_replacement_preserves_mode_under_restrictive_umask(self) -> None:
        """Preserve the exact reviewed mode during install and rollback."""
        target = self.agents / "mode-evidence.md"
        target.write_bytes(b"before\n")
        target.chmod(0o664)
        previous_umask = os.umask(0o022)
        try:
            receipt = FINALIZER_MODULE.replace_worktree_candidate(
                str(self.agents),
                target.name,
                hashlib.sha256(b"before\n").hexdigest(),
                b"candidate\n",
            )
            self.assertEqual(target.stat().st_mode & 0o777, 0o664)
            normalized_mtime = max(1, target.stat().st_mtime_ns - 1_000_000_000)
            os.utime(target, ns=(normalized_mtime, normalized_mtime))
            FINALIZER_MODULE.rollback_worktree_candidate(receipt)
        finally:
            os.umask(previous_umask)
        self.assertEqual(target.read_bytes(), b"before\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o664)

    def test_evidence_partial_candidate_write_keeps_complete_recovery_receipt(self) -> None:
        """Describe both retained artifacts even when candidate writing fails early."""
        target = self.agents / "tasks" / "standing.md"
        original = target.read_bytes()
        receipt: dict[str, object] = {
            "base_head": "1" * 40,
            "candidate_head": "2" * 40,
        }

        def fail_after_partial_write(descriptor: int, content: bytes) -> None:
            os.write(descriptor, content[:7])
            raise FINALIZER_MODULE.FinalizationError("fixture partial write")

        with mock.patch.object(
            FINALIZER_MODULE,
            "write_all",
            side_effect=fail_after_partial_write,
        ), self.assertRaisesRegex(
            FINALIZER_MODULE.FinalizationError, "fixture partial write"
        ):
            FINALIZER_MODULE.replace_worktree_candidate(
                str(self.agents),
                "tasks/standing.md",
                hashlib.sha256(original).hexdigest(),
                b"candidate standing task\n",
                str(self.agents / ".git"),
                recovery_receipt=receipt,
            )
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue(receipt["original_restored"])
        self.assertFalse(receipt["canonical_candidate_installed"])
        self.assertEqual(receipt["candidate_size"], 7)
        recovery = FINALIZER_MODULE.evidence_recovery(receipt, False, False)
        self.assertEqual(recovery["candidate"]["size"], 7)
        self.assertTrue(recovery["original_restored"])

    def test_evidence_rollback_refuses_same_byte_replacement(self) -> None:
        """Bind rollback authority to the installed candidate inode, not its SHA alone."""
        target = self.agents / "evidence-same-byte-replacement.md"
        target.write_bytes(b"before\n")
        target.chmod(0o664)
        receipt = FINALIZER_MODULE.replace_worktree_candidate(
            str(self.agents),
            target.name,
            hashlib.sha256(b"before\n").hexdigest(),
            b"candidate\n",
        )
        owned_identity = tuple(receipt["candidate_identity"])
        target.unlink()
        target.write_bytes(b"candidate\n")
        target.chmod(stat.S_IMODE(int(receipt["candidate_mode"])))
        replacement_identity = (target.stat().st_dev, target.stat().st_ino)
        self.assertNotEqual(replacement_identity, owned_identity)

        with self.assertRaisesRegex(
            FINALIZER_MODULE.FinalizationError, "rollback refused"
        ):
            FINALIZER_MODULE.rollback_worktree_candidate(receipt)
        self.assertEqual(target.read_bytes(), b"candidate\n")
        self.assertEqual(
            (target.stat().st_dev, target.stat().st_ino), replacement_identity
        )

    def test_evidence_install_restores_a_last_check_replacement(self) -> None:
        """Never overwrite an entry swapped in immediately before install quarantine."""
        target = self.agents / "evidence-install-race.md"
        target.write_bytes(b"before\n")
        target.chmod(0o664)
        real_rename = FINALIZER_MODULE.rename_no_replace
        calls = 0
        replacement_identity: tuple[int, int] | None = None

        def replace_before_quarantine(*args: object, **kwargs: object) -> None:
            nonlocal calls, replacement_identity
            calls += 1
            if calls == 1:
                target.unlink()
                target.write_bytes(b"third party\n")
                target.chmod(0o640)
                replacement_identity = (target.stat().st_dev, target.stat().st_ino)
            real_rename(*args, **kwargs)

        with mock.patch.object(
            FINALIZER_MODULE,
            "rename_no_replace",
            side_effect=replace_before_quarantine,
        ), self.assertRaisesRegex(
            FINALIZER_MODULE.FinalizationError, "replacement was restored"
        ):
            FINALIZER_MODULE.replace_worktree_candidate(
                str(self.agents),
                target.name,
                hashlib.sha256(b"before\n").hexdigest(),
                b"candidate\n",
            )
        self.assertEqual(target.read_bytes(), b"third party\n")
        self.assertEqual(
            (target.stat().st_dev, target.stat().st_ino), replacement_identity
        )
        self.assertEqual(
            list(self.agents.glob(f".{target.name}.publication-quarantine-*")), []
        )

    def test_evidence_rollback_restores_a_last_check_replacement(self) -> None:
        """Restore, rather than delete, an entry swapped in before rollback quarantine."""
        target = self.agents / "evidence-rollback-race.md"
        target.write_bytes(b"before\n")
        target.chmod(0o664)
        receipt = FINALIZER_MODULE.replace_worktree_candidate(
            str(self.agents),
            target.name,
            hashlib.sha256(b"before\n").hexdigest(),
            b"candidate\n",
        )
        real_rename = FINALIZER_MODULE.rename_no_replace
        calls = 0
        replacement_identity: tuple[int, int] | None = None

        def replace_before_quarantine(*args: object, **kwargs: object) -> None:
            nonlocal calls, replacement_identity
            calls += 1
            if calls == 1:
                target.unlink()
                target.write_bytes(b"candidate\n")
                target.chmod(stat.S_IMODE(int(receipt["candidate_mode"])))
                replacement_identity = (target.stat().st_dev, target.stat().st_ino)
            real_rename(*args, **kwargs)

        with mock.patch.object(
            FINALIZER_MODULE,
            "rename_no_replace",
            side_effect=replace_before_quarantine,
        ), self.assertRaisesRegex(
            FINALIZER_MODULE.FinalizationError, "replacement was restored"
        ):
            FINALIZER_MODULE.rollback_worktree_candidate(receipt)
        self.assertEqual(target.read_bytes(), b"candidate\n")
        self.assertEqual(
            (target.stat().st_dev, target.stat().st_ino), replacement_identity
        )
        self.assertEqual(
            list(self.agents.glob(f".{target.name}.rollback-quarantine-*")), []
        )

    def test_evidence_rollback_quarantines_owned_candidate_on_reoccupation(self) -> None:
        """Preserve both the owned candidate and a new path occupant on collision."""
        target = self.agents / "evidence-rollback-reoccupation.md"
        target.write_bytes(b"before\n")
        target.chmod(0o664)
        receipt = FINALIZER_MODULE.replace_worktree_candidate(
            str(self.agents),
            target.name,
            hashlib.sha256(b"before\n").hexdigest(),
            b"candidate\n",
        )
        owned_identity = tuple(receipt["candidate_identity"])
        real_rename = FINALIZER_MODULE.rename_no_replace
        calls = 0

        def reoccupy_after_quarantine(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            real_rename(*args, **kwargs)
            if calls == 1:
                target.write_bytes(b"new occupant\n")

        with mock.patch.object(
            FINALIZER_MODULE,
            "rename_no_replace",
            side_effect=reoccupy_after_quarantine,
        ), self.assertRaisesRegex(
            FINALIZER_MODULE.FinalizationError,
            "both tombstones were retained",
        ):
            FINALIZER_MODULE.rollback_worktree_candidate(receipt)
        self.assertEqual(target.read_bytes(), b"new occupant\n")
        quarantines = list(
            (self.agents / ".git").glob(".publication-evidence-candidate-*")
        )
        self.assertEqual(len(quarantines), 1)
        held = quarantines[0] / "artifact"
        self.assertEqual((held.stat().st_dev, held.stat().st_ino), owned_identity)
        self.assertEqual(held.read_bytes(), b"candidate\n")

    def test_evidence_original_detachment_reopens_post_move_retention(self) -> None:
        """Seal detached original bytes through the bound quarantine directory."""
        for drift in ("content", "size", "mode"):
            with self.subTest(drift=drift):
                target = self.agents / f"evidence-original-post-move-{drift}.md"
                target.write_bytes(b"before\n")
                target.chmod(0o664)
                quarantine_root = self.agents / ".git"
                before_quarantines = set(
                    quarantine_root.glob(".publication-evidence-original-*")
                )
                real_rename = FINALIZER_MODULE.rename_no_replace

                def drift_after_detachment(
                    source_fd: int,
                    source_name: str,
                    destination_fd: int,
                    destination_name: str,
                ) -> None:
                    real_rename(
                        source_fd, source_name, destination_fd, destination_name
                    )
                    retained_fd = os.open(
                        destination_name,
                        os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=destination_fd,
                    )
                    try:
                        if drift == "content":
                            os.write(retained_fd, b"tamper\n")
                            os.ftruncate(retained_fd, len(b"tamper\n"))
                        elif drift == "size":
                            os.write(retained_fd, b"before extended\n")
                            os.ftruncate(retained_fd, len(b"before extended\n"))
                        else:
                            os.fchmod(retained_fd, 0o600)
                        os.fsync(retained_fd)
                    finally:
                        os.close(retained_fd)

                with mock.patch.object(
                    FINALIZER_MODULE,
                    "rename_no_replace",
                    side_effect=drift_after_detachment,
                ), self.assertRaisesRegex(
                    FINALIZER_MODULE.FinalizationError,
                    "retained original changed after detachment",
                ):
                    FINALIZER_MODULE.replace_worktree_candidate(
                        str(self.agents),
                        target.name,
                        hashlib.sha256(b"before\n").hexdigest(),
                        b"candidate\n",
                    )
                self.assertFalse(target.exists())
                quarantines = set(
                    quarantine_root.glob(".publication-evidence-original-*")
                ) - before_quarantines
                self.assertEqual(len(quarantines), 1)
                quarantine = next(iter(quarantines))
                retained = quarantine / "detached"
                reserved = quarantine / "artifact"
                self.assertEqual(
                    (retained.stat().st_dev, retained.stat().st_ino),
                    (reserved.stat().st_dev, reserved.stat().st_ino),
                )
                if drift == "content":
                    self.assertEqual(retained.read_bytes(), b"tamper\n")
                elif drift == "size":
                    self.assertEqual(retained.read_bytes(), b"before extended\n")
                else:
                    self.assertEqual(retained.read_bytes(), b"before\n")
                    self.assertEqual(stat.S_IMODE(retained.stat().st_mode), 0o600)

    def test_evidence_install_retains_original_on_same_inode_drift(self) -> None:
        """Recheck candidate bytes, size, and mode after the install rename."""
        mutations = {
            "content": lambda target: target.write_bytes(b"changed!\n"),
            "size": lambda target: target.write_bytes(b"candidate extended\n"),
            "mode": lambda target: target.chmod(0o600),
        }
        for name, mutate in mutations.items():
            with self.subTest(drift=name):
                directory = self.agents / f"install-{name}"
                directory.mkdir()
                target = directory / "standing.md"
                target.write_bytes(b"before\n")
                target.chmod(0o664)
                quarantine_root = self.agents / ".git"
                before_quarantines = set(
                    quarantine_root.glob(".publication-evidence-original-*")
                )
                real_link = FINALIZER_MODULE.link_no_replace_durable
                calls = 0

                def mutate_after_candidate_install(
                    *args: object, **kwargs: object
                ) -> None:
                    nonlocal calls
                    calls += 1
                    real_link(*args, **kwargs)
                    if calls == 2:
                        mutate(target)

                with mock.patch.object(
                    FINALIZER_MODULE,
                    "link_no_replace_durable",
                    side_effect=mutate_after_candidate_install,
                ), self.assertRaisesRegex(
                    FINALIZER_MODULE.FinalizationError,
                    "candidate changed after installation",
                ):
                    FINALIZER_MODULE.replace_worktree_candidate(
                        str(self.agents),
                        str(target.relative_to(self.agents)),
                        hashlib.sha256(b"before\n").hexdigest(),
                        b"candidate\n",
                    )
                quarantines = list(
                    set(quarantine_root.glob(".publication-evidence-original-*"))
                    - before_quarantines
                )
                self.assertEqual(len(quarantines), 1)
                self.assertEqual(
                    (quarantines[0] / "artifact").read_bytes(), b"before\n"
                )

    def test_evidence_rollback_refuses_same_inode_metadata_drift(self) -> None:
        """Preserve a same-inode concurrent edit and the retained original."""
        mutations = {
            "content": lambda target: target.write_bytes(b"changed!\n"),
            "size": lambda target: target.write_bytes(b"candidate extended\n"),
            "mode": lambda target: target.chmod(0o600),
        }
        for name, mutate in mutations.items():
            with self.subTest(drift=name):
                directory = self.agents / f"rollback-{name}"
                directory.mkdir()
                target = directory / "standing.md"
                target.write_bytes(b"before\n")
                target.chmod(0o664)
                receipt = FINALIZER_MODULE.replace_worktree_candidate(
                    str(self.agents),
                    str(target.relative_to(self.agents)),
                    hashlib.sha256(b"before\n").hexdigest(),
                    b"candidate\n",
                )
                identity = (target.stat().st_dev, target.stat().st_ino)
                mutate(target)
                self.assertEqual(
                    (target.stat().st_dev, target.stat().st_ino), identity
                )
                with self.assertRaisesRegex(
                    FINALIZER_MODULE.FinalizationError,
                    "candidate tombstone changed",
                ):
                    FINALIZER_MODULE.rollback_worktree_candidate(receipt)
                original = (
                    Path(str(receipt["original_quarantine_root"]))
                    / str(receipt["original_quarantine_name"])
                    / "artifact"
                )
                self.assertEqual(original.read_bytes(), b"before\n")

    def test_evidence_rollback_reopens_post_move_worktree(self) -> None:
        """Seal the extra rollback worktree through its destination directory."""
        for drift in ("content", "size", "mode"):
            with self.subTest(drift=drift):
                target = self.agents / f"evidence-rollback-post-move-{drift}.md"
                target.write_bytes(b"before\n")
                target.chmod(0o664)
                receipt = FINALIZER_MODULE.replace_worktree_candidate(
                    str(self.agents),
                    target.name,
                    hashlib.sha256(b"before\n").hexdigest(),
                    b"candidate\n",
                )
                real_rename = FINALIZER_MODULE.rename_no_replace

                def drift_after_move(
                    source_fd: int,
                    source_name: str,
                    destination_fd: int,
                    destination_name: str,
                ) -> None:
                    real_rename(
                        source_fd, source_name, destination_fd, destination_name
                    )
                    if destination_name != "worktree":
                        return
                    retained_fd = os.open(
                        destination_name,
                        os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=destination_fd,
                    )
                    try:
                        if drift == "content":
                            os.write(retained_fd, b"changed!!\n")
                            os.ftruncate(retained_fd, len(b"changed!!\n"))
                        elif drift == "size":
                            os.write(retained_fd, b"candidate extended\n")
                            os.ftruncate(retained_fd, len(b"candidate extended\n"))
                        else:
                            os.fchmod(retained_fd, 0o600)
                        os.fsync(retained_fd)
                    finally:
                        os.close(retained_fd)

                with mock.patch.object(
                    FINALIZER_MODULE,
                    "rename_no_replace",
                    side_effect=drift_after_move,
                ), self.assertRaisesRegex(
                    FINALIZER_MODULE.FinalizationError,
                    "retained candidate changed; replacement was restored",
                ):
                    FINALIZER_MODULE.rollback_worktree_candidate(receipt)
                self.assertTrue(target.exists())
                retained = (
                    Path(str(receipt["original_quarantine_root"]))
                    / str(receipt["candidate_quarantine_name"])
                    / "artifact"
                )
                self.assertEqual(
                    (target.stat().st_dev, target.stat().st_ino),
                    (retained.stat().st_dev, retained.stat().st_ino),
                )
                if drift == "content":
                    self.assertEqual(target.read_bytes(), b"changed!!\n")
                elif drift == "size":
                    self.assertEqual(target.read_bytes(), b"candidate extended\n")
                else:
                    self.assertEqual(target.read_bytes(), b"candidate\n")
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_evidence_success_result_waits_for_index_retention(self) -> None:
        """Never expose success JSON before shared-index cleanup succeeds."""
        output = self.workdir / "gated-evidence-result.json"
        candidate = (
            str(self.workdir / "private-index"),
            {"sha256": "a" * 64, "identity": [1, 2, 3, 0o100600, 4, 5]},
        )
        result = {"outcome": "success"}

        def reject_retention(*args: object, **kwargs: object) -> None:
            self.assertFalse(output.exists())
            raise ATOMIC_FILE_OPS_MODULE.AtomicTransactionError(
                "fixture retention failure"
            )

        with mock.patch.object(
            FINALIZER_MODULE,
            "retain_path_no_replace",
            side_effect=reject_retention,
        ), self.assertRaisesRegex(
            ATOMIC_FILE_OPS_MODULE.AtomicTransactionError,
            "fixture retention failure",
        ):
            FINALIZER_MODULE.publish_success_result_after_cleanup(
                output, result, candidate
            )
        self.assertFalse(output.exists())

        def accept_retention(*args: object, **kwargs: object) -> str:
            self.assertFalse(output.exists())
            return str(self.workdir / "retained-index")

        with mock.patch.object(
            FINALIZER_MODULE,
            "retain_path_no_replace",
            side_effect=accept_retention,
        ):
            FINALIZER_MODULE.publish_success_result_after_cleanup(
                output, result, candidate
            )
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), result)

    def test_evidence_install_retains_candidate_after_path_replacement(self) -> None:
        """Keep the exact candidate durable when its canonical hardlink is replaced."""
        target = self.agents / "candidate-path-replacement.md"
        target.write_bytes(b"before\n")
        receipt: dict[str, object] = {
            "base_head": "a" * 40,
            "candidate_head": "b" * 40,
        }
        real_link = FINALIZER_MODULE.link_no_replace_durable
        calls = 0

        def replace_after_candidate_link(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            real_link(*args, **kwargs)
            if calls == 2:
                target.unlink()
                target.write_bytes(b"third party\n")

        with mock.patch.object(
            FINALIZER_MODULE,
            "link_no_replace_durable",
            side_effect=replace_after_candidate_link,
        ), self.assertRaisesRegex(
            FINALIZER_MODULE.FinalizationError,
            "candidate changed after installation",
        ):
            FINALIZER_MODULE.replace_worktree_candidate(
                str(self.agents),
                target.name,
                hashlib.sha256(b"before\n").hexdigest(),
                b"candidate\n",
                recovery_receipt=receipt,
            )
        candidate = (
            Path(str(receipt["original_quarantine_root"]))
            / str(receipt["candidate_quarantine_name"])
            / "artifact"
        )
        self.assertEqual(candidate.read_bytes(), b"candidate\n")
        self.assertEqual(
            (candidate.stat().st_dev, candidate.stat().st_ino),
            tuple(receipt["candidate_identity"]),
        )
        self.assertEqual(target.read_bytes(), b"third party\n")

    def test_evidence_transaction_finalizes_retained_original(self) -> None:
        """Retain the private original after explicit phase-two verification."""
        directory = self.agents / "finalize-transaction"
        directory.mkdir()
        target = directory / "standing.md"
        target.write_bytes(b"before\n")
        target.chmod(0o664)
        receipt = FINALIZER_MODULE.replace_worktree_candidate(
            str(self.agents),
            str(target.relative_to(self.agents)),
            hashlib.sha256(b"before\n").hexdigest(),
            b"candidate\n",
        )
        quarantine = (
            Path(str(receipt["original_quarantine_root"]))
            / str(receipt["original_quarantine_name"])
        )
        self.assertEqual((quarantine / "artifact").read_bytes(), b"before\n")
        FINALIZER_MODULE.finalize_worktree_candidate(receipt)
        self.assertEqual(target.read_bytes(), b"candidate\n")
        self.assertEqual((quarantine / "artifact").read_bytes(), b"before\n")

    def test_evidence_rollback_rechecks_original_after_restore(self) -> None:
        """Detect same-inode original drift in the final restore window."""
        target = self.agents / "restore-recheck.md"
        target.write_bytes(b"before\n")
        target.chmod(0o664)
        receipt = FINALIZER_MODULE.replace_worktree_candidate(
            str(self.agents),
            target.name,
            hashlib.sha256(b"before\n").hexdigest(),
            b"candidate\n",
        )
        real_link = FINALIZER_MODULE.link_no_replace_durable

        def drift_after_restore(*args: object, **kwargs: object) -> None:
            real_link(*args, **kwargs)
            target.write_bytes(b"restored but changed\n")

        with mock.patch.object(
            FINALIZER_MODULE,
            "link_no_replace_durable",
            side_effect=drift_after_restore,
        ), self.assertRaisesRegex(
            FINALIZER_MODULE.FinalizationError,
            "original changed after restore",
        ):
            FINALIZER_MODULE.rollback_worktree_candidate(receipt)
        self.assertEqual(target.read_bytes(), b"restored but changed\n")
        rollback_tombstones = list(
            (self.agents / ".git").glob(".publication-evidence-candidate-*")
        )
        self.assertEqual(len(rollback_tombstones), 1)
        self.assertEqual(
            (rollback_tombstones[0] / "artifact").read_bytes(), b"candidate\n"
        )

    def test_evidence_rebinds_parent_chain_and_quarantine_root(self) -> None:
        """Reject directory replacement across transaction phases."""
        parent = self.agents / "parent-binding"
        parent.mkdir()
        target = parent / "standing.md"
        target.write_bytes(b"before\n")
        receipt = FINALIZER_MODULE.replace_worktree_candidate(
            str(self.agents),
            str(target.relative_to(self.agents)),
            hashlib.sha256(b"before\n").hexdigest(),
            b"candidate\n",
        )
        moved_parent = self.agents / "parent-binding-original"
        parent.rename(moved_parent)
        parent.mkdir()
        replacement = parent / "standing.md"
        replacement.write_bytes(b"third party\n")
        with self.assertRaisesRegex(
            FINALIZER_MODULE.FinalizationError, "parent chain changed"
        ):
            FINALIZER_MODULE.finalize_worktree_candidate(receipt)
        self.assertEqual(replacement.read_bytes(), b"third party\n")
        self.assertEqual(
            (moved_parent / "standing.md").read_bytes(), b"candidate\n"
        )

        custom_root = self.agents / "private-quarantine"
        custom_root.mkdir()
        second = self.agents / "quarantine-binding.md"
        second.write_bytes(b"before\n")
        second_receipt = FINALIZER_MODULE.replace_worktree_candidate(
            str(self.agents),
            second.name,
            hashlib.sha256(b"before\n").hexdigest(),
            b"candidate\n",
            str(custom_root),
        )
        moved_root = self.agents / "private-quarantine-original"
        custom_root.rename(moved_root)
        custom_root.mkdir()
        with self.assertRaisesRegex(
            FINALIZER_MODULE.FinalizationError, "quarantine root changed"
        ):
            FINALIZER_MODULE.finalize_worktree_candidate(second_receipt)
        self.assertEqual(second.read_bytes(), b"candidate\n")

    def test_evidence_rollback_retains_original_after_restore_name_race(self) -> None:
        """Keep the exact original tombstone after replacement or deletion."""
        for race in ("replacement", "deletion"):
            with self.subTest(race=race):
                target = self.agents / f"restore-name-race-{race}.md"
                target.write_bytes(b"before\n")
                receipt = FINALIZER_MODULE.replace_worktree_candidate(
                    str(self.agents),
                    target.name,
                    hashlib.sha256(b"before\n").hexdigest(),
                    b"candidate\n",
                )
                real_link = FINALIZER_MODULE.link_no_replace_durable

                def race_after_restore(*arguments: object, **kwargs: object) -> None:
                    real_link(*arguments, **kwargs)
                    target.unlink()
                    if race == "replacement":
                        target.write_bytes(b"third party\n")

                with mock.patch.object(
                    FINALIZER_MODULE,
                    "link_no_replace_durable",
                    side_effect=race_after_restore,
                ), self.assertRaisesRegex(
                    FINALIZER_MODULE.FinalizationError, "after restore"
                ):
                    FINALIZER_MODULE.rollback_worktree_candidate(receipt)
                original = (
                    Path(str(receipt["original_quarantine_root"]))
                    / str(receipt["original_quarantine_name"])
                    / "artifact"
                )
                candidate = (
                    Path(str(receipt["original_quarantine_root"]))
                    / str(receipt["rollback_candidate_quarantine_name"])
                    / "artifact"
                )
                self.assertEqual(original.read_bytes(), b"before\n")
                self.assertEqual(candidate.read_bytes(), b"candidate\n")
                if race == "replacement":
                    self.assertEqual(target.read_bytes(), b"third party\n")
                else:
                    self.assertFalse(target.exists())

    def test_evidence_target_rejects_intermediate_symlink(self) -> None:
        """Bind every evidence parent component to the Vault root descriptor."""
        outside = self.root / "outside-evidence"
        nested = outside / "nested"
        nested.mkdir(parents=True)
        target = nested / "standing.md"
        target.write_bytes(b"outside before\n")
        (self.agents / "linked-evidence").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(
            FINALIZER_MODULE.FinalizationError,
            "parent contains a symlink",
        ):
            FINALIZER_MODULE.replace_worktree_candidate(
                str(self.agents),
                "linked-evidence/nested/standing.md",
                hashlib.sha256(b"outside before\n").hexdigest(),
                b"candidate\n",
            )
        self.assertEqual(target.read_bytes(), b"outside before\n")

    def test_evidence_push_retries_transient_remote_verification(self) -> None:
        """A transient ls-remote failure must not discard a successful fixed push."""
        commit = "b" * 40
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            FINALIZER_MODULE, "require_fast_forward_target"
        ) as ancestry, mock.patch.object(
            FINALIZER_MODULE, "git", return_value=completed
        ) as git, mock.patch.object(
            FINALIZER_MODULE,
            "remote_head",
            side_effect=[
                "a" * 40,
                TRANSPORT_MODULE.TransportError("transient"),
                commit,
            ],
        ):
            remote = FINALIZER_MODULE.push_evidence_with_retry(
                "/agents", "remote", "/agents/.git", commit, "a" * 40
            )
        self.assertEqual(remote, commit)
        self.assertEqual(git.call_count, 1)
        ancestry.assert_called_once_with(
            "/agents", "a" * 40, commit, "/agents/.git"
        )
        for call in git.call_args_list:
            self.assertEqual(
                call.args[-3:],
                ("push", "remote", f"{commit}:refs/heads/main"),
            )
            self.assertFalse(
                any(str(argument).startswith("--force") for argument in call.args)
            )

    def test_evidence_push_rejects_non_descendant_target_before_transport(self) -> None:
        """Do not let evidence publication rewrite reviewed remote history."""
        completed = subprocess.CompletedProcess([], 1, "", "")
        with mock.patch.object(FINALIZER_MODULE, "git", return_value=completed) as git:
            with self.assertRaisesRegex(
                FINALIZER_MODULE.FinalizationError, "not a descendant"
            ):
                FINALIZER_MODULE.push_evidence_with_retry(
                    "/agents", "remote", "/agents/.git", "b" * 40, "a" * 40
                )
        git.assert_called_once_with(
            "/agents",
            "merge-base",
            "--is-ancestor",
            "a" * 40,
            "b" * 40,
            check=False,
            git_dir="/agents/.git",
        )

    def test_evidence_target_content_guards_fail_closed(self) -> None:
        """Reject malformed, oversized, growing, and oversized-output targets."""
        task = self.agents / "tasks" / "standing.md"
        relative = Path("tasks/standing.md")
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()

        task.write_bytes(b"\xff")
        with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "UTF-8"):
            EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                self.agents, relative, block
            )

        with task.open("wb") as output:
            output.truncate(EVIDENCE_MODULE.MAX_TASK_BYTES + 1)
        with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "size"):
            EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                self.agents, relative, block
            )

        task.write_text("### Vault Publication Evidence\n", encoding="utf-8")
        one_megabyte = b"x" * (1024 * 1024)
        with mock.patch.object(
            EVIDENCE_MODULE.os, "read", side_effect=[one_megabyte] * 11
        ):
            with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "grew"):
                EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                    self.agents, relative, block
                )

        prefix = "### Vault Publication Evidence\n"
        padding = "x" * (
            EVIDENCE_MODULE.MAX_TASK_BYTES - len(prefix.encode()) - 1
        )
        task.write_text(prefix + padding, encoding="utf-8")
        with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "updated"):
            EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                self.agents, relative, block
            )

    def test_evidence_inputs_are_bounded_before_allocation(self) -> None:
        """Reject oversized control files and Git blobs through streaming gates."""
        oversized = self.workdir / "oversized-evidence-input"
        with oversized.open("wb") as output:
            output.truncate(EVIDENCE_MODULE.MAX_TASK_BYTES + 1)
        with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "allowed size"):
            EVIDENCE_MODULE.read_regular_nofollow(oversized)
        with self.assertRaisesRegex(FINALIZER_MODULE.FinalizationError, "allowed size"):
            FINALIZER_MODULE.stable_regular_bytes(oversized)

        content = b"12345"
        oid = subprocess.run(
            ["git", "-C", str(self.agents), "hash-object", "-w", "--stdin"],
            input=content,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        with mock.patch.object(EVIDENCE_MODULE, "MAX_TASK_BYTES", 4):
            with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "exceeds"):
                EVIDENCE_MODULE.git_bytes(
                    str(self.agents), str(self.agents / ".git"),
                    "cat-file", "blob", oid,
                )
        with mock.patch.object(FINALIZER_MODULE, "MAX_TASK_BYTES", 4):
            with self.assertRaisesRegex(
                FINALIZER_MODULE.FinalizationError, "exceeds"
            ):
                FINALIZER_MODULE.git_object_bytes(
                    str(self.agents), str(self.agents / ".git"), oid
                )

    def test_evidence_atomic_failure_preserves_original_target(self) -> None:
        """Leave the canonical task unchanged after partial or zero writes."""
        task = self.agents / "tasks" / "standing.md"
        relative = Path("tasks/standing.md")
        original = task.read_bytes()
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()
        for side_effect in ([55, OSError("disk full")], [0]):
            with self.subTest(side_effect=side_effect):
                task.write_bytes(original)
                with mock.patch.object(
                    EVIDENCE_MODULE.os, "write", side_effect=side_effect
                ):
                    with self.assertRaises((OSError, EVIDENCE_MODULE.EvidenceError)):
                        EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                            self.agents, relative, block
                        )
                self.assertEqual(task.read_bytes(), original)
                self.assertEqual(list(task.parent.glob("*.evidence-*.tmp")), [])

    def test_evidence_temp_cleanup_retains_a_post_check_replacement(self) -> None:
        """Fail closed without deleting a temporary pathname replacement."""
        task = self.agents / "tasks" / "standing.md"
        relative = Path("tasks/standing.md")
        original = task.read_bytes()
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()

        def replace_temporary_then_fail(_descriptor: int, _content: bytes) -> None:
            temporary = next(task.parent.glob(f".{task.name}.evidence-*.tmp"))
            replacement = task.parent / ".evidence-third-party-replacement"
            replacement.write_bytes(b"third-party temporary\n")
            os.replace(replacement, temporary)
            raise OSError("injected temporary write failure")

        with mock.patch.object(
            EVIDENCE_MODULE,
            "write_all",
            side_effect=replace_temporary_then_fail,
        ), self.assertRaisesRegex(
            EVIDENCE_MODULE.EvidenceError,
            "third-party inode retained",
        ):
            EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                self.agents, relative, block
            )
        retained = list(
            task.parent.glob(f".{task.name}.evidence-retained-*")
        )
        self.assertEqual(len(retained), 1)
        self.assertEqual(
            (retained[0] / "entry").read_bytes(), b"third-party temporary\n"
        )
        self.assertEqual(task.read_bytes(), original)

    def test_evidence_concurrent_mutation_is_not_overwritten(self) -> None:
        """Detect a target edit before replacement and preserve that edit."""
        task = self.agents / "tasks" / "standing.md"
        relative = Path("tasks/standing.md")
        original = task.read_text(encoding="utf-8")
        concurrent = original + "concurrent edit\n"
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()
        real_stat = EVIDENCE_MODULE.os.stat

        def mutate_then_stat(*args, **kwargs):
            task.write_text(concurrent, encoding="utf-8")
            return real_stat(*args, **kwargs)

        with mock.patch.object(
            EVIDENCE_MODULE.os, "stat", side_effect=mutate_then_stat
        ):
            with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "changed"):
                EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                    self.agents, relative, block
                )
        self.assertEqual(task.read_text(encoding="utf-8"), concurrent)

    def test_evidence_detach_race_is_restored_without_overwrite(self) -> None:
        """Detect an edit after final verification and restore that exact occupant."""
        task = self.agents / "tasks" / "standing.md"
        relative = Path("tasks/standing.md")
        concurrent = task.read_text(encoding="utf-8") + "late concurrent edit\n"
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()
        real_rename = EVIDENCE_MODULE.rename_no_replace
        calls = 0

        def mutate_before_detach(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                task.write_text(concurrent, encoding="utf-8")
            real_rename(*args, **kwargs)

        with mock.patch.object(
            EVIDENCE_MODULE,
            "rename_no_replace",
            side_effect=mutate_before_detach,
        ), self.assertRaisesRegex(
            EVIDENCE_MODULE.EvidenceError,
            "raced during original detachment",
        ):
            EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                self.agents, relative, block
            )
        self.assertEqual(task.read_text(encoding="utf-8"), concurrent)

    def test_evidence_candidate_post_move_replacement_is_retained(self) -> None:
        """Verify the installed name, retain a replacement, and restore the task."""
        task = self.agents / "tasks" / "standing.md"
        relative = Path("tasks/standing.md")
        original = task.read_bytes()
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()
        replacement = task.parent / ".post-move-third-party"
        replacement.write_bytes(b"third-party standing replacement\n")
        real_rename = EVIDENCE_MODULE.rename_no_replace
        calls = 0

        def replace_after_candidate_move(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            real_rename(*args, **kwargs)
            if calls == 2:
                os.replace(replacement, task)

        with mock.patch.object(
            EVIDENCE_MODULE,
            "rename_no_replace",
            side_effect=replace_after_candidate_move,
        ), self.assertRaisesRegex(
            EVIDENCE_MODULE.EvidenceError,
            "changed during installation",
        ):
            EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                self.agents, relative, block
            )
        self.assertEqual(task.read_bytes(), original)
        retained = list(task.parent.glob(".evidence-candidate-retained-*"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(
            (retained[0] / "entry").read_bytes(),
            b"third-party standing replacement\n",
        )

    def test_publication_cleanup_paths_contain_no_pathname_unlink(self) -> None:
        """Keep every reviewed publication temporary on a retention transition."""
        for module in (
            ATOMIC_FILE_OPS_MODULE,
            EVIDENCE_MODULE,
            FINALIZER_MODULE,
            COMMITTER_MODULE,
        ):
            with self.subTest(module=module.__name__):
                source = Path(module.__file__).read_text(encoding="utf-8")
                self.assertNotIn("os.unlink(", source)

    def test_owned_temporary_cleanup_retains_a_replacement(self) -> None:
        """Reject replacement of a prepared index at the common cleanup boundary."""
        path = Path(
            ATOMIC_FILE_OPS_MODULE.allocate_private_entry_path(
                self.workdir,
                prefix=".owned-index-work-",
                entry_name="index",
            )
        )
        path.write_bytes(b"owned-index\n")
        content, identity = FINALIZER_MODULE.index_file_contract(path)
        contract = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "identity": identity,
        }
        replacement = path.parent / "replacement"
        replacement.write_bytes(b"third-party-index\n")
        os.replace(replacement, path)
        with self.assertRaisesRegex(
            ATOMIC_FILE_OPS_MODULE.AtomicTransactionError,
            "third-party inode retained",
        ):
            ATOMIC_FILE_OPS_MODULE.retain_path_no_replace(
                path,
                expected=contract,
                label="owned publication index",
                prefix=".owned-index-retained-",
            )
        retained = list(path.parent.glob(".owned-index-retained-*"))
        self.assertEqual(len(retained), 1)
        self.assertEqual((retained[0] / "entry").read_bytes(), b"third-party-index\n")

    def test_failed_push_does_not_report_unpublished_preexisting_commits(self) -> None:
        """Include local-ahead hashes only after remote publication succeeds."""
        reported = {
            "commit_status": "not_started",
            "commit_hashes": [],
            "local_head": "b" * 40,
            "clean": True,
            "publication_mode": "sweep",
            "deferred_cleanup": [],
        }
        pre = {"local_commits": [{"commit": "a" * 40}]}
        failed = PUSH_MODULE.final_vault(reported, "failed", "0" * 40, pre)
        self.assertEqual(failed["commit_hashes"], [])
        self.assertEqual(failed["commit_status"], "not_started")
        not_started = PUSH_MODULE.final_vault(
            reported, "not_started", "0" * 40, pre
        )
        self.assertEqual(not_started["commit_hashes"], [])
        self.assertEqual(not_started["commit_status"], "not_started")
        complete = PUSH_MODULE.final_vault(reported, "complete", "b" * 40, pre)
        self.assertEqual(complete["commit_hashes"], ["a" * 40])
        self.assertEqual(complete["commit_status"], "complete")

    def test_pusher_rejects_completed_commit_status_for_blocked_vault(self) -> None:
        """A blocked Vault cannot claim that its publication commit completed."""
        head = create_empty_base(self.user)
        subprocess.run(["git", "-C", str(self.user), "branch", "-M", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(self.user), "push", "-q", "-u", "origin", "main"],
            check=True,
        )
        pre = CAPTURE_MODULE.capture(str(self.user), include_local_history=True)
        reported = {
            "publication_mode": "blocked",
            "commit_status": "complete",
            "commit_hashes": [],
            "pre_local_head": head,
            "local_head": head,
            "post_dirty_digest": pre["dirty_digest"],
        }
        with self.assertRaisesRegex(PUSH_MODULE.PushError, "blocked Vault changed"):
            PUSH_MODULE.validate_local(
                str(self.user), pre, reported, current_state=pre
            )

    def test_index_only_pusher_uses_recaptured_index_state_without_status(self) -> None:
        """Validate own_only publication without walking the cloud worktree."""
        head = create_empty_base(self.user)
        subprocess.run(["git", "-C", str(self.user), "branch", "-M", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(self.user), "push", "-q", "-u", "origin", "main"],
            check=True,
        )
        pre = CAPTURE_MODULE.capture(
            str(self.user), include_local_history=True, worktree_scope="index_only"
        )
        reported = {
            "publication_mode": "own_only",
            "commit_status": "not_required",
            "commit_hashes": [],
            "pre_local_head": head,
            "local_head": head,
            "pre_dirty_digest": pre["dirty_digest"],
            "post_dirty_digest": pre["dirty_digest"],
            "clean": False,
        }
        with mock.patch.object(
            PUSH_MODULE,
            "dirty_digest",
            side_effect=AssertionError("worktree status must not run"),
        ) as dirty:
            self.assertEqual(
                PUSH_MODULE.validate_local(
                    str(self.user),
                    pre,
                    reported,
                    current_state=pre,
                    artifact_path="reports/news.md",
                ),
                head,
            )
        dirty.assert_not_called()

        drifted = json.loads(json.dumps(pre))
        drifted["index_entries"] = [
            {
                "path": "third-party.md",
                "mode": "100644",
                "git_blob_oid": "f" * 40,
                "stage": 0,
            }
        ]
        with self.assertRaisesRegex(
            PUSH_MODULE.PushError, "non-owned index entry"
        ), mock.patch.object(
            PUSH_MODULE,
            "dirty_digest",
            side_effect=AssertionError("worktree status must not run"),
        ):
            PUSH_MODULE.validate_local(
                str(self.user),
                pre,
                reported,
                current_state=drifted,
                artifact_path="reports/news.md",
            )

    def test_fixed_pusher_validates_and_pushes_exact_heads(self) -> None:
        """Push both validated local main heads outside the Codex process."""
        runtime = {
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(self.user),
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(self.user / ".git"),
            "agents_remote_url": str(self.origins["agents"]),
            "user_remote_url": str(self.origins["user"]),
            "gitleaks_bin": str(self.fake_gitleaks),
            "gitleaks_version": "fixture-gitleaks 8.30.1",
        }
        for repo, key in ((self.agents, "agents"), (self.user, "user")):
            (repo / "initial.md").write_text("initial\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Fixture"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "config",
                    "user.email",
                    "fixture@example.invalid",
                ],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "initial"],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
                check=True,
            )
        runtime_path = self.workdir / "fixed-runtime.json"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        pre_path = self.workdir / "fixed-pre.json"
        with pre_path.open("w", encoding="utf-8") as output:
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(runtime_path)],
                check=True,
                stdout=output,
                text=True,
            )
        pre = json.loads(pre_path.read_text(encoding="utf-8"))
        commits = {}
        empty_digest = hashlib.sha256(b"").hexdigest()
        for key, repo in (("agents_vault", self.agents), ("user_vault", self.user)):
            (repo / "published.md").write_text("published\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "published.md"], check=True
            )
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "publish"],
                check=True,
            )
            head = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()
            commits[key] = {
                "commit_status": "complete",
                "commit_hashes": [head],
                "pre_local_head": pre[key]["local_head"],
                "local_head": head,
                "pre_dirty_digest": pre[key]["dirty_digest"],
                "post_dirty_digest": empty_digest,
                "clean": True,
                "publication_mode": "sweep",
                "deferred_cleanup": [],
            }
        commit_result = {
            "outcome": "ready_to_push",
            "phase": "local_commit",
            "daily_pipeline_status": "complete",
            "summary_path": str(self.user / "published.md"),
            "advisory_path": str(self.agents / "published.md"),
            "notification_result": "none",
            "agents_vault": commits["agents_vault"],
            "user_vault": commits["user_vault"],
            "publication_mode": {
                "agents_vault": "sweep", "user_vault": "sweep"
            },
            "deferred_cleanup": {"agents_vault": [], "user_vault": []},
            "evidence_finalization_commit": None,
            "next_action": None,
        }
        commit_path = self.workdir / "fixed-commit.json"
        final_path = self.workdir / "fixed-final.json"
        context_path = self.workdir / "fixed-context.json"
        review_path = self.workdir / "fixed-review.json"
        plan_path = self.workdir / "fixed-plan.json"
        artifact_hash = hashlib.sha256(b"published\n").hexdigest()
        manifest = lambda root: {
            "repo_root": str(root),
            "task_id": "TSK-AUTH",
            "publication_mode": "sweep",
            "core_review_status": "quality_ok",
            "residual_review_status": "quality_ok",
            "owned_paths": ["published.md"],
            "excluded_paths": [],
            "deferred_cleanup": [],
            "approved_diff_snapshot_sha256": hashlib.sha256(b"").hexdigest(),
            "approved_existing_commits": [],
            "approved_dirty_entries": [],
            "reviewed_artifacts": [
                {
                    "role": "agents_security_advisory"
                    if root == self.agents
                    else "user_it_news_summary",
                    "source_sha256": artifact_hash,
                    "target_path": "published.md",
                }
            ],
            "validation_evidence": {
                "file_guard": "passed",
                "secret_scan": "passed",
                "secret_scan_tool": "gitleaks",
                "secret_scan_tool_version": "fixture-gitleaks 8.30.1",
                "reviewed_snapshot_sha256": hashlib.sha256(b"").hexdigest(),
                "reviewed_history_sha256": pre[
                    "agents_vault" if root == self.agents else "user_vault"
                ]["history_snapshot_sha256"],
            },
            "review_or_validation_status": "quality_ok",
            "commit_required": True,
            "unrelated_dirty_paths": [],
            "commit_groups": [{"message": "publish", "paths": ["published.md"]}],
            "evidence_finalization": None,
        }
        plan_payload = {
            "summary_target": str(self.user / "published.md"),
            "advisory_target": str(self.agents / "published.md"),
        }
        plan_path.write_text(json.dumps(plan_payload), encoding="utf-8")
        context_path.write_text(
            json.dumps(
                {
                    "runtime": runtime,
                    "pre_collection_state": pre,
                    "artifact_plan": plan_payload,
                }
            ),
            encoding="utf-8",
        )
        context_digest = hashlib.sha256(context_path.read_bytes()).hexdigest()
        review_payload = {
            "outcome": "approved",
            "publication_context_sha256": context_digest,
            "agents_vault": manifest(self.agents),
            "user_vault": manifest(self.user),
            "next_action": None,
        }
        commit_path.write_text(json.dumps(commit_result), encoding="utf-8")
        command = [
            str(SCRIPTS / "push-committed-heads.py"),
            str(runtime_path),
            str(pre_path),
            str(commit_path),
            str(final_path),
            "0",
            str(context_path),
            str(review_path),
            str(plan_path),
            "review-digest-placeholder",
        ]
        rejected_review = json.loads(json.dumps(review_payload))
        for key in ("agents_vault", "user_vault"):
            rejected_review[key]["commit_groups"][0]["paths"] = ["approved.md"]
        review_path.write_text(json.dumps(rejected_review), encoding="utf-8")
        command[-1] = hashlib.sha256(review_path.read_bytes()).hexdigest()
        rejected = subprocess.run(command, check=False)
        self.assertEqual(rejected.returncode, 75)
        for key, repo in (("agents_vault", self.agents), ("user_vault", self.user)):
            remote = subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            self.assertEqual(remote, pre[key]["remote_head"])

        wrong_paths = json.loads(json.dumps(commit_result))
        wrong_paths["summary_path"] = str(self.user / "wrong.md")
        commit_path.write_text(json.dumps(wrong_paths), encoding="utf-8")
        review_path.write_text(json.dumps(review_payload), encoding="utf-8")
        command[-1] = hashlib.sha256(review_path.read_bytes()).hexdigest()
        rejected = subprocess.run(command, check=False)
        self.assertEqual(rejected.returncode, 75)
        for key, repo in (("agents_vault", self.agents), ("user_vault", self.user)):
            remote = subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            expected = (
                commit_result["agents_vault"]["local_head"]
                if key == "agents_vault"
                else pre[key]["remote_head"]
            )
            self.assertEqual(remote, expected)

        bad_status = list(command)
        bad_status[5] = "not-a-number"
        rejected = subprocess.run(bad_status, check=False)
        self.assertEqual(rejected.returncode, 75)
        self.assertTrue(final_path.is_file())

        remote_before_rejections = {
            key: subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            for key, repo in (("agents", self.agents), ("user", self.user))
        }
        failed_process = list(command)
        failed_process[5] = "75"
        self.assertEqual(subprocess.run(failed_process, check=False).returncode, 75)
        tampered_runtime = dict(runtime)
        tampered_runtime["user_remote_url"] = str(self.origins["agents"])
        tampered_runtime_path = self.workdir / "tampered-runtime.json"
        tampered_runtime_path.write_text(json.dumps(tampered_runtime), encoding="utf-8")
        tampered_command = list(command)
        tampered_command[1] = str(tampered_runtime_path)
        self.assertEqual(subprocess.run(tampered_command, check=False).returncode, 75)
        for key, repo in (("agents", self.agents), ("user", self.user)):
            observed = subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            self.assertEqual(observed, remote_before_rejections[key])

        commit_path.write_text(json.dumps(commit_result), encoding="utf-8")
        result = subprocess.run(command, check=False)
        self.assertEqual(
            result.returncode,
            0,
            msg=final_path.read_text(encoding="utf-8") if final_path.exists() else "missing result",
        )
        final = json.loads(final_path.read_text(encoding="utf-8"))
        self.assertEqual(final["outcome"], "partial_publication")
        self.assertEqual(
            final["agents_vault"]["local_head"],
            final["agents_vault"]["remote_head"],
        )

    def test_pusher_scans_remote_to_final_and_isolates_remote_races(self) -> None:
        """Scan the full range and contain a remote race to the affected Vault."""
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(PUSH_MODULE, "run_local_command", return_value=completed) as run:
            PUSH_MODULE.scan_commits("/tools/gitleaks", "/vault", "a" * 40, "b" * 40)
        self.assertIn(
            f"{'a' * 40}..{'b' * 40}",
            run.call_args.args[0],
        )
        with mock.patch.object(
            PUSH_MODULE, "remote_head", return_value="a" * 40
        ), mock.patch.object(
            PUSH_MODULE, "push_one", return_value=("complete", "b" * 40)
        ) as push:
            self.assertEqual(
                PUSH_MODULE.push_one_independently(
                    "/vault", "remote", "b" * 40, "a" * 40, "own_only"
                ),
                ("complete", "b" * 40),
            )
            push.assert_called_once()
        with mock.patch.object(
            PUSH_MODULE, "remote_head", return_value="c" * 40
        ), mock.patch.object(PUSH_MODULE, "push_one") as push:
            self.assertEqual(
                PUSH_MODULE.push_one_independently(
                    "/vault", "remote", "b" * 40, "a" * 40, "sweep"
                ),
                ("failed", "c" * 40),
            )
            push.assert_not_called()
            self.assertEqual(
                PUSH_MODULE.push_one_independently(
                    "/vault", "remote", "a" * 40, "a" * 40, "blocked"
                ),
                ("not_started", "c" * 40),
            )
        with mock.patch.object(
            PUSH_MODULE,
            "remote_head",
            side_effect=TRANSPORT_MODULE.TransportError("deadline"),
        ):
            self.assertEqual(
                PUSH_MODULE.push_one_independently(
                    "/agents", "remote", "b" * 40, "a" * 40, "own_only"
                ),
                ("failed", "a" * 40),
            )
        with mock.patch.object(
            PUSH_MODULE, "remote_head", return_value="a" * 40
        ), mock.patch.object(
            PUSH_MODULE, "push_one", return_value=("complete", "b" * 40)
        ):
            self.assertEqual(
                PUSH_MODULE.push_one_independently(
                    "/user", "remote", "b" * 40, "a" * 40, "own_only"
                ),
                ("complete", "b" * 40),
            )

    def test_pusher_outer_fallback_survives_both_remote_deadlines(self) -> None:
        """Always emit structured per-Vault unknown remote observations."""
        root = self.workdir / "pusher-remote-fallback"
        root.mkdir()
        runtime = {
            "agents_vault_root": str(self.agents),
            "agents_git_dir": str(self.agents / ".git"),
            "agents_remote_url": "fixture-agents",
            "user_vault_root": str(self.user),
            "user_git_dir": str(self.user / ".git"),
            "user_remote_url": "fixture-user",
        }
        pre = {
            "agents_vault": {"local_head": "a" * 40, "dirty_digest": "d" * 64},
            "user_vault": {"local_head": "b" * 40, "dirty_digest": "e" * 64},
        }
        plan: dict[str, object] = {}
        context = {"runtime": runtime, "pre_collection_state": pre, "artifact_plan": plan}
        context_path = root / "context.json"
        context_path.write_text(json.dumps(context), encoding="utf-8")
        review = {
            "publication_context_sha256": hashlib.sha256(
                context_path.read_bytes()
            ).hexdigest()
        }
        values = {
            "runtime.json": runtime,
            "pre.json": pre,
            "commit.json": {},
            "review.json": review,
            "plan.json": plan,
        }
        paths: dict[str, Path] = {}
        for name, value in values.items():
            path = root / name
            path.write_text(json.dumps(value), encoding="utf-8")
            paths[name] = path
        output = root / "result.json"
        unchanged = [
            {
                "commit_status": "not_started",
                "commit_hashes": [],
                "pre_local_head": "a" * 40,
                "local_head": "a" * 40,
                "pre_dirty_digest": "d" * 64,
                "post_dirty_digest": "d" * 64,
                "clean": False,
            },
            {
                "commit_status": "not_started",
                "commit_hashes": [],
                "pre_local_head": "b" * 40,
                "local_head": "b" * 40,
                "pre_dirty_digest": "e" * 64,
                "post_dirty_digest": "e" * 64,
                "clean": False,
            },
        ]
        with mock.patch.object(
            PUSH_MODULE, "current_local", side_effect=unchanged
        ), mock.patch.object(
            PUSH_MODULE,
            "remote_head",
            side_effect=TRANSPORT_MODULE.TransportError("deadline"),
        ):
            exit_code = PUSH_MODULE.main(
                [
                    "push-committed-heads.py",
                    str(paths["runtime.json"]),
                    str(paths["pre.json"]),
                    str(paths["commit.json"]),
                    str(output),
                    "75",
                    str(context_path),
                    str(paths["review.json"]),
                    str(paths["plan.json"]),
                    hashlib.sha256(paths["review.json"].read_bytes()).hexdigest(),
                ]
            )
        self.assertEqual(exit_code, 75)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "partial_publication")
        for key in ("agents_vault", "user_vault"):
            self.assertIsNone(result[key]["remote_head"])
            self.assertEqual(result[key]["push_status"], "failed")
        schema = json.loads(
            (SKILL_ROOT / "references" / "automation-result.schema.json").read_text()
        )
        CANONICAL_MODULE.validate(result, schema, schema)

    def test_fixed_non_force_push_is_bounded(self) -> None:
        """Retry one fixed fast-forward non-force refspec at most three times."""
        before = "a" * 40
        local = "b" * 40
        failed = subprocess.CompletedProcess([], 1, "", "rejected")
        with mock.patch.object(
            PUSH_MODULE, "remote_head", return_value=before
        ), mock.patch.object(
            PUSH_MODULE, "require_fast_forward_target"
        ), mock.patch.object(PUSH_MODULE, "git", return_value=failed) as git:
            self.assertEqual(
                PUSH_MODULE.push_one(
                    "/vault", "remote", local, True, before, "/vault/.git"
                ),
                ("failed", before),
            )
            self.assertEqual(git.call_count, 3)
            for call in git.call_args_list:
                self.assertEqual(
                    call.args[1:],
                    (
                        "push",
                        "remote",
                        f"{local}:refs/heads/main",
                    ),
                )
                self.assertFalse(
                    any(str(argument).startswith("--force") for argument in call.args)
                )
                self.assertFalse(call.args[-1].startswith("+"))
        with mock.patch.object(
            PUSH_MODULE, "remote_head", return_value="c" * 40
        ), mock.patch.object(
            PUSH_MODULE, "require_fast_forward_target"
        ), mock.patch.object(PUSH_MODULE, "git") as git:
            self.assertEqual(
                PUSH_MODULE.push_one(
                    "/vault", "remote", local, True, before, "/vault/.git"
                ),
                ("failed", "c" * 40),
            )
            git.assert_not_called()

    def test_fixed_push_rejects_non_descendant_target_before_transport(self) -> None:
        """Never send a non-descendant target to the non-force transport."""
        completed = subprocess.CompletedProcess([], 1, "", "")
        with mock.patch.object(PUSH_MODULE, "git", return_value=completed) as git:
            with self.assertRaisesRegex(PUSH_MODULE.PushError, "not a descendant"):
                PUSH_MODULE.push_one(
                    "/vault", "remote", "b" * 40, True, "a" * 40, "/vault/.git"
                )
        git.assert_called_once_with(
            "/vault",
            "merge-base",
            "--is-ancestor",
            "a" * 40,
            "b" * 40,
            check=False,
            git_dir="/vault/.git",
        )

    def test_fixed_non_force_push_safely_restores_ancestor_rewind_race(self) -> None:
        """A plain fast-forward push may restore reviewed commits after a rewind."""
        repo = self.user
        origin = self.origins["user"]
        ancestor = create_empty_base(repo)
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True
        )
        (repo / "expected.md").write_text("expected\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "expected.md"], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo),
                "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid",
                "commit", "-q", "-m", "expected",
            ],
            check=True,
        )
        expected = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            ["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True
        )
        (repo / "candidate.md").write_text("candidate\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "candidate.md"], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo),
                "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid",
                "commit", "-q", "-m", "candidate",
            ],
            check=True,
        )
        candidate = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            [
                "git", f"--git-dir={origin}", "update-ref",
                "refs/heads/main", ancestor, expected,
            ],
            check=True,
        )
        actual_remote_head = PUSH_MODULE.remote_head
        observations = iter((expected,))

        def stale_once(*args: object, **kwargs: object) -> str:
            try:
                return next(observations)
            except StopIteration:
                return actual_remote_head(*args, **kwargs)

        with mock.patch.object(PUSH_MODULE, "remote_head", side_effect=stale_once):
            status, observed = PUSH_MODULE.push_one(
                str(repo), str(origin), candidate, True, expected, str(repo / ".git")
            )
        self.assertEqual((status, observed), ("complete", candidate))
        self.assertEqual(
            actual_remote_head(str(repo), str(origin), str(repo / ".git")),
            candidate,
        )

    def test_fixed_non_force_push_rejects_divergent_remote_race(self) -> None:
        """Let the server reject a post-check divergent update without rewriting it."""
        repo = self.user
        origin = self.origins["user"]
        create_empty_base(repo)
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        (repo / "expected.md").write_text("expected\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "expected.md"], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo), "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid", "commit", "-q",
                "-m", "expected",
            ],
            check=True,
        )
        expected = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
            check=True,
        )
        racer = self.root / "remote-racer"
        subprocess.run(
            ["git", "clone", "-q", "--branch", "main", str(origin), str(racer)],
            check=True,
        )
        (repo / "candidate.md").write_text("candidate\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "candidate.md"], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo), "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid", "commit", "-q",
                "-m", "candidate",
            ],
            check=True,
        )
        candidate = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        (racer / "racer.md").write_text("racer\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(racer), "add", "racer.md"], check=True)
        subprocess.run(
            [
                "git", "-C", str(racer), "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid", "commit", "-q",
                "-m", "racer",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(racer), "push", "-q", "origin", "main"],
            check=True,
        )
        raced = subprocess.check_output(
            ["git", "-C", str(racer), "rev-parse", "HEAD"], text=True
        ).strip()
        actual_remote_head = PUSH_MODULE.remote_head
        observations = iter((expected,))

        def stale_once(*args: object, **kwargs: object) -> str:
            try:
                return next(observations)
            except StopIteration:
                return actual_remote_head(*args, **kwargs)

        with mock.patch.object(PUSH_MODULE, "remote_head", side_effect=stale_once):
            status, observed = PUSH_MODULE.push_one(
                str(repo), str(origin), candidate, True, expected, str(repo / ".git")
            )
        self.assertEqual((status, observed), ("failed", raced))
        self.assertEqual(
            actual_remote_head(str(repo), str(origin), str(repo / ".git")), raced
        )

    def test_review_rejects_forbidden_path_in_local_only_history(self) -> None:
        """Apply the .obsidian guard to commits that already exist locally."""
        state = {
            "repo_root": str(self.agents),
            "branch": "main",
            "upstream": "origin/main",
            "history_relation": "local_ahead",
            "operation_in_progress": False,
            "local_commits": [
                {
                    "commit": "a" * 40,
                    "parents": ["b" * 40],
                    "tree": "c" * 40,
                    "message": "unsafe",
                    "changed_paths": [".obsidian/workspace.json"],
                }
            ],
            "dirty_paths": [],
        }
        with self.assertRaisesRegex(REVIEW_MODULE.ReviewError, "forbidden .obsidian"):
            REVIEW_MODULE.validate_manifest(
                {
                    "repo_root": str(self.agents),
                    "task_id": "TSK-AUTH",
                    "publication_mode": "sweep",
                    "approved_existing_commits": [
                        {**state["local_commits"][0], "patch_sha256": "d" * 64}
                    ],
                },
                state,
                str(self.agents),
                "TSK-AUTH",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "e" * 64,
                    "target_path": str(self.agents / "advisory.md"),
                },
                None,
                "fixture-gitleaks 8.30.1",
                {"required_mode": "sweep"},
                materialized_commits=[{"patch_sha256": "d" * 64}],
            )

    def test_partial_evidence_keeps_initial_and_finalization_commits(self) -> None:
        """Do not discard already-published local-ahead hashes on partial recovery."""
        initial_hash = "1" * 40
        finalization_hash = "2" * 40
        initial = {
            "agents_vault": {
                "commit_status": "complete",
                "commit_hashes": [initial_hash],
                "push_status": "complete",
                "local_head": initial_hash,
                "remote_head": initial_hash,
                "clean": True,
                "publication_mode": "own_only",
                "deferred_cleanup": [],
            },
            "user_vault": {
                "commit_status": "complete",
                "commit_hashes": ["3" * 40],
                "push_status": "complete",
                "local_head": "3" * 40,
                "remote_head": "3" * 40,
                "clean": True,
                "publication_mode": "own_only",
                "deferred_cleanup": [],
            },
        }
        git_results = [
            subprocess.CompletedProcess([], 0, finalization_hash + "\n", ""),
            subprocess.CompletedProcess([], 0, finalization_hash + "\n", ""),
            subprocess.CompletedProcess([], 0, "3" * 40 + "\n", ""),
        ]
        with mock.patch.object(FINALIZER_MODULE, "git", side_effect=git_results), \
             mock.patch.object(FINALIZER_MODULE, "dirty_status", return_value=(True, "")), \
             mock.patch.object(
                 FINALIZER_MODULE,
                 "remote_head",
                 side_effect=[initial_hash, "3" * 40],
             ):
            result = FINALIZER_MODULE.partial_result(
                {
                    "agents_vault_root": str(self.agents),
                    "agents_remote_url": "fixture",
                    "agents_git_dir": str(self.agents / ".git"),
                    "user_vault_root": str(self.user),
                    "user_remote_url": "fixture-user",
                    "user_git_dir": str(self.user / ".git"),
                },
                {},
                initial,
                "fixture failure",
            )
        self.assertEqual(
            result["agents_vault"]["commit_hashes"],
            [initial_hash, finalization_hash],
        )

    def test_partial_evidence_uses_unknown_remote_without_losing_json(self) -> None:
        """Represent per-Vault transport failures as null/failed observations."""
        initial = {
            "agents_vault": {
                "commit_status": "complete",
                "commit_hashes": ["1" * 40],
                "push_status": "complete",
                "local_head": "1" * 40,
                "remote_head": "1" * 40,
                "clean": True,
                "publication_mode": "own_only",
                "deferred_cleanup": [],
            },
            "user_vault": {
                "commit_status": "complete",
                "commit_hashes": ["2" * 40],
                "push_status": "complete",
                "local_head": "2" * 40,
                "remote_head": "2" * 40,
                "clean": True,
                "publication_mode": "own_only",
                "deferred_cleanup": [],
            },
        }
        git_results = [
            subprocess.CompletedProcess([], 0, "1" * 40 + "\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "2" * 40 + "\n", ""),
        ]
        with mock.patch.object(FINALIZER_MODULE, "git", side_effect=git_results), \
             mock.patch.object(FINALIZER_MODULE, "dirty_status", return_value=(True, "")), \
             mock.patch.object(
                 FINALIZER_MODULE,
                 "remote_head",
                 side_effect=TRANSPORT_MODULE.TransportError("deadline"),
             ):
            result = FINALIZER_MODULE.partial_result(
                {
                    "agents_vault_root": str(self.agents),
                    "agents_remote_url": "fixture-agents",
                    "agents_git_dir": str(self.agents / ".git"),
                    "user_vault_root": str(self.user),
                    "user_remote_url": "fixture-user",
                    "user_git_dir": str(self.user / ".git"),
                },
                {},
                initial,
                "fixture transport failure",
            )
        for key in ("agents_vault", "user_vault"):
            self.assertIsNone(result[key]["remote_head"])
            self.assertEqual(result[key]["push_status"], "failed")

    def test_review_rejects_forbidden_obsidian_scope(self) -> None:
        """Reject forbidden Vault metadata before any write-capable phase."""
        state = {
            "repo_root": str(self.agents),
            "branch": "main",
            "upstream": "origin/main",
            "local_head": "a" * 40,
            "remote_head": "a" * 40,
            "operation_in_progress": False,
            "dirty_paths": [".obsidian/workspace.json"],
            "dirty_entries": [
                {
                    "path": ".obsidian/workspace.json",
                    "git_blob_oid": "b" * 40,
                    "mode": "100644",
                }
            ],
            "diff_snapshot_sha256": "c" * 64,
        }
        with self.assertRaises(REVIEW_MODULE.ReviewError):
            REVIEW_MODULE.validate_manifest(
                {"repo_root": str(self.agents), "task_id": "TSK-AUTH", "publication_mode": "sweep"},
                state,
                str(self.agents),
                "TSK-AUTH",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "d" * 64,
                    "target_path": str(self.agents / "advisory.md"),
                },
                str(self.agents / "tasks" / "standing.md"),
                "fixture-gitleaks 8.30.1",
                {"required_mode": "sweep"},
            )

    def test_review_rejects_standing_task_as_manifest_identity(self) -> None:
        """Keep recurring evidence ownership distinct from authorization."""
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "Task Change Manifest identity mismatch"
        ):
            REVIEW_MODULE.validate_manifest(
                {"repo_root": str(self.agents), "task_id": "TSK-STANDING"},
                {},
                str(self.agents),
                "TSK-AUTH",
                {},
                None,
                "fixture-gitleaks 8.30.1",
                {"required_mode": "sweep"},
            )

    def test_review_prompt_selects_authorization_task_for_both_manifests(self) -> None:
        """Expose the validator's identity contract to the read-only reviewer."""
        prompt = (SKILL_ROOT / "assets" / "daily-it-news.review.prompt.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("publication_context.authorization_task_id", prompt)
        self.assertIn("both manifests", prompt)
        self.assertIn("must not replace the authorization identity", prompt)

    def test_review_prompt_distinguishes_diff_and_mode_hint_digests(self) -> None:
        """Tell the reviewer which sealed digest belongs in typed evidence."""
        prompt = (SKILL_ROOT / "assets" / "daily-it-news.review.prompt.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "pre_collection_state.<vault>.diff_snapshot_sha256", prompt
        )
        self.assertIn("review_state_sha256", prompt)
        self.assertIn("never copy it", prompt)
        self.assertIn("never changes the reviewer-owned `file_guard`", prompt)

    def test_review_canonicalizes_both_containment_paths(self) -> None:
        """Accept a target expressed through a symlinked Vault path."""
        real_root = self.root / "real-vault"
        real_root.mkdir()
        alias = self.root / "vault-alias"
        alias.symlink_to(real_root, target_is_directory=True)
        self.assertEqual(
            REVIEW_MODULE.relative_target(
                str(real_root), str(alias / "reports" / "artifact.md")
            ),
            "reports/artifact.md",
        )

    def test_staged_evidence_digest_rebinds_reviewed_bytes(self) -> None:
        """Detect a race that substitutes evidence bytes during staging."""
        repo = self.agents
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        target = repo / "evidence.md"
        target.write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "evidence.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True
        )
        target.write_text("reviewed\n", encoding="utf-8")
        reviewed = FINALIZER_MODULE.diff_digest(str(repo), "evidence.md")
        subprocess.run(["git", "-C", str(repo), "add", "evidence.md"], check=True)
        self.assertEqual(
            FINALIZER_MODULE.cached_diff_digest(str(repo), "evidence.md"),
            reviewed,
        )
        subprocess.run(
            ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", "evidence.md"],
            check=True,
        )
        target.write_text("substituted\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "evidence.md"], check=True)
        self.assertNotEqual(
            FINALIZER_MODULE.cached_diff_digest(str(repo), "evidence.md"),
            reviewed,
        )

    def test_scope_validator_rejects_group_split_and_symlink_artifact(self) -> None:
        """Reject split meaning units and non-regular committed artifacts."""
        repo = self.agents
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        (repo / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True
        )
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        commits = []
        for filename in ("a.md", "b.md"):
            (repo / filename).write_text(filename + "\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "approved group"],
                check=True,
            )
            commits.append(
                subprocess.check_output(
                    ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
                ).strip()
            )
        manifest = {
            "commit_groups": [
                {"message": "approved group", "paths": ["a.md", "b.md"]}
            ],
            "reviewed_artifacts": [],
            "approved_existing_commits": [],
            "approved_dirty_entries": [],
        }
        with self.assertRaises(PUSH_MODULE.PushError):
            PUSH_MODULE.validate_scope(
                str(repo),
                {"remote_head": before, "local_head": before, "local_commits": []},
                {"local_head": commits[-1], "commit_hashes": commits},
                manifest,
            )
        second_before = commits[-1]
        (repo / "artifact.md").symlink_to("outside-target")
        subprocess.run(["git", "-C", str(repo), "add", "artifact.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "artifact"],
            check=True,
        )
        symlink_commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        symlink_manifest = {
            "commit_groups": [
                {"message": "artifact", "paths": ["artifact.md"]}
            ],
            "reviewed_artifacts": [
                {
                    "target_path": "artifact.md",
                    "source_sha256": hashlib.sha256(b"outside-target").hexdigest(),
                }
            ],
            "approved_existing_commits": [],
            "approved_dirty_entries": [],
        }
        with self.assertRaises(PUSH_MODULE.PushError):
            PUSH_MODULE.validate_scope(
                str(repo),
                {
                    "remote_head": second_before,
                    "local_head": second_before,
                    "local_commits": [],
                },
                {
                    "local_head": symlink_commit,
                    "commit_hashes": [symlink_commit],
                },
                symlink_manifest,
            )
        third_before = symlink_commit
        dirty_path = repo / "approved-dirty.md"
        dirty_path.write_text("reviewed bytes\n", encoding="utf-8")
        approved_oid = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo),
                "hash-object",
                "--path=approved-dirty.md",
                "--",
                "approved-dirty.md",
            ],
            text=True,
        ).strip()
        dirty_path.write_text("substituted bytes\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "approved-dirty.md"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "dirty"],
            check=True,
        )
        substituted_commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        dirty_manifest = {
            "commit_groups": [
                {"message": "dirty", "paths": ["approved-dirty.md"]}
            ],
            "reviewed_artifacts": [],
            "approved_existing_commits": [],
            "approved_dirty_entries": [
                {
                    "path": "approved-dirty.md",
                    "git_blob_oid": approved_oid,
                    "mode": "100644",
                }
            ],
        }
        with self.assertRaises(PUSH_MODULE.PushError):
            PUSH_MODULE.validate_scope(
                str(repo),
                {
                    "remote_head": third_before,
                    "local_head": third_before,
                    "local_commits": [],
                },
                {
                    "local_head": substituted_commit,
                    "commit_hashes": [substituted_commit],
                },
                dirty_manifest,
            )

    def test_review_context_bounds_large_residual_snapshot(self) -> None:
        """Large residual state must not exceed the model request boundary."""
        def state(dirty_count: int, include_history: bool = False) -> dict[str, object]:
            paths = [f"01-Projects/task-{index:05d}.md" for index in range(dirty_count)]
            entries = [
                {
                    "path": path,
                    "git_blob_oid": f"{index:040x}",
                    "mode": "100644",
                }
                for index, path in enumerate(paths)
            ]
            metadata = [
                {
                    "path": path,
                    "exists": True,
                    "size": index,
                    "mtime_ns": index,
                    "st_mode": 33188,
                }
                for index, path in enumerate(paths)
            ]
            return {
                "capture_status": "available",
                "capture_reason": None,
                "repo_root": str(self.agents),
                "branch": "main",
                "upstream": "origin/main",
                "local_head": "a" * 40,
                "remote_head": "a" * 40,
                "history_relation": "local_ahead" if include_history else "equal",
                "local_commits": (
                    [
                        {
                            "commit": "b" * 40,
                            "parents": ["a" * 40],
                            "tree": "c" * 40,
                            "message": "fixture history",
                            "changed_paths": paths,
                        }
                    ]
                    if include_history
                    else []
                ),
                "history_capture_status": "available",
                "history_capture_reason": None,
                "history_snapshot_sha256": "d" * 64,
                "operation_in_progress": False,
                "git_control_sha256": "e" * 64,
                "dirty_lines": [f" M {path}" for path in paths],
                "dirty_paths": paths,
                "dirty_entries": entries,
                "dirty_metadata": metadata,
                "staged_paths": [],
                "index_entries": [
                    {"path": f"tracked-{index:05d}.md", "mode": "100644", "git_blob_oid": "f" * 40, "stage": 0}
                    for index in range(dirty_count * 2)
                ],
                "index_sha256": "1" * 64,
                "index_identity": [1, 2, 3, 4, 5, 6],
                "dirty_worktree_sha256": "2" * 64,
                "dirty_digest": "3" * 64,
                "diff_snapshot_sha256": "4" * 64,
            }

        agents_state = state(2_000)
        user_state = state(2)
        full_context = {
            "pre_collection_state": {
                "agents_vault": agents_state,
                "user_vault": user_state,
            },
            "artifact_plan": {"summary_target": "/tmp/summary.md", "advisory_target": "/tmp/advisory.md"},
            "publication_manifest": {
                "artifact_manifest": {
                    "summary": {"role": "user_it_news_summary", "sha256": "5" * 64},
                    "advisory": {"role": "agents_security_advisory", "sha256": "6" * 64},
                },
                "pre_collection_state": {
                    "agents_vault": agents_state,
                    "user_vault": user_state,
                },
            },
            "carried_commit_result": None,
        }
        envelope = {
            "publication_context_file": str(self.workdir / "publication-context.json"),
            "publication_context_sha256": "7" * 64,
            "publication_context_projection": "review_bounded_v2",
            "publication_context": full_context,
            "artifact_plan": full_context["artifact_plan"],
            "review_schema": "publication-review-result.schema.json",
        }
        envelope_path = self.workdir / "review-envelope.json"
        prompt_path = self.workdir / "review.prompt.md"
        context_path = self.workdir / "review-context.json"
        request_path = self.workdir / "review-request.txt"
        metrics_path = self.workdir / "review-metrics.json"
        envelope_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        prompt_path.write_text("Review the bounded context.", encoding="utf-8")
        original_digest = hashlib.sha256(envelope_path.read_bytes()).hexdigest()

        REVIEW_CONTEXT_MODULE.prepare(
            envelope_path, prompt_path, context_path, request_path, metrics_path
        )
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        projected = json.loads(context_path.read_text(encoding="utf-8"))
        request = request_path.read_text(encoding="utf-8")

        def assert_no_key(value: object, key: str) -> None:
            if isinstance(value, dict):
                self.assertNotIn(key, value)
                for child in value.values():
                    assert_no_key(child, key)
            elif isinstance(value, list):
                for child in value:
                    assert_no_key(child, key)

        assert_no_key(projected, "index_entries")
        projected_agents = projected["publication_context"]["pre_collection_state"]["agents_vault"]
        self.assertTrue(projected_agents["dirty_paths"]["omitted"])
        self.assertEqual(projected_agents["dirty_paths"]["count"], 2_000)
        self.assertIn("agents_vault", metrics["residual_review_budget_vaults"])
        self.assertEqual(metrics["mode_floor"]["agents_vault"], "own_only")
        self.assertEqual(metrics["mode_floor"]["user_vault"], "sweep")
        self.assertEqual(metrics["status"], "ready")
        self.assertLessEqual(metrics["request_chars"], REVIEW_CONTEXT_MODULE.MAX_REQUEST_CHARS)
        self.assertLessEqual(metrics["request_bytes"], REVIEW_CONTEXT_MODULE.MAX_REQUEST_BYTES)
        self.assertEqual(original_digest, hashlib.sha256(envelope_path.read_bytes()).hexdigest())
        self.assertEqual(request.split("Runtime context JSON:\n", 1)[1].strip(), context_path.read_text(encoding="utf-8").strip())

    def test_review_context_keeps_small_residuals_exact(self) -> None:
        """Small residual arrays stay exact while the tracked index is omitted."""
        state = {
            "dirty_paths": ["tasks/standing.md"],
            "dirty_entries": [{"path": "tasks/standing.md", "git_blob_oid": "a" * 40, "mode": "100644"}],
            "dirty_metadata": [{"path": "tasks/standing.md", "exists": True, "size": 8, "mtime_ns": 1, "st_mode": 33188}],
            "dirty_lines": [" M tasks/standing.md"],
            "staged_paths": [],
            "local_commits": [],
            "index_entries": [{"path": "tracked.md", "mode": "100644", "git_blob_oid": "b" * 40, "stage": 0}],
            "index_sha256": "c" * 64,
        }
        envelope = {
            "publication_context_sha256": "d" * 64,
            "publication_context_projection": "review_bounded_v2",
            "publication_context": {
                "pre_collection_state": {"agents_vault": state, "user_vault": state},
                "publication_manifest": {"pre_collection_state": {"agents_vault": state, "user_vault": state}},
            },
        }
        envelope_path = self.workdir / "small-envelope.json"
        prompt_path = self.workdir / "small.prompt.md"
        context_path = self.workdir / "small-context.json"
        request_path = self.workdir / "small-request.txt"
        metrics_path = self.workdir / "small-metrics.json"
        envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
        prompt_path.write_text("Review.", encoding="utf-8")
        REVIEW_CONTEXT_MODULE.prepare(
            envelope_path, prompt_path, context_path, request_path, metrics_path
        )
        projected = json.loads(context_path.read_text(encoding="utf-8"))
        projected_state = projected["publication_context"]["pre_collection_state"]["agents_vault"]
        self.assertEqual(projected_state["dirty_paths"], ["tasks/standing.md"])
        self.assertNotIn("index_entries", projected_state)
        self.assertEqual(
            projected["publication_context"]["publication_manifest"]["pre_collection_state"]["$ref"],
            "publication_context.pre_collection_state",
        )
        self.assertEqual(json.loads(metrics_path.read_text())["projection_mode"], "inline_residuals_v1")

    def test_review_context_marks_omitted_local_history_blocked(self) -> None:
        """A local-ahead history budget must force blocked mode for that Vault."""
        state = {
            "history_relation": "local_ahead",
            "local_commits": [
                {
                    "commit": f"{index:040x}",
                    "parents": ["a" * 40],
                    "tree": "b" * 40,
                    "message": f"fixture commit {index}",
                    "changed_paths": [f"path-{index:04d}.md"],
                }
                for index in range(200)
            ],
            "index_entries": [],
        }
        envelope = {
            "publication_context_sha256": "e" * 64,
            "publication_context_projection": "review_bounded_v2",
            "publication_context": {
                "pre_collection_state": {
                    "agents_vault": state,
                    "user_vault": {"history_relation": "equal", "local_commits": []},
                }
            },
        }
        envelope_path = self.workdir / "history-envelope.json"
        prompt_path = self.workdir / "history.prompt.md"
        context_path = self.workdir / "history-context.json"
        request_path = self.workdir / "history-request.txt"
        metrics_path = self.workdir / "history-metrics.json"
        envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
        prompt_path.write_text("Review.", encoding="utf-8")
        REVIEW_CONTEXT_MODULE.prepare(
            envelope_path, prompt_path, context_path, request_path, metrics_path
        )
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        self.assertIn("agents_vault", metrics["history_review_budget_vaults"])
        self.assertEqual(metrics["mode_floor"]["agents_vault"], "blocked")
        projected_history = json.loads(context_path.read_text(encoding="utf-8"))["publication_context"][
            "pre_collection_state"
        ]["agents_vault"]["local_commits"]
        self.assertTrue(projected_history["omitted"])
        self.assertEqual(projected_history["count"], 200)

    def test_review_context_marks_nested_history_paths_blocked(self) -> None:
        """Nested changed_paths omission must never downgrade history to own_only."""
        state = {
            "history_relation": "local_ahead",
            "local_commits": [{
                "commit": "a" * 40,
                "parents": ["b" * 40],
                "tree": "c" * 40,
                "message": "fixture commit",
                "changed_paths": [f"path-{index:04d}.md" for index in range(200)],
            }],
            "index_entries": [],
        }
        envelope = {
            "publication_context_sha256": "d" * 64,
            "publication_context_projection": "review_bounded_v2",
            "publication_context": {"pre_collection_state": {
                "agents_vault": state,
                "user_vault": {"history_relation": "equal", "local_commits": []},
            }},
        }
        paths = {
            "envelope": self.workdir / "nested-history-envelope.json",
            "prompt": self.workdir / "nested-history.prompt.md",
            "context": self.workdir / "nested-history-context.json",
            "request": self.workdir / "nested-history-request.txt",
            "metrics": self.workdir / "nested-history-metrics.json",
        }
        paths["envelope"].write_text(json.dumps(envelope), encoding="utf-8")
        paths["prompt"].write_text("Review.", encoding="utf-8")
        REVIEW_CONTEXT_MODULE.prepare(
            paths["envelope"], paths["prompt"], paths["context"],
            paths["request"], paths["metrics"],
        )
        metrics = json.loads(paths["metrics"].read_text(encoding="utf-8"))
        projected = json.loads(paths["context"].read_text(encoding="utf-8"))
        projected_paths = projected["publication_context"]["pre_collection_state"][
            "agents_vault"
        ]["local_commits"][0]["changed_paths"]
        self.assertTrue(projected_paths["omitted"])
        self.assertEqual(metrics["mode_floor"]["agents_vault"], "blocked")
        self.assertIn("agents_vault", metrics["history_review_budget_vaults"])

    def test_review_context_marks_long_history_message_blocked(self) -> None:
        """A clipped commit message is sealed history, not a schema-shaped dict."""
        state = {
            "history_relation": "local_ahead",
            "local_commits": [{
                "commit": "a" * 40,
                "parents": ["b" * 40],
                "tree": "c" * 40,
                "message": "x" * (REVIEW_CONTEXT_MODULE.MAX_COMMIT_MESSAGE_CHARS + 1),
                "changed_paths": ["path.md"],
            }],
            "index_entries": [],
        }
        envelope = {
            "publication_context_sha256": "e" * 64,
            "publication_context_projection": "review_bounded_v2",
            "publication_context": {"pre_collection_state": {
                "agents_vault": state,
                "user_vault": {"history_relation": "equal", "local_commits": []},
            }},
        }
        envelope_path = self.workdir / "long-message-envelope.json"
        prompt_path = self.workdir / "long-message.prompt.md"
        context_path = self.workdir / "long-message-context.json"
        request_path = self.workdir / "long-message-request.txt"
        metrics_path = self.workdir / "long-message-metrics.json"
        envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
        prompt_path.write_text("Review.", encoding="utf-8")
        REVIEW_CONTEXT_MODULE.prepare(
            envelope_path, prompt_path, context_path, request_path, metrics_path
        )
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        projected_commit = json.loads(context_path.read_text(encoding="utf-8"))["publication_context"][
            "pre_collection_state"
        ]["agents_vault"]["local_commits"][0]
        self.assertIsInstance(projected_commit["message"], dict)
        self.assertEqual(metrics["mode_floor"]["agents_vault"], "blocked")
        self.assertIn("agents_vault", metrics["history_review_budget_vaults"])

    def test_review_input_mode_floor_is_enforced_by_validator(self) -> None:
        """A bounded residual projection cannot be approved as an unsafe sweep."""
        context_path = self.workdir / "floor-context.json"
        context_path.write_text(json.dumps({"context": "fixture"}), encoding="utf-8")
        context_digest = hashlib.sha256(context_path.read_bytes()).hexdigest()
        metrics = {
            "version": 1,
            "status": "ready",
            "publication_context_projection": "review_bounded_v2",
            "publication_context_sha256": context_digest,
            "request_chars": 100,
            "request_bytes": 100,
            "residual_review_budget_vaults": ["agents_vault"],
            "history_review_budget_vaults": [],
            "mode_floor": {"agents_vault": "own_only", "user_vault": "sweep"},
            "omitted_fields": [
                "publication_context.pre_collection_state.agents_vault.dirty_paths"
            ],
        }
        metrics_path = self.workdir / "floor-metrics.json"
        metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
        metrics_digest = hashlib.sha256(metrics_path.read_bytes()).hexdigest()
        self.assertEqual(
            REVIEW_MODULE.validate_review_input_contract(
                metrics_path, metrics_digest, context_path
            ),
            {"agents_vault": "own_only", "user_vault": "sweep"},
        )

        state = {
            "repo_root": str(self.agents),
            "history_relation": "equal",
            "local_commits": [],
            "history_capture_status": "available",
            "dirty_paths": [],
            "dirty_entries": [],
            "diff_snapshot_sha256": "a" * 64,
            "history_snapshot_sha256": "b" * 64,
        }
        manifest = {
            "repo_root": str(self.agents),
            "task_id": "TSK-FLOOR",
            "publication_mode": "sweep",
            "approved_diff_snapshot_sha256": state["diff_snapshot_sha256"],
            "approved_existing_commits": [],
            "reviewed_artifacts": [
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "c" * 64,
                    "target_path": "artifact.md",
                }
            ],
            "validation_evidence": {
                "file_guard": "passed",
                "secret_scan": "passed",
                "secret_scan_tool": "gitleaks",
                "secret_scan_tool_version": "fixture",
                "reviewed_snapshot_sha256": state["diff_snapshot_sha256"],
                "reviewed_history_sha256": state["history_snapshot_sha256"],
            },
            "core_review_status": "quality_ok",
            "review_or_validation_status": "quality_ok",
            "residual_review_status": "quality_ok",
            "owned_paths": ["artifact.md"],
            "excluded_paths": [],
            "unrelated_dirty_paths": [],
            "deferred_cleanup": [],
            "approved_dirty_entries": [],
            "commit_groups": [{"message": "publish", "paths": ["artifact.md"]}],
            "commit_required": True,
            "evidence_finalization": None,
        }
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError,
            "review mode weakens the deterministic mode hint",
        ):
            REVIEW_MODULE.validate_manifest(
                manifest,
                state,
                str(self.agents),
                "TSK-FLOOR",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "c" * 64,
                    "target_path": str(self.agents / "artifact.md"),
                },
                None,
                "fixture",
                {"required_mode": "sweep"},
                [],
                [],
                False,
                "own_only",
            )

    def test_dedicated_runner_completes_separated_publication(self) -> None:
        """Complete collection, two reviews, local commits, and fixed pushes."""
        runtime = self.root / "runtime"
        runtime.mkdir()
        for source in (
            SKILL_ROOT / "assets" / "run-daily-it-news-vulnerability-check.sh",
            SKILL_ROOT / "assets" / "daily-it-news.collect.prompt.md",
            SKILL_ROOT / "assets" / "daily-it-news.review.prompt.md",
            SKILL_ROOT / "assets" / "daily-it-news.publish.prompt.md",
            SKILL_ROOT / "assets" / "daily-it-news.evidence-review.prompt.md",
            SKILL_ROOT / "references" / "collection-result.schema.json",
            SKILL_ROOT / "references" / "publication-review-result.schema.json",
            SKILL_ROOT / "references" / "publication-commit-result.schema.json",
            SKILL_ROOT / "references" / "evidence-review-result.schema.json",
            SKILL_ROOT / "references" / "automation-result.schema.json",
            SCRIPTS / "resolve-runtime-context.py",
            SCRIPTS / "fetch-vault-main.py",
            SCRIPTS / "capture-vault-state.py",
            SCRIPTS / "determine-publication-modes.py",
            SCRIPTS / "validate-collection-result.py",
            SCRIPTS / "install-verified-artifacts.py",
            SCRIPTS / "commit-reviewed-publication.py",
            SCRIPTS / "validate-publication-review.py",
            SCRIPTS / "push-committed-heads.py",
            SCRIPTS / "send-it-news-discord-notification.py",
            SCRIPTS / "prepare-publication-evidence.py",
            SCRIPTS / "commit-push-publication-evidence.py",
            SCRIPTS / "evidence_hunk.py",
            SCRIPTS / "git_diff_digest.py",
            SCRIPTS / "isolated_git_transport.py",
            SCRIPTS / "atomic_file_ops.py",
            SCRIPTS / "trusted_gitleaks.py",
            SCRIPTS / "gitleaks-default.toml",
            SCRIPTS / "prepare-codex-output-schema.py",
            SCRIPTS / "validate-canonical-result.py",
            SCRIPTS / "stage-standing-task.py",
            SCRIPTS / "stage-dirty-review-inputs.py",
            SCRIPTS / "prepare-publication-review-context.py",
            SCRIPTS / "run-pinned-review.py",
            SCRIPTS / "verify-runtime-release.py",
            SCRIPTS / "interpret-automation-result.sh",
            REPO_ROOT / "summarize-it-news" / "scripts" / "collect-public-sources.py",
            SOURCE_CATALOG,
        ):
            shutil.copy2(source, runtime / source.name)

        runtime_verifier = runtime / "verify-runtime-release.py"
        runtime_verifier.chmod(0o755)

        notification_sender = runtime / "send-it-news-discord-notification.py"
        notification_sender.write_text(
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
initial=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if os.environ.get("FAKE_NOTIFICATION_NO_OUTPUT") == "1":
    raise SystemExit(75)
summary="discord:delivered;summary_commit="+initial["user_vault"]["remote_head"]+";receipt_sha256="+("d"*64)+";message_id=2234567890123456789"
receipt={"schema_version":1,"status":"delivered","notification_result":summary}
Path(sys.argv[4]).write_text(json.dumps(receipt),encoding="utf-8")
initial["notification_result"]=summary
Path(sys.argv[5]).write_text(json.dumps(initial),encoding="utf-8")
""",
            encoding="utf-8",
        )
        notification_sender.chmod(0o755)

        installer = runtime / "install-verified-artifacts.py"
        real_installer = runtime / "install-verified-artifacts.real.py"
        installer.rename(real_installer)
        installer.write_text(
            """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
real=Path(__file__).with_name("install-verified-artifacts.real.py")
marker=Path(__file__).with_name("artifact-target-conflict-once.marker")
if os.environ.get("FAKE_INSTALL_TARGET_CONFLICT_ALWAYS") == "user" and len(sys.argv) == 5 and sys.argv[4] == "user_it_news_summary":
    plan=json.loads(Path(sys.argv[3]).read_text())
    target=Path(plan["summary_target"])
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text("persistent third-party target must survive\\n")
completed=subprocess.run([str(real),*sys.argv[1:]],check=False,capture_output=True,text=True)
if completed.returncode == 0 and len(sys.argv) > 1 and sys.argv[1] == "--plan" and os.environ.get("FAKE_PLAN_TARGET_CONFLICT_ONCE") == "1" and not marker.exists():
    plan=json.loads(completed.stdout)
    target=Path(plan["summary_target"])
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text("concurrent target must survive\\n")
    marker.write_text(str(target)+"\\n")
sys.stdout.write(completed.stdout)
sys.stderr.write(completed.stderr)
raise SystemExit(completed.returncode)
""",
            encoding="utf-8",
        )
        installer.chmod(0o755)

        (runtime / "collect-public-sources.py").write_text(
            """#!/usr/bin/env python3
import hashlib, json, os, sys
from datetime import date,timedelta
from pathlib import Path
if sys.argv[1] == "--verify-resolutions":
    Path(__file__).with_name("resolution-verifier-invoked.marker").write_text("invoked\\n")
    request=json.loads(Path(sys.argv[2]).read_text())
    Path(sys.argv[3]).write_text(json.dumps({"version":1,"resolutions":request["resolutions"],"date_evidence":[]}))
    raise SystemExit(0)
catalog_path=Path(sys.argv[1]); output=Path(sys.argv[2]); output.mkdir()
catalog=json.loads(catalog_path.read_text())
run_date=date.fromisoformat(sys.argv[3][:10]); window_start=run_date-timedelta(days=6)
sources=[]
for index,source in enumerate(catalog["sources"]):
    url=source["feed_url"] or source["page_url"]
    method="rss" if source["feed_url"] else "public_page"
    if os.environ.get("FAKE_COLLECTION_CONSTRAINT") == "1" and index == len(catalog["sources"])-1:
        attempts=[]
        for attempt_method,attempt_url in (("rss",source["feed_url"]),("public_page",source["page_url"])):
            if not attempt_url:
                continue
            attempts.append({"method":attempt_method,"url":attempt_url,"status":"access_constraint","reason":"robots_disallowed","requested_url":attempt_url,"final_url":attempt_url,"constraint":"robots","http_status":None,"robots_url":f"https://{attempt_url.split('/')[2]}/robots.txt","robots_sha256":"a"*64})
        sources.append({"name":source["name"],"tier":source["tier"],"status":"access_constraint","method":method,"requested_url":url,"final_url":url,"constraint":"robots","http_status":None,"robots_url":f"https://{url.split('/')[2]}/robots.txt","robots_sha256":"a"*64,"attempts":attempts})
        continue
    extract_file=f"source-{index}.extract.json"
    (output/extract_file).write_text(json.dumps({"format":"feed" if method=="rss" else "html_links","entries":[{"url":f"{url}?fixture={index}","published":"2020-01-01"}]}))
    sources.append({"name":source["name"],"tier":source["tier"],"status":"fetched","method":method,"final_url":url,"extract_file":extract_file,"extracted_entry_count":1,"jst_window_start":window_start.isoformat(),"jst_window_end":run_date.isoformat(),"jst_window_item_count":0,"attempts":[]})
manifest={"catalog_sha256":hashlib.sha256(catalog_path.read_bytes()).hexdigest(),"sources":sources}
(output/"source-manifest.json").write_text(json.dumps(manifest))
print(json.dumps(manifest))
""",
            encoding="utf-8",
        )
        (runtime / "collect-public-sources.py").chmod(0o755)

        finalizer = runtime / "commit-push-publication-evidence.py"
        real_finalizer = runtime / "commit-push-publication-evidence.real.py"
        finalizer.rename(real_finalizer)
        finalizer.write_text(
            """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
real=Path(__file__).with_name("commit-push-publication-evidence.real.py")
completed=subprocess.run([str(real),*sys.argv[1:]],check=False)
if completed.returncode == 0 and os.environ.get("FAKE_CANONICAL_INVALID_FINAL") == "1":
    output=Path(sys.argv[6])
    result=json.loads(output.read_text())
    result["unexpected_property"]=True
    output.write_text(json.dumps(result))
raise SystemExit(completed.returncode)
""",
            encoding="utf-8",
        )
        finalizer.chmod(0o755)

        committer = runtime / "commit-reviewed-publication.py"
        real_committer = runtime / "commit-reviewed-publication.real.py"
        committer.rename(real_committer)
        committer.write_text(
            """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
real=Path(__file__).with_name("commit-reviewed-publication.real.py")
marker=Path(__file__).with_name("commit-head-drift-once.marker")
blocked_marker=Path(__file__).with_name("commit-blocked-replan-once.marker")
if len(sys.argv) > 1 and sys.argv[1] == "--recover":
    raise SystemExit(subprocess.run([str(real),*sys.argv[1:]],check=False).returncode)
if os.environ.get("FAKE_COMMIT_BLOCKED_REPLAN_ONCE") == "1" and not blocked_marker.exists():
    pre=json.loads(Path(sys.argv[2]).read_text())
    collection=json.loads(Path(sys.argv[3]).read_text())
    def blocked(key):
        state=pre[key]
        return {"commit_status":"not_started","commit_hashes":[],"pre_local_head":state["local_head"],"local_head":state["local_head"],"pre_dirty_digest":state["dirty_digest"],"post_dirty_digest":state["dirty_digest"],"clean":not bool(state.get("dirty_paths",[])),"publication_mode":"blocked","deferred_cleanup":[]}
    result={"outcome":"blocked","phase":"local_commit","daily_pipeline_status":"complete","summary_path":None,"advisory_path":None,"notification_result":collection.get("notification_result"),"agents_vault":blocked("agents_vault"),"user_vault":blocked("user_vault"),"publication_mode":{"agents_vault":"blocked","user_vault":"blocked"},"deferred_cleanup":{"agents_vault":[],"user_vault":[]},"evidence_finalization_commit":None,"retry_disposition":"replan","replan_vaults":["user_vault"],"next_action":"fixture no-progress target collision"}
    Path(sys.argv[10]).write_text(json.dumps(result))
    blocked_marker.write_text("replanned without progress\\n")
    raise SystemExit(75)
if os.environ.get("FAKE_COMMIT_HEAD_DRIFT_ONCE") == "1" and not marker.exists():
    runtime=json.loads(Path(sys.argv[1]).read_text())
    for key in ("agents_vault_root",):
        repo=runtime[key]
        original=subprocess.check_output(["git","-C",repo,"rev-parse","HEAD"],text=True).strip()
        tree=subprocess.check_output(["git","-C",repo,"show","-s","--format=%T",original],text=True).strip()
        drift=subprocess.check_output(
            ["git","-C",repo,"-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit-tree",tree,"-p",original],
            input="fixture persistent HEAD drift\\n",text=True,
        ).strip()
        subprocess.run(["git","-C",repo,"update-ref","HEAD",drift,original],check=True)
    marker.write_text("replanned\\n")
    completed=subprocess.run([str(real),*sys.argv[1:]],check=False)
    raise SystemExit(completed.returncode)
completed=subprocess.run([str(real),*sys.argv[1:]],check=False)
raise SystemExit(completed.returncode)
""",
            encoding="utf-8",
        )
        committer.chmod(0o755)

        for repo, key in ((self.agents, "agents"), (self.user, "user")):
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
            (repo / "initial.md").write_text("initial\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)

        fake_codex = runtime / "fake-codex.py"
        fake_codex.write_text(
            """#!/usr/bin/env python3
import hashlib, json, os, re, subprocess, sys
from pathlib import Path
args=sys.argv[1:]
output=Path(args[args.index("--output-last-message")+1])
assert args[-1] == "-"
prompt=sys.stdin.read()
context=json.loads(prompt.split("Runtime context JSON:\\n",1)[1])
def contains_key(value,key):
    if isinstance(value,dict):
        return key in value or any(contains_key(item,key) for item in value.values())
    if isinstance(value,list):
        return any(contains_key(item,key) for item in value)
    return False
schema=Path(args[args.index("--output-schema")+1]).name
schema_document=json.loads(Path(args[args.index("--output-schema")+1]).read_text())
schema_encoded=json.dumps(schema_document,sort_keys=True)
assert all(f'"{keyword}"' not in schema_encoded for keyword in ("allOf","if","then","else","oneOf","uniqueItems"))
stage="collection" if "--search" in args else ("review" if schema=="publication-review-result.schema.json" else ("evidence_review" if schema=="evidence-review-result.schema.json" else "publication"))
log_path=output.parent/"invocations.log"
if stage=="review":
    log_path=Path(context["publication_context_file"]).parent.parent/"invocations.log"
with log_path.open("a") as log:
    log.write(stage+"\\n")
if stage=="evidence_review" and os.environ.get("FAKE_EVIDENCE_REVIEW_INPUT_TOO_LARGE") == "1":
    print("input exceeds maximum length", file=sys.stderr)
    raise SystemExit(75)
if "--search" in args:
    staging=Path(context["collection_output_root"])
    standing=Path(context["standing_task"])
    assert standing.parent == staging
    assert standing.name == "standing-task.md"
    assert standing.read_text()
    run_date=context["started_at"][:10]
    summary=staging/f"SUMMARY-IT-NEWS-{run_date}.md"
    advisory=staging/f"Personal-Vulnerability-Advisory-{run_date}.md"
    catalog=json.loads(Path(context["source_catalog"]).read_text())
    manifest=json.loads(Path(context["source_manifest"]).read_text())
    assert len(manifest["sources"]) == len(catalog["sources"])
    (staging/"source-resolutions.json").write_text(json.dumps({"version":1,"resolutions":[],"date_evidence":[]}))
    lines=["summary "+context["run_id"],"","## 確認済みサイト一覧","","| サイト | Tier | 状態 | 取得方法 | 確認URL | 期間内件数 | 理由 |","|---|---:|---|---|---|---:|---|"]
    evidence_by_name={item["name"]:item for item in manifest["sources"]}
    for source_index,source in enumerate(catalog["sources"]):
        url=source["feed_url"] or source["page_url"]
        method="RSS" if source["feed_url"] else "公開ページ"
        evidence=evidence_by_name[source["name"]]
        if evidence["status"] == "access_constraint":
            reason=os.environ.get("FAKE_COLLECTION_CONSTRAINT_REASON",evidence["constraint"])
            lines.append(f"| {source['name']} | {source['tier']} | アクセス制約 | {method} | {url} | 0 | {reason} |")
        else:
            status="取得済み" if os.environ.get("FAKE_COLLECTION_STATUS_FROM_FETCH") == "1" and source_index == 0 else "対象期間記事なし"
            lines.append(f"| {source['name']} | {source['tier']} | {status} | {method} | {url} | 0 | fixture確認 |")
    summary.write_text("\\n".join(lines)+"\\n")
    digest=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    advisory.write_text(f"- 入力ニュース: {summary.name} (same-run SHA-256: {digest(summary)})\\n")
    result={"daily_pipeline_status":"complete","run_id":context["run_id"],"summary_path":str(summary),"summary_sha256":digest(summary),"advisory_path":str(advisory),"advisory_sha256":digest(advisory),"notification_result":"none","vault_artifacts_complete":True,"next_action":None}
    if os.environ.get("FAKE_CANONICAL_INVALID_COLLECTION") == "1":
        result["next_action"]="must be null for a complete result"
elif stage=="review":
    assert context["publication_context_projection"] == "review_bounded_v2"
    assert not contains_key(context,"index_entries")
    publication=context["publication_context"]
    authorization=Path(publication["authorization_task"])
    assert authorization.name == "authorization-task.md"
    assert authorization.parent.name == "review-input"
    assert hashlib.sha256(authorization.read_bytes()).hexdigest() == publication["authorization_task_sha256"]
    dirty_manifest=Path(publication["dirty_snapshot_manifest_file"])
    assert hashlib.sha256(dirty_manifest.read_bytes()).hexdigest() == publication["dirty_snapshot_manifest_sha256"]
    snapshot_manifest=json.loads(dirty_manifest.read_text())
    assert snapshot_manifest["version"] == 4
    runtime_context=publication["runtime"]
    pre=publication["pre_collection_state"]
    assert all("index_entries" not in state for state in pre.values())
    mode_hint=publication["publication_mode_hint"]
    plan=context["artifact_plan"]
    artifacts=publication["publication_manifest"]["artifact_manifest"]
    def rel(root,target):
        return os.path.relpath(target,root)
    def manifest(kind):
        if kind=="agents":
            state=pre["agents_vault"]; root=runtime_context["agents_vault_root"]; item=artifacts["advisory"]; target=plan["advisory_target"]
            evidence=rel(root,publication["standing_task"])
        else:
            state=pre["user_vault"]; root=runtime_context["user_vault_root"]; item=artifacts["summary"]; target=plan["summary_target"]
            evidence=None
        target_rel=rel(root,target)
        key="agents_vault" if kind=="agents" else "user_vault"
        mode=mode_hint[key]["required_mode"]
        dirty_materialization=snapshot_manifest["vaults"][key]
        commit_materialization=snapshot_manifest["local_commits"][key]
        assert all(entry["materialization_status"]!="blocked" for entry in commit_materialization)
        if any(entry["materialization_status"]=="deferred" for entry in dirty_materialization):
            assert mode in {"own_only","blocked"}
        initial=sorted(set(state["dirty_paths"]+[target_rel]))
        existing_paths=[path for commit in state["local_commits"] for path in commit["changed_paths"]]
        if mode=="sweep":
            owned=sorted(set(initial+existing_paths+([evidence] if evidence else [])))
            excluded=[]; deferred=[]; approved_dirty=state["dirty_entries"]; groups=[{"message":"fixture publication","paths":initial}]; residual="quality_ok"; commit_required=True; finalization=({"target_path":evidence,"template":"daily_publication_v1"} if evidence else None)
        elif mode=="own_only":
            carried=mode_hint[key].get("artifact_already_committed",False)
            owned=sorted(set([target_rel]+([evidence] if evidence else [])))
            excluded=state["dirty_paths"]; deferred=[{"path":path,"reason":"fixture deferred residual"} for path in excluded]; approved_dirty=[]; groups=[] if carried else [{"message":"fixture publication","paths":[target_rel]}]; residual="deferred"; commit_required=not carried; finalization=({"target_path":evidence,"template":"daily_publication_v1"} if evidence else None)
        else:
            owned=[target_rel]; excluded=state["dirty_paths"]; deferred=[{"path":path,"reason":"fixture blocked state"} for path in excluded]; approved_dirty=[]; groups=[]; residual="blocked"; commit_required=False; finalization=None
        approved_commits=[{**commit,"patch_sha256":material.get("patch_sha256")} for commit,material in zip(state["local_commits"],commit_materialization)]
        reviewed_snapshot=state["diff_snapshot_sha256"]
        if os.environ.get("FAKE_REVIEW_STATE_AS_TYPED_EVIDENCE") == "1":
            reviewed_snapshot=mode_hint[key]["review_state_sha256"]
        return {"repo_root":root,"task_id":publication["authorization_task_id"],"publication_mode":mode,"core_review_status":"quality_ok","residual_review_status":residual,"owned_paths":owned,"excluded_paths":excluded,"deferred_cleanup":deferred,"approved_diff_snapshot_sha256":state["diff_snapshot_sha256"],"approved_existing_commits":approved_commits,"approved_dirty_entries":approved_dirty,"reviewed_artifacts":[{"role":item["role"],"source_sha256":item["sha256"],"target_path":target_rel}],"validation_evidence":{"file_guard":"passed","secret_scan":"passed","secret_scan_tool":"gitleaks","secret_scan_tool_version":runtime_context["gitleaks_version"],"reviewed_snapshot_sha256":reviewed_snapshot,"reviewed_history_sha256":state["history_snapshot_sha256"]},"review_or_validation_status":"quality_ok","commit_required":commit_required,"unrelated_dirty_paths":excluded,"commit_groups":groups,"evidence_finalization":finalization}
    agents_manifest=manifest("agents"); user_manifest=manifest("user")
    if os.environ.get("FAKE_OMIT_AGENTS_RESIDUALS") == "1" and agents_manifest["publication_mode"] == "own_only":
        agents_manifest["excluded_paths"]=[]
        agents_manifest["unrelated_dirty_paths"]=[]
        agents_manifest["deferred_cleanup"]=[]
    blocked_modes=agents_manifest["publication_mode"]=="blocked" or user_manifest["publication_mode"]=="blocked"
    both_blocked=agents_manifest["publication_mode"]=="blocked" and user_manifest["publication_mode"]=="blocked"
    result={"outcome":"blocked" if both_blocked else "approved","publication_context_sha256":context["publication_context_sha256"],"agents_vault":agents_manifest,"user_vault":user_manifest,"next_action":"fixture blocked Vault" if blocked_modes else None}
elif stage=="publication":
    publication=context["publication_context"]
    runtime_context=publication["runtime"]
    pre=publication["pre_collection_state"]
    approved=context["approved_review"]
    installed={}
    for role in ("agents_security_advisory","user_it_news_summary"):
        installed.update(json.loads(subprocess.check_output([publication["installer"],publication["runtime_context_file"],publication["collection_result_file"],publication["artifact_plan_file"],role],text=True)))
    def publish(key,root):
        manifest=approved[key]
        for group in manifest["commit_groups"]:
            subprocess.run(["git","-C",root,"add","--",*group["paths"]],check=True)
            subprocess.run(["git","-C",root,"commit","-q","-m",group["message"]],check=True)
        head=subprocess.check_output(["git","-C",root,"rev-parse","HEAD"],text=True).strip()
        state=pre[key]
        commits=subprocess.check_output(["git","-C",root,"rev-list","--reverse",f"{state['local_head']}..{head}"],text=True).splitlines()
        return {"commit_status":"complete","commit_hashes":commits,"pre_local_head":state["local_head"],"local_head":head,"pre_dirty_digest":state["dirty_digest"],"post_dirty_digest":hashlib.sha256(b"").hexdigest(),"clean":True}
    verified=publication["verified_collection"]
    result={"outcome":"ready_to_push","phase":"local_commit","daily_pipeline_status":"complete","summary_path":installed["summary_target"],"advisory_path":installed["advisory_target"],"notification_result":"none","agents_vault":publish("agents_vault",runtime_context["agents_vault_root"]),"user_vault":publish("user_vault",runtime_context["user_vault_root"]),"evidence_finalization_commit":None,"next_action":None}
else:
    assert context["publication_context_projection"] == "review_bounded_v2"
    assert not contains_key(context,"index_entries")
    plan=context["evidence_plan"]
    publication=context["publication_context"]
    evidence_diff=Path(plan["review_patch_path"]).read_bytes()
    assert hashlib.sha256(evidence_diff).hexdigest() == plan["evidence_diff_sha256"]
    payload_lines=[line[1:] for line in evidence_diff.decode().splitlines() if line.startswith('+{')]
    assert len(payload_lines) == 1
    evidence_payload=json.loads(payload_lines[0])
    assert set(evidence_payload) == {
        "run_id","publication_context_sha256","agents_vault","user_vault",
        "summary_repo_path","advisory_repo_path","publication_mode",
        "deferred_cleanup","notification_result",
    }
    assert re.fullmatch(
        r"discord:(delivered|already_delivered|failed|ambiguous);"
        r"summary_commit=[0-9a-f]{40};"
        r"receipt_sha256=([0-9a-f]{64}|none)"
        r"(;message_id=[1-9][0-9]{16,19})?"
        r"(;error_code=[a-z0-9_]+)?",
        evidence_payload["notification_result"],
    )
    if os.environ.get("FAKE_EVIDENCE_REVIEW_BLOCKED") == "1":
        result={"outcome":"blocked","target_path":plan["target_path"],"evidence_diff_sha256":plan["evidence_diff_sha256"],"publication_context_sha256":plan["publication_context_sha256"],"review_status":"blocked","next_action":"fixture evidence review rejection"}
    else:
        result={"outcome":"approved","target_path":plan["target_path"],"evidence_diff_sha256":plan["evidence_diff_sha256"],"publication_context_sha256":plan["publication_context_sha256"],"review_status":"quality_ok","next_action":None}
output.write_text(json.dumps(result))
if stage=="collection" and os.environ.get("FAKE_SNAPSHOT_RAW_COLLECTION") == "1":
    raw_paths=(Path(result["summary_path"]),Path(result["advisory_path"]),output)
    snapshot=[]
    for raw_path in raw_paths:
        metadata=raw_path.stat()
        snapshot.append({"path":str(raw_path),"content_hex":raw_path.read_bytes().hex(),"mode":metadata.st_mode & 0o7777,"mtime_ns":metadata.st_mtime_ns})
    (output.parent/"raw-collection-pre-normalization.json").write_text(json.dumps({"files":snapshot}))
""",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        (runtime / "automation.local.env").write_text(
            self.config.read_text(encoding="utf-8").replace(
                "CODEX_BIN=/usr/bin/true", f"CODEX_BIN={fake_codex}"
            ),
            encoding="utf-8",
        )
        self.write_runtime_release_manifest(runtime, "c" * 40)

        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(self.user), "branch", "--unset-upstream"],  # noqa: S607
            check=True,
        )
        missing_upstream = subprocess.run(  # noqa: S603 -- controlled fixture executable
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={**os.environ, "PATH": os.environ["PATH"]},
        )
        self.assertEqual(missing_upstream.returncode, 75)
        missing_upstream_logs = list((runtime / "logs").rglob("invocations.log"))
        self.assertEqual(len(missing_upstream_logs), 1)
        self.assertEqual(
            missing_upstream_logs[0].read_text(encoding="utf-8").splitlines(),
            ["collection", "review"],
            msg=(runtime / "last-status.txt").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "phase=publication",
            (runtime / "last-status.txt").read_text(encoding="utf-8"),
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            [  # noqa: S607 -- controlled Git fixture command
                "git", "-C", str(self.user), "branch", "--set-upstream-to",
                "origin/main", "main",
            ],
            check=True,
        )
        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        ahead = self.root / "ahead-clone"
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--branch",
                "main",
                str(self.origins["user"]),
                str(ahead),
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(ahead), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(ahead),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        (ahead / "ahead.md").write_text("ahead\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(ahead), "add", "ahead.md"], check=True)
        subprocess.run(
            ["git", "-C", str(ahead), "commit", "-q", "-m", "ahead"], check=True
        )
        subprocess.run(["git", "-C", str(ahead), "push", "-q", "origin", "main"], check=True)

        blocked = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={**os.environ, "PATH": os.environ["PATH"]},
        )
        self.assertEqual(blocked.returncode, 75)
        blocked_logs = list((runtime / "logs").rglob("invocations.log"))
        self.assertEqual(len(blocked_logs), 1)
        self.assertEqual(
            blocked_logs[0].read_text(encoding="utf-8").splitlines(),
            ["collection", "review"],
        )
        self.assertIn(
            "phase=publication",
            (runtime / "last-status.txt").read_text(encoding="utf-8"),
        )
        subprocess.run(
            ["git", "-C", str(self.user), "merge", "--ff-only", "origin/main"],
            check=True,
            capture_output=True,
        )
        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        verifier_marker = runtime / "resolution-verifier-invoked.marker"
        verifier_marker.unlink(missing_ok=True)
        invalid_result = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_CANONICAL_INVALID_COLLECTION": "1",
            },
        )
        self.assertEqual(invalid_result.returncode, 75)
        invalid_logs = list((runtime / "logs").rglob("invocations.log"))
        self.assertEqual(
            len(invalid_logs),
            1,
            msg=f"stdout={invalid_result.stdout}\nstderr={invalid_result.stderr}\nstatus={(runtime / 'last-status.txt').read_text(encoding='utf-8')}\ncollection_stderr={[path.read_text(encoding='utf-8') for path in (runtime / 'logs').rglob('collection.stderr.log')]}",
        )
        self.assertEqual(
            invalid_logs[0].read_text(encoding="utf-8").splitlines(),
            ["collection"],
        )
        self.assertIn(
            "phase=collection",
            (runtime / "last-status.txt").read_text(encoding="utf-8"),
        )
        self.assertFalse(
            verifier_marker.exists(),
            "raw schema failure must precede resolution verification",
        )
        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        standing = self.agents / "tasks" / "standing.md"
        standing_relative = "tasks/standing.md"
        standing_head_before = subprocess.check_output(
            ["git", "-C", str(self.agents), "show", f"HEAD:{standing_relative}"]
        )
        standing.write_bytes(standing.read_bytes() + b"staged parallel evidence\n")
        subprocess.run(
            ["git", "-C", str(self.agents), "add", standing_relative], check=True
        )
        standing_index_before = subprocess.check_output(
            ["git", "-C", str(self.agents), "show", f":{standing_relative}"]
        )
        standing.write_bytes(standing.read_bytes() + b"unstaged parallel evidence\n")
        standing_worktree_before = standing.read_bytes()
        standing_status_before = subprocess.check_output(
            ["git", "-C", str(self.agents), "status", "--porcelain=v1", "--", standing_relative],
            text=True,
        )

        (self.user / "local-ahead.md").write_text("local ahead\n", encoding="utf-8")
        unsafe_handoff = self.user / ".codex-handoff" / "unsafe.md"
        unsafe_handoff.parent.mkdir()
        unsafe_handoff.write_text(
            f"existing unsafe handoff {Path.home()}\n", encoding="utf-8"
        )
        unsafe_handoff.chmod(0o640)
        unsafe_before = unsafe_handoff.read_bytes()
        unsafe_stat_before = unsafe_handoff.stat()
        agents_unsafe = self.agents / ".codex-handoff" / "unsafe-agents.md"
        agents_unsafe.parent.mkdir()
        agents_unsafe.write_text(
            f"existing unsafe agents residual {Path.home()}\n", encoding="utf-8"
        )
        agents_unsafe.chmod(0o640)
        agents_unsafe_before = agents_unsafe.read_bytes()
        agents_unsafe_stat_before = agents_unsafe.stat()
        subprocess.run(
            ["git", "-C", str(self.user), "add", "local-ahead.md"], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.user), "commit", "-q", "-m", "local ahead"],
            check=True,
        )
        local_ahead_commit = subprocess.check_output(
            ["git", "-C", str(self.user), "rev-parse", "HEAD"], text=True
        ).strip()

        result = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_COMMIT_HEAD_DRIFT_ONCE": "1",
                "FAKE_PLAN_TARGET_CONFLICT_ONCE": "1",
                "FAKE_OMIT_AGENTS_RESIDUALS": "1",
                "FAKE_COLLECTION_CONSTRAINT": "1",
                "FAKE_COLLECTION_CONSTRAINT_REASON": "paywall",
                "FAKE_COLLECTION_STATUS_FROM_FETCH": "1",
                "FAKE_SNAPSHOT_RAW_COLLECTION": "1",
                "FAKE_REVIEW_STATE_AS_TYPED_EVIDENCE": "1",
            },
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(runtime / "last-status.txt").read_text(encoding="utf-8")
            + "\n"
            + "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in (runtime / "logs").rglob("*.stderr.log")
            )
            + "\nRESULTS\n"
            + "\n".join(
                path.name + ":" + path.read_text(encoding="utf-8", errors="replace")
                for path in (runtime / "logs").rglob("*result.json")
            ),
        )
        invocation_logs = list((runtime / "logs").rglob("invocations.log"))
        self.assertEqual(len(invocation_logs), 1)
        self.assertEqual(
            invocation_logs[0].read_text(encoding="utf-8").splitlines(),
            ["collection", "review", "review", "evidence_review"],
        )
        self.assertTrue((runtime / "commit-head-drift-once.marker").is_file())
        conflict_marker = runtime / "artifact-target-conflict-once.marker"
        self.assertTrue(conflict_marker.is_file())
        conflicted_target = Path(conflict_marker.read_text(encoding="utf-8").strip())
        self.assertEqual(
            conflicted_target.read_text(encoding="utf-8"),
            "concurrent target must survive\n",
        )
        status_files = list(runtime.glob("last-status.txt"))
        self.assertEqual(len(status_files), 1)
        terminal_status = status_files[0].read_text()
        self.assertIn("semantic_status=success", terminal_status)
        self.assertIn("notification_disposition=attempted", terminal_status)
        self.assertIn("notification_status=0", terminal_status)
        release_checks = list(runtime.glob("logs/**/runtime-release-verification.txt"))
        self.assertEqual(len(release_checks), 1)
        self.assertIn("source_commit=" + "c" * 40, release_checks[0].read_text())
        for audit_name in (
            "collection-agent-result.json",
            "collection-result.json",
            "collection-normalization.json",
            "publication-review-agent-result.json",
            "publication-review-result.json",
            "publication-review-normalization.json",
        ):
            self.assertIn(audit_name, terminal_status)
        collection_results = list(runtime.glob("logs/**/collection-result.json"))
        self.assertEqual(len(collection_results), 1)
        collection_result_path = collection_results[0]
        collection_root = collection_result_path.parent
        raw_collection_path = collection_root / "collection-agent-result.json"
        collection_receipt_path = collection_root / "collection-normalization.json"
        raw_snapshot_path = collection_root / "raw-collection-pre-normalization.json"
        raw_snapshot = json.loads(raw_snapshot_path.read_text())
        for raw_file in raw_snapshot["files"]:
            path = Path(raw_file["path"])
            metadata = path.stat()
            self.assertEqual(path.read_bytes(), bytes.fromhex(raw_file["content_hex"]))
            self.assertEqual(stat.S_IMODE(metadata.st_mode), raw_file["mode"])
            self.assertEqual(metadata.st_mtime_ns, raw_file["mtime_ns"])
        raw_collection = json.loads(raw_collection_path.read_text())
        canonical_collection = json.loads(collection_result_path.read_text())
        raw_summary_path = Path(raw_collection["summary_path"])
        canonical_summary_path = Path(canonical_collection["summary_path"])
        canonical_advisory_path = Path(canonical_collection["advisory_path"])
        self.assertEqual(
            stat.S_IMODE(canonical_summary_path.parent.stat().st_mode), 0o700
        )
        for private_path in (
            canonical_summary_path,
            canonical_advisory_path,
            collection_result_path,
            collection_receipt_path,
        ):
            self.assertEqual(stat.S_IMODE(private_path.stat().st_mode), 0o600)
        raw_summary_bytes = raw_summary_path.read_bytes()
        self.assertIn(b"paywall", raw_summary_bytes)
        self.assertIn(
            b"| TechCrunch | 1 | \xe5\x8f\x96\xe5\xbe\x97\xe6\xb8\x88\xe3\x81\xbf |",
            raw_summary_bytes,
        )
        self.assertNotIn("paywall", canonical_summary_path.read_text())
        self.assertIn("| robots |", canonical_summary_path.read_text())
        self.assertIn(
            "| TechCrunch | 1 | 対象期間記事なし |",
            canonical_summary_path.read_text(),
        )
        collection_receipt = json.loads(collection_receipt_path.read_text())
        self.assertEqual(collection_receipt["corrected_reason_count"], 1)
        self.assertEqual(collection_receipt["corrected_status_count"], 1)
        self.assertEqual(
            collection_receipt["raw_collection_result_sha256"],
            hashlib.sha256(raw_collection_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            collection_receipt["canonical_collection_result_sha256"],
            hashlib.sha256(collection_result_path.read_bytes()).hexdigest(),
        )
        for label in ("catalog", "manifest", "verified_resolutions"):
            evidence = collection_receipt["source_evidence"][label]
            evidence_path = Path(evidence["path"])
            self.assertEqual(
                evidence["sha256"], hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            )
        self.assertEqual(raw_summary_path.read_bytes(), raw_summary_bytes)
        self.assertIn(
            f"same-run SHA-256: {canonical_collection['summary_sha256']}",
            canonical_advisory_path.read_text(),
        )
        publication_results = list(runtime.glob("logs/**/publication-result.json"))
        self.assertEqual(len(publication_results), 1)
        publication_result = json.loads(publication_results[0].read_text())
        self.assertTrue(
            publication_result["notification_result"].startswith("discord:delivered;")
        )
        self.assertEqual(
            publication_result["evidence_review"]["reason_code"], "approved"
        )
        self.assertEqual(publication_result["evidence_review"]["process_status"], 0)
        self.assertEqual(publication_result["evidence_review"]["status"], 0)
        self.assertTrue(publication_result["evidence_review"]["result_present"])
        self.assertRegex(
            publication_result["evidence_review"]["stderr_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertIn(
            local_ahead_commit, publication_result["user_vault"]["commit_hashes"]
        )
        self.assertEqual(
            publication_result["user_vault"]["local_head"],
            publication_result["user_vault"]["remote_head"],
        )
        actual_user_clean = not bool(
            subprocess.check_output(
                [
                    "git", "-C", str(self.user), "status",
                    "--porcelain=v1", "--untracked-files=all",
                ],
                text=True,
            )
        )
        self.assertEqual(
            publication_result["user_vault"]["clean"], actual_user_clean
        )
        self.assertEqual(publication_result["publication_mode"]["user_vault"], "own_only")
        self.assertEqual(
            publication_result["publication_mode"]["agents_vault"], "own_only"
        )
        self.assertEqual(
            [item["path"] for item in publication_result["deferred_cleanup"]["user_vault"]],
            [],
        )
        self.assertEqual(
            [
                item["path"]
                for item in publication_result["deferred_cleanup"]["agents_vault"]
            ],
            [standing_relative],
        )
        normalization_receipts = list(
            runtime.glob("logs/**/publication-review-normalization.json")
        )
        self.assertGreaterEqual(len(normalization_receipts), 1)
        self.assertTrue(
            any(
                json.loads(path.read_text())["vaults"]["agents_vault"][
                    "filled_reason_count"
                ]
                > 0
                for path in normalization_receipts
            )
        )
        saw_typed_identity_repair = False
        for receipt_path in normalization_receipts:
            attempt_root = receipt_path.parent
            raw_path = attempt_root / "publication-review-agent-result.json"
            canonical_path = attempt_root / "publication-review-result.json"
            receipt = json.loads(receipt_path.read_text())
            raw_review = json.loads(raw_path.read_text())
            canonical_review = json.loads(canonical_path.read_text())
            reviewed_state = json.loads(
                (attempt_root / "reviewed-publication-state.json").read_text()
            )
            self.assertEqual(receipt["version"], 2)
            for key in ("agents_vault", "user_vault"):
                raw_snapshot = raw_review[key]["validation_evidence"][
                    "reviewed_snapshot_sha256"
                ]
                expected_snapshot = reviewed_state[key]["diff_snapshot_sha256"]
                self.assertNotEqual(raw_snapshot, expected_snapshot)
                self.assertEqual(
                    canonical_review[key]["validation_evidence"][
                        "reviewed_snapshot_sha256"
                    ],
                    expected_snapshot,
                )
                evidence_receipt = receipt["validation_evidence"][key]
                self.assertTrue(evidence_receipt["normalized"])
                self.assertIn(
                    "reviewed_snapshot_sha256",
                    evidence_receipt["corrected_fields"],
                )
                saw_typed_identity_repair = True
            if raw_review["agents_vault"]["publication_mode"] != "own_only":
                continue
            self.assertEqual(raw_review["agents_vault"]["excluded_paths"], [])
            self.assertEqual(
                canonical_review["agents_vault"]["excluded_paths"],
                sorted(reviewed_state["agents_vault"]["dirty_paths"]),
            )
            self.assertEqual(
                receipt["raw_review_sha256"],
                hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                receipt["canonical_review_sha256"],
                hashlib.sha256(canonical_path.read_bytes()).hexdigest(),
            )
        self.assertTrue(saw_typed_identity_repair)
        self.assertEqual(unsafe_handoff.read_bytes(), unsafe_before)
        unsafe_stat_after = unsafe_handoff.stat()
        self.assertEqual(unsafe_stat_after.st_mode, unsafe_stat_before.st_mode)
        self.assertEqual(unsafe_stat_after.st_mtime_ns, unsafe_stat_before.st_mtime_ns)
        self.assertEqual(agents_unsafe.read_bytes(), agents_unsafe_before)
        agents_unsafe_stat_after = agents_unsafe.stat()
        self.assertEqual(
            agents_unsafe_stat_after.st_mode, agents_unsafe_stat_before.st_mode
        )
        self.assertEqual(
            agents_unsafe_stat_after.st_mtime_ns,
            agents_unsafe_stat_before.st_mtime_ns,
        )
        standing_head_after = subprocess.check_output(
            ["git", "-C", str(self.agents), "show", f"HEAD:{standing_relative}"]
        )
        standing_index_after = subprocess.check_output(
            ["git", "-C", str(self.agents), "show", f":{standing_relative}"]
        )
        standing_worktree_after = standing.read_bytes()
        standing_status_after = subprocess.check_output(
            ["git", "-C", str(self.agents), "status", "--porcelain=v1", "--", standing_relative],
            text=True,
        )
        self.assertNotIn(b"staged parallel evidence", standing_head_after)
        self.assertIn(b"vault-change-publisher:", standing_head_after)
        self.assertIn(b"staged parallel evidence", standing_index_after)
        self.assertNotIn(b"unstaged parallel evidence", standing_index_after)
        self.assertIn(b"vault-change-publisher:", standing_index_after)
        self.assertIn(b"staged parallel evidence", standing_worktree_after)
        self.assertIn(b"unstaged parallel evidence", standing_worktree_after)
        self.assertIn(b"vault-change-publisher:", standing_worktree_after)
        self.assertEqual(standing_status_after, standing_status_before)

        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        no_progress_replan = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_COMMIT_BLOCKED_REPLAN_ONCE": "1",
            },
        )
        self.assertEqual(
            no_progress_replan.returncode,
            0,
            msg=(runtime / "last-status.txt").read_text(encoding="utf-8")
            + "\n"
            + "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in (runtime / "logs").rglob("*.stderr.log")
            ),
        )
        no_progress_roots = [
            path
            for date_root in (runtime / "logs").iterdir()
            for path in date_root.iterdir()
            if path.is_dir()
        ]
        self.assertEqual(len(no_progress_roots), 1)
        no_progress_root = no_progress_roots[0]
        first_no_progress = json.loads(
            (
                no_progress_root
                / "publication-attempt-1"
                / "publication-commit-result.json"
            ).read_text()
        )
        self.assertEqual(first_no_progress["outcome"], "blocked")
        self.assertEqual(first_no_progress["retry_disposition"], "replan")
        second_context = json.loads(
            (
                no_progress_root
                / "publication-attempt-2"
                / "publication-context.json"
            ).read_text()
        )
        self.assertIsNone(second_context["carried_commit_result"])
        self.assertIsNone(second_context["carried_commit_result_sha256"])
        self.assertTrue((runtime / "commit-blocked-replan-once.marker").is_file())

        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        remote_before_notification_failure = {
            key: subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            for key, repo in (("agents", self.agents), ("user", self.user))
        }
        notification_failure = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_NOTIFICATION_NO_OUTPUT": "1",
            },
        )
        self.assertEqual(notification_failure.returncode, 75)
        notification_failure_status = (runtime / "last-status.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("phase=notification", notification_failure_status)
        self.assertIn(
            "semantic_status=notification_failed", notification_failure_status
        )
        self.assertIn("notification_status=75", notification_failure_status)
        self.assertIn("evidence_finalization_status=0", notification_failure_status)
        self.assertIn(
            "discord-notification-fallback-result.json", notification_failure_status
        )
        notification_failure_results = list(
            runtime.glob("logs/**/publication-result.json")
        )
        self.assertEqual(len(notification_failure_results), 1)
        notification_failure_result = json.loads(
            notification_failure_results[0].read_text(encoding="utf-8")
        )
        self.assertTrue(
            notification_failure_result["notification_result"].startswith(
                "discord:ambiguous;"
            )
        )
        self.assertIn(
            "error_code=sender_failed_closed",
            notification_failure_result["notification_result"],
        )
        self.assertEqual(
            notification_failure_result["evidence_review"]["reason_code"],
            "approved",
        )
        self.assertIsNotNone(
            notification_failure_result["evidence_finalization_commit"]
        )
        remote_after_notification_failure = {
            key: subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            for key, repo in (("agents", self.agents), ("user", self.user))
        }
        for key in remote_before_notification_failure:
            self.assertNotEqual(
                remote_before_notification_failure[key],
                remote_after_notification_failure[key],
            )
        self.assertEqual(
            notification_failure_result["user_vault"]["local_head"],
            notification_failure_result["user_vault"]["remote_head"],
        )

        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        remote_before_persistent = {
            key: subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            for key, repo in (("agents", self.agents), ("user", self.user))
        }
        persistent_collision = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_INSTALL_TARGET_CONFLICT_ALWAYS": "user",
            },
        )
        self.assertEqual(persistent_collision.returncode, 75)
        persistent_status = (runtime / "last-status.txt").read_text(encoding="utf-8")
        persistent_roots = [
            path
            for date_root in (runtime / "logs").iterdir()
            for path in date_root.iterdir()
            if path.is_dir()
        ]
        self.assertEqual(
            len(persistent_roots),
            1,
            msg=persistent_status,
        )
        persistent_root = persistent_roots[0]
        self.assertTrue((persistent_root / "publication-attempt-4").is_dir())
        exhausted = json.loads(
            (persistent_root / "exhausted-replan-vaults.json").read_text()
        )
        self.assertEqual(exhausted, ["user_vault"])
        forced_hint = json.loads(
            (
                persistent_root
                / "publication-attempt-4"
                / "publication-mode-hint.json"
            ).read_text()
        )
        self.assertEqual(forced_hint["user_vault"]["required_mode"], "blocked")
        self.assertNotEqual(forced_hint["agents_vault"]["required_mode"], "blocked")
        remote_after_persistent = {
            key: subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            for key, repo in (("agents", self.agents), ("user", self.user))
        }
        self.assertNotEqual(
            remote_after_persistent["agents"],
            remote_before_persistent["agents"],
            msg=persistent_status
            + "\n"
            + "\n".join(
                f"{path.name}: {path.read_text(encoding='utf-8', errors='replace')}"
                for path in persistent_root.rglob("*result.json")
            ),
        )
        self.assertEqual(
            subprocess.check_output(
                [
                    "git", "-C", str(self.agents), "rev-list", "--count",
                    f"{remote_before_persistent['agents']}..{remote_after_persistent['agents']}",
                ],
                text=True,
            ).strip(),
            "1",
            msg="a successful peer Vault must be carried, not published again",
        )
        advisory_targets = {
            Path(json.loads(plan.read_text(encoding="utf-8"))["advisory_target"])
            for plan in persistent_root.glob(
                "publication-attempt-[1234]/artifact-plan.json"
            )
        }
        self.assertEqual(
            len(advisory_targets),
            1,
            msg="bounded User re-plan changed the completed Agents target",
        )
        self.assertEqual(
            remote_after_persistent["user"], remote_before_persistent["user"]
        )
        collision_targets = {
            Path(
                json.loads(plan.read_text(encoding="utf-8"))["summary_target"]
            )
            for plan in persistent_root.glob("publication-attempt-[123]/artifact-plan.json")
        }
        self.assertEqual(len(collision_targets), 3)
        for target in collision_targets:
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "persistent third-party target must survive\n",
            )
            target.unlink()
        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        invalid_final = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_CANONICAL_INVALID_FINAL": "1",
            },
        )
        self.assertEqual(invalid_final.returncode, 65)
        self.assertIn(
            "semantic_status=invalid_result",
            (runtime / "last-status.txt").read_text(encoding="utf-8"),
        )

        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()
        oversized_evidence_before = standing.read_bytes()
        oversized_evidence_stat = standing.stat()
        oversized_evidence = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_EVIDENCE_REVIEW_INPUT_TOO_LARGE": "1",
            },
        )
        self.assertEqual(oversized_evidence.returncode, 75)
        oversized_results = list(runtime.glob("logs/**/publication-result.json"))
        self.assertEqual(len(oversized_results), 1)
        oversized_result = json.loads(
            oversized_results[0].read_text(encoding="utf-8")
        )
        self.assertEqual(
            oversized_result["evidence_review"]["reason_code"], "input_too_large"
        )
        self.assertEqual(oversized_result["evidence_review"]["process_status"], 75)
        self.assertEqual(oversized_result["evidence_review"]["status"], 75)
        self.assertFalse(oversized_result["evidence_review"]["result_present"])
        self.assertIsNone(oversized_result["evidence_review"]["result_sha256"])
        self.assertEqual(standing.read_bytes(), oversized_evidence_before)
        self.assertEqual(standing.stat().st_mode, oversized_evidence_stat.st_mode)
        self.assertEqual(standing.stat().st_mtime_ns, oversized_evidence_stat.st_mtime_ns)

        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()
        evidence_rejection_before = standing.read_bytes()
        evidence_rejection_stat = standing.stat()
        rejected_evidence = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_EVIDENCE_REVIEW_BLOCKED": "1",
            },
        )
        self.assertEqual(rejected_evidence.returncode, 75)
        rejected_results = list(runtime.glob("logs/**/publication-result.json"))
        self.assertEqual(len(rejected_results), 1)
        rejected_result = json.loads(rejected_results[0].read_text(encoding="utf-8"))
        self.assertEqual(
            rejected_result["evidence_review"]["reason_code"], "result_rejected"
        )
        self.assertEqual(rejected_result["evidence_review"]["status"], 75)
        self.assertTrue(rejected_result["evidence_review"]["result_present"])
        self.assertEqual(standing.read_bytes(), evidence_rejection_before)
        self.assertEqual(standing.stat().st_mode, evidence_rejection_stat.st_mode)
        self.assertEqual(
            standing.stat().st_mtime_ns, evidence_rejection_stat.st_mtime_ns
        )


if __name__ == "__main__":
    unittest.main()
