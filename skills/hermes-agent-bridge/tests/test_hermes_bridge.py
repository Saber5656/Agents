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
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "oneshot", "--prompt", "bounded", "--timeout", "0.05"],
        text=True,
        capture_output=True,
        env={"PATH": str(tmp_path)},
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 124
    assert payload["error"] == "timeout"
    assert payload["timeout_seconds"] == 0.05
    assert payload["command"][:2] == ["hermes", "-z"]


def test_missing_executable_is_structured_without_fallback(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "oneshot", "--prompt", "missing"],
        text=True,
        capture_output=True,
        env={"PATH": str(tmp_path)},
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 127
    assert payload["error"] == "executable_not_found"
    assert payload["command"][0] == "hermes"


def _run_cli(tmp_path, *args, extra_env=None):
    env = {"PATH": str(tmp_path), "PYTHONPATH": ""}
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        env=env,
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

    child = int(child_pid.read_text())
    try:
        os.kill(child, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("timeout left the fake Hermes child running")


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
