"""Regression tests for company-scoped third-party subscription creation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.features.subscriptions import portal_routes


class _FormRequest:
    async def form(self, **_kwargs):
        return self.form_data


def test_portal_exposes_external_subscription_creation_fields():
    index = Path("app/templates/subscriptions/index.html").read_text(encoding="utf-8")
    template = Path("app/templates/subscriptions/create_external.html").read_text(
        encoding="utf-8"
    )

    assert 'href="/subscriptions/create"' in index
    assert "Externally billed / third-party" in template
    for field in (
        "vendor",
        "external_name",
        "external_sku",
        "billing_frequency",
        "start_date",
        "end_date",
        "quantity",
    ):
        assert f'name="{field}"' in template
    assert 'name="product_id"' not in template
    assert 'include "partials/csrf.html"' in template


def test_manager_creates_company_scoped_reminder_only_subscription(monkeypatch):
    request = _FormRequest()
    request.form_data = {
        "vendor": "Acme Vendor",
        "external_name": "Security Suite",
        "external_sku": "SEC-100",
        "billing_frequency": "annual",
        "start_date": "2026-09-01",
        "end_date": "2027-09-01",
        "quantity": "8",
        "auto_renew": "on",
    }
    user = {"id": 7, "is_super_admin": False}
    membership = {"can_manage_licenses": True, "can_access_cart": True}
    captured = {}

    async def context(_request):
        return user, membership, {"id": 12, "name": "Example Co"}, 12, None

    async def create(payload, current_user):
        captured.update(payload.model_dump())
        assert current_user is user
        return SimpleNamespace(id="external-123")

    monkeypatch.setattr(portal_routes, "_load_subscription_context", context)
    monkeypatch.setattr(
        portal_routes, "create_existing_subscription_record", create
    )

    response = asyncio.run(portal_routes.create_external_subscription(request))

    assert response.status_code == 303
    assert response.headers["location"] == "/subscriptions?created=1"
    assert captured["customer_id"] == 12
    assert captured["product_id"] is None
    assert captured["reminder_only"] is True
    assert captured["vendor"] == "Acme Vendor"
    assert captured["external_name"] == "Security Suite"
    assert captured["external_sku"] == "SEC-100"
    assert captured["billing_frequency"] == "annual"
    assert captured["quantity"] == 8


def test_read_only_user_cannot_open_external_subscription_form(monkeypatch):
    async def context(_request):
        return (
            {"id": 7, "is_super_admin": False},
            {"can_manage_licenses": False, "can_access_cart": False},
            {"id": 12},
            12,
            None,
        )

    class Main:
        @staticmethod
        def _membership_menu_can(*_args, **_kwargs):
            return False

    monkeypatch.setattr(portal_routes, "_load_subscription_context", context)
    monkeypatch.setattr(portal_routes, "_main", lambda: Main)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(portal_routes.create_external_subscription_page(object()))

    assert exc.value.status_code == 403
