from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.features.assets import routes
from app.repositories import assets


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _request(method="POST", body=b"name=Printer&type=Printer&status=Active&serial_number=P-1&location=Office"):
    sent = False
    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "method": method, "path": "/assets",
                    "headers": [(b"content-type", b"application/x-www-form-urlencoded")]}, receive)


@pytest.mark.anyio
async def test_manual_create_uses_active_company_and_redirects_to_canonical_page(monkeypatch):
    monkeypatch.setattr(routes, "_asset_write_context", AsyncMock(return_value=({"id": 8}, {"id": 4}, 4, None)))
    create = AsyncMock(return_value=91)
    monkeypatch.setattr(routes.asset_repo, "create_manual_asset", create)
    monkeypatch.setattr(routes.asset_custom_fields_repo, "list_field_definitions", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())

    response = await routes.create_manual_asset(_request())

    assert response.status_code == 303
    assert response.headers["location"] == "/assets/91"
    assert create.await_args.kwargs == {
        "company_id": 4, "created_by": 8, "name": "Printer", "type": "Printer",
        "status": "Active", "serial_number": "P-1", "location": "Office",
    }


@pytest.mark.anyio
async def test_read_only_actor_cannot_open_manual_create(monkeypatch):
    monkeypatch.setattr(routes, "_load_asset_context", AsyncMock(return_value=({"id": 8}, {}, {"id": 4}, 4, None)))
    monkeypatch.setattr(routes._main(), "_membership_menu_can", lambda *args, **kwargs: False)
    with pytest.raises(routes.HTTPException) as error:
        await routes.new_asset_page(_request("GET", b""))
    assert error.value.status_code == 403


@pytest.mark.anyio
async def test_discovered_serial_match_requires_review_without_overwrite(monkeypatch):
    monkeypatch.setattr(assets.db, "fetch_one", AsyncMock(return_value=None))
    monkeypatch.setattr(assets.db, "fetch_all", AsyncMock(return_value=[{"id": 31, "provenance": "manual"}]))
    execute = AsyncMock(return_value=1)
    monkeypatch.setattr(assets.db, "execute", execute)

    result = await assets.upsert_asset(
        company_id=4, name="Printer", serial_number="P-1", source="syncro",
        source_external_id="remote-4", syncro_asset_id="remote-4",
    )

    assert result is None
    statements = "\n".join(call.args[0] for call in execute.await_args_list)
    assert "asset_source_records" in statements
    assert not any("UPDATE assets" in call.args[0] for call in execute.await_args_list)


@pytest.mark.anyio
async def test_health_queries_are_company_scoped(monkeypatch):
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(assets.db, "fetch_all", fetch)

    await assets.list_integration_health(44)
    await assets.list_company_reconciliation_queue(44)

    assert fetch.await_args_list[0].args[1] == (44,)
    assert fetch.await_args_list[1].args[1] == (44,)
    assert all("company_id = %s" in call.args[0] for call in fetch.await_args_list)


@pytest.mark.anyio
async def test_sync_failure_redacts_secret_bearing_error(monkeypatch):
    execute = AsyncMock(return_value=1)
    monkeypatch.setattr(assets.db, "execute", execute)

    await assets.finish_sync_run(7, error="Authorization: Bearer abc123 token=hidden")

    params = execute.await_args.args[1]
    assert params[0] == "failed"
    assert "abc123" not in params[2]
    assert "hidden" not in params[2]
    assert "[REDACTED]" in params[2]
