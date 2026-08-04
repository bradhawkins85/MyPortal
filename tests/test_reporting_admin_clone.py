"""Regression coverage for cloning saved reports."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.features.reporting import handlers as reporting_handlers
from app.repositories import reporting as reporting_repo


def test_admin_reporting_clone_prefills_create_form(monkeypatch):
    captured: dict[str, object] = {}
    source = {
        "id": 12,
        "name": "User Mailboxes",
        "slug": "user-mailboxes",
        "description": "Mailbox storage by user",
        "sql_query": "SELECT * FROM m365_mailboxes WHERE mailbox_type = 'User'",
        "is_system": False,
    }

    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def fake_get_query(report_id):
        assert report_id == 12
        return source

    async def fake_list_permissions(report_id):
        assert report_id == 12
        return [7, 9]

    async def fake_render_template(template, _request, user, *, extra):
        captured.update(template=template, user=user, extra=extra)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require_super_admin_page,
            _render_template=fake_render_template,
        ),
    )
    monkeypatch.setattr(
        reporting_handlers,
        "_list_reporting_eligible_users",
        lambda: _eligible_users(),
    )
    monkeypatch.setattr(reporting_repo, "get_query", fake_get_query)
    monkeypatch.setattr(reporting_repo, "list_permission_user_ids", fake_list_permissions)

    response = asyncio.run(reporting_handlers.admin_reporting_clone(SimpleNamespace(), 12))

    assert response.status_code == 200
    assert captured["template"] == "admin/reporting_form.html"
    extra = captured["extra"]
    assert extra["form_action"] == "/admin/reporting"
    assert extra["submit_label"] == "Create cloned report"
    assert extra["granted_user_ids"] == {7, 9}
    assert extra["report"] == {
        **source,
        "id": None,
        "name": "User Mailboxes (Copy)",
        "slug": "user-mailboxes-copy",
    }


def test_admin_reporting_clone_redirects_when_source_is_missing(monkeypatch):
    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def fake_get_query(_report_id):
        return None

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(_require_super_admin_page=fake_require_super_admin_page),
    )
    monkeypatch.setattr(reporting_repo, "get_query", fake_get_query)

    response = asyncio.run(reporting_handlers.admin_reporting_clone(SimpleNamespace(), 404))

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/reporting"


async def _eligible_users():
    return [{"id": 7, "label": "Tech One"}, {"id": 9, "label": "Tech Two"}]
