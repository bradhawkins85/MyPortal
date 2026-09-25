"""Tests for Office 365 Mailbox Import module registration."""
from __future__ import annotations

import pytest

from app.services.modules import DEFAULT_MODULES, _coerce_settings


def test_m365_mail_module_in_default_modules():
    """m365-mail must be registered in DEFAULT_MODULES."""
    slugs = [m["slug"] for m in DEFAULT_MODULES]
    assert "m365-mail" in slugs


def test_m365_mail_module_metadata():
    """m365-mail module entry has required metadata fields."""
    entry = next(m for m in DEFAULT_MODULES if m["slug"] == "m365-mail")
    assert entry["name"]
    assert entry["description"]
    assert entry["icon"]
    assert entry["settings"]["manage_url"] == "/admin/modules/m365-mail"


def test_m365_mail_coerce_settings_default_manage_url():
    """_coerce_settings for m365-mail should default manage_url."""
    result = _coerce_settings("m365-mail", {})
    assert result["manage_url"] == "/admin/modules/m365-mail"


def test_m365_mail_coerce_settings_preserves_custom_manage_url():
    """_coerce_settings for m365-mail should accept a custom manage_url."""
    result = _coerce_settings("m365-mail", {"manage_url": "/custom/path"})
    assert result["manage_url"] == "/custom/path"


def test_m365_mail_coerce_settings_empty_manage_url_defaults():
    """_coerce_settings for m365-mail should default when manage_url is empty."""
    result = _coerce_settings("m365-mail", {"manage_url": ""})
    assert result["manage_url"] == "/admin/modules/m365-mail"
