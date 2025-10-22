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

    recorded: dict[str, object] = {}

    async def fake_handle_event(event_name, context):  # pragma: no cover - via test
        recorded["event"] = event_name
        recorded["context"] = context
        return []

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)

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
    assert recorded["event"] == "tickets.created"
    assert recorded["context"] == {"ticket": ticket}


@pytest.mark.anyio
async def test_create_ticket_can_skip_automations(monkeypatch):
    async def fake_create_ticket(**kwargs):
        return {"id": 77, **kwargs}

    called = False

    async def fake_handle_event(event_name, context):  # pragma: no cover - via test
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)

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
    assert called is False
