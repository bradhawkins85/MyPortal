"""Tests for SMTP2Go module registration defaults."""

from __future__ import annotations

from app.services.modules import DEFAULT_MODULES, _coerce_settings


def test_smtp2go_module_metadata_exposes_manage_url():
    entry = next(module for module in DEFAULT_MODULES if module["slug"] == "smtp2go")
    assert entry["settings"]["manage_url"] == "/admin/modules/smtp2go"
    assert entry["settings"]["rate_limit_max_retries"] == 3
    assert entry["settings"]["retry_backoff_seconds"] == 60
    assert entry["settings"]["not_engaged_delay_seconds"] == 86400


def test_smtp2go_coerce_settings_defaults_manage_url_and_campaigns():
    result = _coerce_settings("smtp2go", {})
    assert result["manage_url"] == "/admin/modules/smtp2go"
    assert result["ab_campaigns"] == []


def test_smtp2go_coerce_settings_normalises_numeric_fields():
    result = _coerce_settings(
        "smtp2go",
        {
            "manage_url": "",
            "rate_limit_max_retries": "-5",
            "retry_backoff_seconds": "0",
            "not_engaged_delay_seconds": "abc",
            "ab_campaigns": {"invalid": True},
        },
    )
    assert result["manage_url"] == "/admin/modules/smtp2go"
    assert result["rate_limit_max_retries"] == 0
    assert result["retry_backoff_seconds"] == 1
    assert result["not_engaged_delay_seconds"] == 86400
    assert result["ab_campaigns"] == []
