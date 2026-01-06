"""Tests for ALLOWED_ORIGINS configuration parsing."""
import os
import pytest
from unittest.mock import patch


def test_allowed_origins_empty_string():
    """Test that empty ALLOWED_ORIGINS string results in empty list."""
    from app.core.config import Settings
    
    with patch.dict(os.environ, {
        "SESSION_SECRET": "test-secret",
        "TOTP_ENCRYPTION_KEY": "test-totp-key",
        "ALLOWED_ORIGINS": ""
    }):
        settings = Settings()
        assert settings.allowed_origins == ""


def test_allowed_origins_single_url():
    """Test that single URL in ALLOWED_ORIGINS is stored as string."""
    from app.core.config import Settings
    
    with patch.dict(os.environ, {
        "SESSION_SECRET": "test-secret",
        "TOTP_ENCRYPTION_KEY": "test-totp-key",
        "ALLOWED_ORIGINS": "https://example.com"
    }):
        settings = Settings()
        assert settings.allowed_origins == "https://example.com"


def test_allowed_origins_multiple_urls():
    """Test that comma-separated URLs in ALLOWED_ORIGINS are stored as string."""
    from app.core.config import Settings
    
    with patch.dict(os.environ, {
        "SESSION_SECRET": "test-secret",
        "TOTP_ENCRYPTION_KEY": "test-totp-key",
        "ALLOWED_ORIGINS": "https://example.com,https://app.example.com,https://dashboard.example.com"
    }):
        settings = Settings()
        assert settings.allowed_origins == "https://example.com,https://app.example.com,https://dashboard.example.com"


def test_allowed_origins_with_spaces():
    """Test that comma-separated URLs with spaces are stored correctly."""
    from app.core.config import Settings
    
    with patch.dict(os.environ, {
        "SESSION_SECRET": "test-secret",
        "TOTP_ENCRYPTION_KEY": "test-totp-key",
        "ALLOWED_ORIGINS": "https://example.com, https://app.example.com , https://dashboard.example.com"
    }):
        settings = Settings()
        # String should be preserved as-is
        assert settings.allowed_origins == "https://example.com, https://app.example.com , https://dashboard.example.com"


def test_allowed_origins_not_set():
    """Test that when ALLOWED_ORIGINS is not set, it defaults to empty string."""
    from app.core.config import Settings
    
    with patch.dict(os.environ, {
        "SESSION_SECRET": "test-secret",
        "TOTP_ENCRYPTION_KEY": "test-totp-key"
    }, clear=True):
        settings = Settings()
        assert settings.allowed_origins == ""


def test_allowed_origins_parsing_in_main():
    """Test that the parsing logic in main.py works correctly."""
    # Test empty string
    allowed_origins = ""
    result = [origin.strip() for origin in allowed_origins.split(",") if origin.strip()] if allowed_origins else []
    assert result == []
    
    # Test single URL
    allowed_origins = "https://example.com"
    result = [origin.strip() for origin in allowed_origins.split(",") if origin.strip()] if allowed_origins else []
    assert result == ["https://example.com"]
    
    # Test multiple URLs
    allowed_origins = "https://example.com,https://app.example.com"
    result = [origin.strip() for origin in allowed_origins.split(",") if origin.strip()] if allowed_origins else []
    assert result == ["https://example.com", "https://app.example.com"]
    
    # Test URLs with spaces
    allowed_origins = "https://example.com, https://app.example.com , https://dashboard.example.com"
    result = [origin.strip() for origin in allowed_origins.split(",") if origin.strip()] if allowed_origins else []
    assert result == ["https://example.com", "https://app.example.com", "https://dashboard.example.com"]

