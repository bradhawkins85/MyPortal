"""Tests for staff_field_config service – pure helpers and form validation."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.staff_field_config import (
    _parse_bool,
    _to_datetime,
    _parse_options_text,
    validate_staff_form_values,
)


# ---------------------------------------------------------------------------
# _parse_bool
# ---------------------------------------------------------------------------


def test_parse_bool_none_returns_false():
    assert _parse_bool(None) is False


def test_parse_bool_true_bool():
    assert _parse_bool(True) is True


def test_parse_bool_false_bool():
    assert _parse_bool(False) is False


def test_parse_bool_truthy_strings():
    for value in ("1", "true", "True", "TRUE", "t", "T", "yes", "YES", "y", "Y", "on", "ON"):
        assert _parse_bool(value) is True, f"expected True for {value!r}"


def test_parse_bool_falsy_strings():
    for value in ("0", "false", "False", "no", "off", "  false  "):
        assert _parse_bool(value) is False, f"expected False for {value!r}"


def test_parse_bool_unknown_string_returns_false():
    assert _parse_bool("maybe") is False
    assert _parse_bool("nope") is False
    assert _parse_bool("") is False


def test_parse_bool_integer_truthy():
    # Only integer 1 maps to truthy (via str coercion "1")
    assert _parse_bool(1) is True


def test_parse_bool_integer_non_one_returns_false():
    # Integers other than 0 or 1 are coerced to strings like "2" which is
    # not in the truthy set, so they return False
    assert _parse_bool(2) is False
    assert _parse_bool(-1) is False


def test_parse_bool_integer_zero_via_string():
    # Integer 0 is coerced to "0" which maps to False
    assert _parse_bool(0) is False


# ---------------------------------------------------------------------------
# _to_datetime
# ---------------------------------------------------------------------------


def test_to_datetime_none_returns_none():
    assert _to_datetime(None) is None


def test_to_datetime_empty_string_returns_none():
    assert _to_datetime("") is None
    assert _to_datetime("   ") is None


def test_to_datetime_iso_date_only():
    result = _to_datetime("2024-06-15")
    assert result is not None
    assert result.year == 2024
    assert result.month == 6
    assert result.day == 15
    assert result.tzinfo == timezone.utc


def test_to_datetime_iso_datetime_with_timezone():
    result = _to_datetime("2024-06-15T10:30:00+10:00")
    assert result is not None
    assert result.tzinfo == timezone.utc
    # 10:30 AEST = 00:30 UTC
    assert result.hour == 0
    assert result.minute == 30


def test_to_datetime_iso_datetime_naive_assumes_utc():
    result = _to_datetime("2024-06-15T10:30:00")
    assert result is not None
    assert result.tzinfo == timezone.utc
    assert result.year == 2024
    assert result.hour == 10


def test_to_datetime_invalid_string_returns_none():
    assert _to_datetime("not-a-date") is None
    assert _to_datetime("31/12/2024") is None
    assert _to_datetime("12-2024") is None


def test_to_datetime_returns_datetime_instance():
    result = _to_datetime("2024-01-01")
    assert isinstance(result, datetime)


# ---------------------------------------------------------------------------
# _parse_options_text
# ---------------------------------------------------------------------------


def test_parse_options_text_empty():
    assert _parse_options_text("") == []
    assert _parse_options_text(None) == []


def test_parse_options_text_simple_values():
    result = _parse_options_text("red, green, blue")
    assert result == [
        {"value": "red", "label": "red"},
        {"value": "green", "label": "green"},
        {"value": "blue", "label": "blue"},
    ]


def test_parse_options_text_with_label_override():
    result = _parse_options_text("yes:Yes I agree, no:No thanks")
    assert result == [
        {"value": "yes", "label": "Yes I agree"},
        {"value": "no", "label": "No thanks"},
    ]


def test_parse_options_text_value_only_colon_uses_value_as_label():
    result = _parse_options_text("opt:")
    # Empty label after colon → falls back to value
    assert result == [{"value": "opt", "label": "opt"}]


def test_parse_options_text_skips_blank_entries():
    result = _parse_options_text("red, , blue")
    assert len(result) == 2
    values = [o["value"] for o in result]
    assert "red" in values
    assert "blue" in values


def test_parse_options_text_strips_whitespace():
    result = _parse_options_text("  red  ,  green  ")
    assert result[0]["value"] == "red"
    assert result[1]["value"] == "green"


def test_parse_options_text_multiple_colons_splits_on_first():
    # "a:b:c" → value="a", label="b:c"
    result = _parse_options_text("a:b:c")
    assert result == [{"value": "a", "label": "b:c"}]


# ---------------------------------------------------------------------------
# validate_staff_form_values
# ---------------------------------------------------------------------------


def _field(key, label=None, field_type="text", required=False, options=None, validation_metadata=None):
    return {
        "key": key,
        "label": label or key.replace("_", " ").title(),
        "type": field_type,
        "required": required,
        "options": options or [],
        "validation_metadata": validation_metadata or {},
    }


def test_validate_text_field_simple():
    config = [_field("first_name", "First Name", required=True)]
    values, errors = validate_staff_form_values({"first_name": "Alice"}, config)
    assert values["first_name"] == "Alice"
    assert errors == []


def test_validate_required_text_missing():
    config = [_field("first_name", "First Name", required=True)]
    _, errors = validate_staff_form_values({}, config)
    assert errors
    assert any("required" in e.lower() or "First Name" in e for e in errors)


def test_validate_optional_text_empty_no_error():
    config = [_field("bio", "Bio", required=False)]
    values, errors = validate_staff_form_values({"bio": ""}, config)
    assert errors == []
    assert values["bio"] == ""


def test_validate_checkbox_truthy():
    config = [_field("is_active", "Is Active", field_type="checkbox")]
    values, errors = validate_staff_form_values({"is_active": "1"}, config)
    assert values["is_active"] is True
    assert errors == []


def test_validate_checkbox_falsy():
    config = [_field("is_active", "Is Active", field_type="checkbox")]
    values, errors = validate_staff_form_values({"is_active": "0"}, config)
    assert values["is_active"] is False
    assert errors == []


def test_validate_date_valid():
    config = [_field("start_date", "Start Date", field_type="date")]
    values, errors = validate_staff_form_values({"start_date": "2024-06-15"}, config)
    assert errors == []
    assert isinstance(values["start_date"], datetime)


def test_validate_date_invalid():
    config = [_field("start_date", "Start Date", field_type="date")]
    _, errors = validate_staff_form_values({"start_date": "not-a-date"}, config)
    assert errors
    assert any("valid date" in e.lower() for e in errors)


def test_validate_date_empty_not_required():
    config = [_field("end_date", "End Date", field_type="date", required=False)]
    values, errors = validate_staff_form_values({"end_date": ""}, config)
    assert errors == []
    assert values["end_date"] == ""


def test_validate_select_valid_option():
    opts = [{"value": "admin", "label": "Admin"}, {"value": "user", "label": "User"}]
    config = [_field("role", "Role", field_type="select", options=opts)]
    values, errors = validate_staff_form_values({"role": "admin"}, config)
    assert errors == []
    assert values["role"] == "admin"


def test_validate_select_invalid_option():
    opts = [{"value": "admin", "label": "Admin"}]
    config = [_field("role", "Role", field_type="select", options=opts)]
    _, errors = validate_staff_form_values({"role": "superuser"}, config)
    assert errors


def test_validate_multiselect_valid_options():
    opts = [
        {"value": "a", "label": "A"},
        {"value": "b", "label": "B"},
    ]
    config = [_field("tags", "Tags", field_type="multiselect", options=opts)]
    values, errors = validate_staff_form_values({"tags": ["a", "b"]}, config)
    assert errors == []
    assert "a" in values["tags"]
    assert "b" in values["tags"]


def test_validate_multiselect_invalid_option():
    opts = [{"value": "a", "label": "A"}]
    config = [_field("tags", "Tags", field_type="multiselect", options=opts)]
    _, errors = validate_staff_form_values({"tags": ["a", "z"]}, config)
    assert errors


def test_validate_max_length_exceeded():
    config = [_field("bio", "Bio", validation_metadata={"max_length": 10})]
    _, errors = validate_staff_form_values({"bio": "x" * 11}, config)
    assert errors
    assert any("maximum length" in e.lower() for e in errors)


def test_validate_max_length_not_exceeded():
    config = [_field("bio", "Bio", validation_metadata={"max_length": 10})]
    values, errors = validate_staff_form_values({"bio": "x" * 10}, config)
    assert errors == []


def test_validate_email_format_valid():
    config = [
        _field("email", "Email", validation_metadata={"format": "email"})
    ]
    values, errors = validate_staff_form_values(
        {"email": "test@example.com"}, config
    )
    assert errors == []
    assert values["email"] == "test@example.com"


def test_validate_email_format_invalid():
    config = [
        _field("email", "Email", validation_metadata={"format": "email"})
    ]
    _, errors = validate_staff_form_values({"email": "not-an-email"}, config)
    assert errors
    assert any("email" in e.lower() for e in errors)


def test_validate_multiple_fields_multiple_errors():
    config = [
        _field("first_name", "First Name", required=True),
        _field("last_name", "Last Name", required=True),
    ]
    _, errors = validate_staff_form_values({}, config)
    assert len(errors) >= 2
