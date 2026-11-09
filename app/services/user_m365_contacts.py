"""Delegated Microsoft Graph contact lookup for an individual technician."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from app.services.monitored_http import monitored_client

from app.repositories import user_m365_contacts as contacts_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import m365 as m365_service

CONTACTS_SCOPE = "openid profile email offline_access https://graph.microsoft.com/Contacts.ReadWrite"
GRAPH_CONTACTS_BASE_URL = "https://graph.microsoft.com/v1.0/me/contacts"
GRAPH_CONTACTS_URL = (
    "https://graph.microsoft.com/v1.0/me/contacts"
    "?$select=displayName,givenName,surname,mobilePhone,businessPhones,homePhones&$top=100"
)


def tenant_id_from_token_response(payload: Mapping[str, Any]) -> str:
    """Return the tenant from an OAuth response without assuming Graph tokens are JWTs."""
    id_token = str(payload.get("id_token") or "")
    access_token = str(payload.get("access_token") or "")
    for token in (id_token, access_token):
        if not token:
            continue
        try:
            return m365_service.extract_tenant_id_from_token(token)
        except (TypeError, ValueError):
            continue
    raise ValueError("Microsoft did not return a usable account identity")


async def status_for_user(user_id: int) -> dict[str, Any]:
    record = await contacts_repo.get_integration(user_id)
    return {"connected": bool(record), "account_email": record.get("account_email") if record else None}


async def store_tokens(user_id: int, *, tenant_id: str, account_email: str | None,
                       refresh_token: str, access_token: str, expires_at: datetime | None,
                       client_id: str | None = None, account_id: str | None = None,
                       scopes: str | None = None, connection_version: int | None = None,
                       expected_revision: int | None = None) -> None:
    await contacts_repo.upsert_integration(
        user_id, tenant_id=tenant_id, account_email=account_email,
        refresh_token=encrypt_secret(refresh_token), access_token=encrypt_secret(access_token),
        token_expires_at=expires_at,
        oauth_client_id=client_id,
        oauth_authority=f"https://login.microsoftonline.com/{tenant_id}",
        oauth_account_id=account_id, oauth_scopes=scopes,
        oauth_connection_version=connection_version, expected_revision=expected_revision,
    )


class ReconnectRequired(ValueError):
    """The stored Outlook connection predates write access to contacts."""


def scopes_allow_write(scopes: str | None) -> bool:
    return any(
        scope.rsplit("/", 1)[-1].casefold() == "contacts.readwrite"
        for scope in str(scopes or "").split()
    )


async def acquire_access_token(user_id: int, *, require_write: bool = False) -> str:
    record = await contacts_repo.get_integration(user_id)
    if not record:
        raise ValueError("Microsoft 365 contacts are not connected")
    if require_write and not scopes_allow_write(record.get("oauth_scopes")):
        raise ReconnectRequired(
            "Reconnect Outlook contacts from your profile to let MyPortal save contacts to Outlook"
        )
    expires = record.get("token_expires_at")
    if record.get("access_token") and expires and expires > datetime.now(timezone.utc) + timedelta(minutes=5):
        return decrypt_secret(record["access_token"])
    data = {
        "client_id": record.get("oauth_client_id") or await m365_service.get_effective_pkce_client_id(),
        "grant_type": "refresh_token",
        "refresh_token": decrypt_secret(record["refresh_token"]),
        "scope": record.get("oauth_scopes") or CONTACTS_SCOPE,
    }
    url = f"https://login.microsoftonline.com/{record['tenant_id']}/oauth2/v2.0/token"
    async with monitored_client(httpx.AsyncClient, timeout=30) as client:
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
                       refresh_token=refresh_token, access_token=access_token, expires_at=expires_at,
                       expected_revision=int(record.get("token_revision") or 0))
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
    contacts: list[Mapping[str, Any]] = []
    next_url: str | None = GRAPH_CONTACTS_URL
    visited_urls: set[str] = set()
    async with monitored_client(httpx.AsyncClient, timeout=30) as client:
        while next_url and next_url not in visited_urls:
            visited_urls.add(next_url)
            response = await client.get(next_url, headers={"Authorization": f"Bearer {token}"})
            if response.status_code != 200:
                raise ValueError("Unable to read Outlook contacts")
            payload = response.json()
            contacts.extend(payload.get("value") or [])
            next_url = payload.get("@odata.nextLink")
    return match_contact_phones(requester_name, contacts)


def build_outlook_contact(details: Mapping[str, Any]) -> dict[str, Any]:
    """Map MyPortal requester details to a Graph contact body.

    Only values MyPortal knows are included, so updating an existing Outlook
    contact never blanks fields the technician added in Outlook.
    """

    def text(key: str) -> str:
        return str(details.get(key) or "").strip()

    first_name, last_name, email = text("first_name"), text("last_name"), text("email")
    display_name = " ".join(part for part in (first_name, last_name) if part) or email
    contact: dict[str, Any] = {}
    if display_name:
        contact["displayName"] = display_name
    if first_name:
        contact["givenName"] = first_name
    if last_name:
        contact["surname"] = last_name
    if email:
        contact["emailAddresses"] = [{"address": email, "name": display_name or email}]
    if text("mobile_phone"):
        contact["mobilePhone"] = text("mobile_phone")
    business_phones: list[str] = []
    seen: set[str] = set()
    for key in ("landline_phone", "company_phone"):
        value = text(key)
        digits = re.sub(r"\D", "", value)
        if value and digits and digits not in seen:
            seen.add(digits)
            business_phones.append(value)
    if business_phones:
        contact["businessPhones"] = business_phones
    for key, graph_key in (("company_name", "companyName"), ("job_title", "jobTitle")):
        if text(key):
            contact[graph_key] = text(key)
    return contact


def _find_existing_contact(contacts: list[Mapping[str, Any]], email: str, display_name: str) -> str | None:
    wanted_email = email.casefold()
    if wanted_email:
        for contact in contacts:
            for address in contact.get("emailAddresses") or []:
                if str((address or {}).get("address") or "").strip().casefold() == wanted_email:
                    return str(contact.get("id") or "") or None
        return None
    wanted_name = display_name.casefold()
    for contact in contacts:
        if wanted_name and str(contact.get("displayName") or "").strip().casefold() == wanted_name:
            return str(contact.get("id") or "") or None
    return None


async def upsert_contact(user_id: int, details: Mapping[str, Any]) -> dict[str, str]:
    """Create or update the requester in the technician's Outlook contacts."""
    body = build_outlook_contact(details)
    if not body.get("displayName"):
        raise ValueError("The requester has no name or email to save to Outlook")
    token = await acquire_access_token(user_id, require_write=True)
    headers = {"Authorization": f"Bearer {token}"}
    email = str(details.get("email") or "").strip()
    async with monitored_client(httpx.AsyncClient, timeout=30) as client:
        contacts: list[Mapping[str, Any]] = []
        next_url: str | None = (
            GRAPH_CONTACTS_BASE_URL + "?$select=id,displayName,emailAddresses&$top=100"
        )
        visited_urls: set[str] = set()
        while next_url and next_url not in visited_urls:
            visited_urls.add(next_url)
            response = await client.get(next_url, headers=headers)
            if response.status_code != 200:
                raise ValueError("Unable to read Outlook contacts")
            payload = response.json()
            contacts.extend(payload.get("value") or [])
            next_url = payload.get("@odata.nextLink")
        contact_id = _find_existing_contact(contacts, email, str(body["displayName"]))
        if contact_id:
            response = await client.patch(
                GRAPH_CONTACTS_BASE_URL + "/" + quote(contact_id, safe=""),
                headers=headers, json=body,
            )
            action = "updated"
        else:
            response = await client.post(GRAPH_CONTACTS_BASE_URL, headers=headers, json=body)
            action = "created"
    if response.status_code == 403:
        raise ReconnectRequired(
            "Reconnect Outlook contacts from your profile to let MyPortal save contacts to Outlook"
        )
    if response.status_code not in (200, 201):
        raise ValueError("Unable to save the contact to Outlook")
    return {"action": action, "display_name": str(body["displayName"])}
