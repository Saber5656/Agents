"""Offline contract tests for the review coordinator."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.service_review import decide_findings, stable_finding_id  # noqa: E402
from harness.tasks import TaskStore  # noqa: E402


@pytest.fixture
def roots(tmp_path):
    agents = tmp_path / "agents"
    vault = tmp_path / "vault"
    agents.mkdir()
    vault.mkdir()
    return agents, vault


def spec(agents, vault, task_id):
    return {
        "task": {"id": task_id, "purpose": "ship the change"},
        "job": {"id": "job-1", "workspace": str(agents)},
        "agents_root": str(agents),
        "vault_root": str(vault),
    }


def review(findings):
    return {
        "findings": findings,
        "evidence_links": ["vault://review/evidence.json"],
    }


def finding(issue="fix parser"):
    return {"severity": "high", "file": "src/parser.py:10", "issue": issue}


def response_for(findings, decision="adopt"):
    return json.dumps({
        "decisions": [
            {
                "finding_id": stable_finding_id(item),
                "decision": decision,
                "reason": "The finding directly affects the task acceptance.",
                "evidence": ["vault://review/evidence.json"],
            }
            for item in findings
        ]
    })


def test_missing_or_malformed_review_is_incomplete_and_persisted(roots):
    agents, vault = roots
    result = decide_findings(spec(agents, vault, "task-root"), {"findings": []}, runner=lambda _: "{}")
    assert result["status"] == "incomplete"
    assert result["reason"]
    assert (vault / "service-review").exists()


def test_all_findings_are_decided_once_with_required_evidence(roots):
    agents, vault = roots
    findings = [finding(), finding("update docs")]
    calls = []

    def runner(request):
        calls.append(request)
        return {"status": "completed", "text": response_for(findings), "usage": {"input": 3}}

    result = decide_findings(spec(agents, vault, "task-root"), review(findings), runner=runner)
    assert result["status"] == "complete"
    assert [item["decision"] for item in result["decisions"]] == ["adopt", "adopt"]
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-6-astra"
    assert calls[0]["reasoning_effort"] == "high"
    assert calls[0]["sandbox"] == "read-only"
    assert set(calls[0]["disabled_features"]) >= {"multi_agent", "apps", "plugins"}


def test_separate_registers_idempotent_task_without_remote_issue(roots):
    agents, vault = roots
    with TaskStore(agents_root=agents, vault_root=vault) as store:
        root = store.create_task(purpose="root task")
        one = [finding()]
        result = decide_findings(spec(agents, vault, root["id"]), review(one),
                                 runner=lambda _: {"status": "completed", "text": response_for(one, "separate")})
        again = decide_findings(spec(agents, vault, root["id"]), review(one),
                                runner=lambda _: (_ for _ in ()).throw(AssertionError("must reuse complete result")))
        tasks = store.list_tasks()
    assert result["status"] == "complete"
    assert result["separate_task_ids"]
    assert again == result
    assert len(tasks) == 2
    follow_up = next(task for task in tasks if task["id"] in result["separate_task_ids"])
    assert follow_up["source_task_id"] == root["id"]
    assert follow_up["source_event_key"].startswith("review:")


def test_incomplete_model_output_never_creates_partial_separate_tasks(roots):
    agents, vault = roots
    with TaskStore(agents_root=agents, vault_root=vault) as store:
        root = store.create_task(purpose="root task")
        findings = [finding(), finding("second")]
        text = json.dumps({"decisions": [{
            "finding_id": stable_finding_id(findings[0]), "decision": "separate",
            "reason": "Follow-up is outside the task.", "evidence": ["vault://review/evidence.json"],
        }]})
        result = decide_findings(spec(agents, vault, root["id"]), review(findings), runner=lambda _: text)
        assert result["status"] == "incomplete"
        assert len(store.list_tasks()) == 1


def test_terminal_invalid_json_is_reused_for_unchanged_input(roots):
    agents, vault = roots
    findings = [finding()]
    calls = []

    def malformed(_):
        calls.append(1)
        return {"status": "completed", "text": "not json", "usage": {"input": 1},
                "process_identity": {"pid": 42}}

    first = decide_findings(spec(agents, vault, "task-root"), review(findings), runner=malformed)
    assert first["status"] == "incomplete"
    assert first["retryable"] is False
    digest = first["input_digest"]
    run_dir = vault / "service-review" / digest
    assert (run_dir / "request.json").exists()
    assert (run_dir / "provider-output.json").exists()

    resumed = decide_findings(
        spec(agents, vault, "task-root"), review(findings),
        runner=lambda _: (_ for _ in ()).throw(AssertionError("unchanged malformed review must be reused")))
    assert resumed == first
    assert calls == [1]


def test_saved_valid_response_retries_local_registration_without_provider(roots, monkeypatch):
    agents, vault = roots
    findings = [finding()]
    with TaskStore(agents_root=agents, vault_root=vault) as store:
        root = store.create_task(purpose="root task")
        calls = []

        def runner(_):
            calls.append(1)
            return {"status": "completed", "text": response_for(findings, "separate")}

        attempts = iter([OSError("local registration temporarily unavailable"), []])
        def register(*args):
            result = next(attempts)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr("harness.service_review._register_separate", register)
        request = spec(agents, vault, root["id"])
        first = decide_findings(request, review(findings), runner=runner)
        second = decide_findings(request, review(findings), runner=runner)

    assert first["status"] == "incomplete"
    assert first["retryable"] is True
    assert second["status"] == "complete"
    assert calls == [1]


def test_changed_review_evidence_requests_a_new_provider_turn(roots):
    agents, vault = roots
    findings = [finding()]
    calls = []

    def runner(_):
        calls.append(1)
        return {"status": "completed", "text": "not json"}

    first = decide_findings(spec(agents, vault, "task-root"), review(findings), runner=runner)
    changed = {"findings": findings, "evidence_links": ["vault://review/updated.json"]}
    second = decide_findings(spec(agents, vault, "task-root"), changed, runner=runner)
    assert first["input_digest"] != second["input_digest"]
    assert calls == [1, 1]


def test_provider_failure_is_incomplete(roots):
    agents, vault = roots
    result = decide_findings(spec(agents, vault, "task-root"), review([finding()]),
                             runner=lambda _: {"status": "failed", "reason": "provider unavailable"})
    assert result["status"] == "incomplete"


def test_paid_api_route_is_rejected_before_any_runner_call(roots, monkeypatch):
    agents, vault = roots
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    called = []
    result = decide_findings(spec(agents, vault, "task-root"), review([finding()]),
                             runner=lambda _: called.append(1))
    assert result["status"] == "incomplete"
    assert called == []
    assert "synthetic-secret" not in json.dumps(result)


def test_login_status_must_confirm_authenticated_chatgpt_subscription(roots, monkeypatch):
    agents, vault = roots
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("harness.service_review.shutil.which", lambda name, path=None: "/bin/codex")
    monkeypatch.setattr("harness.service_review.subprocess.run", lambda *args, **kwargs:
                        type("Completed", (), {"returncode": 0, "stdout": "ChatGPT subscription: not logged in", "stderr": ""})())
    result = decide_findings(spec(agents, vault, "task-root"), review([finding()]))
    assert result["status"] == "incomplete"
    assert "login" in result["reason"].lower()


def test_default_codex_request_has_read_only_boundaries_and_persists_identity(roots, monkeypatch):
    agents, vault = roots
    findings = [finding()]
    commands = []

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("harness.service_review.shutil.which", lambda name, path=None: "/bin/codex")
    monkeypatch.setattr("harness.service_review.subprocess.run", lambda *args, **kwargs:
                        type("Completed", (), {"returncode": 0, "stdout": "Logged in with ChatGPT subscription", "stderr": ""})())
    from harness.runner import ProcessResult
    def execute(command, env, cwd, prompt, timeout, **kwargs):
        commands.append((command, kwargs))
        events = (json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": response_for(findings)}})
                  + "\n" + json.dumps({"type": "turn.completed", "usage": {"input": 4}}))
        kwargs["state_path"].write_text(json.dumps({"pid": 4242, "status": "completed"}))
        kwargs["stdout_path"].write_text(events)
        kwargs["stderr_path"].write_text("provider diagnostic")
        return ProcessResult(0, events, "provider diagnostic")
    monkeypatch.setattr("harness.runner.execute", execute)
    result = decide_findings(spec(agents, vault, "task-root"), review(findings))
    assert result["status"] == "complete"
    command = commands[0][0]
    assert ["-m", "gpt-6-astra"] == command[command.index("-m"):command.index("-m") + 2]
    for feature in ("multi_agent", "apps", "plugins"):
        assert command[command.index("--disable", command.index(feature) - 2) + 1] == feature
    assert result["process_identity"]["pid"] == 4242
    assert any("turn.completed" in p.read_text() for p in (vault / "service-review").rglob("stdout.jsonl"))
    assert any("provider diagnostic" in p.read_text() for p in (vault / "service-review").rglob("stderr.txt"))


def test_reject_is_a_decision_and_completed_result_is_reused(roots):
    agents, vault = roots
    findings = [finding()]
    calls = []

    def runner(_):
        calls.append(1)
        return {"status": "completed", "text": response_for(findings, "reject")}

    first = decide_findings(spec(agents, vault, "task-root"), review(findings), runner=runner)
    second = decide_findings(spec(agents, vault, "task-root"), review(findings), runner=runner)
    assert first["decisions"][0]["decision"] == "reject"
    assert second == first
    assert calls == [1]


def test_invalid_subscription_route_is_incomplete_without_runner(roots, monkeypatch):
    agents, vault = roots
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    result = decide_findings(spec(agents, vault, "task-root"), review([finding()]))
    assert result["status"] == "incomplete"
    assert "paid" in result["reason"]
    assert "synthetic-secret" not in json.dumps(result)


def test_overlapping_same_input_runs_one_coordinator(roots):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    agents, vault = roots
    findings = [finding()]
    entered = threading.Event(); release = threading.Event(); calls = []
    def runner(_):
        calls.append(1); entered.set(); release.wait(2)
        return response_for(findings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(decide_findings, spec(agents, vault, "task-root"), review(findings), runner=runner)
        assert entered.wait(2)
        second = pool.submit(decide_findings, spec(agents, vault, "task-root"), review(findings), runner=runner)
        release.set()
        assert first.result()["status"] == second.result()["status"] == "complete"
    assert calls == [1]


def test_retry_preserves_previous_provider_output(roots):
    agents, vault = roots
    findings = [finding()]
    first = decide_findings(spec(agents, vault, "task-root"), review(findings), runner=lambda _: "first-invalid-output")
    decide_findings(spec(agents, vault, "task-root"), review(findings), runner=lambda _: response_for(findings))
    files = list((vault / "service-review" / first["input_digest"]).rglob("*.json"))
    assert any("first-invalid-output" in file.read_text() for file in files)


def test_separate_inherits_source_task_repository(roots):
    agents, vault = roots
    with TaskStore(agents_root=agents, vault_root=vault) as store:
        source = store.create_task(purpose="root", repository="Saber5656/Agents")
        request = spec(agents, vault, source["id"]); request["task"] = source
        findings = [finding()]
        result = decide_findings(request, review(findings), runner=lambda _: response_for(findings, "separate"))
        assert store.get_task(result["separate_task_ids"][0])["repository"] == "Saber5656/Agents"


def test_same_separate_finding_does_not_duplicate_when_job_state_changes(roots):
    agents, vault = roots
    with TaskStore(agents_root=agents, vault_root=vault) as store:
        source = store.create_task(purpose="root", repository="Saber5656/Agents")
        request = spec(agents, vault, source["id"]); request["task"] = source
        findings = [finding()]
        first = decide_findings(request, review(findings), runner=lambda _: response_for(findings, "separate"))
        request["job"]["attempts_count"] = 2
        second = decide_findings(request, review(findings), runner=lambda _: response_for(findings, "separate"))
        assert first["separate_task_ids"] == second["separate_task_ids"]
        assert len(store.list_tasks()) == 2


def test_restart_does_not_duplicate_surviving_decision_provider(roots, monkeypatch):
    from harness.service_review import input_digest
    agents, vault = roots
    request = spec(agents, vault, "task-root"); findings = [finding()]
    directory = vault / "service-review" / input_digest(request, review(findings)) / "attempt-prior"
    directory.mkdir(parents=True)
    (directory / "process.json").write_text(json.dumps({"pid": 4242, "status": "running"}))
    monkeypatch.setattr("harness.runner.reconcile_process", lambda _: {"status": "alive"})
    calls = []
    result = decide_findings(request, review(findings), runner=lambda _: calls.append(1))
    assert result["status"] == "incomplete"
    assert "running" in result["reason"]
    assert calls == []
