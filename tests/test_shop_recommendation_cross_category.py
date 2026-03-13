"""Tests that upsell and cross-sell products are no longer restricted to the same category."""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status

from app import main


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_validate_recommendation_allows_different_category(monkeypatch):
    """Products from a different category should be accepted as recommendations."""
    candidates = [
        {
            "id": 99,
            "name": "Other Category Product",
            "sku": "OTHER-1",
            "category_id": 5,  # different from product being edited (category 3)
            "archived": 0,
        }
    ]
    monkeypatch.setattr(
        main.shop_repo,
        "list_products_by_ids",
        AsyncMock(return_value=candidates),
    )

    result = await main._validate_recommendation_product_ids(
        [99],
        field_label="Cross-sell",
        disallow_product_id=1,
    )
    assert result == [99]


@pytest.mark.anyio("asyncio")
async def test_validate_recommendation_allows_no_category(monkeypatch):
    """Recommendations should be accepted even when the base product has no category."""
    candidates = [
        {
            "id": 10,
            "name": "Uncategorised Product",
            "sku": "UNCAT-1",
            "category_id": None,
            "archived": 0,
        }
    ]
    monkeypatch.setattr(
        main.shop_repo,
        "list_products_by_ids",
        AsyncMock(return_value=candidates),
    )

    result = await main._validate_recommendation_product_ids(
        [10],
        field_label="Up-sell",
        disallow_product_id=1,
    )
    assert result == [10]


@pytest.mark.anyio("asyncio")
async def test_validate_recommendation_rejects_archived(monkeypatch):
    """Archived products must still be rejected."""
    candidates = [
        {
            "id": 7,
            "name": "Archived Product",
            "sku": "ARCH-1",
            "category_id": 3,
            "archived": 1,
        }
    ]
    monkeypatch.setattr(
        main.shop_repo,
        "list_products_by_ids",
        AsyncMock(return_value=candidates),
    )

    with pytest.raises(HTTPException) as exc_info:
        await main._validate_recommendation_product_ids(
            [7],
            field_label="Cross-sell",
            disallow_product_id=1,
        )
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "archived" in exc_info.value.detail.lower()


@pytest.mark.anyio("asyncio")
async def test_validate_recommendation_rejects_self_reference(monkeypatch):
    """A product cannot be its own recommendation."""
    candidates = [
        {
            "id": 1,
            "name": "Self",
            "sku": "SELF-1",
            "category_id": 3,
            "archived": 0,
        }
    ]
    monkeypatch.setattr(
        main.shop_repo,
        "list_products_by_ids",
        AsyncMock(return_value=candidates),
    )

    with pytest.raises(HTTPException) as exc_info:
        await main._validate_recommendation_product_ids(
            [1],
            field_label="Up-sell",
            disallow_product_id=1,
        )
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio("asyncio")
async def test_validate_recommendation_empty_returns_empty(monkeypatch):
    """Empty input should return an empty list without hitting the database."""
    fetch_mock = AsyncMock()
    monkeypatch.setattr(main.shop_repo, "list_products_by_ids", fetch_mock)

    result = await main._validate_recommendation_product_ids(
        [],
        field_label="Cross-sell",
    )
    assert result == []
    fetch_mock.assert_not_called()
