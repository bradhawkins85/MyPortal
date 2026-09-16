from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.features.service_status import routes as service_status_routes
from app.services import service_status as service_status_service


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/service-status/public/1/token", "headers": []})


def test_public_status_token_validation_uses_company_scope(monkeypatch):
    monkeypatch.setattr(
        service_status_service,
        "get_settings",
        lambda: SimpleNamespace(secret_key="test-secret"),
    )

    first = service_status_service.build_public_status_token(1)
    second = service_status_service.build_public_status_token(2)

    assert first != second
    assert service_status_service.is_valid_public_status_token(1, first) is True
    assert service_status_service.is_valid_public_status_token(2, first) is False


def test_public_status_iso_fallback_uses_isoformat():
    value = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    assert service_status_routes._to_iso_fallback(value) == "2026-09-16T06:00:00+00:00"


def test_public_service_status_dashboard_rejects_invalid_token(monkeypatch):
    async def _run() -> None:
        with pytest.raises(HTTPException) as exc:
            await service_status_routes.public_service_status_dashboard(_request(), 1, "bad-token")
        assert exc.value.status_code == 404

    monkeypatch.setattr(service_status_service, "is_valid_public_status_token", lambda *_args: False)
    asyncio.run(_run())


def test_public_service_status_dashboard_renders_company_view(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_get_company_by_id(company_id):
        assert company_id == 4
        return {"id": 4, "name": "Acme", "archived": 0}

    async def fake_list_services_for_company(company_id):
        assert company_id == 4
        return [{"id": 1, "name": "Email", "status": "operational"}]

    async def fake_render_template(template, _request, user, *, extra):
        captured.update(template=template, user=user, extra=extra)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(service_status_service, "is_valid_public_status_token", lambda *_args: True)
    monkeypatch.setattr(service_status_routes.company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(service_status_service, "list_services_for_company", fake_list_services_for_company)
    monkeypatch.setattr(service_status_service, "summarise_services", lambda rows: {"total": len(rows), "by_status": {"operational": len(rows)}})
    monkeypatch.setattr(
        service_status_routes,
        "_main",
        lambda: SimpleNamespace(_render_template=fake_render_template),
    )

    response = asyncio.run(
        service_status_routes.public_service_status_dashboard(_request(), 4, "valid-token")
    )

    assert response.status_code == 200
    assert captured["template"] == "service_status/public_dashboard.html"
    assert captured["user"] == {"id": 0, "is_super_admin": False}
    assert captured["extra"]["public_company"]["name"] == "Acme"
