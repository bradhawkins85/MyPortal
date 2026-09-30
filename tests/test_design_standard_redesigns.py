"""Rendering checks for pages redesigned to the gold-standard UI patterns."""
from __future__ import annotations

import json
import re
from pathlib import Path

import jinja2

TEMPLATES = Path("app/templates")


def _env() -> jinja2.Environment:
    return jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)


def _section(template: str, start: str, end: str) -> str:
    source = (TEMPLATES / template).read_text()
    return source[source.index(start) : source.index(end)]


def _json_block(html: str, element_id: str) -> dict:
    match = re.search(rf'id="{element_id}">(.*?)</script>', html, re.S)
    assert match, element_id
    return json.loads(match.group(1))


def test_staff_intake_fields_render_as_card_list_with_modal_editor():
    snippet = _section(
        "admin/company_edit.html", "{% set sif_type_labels", "{% set scf_type_labels"
    )
    fields = [
        {"key": "first_name", "label": "First name", "type": "text", "visible": True,
         "required": True, "sort_order": 1, "options": []},
        {"key": "department", "label": "Department", "type": "select", "visible": True,
         "required": False, "sort_order": 5, "options": [{"value": "s", "label": "Sales"}]},
        {"key": "shirt", "label": "Shirt", "type": "multiselect", "visible": False,
         "required": False, "sort_order": 9, "options": []},
    ]
    html = _env().from_string(snippet).render(
        staff_field_config=fields, company={"id": 3}, csrf_token="t"
    )

    assert 'data-sif-edit="department"' in html
    assert 'id="staff-intake-field-modal"' in html
    assert "sif-chip--hidden" in html and "No options yet" in html
    # Raw "value:Label" strings only survive in the no-JavaScript fallback.
    assert html.count('name="field_department_options"') == 1
    assert html.index("<noscript>") < html.index('name="field_department_options"')
    data = _json_block(html, "staff-intake-field-editor-data")
    assert data["coreKeys"] == ["first_name", "last_name"]
    assert data["fields"][1]["options"] == [{"value": "s", "label": "Sales"}]


def test_tray_ticket_questions_render_grouped_list_and_editor():
    questions = [
        {"id": 1, "scope": "global", "company_id": None, "label": "Which site?", "placeholder": "",
         "field_type": "select", "is_required": True, "is_active": True, "sort_order": 10,
         "options": ["Sydney"], "conditions": []},
        {"id": 3, "scope": "company", "company_id": 5, "label": "Room", "placeholder": "",
         "field_type": "text", "is_required": False, "is_active": False, "sort_order": 5,
         "options": [], "conditions": [
             {"parent_question_id": 1, "operator": "equals", "expected_value": "Sydney"}
         ]},
    ]
    html = _env().from_string(
        '{% include "admin/tray/_ticket_question_editor.html" %}'
    ).render(
        tq_questions=questions,
        tq_parents=questions,
        tq_base_url="/admin/tray/ticket-questions",
        tq_companies=[{"id": 5, "name": "Acme"}],
        tq_company=None,
        csrf_token="t",
    )

    assert html.index("Global — every company") < html.index("Company — Acme")
    assert "Shown when Which site? is “Sydney”" in html
    # Edit links keep working without JavaScript and open the modal with it.
    assert 'href="/admin/tray/ticket-questions/3/edit" data-tq-edit="3"' in html
    assert 'id="tq-modal"' in html
    data = _json_block(html, "tq-editor-data")
    assert data["baseUrl"] == "/admin/tray/ticket-questions"
    assert data["questions"][1]["conditions"][0]["expected_value"] == "Sydney"


def test_tray_ticket_questions_empty_state_offers_first_question():
    html = _env().from_string(
        '{% include "admin/tray/_ticket_question_editor.html" %}'
    ).render(
        tq_questions=[],
        tq_parents=[],
        tq_base_url="/admin/companies/2/tray/ticket-questions",
        tq_companies=None,
        tq_company={"id": 2, "name": "Globex"},
        csrf_token="t",
    )

    assert "No questions yet" in html
    assert "+ Add your first question" in html
    assert 'name="company_id" value="2"' in html


def test_scheduled_ticket_payload_macro_keeps_json_field_name():
    html = _env().from_string(
        '{% from "macros/scheduled_ticket_payload.html" import scheduled_ticket_payload %}'
        "{{ scheduled_ticket_payload('bulk-task', 'hidden', 'Copied to each company.') }}"
    ).render()

    assert 'id="bulk-task-json-payload-field" hidden data-ticket-payload' in html
    assert 'name="jsonPayload"' in html
    assert 'data-stp-field="subject"' in html
    assert "Copied to each company." in html


def test_scheduled_task_pages_use_structured_ticket_payload():
    for template in (
        "admin/scheduled_tasks.html",
        "admin/automation.html",
        "admin/company_edit.html",
    ):
        source = (TEMPLATES / template).read_text()
        assert "JSON Payload</label>" not in source, template
        assert "scheduled_ticket_payload('task'" in source, template
        assert "scheduled_ticket_payload.js" in source, template
