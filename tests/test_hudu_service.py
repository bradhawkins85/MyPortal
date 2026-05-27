"""Tests for app.services.hudu – specifically create_asset_password."""
from __future__ import annotations

import pytest

from app.services import hudu as hudu_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_create_asset_password_uses_company_scoped_endpoint_and_sends_auth(monkeypatch):
    """create_asset_password must POST to the company-scoped URL and include the x-api-key header.

    The flat /api/v1/asset_passwords endpoint does not carry the company ID
    in the URL, which causes Hudu to return 401 because it cannot verify that
    the API key is authorised for the requested company.  The correct endpoint
    is /api/v1/companies/{company_id}/asset_passwords.
    """
    captured: dict[str, object] = {}

    class DummyResponse:
        status_code = 201

        def raise_for_status(self):
            pass

        def json(self):
            return {"asset_password": {"id": 42, "name": "Test Password"}}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers=None, json=None, **kwargs):
            captured["url"] = url
            captured["headers"] = dict(headers) if headers else {}
            captured["json"] = json
            return DummyResponse()

    async def fake_load_settings():
        return {"base_url": "https://hudu.example.com", "api_key": "test-api-key-abc"}

    monkeypatch.setattr(hudu_service, "_load_settings", fake_load_settings)
    monkeypatch.setattr(hudu_service.httpx, "AsyncClient", DummyClient)

    result = await hudu_service.create_asset_password(
        company_id="99",
        name="Test Password",
        password="s3cr3t",
        username="alice@example.com",
    )

    # Endpoint must be company-scoped (not the flat /api/v1/asset_passwords)
    assert captured["url"] == "https://hudu.example.com/api/v1/companies/99/asset_passwords"

    # Authentication header must be present with the configured API key
    assert captured["headers"].get("x-api-key") == "test-api-key-abc"

    # company_id must NOT be in the body (it lives in the URL)
    pw_body = captured["json"]["asset_password"]
    assert "company_id" not in pw_body

    # Core fields must be present
    assert pw_body["name"] == "Test Password"
    assert pw_body["password"] == "s3cr3t"
    assert pw_body["username"] == "alice@example.com"

    assert result["id"] == 42
