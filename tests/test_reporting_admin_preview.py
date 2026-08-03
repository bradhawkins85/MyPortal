"""Regression coverage for testing unsaved reporting query changes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from starlette.datastructures import FormData

from app.features.reporting import handlers as reporting_handlers
from app.repositories import reporting as reporting_repo
from app.services import reporting as reporting_service


def test_admin_reporting_test_renders_unsaved_query_without_updating(monkeypatch):
    captured: dict[str, object] = {}
    request = SimpleNamespace(
        state=SimpleNamespace(active_company_id=42),
        form=lambda: None,
    )

    async def fake_form():
        return FormData(
            [
                ("name", "Preview name"),
                ("slug", "preview-slug"),
                ("description", "Unsaved description"),
                ("sql_query", "SELECT 2 AS changed"),
                ("permission_user_ids", "7"),
                ("action", "test"),
            ]
        )

    request.form = fake_form

    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def fake_get_query(report_id):
        assert report_id == 12
        return {
            "id": 12,
            "name": "Saved name",
            "slug": "saved-slug",
            "description": None,
            "sql_query": "SELECT 1 AS saved",
        }

    async def fake_run_query(sql_query, *, company_id):
        assert sql_query == "SELECT 2 AS changed"
        assert company_id == 42
        return {
            "columns": ["changed"],
            "rows": [{"changed": 2}],
            "row_count": 1,
            "truncated": False,
        }

    async def fake_render_template(template, _request, user, *, extra):
        captured.update(template=template, user=user, extra=extra)
        return SimpleNamespace(status_code=200)

    async def fail_update(*args, **kwargs):
        raise AssertionError("Testing changes must not persist the report")

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require_super_admin_page,
            _render_template=fake_render_template,
        ),
    )
    monkeypatch.setattr(reporting_handlers, "_list_reporting_eligible_users", lambda: _empty_users())
    monkeypatch.setattr(reporting_repo, "get_query", fake_get_query)
    monkeypatch.setattr(reporting_repo, "update_query", fail_update)
    monkeypatch.setattr(reporting_service, "run_query_with_context", fake_run_query)

    response = asyncio.run(reporting_handlers.admin_reporting_update(request, 12))

    assert response.status_code == 200
    assert captured["template"] == "admin/reporting_form.html"
    extra = captured["extra"]
    assert extra["report"]["sql_query"] == "SELECT 2 AS changed"
    assert extra["report"]["slug"] == "saved-slug"
    assert extra["granted_user_ids"] == {7}
    assert extra["test_result"]["rows"] == [{"changed": 2}]
    assert extra["test_error"] is None


async def _empty_users():
    return []
