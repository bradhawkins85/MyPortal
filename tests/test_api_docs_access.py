"""The OpenAPI schema and Swagger UI are restricted to staff accounts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

import app.main as main_module


def _request():
    return SimpleNamespace(state=SimpleNamespace(), url=SimpleNamespace(path="/docs"))


def _as_user(monkeypatch, user, *, technician: bool):
    async def fake_load_session(request, *, allow_inactive=False):
        return object()

    async def fake_require_user(request):
        return dict(user), None

    async def fake_is_technician(user, request=None):
        return bool(user.get("is_super_admin")) or technician

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    monkeypatch.setattr(main_module, "_is_helpdesk_technician", fake_is_technician)
    monkeypatch.setattr(main_module.app, "openapi", lambda: {"openapi": "3.1.0"})


_HANDLERS = [main_module.authenticated_openapi_schema, main_module.authenticated_swagger_ui]


@pytest.mark.parametrize("handler", _HANDLERS)
def test_customer_cannot_view_api_docs(monkeypatch, handler):
    _as_user(monkeypatch, {"id": 5, "is_super_admin": False}, technician=False)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(handler(_request()))
    assert excinfo.value.status_code == 403


@pytest.mark.parametrize("handler", _HANDLERS)
@pytest.mark.parametrize(
    "user,technician",
    [({"id": 1, "is_super_admin": True}, False), ({"id": 2, "is_super_admin": False}, True)],
)
def test_staff_can_view_api_docs(monkeypatch, handler, user, technician):
    _as_user(monkeypatch, user, technician=technician)
    response = asyncio.run(handler(_request()))
    assert response.status_code == 200


def test_anonymous_api_docs_request_is_rejected(monkeypatch):
    async def no_session(request, *, allow_inactive=False):
        return None

    async def redirect_to_login(request):
        return None, RedirectResponse(url="/login", status_code=303)

    monkeypatch.setattr(main_module.session_manager, "load_session", no_session)
    monkeypatch.setattr(main_module, "_require_authenticated_user", redirect_to_login)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main_module.authenticated_openapi_schema(_request()))
    assert excinfo.value.status_code == 401
    response = asyncio.run(main_module.authenticated_swagger_ui(_request()))
    assert response.status_code == 303
