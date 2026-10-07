from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest
from starlette.datastructures import FormData

from app.core.database import Database
from app.repositories import client_onboarding_questions as questions_repo
from app.services import client_onboarding_questions as questions


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _question(field_type="short_text", **overrides):
    value = {
        "id": 7, "label": "Additional information", "field_type": field_type,
        "section": "business", "help_text": "", "required": False,
        "options": ["Email", "Phone", "SMS"], "display_order": 0,
    }
    value.update(overrides)
    return value


def _parse(value, field_type="short_text", **overrides):
    values = value if isinstance(value, list) else [value]
    form = FormData([("custom-7", item) for item in values])
    return questions.parse_answers(form, [_question(field_type, **overrides)], sections=["business"])


@pytest.mark.parametrize(
    ("field_type", "value", "expected", "display"),
    [
        ("short_text", " Call after lunch ", "Call after lunch", "Call after lunch"),
        ("long_text", "First line\nSecond line", "First line\nSecond line", "First line\nSecond line"),
        ("email", " Jo@Example.com ", "jo@example.com", "jo@example.com"),
        ("phone", "+61 (7) 3000-0000 ext. 123", "+61 (7) 3000-0000 ext. 123", "+61 (7) 3000-0000 ext. 123"),
        ("date", "2028-02-29", "2028-02-29", "2028-02-29"),
        ("dropdown", "Email", "Email", "Email"),
        ("multi_select", ["SMS", "Email", "SMS"], ["SMS", "Email"], "SMS, Email"),
        ("checkbox", "on", True, "Yes"),
        ("radio", "Phone", "Phone", "Phone"),
        ("number", "-1.25e2", "-125", "-125"),
        ("url", "https://example.com/contact?x=1", "https://example.com/contact?x=1", "https://example.com/contact?x=1"),
        ("time", "09:30", "09:30", "09:30"),
        ("time", "09:30:45", "09:30:45", "09:30:45"),
        ("datetime_local", "2028-02-29T09:30", "2028-02-29T09:30", "2028-02-29T09:30"),
        ("datetime_local", "2028-02-29T09:30:45", "2028-02-29T09:30:45", "2028-02-29T09:30:45"),
    ],
)
def test_all_types_produce_serializable_answer_snapshots(field_type, value, expected, display):
    answers, errors = _parse(value, field_type, required=True)
    assert not errors
    assert answers == [{
        "id": 7, "label": "Additional information", "field_type": field_type,
        "section": "business", "value": expected, "display_value": display,
    }]


@pytest.mark.parametrize("field_type", questions.FIELD_TYPES)
def test_required_answers_cannot_be_missing(field_type):
    answers, errors = questions.parse_answers(
        FormData(), [_question(field_type, required=True)], sections=["business"],
    )
    assert not answers
    assert len(errors) == 1
    assert errors[0].startswith("Your business: Additional information: ")


@pytest.mark.parametrize("field_type", questions.FIELD_TYPES)
def test_optional_empty_answers_are_retained(field_type):
    answers, errors = questions.parse_answers(FormData(), [_question(field_type)], sections=["business"])
    assert not errors
    assert answers[0]["display_value"] == ("No" if field_type == "checkbox" else "Not provided")


@pytest.mark.parametrize(
    ("field_type", "value"),
    [
        ("email", "user@@example.com"), ("email", "user@example..com"),
        ("email", "user@example"), ("email", "bad name@example.com"),
        ("phone", "call me tomorrow"), ("phone", "++61 700000000"), ("phone", "12"),
        ("date", "2027-02-29"), ("date", "2026-04-31"), ("date", "20261007"),
        ("date", "2026-13-01"), ("dropdown", "Unknown"),
        ("multi_select", ["Email", "Unknown"]), ("radio", "Unknown"),
        ("checkbox", "maybe"), ("number", "NaN"), ("number", "Infinity"),
        ("number", "1e101"), ("number", "1e9999999999999999999999999"),
        ("number", "twelve"), ("number", "1_000"),
        ("url", "javascript:alert(1)"), ("url", "https://"),
        ("url", "https://bad host.example"), ("url", "https://example.com:99999/"),
        ("url", "https://user:password@example.com/"),
        ("time", "24:00"), ("time", "12:60"), ("time", "12:30Z"),
        ("datetime_local", "2026-04-31T09:30"),
        ("datetime_local", "2026-10-07T09:30Z"),
        ("short_text", "hello\x00world"), ("short_text", "line one\nline two"),
    ],
)
def test_invalid_answers_are_rejected_even_when_optional(field_type, value):
    answers, errors = _parse(value, field_type)
    assert not answers
    assert len(errors) == 1


@pytest.mark.parametrize("field_type", ["short_text", "long_text", "email", "phone", "url", "number"])
def test_oversized_answers_are_rejected_without_truncation(field_type):
    value = "x" * (questions.ANSWER_LIMITS[field_type] + 1)
    answers, errors = _parse(value, field_type)
    assert not answers
    assert "characters" in errors[0]
    state = questions.answer_state(FormData([("custom-7", value)]), [_question(field_type)])
    assert state["7"] == value


def test_required_tick_box_means_checked():
    for value in ("", "0", "off", "false", "no"):
        assert _parse(value, "checkbox", required=True)[1]
    for value in ("1", "on", "true", "yes"):
        assert _parse(value, "checkbox", required=True)[0][0]["value"] is True


def test_multiple_scalar_answers_are_rejected():
    assert _parse(["first", "second"])[1]
    assert _parse(["false", "true"], "checkbox")[1]


def test_site_answers_are_isolated_and_form_sections_are_filtered():
    definitions = [
        _question(section="site_address", required=True),
        _question(id=8, section="billing", required=True),
    ]
    form = FormData([
        ("site-0-custom-7", "Ground floor"), ("site-1-custom-7", "Third floor"),
        ("custom-7", "Wrong scope"),
    ])
    first, errors = questions.parse_answers(form, definitions, sections=["site_address"], prefix="site-0-")
    second, other_errors = questions.parse_answers(form, definitions, sections=["site_address"], prefix="site-1-")
    missing, missing_errors = questions.parse_answers(form, definitions, sections=["site_address"], prefix="site-2-")
    assert not errors and not other_errors
    assert first[0]["value"] == "Ground floor"
    assert second[0]["value"] == "Third floor"
    assert not missing and missing_errors[0].startswith("address: Additional information: ")


def test_invalid_values_and_per_site_multi_select_state_survive_rerender():
    definitions = [_question("email"), _question("multi_select", id=8), _question("checkbox", id=9)]
    form = FormData([
        ("site-1-custom-7", " bad@value "),
        ("site-1-custom-8", "Unknown"), ("site-1-custom-8", "Email"),
        ("site-1-custom-9", "invalid"), ("site-2-custom-8", "SMS"),
    ])
    assert questions.answer_state(form, definitions, prefix="site-1-") == {
        "7": " bad@value ", "8": ["Unknown", "Email"], "9": "invalid",
    }
    assert questions.answer_state(form, definitions, prefix="site-2-")["8"] == ["SMS"]
    assert questions.answer_state(None, definitions) == {"7": "", "8": [], "9": False}


def test_snapshot_is_independent_of_later_configuration_edits():
    definition = _question("multi_select")
    before = deepcopy(definition)
    answers, errors = questions.parse_answers(
        FormData([("custom-7", "Email")]), [definition], sections=["business"],
    )
    assert not errors and definition == before
    definition["label"] = "Changed label"
    definition["options"].clear()
    assert answers[0]["label"] == "Additional information"
    assert answers[0]["value"] == ["Email"]


def test_question_groups_include_empty_sections_and_stable_order():
    grouped = questions.group_questions([
        _question(id=8, display_order=2), _question(id=9, display_order=1),
        _question(id=7, display_order=2), _question(id=10, section="site_contact"),
    ])
    assert list(grouped) == list(questions.SECTIONS)
    assert [item["id"] for item in grouped["business"]] == [9, 7, 8]
    assert grouped["review"] == []


def test_question_configuration_normalises_newline_options_and_required_flag():
    data, errors = questions.validate_question({
        "label": " Contact preference ", "field_type": "multi_select", "section": "site_contact",
        "help_text": " Choose all that apply. ", "required": "on",
        "options": "Email\r\n\nPhone\n SMS ", "display_order": "20",
    })
    assert not errors
    assert data == {
        "label": "Contact preference", "field_type": "multi_select", "section": "site_contact",
        "help_text": "Choose all that apply.", "required": True,
        "options": ["Email", "Phone", "SMS"], "display_order": 20,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"label": ""}, {"label": "x" * 256}, {"label": "Two\nlines"},
        {"field_type": "unsafe_html"}, {"section": "unknown"},
        {"help_text": "x" * 2001}, {"required": "maybe"},
        {"display_order": "-1"}, {"display_order": "1.5"}, {"display_order": "10001"},
        {"field_type": "dropdown", "options": []},
        {"field_type": "radio", "options": ["Duplicate", "Duplicate"]},
        {"field_type": "multi_select", "options": ["x" * 256]},
        {"field_type": "dropdown", "options": [str(i) for i in range(101)]},
    ],
)
def test_invalid_configuration_cannot_be_saved(overrides):
    definition = _question()
    definition.update(overrides)
    assert questions.validate_question(definition)[1]


def test_text_fields_discard_unused_choice_options():
    data, errors = questions.validate_question(_question())
    assert not errors
    assert data["options"] == []


class _SQLiteCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid

    async def fetchone(self):
        return self.cursor.fetchone()

    async def fetchall(self):
        return self.cursor.fetchall()


class _SQLiteConnection:
    """Exercise Database's SQLite queries without an external worker thread."""
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row

    async def execute(self, sql, params=()):
        return _SQLiteCursor(self.connection.execute(sql, params))

    async def commit(self):
        self.connection.commit()

    async def close(self):
        self.connection.close()


@pytest.fixture
async def question_database(monkeypatch):
    database = Database()
    database._use_sqlite = True
    database._sqlite_conn = _SQLiteConnection()
    migration = Path("migrations/463_client_onboarding_questions.sql").read_text(encoding="utf-8")
    for statement in database._split_sql_statements(database._adapt_sql_for_sqlite(migration)):
        await database.execute(statement)
    monkeypatch.setattr(questions_repo, "db", database)
    try:
        yield database
    finally:
        await database.disconnect()


@pytest.mark.anyio
async def test_migration_and_question_crud_preserve_ids_and_snapshots(question_database):
    first = await questions.save_question(None, _question("dropdown", label="Preferred channel", display_order=2))
    second = await questions.save_question(None, _question(label="Business notes", display_order=1))
    assert first["id"] != second["id"]
    assert first["options"] == ["Email", "Phone", "SMS"]
    assert first["required"] is False
    assert [item["id"] for item in await questions.list_questions()] == [second["id"], first["id"]]
    answers, errors = questions.parse_answers(
        {f"custom-{first['id']}": "Email"}, [first], sections=["business"],
    )
    assert not errors
    updated = await questions.save_question(first["id"], _question("long_text", label="New label", required=True))
    assert updated["id"] == first["id"] and updated["required"] is True
    assert updated["options"] == []
    assert await questions_repo.get_question(first["id"]) == updated
    assert await questions.delete_question(first["id"]) is True
    assert await questions.delete_question(first["id"]) is False
    assert await questions_repo.get_question(first["id"]) is None
    assert answers[0]["label"] == "Preferred channel" and answers[0]["value"] == "Email"
    assert await questions.save_question(999, _question()) is None


@pytest.mark.anyio
async def test_service_refuses_invalid_configuration_before_database_write(monkeypatch):
    async def unexpected_save(*args, **kwargs):
        pytest.fail("Invalid configuration reached the database")

    monkeypatch.setattr(questions_repo, "save_question", unexpected_save)
    with pytest.raises(ValueError, match="supported field type"):
        await questions.save_question(None, _question("bad_type"))
