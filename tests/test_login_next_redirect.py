"""Post-login redirect (``?next=``) handling."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main as main_module
from app.features.staff import handlers as staff_handlers
from app.main import app


def _request(path: str, query: str = "", method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query.encode(),
            "headers": [],
            "scheme": "https",
            "server": ("portal.example.com", 443),
        }
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        ("/staff", "/staff"),
        ("/staff/addstaff", "/staff/addstaff"),
        ("/tickets?status=open", "/tickets?status=open"),
        ("", None),
        (None, None),
        ("https://evil.example.com/", None),
        ("//evil.example.com/", None),
        ("/%2F%2Fevil.example.com/", None),
        ("/%5C%5Cevil.example.com/", None),
        ("/\\evil.example.com", None),
        ("/%E0%A4%A", None),
        ("/%2", None),
        ("staff", None),
        ("/login", None),
        ("/login?next=/staff", None),
        ("/logout", None),
        ("/staff\r\nSet-Cookie: x=1", None),
    ],
)
def test_safe_next_path(value, expected):
    assert main_module._safe_next_path(value) == expected


def test_login_redirect_includes_next_for_get():
    response = main_module._login_redirect(_request("/staff", "department=IT"))
    assert response.headers["location"] == "/login?next=%2Fstaff%3Fdepartment%3DIT"


def test_login_redirect_omits_next_for_non_get_and_root():
    assert main_module._login_redirect(_request("/staff", method="POST")).headers["location"] == "/login"
    assert main_module._login_redirect(_request("/")).headers["location"] == "/login"


def test_require_authenticated_user_redirects_with_next(monkeypatch):
    async def no_session(request):
        return None

    monkeypatch.setattr(main_module.session_manager, "load_session", no_session)
    user, redirect = asyncio.run(main_module._require_authenticated_user(_request("/staff/addstaff")))
    assert user is None
    assert redirect.headers["location"] == "/login?next=%2Fstaff%2Faddstaff"


@pytest.fixture
def anonymous_client(monkeypatch):
    async def no_session(request):
        return None

    async def existing_user_count():
        return 1

    monkeypatch.setattr(main_module.session_manager, "load_session", no_session)
    monkeypatch.setattr(main_module.user_repo, "count_users", existing_user_count)
    return TestClient(app)


def test_login_page_uses_next_as_success_redirect(anonymous_client):
    response = anonymous_client.get("/login?next=%2Fstaff")
    assert response.status_code == 200
    assert 'data-success-redirect="/staff"' in response.text


def test_login_page_ignores_external_next(anonymous_client):
    response = anonymous_client.get("/login?next=https%3A%2F%2Fevil.example.com")
    assert response.status_code == 200
    assert 'data-success-redirect="/"' in response.text


def test_login_page_redirects_authenticated_user_to_next(monkeypatch):
    async def has_session(request):
        return SimpleNamespace(user_id=1)

    async def get_user(user_id):
        return {"id": 1}

    async def no_totp_needed(user):
        return False

    monkeypatch.setattr(main_module.session_manager, "load_session", has_session)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", get_user)
    monkeypatch.setattr(main_module, "_user_requires_totp_enrollment", no_totp_needed)
    client = TestClient(app)
    response = client.get("/login?next=%2Fstaff", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/staff"


def test_sanitize_local_redirect_target_allows_decoded_local_path():
    result = main_module._sanitize_local_redirect_target(
        "/staff%2Faddstaff?department=IT",
        fallback="/",
    )
    assert result == "/staff/addstaff?department=IT"


@pytest.mark.parametrize(
    "value",
    [
        "//evil.example.com/",
        "/%2F%2Fevil.example.com/",
        "/%5C%5Cevil.example.com/",
        "/%E0%A4%A",
        "/%2",
    ],
)
def test_sanitize_local_redirect_target_rejects_encoded_and_malformed_targets(value):
    result = main_module._sanitize_local_redirect_target(value, fallback="/home")
    assert result == "/home"


def test_staff_add_page_flags_modal_open(monkeypatch):
    captured = {}

    async def fake_staff_page(request, *, enabled, department, show_ex_staff):
        captured["flag"] = request.state.open_add_staff_modal
        captured["department"] = department
        return "rendered"

    monkeypatch.setattr(staff_handlers, "staff_page", fake_staff_page)
    result = asyncio.run(
        staff_handlers.staff_add_page(_request("/staff/addstaff"), department="IT")
    )
    assert result == "rendered"
    assert captured == {"flag": True, "department": "IT"}
