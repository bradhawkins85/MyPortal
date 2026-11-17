"""The automation editors render the step designer inside the real form."""

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

import app.main as main_module
from app.features.automations import handlers
from app.services import automations as automations_service
from app.services import modules as modules_service

TEMPLATES = Path(__file__).resolve().parent.parent / "app/templates"
BASE_STUB = (
    "{% block styles %}{% endblock %}{% block header_title %}{% endblock %}"
    "{% block header_actions %}{% endblock %}{% block content %}{% endblock %}"
    "{% block scripts %}{% endblock %}"
)

ACTIONS = {
    "actions": [
        {"order": 0, "module": "ai-custom-prompt", "note": "Classify", "output_var": "triage",
         "payload": {"instructions": "What is this?", "response_format": "choice", "choices": "Billing, Outage"}},
        {"order": 1, "module": "smtp", "note": "Email </script><b>billing</b>",
         "payload": {"subject": "{{vars.triage.choice}}"},
         "run_if": {"match": "all", "conditions": [
             {"field": "vars.triage.choice", "operator": "equals", "value": "Billing"}]}},
    ]
}


def render_environment() -> Environment:
    env = Environment(
        loader=ChoiceLoader([DictLoader({"base.html": BASE_STUB}), FileSystemLoader(TEMPLATES)]),
        autoescape=select_autoescape(("html", "xml")),
    )
    env.globals["static_url"] = lambda path: path
    return env


async def render_editor(monkeypatch, kind, automation=None):
    async def render_content(template_name, request, user, *, extra):
        return HTMLResponse(render_environment().get_template(template_name).render({**extra, "csrf_token": "tok"}))

    monkeypatch.setattr(main_module, "_render_template", render_content)
    monkeypatch.setattr(modules_service, "list_trigger_action_modules", AsyncMock(return_value=[
        {"slug": "ai-custom-prompt", "name": "AI Custom Prompt", "payload_schema": None},
        {"slug": "smtp", "name": "Send Email", "description": "Send an email.", "payload_schema": None},
    ]))
    request = Request({"type": "http", "method": "GET", "path": "/admin/automations/create/" + kind})
    values = None
    if automation is not None:
        values = handlers._automation_to_form_values(automation)
    response = await handlers._render_automation_form(
        request, {"id": 1}, kind=kind, form_values=values,
        mode="edit" if automation else "create", automation_id=7 if automation else None,
    )
    return response.body.decode()


def _form_html(body):
    start = body.index("<form action=")
    return body[start:body.index("</form>", start)]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["event", "scheduled"])
async def test_editor_posts_steps_from_inside_the_form(monkeypatch, kind):
    automation = {
        "id": 7, "name": "Triage", "kind": kind, "status": "active",
        "trigger_event": "tickets.created" if kind == "event" else None,
        "cadence": "daily" if kind == "scheduled" else None,
        "action_payload": ACTIONS,
    }
    body = await render_editor(monkeypatch, kind, automation)
    form = _form_html(body)

    hidden = re.search(r'name="actionPayload" value="([^"]*)"', form)
    assert hidden is not None
    assert json.loads(hidden.group(1).replace("&#34;", '"').replace("&lt;", "<").replace("&gt;", ">")
                      .replace("&amp;", "&").replace("&#39;", "'"))["actions"][0]["output_var"] == "triage"
    assert 'name="status" value="active" checked' in form
    # Modals hold named radios and must stay outside the posted form.
    assert 'id="af-step-modal"' not in form and 'id="af-step-modal"' in body
    assert "af_condition_mode" not in form
    # The step note is only ever carried as escaped data.
    assert "</script><b>billing</b>" not in body
    assert "/static/js/automation_flow.js" in body
    assert "/static/css/staff_workflows.css" in body
    assert 'data-automation-delete' in body


@pytest.mark.asyncio
async def test_event_editor_names_the_trigger_in_the_flow(monkeypatch):
    monkeypatch.setattr(automations_service, "list_trigger_events", lambda: [
        {"value": "tickets.created", "label": "Ticket created"},
    ])
    body = await render_editor(monkeypatch, "event", {
        "id": 7, "name": "Triage", "kind": "event", "trigger_event": "tickets.created", "action_payload": ACTIONS,
    })
    trigger = re.search(r'<script type="application/json" data-af-trigger>(.*?)</script>', body, re.S)
    assert json.loads(trigger.group(1))["title"] == "When Ticket created"


@pytest.mark.asyncio
async def test_new_scheduled_editor_has_an_empty_flow(monkeypatch):
    body = await render_editor(monkeypatch, "scheduled")
    form = _form_html(body)
    assert 'name="actionPayload" value=""' in form
    assert 'name="status" value="inactive" checked' in form
    assert "data-automation-delete" not in body
