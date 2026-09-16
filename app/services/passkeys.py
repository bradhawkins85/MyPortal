from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, options_to_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.config import Settings, get_settings

PASSKEY_CHALLENGE_TTL_SECONDS = 300


def _bytes_to_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def bytes_to_base64url(value: bytes) -> str:
    return _bytes_to_base64url(value)


def base64url_to_bytes_safe(value: str) -> bytes:
    return base64url_to_bytes(value)


def _normalise_name(value: str | None, *, fallback: str) -> str:
    if value and value.strip():
        return value.strip()
    return fallback


def credential_id_hash(credential_id: str) -> str:
    return hashlib.sha256(credential_id.encode("utf-8")).hexdigest()[:12]


def browser_binding_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_browser_binding_token() -> str:
    return secrets.token_urlsafe(32)


def relying_party_id(settings: Settings | None = None) -> str:
    active_settings = settings or get_settings()
    explicit = active_settings.passkey_rp_id.strip()
    if explicit:
        return explicit
    if active_settings.portal_url:
        return urlsplit(active_settings.portal_url.unicode_string()).hostname or "localhost"
    return "localhost"


def relying_party_name(settings: Settings | None = None) -> str:
    active_settings = settings or get_settings()
    return _normalise_name(active_settings.passkey_rp_name, fallback=active_settings.app_name)


def allowed_origins(settings: Settings | None = None) -> list[str]:
    active_settings = settings or get_settings()
    return active_settings.passkey_allowed_origin_list()


def user_display_name(user: dict[str, Any]) -> str:
    full_name = " ".join(
        part.strip() for part in (str(user.get("first_name") or ""), str(user.get("last_name") or "")) if part.strip()
    ).strip()
    return full_name or str(user.get("email") or "MyPortal user")


def generate_user_handle() -> str:
    return _bytes_to_base64url(secrets.token_bytes(32))


def generate_challenge_pair() -> tuple[str, str, datetime]:
    return secrets.token_hex(16), _bytes_to_base64url(secrets.token_bytes(32)), datetime.utcnow() + timedelta(
        seconds=PASSKEY_CHALLENGE_TTL_SECONDS
    )


def parse_transports(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, list):
        return []
    supported = {item.value for item in AuthenticatorTransport}
    return [str(item) for item in value if str(item) in supported]


def registration_options(
    *,
    user: dict[str, Any],
    user_handle: str,
    existing_credentials: list[dict[str, Any]],
    settings: Settings | None = None,
) -> dict[str, Any]:
    active_settings = settings or get_settings()
    challenge_id, challenge, expires_at = generate_challenge_pair()
    exclude_credentials = [
        PublicKeyCredentialDescriptor(
            id=base64url_to_bytes(str(record["credential_id"])),
            transports=[
                AuthenticatorTransport(transport)
                for transport in parse_transports(record.get("transports"))
            ]
            or None,
        )
        for record in existing_credentials
        if record.get("credential_id")
    ]
    options = generate_registration_options(
        rp_id=relying_party_id(active_settings),
        rp_name=relying_party_name(active_settings),
        user_name=str(user.get("email") or ""),
        user_id=base64url_to_bytes(user_handle),
        user_display_name=user_display_name(user),
        challenge=base64url_to_bytes(challenge),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=exclude_credentials,
    )
    return {
        "challenge_id": challenge_id,
        "challenge": challenge,
        "expires_at": expires_at,
        "public_key": json.loads(options_to_json(options)),
    }


def authentication_options(*, settings: Settings | None = None) -> dict[str, Any]:
    active_settings = settings or get_settings()
    challenge_id, challenge, expires_at = generate_challenge_pair()
    options = generate_authentication_options(
        rp_id=relying_party_id(active_settings),
        challenge=base64url_to_bytes(challenge),
        allow_credentials=None,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return {
        "challenge_id": challenge_id,
        "challenge": challenge,
        "expires_at": expires_at,
        "public_key": json.loads(options_to_json(options)),
    }


def verify_registration(
    *,
    credential: dict[str, Any],
    expected_challenge: str,
    settings: Settings | None = None,
):
    active_settings = settings or get_settings()
    return verify_registration_response(
        credential=credential,
        expected_challenge=base64url_to_bytes(expected_challenge),
        expected_rp_id=relying_party_id(active_settings),
        expected_origin=allowed_origins(active_settings),
        require_user_verification=True,
    )


def verify_authentication(
    *,
    credential: dict[str, Any],
    expected_challenge: str,
    public_key: bytes,
    sign_count: int,
    settings: Settings | None = None,
):
    active_settings = settings or get_settings()
    return verify_authentication_response(
        credential=credential,
        expected_challenge=base64url_to_bytes(expected_challenge),
        expected_rp_id=relying_party_id(active_settings),
        expected_origin=allowed_origins(active_settings),
        credential_public_key=public_key,
        credential_current_sign_count=sign_count,
        require_user_verification=True,
    )
