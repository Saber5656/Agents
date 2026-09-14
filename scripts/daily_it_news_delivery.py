"""Publish a generated IT-news artifact and notify Discord.

This module deliberately has no dependency on the retired harness.  Each operation
is resumable from its run receipt and treats an uncertain remote operation as unknown.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote
from daily_it_news_runtime import ProcessStartError, read_verified, run_command


class DeliveryError(RuntimeError):
    pass


class RetryableDeliveryError(DeliveryError):
    pass


_SNOWFLAKE = re.compile(r"^[1-9][0-9]{16,19}$")
_TARGET = re.compile(r"^discord:[1-9][0-9]{16,19}$")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(args: list[str], cwd: Path | None = None, *, timeout: int = 60) -> str:
    try:
        p = run_command(["git", *args], cwd=cwd, text=True, capture_output=True,
                        timeout=timeout, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except subprocess.TimeoutExpired as exc:
        raise RetryableDeliveryError("git operation timed out") from exc
    if p.returncode:
        diagnostic = "\n".join(part.strip() for part in (p.stderr, p.stdout) if part.strip())
        error = RetryableDeliveryError if args[0] in {"fetch", "push", "ls-remote"} else DeliveryError
        raise error(f"git {' '.join(args[:2])} failed: {diagnostic[:300]}")
    return p.stdout.strip()


def _receipt(run_root: Path, name: str, payload: dict) -> None:
    run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = run_root / name
    tmp = target.with_name("." + target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, target)


def _validate_path(relative_path: str) -> Path:
    p = Path(relative_path)
    if p.is_absolute() or ".." in p.parts or not p.parts or any(part in {".git", ".gitmodules"} for part in p.parts):
        raise DeliveryError("unsafe artifact path")
    return p


def _public_url(repo_url: str, branch: str, relative_path: str) -> str:
    clean = repo_url[:-4] if repo_url.endswith(".git") else repo_url
    if clean.startswith("git@github.com:"):
        clean = "https://github.com/" + clean.split(":", 1)[1]
    return clean.rstrip("/") + "/blob/" + quote(branch, safe="/") + "/" + quote(relative_path, safe="/")


def publish_artifact(repo_url: str, branch: str, artifact: Path | str, relative_path: str,
                     run_root: Path | str, git_name: str, git_email: str,
                     gitleaks_bin: str, *, expected_sha256: str | None = None) -> dict:
    """Publish one immutable byte snapshot, retrying only remote Git failures."""
    artifact, run_root = Path(artifact), Path(run_root)
    _validate_path(relative_path)
    content = read_verified(artifact, expected_sha256)
    for attempt in range(3):
        try:
            return _publish_artifact_once(repo_url, branch, artifact, relative_path, run_root,
                                          git_name, git_email, gitleaks_bin, content)
        except RetryableDeliveryError as exc:
            _receipt(run_root, f"publication-attempt-{attempt + 1}.json",
                     {"status": "failed", "error": str(exc)})
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))


def _publish_artifact_once(repo_url, branch, artifact, relative_path, run_root,
                           git_name, git_email, gitleaks_bin, content):
    path = _validate_path(relative_path)
    content_sha = hashlib.sha256(content).hexdigest()
    result = {"artifact_sha256": content_sha, "repo_url": repo_url, "branch": branch,
              "path": relative_path, "status": "failed"}
    run_root.mkdir(parents=True, exist_ok=True)
    checkout = Path(tempfile.mkdtemp(prefix="delivery-", dir=run_root))
    try:
        shutil.rmtree(checkout)
        _git(["init", str(checkout)])
        _git(["remote", "add", "origin", repo_url], checkout)
        _git(["fetch", "--depth=1", "--filter=blob:none", "origin", f"refs/heads/{branch}"], checkout)
        fetched_head = _git(["rev-parse", "FETCH_HEAD"], checkout)
        _git(["sparse-checkout", "init", "--no-cone"], checkout)
        _git(["sparse-checkout", "set", "--no-cone", relative_path], checkout)
        _git(["checkout", "--detach", fetched_head], checkout)
        destination = checkout / path
        parent = checkout
        for part in path.parts:
            parent = parent / part
            if parent.is_symlink():
                raise DeliveryError("artifact destination or parent is a symlink")
        if destination.is_file() and not destination.is_symlink() and _sha(destination) == content_sha:
            result.update(status="already_present", commit=fetched_head, url=_public_url(repo_url, fetched_head, relative_path))
            _receipt(run_root, "publish-success.json", result)
            return result
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        if not gitleaks_bin:
            raise DeliveryError("gitleaks is required")
        scan_dir = Path(tempfile.mkdtemp(prefix="artifact-scan-", dir=run_root))
        try:
            (scan_dir / artifact.name).write_bytes(content)
            scan = run_command([gitleaks_bin, "dir", str(scan_dir), "--no-banner", "--redact"], text=True, capture_output=True, timeout=120)
            if scan.returncode:
                raise DeliveryError("gitleaks detected a secret")
            if re.search(r"(?:/Users/|/home/)[^\s\n]+", content.decode("utf-8")):
                raise DeliveryError("artifact contains a private home path")
        finally:
            shutil.rmtree(scan_dir, ignore_errors=True)
        _git(["config", "user.name", git_name], checkout)
        _git(["config", "user.email", git_email], checkout)
        _git(["add", "--", relative_path], checkout)
        # Keep generated Markdown byte-identical, allowing only harmless EOF blanks.
        whitespace = _git(["config", "--default", "", "--get", "core.whitespace"], checkout)
        whitespace = f"{whitespace},-blank-at-eof" if whitespace else "-blank-at-eof"
        _git(["-c", f"core.whitespace={whitespace}",
              "diff", "--cached", "--check"], checkout)
        selected = _git(["diff", "--cached", "--name-only"], checkout).splitlines()
        if selected != [relative_path]:
            raise DeliveryError("staged publication includes an unexpected path")
        _receipt(run_root, "publication-review.json", {"review_type": "automated_artifact_review",
                 "path": relative_path, "sha256": content_sha, "secret_scan": "passed",
                 "private_paths": "absent", "staged_scope": selected, "diff_check": "passed",
                 "tdd": "not_applicable_to_generated_documents"})
        _git(["commit", "-m", f"docs: publish daily report {artifact.name}"], checkout)
        local_commit = _git(["rev-parse", "HEAD"], checkout)
        changed = _git(["diff-tree", "--no-commit-id", "--name-only", "-r", local_commit], checkout).splitlines()
        if changed != [relative_path]:
            raise DeliveryError("publication commit includes an unexpected path")
        push_error = None
        try:
            _git(["push", "origin", f"HEAD:refs/heads/{branch}"], checkout)
        except RetryableDeliveryError as exc:
            push_error = exc
        _git(["fetch", "--depth=1", "origin", f"refs/heads/{branch}"], checkout)
        observed = _git(["rev-parse", "FETCH_HEAD"], checkout)
        tree = _git(["ls-tree", "FETCH_HEAD", "--", relative_path], checkout).split()
        remote_blob = tree[2] if len(tree) >= 3 else None
        if remote_blob != _git(["hash-object", str(destination)], checkout):
            if push_error:
                raise push_error
            raise DeliveryError("published artifact blob verification failed")
        result.update(status="published", commit=observed, url=_public_url(repo_url, observed, relative_path))
        _receipt(run_root, "publish-success.json", result)
        return result
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        _receipt(run_root, "publish-failure.json", result)
        if isinstance(exc, DeliveryError):
            raise
        raise DeliveryError(str(exc)) from exc
    finally:
        shutil.rmtree(checkout, ignore_errors=True)


def _bridge_payload(wrapper):
    if not isinstance(wrapper, dict):
        return {}
    try:
        nested = wrapper.get("stdout")
        value = json.loads(nested) if isinstance(nested, str) else wrapper
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _confirmed_message(payload, returncode, target):
    msgid = payload.get("message_id")
    if (returncode == 0 and payload.get("success") is True
            and payload.get("platform") == "discord"
            and str(payload.get("chat_id")) == target.removeprefix("discord:")
            and _SNOWFLAKE.fullmatch(str(msgid or ""))):
        return str(msgid)
    return None


def _reconcile_bridge(base, request_id, target, message_file, message):
    """Only a receipt bound to this exact request, target and message proves delivery."""
    try:
        if message_file.is_symlink() or message_file.read_text() != message:
            return None
    except OSError:
        return None
    for attempt in (base / request_id).glob("attempt-*"):
        try:
            request_path = attempt / "request.json"
            result_path = attempt / "result.json"
            if not result_path.is_file():
                result_path = attempt / "state.json"
            if any(path.is_symlink() for path in (attempt, request_path, result_path)):
                continue
            request = json.loads(request_path.read_text())
            result = json.loads(result_path.read_text())
            command = request["command"]
            expected = ["hermes", "send", "--to", target, "--json", "--file", str(message_file)]
            if (request.get("request_id") != request_id or result.get("request_id") != request_id
                    or command != expected):
                continue
            message_id = _confirmed_message(_bridge_payload(result), result.get("returncode"), target)
            if message_id:
                return message_id
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


def notify_discord(summary_date: str, news_url: str, advice_url: str, artifact_sha256: str,
                   run_root: Path | str, hermes_python: str, target: str,
                   request_id: str, hermes_bridge: Path | str | None = None) -> dict:
    """Send a short immutable-link message through the Hermes bridge."""
    run_root = Path(run_root)
    if not _TARGET.fullmatch(target):
        return {"delivery_status": "failed", "message_id": None, "error": "invalid Discord target"}
    if request_id in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", request_id):
        return {"delivery_status": "failed", "message_id": None, "error": "invalid delivery request id"}
    bridge = Path(hermes_bridge or (Path(os.environ.get("SKILLS_ROOT", ".")) / "hermes-agent-bridge/scripts/hermes_bridge.py"))
    message = f"ITニュース {summary_date}\nニュース: {news_url}\n助言: {advice_url}"
    key = hashlib.sha256(f"{artifact_sha256}:{target}".encode()).hexdigest()
    sent = run_root / "discord-bridge" / f"sent-{key}.json"
    if sent.exists():
        try:
            prior = json.loads(sent.read_text())
            if (not sent.is_symlink() and prior.get("target") == target
                    and prior.get("artifact_sha256") == artifact_sha256
                    and _SNOWFLAKE.fullmatch(str(prior.get("message_id") or ""))):
                return {"delivery_status": "skipped", "message_id": prior["message_id"]}
        except (OSError, ValueError, AttributeError):
            pass
        return {"delivery_status": "unknown", "message_id": None, "error": "invalid sent receipt"}
    intent = run_root / "discord-bridge" / f"intent-{key}.json"
    message_file = run_root / "discord-bridge" / f"message-{key}.txt"
    if intent.exists() and not sent.exists():
        try:
            prior = json.loads(intent.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prior = {}
        if not isinstance(prior, dict):
            prior = {}
        if prior.get("artifact_sha256") == artifact_sha256 and prior.get("target") == target:
            message_id = _reconcile_bridge(run_root / "discord-bridge", request_id, target,
                                           message_file, prior.get("message"))
            if message_id:
                _receipt(sent.parent, sent.name, {"message_id": message_id,
                         "artifact_sha256": artifact_sha256, "target": target})
                return {"delivery_status": "delivered", "message_id": message_id}
            return {"delivery_status": "unknown", "message_id": None, "error": "existing intent requires reconciliation"}
        return {"delivery_status": "unknown", "message_id": None, "error": "invalid delivery intent"}
    if not bridge.is_file():
        return {"delivery_status": "failed", "message_id": None, "error": "delivery bridge is missing"}
    message_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    message_file.write_text(message, encoding="utf-8")
    message_file.chmod(0o600)
    _receipt(run_root / "discord-bridge", f"intent-{key}.json", {"artifact_sha256": artifact_sha256, "target": target, "message": message})
    cmd = [hermes_python, str(bridge), "send", "--target", target, "--file", str(message_file),
           "--receipt-dir", str(run_root / "discord-bridge"), "--request-id", request_id]
    try:
        env = dict(os.environ)
        env["HERMES_INFERENCE_PROVIDER"] = "openai-codex"
        try:
            p = run_command(cmd, text=True, capture_output=True, timeout=90, env=env)
        except ProcessStartError as exc:
            # No process was started, so retrying cannot duplicate a message.
            intent.unlink(missing_ok=True)
            return {"delivery_status": "failed", "message_id": None, "error": str(exc)}
        _receipt(run_root / "discord-bridge", f"bridge-{key}.stdout.log", {"stdout": p.stdout})
        _receipt(run_root / "discord-bridge", f"bridge-{key}.stderr.log", {"stderr": p.stderr})
        try:
            wrapper = json.loads(p.stdout)
        except (ValueError, TypeError):
            wrapper = {}
        if not isinstance(wrapper, dict):
            wrapper = {}
        payload = _bridge_payload(wrapper)
        msgid = _confirmed_message(payload, p.returncode, target)
        if not msgid:
            definite_failure = payload.get("success") is False or wrapper.get("action") == "rejected_before_process"
            status = "failed" if definite_failure else "unknown"
            out = {"delivery_status": status, "message_id": None, "error": payload.get("error", p.stderr.strip()[:300])}
            _receipt(run_root, "discord-failure.json", out)
            # A bridge rejection/process failure is definite and may be retried;
            # preserve the intent only when the process outcome is uncertain.
            if status == "failed":
                try:
                    intent.unlink()
                except FileNotFoundError:
                    pass
            return out
        out = {"delivery_status": "delivered", "message_id": msgid}
        _receipt(run_root / "discord-bridge", f"sent-{key}.json", {"message_id": msgid, "artifact_sha256": artifact_sha256, "target": target})
        return out
    except subprocess.TimeoutExpired:
        msgid = _reconcile_bridge(run_root / "discord-bridge", request_id, target, message_file, message)
        if msgid:
            _receipt(sent.parent, sent.name, {"message_id": msgid, "artifact_sha256": artifact_sha256, "target": target})
            return {"delivery_status": "delivered", "message_id": msgid}
        out = {"delivery_status": "unknown", "message_id": None, "error": "bridge timeout"}
        _receipt(run_root, "discord-failure.json", out)
        return out
