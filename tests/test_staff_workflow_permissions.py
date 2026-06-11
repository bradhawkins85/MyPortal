import asyncio
from types import SimpleNamespace

from app.features.staff import handlers
from app.features.staff import helpers


def async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def test_staff_workflow_handlers_request_super_admin_context(monkeypatch):
    captured_calls = []
    redirect = SimpleNamespace(status_code=303)

    async def fake_load_staff_context(request, **kwargs):
        captured_calls.append(kwargs)
        return (
            {"id": 10, "company_id": 1, "is_super_admin": False},
            {"menu_permissions": {"menu.staff": "write"}, "staff_permission": 3},
            None,
            3,
            1,
            redirect,
        )

    monkeypatch.setattr(handlers, "_load_staff_context", fake_load_staff_context)
    request = SimpleNamespace(query_params={})

    calls = [
        handlers.staff_onboarding_workflow_page(request),
        handlers.staff_onboarding_workflow_policy(request),
        handlers.upsert_staff_onboarding_workflow_policy(request),
        handlers.staff_offboarding_workflow_page(request),
        handlers.staff_offboarding_workflow_policy(request),
        handlers.upsert_staff_offboarding_workflow_policy(request),
        handlers.list_staff_workflow_policies("onboarding", request),
        handlers.create_staff_workflow_policy("onboarding", request),
        handlers.update_staff_workflow_policy("onboarding", 1, request),
        handlers.delete_staff_workflow_policy("onboarding", 1, request),
        handlers.staff_workflow_history_page(request),
        handlers.staff_workflow_history_recent(request),
        handlers.retry_workflow_execution(1, request),
        handlers.resume_workflow_execution(1, request),
    ]

    for call in calls:
        result = asyncio.run(call)
        assert result is redirect

    assert captured_calls
    assert all(call.get("require_super_admin") is True for call in captured_calls)


def test_load_staff_context_rejects_staff_menu_assignee_for_super_admin_context(monkeypatch):
    class FakeMain:
        user_company_repo = SimpleNamespace(
            get_user_company=async_return(
                {"menu_permissions": {"menu.staff": "write"}, "staff_permission": 3}
            )
        )

        @staticmethod
        async def _require_authenticated_user(request):
            return {"id": 10, "company_id": 1, "is_super_admin": False}, None

    monkeypatch.setattr(helpers, "_main", lambda: FakeMain)

    user, membership, company, staff_permission, company_id, redirect = asyncio.run(
        helpers._load_staff_context(SimpleNamespace(), require_super_admin=True)
    )

    assert user["id"] == 10
    assert membership is None
    assert company is None
    assert staff_permission == 0
    assert company_id is None
    assert redirect.status_code == 303


def test_staff_page_workflow_links_are_super_admin_only():
    template = open("app/templates/staff/index.html", encoding="utf-8").read()

    assert "{% if is_super_admin %}" in template
    assert "is_super_admin or staff_permission == 3" not in template
