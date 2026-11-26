"""Tests for the read-only Fail2ban status page (/admin/fail2ban)."""

from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from app.features.fail2ban import handlers
from app.features.fail2ban import service

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)


def line(at: datetime, level: str, feature: str, message: str) -> str:
    stamp = at.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    return f"{stamp} | {level} | - | - | {feature} | {message}\n"


def fail(at, ip, reason="invalid_credentials"):
    return line(
        at, "ERROR", "-",
        f"AUTH LOGIN FAIL | client_ip={ip} email_hash=abc123 ip={ip} reason={reason} route=/auth/login "
        "user_agent=curl/8 ip=10.9.9.9",
    )


def trap(at, ip, path="/.env"):
    return line(at, "WARNING", "honeypot", f"HONEYPOT TRAP ip={ip} rule=dotfile status=404 method=GET path={path}")


def probe(at, ip, path="/api/v1/users"):
    return line(at, "WARNING", "honeypot", f"HONEYPOT PROBE ip={ip} status=404 method=GET path={path}")


def success(at, ip):
    return line(at, "INFO", "-", f"AUTH LOGIN SUCCESS | auth_method=password email_hash=abc ip={ip} user_id=1")


def snapshot(generated_at=NOW - timedelta(seconds=30), *, jails=None, fail2ban=None, log_path="/var/log/myportal/auth.log"):
    def jail(name, bans=()):
        return {
            "name": name, "running": True, "currently_failed": 1, "total_failed": 9,
            "currently_banned": len(bans), "total_banned": 4, "files": [log_path],
            "settings": {"bantime": 3600, "findtime": 300, "maxretry": 5,
                         "bantime_increment": False, "bantime_maxtime": None},
            "bans": list(bans), "bans_truncated": False,
        }

    return {
        "version": 1,
        "generated_at": generated_at.isoformat(),
        "fail2ban": fail2ban or {"available": True, "running": True, "error": None, "version": "1.0.2"},
        "jails": jails if jails is not None else [
            jail("myportal-auth", [{"ip": "203.0.113.10", "banned_at": "2026-11-26T11:00:00+00:00",
                                    "ban_seconds": 3600, "unbans_at": "2026-11-26T12:00:00+00:00"}]),
            jail("myportal-honeypot", [{"ip": "198.51.100.7", "banned_at": "2026-11-26T11:30:00+00:00",
                                        "ban_seconds": -1, "unbans_at": None}]),
            jail("myportal-probe"),
        ],
    }


@pytest.fixture
def files(tmp_path):
    log = tmp_path / "auth.log"
    status = tmp_path / "status.json"
    return SimpleNamespace(log=log, status=status)


def write_status(files, data):
    files.status.write_text(json.dumps(data))


def checks(overview):
    return {check["title"]: check["status"] for check in overview["health"]}


def test_parse_log_line_takes_the_first_ip_field_only():
    event = service.parse_log_line(fail(NOW, "203.0.113.10"))
    assert event.kind == service.EVENT_LOGIN_FAIL
    assert event.ip == "203.0.113.10"
    assert event.detail == "invalid credentials"
    assert event.jail == "myportal-auth"

    spoofed = service.parse_log_line(probe(NOW, "198.51.100.1", path="/x_ip=1.2.3.4"))
    assert spoofed.ip == "198.51.100.1"
    assert service.parse_log_line("garbage line") is None
    assert service.parse_log_line(line(NOW, "INFO", "-", "Something else ip=1.2.3.4")) is None


def test_healthy_overview(files):
    files.log.write_text(
        fail(NOW - timedelta(minutes=5), "203.0.113.10")
        + trap(NOW - timedelta(minutes=4), "198.51.100.7")
        + probe(NOW - timedelta(minutes=3), "192.0.2.4")
        + success(NOW - timedelta(minutes=2), "192.0.2.50")
    )
    write_status(files, snapshot(log_path=str(files.log)))

    overview = service.build_overview(files.log, files.status, NOW)

    assert checks(overview) == {
        "The Fail2ban log is being written": "success",
        "The ban snapshot is current": "success",
    }
    assert [jail["label"] for jail in overview["jails"]] == ["Sign-in failures", "Scanner traps", "Unknown pages"]
    assert overview["jails"][0]["rules"] == "Blocks an address after 5 failed sign-ins within 5 minutes, for 1 hour."
    bans = overview["bans"]
    assert [ban["ip"] for ban in bans] == ["198.51.100.7", "203.0.113.10"]
    assert bans[0]["permanent"] is True
    assert bans[0]["reason"] == "Scanner trap: GET /.env"
    assert bans[1]["reason"] == "Failed sign-in: invalid credentials"
    counts = {item["kind"]: item["count"] for item in overview["activity"]["counts"]}
    assert counts == {"login_fail": 1, "trap": 1, "probe": 1, "login_success": 1}
    assert overview["activity"]["addresses"] == 3
    top = {row["ip"]: row for row in overview["activity"]["top_addresses"]}
    assert top["203.0.113.10"]["blocked"] is True
    assert top["192.0.2.4"]["blocked"] is False
    assert overview["activity"]["latest"][0]["label"] == "Signed in"


def test_missing_log_setting_and_snapshot(files):
    overview = service.build_overview(None, files.status, NOW)
    assert checks(overview) == {
        "MyPortal isn't writing a Fail2ban log": "danger",
        "No ban snapshot yet": "danger",
    }
    assert overview["bans"] == []
    assert overview["jails"] == []


def test_stale_snapshot_and_quiet_log(files):
    files.log.write_text(fail(NOW - timedelta(days=2), "203.0.113.10"))
    write_status(files, snapshot(NOW - timedelta(minutes=20), log_path=str(files.log)))
    result = checks(service.build_overview(files.log, files.status, NOW))
    assert result["No events in the last 24 hours"] == "warning"
    assert result["The ban snapshot is out of date"] == "warning"


def test_loopback_addresses_point_at_trusted_proxies(files):
    files.log.write_text("".join(probe(NOW - timedelta(minutes=i), "127.0.0.1") for i in range(12)))
    write_status(files, snapshot(log_path=str(files.log)))
    overview = service.build_overview(files.log, files.status, NOW)
    problem = next(c for c in overview["health"] if "server itself" in c["title"])
    assert problem["status"] == "danger"
    assert "TRUSTED_PROXIES" in problem["detail"]


@pytest.mark.parametrize(
    "fail2ban, title",
    [
        ({"available": False, "running": False, "error": "fail2ban-client is not installed.", "version": None},
         "Fail2ban isn't installed"),
        ({"available": True, "running": False, "error": "Failed to access socket path", "version": None},
         "Fail2ban isn't running"),
    ],
)
def test_fail2ban_down(files, fail2ban, title):
    write_status(files, snapshot(jails=[], fail2ban=fail2ban))
    assert checks(service.build_overview(files.log, files.status, NOW))[title] == "danger"


def test_missing_jail_and_wrong_log_file(files):
    files.log.write_text(fail(NOW - timedelta(minutes=1), "203.0.113.10"))
    data = snapshot(log_path="/var/log/other.log")
    data["jails"] = [jail for jail in data["jails"] if jail["name"] != "myportal-probe"]
    write_status(files, data)
    result = checks(service.build_overview(files.log, files.status, NOW))
    assert result["The Unknown pages jail isn't loaded"] == "warning"
    assert result["The Sign-in failures jail reads a different file"] == "warning"


def test_invalid_and_unreadable_snapshot(files):
    files.status.write_text("{not json")
    assert checks(service.build_overview(None, files.status, NOW))["The ban snapshot couldn't be read"] == "danger"

    files.status.write_text(json.dumps({"version": 99}))
    assert checks(service.build_overview(None, files.status, NOW))["The ban snapshot couldn't be read"] == "danger"

    if os.geteuid() != 0:
        files.status.write_text(json.dumps(snapshot()))
        files.status.chmod(0)
        result = checks(service.build_overview(None, files.status, NOW))
        assert result["MyPortal can't read the ban snapshot"] == "danger"


def test_reads_the_rotated_log_when_the_current_file_is_new(files):
    rotated = files.log.parent / "auth.2026-11-26_00-00-00_000000.log.gz"
    with gzip.open(rotated, "wt") as handle:
        handle.write(trap(NOW - timedelta(hours=20), "198.51.100.7"))
    files.log.write_text(probe(NOW - timedelta(minutes=10), "192.0.2.4"))
    result = service.read_recent_events(files.log, NOW)
    assert [event.kind for event in result.events] == ["trap", "probe"]


def test_tail_reads_only_the_end_of_large_logs(files):
    files.log.write_text("".join(probe(NOW - timedelta(minutes=1), f"192.0.2.{i % 250}") for i in range(200)))
    lines = service._tail_lines(files.log, max_bytes=2000)
    assert 0 < len(lines) < 200
    assert all(service.parse_log_line(item) for item in lines)


@pytest.mark.parametrize(
    "seconds, text",
    [(None, None), (-1, "permanently"), (300, "5 minutes"), (3600, "1 hour"), (86400, "1 day"),
     (2419200, "4 weeks"), (5400, "90 minutes"), (45, "45 seconds")],
)
def test_humanize_seconds(seconds, text):
    assert service.humanize_seconds(seconds) == text


def test_describe_rules_for_first_hit_and_increments():
    info = service.EXPECTED_JAILS["myportal-honeypot"]
    text = service.describe_rules(info, {"maxretry": 1, "findtime": 3600, "bantime": 86400,
                                         "bantime_increment": True, "bantime_maxtime": 2419200})
    assert text == ("Blocks an address on the first of its scanner requests, for 1 day. "
                    "Repeat offenders are blocked for longer each time, up to 4 weeks.")


# --- page -----------------------------------------------------------------

def page_request(query: bytes = b""):
    import app.main as main

    return Request({
        "type": "http", "method": "GET", "scheme": "https", "path": "/admin/fail2ban", "root_path": "",
        "query_string": query, "headers": [], "server": ("portal.test", 443),
        "app": main.app, "router": main.app.router,
    })


@pytest.fixture
def page_env(monkeypatch, files):
    import app.main as main
    from app.core import config

    async def base_context(request, user, *, extra=None):
        context = await main._build_public_context(request, extra=extra)
        context.update(current_user=user, user=user, is_super_admin=True, has_admin_access=True,
                       menu_access={}, current_path=request.url.path)
        return context

    async def admin(request):
        return {"id": 1, "is_super_admin": True, "email": "admin@example.com"}, None

    monkeypatch.setattr(main, "_build_base_context", base_context)
    monkeypatch.setattr(main, "_require_super_admin_page", admin)
    settings = SimpleNamespace(fail2ban_log_path=files.log, fail2ban_status_path=files.status)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    return files


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.asyncio
async def test_page_rejects_non_super_admin(monkeypatch, authenticated):
    import app.main as main

    async def user(request):
        if authenticated:
            return {"id": 2, "is_super_admin": False}, None
        return None, RedirectResponse("/login", status_code=303)

    monkeypatch.setattr(main, "_require_authenticated_user", user)
    if authenticated:
        with pytest.raises(HTTPException) as error:
            await handlers.admin_fail2ban_page(page_request())
        assert error.value.status_code == 403
    else:
        response = await handlers.admin_fail2ban_page(page_request())
        assert response.status_code == 303


@pytest.mark.asyncio
async def test_page_renders_health_jails_bans_and_activity(page_env):
    now = datetime.now(timezone.utc)
    page_env.log.write_text(trap(now - timedelta(minutes=2), "198.51.100.7", path="/<script>alert(1)</script>"))
    write_status(page_env, snapshot(now - timedelta(seconds=10), log_path=str(page_env.log)))

    response = await handlers.admin_fail2ban_page(page_request())
    body = response.body.decode()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "The ban snapshot is current" in body
    assert "Sign-in failures" in body and "Scanner traps" in body and "Unknown pages" in body
    assert "198.51.100.7" in body and "203.0.113.10" in body
    assert "sudo fail2ban-client unban" in body
    assert "<script>alert(1)</script>" not in body
    assert "unbanip" not in body and "<form method=\"post\"" not in body


@pytest.mark.asyncio
async def test_page_filters_bans_by_jail_and_search(page_env):
    write_status(page_env, snapshot(datetime.now(timezone.utc), log_path=str(page_env.log)))

    body = (await handlers.admin_fail2ban_page(page_request(b"jail=myportal-honeypot"))).body.decode()
    assert "<code>198.51.100.7</code>" in body
    assert "<code>203.0.113.10</code>" not in body

    body = (await handlers.admin_fail2ban_page(page_request(b"q=203.0.113"))).body.decode()
    assert "<code>203.0.113.10</code>" in body
    assert "<code>198.51.100.7</code>" not in body

    body = (await handlers.admin_fail2ban_page(page_request(b"jail=sshd"))).body.decode()
    assert "<code>198.51.100.7</code>" in body and "<code>203.0.113.10</code>" in body


@pytest.mark.asyncio
async def test_page_explains_a_missing_setup(page_env):
    response = await handlers.admin_fail2ban_page(page_request())
    body = response.body.decode()
    assert response.status_code == 200
    assert "No ban snapshot yet" in body
    assert "myportal-fail2ban-snapshot.timer" in body
