from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import main


async def _dummy_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/shop") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.mark.anyio("asyncio")
async def test_shop_page_pagination_counts_only_visible_products(monkeypatch):
    request = _make_request("/shop")
    user = {"id": 9, "company_id": 5}
    membership = {"can_access_shop": 1}
    company = {"id": 5, "is_vip": 0}

    monkeypatch.setattr(
        main,
        "_load_company_section_context",
        AsyncMock(return_value=(user, membership, company, 5, None)),
    )
    monkeypatch.setattr(main.shop_repo, "list_categories", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        main.shop_repo,
        "get_category_ids_with_available_products",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        main.shop_repo,
        "list_products_summary",
        AsyncMock(
            return_value=[
                {"id": 1, "name": "Visible", "price": "10.00", "vip_price": None},
                {"id": 2, "name": "Below DBP", "price": "12.00", "vip_price": None},
                {"id": 3, "name": "No Price", "price": "0", "vip_price": None},
            ]
        ),
    )
    monkeypatch.setattr(
        main.shop_service,
        "is_price_below_dbp_threshold",
        lambda product, is_vip=False: product.get("id") == 2,
    )
    monkeypatch.setattr(
        main.subscriptions_repo,
        "get_active_subscription_product_ids",
        AsyncMock(return_value=[]),
    )

    captured_extra: dict[str, object] = {}

    async def mock_render_template(template_name, request, user, extra=None):
        captured_extra.update(extra or {})
        return "ok"

    monkeypatch.setattr(main, "_render_template", mock_render_template)

    response = await main.shop_page(request, page=1, page_size=2)

    assert response == "ok"
    assert captured_extra["total_count"] == 1
    assert captured_extra["total_pages"] == 1
    assert [product["id"] for product in captured_extra["products"]] == [1]
