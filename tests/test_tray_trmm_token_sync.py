import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import tacticalrmm
from app.services import tray


def test_update_trmm_client_token_field_uses_trmm_put_payload(monkeypatch):
    calls = []

    async def fake_call_endpoint(endpoint, *, method="GET", body=None):
        calls.append({"endpoint": endpoint, "method": method, "body": body})
        if method == "GET":
            return {
                "id": 42,
                "name": "Acme",
                "custom_fields": [
                    {"id": 11, "field": 7, "name": "MyPortalToken", "value": "old"}
                ],
            }
        return {"status": "ok"}

    from app.services import tacticalrmm

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    result = asyncio.run(
        tray.update_trmm_client_token_field(trmm_client_id=42, token="new-token")
    )

    assert result == {"status": "ok"}
    assert calls == [
        {"endpoint": "/clients/42/", "method": "GET", "body": None},
        {
            "endpoint": "/clients/42/",
            "method": "PUT",
            "body": {
                "custom_fields": [{"field": 7, "value": "new-token", "id": 11}],
            },
        },
    ]


def test_update_trmm_client_token_field_resolves_client_field_definition(monkeypatch):
    calls = []

    async def fake_call_endpoint(endpoint, *, method="GET", body=None):
        calls.append({"endpoint": endpoint, "method": method, "body": body})
        if endpoint == "/clients/42/" and method == "GET":
            return {"id": 42, "name": "Acme", "custom_fields": []}
        if endpoint == "/core/customfields/" and method == "GET":
            return [
                {"id": 3, "model": "agent", "name": "Portal Token"},
                {"id": 9, "model": "client", "name": "Portal Token"},
            ]
        return {"status": "ok"}

    from app.services import tacticalrmm

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    asyncio.run(
        tray.update_trmm_client_token_field(
            trmm_client_id="42", token="new-token", field_name="Portal Token"
        )
    )

    assert calls == [
        {"endpoint": "/clients/42/", "method": "GET", "body": None},
        {"endpoint": "/core/customfields/", "method": "GET", "body": None},
        {
            "endpoint": "/clients/42/",
            "method": "PUT",
            "body": {
                "custom_fields": [{"field": 9, "value": "new-token"}],
            },
        },
    ]


def test_update_trmm_client_token_field_preserves_id_only_custom_fields(monkeypatch):
    calls = []

    async def fake_call_endpoint(endpoint, *, method="GET", body=None):
        calls.append({"endpoint": endpoint, "method": method, "body": body})
        if endpoint == "/clients/86/" and method == "GET":
            return {
                "id": 86,
                "name": "Forgate Contracting",
                "custom_fields": [
                    {"id": 12, "field": 12, "client": 86, "value": "forgate"},
                    {"id": 13, "field": 4, "client": 86, "value": None},
                    {"id": 14, "field": 5, "client": 86, "value": None},
                ],
            }
        if endpoint == "/core/customfields/" and method == "GET":
            return {
                "results": [
                    {"id": 12, "model": "client", "name": "Company Slug"},
                    {"id": 4, "model": "client", "name": "MyPortalToken"},
                    {"id": 5, "model": "client", "name": "Other"},
                ]
            }
        return {"status": "ok"}

    from app.services import tacticalrmm

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    asyncio.run(
        tray.update_trmm_client_token_field(trmm_client_id=86, token="new-token")
    )

    assert calls[-1] == {
        "endpoint": "/clients/86/",
        "method": "PUT",
        "body": {
            "custom_fields": [
                {"field": 12, "value": "forgate", "id": 12},
                {"field": 4, "value": "new-token", "id": 13},
                {"field": 5, "value": None, "id": 14},
            ]
        },
    }


@pytest.mark.asyncio
async def test_update_trmm_client_token_field_propagates_put_errors(monkeypatch):
    from app.services.tacticalrmm import TacticalRMMAPIError

    async def fake_call_endpoint(endpoint, method="GET", body=None):
        if method == "GET" and endpoint == "/clients/123/":
            return _client_with_token_field("old")
        if method == "PUT":
            raise TacticalRMMAPIError("custom field does not exist")
        return {"status": "ok"}

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    with pytest.raises(TacticalRMMAPIError):
        await tray.update_trmm_client_token_field(trmm_client_id="123", token="new")


def _company(client_id: str | None) -> dict:
    return {"id": 7, "name": "Acme", "tacticalrmm_client_id": client_id}


def _client_with_token_field(value: str | None) -> dict:
    custom_fields = [{"id": 11, "field": 9, "name": "MyPortalToken"}]
    if value is not None:
        custom_fields[0]["value"] = value
    return {"id": 7, "name": "Acme", "custom_fields": custom_fields}


def _install_token(record_id: int, company_id: int = 7, revoked: bool = False) -> dict:
    return {
        "id": record_id,
        "company_id": company_id,
        "revoked_at": "2026-01-01T00:00:00Z" if revoked else None,
        "expires_at": None,
    }


def _async_get_company(client_id: str | None):
    async def _get(company_id: int):
        return _company(client_id)

    return _get


@pytest.mark.asyncio
async def test_read_trmm_client_token_field_returns_value(monkeypatch):
    async def fake_call_endpoint(endpoint, method="GET", body=None):
        assert endpoint == "/clients/7/"
        assert method == "GET"
        return _client_with_token_field("my-portal-token")

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
    value = await tray.read_trmm_client_token_field(trmm_client_id="7")
    assert value == "my-portal-token"


@pytest.mark.asyncio
async def test_read_trmm_client_token_field_none_when_blank_or_absent(monkeypatch):
    cases = [
        _client_with_token_field(""),  # present but blank
        {"id": 7, "custom_fields": []},  # field absent
        {"id": 7},  # no custom_fields key
    ]
    for client in cases:
        async def fake_call_endpoint(endpoint, method="GET", body=None, client=client):
            return client

        monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
        value = await tray.read_trmm_client_token_field(trmm_client_id="7")
        assert value is None


@pytest.mark.asyncio
async def test_sync_skips_when_client_already_carries_active_company_token(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_call_endpoint(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return _client_with_token_field("active-company-token")

    async def fake_list_install_tokens(company_id=None):
        return [_install_token(5)]

    async def fake_get_token_by_hash(token_hash):
        return _install_token(5)

    create = AsyncMock(return_value={"id": 99})
    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
    monkeypatch.setattr(tray.tray_repo, "list_install_tokens", fake_list_install_tokens)
    monkeypatch.setattr(
        tray.tray_repo, "get_install_token_by_hash", fake_get_token_by_hash
    )
    monkeypatch.setattr(tray.tray_repo, "create_install_token", create)
    monkeypatch.setattr(
        tray.companies_repo, "get_company_by_id", _async_get_company("7")
    )

    result = await tray.sync_company_trmm_tray_token(company_id=7, created_by_user_id=1)

    assert result["status"] == "skipped"
    assert result["reason"] == "already_synced"
    assert result["token_id"] == 5
    assert create.await_count == 0
    assert all(method != "PUT" for _, method, _ in calls), "must not re-publish"


@pytest.mark.asyncio
async def test_sync_replaces_token_when_client_field_is_empty(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_call_endpoint(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return {"status": "ok"} if method == "PUT" else _client_with_token_field("")

    async def fake_list_install_tokens(company_id=None):
        return [_install_token(5)]  # active token exists, raw value unrecoverable

    create = AsyncMock(return_value={"id": 99})
    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
    monkeypatch.setattr(tray.tray_repo, "list_install_tokens", fake_list_install_tokens)
    monkeypatch.setattr(tray.tray_repo, "create_install_token", create)
    monkeypatch.setattr(tray.companies_repo, "get_company_by_id", _async_get_company("7"))

    result = await tray.sync_company_trmm_tray_token(company_id=7, created_by_user_id=1)

    assert result["status"] == "updated"
    assert result["token_id"] == 99
    assert create.await_count == 1
    puts = [call for call in calls if call[1] == "PUT"]
    assert len(puts) == 1
    put_endpoint, _, put_body = puts[0]
    assert put_endpoint == "/clients/7/"
    field_value = put_body["custom_fields"][0]["value"]
    assert isinstance(field_value, str) and field_value


@pytest.mark.asyncio
async def test_sync_replaces_token_when_client_carries_revoked_token(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_call_endpoint(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return {"status": "ok"} if method == "PUT" else _client_with_token_field("old")

    async def fake_list_install_tokens(company_id=None):
        return [_install_token(5)]  # an active token exists

    async def fake_get_token_by_hash(token_hash):
        return _install_token(3, revoked=True)  # field maps to a revoked token

    create = AsyncMock(return_value={"id": 99})
    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
    monkeypatch.setattr(tray.tray_repo, "list_install_tokens", fake_list_install_tokens)
    monkeypatch.setattr(
        tray.tray_repo, "get_install_token_by_hash", fake_get_token_by_hash
    )
    monkeypatch.setattr(tray.tray_repo, "create_install_token", create)
    monkeypatch.setattr(tray.companies_repo, "get_company_by_id", _async_get_company("7"))

    result = await tray.sync_company_trmm_tray_token(company_id=7, created_by_user_id=1)

    assert result["status"] == "updated"
    assert create.await_count == 1
    assert any(method == "PUT" for _, method, _ in calls)


@pytest.mark.asyncio
async def test_sync_creates_and_publishes_token_when_none_exists(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_call_endpoint(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return {"status": "ok"} if method == "PUT" else _client_with_token_field("")

    async def fake_list_install_tokens(company_id=None):
        return []  # no token yet

    create = AsyncMock(return_value={"id": 42})
    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
    monkeypatch.setattr(tray.tray_repo, "list_install_tokens", fake_list_install_tokens)
    monkeypatch.setattr(tray.tray_repo, "create_install_token", create)
    monkeypatch.setattr(tray.companies_repo, "get_company_by_id", _async_get_company("7"))

    result = await tray.sync_company_trmm_tray_token(company_id=7, created_by_user_id=1)

    assert result["status"] == "updated"
    assert result["token_id"] == 42
    assert create.await_count == 1
    assert len([call for call in calls if call[1] == "PUT"]) == 1


@pytest.mark.asyncio
async def test_sync_skips_when_company_has_no_trmm_client(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_call_endpoint(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return {}

    create = AsyncMock(return_value={"id": 99})
    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)
    monkeypatch.setattr(tray.tray_repo, "create_install_token", create)
    monkeypatch.setattr(
        tray.companies_repo, "get_company_by_id", _async_get_company(None)
    )

    result = await tray.sync_company_trmm_tray_token(company_id=7, created_by_user_id=1)

    assert result["status"] == "skipped"
    assert result["reason"] == "missing_tacticalrmm_client_id"
    assert calls == []  # no TRMM calls at all
    assert create.await_count == 0
