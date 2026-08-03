"""Tests for labour type form handling in the ticket admin."""

from app.features.tickets.admin_routes import _is_default_labour_type


def test_existing_labour_type_is_matched_by_database_id():
    assert _is_default_labour_type("42", "42", 0) is True
    assert _is_default_labour_type("43", "42", 1) is False


def test_new_labour_type_is_matched_by_row_marker():
    assert _is_default_labour_type("", "new-1", 1) is True
    assert _is_default_labour_type("", "new-1", 0) is False


def test_empty_radio_value_does_not_select_new_labour_type():
    assert _is_default_labour_type("", "", 0) is False
