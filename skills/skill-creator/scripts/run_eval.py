#!/usr/bin/env python3
"""Run Codex-based routing evaluation for a skill description.

Tests whether Codex judges that a skill's description should be used for a set
of queries. Outputs results as JSON. This intentionally avoids `claude -p`.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from scripts.utils import parse_skill_md


def find_project_root() -> Path:
    """Find the project root by walking up from cwd looking for .codex/."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".codex").is_dir() or (parent / ".git").is_dir():
            return parent
    return current


ROUTING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "should_use_skill": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["should_use_skill", "confidence", "reason"],
}


def build_routing_prompt(query: str, skill_name: str, skill_description: str) -> str:
    return f"""You are evaluating whether a Codex skill should be used.

Do not perform the user's task. Classify only.

Skill name:
{skill_name}

Skill description:
{skill_description}

User query:
{query}

Return JSON that matches the provided schema:
- should_use_skill: true if this skill should be used for the query.
- confidence: number from 0 to 1.
- reason: one short sentence explaining the decision.
"""


def parse_json_object(text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("empty output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def run_single_query(
    query: str,
    skill_name: str,
    skill_description: str,
    timeout: int,
    project_root: str,
    model: Optional[str] = None,
) -> dict:
    """Run a single query and return Codex's skill routing decision."""
    with tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False) as schema_file:
        json.dump(ROUTING_SCHEMA, schema_file)
        schema_path = Path(schema_file.name)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as output_file:
        output_path = Path(output_file.name)

    prompt = build_routing_prompt(query, skill_name, skill_description)
    try:
        cmd = [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--output-schema", str(schema_path),
            "--output-last-message", str(output_path),
            "--color", "never",
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=project_root,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "should_use_skill": None,
                "confidence": None,
                "reason": f"codex exec timed out after {timeout}s",
                "error": "timeout",
            }
        except FileNotFoundError:
            return {
                "should_use_skill": None,
                "confidence": None,
                "reason": "`codex` command was not found on PATH",
                "error": "codex_not_found",
            }

        output_text = (
            output_path.read_text()
            if output_path.exists() and output_path.stat().st_size > 0
            else result.stdout
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "codex exec failed"
            return {
                "should_use_skill": None,
                "confidence": None,
                "reason": detail[:500],
                "error": "codex_failed",
            }

        try:
            parsed = parse_json_object(output_text)
        except Exception as exc:
            return {
                "should_use_skill": None,
                "confidence": None,
                "reason": f"Could not parse codex output as JSON: {exc}",
                "error": "parse_failed",
                "raw_output": output_text[:1000],
            }

        return {
            "should_use_skill": bool(parsed.get("should_use_skill")),
            "confidence": float(parsed.get("confidence", 0.0)),
            "reason": str(parsed.get("reason", "")),
        }
    finally:
        schema_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def run_eval(
    eval_set: list[dict],
    skill_name: str,
    description: str,
    num_workers: int,
    timeout: int,
    project_root: Path,
    runs_per_query: int = 1,
    trigger_threshold: float = 0.5,
    model: Optional[str] = None,
) -> dict:
    """Run the full eval set and return results."""
    results = []

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        future_to_info = {}
        for item in eval_set:
            for run_idx in range(runs_per_query):
                future = executor.submit(
                    run_single_query,
                    item["query"],
                    skill_name,
                    description,
                    timeout,
                    str(project_root),
                    model,
                )
                future_to_info[future] = (item, run_idx)

        query_decisions: dict[str, list[dict]] = {}
        query_items: dict[str, dict] = {}
        for future in as_completed(future_to_info):
            item, _ = future_to_info[future]
            query = item["query"]
            query_items[query] = item
            if query not in query_decisions:
                query_decisions[query] = []
            try:
                query_decisions[query].append(future.result())
            except Exception as e:
                print(f"Warning: query failed: {e}", file=sys.stderr)
                query_decisions[query].append({
                    "should_use_skill": None,
                    "confidence": None,
                    "reason": str(e),
                    "error": "exception",
                })

    for query, decisions in query_decisions.items():
        item = query_items[query]
        errored_decisions = [d for d in decisions if d.get("error")]
        valid_decisions = [d for d in decisions if not d.get("error")]
        triggers = [bool(d["should_use_skill"]) for d in valid_decisions]
        trigger_rate = sum(triggers) / len(triggers) if triggers else 0.0
        should_trigger = item["should_trigger"]
        if errored_decisions:
            did_pass = False
        elif should_trigger:
            did_pass = trigger_rate >= trigger_threshold
        else:
            did_pass = trigger_rate < trigger_threshold
        results.append({
            "query": query,
            "should_trigger": should_trigger,
            "trigger_rate": trigger_rate,
            "triggers": sum(triggers),
            "runs": len(decisions),
            "valid_runs": len(valid_decisions),
            "errors": len(errored_decisions),
            "pass": did_pass,
            "decisions": decisions,
        })

    passed = sum(1 for r in results if r["pass"])
    errors = sum(r["errors"] for r in results)
    total = len(results)

    return {
        "skill_name": skill_name,
        "description": description,
        "results": results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "errors": errors,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Run Codex-based routing evaluation for a skill description")
    parser.add_argument("--eval-set", required=True, help="Path to eval set JSON file")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory")
    parser.add_argument("--description", default=None, help="Override description to test")
    parser.add_argument("--num-workers", type=int, default=6, help="Number of parallel workers")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout per query in seconds")
    parser.add_argument("--runs-per-query", type=int, default=3, help="Number of runs per query")
    parser.add_argument("--trigger-threshold", type=float, default=0.5, help="Trigger rate threshold")
    parser.add_argument("--model", default=None, help="Model to use for codex exec (default: user's configured model)")
    parser.add_argument("--verbose", action="store_true", help="Print progress to stderr")
    args = parser.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text())
    skill_path = Path(args.skill_path)

    if not (skill_path / "SKILL.md").exists():
        print(f"Error: No SKILL.md found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    name, original_description, content = parse_skill_md(skill_path)
    description = args.description or original_description
    project_root = find_project_root()

    if args.verbose:
        print(f"Evaluating: {description}", file=sys.stderr)

    output = run_eval(
        eval_set=eval_set,
        skill_name=name,
        description=description,
        num_workers=args.num_workers,
        timeout=args.timeout,
        project_root=project_root,
        runs_per_query=args.runs_per_query,
        trigger_threshold=args.trigger_threshold,
        model=args.model,
    )

    if args.verbose:
        summary = output["summary"]
        print(f"Results: {summary['passed']}/{summary['total']} passed", file=sys.stderr)
        for r in output["results"]:
            status = "PASS" if r["pass"] else "FAIL"
            rate_str = f"{r['triggers']}/{r['valid_runs']}"
            if r["errors"]:
                rate_str += f" errors={r['errors']}"
            print(f"  [{status}] rate={rate_str} expected={r['should_trigger']}: {r['query'][:70]}", file=sys.stderr)

    print(json.dumps(output, indent=2))
    if output["summary"].get("errors", 0) > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
