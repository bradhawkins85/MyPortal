from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import FormData
from starlette.requests import Request

from app.features.companies import handlers
from app.repositories import companies
from app.security.flash import pop_flash
from app.services import audit, chocolatey_sources


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("keys", [["DEFAULT", "CUSTOM1"], []])
async def test_save_sources_returns_to_company_edit_section(monkeypatch, keys):
    company_id = 42
    main = SimpleNamespace(
        _require_authenticated_user=AsyncMock(return_value=({"id": 9}, None)),
        _feature_pack_available=lambda slug: True,
    )
    monkeypatch.setattr(handlers, "_main", lambda: main)
    permission = AsyncMock()
    monkeypatch.setattr(handlers, "_ensure_company_permission", permission)
    monkeypatch.setattr(companies, "get_company_by_id", AsyncMock(return_value={"id": company_id}))
    save = AsyncMock()
    monkeypatch.setattr(chocolatey_sources, "save", save)
    record = AsyncMock()
    monkeypatch.setattr(audit, "record", record)
    request = SimpleNamespace(method="POST", form=AsyncMock(
        return_value=FormData([("sources", key) for key in keys])))

    response = await handlers.admin_company_chocolatey_sources(company_id, request)

    save.assert_awaited_once_with(company_id, keys)
    permission.assert_awaited_once_with(request, {"id": 9}, company_id, require_admin=True)
    record.assert_awaited_once_with(
        action="rmm.chocolatey.sources", request=request, user_id=9,
        entity_type="company", entity_id=company_id, metadata={"enabled_sources": keys})
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/companies/42/edit#ce-chocolatey-sources"
    flash_request = Request({"type": "http", "headers": [
        (b"cookie", response.headers["set-cookie"].split(";", 1)[0].encode())]})
    assert pop_flash(flash_request) == {
        "message": "Chocolatey sources saved.", "variant": "success"}
