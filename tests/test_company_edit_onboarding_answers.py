"""Approved companies retain submitted onboarding answers for super admins."""

from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main
from app.core.database import db
from app.features.companies import business_hours_handlers
from app.features.companies import handlers
from app.repositories import client_onboarding as onboarding_repo
from app.repositories import companies as company_repo


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("is_super_admin", [True, False])
async def test_approved_company_loads_saved_onboarding_only_for_super_admins(monkeypatch, is_super_admin):
    company = {"id": 4, "name": "Acme", "email_domains": [], "pending_approval": False}
    saved_onboarding = {
        "id": 9,
        "status": "approved",
        "submission": {
            "custom_answers": [{
                "id": 2,
                "label": "Original question label",
                "section": "business",
                "field_type": "text",
                "value": "Saved answer",
                "display_value": "Saved answer",
            }],
            "sites": [],
        },
    }
    # Unrelated company settings are empty; this test exercises the real handler.
    monkeypatch.setattr(db, "fetch_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "fetch_one", AsyncMock(return_value=None))
    monkeypatch.setattr(company_repo, "get_company_by_id", AsyncMock(return_value=company))
    monkeypatch.setattr(
        handlers, "_get_company_management_scope",
        AsyncMock(return_value=(is_super_admin, [company], {})),
    )
    monkeypatch.setattr(main, "_get_m365_admin_credentials", AsyncMock(return_value=("", "", "")))
    monkeypatch.setattr(business_hours_handlers, "company_edit_context", AsyncMock(return_value={}))
    get_onboarding = AsyncMock(return_value=saved_onboarding)
    get_site_profiles = AsyncMock(return_value=[])
    monkeypatch.setattr(onboarding_repo, "get_by_company_id", get_onboarding)
    monkeypatch.setattr(onboarding_repo, "list_site_profiles", get_site_profiles)
    captured = {}

    async def render(template, request, user, *, extra):
        captured.update(extra)
        assert template == "admin/company_edit.html"
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", render)
    request = Request({
        "type": "http", "method": "GET", "path": "/admin/companies/4/edit", "headers": [],
    })
    response = await handlers._render_company_edit_page(
        request, {"id": 1, "is_super_admin": is_super_admin}, company_id=4,
    )

    assert response.status_code == 200
    if is_super_admin:
        get_onboarding.assert_awaited_once_with(4)
        assert captured["client_onboarding"] is saved_onboarding
        assert captured["client_onboarding"]["submission"]["custom_answers"][0]["label"] == "Original question label"
    else:
        get_onboarding.assert_not_awaited()
        assert "client_onboarding" not in captured
    get_site_profiles.assert_not_awaited()
