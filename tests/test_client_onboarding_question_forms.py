"""Custom form questions: the client adds repeating entries of sub-questions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from starlette.datastructures import FormData

from app.repositories import client_onboarding_questions as questions_repo
from app.services import client_onboarding_questions as questions
from tests.test_client_onboarding_questions import question_database  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


FIELDS = [
    {"key": "f1", "label": "Stakeholder name", "field_type": "short_text", "required": True, "options": []},
    {"key": "f2", "label": "Phone", "field_type": "phone", "required": False, "options": []},
    {"key": "f3", "label": "Role", "field_type": "dropdown", "required": False, "options": ["Owner", "Director"]},
]


def _question(**overrides):
    value = {
        "id": 5, "label": "Other stakeholders in the business", "field_type": "form",
        "section": "business", "help_text": "", "required": False, "options": [],
        "display_order": 0, "form": {"entry_label": "Stakeholder", "fields": FIELDS},
    }
    value.update(overrides)
    return value


def _parse(pairs, **overrides):
    return questions.parse_answers(FormData(pairs), [_question(**overrides)], sections=["business"])


def test_form_type_is_offered_with_its_own_sub_question_types():
    assert "form" in questions.FIELD_TYPES
    assert "form" not in questions.FORM_FIELD_TYPES
    assert set(questions.FORM_FIELD_TYPES) <= set(questions.ANSWER_LIMITS)


def test_editor_rows_become_a_keyed_definition_and_blank_rows_are_ignored():
    form = FormData([
        ("label", "Key software vendors"), ("field_type", "form"), ("section", "business"),
        ("required", "1"), ("display_order", "4"), ("form_entry_label", " Vendor "),
        ("form_field_label", "Application"), ("form_field_type", "short_text"),
        ("form_field_required", "1"), ("form_field_options", ""),
        ("form_field_label", ""), ("form_field_type", "short_text"),
        ("form_field_required", "0"), ("form_field_options", ""),
        ("form_field_label", "Support contract"), ("form_field_type", "dropdown"),
        ("form_field_required", "0"), ("form_field_options", "Yes\nNo\n"),
    ])
    data, errors = questions.validate_question(form)
    assert not errors
    assert data["options"] == []
    assert data["form"] == {
        "entry_label": "Vendor",
        "fields": [
            {"key": "f1", "label": "Application", "field_type": "short_text", "required": True, "options": []},
            {"key": "f2", "label": "Support contract", "field_type": "dropdown", "required": False,
             "options": ["Yes", "No"]},
        ],
    }
    # A validated definition re-validates unchanged (the service validates twice).
    again, errors = questions.validate_question(data)
    assert not errors and again == data


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([], "Add at least one question to the custom form."),
        ([("Role", "dropdown", "0", "")], "add at least one dropdown option"),
        ([("Role", "checkbox", "0", "")], "choose a supported answer type"),
        ([("Role", "dropdown", "0", "A\nA")], "each option must be unique"),
        ([("Name", "short_text", "maybe", "")], "choose whether an answer is required"),
        ([(f"Q{index}", "short_text", "0", "") for index in range(21)], "no more than 20 questions"),
    ],
)
def test_invalid_form_definitions_are_rejected(rows, message):
    pairs = [("label", "Stakeholders"), ("field_type", "form"), ("section", "business")]
    for label, field_type, required, options in rows:
        pairs += [("form_field_label", label), ("form_field_type", field_type),
                  ("form_field_required", required), ("form_field_options", options)]
    _, errors = questions.validate_question(FormData(pairs))
    assert any(message in error for error in errors), errors


def test_non_form_questions_do_not_store_a_form_definition():
    data, errors = questions.validate_question({
        "label": "Notes", "field_type": "short_text", "form_field_label": "Ignored",
    })
    assert not errors and "form" not in data


def test_entries_are_validated_snapshotted_and_blank_entries_dropped():
    answers, errors = _parse([
        ("custom-5-entry", "0"), ("custom-5-entry", "3"), ("custom-5-entry", "4"),
        ("custom-5-0-f1", " Jo Smith "), ("custom-5-0-f2", "07 3000 0000"), ("custom-5-0-f3", "Owner"),
        ("custom-5-3-f1", ""), ("custom-5-3-f2", " "), ("custom-5-3-f3", ""),
        ("custom-5-4-f1", "Sam Lee"),
    ])
    assert not errors
    answer = answers[0]
    assert answer["field_type"] == "form" and answer["entry_label"] == "Stakeholder"
    assert [[cell["value"] for cell in entry] for entry in answer["value"]] == [
        ["Jo Smith", "07 3000 0000", "Owner"], ["Sam Lee", "", ""],
    ]
    assert answer["value"][1][1]["display_value"] == "Not provided"
    assert answer["display_value"] == (
        "Stakeholder 1\nStakeholder name: Jo Smith\nPhone: 07 3000 0000\nRole: Owner\n\n"
        "Stakeholder 2\nStakeholder name: Sam Lee\nPhone: Not provided\nRole: Not provided"
    )
    json.dumps(answers)


def test_sub_question_errors_name_the_entry_and_question():
    _, errors = _parse([
        ("custom-5-entry", "0"), ("custom-5-entry", "1"),
        ("custom-5-0-f1", "Jo"), ("custom-5-0-f3", "Chairperson"),
        ("custom-5-1-f2", "call me"),
    ])
    assert errors == [
        "Your business: Other stakeholders in the business: Stakeholder 1: Role: select one of the available choices.",
        "Your business: Other stakeholders in the business: Stakeholder 2: Stakeholder name: provide an answer.",
        "Your business: Other stakeholders in the business: Stakeholder 2: Phone: enter a valid phone number.",
    ]


def test_required_form_needs_one_entry_and_optional_form_may_be_empty():
    _, errors = _parse([("custom-5-entry", "0"), ("custom-5-0-f1", "")], required=True)
    assert errors == ["Your business: Other stakeholders in the business: add at least one stakeholder."]
    answers, errors = _parse([])
    assert not errors
    assert answers[0]["value"] == [] and answers[0]["display_value"] == "Not provided"


@pytest.mark.parametrize(
    "tokens",
    [["0", "0"], ["abc"], ["1234567"], [str(index) for index in range(51)]],
)
def test_tampered_or_excessive_entries_are_rejected(tokens):
    pairs = [("custom-5-entry", token) for token in tokens] + [("custom-5-0-f1", "Jo")]
    answers, errors = _parse(pairs)
    assert not answers and len(errors) == 1


def test_answer_state_keeps_entries_for_rerender():
    form = FormData([
        ("custom-5-entry", "2"), ("custom-5-entry", "bad"),
        ("custom-5-2-f1", "Jo"), ("custom-5-2-f3", "Unknown"),
    ])
    state = questions.answer_state(form, [_question()])
    assert state == {"5": [{"token": "2", "values": {"f1": "Jo", "f2": "", "f3": "Unknown"}}]}
    assert questions.answer_state(None, [_question()]) == {"5": []}


@pytest.mark.anyio
async def test_form_definition_round_trips_through_the_database(question_database):  # noqa: F811
    saved = await questions.save_question(None, {
        "label": "Stakeholders", "field_type": "form", "section": "business", "required": "1",
        "form": {"entry_label": "Stakeholder", "fields": FIELDS},
    })
    assert saved["form"] == {"entry_label": "Stakeholder", "fields": FIELDS}
    plain = await questions.save_question(saved["id"], {"label": "Notes", "field_type": "short_text"})
    assert plain["form"] is None
    assert (await questions_repo.get_question(saved["id"]))["form"] is None


def test_migration_adds_the_form_definition_column():
    sql = (ROOT / "migrations" / "562_client_onboarding_question_forms.sql").read_text(encoding="utf-8")
    assert sql.startswith("-- phase: expand")
    assert "ALTER TABLE client_onboarding_questions ADD COLUMN IF NOT EXISTS form_json LONGTEXT NULL" in sql


def _env():
    return Environment(loader=FileSystemLoader(ROOT / "app" / "templates"), autoescape=True)


def test_client_form_renders_entries_template_and_add_button():
    template = _env().from_string(
        '{% from "onboarding/_custom_questions.html" import custom_questions %}'
        "{{ custom_questions(questions, answers, prefix) }}"
    )
    html = template.render(
        questions=[_question(required=True, label="<b>Stakeholders</b>")], prefix="site-1-",
        answers={"5": [{"token": "7", "values": {"f1": "Jo <Smith>", "f2": "", "f3": "Owner"}}]},
    )
    assert 'data-cof-custom-type="form"' in html
    assert 'name="site-1-custom-5-entry" value="7"' in html
    assert 'name="site-1-custom-5-7-f1"' in html and 'value="Jo &lt;Smith&gt;"' in html
    assert '<option value="Owner" selected>' in html
    assert 'name="site-1-custom-5-__ENTRY__-f1"' in html
    assert "+ Add stakeholder" in html
    assert "&lt;b&gt;Stakeholders&lt;/b&gt;" in html
    blank = template.render(questions=[_question()], prefix="", answers={})
    assert 'name="custom-5-entry" value="0"' in blank


def test_staff_view_shows_entries_as_a_table():
    answers, _ = _parse([("custom-5-entry", "0"), ("custom-5-0-f1", "Jo <Smith>"), ("custom-5-0-f3", "Owner")])
    template = _env().from_string(
        '{% from "admin/_client_onboarding_answers.html" import answer_list %}{{ answer_list(answers) }}'
    )
    html = template.render(answers=answers)
    assert "<table" in html and '<th scope="col">Stakeholder name</th>' in html
    assert "Jo &lt;Smith&gt;" in html and "<td" in html
    empty, _ = _parse([])
    assert "Not provided" in template.render(answers=empty)


def test_admin_editor_preserves_form_rows_after_a_failed_save():
    from tests.test_client_onboarding_questions_admin import _render

    html = _render(question_errors=["Form question 1: enter a label."], question_form={
        "label": "Vendors", "field_type": "form", "section": "business",
        "form": questions.posted_form_definition(FormData([
            ("form_entry_label", "Vendor"),
            ("form_field_label", "Vendor <name>"), ("form_field_type", "phone"),
            ("form_field_required", "1"), ("form_field_options", ""),
        ])),
    })
    assert 'name="form_entry_label"' in html and 'value="Vendor"' in html
    assert 'value="Vendor &lt;name&gt;"' in html
    assert '<option value="phone" selected>' in html
    assert '<option value="1" selected>Required</option>' in html
    assert "data-coq-form-template" in html
