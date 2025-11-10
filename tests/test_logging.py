"""Tests for logging functionality."""

import pytest
from app.core.logging import log_debug, log_error, log_info, log_warning


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
