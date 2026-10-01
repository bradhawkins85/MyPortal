from __future__ import annotations

from pathlib import Path
import re
import subprocess

import pytest
from pydantic import ValidationError

from app.api.routes import click_to_call
from app.api.routes.click_to_call import ClickToCallSettingsUpdate, _public_settings


def test_click_to_call_phone_pattern_requires_valid_leading_boundary():
    javascript = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "click_to_call.js"
    ).read_text(encoding="utf-8")
    pattern = re.search(r"const PHONE_PATTERN = (/.*?/g);", javascript)

    assert pattern is not None
    result = subprocess.run(
        [
            "node",
            "-e",
            """
const pattern = %s;
const values = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(values.map((value) => value.match(pattern))));
""" % pattern.group(1),
            """[
                "2026-08-31T21:26:07.979-04:00",
                "reference-0412345678",
                "reference:0412345678",
                "Call 0412 345 678",
                "+61 412 345 678",
                "0412 345 678"
            ]""",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == (
        '[["2026-08-31"],null,null,["0412 345 678"],'
        '["+61 412 345 678"],["0412 345 678"]]'
    )


def test_click_to_call_migration_matches_user_id_type():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "307_user_click_to_call_settings.sql"
    ).read_text(encoding="utf-8")

    assert "user_id INT NOT NULL PRIMARY KEY" in migration
    assert "user_id BIGINT" not in migration


def test_click_to_call_settings_accept_private_phone_ip():
    payload = ClickToCallSettingsUpdate(
        enabled=True,
        phone_ip="192.168.1.50",
        login_username="admin",
        password="secret",
    )

    assert payload.phone_ip == "192.168.1.50"
    assert payload.login_username == "admin"


@pytest.mark.parametrize(
    "phone_ip",
    ["localhost", "127.0.0.1", "169.254.1.1", "8.8.8.8", "100.64.0.1", "::1", "fe80::1", "2001:4860::8888", "0.0.0.0"],
)
def test_click_to_call_settings_reject_unsafe_phone_ip(phone_ip):
    with pytest.raises(ValidationError):
        ClickToCallSettingsUpdate(phone_ip=phone_ip)


def test_public_settings_never_exposes_encrypted_password(monkeypatch):
    monkeypatch.setattr(
        click_to_call,
        "get_app_settings",
        lambda: type("Settings", (), {"click_to_call_phone_prefixes": "+61, 04"})(),
    )
    result = _public_settings(
        {
            "enabled": 1,
            "phone_ip": "10.0.0.20",
            "login_username": "phone-user",
            "password_encrypted": "encrypted-value",
        }
    )

    assert result == {
        "enabled": True,
        "phone_ip": "10.0.0.20",
        "login_username": "phone-user",
        "password_configured": True,
        "phone_prefixes": ["+61", "04"],
    }
    assert "password_encrypted" not in result


def test_public_settings_ignores_empty_phone_prefixes(monkeypatch):
    monkeypatch.setattr(
        click_to_call,
        "get_app_settings",
        lambda: type(
            "Settings", (), {"click_to_call_phone_prefixes": " +61, , 617, "}
        )(),
    )

    assert _public_settings(None)["phone_prefixes"] == ["+61", "617"]


@pytest.mark.parametrize("phone_ip", ["10.1.2.3", "172.16.0.9", "192.168.0.2", "fd12:3456::1"])
def test_click_to_call_settings_accept_rfc1918_and_ula(phone_ip):
    assert ClickToCallSettingsUpdate(phone_ip=phone_ip).phone_ip == phone_ip


def _run_make_call(monkeypatch, settings, handler):
    import asyncio

    import httpx
    from fastapi import HTTPException

    calls: list[httpx.Request] = []

    def transport_handler(request):
        calls.append(request)
        return handler(request)

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(transport_handler)
        return real_client(*args, **kwargs)

    async def fake_get_settings(user_id):
        return settings

    monkeypatch.setattr(click_to_call.click_to_call_repo, "get_settings", fake_get_settings)
    monkeypatch.setattr(click_to_call.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(click_to_call, "decrypt_secret", lambda value: "pw")

    async def run():
        return await click_to_call.make_call(
            click_to_call.MakeCallRequest(phone_number="0412 345 678"),
            None,
            {"id": 1},
        )

    try:
        return asyncio.run(run()), calls
    except HTTPException as exc:
        return exc, calls


def test_make_call_refuses_legacy_public_phone_ip(monkeypatch):
    import httpx

    settings = {"enabled": 1, "phone_ip": "8.8.8.8", "login_username": "u", "password_encrypted": "x"}
    result, calls = _run_make_call(monkeypatch, settings, lambda r: httpx.Response(200))
    assert getattr(result, "status_code", None) == 502
    assert calls == []


def test_make_call_does_not_follow_redirects_and_hides_details(monkeypatch):
    import httpx

    settings = {"enabled": 1, "phone_ip": "192.168.1.50", "login_username": "u", "password_encrypted": "x"}
    result, calls = _run_make_call(
        monkeypatch, settings, lambda r: httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    )
    assert getattr(result, "status_code", None) == 502
    assert result.detail == "The Grandstream phone could not be reached"
    assert [str(c.url.host) for c in calls] == ["192.168.1.50"]
    assert calls[0].url.path == "/cgi-bin/api-make_call"


def test_make_call_success_returns_only_ok(monkeypatch):
    import httpx

    settings = {"enabled": 1, "phone_ip": "192.168.1.50", "login_username": "u", "password_encrypted": "x"}
    result, _ = _run_make_call(monkeypatch, settings, lambda r: httpx.Response(200, text="secret body"))
    assert result == {"ok": True, "phone_number": "0412345678"}
