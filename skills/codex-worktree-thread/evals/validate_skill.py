#!/usr/bin/env python3
"""Static and scenario validation for codex-worktree-thread."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def require(name: str, condition: bool, evidence: str, failures: list[dict]) -> None:
    if not condition:
        failures.append({"check": name, "evidence": evidence})


def main() -> int:
    failures: list[dict] = []
    skill = load_text(ROOT / "SKILL.md")
    readme = load_text(ROOT / "README.md")
    evals = load_json(ROOT / "evals" / "evals.json")
    triggers = load_json(ROOT / "evals" / "trigger-eval.json")
    scenarios = load_json(ROOT / "evals" / "scenario-evals.json")

    require("frontmatter name", "name: codex-worktree-thread" in skill, "frontmatter name is missing", failures)
    require("description mentions default fork", "既定は `fork_thread + worktree`" in skill, "default fork policy missing", failures)
    require("description mentions fresh context", "`create_thread + existing project + worktree`" in skill, "fresh context policy missing", failures)
    require("tmux prohibition", len(re.findall(r"\btmux\b", skill, flags=re.IGNORECASE)) >= 8, "tmux prohibition is not emphasized", failures)
    require("projectless prohibition", len(re.findall(r"\bprojectless\b", skill, flags=re.IGNORECASE)) >= 8, "projectless prohibition is not emphasized", failures)
    require("tool_search required", "tool_search" in skill and "まず `tool_search`" in skill, "tool_search discovery not required", failures)
    require("fork_thread required", skill.count("fork_thread") >= 8, "fork_thread guidance too thin", failures)
    require("create_thread required", skill.count("create_thread") >= 7, "create_thread guidance too thin", failures)
    require("list_projects required", "list_projects" in skill, "list_projects guidance missing", failures)
    require("send_message_to_thread required", "send_message_to_thread" in skill, "follow-up prompt guidance missing", failures)
    require("set_thread_title required", "set_thread_title" in skill, "title-setting guidance missing", failures)
    require("pending id handling", "pendingWorktreeId" in skill and "捏造しない" in skill, "pending id safety missing", failures)
    require("startingState failure handling", "startingState" in skill and "plain worktree" in skill, "startingState fallback missing", failures)
    require("output table", "| Task | Mode | Tool | Thread/Pending ID | Title | Branch/worktree | Status |" in skill, "result table missing", failures)
    require("child prompt required fields", all(term in skill for term in ["AGENTS.md", "Vault", "commit/push/PR", "security-sensitive"]), "child prompt constraints incomplete", failures)

    require("README has default table", "Default Policy" in readme and "fork_thread + worktree" in readme, "README quick policy missing", failures)

    require("eval skill name", evals.get("skill_name") == "codex-worktree-thread", "eval skill_name mismatch", failures)
    require("eval count", len(evals.get("evals", [])) >= 12, "expected at least 12 evals", failures)
    for item in evals.get("evals", []):
        require(f"eval {item.get('id')} expectations", len(item.get("expectations", [])) >= 3, "each eval needs at least 3 expectations", failures)

    trigger_true = sum(1 for item in triggers if item.get("should_trigger") is True)
    trigger_false = sum(1 for item in triggers if item.get("should_trigger") is False)
    require("trigger eval count", len(triggers) >= 24, "expected at least 24 trigger evals", failures)
    require("trigger positives", trigger_true >= 12, f"only {trigger_true} positive trigger evals", failures)
    require("trigger negatives", trigger_false >= 12, f"only {trigger_false} negative trigger evals", failures)

    require("scenario count", len(scenarios.get("scenarios", [])) >= 6, "expected at least 6 scenario evals", failures)
    scenario_index = {item["id"]: item for item in scenarios.get("scenarios", [])}
    require("default scenario chooses fork", scenario_index.get("default_issue_split", {}).get("expected_tool") == "fork_thread", "default issue split should use fork_thread", failures)
    require("fresh scenario chooses create", scenario_index.get("fresh_context_same_project", {}).get("expected_tool") == "create_thread", "fresh context should use create_thread", failures)
    require("tmux scenario rejects tmux", scenario_index.get("tmux_requested", {}).get("expected_mode") == "reject-tmux", "tmux scenario should reject tmux", failures)
    require("pending scenario avoids invented id", "invented threadId" in scenario_index.get("pending_worktree", {}).get("must_avoid", []), "pending scenario should avoid invented id", failures)

    result = {
        "skill_name": "codex-worktree-thread",
        "checks": 31,
        "passed": not failures,
        "failures": failures,
        "summary": {
            "evals": len(evals.get("evals", [])),
            "trigger_evals": len(triggers),
            "trigger_positive": trigger_true,
            "trigger_negative": trigger_false,
            "scenarios": len(scenarios.get("scenarios", [])),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
