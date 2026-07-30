from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.routes.click_to_call import ClickToCallSettingsUpdate, _public_settings


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


def test_public_settings_never_exposes_encrypted_password():
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
    }
    assert "password_encrypted" not in result
