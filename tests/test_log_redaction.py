"""Tests for :mod:`app.core.log_redaction`."""

from __future__ import annotations

from app.core.log_redaction import redact_headers, redact_mapping


def test_redact_headers_masks_authorization_and_cookie():
    result = redact_headers(
        {
            "Authorization": "Bearer abc",
            "Cookie": "session=xyz",
            "X-API-Key": "secret",
            "Content-Type": "application/json",
            "User-Agent": "pytest",
        }
    )
    assert result["Authorization"] == "[REDACTED]"
    assert result["Cookie"] == "[REDACTED]"
    assert result["X-API-Key"] == "[REDACTED]"
    assert result["Content-Type"] == "application/json"
    assert result["User-Agent"] == "pytest"


def test_redact_headers_masks_csrf_and_set_cookie():
    result = redact_headers(
        {
            "Set-Cookie": "session=xyz; HttpOnly",
            "X-CSRF-Token": "tok",
        }
    )
    assert result["Set-Cookie"] == "[REDACTED]"
    assert result["X-CSRF-Token"] == "[REDACTED]"


def test_redact_mapping_masks_sensitive_keys_recursively():
    data = {
        "email": "user@example.com",
        "password": "hunter2",
        "nested": {
            "api_key": "sensitive",
            "totp_code": "123456",
            "value": 42,
        },
        "items": [{"access_token": "xxx", "name": "ok"}],
    }
    redacted = redact_mapping(data)
    assert redacted["email"] == "user@example.com"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert redacted["nested"]["totp_code"] == "[REDACTED]"
    assert redacted["nested"]["value"] == 42
    assert redacted["items"][0]["access_token"] == "[REDACTED]"
    assert redacted["items"][0]["name"] == "ok"


def test_redact_headers_handles_none():
    assert redact_headers(None) == {}
