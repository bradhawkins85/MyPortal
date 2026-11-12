from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.api.routes import invoices, orders, quotes
from app.security.commerce_permissions import commerce_record_visible, commerce_scope
from app.security.menu_permissions import build_menu_access_map, normalize_menu_permissions


USER = {"id": 5, "company_id": 7, "is_super_admin": False}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def records(kind):
    now = datetime.now(timezone.utc)
    base = {"company_id": 7, "status": "draft", "created_at": now, "notes": None}
    if kind == "invoices":
        base |= {"invoice_number": "INV", "amount": Decimal("12.00")}
    else:
        base |= {kind[:-1] + "_number": "REF", "shipping_status": "pending", "order_date": now}
    owner_field = "created_by" if kind == "invoices" else "user_id"
    return [base | {"id": 1, owner_field: 5},
            base | {"id": 2, owner_field: 8, "assigned_user_id": 5},
            base | {"id": 3, owner_field: 8, "assigned_user_id": 9},
            base | {"id": 4}]


@pytest.mark.parametrize("kind", ["quotes", "orders", "invoices"])
@pytest.mark.parametrize("level, expected", [("none", 0), ("mine", 2), ("all", 4)])
@pytest.mark.anyio
async def test_api_visibility_applies_to_lists_and_direct_links(monkeypatch, kind, level, expected):
    module = {"quotes": quotes, "orders": orders, "invoices": invoices}[kind]
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[require_database] = lambda: None
    membership = {"menu_permissions": {"menu." + kind: level}}
    monkeypatch.setattr(module.user_company_repo, "get_user_company", AsyncMock(return_value=membership))
    monkeypatch.setattr(module.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 7}))
    repo = module.invoice_repo if kind == "invoices" else module.shop_repo
    list_method = "list_company_invoices" if kind == "invoices" else "list_" + kind[:-1] + "_summaries"
    detail_method = "get_invoice_by_id" if kind == "invoices" else "get_" + kind[:-1] + "_summary"
    monkeypatch.setattr(repo, list_method, AsyncMock(return_value=records(kind)))
    detail = AsyncMock(return_value=records(kind)[2])
    monkeypatch.setattr(repo, detail_method, detail)
    if kind != "invoices":
        monkeypatch.setattr(repo, "list_" + kind[:-1] + "_items", AsyncMock(return_value=[]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/" + kind, params={"companyId": 7})
        if level == "none":
            assert response.status_code == 403
        else:
            assert response.status_code == 200, response.text
            assert len(response.json()) == expected
        response = await client.get("/api/" + kind + "/3", params={"companyId": 7})
        assert response.status_code == {"none": 403, "mine": 404, "all": 200}[level], response.text
        if level == "mine":
            for record in records(kind)[:2]:
                detail.return_value = record
                response = await client.get("/api/" + kind + "/1", params={"companyId": 7})
                assert response.status_code == 200, response.text


@pytest.mark.parametrize("kind", ["quotes", "orders", "invoices"])
def test_scope_respects_explicit_denials_and_does_not_promote_mine(kind):
    key = "menu." + kind
    flags = {"can_access_quotes": True, "can_access_orders": True, "can_manage_invoices": True}
    membership = flags | {"menu_permissions": {key: "read"}}
    assert commerce_scope(USER, membership, key) == "mine"
    assert build_menu_access_map(is_super_admin=False, membership_data=membership)[key] == "read"
    membership["menu_permissions"][key] = "none"
    assert commerce_scope(USER, membership, key) == "none"
    assert build_menu_access_map(is_super_admin=False, membership_data=membership)[key] == "none"
    assert commerce_scope(USER, None, key) == "none"
    assert commerce_scope(USER, flags, key) == "all"


@pytest.mark.parametrize("extra", [{"is_admin": True}, {"menu_permissions": {"menu.admin.technician": "write"}}])
def test_company_admins_and_technicians_retain_all_access(extra):
    assert commerce_scope(USER, extra, "menu.invoices") == "all"
    assert commerce_scope(USER | {"is_super_admin": True}, None, "menu.invoices") == "all"
    assert commerce_scope(USER, {"menu_permissions": {"menu.admin.company": "read"}}, "menu.invoices") == "none"


def test_mine_never_exposes_unowned_records_or_uses_invoice_approver_as_owner():
    assert not commerce_record_visible(USER, "mine", {}, "menu.invoices")
    assert not commerce_record_visible(USER, "mine", {"approved_by": 5}, "menu.invoices")
    assert normalize_menu_permissions({"menu.invoices": "Mine", "menu.quotes": "All"})["menu.invoices"] == "read"


@pytest.mark.anyio
async def test_api_role_preview_uses_simulated_permissions(monkeypatch):
    monkeypatch.setattr(invoices.user_company_repo, "get_user_company", AsyncMock(return_value={"is_admin": True}))
    user = USER | {"simulated_membership": {"company_id": 7, "menu_permissions": {"menu.invoices": "mine"}}}
    assert await invoices._ensure_company_access(user, 7) == "mine"
    with pytest.raises(HTTPException) as exc:
        await invoices._ensure_company_access(user, 8)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("action", ["export_quote_pdf", "load_quote_to_cart"])
@pytest.mark.anyio
async def test_mine_cannot_download_or_load_another_users_quote(monkeypatch, action):
    from app.features.quotes import routes

    membership = {"menu_permissions": {"menu.quotes": "mine"}}
    main = SimpleNamespace(_load_company_section_context=AsyncMock(return_value=(USER, membership, {}, 7, None)))
    monkeypatch.setattr(routes, "_main", lambda: main)
    monkeypatch.setattr(routes.session_manager, "load_session", AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(routes.shop_repo, "get_quote_summary", AsyncMock(return_value={"user_id": 9}))
    items = AsyncMock()
    monkeypatch.setattr(routes.shop_repo, "list_quote_items", items)
    with pytest.raises(HTTPException) as exc:
        await getattr(routes, action)(Request({"type": "http", "method": "GET", "path": "/quotes"}), "Q1")
    assert exc.value.status_code == 404
    items.assert_not_awaited()


@pytest.mark.parametrize("kind", ["quotes", "orders", "invoices"])
@pytest.mark.anyio
async def test_portal_lists_and_totals_only_include_visible_records(monkeypatch, kind):
    from app.features.invoices import routes as invoice_pages
    from app.features.orders import routes as order_pages
    from app.features.quotes import routes as quote_pages

    module = {"quotes": quote_pages, "orders": order_pages, "invoices": invoice_pages}[kind]
    membership = {"menu_permissions": {"menu." + kind: "mine"}}
    main = SimpleNamespace(
        _load_company_section_context=AsyncMock(return_value=(USER, membership, {}, 7, None)),
        _render_template=AsyncMock(side_effect=lambda *args, **kwargs: kwargs["extra"]),
    )
    monkeypatch.setattr(module, "_main", lambda: main)
    if kind == "invoices":
        monkeypatch.setattr(module, "_load_invoice_context", main._load_company_section_context)
        monkeypatch.setattr(module.invoice_repo, "list_company_invoices", AsyncMock(return_value=records(kind)))
    else:
        monkeypatch.setattr(module.shop_repo, "list_" + kind[:-1] + "_summaries", AsyncMock(return_value=records(kind)))
    if kind == "quotes":
        monkeypatch.setattr(module.user_repo, "get_user_by_id", AsyncMock(return_value={"email": "user@example.com"}))
    kwargs = {} if kind == "invoices" else {"status_filter": None}
    if kind == "orders":
        kwargs["shipping_filter"] = None
    result = await getattr(module, kind + "_page")(Request({"type": "http"}), **kwargs)
    assert len(result[kind]) == 2
    if kind == "invoices":
        assert result["total_amount_display"] == "$24.00"
    else:
        assert result[kind + "_total_all"] == 2


@pytest.mark.anyio
async def test_dashboard_invoice_totals_respect_mine(monkeypatch):
    from app.services import dashboard

    membership = {"can_manage_invoices": True, "menu_permissions": {"menu.invoices": "mine"}}
    ctx = dashboard._DashboardContext(
        request=Request({"type": "http"}), user=USER, user_id=5,
        is_super_admin=False, active_company_id=7, active_company={},
        available_companies=[], membership=membership,
    )
    monkeypatch.setattr(dashboard.tickets_repo, "count_tickets_for_user", AsyncMock(return_value=0))
    monkeypatch.setattr(dashboard.invoice_repo, "list_company_invoices", AsyncMock(return_value=records("invoices")))
    result = await dashboard._attention_section(ctx)
    item = next(item for item in result["items"] if item["key"] == "invoices.outstanding")
    assert item["count"] == 2
    assert item["detail"] == "$24.00"
