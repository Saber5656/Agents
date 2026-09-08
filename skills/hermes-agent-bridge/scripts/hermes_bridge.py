#!/usr/bin/env python3
"""Small bounded, subscription-safe wrapper for the Hermes Agent CLI."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


DEFAULT_TIMEOUT_SECONDS = 30.0
SUBSCRIPTION_PROVIDERS = frozenset({"openai-codex"})
PAID_ROUTE_ENV_VARS = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CODEX_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "XAI_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "TOGETHER_API_KEY",
        "DEEPSEEK_API_KEY",
        "COHERE_API_KEY",
    }
)


class RoutePolicyError(RuntimeError):
    """The requested route is not proven to use the ChatGPT subscription."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _redact_text(text: str, env: Mapping[str, str]) -> str:
    result = text
    for key, value in sorted(env.items(), key=lambda pair: -len(pair[1])):
        if value and len(value) >= 4 and re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|AUTH|PRIVATE_KEY", key, re.I):
            result = result.replace(value, "[REDACTED]")
    result = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        result,
        flags=re.S,
    )
    result = re.sub(
        r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s\"\\]+",
        r"\1[REDACTED]",
        result,
    )
    return re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:ant-)?[A-Za-z0-9_-]{20,})",
        "[REDACTED]",
        result,
    )


def _redact_value(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return _redact_text(value, env)
    if isinstance(value, list):
        return [_redact_value(item, env) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, env) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item, env) for key, item in value.items()}
    return value


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.is_symlink():
        raise ValueError(f"receipt path must not be a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _private_text(path: Path, text: str) -> None:
    """Write a receipt sidecar with a private mode regardless of umask."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            stream.write(text)
    finally:
        if fd is not None:
            os.close(fd)


def _receipt_context(receipt_dir: str | Path | None, request_id: str, request: Mapping[str, Any]):
    if receipt_dir is None:
        return None
    if request_id in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", request_id):
        raise ValueError("request_id must be a single filesystem-safe name")
    parent = Path(receipt_dir).expanduser()
    if parent.exists() and parent.is_symlink():
        raise ValueError("receipt directory must not be a symlink")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        parent.chmod(0o700)
    except OSError:
        pass
    base_root = parent.resolve() / request_id
    if base_root.exists() and base_root.is_symlink():
        raise ValueError("receipt request directory must not be a symlink")
    base_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        base_root.chmod(0o700)
    except OSError:
        pass
    root = base_root / f"attempt-{secrets.token_hex(8)}"
    root.mkdir(mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    request_payload = dict(request)
    request_payload["attempt_id"] = root.name
    context = {
        "root": root,
        "request": root / "request.json",
        "state": root / "state.json",
        "result": root / "result.json",
    }
    _atomic_json(context["request"], request_payload)
    _atomic_json(context["state"], {"status": "pending", "request_id": request_id})
    return context


def _save_receipt(context, payload: Mapping[str, Any], stdout: str = "", stderr: str = "") -> dict[str, str]:
    if context is None:
        return {}
    root = context["root"]
    stdout_path = root / "stdout.txt"
    stderr_path = root / "stderr.txt"
    _private_text(stdout_path, stdout)
    _private_text(stderr_path, stderr)
    artifacts = {
        "request": str(context["request"]),
        "state": str(context["state"]),
        "result": str(context["result"]),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    result = dict(payload)
    result["artifacts"] = artifacts
    result["receipt_path"] = str(context["result"])
    _atomic_json(context["state"], result)
    _atomic_json(context["result"], result)
    return artifacts


def read_receipt(path: str | Path) -> dict[str, Any]:
    """Read a completed bridge receipt without retrying its external command."""
    receipt = Path(path).expanduser()
    if receipt.is_symlink():
        raise ValueError("receipt path must not be a symlink")
    return json.loads(receipt.read_text(encoding="utf-8"))


def validate_subscription_route(
    provider: str | None = None,
    model: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Fail closed unless an override is bound to Hermes' OAuth subscription route."""
    environment = dict(os.environ if env is None else env)
    paid = sorted(key for key in PAID_ROUTE_ENV_VARS if environment.get(key))
    if paid:
        raise RoutePolicyError("paid API route is configured: " + ", ".join(paid))

    config = _read_hermes_config(environment)
    configured_provider = environment.get("HERMES_INFERENCE_PROVIDER") or config["provider"]
    configured_model = environment.get("HERMES_INFERENCE_MODEL") or config["model"]
    effective_provider = provider if provider is not None else configured_provider
    effective_model = model if model is not None else configured_model
    fallback = sorted(
        key for key in ("HERMES_FALLBACK_PROVIDER", "HERMES_FALLBACK_MODEL")
        if environment.get(key)
    )
    if fallback:
        raise RoutePolicyError("fallback route is not allowed: " + ", ".join(fallback))
    configured_fallbacks = config["fallbacks"]
    if any(item not in SUBSCRIPTION_PROVIDERS for item in configured_fallbacks):
        raise RoutePolicyError("Hermes config contains a non-subscription fallback route")
    if provider is None and configured_provider not in SUBSCRIPTION_PROVIDERS:
        raise RoutePolicyError("Hermes config is not bound to the openai-codex subscription route")
    if effective_provider is not None and effective_provider not in SUBSCRIPTION_PROVIDERS:
        raise RoutePolicyError(
            f"provider is outside the ChatGPT subscription route: {effective_provider}"
        )
    if effective_model is not None and effective_provider is None:
        raise RoutePolicyError("model override requires an explicit openai-codex provider")
    return {
        "provider": "openai-codex",
        "model": effective_model,
        "source": "explicit override" if provider is not None else "Hermes config verified",
        "subscription_route": True,
        "config_path": config["path"],
    }


def _read_hermes_config(env: Mapping[str, str]) -> dict[str, Any]:
    configured = env.get("HERMES_CONFIG")
    if configured:
        path = Path(configured).expanduser()
    else:
        home = Path(
            env.get("HERMES_HOME", str(Path(env.get("HOME", str(Path.home()))) / ".hermes"))
        )
        path = home / "config.yaml"
    if not path.is_file() or path.is_symlink():
        raise RoutePolicyError("Hermes config is unavailable for route verification")
    try:
        import yaml
    except ImportError as exc:
        raise RoutePolicyError("PyYAML is required to verify Hermes config") from exc
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RoutePolicyError("Hermes config could not be parsed") from exc
    if not isinstance(value, dict) or not isinstance(value.get("model"), dict):
        raise RoutePolicyError("Hermes config has no verifiable model route")
    model = value["model"]
    provider = model.get("provider")
    default_model = model.get("default")
    if provider is not None and not isinstance(provider, str):
        raise RoutePolicyError("Hermes config provider is malformed")
    if default_model is not None and not isinstance(default_model, str):
        raise RoutePolicyError("Hermes config model is malformed")
    fallbacks: list[str] = []
    configured_fallbacks = value.get("fallback_providers", [])
    if configured_fallbacks is None:
        configured_fallbacks = []
    if not isinstance(configured_fallbacks, list) or not all(isinstance(item, str) for item in configured_fallbacks):
        raise RoutePolicyError("Hermes config fallback providers are malformed")
    fallbacks.extend(configured_fallbacks)
    fallback_model = value.get("fallback_model")
    if isinstance(fallback_model, dict) and fallback_model.get("provider"):
        if not isinstance(fallback_model["provider"], str):
            raise RoutePolicyError("Hermes config fallback model is malformed")
        fallbacks.append(fallback_model["provider"])
    elif fallback_model not in (None, {}):
        raise RoutePolicyError("Hermes config fallback model is malformed")
    return {"path": str(path), "provider": provider, "model": default_model, "fallbacks": fallbacks}


def _process_identity(pid: int) -> dict[str, Any]:
    identity: dict[str, Any] = {"pid": pid, "observed_at": _now()}
    if os.name == "posix":
        try:
            identity["process_group"] = os.getpgid(pid)
        except OSError:
            identity["process_group"] = None
    try:
        os.kill(pid, 0)
    except OSError:
        identity["alive"] = False
        return identity
    identity["alive"] = True
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        fields = stat_path.read_text(encoding="utf-8").split()
        if len(fields) > 21:
            identity["start_ticks"] = fields[21]
    except (OSError, ValueError):
        pass
    try:
        identity["command"] = (
            Path(f"/proc/{pid}/cmdline")
            .read_bytes()
            .replace(b"\0", b" ")
            .decode(errors="replace")
            .strip()
        )
    except OSError:
        pass
    return identity


def _group_members(process_group: int | None) -> list[dict[str, Any]] | None:
    ps = shutil.which("ps") or next(
        (candidate for candidate in ("/bin/ps", "/usr/bin/ps") if Path(candidate).exists()),
        None,
    )
    if not process_group or ps is None:
        return None
    try:
        result = subprocess.run(
            [ps, "-eo", "pid=,pgid=,stat="], capture_output=True, text=True, timeout=1
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    members = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            pid, pgid = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        if pgid == process_group and not fields[2].startswith("Z"):
            members.append({"pid": pid, "process_group": pgid, "state": fields[2]})
    return members


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> dict[str, Any]:
    identity = _process_identity(proc.pid)
    terminated = False
    signal_sent = None
    if os.name == "posix" and identity.get("process_group"):
        try:
            os.killpg(identity["process_group"], signal.SIGTERM)
            terminated = True
            signal_sent = "SIGTERM"
        except ProcessLookupError:
            terminated = True
        except OSError:
            pass
    if not terminated:
        try:
            proc.terminate()
            terminated = True
            signal_sent = "terminate"
        except OSError:
            pass
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        if os.name == "posix" and identity.get("process_group"):
            try:
                os.killpg(identity["process_group"], signal.SIGKILL)
                signal_sent = "SIGKILL"
            except OSError:
                pass
        else:
            try:
                proc.kill()
                signal_sent = "kill"
            except OSError:
                pass
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
    remaining = _group_members(identity.get("process_group"))
    if remaining:
        if os.name == "posix" and identity.get("process_group"):
            try:
                os.killpg(identity["process_group"], signal.SIGKILL)
                signal_sent = "SIGKILL"
            except OSError:
                pass
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        remaining = _group_members(identity.get("process_group"))
    verified = remaining == []
    terminated = proc.poll() is not None and verified
    return {
        "requested": True,
        "terminated": terminated,
        "signal": signal_sent,
        "process_identity": identity,
        "remaining_members": remaining,
        "verified": verified,
    }


def _usage_from_output(stdout: str) -> tuple[dict[str, Any] | None, str]:
    def find_usage(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            candidate = value.get("usage")
            if isinstance(candidate, dict):
                numeric = {
                    key: item
                    for key, item in candidate.items()
                    if isinstance(item, (int, float)) and not isinstance(item, bool)
                }
                if numeric:
                    return numeric
            for child in value.values():
                found = find_usage(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_usage(child)
                if found is not None:
                    return found
        return None

    candidates = [stdout.strip()]
    candidates.extend(line.strip() for line in stdout.splitlines())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        usage = find_usage(value)
        if usage is not None:
            return usage, "provider_reported"
    return None, "provider_did_not_report"


def _outcome_kind(returncode: int, stdout: str, stderr: str, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if returncode == 0:
        return "completed"
    combined = (stdout + "\n" + stderr).lower()
    if any(token in combined for token in ("unauthorized", "authentication", "logged out", "login")):
        return "auth_failure"
    if any(token in combined for token in ("rate limit", "quota", "usage limit")):
        return "quota_failure"
    return "process_failure"


def run_command(
    args: list[str],
    cwd: str | None = None,
    timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    *,
    provider: str | None = None,
    model: str | None = None,
    receipt_dir: str | Path | None = None,
    request_id: str | None = None,
) -> int:
    if timeout is None or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive and finite")
    environment = dict(os.environ)
    request_id = request_id or secrets.token_hex(16)
    safe_args = _redact_value(args, environment)
    request = {
        "schema": "hermes-bridge-request/v1",
        "request_id": request_id,
        "command": safe_args,
        "cwd": _redact_text(cwd or "", environment) or None,
        "requested_route": {"provider": provider, "model": model},
        "timeout_seconds": timeout,
        "started_at": _now(),
    }
    base: dict[str, Any] = {
        "command": safe_args,
        "cwd": _redact_text(cwd or "", environment) or None,
        "request_id": request_id,
    }
    try:
        context = _receipt_context(receipt_dir, request_id, request)
    except ValueError as exc:
        payload = {
            **base,
            "status": "blocked",
            "error": "receipt_policy",
            "reason": str(exc),
            "action": "rejected_before_process",
            "returncode": 126,
            "reconciliation_required": False,
            "resumable": False,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 126
    try:
        route = validate_subscription_route(provider, model, env=environment)
    except RoutePolicyError as exc:
        payload = {
            **base,
            "status": "blocked",
            "error": "route_policy",
            "reason": str(exc),
            "action": "rejected_before_process",
            "returncode": 126,
            "route": {"provider": provider, "model": model, "source": "request"},
            "reconciliation_required": False,
            "resumable": False,
        }
        artifacts = _save_receipt(context, payload)
        if artifacts:
            payload["artifacts"] = artifacts
            payload["receipt_path"] = str(context["result"])
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 126

    started = time.monotonic()
    process_identity = None
    stdout = ""
    stderr = ""
    termination = None
    timed_out = False
    returncode = 127
    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        process_identity = _process_identity(proc.pid)
        if context is not None:
            _atomic_json(
                context["state"],
                {"status": "running", "request_id": request_id, "process_identity": process_identity},
            )
        try:
            out_bytes, err_bytes = proc.communicate(timeout=timeout)
            stdout, stderr = _decode_output(out_bytes), _decode_output(err_bytes)
            returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = _decode_output(exc.stdout)
            stderr = _decode_output(exc.stderr)
            termination = _terminate_process_group(proc)
            try:
                out_bytes, err_bytes = proc.communicate(timeout=0.5)
                stdout = _decode_output(out_bytes) or stdout
                stderr = _decode_output(err_bytes) or stderr
            except subprocess.TimeoutExpired as tail:
                stdout = _decode_output(tail.stdout) or stdout
                stderr = _decode_output(tail.stderr) or stderr
            returncode = 124
    except FileNotFoundError as exc:
        stderr = str(exc)
        returncode = 127
    except OSError as exc:
        stderr = str(exc)
        returncode = 126

    elapsed = max(0.0, time.monotonic() - started)
    usage, usage_source = _usage_from_output(stdout)
    safe_stdout = _redact_text(stdout, environment)
    safe_stderr = _redact_text(stderr, environment)
    kind = _outcome_kind(returncode, safe_stdout, safe_stderr, timed_out)
    payload = {
        **base,
        "status": "timeout" if timed_out else ("completed" if returncode == 0 else "failed"),
        "route": route,
        "returncode": 124 if timed_out else returncode,
        "stdout": safe_stdout,
        "stderr": safe_stderr,
        "elapsed_seconds": elapsed,
        "usage": usage,
        "usage_source": usage_source,
        "outcome": kind,
        "process_identity": process_identity,
        "termination": termination,
        "reconciliation_required": timed_out,
        "resumable": not timed_out and returncode == 0,
    }
    if timed_out:
        payload["error"] = "timeout"
        payload["timeout_seconds"] = timeout
    elif returncode == 127 and process_identity is None:
        payload["error"] = "executable_not_found"
    elif returncode != 0:
        payload["error"] = "execution_error"
    artifacts = _save_receipt(context, payload, safe_stdout, safe_stderr)
    if artifacts:
        payload["artifacts"] = artifacts
        payload["receipt_path"] = str(context["result"])
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 124 if timed_out else returncode


def _positive_finite_timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be positive and finite") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive and finite")
    return parsed


def _add_execution_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=_positive_finite_timeout, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--receipt-dir", help="Private directory for durable request/result artifacts.")
    parser.add_argument("--request-id", help="Stable id for a resumable receipt.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Call Hermes Agent as a bounded, subscription-safe bridge."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    oneshot = sub.add_parser("oneshot", help="Run Hermes and return final stdout.")
    oneshot.add_argument("--prompt", required=True)
    oneshot.add_argument("--model")
    oneshot.add_argument("--provider")
    oneshot.add_argument("--toolsets")
    oneshot.add_argument("--skills")
    oneshot.add_argument("--cwd")
    _add_execution_options(oneshot)
    oneshot.add_argument("--timeout-note", default="caller-managed", help=argparse.SUPPRESS)

    send = sub.add_parser("send", help="Send a message through Hermes gateway.")
    send.add_argument("--target", required=True)
    send.add_argument("--message")
    send.add_argument("--file")
    send.add_argument("--subject")
    _add_execution_options(send)

    targets = sub.add_parser("list-targets", help="List Hermes send targets.")
    targets.add_argument("--platform")
    _add_execution_options(targets)

    resume = sub.add_parser("resume", help="Read a durable receipt without rerunning Hermes.")
    resume.add_argument("--receipt", required=True)

    sub.add_parser("mcp-info", help="Print Hermes MCP bridge guidance.")
    return parser


def main() -> int:
    parser = build_parser()
    ns = parser.parse_args()

    if ns.command == "resume":
        print(json.dumps(read_receipt(ns.receipt), ensure_ascii=False, indent=2))
        return 0

    if ns.command == "oneshot":
        cmd = ["hermes", "-z", ns.prompt]
        if ns.model:
            cmd.extend(["--model", ns.model])
        cmd.extend(["--provider", ns.provider or "openai-codex"])
        if ns.toolsets:
            cmd.extend(["--toolsets", ns.toolsets])
        if ns.skills:
            cmd.extend(["--skills", ns.skills])
        return run_command(
            cmd,
            cwd=ns.cwd,
            timeout=ns.timeout,
            provider=ns.provider,
            model=ns.model,
            receipt_dir=ns.receipt_dir,
            request_id=ns.request_id,
        )

    if ns.command == "send":
        if bool(ns.message) == bool(ns.file):
            parser.error("send requires exactly one of --message or --file")
        cmd = ["hermes", "send", "--to", ns.target, "--json"]
        if ns.subject:
            cmd.extend(["--subject", ns.subject])
        if ns.file:
            cmd.extend(["--file", str(Path(ns.file).expanduser())])
        else:
            cmd.append(ns.message)
        return run_command(
            cmd,
            timeout=ns.timeout,
            receipt_dir=ns.receipt_dir,
            request_id=ns.request_id,
        )

    if ns.command == "list-targets":
        cmd = ["hermes", "send", "--list", "--json"]
        if ns.platform:
            cmd.append(ns.platform)
        return run_command(
            cmd,
            timeout=ns.timeout,
            receipt_dir=ns.receipt_dir,
            request_id=ns.request_id,
        )

    if ns.command == "mcp-info":
        info = {
            "command": "hermes mcp serve",
            "purpose": "Expose Hermes conversations, messages, live events, and send targets to an MCP client.",
            "important_tools": ["messages_send", "events_poll", "events_wait", "messages_read", "channels_list"],
            "limitation": "A SKILL.md alone cannot receive asynchronous Hermes/Discord replies after the agent turn ends. Configure MCP, polling automation, or a callback runtime.",
        }
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    parser.error(f"unknown command: {ns.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
