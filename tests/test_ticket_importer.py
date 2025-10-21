import json

import pytest

from app.services import ticket_importer
from app.services import syncro
from app.repositories import tickets as tickets_repo
from app.repositories import companies as company_repo


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_normalise_status_mapping():
    assert ticket_importer._normalise_status("In Progress") == "in_progress"
    assert ticket_importer._normalise_status("Waiting on customer") == "pending"
    assert ticket_importer._normalise_status("Completed") == "resolved"


def test_normalise_priority_mapping():
    assert ticket_importer._normalise_priority("Critical") == "urgent"
    assert ticket_importer._normalise_priority("LOW") == "low"
    assert ticket_importer._normalise_priority(None) == "normal"


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

    monkeypatch.setattr(syncro, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", fake_get_company)
    monkeypatch.setattr(tickets_repo, "get_ticket_by_external_reference", fake_get_existing)
    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)

    summary = await ticket_importer.import_ticket_by_id(101, rate_limiter=None)

    assert summary.mode == "single"
    assert summary.fetched == 1
    assert summary.created == 1
    assert summary.updated == 0
    assert summary.skipped == 0
    assert created_calls["company_id"] == 7
    assert created_calls["priority"] == "high"
    assert created_calls["status"] == "in_progress"
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

    monkeypatch.setattr(syncro, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", fake_get_company)
    monkeypatch.setattr(tickets_repo, "get_ticket_by_external_reference", fake_get_existing)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)

    summary = await ticket_importer.import_ticket_by_id(500, rate_limiter=None)

    assert summary.updated == 1
    assert summary.created == 0
    assert update_calls[0][0] == 99
    assert update_calls[0][1]["status"] == "resolved"


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
    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)

    summary = await ticket_importer.import_ticket_range(10, 11, rate_limiter=None)

    assert summary.mode == "range"
    assert summary.fetched == 1
    assert summary.created == 1
    assert summary.skipped == 1


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
    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)

    summary = await ticket_importer.import_all_tickets(rate_limiter=None)

    assert summary.mode == "all"
    assert summary.fetched == 2
    assert summary.created == 1
    assert summary.updated == 1
    assert created[0]["external_reference"] == "201"
    assert updated[0][0] == 88


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

    async def fake_record_success(event_id, *, attempt_number, response_status, response_body):
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

    async def fake_create_manual_event(**kwargs):
        raise RuntimeError("webhook monitor unavailable")

    async def fake_create_event(**kwargs):
        recorded["fallback_create"] = kwargs
        return {"id": 55}

    async def fake_mark_in_progress(event_id):
        recorded["mark_in_progress"] = {"event_id": event_id}

    async def fake_record_success(event_id, *, attempt_number, response_status, response_body):
        recorded["success"] = {
            "event_id": event_id,
            "attempt_number": attempt_number,
            "response_status": response_status,
            "response_body": response_body,
        }

    monkeypatch.setattr(ticket_importer, "import_ticket_by_id", fake_import_ticket_by_id)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_success", fake_record_success)
    monkeypatch.setattr(ticket_importer.webhook_monitor, "record_manual_failure", lambda *_, **__: None)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "create_event", fake_create_event)
    monkeypatch.setattr(ticket_importer.webhook_events_repo, "mark_in_progress", fake_mark_in_progress)

    result = await ticket_importer.import_from_request(
        mode="single", ticket_id=21, start_id=None, end_id=None, rate_limiter=None
    )

    assert result is summary
    assert recorded["fallback_create"]["name"] == "syncro.ticket.import"
    assert recorded["mark_in_progress"]["event_id"] == 55
    success_payload = json.loads(recorded["success"]["response_body"])
    assert success_payload == summary.as_dict()
    assert recorded["success"]["event_id"] == 55


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
