from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/automations/1/delete") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
    }
    return Request(scope, _dummy_receive)


@pytest.mark.anyio("asyncio")
async def test_admin_delete_automation_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _make_request()

    current_user = {"id": 5}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    get_mock = AsyncMock(return_value={"id": 1, "name": "Nightly cleanup"})
    delete_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(main.automation_repo, "get_automation", get_mock)
    monkeypatch.setattr(main.automation_repo, "delete_automation", delete_mock)

    log_calls: list[dict[str, Any]] = []

    def fake_log_info(message: str, **kwargs: Any) -> None:  # pragma: no cover - capture side effect
        log_calls.append({"message": message, "kwargs": kwargs})

    monkeypatch.setattr(main, "log_info", fake_log_info)

    response = await main.admin_delete_automation(1, request)

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/admin/automations?success=Automation%201%20deleted."
    get_mock.assert_awaited_once_with(1)
    delete_mock.assert_awaited_once_with(1)
    assert log_calls == [
        {
            "message": "Automation deleted",
            "kwargs": {"automation_id": 1, "deleted_by": current_user["id"]},
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_admin_delete_automation_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _make_request("/admin/automations/99/delete")

    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=({"id": 9}, None)),
    )

    get_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(main.automation_repo, "get_automation", get_mock)

    with pytest.raises(HTTPException) as exc:
        await main.admin_delete_automation(99, request)

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc.value.detail == "Automation not found"
    get_mock.assert_awaited_once_with(99)


@pytest.mark.anyio("asyncio")
async def test_admin_delete_automation_failure_renders_error(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _make_request("/admin/automations/7/delete")

    user = {"id": 42}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(user, None)),
    )

    automation = {"id": 7, "name": "Weekly sync"}
    get_mock = AsyncMock(return_value=automation)
    delete_mock = AsyncMock(side_effect=RuntimeError("db error"))
    monkeypatch.setattr(main.automation_repo, "get_automation", get_mock)
    monkeypatch.setattr(main.automation_repo, "delete_automation", delete_mock)

    error_calls: list[dict[str, Any]] = []

    def fake_log_error(message: str, **kwargs: Any) -> None:  # pragma: no cover - capture side effect
        error_calls.append({"message": message, "kwargs": kwargs})

    monkeypatch.setattr(main, "log_error", fake_log_error)

    failure_response = HTMLResponse("error", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    render_mock = AsyncMock(return_value=failure_response)
    monkeypatch.setattr(main, "_render_automations_dashboard", render_mock)

    response = await main.admin_delete_automation(7, request)

    assert response is failure_response
    get_mock.assert_awaited_once_with(7)
    delete_mock.assert_awaited_once_with(7)
    render_mock.assert_awaited_once()
    assert error_calls == [
        {
            "message": "Failed to delete automation",
            "kwargs": {"automation_id": 7, "error": "db error"},
        }
    ]
