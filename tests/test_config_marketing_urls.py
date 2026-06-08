"""Tests for compliance marketing URL settings validation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _base_env() -> dict[str, str]:
    return {
        "SESSION_SECRET": "test-secret",
        "TOTP_ENCRYPTION_KEY": "test-totp-key",
    }


def test_marketing_urls_accept_relative_paths() -> None:
    with patch.dict(
        os.environ,
        {
            **_base_env(),
            "ESSENTIAL8_COMPLIANCE_MARKETING_URL": "/marketing/essential8-help",
            "BCP_COMPLIANCE_MARKETING_URL": "/marketing/bcp-help",
        },
        clear=True,
    ):
        settings = Settings()
        assert settings.essential8_compliance_marketing_url == "/marketing/essential8-help"
        assert settings.bcp_compliance_marketing_url == "/marketing/bcp-help"


def test_marketing_urls_accept_https_urls() -> None:
    with patch.dict(
        os.environ,
        {
            **_base_env(),
            "ESSENTIAL8_COMPLIANCE_MARKETING_URL": "https://example.com/essential8",
            "BCP_COMPLIANCE_MARKETING_URL": "https://example.com/bcp",
        },
        clear=True,
    ):
        settings = Settings()
        assert settings.essential8_compliance_marketing_url == "https://example.com/essential8"
        assert settings.bcp_compliance_marketing_url == "https://example.com/bcp"


@pytest.mark.parametrize(
    "field,value",
    [
        ("ESSENTIAL8_COMPLIANCE_MARKETING_URL", "javascript:alert(1)"),
        ("ESSENTIAL8_COMPLIANCE_MARKETING_URL", "data:text/html;base64,abcd"),
        ("BCP_COMPLIANCE_MARKETING_URL", "//example.com/marketing"),
        ("BCP_COMPLIANCE_MARKETING_URL", "not-a-url"),
    ],
)
def test_marketing_urls_reject_unsafe_or_invalid_values(field: str, value: str) -> None:
    with patch.dict(
        os.environ,
        {
            **_base_env(),
            "ESSENTIAL8_COMPLIANCE_MARKETING_URL": "/marketing/essential8",
            "BCP_COMPLIANCE_MARKETING_URL": "/marketing/bcp",
            field: value,
        },
        clear=True,
    ):
        with pytest.raises(ValidationError, match="Marketing URL"):
            Settings()
