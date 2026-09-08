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


def test_invalid_json_is_saved_and_can_resume_with_a_later_provider_result(roots):
    agents, vault = roots
    findings = [finding()]
    first = decide_findings(spec(agents, vault, "task-root"), review(findings),
                            runner=lambda _: {"status": "completed", "text": "not json", "usage": {"input": 1},
                                               "process_identity": {"pid": 42}})
    assert first["status"] == "incomplete"
    digest = first["input_digest"]
    run_dir = vault / "service-review" / digest
    assert (run_dir / "request.json").exists()
    assert (run_dir / "provider-output.json").exists()

    resumed = decide_findings(spec(agents, vault, "task-root"), review(findings),
                              runner=lambda _: {"status": "completed", "text": response_for(findings),
                                                 "usage": {"input": 2}, "process_identity": {"pid": 43}})
    assert resumed["status"] == "complete"
    assert resumed["usage"] == {"input": 2}
    assert resumed["process_identity"] == {"pid": 43}


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

    class Process:
        pid = 4242
        returncode = 0

        def communicate(self, prompt, timeout):
            assert prompt
            assert timeout == 300.0
            return (json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": response_for(findings)}})
                    + "\n" + json.dumps({"type": "turn.completed", "status": "completed", "usage": {"input": 4}}), "")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("harness.service_review.shutil.which", lambda name, path=None: "/bin/codex")
    monkeypatch.setattr("harness.service_review.subprocess.run", lambda *args, **kwargs:
                        type("Completed", (), {"returncode": 0, "stdout": "Logged in with ChatGPT subscription", "stderr": ""})())
    def popen(command, **kwargs):
        commands.append((command, kwargs))
        return Process()
    monkeypatch.setattr("harness.service_review.subprocess.Popen", popen)
    result = decide_findings(spec(agents, vault, "task-root"), review(findings))
    assert result["status"] == "complete"
    command = commands[0][0]
    assert ["-m", "gpt-6-astra"] == command[command.index("-m"):command.index("-m") + 2]
    for feature in ("multi_agent", "apps", "plugins"):
        assert command[command.index("--disable", command.index(feature) - 2) + 1] == feature
    assert result["process_identity"]["pid"] == 4242


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
