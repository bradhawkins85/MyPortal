from __future__ import annotations

from typing import Any

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

from app import main


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/shop/admin/product/1/delete",
        "headers": [],
    }
    return Request(scope, _dummy_receive)


@pytest.mark.anyio("asyncio")
async def test_admin_delete_shop_product_removes_image(monkeypatch):
    request = _make_request()

    current_user = {"id": 7}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    product = {"id": 1, "image_url": "uploads/shop/example.png"}
    get_mock = AsyncMock(return_value=product)
    monkeypatch.setattr(main.shop_repo, "get_product_by_id", get_mock)

    delete_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(main.shop_repo, "delete_product", delete_mock)

    deleted_paths: list[tuple[str, Any]] = []

    def fake_delete(path: str, root: Any) -> None:  # pragma: no cover - simple capture
        deleted_paths.append((path, root))

    monkeypatch.setattr(main, "delete_stored_file", fake_delete)

    response = await main.admin_delete_shop_product(request, product_id=1)

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/admin/shop"
    get_mock.assert_awaited_once_with(1)
    delete_mock.assert_awaited_once_with(1)
    assert deleted_paths == [("uploads/shop/example.png", main._private_uploads_path)]


@pytest.mark.anyio("asyncio")
async def test_admin_delete_shop_product_missing_raises(monkeypatch):
    request = _make_request()

    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=({"id": 1}, None)),
    )

    get_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(main.shop_repo, "get_product_by_id", get_mock)

    delete_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(main.shop_repo, "delete_product", delete_mock)

    with pytest.raises(HTTPException) as exc:
        await main.admin_delete_shop_product(request, product_id=99)

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    get_mock.assert_awaited_once_with(99)
    delete_mock.assert_not_called()
