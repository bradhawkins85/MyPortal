"""Conditional logic: show a field only when other answers match its rules."""
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

from app.api.routes import forms as forms_api
from app.core.database import db
from app.repositories import forms as forms_repo
from app.schemas.forms import FormFieldCreate, FormFieldUpdate
from app.services import custom_forms

ROOT = Path(__file__).resolve().parents[1]
ADMIN = {"id": 9, "is_super_admin": True}
FORM = {"id": 1, "slug": "support", "name": "Support", "active": True}


def _field(field_id, field_type="text", label=None, *, required=False, options=None, conditions=None, **extra):
    return {
        "id": field_id, "form_id": 1, "field_type": field_type, "label": label or f"Field {field_id}",
        "required": required, "options": options or [], "config": {}, "default_value": None,
        "conditions": conditions, **extra,
    }


def _when(field_id, operator, value=None, match="all", *more):
    rules = [{"field_id": field_id, "operator": operator, "value": value}]
    rules += [{"field_id": f, "operator": o, "value": v} for f, o, v in more]
    return {"match": match, "rules": rules}


URGENT = _field(1, "select", "Urgency", options=["Low", "High"])
CALLBACK = _field(2, "checkbox", "Call me back")
PHONE = _field(3, "text", "Phone", required=True, conditions=_when(2, "equals", "Yes"))


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source, operator, value, answer, expected",
    [
        (_field(1, "select", options=["Low", "High"]), "equals", "High", "High", True),
        (_field(1, "select", options=["Low", "High"]), "equals", "High", "high", True),
        (_field(1, "select", options=["Low", "High"]), "equals", "High", "Low", False),
        (_field(1, "select", options=["Low", "High"]), "not_equals", "High", "Low", True),
        (_field(1, "select", options=["Low", "High"]), "not_equals", "High", "", True),
        (_field(1, "multiselect", options=["A", "B"]), "equals", "B", ["A", "B"], True),
        (_field(1, "multiselect", options=["A", "B"]), "not_equals", "B", ["A"], True),
        (_field(1, "checkbox"), "equals", "Yes", True, True),
        (_field(1, "checkbox"), "equals", "Yes", "1", True),
        (_field(1, "checkbox"), "equals", "No", "", True),
        (_field(1, "checkbox"), "not_equals", "Yes", False, True),
        (_field(1, "text"), "contains", "printer", "My Printer is jammed", True),
        (_field(1, "text"), "not_contains", "printer", "Laptop", True),
        (_field(1, "text"), "empty", None, "   ", True),
        (_field(1, "text"), "not_empty", None, "x", True),
        (_field(1, "number"), "greater_than", "10", "11.5", True),
        (_field(1, "number"), "greater_than", "10", "9", False),
        (_field(1, "number"), "less_than", "10", "9", True),
        (_field(1, "number"), "equals", "5", "5.0", True),
        (_field(1, "number"), "greater_than", "10", "", False),
        (_field(1, "rating"), "less_than", "3", "2", True),
        (_field(1, "date"), "greater_than", "2026-01-01", "2026-06-30", True),
        (_field(1, "time"), "less_than", "09:00", "08:30", True),
    ],
)
def test_rule_operators(source, operator, value, answer, expected):
    target = _field(2, conditions=_when(1, operator, value))
    assert (2 in custom_forms.visible_field_ids([source, target], {1: answer})) is expected


def test_match_all_and_any():
    rules = [(2, "equals", "Yes")]
    both = _field(3, conditions=_when(1, "equals", "High", "all", *rules))
    either = _field(4, conditions=_when(1, "equals", "High", "any", *rules))
    fields = [URGENT, CALLBACK, both, either]
    assert custom_forms.visible_field_ids(fields, {1: "High", 2: False}) == {1, 2, 4}
    assert custom_forms.visible_field_ids(fields, {1: "High", 2: True}) == {1, 2, 3, 4}
    assert custom_forms.visible_field_ids(fields, {1: "Low", 2: False}) == {1, 2}


def test_hidden_source_counts_as_unanswered_and_chains():
    # Phone shows when Call me back is ticked; Extension shows when Phone is answered.
    extension = _field(4, "text", "Extension", conditions=_when(3, "not_empty"))
    fields = [CALLBACK, PHONE, extension]
    assert custom_forms.visible_field_ids(fields, {2: True, 3: "0400"}) == {2, 3, 4}
    # Phone is hidden, so its stale answer no longer reveals Extension.
    assert custom_forms.visible_field_ids(fields, {2: False, 3: "0400"}) == {2}


def test_rules_for_deleted_fields_are_ignored_and_loops_terminate():
    orphan = _field(5, conditions=_when(99, "equals", "x"))
    assert custom_forms.visible_field_ids([orphan], {}) == {5}
    # Saving a loop is rejected, but stored data must never recurse forever.
    a = _field(6, conditions=_when(7, "not_empty"))
    b = _field(7, conditions=_when(6, "not_empty"))
    assert custom_forms.visible_field_ids([a, b], {6: "x", 7: "y"}) <= {6, 7}


def test_hidden_required_field_is_skipped_and_not_kept():
    clean, errors = custom_forms.validate_submission([CALLBACK, PHONE], {2: "", 3: "stale answer"})
    assert errors == []
    assert clean == {2: False}
    clean, errors = custom_forms.validate_submission([CALLBACK, PHONE], {2: "1", 3: ""})
    assert errors == ["'Phone' is required."]


def test_description_leaves_out_hidden_fields():
    clean, _ = custom_forms.validate_submission([CALLBACK, PHONE], {2: "", 3: "0400"})
    description = custom_forms.build_description("Support", [CALLBACK, PHONE], clean)
    assert "Call me back" in description
    assert "Phone" not in description


@pytest.mark.asyncio
async def test_files_on_hidden_fields_are_not_attached(monkeypatch):
    from app.services import ticket_attachments, tickets
    upload = _field(4, "file", "Photo", conditions=_when(2, "equals", "Yes"))
    monkeypatch.setattr(tickets, "create_ticket", AsyncMock(return_value={"id": 77}))
    save = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(ticket_attachments, "save_uploaded_file", save)
    record = AsyncMock(return_value={"id": 3})
    monkeypatch.setattr(forms_repo, "create_submission", record)
    result = await custom_forms.submit_form(
        form=FORM, fields=[CALLBACK, upload], values={2: False},
        files={4: [SimpleNamespace(filename="photo.png")]}, requester={"id": 5, "company_id": 7},
    )
    save.assert_not_awaited()
    assert result["attachments"] == []
    assert record.await_args.kwargs["data_json"] == '{"2": false}'


# ---------------------------------------------------------------------------
# Definition validation
# ---------------------------------------------------------------------------


def test_valid_conditions_are_normalised():
    conditions, errors = custom_forms.validate_conditions(
        {"match": "any", "rules": [
            {"field_id": 1, "operator": "equals", "value": " High "},
            {"field_id": 2, "operator": "equals", "value": "No"},
            {"field_id": 3, "operator": "empty", "value": "ignored"},
        ]},
        field_id=4, fields=[URGENT, CALLBACK, PHONE],
    )
    assert errors == []
    assert conditions == {"match": "any", "rules": [
        {"field_id": 1, "operator": "equals", "value": "High"},
        {"field_id": 2, "operator": "equals", "value": "No"},
        {"field_id": 3, "operator": "empty", "value": ""},
    ]}


@pytest.mark.parametrize("conditions", [None, {"match": "all", "rules": []}])
def test_no_rules_means_always_shown(conditions):
    assert custom_forms.validate_conditions(conditions, field_id=4, fields=[URGENT]) == (None, [])


@pytest.mark.parametrize(
    "rule, message",
    [
        ({"field_id": 4, "operator": "empty"}, "Choose another question on this form for each condition."),
        ({"field_id": 99, "operator": "empty"}, "Choose another question on this form for each condition."),
        ({"field_id": 5, "operator": "not_empty"}, "'Photo' can't be used in a condition."),
        ({"field_id": 1, "operator": "contains", "value": "Hi"}, "Choose a valid comparison for 'Urgency'."),
        ({"field_id": 1, "operator": "equals", "value": "Medium"}, "Choose one of the choices for 'Urgency'."),
        ({"field_id": 1, "operator": "equals", "value": "  "}, "Enter the value to compare 'Urgency' with."),
        ({"field_id": 2, "operator": "equals", "value": "true"}, "Choose Yes or No for 'Call me back'."),
        ({"field_id": 6, "operator": "greater_than", "value": "ten"}, "Enter a number to compare 'Seats' with."),
    ],
)
def test_invalid_conditions_are_rejected(rule, message):
    fields = [URGENT, CALLBACK, PHONE, _field(4), _field(5, "file", "Photo"), _field(6, "number", "Seats")]
    assert custom_forms.validate_conditions({"rules": [rule]}, field_id=4, fields=fields) == (None, [message])


def test_conditions_that_would_loop_are_rejected():
    # Phone (3) already depends on Call me back (2); 2 can't depend on 3.
    _, errors = custom_forms.validate_conditions(
        {"rules": [{"field_id": 3, "operator": "not_empty"}]}, field_id=2, fields=[URGENT, CALLBACK, PHONE],
    )
    assert errors == ["These conditions depend on this question, which would create a loop."]


def test_summary_and_builder_meta():
    assert custom_forms.condition_summary(PHONE, [CALLBACK, PHONE]) == "Shown when Call me back is Yes"
    either = _field(4, conditions=_when(1, "equals", "High", "any", (3, "empty", "")))
    assert custom_forms.condition_summary(either, [URGENT, PHONE]) == "Shown when Urgency is High or Phone is empty"
    assert custom_forms.condition_summary(_field(5, conditions=_when(99, "empty")), [URGENT]) == ""
    meta = custom_forms.condition_builder_meta()
    assert meta["operators_by_type"]["checkbox"] == ["equals", "not_equals"]
    assert "file" not in meta["operators_by_type"]


# ---------------------------------------------------------------------------
# API and storage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_create_field_saves_conditions(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=FORM))
    monkeypatch.setattr(forms_repo, "list_fields", AsyncMock(return_value=[URGENT, CALLBACK]))
    create = AsyncMock(return_value=PHONE)
    monkeypatch.setattr(forms_repo, "create_field", create)
    payload = FormFieldCreate(field_type="text", label="Phone", conditions=_when(2, "equals", "Yes"))
    await forms_api.api_create_field(1, payload, ADMIN)
    assert create.await_args.kwargs["conditions"] == {"match": "all", "rules": [{"field_id": 2, "operator": "equals", "value": "Yes"}]}
    assert create.await_args.kwargs["sort_order"] == 2


@pytest.mark.asyncio
async def test_api_create_field_rejects_invalid_conditions(monkeypatch):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=FORM))
    monkeypatch.setattr(forms_repo, "list_fields", AsyncMock(return_value=[URGENT]))
    create = AsyncMock()
    monkeypatch.setattr(forms_repo, "create_field", create)
    with pytest.raises(HTTPException) as exc:
        await forms_api.api_create_field(1, FormFieldCreate(field_type="text", label="Phone", conditions=_when(1, "equals", "Medium")), ADMIN)
    assert exc.value.status_code == 400
    assert exc.value.detail == {"errors": ["Choose one of the choices for 'Urgency'."]}
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, expected",
    [
        (FormFieldUpdate(label="Phone number"), _when(2, "equals", "Yes")),  # omitted: kept
        (FormFieldUpdate(conditions=None), None),  # explicit null: cleared
        (FormFieldUpdate(conditions=_when(1, "equals", "High")), {"match": "all", "rules": [{"field_id": 1, "operator": "equals", "value": "High"}]}),
    ],
)
async def test_api_update_field_conditions(monkeypatch, payload, expected):
    monkeypatch.setattr(forms_repo, "get_form", AsyncMock(return_value=FORM))
    monkeypatch.setattr(forms_repo, "get_field", AsyncMock(return_value=dict(PHONE, placeholder=None, help_text=None)))
    monkeypatch.setattr(forms_repo, "list_fields", AsyncMock(return_value=[URGENT, CALLBACK, PHONE]))
    update = AsyncMock(return_value=PHONE)
    monkeypatch.setattr(forms_repo, "update_field", update)
    await forms_api.api_update_field(1, 3, payload, ADMIN)
    assert update.await_args.kwargs["conditions"] == expected


@pytest.mark.asyncio
async def test_repository_round_trips_conditions(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE custom_forms (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO custom_forms (id) VALUES (1)")
    connection.execute(
        "CREATE TABLE custom_form_fields (id INTEGER PRIMARY KEY AUTOINCREMENT, form_id INT, field_type TEXT, label TEXT, "
        "placeholder TEXT, help_text TEXT, required INT, options TEXT, default_value TEXT, config TEXT, sort_order INT)"
    )
    migration = ROOT / "migrations/503_custom_form_field_conditions.sql"
    assert db._migration_metadata(migration)["phase"] == "expand"
    connection.executescript(db._adapt_sql_for_sqlite(migration.read_text()))

    async def insert(sql, params):
        return connection.execute(sql, params).lastrowid

    async def fetch(sql, params):
        row = connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    async def execute(sql, params):
        connection.execute(sql, params)

    monkeypatch.setattr(forms_repo.db, "execute_returning_lastrowid", insert)
    monkeypatch.setattr(forms_repo.db, "fetch_one", fetch)
    monkeypatch.setattr(forms_repo.db, "execute", execute)
    rule = _when(2, "equals", "Yes")
    created = await forms_repo.create_field(form_id=1, field_type="text", label="Phone", placeholder=None, help_text=None,
                                            required=True, options=None, default_value=None, sort_order=0, conditions=rule)
    assert created["conditions"] == rule
    updated = await forms_repo.update_field(created["id"], field_type="text", label="Phone", placeholder=None, help_text=None,
                                            required=True, options=None, default_value=None, conditions=None)
    assert updated["conditions"] is None
    connection.close()


def test_public_template_exposes_rules_and_defers_required():
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}{% block scripts %}{% endblock %}"}),
        FileSystemLoader(ROOT / "app/templates"),
    ]), autoescape=select_autoescape())
    html = env.get_template("forms/submit.html").render(
        form=FORM, fields=[CALLBACK, PHONE, _field(4, "text", "Name", required=True)], errors=[], static_url=lambda path: path,
    )
    assert 'data-cf-conditions="{&#34;match&#34;: &#34;all&#34;, &#34;rules&#34;: [{&#34;field_id&#34;: 2' in html
    phone = html[html.index('data-field-id="3"'):html.index('data-field-id="4"')]
    name = html[html.index('data-field-id="4"'):]
    assert 'data-required="true"' in phone
    assert " required " not in phone and " required/>" not in phone and " required />" not in phone
    assert " required " in name
