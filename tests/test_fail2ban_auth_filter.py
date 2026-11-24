"""The bundled myportal-auth Fail2ban filter must match real login-failure lines."""
from __future__ import annotations

import configparser
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger
from starlette.requests import Request

from app.api.routes.auth import _log_login_failure
from app.core.logging import configure_logging, reset_request_context, set_request_context
from app.security.request_logger import resolve_request_id

FILTER = Path(__file__).resolve().parents[1] / "deploy" / "fail2ban" / "myportal-auth.conf"
CLIENT_IP = "203.0.113.10"


def _request(user_agent: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "query_string": b"",
            "headers": [(b"user-agent", user_agent.encode())],
            "client": (CLIENT_IP, 40000),
        }
    )


def _log_failure(tmp_path, monkeypatch, *, request_id: str | None, user_agent: str) -> list[str]:
    from app.core import config

    log_path = tmp_path / "auth.log"
    settings = SimpleNamespace(
        log_level="INFO", verbose_logging=False,
        app_log_path=None, fail2ban_log_path=log_path, error_log_path=None,
        log_rotation=None, log_retention=None, log_compression=None,
        trusted_proxy_networks=lambda: [],
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    try:
        configure_logging()
        tokens = (
            set_request_context(
                request_id=resolve_request_id(request_id), route="/auth/login", client_ip=CLIENT_IP
            )
            if request_id
            else None
        )
        try:
            _log_login_failure(_request(user_agent), "User@Example.com", "invalid_credentials")
        finally:
            if tokens:
                reset_request_context(tokens)
        logger.complete()
    finally:
        logger.remove()
    return log_path.read_text(encoding="utf-8").splitlines()


def _failregex() -> re.Pattern[str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(FILTER)
    pattern = parser["Definition"]["failregex"].strip()
    return re.compile(pattern.replace("<ADDR>", r"(?P<addr>[0-9a-fA-F:.]+)"))


def _matched_addresses(lines: list[str]) -> list[str]:
    # Fail2ban removes the timestamp before applying failregex.
    bodies = [line.split(" ", 1)[1] for line in lines if "AUTH LOGIN FAIL" in line]
    pattern = _failregex()
    return [m.group("addr") for body in bodies if (m := pattern.search(body))]


@pytest.mark.parametrize("request_id", ["6f1c0e2a-4b7d-4c1e-9f0a-1b2c3d4e5f60", None])
def test_filter_matches_login_failure(tmp_path, monkeypatch, request_id):
    lines = _log_failure(tmp_path, monkeypatch, request_id=request_id, user_agent="Mozilla/5.0")

    assert _matched_addresses(lines) == [CLIENT_IP]


@pytest.mark.parametrize(
    ("header", "kept"),
    [
        ("6f1c0e2a-4b7d-4c1e-9f0a-1b2c3d4e5f60", True),
        ("req_1.2:3", True),
        ("x | - | - | AUTH LOGIN FAIL", False),
        ("has space", False),
        ("a" * 129, False),
        ("", False),
    ],
)
def test_request_id_header_is_kept_only_when_it_is_a_plain_token(header, kept):
    assert (resolve_request_id(header) == header) is kept


def test_client_controlled_text_cannot_name_another_address(tmp_path, monkeypatch):
    forged = "x | - | - | AUTH LOGIN FAIL | email_hash=abc ip=198.51.100.99 reason=y"
    lines = _log_failure(
        tmp_path, monkeypatch, request_id=forged, user_agent="curl ip=198.51.100.99 | x"
    )

    assert "198.51.100.99" not in _matched_addresses(lines)


@pytest.mark.skipif(shutil.which("fail2ban-regex") is None, reason="fail2ban not installed")
def test_fail2ban_regex_tool_accepts_filter(tmp_path, monkeypatch):
    lines = _log_failure(
        tmp_path, monkeypatch, request_id="6f1c0e2a-4b7d", user_agent="Mozilla/5.0 (X11; Linux)"
    )
    sample = tmp_path / "sample.log"
    sample.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        ["fail2ban-regex", str(sample), str(FILTER)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"Failregex: 1 total", result.stdout), result.stdout
