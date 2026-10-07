"""Custom questions reach public validation, saved submissions and admin routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from httpx import ASGITransport, AsyncClient
from starlette.datastructures import FormData

from app.features.client_onboarding import routes
from app.services import client_onboarding as onboarding
from app.services import client_onboarding_questions as questions


def question(question_id=1, **overrides):
    return {
        "id": question_id, "label": "Preferred service", "field_type": "dropdown",
        "section": "business", "required": True, "options": ["Support", "Cloud"],
        "help_text": "", "display_order": 0,
    } | overrides


def valid_form(*extra):
    return FormData([
        ("client_name", "Acme"), ("company_phone", "07 3000 0001"),
        ("company_email", "hello@acme.com.au"), ("site_index", "3"),
        ("site-3-name", "Head office"), ("site-3-street", "1 Main St"),
        ("site-3-contact_first_name", "Jo"), ("site-3-contact_last_name", "Bloggs"),
        ("site-3-contact_email", "jo@acme.com.au"),
        ("site-3-timezone", "Australia/Brisbane"),
        ("billing_same_as_primary", "1"), ("confirm_details", "1"), *extra,
    ])


@pytest.fixture
def route_main(monkeypatch):
    main = SimpleNamespace(
        _require_super_admin_page=AsyncMock(return_value=({"id": 7}, None)),
        _render_template=AsyncMock(return_value=HTMLResponse("rendered")),
    )
    monkeypatch.setattr(routes, "_main", lambda: main)
    return main


@pytest.mark.anyio
async def test_questions_path_precedes_dynamic_onboarding_id(route_main, monkeypatch):
    monkeypatch.setattr(questions, "list_questions", AsyncMock(return_value=[]))
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/client-onboarding/questions")
    assert response.status_code == 200
    assert route_main._render_template.await_args.args[0] == "admin/client_onboarding_questions.html"


@pytest.mark.anyio
async def test_unauthorised_admin_cannot_save_or_delete(route_main, monkeypatch):
    redirect = RedirectResponse("/login")
    route_main._require_super_admin_page.return_value = (None, redirect)
    save = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(questions, "save_question", save)
    monkeypatch.setattr(questions, "delete_question", delete)
    request = SimpleNamespace(form=AsyncMock(return_value=FormData()))
    assert await routes.admin_save_client_onboarding_question(request) is redirect
    assert await routes.admin_delete_client_onboarding_question(1, request) is redirect
    request.form.assert_not_awaited()
    save.assert_not_awaited()
    delete.assert_not_awaited()


@pytest.mark.anyio
async def test_invalid_admin_question_preserves_edit_values(route_main, monkeypatch):
    monkeypatch.setattr(questions, "list_questions", AsyncMock(return_value=[question()]))
    save = AsyncMock()
    monkeypatch.setattr(questions, "save_question", save)
    request = SimpleNamespace(form=AsyncMock(return_value=FormData([
        ("question_id", "1"), ("label", "Service"), ("field_type", "dropdown"),
        ("section", "billing"), ("required", "1"), ("options", "Cloud\nCloud"),
    ])))
    response = await routes.admin_save_client_onboarding_question(request)
    assert response.status_code == 400
    extra = route_main._render_template.await_args.kwargs["extra"]
    assert extra["editing_question_id"] == 1
    assert extra["question_form"]["options"] == "Cloud\nCloud"
    assert extra["question_form"]["required"] is True
    assert extra["question_errors"]
    save.assert_not_awaited()


@pytest.mark.anyio
async def test_admin_save_validates_and_audits(route_main, monkeypatch):
    monkeypatch.setattr(questions, "list_questions", AsyncMock(return_value=[]))
    save = AsyncMock(return_value=question(9))
    audit = AsyncMock()
    monkeypatch.setattr(questions, "save_question", save)
    monkeypatch.setattr(routes.audit_service, "record", audit)
    request = SimpleNamespace(form=AsyncMock(return_value=FormData([
        ("label", "Preferred service"), ("field_type", "dropdown"),
        ("section", "business"), ("required", "1"), ("options", "Support\nCloud"),
    ])))
    response = await routes.admin_save_client_onboarding_question(request)
    assert response.status_code == 303
    assert save.await_args.args[0] is None
    assert save.await_args.args[1]["options"] == ["Support", "Cloud"]
    assert audit.await_args.kwargs["action"] == "client_onboarding.question.create"
    assert audit.await_args.kwargs["entity_id"] == 9


@pytest.mark.anyio
async def test_admin_delete_removes_only_definition_and_audits(route_main, monkeypatch):
    delete = AsyncMock(return_value=True)
    audit = AsyncMock()
    monkeypatch.setattr(questions, "delete_question", delete)
    monkeypatch.setattr(routes.audit_service, "record", audit)
    response = await routes.admin_delete_client_onboarding_question(4, SimpleNamespace())
    assert response.status_code == 303
    delete.assert_awaited_once_with(4)
    assert audit.await_args.kwargs["action"] == "client_onboarding.question.delete"
    assert audit.await_args.kwargs["entity_id"] == 4


@pytest.mark.anyio
async def test_public_get_loads_shared_question_configuration(monkeypatch):
    definitions = [question()]
    monkeypatch.setattr(questions, "list_questions", AsyncMock(return_value=definitions))
    monkeypatch.setattr(onboarding, "get_open_onboarding", AsyncMock(return_value={
        "id": 1, "client_name": "Acme", "contact_name": "Jo Bloggs",
    }))
    render = AsyncMock(return_value=HTMLResponse("open"))
    monkeypatch.setattr(routes, "_render_public", render)
    await routes.public_client_onboarding_form("token", SimpleNamespace())
    assert render.await_args.kwargs["questions"] is definitions
    assert render.await_args.kwargs["form"]["client_name"] == "Acme"


@pytest.mark.anyio
async def test_admin_question_count_limit_preserves_form(route_main, monkeypatch):
    monkeypatch.setattr(questions, "list_questions", AsyncMock(return_value=[question(i) for i in range(100)]))
    save = AsyncMock()
    monkeypatch.setattr(questions, "save_question", save)
    request = SimpleNamespace(form=AsyncMock(return_value=FormData([
        ("label", "Extra"), ("field_type", "short_text"), ("section", "review"),
    ])))
    response = await routes.admin_save_client_onboarding_question(request)
    assert response.status_code == 400
    assert "100" in " ".join(route_main._render_template.await_args.kwargs["extra"]["question_errors"])
    save.assert_not_awaited()


@pytest.mark.anyio
async def test_public_validation_keeps_answers_when_required_site_answer_missing(monkeypatch):
    definitions = [question(), question(2, field_type="email", section="site_contact", options=[])]
    form = valid_form(("custom-1", "Cloud"), ("site-3-custom-2", "invalid-email"))
    request = SimpleNamespace(form=AsyncMock(return_value=form))
    monkeypatch.setattr(onboarding, "get_open_onboarding", AsyncMock(return_value={"id": 7}))
    monkeypatch.setattr(questions, "list_questions", AsyncMock(return_value=definitions))
    render = AsyncMock(return_value=HTMLResponse("errors"))
    complete = AsyncMock()
    monkeypatch.setattr(routes, "_render_public", render)
    monkeypatch.setattr(onboarding, "complete_onboarding", complete)
    await routes.public_client_onboarding_submit("token", request)
    state = render.await_args.kwargs
    assert state["status_code"] == 400
    assert state["form"]["custom_answers"]["1"] == "Cloud"
    assert state["form"]["sites"][0]["custom_answers"]["2"] == "invalid-email"
    assert state["errors"][0].startswith("Site 1 (Head office) primary contact:")
    complete.assert_not_awaited()


@pytest.mark.anyio
async def test_public_submission_includes_answer_snapshots(monkeypatch):
    definitions = [question(), question(2, field_type="long_text", section="site_address", options=[])]
    form = valid_form(("custom-1", "Cloud"), ("site-3-custom-2", "<script>x</script>\nLevel 2"))
    request = SimpleNamespace(form=AsyncMock(return_value=form))
    monkeypatch.setattr(onboarding, "get_open_onboarding", AsyncMock(return_value={"id": 7}))
    monkeypatch.setattr(questions, "list_questions", AsyncMock(return_value=definitions))
    monkeypatch.setattr(routes, "_render_public", AsyncMock(return_value=HTMLResponse("complete")))
    complete = AsyncMock()
    monkeypatch.setattr(onboarding, "complete_onboarding", complete)
    await routes.public_client_onboarding_submit("token", request)
    submission = complete.await_args.args[1]
    saved = submission.to_json()
    assert saved["custom_answers"][0]["value"] == "Cloud"
    assert saved["sites"][0]["custom_answers"][0]["value"] == "<script>x</script>\nLevel 2"
    definitions[0]["label"] = "Changed label"
    assert saved["custom_answers"][0]["label"] == "Preferred service"
    description = onboarding.ticket_description(submission)
    assert "<script>" not in description
    assert "&lt;script&gt;x&lt;/script&gt;<br>Level 2" in description
    assert "Preferred service" in description


def test_repeated_question_answers_stay_with_their_submitted_site():
    fields = list(valid_form(("site-3-custom-2", "Front door")).multi_items())
    fields += [
        ("site_index", "8"), ("site-8-name", "Warehouse"),
        ("site-8-street", "2 Side St"), ("site-8-contact_first_name", "Sam"),
        ("site-8-contact_last_name", "Smith"), ("site-8-contact_email", "sam@acme.com.au"),
        ("site-8-timezone", "Australia/Brisbane"), ("site-8-custom-2", "Loading dock"),
    ]
    definitions = [question(2, field_type="short_text", section="site_address", options=[])]
    submission, errors = onboarding.parse_submission(FormData(fields), definitions)
    assert not errors
    saved_sites = submission.to_json()["sites"]
    assert [(site["name"], site["custom_answers"][0]["value"]) for site in saved_sites] == [
        ("Head office", "Front door"), ("Warehouse", "Loading dock"),
    ]
    description = onboarding.ticket_description(submission)
    assert "Custom questions for Head office" in description
    assert "Custom questions for Warehouse" in description
