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
from unittest.mock import AsyncMock, Mock


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
@pytest.mark.parametrize("include_archived", [False, True])
async def test_bulk_refresh_shop_product_descriptions_queues_without_waiting(monkeypatch, include_archived):
    from app import main as app_main
    from app.features.shop import handlers

    async def _fake_require_super_admin_page(request):
        return ({"id": 9, "is_super_admin": True}, None)

    monkeypatch.setattr(
        app_main, "_require_super_admin_page", _fake_require_super_admin_page
    )

    from app.services import background

    queue = Mock(return_value="test-task")
    monkeypatch.setattr(background, "queue_background_task", queue)
    captured_include_archived = []

    async def _list_product_description_refresh_ids(*, include_archived):
        captured_include_archived.append(include_archived)
        return [1, 2]

    monkeypatch.setattr(
        product_descriptions.shop_repo,
        "list_product_description_refresh_ids",
        _list_product_description_refresh_ids,
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
            return {"include_archived": "1"} if include_archived else {}

    response = await handlers.admin_bulk_refresh_shop_product_descriptions(_Request())

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/shop")
    queue.assert_called_once()
    from app.security.flash import pop_flash
    from http.cookies import SimpleCookie
    from types import SimpleNamespace

    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    flash = pop_flash(SimpleNamespace(cookies={"_flash": cookie["_flash"].value}))
    assert "background" in flash["message"]
    assert flash["variant"] == "success"
    if include_archived:
        assert "showArchived=1" in response.headers["location"]
    assert captured_include_archived == []
    refresh.assert_not_awaited()
    audit_record.assert_not_awaited()

    # Run the captured job separately, after the redirect has returned.
    summary = await queue.call_args.args[0]()
    assert summary["task_id"] == queue.call_args.kwargs["task_id"]
    assert captured_include_archived == [include_archived]
    assert refresh.await_count == 2
    assert [call.args[0] for call in refresh.await_args_list] == [1, 2]
    audit_record.assert_awaited_once()
    assert (
        audit_record.await_args.kwargs["action"]
        == "shop.product.description_bulk_refresh"
    )
    assert audit_record.await_args.kwargs["metadata"]["refreshed_count"] == 2
    assert audit_record.await_args.kwargs["user_id"] == 9
    assert audit_record.await_args.kwargs["source"] == "background"
    assert "request" not in audit_record.await_args.kwargs


def test_parse_ai_payload_fails_closed_on_malformed_or_nonobject_output():
    assert product_descriptions._parse_ai_payload("not valid json") == (None, [])
    assert product_descriptions._parse_ai_payload("[1, 2, 3]") == (None, [])
    assert product_descriptions._parse_ai_payload('{"unrelated":"value"}') == (None, [])


@pytest.mark.anyio("asyncio")
async def test_bulk_refresh_continues_after_failed_and_missing_products(monkeypatch):
    monkeypatch.setattr(
        product_descriptions.shop_repo, "list_product_description_refresh_ids",
        AsyncMock(return_value=[1, 2, 3]),
    )
    refresh = AsyncMock(side_effect=[RuntimeError("failed"), None, {"description": "ok"}])
    monkeypatch.setattr(product_descriptions, "improve_product_description", refresh)
    audit_record = AsyncMock()
    monkeypatch.setattr("app.services.audit.record", audit_record)

    summary = await product_descriptions.bulk_refresh_product_descriptions(
        include_archived=False, user_id=9, task_id="bulk-test"
    )

    assert [call.args[0] for call in refresh.await_args_list] == [1, 2, 3]
    assert summary["requested_count"] == 3
    assert summary["refreshed_count"] == 1
    assert summary["failed_count"] == 2
    audit_record.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_bulk_refresh_requires_admin_before_queueing(monkeypatch):
    from app.features.shop import handlers
    from app.services import background
    from types import SimpleNamespace

    redirect = object()
    main = SimpleNamespace(_require_super_admin_page=AsyncMock(return_value=(None, redirect)))
    monkeypatch.setattr(handlers, "_main", lambda: main)
    queue = Mock()
    monkeypatch.setattr(background, "queue_background_task", queue)
    request = SimpleNamespace(form=AsyncMock())

    assert await handlers.admin_bulk_refresh_shop_product_descriptions(request) is redirect
    request.form.assert_not_awaited()
    queue.assert_not_called()
