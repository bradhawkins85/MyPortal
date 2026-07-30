from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.routes import click_to_call
from app.api.routes.click_to_call import ClickToCallSettingsUpdate, _public_settings


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
