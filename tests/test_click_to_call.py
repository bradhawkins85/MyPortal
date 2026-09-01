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


@pytest.mark.parametrize("phone_ip", ["localhost", "127.0.0.1", "169.254.1.1"])
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
