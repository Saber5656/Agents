"""Separate, durable batch adapter for turning local tasks into GitHub Issues.

Workers only capture local discoveries. This module is the independent batch
boundary that drafts public English content, reconciles remote outcomes, and
links a verified Issue back to the local TaskStore.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from .delivery import public_text
from .runner import redact
from .tasks import ConflictError, IssueizationError, TaskStore

try:  # Unix is the supported local batch surface; tests can still inject locks.
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is process-local only.
    fcntl = None


class IssueizationErrorBase(RuntimeError):
    """Base error for issueization failures."""


class DraftError(IssueizationErrorBase):
    pass


class SubscriptionBoundaryError(IssueizationErrorBase):
    pass


class RemoteError(IssueizationErrorBase):
    pass


class AuthorizationError(RemoteError):
    pass


class AmbiguousRemoteError(RemoteError):
    """The remote call may have happened; reconcile before creating again."""


class RemoteMalformedError(AmbiguousRemoteError):
    pass


class RemoteNetworkError(AmbiguousRemoteError):
    pass


@dataclass(frozen=True)
class IssueDraft:
    title: str
    body: str
    acceptance: tuple[str, ...]


def _marker(task_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
        raise DraftError("local task identity is not safe for a public marker")
    return f"<!-- agents-local-task:{task_id} -->"


def _decode_json(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise DraftError("agent output must be a JSON object")
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        result = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise DraftError("agent output is not valid JSON") from exc
    if not isinstance(result, dict):
        raise DraftError("agent output must be a JSON object")
    return result


def parse_draft(value, *, env=None) -> IssueDraft:
    """Validate the agent's public English title/body/acceptance payload."""
    data = _decode_json(value)
    title, body, acceptance = data.get("title"), data.get("body"), data.get("acceptance")
    if not isinstance(title, str) or not title.strip() or "\n" in title or "\r" in title:
        raise DraftError("draft title must be a single non-empty line")
    if not isinstance(body, str) or not body.strip():
        raise DraftError("draft body is required")
    if not isinstance(acceptance, list) or not acceptance or any(
            not isinstance(item, str) or not item.strip() for item in acceptance):
        raise DraftError("draft acceptance must contain observable non-empty strings")
    title, body = title.strip(), body.strip()
    acceptance = tuple(item.strip() for item in acceptance)
    try:
        public_text(title, env if env is not None else os.environ, english=True)
        public_text(body, env if env is not None else os.environ, english=True)
        for item in acceptance:
            public_text(item, env if env is not None else os.environ, english=True)
    except ValueError as exc:
        raise DraftError(str(exc)) from exc
    return IssueDraft(title, body, acceptance)


def render_issue_body(task_id: str, draft: IssueDraft, *, env=None) -> str:
    marker = _marker(task_id)
    body = draft.body.strip()
    if marker in body:
        if not body.startswith(marker):
            raise DraftError("local task marker must be stable and leading")
        body = body[len(marker):].lstrip()
        if marker in body:
            raise DraftError("local task marker must occur exactly once")
    rendered = marker + "\n\n" + body + "\n\n## Acceptance\n\n" + "\n".join(
        f"- [ ] {item}" for item in draft.acceptance) + "\n"
    try:
        return public_text(rendered, env if env is not None else os.environ, english=True)
    except ValueError as exc:
        raise DraftError(str(exc)) from exc


_PAID_ROUTE_KEYS = {
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE",
    "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
}


def validate_subscription_environment(env):
    present = sorted(key for key in _PAID_ROUTE_KEYS if env.get(key))
    if present:
        raise SubscriptionBoundaryError(
            "paid API credentials/routes are not allowed: " + ", ".join(present))


def _codex_text(output: str) -> str:
    """Extract agent messages from Codex JSONL while retaining direct JSON mocks."""
    try:
        value = json.loads(output.strip())
        if isinstance(value, dict) and {"title", "body", "acceptance"}.issubset(value):
            return output
    except (ValueError, TypeError):
        pass
    messages = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if event.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                messages.append(item["text"])
    if not messages:
        raise DraftError("Codex returned no agent message")
    return "\n".join(messages)


class CodexDraftAgent:
    """Use the authenticated Codex subscription surface at Luna/low only."""

    def __init__(self, *, model="gpt-5.6-luna", effort="low", timeout=300, env=None):
        if model != "gpt-5.6-luna" or effort != "low":
            raise SubscriptionBoundaryError("issueization is restricted to Codex gpt-5.6-luna/low")
        self.model, self.effort, self.timeout = model, effort, timeout
        self.env = dict(env or os.environ)
        validate_subscription_environment(self.env)

    def draft(self, task):
        prompt = {
            "task_id": task["id"],
            "purpose": task.get("purpose"),
            "expected_result": task.get("expected_result"),
            "repository": task.get("repository"),
            "acceptance": task.get("acceptance_evidence", []),
            "evidence_links": task.get("evidence_links", []),
            "instructions": (
                "Return JSON only with title, body, and acceptance fields. "
                "Write public GitHub Issue content in English. Preserve exact technical "
                "identifiers, omit private Vault paths/secrets, and make acceptance observable."
            ),
        }
        argv = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--json",
                "--skip-git-repo-check", "-m", self.model, "-s", "read-only",
                "-c", 'approval_policy="never"', "-c", 'model_reasoning_effort="low"',
                "--disable", "multi_agent", "-"]
        try:
            result = subprocess.run(argv, input=json.dumps(prompt, ensure_ascii=False),
                                    env=self.env, capture_output=True, text=True,
                                    timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RemoteNetworkError(f"Codex draft execution incomplete: {type(exc).__name__}") from exc
        if result.returncode:
            detail = redact((result.stderr or result.stdout or "Codex draft failed").strip(), self.env)
            if any(term in detail.lower() for term in ("login", "unauthorized", "authentication", "not logged")):
                raise AuthorizationError(detail[:1000])
            raise RemoteError(detail[:1000])
        return _codex_text(result.stdout)


def _remote_error(detail: str, *, create=False):
    lower = detail.lower()
    if any(term in lower for term in ("authentication", "unauthorized", "not logged", "login", "401", "permission denied")):
        return AuthorizationError(detail[:1000])
    if any(term in lower for term in ("timeout", "timed out", "connection", "network", "temporary", "502", "503", "504")):
        return RemoteNetworkError(detail[:1000])
    return RemoteError(detail[:1000])


class GitHubIssueAdapter:
    """Paginated, non-search GitHub Issue adapter using the installed `gh`."""

    def __init__(self, repository, *, env=None, timeout=60, command_runner=None, page_size=100):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid repository")
        self.repository = repository
        self.env = dict(env or os.environ)
        self.timeout, self.command_runner, self.page_size = timeout, command_runner, page_size

    def _command(self, argv):
        if self.command_runner:
            try:
                return self.command_runner(argv)
            except (AuthorizationError, AmbiguousRemoteError, RemoteError):
                raise
            except OSError as exc:
                raise RemoteNetworkError(f"GitHub command incomplete: {type(exc).__name__}") from exc
        try:
            result = subprocess.run(argv, env=self.env, capture_output=True, text=True,
                                    timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RemoteNetworkError(f"GitHub command incomplete: {type(exc).__name__}") from exc
        if result.returncode:
            raise _remote_error(result.stderr or result.stdout or "GitHub command failed")
        return result.stdout

    def list_issues(self, repository=None):
        repository = repository or self.repository
        if repository != self.repository:
            raise ValueError("adapter repository mismatch")
        rows, page = [], 1
        while True:
            output = self._command(["gh", "api", f"repos/{repository}/issues?state=all&per_page={self.page_size}&page={page}"])
            try:
                page_rows = json.loads(output)
            except (TypeError, ValueError) as exc:
                raise RemoteMalformedError("GitHub Issue listing returned malformed JSON") from exc
            if not isinstance(page_rows, list):
                raise RemoteMalformedError("GitHub Issue listing was not an array")
            rows.extend(item for item in page_rows if isinstance(item, dict) and "pull_request" not in item)
            if len(page_rows) < self.page_size:
                return rows
            page += 1

    def create_issue(self, repository, title, body):
        if repository != self.repository:
            raise ValueError("adapter repository mismatch")
        output = self._command(["gh", "api", f"repos/{repository}/issues", "--method", "POST",
                                "-f", f"title={title}", "-f", f"body={body}"])
        try:
            value = json.loads(output)
        except (TypeError, ValueError) as exc:
            raise RemoteMalformedError("GitHub create response was malformed; outcome is ambiguous") from exc
        if not isinstance(value, dict) or not value.get("number"):
            raise RemoteMalformedError("GitHub create response omitted Issue identity")
        return value

    def read_issue(self, repository, number):
        if repository != self.repository:
            raise ValueError("adapter repository mismatch")
        output = self._command(["gh", "api", f"repos/{repository}/issues/{int(number)}"])
        try:
            value = json.loads(output)
        except (TypeError, ValueError) as exc:
            raise RemoteMalformedError("GitHub readback was malformed") from exc
        if not isinstance(value, dict):
            raise RemoteMalformedError("GitHub readback was not an object")
        return value


def _atomic_json(path: Path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(value, ensure_ascii=False, indent=2))
            stream.flush(); os.fsync(stream.fileno())
        temporary.chmod(0o600); os.replace(temporary, path)
    except Exception:
        if temporary:
            try: temporary.unlink()
            except OSError: pass
        raise


@contextmanager
def lifetime_lock(path: Path):
    path = Path(path); path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with open(path, "a+") as stream:
        os.chmod(path, 0o600)
        if fcntl is not None:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class IssueizationBatch:
    def __init__(self, store: TaskStore, remote, agent, *, owner="issue-batch",
                 lease_seconds=900, receipt_dir=None, lock_path=None, env=None,
                 repository=None):
        self.store, self.remote, self.agent, self.owner = store, remote, agent, owner
        self.lease_seconds = lease_seconds
        self.repository = repository
        self.receipt_dir = Path(receipt_dir or store.vault_root / "01-Projects" / "issueization" / "receipts")
        self.lock_path = Path(lock_path or self.receipt_dir.parent / "issueization.lock")
        self.env = dict(env or os.environ)
        validate_subscription_environment(self.env)

    def _receipt_path(self, task_id):
        _marker(task_id)
        return self.receipt_dir / f"{task_id}.json"

    def _receipt(self, task_id):
        path = self._receipt_path(task_id)
        try:
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def _save_receipt(self, task_id, **fields):
        current = self._receipt(task_id) or {"task_id": task_id}
        current.update(fields, updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        _atomic_json(self._receipt_path(task_id), current)
        return current

    def _remote_issue(self, repository, task_id):
        marker = _marker(task_id)
        issues = self.remote.list_issues(repository)
        if not isinstance(issues, list):
            raise RemoteMalformedError("remote Issue listing was not a list")
        matches = [issue for issue in issues if isinstance(issue, dict) and marker in str(issue.get("body", ""))]
        if len(matches) > 1:
            raise RemoteMalformedError("multiple remote Issues carry the same local task marker")
        return matches[0] if matches else None

    def _readback_for_task(self, repository, task_id, issue):
        if not isinstance(issue, dict):
            raise RemoteMalformedError("remote Issue identity was not an object")
        number = issue.get("number", issue.get("issue_id"))
        try:
            number = int(number)
        except (TypeError, ValueError) as exc:
            raise RemoteMalformedError("remote Issue omitted a numeric number") from exc
        value = self.remote.read_issue(repository, number)
        if not isinstance(value, dict):
            raise RemoteMalformedError("remote Issue readback was not an object")
        url = value.get("html_url", value.get("url", value.get("issue_url")))
        expected = f"https://github.com/{repository}/issues/{int(number)}"
        try:
            value_number = int(value.get("number", number))
        except (TypeError, ValueError) as exc:
            raise RemoteMalformedError("remote Issue readback number was malformed") from exc
        if url != expected or value_number != number or _marker(task_id) not in str(value.get("body", "")):
            raise RemoteMalformedError("remote Issue readback identity or marker mismatch")
        return {"repository": repository, "issue_id": int(number), "number": int(number),
                "url": expected, "issue_url": expected, "html_url": expected,
                "title": value.get("title"), "body": value.get("body", ""), "raw": value}

    def _mark_failure(self, claim, diagnostic, *, ambiguous=False):
        task_id, token = claim["id"], claim["claim_token"]
        diagnostic = redact(str(diagnostic), self.env)
        if ambiguous:
            self._save_receipt(task_id, status="ambiguous", diagnostic=diagnostic)
            self.store.mark_issueization_ambiguous(task_id, token, diagnostic)
            return "ambiguous"
        self._save_receipt(task_id, status="retry", diagnostic=diagnostic)
        self.store.record_issueization_failure(task_id, token, diagnostic)
        return "retry"

    def _process(self, candidate):
        task_id, repository = candidate["id"], candidate.get("repository")
        if not repository:
            return "skipped"
        marker = _marker(task_id)
        receipt = self._receipt(task_id)
        existing = None
        if candidate.get("reconciliation_required"):
            # Inspect the remote while the expired token still identifies the
            # old attempt, then transition it to ambiguous under our lifetime
            # lock before acquiring a new claim.
            try:
                existing = self._remote_issue(repository, task_id)
            except AuthorizationError as exc:
                self.store.reconcile_expired_claim(task_id, str(exc))
                return "retry"
            except AmbiguousRemoteError as exc:
                self.store.reconcile_expired_claim(task_id, str(exc))
                return "ambiguous"
            self.store.reconcile_expired_claim(task_id, "expired claim reconciled before issueization")
            candidate = self.store.get_task(task_id)
        if candidate.get("issueization_state") == "claimed":
            return "skipped"
        try:
            claim = self.store.claim_issueization(task_id, self.owner, lease_seconds=self.lease_seconds)
        except IssueizationError:
            return "skipped"
        self._save_receipt(task_id, status="prepare", marker=marker, repository=repository,
                           claim_token=claim["claim_token"])
        try:
            if existing is None:
                existing = self._remote_issue(repository, task_id)
        except AuthorizationError as exc:
            return self._mark_failure(claim, str(exc), ambiguous=False)
        except AmbiguousRemoteError as exc:
            return self._mark_failure(claim, str(exc),
                                      ambiguous=receipt and receipt.get("status") in ("creating", "linking"))
        if existing:
            try:
                readback = self._readback_for_task(repository, task_id, existing)
                self._save_receipt(task_id, status="linking", issue=readback)
                self.store.link_issue(task_id, repository, readback["issue_id"], readback["url"],
                                      claim_token=claim["claim_token"], verified=True, readback=readback)
                self._save_receipt(task_id, status="linked", issue=readback)
                return "issued"
            except (RemoteError, ValueError, ConflictError) as exc:
                return self._mark_failure(claim, str(exc), ambiguous=True)
        try:
            draft = parse_draft(self.agent.draft(claim), env=self.env)
            body = render_issue_body(task_id, draft, env=self.env)
        except (DraftError, AuthorizationError, RemoteError) as exc:
            return self._mark_failure(claim, str(exc), ambiguous=False)
        except Exception as exc:
            return self._mark_failure(claim, str(exc), ambiguous=False)
        self._save_receipt(task_id, status="creating", marker=marker, repository=repository,
                           title=draft.title, body=body, claim_token=claim["claim_token"])
        try:
            created = self.remote.create_issue(repository, draft.title, body)
        except AuthorizationError as exc:
            return self._mark_failure(claim, str(exc), ambiguous=False)
        except AmbiguousRemoteError as exc:
            return self._mark_failure(claim, str(exc), ambiguous=True)
        except RemoteError as exc:
            return self._mark_failure(claim, str(exc), ambiguous=True)
        try:
            readback = self._readback_for_task(repository, task_id, created)
            self._save_receipt(task_id, status="linking", issue=readback)
            self.store.link_issue(task_id, repository, readback["issue_id"], readback["url"],
                                  claim_token=claim["claim_token"], verified=True, readback=readback)
            self._save_receipt(task_id, status="linked", issue=readback)
            return "issued"
        except (RemoteError, ValueError, ConflictError) as exc:
            return self._mark_failure(claim, str(exc), ambiguous=True)

    def run(self, *, limit=None):
        result = {"status": "completed", "considered": 0, "issued": 0,
                  "retry": 0, "ambiguous": 0, "skipped": 0, "errors": []}
        with lifetime_lock(self.lock_path):
            candidates = self.store.list_issueization_candidates(limit=limit)
            if self.repository:
                candidates = [task for task in candidates if task.get("repository") == self.repository]
            for candidate in candidates:
                result["considered"] += 1
                try:
                    outcome = self._process(candidate)
                except Exception as exc:  # Keep batch moving; task remains local and diagnosable.
                    outcome = "retry"
                    result["errors"].append({"task_id": candidate.get("id"),
                                              "error": redact(str(exc), self.env)})
                result[outcome] = result.get(outcome, 0) + 1
        if result["errors"] or result["retry"] or result["ambiguous"]:
            result["status"] = "partial"
        elif result["considered"] == 0:
            result["status"] = "idle"
        return result


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Issueize eligible local tasks through the subscription Codex batch")
    parser.add_argument("--db", help="SQLite path (default: $AGENTS_ROOT/.local/tasks.sqlite3)")
    parser.add_argument("--repository", required=True, help="owner/repository to issueize")
    parser.add_argument("--owner", default="issue-batch")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    try:
        store = TaskStore(args.db)
        try:
            remote = GitHubIssueAdapter(args.repository)
            agent = CodexDraftAgent()
            result = IssueizationBatch(store, remote, agent, owner=args.owner,
                                       env=os.environ, repository=args.repository).run(limit=args.limit)
        finally:
            store.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("completed", "idle") else 2
    except (ValueError, IssueizationErrorBase, OSError, KeyError) as exc:
        print(json.dumps({"status": "incomplete", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
