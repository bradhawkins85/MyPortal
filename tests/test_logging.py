"""Tests for logging functionality."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from loguru import logger
from app.core.logging import configure_logging, log_debug, log_error, log_info, log_warning


def test_log_info_without_metadata():
    """Test log_info works without metadata."""
    log_info("Test info message")


def test_log_info_with_metadata():
    """Test log_info works with metadata."""
    log_info("Test info message with metadata", user_id=123, action="test")


def test_log_error_without_metadata():
    """Test log_error works without metadata."""
    log_error("Test error message")


def test_log_error_with_metadata():
    """Test log_error works with metadata."""
    log_error("Test error message with metadata", error_code=500, details="test error")


def test_log_warning_without_metadata():
    """Test log_warning works without metadata."""
    log_warning("Test warning message")


def test_log_warning_with_metadata():
    """Test log_warning works with metadata."""
    log_warning("Test warning message with metadata", reason="rate_limit", ip="127.0.0.1")


def test_log_debug_without_metadata():
    """Test log_debug works without metadata."""
    log_debug("Test debug message")


def test_log_debug_with_metadata():
    """Test log_debug works with metadata."""
    log_debug("Test debug message with metadata", query="SELECT *", duration_ms=25.5)


def test_configure_logging_writes_application_log_one_line_per_entry(tmp_path):
    """Application log file includes feature field and escapes embedded newlines."""
    log_path = tmp_path / "myportal.log"

    from app.core.config import get_settings

    get_settings.cache_clear()
    with patch.dict(
        os.environ,
        {
            "SESSION_SECRET": "test-secret",
            "TOTP_ENCRYPTION_KEY": "test-totp-key",
            "APP_LOG_PATH": str(log_path),
            "LOG_ROTATION": "",
            "LOG_RETENTION": "",
            "LOG_COMPRESSION": "",
        },
    ):
        configure_logging()
        logger.bind(feature="tickets").info("first line\nsecond line")
        logger.complete()

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "| INFO | - | - | tickets |" in lines[0]
    assert "first line\\nsecond line" in lines[0]
    get_settings.cache_clear()
    logger.remove()


def test_default_application_log_rotation_and_retention():
    """Main file log defaults to daily rotation and seven-day retention."""
    from app.core.config import Settings

    with patch.dict(
        os.environ,
        {
            "SESSION_SECRET": "test-secret",
            "TOTP_ENCRYPTION_KEY": "test-totp-key",
        },
        clear=True,
    ):
        settings = Settings()

    assert settings.app_log_path == Path("/var/log/myportal/myportal.log")
    assert settings.log_rotation == "00:00"
    assert settings.log_retention == "7 days"


def test_blank_application_log_path_disables_file_sink():
    """APP_LOG_PATH can be set empty in .env to disable the main file log."""
    from app.core.config import Settings

    with patch.dict(
        os.environ,
        {
            "SESSION_SECRET": "test-secret",
            "TOTP_ENCRYPTION_KEY": "test-totp-key",
            "APP_LOG_PATH": "",
        },
        clear=True,
    ):
        settings = Settings()

    assert settings.app_log_path is None


def test_uvicorn_access_filter_suppresses_heartbeat_when_not_verbose():
    """Normal logging hides uvicorn's high-frequency tray heartbeat access entries."""
    import logging

    from app.core.logging import _configure_uvicorn_access_logging

    access_logger = logging.getLogger("uvicorn.access")
    original_filters = list(access_logger.filters)
    try:
        _configure_uvicorn_access_logging(verbose=False)
        heartbeat_record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1234", "POST", "/api/tray/heartbeat", "1.1", 200),
            None,
        )
        normal_record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1234", "GET", "/api/ping", "1.1", 200),
            None,
        )

        assert not access_logger.filter(heartbeat_record)
        assert access_logger.filter(normal_record)
    finally:
        access_logger.filters = original_filters


def test_uvicorn_access_filter_allows_heartbeat_when_verbose():
    """Verbose logging leaves uvicorn access logs unfiltered for troubleshooting."""
    import logging

    from app.core.logging import _configure_uvicorn_access_logging

    access_logger = logging.getLogger("uvicorn.access")
    original_filters = list(access_logger.filters)
    try:
        _configure_uvicorn_access_logging(verbose=False)
        _configure_uvicorn_access_logging(verbose=True)
        heartbeat_record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1234", "POST", "/api/tray/heartbeat", "1.1", 200),
            None,
        )

        assert access_logger.filter(heartbeat_record)
    finally:
        access_logger.filters = original_filters
