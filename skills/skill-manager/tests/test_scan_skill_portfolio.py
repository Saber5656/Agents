import importlib.util
import json
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "scan_skill_portfolio.py"
SPEC = importlib.util.spec_from_file_location("portfolio_scanner", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

FRONTMATTER = """---
name: {name}
description: Test skill
user-invocable: true
allowed-tools: Read, Grep
category: Dev
created: 2026-07-16
status: active
purpose: One responsibility
---
# Skill
"""


class PortfolioScannerTests(unittest.TestCase):
    def make_skill(self, root, path, name=None, evals=True, status="active", category="Dev"):
        directory = root / path
        directory.mkdir()
        text = FRONTMATTER.format(name=name or path).replace("status: active", f"status: {status}").replace("category: Dev", f"category: {category}")
        (directory / "SKILL.md").write_text(text)
        if evals:
            (directory / "evals").mkdir()
            (directory / "evals" / "evals.json").write_text(json.dumps({
                "skill_name": name or path,
                "evals": [{"id": 1, "prompt": "x", "expected_output": "y", "expectations": ["z"]}],
            }))

    def test_clean_skill_is_inventoried(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            report = MODULE.scan(root, "audit-1", "repo_native")
            self.assertEqual(1, len(report["inventory"]))
            self.assertTrue(report["summary"]["hard_gate_pass"])

    def test_name_path_mismatch_is_blocker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha", "beta")
            report = MODULE.scan(root, "audit-1", "repo_native")
            self.assertTrue(any(item["rule_id"] == "name_path_mismatch" for item in report["findings"]))
            self.assertFalse(report["summary"]["hard_gate_pass"])

    def test_invalid_eval_json_is_blocker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha", evals=False)
            (root / "alpha" / "evals").mkdir()
            (root / "alpha" / "evals" / "evals.json").write_text("{")
            report = MODULE.scan(root, "audit-1", "repo_native")
            self.assertTrue(any(item["rule_id"] == "invalid_eval_json" for item in report["findings"]))

    def test_scan_is_read_only_for_skill_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            before = (root / "alpha" / "SKILL.md").read_bytes()
            MODULE.scan(root, "audit-1", "repo_native")
            self.assertEqual(before, (root / "alpha" / "SKILL.md").read_bytes())

    def test_unclassified_profile_reports_uncertainty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            report = MODULE.scan(root, "audit-1", "unclassified")
            self.assertTrue(any(item["rule_id"] == "provenance_unclassified" for item in report["findings"]))

    def test_invalid_category_and_missing_fixture_are_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha", category="Wrong")
            payload = json.loads((root / "alpha/evals/evals.json").read_text())
            payload["evals"][0]["files"] = ["evals/files/missing.txt"]
            (root / "alpha/evals/evals.json").write_text(json.dumps(payload))
            rules = {item["rule_id"] for item in MODULE.scan(root, "audit-1", "repo_native")["findings"]}
            self.assertTrue({"invalid_category", "missing_eval_fixture"} <= rules)

    def test_deprecated_duplicate_name_is_not_active_name_blocker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha", name="same")
            self.make_skill(root, "beta", name="same", status="deprecated")
            rules = {item["rule_id"] for item in MODULE.scan(root, "audit-1", "repo_native")["findings"]}
            self.assertNotIn("duplicate_skill_name", rules)

    def test_scope_digest_covers_references(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            (root / "alpha/references").mkdir()
            ref = root / "alpha/references/x.md"
            ref.write_text("one")
            first = MODULE.scan(root, "audit-1", "repo_native")["audit"]["scope_digest"]
            ref.write_text("two")
            second = MODULE.scan(root, "audit-1", "repo_native")["audit"]["scope_digest"]
            self.assertNotEqual(first, second)

    def test_previous_report_classifies_new_existing_and_improved(self):
        current = {"audit": {"previous_audit_id": None}, "findings": [
            {"finding_id": "SM-new", "baseline_state": "unverified"},
            {"finding_id": "SM-same", "baseline_state": "unverified"},
        ], "summary": {"metrics": {}}}
        previous = {"audit": {"id": "old"}, "findings": [{"finding_id": "SM-same"}, {"finding_id": "SM-gone"}]}
        MODULE.classify_baseline(current, previous)
        self.assertEqual(["new", "existing"], [item["baseline_state"] for item in current["findings"]])
        self.assertEqual(1, current["summary"]["metrics"]["improved_count"])

    def test_output_paths_inside_root_or_same_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with self.assertRaises(ValueError):
                MODULE.verify_output_paths(root, [root / "report.json", Path("/tmp/report.md")])
            with self.assertRaises(ValueError):
                MODULE.verify_output_paths(root, [Path("/tmp/report.json"), Path("/tmp/report.json")])

    def test_snapshot_rejects_dirty_audited_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            self.make_skill(root, "alpha")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
            revision = MODULE.git_revision(root)
            MODULE.verify_snapshot(root, revision, [root / "alpha"])
            (root / "alpha/SKILL.md").write_text("dirty")
            with self.assertRaises(ValueError):
                MODULE.verify_snapshot(root, revision, [root / "alpha"])

    def test_per_skill_profile_controls_required_metadata_severity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            text = (root / "alpha/SKILL.md").read_text().replace("purpose: One responsibility\n", "")
            (root / "alpha/SKILL.md").write_text(text)
            report = MODULE.scan(root, "audit-1", "upstream_compatible", profiles={"alpha": "repo_native"})
            finding = next(item for item in report["findings"] if item["rule_id"] == "required_metadata")
            self.assertEqual("high", finding["severity"])

    def test_verified_benchmark_rate_uses_contract_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            digest = MODULE.digest_bytes((root / "alpha/SKILL.md").read_bytes())
            benchmark = root / "alpha/benchmarks/run/benchmark.json"
            benchmark.parent.mkdir(parents=True)
            benchmark.write_text(json.dumps({"metadata": {"evaluated_contract_digest": digest}}))
            report = MODULE.scan(root, "audit-1", "repo_native")
            self.assertEqual(1.0, report["summary"]["metrics"]["benchmark_verified_rate"])

    def test_initial_manifest_requires_report_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            path.write_text(json.dumps({
                "audit_id": "a", "repository_root": "/repo", "revision_sha": "a" * 40,
                "scope": {"include": [], "exclude": []}, "profile_source": {"default": "repo_native"},
                "mode": "full", "fail_policy": "new_blockers", "previous_report": None,
            }))
            with self.assertRaises(ValueError):
                MODULE.load_manifest(path)

    def test_ignored_local_file_does_not_change_revision_scope_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            self.make_skill(root, "alpha")
            (root / ".gitignore").write_text("*.local\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
            revision = MODULE.git_revision(root)
            directory = root / "alpha"
            before = MODULE.scoped_digest([directory], root, revision)
            (directory / "cache.local").write_text("machine one")
            after = MODULE.scoped_digest([directory], root, revision)
            (directory / "cache.local").write_text("machine two")
            final = MODULE.scoped_digest([directory], root, revision)
            self.assertEqual(before, after)
            self.assertEqual(after, final)

    def test_rejects_symlinked_skill_directory_and_nested_file(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            self.make_skill(Path(outside), "external")
            (root / "external").symlink_to(Path(outside) / "external", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked skill boundary"):
                MODULE.scan(root, "audit-1", "repo_native")
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            self.make_skill(root, "alpha")
            (Path(outside) / "secret.txt").write_text("outside")
            (root / "alpha" / "references").mkdir()
            (root / "alpha" / "references" / "secret.txt").symlink_to(Path(outside) / "secret.txt")
            with self.assertRaisesRegex(ValueError, "symlink inside audited skill"):
                MODULE.scan(root, "audit-1", "repo_native")

    def test_reports_local_reference_escape_without_reading_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            with (root / "alpha" / "SKILL.md").open("a") as handle:
                handle.write("\n[escape](../../outside.md)\n")
            rules = {item["rule_id"] for item in MODULE.scan(root, "audit-1", "repo_native")["findings"]}
            self.assertIn("reference_scope_escape", rules)

    def test_rejects_absolute_and_parent_eval_fixtures(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            outside = root / "outside.txt"
            outside.write_text("exists")
            payload_path = root / "alpha/evals/evals.json"
            payload = json.loads(payload_path.read_text())
            payload["evals"][0]["files"] = [str(outside), "../../outside.txt"]
            payload_path.write_text(json.dumps(payload))
            rules = [item["rule_id"] for item in MODULE.scan(root, "audit-1", "repo_native")["findings"]]
            self.assertEqual(2, rules.count("fixture_scope_escape"))

    def test_excluded_skill_is_not_traversed(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            self.make_skill(root, "included")
            self.make_skill(root, "excluded")
            (Path(outside) / "secret.txt").write_text("outside")
            (root / "excluded" / "secret.txt").symlink_to(Path(outside) / "secret.txt")
            report = MODULE.scan(root, "audit-1", "repo_native", exclude=["excluded"])
            self.assertEqual(["included"], [item["path"] for item in report["inventory"]])

    def test_excluded_top_level_symlink_is_not_statted(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            self.make_skill(root, "included")
            self.make_skill(Path(outside), "external")
            excluded = root / "excluded"
            excluded.symlink_to(Path(outside) / "external", target_is_directory=True)
            original_is_dir = Path.is_dir

            def guarded_is_dir(path):
                if path == excluded:
                    raise AssertionError("excluded symlink was dereferenced")
                return original_is_dir(path)

            with patch.object(Path, "is_dir", guarded_is_dir):
                report = MODULE.scan(root, "audit-1", "repo_native", exclude=["excluded"])
            self.assertEqual(["included"], [item["path"] for item in report["inventory"]])

    def test_snapshot_rejects_deleted_revision_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            self.make_skill(root, "alpha")
            self.make_skill(root, "beta")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
            revision = MODULE.git_revision(root)
            for path in (root / "beta").rglob("*"):
                if path.is_file():
                    path.unlink()
            (root / "beta/evals").rmdir()
            (root / "beta").rmdir()
            with self.assertRaisesRegex(ValueError, "audited scope is not clean"):
                MODULE.verify_snapshot(root, revision, [root / "alpha"], [], [])

    def test_finding_categories_and_duplicate_paths_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha", name="same")
            self.make_skill(root, "beta", name="same")
            report = MODULE.scan(root, "audit-1", "unclassified")
            provenance = next(item for item in report["findings"] if item["rule_id"] == "provenance_unclassified")
            duplicate = next(item for item in report["findings"] if item["rule_id"] == "duplicate_skill_name")
            self.assertEqual("provenance", provenance["category"])
            self.assertEqual("duplicate", duplicate["category"])
            self.assertEqual(["alpha", "beta"], duplicate["skills"])

    def test_blank_required_metadata_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            skill = root / "alpha/SKILL.md"
            skill.write_text(skill.read_text().replace("description: Test skill", "description:"))
            finding = next(item for item in MODULE.scan(root, "audit-1", "repo_native")["findings"] if item["rule_id"] == "required_metadata")
            self.assertIn("description", finding["evidence"][0])

    def test_reference_scan_ignores_fences_and_flags_absolute_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            skill = root / "alpha/SKILL.md"
            with skill.open("a") as handle:
                handle.write("\n```md\n[example](Missing.md)\n```\n[absolute](/tmp/secret.md)\n")
            findings = MODULE.scan(root, "audit-1", "repo_native")["findings"]
            escaped = [item for item in findings if item["rule_id"] == "reference_scope_escape"]
            self.assertEqual(1, len(escaped))
            self.assertFalse(any(item["rule_id"] == "broken_local_reference" and "Missing.md" in item["evidence"] for item in findings))

    def test_list_allowed_tools_and_invalid_eval_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            skill = root / "alpha/SKILL.md"
            skill.write_text(skill.read_text().replace("allowed-tools: Read, Grep", "allowed-tools: [Read, Write]") + "\nRead-only audit.\n")
            eval_path = root / "alpha/evals/evals.json"
            eval_path.write_text(json.dumps({"skill_name": "alpha", "evals": {"bad": True}}))
            report = MODULE.scan(root, "audit-1", "repo_native")
            rules = {item["rule_id"] for item in report["findings"]}
            self.assertTrue({"read_only_tool_mismatch", "invalid_eval_schema"} <= rules)
            self.assertEqual("invalid", report["inventory"][0]["eval_status"])

    def test_identical_findings_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root, "alpha")
            with (root / "alpha/SKILL.md").open("a") as handle:
                handle.write("\n[one](missing.md)\n[one](missing.md)\n")
            findings = [item for item in MODULE.scan(root, "audit-1", "repo_native")["findings"] if item["rule_id"] == "broken_local_reference"]
            self.assertEqual(1, len(findings))


if __name__ == "__main__":
    unittest.main()
