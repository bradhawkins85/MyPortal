"""Regression test: shop admin price changes must produce an audit_logs row.

This was the originating bug report — updating a shop product (e.g. changing
its price) in the admin UI was not generating any entry in the audit trail.
The fix wires ``audit_service.record(...)`` into ``admin_update_shop_product``;
this test pins that behaviour so it cannot regress.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app import main as app_main
from app.services import audit


class _CapturingRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_audit_log(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _FakeRequest:
    """Minimal stand-in for FastAPI Request used by the handler."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.client = None

        class _State:
            pass

        self.state = _State()

    @property
    def query_params(self):
        # Handler accesses request.query_params and KeyError-guards it; emulate
        # the empty-query case via a dict.
        class _QP:
            def get(self_inner, key, default=""):
                return default

        return _QP()


@pytest.mark.asyncio
async def test_shop_product_price_change_writes_audit_row(monkeypatch):
    """Updating a shop product's price must produce an audit_logs row whose
    diff captures the price field and excludes unchanged fields.
    """

    captured = _CapturingRepo()
    monkeypatch.setattr(audit, "audit_repo", captured)

    # Stub out auth: pretend we're already a super admin.
    async def _fake_require_super_admin_page(request):
        return ({"id": 7, "is_super_admin": True}, None)

    monkeypatch.setattr(
        app_main, "_require_super_admin_page", _fake_require_super_admin_page
    )

    existing_product = {
        "id": 42,
        "name": "Widget",
        "sku": "WID-001",
        "vendor_sku": "VND-001",
        "description": "Original",
        "price": Decimal("99.00"),
        "stock": 5,
        "vip_price": None,
        "category_id": None,
        "image_url": None,
        "archived": False,
        "subscription_category_id": None,
        "commitment_type": None,
        "payment_frequency": None,
        "buy_price": Decimal("50.00"),  # must be redacted in the audit row
    }

    updated_product = dict(existing_product)
    updated_product["price"] = Decimal("109.00")
    updated_product["description"] = "Original"  # unchanged

    # Patch the shop repo functions the handler hits on the success path.
    async def _get_product(product_id, include_archived=False):
        assert product_id == 42
        return existing_product

    async def _update_product(product_id, **kwargs):
        assert product_id == 42
        # The handler passes price as a Decimal; verify and reflect it back.
        assert kwargs["price"] == Decimal("109.00")
        return updated_product

    async def _replace_features(product_id, payload):  # pragma: no cover
        return None

    monkeypatch.setattr(app_main.shop_repo, "get_product_by_id", _get_product)
    monkeypatch.setattr(app_main.shop_repo, "update_product", _update_product)
    monkeypatch.setattr(
        app_main.shop_repo, "replace_product_features", _replace_features
    )

    # Avoid hitting the recommendation validators / SKU lookups.
    async def _validate_recs(*args, **kwargs):
        return []

    async def _resolve_sku(*args, **kwargs):
        return None

    monkeypatch.setattr(
        app_main, "_validate_recommendation_product_ids", _validate_recs
    )
    monkeypatch.setattr(
        app_main, "_resolve_related_product_id_by_sku", _resolve_sku
    )

    # Invoke the handler directly with the same form params the admin UI sends.
    response = await app_main.admin_update_shop_product(
        request=_FakeRequest(),
        product_id=42,
        name="Widget",
        sku="WID-001",
        vendor_sku="VND-001",
        description="Original",
        price="109.00",
        stock="5",
        vip_price=None,
        category_id=None,
        image=None,
        features=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
        cross_sell_sku=None,
        upsell_sku=None,
        subscription_category_id=None,
        commitment_type=None,
        payment_frequency=None,
        price_monthly_commitment=None,
        price_annual_monthly_payment=None,
        price_annual_annual_payment=None,
        scheduled_price=None,
        scheduled_vip_price=None,
        scheduled_buy_price=None,
        price_change_date=None,
    )

    # Successful update redirects back to /admin/shop.
    assert response.status_code == 303

    # Exactly one audit row must have been written for the update.
    assert len(captured.calls) == 1, captured.calls
    call = captured.calls[0]
    assert call["action"] == "shop.product.update"
    assert call["entity_type"] == "shop.product"
    assert call["entity_id"] == 42
    # user_id propagation from request context is covered by test_audit_record.
    # In this direct-handler test the request middleware isn't running, so
    # user_id falls through as None — that's expected.

    # The diff should include price and exclude unchanged fields like name/sku.
    prev = call["previous_value"] or {}
    new = call["new_value"] or {}
    assert "price" in prev and "price" in new, (prev, new)
    # _coerce normalises Decimals: 99.00 -> 99 (int) since it's an integral
    # value, 109.00 -> 109. The point is they differ and capture the change.
    assert prev["price"] != new["price"]
    assert prev["price"] in (99, 99.0, "99")
    assert new["price"] in (109, 109.0, "109")
    # Unchanged fields must not appear in the diff.
    assert "name" not in prev and "name" not in new
    assert "sku" not in prev and "sku" not in new
    assert "description" not in prev and "description" not in new

    # Sensitive fields must be redacted on both sides if they leak through
    # (buy_price is in our sensitive_extra_keys list).
    from app.services.audit_diff import REDACTED

    if "buy_price" in prev:
        assert prev["buy_price"] == REDACTED
    if "buy_price" in new:
        assert new["buy_price"] == REDACTED
