import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.api.routes import tickets as tickets_routes
from app.schemas.tickets import TacticalRMMTicketCreate, TacticalRMMTicketResolve


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/tickets/tacticalrmm",
            "headers": [],
        }
    )


def test_tacticalrmm_ticket_maps_external_company_and_agent_ids(monkeypatch):
    asyncio.run(
        _test_tacticalrmm_ticket_maps_external_company_and_agent_ids(monkeypatch)
    )


async def _test_tacticalrmm_ticket_maps_external_company_and_agent_ids(monkeypatch):
    now = datetime.now(timezone.utc)
    created = {
        "id": 91,
        "subject": "Disk alert on workstation",
        "status": "new",
        "priority": "normal",
        "requester_id": None,
        "company_id": 42,
        "assigned_user_id": None,
        "category": "disk",
        "module_slug": "tacticalrmm",
        "external_reference": "alert-123",
        "created_at": now,
        "updated_at": now,
    }
    create_ticket = AsyncMock(return_value=created)
    replace_assets = AsyncMock(return_value=[])

    monkeypatch.setattr(
        tickets_routes.companies_repo,
        "get_company_by_tactical_id",
        AsyncMock(return_value={"id": 42}),
    )
    monkeypatch.setattr(
        tickets_routes.tickets_repo,
        "get_ticket_by_external_reference",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        tickets_routes.assets_repo,
        "get_asset_by_tactical_id",
        AsyncMock(return_value={"id": 73}),
    )
    monkeypatch.setattr(
        tickets_routes.tickets_service,
        "validate_status_choice",
        AsyncMock(return_value="new"),
    )
    monkeypatch.setattr(tickets_routes.tickets_service, "create_ticket", create_ticket)
    monkeypatch.setattr(
        tickets_routes.tickets_service, "refresh_ticket_ai_summary", AsyncMock()
    )
    monkeypatch.setattr(
        tickets_routes.tickets_service, "refresh_ticket_ai_tags", AsyncMock()
    )
    monkeypatch.setattr(
        tickets_routes.tickets_repo, "replace_ticket_assets", replace_assets
    )
    monkeypatch.setattr(tickets_routes.audit_service, "record", AsyncMock())
    monkeypatch.setattr(
        tickets_routes, "_build_ticket_detail", AsyncMock(return_value=created)
    )

    payload = TacticalRMMTicketCreate(
        subject="Disk alert on workstation",
        description="Disk is almost full",
        status="new",
        category="disk",
        company_id=1007,
        tactical_agent_id="agent-9",
        requester_id=888,
        assigned_user_id=999,
        alert_id=123,
    )
    result = await tickets_routes.create_tacticalrmm_ticket(
        payload, _request(), {"user": None, "api_key": {"id": 3, "name": "TRMM"}}
    )

    assert result == created
    tickets_routes.companies_repo.get_company_by_tactical_id.assert_awaited_once_with(
        "1007"
    )
    tickets_routes.assets_repo.get_asset_by_tactical_id.assert_awaited_once_with(
        42, "agent-9"
    )
    replace_assets.assert_awaited_once_with(91, [73])
    assert create_ticket.await_args.kwargs["company_id"] == 42
    assert create_ticket.await_args.kwargs["requester_id"] is None
    assert create_ticket.await_args.kwargs["assigned_user_id"] is None
    assert create_ticket.await_args.kwargs["module_slug"] == "tacticalrmm"
    assert (
        create_ticket.await_args.kwargs["external_reference"] == "tacticalrmm:alert:123"
    )


def test_tacticalrmm_ticket_rejects_unmapped_company_before_creation(monkeypatch):
    asyncio.run(
        _test_tacticalrmm_ticket_rejects_unmapped_company_before_creation(monkeypatch)
    )


async def _test_tacticalrmm_ticket_rejects_unmapped_company_before_creation(
    monkeypatch,
):
    create_ticket = AsyncMock()
    monkeypatch.setattr(
        tickets_routes.tickets_repo,
        "get_ticket_by_external_reference",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        tickets_routes.companies_repo,
        "get_company_by_tactical_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(tickets_routes.tickets_service, "create_ticket", create_ticket)

    with pytest.raises(Exception) as exc_info:
        await tickets_routes.create_tacticalrmm_ticket(
            TacticalRMMTicketCreate(
                subject="Alert", company_id="missing-client", alert_id=456
            ),
            _request(),
            {"user": None, "api_key": {"id": 3}},
        )

    assert getattr(exc_info.value, "status_code", None) == 404
    assert "missing-client" in str(getattr(exc_info.value, "detail", ""))
    create_ticket.assert_not_awaited()


def test_tacticalrmm_ticket_reuses_ticket_for_retried_alert(monkeypatch):
    asyncio.run(_test_tacticalrmm_ticket_reuses_ticket_for_retried_alert(monkeypatch))


async def _test_tacticalrmm_ticket_reuses_ticket_for_retried_alert(monkeypatch):
    existing = {"id": 91, "external_reference": "tacticalrmm:alert:123"}
    monkeypatch.setattr(
        tickets_routes.tickets_repo,
        "get_ticket_by_external_reference",
        AsyncMock(return_value=existing),
    )
    monkeypatch.setattr(
        tickets_routes.companies_repo, "get_company_by_tactical_id", AsyncMock()
    )
    monkeypatch.setattr(
        tickets_routes,
        "_build_ticket_detail",
        AsyncMock(return_value={**existing, "status": "new"}),
    )

    result = await tickets_routes.create_tacticalrmm_ticket(
        TacticalRMMTicketCreate(subject="Alert", company_id=1007, alert_id=123),
        _request(),
        {"user": None, "api_key": {"id": 3}},
    )

    assert result["id"] == 91
    tickets_routes.companies_repo.get_company_by_tactical_id.assert_not_awaited()


def test_tacticalrmm_resolved_webhook_resolves_matching_ticket(monkeypatch):
    asyncio.run(
        _test_tacticalrmm_resolved_webhook_resolves_matching_ticket(monkeypatch)
    )


async def _test_tacticalrmm_resolved_webhook_resolves_matching_ticket(monkeypatch):
    existing = {
        "id": 91,
        "status": "new",
        "external_reference": "tacticalrmm:alert:123",
    }
    resolved = {**existing, "status": "resolved"}
    monkeypatch.setattr(
        tickets_routes.tickets_repo,
        "get_ticket_by_external_reference",
        AsyncMock(return_value=existing),
    )
    monkeypatch.setattr(
        tickets_routes.tickets_service,
        "validate_status_choice",
        AsyncMock(return_value="resolved"),
    )
    set_status = AsyncMock(return_value=resolved)
    monkeypatch.setattr(tickets_routes.tickets_repo, "set_ticket_status", set_status)
    monkeypatch.setattr(
        tickets_routes.tickets_service, "broadcast_ticket_event", AsyncMock()
    )
    monkeypatch.setattr(
        tickets_routes.tickets_service, "emit_ticket_updated_event", AsyncMock()
    )
    monkeypatch.setattr(tickets_routes.audit_service, "record", AsyncMock())
    monkeypatch.setattr(
        tickets_routes, "_build_ticket_detail", AsyncMock(return_value=resolved)
    )

    result = await tickets_routes.resolve_tacticalrmm_ticket(
        TacticalRMMTicketResolve(alert_id=123),
        _request(),
        {"user": None, "api_key": {"id": 3, "name": "TRMM"}},
    )

    assert result["status"] == "resolved"
    set_status.assert_awaited_once_with(91, "resolved")
