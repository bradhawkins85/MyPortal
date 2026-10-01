"""Company-scoping regression coverage for reporting permissions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.features.reporting import handlers as reporting_handlers
from app.repositories import reporting as reporting_repo
from app.repositories import users as users_repo
from app.services import report_query_builder


def test_eligible_reporting_users_are_loaded_for_selected_company(monkeypatch):
    seen_company_ids: list[int] = []

    async def fake_list_users_for_company(company_id: int):
        seen_company_ids.append(company_id)
        return [
            {
                "id": 8,
                "email": "user@example.test",
                "first_name": "Portal",
                "last_name": "User",
                "is_super_admin": False,
            },
            {
                "id": 1,
                "email": "admin@example.test",
                "first_name": "Super",
                "last_name": "Admin",
                "is_super_admin": True,
            },
        ]

    monkeypatch.setattr(users_repo, "list_users_for_company", fake_list_users_for_company)

    result = asyncio.run(reporting_handlers._list_reporting_eligible_users(42))

    assert seen_company_ids == [42]
    assert result == [{"id": 8, "label": "Portal User <user@example.test>"}]


def test_eligible_reporting_users_fail_closed_without_selected_company(monkeypatch):
    async def fail_list_users_for_company(_company_id: int):
        raise AssertionError("The repository must not be queried without a company")

    monkeypatch.setattr(users_repo, "list_users_for_company", fail_list_users_for_company)

    assert asyncio.run(reporting_handlers._list_reporting_eligible_users(None)) == []


def test_edit_uses_active_company_for_permission_users(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def fake_get_query(_report_id: int):
        return {"id": 12, "name": "Company report", "slug": "company-report"}

    async def fake_list_permissions(_report_id: int):
        return [8]

    async def fake_eligible(company_id: int | None):
        captured["company_id"] = company_id
        return [{"id": 8, "label": "Portal User"}]

    async def fake_render(template, _request, _user, *, extra):
        captured.update(template=template, extra=extra)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require_super_admin_page,
            _render_template=fake_render,
        ),
    )
    monkeypatch.setattr(reporting_handlers, "_list_reporting_eligible_users", fake_eligible)
    monkeypatch.setattr(reporting_repo, "get_query", fake_get_query)
    monkeypatch.setattr(reporting_repo, "list_permission_user_ids", fake_list_permissions)
    monkeypatch.setattr(report_query_builder, "describe_schema", lambda: _empty_schema())

    response = asyncio.run(
        reporting_handlers.admin_reporting_edit(
            SimpleNamespace(state=SimpleNamespace(active_company_id=42)), 12
        )
    )

    assert response.status_code == 200
    assert captured["company_id"] == 42
    assert captured["extra"]["eligible_users"] == [{"id": 8, "label": "Portal User"}]


async def _empty_schema():
    return {"tables": [], "relations": []}
