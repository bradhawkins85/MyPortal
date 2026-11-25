"""Tests for custom ticket details: definitions, visibility, values and UI."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jinja2 import Environment, FileSystemLoader

from app.services import modules
from app.services import ticket_custom_fields as service


def _definition(**overrides):
    base = {
        "id": 1,
        "field_key": "reason",
        "label": "Reason",
        "help_text": None,
        "field_type": "text",
        "options": [],
        "config": {},
        "visibility": "private",
        "editable": True,
        "show_in_list": False,
        "is_active": True,
        "sort_order": 0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_creates_tables_with_phase_header():
    sql = Path("migrations/573_ticket_custom_fields.sql").read_text()
    assert sql.startswith("-- phase: expand")
    assert "CREATE TABLE IF NOT EXISTS ticket_custom_field_definitions" in sql
    assert "CREATE TABLE IF NOT EXISTS ticket_custom_field_values" in sql
    assert "UNIQUE KEY uq_ticket_custom_field_value (ticket_id, field_id)" in sql


# ---------------------------------------------------------------------------
# Visibility and edit rights
# ---------------------------------------------------------------------------


def test_visible_scopes_by_role():
    assert service.visible_scopes(is_super_admin=False, is_staff=False) == {"public"}
    assert service.visible_scopes(is_super_admin=False, is_staff=True) == {"public", "private"}
    assert service.visible_scopes(is_super_admin=True, is_staff=False) == {"public", "private", "admin_only"}


@pytest.mark.parametrize(
    ("definition", "super_admin", "staff", "expected"),
    [
        (_definition(), False, True, True),
        (_definition(editable=False), True, True, False),
        (_definition(visibility="public"), False, False, False),
        (_definition(visibility="admin_only"), False, True, False),
        (_definition(visibility="admin_only"), True, True, True),
        (_definition(is_active=False), True, True, False),
    ],
)
def test_can_edit(definition, super_admin, staff, expected):
    assert service.can_edit(definition, is_super_admin=super_admin, is_staff=staff) is expected


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------


def test_normalise_definition_generates_key_and_parses_options():
    values, errors = service.normalise_definition(
        {
            "label": "Escalation reason",
            "field_type": "select",
            "options": json.dumps(["Vendor", "Customer", "Vendor", " "]),
            "visibility": "public",
            "editable": "",
            "show_in_list": "1",
        }
    )
    assert errors == []
    assert values["field_key"] == "escalation_reason"
    assert values["options"] == ["Vendor", "Customer"]
    assert values["editable"] is False
    assert values["show_in_list"] is True


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"label": ""}, "Enter a label"),
        ({"label": "X", "field_key": "1bad"}, "The key must start"),
        ({"label": "X", "field_type": "signature"}, "supported field type"),
        ({"label": "X", "visibility": "everyone"}, "who can see"),
        ({"label": "X", "field_type": "select"}, "at least one option"),
        ({"label": "X", "field_type": "rating", "rating_max": "11"}, "rating must be"),
    ],
)
def test_normalise_definition_errors(data, message):
    _, errors = service.normalise_definition(data)
    assert any(message in error for error in errors), errors


def test_normalise_definition_rejects_duplicate_key():
    _, errors = service.normalise_definition({"label": "Reason"}, existing_keys=["reason"])
    assert any("already uses" in error for error in errors)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "raw", "expected"),
    [
        ({}, "  hello  ", "hello"),
        ({}, "", None),
        ({"field_type": "number"}, "4.0", "4"),
        ({"field_type": "number"}, "2.5", "2.5"),
        ({"field_type": "checkbox"}, True, "1"),
        ({"field_type": "checkbox"}, "no", "0"),
        ({"field_type": "checkbox"}, "", None),
        ({"field_type": "date"}, "2026-11-25T10:00:00", "2026-11-25"),
        ({"field_type": "time"}, "09:30", "09:30"),
        ({"field_type": "select", "options": ["A", "B"]}, "B", "B"),
        ({"field_type": "multiselect", "options": ["A", "B", "C"]}, ["C", "A"], '["A", "C"]'),
        ({"field_type": "multiselect", "options": ["A", "B"]}, "B, A", '["A", "B"]'),
        ({"field_type": "rating", "config": {"max": 5}}, "4", "4"),
        ({"field_type": "url"}, "https://example.com/x", "https://example.com/x"),
        ({"field_type": "email"}, "a@example.com", "a@example.com"),
    ],
)
def test_coerce_value(overrides, raw, expected):
    assert service.coerce_value(_definition(**overrides), raw) == expected


@pytest.mark.parametrize(
    ("overrides", "raw"),
    [
        ({"field_type": "number"}, "ten"),
        ({"field_type": "number"}, "nan"),
        ({"field_type": "select", "options": ["A"]}, "Z"),
        ({"field_type": "multiselect", "options": ["A"]}, ["A", "Z"]),
        ({"field_type": "rating", "config": {"max": 5}}, "6"),
        ({"field_type": "url"}, "javascript:alert(1)"),
        ({"field_type": "email"}, "nope"),
        ({"field_type": "date"}, "25/11/2026"),
        ({"field_type": "checkbox"}, "maybe"),
        ({}, "x" * 1001),
    ],
)
def test_coerce_value_rejects_bad_input(overrides, raw):
    with pytest.raises(ValueError):
        service.coerce_value(_definition(**overrides), raw)


def test_display_value():
    assert service.display_value(_definition(field_type="checkbox"), "1") == "Yes"
    assert service.display_value(_definition(field_type="multiselect"), '["A", "B"]') == "A, B"
    assert service.display_value(_definition(field_type="rating", config={"max": 5}), "3") == "3 / 5"
    assert service.display_value(_definition(), None) == ""


@pytest.fixture
def fake_repo(monkeypatch):
    definitions = [
        _definition(id=1, field_key="reason"),
        _definition(id=2, field_key="ai_summary", editable=False),
        _definition(id=3, field_key="margin", visibility="admin_only"),
        _definition(id=4, field_key="site", visibility="public", show_in_list=True),
    ]
    store: dict[int, dict] = {4: {"value_text": "Sydney", "updated_source": "user"}}

    async def list_definitions(include_inactive=True):
        return [dict(item) for item in definitions]

    async def get_definition_by_key(key):
        return next((dict(item) for item in definitions if item["field_key"] == key), None)

    async def get_ticket_values(ticket_id):
        return {key: dict(value) for key, value in store.items()}

    async def set_value(ticket_id, field_id, value_text, *, updated_by=None, source="user"):
        if value_text is None:
            store.pop(field_id, None)
        else:
            store[field_id] = {"value_text": value_text, "updated_source": source}

    async def get_values_for_tickets(ticket_ids, field_ids):
        return {ticket_id: {fid: store[fid]["value_text"] for fid in field_ids if fid in store} for ticket_id in ticket_ids}

    repo = SimpleNamespace(
        list_definitions=list_definitions,
        get_definition_by_key=get_definition_by_key,
        get_ticket_values=get_ticket_values,
        set_value=set_value,
        get_values_for_tickets=get_values_for_tickets,
    )
    monkeypatch.setattr(service, "repo", repo)
    return store


@pytest.mark.asyncio
async def test_technician_saves_editable_field(fake_repo):
    changes, errors = await service.save_ticket_values(
        7, {"reason": "Waiting on vendor"}, is_super_admin=False, is_staff=True, user_id=3
    )
    assert errors == []
    assert changes == [{"field_key": "reason", "previous": None, "value": "Waiting on vendor"}]
    assert fake_repo[1]["value_text"] == "Waiting on vendor"


@pytest.mark.asyncio
async def test_read_only_field_cannot_be_set_directly(fake_repo):
    changes, errors = await service.save_ticket_values(
        7, {"reason": "ok", "ai_summary": "manual"}, is_super_admin=True, is_staff=True, user_id=1
    )
    assert changes == []
    assert any("read-only" in error for error in errors)
    assert 1 not in fake_repo  # nothing saved when any field is refused


@pytest.mark.asyncio
async def test_technician_cannot_see_or_set_admin_only_field(fake_repo):
    _, errors = await service.save_ticket_values(
        7, {"margin": "40%"}, is_super_admin=False, is_staff=True, user_id=3
    )
    assert errors and "Unknown ticket detail" in errors[0]
    fields = await service.build_ticket_fields(7, is_super_admin=False, is_staff=True)
    assert "margin" not in {field["field_key"] for field in fields}


@pytest.mark.asyncio
async def test_requester_sees_only_public_fields_read_only(fake_repo):
    fields = await service.build_ticket_fields(7, is_super_admin=False, is_staff=False)
    assert [(field["field_key"], field["display"], field["can_edit"]) for field in fields] == [
        ("site", "Sydney", False)
    ]


@pytest.mark.asyncio
async def test_automation_sets_read_only_field(fake_repo):
    outcome = await service.set_value_by_key(7, "ai_summary", "Printer offline")
    assert outcome["changed"] is True
    assert fake_repo[2] == {"value_text": "Printer offline", "updated_source": "automation"}
    context = await service.automation_context(7)
    assert context["ai_summary"] == "Printer offline"
    assert context["reason"] is None


@pytest.mark.asyncio
async def test_list_columns_respect_visibility(fake_repo):
    columns = await service.list_columns(is_super_admin=False, is_staff=False)
    assert [column["field_key"] for column in columns] == ["site"]
    assert columns[0]["column"] == "cf-site"
    values = await service.list_values(["5", "bad"], columns)
    assert values == {5: {"site": "Sydney"}}


# ---------------------------------------------------------------------------
# Automation action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_ticket_custom_field_action_uses_context_ticket(monkeypatch, fake_repo):
    tickets_repo = SimpleNamespace(get_ticket=AsyncMock(return_value={"id": 9}))
    monkeypatch.setattr(modules, "_tickets_repo", lambda: tickets_repo)
    result = await modules._invoke_set_ticket_custom_field(
        {}, {"field_key": "ai_summary", "value": "Done", "context": {"ticket": {"id": 9}}}
    )
    assert result["status"] == "succeeded"
    assert result["ticket_id"] == 9
    assert fake_repo[2]["value_text"] == "Done"

    cleared = await modules._invoke_set_ticket_custom_field(
        {}, {"ticket_id": "9", "field_key": "ai_summary", "clear": True}
    )
    assert cleared["value"] is None and 2 not in fake_repo


@pytest.mark.asyncio
async def test_set_ticket_custom_field_action_reports_bad_value(monkeypatch, fake_repo):
    monkeypatch.setattr(modules, "_tickets_repo", lambda: SimpleNamespace(get_ticket=AsyncMock(return_value={"id": 9})))
    result = await modules._invoke_set_ticket_custom_field({}, {"ticket_id": 9, "field_key": "nope", "value": "x"})
    assert result["status"] == "failed"
    assert "Unknown ticket detail" in result["error"]


def test_action_is_always_on_ticket_module():
    assert "set-ticket-custom-field" in modules._ALWAYS_ON_TICKET_ACTION_MODULE_SLUGS
    schema = modules.get_action_payload_schema("set-ticket-custom-field")
    assert {field["name"] for field in schema["fields"]} == {"ticket_id", "field_key", "value", "clear"}


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _render_card(fields, **kwargs):
    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    template = env.from_string(
        '{% from "macros/ticket_custom_details.html" import custom_details_card with context %}'
        "{{ custom_details_card(fields, **kwargs) }}"
    )
    return template.render(fields=fields, kwargs=kwargs, csrf_token="tok")


def _field(**overrides):
    field = _definition(**overrides)
    value_text = overrides.get("value_text")
    field.update(
        value_text=value_text,
        value=service.decode_value(field, value_text),
        display=service.display_value(field, value_text),
        updated_source=None,
        can_edit=overrides.get("can_edit", True),
    )
    return field


def test_card_renders_inputs_for_editable_and_text_for_read_only():
    html = _render_card(
        [
            _field(field_key="reason", value_text="<b>x</b>"),
            _field(field_key="ai_summary", label="AI summary", editable=False, can_edit=False, value_text="Done"),
        ],
        action="/admin/tickets/9/custom-fields",
        show_visibility=True,
    )
    assert 'action="/admin/tickets/9/custom-fields"' in html
    assert 'name="cf__reason"' in html and 'value="&lt;b&gt;x&lt;/b&gt;"' in html
    assert 'name="cf__ai_summary"' not in html
    assert "Read-only" in html and "Done" in html
    assert 'name="_csrf" value="tok"' in html


def test_card_is_read_only_without_action():
    html = _render_card([_field(field_key="site", visibility="public", value_text="Sydney")])
    assert "<form" not in html
    assert "Sydney" in html


def test_card_hidden_when_no_fields():
    assert "Custom details" not in _render_card([])


def test_admin_page_uses_modal_editor_and_script():
    template = Path("app/templates/admin/ticket_custom_fields.html").read_text()
    assert 'id="ticket-custom-field-modal" role="dialog"' in template
    assert "<dialog" not in template
    assert "ticket_custom_fields_admin.js" in template
    assert Path("app/static/js/ticket_custom_fields_admin.js").exists()


@pytest.mark.parametrize(
    ("text", "valid"),
    [
        ("a@example.com", True),
        ("first.last@sub.example.com.au", True),
        ("a@b", False),
        ("a@.com", False),
        ("a@b.", False),
        ("a b@c.d", False),
        ("a@@b.c", False),
        ("@b.c", False),
        ("x@" + "." * 50000 + "!", False),
    ],
)
def test_email_check_is_linear_and_correct(text, valid):
    assert service._looks_like_email(text) is valid
