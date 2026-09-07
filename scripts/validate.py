#!/usr/bin/env python3
"""Run the repository's reproducible tests and scoped portfolio audit.

This entrypoint intentionally requires the declared development dependency
(`pytest`). A missing dependency or an incomplete portfolio snapshot is a
failure; neither is silently converted into a pass.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=root, text=True, capture_output=True)


def test_summary(output: str) -> dict[str, int]:
    summary = {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0}
    for value, label in re.findall(r"(\d+) (passed|failed|errors?|skipped|xfailed)", output):
        key = "errors" if label.startswith("error") else label
        summary[key] = int(value)
    collected = re.search(r"(\d+) tests? collected", output)
    if collected:
        summary["collected"] = int(collected.group(1))
    if not summary["collected"]:
        summary["collected"] = sum(summary[key] for key in ("passed", "failed", "errors", "skipped", "xfailed"))
    return summary


def portfolio_manifest(root: Path, revision: str, path: Path) -> None:
    path.write_text(json.dumps({
        "audit_id": "validate-local",
        "repository_root": str(root),
        "revision_sha": revision,
        "scope": {"include": ["skills/*"], "exclude": []},
        "profile_source": {"default": "repo_native"},
        "mode": "full",
        "fail_policy": "report_only",
        "previous_report": None,
    }, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--skip-portfolio", action="store_true", help="run tests only")
    args = parser.parse_args()
    root = args.root.resolve()

    dependency = run([sys.executable, "-c", "import pytest"], root)
    if dependency.returncode:
        print("validation error: pytest is missing; install requirements-dev.txt first", file=sys.stderr)
        return 2

    test_paths = [path for path in (root / "tests", root / "skills") if path.exists()]
    if not test_paths:
        print("validation error: no test roots found", file=sys.stderr)
        return 2
    command = [sys.executable, "-m", "pytest", "-q", "-rs", *[str(path) for path in test_paths]]
    tests = run(command, root)
    test_output = tests.stdout + tests.stderr
    counts = test_summary(test_output)
    print(f"tests: discovered={counts['collected']} passed={counts['passed']} failed={counts['failed']} errors={counts['errors']} skipped={counts['skipped']} xfailed={counts['xfailed']}")
    for line in test_output.splitlines():
        if line.lstrip().startswith("SKIPPED"):
            print(f"tests: {line.strip()}")
    if tests.returncode:
        print(test_output, file=sys.stderr, end="")
        return tests.returncode
    if counts["collected"] == 0:
        print("validation error: pytest discovered no tests", file=sys.stderr)
        return 2

    if args.skip_portfolio:
        print("portfolio: skipped by explicit option")
        return 0

    revision_result = run(["git", "rev-parse", "HEAD"], root)
    revision = revision_result.stdout.strip()
    if revision_result.returncode or not re.fullmatch(r"[0-9a-f]{40}", revision):
        print("validation error: cannot resolve a full Git HEAD revision", file=sys.stderr)
        return 2

    scanner = root / "skills/skill-manager/scripts/scan_skill_portfolio.py"
    if not scanner.is_file():
        print(f"validation error: referenced scanner is missing: {scanner}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="agents-validate-") as temp:
        evidence = Path(temp)
        manifest = evidence / "manifest.json"
        report_json = evidence / "portfolio-audit.json"
        report_md = evidence / "portfolio-audit.md"
        portfolio_manifest(root, revision, manifest)
        result = run([sys.executable, str(scanner), "--manifest", str(manifest), "--json", str(report_json), "--markdown", str(report_md)], root)
        if not report_json.is_file() or not report_md.is_file():
            print("validation error: portfolio scanner did not produce both evidence files", file=sys.stderr)
            print(result.stderr, file=sys.stderr, end="")
            return 2
        report = json.loads(report_json.read_text(encoding="utf-8"))
        audit = report.get("audit", {})
        summary = report.get("summary", {})
        metrics = summary.get("metrics", {})
        print(
            "portfolio: status={status} skills={skills} findings={findings} blockers={blockers} "
            "compliance={compliance} scope_digest={digest}".format(
                status=audit.get("status", "missing"),
                skills=metrics.get("skill_count", len(report.get("inventory", []))),
                findings=len(report.get("findings", [])),
                blockers=metrics.get("blocker_count", "unknown"),
                compliance=summary.get("compliance_status", "missing"),
                digest=audit.get("scope_digest", "missing"),
            )
        )
        if result.returncode == 2 or audit.get("status") != "complete":
            print(f"portfolio error: {audit.get('incomplete_reason', result.stderr.strip() or 'audit incomplete')}", file=sys.stderr)
            return 2
        if result.returncode not in (0, 1):
            print(result.stderr, file=sys.stderr, end="")
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
