"""Tests for boolean configuration fields with inline comments."""
import os
import pytest
from unittest.mock import patch


def test_boolean_field_with_inline_comment():
    """Test that boolean fields handle inline comments (e.g., 'true  # comment')."""
    from app.core.config import Settings
    
    with patch.dict(os.environ, {
        "SESSION_SECRET": "test-secret",
        "TOTP_ENCRYPTION_KEY": "test-totp-key",
        "IP_WHITELIST_ADMIN_ONLY": "true  # admin routes only"
    }, clear=True):
        settings = Settings()
        assert settings.ip_whitelist_admin_only is True


def test_boolean_field_false_with_inline_comment():
    """Test that false boolean fields handle inline comments."""
    from app.core.config import Settings
    
    with patch.dict(os.environ, {
        "SESSION_SECRET": "test-secret",
        "TOTP_ENCRYPTION_KEY": "test-totp-key",
        "IP_WHITELIST_ENABLED": "false  # disabled for now"
    }, clear=True):
        settings = Settings()
        assert settings.ip_whitelist_enabled is False


def test_boolean_field_without_comment():
    """Test that boolean fields without comments still work."""
    from app.core.config import Settings
    
    with patch.dict(os.environ, {
        "SESSION_SECRET": "test-secret",
        "TOTP_ENCRYPTION_KEY": "test-totp-key",
        "IP_WHITELIST_ADMIN_ONLY": "true"
    }, clear=True):
        settings = Settings()
        assert settings.ip_whitelist_admin_only is True


def test_multiple_boolean_fields_with_comments():
    """Test that multiple boolean fields with comments work."""
    from app.core.config import Settings
    
    with patch.dict(os.environ, {
        "SESSION_SECRET": "test-secret",
        "TOTP_ENCRYPTION_KEY": "test-totp-key",
        "ENABLE_CSRF": "true  # enable CSRF protection",
        "ENABLE_AUTO_REFRESH": "false  # disable auto refresh",
        "BCP_ENABLED": "true  # business continuity enabled"
    }, clear=True):
        settings = Settings()
        assert settings.enable_csrf is True
        assert settings.enable_auto_refresh is False
        assert settings.bcp_enabled is True
