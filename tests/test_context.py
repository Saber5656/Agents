import json
import os
from pathlib import Path
import threading

import pytest

from harness.context import ContextError, RequirementLedger, VaultContext
from harness.tasks import TaskStore


def make_store(tmp_path):
    agents = tmp_path / "agents"
    vault = tmp_path / "vault"
    (agents / ".local").mkdir(parents=True)
    vault.mkdir()
    return TaskStore(agents_root=agents, vault_root=vault), agents, vault


def test_requirements_survive_partial_selection_revision_and_followup(tmp_path):
    store, agents, vault = make_store(tmp_path)
    ledger = RequirementLedger(store, agents / ".local" / "requirements.json")
    try:
        first = ledger.add("preserve original", acceptance=["check:first"])
        second = ledger.add("retain second", acceptance=["check:second"])
        third = ledger.add("retain dependent", acceptance=["check:third"], depends_on=[first["id"]])
        task = store.create_task(purpose="first implementation", execution_status="completed",
                                 acceptance_evidence=["check:first"], completion_evidence=["run:first"])
        store.link_requirement_task(first["id"], task["id"])
        ledger.select(first["id"])
        ledger.revise(second["id"], "corrected second scope")
        followup = ledger.record_followup(task["id"], "unrelated improvement", evidence=["diff:unrelated"])

        handoff = ledger.handoff()
        assert {row["id"] for row in handoff["requirements"]} == {first["id"], second["id"], third["id"]}
        by_id = {row["id"]: row for row in handoff["requirements"]}
        assert by_id[second["id"]]["latest_text"] == "corrected second scope"
        assert followup["status"] == "local_only"
        assert ledger.completion_report()["complete"] is False
        assert ledger.completion_report()["missing_requirements"]
    finally:
        store.close()


def test_unknown_requirement_dependency_is_rejected_before_store_mutation(tmp_path):
    store, agents, _ = make_store(tmp_path)
    ledger = RequirementLedger(store, agents / ".local" / "requirements.json")
    try:
        with pytest.raises(ContextError):
            ledger.add("invalid", depends_on=["missing"])
        assert store.list_requirements() == []
    finally:
        store.close()


def test_requirement_dependencies_survive_sidecar_interruption_and_reopen(tmp_path, monkeypatch):
    store, agents, _ = make_store(tmp_path)
    ledger = RequirementLedger(store, agents / ".local" / "requirements.json")
    try:
        prerequisite = ledger.add("prerequisite")
        original_save = ledger._save

        def fail_once(value):
            monkeypatch.setattr(ledger, "_save", original_save)
            raise OSError("simulated sidecar interruption")

        monkeypatch.setattr(ledger, "_save", fail_once)
        with pytest.raises(OSError):
            ledger.add("dependent", depends_on=[prerequisite["id"]])

        stored = store.list_requirements()
        dependent = next(row for row in stored if row["text"] == "dependent")
        assert dependent["dependencies"] == [prerequisite["id"]]
        reopened = RequirementLedger(store, agents / ".local" / "requirements.json")
        handoff = reopened.handoff()
        visible = next(row for row in handoff["requirements"] if row["id"] == dependent["id"])
        assert visible["depends_on"] == [prerequisite["id"]]
    finally:
        store.close()


def test_followup_is_idempotently_registered_as_local_discovery(tmp_path):
    store, agents, _ = make_store(tmp_path)
    ledger = RequirementLedger(store, agents / ".local" / "requirements.json")
    try:
        origin = store.create_task(purpose="origin")
        first = ledger.record_followup(origin["id"], "local improvement", evidence=["vault:first"])
        second = ledger.record_followup(origin["id"], "local improvement", evidence=["vault:second"])

        assert first["task_id"] == second["task_id"]
        discoveries = [
            row for row in store.list_tasks()
            if row["source_task_id"] == origin["id"]
            and row["source_event_key"] == first["id"]
        ]
        assert len(discoveries) == 1
        assert discoveries[0]["issueization_state"] == "unissued"
        assert discoveries[0]["evidence_links"] == ["vault:first", "vault:second"]
    finally:
        store.close()


def test_followup_sidecar_interruption_is_recoverable_from_task_store(tmp_path, monkeypatch):
    store, agents, _ = make_store(tmp_path)
    ledger = RequirementLedger(store, agents / ".local" / "requirements.json")
    try:
        origin = store.create_task(purpose="origin")
        def fail_save(value):
            raise OSError("interrupted")

        monkeypatch.setattr(ledger, "_save", fail_save)
        with pytest.raises(OSError):
            ledger.record_followup(origin["id"], "recoverable follow-up", evidence=["vault:followup"])

        reopened = RequirementLedger(store, agents / ".local" / "requirements.json")
        recovered = reopened.handoff()["followups"]
        assert len(recovered) == 1
        assert recovered[0]["purpose"] == "recoverable follow-up"
        assert recovered[0]["evidence"] == ["vault:followup"]
        assert recovered[0]["status"] == "local_only"
    finally:
        store.close()


def test_visible_context_chunks_redacts_and_excludes_reasoning(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("DUMMY_SECRET", "fixture-secret-value")
    context = VaultContext(vault, "run-1", chunk_size=16)
    result = context.save_records(
        "stdout",
        [
            {"type": "message", "channel": "commentary", "text": "fixture-secret-value"},
            {"type": "reasoning", "text": "private thought"},
            {"type": "event", "payload": {"type": "reasoning", "text": "nested private thought"}},
            {"type": "message", "channel": "commentary", "text": "long output " + "x" * 80},
        ],
        complete=False,
        truncation="upstream_compacted",
    )
    assert result["status"] == "saved"
    index = context.index()
    assert index["truncation"] == "upstream_compacted"
    assert len(index["records"]) > 1
    raw = "".join(Path(vault / "run-1" / row["path"]).read_text() for row in index["records"])
    assert "private thought" not in raw
    assert "nested private thought" not in raw
    assert "fixture-secret-value" not in raw
    assert "[REDACTED]" in raw
    for stream in ("stderr", "diff"):
        context.save_records(stream, [{"text": "fixture-secret-value"}], complete=True)
    for stream in ("stderr", "diff"):
        paths = [row["path"] for row in context.index()["records"] if row["stream"] == stream]
        assert paths and all("fixture-secret-value" not in (vault / "run-1" / path).read_text() for path in paths)


def test_context_keeps_append_only_generations_and_stream_completion(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    context = VaultContext(vault, "run-1", chunk_size=64)
    context.save_records("stdout", [{"text": "first"}], complete=False, truncation="live")
    context.save_records("stdout", [{"text": "second"}], complete=True)
    context.save_records("stderr", [{"text": "error"}], complete=False, truncation="live")
    index = context.index()
    assert index["complete"] is False
    assert index["streams"]["stdout"]["complete"] is True
    assert index["streams"]["stderr"]["complete"] is False
    assert len([row for row in index["records"] if row["stream"] == "stdout"]) == 2
    contents = "".join((context.run_dir / row["path"]).read_text() for row in index["records"])
    assert "first" in contents and "second" in contents


def test_repeated_source_events_are_not_written_twice(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    context = VaultContext(vault, "run-1")
    first = [
        {"source_event_id": "turn-1", "text": "initial objective"},
        {"source_event_id": "turn-2", "text": "user correction"},
    ]
    context.save_records("conversation", first, complete=False, truncation="live")
    context.save_records(
        "conversation",
        first + [{"source_event_id": "turn-3", "text": "recovered tool result"}],
        complete=True,
    )

    reopened = VaultContext(vault, "run-1")
    index = reopened.index()
    paths = [reopened.run_dir / row["path"] for row in index["records"]]
    raw = "".join(path.read_text() for path in paths)
    assert raw.count('"source_event_id": "turn-1"') == 1
    assert raw.count('"source_event_id": "turn-2"') == 1
    assert raw.count('"source_event_id": "turn-3"') == 1
    assert index["streams"]["conversation"]["complete"] is True
    assert len(index["streams"]["conversation"]["records"]) == 2
    assert len(index["streams"]["conversation"]["source_digests"]) == 3


def test_serialized_tool_result_excludes_nested_reasoning(tmp_path):
    context = VaultContext(tmp_path, "run")
    payload = json.dumps({"events": [
        {"type": "reasoning", "text": "private nested rationale"},
        {"channel": "analysis", "text": "private analysis payload"},
        {"type": "message", "text": "visible observation"},
    ]})
    result = context.save_records("tools", [{"output": payload}], complete=True)
    raw = "".join((context.run_dir / r["path"]).read_text() for r in result["records"])
    assert "private nested rationale" not in raw
    assert "private analysis payload" not in raw
    assert "visible observation" in raw


def test_stream_generations_retain_observed_order(tmp_path, monkeypatch):
    import harness.context as module
    from types import SimpleNamespace
    ids = iter(["z" * 32, "a" * 32])
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex=next(ids)))
    context = VaultContext(tmp_path, "run")
    context.save_records("stdout", [{"text": "first observation"}])
    result = context.save_records("stdout", [{"text": "second observation"}])
    raw = "".join((context.run_dir / r["path"]).read_text() for r in result["records"])
    assert raw.index("first observation") < raw.index("second observation")


def test_context_serializes_concurrent_stream_index_updates(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    context = VaultContext(vault, "run-1")
    barrier = threading.Barrier(2)

    def write(stream):
        barrier.wait()
        context.save_records(stream, [{"stream": stream}], complete=True)

    threads = [threading.Thread(target=write, args=(stream,)) for stream in ("stdout", "stderr")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    index = context.index()
    assert set(index["streams"]) == {"stdout", "stderr"}
    assert {row["stream"] for row in index["records"]} == {"stdout", "stderr"}


def test_revision_handoff_uses_task_store_after_sidecar_save_failure(tmp_path, monkeypatch):
    store, agents, _ = make_store(tmp_path)
    ledger = RequirementLedger(store, agents / ".local" / "requirements.json")
    try:
        requirement = ledger.add("before")
        original_save = ledger._save
        calls = {"count": 0}

        def fail_once(value):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("simulated sidecar interruption")
            return original_save(value)

        monkeypatch.setattr(ledger, "_save", fail_once)
        with pytest.raises(OSError):
            ledger.revise(requirement["id"], "after")
        by_id = {row["id"]: row for row in ledger.handoff()["requirements"]}
        assert by_id[requirement["id"]]["latest_text"] == "after"
        assert by_id[requirement["id"]]["revisions"] == ["after"]
    finally:
        store.close()


def test_run_id_cannot_escape_or_follow_symlink(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ContextError):
        VaultContext(vault, "..")
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ContextError):
        VaultContext(vault, "link")


def test_missing_or_unwritable_vault_never_uses_fallback(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(ContextError):
        VaultContext(missing, "run")

    vault = tmp_path / "vault"
    vault.mkdir()
    vault.chmod(0o500)
    try:
        with pytest.raises(ContextError):
            VaultContext(vault, "run")
    finally:
        vault.chmod(0o700)


def test_source_event_revisions_preserve_new_visible_content(tmp_path):
    context = VaultContext(tmp_path, "revisions")
    first = {"id": "shared", "text": "partial result"}
    revised = {"id": "shared", "text": "complete corrected result"}
    context.save_records("tools", [first])
    context.save_records("tools", [first, revised], complete=True)
    context.save_records("tools", [revised], complete=True)
    raw = "".join((context.run_dir / row["path"]).read_text()
                  for row in context.index()["records"])
    assert raw.count("partial result") == 1
    assert raw.count("complete corrected result") == 1


def test_filtered_event_does_not_suppress_later_visible_event(tmp_path):
    context = VaultContext(tmp_path, "visibility")
    context.save_records("conversation", [{"id": "message", "channel": "analysis", "text": "private"}])
    context.save_records("conversation", [{"id": "message", "channel": "final", "text": "visible result"}])
    raw = "".join((context.run_dir / row["path"]).read_text()
                  for row in context.index()["records"])
    assert "visible result" in raw
    assert "private" not in raw
