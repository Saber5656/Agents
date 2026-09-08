import json
import subprocess
import sys
from pathlib import Path

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
