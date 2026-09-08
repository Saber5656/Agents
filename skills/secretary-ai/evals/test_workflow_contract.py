from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from secretary_workflow import (
    ConnectorUnavailable,
    MutationResponseLost,
    calendar_mutation,
    create_reply_draft,
    resolve_recipient,
    summarize_schedule,
)


class FakeMail:
    def __init__(self) -> None:
        self.saved = []
        self.sent = []

    def save_draft(self, draft):
        self.saved.append(draft)
        return {"draft_id": "draft-1", **draft}

    def get_draft(self, draft_id):
        return next(
            {"draft_id": draft_id, **draft}
            for draft in self.saved
            if draft_id == "draft-1"
        )

    def send(self, message):
        self.sent.append(message)
        raise AssertionError("send must never be called")


class FakeCalendar:
    def __init__(self) -> None:
        self.events = {}
        self.create_calls = 0
        self.update_calls = 0
        self.fail_create = None
        self.fail_update = None

    def create_event(self, calendar_id, fields, request_token):
        self.create_calls += 1
        if self.fail_create:
            raise self.fail_create
        event_id = f"event-{self.create_calls}"
        self.events[(calendar_id, event_id)] = {
            "calendar_id": calendar_id,
            "event_id": event_id,
            "request_token": request_token,
            **fields,
        }
        return self.events[(calendar_id, event_id)]

    def update_event(self, calendar_id, event_id, fields, request_token):
        self.update_calls += 1
        if self.fail_update:
            raise self.fail_update
        current = self.events[(calendar_id, event_id)]
        current.update(fields)
        current["request_token"] = request_token
        return current

    def find_by_request_token(self, calendar_id, request_token):
        return [
            event
            for (candidate_calendar, _), event in self.events.items()
            if candidate_calendar == calendar_id
            and event.get("request_token") == request_token
        ]

    def get_event(self, calendar_id, event_id):
        return self.events[(calendar_id, event_id)]


def test_schedule_summary_and_draft_do_not_send_or_mutate_calendar() -> None:
    calendar = FakeCalendar()
    mail = FakeMail()
    events = [
        {
            "calendar_id": "primary",
            "event_id": "event-1",
            "title": "Boundary",
            "start": "2026-09-30T23:30:00+00:00",
            "end": "2026-10-01T00:30:00+00:00",
        }
    ]

    summary = summarize_schedule(events, timezone_name="Asia/Tokyo")
    draft = create_reply_draft(
        mail,
        message_id="message-1",
        recipients=[{"name": "A", "email": "a@example.test"}],
        subject="Re: Boundary",
        body="Draft only",
        authorized=True,
    )

    assert summary[0]["start"] == "2026-10-01T08:30:00+09:00"
    assert summary[0]["end"] == "2026-10-01T09:30:00+09:00"
    assert draft["status"] == "drafted"
    assert len(mail.saved) == 1
    assert mail.sent == []
    assert calendar.create_calls == 0
    assert calendar.update_calls == 0


def test_calendar_mutation_requires_authorization_and_reads_back_exact_event() -> None:
    calendar = FakeCalendar()
    request = {
        "operation": "create",
        "calendar_id": "primary",
        "request_token": "req-1",
        "fields": {"title": "Test", "start": "2026-10-01T10:00:00+09:00"},
    }

    denied = calendar_mutation(calendar, request, authorized=False)
    assert denied["status"] == "needs_confirmation"
    assert calendar.create_calls == 0

    result = calendar_mutation(calendar, request, authorized=True)
    assert result["status"] == "completed"
    assert result["event"]["title"] == "Test"
    assert result["event"]["event_id"] == "event-1"


def test_authorized_update_targets_one_event_and_reads_back_the_update() -> None:
    calendar = FakeCalendar()
    created = calendar.create_event(
        "primary",
        {"title": "Before", "start": "2026-10-01T10:00:00+09:00"},
        "seed",
    )
    request = {
        "operation": "update",
        "calendar_id": "primary",
        "event_id": created["event_id"],
        "request_token": "req-update",
        "fields": {"title": "After"},
    }

    result = calendar_mutation(calendar, request, authorized=True)

    assert result["status"] == "completed"
    assert result["event"]["event_id"] == created["event_id"]
    assert result["event"]["title"] == "After"
    assert calendar.update_calls == 1


def test_ambiguous_recipient_is_a_question_without_expanding_recipients() -> None:
    result = resolve_recipient(
        [{"name": "A", "email": "a1@example.test"}, {"name": "A", "email": "a2@example.test"}],
        requested="A",
    )
    assert result == {"status": "needs_clarification", "reason": "ambiguous_recipient"}


def test_unavailable_connector_is_safe_and_does_not_fallback_to_send() -> None:
    mail = FakeMail()
    mail.save_draft = lambda draft: (_ for _ in ()).throw(ConnectorUnavailable("mail"))
    result = create_reply_draft(
        mail,
        message_id="message-1",
        recipients=[{"name": "A", "email": "a@example.test"}],
        subject="Re: Test",
        body="Draft only",
        authorized=True,
    )
    assert result == {"status": "pending_reconciliation", "reason": "connector_unavailable"}
    assert mail.sent == []


def test_draft_requires_provider_id_and_exact_readback() -> None:
    mail = FakeMail()
    result = create_reply_draft(
        mail,
        message_id="message-1",
        recipients=[{"name": "A", "email": "a@example.test"}],
        subject="Re: Test",
        body="Draft only",
        authorized=True,
    )
    assert result["status"] == "drafted"
    assert result["draft"]["draft_id"] == "draft-1"


def test_arbitrary_draft_success_without_id_stays_pending() -> None:
    mail = FakeMail()
    mail.save_draft = lambda draft: {"ok": True}
    result = create_reply_draft(
        mail,
        message_id="message-1",
        recipients=[{"name": "A", "email": "a@example.test"}],
        subject="Re: Test",
        body="Draft only",
        authorized=True,
    )
    assert result == {"status": "pending_reconciliation", "reason": "draft_readback_unavailable"}


def test_draft_wrong_recipient_readback_stays_pending() -> None:
    mail = FakeMail()
    mail.get_draft = lambda draft_id: {
        "draft_id": draft_id,
        "message_id": "message-1",
        "recipients": ({"name": "Other", "email": "other@example.test"},),
        "subject": "Re: Test",
        "body": "Draft only",
        "send_allowed": False,
    }
    result = create_reply_draft(
        mail,
        message_id="message-1",
        recipients=[{"name": "A", "email": "a@example.test"}],
        subject="Re: Test",
        body="Draft only",
        authorized=True,
    )
    assert result == {"status": "pending_reconciliation", "reason": "draft_readback_mismatch"}


def test_lost_calendar_response_reconciles_without_duplicate_create() -> None:
    calendar = FakeCalendar()
    request = {
        "operation": "create",
        "calendar_id": "primary",
        "request_token": "req-lost",
        "fields": {"title": "Once", "start": "2026-10-01T10:00:00+09:00"},
    }
    original = calendar.create_event

    def create_then_lose(calendar_id, fields, request_token):
        created = original(calendar_id, fields, request_token)
        raise MutationResponseLost(created["event_id"])

    calendar.create_event = create_then_lose
    result = calendar_mutation(calendar, request, authorized=True)
    assert result["status"] == "reconciled"
    assert result["event"]["title"] == "Once"
    assert calendar.create_calls == 1


def test_lost_calendar_response_without_readback_stays_pending() -> None:
    calendar = FakeCalendar()
    calendar.fail_create = MutationResponseLost("unknown")
    request = {
        "operation": "create",
        "calendar_id": "primary",
        "request_token": "req-unknown",
        "fields": {"title": "Unknown", "start": "2026-10-01T10:00:00+09:00"},
    }
    result = calendar_mutation(calendar, request, authorized=True)
    assert result == {"status": "pending_reconciliation", "reason": "mutation_outcome_unknown"}
    assert calendar.create_calls == 1


def test_lost_update_is_bound_to_original_event_id() -> None:
    calendar = FakeCalendar()
    calendar.events[("primary", "event-original")] = {
        "calendar_id": "primary",
        "event_id": "event-original",
        "request_token": "old-token",
        "title": "Before",
    }
    calendar.events[("primary", "event-other")] = {
        "calendar_id": "primary",
        "event_id": "event-other",
        "request_token": "req-update-lost",
        "title": "After",
    }

    def update_then_lose(calendar_id, event_id, fields, request_token):
        raise MutationResponseLost("event-original")

    calendar.update_event = update_then_lose
    request = {
        "operation": "update",
        "calendar_id": "primary",
        "event_id": "event-original",
        "request_token": "req-update-lost",
        "fields": {"title": "After"},
    }
    result = calendar_mutation(calendar, request, authorized=True)
    assert result == {"status": "pending_reconciliation", "reason": "readback_mismatch"}


def test_calendar_rejects_blank_identity_before_adapter() -> None:
    calendar = FakeCalendar()
    request = {
        "operation": "create",
        "calendar_id": "   ",
        "request_token": "",
        "fields": {"title": "Test"},
    }
    result = calendar_mutation(calendar, request, authorized=True)
    assert result == {"status": "invalid_request", "reason": "mutation_contract_invalid"}
    assert calendar.create_calls == 0


def test_invalid_timezone_and_naive_event_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        summarize_schedule([], timezone_name="Not/AZone")
    with pytest.raises(ValueError, match="timezone"):
        summarize_schedule(
            [{"title": "naive", "start": datetime(2026, 10, 1, 10), "end": datetime(2026, 10, 1, 11)}],
            timezone_name="Asia/Tokyo",
        )


def test_mail_draft_script_does_not_reply_all_or_send() -> None:
    script = Path(__file__).parents[1] / "scripts" / "mail-create-draft.applescript"
    source = script.read_text(encoding="utf-8").lower()
    assert "reply to all" not in source
    assert " send " not in source


def test_draft_readback_rejects_unparseable_extra_recipient():
    class ExtraRecipient(FakeMail):
        def get_draft(self, draft_id):
            value = super().get_draft(draft_id)
            value["recipients"] = [*value["recipients"], {"email": None}]
            return value
    result = create_reply_draft(ExtraRecipient(), message_id="m1",
        recipients=[{"email":"a@example.test"}], subject="draft", body="text", authorized=True)
    assert result["status"] == "pending_reconciliation"


def test_draft_save_lost_response_is_pending_without_retry():
    class LostResponse(FakeMail):
        def save_draft(self, draft):
            self.saved.append(draft)
            raise MutationResponseLost("fixture response lost")
    mail = LostResponse()
    result = create_reply_draft(mail, message_id="m1",
        recipients=[{"email":"a@example.test"}], subject="draft", body="text", authorized=True)
    assert result["status"] == "pending_reconciliation"
    assert len(mail.saved) == 1
    assert mail.sent == []
