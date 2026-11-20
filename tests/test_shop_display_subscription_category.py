"""One-time products can be listed with the subscriptions in a category."""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

from app import main
from app.features.shop import handlers as shop_handlers
from app.repositories import shop as shop_repo
from app.repositories import subscription_categories as subscription_categories_repo

MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "569_shop_product_display_subscription_category.sql"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str) -> Request:
    return Request(
        {"type": "http", "method": "POST", "path": path, "headers": []},
        _dummy_receive,
    )


async def _call_form_handler(handler, request: Request, **kwargs):
    """Call a form handler directly, giving omitted form fields their defaults."""
    for name, param in inspect.signature(handler).parameters.items():
        if name == "request" or name in kwargs:
            continue
        default = getattr(param.default, "default", param.default)
        if default is not inspect.Parameter.empty and default is not ...:
            kwargs[name] = default
    return await handler(request, **kwargs)


def test_migration_adds_display_column_idempotently():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "-- phase: expand" in sql
    assert (
        "ADD COLUMN IF NOT EXISTS display_subscription_category_id INT NULL" in sql
    )
    assert "CREATE INDEX IF NOT EXISTS idx_shop_products_display_subscription_category" in sql


@pytest.mark.parametrize(
    ("product", "expected"),
    [
        ({"subscription_category_id": 2, "display_subscription_category_id": None}, 2),
        ({"subscription_category_id": None, "display_subscription_category_id": 3}, 3),
        # A subscription keeps its own category even if a stale display one exists.
        ({"subscription_category_id": 2, "display_subscription_category_id": 3}, 2),
        ({"subscription_category_id": None, "display_subscription_category_id": None}, None),
        ({}, None),
    ],
)
def test_subscription_group_id(product, expected):
    assert shop_handlers._subscription_group_id(product) == expected


def _patch_admin(monkeypatch, *, category: dict[str, Any] | None):
    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 42}, None))
    )
    monkeypatch.setattr(
        subscription_categories_repo,
        "get_category",
        AsyncMock(return_value=category),
    )


async def _create(monkeypatch, **overrides):
    create_mock = AsyncMock(return_value={"id": 1, "sku": "SKU-1", "vendor_sku": "VENDOR-1"})
    monkeypatch.setattr(shop_repo, "create_product", create_mock)
    monkeypatch.setattr(
        shop_handlers, "_default_subscription_shop_category_id", AsyncMock(return_value=None)
    )
    kwargs: dict[str, Any] = dict(
        name="AI Receptionist capacity pack",
        sku="SKU-1",
        vendor_sku="VENDOR-1",
        description=None,
        product_link=None,
        price="49.00",
        stock="100",
        vip_price="",
        category_id="",
        image=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
        subscription_category_id="",
        commitment_type=None,
        payment_frequency=None,
        price_monthly_commitment=None,
        price_annual_monthly_payment=None,
        price_annual_annual_payment=None,
    )
    kwargs.update(overrides)
    response = await _call_form_handler(
        shop_handlers.admin_create_shop_product, _make_request("/shop/admin/product"), **kwargs
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    return create_mock.await_args.kwargs


@pytest.mark.anyio("asyncio")
async def test_create_one_time_product_with_display_category(monkeypatch):
    _patch_admin(monkeypatch, category={"id": 7, "name": "Yeastar"})

    call_kwargs = await _create(monkeypatch, display_subscription_category_id="7")

    assert call_kwargs["subscription_category_id"] is None
    assert call_kwargs["display_subscription_category_id"] == 7


@pytest.mark.anyio("asyncio")
async def test_create_subscription_ignores_display_category(monkeypatch):
    _patch_admin(monkeypatch, category={"id": 7, "name": "Yeastar"})

    call_kwargs = await _create(
        monkeypatch,
        subscription_category_id="7",
        price_annual_annual_payment="499.00",
        display_subscription_category_id="7",
    )

    assert call_kwargs["subscription_category_id"] == 7
    assert call_kwargs["display_subscription_category_id"] is None


@pytest.mark.anyio("asyncio")
async def test_create_rejects_unknown_display_category(monkeypatch):
    _patch_admin(monkeypatch, category=None)

    with pytest.raises(HTTPException) as excinfo:
        await _create(monkeypatch, display_subscription_category_id="99")

    assert excinfo.value.status_code == status.HTTP_400_BAD_REQUEST


async def _update(monkeypatch, **overrides):
    monkeypatch.setattr(
        shop_repo,
        "get_product_by_id",
        AsyncMock(return_value={"id": 1, "image_url": None}),
    )
    update_mock = AsyncMock(return_value={"image_url": None})
    monkeypatch.setattr(shop_repo, "update_product", update_mock)
    monkeypatch.setattr(shop_repo, "replace_product_features", AsyncMock())
    monkeypatch.setattr(shop_repo, "remove_inbound_product_recommendations", AsyncMock())
    monkeypatch.setattr(shop_handlers, "_resolve_related_product_id_by_sku", AsyncMock(return_value=None))
    kwargs: dict[str, Any] = dict(
        product_id=1,
        name="AI Receptionist capacity pack",
        sku="SKU-1",
        vendor_sku="VENDOR-1",
        description=None,
        invoice_description=None,
        product_link=None,
        price="49.00",
        stock="5",
        vip_price="",
        category_id="",
        image=None,
        cross_sell_sku=None,
        upsell_sku=None,
        subscription_category_id="",
        features="[]",
    )
    kwargs.update(overrides)
    response = await _call_form_handler(
        shop_handlers.admin_update_shop_product, _make_request("/shop/admin/product/1"), **kwargs
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    return update_mock.await_args.kwargs


@pytest.mark.anyio("asyncio")
async def test_update_sets_and_clears_display_category(monkeypatch):
    _patch_admin(monkeypatch, category={"id": 7, "name": "Yeastar"})

    assert (
        await _update(monkeypatch, display_subscription_category_id="7")
    )["display_subscription_category_id"] == 7
    assert (
        await _update(monkeypatch, display_subscription_category_id="")
    )["display_subscription_category_id"] is None


@pytest.mark.anyio("asyncio")
async def test_update_without_field_keeps_stored_display_category(monkeypatch):
    _patch_admin(monkeypatch, category={"id": 7, "name": "Yeastar"})

    call_kwargs = await _update(monkeypatch)

    assert call_kwargs["display_subscription_category_id"] is shop_repo.UNSET


@pytest.mark.anyio("asyncio")
async def test_deleting_category_detaches_one_time_products(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(subscription_categories_repo.db, "execute", execute)

    await subscription_categories_repo.delete_category(7)

    statements = [call.args[0] for call in execute.await_args_list]
    assert "SET display_subscription_category_id = NULL" in statements[0]
    assert execute.await_args_list[0].args[1] == (7,)
    assert statements[1].startswith("DELETE FROM subscription_categories")


def test_admin_forms_offer_display_category_for_one_time_products():
    edit_template = Path("app/templates/admin/shop.html").read_text(encoding="utf-8")
    create_template = Path("app/templates/admin/shop_product_create.html").read_text(
        encoding="utf-8"
    )
    script = Path("app/static/js/shop_admin.js").read_text(encoding="utf-8")

    for template in (edit_template, create_template):
        assert 'name="display_subscription_category_id"' in template
        assert "Show with subscriptions in" in template
    assert "product.display_subscription_category_id" in script


def test_shop_card_marks_one_time_items_in_subscription_view():
    template = Path("app/templates/shop/index.html").read_text(encoding="utf-8")

    assert "show_subscriptions and not is_subscription and not product.is_package" in template
    assert "One-time purchase" in template


class _FakeCursor:
    def __init__(self, calls: list[tuple[str, tuple[Any, ...]]]):
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        self._calls.append((sql, tuple(params)))


class _FakeConnection:
    def __init__(self, calls):
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, *_args):
        return _FakeCursor(self._calls)


async def _run_repo_update(monkeypatch, **overrides) -> tuple[str, tuple[Any, ...]]:
    calls: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setattr(shop_repo.db, "acquire", lambda: _FakeConnection(calls))
    monkeypatch.setattr(shop_repo, "replace_product_recommendations", AsyncMock())
    monkeypatch.setattr(shop_repo, "get_product_by_id", AsyncMock(return_value={"id": 1}))
    kwargs: dict[str, Any] = dict(
        name="Pack",
        sku="SKU-1",
        vendor_sku="VENDOR-1",
        description=None,
        price=0,
        stock=1,
        vip_price=None,
        category_id=None,
        image_url=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
    )
    kwargs.update(overrides)
    await shop_repo.update_product(1, **kwargs)
    return calls[0]


@pytest.mark.anyio("asyncio")
async def test_repo_update_leaves_display_category_when_not_supplied(monkeypatch):
    sql, _params = await _run_repo_update(monkeypatch)

    assert "display_subscription_category_id" not in sql


@pytest.mark.anyio("asyncio")
async def test_repo_update_writes_supplied_display_category(monkeypatch):
    sql, params = await _run_repo_update(monkeypatch, display_subscription_category_id=7)

    assert "display_subscription_category_id = %s" in sql
    assert params[0] == 7


@pytest.mark.anyio("asyncio")
async def test_repo_update_clears_display_category_for_subscriptions(monkeypatch):
    sql, params = await _run_repo_update(
        monkeypatch, subscription_category_id=7, display_subscription_category_id=7
    )

    assert "display_subscription_category_id = %s" in sql
    assert params[0] is None
