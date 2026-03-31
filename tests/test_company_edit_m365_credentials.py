"""Tests for Microsoft 365 credential management on the company edit page."""
import pytest
from typing import Any
from unittest.mock import AsyncMock
from fastapi import status
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/companies/1/edit") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _base_monkeypatches(monkeypatch, company_id: int = 1):
    """Apply standard monkeypatches needed for _render_company_edit_page."""
    company_record = {
        "id": company_id,
        "name": "Test Company",
        "email_domains": [],
        "syncro_company_id": None,
        "xero_id": None,
        "tacticalrmm_client_id": None,
        "is_vip": 0,
    }
    monkeypatch.setattr(
        main.company_repo,
        "get_company_by_id",
        AsyncMock(return_value=company_record),
    )
    monkeypatch.setattr(
        main,
        "_get_company_management_scope",
        AsyncMock(return_value=(True, [{"id": company_id, "name": "Test Company"}], {})),
    )
    monkeypatch.setattr(
        main.user_company_repo,
        "list_assignments",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.role_repo,
        "list_roles",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.pending_staff_access_repo,
        "list_assignments_for_company",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.staff_repo,
        "list_staff_with_users",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.scheduled_tasks_repo,
        "list_tasks",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.recurring_items_repo,
        "list_company_recurring_invoice_items",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.billing_contacts_repo,
        "list_billing_contacts_for_company",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.staff_repo,
        "list_staff",
        AsyncMock(return_value=[]),
    )
    # Mock per-company admin credentials (returns None by default)
    monkeypatch.setattr(
        main.m365_service,
        "get_company_admin_credentials",
        AsyncMock(return_value=None),
    )


def _capture_render_template() -> tuple[dict[str, Any], Any]:
    """Return a (captured dict, fake_render_template) pair for use in tests."""
    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("ok")

    return captured, fake_render_template


@pytest.mark.anyio("asyncio")
async def test_m365_credentials_passed_to_template_when_present(monkeypatch):
    """M365 credentials are loaded and passed to the template when they exist."""
    _base_monkeypatches(monkeypatch, company_id=1)

    m365_creds = {
        "company_id": 1,
        "tenant_id": "test-tenant-id",
        "client_id": "test-client-id",
        "client_secret": "encrypted-secret",
        "token_expires_at": None,
    }
    monkeypatch.setattr(
        main.m365_service,
        "get_credentials",
        AsyncMock(return_value=m365_creds),
    )

    captured, fake_render_template = _capture_render_template()
    monkeypatch.setattr(main, "_render_template", fake_render_template)

    request = _make_request("/admin/companies/1/edit")
    current_user = {"id": 1, "is_super_admin": True}

    response = await main._render_company_edit_page(
        request,
        current_user,
        company_id=1,
    )

    assert response.status_code == status.HTTP_200_OK
    extra = captured.get("extra", {})
    assert extra.get("m365_has_credentials") is True
    cred = extra.get("m365_credential")
    assert cred is not None
    assert cred["tenant_id"] == "test-tenant-id"
    assert cred["client_id"] == "test-client-id"


@pytest.mark.anyio("asyncio")
async def test_m365_credentials_none_when_not_configured(monkeypatch):
    """m365_credential is None when no credentials are stored."""
    _base_monkeypatches(monkeypatch, company_id=2)

    monkeypatch.setattr(
        main.m365_service,
        "get_credentials",
        AsyncMock(return_value=None),
    )

    captured, fake_render_template = _capture_render_template()
    monkeypatch.setattr(main, "_render_template", fake_render_template)

    request = _make_request("/admin/companies/2/edit")
    current_user = {"id": 1, "is_super_admin": True}

    response = await main._render_company_edit_page(
        request,
        current_user,
        company_id=2,
    )

    assert response.status_code == status.HTTP_200_OK
    extra = captured.get("extra", {})
    assert extra.get("m365_has_credentials") is False
    assert extra.get("m365_credential") is None


@pytest.mark.anyio("asyncio")
async def test_m365_credentials_update_without_new_secret_preserves_encrypted_tokens(monkeypatch):
    """When no new secret is provided, encrypted values are preserved at rest."""
    company_id = 1
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=({"id": 9, "is_super_admin": True}, None)),
    )
    monkeypatch.setattr(
        main.company_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": company_id, "name": "Test Company"}),
    )

    class _FormRequest:
        async def form(self):
            return {
                "tenantId": "updated-tenant",
                "clientId": "updated-client",
                "clientSecret": "",
            }

    request = _FormRequest()

    monkeypatch.setattr(
        main.m365_repo,
        "get_credentials",
        AsyncMock(
            return_value={
                "client_secret": "enc-secret",
                "refresh_token": "enc-refresh",
                "access_token": "enc-access",
                "token_expires_at": None,
            }
        ),
    )
    monkeypatch.setattr(main.m365_service, "upsert_credentials", AsyncMock())
    upsert_mock = AsyncMock()
    monkeypatch.setattr(main.m365_repo, "upsert_credentials", upsert_mock)

    response = await main.admin_save_company_m365_credentials(company_id, request)

    assert response.status_code == status.HTTP_303_SEE_OTHER
    upsert_mock.assert_awaited_once_with(
        company_id=company_id,
        tenant_id="updated-tenant",
        client_id="updated-client",
        client_secret="enc-secret",
        refresh_token="enc-refresh",
        access_token="enc-access",
        token_expires_at=None,
    )
    main.m365_service.upsert_credentials.assert_not_awaited()
