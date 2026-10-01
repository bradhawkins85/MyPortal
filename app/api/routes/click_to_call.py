from __future__ import annotations

import ipaddress
import re

import httpx

from app.services.monitored_http import monitored_client
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.core.config import get_settings as get_app_settings
from app.repositories import click_to_call as click_to_call_repo
from app.security.encryption import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/api/click-to-call", tags=["Users"])


# Desk phones live on the customer LAN.  The phone IP is configured by each
# user on their own profile, so it is untrusted input: restrict it to private
# (RFC1918 / IPv6 unique-local) addresses so the server cannot be pointed at
# public hosts, loopback services, or link-local cloud metadata endpoints.
_ALLOWED_PHONE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


def _is_allowed_phone_ip(value: str | None) -> bool:
    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return False
    return any(address.version == net.version and address in net for net in _ALLOWED_PHONE_NETWORKS)


def _phone_host(value: str) -> str:
    address = ipaddress.ip_address(value.strip())
    return f"[{address}]" if address.version == 6 else str(address)


class ClickToCallSettingsUpdate(BaseModel):
    enabled: bool = False
    phone_ip: str | None = Field(default=None, max_length=255)
    login_username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)

    @field_validator("phone_ip")
    @classmethod
    def validate_phone_ip(cls, value: str | None) -> str | None:
        value = str(value or "").strip() or None
        if value is None:
            return None
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("Enter a valid phone IP address") from exc
        if not _is_allowed_phone_ip(str(address)):
            raise ValueError("The phone IP address must be a private (LAN) address")
        return value

    @field_validator("login_username", "password")
    @classmethod
    def strip_optional_values(cls, value: str | None) -> str | None:
        return str(value or "").strip() or None


class MakeCallRequest(BaseModel):
    phone_number: str = Field(min_length=3, max_length=40)


def _public_settings(record: dict | None) -> dict[str, object]:
    record = record or {}
    return {
        "enabled": bool(record.get("enabled")),
        "phone_ip": record.get("phone_ip") or "",
        "login_username": record.get("login_username") or "",
        "password_configured": bool(record.get("password_encrypted")),
        "phone_prefixes": [
            prefix.strip()
            for prefix in get_app_settings().click_to_call_phone_prefixes.split(",")
            if prefix.strip()
        ],
    }


@router.get("/settings")
async def get_settings(
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    record = await click_to_call_repo.get_settings(int(current_user["id"]))
    return _public_settings(record)


@router.put("/settings")
async def update_settings(
    payload: ClickToCallSettingsUpdate,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    existing = await click_to_call_repo.get_settings(int(current_user["id"]))
    has_password = bool(payload.password or (existing or {}).get("password_encrypted"))
    if payload.enabled and not (payload.phone_ip and payload.login_username and has_password):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Phone IP, login username, and password are required when click to call is enabled",
        )
    saved = await click_to_call_repo.upsert_settings(
        int(current_user["id"]),
        enabled=payload.enabled,
        phone_ip=payload.phone_ip,
        login_username=payload.login_username,
        password_encrypted=encrypt_secret(payload.password) if payload.password else None,
    )
    return _public_settings(saved)


@router.post("/call")
async def make_call(
    payload: MakeCallRequest,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    number = re.sub(r"[^0-9+]", "", payload.phone_number)
    if not re.fullmatch(r"\+?[0-9]{3,15}", number):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid phone number")

    settings = await click_to_call_repo.get_settings(int(current_user["id"]))
    if not settings or not settings.get("enabled"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Click to call is not enabled")
    unreachable = HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail="The Grandstream phone could not be reached"
    )
    # Re-check at call time: values saved before this rule existed must not be
    # used to reach public, loopback or link-local addresses.
    if not _is_allowed_phone_ip(settings.get("phone_ip")):
        raise unreachable
    try:
        password = decrypt_secret(str(settings.get("password_encrypted") or ""))
        # verify=False is deliberate: Grandstream desk phones ship with
        # self-signed certificates and are only reachable on private LAN
        # addresses (enforced above).  Redirects are never followed, the path is
        # fixed, and the caller only learns success or a generic failure.
        async with monitored_client(
            httpx.AsyncClient, verify=False, timeout=10.0, follow_redirects=False
        ) as client:
            response = await client.get(
                f"https://{_phone_host(str(settings['phone_ip']))}/cgi-bin/api-make_call",
                params={
                    "phonenumber": number,
                    "account": 0,
                    "login": settings["login_username"],
                    "password": password,
                },
            )
        if not 200 <= response.status_code < 300:
            raise unreachable
    except (httpx.HTTPError, ValueError, KeyError):
        raise unreachable from None
    return {"ok": True, "phone_number": number}
