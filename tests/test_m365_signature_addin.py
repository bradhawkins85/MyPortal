import hashlib
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import jinja2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.features.m365_admin import addin_routes
from app.services import m365_signature_addin as addin

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BASE = "https://portal.example.com"
MSAL_SHA256 = "2b580165064a6a4ec041ebd44832299172688a23a42c3b81d65314799dba04eb"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _async(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def tenant(monkeypatch, signing_key):
    """Stub the company lookup and the tenant's published signing keys."""
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk["kid"] = "key-1"
    lookups = []

    async def fake_lookup(client_id, tenant_id):
        lookups.append((client_id, tenant_id))
        return [7] if (client_id, tenant_id) == (CLIENT, TENANT) else []

    monkeypatch.setattr(addin.m365_repo, "list_company_ids_for_app_tenant", fake_lookup)
    monkeypatch.setattr(addin, "_tenant_signing_keys", _async([jwk]))
    return lookups


def _token(key, **overrides):
    now = int(time.time())
    claims = {
        "aud": CLIENT,
        "azp": CLIENT,
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "tid": TENANT,
        "iat": now,
        "nbf": now,
        "exp": now + 600,
        "scp": "access_as_user",
        "preferred_username": "ada@example.com",
    }
    claims.update(overrides)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "key-1"})


@pytest.mark.anyio
async def test_valid_token_returns_claims_and_company(tenant, signing_key):
    claims, company_ids = await addin.validate_addin_token(_token(signing_key))

    assert company_ids == [7]
    assert claims["preferred_username"] == "ada@example.com"
    assert tenant == [(CLIENT, TENANT)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [
        {"tid": "99999999-2222-3333-4444-555555555555"},
        {"aud": "not-a-guid"},
        {"azp": "99999999-bbbb-cccc-dddd-eeeeeeeeeeee"},
        {"scp": "User.Read"},
        {"scp": None},
        {"exp": int(time.time()) - 3600},
        {"iss": f"https://sts.windows.net/{TENANT}/"},
    ],
    ids=["other-tenant", "bad-audience", "other-app", "wrong-scope", "no-scope", "expired", "v1-issuer"],
)
async def test_tokens_that_must_be_rejected(tenant, signing_key, overrides):
    with pytest.raises(addin.AddinAuthError):
        await addin.validate_addin_token(_token(signing_key, **overrides))


@pytest.mark.anyio
async def test_token_signed_by_another_key_is_rejected(tenant):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(addin.AddinAuthError):
        await addin.validate_addin_token(_token(other))


@pytest.mark.anyio
async def test_malformed_token_is_rejected(tenant):
    with pytest.raises(addin.AddinAuthError):
        await addin.validate_addin_token("not-a-jwt")


@pytest.fixture
def staff_signatures(monkeypatch):
    company = {"id": 7, "signature_delivery_mode": "addin"}
    rendered_for = []

    async def fake_render(company_id, addresses):
        rendered_for.append((company_id, addresses))
        return {
            "signatures": [{"address": addresses[0], "name": "Standard", "html": "<p>Ada</p>"}],
            "additional_signatures": [],
            "skipped": {},
        }

    monkeypatch.setattr(addin.companies_repo, "get_company_by_id", lambda company_id: _async(company)())
    monkeypatch.setattr(
        addin.deployment_service, "render_classic_outlook_signatures", fake_render
    )
    return company, rendered_for


@pytest.mark.anyio
async def test_signature_for_token_renders_primary_signature(tenant, signing_key, staff_signatures):
    _, rendered_for = staff_signatures

    assert await addin.signature_for_token(_token(signing_key)) == {
        "html": "<p>Ada</p>",
        "name": "Standard",
    }
    assert rendered_for == [(7, ["ada@example.com"])]


@pytest.mark.anyio
async def test_signature_for_token_is_empty_in_exchange_mode(tenant, signing_key, staff_signatures):
    company, rendered_for = staff_signatures
    company["signature_delivery_mode"] = "exchange"

    assert await addin.signature_for_token(_token(signing_key)) is None
    assert rendered_for == []


@pytest.mark.anyio
async def test_signature_for_token_needs_an_address(tenant, signing_key, staff_signatures):
    assert await addin.signature_for_token(_token(signing_key, preferred_username="ada")) is None


@pytest.fixture
def organization(monkeypatch):
    calls = []
    updates = []

    async def fake_invoke(token, tenant_id, cmdlet, parameters=None):
        calls.append((cmdlet, parameters))
        return {}

    async def fake_update(company_id, **fields):
        updates.append((company_id, fields))

    monkeypatch.setattr(
        addin.deployment_service.m365_service, "_acquire_exo_access_token", _async(("exo", TENANT))
    )
    monkeypatch.setattr(addin.deployment_service.m365_service, "_exo_invoke_command", fake_invoke)
    monkeypatch.setattr(addin.companies_repo, "update_company", fake_update)
    return calls, updates


@pytest.mark.anyio
@pytest.mark.parametrize("mode,postponed", [("addin", False), ("exchange", True)])
async def test_set_delivery_mode_switches_roaming_signatures(organization, mode, postponed):
    calls, updates = organization

    await addin.set_delivery_mode(7, mode)

    assert calls == [("Set-OrganizationConfig", {"PostponeRoamingSignaturesUntilLater": postponed})]
    assert updates == [(7, {"signature_delivery_mode": mode})]


@pytest.mark.anyio
async def test_set_delivery_mode_keeps_stored_mode_when_exchange_refuses(organization, monkeypatch):
    calls, updates = organization

    async def refuse(*args, **kwargs):
        raise addin.m365_service.M365Error("forbidden")

    monkeypatch.setattr(addin.deployment_service.m365_service, "_exo_invoke_command", refuse)

    with pytest.raises(addin.m365_service.M365Error):
        await addin.set_delivery_mode(7, "addin")
    assert updates == []


@pytest.mark.anyio
async def test_set_delivery_mode_rejects_unknown_modes(organization):
    calls, updates = organization

    with pytest.raises(ValueError):
        await addin.set_delivery_mode(7, "roaming")
    assert calls == [] and updates == []


@pytest.mark.parametrize(
    "mode,postponed,expected",
    [
        ("exchange", True, True),
        ("exchange", False, False),
        ("addin", False, True),
        ("addin", True, False),
        ("addin", None, None),
    ],
)
def test_tenant_matches_mode(mode, postponed, expected):
    assert addin.tenant_matches_mode(mode, {"postponed": postponed}) is expected


def test_registration_changes_for_a_fresh_app():
    application = {
        "id": "obj",
        "identifierUris": [],
        "api": {"oauth2PermissionScopes": [], "preAuthorizedApplications": []},
        "spa": {"redirectUris": []},
        "web": {
            "redirectUris": [f"{BASE}/m365/callback"],
            "implicitGrantSettings": {"enableIdTokenIssuance": False},
            "redirectUriSettings": [{"uri": f"{BASE}/m365/callback", "index": None}],
        },
    }

    patch, missing = addin.registration_changes(application, BASE, CLIENT)

    assert missing == [
        "Application ID URI",
        "access_as_user scope",
        "v2 access tokens",
        "Outlook sign-in redirect",
        "Admin consent redirect",
    ]
    assert patch["identifierUris"] == [f"api://portal.example.com/{CLIENT}"]
    assert patch["api"]["requestedAccessTokenVersion"] == 2
    assert patch["api"]["preAuthorizedApplications"] == []
    (scope,) = patch["api"]["oauth2PermissionScopes"]
    assert scope["value"] == "access_as_user" and scope["id"] == addin.scope_id(CLIENT)
    assert patch["spa"] == {"redirectUris": ["brk-multihub://portal.example.com"]}
    assert patch["web"]["redirectUris"] == [
        f"{BASE}/m365/callback",
        f"{BASE}/m365/signatures/addin/consent",
    ]
    assert patch["web"]["implicitGrantSettings"] == {"enableIdTokenIssuance": False}
    assert "redirectUriSettings" not in patch["web"]


def test_registration_changes_are_empty_once_prepared():
    application = {
        "identifierUris": [f"api://portal.example.com/{CLIENT}"],
        "api": {
            "requestedAccessTokenVersion": 2,
            "oauth2PermissionScopes": [{"id": "x", "value": "access_as_user"}],
        },
        "spa": {"redirectUris": ["brk-multihub://portal.example.com"]},
        "web": {"redirectUris": [f"{BASE}/m365/signatures/addin/consent"]},
    }

    assert addin.registration_changes(application, BASE, CLIENT) == ({}, [])


def test_portal_base_url_requires_https(monkeypatch):
    class _Settings:
        portal_url = "http://portal.example.com"

    monkeypatch.setattr(addin, "get_settings", lambda: _Settings)
    assert addin.portal_base_url() is None
    _Settings.portal_url = "https://portal.example.com/"
    assert addin.portal_base_url() == BASE


def test_manifest_declares_launch_events_and_company_app():
    addin_id = "0f8fad5b-d9cb-469f-a165-70867728950e"
    manifest = addin.build_manifest(
        addin_id=addin_id, client_id=CLIENT, base_url=BASE, company_name="Acme & Co"
    )
    root = ET.fromstring(manifest)
    ns = {
        "o": "http://schemas.microsoft.com/office/appforoffice/1.1",
        "v": "http://schemas.microsoft.com/office/mailappversionoverrides/1.1",
        "bt": "http://schemas.microsoft.com/office/officeappbasictypes/1.0",
    }

    assert root.find("o:Id", ns).text == addin_id
    assert root.find("o:ProviderName", ns).text == "Acme & Co"
    assert root.find("o:Permissions", ns).text == "ReadWriteItem"
    events = {
        event.get("Type"): event.get("FunctionName")
        for event in root.iter("{http://schemas.microsoft.com/office/mailappversionoverrides/1.1}LaunchEvent")
    }
    assert events == {
        "OnNewMessageCompose": "onNewMessageComposeHandler",
        "OnNewAppointmentOrganizer": "onNewAppointmentOrganizerHandler",
    }
    urls = {url.get("id"): url.get("DefaultValue") for url in root.iter(f"{{{ns['bt']}}}Url")}
    assert urls == {
        "WebViewRuntime.Url": f"{BASE}/outlook-addin/{addin_id}/launch.html",
        "JSRuntime.Url": f"{BASE}/outlook-addin/{addin_id}/launch.js",
    }
    web_app = root.find(".//v:WebApplicationInfo", ns)
    assert web_app.find("v:Id", ns).text == CLIENT
    assert web_app.find("v:Resource", ns).text == f"api://portal.example.com/{CLIENT}"


def test_handler_names_match_the_manifest():
    source = addin.HANDLER_SCRIPT.read_text(encoding="utf-8")

    assert 'Office.actions.associate("onNewMessageComposeHandler"' in source
    assert 'Office.actions.associate("onNewAppointmentOrganizerHandler"' in source
    assert "event.completed()" in source


def test_launch_script_bundles_msal_config_and_handlers():
    script = addin.build_launch_script(client_id=CLIENT, base_url=BASE)

    assert script.startswith("/*! @azure/msal-browser v4.30.0")
    assert f'"scope": "api://portal.example.com/{CLIENT}/access_as_user"' in script
    assert f'"signatureUrl": "{BASE}/api/outlook-addin/signature"' in script
    assert script.endswith(addin.HANDLER_SCRIPT.read_text(encoding="utf-8"))


def test_launch_page_loads_office_js_and_the_bundle():
    html = addin.build_launch_html(addin_id="abc", base_url=BASE)

    assert addin.OFFICE_JS_URL in html
    assert f'src="{BASE}/outlook-addin/abc/launch.js"' in html


def test_vendored_msal_matches_the_reviewed_bytes():
    assert hashlib.sha256(addin.MSAL_BUNDLE.read_bytes()).hexdigest() == MSAL_SHA256


@pytest.mark.parametrize("size", addin.ICON_SIZES)
def test_icons_are_png(size):
    from io import BytesIO

    from PIL import Image

    data = addin.render_icon(size)
    assert data.startswith(b"\x89PNG")
    assert Image.open(BytesIO(data)).size == (size, size)


@pytest.fixture
def api(monkeypatch):
    app = FastAPI()
    app.include_router(addin_routes.router)
    return TestClient(app)


def test_signature_api_requires_a_bearer_token(api):
    assert api.get("/api/outlook-addin/signature").status_code == 401
    assert api.get(
        "/api/outlook-addin/signature", headers={"Authorization": "Basic abc"}
    ).status_code == 401


def test_signature_api_rejects_invalid_tokens(api, monkeypatch):
    async def reject(token):
        raise addin.AddinAuthError("bad")

    monkeypatch.setattr(addin_routes.addin_service, "signature_for_token", reject)
    response = api.get("/api/outlook-addin/signature", headers={"Authorization": "Bearer x"})
    assert response.status_code == 401


def test_signature_api_returns_signature_or_no_content(api, monkeypatch):
    results = [{"html": "<p>Ada</p>", "name": "Standard"}, None]

    async def fake(token):
        assert token == "abc"
        return results.pop(0)

    monkeypatch.setattr(addin_routes.addin_service, "signature_for_token", fake)
    first = api.get("/api/outlook-addin/signature", headers={"Authorization": "Bearer abc"})
    assert first.status_code == 200
    assert first.json() == {"html": "<p>Ada</p>", "name": "Standard"}
    assert first.headers["cache-control"] == "no-store"
    second = api.get("/api/outlook-addin/signature", headers={"Authorization": "Bearer abc"})
    assert second.status_code == 204


def test_unknown_addin_assets_are_not_found(api, monkeypatch):
    monkeypatch.setattr(addin_routes.addin_service, "portal_base_url", lambda: BASE)
    monkeypatch.setattr(
        addin_routes.companies_repo, "get_company_by_outlook_addin_id", _async(None)
    )

    assert api.get("/outlook-addin/not-a-guid/launch.js").status_code == 404
    assert (
        api.get("/outlook-addin/0f8fad5b-d9cb-469f-a165-70867728950e/launch.html").status_code
        == 404
    )
    assert api.get("/outlook-addin/icon-999.png").status_code == 404


def test_addin_assets_are_served_for_a_known_company(api, monkeypatch):
    addin_id = "0f8fad5b-d9cb-469f-a165-70867728950e"
    monkeypatch.setattr(addin_routes.addin_service, "portal_base_url", lambda: BASE)
    monkeypatch.setattr(
        addin_routes.companies_repo,
        "get_company_by_outlook_addin_id",
        _async({"id": 7, "name": "Acme"}),
    )
    monkeypatch.setattr(addin_routes.m365_service, "get_credentials", _async({"client_id": CLIENT}))

    page = api.get(f"/outlook-addin/{addin_id}/launch.html")
    script = api.get(f"/outlook-addin/{addin_id}/launch.js")
    icon = api.get("/outlook-addin/icon-32.png")

    assert page.status_code == 200 and "office.js" in page.text
    assert script.status_code == 200 and script.headers["content-type"].startswith("text/javascript")
    assert CLIENT in script.text
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"


def _page_env() -> jinja2.Environment:
    class _Loader(jinja2.FileSystemLoader):
        def get_source(self, environment, template):
            if template == "base.html":
                source = (
                    "{% block header_actions %}{% endblock %}"
                    "{% block content %}{% endblock %}{% block scripts %}{% endblock %}"
                )
                return source, None, lambda: True
            return super().get_source(environment, template)

    env = jinja2.Environment(loader=_Loader(str(TEMPLATES)), autoescape=True)
    env.globals["static_url"] = lambda path: path
    return env


def _signatures_page(**context):
    return _page_env().get_template("m365/signatures.html").render(
        templates=[],
        current_primary=None,
        schedule_timezone="UTC",
        csrf_token="t",
        can_write=True,
        portal_base_url=BASE,
        **context,
    )


def test_signatures_page_offers_both_delivery_modes():
    html = _signatures_page(delivery_mode="exchange", addin_registration=None)

    assert 'action="/m365/signatures/delivery-mode"' in html
    assert 'value="exchange" checked' in html
    assert 'value="addin" checked' not in html
    assert "Outlook add-in setup" not in html


def test_signatures_page_shows_addin_setup_in_addin_mode():
    html = _signatures_page(
        delivery_mode="addin",
        addin_registration={"ready": False, "missing": ["Outlook sign-in redirect"], "error": None},
    )

    assert 'value="addin" checked' in html
    assert "Outlook add-in setup" in html
    assert "Needs: Outlook sign-in redirect" in html
    assert 'href="/m365/signatures/addin/manifest.xml"' in html
    assert 'href="/m365/signatures/addin/grant-consent"' in html


def _deploy_page(**context):
    return _page_env().get_template("m365/signatures_deploy.html").render(
        template_record={"id": 5, "name": "Standard", "slug": "standard", "status": "published"},
        targets=[{"user_principal_name": "ada@example.com", "display_name": "Ada", "staff_id": 1,
                  "staff_label": "Ada", "targeted": True}],
        targeting_summary="Everyone",
        max_mailboxes=200,
        outcomes=[],
        submitted={},
        csrf_token="t",
        **context,
    )


def test_deploy_page_warns_about_roaming_in_exchange_mode():
    html = _deploy_page(
        delivery_mode="exchange",
        roaming={"postponed": False, "error": None},
        tenant_matches_mode=False,
    )

    assert "Roaming signatures are active in this tenant." in html
    assert "Postpone roaming signatures" in html
    assert 'name="return_to" value="/m365/signatures/5/deploy"' in html


def test_deploy_page_disables_exchange_push_in_addin_mode():
    html = _deploy_page(
        delivery_mode="addin",
        roaming={"postponed": False, "error": None},
        tenant_matches_mode=True,
    )

    assert "MyPortal Outlook add-in" in html
    assert "Roaming signatures are active in this tenant." not in html
    (deploy_button,) = re.findall(r"<button[^>]*>Deploy to selected mailboxes", html)
    assert "disabled" in deploy_button


def test_deploy_page_offers_to_turn_roaming_back_on_in_addin_mode():
    html = _deploy_page(
        delivery_mode="addin",
        roaming={"postponed": True, "error": None},
        tenant_matches_mode=False,
    )

    assert "Turn roaming signatures back on" in html


def test_migration_adds_delivery_columns():
    sql = (ROOT / "migrations" / "532_outlook_signature_delivery_mode.sql").read_text()

    assert "-- phase: expand" in sql
    assert "ADD COLUMN IF NOT EXISTS signature_delivery_mode VARCHAR(16) NOT NULL DEFAULT 'exchange'" in sql
    assert "ADD COLUMN IF NOT EXISTS outlook_addin_id CHAR(36) NULL" in sql
