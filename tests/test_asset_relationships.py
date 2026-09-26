from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main as main_module
from app.features.assets import routes


def _request(path: str = "/assets/10/relationships") -> Request:
    body = b"target_type=asset&target_id=20&relationship_type=runs_on"
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({
        "type": "http", "method": "POST", "path": path,
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
    }, receive)


@pytest.mark.anyio
async def test_relationship_rejects_cross_company_target(monkeypatch):
    monkeypatch.setattr(
        routes, "_load_asset_context",
        AsyncMock(return_value=({"id": 7, "is_super_admin": True}, None, {}, 3, None)),
    )
    monkeypatch.setattr(
        routes.asset_repo, "get_asset_by_id",
        AsyncMock(side_effect=[{"id": 10, "company_id": 3}, {"id": 20, "company_id": 4}]),
    )

    with pytest.raises(HTTPException) as error:
        await routes.create_asset_relationship(_request(), 10)

    assert error.value.status_code == 404
    assert error.value.detail == "Relationship target not found"


@pytest.mark.anyio
async def test_relationship_duplicate_is_conflict(monkeypatch):
    monkeypatch.setattr(
        routes, "_load_asset_context",
        AsyncMock(return_value=({"id": 7, "is_super_admin": True}, None, {}, 3, None)),
    )
    monkeypatch.setattr(
        routes.asset_repo, "get_asset_by_id",
        AsyncMock(side_effect=[{"id": 10, "company_id": 3}, {"id": 20, "company_id": 3}]),
    )
    monkeypatch.setattr(routes.asset_repo, "create_relationship", AsyncMock(return_value=False))

    with pytest.raises(HTTPException) as error:
        await routes.create_asset_relationship(_request(), 10)

    assert error.value.status_code == 409
    assert error.value.detail == "Relationship already exists"


@pytest.mark.anyio
async def test_detail_omits_inaccessible_relationship_target(monkeypatch):
    monkeypatch.setattr(
        routes, "_load_asset_context",
        AsyncMock(return_value=({"id": 7, "is_super_admin": True}, None, {"id": 3}, 3, None)),
    )
    monkeypatch.setattr(routes.asset_repo, "get_asset_by_id", AsyncMock(return_value={"id": 10, "company_id": 3}))
    monkeypatch.setattr(routes.asset_custom_fields_repo, "list_field_definitions", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.asset_custom_fields_repo, "get_all_asset_field_values", AsyncMock(return_value={}))
    monkeypatch.setattr(routes.asset_repo, "list_required_fields", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.asset_repo, "list_tickets_for_asset", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.asset_repo, "list_company_assets", AsyncMock(return_value=[{"id": 10, "name": "App"}]))
    monkeypatch.setattr(routes.asset_repo, "list_relationships_for_asset", AsyncMock(return_value=[{
        "id": 2, "direction": "outbound", "target_type": "knowledge_base_article",
        "target_id": 999, "relationship_type": "documented_by",
    }]))
    monkeypatch.setattr(routes.knowledge_base_service, "build_access_context", AsyncMock(return_value=object()))
    monkeypatch.setattr(routes.knowledge_base_service, "list_articles_for_context", AsyncMock(return_value=[]))
    renderer = AsyncMock(return_value=routes.HTMLResponse("detail"))
    monkeypatch.setattr(main_module, "_render_template", renderer)

    await routes.asset_detail_page(_request("/assets/10"), 10)

    assert renderer.await_args.kwargs["extra"]["relationships"] == []
