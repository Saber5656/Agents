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
from pathlib import Path
from urllib.parse import quote


class DeliveryError(RuntimeError):
    pass


_SNOWFLAKE = re.compile(r"^[1-9][0-9]{16,19}$")
_TARGET = re.compile(r"^discord:[1-9][0-9]{16,19}$")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(args: list[str], cwd: Path | None = None, *, timeout: int = 60) -> str:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise DeliveryError("git operation timed out") from exc
    if p.returncode:
        diagnostic = "\n".join(part.strip() for part in (p.stderr, p.stdout) if part.strip())
        raise DeliveryError(f"git {' '.join(args[:2])} failed: {diagnostic[:300]}")
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
                     gitleaks_bin: str) -> dict:
    """Commit/push one artifact to an existing remote branch and verify its blob."""
    artifact, run_root = Path(artifact), Path(run_root)
    path = _validate_path(relative_path)
    if not artifact.is_file() or artifact.is_symlink():
        raise DeliveryError("artifact must be a regular file")
    content_sha = _sha(artifact)
    result = {"artifact_sha256": content_sha, "repo_url": repo_url, "branch": branch,
              "path": relative_path, "status": "failed"}
    run_root.mkdir(parents=True, exist_ok=True)
    checkout = Path(tempfile.mkdtemp(prefix="delivery-", dir=run_root))
    try:
        head_output = _git(["ls-remote", repo_url, f"refs/heads/{branch}"])
        remote_head = head_output.split()[0] if head_output else ""
        if not remote_head:
            raise DeliveryError("remote branch not found")
        shutil.rmtree(checkout)
        _git(["init", str(checkout)])
        _git(["remote", "add", "origin", repo_url], checkout)
        _git(["fetch", "--depth=1", "--filter=blob:none", "origin", f"refs/heads/{branch}"], checkout)
        fetched_head = _git(["rev-parse", "FETCH_HEAD"], checkout)
        if fetched_head != remote_head:
            raise DeliveryError("remote changed during fetch")
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
            result.update(status="already_present", commit=remote_head, url=_public_url(repo_url, remote_head, relative_path))
            _receipt(run_root, "publish-success.json", result)
            return result
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact, destination)
        if not gitleaks_bin:
            raise DeliveryError("gitleaks is required")
        scan_dir = Path(tempfile.mkdtemp(prefix="artifact-scan-", dir=run_root))
        try:
            shutil.copyfile(artifact, scan_dir / artifact.name)
            scan = subprocess.run([gitleaks_bin, "dir", str(scan_dir), "--no-banner", "--redact"], text=True, capture_output=True, timeout=120)
            if scan.returncode:
                raise DeliveryError("gitleaks detected a secret")
            if re.search(r"(?:/Users/|/home/)[^\s\n]+", artifact.read_text(encoding="utf-8")):
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
        try:
            _git(["push", "origin", f"HEAD:refs/heads/{branch}"], checkout)
        except DeliveryError:
            observed = _git(["ls-remote", repo_url, f"refs/heads/{branch}"]).split()[0]
            if observed != local_commit:
                raise DeliveryError("push outcome unknown; remote head did not match local commit")
        observed = _git(["ls-remote", repo_url, f"refs/heads/{branch}"]).split()[0]
        if observed != local_commit:
            raise DeliveryError("remote head verification failed")
        _git(["fetch", "--depth=1", "origin", f"refs/heads/{branch}"], checkout)
        remote_blob = _git(["ls-tree", "FETCH_HEAD", "--", relative_path], checkout).split()[2]
        if remote_blob != _git(["hash-object", str(destination)], checkout):
            raise DeliveryError("published artifact blob verification failed")
        result.update(status="published", commit=local_commit, url=_public_url(repo_url, local_commit, relative_path))
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


def notify_discord(summary_date: str, news_url: str, advice_url: str, artifact_sha256: str,
                   run_root: Path | str, hermes_python: str, target: str,
                   request_id: str, hermes_bridge: Path | str | None = None) -> dict:
    """Send a short immutable-link message through the Hermes bridge."""
    run_root = Path(run_root)
    if not _TARGET.fullmatch(target):
        return {"delivery_status": "failed", "message_id": None, "error": "invalid Discord target"}
    bridge = Path(hermes_bridge or (Path(os.environ.get("SKILLS_ROOT", ".")) / "hermes-agent-bridge/scripts/hermes_bridge.py"))
    message = f"ITニュース {summary_date}\nニュース: {news_url}\n助言: {advice_url}"
    key = hashlib.sha256(f"{artifact_sha256}:{target}".encode()).hexdigest()
    sent = run_root / "discord-bridge" / f"sent-{key}.json"
    if sent.exists():
        return {"delivery_status": "skipped", "message_id": json.loads(sent.read_text()).get("message_id")}
    intent = run_root / "discord-bridge" / f"intent-{key}.json"
    if intent.exists() and not sent.exists():
        prior = json.loads(intent.read_text(encoding="utf-8"))
        if prior.get("artifact_sha256") == artifact_sha256 and prior.get("target") == target:
            return {"delivery_status": "unknown", "message_id": None, "error": "existing intent requires reconciliation"}
    _receipt(run_root / "discord-bridge", f"intent-{key}.json", {"artifact_sha256": artifact_sha256, "target": target, "message": message})
    message_file = run_root / "discord-bridge" / f"message-{key}.txt"
    message_file.write_text(message, encoding="utf-8")
    message_file.chmod(0o600)
    cmd = [hermes_python, str(bridge), "send", "--target", target, "--file", str(message_file),
           "--receipt-dir", str(run_root / "discord-bridge"), "--request-id", request_id]
    try:
        env = dict(os.environ)
        env["HERMES_INFERENCE_PROVIDER"] = "openai-codex"
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=90, env=env)
        _receipt(run_root / "discord-bridge", f"bridge-{key}.stdout.log", {"stdout": p.stdout})
        _receipt(run_root / "discord-bridge", f"bridge-{key}.stderr.log", {"stderr": p.stderr})
        payload = {}
        wrapper = {}
        try:
            wrapper = json.loads(p.stdout)
            if not isinstance(wrapper, dict):
                wrapper = {}
            if isinstance(wrapper, dict):
                nested = wrapper.get("stdout")
                payload = json.loads(nested) if isinstance(nested, str) else wrapper
                if not isinstance(payload, dict):
                    payload = {}
        except (ValueError, TypeError):
            payload = {}
        msgid = payload.get("message_id")
        delivered = (p.returncode == 0 and payload.get("success") is True
                     and payload.get("platform") == "discord"
                     and str(payload.get("chat_id")) == target.removeprefix("discord:")
                     and _SNOWFLAKE.fullmatch(str(msgid or "")) is not None)
        if not delivered:
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
        out = {"delivery_status": "unknown", "message_id": None, "error": "bridge timeout"}
        _receipt(run_root, "discord-failure.json", out)
        return out
