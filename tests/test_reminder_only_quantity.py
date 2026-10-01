"""Regression tests for direct external subscription quantity adjustments."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.features.subscriptions import portal_routes


class _Request:
    async def json(self):
        return {"quantity": 12}


def test_subscriptions_page_exposes_direct_quantity_adjustment():
    template = Path("app/templates/subscriptions/index.html").read_text(encoding="utf-8")

    assert "data-subscription-adjust" in template
    assert 'id="quantity-dialog"' in template
    assert "/quantity" in template


def test_reminder_only_quantity_is_updated_directly(monkeypatch):
    updated = {}

    async def context(_request):
        return {"id": 7, "is_super_admin": True}, {}, {}, 4, None

    async def subscription(_subscription_id):
        return {"id": "sub-1", "customer_id": 4, "quantity": 5, "reminder_only": True}

    async def update(subscription_id, **values):
        updated.update(subscription_id=subscription_id, **values)

    monkeypatch.setattr(portal_routes, "_load_subscription_context", context)
    monkeypatch.setattr(
        portal_routes.subscriptions_repo, "get_subscription", subscription
    )
    monkeypatch.setattr(portal_routes.subscriptions_repo, "update_subscription", update)

    response = asyncio.run(
        portal_routes.update_reminder_only_quantity(_Request(), "sub-1")
    )

    assert response.status_code == 200
    assert updated == {"subscription_id": "sub-1", "quantity": 12}


def test_billed_subscription_cannot_bypass_change_workflow(monkeypatch):
    async def context(_request):
        return {"id": 7, "is_super_admin": True}, {}, {}, 4, None

    async def subscription(_subscription_id):
        return {"id": "sub-1", "customer_id": 4, "quantity": 5, "reminder_only": False}

    monkeypatch.setattr(portal_routes, "_load_subscription_context", context)
    monkeypatch.setattr(
        portal_routes.subscriptions_repo, "get_subscription", subscription
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(portal_routes.update_reminder_only_quantity(_Request(), "sub-1"))

    assert exc.value.status_code == 409
