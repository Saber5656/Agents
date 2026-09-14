import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import daily_it_news as news


class DailyNewsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.work = root / "runtime"
        self.work.mkdir()
        self.cfg = news.Config(self.work, self.work, root / "archive", root / "agents",
                               Path(__file__).parents[1] / "skills", root / "vault", "codex")
        self.date = news.dt.date(2026, 9, 10)
        self.target = self.cfg.archive_root / "2026/09/10"
        self.target.mkdir(parents=True)
        self.helper_calls = []
        self.real_loader = news.load_module

    def tearDown(self):
        for p in self.work.rglob("source-inputs"):
            p.chmod(0o755)
        self.temp.cleanup()

    def body(self):
        rows = "\n".join(f"| Site {i} | 1 | 対象期間記事なし | RSS | https://example{i}.com/news | 0 | checked |" for i in range(26))
        return f"""---
created: 2026-09-10
type: it-news-summary
---
# 今日の主要トピック
## ハイライト
1. 開発ツールの更新
## 個別トピック
### 更新 — 重要度: 小（重複: 1サイト）
更新内容を確認した。
- 出典: [公式](https://example.com/news)
- 公開日: 2026-09-10
## 総括
公開された更新を確認する。
## 注目キーワード
- 開発ツール
## 確認済みサイト一覧
{news.HEADER}
|---|---:|---|---|---|---:|---|
{rows}
"""

    def write_summary(self, text=None):
        path = self.target / "SUMMARY-IT-NEWS-2026-09-10.md"
        path.write_text(self.body() if text is None else text)
        return path

    def execute(self, argv, **kwargs):
        if "--verify-resolutions" in argv:
            Path(argv[-1]).write_text('{"resolutions":[],"date_evidence":[]}')
        elif str(argv[1]).endswith("collect-public-sources.py"):
            dest = Path(argv[3]); dest.mkdir(parents=True)
            rows = [{"name": f"Site {i}", "tier": 1, "status": "fetched", "method": "rss",
                     "jst_window_item_count": 0, "final_url": f"https://example{i}.com/news"} for i in range(26)]
            (dest / "source-manifest.json").write_text(json.dumps({"source_count": 26, "sources": rows}))
        else:
            staging = Path(kwargs["cwd"])
            out = staging / "SUMMARY-IT-NEWS-2026-09-10.md"
            out.write_text(self.body())
            (staging / "source-resolutions.json").write_text('{"version":1,"resolutions":[],"date_evidence":[]}')
            return subprocess.CompletedProcess(argv, 0, json.dumps({"summary_status": "created", "summary_path": str(out)}), "")
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    def helper(self, path, name):
        if name == "news_coverage_validator":
            owner = self
            class Validator:
                def validate_source_coverage(self, *args):
                    owner.helper_calls.append(args)
            return Validator()
        return self.real_loader(path, name)

    def test_codex_command_uses_global_search_and_safe_exec_options(self):
        command = news.codex_command(self.cfg, "prompt")
        self.assertLess(command.index("--search"), command.index("exec"))
        self.assertEqual(command[command.index("-a") + 1], "never")
        self.assertIn("--skip-git-repo-check", command)
        self.assertEqual(command.count("--sandbox"), 1)

    def test_config_fallback_and_empty_environment(self):
        env = {"IT_NEWS_RUNTIME_ROOT": str(self.work), "IT_NEWS_ARCHIVE_ROOT": str(self.cfg.archive_root),
               "AGENTS_ROOT": str(self.cfg.agents_root), "SKILLS_ROOT": str(self.cfg.skills_root),
               "AGENTS_VAULT_ROOT": str(self.cfg.vault_root), "CODEX_BIN": "codex"}
        self.assertEqual(news.Config.from_env(env).workdir, self.work)
        with self.assertRaises(news.RunnerError):
            news.Config.from_env({})

    def test_structure_rejects_each_corruption(self):
        self.assertTrue(news.valid_summary(self.write_summary(), self.date))
        mutations = [lambda s: s.replace("## 個別トピック", ""), lambda s: s.replace("対象期間記事なし", "INVALID"),
                     lambda s: s + "| EXTRA | 1 | 対象期間記事なし | RSS | https://example.com | 0 | x |\n",
                     lambda s: s.replace("https://example.com/news", "https://example.com/"),
                     lambda s: s.replace("Site 1 |", "Site 0 |"), lambda s: s.replace("created: 2026-09-10", "created: 2026-09-09")]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.assertFalse(news.valid_summary(self.write_summary(mutate(self.body())), self.date))

    def test_lock_excludes_concurrent_runs_and_releases_after_close(self):
        fd = news.acquire_lock(self.work)
        try:
            with self.assertRaises(news.RunnerError):
                news.acquire_lock(self.work)
        finally:
            os.close(fd)
        os.close(news.acquire_lock(self.work))

    def test_collection_failure_records_logs_and_releases_lock(self):
        result = news.run(self.cfg, today=self.date, runner=lambda *a, **k: subprocess.CompletedProcess([], 75, "out", "failure"))
        self.assertEqual(result["status"], "blocked")
        root = Path(result["run_root"])
        self.assertEqual((root / "collector.stdout.log").read_text(), "out")
        self.assertEqual((root / "collector.stderr.log").read_text(), "failure")
        os.close(news.acquire_lock(self.work))

    def test_save_preserves_incomplete_file_and_validated_retry_is_idempotent(self):
        old = self.write_summary("incomplete")
        self.assertIsNone(news.existing_summary(self.target, self.date))
        with patch.object(news, "load_module", side_effect=self.helper):
            result = news.run(self.cfg, today=self.date, runner=self.execute)
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(old.read_text(), "incomplete")
        self.assertTrue(Path(result["summary_path"]).name.endswith("-2.md"))
        self.assertEqual(len(self.helper_calls), 1)
        retry = news.run(self.cfg, today=self.date, runner=lambda *a, **k: self.fail("must not collect twice"))
        self.assertEqual(retry["status"], "already_complete")
        Path(result["summary_path"]).write_text("corrupted")
        self.assertIsNone(news.existing_summary(self.target, self.date))

    def test_format_error_gets_one_bounded_repair(self):
        model_calls = []
        def execute(argv, **kwargs):
            result = self.execute(argv, **kwargs)
            if "exec" in argv:
                model_calls.append(argv)
                if len(model_calls) == 1:
                    info = json.loads(result.stdout)
                    Path(info["summary_path"]).write_text("missing required headings")
            return result
        with patch.object(news, "load_module", side_effect=self.helper):
            result = news.run(self.cfg, today=self.date, runner=execute)
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(len(model_calls), 2)
        self.assertTrue((Path(result["run_root"]) / "codex.validation-error.txt").exists())

    def test_audit_is_generated_from_evidence_without_changing_model_output(self):
        outputs = []
        def execute(argv, **kwargs):
            result = self.execute(argv, **kwargs)
            if "exec" in argv:
                path = Path(json.loads(result.stdout)["summary_path"])
                path.write_text(self.body().replace(
                    "| RSS | https://example0.com/news | 0 | checked |",
                    "| 公開ページ | https://example0.com/representative-article | 9 | guessed |"))
                outputs.append(path)
            return result
        with patch.object(news, "load_module", side_effect=self.helper):
            result = news.run(self.cfg, today=self.date, runner=execute)
        self.assertEqual(result["status"], "complete", result)
        saved = Path(result["summary_path"]).read_text()
        self.assertIn("| Site 0 | 1 | 対象期間記事なし | RSS | https://example0.com/news | 0 |", saved)
        self.assertIn("representative-article", outputs[0].read_text())
        self.assertNotIn("representative-article", saved)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(len(self.helper_calls), 1)

    def test_verified_fallback_and_constraints_define_audit(self):
        manifest = {"sources": [
            {"name": "Blocked", "tier": 1, "status": "access_constraint", "method": "public_page",
             "final_url": "https://example.com/", "constraint": "robots"},
            {"name": "Fallback", "tier": 2, "status": "needs_search_fallback"},
            {"name": "Captcha", "tier": 2, "status": "needs_search_fallback"},
        ]}
        verified = {"resolutions": [
            {"name": "Fallback", "status": "verified_fallback", "method": "site_search",
             "requested_url": "https://example.com/article", "published_dates": ["2026-09-03T15:00:00Z", "2026-09-10T15:00:00Z"]},
            {"name": "Captcha", "status": "verified_access_constraint",
             "requested_url": "https://example.com/challenge", "constraint": "captcha"},
        ]}
        parse_date = lambda value: news.dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(news.JST).date()
        before = "News body\n## 確認済みサイト一覧\nold audit\n## Appendix\nKeep me\n"
        rendered = news.render_source_coverage(before, manifest, verified, self.date, parse_date)
        self.assertTrue(rendered.startswith("News body\n"))
        self.assertTrue(rendered.endswith("## Appendix\nKeep me\n"))
        self.assertIn("| Blocked | 1 | アクセス制約 | 公開ページ | https://example.com/ | 0 | robots.txt", rendered)
        self.assertIn("| Fallback | 2 | 取得済み | サイト限定検索 | https://example.com/article | 1 |", rendered)
        self.assertIn("| Captcha | 2 | アクセス制約 | 公開ページ | https://example.com/challenge | 0 | CAPTCHA", rendered)
        with self.assertRaises(news.RunnerError):
            news.render_source_coverage(before, manifest, {"resolutions": []}, self.date, parse_date)
        verified["resolutions"][0]["published_dates"] = ["bad-date"]
        with self.assertRaises((news.RunnerError, ValueError)):
            news.render_source_coverage(before, manifest, verified, self.date, parse_date)

    def test_real_validator_accepts_projection_but_rejects_corrupt_evidence(self):
        validator = self.real_loader(self.cfg.skills_root /
            "vault-change-publisher/scripts/validate-collection-result.py", "test_real_coverage_validator")
        catalog_path = self.work / "catalog.json"
        catalog_path.write_text(json.dumps({"version": 1, "sources": [
            {"name": "Example", "tier": 1, "feed_url": "https://example.com/feed/",
             "page_url": "https://example.com/"}]}))
        manifest = {"catalog_sha256": news.digest(catalog_path), "sources": [
            {"name": "Example", "tier": 1, "status": "fetched", "method": "rss",
             "final_url": "https://example.com/feed/", "extracted_entry_count": 1,
             "extract_file": "extract.json", "jst_window_item_count": 1,
             "jst_window_start": "2026-09-04", "jst_window_end": "2026-09-10"}]}
        manifest_path = self.work / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        extract_path = self.work / "extract.json"
        extract_path.write_text(json.dumps({"format": "feed", "entries": [
            {"url": "https://example.com/article", "published": "2026-09-04"}]}))
        verified = {"version": 1, "resolutions": [], "date_evidence": []}
        verified_path = self.work / "verified.json"
        verified_path.write_text(json.dumps(verified))
        raw = "## 確認済みサイト一覧\n" + news.HEADER + "\n|---|---:|---|---|---|---:|---|\n" + \
              "| Example | 1 | 取得済み | RSS | https://example.com/article | 1 | representative |\n"
        validate = lambda body: validator.validate_source_coverage(
            body, catalog_path, manifest_path, verified_path, self.date)
        with self.assertRaisesRegex(validator.ValidationError, "does not match fetch evidence"):
            validate(raw)
        normalized = news.render_source_coverage(raw, manifest, verified, self.date, validator.parse_publication_date)
        validate(normalized)
        manifest["sources"][0]["jst_window_item_count"] = 2
        manifest_path.write_text(json.dumps(manifest))
        normalized = news.render_source_coverage(raw, manifest, verified, self.date, validator.parse_publication_date)
        with self.assertRaisesRegex(validator.ValidationError, "manifest JST window count is invalid"):
            validate(normalized)
        manifest["sources"][0]["jst_window_item_count"] = 1
        manifest_path.write_text(json.dumps(manifest))
        extract_path.write_text(json.dumps({"format": "feed", "entries": [
            {"url": "https://example.com/article", "published": "undated"}]}))
        with self.assertRaisesRegex(validator.ValidationError, "lacks publication-date evidence"):
            validate(normalized)

    def test_run_uses_real_validator_for_verified_fallback_dates(self):
        validator_path = self.cfg.skills_root / "vault-change-publisher/scripts/validate-collection-result.py"
        (self.work / "validate-collection-result.py").write_bytes(validator_path.read_bytes())
        def execute(argv, **kwargs):
            result = self.execute(argv, **kwargs)
            if "--verify-resolutions" in argv:
                Path(argv[-1]).write_text(json.dumps({"version": 1, "date_evidence": [], "resolutions": [
                    {"name": "Site 0", "status": "verified_fallback", "method": "site_search",
                     "requested_url": "https://example0.com/article", "final_url": "https://example0.com/article",
                     "extracted_entry_count": 2, "candidate_entry_count": 2, "date_evidence_count": 2,
                     "published_dates": ["2026-09-03T15:00:00z", "2026-09-10T15:00:00Z"],
                     "candidate_evidence": [
                         {"url": "https://example0.com/article", "provenance": "html_meta", "published": "2026-09-03T15:00:00z"},
                         {"url": "https://example0.com/next", "provenance": "html_meta", "published": "2026-09-10T15:00:00Z"}]}]}))
            elif str(argv[1]).endswith("collect-public-sources.py"):
                dest = Path(argv[3])
                manifest_path = dest / "source-manifest.json"
                manifest = json.loads(manifest_path.read_text())
                catalog = {"version": 1, "sources": [
                    {"name": f"Site {i}", "tier": 1, "feed_url": f"https://example{i}.com/news",
                     "page_url": f"https://example{i}.com/"} for i in range(26)]}
                catalog_path = self.work / "it-news-sources.json"
                catalog_path.write_text(json.dumps(catalog))
                manifest["catalog_sha256"] = news.digest(catalog_path)
                manifest["sources"][0] = {"name": "Site 0", "tier": 1, "status": "needs_search_fallback"}
                for i, source in enumerate(manifest["sources"][1:], 1):
                    source.update(extract_file=f"{i}.json", extracted_entry_count=1,
                                  jst_window_start="2026-09-04", jst_window_end="2026-09-10")
                    (dest / f"{i}.json").write_text(json.dumps({"format": "feed", "entries": [
                        {"url": f"https://example{i}.com/article", "published": "2026-09-01"}]}))
                manifest_path.write_text(json.dumps(manifest))
            return result
        result = news.run(self.cfg, today=self.date, runner=execute)
        self.assertEqual(result["status"], "complete", result)
        self.assertIn("| Site 0 | 1 | 取得済み | サイト限定検索 | https://example0.com/article | 1 |",
                      Path(result["summary_path"]).read_text())

    def test_malformed_model_output_gets_one_bounded_repair_instead_of_stopping(self):
        model_calls = []
        def execute(argv, **kwargs):
            if "exec" in argv:
                model_calls.append(argv)
                if len(model_calls) == 1:
                    return subprocess.CompletedProcess(argv, 0, "not-json-and-no-summary_status", "")
            return self.execute(argv, **kwargs)
        with patch.object(news, "load_module", side_effect=self.helper):
            result = news.run(self.cfg, today=self.date, runner=execute)
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(len(model_calls), 2)
        self.assertTrue((Path(result["run_root"]) / "codex.validation-error.txt").exists())

    def test_transient_empty_receipt_read_reuses_summary_without_regenerating(self):
        with patch.object(news, "load_module", side_effect=self.helper):
            result = news.run(self.cfg, today=self.date, runner=self.execute)
        self.assertEqual(result["status"], "complete", result)
        calls = []
        original_reader = news.runtime._read_once
        def flaky_reader(path):
            calls.append(path)
            if len(calls) == 1:
                return b""
            return original_reader(path)
        with patch.object(news.runtime, "_read_once", side_effect=flaky_reader):
            retry = news.run(self.cfg, today=self.date, runner=lambda *a, **k: self.fail("must not collect twice"))
        self.assertEqual(retry["status"], "already_complete")

    def test_resume_rejects_paths_and_previous_dates_without_leaking_lock(self):
        for value in ["../outside", "20260909T040000+0900-1-1"]:
            with self.assertRaises(news.RunnerError):
                news.run(self.cfg, today=self.date, resume_run=value)
            os.close(news.acquire_lock(self.work))

    def test_resume_does_not_overwrite_completed_run_status(self):
        run_id = "20260910T040000+0900-1-1"
        root = self.work / "logs/2026-09-10" / run_id
        root.mkdir(parents=True)
        state = root / "status.json"
        original = '{"status":"complete"}'
        state.write_text(original)
        with self.assertRaises(news.RunnerError):
            news.run(self.cfg, today=self.date, resume_run=run_id)
        self.assertEqual(state.read_text(), original)
        os.close(news.acquire_lock(self.work))

    def test_independent_evidence_rejection_prevents_save(self):
        def reject(path, name):
            if name == "news_coverage_validator":
                class Validator:
                    def validate_source_coverage(self, *args):
                        raise ValueError("fallback item count does not match date evidence")
                return Validator()
            return self.real_loader(path, name)
        with patch.object(news, "load_module", side_effect=reject):
            result = news.run(self.cfg, today=self.date, runner=self.execute)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("count", result["error"])
        self.assertEqual(list(self.target.glob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
