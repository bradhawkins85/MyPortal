"""Conditional logic in the browser; RUN_BROWSER_TESTS=1 uses a local Chromium.

Renders the production templates with their real scripts. Requests are
intercepted so no database or application server is needed.
"""

import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape
import pytest

from app.services import custom_forms

ROOT = Path(__file__).resolve().parents[1]
BASE = """<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csrf-token" content="token"><link rel="stylesheet" href="/static/css/app.css">
{% block styles %}{% endblock %}</head><body class="theme-default">
{% block content %}{% endblock %}{% block scripts %}{% endblock %}</body></html>"""

FIELDS = [
    {"id": 1, "field_type": "select", "label": "Urgency", "required": False, "options": ["Low", "High"], "config": {}},
    {"id": 2, "field_type": "checkbox", "label": "Call me back", "required": False, "options": [], "config": {}},
    {"id": 3, "field_type": "text", "label": "Phone", "required": True, "options": [], "config": {},
     "conditions": {"match": "all", "rules": [{"field_id": 2, "operator": "equals", "value": "Yes"}]}},
    {"id": 4, "field_type": "multiselect", "label": "Affected", "required": True, "options": ["Email", "Phone"], "config": {},
     "conditions": {"match": "any", "rules": [{"field_id": 1, "operator": "equals", "value": "High"},
                                              {"field_id": 3, "operator": "not_empty", "value": ""}]}},
]


def _render(template, **context):
    environment = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": BASE}), FileSystemLoader(ROOT / "app/templates"),
    ]), autoescape=select_autoescape())
    return environment.get_template(template).render(static_url=lambda path: path, **context)


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("RUN_BROWSER_TESTS") != "1":
        pytest.skip("Set RUN_BROWSER_TESTS=1 to run browser checks")
    api = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium")
    if not executable:
        pytest.skip("Chromium is required")
    with api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        yield browser
        browser.close()


@pytest.fixture
def mount(browser):
    contexts, errors = [], []

    def create(html, response_body="{}"):
        context = browser.new_context(viewport={"width": 1100, "height": 900})
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        sent = []

        def intercept(route):
            path = urlsplit(route.request.url).path
            if route.request.method in {"POST", "PATCH"}:
                sent.append(route.request)
                route.fulfill(status=200, content_type="application/json", body=response_body)
            elif path.startswith("/static/"):
                asset = ROOT / "app" / path.lstrip("/")
                if asset.is_file():
                    route.fulfill(content_type="text/css" if asset.suffix == ".css" else "text/javascript", body=asset.read_bytes())
                else:
                    route.fulfill(status=204)
            else:
                route.fulfill(content_type="text/html", body=html)

        context.route("**/*", intercept)
        page.goto("https://portal.test/page")
        return page, sent

    yield create
    for context in contexts:
        context.close()
    assert not errors, errors


def test_fields_show_and_hide_as_answers_change(mount):
    page, sent = mount(_render("forms/submit.html", form={"name": "Support", "slug": "support"}, fields=FIELDS, errors=[]),
                       response_body='{"ticket_id": 42}')
    phone, affected = page.locator('[data-field-id="3"]'), page.locator('[data-field-id="4"]')
    assert phone.is_hidden() and affected.is_hidden()

    page.get_by_label("Urgency").select_option("High")
    assert affected.is_visible() and phone.is_hidden()
    page.get_by_label("Urgency").select_option("Low")
    assert affected.is_hidden()

    page.get_by_label("Call me back").check()
    assert phone.is_visible()
    assert page.locator("#cf-f-3").evaluate("input => input.required")
    page.locator("#cf-f-3").fill("0400 000 000")
    assert affected.is_visible()  # any: Phone is answered

    # Unticking hides Phone, which in turn hides Affected (its answer no longer counts).
    page.get_by_label("Call me back").uncheck()
    assert phone.is_hidden() and affected.is_hidden()

    # Hidden required questions don't block submitting, and aren't sent.
    page.get_by_role("button", name="Submit request").click()
    page.get_by_role("link", name="View ticket #42").wait_for()
    body = sent[0].post_data_buffer.decode(errors="replace")
    form_data = json.loads(body.split('name="form_data"')[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0])
    assert set(form_data) == {"1", "2"}
    assert "field_3" not in body


def test_required_conditional_question_is_enforced_when_shown(mount):
    page, sent = mount(_render("forms/submit.html", form={"name": "Support", "slug": "support"}, fields=FIELDS, errors=[]))
    page.get_by_label("Urgency").select_option("High")
    page.get_by_role("button", name="Submit request").click()
    assert page.get_by_text("Choose at least one option for Affected").is_visible()
    assert not sent


def test_builder_saves_conditions(mount):
    field_rows = [dict(field, form_id=1, placeholder=None, help_text=None, default_value=None, sort_order=index)
                  for index, field in enumerate(FIELDS)]
    html = _render(
        "admin/custom_forms.html", forms=[{"id": 1, "slug": "support", "name": "Support", "active": True}],
        selected_form={"id": 1, "slug": "support", "name": "Support", "active": True}, selected_fields=field_rows,
        access_user_ids=[], available_users=[], submissions=[], access_user_labels={}, csrf_token="token",
        field_types=custom_forms.field_type_choices(), field_type_labels=dict(custom_forms.FIELD_TYPES),
        condition_meta=custom_forms.condition_builder_meta(),
        condition_summaries={field["id"]: custom_forms.condition_summary(field, field_rows) for field in field_rows},
    )
    page, sent = mount(html)
    assert page.get_by_text("Shown when Call me back is Yes").is_visible()

    page.get_by_role("button", name="Edit Phone").click()
    toggle = page.get_by_label("Only show this question when other answers match")
    assert toggle.is_checked()
    assert page.get_by_label("Question for condition 1").input_value() == "2"
    # Phone can't depend on itself.
    assert "Phone" not in page.get_by_label("Question for condition 1").locator("option").all_inner_texts()

    page.get_by_role("button", name="Add condition").click()
    page.get_by_label("Question for condition 2").select_option(label="Urgency")
    page.get_by_label("Comparison for condition 2").select_option(label="is not")
    page.get_by_label("Value for condition 2").select_option("Low")
    page.get_by_label("Show this question when", exact=True).select_option("any")
    page.get_by_role("button", name="Save field").click()
    page.wait_for_url("**/admin/custom-forms?form_id=1")
    payload = json.loads(sent[0].post_data)
    assert sent[0].method == "PATCH" and sent[0].url.endswith("/api/forms/1/fields/3")
    assert payload["conditions"] == {"match": "any", "rules": [
        {"field_id": 2, "operator": "equals", "value": "Yes"},
        {"field_id": 1, "operator": "not_equals", "value": "Low"},
    ]}


def test_builder_clears_conditions_and_requires_values(mount):
    field_rows = [dict(field, form_id=1, placeholder=None, help_text=None, default_value=None, sort_order=index)
                  for index, field in enumerate(FIELDS)]
    html = _render(
        "admin/custom_forms.html", forms=[{"id": 1, "slug": "support", "name": "Support", "active": True}],
        selected_form={"id": 1, "slug": "support", "name": "Support", "active": True}, selected_fields=field_rows,
        access_user_ids=[], available_users=[], submissions=[], access_user_labels={}, csrf_token="token",
        field_types=custom_forms.field_type_choices(), field_type_labels=dict(custom_forms.FIELD_TYPES),
        condition_meta=custom_forms.condition_builder_meta(), condition_summaries={},
    )
    page, sent = mount(html)
    page.get_by_role("button", name="Edit Urgency").click()
    toggle = page.get_by_label("Only show this question when other answers match")
    assert not toggle.is_checked()
    page.get_by_text("Only show this question when other answers match").click()
    assert toggle.is_checked()
    page.get_by_label("Question for condition 1").select_option(label="Phone")
    page.get_by_label("Comparison for condition 1").select_option(label="contains")
    page.get_by_role("button", name="Save field").click()
    assert page.get_by_text("Enter a value for each condition, or remove it.").is_visible()
    assert not sent
    page.get_by_role("button", name="Remove condition 1").click()
    assert not toggle.is_checked()
    page.get_by_role("button", name="Save field").click()
    page.wait_for_url("**/admin/custom-forms?form_id=1")
    assert json.loads(sent[0].post_data)["conditions"] is None
