"""The asset page's at-a-glance chips show who is logged on to the device."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import main as main_module
from app.features.assets import routes

DETAIL_TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "templates" / "assets" / "detail.html"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/assets/10", "headers": [], "query_string": b""})


def _patch_detail(monkeypatch, *, record, tray_devices, user=None):
    user = user or {"id": 7, "is_super_admin": True}
    monkeypatch.setattr(routes, "_load_asset_context", AsyncMock(return_value=(user, None, {"id": 3}, 3, None)))
    monkeypatch.setattr(routes.asset_repo, "get_asset_by_id", AsyncMock(return_value=record))
    monkeypatch.setattr(routes.asset_custom_fields_repo, "list_field_definitions", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.asset_custom_fields_repo, "get_all_asset_field_values", AsyncMock(return_value={}))
    monkeypatch.setattr(routes.asset_repo, "list_required_fields", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.asset_repo, "list_tickets_for_asset", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.asset_repo, "list_company_assets", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.asset_repo, "list_relationships_for_asset", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.knowledge_base_service, "build_access_context", AsyncMock(return_value=object()))
    monkeypatch.setattr(routes.knowledge_base_service, "list_articles_for_context", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.asset_photo_repo, "list_for_asset", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.tickets_repo, "list_tickets", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.infrastructure_repo, "overview", AsyncMock(return_value={"networks": [], "racks": []}))
    monkeypatch.setattr(routes.infrastructure_repo, "for_asset", AsyncMock(return_value={"addresses": [], "placements": []}))
    monkeypatch.setattr(routes.processes_repo, "list_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.websites_repo, "list_websites", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.websites_repo, "list_for_asset", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.bcp_repo, "list_bcp_context_for_asset", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.role_repo, "list_roles", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.audience_repo, "list_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.asset_repo, "list_reconciliation_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.asset_repo, "list_asset_sources", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes, "_customer_role_can_view_asset", AsyncMock(return_value=True))
    monkeypatch.setattr(
        routes, "_asset_type_picker",
        AsyncMock(return_value={"asset_type_mode": "manual", "asset_type_groups": [], "custom_asset_types": []}),
    )
    monkeypatch.setattr(routes, "_warranty_context", AsyncMock(return_value=None))
    tray_lookup = AsyncMock(return_value=tray_devices)
    monkeypatch.setattr(routes.tray_repo, "list_active_devices_by_asset_ids", tray_lookup)
    monkeypatch.setattr(main_module, "_feature_pack_available", lambda name: False)
    renderer = AsyncMock(return_value=routes.HTMLResponse("detail"))
    monkeypatch.setattr(main_module, "_render_template", renderer)
    return renderer, tray_lookup


@pytest.mark.anyio
async def test_tray_console_user_is_shown_over_the_synced_last_user(monkeypatch):
    renderer, tray_lookup = _patch_detail(
        monkeypatch,
        record={"id": 10, "company_id": 3, "last_user": "old.user"},
        tray_devices={10: {"id": 5, "console_user": " CONTOSO\\alice "}},
    )

    await routes.asset_detail_page(_request(), 10, customer_preview=False)

    tray_lookup.assert_awaited_once_with([10])
    assert renderer.await_args.kwargs["extra"]["logged_on_user"] == "CONTOSO\\alice"


@pytest.mark.anyio
async def test_synced_last_user_is_shown_without_a_tray_app(monkeypatch):
    renderer, _tray_lookup = _patch_detail(
        monkeypatch,
        record={"id": 10, "company_id": 3, "last_user": "bob"},
        tray_devices={},
    )

    await routes.asset_detail_page(_request(), 10, customer_preview=False)

    assert renderer.await_args.kwargs["extra"]["logged_on_user"] == "bob"


@pytest.mark.anyio
async def test_no_chip_when_nobody_is_reported(monkeypatch):
    renderer, _tray_lookup = _patch_detail(
        monkeypatch,
        record={"id": 10, "company_id": 3, "last_user": "  "},
        tray_devices={10: {"id": 5, "console_user": None}},
    )

    await routes.asset_detail_page(_request(), 10, customer_preview=False)

    assert renderer.await_args.kwargs["extra"]["logged_on_user"] is None


@pytest.mark.anyio
async def test_customer_view_omits_the_logged_on_user(monkeypatch):
    renderer, tray_lookup = _patch_detail(
        monkeypatch,
        record={"id": 10, "company_id": 3, "customer_visible": True, "last_user": "bob"},
        tray_devices={10: {"id": 5, "console_user": "alice"}},
        user={"id": 7},
    )

    await routes.asset_detail_page(_request(), 10, customer_preview=False)

    tray_lookup.assert_not_awaited()
    assert renderer.await_args.kwargs["extra"]["logged_on_user"] is None


def test_logged_on_chip_sits_in_the_technician_only_summary():
    template = DETAIL_TEMPLATE.read_text()
    chip = "{% if logged_on_user %}"
    assert chip in template
    technician_block = template.index("{% if not customer_safe %}", template.index('aria-label="Asset summary"'))
    assert technician_block < template.index(chip) < template.index("{% endif %}\n        </ul>", technician_block)
