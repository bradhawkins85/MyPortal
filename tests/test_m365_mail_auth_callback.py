"""Regression tests for the M365 mail OAuth callback."""
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    async def fake_start():
        return None

    async def fake_stop():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


def test_m365_mail_callback_handles_null_company_id(monkeypatch):
    """Shared mailbox sign-in (company_id None) should not raise during callback."""

    async def fake_pkce_client_id_for_company(company_id, *, redirect_uri=None):
        raise AssertionError("company-specific PKCE client ID should not be requested")

    async def fake_pkce_client_id(*, redirect_uri=None):
        return "pkce-client-id"

    monkeypatch.setattr(
        main_module.m365_service,
        "get_effective_pkce_client_id_for_company",
        fake_pkce_client_id_for_company,
    )
    monkeypatch.setattr(
        main_module.m365_service, "get_effective_pkce_client_id", fake_pkce_client_id
    )
    monkeypatch.setattr(
        main_module.m365_service,
        "extract_tenant_id_from_token",
        lambda token: "tenant-123",
    )

    stored: dict = {}

    async def fake_store_tokens(
        account_id, *, tenant_id, refresh_token, access_token, expires_at
    ):
        stored.update(
            {
                "account_id": account_id,
                "tenant_id": tenant_id,
                "refresh_token": refresh_token,
                "access_token": access_token,
                "expires_at": expires_at,
            }
        )

    async def fake_get_account(account_id):
        return {"name": "Shared Mailbox", "id": account_id}

    monkeypatch.setattr(
        main_module.m365_mail_service, "store_delegated_tokens", fake_store_tokens
    )
    monkeypatch.setattr(main_module.m365_mail_service, "get_account", fake_get_account)

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
                "id_token": "id-token",
            }

    calls = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

        async def post(self, url, data=None, **kwargs):
            calls.append({"url": url, "data": data})
            return FakeResponse()

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)

    state = main_module.oauth_state_serializer.dumps(
        {
            "flow": "m365_mail_auth",
            "account_id": 1,
            "company_id": None,  # shared mailbox without company association
            "code_verifier": "code-verify",
        }
    )

    with TestClient(app) as client:
        response = client.get(
            f"/m365/callback?code=test-code&state={quote(state)}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/modules/m365-mail?success=")
    assert stored["account_id"] == 1
    assert stored["tenant_id"] == "tenant-123"
    assert calls, "Token exchange should be attempted"
    assert calls[0]["data"]["code_verifier"] == "code-verify"
