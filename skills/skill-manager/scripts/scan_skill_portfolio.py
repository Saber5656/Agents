#!/usr/bin/env python3
"""Deterministic read-only inventory and hard checks for a skill repository."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PORTABLE_REQUIRED = {"name", "description"}
REPO_REQUIRED = {"name", "description", "user-invocable", "allowed-tools", "category", "created", "status", "purpose"}
VALID_STATUS = {"active", "draft", "deprecated"}
VALID_CATEGORY = {"Dev", "News-Data", "Obsidian", "Operation", "Review", "Security", "Utility"}
LINK_RE = re.compile(r"\[[^\]]+\]\((?!#)([^)]+)\)")
RULE_CATEGORIES = {
    "frontmatter_unparsed": "contract",
    "provenance_unclassified": "provenance",
    "stale_benchmark": "quality", "invalid_benchmark_json": "quality",
    "invalid_eval_json": "quality", "invalid_eval_schema": "quality",
    "eval_skill_mismatch": "quality", "duplicate_eval_id": "quality",
    "missing_eval_fixture": "quality", "fixture_scope_escape": "quality",
    "duplicate_skill_name": "duplicate",
    "audit_incomplete": "contract",
}

BLOCK_HEADER_RE = re.compile(r"^([>|])(?:(\d)?([+-])?|([+-])(\d)?)$")


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _block_header(value: str) -> tuple[str, str, int | None] | None:
    match = BLOCK_HEADER_RE.fullmatch(value)
    if not match:
        return None
    style = match.group(1)
    indent = match.group(2) or match.group(5)
    chomp = match.group(3) or match.group(4) or ""
    return style, chomp, int(indent) if indent else None


def _block_scalar(
    rows: list[str], start: int, style: str, chomp: str, explicit_indent: int | None
) -> tuple[str, int]:
    body: list[str] = []
    index = start
    while index < len(rows):
        row = rows[index]
        if not row.strip() or row[:1].isspace():
            body.append(row)
            index += 1
            continue
        break
    if not body or not any(row.strip() for row in body):
        return "", index
    nonblank = [len(row) - len(row.lstrip(" ")) for row in body if row.strip()]
    indent = explicit_indent or (min(nonblank) if nonblank else 0)
    lines = [row[indent:] if row.strip() else "" for row in body]
    if style == "|":
        value = "\n".join(lines)
    else:
        folded: list[str] = []
        for line_index, line in enumerate(lines):
            if line_index and lines[line_index - 1] == "":
                folded.append("\n")
            elif line_index:
                folded.append(" ")
            folded.append(line)
        value = "".join(folded)
    if chomp == "-":
        value = value.rstrip("\n")
    elif chomp != "+":
        value = value.rstrip("\n") + "\n"
    else:
        value += "\n"
    return value, index


def _scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
        return decoded if isinstance(decoded, str) else value[1:-1]
    return value


def frontmatter(text: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if not text.startswith("---\n"):
        return {}, ["missing YAML frontmatter"]
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, ["unterminated YAML frontmatter"]
    rows = text[4:end].splitlines()
    data: dict[str, Any] = {}
    index = 0
    while index < len(rows):
        row = rows[index]
        if ":" not in row:
            errors.append(f"unparsed frontmatter line: {row}")
            index += 1
            continue
        key, value = row.split(":", 1)
        key, value = key.strip(), value.strip()
        block = _block_header(value) if value[:1] in {">", "|"} else None
        if block:
            data[key], index = _block_scalar(rows, index + 1, *block)
            continue
        if value[:1] in {">", "|"}:
            errors.append(f"unparsed frontmatter value: {row}")
        if value.startswith("[") and value.endswith("]"):
            data[key] = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        elif value in {"true", "false"}:
            data[key] = value == "true"
        else:
            data[key] = _scalar(value)
        index += 1
    return data, errors


def git_revision(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unversioned"


def _relative(path: Path, root: Path) -> str:
    return Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root))).as_posix()


def _scope_match(path: Path, root: Path, patterns: list[str]) -> bool:
    relative = _relative(path, root)
    return any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def _check_skill_boundary(path: Path, root: Path) -> None:
    relative = _relative(path, root)
    if path.is_symlink():
        raise ValueError(f"symlinked skill boundary is forbidden: {relative}")
    skill_path = path / "SKILL.md"
    if skill_path.is_symlink():
        raise ValueError(f"symlinked skill boundary is forbidden: {_relative(skill_path, root)}")
    try:
        path.resolve().relative_to(root.resolve())
        skill_path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"skill path escapes repository root: {path}") from error
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in [*dirs, *files]:
            nested = Path(base) / name
            if nested.is_symlink():
                raise ValueError(f"symlink inside audited skill is forbidden: {_relative(nested, root)}")


def select_directories(root: Path, include: list[str], exclude: list[str]) -> list[Path]:
    """Select skill directories without traversing unselected symlink paths.

    Include patterns are repository-relative (for example ``skills/*``) and
    may also be a direct skill basename for compatibility with a skills-root
    invocation. With no include, only immediate children are considered.
    """
    root = Path(os.path.abspath(root))
    if include:
        candidates: list[Path] = []
        for pattern in include:
            candidates.extend(root.glob(pattern))
    else:
        candidates = list(root.iterdir())

    directories: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(candidates):
        relative = _relative(path, root)
        if path.name.startswith(".") or _scope_match(path, root, exclude):
            continue
        if path.is_symlink():
            explicit = any(not any(marker in pattern for marker in "*?[") and relative == pattern.rstrip("/") for pattern in include)
            if explicit or (path / "SKILL.md").is_file():
                raise ValueError(f"symlinked skill boundary is forbidden: {relative}")
            continue
        if not path.is_dir():
            continue

        children = [path]
        if not (path / "SKILL.md").exists() and include:
            # An explicit container such as ``skills`` selects its direct
            # skill children, while avoiding recursive discovery of unrelated
            # directories and symlink aliases.
            children = sorted(path.iterdir())
        for candidate in children:
            if candidate.name.startswith(".") or _scope_match(candidate, root, exclude):
                continue
            if candidate.is_symlink():
                candidate_relative = _relative(candidate, root)
                explicit = any(not any(marker in pattern for marker in "*?[") and candidate_relative == pattern.rstrip("/") for pattern in include)
                if explicit or (candidate / "SKILL.md").is_file():
                    raise ValueError(f"symlinked skill boundary is forbidden: {candidate_relative}")
                continue
            if not candidate.is_dir() or not (candidate / "SKILL.md").is_file():
                continue
            _check_skill_boundary(candidate, root)
            if candidate not in seen:
                seen.add(candidate)
                directories.append(candidate)
    return sorted(directories)


def scoped_digest(directories: list[Path], root: Path, revision_sha: str | None = None) -> str:
    parts: list[str] = []
    if revision_sha and revision_sha != "unversioned":
        relative_dirs = [_relative(directory, root) for directory in directories]
        completed = subprocess.run(
            ["git", "ls-tree", "-r", "-z", "--name-only", revision_sha, "--", *relative_dirs],
            cwd=root, capture_output=True, check=True,
        )
        relative_paths = [item.decode() for item in completed.stdout.split(b"\0") if item]
        for relative in sorted(relative_paths):
            blob = subprocess.run(
                ["git", "show", f"{revision_sha}:{relative}"], cwd=root,
                capture_output=True, check=True,
            ).stdout
            parts.append(f"{relative}:{digest_bytes(blob)}")
        return digest_bytes("\n".join(parts).encode())
    else:
        paths = [path for directory in directories for path in directory.rglob("*") if path.is_file()]
    for path in sorted(paths):
        parts.append(f"{_relative(path, root)}:{digest_bytes(path.read_bytes())}")
    return digest_bytes("\n".join(parts).encode())


def finding(rule: str, severity: str, skill: str | list[str], evidence: str, impact: str, confidence: str = "certain") -> dict[str, Any]:
    skills = [skill] if isinstance(skill, str) else sorted(set(skill))
    fid = digest_bytes(f"{rule}|{','.join(skills)}|{evidence}".encode())[:12]
    return {
        "finding_id": f"SM-{fid}", "rule_id": rule, "category": RULE_CATEGORIES.get(rule, "contract"),
        "severity": severity, "confidence": confidence, "skills": skills,
        "evidence": [evidence], "impact": impact,
        "proposed_disposition": "hand off for bounded correction",
        "handler": "skill-updater", "approval_required": True, "baseline_state": "unverified",
    }


def scan(root: Path, audit_id: str, profile: str, include: list[str] | None = None,
         exclude: list[str] | None = None, profiles: dict[str, str] | None = None,
         revision_sha: str | None = None) -> dict[str, Any]:
    inventory: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    names: dict[str, list[str]] = {}
    include, exclude, profiles = include or [], exclude or [], profiles or {}

    skill_dirs = select_directories(root, include, exclude)
    for directory in skill_dirs:
        path = directory / "SKILL.md"
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        metadata, parse_errors = frontmatter(text)
        skill_id = str(metadata.get("name") or directory.name)
        skill_profile = profiles.get(directory.name, profile)
        required = REPO_REQUIRED if skill_profile == "repo_native" else PORTABLE_REQUIRED
        if skill_profile == "unclassified":
            findings.append(finding(
                "provenance_unclassified", "medium", directory.name,
                "no approved provenance profile", "Applicable repository policy cannot be selected safely.",
            ))
        missing = sorted(key for key in required if not metadata.get(key))
        for error in parse_errors:
            if error.startswith("unparsed frontmatter"):
                findings.append(finding(
                    "frontmatter_unparsed", "medium", directory.name, error,
                    "Metadata syntax is outside the deterministic parser; skill validity remains unverified.",
                    "probable",
                ))
            else:
                findings.append(finding("frontmatter_parse", "blocker", directory.name, error, "Skill routing metadata cannot be trusted."))
        if missing:
            findings.append(finding("required_metadata", "high" if skill_profile == "repo_native" else "medium", directory.name, f"missing: {', '.join(missing)}", "Applicable skill contract is incomplete."))
        if metadata.get("name") and metadata.get("name") != directory.name:
            findings.append(finding("name_path_mismatch", "blocker", directory.name, f"name={metadata.get('name')} path={directory.name}", "Skill identity and routing path disagree."))
        if metadata.get("status") and metadata.get("status") not in VALID_STATUS:
            findings.append(finding("invalid_status", "high", directory.name, f"status={metadata.get('status')}", "Lifecycle state is not machine-readable."))
        if skill_profile == "repo_native" and metadata.get("category") and metadata.get("category") not in VALID_CATEGORY:
            findings.append(finding("invalid_category", "high", directory.name, f"category={metadata.get('category')}", "Repository category is outside the approved taxonomy."))
        if metadata.get("status") == "active":
            names.setdefault(skill_id, []).append(directory.name)
        if len(text.splitlines()) > 500:
            findings.append(finding("skill_md_too_large", "medium", directory.name, f"lines={len(text.splitlines())}", "Progressive disclosure is weakened."))
        instruction_text = re.sub(r"```.*?```|~~~.*?~~~", "", text, flags=re.DOTALL)
        for link in LINK_RE.findall(instruction_text):
            clean = link.strip("<>").split("#", 1)[0]
            if "://" in clean:
                continue
            if any(marker in clean for marker in ("...", "$", "{", "}")):
                continue
            if clean:
                if Path(clean).is_absolute():
                    findings.append(finding("reference_scope_escape", "high", directory.name, link, "A local reference escapes the audited skill boundary."))
                    continue
                linked = (directory / clean).resolve()
                try:
                    linked.relative_to(directory.resolve())
                except ValueError:
                    findings.append(finding("reference_scope_escape", "high", directory.name, link, "A local reference escapes the audited skill boundary."))
                    continue
                if not linked.exists():
                    findings.append(finding("broken_local_reference", "high", directory.name, link, "Referenced local instructions are unavailable."))

        eval_path = directory / "evals" / "evals.json"
        eval_status = "missing"
        eval_count = 0
        assertion_count = 0
        if eval_path.exists():
            try:
                payload = json.loads(eval_path.read_text(encoding="utf-8"))
                evals = payload.get("evals", [])
                if not isinstance(evals, list) or not all(isinstance(item, dict) for item in evals):
                    eval_status = "invalid"
                    findings.append(finding("invalid_eval_schema", "blocker", directory.name, "evals must be an array of objects", "Behavior evaluation coverage cannot be trusted."))
                    evals = []
                ids = [item.get("id") for item in evals if isinstance(item, dict)]
                eval_count = len(evals)
                assertion_count = sum(bool(item.get("expectations") or item.get("assertions")) for item in evals if isinstance(item, dict))
                if payload.get("skill_name") and payload.get("skill_name") != skill_id:
                    findings.append(finding("eval_skill_mismatch", "high", directory.name, f"skill_name={payload.get('skill_name')}", "Evals can be attributed to the wrong skill."))
                if len(ids) != len(set(map(str, ids))):
                    findings.append(finding("duplicate_eval_id", "high", directory.name, "eval IDs are not unique", "Eval results cannot be stably identified."))
                for item in evals:
                    if not isinstance(item, dict):
                        continue
                    for fixture in item.get("files", []) if isinstance(item.get("files", []), list) else []:
                        fixture_path = Path(str(fixture))
                        if fixture_path.is_absolute():
                            findings.append(finding("fixture_scope_escape", "high", directory.name, str(fixture), "An eval fixture escapes the audited skill boundary."))
                            continue
                        resolved_fixture = (directory / fixture_path).resolve()
                        try:
                            resolved_fixture.relative_to(directory.resolve())
                        except ValueError:
                            findings.append(finding("fixture_scope_escape", "high", directory.name, str(fixture), "An eval fixture escapes the audited skill boundary."))
                            continue
                        if not resolved_fixture.is_file():
                            findings.append(finding("missing_eval_fixture", "high", directory.name, str(fixture), "An eval cannot reproduce its declared input."))
                if eval_status != "invalid":
                    eval_status = "valid"
            except (OSError, json.JSONDecodeError) as exc:
                eval_status = "invalid"
                findings.append(finding("invalid_eval_json", "blocker", directory.name, str(exc), "Behavior evaluation cannot run."))

        benchmark_status = "unverified"
        benchmark_paths = sorted(directory.glob("benchmarks/**/benchmark.json"))
        for benchmark_path in benchmark_paths:
            try:
                benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
                evaluated = benchmark.get("metadata", {}).get("evaluated_contract_digest")
                if evaluated is None:
                    continue
                if evaluated != digest_bytes(raw):
                    benchmark_status = "stale"
                    findings.append(finding("stale_benchmark", "high", directory.name, str(benchmark_path.relative_to(directory)), "Benchmark evidence does not match the current skill contract."))
                else:
                    benchmark_status = "verified"
            except (OSError, json.JSONDecodeError) as exc:
                findings.append(finding("invalid_benchmark_json", "high", directory.name, str(exc), "Benchmark freshness cannot be verified."))

        tools = metadata.get("allowed-tools", "")
        tool_names = set(tools) if isinstance(tools, list) else {token.strip() for token in str(tools).split(",") if token.strip()}
        read_only_claim = "read-only" in text.lower() or "read only" in text.lower()
        if read_only_claim and tool_names.intersection({"Write", "Edit"}):
            findings.append(finding("read_only_tool_mismatch", "medium", directory.name, f"allowed-tools={tools}", "A wording-based signal suggests the declared tools may exceed the mutation boundary; this is a review candidate, not a compliance verdict.", "probable"))

        inventory.append({
            "skill_id": skill_id, "path": _relative(directory, root), "profile": skill_profile,
            "canonical_repository": str(root), "revision_sha": revision_sha or git_revision(root), "copy_type": "tracked_source",
            "status": metadata.get("status", "unknown"), "contract_digest": digest_bytes(raw),
            "responsibility_summary": metadata.get("purpose", ""),
            "trigger_summary": metadata.get("description", ""),
            "eval_status": eval_status, "eval_count": eval_count,
            "assertion_eval_count": assertion_count,
            "benchmark_status": benchmark_status,
        })

    for name, paths in names.items():
        if len(paths) > 1:
            findings.append(finding("duplicate_skill_name", "blocker", paths, f"name={name}; paths={paths}", "Skill identity is ambiguous."))

    findings = list({item["finding_id"]: item for item in findings}.values())

    blocker_count = sum(item["severity"] == "blocker" for item in findings)
    total = len(inventory)
    eval_covered = sum(item["eval_status"] == "valid" for item in inventory)
    assertion_covered = sum(item["assertion_eval_count"] > 0 for item in inventory)
    benchmark_verified = sum(item["benchmark_status"] == "verified" for item in inventory)
    uncertain = any(item["rule_id"] == "provenance_unclassified" for item in findings)
    heuristic = any(item["rule_id"] == "read_only_tool_mismatch" for item in findings)
    return {
        "audit": {
            "id": audit_id, "revision_sha": revision_sha or git_revision(root),
            "status": "complete",
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "scope_digest": scoped_digest(skill_dirs, root, revision_sha),
            "previous_audit_id": None,
        },
        "inventory": inventory,
        "findings": findings,
        "summary": {
            "status": "complete",
            "compliance_status": "unverified" if uncertain or heuristic else "deterministic_checks_only",
            "hard_gate_pass": blocker_count == 0,
            "metrics": {
                "skill_count": total,
                "blocker_count": blocker_count,
                "contract_valid_rate": (total - len({s for f in findings if f["severity"] in {"blocker", "high"} for s in f["skills"]})) / total if total else 1.0,
                "eval_coverage_rate": eval_covered / total if total else 1.0,
                "assertion_coverage_rate": assertion_covered / total if total else 1.0,
                "benchmark_verified_rate": benchmark_verified / total if total else 1.0,
            },
            "handoffs": [],
        },
    }


def classify_baseline(report: dict[str, Any], previous: dict[str, Any] | None) -> None:
    if previous is None:
        return
    old_ids = {item.get("finding_id") for item in previous.get("findings", []) if isinstance(item, dict)}
    current_ids = {item["finding_id"] for item in report["findings"]}
    for item in report["findings"]:
        item["baseline_state"] = "existing" if item["finding_id"] in old_ids else "new"
    report["audit"]["previous_audit_id"] = previous.get("audit", {}).get("id")
    report["summary"]["metrics"]["new_debt_count"] = sum(item["baseline_state"] == "new" for item in report["findings"])
    report["summary"]["metrics"]["improved_count"] = len(old_ids - current_ids)


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    required = {"audit_id", "repository_root", "revision_sha", "scope", "profile_source", "mode", "fail_policy"}
    missing = sorted(required - manifest.keys())
    if missing:
        raise ValueError(f"manifest missing: {', '.join(missing)}")
    if manifest["mode"] not in {"full", "changed"} or manifest["fail_policy"] not in {"report_only", "new_blockers"}:
        raise ValueError("invalid mode or fail_policy")
    if manifest["mode"] == "changed" and not manifest.get("previous_report"):
        raise ValueError("changed mode requires previous_report")
    if not manifest.get("previous_report") and manifest["fail_policy"] != "report_only":
        raise ValueError("an initial audit must use report_only")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest["revision_sha"])):
        raise ValueError("revision_sha must be a full lowercase SHA")
    return manifest


def revision_skill_paths(root: Path, revision_sha: str, include: list[str], exclude: list[str]) -> list[str]:
    """Return repository-relative selected skill directories at a revision."""
    completed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", revision_sha], cwd=root,
        capture_output=True, text=True, check=True,
    )
    names = {
        line.removesuffix("/SKILL.md")
        for line in completed.stdout.splitlines()
        if line.endswith("/SKILL.md")
    }
    selected: list[str] = []
    for name in sorted(names):
        path = Path(name)
        def matches(pattern: str) -> bool:
            normalized = pattern.rstrip("/")
            return (
                fnmatch.fnmatch(name, pattern)
                or (not any(marker in normalized for marker in "*?[") and name.startswith(normalized + "/"))
            )

        if include and not any(matches(pattern) for pattern in include):
            continue
        if any(matches(pattern) for pattern in exclude):
            continue
        selected.append(name)
    return selected


def verify_snapshot(root: Path, revision_sha: str, directories: list[Path], include: list[str] | None = None, exclude: list[str] | None = None) -> None:
    if git_revision(root) != revision_sha:
        raise ValueError("repository HEAD does not match revision_sha")
    current = sorted(_relative(path, root) for path in directories)
    expected = revision_skill_paths(root, revision_sha, include or [], exclude or [])
    if current != expected:
        raise ValueError("audited scope is incomplete at revision_sha: selected skill set differs")
    paths = sorted(set(current))
    if not paths:
        raise ValueError("audited scope is incomplete at revision_sha: no tracked skills")
    completed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", *paths], cwd=root, capture_output=True, text=True)
    if completed.returncode or completed.stdout.strip():
        raise ValueError("audited scope is not clean at revision_sha")


def verify_output_paths(root: Path, paths: list[Path]) -> None:
    resolved = [path.resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("evidence output paths must be distinct")
    if any(path == root or root in path.parents for path in resolved):
        raise ValueError("evidence outputs must be outside the audited repository")


def incomplete_report(manifest: dict[str, Any], root: Path | None, directories: list[Path], reason: str) -> dict[str, Any]:
    """Create an explicit incomplete result when snapshot identity is unusable."""
    audit_root = root or Path(str(manifest.get("repository_root", "unavailable")))
    try:
        scope_digest = scoped_digest(directories, audit_root)
    except (OSError, ValueError):
        scope_digest = "0" * 64
    selected = [_relative(path, audit_root) for path in directories if root is not None]
    skills = [Path(path).name for path in selected] or ["<scope>"]
    report = {
        "audit": {
            "id": str(manifest.get("audit_id", "unknown")),
            "revision_sha": str(manifest.get("revision_sha", "unversioned")),
            "status": "audit_incomplete",
            "incomplete_reason": reason,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "scope_digest": scope_digest,
            "previous_audit_id": None,
        },
        "inventory": [],
        "findings": [finding(
            "audit_incomplete", "blocker", skills, reason,
            "The requested revision and selected tracked scope could not be verified.",
        )],
        "summary": {
            "status": "audit_incomplete",
            "compliance_status": "unverified",
            "hard_gate_pass": False,
            "metrics": {
                "skill_count": 0, "blocker_count": 1, "contract_valid_rate": 0.0,
                "eval_coverage_rate": 0.0, "assertion_coverage_rate": 0.0,
                "benchmark_verified_rate": 0.0,
            },
            "handoffs": [],
        },
    }
    return report


def markdown(report: dict[str, Any]) -> str:
    lines = ["# Skill Portfolio Audit", "", f"- Status: `{report['audit'].get('status', 'unknown')}`", f"- Revision: `{report['audit']['revision_sha']}`", f"- Skills: {len(report['inventory'])}", f"- Findings: {len(report['findings'])}", f"- Hard gate: {'pass' if report['summary']['hard_gate_pass'] else 'fail'}", f"- Compliance status: `{report['summary'].get('compliance_status', 'unverified')}`"]
    if report["audit"].get("incomplete_reason"):
        lines.extend([f"- Incomplete reason: {report['audit']['incomplete_reason']}"])
    lines.extend(["", "## Findings", ""])
    if not report["findings"]:
        lines.append("No deterministic findings.")
    else:
        lines.extend(["| ID | Severity | Rule | Skills | Evidence |", "|---|---|---|---|---|"])
        for item in report["findings"]:
            evidence = "; ".join(item["evidence"]).replace("|", "\\|")
            lines.append(f"| {item['finding_id']} | {item['severity']} | {item['rule_id']} | {', '.join(item['skills'])} | {evidence} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    args = parser.parse_args()
    manifest: dict[str, Any] | None = None
    root: Path | None = None
    directories: list[Path] = []
    try:
        manifest = load_manifest(args.manifest)
        root = Path(manifest["repository_root"]).resolve()
        if not root.is_dir():
            raise ValueError("audit root does not exist")
        scope = manifest["scope"]
        include, exclude = scope.get("include", []), scope.get("exclude", [])
        directories = select_directories(root, include, exclude)
        if not directories:
            # Keep the revision-side scope so a deleted selected skill is
            # reported as incomplete instead of silently becoming empty.
            directories = [root / path for path in revision_skill_paths(root, manifest["revision_sha"], include, exclude)]
        if not directories:
            raise ValueError("audit scope selects no skills")
        verify_output_paths(root, [args.json, args.markdown])
        verify_snapshot(root, manifest["revision_sha"], directories, include, exclude)
        profile_source = manifest["profile_source"]
        default_profile = profile_source.get("default", "unclassified") if isinstance(profile_source, dict) else "unclassified"
        profile_map = profile_source.get("skills", {}) if isinstance(profile_source, dict) else {}
        if default_profile not in {"repo_native", "upstream_compatible", "unclassified"} or any(value not in {"repo_native", "upstream_compatible", "unclassified"} for value in profile_map.values()):
            raise ValueError("invalid provenance profile")
        report = scan(root, manifest["audit_id"], default_profile, include, exclude, profile_map, manifest["revision_sha"])
        verify_snapshot(root, manifest["revision_sha"], directories, include, exclude)
        previous = None
        if manifest.get("previous_report"):
            previous = json.loads(Path(manifest["previous_report"]).read_text(encoding="utf-8"))
        classify_baseline(report, previous)
    except (OSError, json.JSONDecodeError, ValueError, subprocess.CalledProcessError) as error:
        if manifest is not None:
            try:
                if root is not None:
                    verify_output_paths(root, [args.json, args.markdown])
                report = incomplete_report(manifest, root, directories, str(error))
                args.json.parent.mkdir(parents=True, exist_ok=True)
                args.markdown.parent.mkdir(parents=True, exist_ok=True)
                args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                args.markdown.write_text(markdown(report), encoding="utf-8")
            except (OSError, ValueError) as output_error:
                print(f"audit incomplete: {error}; cannot write report: {output_error}", file=sys.stderr)
                return 2
            print(f"audit incomplete: {error}", file=sys.stderr)
            return 2
        print(f"audit invalid: {error}", file=sys.stderr)
        return 2
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(report), encoding="utf-8")
    new_blocker = any(item["severity"] == "blocker" and item["baseline_state"] == "new" for item in report["findings"])
    return 1 if manifest["fail_policy"] == "new_blockers" and new_blocker else 0


if __name__ == "__main__":
    raise SystemExit(main())
