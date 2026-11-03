from pathlib import Path


def test_shop_product_details_modal_has_super_admin_refresh_form() -> None:
    template = Path("app/templates/shop/index.html").read_text()

    assert "{% if is_shop_super_admin %}" in template
    assert "data-product-refresh-form" in template
    assert "data-product-refresh-button" in template
    assert "Refresh product details" in template
    assert "{% include \"partials/csrf.html\" %}" in template


def test_shop_product_details_refresh_form_uses_admin_refresh_endpoint() -> None:
    script = Path("app/static/js/shop.js").read_text()

    assert "detailsModal.querySelector('[data-product-refresh-form]')" in script
    assert "refreshForm.action = `/shop/admin/product/${id}/refresh-description`;" in script
    assert "refreshForm.hidden = false;" in script
    assert "refreshForm.hidden = true;" in script


def test_shop_product_details_modal_links_authorised_admins_to_editor() -> None:
    template = Path("app/templates/shop/index.html").read_text()
    shop_script = Path("app/static/js/shop.js").read_text()
    admin_script = Path("app/static/js/shop_admin.js").read_text()

    assert "{% if can_edit_shop_items %}" in template
    assert "data-product-edit-link" in template
    assert 'aria-label="Edit product"' in template
    assert "editLink.href = `/admin/shop?editProduct=${encodeURIComponent(String(id))}`;" in shop_script
    assert ".get('editProduct')" in admin_script
    assert "requestedEditButton.click();" in admin_script


def test_shop_product_details_refresh_form_submits_in_background() -> None:
    script = Path("app/static/js/shop.js").read_text()

    assert "refreshForm.addEventListener('submit'" in script
    assert "event.preventDefault();" in script
    assert "Accept: 'application/json'" in script
    assert "showShopToast(message, 'success');" in script


# --- Handler behaviour -------------------------------------------------------

from typing import Any  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app import main  # noqa: E402
from app.features.shop import handlers as shop_handlers  # noqa: E402
from app.repositories import shop as shop_repo  # noqa: E402
from app.services import audit as audit_service  # noqa: E402
from app.services import background as background_tasks  # noqa: E402
from app.services import product_descriptions  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(b"host", b"portal.example.com")]
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "https",
        "server": ("portal.example.com", 443),
        "path": "/shop/admin/product/7/refresh-description",
        "query_string": b"",
        "headers": raw_headers,
    }
    return Request(scope, _dummy_receive)


@pytest.fixture
def queued(monkeypatch):
    calls: list[dict[str, Any]] = []

    def _fake_queue(factory, *, task_id=None, description=None, **_kwargs):
        calls.append({"factory": factory, "task_id": task_id, "description": description})
        return task_id or "task"

    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=({"id": 42}, None)))
    monkeypatch.setattr(shop_repo, "get_product_by_id", AsyncMock(return_value={"id": 7, "description": "Old"}))
    monkeypatch.setattr(background_tasks, "queue_background_task", _fake_queue)
    improve = AsyncMock(return_value={"description": "New", "features": []})
    monkeypatch.setattr(product_descriptions, "improve_product_description", improve)
    return calls, improve


@pytest.mark.anyio("asyncio")
async def test_refresh_queues_background_task_and_returns_json(queued) -> None:
    calls, improve = queued
    request = _make_request({"Accept": "application/json"})

    response = await shop_handlers.admin_refresh_shop_product_description(request, 7)

    assert response.status_code == 202
    assert b'"status":"queued"' in response.body
    assert len(calls) == 1
    assert calls[0]["description"] == "shop-product-description-refresh"
    improve.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_refresh_without_js_returns_to_current_product(queued) -> None:
    request = _make_request({"Referer": "https://portal.example.com/shop?category=3&product=2"})

    response = await shop_handlers.admin_refresh_shop_product_description(request, 7)

    assert response.status_code == 303
    location = response.headers["location"]
    assert not location.startswith("/admin/shop")
    assert location == "/shop?category=3&product=7"


@pytest.mark.anyio("asyncio")
async def test_refresh_ignores_foreign_referer(queued) -> None:
    request = _make_request({"Referer": "https://evil.example.net/shop?x=1"})

    response = await shop_handlers.admin_refresh_shop_product_description(request, 7)

    assert response.headers["location"] == "/shop?product=7"


@pytest.mark.anyio("asyncio")
async def test_background_refresh_runs_improvement_and_audits(queued, monkeypatch) -> None:
    calls, improve = queued
    record = AsyncMock()
    monkeypatch.setattr(audit_service, "record", record)
    request = _make_request({"Accept": "application/json"})

    await shop_handlers.admin_refresh_shop_product_description(request, 7)
    await calls[0]["factory"]()

    improve.assert_awaited_once_with(7)
    record.assert_awaited_once()
    assert record.await_args.kwargs["user_id"] == 42
    assert record.await_args.kwargs["action"] == "shop.product.description_refresh"
