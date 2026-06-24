from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/tray/install-tokens") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_tray_install_tokens_hides_revoked_by_default(monkeypatch):
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(
        tray_repo,
        "list_install_tokens",
        AsyncMock(
            return_value=[
                {"id": 1, "label": "Active", "revoked_at": None},
                {"id": 2, "label": "Revoked", "revoked_at": "2026-06-01T00:00:00"},
            ]
        ),
    )
    monkeypatch.setattr(
        companies_repo, "list_companies", AsyncMock(return_value=[])
    )

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request, user, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    await main.admin_tray_install_tokens_page(_make_request())

    assert [token["id"] for token in captured["extra"]["tokens"]] == [1]
    assert captured["extra"]["show_revoked"] is False
    assert captured["extra"]["hidden_revoked_count"] == 1


@pytest.mark.anyio
async def test_tray_install_tokens_can_show_revoked(monkeypatch):
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(
        tray_repo,
        "list_install_tokens",
        AsyncMock(
            return_value=[
                {"id": 1, "label": "Active", "revoked_at": None},
                {"id": 2, "label": "Revoked", "revoked_at": "2026-06-01T00:00:00"},
            ]
        ),
    )
    monkeypatch.setattr(
        companies_repo, "list_companies", AsyncMock(return_value=[])
    )

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request, user, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    await main.admin_tray_install_tokens_page(_make_request(), show_revoked=True)

    assert [token["id"] for token in captured["extra"]["tokens"]] == [1, 2]
    assert captured["extra"]["show_revoked"] is True
    assert captured["extra"]["hidden_revoked_count"] == 0


@pytest.mark.anyio
async def test_tray_install_tokens_snippet_uses_https_when_request_is_http(monkeypatch):
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(tray_repo, "list_install_tokens", AsyncMock(return_value=[]))
    monkeypatch.setattr(companies_repo, "list_companies", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.settings, "portal_url", None)

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request, user, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    await main.admin_tray_install_tokens_page(_make_request(), new_token="abc")

    assert captured["extra"]["portal_url"] == "https://testserver"


@pytest.mark.anyio
async def test_tray_install_tokens_snippet_prefers_configured_portal_url(monkeypatch):
    import app.repositories.companies as companies_repo
    import app.repositories.tray as tray_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(tray_repo, "list_install_tokens", AsyncMock(return_value=[]))
    monkeypatch.setattr(companies_repo, "list_companies", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        main.settings, "portal_url", AnyHttpUrl("https://portal.example.com")
    )

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request, user, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    await main.admin_tray_install_tokens_page(_make_request(), new_token="abc")

    assert captured["extra"]["portal_url"] == "https://portal.example.com"


@pytest.mark.anyio
async def test_tray_bulk_purge_revoked_install_tokens_calls_repo(monkeypatch):
    import app.repositories.tray as tray_repo

    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    delete_mock = AsyncMock(return_value=2)
    monkeypatch.setattr(tray_repo, "delete_revoked_install_tokens", delete_mock)

    response = await main.admin_tray_bulk_purge_revoked_install_tokens(
        _make_request("/admin/tray/install-tokens/bulk-purge-revoked")
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/tray/install-tokens"
    delete_mock.assert_awaited_once()
