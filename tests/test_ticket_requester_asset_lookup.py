import asyncio
import json
from unittest.mock import AsyncMock

from app.repositories import assets as assets_repo
from app.features.tickets import admin_routes


def test_requester_asset_lookup_combines_previous_links_and_last_user(monkeypatch):
    monkeypatch.setattr(
        assets_repo.db,
        "fetch_one",
        AsyncMock(return_value={
            "company_id": 5, "requester_id": 7, "requester_staff_id": None,
            "email": "alex.smith@example.com", "first_name": "Alex", "last_name": "Smith",
        }),
    )
    monkeypatch.setattr(
        assets_repo.db, "fetch_all", AsyncMock(return_value=[{"asset_id": 10}])
    )
    monkeypatch.setattr(
        assets_repo,
        "list_company_assets",
        AsyncMock(return_value=[
            {"id": 10, "name": "Previously linked", "last_user": "other"},
            {"id": 11, "name": "Current computer", "last_user": "DOMAIN\\alex.smith"},
            {"id": 12, "name": "Unrelated", "last_user": "someone.else"},
        ]),
    )

    results = asyncio.run(assets_repo.list_assets_for_ticket_requester(123))

    assert [asset["id"] for asset in results] == [10, 11]
    assert results[0]["match_reasons"] == ["Previously linked to requester"]
    assert results[1]["match_reasons"] == ["Last logged-in user"]


def test_requester_asset_lookup_returns_empty_without_requester_company(monkeypatch):
    monkeypatch.setattr(assets_repo.db, "fetch_one", AsyncMock(return_value=None))
    fetch_all = AsyncMock()
    monkeypatch.setattr(assets_repo.db, "fetch_all", fetch_all)

    assert asyncio.run(assets_repo.list_assets_for_ticket_requester(404)) == []
    fetch_all.assert_not_awaited()


def test_ticket_assets_ui_keeps_manual_selector_and_adds_requester_lookup():
    template = open("app/templates/admin/ticket_detail.html", encoding="utf-8").read()

    assert "data-ticket-asset-selector" in template
    assert "data-requester-assets-lookup" in template
    assert "No assets associated with this requester were found." in open(
        "app/static/js/ticket_detail.js", encoding="utf-8"
    ).read()


def test_requester_asset_endpoint_returns_matching_assets(monkeypatch):
    monkeypatch.setattr(
        admin_routes,
        "_main",
        lambda: type("Main", (), {
            "_require_helpdesk_page": AsyncMock(return_value=({"id": 1}, None))
        })(),
    )
    monkeypatch.setattr(
        admin_routes.tickets_repo, "get_ticket", AsyncMock(return_value={"id": 123})
    )
    monkeypatch.setattr(
        admin_routes.assets_repo,
        "list_assets_for_ticket_requester",
        AsyncMock(return_value=[{
            "id": 11, "name": "Laptop", "serial_number": "ABC", "status": "active",
            "tactical_asset_id": "agent-1", "match_reasons": ["Last logged-in user"],
        }]),
    )

    response = asyncio.run(admin_routes.admin_ticket_requester_assets(123, object()))

    assert response.status_code == 200
    assert json.loads(response.body) == [{
        "id": 11, "name": "Laptop", "serial_number": "ABC", "status": "active",
        "tactical_asset_id": "agent-1", "match_reasons": ["Last logged-in user"],
    }]
