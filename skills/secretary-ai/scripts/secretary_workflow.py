"""Small, provider-neutral safety contracts for secretary workflows.

The module deliberately knows nothing about Gmail, Calendar, or Mail.app. A
connector adapter is injected by the caller, which keeps the safety behavior
testable with disposable fixtures and prevents a fake successful provider
response from being mistaken for a live integration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConnectorUnavailable(RuntimeError):
    """The requested provider surface is not currently available."""


class MutationResponseLost(RuntimeError):
    """The provider may have mutated state but its response was lost."""


def _datetime(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("event timezone is required")
    return value


def summarize_schedule(events: Sequence[Mapping[str, Any]], *, timezone_name: str) -> tuple[dict[str, Any], ...]:
    """Return deterministic local-time summaries without invoking mutations."""
    try:
        target_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone is unavailable") from exc

    summaries = []
    for event in events:
        start = _datetime(event["start"])
        end = _datetime(event["end"])
        if end < start:
            raise ValueError("event end precedes start")
        summaries.append(
            {
                "calendar_id": event.get("calendar_id"),
                "event_id": event.get("event_id"),
                "title": str(event.get("title", "")),
                "start": start.astimezone(target_zone).isoformat(),
                "end": end.astimezone(target_zone).isoformat(),
            }
        )
    return tuple(sorted(summaries, key=lambda item: item["start"]))


def resolve_recipient(candidates: Sequence[Mapping[str, str]], *, requested: str) -> dict[str, Any]:
    """Resolve one exact recipient; never broaden a name to multiple addresses."""
    query = requested.strip().casefold()
    if not query:
        return {"status": "needs_clarification", "reason": "recipient_required"}
    email_matches = [
        candidate for candidate in candidates
        if candidate.get("email", "").strip().casefold() == query
    ]
    if len(email_matches) == 1:
        return {"status": "resolved", "recipient": dict(email_matches[0])}
    if len(email_matches) > 1:
        return {"status": "needs_clarification", "reason": "ambiguous_recipient"}
    name_matches = [
        candidate for candidate in candidates
        if candidate.get("name", "").strip().casefold() == query
    ]
    if len(name_matches) == 1:
        return {"status": "resolved", "recipient": dict(name_matches[0])}
    if len(name_matches) > 1:
        return {"status": "needs_clarification", "reason": "ambiguous_recipient"}
    return {"status": "needs_clarification", "reason": "recipient_not_found"}


def create_reply_draft(
    adapter: Any,
    *,
    message_id: str,
    recipients: Sequence[Mapping[str, str]],
    subject: str,
    body: str,
    authorized: bool,
) -> dict[str, Any]:
    """Save a draft only after explicit authorization; never call a send API."""
    if not authorized:
        return {"status": "needs_confirmation", "reason": "draft_confirmation_required"}
    if not recipients or any(not item.get("email", "").strip() for item in recipients):
        return {"status": "needs_clarification", "reason": "recipient_required"}
    addresses = [item["email"].strip().casefold() for item in recipients]
    if len(addresses) != len(set(addresses)):
        return {"status": "needs_clarification", "reason": "ambiguous_recipient"}
    draft = {
        "message_id": message_id,
        "recipients": tuple(dict(item) for item in recipients),
        "subject": subject,
        "body": body,
        "send_allowed": False,
    }
    try:
        saved = adapter.save_draft(draft)
    except ConnectorUnavailable:
        return {"status": "pending_reconciliation", "reason": "connector_unavailable"}
    except Exception:
        # A remote draft may exist even when the response is missing. Do not retry.
        return {"status": "pending_reconciliation", "reason": "draft_outcome_unknown"}
    draft_id = saved.get("draft_id") if isinstance(saved, Mapping) else None
    if not isinstance(draft_id, str) or not draft_id.strip():
        return {"status": "pending_reconciliation", "reason": "draft_readback_unavailable"}
    getter = getattr(adapter, "get_draft", None)
    if not callable(getter):
        return {"status": "pending_reconciliation", "reason": "draft_readback_unavailable"}
    try:
        readback = getter(draft_id)
    except ConnectorUnavailable:
        return {"status": "pending_reconciliation", "reason": "connector_unavailable"}
    except Exception:
        return {"status": "pending_reconciliation", "reason": "draft_readback_unavailable"}
    if not isinstance(readback, Mapping) or readback.get("draft_id") != draft_id:
        return {"status": "pending_reconciliation", "reason": "draft_readback_mismatch"}
    expected_recipients = tuple(
        (item.get("name", "").strip(), item["email"].strip().casefold())
        for item in draft["recipients"]
    )
    actual_recipients = readback.get("recipients")
    if (not isinstance(actual_recipients, (list, tuple))
            or any(not isinstance(item, Mapping) or not isinstance(item.get("email"), str)
                   for item in actual_recipients)):
        return {"status": "pending_reconciliation", "reason": "draft_readback_mismatch"}
    try:
        actual_recipient_keys = tuple(
            (item.get("name", "").strip(), item["email"].strip().casefold())
            for item in actual_recipients
        )
    except (AttributeError, KeyError):
        actual_recipient_keys = ()
    if (
        actual_recipient_keys != expected_recipients
        or readback.get("message_id") != message_id
        or readback.get("subject") != subject
        or readback.get("body") != body
        or readback.get("send_allowed") is not False
    ):
        return {"status": "pending_reconciliation", "reason": "draft_readback_mismatch"}
    return {"status": "drafted", "draft": dict(readback)}


def _readback(
    adapter: Any,
    *,
    calendar_id: str,
    event_id: str,
    fields: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    try:
        event = adapter.get_event(calendar_id, event_id)
    except ConnectorUnavailable:
        return {"status": "pending_reconciliation", "reason": "connector_unavailable"}
    if not isinstance(event, Mapping) or event.get("calendar_id") != calendar_id:
        return {"status": "pending_reconciliation", "reason": "readback_mismatch"}
    if event.get("event_id") != event_id or any(event.get(key) != value for key, value in fields.items()):
        return {"status": "pending_reconciliation", "reason": "readback_mismatch"}
    return {"status": status, "event": dict(event)}


def _reconcile(
    adapter: Any,
    *,
    calendar_id: str,
    request_token: str,
    fields: Mapping[str, Any],
    expected_event_id: str | None = None,
) -> dict[str, Any]:
    try:
        matches = adapter.find_by_request_token(calendar_id, request_token)
    except ConnectorUnavailable:
        return {"status": "pending_reconciliation", "reason": "connector_unavailable"}
    if len(matches) != 1 or not matches[0].get("event_id"):
        return {"status": "pending_reconciliation", "reason": "mutation_outcome_unknown"}
    if expected_event_id is not None and matches[0].get("event_id") != expected_event_id:
        return {"status": "pending_reconciliation", "reason": "readback_mismatch"}
    return _readback(
        adapter,
        calendar_id=calendar_id,
        event_id=matches[0]["event_id"],
        fields=fields,
        status="reconciled",
    )


def calendar_mutation(adapter: Any, request: Mapping[str, Any], *, authorized: bool) -> dict[str, Any]:
    """Perform one authorized mutation and prove it by exact readback.

    A lost response is reconciled by the caller-supplied request token. The
    function never retries a create when the remote outcome is unknown.
    """
    if not authorized:
        return {"status": "needs_confirmation", "reason": "calendar_confirmation_required"}
    operation = request.get("operation")
    calendar_id = request.get("calendar_id")
    token = request.get("request_token")
    fields = request.get("fields")
    if (
        operation not in {"create", "update"}
        or not isinstance(calendar_id, str)
        or not calendar_id.strip()
        or not isinstance(token, str)
        or not token.strip()
        or not isinstance(fields, Mapping)
    ):
        return {"status": "invalid_request", "reason": "mutation_contract_invalid"}
    try:
        if operation == "create":
            response = adapter.create_event(calendar_id, dict(fields), token)
        else:
            event_id = request.get("event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                return {"status": "invalid_request", "reason": "event_id_required"}
            response = adapter.update_event(calendar_id, event_id, dict(fields), token)
    except MutationResponseLost:
        return _reconcile(
            adapter,
            calendar_id=calendar_id,
            request_token=token,
            fields=fields,
            expected_event_id=request.get("event_id") if operation == "update" else None,
        )
    except ConnectorUnavailable:
        return {"status": "pending_reconciliation", "reason": "connector_unavailable"}
    except Exception:
        return {"status": "failed", "reason": "provider_error"}

    event_id = response.get("event_id") if isinstance(response, Mapping) else None
    if operation == "update":
        event_id = request.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return _reconcile(
            adapter,
            calendar_id=calendar_id,
            request_token=token,
            fields=fields,
            expected_event_id=request.get("event_id") if operation == "update" else None,
        )
    return _readback(adapter, calendar_id=calendar_id, event_id=event_id, fields=fields, status="completed")
