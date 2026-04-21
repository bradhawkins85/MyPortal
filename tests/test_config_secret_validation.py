"""Tests for production-only weak secret validation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "SESSION_SECRET": "a" * 48 + "BCDEFGHIJKL",
        "TOTP_ENCRYPTION_KEY": "b" * 48 + "MNOPQRSTUVW",
        "DB_HOST": "localhost",
        "DB_USER": "user",
        "DB_PASSWORD": "password",
        "DB_NAME": "testdb",
    }
    env.update(overrides)
    return env


def test_weak_secret_accepted_outside_production():
    """Development / test environments keep the lax defaults."""
    from app.core.config import Settings

    env = _base_env(SESSION_SECRET="change-me", ENVIRONMENT="development")
    with patch.dict(os.environ, env, clear=False):
        settings = Settings()
        assert settings.secret_key == "change-me"


def test_placeholder_session_secret_rejected_in_production():
    from app.core.config import Settings

    env = _base_env(SESSION_SECRET="change-me", ENVIRONMENT="production")
    with patch.dict(os.environ, env, clear=False):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
    assert "SESSION_SECRET" in str(excinfo.value)


def test_short_totp_key_rejected_in_production():
    from app.core.config import Settings

    env = _base_env(TOTP_ENCRYPTION_KEY="short", ENVIRONMENT="production")
    with patch.dict(os.environ, env, clear=False):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
    assert "TOTP_ENCRYPTION_KEY" in str(excinfo.value)


def test_low_entropy_secret_rejected_in_production():
    """Secrets made of only a handful of unique chars are rejected."""
    from app.core.config import Settings

    env = _base_env(SESSION_SECRET="a" * 64, ENVIRONMENT="production")
    with patch.dict(os.environ, env, clear=False):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
    assert "SESSION_SECRET" in str(excinfo.value)


def test_strong_secrets_accepted_in_production():
    from app.core.config import Settings

    env = _base_env(ENVIRONMENT="production")
    with patch.dict(os.environ, env, clear=False):
        settings = Settings()
        assert settings.is_production() is True


def test_mcp_token_required_when_mcp_enabled_in_production():
    from app.core.config import Settings

    env = _base_env(ENVIRONMENT="production", MCP_ENABLED="true", MCP_TOKEN="change-me")
    with patch.dict(os.environ, env, clear=False):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
    assert "MCP_TOKEN" in str(excinfo.value)


def test_trusted_proxy_networks_parses_mixed_entries():
    from app.core.config import Settings

    env = _base_env(TRUSTED_PROXIES="127.0.0.1, 10.0.0.0/8, not-an-ip")
    with patch.dict(os.environ, env, clear=False):
        settings = Settings()
        networks = settings.trusted_proxy_networks()
        # Bad entries are skipped silently.
        assert len(networks) == 2


def test_hsts_effective_defaults_on_in_production():
    from app.core.config import Settings

    env = _base_env(ENVIRONMENT="production")
    # Make sure ENABLE_HSTS is not explicitly set.
    env.pop("ENABLE_HSTS", None)
    with patch.dict(os.environ, env, clear=False):
        # Ensure the env has no ENABLE_HSTS key
        if "ENABLE_HSTS" in os.environ:
            del os.environ["ENABLE_HSTS"]
        settings = Settings()
        assert settings.hsts_effective() is True


def test_hsts_effective_respects_explicit_disable_in_production():
    from app.core.config import Settings

    env = _base_env(ENVIRONMENT="production", ENABLE_HSTS="false")
    with patch.dict(os.environ, env, clear=False):
        settings = Settings()
        assert settings.hsts_effective() is False
