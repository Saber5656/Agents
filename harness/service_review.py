"""Review-finding coordinator for the local execution service.

The coordinator gives the primary agent a durable, structured decision point.
It does not edit a worktree, create GitHub objects, or silently turn a review
finding into an implementation instruction.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Any, Mapping

from .tasks import TaskStore


MODEL = "gpt-6-astra"
REASONING_EFFORT = "high"
SANDBOX = "read-only"
DISABLED_FEATURES = ("multi_agent", "apps", "plugins", "browser_use", "computer_use", "image_generation")
PAID_ROUTE_KEYS = frozenset({
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE",
    "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CODEX_API_KEY",
})


class ReviewInputError(ValueError):
    """Review input is incomplete or cannot be evaluated safely."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def input_digest(spec: Mapping[str, Any], review: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical({"spec": spec, "review": review}).encode()).hexdigest()


def stable_finding_id(finding: Mapping[str, Any]) -> str:
    """Derive a deterministic ID from one observed finding."""
    return "finding-" + hashlib.sha256(_canonical(dict(finding)).encode()).hexdigest()[:20]


def _redact(text: str, env: Mapping[str, str] | None = None) -> str:
    env = env or os.environ
    result = text
    for key, value in sorted(env.items(), key=lambda pair: -len(pair[1])):
        if value and len(value) >= 6 and re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|AUTH", key, re.I):
            result = result.replace(value, "[REDACTED]")
    result = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                    "[REDACTED PRIVATE KEY]", result, flags=re.S)
    result = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s\"\\]+",
                    r"\1[REDACTED]", result)
    return re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:ant-)?[A-Za-z0-9_-]{20,})",
                  "[REDACTED]", result)


def _save_json(path: Path, value: Any, env: Mapping[str, str]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(_redact(_canonical(value), env) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _redact_value(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return _redact(value, env)
    if isinstance(value, list):
        return [_redact_value(item, env) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item, env) for key, item in value.items()}
    return value


def _artifact_dir(vault_root: Path, digest: str) -> Path:
    directory = vault_root / "service-review" / digest
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def _validate(spec: Any, review: Any) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str]]:
    if not isinstance(spec, dict) or not isinstance(review, dict):
        raise ReviewInputError("spec and review must be JSON objects")
    for field in ("task", "job", "agents_root", "vault_root"):
        if field not in spec:
            raise ReviewInputError(f"spec.{field} is required")
    task = spec["task"]
    job = spec["job"]
    if not isinstance(task, dict) or not isinstance(task.get("id"), str) or not task["id"]:
        raise ReviewInputError("spec.task.id is required")
    if not isinstance(job, dict):
        raise ReviewInputError("spec.job must be an object")
    agents_root = Path(spec["agents_root"]) if isinstance(spec["agents_root"], str) else None
    vault_root = Path(spec["vault_root"]) if isinstance(spec["vault_root"], str) else None
    if agents_root is None or vault_root is None or not agents_root.is_dir() or not vault_root.is_dir():
        raise ReviewInputError("agents_root and vault_root must be existing directories")
    findings = review.get("findings")
    links = review.get("evidence_links")
    if not isinstance(findings, list) or not findings:
        raise ReviewInputError("review.findings must be a non-empty list")
    if not isinstance(links, list) or not links or not all(isinstance(link, str) and link for link in links):
        raise ReviewInputError("review.evidence_links must contain at least one non-empty link")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            raise ReviewInputError("each finding must be an object")
        normalized_finding = dict(finding)
        finding_id = stable_finding_id(normalized_finding)
        if finding_id in ids:
            raise ReviewInputError("finding IDs are ambiguous")
        ids.add(finding_id)
        normalized_finding["finding_id"] = finding_id
        normalized.append(normalized_finding)
    return {**spec, "agents_root": str(agents_root), "vault_root": str(vault_root)}, review, normalized, list(dict.fromkeys(links))


def _prompt(spec: Mapping[str, Any], findings: list[dict[str, Any]], evidence_links: list[str]) -> str:
    body = {
        "task": spec["task"],
        "job": spec["job"],
        "findings": findings,
        "evidence_links": evidence_links,
    }
    return (
        "You are the primary review coordinator. Read only and do not edit files, "
        "make external mutations, create Issues, or delegate. You may read the local workspace and saved evidence using read-only tools. For every finding exactly "
        "once, return JSON only with decisions [{finding_id, decision, reason, evidence}]. "
        "decision must be adopt, reject, or separate. Every reason and evidence list "
        "must be non-empty and grounded in the supplied evidence. "
        "\nObserved review input:\n" + _canonical(body)
    )


def _run_codex_review(spec: Mapping[str, Any], prompt: str, directory: Path) -> dict[str, Any]:
    env = dict(os.environ)
    dotenv = Path(spec["agents_root"]) / ".env"
    if dotenv.is_file():
        from .runner import load_dotenv
        env = load_dotenv(dotenv, env)
    paid = sorted(key for key in PAID_ROUTE_KEYS if env.get(key))
    if paid:
        return {"status": "incomplete", "reason": "paid API route is configured: " + ", ".join(paid)}
    codex = shutil.which("codex", path=env.get("PATH"))
    if not codex:
        return {"status": "incomplete", "reason": "codex executable is unavailable"}
    try:
        login = subprocess.run([codex, "login", "status"], env=env, capture_output=True,
                               text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "incomplete", "reason": "subscription login status unavailable: " + type(exc).__name__}
    login_text = _redact((login.stdout or "") + "\n" + (login.stderr or ""), env).lower()
    if (login.returncode or "chatgpt" not in login_text or
            not any(marker in login_text for marker in ("logged in", "authenticated")) or
            any(marker in login_text for marker in ("not logged", "logged out", "not authenticated"))):
        return {"status": "incomplete", "reason": "Codex ChatGPT subscription login was not verified"}
    if any(term in login_text for term in ("api key", "api_key", "apikey")):
        return {"status": "incomplete", "reason": "Codex API-key authentication is not allowed"}
    workspace = Path(spec["job"].get("workspace", spec["agents_root"]))
    if not workspace.is_dir():
        return {"status": "incomplete", "reason": "review workspace is unavailable"}
    command = [codex, "exec", "--ignore-user-config", "--ephemeral", "--json",
               "--skip-git-repo-check", "-m", MODEL, "-s", SANDBOX,
               "-c", 'approval_policy="never"', "-c", f'model_reasoning_effort="{REASONING_EFFORT}"']
    for feature in DISABLED_FEATURES:
        command.extend(["--disable", feature])
    command.extend(["-c", 'web_search="disabled"', "-c", "skills.max_context_tokens=1", "-"])
    process_identity = {"pid": None, "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "argv": command, "cwd": str(workspace)}
    from .runner import execute
    _save_json(directory / "command.json", process_identity, env)
    outcome = execute(command, env, workspace, prompt, float(spec["job"].get("timeout", 300)),
                      stdout_path=directory / "stdout.jsonl", stderr_path=directory / "stderr.txt",
                      state_path=directory / "process.json", redaction_env=env)
    process_identity = _load_json(directory / "process.json") or process_identity
    result = (_parse_provider_output(outcome.stdout, outcome.code, process_identity)
              if not outcome.output_pending else {"status": "incomplete", "reason": "output collection still running"})
    result.setdefault("usage", None)
    result["artifact_dir"] = str(directory)
    _save_json(directory / "provider-output.json", {"prompt": prompt, "output": outcome.stdout,
               "stderr": outcome.stderr, "usage": result.get("usage"), "process_identity": process_identity}, env)
    return result


def _parse_provider_output(output: str, returncode: int, process_identity: Mapping[str, Any]) -> dict[str, Any]:
    messages: list[str] = []
    usage = None
    completed = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            messages.append(item["text"])
        if event.get("type") == "turn.completed":
            completed = event.get("status") in (None, "completed", "success", "succeeded")
            usage = event.get("usage")
    if returncode != 0 or not completed or not messages:
        return {"status": "incomplete", "reason": "Codex review did not complete successfully", "usage": usage, "process_identity": dict(process_identity)}
    return {"status": "completed", "text": messages[-1], "usage": usage, "process_identity": dict(process_identity)}


def _invoke_runner(runner: Any, request: dict[str, Any]) -> Any:
    if callable(runner):
        return runner(request)
    run = getattr(runner, "run", None)
    if callable(run):
        return run(request)
    raise TypeError("runner must be callable or expose run(request)")


def _result_text(provider_result: Any) -> tuple[str | None, dict[str, Any]]:
    if isinstance(provider_result, str):
        return provider_result, {"status": "completed", "text": provider_result}
    if not isinstance(provider_result, dict):
        return None, {"status": "incomplete", "reason": "provider result is malformed"}
    if provider_result.get("status") not in (None, "completed", "complete", "success"):
        return None, provider_result
    text = provider_result.get("text", provider_result.get("output"))
    if isinstance(text, dict) and "decisions" in text:
        text = _canonical(text)
    return text if isinstance(text, str) else None, provider_result


def _parse_decisions(text: str | None, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not text:
        raise ReviewInputError("provider returned no decision JSON")
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", candidate, flags=re.S)
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
    except ValueError as exc:
        raise ReviewInputError("provider returned invalid JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
        raise ReviewInputError("provider JSON must contain decisions")
    expected = {finding["finding_id"] for finding in findings}
    seen: set[str] = set()
    decisions: list[dict[str, Any]] = []
    for item in value["decisions"]:
        if not isinstance(item, dict):
            raise ReviewInputError("a decision is malformed")
        finding_id = item.get("finding_id")
        decision = item.get("decision")
        reason = item.get("reason")
        evidence = item.get("evidence")
        if finding_id not in expected or finding_id in seen:
            raise ReviewInputError("decisions must identify each finding exactly once")
        if decision not in {"adopt", "reject", "separate"}:
            raise ReviewInputError("decision must be adopt, reject, or separate")
        if not isinstance(reason, str) or not reason.strip():
            raise ReviewInputError("each decision requires a reason")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(link, str) and link for link in evidence):
            raise ReviewInputError("each decision requires evidence")
        seen.add(finding_id)
        decisions.append({"finding_id": finding_id, "decision": decision, "reason": reason.strip(), "evidence": list(dict.fromkeys(evidence))})
    if seen != expected:
        raise ReviewInputError("provider omitted one or more findings")
    by_id = {finding["finding_id"]: finding for finding in findings}
    decisions_by_id = {item["finding_id"]: item for item in decisions}
    return [{**decisions_by_id[finding["finding_id"]], "finding": finding} for finding in findings]


def _register_separate(spec: Mapping[str, Any], review: Mapping[str, Any], decisions: list[dict[str, Any]], digest: str) -> list[str]:
    separate = [item for item in decisions if item["decision"] == "separate"]
    if not separate:
        return []
    with TaskStore(agents_root=spec["agents_root"], vault_root=spec["vault_root"]) as store:
        if store.get_task(spec["task"]["id"]) is None:
            raise ReviewInputError("source task is not present in TaskStore")
        created: list[str] = []
        for item in separate:
            key = f"review:{spec['task']['id']}:{item['finding_id']}"
            existing = next((task for task in store.list_tasks() if task.get("source_task_id") == spec["task"]["id"] and task.get("source_event_key") == key), None)
            if existing:
                created.append(existing["id"])
                continue
            finding = item["finding"]
            try:
                task = store.create_task(
                    purpose="Review follow-up: " + str(finding.get("issue", item["finding_id"])),
                    source="service-review-separate", source_task_id=spec["task"]["id"], source_event_key=key,
                    expected_result=str(finding.get("issue", "review follow-up")),
                    evidence_links=list(dict.fromkeys(list(review["evidence_links"]) + item["evidence"])),
                    repository=spec["task"].get("repository") or spec["job"].get("repository"), priority=finding.get("severity"),
                )
            except sqlite3.IntegrityError:
                task = next((candidate for candidate in store.list_tasks()
                             if candidate.get("source_task_id") == spec["task"]["id"]
                             and candidate.get("source_event_key") == key), None)
                if task is None:
                    raise ReviewInputError("separate task registration conflicted without a readable row")
            created.append(task["id"])
        return created


def _recover_provider(directory):
    """Observe survivors and reuse terminal output saved before an interruption."""
    from .runner import reconcile_process
    resumable = None
    for attempt in sorted(directory.glob("attempt-*"), key=lambda path: path.stat().st_mtime_ns):
        state_file = attempt / "process.json"
        if not state_file.exists():
            continue
        state = _load_json(state_file)
        if state is None:
            return {"status": "incomplete", "reason": "saved provider identity is unreadable"}
        collectors = state.get("collectors", [])
        if not isinstance(collectors, list):
            return {"status": "incomplete", "reason": "saved collector list is unreadable"}
        records = [state] + collectors
        for record in records:
            if not isinstance(record, dict):
                return {"status": "incomplete", "reason": "saved collector identity is unreadable"}
            try:
                process_state = reconcile_process(record).get("status")
            except (OSError, ValueError, TypeError):
                process_state = "unknown"
            if process_state in ("alive", "unknown"):
                return {"status": "incomplete", "reason": "previous decision provider or collector is still running or uninspectable"}
        if not (attempt / "coordinator-result.json").exists() and not (attempt / "reconciliation.json").exists() and (attempt / "stdout.jsonl").is_file():
            recovered = _parse_provider_output((attempt / "stdout.jsonl").read_text(), state.get("exit_code", 0), state)
            if recovered.get("status") == "completed":
                resumable = {**recovered, "recovered_from": str(attempt)}
    return resumable


def _decide_locked(spec: Mapping[str, Any], review: Mapping[str, Any], *, runner: Any = None) -> dict[str, Any]:
    """Return durable adopt/reject/separate decisions for every review finding."""
    digest = input_digest(spec, review)
    vault_value = spec.get("vault_root") if isinstance(spec, Mapping) else None
    if not isinstance(vault_value, str) or not Path(vault_value).is_dir():
        return {"status": "incomplete", "input_digest": digest, "reason": "vault_root must be an existing directory"}
    directory = _artifact_dir(Path(vault_value), digest)
    cached = _load_json(directory / "result.json")
    if cached and cached.get("status") == "complete":
        return cached
    # A provider turn that reached a terminal response but violated the
    # decision schema is still a review result.  Reuse it for the same input
    # instead of paying for an identical model turn; provider/process failures
    # remain retryable after reconciliation.
    if cached and cached.get("status") == "incomplete" and cached.get("retryable") is False:
        return cached
    recovered = _recover_provider(directory)
    if recovered and recovered.get("status") == "incomplete":
        return {**recovered, "input_digest": digest}
    attempt_dir = Path(tempfile.mkdtemp(prefix="attempt-", dir=directory))
    env = dict(os.environ)
    schema_invalid = False
    try:
        normalized_spec, normalized_review, findings, evidence_links = _validate(spec, review)
        prompt = _prompt(normalized_spec, findings, evidence_links)
        _save_json(directory / "request.json", {"prompt": prompt, "input_digest": digest, "process_identity": {"coordinator_pid": os.getpid()}}, env)
        paid = sorted(key for key in PAID_ROUTE_KEYS if env.get(key))
        if paid:
            raise ReviewInputError("paid API route is configured: " + ", ".join(paid))
        saved_provider = None
        if cached and cached.get("status") == "incomplete" and cached.get("retryable") is True:
            saved = _load_json(directory / "provider-output.json")
            if isinstance(saved, dict) and "output" in saved:
                candidate = saved["output"]
                _, saved_meta = _result_text(candidate)
                if saved_meta.get("status") in (None, "completed", "complete", "success"):
                    saved_provider = candidate
        if recovered is not None:
            provider_result = recovered
        elif saved_provider is not None:
            provider_result = saved_provider
        elif runner is None:
            provider_result = _run_codex_review(normalized_spec, prompt, attempt_dir)
        else:
            request = {"prompt": prompt, "model": MODEL, "reasoning_effort": REASONING_EFFORT,
                       "sandbox": SANDBOX, "read_only": True, "disabled_features": list(DISABLED_FEATURES),
                       "agents_root": normalized_spec["agents_root"], "vault_root": normalized_spec["vault_root"]}
            try:
                provider_result = _invoke_runner(runner, request)
            except Exception as exc:
                provider_result = {"status": "incomplete", "reason": "review runner failed: " + type(exc).__name__}
        _save_json(directory / "provider-output.json", {"prompt": prompt, "output": provider_result, "usage": provider_result.get("usage") if isinstance(provider_result, dict) else None, "process_identity": provider_result.get("process_identity") if isinstance(provider_result, dict) else None}, env)
        text, provider_meta = _result_text(provider_result)
        if provider_meta.get("status") not in (None, "completed", "complete", "success"):
            raise ReviewInputError(_redact(str(provider_meta.get("reason", "review provider did not complete")), env))
        try:
            decisions = _parse_decisions(text, findings)
        except ReviewInputError:
            schema_invalid = True
            raise
        separate_task_ids = _register_separate(normalized_spec, normalized_review, decisions, digest)
        result = {"status": "complete", "input_digest": digest, "decisions": decisions,
                  "adopted_findings": [item for item in decisions if item["decision"] == "adopt"],
                  "rejected_findings": [item for item in decisions if item["decision"] == "reject"],
                  "separate_task_ids": separate_task_ids,
                  "runner": {"model": MODEL, "reasoning_effort": REASONING_EFFORT, "sandbox": SANDBOX,
                             "read_only": True, "disabled_features": list(DISABLED_FEATURES)},
                  "usage": provider_meta.get("usage"), "process_identity": provider_meta.get("process_identity")}
    except (ReviewInputError, OSError, ValueError) as exc:
        result = {"status": "incomplete", "input_digest": digest,
                  "reason": str(exc) or type(exc).__name__,
                  "retryable": not schema_invalid}
    result = _redact_value(result, env)
    _save_json(directory / "result.json", result, env)
    if recovered and recovered.get("recovered_from"):
        _save_json(Path(recovered["recovered_from"]) / "reconciliation.json", result, env)
    # Each attempt keeps all accessible records; root files are only a latest-view index.
    for name in ("request.json", "provider-output.json", "result.json"):
        source = directory / name
        if source.is_file():
            _save_json(attempt_dir / ("coordinator-" + name), json.loads(source.read_text()), env)
    return result


def decide_findings(spec: Mapping[str, Any], review: Mapping[str, Any], *, runner: Any = None) -> dict[str, Any]:
    """Serialize one immutable input throughout inference and local registration."""
    vault = spec.get("vault_root") if isinstance(spec, Mapping) else None
    if not isinstance(vault, str) or not Path(vault).is_dir():
        return _decide_locked(spec, review, runner=runner)
    directory = _artifact_dir(Path(vault), input_digest(spec, review))
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _decide_locked(spec, review, runner=runner)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
