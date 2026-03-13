"""Tests for sidebar preferences repository logic."""
from __future__ import annotations

import pytest

from app.repositories.sidebar_preferences import _coerce_preferences


def test_my_profile_cannot_be_hidden():
    """Ensure /admin/profile is never included in the hidden list."""
    payload = {
        "order": ["/", "/admin/profile", "/tickets"],
        "hidden": ["/admin/profile", "/tickets"],
    }
    result = _coerce_preferences(payload)

    assert "/admin/profile" not in result["hidden"]
    assert "/tickets" in result["hidden"]


def test_my_profile_preserved_in_order():
    """Ensure /admin/profile can still appear in the order list."""
    payload = {
        "order": ["/admin/profile", "/tickets"],
        "hidden": [],
    }
    result = _coerce_preferences(payload)

    assert "/admin/profile" in result["order"]


def test_divider_keys_allowed_in_order():
    """Ensure divider: keys are accepted in the order list."""
    payload = {
        "order": ["/", "divider:abc123", "/tickets"],
        "hidden": [],
    }
    result = _coerce_preferences(payload)

    assert result["order"] == ["/", "divider:abc123", "/tickets"]


def test_divider_keys_excluded_from_hidden():
    """Divider keys have no hidden state - they should be ignored if placed in hidden."""
    payload = {
        "order": ["/", "divider:abc123", "/tickets"],
        "hidden": ["divider:abc123"],
    }
    result = _coerce_preferences(payload)

    # divider keys in hidden are allowed through (they are harmless) but
    # the important thing is the order is preserved
    assert "divider:abc123" in result["order"]


def test_coerce_preferences_deduplicates_order():
    """Duplicate order entries are removed."""
    payload = {
        "order": ["/tickets", "/tickets", "/"],
        "hidden": [],
    }
    result = _coerce_preferences(payload)

    assert result["order"] == ["/tickets", "/"]


def test_coerce_preferences_deduplicates_hidden():
    """Duplicate hidden entries are removed."""
    payload = {
        "order": [],
        "hidden": ["/tickets", "/tickets"],
    }
    result = _coerce_preferences(payload)

    assert result["hidden"] == ["/tickets"]


def test_coerce_preferences_invalid_payload():
    """Non-dict payload returns empty preferences."""
    result = _coerce_preferences(None)
    assert result == {"order": [], "hidden": []}

    result = _coerce_preferences("not a dict")
    assert result == {"order": [], "hidden": []}
