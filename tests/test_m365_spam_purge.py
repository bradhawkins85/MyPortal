from __future__ import annotations

import asyncio
import base64
import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
import httpx
from fastapi import HTTPException

from app.features.m365_admin import api_routes, routes
from app.services import m365 as m365_service
from app.services import m365_spam_purge as service
from app.services.m365 import M365Error, _jwt_appid


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_build_content_match_query_combines_safe_structured_fields():
    query = service.build_content_match_query(
        sender="attacker@example.com",
        subject='Urgent "invoice"',
        received_from=date(2026, 2, 1),
        received_to=date(2026, 2, 4),
        advanced_query=None,
    )
    assert query == (
        '(Received:02/01/2026..02/04/2026) AND '
        '(From:"attacker@example.com") AND (Subject:"Urgent \\"invoice\\"")'
    )


def test_build_content_match_query_requires_meaningful_criteria():
    with pytest.raises(ValueError, match="criterion"):
        service.build_content_match_query(
            sender=None, subject=" ", received_from=None, received_to=None,
            advanced_query=None,
        )


def test_build_content_match_query_rejects_control_characters_in_advanced_query():
    with pytest.raises(ValueError, match="control"):
        service.build_content_match_query(
            sender=None, subject=None, received_from=None, received_to=None,
            advanced_query="From:valid@example.com\nOR Subject:unsafe",
        )


def test_start_purge_requires_completed_non_empty_search(monkeypatch):
    async def fake_get(_request_id):
        return {"id": 7, "company_id": 2, "search_status": "completed", "matched_items": 0,
                "purge_status": "not_started"}

    monkeypatch.setattr(service.purge_repo, "get_request", fake_get)
    with pytest.raises(ValueError, match="no messages"):
        asyncio.run(service.start_purge(7, 2))


def test_start_purge_is_company_scoped(monkeypatch):
    async def fake_get(_request_id):
        return {"id": 7, "company_id": 99}

    monkeypatch.setattr(service.purge_repo, "get_request", fake_get)
    with pytest.raises(LookupError):
        asyncio.run(service.start_purge(7, 2))


@pytest.mark.anyio("asyncio")
async def test_retry_route_flashes_purview_preflight_error(monkeypatch):
    request = object()
    error = M365Error("Purview preflight did not pass", http_status=503)

    monkeypatch.setattr(
        routes, "_context",
        AsyncMock(return_value=({"id": 9}, 2, None)),
    )
    monkeypatch.setattr(
        routes.purge_service, "start_search", AsyncMock(side_effect=error),
    )

    response = await routes.retry_failed_search(7, request)

    assert response.status_code == 303
    assert response.headers["location"] == "/m365/spam-purge"
    assert "set-cookie" in response.headers


@pytest.mark.anyio("asyncio")
async def test_api_start_search_returns_service_unavailable_for_preflight_error(monkeypatch):
    error = M365Error("Purview preflight did not pass", http_status=503)
    monkeypatch.setattr(api_routes, "_company_id", AsyncMock(return_value=2))
    monkeypatch.setattr(
        api_routes.purge_service, "start_search", AsyncMock(side_effect=error),
    )

    with pytest.raises(HTTPException) as exc_info:
        await api_routes.start_search(7, object(), {})

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Purview preflight did not pass"


def test_domain_read_all_is_provisioned_and_visible_in_diagnostics():
    domain_read_all = "dbb9058a-0e50-45d7-ae91-66909b5d4664"

    assert domain_read_all in m365_service.get_required_app_role_ids()
    graph = next(
        app for app in m365_service.ENTERPRISE_APP_CATALOG
        if app["name"] == "Microsoft Graph"
    )
    assert {item["id"]: item["name"] for item in graph["permissions"]}[domain_read_all] == (
        "Domain.Read.All"
    )


@pytest.mark.anyio("asyncio")
async def test_scc_invoke_uses_initial_domain_in_route_and_anchor(monkeypatch):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"appid": "client-id"}).encode()).rstrip(b"=").decode()
    token = f"{header}.{payload}.signature"
    captured: dict = {}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return httpx.Response(200, json={"value": []})

    monkeypatch.setattr(m365_service.httpx, "AsyncClient", FakeClient)

    await m365_service._scc_invoke_command(
        token, "08fa9092-c049-429b-bd82-28119ef5dd7f", "Get-ComplianceSearch",
        organization="contoso.onmicrosoft.com",
    )

    assert "/contoso.onmicrosoft.com/InvokeCommand" in captured["url"]
    assert "08fa9092-c049-429b-bd82-28119ef5dd7f" not in captured["url"]
    assert captured["headers"]["X-AnchorMailbox"] == (
        "app:client-id@contoso.onmicrosoft.com"
    )


@pytest.mark.anyio("asyncio")
async def test_scc_invoke_anchors_delegated_token_to_admin_upn(monkeypatch):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({
        "appid": "client-id", "scp": "user_impersonation",
        "upn": "admin@contoso.onmicrosoft.com",
    }).encode()).rstrip(b"=").decode()
    token = f"{header}.{payload}.signature"
    captured: dict = {}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update(headers=headers)
            return httpx.Response(200, json={"value": []})

    monkeypatch.setattr(m365_service.httpx, "AsyncClient", FakeClient)

    await m365_service._scc_invoke_command(
        token, "08fa9092-c049-429b-bd82-28119ef5dd7f", "New-ComplianceSearch",
        organization="contoso.onmicrosoft.com",
    )

    assert captured["headers"]["X-AnchorMailbox"] == "UPN:admin@contoso.onmicrosoft.com"


def _scc_client_factory(results: list):
    """Build a FakeClient whose post() yields results in order (call or raise)."""

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            outcome = results.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    return FakeClient


@pytest.mark.anyio("asyncio")
async def test_scc_invoke_retries_connect_timeout_then_succeeds(monkeypatch):
    token = "header.payload.signature"
    results = [
        httpx.ConnectTimeout("connect timed out"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.Response(200, json={"value": []}),
    ]
    monkeypatch.setattr(m365_service.httpx, "AsyncClient", _scc_client_factory(results))
    sleep = AsyncMock()
    monkeypatch.setattr(m365_service.asyncio, "sleep", sleep)

    payload = await m365_service._scc_invoke_command(
        token, "08fa9092-c049-429b-bd82-28119ef5dd7f", "New-ComplianceSearch",
        {"Name": "search"}, organization="contoso.onmicrosoft.com",
    )

    assert payload == {"value": []}
    assert sleep.await_count == 2
    assert [call.args[0] for call in sleep.await_args_list] == [5, 10]


@pytest.mark.anyio("asyncio")
async def test_scc_invoke_connect_timeout_exhaustion_is_actionable(monkeypatch):
    token = "header.payload.signature"
    results = [httpx.ConnectTimeout("connect timed out") for _ in range(3)]
    monkeypatch.setattr(m365_service.httpx, "AsyncClient", _scc_client_factory(results))
    monkeypatch.setattr(m365_service.asyncio, "sleep", AsyncMock())

    with pytest.raises(
        M365Error, match="New-ComplianceSearch request timed out \\(ConnectTimeout\\)"
    ) as excinfo:
        await m365_service._scc_invoke_command(
            token, "08fa9092-c049-429b-bd82-28119ef5dd7f", "New-ComplianceSearch",
            {"Name": "search"}, organization="contoso.onmicrosoft.com",
        )
    assert "ps.compliance.protection.outlook.com (HTTPS port 443)" in str(excinfo.value)


def test_jwt_appid_supports_v2_azp_claim():
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"azp": "v2-client-id"}).encode()).rstrip(b"=").decode()

    assert _jwt_appid(f"{header}.{payload}.signature") == "v2-client-id"


def test_spam_purge_template_has_review_confirmation_and_live_refresh():
    source = open("app/templates/m365/spam_purge.html", encoding="utf-8").read()
    assert 'name="confirmation"' in source
    assert 'pattern="PURGE"' in source
    assert 'action="/m365/spam-purge/{{ item.id }}/retry"' in source
    assert "Retry search" in source
    assert "active_jobs" in source
    assert 'data-utc="{{ item.created_at }}"' in source
    assert "Configure Compliance Administrator" not in source
    assert "setup=compliance_role" not in source


def test_compliance_role_setup_is_on_m365_configuration_and_diagnostics_pages():
    configuration = open("app/templates/m365/index.html", encoding="utf-8").read()
    diagnostics = open("app/templates/m365/diagnostics.html", encoding="utf-8").read()

    assert "Configure Compliance Administrator" in configuration
    assert "setup=compliance_role&amp;return_to=m365" in configuration
    assert "Configure Compliance Administrator" in diagnostics
    assert "setup=compliance_role&amp;return_to=diagnostics" in diagnostics


def test_spam_purge_sidebar_requires_explicit_permission():
    source = open("app/templates/base.html", encoding="utf-8").read()

    assert source.count("{% if can_access_m365_spam_purge %}") == 2
    assert "or can_access_m365_spam_purge)" in source
    assert "{% if is_super_admin or is_helpdesk_technician %}" not in source


def test_spam_purge_permission_is_available_to_roles():
    from app.security.menu_permissions import catalogue_for_api

    permission = next(
        item for item in catalogue_for_api() if item["key"] == "menu.m365.spam_purge"
    )
    assert permission["admin_only"] is True
    assert permission["levels"] == ["none", "read", "write"]

def test_jwt_appid_extracts_appid_from_valid_jwt():
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"appid": "my-client-id", "tid": "my-tenant"}).encode()).rstrip(b"=").decode()
    token = f"{header}.{payload}.fakesig"
    assert _jwt_appid(token) == "my-client-id"


def test_jwt_appid_returns_none_when_claim_absent():
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"tid": "my-tenant"}).encode()).rstrip(b"=").decode()
    token = f"{header}.{payload}.fakesig"
    assert _jwt_appid(token) is None


def test_spam_purge_discloses_limits_retention_and_unknown_counts():
    source = open("app/templates/m365/spam_purge.html", encoding="utf-8").read()
    assert "Up to 10 items/mailbox/action" in source
    assert "Holds and retention" in source
    assert "Remaining:" in source and "Unknown" in source
    assert "does not guarantee every match was removed" in source


@pytest.mark.anyio("asyncio")
async def test_start_search_does_not_block_on_advisory_preflight(monkeypatch):
    request = {"id": 7, "company_id": 2, "search_status": "draft"}
    monkeypatch.setattr(service.purge_repo, "get_request", AsyncMock(return_value=request))
    update = AsyncMock()
    monkeypatch.setattr(service.purge_repo, "update_request", update)
    preflight = AsyncMock(return_value={"ready": False, "checks": []})
    monkeypatch.setattr(service.m365_service, "run_purview_preflight", preflight)

    await service.start_search(7, 2)

    preflight.assert_not_awaited()
    assert update.await_args.args[1]["search_status"] == "queued"


def _make_scc_token(claims: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.signature"


def _fake_scc_client(results: list):
    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            return results.pop(0)

    return FakeClient


@pytest.mark.anyio("asyncio")
async def test_scc_invoke_401_without_admin_consent_points_at_consent_kind(monkeypatch):
    # Purview 401 bodies are often opaque; the operator-facing message must
    # carry the token claims so consent-kind vs tenant-role is decidable.
    token = _make_scc_token({
        "aud": "00000007-0000-0ff1-ce00-000000000000",
        "azp": "client-id",
        "scope": "https://ps.compliance.protection.outlook.com/.default",
        "preferred_username": "admin@contoso.com",
        "oid": "user-oid",
    })
    monkeypatch.setattr(
        m365_service.httpx, "AsyncClient", _fake_scc_client([httpx.Response(401, text="")])
    )

    with pytest.raises(M365Error) as excinfo:
        await m365_service._scc_invoke_command(
            token, "08fa9092-c049-429b-bd82-28119ef5dd7f", "New-ComplianceSearch",
            {"Name": "test"}, organization="contoso.onmicrosoft.com",
        )

    assert excinfo.value.http_status == 401
    message = str(excinfo.value)
    assert "Token claims:" in message
    assert "admin@contoso.com" in message
    assert "not admin consent" in message


@pytest.mark.anyio("asyncio")
async def test_scc_invoke_401_with_admin_consent_points_at_tenant_role(monkeypatch):
    token = _make_scc_token({
        "aud": "00000007-0000-0ff1-ce00-000000000000",
        "azp": "client-id",
        "scope": "https://ps.compliance.protection.outlook.com/.default",
        "adminconsenttoken": True,
        "preferred_username": "admin@contoso.com",
        "oid": "user-oid",
    })
    monkeypatch.setattr(
        m365_service.httpx, "AsyncClient", _fake_scc_client([httpx.Response(403, text="")])
    )

    with pytest.raises(M365Error) as excinfo:
        await m365_service._scc_invoke_command(
            token, "08fa9092-c049-429b-bd82-28119ef5dd7f", "New-ComplianceSearch",
            {"Name": "test"}, organization="contoso.onmicrosoft.com",
        )

    assert excinfo.value.http_status == 403
    assert "tenant-side role" in str(excinfo.value)


def test_delegated_reconnect_error_is_not_app_hints():
    """The 503 delegated-reconnect error must surface verbatim, without 401 hints."""
    message = service._failure_message(
        m365_service._scc_delegated_reconnect_error(
            "No delegated administrator sign-in is stored for this company. "
        )
    )
    assert "delegated permissions of the " in message
    assert "Configure Compliance Administrator" in message
    assert "eDiscoveryManager" in message and "Search And Purge" in message


@pytest.mark.anyio("asyncio")
async def test_scc_access_token_requires_delegated_sign_in(monkeypatch):
    """Without a stored refresh token the operator gets an actionable 503,
    and no app-only client_credentials grant is attempted."""
    monkeypatch.setattr(
        m365_service, "get_credentials",
        AsyncMock(return_value={
            "tenant_id": "tenant-id", "client_id": "client-id",
            "client_secret": "secret", "refresh_token": None,
        }),
    )
    exchange = AsyncMock()
    monkeypatch.setattr(m365_service, "_exchange_token", exchange)

    with pytest.raises(M365Error) as excinfo:
        await m365_service._acquire_scc_access_token(2)

    assert excinfo.value.http_status == 503
    assert "delegated permissions" in str(excinfo.value)
    assert "Configure Compliance Administrator" in str(excinfo.value)
    exchange.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_scc_access_token_no_app_only_fallback_on_reauth(monkeypatch):
    """An expired/revoked/under-consented refresh token must not silently fall
    back to the MyPortal application; it must report the reconnect step."""
    monkeypatch.setattr(
        m365_service, "get_credentials",
        AsyncMock(return_value={
            "tenant_id": "tenant-id", "client_id": "client-id",
            "client_secret": "secret", "refresh_token": "stored-rt",
        }),
    )
    monkeypatch.setattr(
        m365_service, "_exchange_token",
        AsyncMock(side_effect=M365Error(
            "Unable to acquire Microsoft 365 access token",
            http_status=400, graph_error_code="invalid_grant",
            failure_kind="reauthentication_required",
        )),
    )

    with pytest.raises(M365Error) as excinfo:
        await m365_service._acquire_scc_access_token(2)

    assert excinfo.value.http_status == 503
    assert "delegated sign-in" in str(excinfo.value)
    assert "consent" in str(excinfo.value)
    # Exactly one grant attempt: the delegated refresh_token exchange, never
    # a client_credentials fallback.
    m365_service._exchange_token.assert_awaited_once()
    assert m365_service._exchange_token.await_args.kwargs["refresh_token"] == "stored-rt"


@pytest.mark.anyio("asyncio")
async def test_scc_access_token_persists_rotated_refresh_token(monkeypatch):
    """Microsoft rotates refresh tokens on each grant; the new token must be
    persisted or the next delegated exchange fails with the stale token."""
    monkeypatch.setattr(
        m365_service, "get_credentials",
        AsyncMock(return_value={
            "tenant_id": "tenant-id", "client_id": "client-id",
            "client_secret": "secret", "refresh_token": "old-rt",
        }),
    )
    monkeypatch.setattr(
        m365_service, "_exchange_token",
        AsyncMock(return_value=("scc-access-token", "rotated-rt", None)),
    )
    update_tokens = AsyncMock()
    monkeypatch.setattr(m365_service.m365_repo, "update_tokens", update_tokens)

    token, tenant_id = await m365_service._acquire_scc_access_token(2)

    assert (token, tenant_id) == ("scc-access-token", "tenant-id")
    update_tokens.assert_awaited_once()
    persisted = update_tokens.await_args.kwargs["refresh_token"]
    assert m365_service.decrypt_secret(persisted) == "rotated-rt"
    assert persisted != "old-rt"


_EOP_SCOPE_ID = "7953c94e-23e3-4f4b-ba94-1bf0b07c7a83"
_EOP_ROLE_ID = "dc50a0fb-09a3-484d-be87-e023b12c6440"


def _manifest_graph(required: list, patches: list):
    async def fake_get(_token, url, **_kwargs):
        if "servicePrincipalNames/any" in url:
            return {"value": [{
                "id": "eop-sp", "appId": m365_service._SCC_APP_ID,
                "oauth2PermissionScopes": [{"id": _EOP_SCOPE_ID, "value": "x", "isEnabled": True}],
            }]}
        if "/applications?" in url:
            return {"value": [{"id": "11111111-2222-3333-4444-555555555555", "requiredResourceAccess": required}]}
        raise AssertionError(url)

    async def fake_patch(_token, url, payload, **_kwargs):
        patches.append((url, payload))
        return {}

    return fake_get, fake_patch


@pytest.mark.anyio("asyncio")
async def test_ensure_scc_delegated_permission_adds_scope_and_keeps_existing(monkeypatch):
    required = [
        {"resourceAppId": "00000003-0000-0000-c000-000000000000",
         "resourceAccess": [{"id": "graph-role", "type": "Role"}]},
        {"resourceAppId": m365_service._SCC_APP_ID,
         "resourceAccess": [{"id": _EOP_ROLE_ID, "type": "Role"}]},
    ]
    patches: list = []
    fake_get, fake_patch = _manifest_graph(required, patches)
    monkeypatch.setattr(m365_service, "get_credentials", AsyncMock(return_value={"client_id": "client-id"}))
    monkeypatch.setattr(m365_service, "_graph_get", fake_get)
    monkeypatch.setattr(m365_service, "_graph_patch", fake_patch)

    assert await m365_service.ensure_scc_delegated_permission(1, "token") is True

    (url, payload), = patches
    assert url.endswith("/applications/11111111-2222-3333-4444-555555555555")
    by_resource = {e["resourceAppId"]: e["resourceAccess"] for e in payload["requiredResourceAccess"]}
    assert by_resource["00000003-0000-0000-c000-000000000000"] == [{"id": "graph-role", "type": "Role"}]
    assert by_resource[m365_service._SCC_APP_ID] == [
        {"id": _EOP_ROLE_ID, "type": "Role"},
        {"id": _EOP_SCOPE_ID, "type": "Scope"},
    ]


@pytest.mark.anyio("asyncio")
async def test_ensure_scc_delegated_permission_is_noop_when_present(monkeypatch):
    required = [{"resourceAppId": m365_service._SCC_APP_ID,
                 "resourceAccess": [{"id": _EOP_SCOPE_ID, "type": "Scope"}]}]
    patches: list = []
    fake_get, fake_patch = _manifest_graph(required, patches)
    monkeypatch.setattr(m365_service, "get_credentials", AsyncMock(return_value={"client_id": "client-id"}))
    monkeypatch.setattr(m365_service, "_graph_get", fake_get)
    monkeypatch.setattr(m365_service, "_graph_patch", fake_patch)

    assert await m365_service.ensure_scc_delegated_permission(1, "token") is False
    assert patches == []


@pytest.mark.anyio("asyncio")
async def test_ensure_scc_delegated_permission_never_raises(monkeypatch):
    async def failing_get(*_args, **_kwargs):
        raise M365Error("Microsoft Graph request failed (403)", http_status=403)

    monkeypatch.setattr(m365_service, "get_credentials", AsyncMock(return_value={"client_id": "client-id"}))
    monkeypatch.setattr(m365_service, "_graph_get", failing_get)

    assert await m365_service.ensure_scc_delegated_permission(1, "token") is False
