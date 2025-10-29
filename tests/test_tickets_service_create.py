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

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)

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

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)

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

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)

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
