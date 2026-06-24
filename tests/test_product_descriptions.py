from app.services import product_descriptions


def test_extract_features_from_key_value_lines():
    description = """
    CPU: Intel Core i7
    Memory - 16GB DDR5
    Storage | 512GB SSD
    Marketing paragraph without a separator.
    """

    features = product_descriptions.extract_features(description)

    assert features == [
        {"name": "CPU", "value": "Intel Core i7", "position": 0},
        {"name": "Memory", "value": "16GB DDR5", "position": 1},
        {"name": "Storage", "value": "512GB SSD", "position": 2},
    ]


def test_parse_ai_payload_sanitizes_html_and_features():
    html, features = product_descriptions._parse_ai_payload(
        {
            "description_html": "<h3>Specs</h3><script>alert(1)</script><p>Safe</p>",
            "features": [
                {"name": "Warranty", "value": "3 years"},
                {"name": "", "value": "ignored"},
            ],
        }
    )

    assert "script" not in (html or "").lower()
    assert "Safe" in (html or "")
    assert features == [{"name": "Warranty", "value": "3 years", "position": 0}]

import pytest
from unittest.mock import AsyncMock


@pytest.mark.anyio("asyncio")
async def test_improve_product_description_invokes_ollama_synchronously(monkeypatch):
    monkeypatch.setattr(
        product_descriptions.shop_repo,
        "get_product_by_id",
        AsyncMock(return_value={"id": 10, "description": "CPU: Fast"}),
    )
    trigger = AsyncMock(
        return_value={
            "status": "success",
            "response": '{"description_html":"<h3>Overview</h3><p>AI copy</p>","features":[{"name":"CPU","value":"Fast"}]}',
        }
    )
    monkeypatch.setattr(product_descriptions.modules_service, "trigger_module", trigger)
    update = AsyncMock(return_value={"description": "<h3>Overview</h3><p>AI copy</p>"})
    monkeypatch.setattr(product_descriptions.shop_repo, "update_product_description", update)
    replace = AsyncMock()
    monkeypatch.setattr(product_descriptions.shop_repo, "replace_product_features", replace)

    result = await product_descriptions.improve_product_description(10)

    assert result == {
        "description": "<h3>Overview</h3><p>AI copy</p>",
        "features": [{"name": "CPU", "value": "Fast", "position": 0}],
    }
    trigger.assert_awaited_once()
    assert trigger.await_args.args[0] == "ollama"
    assert trigger.await_args.kwargs["background"] is False


@pytest.mark.anyio("asyncio")
async def test_bulk_refresh_shop_product_descriptions_refreshes_active_products(monkeypatch):
    from app import main as app_main
    from app.features.shop import handlers

    async def _fake_require_super_admin_page(request):
        return ({"id": 9, "is_super_admin": True}, None)

    monkeypatch.setattr(
        app_main, "_require_super_admin_page", _fake_require_super_admin_page
    )

    captured_filters = []

    async def _list_products_summary(filters):
        captured_filters.append(filters)
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(
        product_descriptions.shop_repo, "list_products_summary", _list_products_summary
    )
    refresh = AsyncMock(
        side_effect=[
            {"description": "one", "features": []},
            {"description": "two", "features": []},
        ]
    )
    monkeypatch.setattr(product_descriptions, "improve_product_description", refresh)

    audit_record = AsyncMock()
    monkeypatch.setattr("app.services.audit.record", audit_record)

    class _Request:
        headers = {}
        client = None

        class _State:
            pass

        state = _State()

        async def form(self):
            return {}

    response = await handlers.admin_bulk_refresh_shop_product_descriptions(_Request())

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/shop")
    assert captured_filters[0].include_archived is False
    assert refresh.await_count == 2
    assert [call.args[0] for call in refresh.await_args_list] == [1, 2]
    audit_record.assert_awaited_once()
    assert (
        audit_record.await_args.kwargs["action"]
        == "shop.product.description_bulk_refresh"
    )
    assert audit_record.await_args.kwargs["metadata"]["refreshed_count"] == 2
