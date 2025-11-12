import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.services import modules as modules_service


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


def test_xero_callback_accepts_payload(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        assert slug == "xero"
        assert redact is False
        return {"enabled": True, "slug": "xero"}

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/xero/callback",
            json={"events": []},
        )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_xero_callback_rejects_invalid_json(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True, "slug": "xero"}

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/xero/callback",
            data="{",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid JSON payload"


def test_xero_callback_disabled_module(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": False, "slug": "xero"}

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.post(
            "/api/integration-modules/xero/callback",
            json={},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Xero module is disabled"


def test_xero_callback_probe(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True, "slug": "xero"}

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.get("/api/integration-modules/xero/callback")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_xero_list_tenants_disabled_module(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": False, "slug": "xero"}

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.get("/api/integration-modules/xero/tenants")

    assert response.status_code == 503
    assert response.json()["detail"] == "Xero module is disabled"


def test_xero_list_tenants_no_credentials(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True, "slug": "xero", "settings": {}}

    async def fake_get_xero_credentials():
        return None

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service, "get_xero_credentials", fake_get_xero_credentials)

    with TestClient(app) as client:
        response = client.get("/api/integration-modules/xero/tenants")

    assert response.status_code == 503
    assert response.json()["detail"] == "Xero credentials not configured"


def test_xero_list_tenants_incomplete_credentials(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True, "slug": "xero", "settings": {}}

    async def fake_get_xero_credentials():
        return {
            "client_id": "test_client_id",
            "client_secret": "",
            "refresh_token": "",
        }

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service, "get_xero_credentials", fake_get_xero_credentials)

    with TestClient(app) as client:
        response = client.get("/api/integration-modules/xero/tenants")

    assert response.status_code == 503
    assert response.json()["detail"] == "Xero OAuth credentials incomplete"


def test_xero_list_tenants_success(monkeypatch):
    import httpx

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {
            "enabled": True,
            "slug": "xero",
            "settings": {"tenant_id": "tenant-123"},
        }

    async def fake_get_xero_credentials():
        return {
            "client_id": "test_client_id",
            "client_secret": "test_secret",
            "refresh_token": "test_refresh_token",
        }

    async def fake_acquire_xero_access_token():
        return "test_access_token"

    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json_data = json_data
            self.status_code = status_code
            self.text = str(json_data)
            self.headers = {}

        def json(self):
            return self._json_data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("Error", request=None, response=self)

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            return MockResponse([
                {
                    "tenantId": "tenant-123",
                    "tenantName": "Test Organization",
                    "tenantType": "ORGANISATION",
                    "createdDateUtc": "2020-01-01T00:00:00Z",
                },
                {
                    "tenantId": "tenant-456",
                    "tenantName": "Another Organization",
                    "tenantType": "ORGANISATION",
                    "createdDateUtc": "2021-01-01T00:00:00Z",
                },
            ])

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service, "get_xero_credentials", fake_get_xero_credentials)
    monkeypatch.setattr(modules_service, "acquire_xero_access_token", fake_acquire_xero_access_token)
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: MockAsyncClient())

    with TestClient(app) as client:
        response = client.get("/api/integration-modules/xero/tenants")

    assert response.status_code == 200
    data = response.json()
    assert "tenants" in data
    assert "current_tenant_id" in data
    assert len(data["tenants"]) == 2
    assert data["tenants"][0]["tenant_id"] == "tenant-123"
    assert data["tenants"][0]["tenant_name"] == "Test Organization"
    assert data["current_tenant_id"] == "tenant-123"
