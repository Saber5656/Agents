import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "hermes_bridge.py"


def test_oneshot_timeout_is_bounded_and_structured(tmp_path):
    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\n/bin/sleep 2\n", encoding="utf-8")
    fake.chmod(0o755)
    result = _run_cli(tmp_path, "oneshot", "--prompt", "bounded", "--timeout", "0.05")
    payload = json.loads(result.stdout)
    assert result.returncode == 124
    assert payload["error"] == "timeout"
    assert payload["timeout_seconds"] == 0.05
    assert payload["command"][:2] == ["hermes", "-z"]


def test_missing_executable_is_structured_without_fallback(tmp_path):
    result = _run_cli(tmp_path, "oneshot", "--prompt", "missing")
    payload = json.loads(result.stdout)
    assert result.returncode == 127
    assert payload["error"] == "executable_not_found"
    assert payload["command"][0] == "hermes"


def _run_cli(tmp_path, *args, extra_env=None):
    config = tmp_path / "hermes-config.yaml"
    if not config.exists():
        config.write_text(
            "model:\n  provider: openai-codex\n  default: gpt-5.6-codex\n"
            "fallback_providers: []\n",
            encoding="utf-8",
        )
    env = {"PATH": str(tmp_path), "PYTHONPATH": "", "HERMES_CONFIG": str(config)}
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )


def test_partial_timeout_output_is_json_and_process_group_is_reconciled(tmp_path):
    child_pid = tmp_path / "child.pid"
    fake = tmp_path / "hermes"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(child_pid)!r}, 'w').write(str(child.pid))\n"
        "print('partial stdout', flush=True)\n"
        "print('partial stderr', file=sys.stderr, flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    receipt_dir = tmp_path / "receipts"

    result = _run_cli(
        tmp_path,
        "oneshot", "--prompt", "bounded", "--timeout", "1.0",
        "--receipt-dir", str(receipt_dir),
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 124
    assert payload["error"] == "timeout"
    assert payload["stdout"] == "partial stdout\n"
    assert payload["stderr"] == "partial stderr\n"
    assert payload["elapsed_seconds"] >= 0
    assert payload["process_identity"]["pid"] > 0
    assert payload["reconciliation_required"] is True
    receipt = json.loads(Path(payload["receipt_path"]).read_text())
    assert receipt["status"] == "timeout"
    assert Path(receipt["artifacts"]["stdout"]).read_text() == "partial stdout\n"
    assert json.loads(Path(receipt["artifacts"]["state"]).read_text())["status"] == "timeout"
    assert Path(payload["receipt_path"]).stat().st_mode & 0o777 == 0o600

    child = int(child_pid.read_text())
    try:
        os.kill(child, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("timeout left the fake Hermes child running")


def test_timeout_kills_child_that_ignores_sigterm(tmp_path):
    child_pid = tmp_path / "child.pid"
    child_ready = tmp_path / "child.ready"
    fake = tmp_path / "hermes"
    child_code = (
        "import pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_ready)!r}).write_text('ready'); time.sleep(30)"
    )
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"open({str(child_pid)!r}, 'w').write(str(child.pid))\n"
        "while not os.path.exists(" + repr(str(child_ready)) + "): time.sleep(0.01)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_DFL)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    result = _run_cli(tmp_path, "oneshot", "--prompt", "kill", "--timeout", "1")

    payload = json.loads(result.stdout)
    assert result.returncode == 124
    assert payload["termination"]["signal"] == "SIGKILL"
    assert payload["termination"]["verified"] is True
    child = int(child_pid.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_gateway_commands_have_bounded_timeout_and_receipt(tmp_path):
    marker = tmp_path / "called"
    fake = tmp_path / "hermes"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo called > {str(marker)!r}\n"
        "/bin/sleep 2\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    for command, extra in (("send", ["--target", "discord:#test", "--message", "hello"]),
                           ("list-targets", [])):
        result = _run_cli(
            tmp_path, command, *extra, "--timeout", "0.2",
            "--receipt-dir", str(tmp_path / f"receipts-{command}"),
        )
        payload = json.loads(result.stdout)
        assert result.returncode == 124
        assert payload["error"] == "timeout"
        assert payload["reconciliation_required"] is True
    assert marker.exists()


def test_paid_route_is_rejected_before_hermes_process(tmp_path):
    marker = tmp_path / "called"
    fake = tmp_path / "hermes"
    fake.write_text(f"#!/bin/sh\necho called > {str(marker)!r}\n", encoding="utf-8")
    fake.chmod(0o755)
    receipt_dir = tmp_path / "receipts"

    result = _run_cli(
        tmp_path, "oneshot", "--prompt", "must not run",
        "--provider", "openrouter", "--model", "anthropic/claude",
        "--receipt-dir", str(receipt_dir),
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 126
    assert payload["error"] == "route_policy"
    assert payload["action"] == "rejected_before_process"
    assert payload["route"]["provider"] == "openrouter"
    assert not marker.exists()
    assert Path(payload["receipt_path"]).exists()


def test_paid_api_environment_is_rejected_without_process(tmp_path):
    marker = tmp_path / "called"
    fake = tmp_path / "hermes"
    fake.write_text(f"#!/bin/sh\necho called > {str(marker)!r}\n", encoding="utf-8")
    fake.chmod(0o755)

    result = _run_cli(
        tmp_path,
        "oneshot", "--prompt", "must not run",
        "--receipt-dir", str(tmp_path / "receipts"),
        extra_env={"OPENROUTER_API_KEY": "present-but-not-recorded"},
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 126
    assert payload["error"] == "route_policy"
    assert "OPENROUTER_API_KEY" in payload["reason"]
    assert not marker.exists()


def test_resume_reads_receipt_without_rerunning_command(tmp_path):
    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\nprintf 'done'\n", encoding="utf-8")
    fake.chmod(0o755)
    result = _run_cli(
        tmp_path,
        "oneshot", "--prompt", "safe", "--timeout", "1",
        "--receipt-dir", str(tmp_path / "receipts"), "--request-id", "stable-1",
    )
    payload = json.loads(result.stdout)
    resumed = _run_cli(tmp_path, "resume", "--receipt", payload["receipt_path"])
    resumed_payload = json.loads(resumed.stdout)
    assert resumed.returncode == 0
    assert resumed_payload["request_id"] == "stable-1"
    assert resumed_payload["status"] == "completed"
    assert resumed_payload["resumable"] is True


def test_untrusted_config_route_is_rejected_before_process(tmp_path):
    marker = tmp_path / "called"
    fake = tmp_path / "hermes"
    fake.write_text(f"#!/bin/sh\necho called > {str(marker)!r}\n", encoding="utf-8")
    fake.chmod(0o755)
    config = tmp_path / "hermes-config.yaml"
    config.write_text(
        "model:\n  provider: xai-oauth\n  default: grok\nfallback_providers: [openrouter]\n",
        encoding="utf-8",
    )

    result = _run_cli(tmp_path, "oneshot", "--prompt", "blocked", "--receipt-dir", str(tmp_path / "receipts"))

    payload = json.loads(result.stdout)
    assert result.returncode == 126
    assert payload["error"] == "route_policy"
    assert not marker.exists()


def test_command_is_bound_to_openai_codex_even_when_route_is_omitted(tmp_path):
    marker = tmp_path / "argv"
    fake = tmp_path / "hermes"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {str(marker)!r}\n", encoding="utf-8")
    fake.chmod(0o755)

    result = _run_cli(tmp_path, "oneshot", "--prompt", "safe", "--timeout", "1")

    assert result.returncode == 0
    assert marker.read_text().splitlines()[:2] == ["-z", "safe"]
    assert "--provider" in marker.read_text().splitlines()
    lines = marker.read_text().splitlines()
    assert lines[lines.index("--provider") + 1] == "openai-codex"


def test_receipt_redacts_prompt_and_provider_output(tmp_path):
    secret = "sensitive-token-123456"
    fake = tmp_path / "hermes"
    fake.write_text(
        "#!/bin/sh\nprintf '%s' \"$TEST_API_TOKEN\"\nprintf '%s' \"$TEST_API_TOKEN\" >&2\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    result = _run_cli(
        tmp_path, "oneshot", "--prompt", secret, "--timeout", "1",
        "--receipt-dir", str(tmp_path / "receipts"),
        extra_env={"TEST_API_TOKEN": secret},
    )

    payload = json.loads(result.stdout)
    receipt_root = Path(payload["receipt_path"]).parent
    for path in receipt_root.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text()
    assert "[REDACTED]" in Path(payload["artifacts"]["stdout"]).read_text()


def test_receipt_sidecars_are_private_under_permissive_umask(tmp_path):
    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\nprintf 'stdout'\nprintf 'stderr' >&2\n", encoding="utf-8")
    fake.chmod(0o755)
    previous = os.umask(0)
    try:
        result = _run_cli(
            tmp_path, "oneshot", "--prompt", "private", "--timeout", "1",
            "--receipt-dir", str(tmp_path / "receipts"),
        )
    finally:
        os.umask(previous)

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    receipt_root = Path(payload["receipt_path"]).parent
    for name in ("request.json", "state.json", "result.json", "stdout.txt", "stderr.txt"):
        assert (receipt_root / name).stat().st_mode & 0o777 == 0o600


def test_request_id_path_traversal_is_rejected(tmp_path):
    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\nprintf done\n", encoding="utf-8")
    fake.chmod(0o755)
    result = _run_cli(
        tmp_path, "oneshot", "--prompt", "blocked", "--request-id", "..",
        "--receipt-dir", str(tmp_path / "receipts"),
    )
    assert result.returncode == 126
    assert json.loads(result.stdout)["error"] == "receipt_policy"
    assert "filesystem-safe" in json.loads(result.stdout)["reason"]


def test_same_request_id_preserves_previous_attempt_receipt(tmp_path):
    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\nprintf 'first\\n'\n", encoding="utf-8")
    fake.chmod(0o755)
    receipt_dir = tmp_path / "receipts"
    first = _run_cli(
        tmp_path, "oneshot", "--prompt", "one", "--timeout", "1",
        "--request-id", "stable", "--receipt-dir", str(receipt_dir),
    )
    fake.write_text("#!/bin/sh\nprintf 'second\\n'\n", encoding="utf-8")
    second = _run_cli(
        tmp_path, "oneshot", "--prompt", "two", "--timeout", "1",
        "--request-id", "stable", "--receipt-dir", str(receipt_dir),
    )
    first_payload, second_payload = json.loads(first.stdout), json.loads(second.stdout)
    assert first_payload["receipt_path"] != second_payload["receipt_path"]
    assert Path(first_payload["artifacts"]["stdout"]).read_text() == "first\n"
    assert Path(second_payload["artifacts"]["stdout"]).read_text() == "second\n"


def test_subscription_route_remains_allowed(tmp_path):
    fake = tmp_path / "hermes"
    fake.write_text(
        "#!/bin/sh\nprintf '{\"usage\":{\"input_tokens\":3}}'\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    result = _run_cli(
        tmp_path, "oneshot", "--prompt", "safe",
        "--provider", "openai-codex", "--model", "gpt-5.6-codex",
        "--timeout", "1", "--receipt-dir", str(tmp_path / "receipts"),
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["usage"] == {"input_tokens": 3}
    assert payload["usage_source"] == "provider_reported"
    assert payload["elapsed_seconds"] >= 0


@pytest.mark.parametrize(
    ("body", "exit_code", "expected_outcome"),
    (("authentication failed", 1, "auth_failure"),
     ("rate limit exceeded", 1, "quota_failure"),
     ("not-json output", 0, "completed")),
)
def test_receipt_classifies_auth_quota_and_missing_usage(tmp_path, body, exit_code, expected_outcome):
    fake = tmp_path / "hermes"
    fake.write_text(
        f"#!/bin/sh\nprintf '%s' {body!r}\nexit {exit_code}\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    result = _run_cli(
        tmp_path, "oneshot", "--prompt", "fixture", "--timeout", "1",
        "--receipt-dir", str(tmp_path / "receipts"),
    )

    payload = json.loads(result.stdout)
    assert payload["outcome"] == expected_outcome
    assert payload["usage"] is None
    assert payload["usage_source"] == "provider_did_not_report"
    assert Path(payload["receipt_path"]).exists()


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf"])
def test_nonfinite_timeout_is_rejected_before_process(tmp_path, timeout):
    marker = tmp_path / "called"
    fake = tmp_path / "hermes"
    fake.write_text(f"#!/bin/sh\necho called > {str(marker)!r}\n")
    fake.chmod(0o755)
    result = _run_cli(tmp_path, "oneshot", "--prompt", "fixture", "--timeout=" + timeout)
    assert result.returncode == 2
    assert "finite" in result.stderr
    assert not marker.exists()


def test_linux_process_metadata_is_redacted_in_every_persisted_state(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("hermes_metadata_fixture", SCRIPT)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    secret = "fixture-process-secret-123456"
    monkeypatch.setenv("TEST_API_TOKEN", secret)
    monkeypatch.setattr(bridge, "validate_subscription_route", lambda *a, **k: {"provider": "fixture-local"})
    monkeypatch.setattr(bridge, "_process_identity", lambda pid: {"pid": pid, "command": "hermes -z " + secret})
    writes = []
    original = bridge._atomic_json
    def observe(path, payload):
        original(path, payload)
        writes.append(path.read_text())
    monkeypatch.setattr(bridge, "_atomic_json", observe)
    code = bridge.run_command([sys.executable, "-c", "print('fixture')"],
                              receipt_dir=tmp_path / "receipts", timeout=1)
    output = capsys.readouterr().out
    assert code == 0
    assert secret not in output
    assert writes and all(secret not in text for text in writes)
    assert "[REDACTED]" in output
