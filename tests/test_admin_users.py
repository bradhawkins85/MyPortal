from pathlib import Path

import pytest

from app.main import app
from app.repositories import users as user_repo


def test_admin_users_routes_are_registered():
    routes = {
        (getattr(route, "path", None), frozenset(getattr(route, "methods", set())))
        for route in app.routes
    }

    assert ("/admin/users", frozenset({"GET"})) in routes
    assert ("/admin/users/{user_id}/{action}", frozenset({"POST"})) in routes


def test_users_sidebar_and_management_table_are_present():
    root = Path(__file__).resolve().parent.parent
    base = (root / "app/templates/base.html").read_text()
    page = (root / "app/templates/admin/users.html").read_text()

    assert 'href="/admin/users"' in base
    assert "{% if is_super_admin %}" in base
    assert "Active MyPortal users" in page
    assert 'action="/admin/users/{{ entry.id }}/deactivate"' in page
    assert 'action="/admin/users/{{ entry.id }}/delete"' in page
    assert "{% include \"partials/csrf.html\" %}" in page


@pytest.mark.anyio
async def test_list_active_users_for_admin_filters_and_orders(monkeypatch):
    captured = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"id": 7, "email": "active@example.com", "company_name": "Example"}]

    monkeypatch.setattr(user_repo.db, "fetch_all", fake_fetch_all)

    result = await user_repo.list_active_users_for_admin()

    assert result == [{"id": 7, "email": "active@example.com", "company_name": "Example"}]
    assert "WHERE u.is_active = 1" in captured["sql"]
    assert "LEFT JOIN companies" in captured["sql"]
    assert "ORDER BY" in captured["sql"]
