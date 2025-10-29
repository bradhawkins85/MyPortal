from collections import Counter
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api.routes.tickets import require_helpdesk_technician
from app.main import app
from app.services import tickets as tickets_service


def test_ticket_dashboard_endpoint(monkeypatch):
    client = TestClient(app)

    sample_state = tickets_service.TicketDashboardState(
        tickets=[
            {
                "id": 41,
                "subject": "Printer offline",
                "status": "open",
                "priority": "high",
                "company_id": 301,
                "assigned_user_id": 22,
                "module_slug": None,
                "requester_id": 12,
                "updated_at": datetime(2024, 5, 1, 9, 30, tzinfo=timezone.utc),
            }
        ],
        total=1,
        status_counts=Counter({"open": 1}),
        available_statuses=["open", "in_progress"],
        modules=[],
        companies=[{"id": 301, "name": "Acme Corp"}],
        technicians=[{"id": 22, "email": "tech@example.com"}],
        company_lookup={301: {"id": 301, "name": "Acme Corp"}},
        user_lookup={22: {"id": 22, "email": "tech@example.com"}},
    )

    load_state_mock = AsyncMock(return_value=sample_state)
    monkeypatch.setattr(tickets_service, "load_dashboard_state", load_state_mock)

    app.dependency_overrides[require_helpdesk_technician] = lambda: {"id": 1, "is_super_admin": True}
    try:
        response = client.get("/api/tickets/dashboard", params={"status": "open", "module": "ops"})
    finally:
        app.dependency_overrides.pop(require_helpdesk_technician, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["status_counts"] == {"open": 1}
    assert payload["filters"]["status"] == "open"
    assert payload["filters"]["module_slug"] == "ops"
    assert payload["items"][0]["company_name"] == "Acme Corp"
    assert payload["items"][0]["assigned_user_email"] == "tech@example.com"

    load_state_mock.assert_awaited_once()
    kwargs = load_state_mock.await_args.kwargs
    assert kwargs["status_filter"] == "open"
    assert kwargs["module_filter"] == "ops"
