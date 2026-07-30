"""Delegated Microsoft Graph contact lookup for an individual technician."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import httpx

from app.repositories import user_m365_contacts as contacts_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import m365 as m365_service

CONTACTS_SCOPE = "openid profile email offline_access https://graph.microsoft.com/Contacts.Read"
GRAPH_CONTACTS_URL = (
    "https://graph.microsoft.com/v1.0/me/contacts"
    "?$select=displayName,givenName,surname,mobilePhone,businessPhones,homePhones&$top=100"
)


async def status_for_user(user_id: int) -> dict[str, Any]:
    record = await contacts_repo.get_integration(user_id)
    return {"connected": bool(record), "account_email": record.get("account_email") if record else None}


async def store_tokens(user_id: int, *, tenant_id: str, account_email: str | None,
                       refresh_token: str, access_token: str, expires_at: datetime | None) -> None:
    await contacts_repo.upsert_integration(
        user_id, tenant_id=tenant_id, account_email=account_email,
        refresh_token=encrypt_secret(refresh_token), access_token=encrypt_secret(access_token),
        token_expires_at=expires_at,
    )


async def acquire_access_token(user_id: int) -> str:
    record = await contacts_repo.get_integration(user_id)
    if not record:
        raise ValueError("Microsoft 365 contacts are not connected")
    expires = record.get("token_expires_at")
    if record.get("access_token") and expires and expires > datetime.now(timezone.utc) + timedelta(minutes=5):
        return decrypt_secret(record["access_token"])
    data = {
        "client_id": await m365_service.get_effective_pkce_client_id(),
        "grant_type": "refresh_token",
        "refresh_token": decrypt_secret(record["refresh_token"]),
        "scope": CONTACTS_SCOPE,
    }
    url = f"https://login.microsoftonline.com/{record['tenant_id']}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, data=data)
    if response.status_code != 200:
        raise ValueError("Microsoft 365 sign-in expired; reconnect it from your profile")
    payload = response.json()
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise ValueError("Microsoft 365 did not return an access token")
    expires_in = payload.get("expires_in")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in or 3600))
    refresh_token = str(payload.get("refresh_token") or decrypt_secret(record["refresh_token"]))
    await store_tokens(user_id, tenant_id=record["tenant_id"], account_email=record.get("account_email"),
                       refresh_token=refresh_token, access_token=access_token, expires_at=expires_at)
    return access_token


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def match_contact_phones(requester_name: str, contacts: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Return de-duplicated phones from contacts whose name matches the requester."""
    wanted = _words(requester_name)
    if not wanted:
        return []
    matches: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for contact in contacts:
        display_name = str(contact.get("displayName") or "").strip()
        contact_words = _words(display_name or f"{contact.get('givenName', '')} {contact.get('surname', '')}")
        overlap = len(wanted & contact_words)
        if not overlap or (len(wanted) > 1 and overlap < len(wanted)):
            continue
        phones = [contact.get("mobilePhone"), *(contact.get("businessPhones") or []), *(contact.get("homePhones") or [])]
        for phone in phones:
            value = str(phone or "").strip()
            key = re.sub(r"\D", "", value)
            if not value or not key or key in seen:
                continue
            seen.add(key)
            matches.append((overlap, display_name or requester_name, value))
    matches.sort(key=lambda item: (-item[0], item[1].casefold(), item[2]))
    return [{"name": name, "phone": phone} for _, name, phone in matches[:10]]


async def lookup_phones(user_id: int, requester_name: str) -> list[dict[str, str]]:
    token = await acquire_access_token(user_id)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(GRAPH_CONTACTS_URL, headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise ValueError("Unable to read Outlook contacts")
    return match_contact_phones(requester_name, response.json().get("value") or [])
