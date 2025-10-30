import json
from typing import Any

import pytest

from app.services import ticket_importer
from app.services import syncro
from app.repositories import tickets as tickets_repo
from app.repositories import companies as company_repo
from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_ticket_update_events(monkeypatch):
    async def fake_emit_event(*args, **kwargs):
        return None

    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", fake_emit_event)

    async def fake_refresh_summary(ticket_id):
        return None

    async def fake_refresh_tags(ticket_id):
        return None

    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_summary", fake_refresh_summary)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_tags", fake_refresh_tags)


def test_normalise_status_mapping():
    allowed = {"open", "in_progress", "pending", "resolved", "closed"}
    default = "open"
    assert ticket_importer._normalise_status("In Progress", allowed, default) == "in_progress"
    assert ticket_importer._normalise_status("Waiting on customer", allowed, default) == "pending"
    assert ticket_importer._normalise_status("Completed", allowed, default) == "resolved"


def test_normalise_priority_mapping():
    assert ticket_importer._normalise_priority("Critical") == "urgent"
    assert ticket_importer._normalise_priority("LOW") == "low"
    assert ticket_importer._normalise_priority(None) == "normal"


def test_clean_text_converts_basic_html():
    value = "<p>Hello<br />World</p>\n<div>Next&nbsp;Line</div>"
    assert ticket_importer._clean_text(value) == "Hello\nWorld\nNext Line"


def test_clean_text_preserves_angle_brackets_when_not_tags():
    assert ticket_importer._clean_text("Value is < 3 &amp; rising") == "Value is < 3 & rising"


@pytest.mark.anyio
async def test_import_ticket_by_id_creates_new_ticket(monkeypatch):
    async def fake_get_ticket(ticket_id, rate_limiter=None):
        assert ticket_id == 101
        return {
            "id": 101,
            "subject": "Printer offline",
            "priority": "High",
            "status": "In Progress",
            "problem": "Printer in marketing is jammed",
            "customer_id": "200",
            "created_at": "2025-01-01T08:30:00Z",
            "updated_at": "2025-01-02T10:45:00Z",
        }

    async def fake_get_company(syncro_id):
        assert syncro_id == "200"
        return {"id": 7}

    async def fake_get_company_by_name(_name):
        return None

    async def fake_get_existing(external_reference):
        assert external_reference == "101"
        return None

    created_calls = {}

    async def fake_create_ticket(**kwargs):
        created_calls.update(kwargs)
        return {"id": 55, **kwargs}

    update_calls = []

    async def fake_update_ticket(ticket_id, **fields):
        update_calls.append((ticket_id, fields))
        return {"id": ticket_id, **fields}

    async def fake_get_user_by_email(_email):
        return None

    monkeypatch.setattr(syncro, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", fake_get_company)
    monkeypatch.setattr(company_repo, "get_company_by_name", fake_get_company_by_name)
    monkeypatch.setattr(tickets_repo, "get_ticket_by_external_reference", fake_get_existing)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)
    monkeypatch.setattr(ticket_importer.user_repo, "get_user_by_email", fake_get_user_by_email)

    summary = await ticket_importer.import_ticket_by_id(101, rate_limiter=None)

    assert summary.mode == "single"
    assert summary.fetched == 1
    assert summary.created == 1
    assert summary.updated == 0
    assert summary.skipped == 0
    assert created_calls["company_id"] == 7
    assert created_calls["priority"] == "high"
    assert created_calls["status"] == "in_progress"
    assert created_calls["ticket_number"] == "101"
    assert created_calls["requester_id"] is None
    assert update_calls[0][0] == 55
    assert "created_at" in update_calls[0][1]


@pytest.mark.anyio
async def test_import_ticket_by_id_updates_existing(monkeypatch):
    async def fake_get_ticket(ticket_id, rate_limiter=None):
        return {
            "id": ticket_id,
            "subject": "Network outage",
            "priority": "Critical",
            "status": "Resolved",
            "problem": "Restored connectivity",
            "customer_id": "200",
            "resolved_at": "2025-01-03T12:00:00Z",
        }

    async def fake_get_existing(external_reference):
        return {"id": 99, "external_reference": external_reference}

    update_calls = []

    async def fake_update_ticket(ticket_id, **fields):
        update_calls.append((ticket_id, fields))
        return {"id": ticket_id, **fields}

    async def fake_get_company(syncro_id):
        assert syncro_id == "200"
        return {"id": 3}

    async def fake_get_company_by_name(_name):
        return None

    async def fake_get_user_by_email(_email):
        return None

    monkeypatch.setattr(syncro, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", fake_get_company)
    monkeypatch.setattr(company_repo, "get_company_by_name", fake_get_company_by_name)
    monkeypatch.setattr(tickets_repo, "get_ticket_by_external_reference", fake_get_existing)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)
    monkeypatch.setattr(ticket_importer.user_repo, "get_user_by_email", fake_get_user_by_email)

    summary = await ticket_importer.import_ticket_by_id(500, rate_limiter=None)

    assert summary.updated == 1
    assert summary.created == 0
    assert update_calls[0][0] == 99
    assert update_calls[0][1]["status"] == "resolved"
    assert update_calls[0][1]["ticket_number"] == "500"
    assert update_calls[0][1]["requester_id"] is None


@pytest.mark.anyio
async def test_import_ticket_range_handles_missing(monkeypatch):
    async def fake_get_ticket(ticket_id, rate_limiter=None):
        if ticket_id == 10:
            return {"id": 10, "subject": "Login error", "status": "Open", "priority": "Normal"}
        return None

    async def fake_get_existing(external_reference):
        if external_reference == "10":
            return None
        return None

    async def fake_create_ticket(**kwargs):
        return {"id": 10, **kwargs}

    async def fake_update_ticket(ticket_id, **fields):
        return {"id": ticket_id, **fields}

    monkeypatch.setattr(syncro, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", lambda syncro_id: None)
    monkeypatch.setattr(tickets_repo, "get_ticket_by_external_reference", fake_get_existing)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)

    summary = await ticket_importer.import_ticket_range(10, 11, rate_limiter=None)

    assert summary.mode == "range"
    assert summary.fetched == 1
    assert summary.created == 1
    assert summary.skipped == 1


@pytest.mark.anyio
async def test_resolve_company_creates_company_when_missing(monkeypatch):
    created_payload: dict[str, Any] = {}

    async def fake_get_company_by_syncro_id(syncro_company_id: str):  # noqa: ARG001
        return None

    async def fake_get_company_by_name(company_name: str):  # noqa: ARG001
        return None

    async def fake_create_company(**payload):
        created_payload.update(payload)
        return {"id": 42, **payload}

    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", fake_get_company_by_syncro_id)
    monkeypatch.setattr(company_repo, "get_company_by_name", fake_get_company_by_name)
    monkeypatch.setattr(company_repo, "create_company", fake_create_company)

    ticket = {
        "id": 501,
        "customer_id": "9001",
        "customer": {"id": 9001, "business_name": "Example Holdings"},
    }

    company_id = await ticket_importer._resolve_company_id(ticket)

    assert company_id == 42
    assert created_payload["name"] == "Example Holdings"
    assert created_payload["syncro_company_id"] == "9001"


@pytest.mark.anyio
async def test_import_all_tickets_uses_pagination(monkeypatch):
    pages = {
        1: ([{"id": 201, "subject": "Laptop setup", "status": "Open", "priority": "Normal", "customer_id": "300"}], {"total_pages": 2}),
        2: ([{"id": 202, "subject": "VPN issue", "status": "Resolved", "priority": "High", "customer_id": "300"}], {"total_pages": 2}),
    }

    async def fake_list_tickets(page, per_page=25, rate_limiter=None):
        return pages.get(page, ([], {}))

    async def fake_get_existing(external_reference):
        if external_reference == "202":
            return {"id": 88, "external_reference": external_reference}
        return None

    created = []
    updated = []

    async def fake_create_ticket(**kwargs):
        created.append(kwargs)
        return {"id": 77, **kwargs}

    async def fake_update_ticket(ticket_id, **fields):
        updated.append((ticket_id, fields))
        return {"id": ticket_id, **fields}

    async def fake_get_company(syncro_id):
        return {"id": 12}

    monkeypatch.setattr(syncro, "list_tickets", fake_list_tickets)
    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", fake_get_company)
    monkeypatch.setattr(tickets_repo, "get_ticket_by_external_reference", fake_get_existing)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)

    summary = await ticket_importer.import_all_tickets(rate_limiter=None)

    assert summary.mode == "all"
    assert summary.fetched == 2
    assert summary.created == 1
    assert summary.updated == 1
    assert created[0]["external_reference"] == "201"
    assert updated[0][0] == 88


@pytest.mark.anyio
async def test_import_ticket_syncs_comments_and_watchers(monkeypatch):
    async def fake_get_ticket(ticket_id, rate_limiter=None):
        assert ticket_id == 5001
        return {
            "id": ticket_id,
            "number": "TCK-5001",
            "subject": "Email outage",
            "priority": {"name": "Normal"},
            "status": {"name": "Open"},
            "customer_business_then_name": "Acme Corp - Jane Doe",
            "comments": [
                {
                    "id": 1,
                    "subject": "Initial Issue",
                    "body": "Customer message",
                    "tech": "customer-reply",
                    "hidden": False,
                    "destination_emails": [
                        "customer@example.com",
                        "tech@example.com",
                    ],
                    "created_at": "2025-01-01T08:00:00Z",
                },
                {
                    "id": 2,
                    "body": "Internal update",
                    "tech": "agent",
                    "user_email": "tech@example.com",
                    "hidden": True,
                    "destination_emails": [
                        "tech@example.com",
                        "support@hawkinsitsolutions.com.au",
                    ],
                    "created_at": "2025-01-01T09:00:00Z",
                },
            ],
            "contact": {"email": "customer@example.com"},
        }

    async def fake_get_existing(_external_reference):
        return None

    async def fake_get_company_by_syncro_id(_syncro_id):
        return None

    async def fake_get_company_by_name(name):
        if name in {"Acme Corp - Jane Doe", "Acme Corp"}:
            return {"id": 8}
        return None

    created_call = {}

    async def fake_create_ticket(**kwargs):
        created_call.update(kwargs)
        return {"id": 400, **kwargs}

    update_calls = []

    async def fake_update_ticket(ticket_id, **fields):
        update_calls.append((ticket_id, fields))
        return {"id": ticket_id, **fields}

    reply_calls = []

    async def fake_list_replies(ticket_id, include_internal=True):  # noqa: ARG001
        return []

    async def fake_create_reply(**kwargs):
        reply_calls.append(kwargs)
        return {"id": len(reply_calls), **kwargs}

    async def fake_list_watchers(ticket_id):  # noqa: ARG001
        return []

    added_watchers = []

    async def fake_add_watcher(ticket_id, user_id):
        added_watchers.append((ticket_id, user_id))

    async def fake_get_user_by_email(email):
        mapping = {
            "customer@example.com": {"id": 21},
            "tech@example.com": {"id": 31},
        }
        return mapping.get(email)

    monkeypatch.setattr(syncro, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", fake_get_company_by_syncro_id)
    monkeypatch.setattr(company_repo, "get_company_by_name", fake_get_company_by_name)
    monkeypatch.setattr(tickets_repo, "get_ticket_by_external_reference", fake_get_existing)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(tickets_repo, "list_watchers", fake_list_watchers)
    monkeypatch.setattr(tickets_repo, "add_watcher", fake_add_watcher)
    monkeypatch.setattr(ticket_importer.user_repo, "get_user_by_email", fake_get_user_by_email)

    summary = await ticket_importer.import_ticket_by_id(5001, rate_limiter=None)

    assert summary.created == 1
    assert created_call["ticket_number"] == "TCK-5001"
    assert created_call["company_id"] == 8
    assert created_call["requester_id"] == 21
    assert created_call["description"] == "Customer message"
    assert len(reply_calls) == 2
    assert reply_calls[0]["external_reference"] == "1"
    assert reply_calls[0]["is_internal"] is False
    assert reply_calls[0]["author_id"] == 21
    assert reply_calls[0].get("minutes_spent") is None
    assert reply_calls[0].get("is_billable", False) is False
    assert reply_calls[1]["external_reference"] == "2"
    assert reply_calls[1]["is_internal"] is True
    assert reply_calls[1]["author_id"] == 31
    assert reply_calls[1].get("minutes_spent") is None
    assert reply_calls[1].get("is_billable", False) is False
    assert added_watchers == [(400, 31)]


@pytest.mark.anyio
async def test_import_ticket_skips_existing_comment_replies(monkeypatch):
    async def fake_get_ticket(ticket_id, rate_limiter=None):
        return {
            "id": ticket_id,
            "subject": "Sync ticket",
            "priority": "Normal",
            "status": "Open",
            "comments": [
                {"id": 1, "body": "Existing", "tech": "agent", "hidden": False},
                {"id": 2, "body": "New", "tech": "agent", "hidden": False},
            ],
        }

    async def fake_get_existing(_external_reference):
        return {"id": 90, "external_reference": "700"}

    async def fake_update_ticket(ticket_id, **fields):
        return {"id": ticket_id, **fields}

    async def fake_list_replies(ticket_id, include_internal=True):  # noqa: ARG001
        return [{"external_reference": "1"}]

    reply_calls = []

    async def fake_create_reply(**kwargs):
        reply_calls.append(kwargs)
        return {"id": len(reply_calls), **kwargs}

    async def fake_list_watchers(ticket_id):  # noqa: ARG001
        return []

    async def fake_add_watcher(ticket_id, user_id):  # noqa: ARG001
        raise AssertionError("Watchers should not be added when no watcher emails are present")

    async def fake_get_user_by_email(_email):
        return None

    async def fake_get_company(syncro_id):
        return {"id": 5}

    async def fake_get_company_by_name(_name):
        return None

    monkeypatch.setattr(syncro, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", fake_get_company)
    monkeypatch.setattr(company_repo, "get_company_by_name", fake_get_company_by_name)
    monkeypatch.setattr(tickets_repo, "get_ticket_by_external_reference", fake_get_existing)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(tickets_repo, "list_watchers", fake_list_watchers)
    monkeypatch.setattr(tickets_repo, "add_watcher", fake_add_watcher)
    monkeypatch.setattr(ticket_importer.user_repo, "get_user_by_email", fake_get_user_by_email)

    summary = await ticket_importer.import_ticket_by_id(700, rate_limiter=None)

    assert summary.updated == 1
    assert len(reply_calls) == 1
    assert reply_calls[0]["external_reference"] == "2"
    assert reply_calls[0].get("minutes_spent") is None
    assert reply_calls[0].get("is_billable", False) is False
@pytest.mark.anyio
async def test_import_from_request_records_webhook_success(monkeypatch):
    summary = ticket_importer.TicketImportSummary(mode="single", fetched=1, created=1)

    async def fake_import_ticket_by_id(ticket_id, rate_limiter=None):
        assert ticket_id == 42
        return summary

    recorded: dict[str, dict[str, object]] = {}

    async def fake_create_manual_event(**kwargs):
        recorded["create"] = kwargs
        return {"id": 77}

    async def fake_record_success(
        event_id,
        *,
        attempt_number,
        response_status,
        response_body,
        **_kwargs,
    ):
        recorded["success"] = {
            "event_id": event_id,
            "attempt_number": attempt_number,
            "response_status": response_status,
            "response_body": response_body,
        }
        return {"id": event_id, "status": "succeeded"}

    async def fake_record_failure(*args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("record_manual_failure should not be invoked on success")

    monkeypatch.setattr(ticket_importer, "import_ticket_by_id", fake_import_ticket_by_id)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_success", fake_record_success)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_failure", fake_record_failure)

    result = await ticket_importer.import_from_request(
        mode="single", ticket_id=42, start_id=None, end_id=None, rate_limiter=None
    )

    assert result is summary
    assert recorded["create"]["name"] == "syncro.ticket.import"
    assert recorded["create"]["target_url"].startswith("syncro://tickets/import?mode=single")
    assert recorded["create"]["payload"]["ticketId"] == 42
    success_payload = json.loads(recorded["success"]["response_body"])
    assert success_payload == summary.as_dict()
    assert recorded["success"]["response_status"] == 200


@pytest.mark.anyio
async def test_import_from_request_falls_back_to_repo(monkeypatch):
    summary = ticket_importer.TicketImportSummary(mode="single", fetched=1, created=1)

    async def fake_import_ticket_by_id(ticket_id, rate_limiter=None):
        assert ticket_id == 21
        return summary

    recorded: dict[str, dict[str, object]] = {}
    attempts: list[dict[str, object]] = []
    completions: list[dict[str, object]] = []

    async def fake_create_manual_event(**kwargs):
        raise RuntimeError("webhook monitor unavailable")

    async def fake_create_event(**kwargs):
        recorded["fallback_create"] = kwargs
        return {"id": 55}

    async def fake_mark_in_progress(event_id):
        recorded["mark_in_progress"] = {"event_id": event_id}

    async def fake_record_success(*_args, **_kwargs):  # pragma: no cover - should not be called
        raise AssertionError("webhook_monitor.record_manual_success should not be used on fallback")

    async def fake_record_attempt(
        *,
        event_id,
        attempt_number,
        status,
        response_status,
        response_body,
        error_message,
        **_extra,
    ):
        attempts.append(
            {
                "event_id": event_id,
                "attempt_number": attempt_number,
                "status": status,
                "response_status": response_status,
                "response_body": response_body,
                "error_message": error_message,
            }
        )

    async def fake_mark_event_completed(event_id, *, attempt_number, response_status, response_body):
        completions.append(
            {
                "event_id": event_id,
                "attempt_number": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    monkeypatch.setattr(ticket_importer, "import_ticket_by_id", fake_import_ticket_by_id)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_success", fake_record_success)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_failure", lambda *_, **__: None)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "create_event", fake_create_event)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "mark_in_progress", fake_mark_in_progress)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "mark_event_completed", fake_mark_event_completed)

    result = await ticket_importer.import_from_request(
        mode="single", ticket_id=21, start_id=None, end_id=None, rate_limiter=None
    )

    assert result is summary
    assert recorded["fallback_create"]["name"] == "syncro.ticket.import"
    assert recorded["mark_in_progress"]["event_id"] == 55
    assert attempts == [
        {
            "event_id": 55,
            "attempt_number": 1,
            "status": "succeeded",
            "response_status": 200,
            "response_body": json.dumps(summary.as_dict()),
            "error_message": None,
        }
    ]
    assert completions == [
        {
            "event_id": 55,
            "attempt_number": 1,
            "response_status": 200,
            "response_body": json.dumps(summary.as_dict()),
        }
    ]


@pytest.mark.anyio
async def test_import_from_request_fallback_repo_create_failure(monkeypatch):
    summary = ticket_importer.TicketImportSummary(mode="single", fetched=1, created=1)

    async def fake_import_ticket_by_id(ticket_id, rate_limiter=None):
        assert ticket_id == 22
        return summary

    errors: list[tuple[str, dict[str, object]]] = []

    async def fake_create_manual_event(**_kwargs):
        raise RuntimeError("webhook monitor down")

    async def fake_create_event(**_kwargs):
        raise RuntimeError("database unavailable")

    async def fake_mark_in_progress(_event_id):  # pragma: no cover - should not be called
        raise AssertionError("mark_in_progress should not be invoked when creation fails")

    async def fake_record_success(*_args, **_kwargs):  # pragma: no cover - should not be called
        raise AssertionError("record_manual_success should not be called without an event")

    def fake_log_error(message, **kwargs):
        errors.append((message, kwargs))

    monkeypatch.setattr(ticket_importer, "import_ticket_by_id", fake_import_ticket_by_id)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_success", fake_record_success)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_failure", lambda *_, **__: None)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "create_event", fake_create_event)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "mark_in_progress", fake_mark_in_progress)
    monkeypatch.setattr(ticket_importer, "log_error", fake_log_error)

    result = await ticket_importer.import_from_request(
        mode="single", ticket_id=22, start_id=None, end_id=None, rate_limiter=None
    )

    assert result is summary
    messages = [message for message, _payload in errors]
    assert "Failed to create fallback Syncro ticket import event" in messages


@pytest.mark.anyio
async def test_import_from_request_fallback_mark_in_progress_failure(monkeypatch):
    summary = ticket_importer.TicketImportSummary(mode="single", fetched=1, created=1)

    async def fake_import_ticket_by_id(ticket_id, rate_limiter=None):
        assert ticket_id == 23
        return summary

    recorded: dict[str, object] = {}

    async def fake_create_manual_event(**_kwargs):
        raise RuntimeError("webhook monitor down")

    async def fake_create_event(**kwargs):
        recorded["create_event"] = kwargs
        return {"id": 66}

    async def fake_mark_in_progress(event_id):
        recorded["mark_in_progress_attempt"] = event_id
        raise RuntimeError("write lock timeout")

    async def fake_record_success(*_args, **_kwargs):  # pragma: no cover - should not be called
        raise AssertionError("record_manual_success should not be called when mark_in_progress fails")

    errors: list[tuple[str, dict[str, object]]] = []

    def fake_log_error(message, **kwargs):
        errors.append((message, kwargs))

    monkeypatch.setattr(ticket_importer, "import_ticket_by_id", fake_import_ticket_by_id)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_success", fake_record_success)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_failure", lambda *_, **__: None)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "create_event", fake_create_event)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "mark_in_progress", fake_mark_in_progress)
    monkeypatch.setattr(ticket_importer, "log_error", fake_log_error)

    result = await ticket_importer.import_from_request(
        mode="single", ticket_id=23, start_id=None, end_id=None, rate_limiter=None
    )

    assert result is summary
    assert recorded["create_event"]["name"] == "syncro.ticket.import"
    assert recorded["mark_in_progress_attempt"] == 66
    messages = [message for message, _payload in errors]
    assert "Failed to mark fallback Syncro ticket import event in progress" in messages


@pytest.mark.anyio
async def test_import_from_request_fallback_failure_records_repo(monkeypatch):
    async def fake_import_ticket_by_id(ticket_id, rate_limiter=None):
        assert ticket_id == 24
        raise RuntimeError("boom")

    async def fake_create_manual_event(**_kwargs):
        raise RuntimeError("webhook monitor down")

    async def fake_create_event(**_kwargs):
        return {"id": 88}

    async def fake_mark_in_progress(event_id):
        assert event_id == 88

    async def fake_record_success(*_args, **_kwargs):  # pragma: no cover - should not run
        raise AssertionError("webhook_monitor.record_manual_success should not be invoked")

    async def fake_record_failure(*_args, **_kwargs):  # pragma: no cover - should not run
        raise AssertionError("webhook_monitor.record_manual_failure should not be invoked")

    attempts: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    async def fake_record_attempt(
        *,
        event_id,
        attempt_number,
        status,
        response_status,
        response_body,
        error_message,
        **_extra,
    ):
        attempts.append(
            {
                "event_id": event_id,
                "attempt_number": attempt_number,
                "status": status,
                "response_status": response_status,
                "response_body": response_body,
                "error_message": error_message,
            }
        )

    async def fake_mark_event_failed(event_id, *, attempt_number, error_message, response_status, response_body):
        failures.append(
            {
                "event_id": event_id,
                "attempt_number": attempt_number,
                "error_message": error_message,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    monkeypatch.setattr(ticket_importer, "import_ticket_by_id", fake_import_ticket_by_id)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_success", fake_record_success)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_failure", fake_record_failure)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "create_event", fake_create_event)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "mark_in_progress", fake_mark_in_progress)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "mark_event_failed", fake_mark_event_failed)

    with pytest.raises(RuntimeError):
        await ticket_importer.import_from_request(
            mode="single", ticket_id=24, start_id=None, end_id=None, rate_limiter=None
        )

    assert attempts == [
        {
            "event_id": 88,
            "attempt_number": 1,
            "status": "failed",
            "response_status": None,
            "response_body": None,
            "error_message": "boom",
        }
    ]
    assert failures == [
        {
            "event_id": 88,
            "attempt_number": 1,
            "error_message": "boom",
            "response_status": None,
            "response_body": None,
        }
    ]


@pytest.mark.anyio
async def test_import_from_request_records_webhook_failure(monkeypatch):
    async def fake_import_ticket_by_id(ticket_id, rate_limiter=None):
        raise RuntimeError("boom")

    recorded: dict[str, dict[str, object]] = {}

    async def fake_create_manual_event(**kwargs):
        recorded["create"] = kwargs
        return {"id": 91}

    async def fake_record_success(*args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("record_manual_success should not be invoked on failure")

    async def fake_record_failure(event_id, *, attempt_number, status, error_message, response_status, response_body):
        recorded["failure"] = {
            "event_id": event_id,
            "attempt_number": attempt_number,
            "status": status,
            "error_message": error_message,
            "response_status": response_status,
            "response_body": response_body,
        }
        return {"id": event_id, "status": status}

    monkeypatch.setattr(ticket_importer, "import_ticket_by_id", fake_import_ticket_by_id)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_success", fake_record_success)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_failure", fake_record_failure)

    with pytest.raises(RuntimeError):
        await ticket_importer.import_from_request(
            mode="single", ticket_id=99, start_id=None, end_id=None, rate_limiter=None
        )

    assert recorded["failure"]["event_id"] == 91
    assert recorded["failure"]["status"] == "failed"
    assert recorded["failure"]["error_message"] == "boom"
