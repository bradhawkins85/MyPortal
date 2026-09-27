from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest
from starlette.datastructures import FormData
from starlette.requests import Request

from app.features.websites import routes
from app.features.websites.routes import _form_payload


def test_website_form_maps_links_and_monitoring_options():
    payload = _form_payload(FormData([
        ("name", "Customer portal"), ("url", "https://example.com"),
        ("monitor_availability", "on"), ("asset_ids", "4"), ("asset_ids", "7"),
        ("knowledge_base_article_ids", "12"),
    ]))

    assert payload.name == "Customer portal"
    assert payload.monitor_availability is True
    assert payload.monitor_tls is False
    assert payload.asset_ids == [4, 7]
    assert payload.knowledge_base_article_ids == [12]


def test_website_form_rejects_invalid_link_identifier():
    with pytest.raises(HTTPException) as exc:
        _form_payload(FormData([
            ("name", "Portal"), ("url", "https://example.com"), ("asset_ids", "other-company"),
        ]))

    assert exc.value.status_code == 422
    assert exc.value.detail == "Invalid linked record"


@pytest.mark.anyio
async def test_website_delete_removes_company_website_and_records_audit(monkeypatch):
    request = Request({"type": "http", "method": "POST", "path": "/websites/42/delete", "headers": []})
    monkeypatch.setattr(routes, "_web_context", AsyncMock(return_value=(
        {"id": 3}, 7, {}, None,
    )))
    monkeypatch.setattr(routes.repo, "get_website", AsyncMock(return_value={
        "id": 42, "name": "Customer portal", "url": "https://example.com",
    }))
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(routes.repo, "delete_website", delete)
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)

    response = await routes.website_delete(request, 42)

    assert response.status_code == 303
    assert response.headers["location"] == "/websites?deleted=1"
    delete.assert_awaited_once_with(7, 42)
    assert audit.await_args.kwargs["action"] == "website.delete"
    assert audit.await_args.kwargs["before"]["name"] == "Customer portal"


@pytest.mark.anyio
async def test_website_delete_returns_not_found_without_deleting(monkeypatch):
    request = Request({"type": "http", "method": "POST", "path": "/websites/42/delete", "headers": []})
    monkeypatch.setattr(routes, "_web_context", AsyncMock(return_value=(
        {"id": 3}, 7, {}, None,
    )))
    monkeypatch.setattr(routes.repo, "get_website", AsyncMock(return_value=None))
    delete = AsyncMock()
    monkeypatch.setattr(routes.repo, "delete_website", delete)

    with pytest.raises(HTTPException) as exc:
        await routes.website_delete(request, 42)

    assert exc.value.status_code == 404
    delete.assert_not_awaited()
