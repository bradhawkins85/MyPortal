"""Tests for the Trello OAuth 2.0 (Atlassian 3LO) integration."""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from app.api.routes import trello as trello_routes
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import trello as trello_service


ROOT = Path(__file__).resolve().parents[1]


def _settings(public_base_url=None, portal_url=None):
    return SimpleNamespace(public_base_url=public_base_url, portal_url=portal_url)


# ---------------------------------------------------------------------------
# Authorize URL / redirect URI / PKCE
# ---------------------------------------------------------------------------


def test_authorize_url_matches_trello_oauth2_format():
    url = trello_service.build_authorize_url(
        client_id="client-abc",
        redirect_uri="https://portal.example.com/trello/callback",
        state="signed-state",
        code_challenge="challenge123",
    )
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://auth.atlassian.com/authorize"
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert query == {
        "client_id": "client-abc",
        "scope": "read:board:trello write:board:trello offline_access",
        "redirect_uri": "https://portal.example.com/trello/callback",
        "response_type": "code",
        "prompt": "consent",
        "state": "signed-state",
        "code_challenge_method": "S256",
        "code_challenge": "challenge123",
    }


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = trello_service.generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected


@pytest.mark.parametrize(
    ("public", "portal", "request_base", "expected"),
    [
        ("https://portal.example.com/", None, "http://internal:8000/", "https://portal.example.com/trello/callback"),
        (None, "https://portal.example.com", "http://internal:8000/", "https://portal.example.com/trello/callback"),
        ("portal.example.com", None, None, "https://portal.example.com/trello/callback"),
        (None, None, "https://host.example.com/", "https://host.example.com/trello/callback"),
    ],
)
def test_redirect_uri_resolution(public, portal, request_base, expected):
    with patch("app.services.trello.get_settings", return_value=_settings(public, portal)):
        assert trello_service.build_oauth_redirect_uri(request_base) == expected


# ---------------------------------------------------------------------------
# Choosing credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_connection_takes_priority_over_legacy_credentials():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    company = {
        "id": 7,
        "trello_api_key": "key",
        "trello_token": "tok",
        "trello_oauth_access_token": encrypt_secret("access-1"),
        "trello_oauth_refresh_token": encrypt_secret("refresh-1"),
        "trello_oauth_expires_at": future,
    }
    auth = await trello_service.get_auth_for_company(company)
    assert auth.is_oauth
    assert auth.headers == {"Authorization": "Bearer access-1"}
    assert auth.params == {}


@pytest.mark.asyncio
async def test_legacy_credentials_are_used_without_oauth_connection():
    auth = await trello_service.get_auth_for_company(
        {"id": 7, "trello_api_key": "key", "trello_token": "tok"}
    )
    assert not auth.is_oauth
    assert auth.params == {"key": "key", "token": "tok"}
    assert auth.headers == {}


@pytest.mark.asyncio
async def test_missing_credentials_raise_auth_error():
    with pytest.raises(trello_service.TrelloAuthError):
        await trello_service.get_auth_for_company({"id": 7})


@pytest.mark.asyncio
async def test_expired_access_token_is_refreshed_and_rotated_token_stored():
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
    company = {
        "id": 9,
        "trello_oauth_access_token": encrypt_secret("old-access"),
        "trello_oauth_refresh_token": encrypt_secret("old-refresh"),
        "trello_oauth_expires_at": past,
        "trello_oauth_member": "alice",
    }
    request_token = AsyncMock(
        return_value={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
    )
    store = AsyncMock()
    with (
        patch.object(trello_service.company_repo, "get_company_by_id", AsyncMock(return_value=company)),
        patch.object(trello_service.company_repo, "set_trello_oauth_tokens", store),
        patch.object(trello_service, "_request_token", request_token),
        patch.object(
            trello_service,
            "get_oauth_client_settings",
            AsyncMock(return_value={"client_id": "cid", "client_secret": "csecret"}),
        ),
    ):
        auth = await trello_service.get_auth_for_company(company)

    assert auth.headers == {"Authorization": "Bearer new-access"}
    payload = request_token.await_args.args[0]
    assert payload == {
        "grant_type": "refresh_token",
        "client_id": "cid",
        "client_secret": "csecret",
        "refresh_token": "old-refresh",
    }
    kwargs = store.await_args.kwargs
    assert decrypt_secret(kwargs["access_token"]) == "new-access"
    assert decrypt_secret(kwargs["refresh_token"]) == "new-refresh"
    assert kwargs["member"] == "alice"
    assert kwargs["expires_at"] is not None


@pytest.mark.asyncio
async def test_refresh_failure_surfaces_as_auth_error():
    company = {
        "id": 10,
        "trello_oauth_refresh_token": encrypt_secret("refresh"),
    }
    with (
        patch.object(trello_service.company_repo, "get_company_by_id", AsyncMock(return_value=company)),
        patch.object(
            trello_service,
            "_request_token",
            AsyncMock(side_effect=trello_service.TrelloOAuthError("HTTP 400")),
        ),
        patch.object(
            trello_service,
            "get_oauth_client_settings",
            AsyncMock(return_value={"client_id": "cid", "client_secret": "csecret"}),
        ),
    ):
        with pytest.raises(trello_service.TrelloAuthError):
            await trello_service.get_auth_for_company(company)


@pytest.mark.asyncio
async def test_list_webhooks_skips_oauth_connections():
    auth = trello_service.TrelloAuth(mode="oauth", headers={"Authorization": "Bearer x"})
    assert await trello_service.list_webhooks(auth) == []


@pytest.mark.asyncio
async def test_exchange_authorization_code_sends_pkce_verifier():
    request_token = AsyncMock(
        return_value={"access_token": "a", "refresh_token": "r", "expires_in": 3600}
    )
    store = AsyncMock()
    with (
        patch.object(trello_service, "_request_token", request_token),
        patch.object(trello_service, "_fetch_member_name", AsyncMock(return_value="bob")),
        patch.object(trello_service.company_repo, "set_trello_oauth_tokens", store),
        patch.object(
            trello_service,
            "get_oauth_client_settings",
            AsyncMock(return_value={"client_id": "cid", "client_secret": "csecret"}),
        ),
    ):
        result = await trello_service.exchange_authorization_code(
            3, code="the-code", code_verifier="verifier", redirect_uri="https://p/trello/callback"
        )

    assert result == {"member": "bob"}
    assert request_token.await_args.args[0] == {
        "grant_type": "authorization_code",
        "client_id": "cid",
        "client_secret": "csecret",
        "code": "the-code",
        "redirect_uri": "https://p/trello/callback",
        "code_verifier": "verifier",
    }
    assert store.await_args.args[0] == 3
    assert decrypt_secret(store.await_args.kwargs["access_token"]) == "a"


# ---------------------------------------------------------------------------
# Callback state handling
# ---------------------------------------------------------------------------


def _callback_request():
    return SimpleNamespace(query_params={}, base_url="https://portal.example.com/")


@pytest.mark.asyncio
async def test_callback_rejects_tampered_state():
    response = await trello_routes.trello_oauth_callback(
        _callback_request(), code="c", state="not-a-valid-state"
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/companies"


@pytest.mark.asyncio
async def test_callback_rejects_state_from_another_session():
    state = trello_routes._oauth_state_serializer().dumps(
        {"user_id": 1, "session_id": 111, "company_id": 5, "verifier": encrypt_secret("v")}
    )
    session = SimpleNamespace(user_id=1, id=222)
    exchange = AsyncMock()
    with (
        patch.object(trello_routes, "_load_super_admin_session", AsyncMock(return_value=(session, None))),
        patch.object(trello_routes.trello_service, "exchange_authorization_code", exchange),
    ):
        response = await trello_routes.trello_oauth_callback(_callback_request(), code="c", state=state)
    assert response.headers["location"] == "/admin/companies"
    exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_exchanges_code_and_returns_to_company():
    state = trello_routes._oauth_state_serializer().dumps(
        {"user_id": 1, "session_id": 111, "company_id": 5, "verifier": encrypt_secret("verifier-x")}
    )
    session = SimpleNamespace(user_id=1, id=111)
    exchange = AsyncMock(return_value={"member": "carol"})
    with (
        patch.object(trello_routes, "_load_super_admin_session", AsyncMock(return_value=(session, None))),
        patch.object(trello_routes.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 5})),
        patch.object(trello_routes.trello_service, "exchange_authorization_code", exchange),
        patch(
            "app.services.trello.get_settings",
            return_value=_settings("https://portal.example.com"),
        ),
    ):
        response = await trello_routes.trello_oauth_callback(_callback_request(), code="c", state=state)

    assert response.headers["location"] == "/admin/companies/5/edit#ce-trello"
    exchange.assert_awaited_once_with(
        5,
        code="c",
        code_verifier="verifier-x",
        redirect_uri="https://portal.example.com/trello/callback",
    )


@pytest.mark.asyncio
async def test_callback_does_not_reflect_provider_error():
    state = trello_routes._oauth_state_serializer().dumps(
        {"user_id": 1, "session_id": 111, "company_id": 5, "verifier": encrypt_secret("v")}
    )
    session = SimpleNamespace(user_id=1, id=111)
    with patch.object(
        trello_routes, "_load_super_admin_session", AsyncMock(return_value=(session, None))
    ):
        response = await trello_routes.trello_oauth_callback(
            _callback_request(), code=None, state=state, error="access_denied"
        )
    assert response.headers["location"] == "/admin/companies/5/edit#ce-trello"
    assert "access_denied" not in response.headers["location"]


# ---------------------------------------------------------------------------
# UI, migration and settings
# ---------------------------------------------------------------------------


def test_modules_action_menu_shows_trello_oauth_callback_url():
    template = (ROOT / "app/templates/admin/modules.html").read_text()
    assert '(module_webhook_urls or {}).get("trello_oauth_callback")' in template
    assert "OAuth Callback URL" in template
    main_source = (ROOT / "app/main.py").read_text()
    assert '"trello_oauth_callback": _trello_service.build_oauth_redirect_uri(' in main_source


def test_company_edit_offers_connect_trello():
    template = (ROOT / "app/templates/admin/company_edit.html").read_text()
    assert 'href="/trello/connect?company_id={{ company.id }}"' in template
    assert 'action="/trello/companies/{{ company.id }}/disconnect" method="post"' in template


def test_migration_adds_oauth_columns_idempotently():
    sql = (ROOT / "migrations/560_trello_oauth2.sql").read_text()
    assert "-- phase: expand" in sql
    for column in (
        "trello_oauth_access_token",
        "trello_oauth_refresh_token",
        "trello_oauth_expires_at",
        "trello_oauth_member",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql


def test_trello_oauth_client_secret_is_redacted_and_preserved():
    from app.services import modules as modules_service

    redacted = modules_service._redact_module_settings(
        {"slug": "trello", "settings": {"oauth_client_id": "cid", "oauth_client_secret": "s3cret"}}
    )
    assert redacted["settings"]["oauth_client_secret"] == "********"
    assert redacted["settings"]["oauth_client_id"] == "cid"

    coerced = modules_service._coerce_settings(
        "trello",
        {"oauth_client_id": " cid ", "oauth_client_secret": "********"},
        {"settings": {"oauth_client_secret": "stored-secret", "api_secret": "app-secret"}},
    )
    assert coerced["oauth_client_id"] == "cid"
    assert coerced["oauth_client_secret"] == "stored-secret"
    assert coerced["api_secret"] == "app-secret"
