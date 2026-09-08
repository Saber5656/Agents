#!/usr/bin/env python3
"""Small bounded, subscription-safe wrapper for the Hermes Agent CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
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


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _receipt_context(receipt_dir: str | Path | None, request_id: str, request: Mapping[str, Any]):
    if receipt_dir is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", request_id):
        raise ValueError("request_id must be a single filesystem-safe name")
    root = Path(receipt_dir).expanduser().resolve() / request_id
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    context = {
        "root": root,
        "request": root / "request.json",
        "state": root / "state.json",
        "result": root / "result.json",
    }
    _atomic_json(context["request"], dict(request))
    _atomic_json(context["state"], {"status": "pending", "request_id": request_id})
    return context


def _save_receipt(context, payload: Mapping[str, Any], stdout: str = "", stderr: str = "") -> dict[str, str]:
    if context is None:
        return {}
    root = context["root"]
    stdout_path = root / "stdout.txt"
    stderr_path = root / "stderr.txt"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
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
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


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

    configured_provider = environment.get("HERMES_INFERENCE_PROVIDER")
    configured_model = environment.get("HERMES_INFERENCE_MODEL")
    effective_provider = provider if provider is not None else configured_provider
    effective_model = model if model is not None else configured_model
    fallback = sorted(
        key for key in ("HERMES_FALLBACK_PROVIDER", "HERMES_FALLBACK_MODEL")
        if environment.get(key)
    )
    if fallback:
        raise RoutePolicyError("fallback route is not allowed: " + ", ".join(fallback))
    if effective_provider is not None and effective_provider not in SUBSCRIPTION_PROVIDERS:
        raise RoutePolicyError(
            f"provider is outside the ChatGPT subscription route: {effective_provider}"
        )
    if effective_model is not None and effective_provider is None:
        raise RoutePolicyError("model override requires an explicit openai-codex provider")
    return {
        "provider": effective_provider,
        "model": effective_model,
        "source": "explicit override" if provider is not None else "Hermes configured route",
        "subscription_route": effective_provider in SUBSCRIPTION_PROVIDERS,
    }


def _process_identity(pid: int) -> dict[str, Any]:
    identity: dict[str, Any] = {"pid": pid, "observed_at": _now()}
    if os.name == "posix":
        try:
            identity["process_group"] = os.getpgid(pid)
        except OSError:
            identity["process_group"] = None
    return identity


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
    return {
        "requested": True,
        "terminated": terminated,
        "signal": signal_sent,
        "process_identity": identity,
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
    if timeout is None or timeout <= 0:
        raise ValueError("timeout must be positive")
    request_id = request_id or secrets.token_hex(16)
    request = {
        "schema": "hermes-bridge-request/v1",
        "request_id": request_id,
        "command": args,
        "cwd": cwd,
        "requested_route": {"provider": provider, "model": model},
        "timeout_seconds": timeout,
        "started_at": _now(),
    }
    context = _receipt_context(receipt_dir, request_id, request)
    base: dict[str, Any] = {"command": args, "cwd": cwd, "request_id": request_id}
    try:
        route = validate_subscription_route(provider, model)
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
    kind = _outcome_kind(returncode, stdout, stderr, timed_out)
    payload = {
        **base,
        "status": "timeout" if timed_out else ("completed" if returncode == 0 else "failed"),
        "route": route,
        "returncode": 124 if timed_out else returncode,
        "stdout": stdout,
        "stderr": stderr,
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
    artifacts = _save_receipt(context, payload, stdout, stderr)
    if artifacts:
        payload["artifacts"] = artifacts
        payload["receipt_path"] = str(context["result"])
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 124 if timed_out else returncode


def _add_execution_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
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
        if ns.provider:
            cmd.extend(["--provider", ns.provider])
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
