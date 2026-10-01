"""Tests for app.services.hudu – specifically create_asset_password."""

from __future__ import annotations

import pytest

from app.services import hudu as hudu_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_create_asset_password_uses_flat_endpoint_with_company_id_and_sends_auth(
    monkeypatch,
):
    """create_asset_password must POST to Hudu's asset_passwords endpoint with company_id in the body.

    Hudu exposes password creation at /api/v1/asset_passwords. Posting to a
    nested /api/v1/companies/{company_id}/asset_passwords URL returns 404 on
    Hudu Cloud instances, so the company association must be sent as the
    asset_password.company_id field.
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

    # Endpoint must be the flat Hudu asset_passwords endpoint (not company-scoped)
    assert captured["url"] == "https://hudu.example.com/api/v1/asset_passwords"

    # Authentication header must be present with the configured API key
    assert captured["headers"].get("x-api-key") == "test-api-key-abc"

    # Hudu expects company_id in the asset_password body
    pw_body = captured["json"]["asset_password"]
    assert pw_body["company_id"] == "99"

    # Core fields must be present
    assert pw_body["name"] == "Test Password"
    assert pw_body["password"] == "s3cr3t"
    assert pw_body["username"] == "alice@example.com"

    assert result["id"] == 42


@pytest.mark.anyio
async def test_create_asset_password_401_reports_password_access_guidance(monkeypatch):
    """401s on the password endpoint should guide admins to Hudu password API scope."""

    class DummyResponse:
        status_code = 401

        def raise_for_status(
            self,
        ):  # pragma: no cover - _raise_for_status handles 401 first
            raise AssertionError(
                "raise_for_status should not be called for 401 responses"
            )

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers=None, json=None, **kwargs):
            return DummyResponse()

    async def fake_load_settings():
        return {"base_url": "https://hudu.example.com", "api_key": "test-api-key-abc"}

    monkeypatch.setattr(hudu_service, "_load_settings", fake_load_settings)
    monkeypatch.setattr(hudu_service.httpx, "AsyncClient", DummyClient)

    with pytest.raises(hudu_service.HuduAuthenticationError) as exc_info:
        await hudu_service.create_asset_password(
            company_id="99",
            name="Test Password",
            password="s3cr3t",
        )

    error = str(exc_info.value)
    assert "Hudu rejected the API key" in error
    assert "Passwords access enabled" in error
    assert "company/IP address" in error


@pytest.mark.anyio
async def test_sync_discovered_device_updates_only_managed_fields(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            if url.endswith("asset_layouts"):
                return Response(
                    {
                        "asset_layouts": [
                            {
                                "id": 8,
                                "name": "Printer",
                                "fields": [
                                    {"id": index, "label": label}
                                    for index, label in enumerate(
                                        hudu_service._DEVICE_FIELDS, 1
                                    )
                                ],
                            }
                        ]
                    }
                )
            return Response(
                {
                    "assets": [
                        {
                            "id": 99,
                            "fields": [
                                {
                                    "asset_layout_field_id": 1,
                                    "value": "AA-BB-CC-DD-EE-FF",
                                },
                                {
                                    "asset_layout_field_id": 77,
                                    "value": "Admin-only value",
                                },
                            ],
                        }
                    ]
                }
            )

        async def put(self, url, **kwargs):
            calls.append((url, kwargs["json"]))
            return Response({"asset": {"id": 99}})

    async def settings():
        return {"base_url": "https://hudu.example.com", "api_key": "key"}

    monkeypatch.setattr(hudu_service, "_load_settings", settings)
    monkeypatch.setattr(hudu_service.httpx, "AsyncClient", Client)
    result = await hudu_service.sync_discovered_device(
        company_id="42",
        device={
            "device_type_name": "Printer",
            "mac_address": "aa:bb:cc:dd:ee:ff",
            "hostname": "reception-printer",
            "ip_address": "192.168.1.20",
            "mac_vendor": "Example Vendor",
            "first_seen_at": "2026-08-24 10:00:00",
        },
    )

    assert result["action"] == "updated"
    assert calls[0][0].endswith("/api/v1/assets/99")
    fields = calls[0][1]["asset"]["fields"]
    assert {"asset_layout_field_id": 77, "value": "Admin-only value"} in fields
    assert {"asset_layout_field_id": 1, "value": "AA:BB:CC:DD:EE:FF"} in fields
