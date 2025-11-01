from datetime import datetime, timezone

import pytest

from app.repositories import tickets as tickets_repo
from app.services import automations as automations_service
from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_create_ticket_triggers_automations(monkeypatch):
    async def fake_create_ticket(**kwargs):
        return {"id": 42, **kwargs}

    async def fake_get_company(company_id):
        assert company_id == 3
        return {"id": company_id, "name": "Acme Corp"}

    async def fake_get_user(user_id):
        return None

    recorded: dict[str, object] = {}

    async def fake_handle_event(event_name, context):  # pragma: no cover - via test
        recorded["event"] = event_name
        recorded["context"] = context
        return []

    async def fake_resolve_status(value):
        return value or "open"

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", fake_resolve_status)

    ticket = await tickets_service.create_ticket(
        subject="Printer offline",
        description="Printer in marketing has jammed",
        requester_id=5,
        company_id=3,
        assigned_user_id=None,
        priority="high",
        status="open",
        category="hardware",
        module_slug=None,
        external_reference=None,
        ticket_number=None,
    )

    assert ticket["id"] == 42
    assert ticket["company_name"] == "Acme Corp"
    assert ticket["assigned_user_display_name"] is None
    assert recorded["event"] == "tickets.created"
    assert recorded["context"] == {"ticket": ticket}


@pytest.mark.anyio
async def test_create_ticket_can_skip_automations(monkeypatch):
    async def fake_create_ticket(**kwargs):
        return {"id": 77, **kwargs}

    async def fake_get_company(company_id):
        return None

    async def fake_get_user(user_id):
        assert user_id == 10
        return {"id": user_id, "email": "tech@example.com", "first_name": "Sam", "last_name": "Support"}

    called = False

    async def fake_handle_event(event_name, context):  # pragma: no cover - via test
        nonlocal called
        called = True
        return []

    async def fake_resolve_status(value):
        return value or "open"

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", fake_resolve_status)

    ticket = await tickets_service.create_ticket(
        subject="Laptop setup",
        description=None,
        requester_id=None,
        company_id=None,
        assigned_user_id=10,
        priority="normal",
        status="open",
        category=None,
        module_slug="syncro",
        external_reference="abc",
        ticket_number="1001",
        trigger_automations=False,
    )

    assert ticket["id"] == 77
    assert ticket["assigned_user_email"] == "tech@example.com"
    assert ticket["assigned_user_display_name"] == "Sam Support"
    assert called is False


@pytest.mark.anyio
async def test_create_ticket_truncates_long_description(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_create_ticket(**kwargs):
        captured.update(kwargs)
        return {"id": 91, **kwargs}

    async def fake_handle_event(event_name, context):  # pragma: no cover - via test
        return []

    async def fake_get_company(company_id):
        return None

    async def fake_get_user(user_id):
        return None

    async def fake_resolve_status(value):
        return value or "open"

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", fake_resolve_status)

    long_description = "A" * (tickets_service._MAX_TICKET_DESCRIPTION_BYTES + 512)

    ticket = await tickets_service.create_ticket(
        subject="Overflowing description",
        description=long_description,
        requester_id=None,
        company_id=None,
        assigned_user_id=None,
        priority="normal",
        status="open",
        category=None,
        module_slug="imap",
        external_reference=None,
        ticket_number=None,
    )

    assert ticket["id"] == 91
    stored_description = captured.get("description")
    assert isinstance(stored_description, str)
    assert stored_description.endswith("Message truncated due to size]")
    assert (
        len(stored_description.encode("utf-8"))
        <= tickets_service._MAX_TICKET_DESCRIPTION_BYTES
    )


@pytest.mark.anyio
async def test_create_ticket_records_initial_reply(monkeypatch):
    recorded_reply: dict[str, object] = {}

    async def fake_create_ticket(**kwargs):
        return {"id": 135, **kwargs}

    async def fake_create_reply(**kwargs):
        recorded_reply.update(kwargs)
        return {"id": 246, **kwargs}

    async def fake_handle_event(event_name, context):  # pragma: no cover - via test
        return []

    async def fake_get_company(company_id):
        return None

    async def fake_get_user(user_id):
        return None

    async def fake_resolve_status(value):
        return value or "open"

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", fake_resolve_status)

    ticket = await tickets_service.create_ticket(
        subject="VPN issue",
        description="Cannot connect to VPN",
        requester_id=12,
        company_id=4,
        assigned_user_id=None,
        priority="normal",
        status="open",
        category=None,
        module_slug=None,
        external_reference=None,
        ticket_number=None,
        initial_reply_author_id=12,
    )

    assert ticket["id"] == 135
    assert recorded_reply["ticket_id"] == 135
    assert recorded_reply["author_id"] == 12
    assert recorded_reply["body"] == "Cannot connect to VPN"
    assert recorded_reply["is_internal"] is False


@pytest.mark.anyio
async def test_enrich_ticket_context_includes_relationship_details(monkeypatch):
    ticket = {
        "id": 99,
        "subject": "Printer offline",
        "company_id": 5,
        "requester_id": 7,
        "assigned_user_id": 11,
        "status": "open",
        "priority": "high",
    }

    watchers_records = [
        {
            "id": 301,
            "ticket_id": 99,
            "user_id": 21,
            "created_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        },
        {
            "id": 302,
            "ticket_id": 99,
            "user_id": 22,
            "created_at": datetime(2025, 1, 2, 8, 30, tzinfo=timezone.utc),
        },
    ]

    replies_records = [
        {
            "id": 401,
            "ticket_id": 99,
            "author_id": 21,
            "body": "Investigating",
            "is_internal": True,
            "created_at": datetime(2025, 1, 2, 9, 0, tzinfo=timezone.utc),
        },
        {
            "id": 402,
            "ticket_id": 99,
            "author_id": 11,
            "body": "Printer restored",
            "is_internal": False,
            "created_at": datetime(2025, 1, 2, 10, 15, tzinfo=timezone.utc),
        },
    ]

    user_records = {
        7: {
            "id": 7,
            "email": "requester@example.com",
            "first_name": "Riley",
            "last_name": "Jones",
        },
        11: {
            "id": 11,
            "email": "tech@example.com",
            "first_name": "Taylor",
            "last_name": "Nguyen",
        },
        21: {
            "id": 21,
            "email": "watcher.one@example.com",
            "first_name": "Morgan",
            "last_name": "Stone",
        },
        22: {
            "id": 22,
            "email": "watcher.two@example.com",
            "first_name": "Quinn",
            "last_name": "Patil",
        },
    }

    async def fake_get_company_by_id(company_id):
        assert company_id == 5
        return {"id": 5, "name": "Acme Corp"}

    async def fake_get_user_by_id(user_id):
        return user_records.get(int(user_id))

    async def fake_list_watchers(ticket_id):
        assert ticket_id == 99
        return watchers_records

    async def fake_list_replies(ticket_id, include_internal=True):
        assert ticket_id == 99
        assert include_internal is True
        return replies_records

    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(tickets_repo, "list_watchers", fake_list_watchers)
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)

    enriched = await tickets_service._enrich_ticket_context(ticket)

    assert enriched["company_name"] == "Acme Corp"
    assert enriched["requester_email"] == "requester@example.com"
    assert enriched["requester_display_name"] == "Riley Jones"
    assert enriched["assigned_user_display_name"] == "Taylor Nguyen"
    assert enriched["watchers_count"] == 2
    assert enriched["watcher_emails"] == [
        "watcher.one@example.com",
        "watcher.two@example.com",
    ]
    assert enriched["watchers"][0]["email"] == "watcher.one@example.com"
    assert enriched["watchers"][1]["display_name"] == "Quinn Patil"
    assert enriched["latest_reply"]
    assert enriched["latest_reply"]["body"] == "Printer restored"
    assert enriched["latest_reply"]["author_email"] == "tech@example.com"
    assert enriched["latest_reply"]["author_display_name"] == "Taylor Nguyen"


@pytest.mark.anyio
async def test_emit_ticket_updated_event_includes_actor_metadata(monkeypatch):
    ticket_record = {"id": 55, "status": "open"}

    async def fake_get_ticket(ticket_id):
        assert ticket_id == 55
        return ticket_record

    async def fake_enrich(ticket):
        assert ticket is ticket_record
        enriched = dict(ticket)
        enriched.update({
            "requester_id": 7,
            "assigned_user_id": 11,
            "watchers": [],
        })
        return enriched

    captured: dict[str, object] = {}

    async def fake_handle_event(event_name, context):
        captured["event"] = event_name
        captured["context"] = context
        return []

    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service, "_enrich_ticket_context", fake_enrich)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)

    actor = {
        "id": 11,
        "email": "tech@example.com",
        "first_name": "Taylor",
        "last_name": "Nguyen",
    }

    await tickets_service.emit_ticket_updated_event(
        55,
        actor_type="Technician",
        actor=actor,
    )

    assert captured["event"] == "tickets.updated"
    context = captured["context"]
    assert context["ticket"]["id"] == 55
    actor_meta = context["ticket_update"]
    assert actor_meta["actor_type"] == "technician"
    assert actor_meta["actor_label"] == "Technician"
    assert actor_meta["actor_user_email"] == "tech@example.com"
    assert actor_meta["actor_user_display_name"] == "Taylor Nguyen"


@pytest.mark.anyio
async def test_emit_ticket_updated_event_auto_detects_requester(monkeypatch):
    ticket_record = {"id": 88, "status": "open", "requester_id": 21}

    async def fake_get_ticket(ticket_id):
        assert ticket_id == 88
        return ticket_record

    async def fake_enrich(ticket):
        assert ticket is ticket_record
        enriched = dict(ticket)
        enriched["watchers"] = []
        return enriched

    captured: dict[str, object] = {}

    async def fake_handle_event(event_name, context):
        captured["event"] = event_name
        captured["context"] = context
        return []

    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service, "_enrich_ticket_context", fake_enrich)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)

    actor = {"id": 21, "email": "requester@example.com"}

    await tickets_service.emit_ticket_updated_event(88, actor=actor)

    context = captured["context"]
    assert context["ticket"]["id"] == 88
    actor_meta = context["ticket_update"]
    assert actor_meta["actor_type"] == "requester"
    assert actor_meta["actor_label"] == "Requester"
