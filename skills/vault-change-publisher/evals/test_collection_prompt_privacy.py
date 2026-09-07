#!/usr/bin/env python3
"""Static contract tests for privacy-safe scheduled collection prompts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class CollectionPromptPrivacyTests(unittest.TestCase):
    """Ensure prompt and PVA instructions agree on artifact privacy."""

    @staticmethod
    def _flat(content: str) -> str:
        return " ".join(content.split())

    @staticmethod
    def _contract_with_if_const(
        contracts: list[dict[str, object]], property_name: str, value: str
    ) -> dict[str, object]:
        """Select a schema branch by meaning instead of array position."""
        return next(
            contract
            for contract in contracts
            if contract.get("if", {}).get("properties", {}).get(property_name, {}).get(
                "const"
            )
            == value
        )

    def test_collection_prompt_requires_formatter_and_forbids_staging_path(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        self.assertIn("format-summary-reference.py", prompt)
        self.assertIn("must not contain", prompt)
        self.assertIn("absolute staging path", prompt)

    def test_vault_publisher_forbids_every_force_push_variant(self) -> None:
        """Keep the publication policy unambiguous about all force-push forms."""
        skill = (ROOT / "vault-change-publisher/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`--force`、`--force-with-lease`、`+` refspec", skill)
        self.assertNotIn("期待OIDなしlease", skill)

    def test_pva_report_contract_uses_basename_and_digest(self) -> None:
        skill = (ROOT / "personal-vulnerability-advisor/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "入力ニュース: {summary basename} (same-run SHA-256: {sha256})",
            skill,
        )
        self.assertIn("format-summary-reference.py", skill)

    def test_collection_instructions_share_exact_jst_calendar_window(self) -> None:
        files = [
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md",
            ROOT / "personal-vulnerability-advisor/SKILL.md",
            ROOT / "summarize-it-news/SKILL.md",
        ]
        for path in files:
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                self.assertIn("run_date - 6 days", content)
                self.assertIn("run_date - 7 days", content)
                self.assertIn("JST", content)

    def test_complete_collection_state_mapping_is_unambiguous(self) -> None:
        files = [
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md",
            ROOT / "personal-vulnerability-advisor/SKILL.md",
        ]
        for path in files:
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                self.assertIn("daily_pipeline_status", content)
                self.assertIn("vault_artifacts_complete", content)
                self.assertIn("next_action", content)
                self.assertIn("publisher", content)
                self.assertIn("handoff", content)

    def test_undated_html_candidates_require_fallback(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        skill = (ROOT / "summarize-it-news/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("zero publication-date evidence is unresolved", prompt)
        self.assertIn("must not be reported as", prompt)
        self.assertIn("date_evidence_count=0", skill)
        self.assertIn("対象期間記事なし", skill)
        for forbidden_resolution in ("home", "category", "archive", "listing"):
            self.assertIn(forbidden_resolution, prompt)
            self.assertIn(forbidden_resolution, skill)
        self.assertIn("specific official article page", prompt)
        self.assertIn("具体的な公式記事ページ", skill)

    def test_direct_counts_are_copied_from_trusted_manifest(self) -> None:
        """Keep routine audit counts deterministic instead of model-recounted."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        skill = (ROOT / "summarize-it-news/SKILL.md").read_text(encoding="utf-8")
        for content in (prompt, skill):
            self.assertIn("jst_window_item_count", content)
        self.assertIn("copy the trusted", prompt)
        self.assertIn("再集計・上書きしない", skill)

    def test_fallback_count_and_direct_date_evidence_are_disjoint(self) -> None:
        """Prevent search-result counts from escaping the sealed fallback candidate set."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        skill = (ROOT / "summarize-it-news/SKILL.md").read_text(encoding="utf-8")
        for content in (prompt, skill):
            self.assertIn("needs_search_fallback", content)
            self.assertIn("date_evidence", content)
            self.assertIn("access_constraint", content)
        self.assertIn("sole candidate set", prompt)
        self.assertIn("must not also appear", prompt)
        self.assertIn("1件のresolutionだけ", skill)
        self.assertIn("監査行へ合算しない", skill)

    def test_complete_collection_preflights_exact_fallback_request(self) -> None:
        """Catch correctable listing choices before the collection agent returns."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        publisher = (ROOT / "vault-change-publisher/SKILL.md").read_text(
            encoding="utf-8"
        )
        summarizer = (ROOT / "summarize-it-news/SKILL.md").read_text(
            encoding="utf-8"
        )
        for content in (prompt, publisher, summarizer):
            self.assertIn("--check-resolutions", content)
        self.assertNotIn("resolution_verification_root", prompt)
        self.assertIn("Do not pass or substitute another catalog", prompt)
        self.assertIn("canonical request path", prompt)
        self.assertIn("catalog/manifest/run rootの任意引数は受け付けず", publisher)
        self.assertIn("A `complete` result is forbidden", prompt)
        self.assertIn("at most three distinct candidates", prompt)
        self.assertIn("individual official article", prompt)
        self.assertIn("date falls outside the collection window", prompt)
        self.assertIn("independently repeats", prompt)
        self.assertIn("agent preflightをauthorityとして信頼しない", publisher)

    def test_resolution_preflight_inputs_are_executable_bound(self) -> None:
        """Keep an untrusted agent from selecting the verifier's host allowlist."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        runner = (
            ROOT
            / "vault-change-publisher/assets/run-daily-it-news-vulnerability-check.sh"
        ).read_text(encoding="utf-8")
        collector = (
            ROOT / "summarize-it-news/scripts/collect-public-sources.py"
        ).read_text(encoding="utf-8")
        command = prompt.split("```sh", 1)[1].split("```", 1)[0]
        self.assertIn('"<resolution_verifier>" --check-resolutions', command)
        self.assertIn("<collection_output_root>/source-resolutions.json", command)
        self.assertNotIn("source_catalog", command)
        self.assertNotIn("source_manifest", command)
        self.assertNotIn("resolution_verification_root", runner)
        self.assertIn("checked_runtime_inputs(Path(argv[2]))", collector)
        self.assertIn("len(argv) == 3", collector)
        self.assertIn("canonical_runtime_root()", collector)
        self.assertIn("pwd.getpwuid(os.getuid()).pw_dir", collector)
        self.assertIn("validate_manifest_catalog_binding", collector)
        self.assertIn('runtime / "it-news-sources.json"', collector)
        self.assertIn("checked_verification_output", collector)
        self.assertNotIn(
            'len(argv) == 6 and argv[1] == "--verify-resolutions"',
            collector,
        )

    def test_coverage_tier_is_exact_catalog_integer(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn("catalog's exact integer (`1` or `2`)", flat)
        self.assertIn("Do not write a display label", flat)
        self.assertIn("`Tier 1` or `Tier 2`", flat)

    def test_approved_publication_has_no_excluded_path_placeholders(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.review.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn("both `excluded_paths` and", prompt)
        self.assertIn("must be empty arrays", prompt)
        self.assertIn("`.obsidian/`", prompt)
        self.assertIn("return `blocked`", flat)

    def test_dirty_regular_files_are_not_blocked_by_lifecycle_inference(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.review.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn("Review captured regular files as inert", flat)
        self.assertIn("temporary handoff", flat)
        self.assertIn("Do not execute or simulate", flat)
        self.assertIn("lifecycle and usefulness judgments are not", flat)
        self.assertIn("Block a captured dirty file only for a concrete", flat)

    def test_sweep_owned_scope_separates_existing_history_and_later_evidence(self) -> None:
        """Make the reviewer emit the exact scope expected by the validator."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.review.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn("exact sorted union", flat)
        self.assertIn("every `changed_paths` entry", flat)
        self.assertIn("do not create a new commit group", flat)
        self.assertIn("must not appear in an initial publication commit group", flat)
        self.assertIn("neither local-ahead-only paths nor the later evidence target", flat)

    def test_publication_review_keeps_core_and_residual_status_separate(self) -> None:
        """Keep the legacy aggregate field bound only to current-run quality."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.review.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn(
            "review_or_validation_status` is the backward-compatible mirror of `core_review_status`, not an aggregate",
            flat,
        )
        self.assertIn(
            "A residual-history block sets only `residual_review_status` to `blocked`",
            flat,
        )
        schema = json.loads(
            (
                ROOT
                / "vault-change-publisher/references/publication-review-result.schema.json"
            ).read_text(encoding="utf-8")
        )
        status_contract = schema["$defs"]["taskChangeManifest"]["allOf"]
        quality_contract = self._contract_with_if_const(
            status_contract, "core_review_status", "quality_ok"
        )
        blocked_contract = self._contract_with_if_const(
            status_contract, "core_review_status", "blocked"
        )
        self.assertEqual(
            quality_contract["then"]["properties"]["review_or_validation_status"]["const"],
            "quality_ok",
        )
        self.assertEqual(
            blocked_contract["then"]["properties"]["review_or_validation_status"]["const"],
            "blocked",
        )

    def test_blocked_local_history_does_not_expand_dirty_residual_scope(self) -> None:
        """Keep local-ahead identity out of dirty-only deferred arrays."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.review.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn(
            "must each cover exactly the captured dirty paths and no other paths",
            flat,
        )
        self.assertIn(
            "never add a local-ahead commit's `changed_paths`",
            flat,
        )
        self.assertIn(
            "Preserve local-ahead identity only in `approved_existing_commits`",
            flat,
        )
        schema = json.loads(
            (
                ROOT
                / "vault-change-publisher/references/publication-review-result.schema.json"
            ).read_text(encoding="utf-8")
        )
        blocked_contract = self._contract_with_if_const(
            schema["$defs"]["taskChangeManifest"]["allOf"],
            "publication_mode",
            "blocked",
        )
        properties = blocked_contract["then"]["properties"]
        self.assertEqual(properties["commit_required"], {"const": False})
        self.assertEqual(properties["commit_groups"], {"maxItems": 0})
        self.assertEqual(properties["approved_dirty_entries"], {"maxItems": 0})
        self.assertEqual(properties["evidence_finalization"], {"type": "null"})

    def test_successful_publication_review_requires_null_next_action(self) -> None:
        """Keep deferred cleanup structured without turning success into a stop."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.review.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn("if and only if both Vault publication modes", flat)
        self.assertIn("belong only in `deferred_cleanup`", flat)
        self.assertIn("never duplicate them in `next_action`", flat)
        self.assertIn("only when at least one Vault is `blocked`", flat)

        schema = json.loads(
            (
                ROOT
                / "vault-change-publisher/references/publication-review-result.schema.json"
            ).read_text(encoding="utf-8")
        )
        mode_contracts = [
            contract
            for contract in schema["allOf"]
            if contract.get("if", {})
            .get("properties", {})
            .get("agents_vault", {})
            .get("properties", {})
            .get("publication_mode", {})
            .get("enum")
            == ["sweep", "own_only"]
        ]
        self.assertEqual(
            len(mode_contracts),
            1,
            "expected exactly one successful-publication next_action contract",
        )
        mode_contract = mode_contracts[0]
        self.assertEqual(
            mode_contract["then"]["properties"]["next_action"],
            {"type": "null"},
        )
        self.assertEqual(
            mode_contract["else"]["properties"]["next_action"],
            {"type": "string", "minLength": 1},
        )

    def test_sealed_residual_guard_forces_own_only_before_review(self) -> None:
        """Keep reviewer mistakes from sending unsafe residuals to the committer."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.review.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn("deterministic residual guard compares", flat)
        self.assertIn("newly added machine-home paths", flat)
        self.assertIn("pinned gitleaks failures", flat)
        self.assertIn("guarded mode hint already forces", flat)
        self.assertIn("Never upgrade it", flat)

    def test_resolution_request_is_limited_to_unresolved_sources(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn("status is exactly `needs_search_fallback`", prompt)
        self.assertIn("Never add a redundant resolution", flat)
        self.assertIn("direct manifest `access_constraint`", prompt)

    def test_audit_method_is_one_exact_manifest_mapping(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        for mapping in (
            "`rss` -> `RSS`",
            "`public_page` ->",
            "`site_search` -> `サイト限定検索`",
            "`official_alternate` -> `公式代替URL`",
        ):
            self.assertIn(mapping, prompt)
        self.assertIn("do not write `RSS / 公開ページ`", prompt)
        self.assertIn("exact sealed constraint in `理由`", prompt)
        self.assertIn("Never describe a sealed `robots` constraint as `購読`", prompt)

    def test_fallback_handoff_does_not_wait_for_post_response_evidence(self) -> None:
        """Collection submits candidates; the runner verifies them afterwards."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn("does not exist and is not readable during", flat)
        self.assertIn("treat that source as provisionally resolved", flat)
        self.assertIn("Do not return blocked only because", flat)
        self.assertIn("After this response", flat)
        self.assertIn("fails closed before publication", flat)
        self.assertIn("A direct `fetched` or `access_constraint` source", flat)
        self.assertIn("A fallback coverage row must match the submitted", flat)
        self.assertNotIn(
            "search/official-alternate result is verified by the fetcher's source manifest",
            flat,
        )


if __name__ == "__main__":
    unittest.main()
