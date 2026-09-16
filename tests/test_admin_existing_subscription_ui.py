import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.features.subscriptions import admin_routes


class _FormRequest:
    async def form(self, **_kwargs):
        return self.form_data


def test_manual_subscription_template_exposes_required_fields():
    template = open(
        "app/templates/admin/subscription_create.html", encoding="utf-8"
    ).read()

    for field in ("customer_id", "product_id", "start_date", "quantity", "auto_renew"):
        assert f'name="{field}"' in template
    assert 'action="/admin/subscriptions/create"' in template
    assert 'include "partials/csrf.html"' in template


def test_manual_subscription_creation_delegates_to_existing_operation(monkeypatch):
    request = _FormRequest()
    request.form_data = {
        "customer_id": "12",
        "product_id": "34",
        "start_date": "2026-09-01",
        "quantity": "5",
        "auto_renew": "on",
    }
    user = {"id": 7, "is_super_admin": True}
    captured = {}

    async def allow(_request):
        return user, None

    async def create(payload, database, current_user):
        captured.update(payload.model_dump())
        assert database is None
        assert current_user is user
        return SimpleNamespace(id="external-123")

    monkeypatch.setattr(admin_routes, "_require_super_admin", allow)
    monkeypatch.setattr(admin_routes, "create_existing_subscription", create)

    response = asyncio.run(admin_routes.admin_create_subscription(request))

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/subscriptions?success=")
    assert captured == {
        "customer_id": 12,
        "product_id": 34,
        "start_date": captured["start_date"],
        "quantity": 5,
        "auto_renew": True,
    }
    assert captured["start_date"].isoformat() == "2026-09-01"


def test_manual_subscription_creation_returns_clear_validation_errors(monkeypatch):
    request = _FormRequest()
    request.form_data = {
        "customer_id": "",
        "product_id": "34",
        "start_date": "not-a-date",
        "quantity": "0",
    }
    rendered = {}

    async def allow(_request):
        return {"id": 7, "is_super_admin": True}, None

    async def render(_request, _user, **kwargs):
        rendered.update(kwargs)
        return SimpleNamespace(status_code=kwargs["response_status"])

    monkeypatch.setattr(admin_routes, "_require_super_admin", allow)
    monkeypatch.setattr(admin_routes, "_render_creation_form", render)

    response = asyncio.run(admin_routes.admin_create_subscription(request))

    assert response.status_code == 422
    assert set(rendered["errors"]) == {"customer_id", "start_date", "quantity"}
    assert rendered["errors"]["customer_id"].startswith("Customer:")


def test_manual_subscription_creation_requires_super_admin(monkeypatch):
    class Main:
        @staticmethod
        async def _require_administration_access(_request):
            return {"id": 3, "is_super_admin": False}, {}, None

    monkeypatch.setattr(admin_routes, "_main", lambda: Main)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_routes._require_super_admin(object()))

    assert exc.value.status_code == 403
