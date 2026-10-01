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


def test_redact_url_query_masks_token_values():
    from app.core.log_redaction import redact_url_query

    redacted = redact_url_query("/api/integration-modules/uptimekuma/alerts?token=s3cr3t&page=2")
    assert "s3cr3t" not in redacted
    assert "token=" in redacted and "page=2" in redacted
    assert redact_url_query("/plain/path") == "/plain/path"
    assert redact_url_query("/x?page=2") == "/x?page=2"


def test_uvicorn_access_log_redacts_query_tokens():
    import logging

    from app.core import logging as app_logging

    app_logging._configure_uvicorn_access_logging(verbose=True)
    access_logger = logging.getLogger("uvicorn.access")
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "POST", "/api/integration-modules/uptimekuma/alerts?token=s3cr3t", "1.1", 202),
        None,
    )
    assert all(f.filter(record) for f in access_logger.filters)
    assert "s3cr3t" not in record.getMessage()
    # Re-configuring must not stack duplicate filters.
    app_logging._configure_uvicorn_access_logging(verbose=True)
    assert sum(isinstance(f, app_logging._UvicornQuerySecretFilter) for f in access_logger.filters) == 1


def test_tray_websocket_ignores_query_string_token():
    import asyncio

    from app import main as app_main

    closed: list[int] = []

    class FakeWebSocket:
        headers: dict[str, str] = {}
        query_params = {"token": "device-token"}

        async def close(self, code: int = 1000) -> None:
            closed.append(code)

    asyncio.run(app_main.tray_device_socket(FakeWebSocket(), "device-1"))
    assert closed == [4401]
