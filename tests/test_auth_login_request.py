import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest


def _base_payload() -> dict[str, object]:
    return {
        "email": "user@example.com",
        "password": "super-secret",
    }


def test_login_request_accepts_legacy_totp_alias_with_whitespace():
    payload = _base_payload()
    payload["totpCode"] = "123 456"

    request = LoginRequest.model_validate(payload)

    assert request.totp_code == "123456"


def test_login_request_discards_blank_totp_values():
    payload = _base_payload()
    payload["totp_code"] = "   "

    request = LoginRequest.model_validate(payload)

    assert request.totp_code is None


def test_login_request_rejects_non_numeric_totp_tokens():
    payload = _base_payload()
    payload["totp"] = "12ab34"

    with pytest.raises(ValidationError) as exc:
        LoginRequest.model_validate(payload)

    assert "TOTP code must contain only digits" in str(exc.value)
