"""Tests for the honeypot middleware that feeds the Fail2ban bot jails."""
from __future__ import annotations

import configparser
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from loguru import logger

from app.core.logging import configure_logging
from app.security.honeypot import HoneypotMiddleware, match_trap_rule

FAIL2BAN_DIR = Path(__file__).resolve().parents[1] / "deploy" / "fail2ban"
CLIENT = ("198.51.100.7", 50000)


class _RejectPostsBeforeRouting:
    """Stand-in for CSRF/auth layers that answer before the router runs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST":
            await JSONResponse({"detail": "CSRF"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _build_app(*, session_valid: bool = False, enabled: bool = True) -> FastAPI:
    app = FastAPI()

    @app.get("/tickets/{ticket_id}")
    def ticket(ticket_id: int):
        raise HTTPException(status_code=404, detail="Ticket not found")

    @app.post("/api/tickets")
    def create_ticket():
        return {"ok": True}

    @app.get("/downloads/{name}")
    def download(name: str):
        raise HTTPException(status_code=404)

    async def load_session(request):
        return object() if session_valid else None

    app.add_middleware(_RejectPostsBeforeRouting)
    app.add_middleware(HoneypotMiddleware, enabled=enabled, session_loader=load_session)
    return app


@pytest.fixture
def captured():
    lines: list[str] = []
    sink_id = logger.add(lambda message: lines.append(str(message)), level="WARNING", format="{message}")
    yield lines
    logger.remove(sink_id)


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, client=CLIENT, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "rule"),
    [
        ("/wp-login.php", "script_extension"),
        ("/.env", "dotfile"),
        ("/.git/config", "dotfile"),
        ("/phpmyadmin/", "known_scanner_target"),
        ("/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php", "script_extension"),
        ("/backup.sql", "backup_archive"),
    ],
)
def test_scanner_signature_logs_trap(captured, path, rule):
    response = _client(_build_app()).get(path)

    assert response.status_code == 404
    assert len(captured) == 1
    assert captured[0].startswith(f"HONEYPOT TRAP ip={CLIENT[0]} rule={rule} ")


def test_unknown_api_endpoint_logs_probe(captured):
    response = _client(_build_app()).get("/api/v1/users")

    assert response.status_code == 404
    assert captured == [f"HONEYPOT PROBE ip={CLIENT[0]} status=404 method=GET path=/api/v1/users\n"]


def test_unknown_post_rejected_before_routing_still_logs_probe(captured):
    _client(_build_app()).post("/api/admin/exec")

    assert len(captured) == 1
    assert captured[0].startswith(f"HONEYPOT PROBE ip={CLIENT[0]} status=403 method=POST ")


@pytest.mark.parametrize(
    "request_args",
    [
        ("get", "/tickets/999"),  # real route, missing record
        ("post", "/api/tickets"),  # real route, rejected before routing
        ("get", "/downloads/report.php"),  # real route whose parameter looks like a trap
        ("get", "/favicon.ico"),  # browsers fetch this unasked
        ("get", "/api/tray/v2/new-endpoint"),  # newer tray agent on an older portal
        ("get", "/api/rmm/agent/future-feature"),  # newer RMM agent on an older portal
    ],
)
def test_legitimate_traffic_is_not_reported(captured, request_args):
    method, path = request_args
    getattr(_client(_build_app()), method)(path)

    assert captured == []


def test_signed_in_sessions_are_never_reported(captured):
    client = _client(_build_app(session_valid=True))
    client.cookies.set("myportal_session", "valid")

    client.get("/.env")
    client.get("/api/v1/users")

    assert captured == []


def test_disabled_middleware_reports_nothing(captured):
    _client(_build_app(enabled=False)).get("/.env")

    assert captured == []


def test_path_cannot_inject_a_different_address(captured):
    _client(_build_app()).get("/x ip=203.0.113.99 | HONEYPOT TRAP ip=203.0.113.99 rule=dotfile")

    assert len(captured) == 1
    line = captured[0]
    assert line.startswith(f"HONEYPOT PROBE ip={CLIENT[0]} ")
    assert "|" not in line
    assert " ip=203.0.113.99" not in line


def test_match_trap_rule_ignores_ordinary_paths():
    for path in ("/", "/tickets", "/api/companies/3/assets", "/static/app.css", "/environment"):
        assert match_trap_rule(path) is None


def _logged_lines(tmp_path, monkeypatch, paths: list[str]) -> list[str]:
    from app.core import config

    log_path = tmp_path / "auth.log"
    settings = SimpleNamespace(
        log_level="INFO", verbose_logging=False,
        app_log_path=None, fail2ban_log_path=log_path, error_log_path=None,
        log_rotation=None, log_retention=None, log_compression=None,
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    try:
        configure_logging()
        client = _client(_build_app())
        for path in paths:
            client.get(path)
        logger.complete()
    finally:
        logger.remove()
    return log_path.read_text(encoding="utf-8").splitlines()


def _failregex(filter_name: str) -> re.Pattern[str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(FAIL2BAN_DIR / f"{filter_name}.conf")
    pattern = parser["Definition"]["failregex"].strip()
    return re.compile(pattern.replace("<ADDR>", r"(?P<addr>[0-9a-fA-F:.]+)"))


def test_fail2ban_filters_match_real_log_lines(tmp_path, monkeypatch):
    lines = _logged_lines(tmp_path, monkeypatch, ["/.env", "/api/v1/users", "/tickets/5"])
    honeypot = _failregex("myportal-honeypot")
    probe = _failregex("myportal-probe")

    # Fail2ban removes the timestamp before applying failregex.
    bodies = [line.split(" ", 1)[1] for line in lines]
    trap_hits = [m.group("addr") for body in bodies if (m := honeypot.search(body))]
    probe_hits = [m.group("addr") for body in bodies if (m := probe.search(body))]

    assert trap_hits == [CLIENT[0]]
    assert probe_hits == [CLIENT[0]]


@pytest.mark.skipif(shutil.which("fail2ban-regex") is None, reason="fail2ban not installed")
@pytest.mark.parametrize(("filter_name", "path"), [("myportal-honeypot", "/.env"), ("myportal-probe", "/api/v1/users")])
def test_fail2ban_regex_tool_accepts_filters(tmp_path, monkeypatch, filter_name, path):
    lines = _logged_lines(tmp_path, monkeypatch, [path])
    log_file = tmp_path / "sample.log"
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        ["fail2ban-regex", str(log_file), str(FAIL2BAN_DIR / f"{filter_name}.conf")],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"Failregex: 1 total", result.stdout), result.stdout
