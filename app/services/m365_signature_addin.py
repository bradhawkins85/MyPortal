"""Deliver MyPortal signatures through the MyPortal Outlook add-in.

Exchange Online ignores signatures set with ``Set-MailboxMessageConfiguration``
while roaming signatures are active, and there is no Microsoft API for the
roaming signature store.  Companies can therefore choose how signatures reach
Outlook:

``exchange``
    Roaming signatures are postponed for the tenant and Deploy pushes each
    mailbox's signature through Exchange Online.
``addin``
    Roaming signatures stay on and an event-based Outlook add-in inserts the
    signer's signature whenever they start a message or meeting invite.

The add-in signs in with nested app authentication against the company's own
MyPortal app registration, which exposes an ``access_as_user`` scope.  MyPortal
validates the resulting access token, maps the signer to a staff record and
returns their rendered primary signature.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt

from app.core.config import get_settings
from app.repositories import companies as companies_repo
from app.repositories import m365 as m365_repo
from app.services import m365 as m365_service
from app.services import m365_signature_deployment as deployment_service
from app.services.monitored_http import monitored_client

DELIVERY_EXCHANGE = "exchange"
DELIVERY_ADDIN = "addin"
DELIVERY_MODES = (DELIVERY_EXCHANGE, DELIVERY_ADDIN)

ADDIN_SCOPE_NAME = "access_as_user"
OFFICE_JS_URL = "https://appsforoffice.microsoft.com/lib/1/hosted/office.js"
ICON_SIZES = (16, 32, 64, 80, 128)

_STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"
MSAL_BUNDLE = _STATIC_ROOT / "vendor" / "msal-browser" / "msal-browser-4.30.0.min.js"
HANDLER_SCRIPT = _STATIC_ROOT / "js" / "outlook_signature_addin.js"

_GUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_SCOPE_NAMESPACE = uuid.UUID("6f3c1f0e-4d7a-4a52-9e0b-6a1f2f4f5c11")
_JWKS_TTL_SECONDS = 3600
_jwks_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


class AddinAuthError(Exception):
    """The add-in's bearer token cannot be accepted."""


def get_delivery_mode(company: dict[str, Any] | None) -> str:
    value = str((company or {}).get("signature_delivery_mode") or "").strip().lower()
    return value if value in DELIVERY_MODES else DELIVERY_EXCHANGE


def portal_base_url() -> str | None:
    """The configured HTTPS portal URL the add-in is served from, if any."""
    value = str(get_settings().portal_url or "").strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or parts.path not in ("", "/"):
        return None
    return f"https://{parts.netloc}"


def _host(base_url: str) -> str:
    return urlsplit(base_url).netloc


def identifier_uri(base_url: str, client_id: str) -> str:
    return f"api://{_host(base_url)}/{client_id}"


def scope_uri(base_url: str, client_id: str) -> str:
    return f"{identifier_uri(base_url, client_id)}/{ADDIN_SCOPE_NAME}"


def nested_redirect_uri(base_url: str) -> str:
    return f"brk-multihub://{_host(base_url)}"


def consent_redirect_uri(base_url: str) -> str:
    return f"{base_url}/m365/signatures/addin/consent"


def scope_id(client_id: str) -> str:
    """Stable id for the ``access_as_user`` scope, so re-runs never duplicate it."""
    return str(uuid.uuid5(_SCOPE_NAMESPACE, client_id.lower()))


async def ensure_addin_id(company_id: int, company: dict[str, Any] | None = None) -> str:
    """Return the company's add-in manifest id, creating it on first use.

    Outlook identifies an add-in by this id, so it must stay the same for the
    company or a new manifest would install a second copy.
    """
    company = company if company is not None else await companies_repo.get_company_by_id(company_id)
    existing = str((company or {}).get("outlook_addin_id") or "").strip()
    if _GUID_PATTERN.match(existing):
        return existing
    addin_id = str(uuid.uuid4())
    await companies_repo.update_company(company_id, outlook_addin_id=addin_id)
    return addin_id


async def set_delivery_mode(company_id: int, mode: str) -> None:
    """Switch the tenant's roaming signature setting, then store the mode.

    The tenant is changed first so a failed Exchange call leaves MyPortal's
    stored mode matching the tenant.  Raises ``ValueError`` for an unknown mode
    and :class:`m365_service.M365Error` when Exchange refuses the change.
    """
    if mode not in DELIVERY_MODES:
        raise ValueError("Choose Exchange push or the Outlook add-in")
    await deployment_service.set_roaming_signatures_postponed(
        company_id, mode == DELIVERY_EXCHANGE
    )
    await companies_repo.update_company(company_id, signature_delivery_mode=mode)


def tenant_matches_mode(mode: str, roaming: dict[str, Any]) -> bool | None:
    """Whether the tenant's roaming setting suits *mode*; ``None`` when unknown."""
    postponed = roaming.get("postponed")
    if not isinstance(postponed, bool):
        return None
    return postponed if mode == DELIVERY_EXCHANGE else not postponed


async def _app_graph_token(company_id: int) -> tuple[str, str]:
    creds = await m365_service.get_credentials(company_id)
    client_id = str((creds or {}).get("client_id") or "").strip()
    if not client_id:
        raise m365_service.M365Error("Connect Microsoft 365 for this company first")
    token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    return token, client_id


async def _load_application(access_token: str, client_id: str) -> dict[str, Any]:
    response = await m365_service._graph_get(
        access_token,
        "https://graph.microsoft.com/v1.0/applications"
        f"?$filter=appId eq '{quote(client_id, safe='')}'"
        "&$select=id,identifierUris,api,spa,web",
    )
    application = next(iter(response.get("value") or []), None)
    if not application or not application.get("id"):
        raise m365_service.M365Error(
            "MyPortal cannot read its app registration; it needs Application.ReadWrite.OwnedBy"
        )
    return application


def registration_changes(
    application: dict[str, Any], base_url: str, client_id: str
) -> tuple[dict[str, Any], list[str]]:
    """Return the Graph PATCH payload the add-in needs and labels for each change."""
    patch: dict[str, Any] = {}
    missing: list[str] = []

    wanted_uri = identifier_uri(base_url, client_id)
    uris = list(application.get("identifierUris") or [])
    if wanted_uri not in uris:
        patch["identifierUris"] = uris + [wanted_uri]
        missing.append("Application ID URI")

    api = dict(application.get("api") or {})
    scopes = [dict(scope) for scope in api.get("oauth2PermissionScopes") or []]
    api_changed = False
    if not any(scope.get("value") == ADDIN_SCOPE_NAME for scope in scopes):
        description = "Lets the MyPortal Outlook add-in add your MyPortal email signature."
        scopes.append(
            {
                "id": scope_id(client_id),
                "value": ADDIN_SCOPE_NAME,
                "type": "User",
                "isEnabled": True,
                "adminConsentDisplayName": "Read the signer's MyPortal email signature",
                "adminConsentDescription": description,
                "userConsentDisplayName": "Read your MyPortal email signature",
                "userConsentDescription": description,
            }
        )
        missing.append("access_as_user scope")
        api_changed = True
    if api.get("requestedAccessTokenVersion") != 2:
        missing.append("v2 access tokens")
        api_changed = True
    if api_changed:
        api["oauth2PermissionScopes"] = scopes
        api["requestedAccessTokenVersion"] = 2
        patch["api"] = api

    nested = nested_redirect_uri(base_url)
    spa_uris = list((application.get("spa") or {}).get("redirectUris") or [])
    if nested not in spa_uris:
        patch["spa"] = {"redirectUris": spa_uris + [nested]}
        missing.append("Outlook sign-in redirect")

    consent = consent_redirect_uri(base_url)
    web = {
        key: value
        for key, value in (application.get("web") or {}).items()
        if key != "redirectUriSettings"
    }
    web_uris = list(web.get("redirectUris") or [])
    if consent not in web_uris:
        web["redirectUris"] = web_uris + [consent]
        patch["web"] = web
        missing.append("Admin consent redirect")
    return patch, missing


async def get_registration_status(company_id: int) -> dict[str, Any]:
    """Report what the app registration still needs for the add-in.

    Returns ``{"ready": bool, "missing": [labels], "error": str | None}``.
    """
    base_url = portal_base_url()
    if not base_url:
        return {"ready": False, "missing": [], "error": "Set PORTAL_URL to the portal's HTTPS address"}
    try:
        token, client_id = await _app_graph_token(company_id)
        application = await _load_application(token, client_id)
    except m365_service.M365Error as exc:
        return {"ready": False, "missing": [], "error": str(exc)}
    _, missing = registration_changes(application, base_url, client_id)
    return {"ready": not missing, "missing": missing, "error": None}


async def prepare_app_registration(company_id: int) -> list[str]:
    """Add the add-in's scope and redirects to the app registration.

    Returns the labels of what changed (empty when already prepared).  Raises
    :class:`m365_service.M365Error`.
    """
    base_url = portal_base_url()
    if not base_url:
        raise m365_service.M365Error("Set PORTAL_URL to the portal's HTTPS address first")
    token, client_id = await _app_graph_token(company_id)
    application = await _load_application(token, client_id)
    patch, missing = registration_changes(application, base_url, client_id)
    if patch:
        await m365_service._graph_patch(
            token,
            "https://graph.microsoft.com/v1.0/applications/"
            f"{m365_service._graph_object_id(application['id'])}",
            patch,
        )
    return missing


async def admin_consent_url(company_id: int) -> str:
    """Microsoft admin consent link granting the add-in scope tenant-wide."""
    base_url = portal_base_url()
    creds = await m365_service.get_credentials(company_id)
    client_id = str((creds or {}).get("client_id") or "").strip()
    if not base_url or not client_id:
        raise m365_service.M365Error("Connect Microsoft 365 and set PORTAL_URL first")
    tenant_id = (
        await companies_repo.get_company_csp_tenant_id(company_id)
        or str(creds.get("tenant_id") or "").strip()
    )
    query = urlencode(
        {
            "client_id": client_id,
            "scope": f"openid profile {scope_uri(base_url, client_id)}",
            "redirect_uri": consent_redirect_uri(base_url),
        }
    )
    return (
        f"https://login.microsoftonline.com/{quote(tenant_id, safe='')}/v2.0/adminconsent?{query}"
    )


def _addin_base(base_url: str, addin_id: str) -> str:
    return f"{base_url}/outlook-addin/{addin_id}"


def build_manifest(
    *, addin_id: str, client_id: str, base_url: str, company_name: str
) -> str:
    """XML add-in manifest for the company's event-based signature add-in."""
    addin_base = _addin_base(base_url, addin_id)
    values = {
        "id": escape(addin_id),
        "provider": escape(company_name or "MyPortal"),
        "base": escape(base_url),
        "html": escape(f"{addin_base}/launch.html"),
        "js": escape(f"{addin_base}/launch.js"),
        "client_id": escape(client_id),
        "resource": escape(identifier_uri(base_url, client_id)),
        **{f"icon{size}": escape(f"{base_url}/outlook-addin/icon-{size}.png") for size in ICON_SIZES},
    }
    return """<?xml version="1.0" encoding="UTF-8"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
           xmlns:bt="http://schemas.microsoft.com/office/officeappbasictypes/1.0"
           xmlns:mailappor="http://schemas.microsoft.com/office/mailappversionoverrides/1.0"
           xsi:type="MailApp">
  <Id>{id}</Id>
  <Version>1.0.0.0</Version>
  <ProviderName>{provider}</ProviderName>
  <DefaultLocale>en-US</DefaultLocale>
  <DisplayName DefaultValue="MyPortal signatures"/>
  <Description DefaultValue="Adds your MyPortal email signature to new messages and meeting invites."/>
  <IconUrl DefaultValue="{icon64}"/>
  <HighResolutionIconUrl DefaultValue="{icon128}"/>
  <SupportUrl DefaultValue="{base}"/>
  <AppDomains>
    <AppDomain>{base}</AppDomain>
  </AppDomains>
  <Hosts>
    <Host Name="Mailbox"/>
  </Hosts>
  <Requirements>
    <Sets>
      <Set Name="Mailbox" MinVersion="1.1"/>
    </Sets>
  </Requirements>
  <FormSettings>
    <Form xsi:type="ItemEdit">
      <DesktopSettings>
        <SourceLocation DefaultValue="{html}"/>
      </DesktopSettings>
    </Form>
  </FormSettings>
  <Permissions>ReadWriteItem</Permissions>
  <Rule xsi:type="RuleCollection" Mode="Or">
    <Rule xsi:type="ItemIs" ItemType="Message" FormType="Edit"/>
    <Rule xsi:type="ItemIs" ItemType="Appointment" FormType="Edit"/>
  </Rule>
  <DisableEntityHighlighting>false</DisableEntityHighlighting>
  <VersionOverrides xmlns="http://schemas.microsoft.com/office/mailappversionoverrides" xsi:type="VersionOverridesV1_0">
    <VersionOverrides xmlns="http://schemas.microsoft.com/office/mailappversionoverrides/1.1" xsi:type="VersionOverridesV1_1">
      <Requirements>
        <bt:Sets DefaultMinVersion="1.10">
          <bt:Set Name="Mailbox"/>
        </bt:Sets>
      </Requirements>
      <Hosts>
        <Host xsi:type="MailHost">
          <Runtimes>
            <Runtime resid="WebViewRuntime.Url">
              <Override type="javascript" resid="JSRuntime.Url"/>
            </Runtime>
          </Runtimes>
          <DesktopFormFactor>
            <FunctionFile resid="WebViewRuntime.Url"/>
            <ExtensionPoint xsi:type="LaunchEvent">
              <LaunchEvents>
                <LaunchEvent Type="OnNewMessageCompose" FunctionName="onNewMessageComposeHandler"/>
                <LaunchEvent Type="OnNewAppointmentOrganizer" FunctionName="onNewAppointmentOrganizerHandler"/>
              </LaunchEvents>
              <SourceLocation resid="WebViewRuntime.Url"/>
            </ExtensionPoint>
          </DesktopFormFactor>
        </Host>
      </Hosts>
      <Resources>
        <bt:Images>
          <bt:Image id="Icon.16x16" DefaultValue="{icon16}"/>
          <bt:Image id="Icon.32x32" DefaultValue="{icon32}"/>
          <bt:Image id="Icon.80x80" DefaultValue="{icon80}"/>
        </bt:Images>
        <bt:Urls>
          <bt:Url id="WebViewRuntime.Url" DefaultValue="{html}"/>
          <bt:Url id="JSRuntime.Url" DefaultValue="{js}"/>
        </bt:Urls>
      </Resources>
      <WebApplicationInfo>
        <Id>{client_id}</Id>
        <Resource>{resource}</Resource>
        <Scopes>
          <Scope>openid</Scope>
          <Scope>profile</Scope>
        </Scopes>
      </WebApplicationInfo>
    </VersionOverrides>
  </VersionOverrides>
</OfficeApp>
""".format(**values)


def build_launch_html(*, addin_id: str, base_url: str) -> str:
    """Page Outlook loads for the add-in's browser runtime."""
    script = escape(f"{_addin_base(base_url, addin_id)}/launch.js")
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>MyPortal signatures</title>\n"
        f"<script src=\"{OFFICE_JS_URL}\"></script>\n"
        f"<script src=\"{script}\"></script>\n"
        "</head>\n<body></body>\n</html>\n"
    )


@lru_cache(maxsize=1)
def _script_assets() -> tuple[str, str]:
    return MSAL_BUNDLE.read_text(encoding="utf-8"), HANDLER_SCRIPT.read_text(encoding="utf-8")


def build_launch_script(*, client_id: str, base_url: str) -> str:
    """One JavaScript file with MSAL.js, the company's config and the handlers.

    Classic Outlook on Windows runs launch events in a JavaScript-only runtime
    that loads a single file, so nothing here may be fetched separately.
    """
    msal_source, handler_source = _script_assets()
    config = {
        "clientId": client_id,
        "scope": scope_uri(base_url, client_id),
        "signatureUrl": f"{base_url}/api/outlook-addin/signature",
    }
    return (
        f"{msal_source}\n;var MYPORTAL_OUTLOOK_ADDIN = {json.dumps(config)};\n{handler_source}"
    )


@lru_cache(maxsize=len(ICON_SIZES))
def render_icon(size: int) -> bytes:
    """PNG icon for the add-in listing: an envelope with a signature line."""
    from PIL import Image, ImageDraw

    if size not in ICON_SIZES:
        raise ValueError("Unsupported icon size")
    scale = 4
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, canvas - 1, canvas - 1), radius=canvas // 5, fill=(37, 99, 235, 255)
    )
    left, top = canvas * 0.18, canvas * 0.28
    right, bottom = canvas * 0.82, canvas * 0.72
    line = max(scale, canvas // 18)
    draw.rectangle((left, top, right, bottom), outline=(255, 255, 255, 255), width=line)
    draw.line(
        [(left, top), (canvas / 2, canvas * 0.52), (right, top)],
        fill=(255, 255, 255, 255),
        width=line,
    )
    image = image.resize((size, size), Image.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _tenant_signing_keys(tenant_id: str, *, refresh: bool = False) -> list[dict[str, Any]]:
    cached = _jwks_cache.get(tenant_id)
    if cached and not refresh and cached[0] > time.monotonic():
        return cached[1]
    try:
        async with monitored_client(httpx.AsyncClient, timeout=15) as client:
            response = await client.get(
                f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
            )
        response.raise_for_status()
        keys = list(response.json().get("keys") or [])
    except (httpx.HTTPError, ValueError) as exc:
        raise AddinAuthError("Microsoft signing keys are unavailable") from exc
    _jwks_cache[tenant_id] = (time.monotonic() + _JWKS_TTL_SECONDS, keys)
    return keys


async def validate_addin_token(token: str) -> tuple[dict[str, Any], list[int]]:
    """Verify an add-in access token; return its claims and matching company ids.

    The token must be a v2 access token signed by the tenant, issued to the
    company's MyPortal app for itself (``aud`` and ``azp`` are the app) with the
    ``access_as_user`` scope, and its tenant must be the company's tenant.
    """
    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise AddinAuthError("Malformed token") from exc
    tenant_id = str(unverified.get("tid") or "")
    client_id = str(unverified.get("aud") or "")
    if not _GUID_PATTERN.match(tenant_id) or not _GUID_PATTERN.match(client_id):
        raise AddinAuthError("Token is not for a MyPortal app")
    company_ids = await m365_repo.list_company_ids_for_app_tenant(client_id, tenant_id)
    if not company_ids:
        raise AddinAuthError("No company uses this app and tenant")

    kid = str(header.get("kid") or "")
    keys = await _tenant_signing_keys(tenant_id)
    jwk = next((key for key in keys if key.get("kid") == kid), None)
    if jwk is None:
        keys = await _tenant_signing_keys(tenant_id, refresh=True)
        jwk = next((key for key in keys if key.get("kid") == kid), None)
    if jwk is None:
        raise AddinAuthError("Unknown signing key")
    try:
        claims = jwt.decode(
            token,
            jwt.algorithms.RSAAlgorithm.from_jwk(jwk),
            algorithms=["RS256"],
            audience=client_id,
            issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            leeway=60,
            options={"require": ["exp", "iat", "aud", "iss", "tid"]},
        )
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise AddinAuthError("Token failed validation") from exc
    if str(claims.get("azp") or "").lower() != client_id.lower():
        raise AddinAuthError("Token was issued to another app")
    if ADDIN_SCOPE_NAME not in str(claims.get("scp") or "").split():
        raise AddinAuthError("Token lacks the add-in scope")
    return claims, company_ids


async def signature_for_token(token: str) -> dict[str, str] | None:
    """Rendered primary signature for the token's user, or ``None``.

    ``None`` means nothing should be inserted: the company does not use the
    add-in, or no active template applies to a staff record for that address.
    Raises :class:`AddinAuthError` for a token that cannot be accepted.
    """
    claims, company_ids = await validate_addin_token(token)
    address = str(claims.get("preferred_username") or claims.get("upn") or "").strip()
    if "@" not in address:
        return None
    for company_id in company_ids:
        company = await companies_repo.get_company_by_id(company_id)
        if get_delivery_mode(company) != DELIVERY_ADDIN:
            continue
        rendered = await deployment_service.render_classic_outlook_signatures(
            company_id, [address]
        )
        primary = next(iter(rendered.get("signatures") or []), None)
        if primary:
            return {"html": primary["html"], "name": primary["name"]}
    return None


__all__ = [
    "ADDIN_SCOPE_NAME",
    "AddinAuthError",
    "DELIVERY_ADDIN",
    "DELIVERY_EXCHANGE",
    "DELIVERY_MODES",
    "ICON_SIZES",
    "admin_consent_url",
    "build_launch_html",
    "build_launch_script",
    "build_manifest",
    "ensure_addin_id",
    "get_delivery_mode",
    "get_registration_status",
    "portal_base_url",
    "prepare_app_registration",
    "registration_changes",
    "render_icon",
    "set_delivery_mode",
    "signature_for_token",
    "tenant_matches_mode",
    "validate_addin_token",
]
