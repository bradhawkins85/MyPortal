from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

from app.features.shop import handlers
from app.repositories import shop as shop_repo
from app.services import audit as audit_service
from app.services.file_storage import copy_product_image


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _request() -> Request:
    return Request(
        {"type": "http", "method": "POST", "path": "/shop/admin/product/7/duplicate", "headers": [], "query_string": b""},
        _receive,
    )


def _form(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": "Widget (copy)",
        "sku": "W-1-COPY",
        "vendor_sku": "V-1-COPY",
        "microsoft_sku": None,
        "description": "Specs",
        "invoice_description": None,
        "product_link": None,
        "price": "19.95",
        "stock": "3",
        "vip_price": "",
        "category_id": "",
        "image": None,
        "remove_image": False,
        "features": None,
        "cross_sell_product_ids": None,
        "upsell_product_ids": None,
        "cross_sell_sku": None,
        "upsell_sku": None,
        "subscription_category_id": "",
        "commitment_type": None,
        "payment_frequency": None,
        "price_monthly_commitment": None,
        "price_annual_monthly_payment": None,
        "price_annual_annual_payment": None,
        "voice_monitor_calls_per_day": None,
        "scheduled_price": None,
        "scheduled_vip_price": None,
        "scheduled_buy_price": None,
        "price_change_date": None,
        "freight_fields": "1",
        "item_size": "",
        "weight": "1.5",
        "length": "",
        "width": "",
        "height": "",
    }
    values.update(overrides)
    return values


@pytest.fixture
def harness(monkeypatch, tmp_path):
    main_stub = SimpleNamespace(
        _require_super_admin_page=AsyncMock(return_value=({"id": 1}, None)),
        _private_uploads_path=tmp_path,
        log_info=lambda *a, **k: None,
        log_error=lambda *a, **k: None,
    )
    monkeypatch.setattr(handlers, "_main", lambda: main_stub)

    source = {
        "id": 7,
        "name": "Widget",
        "sku": "W-1",
        "vendor_sku": "V-1",
        "image_url": None,
        "buy_price": Decimal("12.00"),
        "subscription_category_id": None,
    }
    created = {"id": 99, "name": "Widget (copy)", "subscription_category_id": None, "image_url": None}

    async def _get(product_id: int, **_: Any):
        return {7: source, 99: created}.get(product_id)

    mocks = SimpleNamespace(
        source=source,
        create=AsyncMock(return_value={"id": 99}),
        delete=AsyncMock(return_value="deleted"),
        buy_price=AsyncMock(),
        exclusions=AsyncMock(),
        featured=AsyncMock(),
        audit=AsyncMock(),
        update=AsyncMock(
            return_value=handlers.RedirectResponse(url="/admin/shop", status_code=status.HTTP_303_SEE_OTHER)
        ),
    )
    monkeypatch.setattr(shop_repo, "get_product_by_id", _get)
    monkeypatch.setattr(shop_repo, "create_product", mocks.create)
    monkeypatch.setattr(shop_repo, "delete_product", mocks.delete)
    monkeypatch.setattr(shop_repo, "set_product_buy_price", mocks.buy_price)
    monkeypatch.setattr(
        shop_repo,
        "list_product_restrictions_for_product",
        AsyncMock(return_value=[{"product_id": 7, "company_id": 3, "company_name": "Acme"}]),
    )
    monkeypatch.setattr(shop_repo, "replace_product_exclusions", mocks.exclusions)
    monkeypatch.setattr(
        shop_repo,
        "list_product_featured_companies_for_product",
        AsyncMock(return_value=[{"product_id": 7, "company_id": 5, "company_name": "Beta"}]),
    )
    monkeypatch.setattr(shop_repo, "replace_product_featured_companies", mocks.featured)
    monkeypatch.setattr(audit_service, "record_create", mocks.audit)
    monkeypatch.setattr(handlers, "admin_update_shop_product", mocks.update)
    return mocks


@pytest.mark.anyio("asyncio")
async def test_duplicate_creates_new_product_and_copies_hidden_settings(harness):
    response = await handlers.admin_duplicate_shop_product(_request(), product_id=7, **_form())

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/admin/shop"
    create_kwargs = harness.create.await_args.kwargs
    assert create_kwargs["sku"] == "W-1-COPY"
    assert create_kwargs["vendor_sku"] == "V-1-COPY"

    update_kwargs = harness.update.await_args.kwargs
    assert update_kwargs["product_id"] == 99
    assert update_kwargs["price"] == "19.95"
    assert update_kwargs["weight"] == "1.5"
    assert update_kwargs["remove_inbound_cross_sell_product_ids"] is None

    harness.buy_price.assert_awaited_once_with(99, Decimal("12.00"))
    harness.exclusions.assert_awaited_once_with(99, [3])
    harness.featured.assert_awaited_once_with(99, [5])
    harness.delete.assert_not_awaited()
    audit_kwargs = harness.audit.await_args.kwargs
    assert audit_kwargs["action"] == "shop.product.duplicate"
    assert audit_kwargs["metadata"] == {"source_product_id": 7}


@pytest.mark.anyio("asyncio")
async def test_duplicate_removes_draft_when_form_is_invalid(harness):
    harness.update.side_effect = HTTPException(status_code=400, detail="Price must be a valid number")

    with pytest.raises(HTTPException):
        await handlers.admin_duplicate_shop_product(_request(), product_id=7, **_form(price="abc"))

    harness.delete.assert_awaited_once_with(99)
    harness.exclusions.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_duplicate_reports_conflicting_sku(harness):
    import aiomysql

    harness.create.side_effect = aiomysql.IntegrityError(1062, "Duplicate entry")

    with pytest.raises(HTTPException) as exc:
        await handlers.admin_duplicate_shop_product(_request(), product_id=7, **_form(sku="W-1"))

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "own SKU" in exc.value.detail
    harness.update.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_duplicate_missing_source_returns_404(harness):
    with pytest.raises(HTTPException) as exc:
        await handlers.admin_duplicate_shop_product(_request(), product_id=12345, **_form())
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    harness.create.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_duplicate_copies_stored_image_to_new_file(harness, tmp_path):
    shop_dir = tmp_path / "shop"
    shop_dir.mkdir()
    (shop_dir / "abc.png").write_bytes(b"png-bytes")
    harness.source["image_url"] = "/uploads/shop/abc.png"

    await handlers.admin_duplicate_shop_product(_request(), product_id=7, **_form())

    copied_url = harness.create.await_args.kwargs["image_url"]
    assert copied_url.startswith("/uploads/shop/") and copied_url != "/uploads/shop/abc.png"
    assert (shop_dir / Path(copied_url).name).read_bytes() == b"png-bytes"


@pytest.mark.anyio("asyncio")
async def test_duplicate_skips_image_when_removed(harness, tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "abc.png").write_bytes(b"x")
    harness.source["image_url"] = "/uploads/shop/abc.png"

    await handlers.admin_duplicate_shop_product(_request(), product_id=7, **_form(remove_image=True))

    assert harness.create.await_args.kwargs["image_url"] is None
    assert len(list((tmp_path / "shop").iterdir())) == 1


def test_copy_product_image_rejects_traversal_and_keeps_external_urls(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "secret.png").write_bytes(b"x")
    assert copy_product_image("/uploads/shop/../secret.png", tmp_path) == (None, None)
    assert copy_product_image("/uploads/shop/missing.png", tmp_path) == (None, None)
    assert copy_product_image("https://cdn.example.com/a.png", tmp_path) == (
        "https://cdn.example.com/a.png",
        None,
    )
    assert copy_product_image(None, tmp_path) == (None, None)


@pytest.mark.anyio("asyncio")
async def test_duplicate_runs_through_real_update_handler(harness, monkeypatch):
    monkeypatch.undo()
    main_stub = SimpleNamespace(
        _require_super_admin_page=AsyncMock(return_value=({"id": 1}, None)),
        _private_uploads_path=Path("/nonexistent"),
        log_info=lambda *a, **k: None,
        log_error=lambda *a, **k: None,
    )
    monkeypatch.setattr(handlers, "_main", lambda: main_stub)
    source = {"id": 7, "name": "Widget", "image_url": None, "buy_price": None}
    created = {"id": 99, "name": "Widget (copy)", "image_url": None, "subscription_category_id": None}

    async def _get(product_id: int, **_: Any):
        return {7: source, 99: created}.get(product_id)

    update_repo = AsyncMock(return_value=created)
    monkeypatch.setattr(shop_repo, "get_product_by_id", _get)
    monkeypatch.setattr(shop_repo, "create_product", AsyncMock(return_value={"id": 99}))
    monkeypatch.setattr(shop_repo, "update_product", update_repo)
    monkeypatch.setattr(shop_repo, "replace_product_features", AsyncMock())
    monkeypatch.setattr(shop_repo, "remove_inbound_product_recommendations", AsyncMock())
    monkeypatch.setattr(shop_repo, "list_product_restrictions_for_product", AsyncMock(return_value=[]))
    monkeypatch.setattr(shop_repo, "list_product_featured_companies_for_product", AsyncMock(return_value=[]))
    monkeypatch.setattr(audit_service, "record", AsyncMock())
    monkeypatch.setattr(audit_service, "record_create", AsyncMock())

    response = await handlers.admin_duplicate_shop_product(_request(), product_id=7, **_form())

    assert response.status_code == status.HTTP_303_SEE_OTHER
    args = update_repo.await_args
    assert args.args[0] == 99
    assert args.kwargs["sku"] == "W-1-COPY"
    assert args.kwargs["price"] == Decimal("19.95")
    assert args.kwargs["stock"] == 3
    assert args.kwargs["freight"]["weight"] == Decimal("1.5")
