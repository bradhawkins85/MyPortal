"""Routes for Microsoft 365 single sign-on: start, callback hand-off and TOTP step."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlparse

import pyotp
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.api.routes import auth as auth_routes
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import auth as auth_repo
from app.repositories import user_sso_identities as sso_identity_repo
from app.repositories import users as user_repo
from app.security import csrf
from app.security.oauth_state import oauth_state_serializer
from app.security.session import SessionData, session_manager
from app.services import audit as audit_service
from app.services import m365 as m365_service
from app.services import m365_oauth_transactions, sso_login, sso_policy

EXPIRED = "Your Microsoft sign-in session expired. Please try again."
MS_FAILED = "Microsoft sign-in was cancelled or failed. Please try again."
SESSION_COOKIE = session_manager.session_cookie_name


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def noop():
        return None

    monkeypatch.setattr(db, "connect", noop)
    monkeypatch.setattr(db, "disconnect", noop)
    monkeypatch.setattr(db, "run_migrations", noop)
    monkeypatch.setattr(scheduler_service, "start", noop)
    monkeypatch.setattr(scheduler_service, "stop", noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


def _session(session_id: int, user_id: int) -> SessionData:
    now = datetime(2026, 1, 1)
    return SessionData(
        id=session_id, user_id=user_id, session_token=f"token-{user_id}", csrf_token="c",
        created_at=now, expires_at=now, last_seen_at=now, ip_address=None, user_agent=None,
    )


class Stubs:
    def __init__(self) -> None:
        self.transactions: dict[str, dict] = {}
        self.counter = 0
        self.counts: dict[str, int] = {}
        self.devices: list[dict] = []
        self.audit: dict[str, dict] = {}
        self.sessions_created: list[int] = []
        self.identity_logins: list[dict] = []
        self.sessions_by_token: dict[str, SessionData] = {}
        self.claims = {"tid": "tenant-1", "oid": "oid-1", "preferred_username": "a@contoso.com",
                       "email": "a@contoso.com", "amr": ["pwd", "mfa"]}
        self.result = None
        self.resolve_error: Exception | None = None
        self.redeem_error: Exception | None = None
        self.consume_error: Exception | None = None
        self.session_error: Exception | None = None
        self.identity = {"id": 77, "user_id": 1, "tenant_id": "tenant-1"}
        self.user_companies = {1}
        self.tenant_companies = [1]
        self.mode = "optional"
        self.user = {"id": 1, "email": "a@contoso.com", "first_name": "A", "last_name": "User",
                     "company_id": 1, "is_active": 1, "is_super_admin": 0}

    def outcome(self, *, user_id: int = 1, mfa: bool = True, failure: str | None = None) -> None:
        self.user["id"] = user_id
        if failure:
            self.result = sso_login.Outcome(
                user=None, identity=None, failure=failure, mfa_satisfied=False,
                message=sso_login.GENERIC_FAILURE,
            )
        else:
            self.result = sso_login.Outcome(
                user=dict(self.user), identity={"id": 77, "user_id": user_id}, failure=None,
                mfa_satisfied=mfa, message="",
            )

    def begin(self, client: TestClient, *, next_path: str | None = None, email: str | None = None) -> str:
        params = []
        if email:
            params.append("email=" + quote(email))
        if next_path:
            params.append("next=" + quote(next_path, safe=""))
        response = client.get("/auth/microsoft/start?" + "&".join(params), follow_redirects=False)
        assert response.status_code == 303, response.text
        return parse_qs(urlparse(response.headers["location"]).query)["state"][0]

    def put_transaction(self, **payload) -> str:
        self.counter += 1
        transaction_id = f"tx{self.counter}"
        self.transactions[transaction_id] = dict(payload)
        return transaction_id


@pytest.fixture
def stubs(monkeypatch):
    s = Stubs()

    async def create(**context):
        return s.put_transaction(**context)

    async def consume(transaction_id):
        if s.consume_error:
            raise s.consume_error
        return s.transactions.pop(transaction_id, None)

    async def pkce_client_id(*, redirect_uri=None):
        return "client-id"

    async def register_login_attempt(identifier, *, window_seconds, max_attempts):
        s.counts[identifier] = s.counts.get(identifier, 0) + 1
        return identifier.startswith("account:") or s.counts[identifier] <= max_attempts

    async def get_login_attempt_count(identifier, *, window_seconds):
        return s.counts.get(identifier, 0)

    async def clear_login_attempts(identifier):
        s.counts.pop(identifier, None)

    async def get_totp_authenticators(user_id):
        return s.devices

    async def claim_totp_step(authenticator_id, step):
        return True

    async def record_user_login(user_id, when):
        return dict(s.user, id=user_id)

    async def get_user_by_id(user_id):
        return dict(s.user) if int(user_id) == int(s.user["id"]) else None

    async def create_session(user_id, request, *, active_company_id=None, **kwargs):
        if s.session_error:
            raise s.session_error
        s.sessions_created.append(user_id)
        return _session(100 + len(s.sessions_created), user_id)

    def apply_session_cookies(response, session, request=None):
        response.set_cookie(SESSION_COOKIE, session.session_token, httponly=True, samesite="lax")

    async def load_session(request, **kwargs):
        return s.sessions_by_token.get(request.cookies.get(SESSION_COOKIE) or "")

    async def record(*, action, **kwargs):
        s.audit[action] = kwargs

    async def redeem(transaction, code):
        if s.redeem_error:
            raise s.redeem_error
        return dict(s.claims)

    async def resolve(claims, *, request):
        if s.resolve_error:
            raise s.resolve_error
        return s.result

    async def identity_login(identity_id, *, upn, email, when):
        s.identity_logins.append({"id": identity_id, "upn": upn, "email": email, "when": when})

    async def discover(email):
        return s.mode

    async def identity_for_user(user_id, provider="m365"):
        if s.identity and int(s.identity["user_id"]) == int(user_id):
            return dict(s.identity)
        return None

    async def user_company_ids(user):
        return set(s.user_companies)

    async def companies_for_tenant(tenant_id):
        return list(s.tenant_companies) if tenant_id == "tenant-1" else []

    async def first_company(user):
        return 1

    monkeypatch.setattr(main_module.settings, "portal_url", "https://portal.example")
    monkeypatch.setattr(m365_oauth_transactions, "create", create)
    monkeypatch.setattr(m365_oauth_transactions, "consume", consume)
    monkeypatch.setattr(m365_service, "get_effective_pkce_client_id", pkce_client_id)
    monkeypatch.setattr(auth_repo, "register_login_attempt", register_login_attempt)
    monkeypatch.setattr(auth_repo, "get_login_attempt_count", get_login_attempt_count)
    monkeypatch.setattr(auth_repo, "clear_login_attempts", clear_login_attempts)
    monkeypatch.setattr(auth_repo, "get_totp_authenticators", get_totp_authenticators)
    monkeypatch.setattr(auth_repo, "claim_totp_step", claim_totp_step)
    monkeypatch.setattr(user_repo, "record_login", record_user_login)
    monkeypatch.setattr(user_repo, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(session_manager, "create_session", create_session)
    monkeypatch.setattr(session_manager, "apply_session_cookies", apply_session_cookies)
    monkeypatch.setattr(session_manager, "load_session", load_session)
    monkeypatch.setattr(audit_service, "record", record)
    monkeypatch.setattr(sso_login, "redeem_code", redeem)
    monkeypatch.setattr(sso_login, "resolve", resolve)
    monkeypatch.setattr(sso_identity_repo, "record_login", identity_login)
    monkeypatch.setattr(sso_policy, "discover", discover)
    monkeypatch.setattr(sso_identity_repo, "get_for_user", identity_for_user)
    monkeypatch.setattr(sso_policy, "user_company_ids", user_company_ids)
    monkeypatch.setattr(sso_policy, "companies_for_tenant", companies_for_tenant)
    monkeypatch.setattr(auth_routes, "_determine_active_company_id", first_company)
    return s


@pytest.fixture
def client():
    # No lifespan: startup tasks need a real database.
    return TestClient(app)


def _set_cookies(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def _cookie_header(response, name: str) -> str:
    for header in _set_cookies(response):
        if header.startswith(name + "="):
            return header
    return ""


def _flash(response) -> dict | None:
    header = _cookie_header(response, "_flash")
    if not header:
        return None
    value = header.split("=", 1)[1].split(";", 1)[0].strip('"')
    signed = base64.b64decode(value.encode()).decode()
    return json.loads(signed.rsplit("|", 1)[0])


def _cookie_cleared(response, name: str) -> bool:
    header = _cookie_header(response, name)
    return header.startswith(name + '=""') and "Max-Age=0" in header and "expires=" in header


def _binding_cleared(response) -> bool:
    return _cookie_cleared(response, "myportal_sso_binding")


def _session_cookie_value(response) -> str | None:
    header = _cookie_header(response, SESSION_COOKIE)
    if not header:
        return None
    return header.split("=", 1)[1].split(";", 1)[0]


# --- start -----------------------------------------------------------------


def test_start_redirects_to_microsoft_and_sets_binding_cookie(client, stubs):
    r = client.get("/auth/microsoft/start?email=a@contoso.com", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?")
    cookie = _cookie_header(r, "myportal_sso_binding")
    assert "myportal_sso_binding=" in cookie and "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower() and "Path=/" in cookie and "Max-Age=600" in cookie

    query = parse_qs(urlparse(r.headers["location"]).query)
    assert query["redirect_uri"] == ["https://portal.example/m365/callback"]
    assert query["login_hint"] == ["a@contoso.com"]
    envelope = oauth_state_serializer.loads(query["state"][0])
    assert set(envelope) == {"sso_transaction_id"}
    stored = stubs.transactions[envelope["sso_transaction_id"]]
    assert stored["flow"] == "sso_login"
    binding = cookie.split("=", 1)[1].split(";", 1)[0]
    assert sso_login.binding_matches(stored, binding)


def test_start_stores_safe_next_only(client, stubs):
    state = stubs.begin(client, next_path="https://evil.example/")
    envelope = oauth_state_serializer.loads(state)
    assert stubs.transactions[envelope["sso_transaction_id"]]["next_path"] == "/"


def test_start_is_rate_limited(client, stubs):
    assert auth_routes.SSO_RATE_LIMIT_ATTEMPTS == 30
    for _ in range(auth_routes.SSO_RATE_LIMIT_ATTEMPTS):
        assert client.get("/auth/microsoft/start", follow_redirects=False).status_code == 303
    r = client.get("/auth/microsoft/start", follow_redirects=False)
    assert r.status_code == 429
    assert any(key.startswith("sso-start:") for key in stubs.counts)


# --- callback --------------------------------------------------------------


def test_callback_happy_path_sets_session_and_redirects_to_next(client, stubs):
    stubs.outcome(user_id=1, mfa=True)
    state = stubs.begin(client, next_path="/tickets")
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tickets"
    assert stubs.audit["auth.login.succeed"]["metadata"].items() >= {
        "authentication_method": "m365_sso", "mfa_satisfied_by": "microsoft"}.items()
    assert stubs.sessions_created == [1]
    assert _session_cookie_value(r) == "token-1"
    assert _binding_cleared(r)
    assert stubs.identity_logins and stubs.identity_logins[0]["id"] == 77
    assert stubs.identity_logins[0]["upn"] == "a@contoso.com"
    assert isinstance(stubs.identity_logins[0]["when"], datetime)


def test_callback_unsafe_next_falls_back_to_root(client, stubs):
    # Review Focus 1: an off-site next must never be honoured.
    for unsafe in ("https://evil.example", "//evil.example/x", "/\\evil.example"):
        stubs.outcome(user_id=1, mfa=True)
        state = stubs.begin(client, next_path=unsafe)
        r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/", unsafe


def test_callback_unsafe_stored_next_falls_back_to_root(client, stubs):
    stubs.outcome(user_id=1, mfa=True)
    state = stubs.begin(client, next_path="/tickets")
    envelope = oauth_state_serializer.loads(state)
    stubs.transactions[envelope["sso_transaction_id"]]["next_path"] = "https://evil.example"
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/"


def test_callback_replaces_existing_session_of_other_user(client, stubs):
    # Review Focus 4: a browser signed in as user 2 completes Microsoft
    # sign-in as user 1 and must end up with a new session for user 1.
    stubs.sessions_by_token["token-2"] = _session(9, 2)
    client.cookies.set(SESSION_COOKIE, "token-2")
    stubs.outcome(user_id=1, mfa=True)
    state = stubs.begin(client, next_path="/tickets")
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303
    assert stubs.sessions_created == [1]
    assert _session_cookie_value(r) == "token-1"


@pytest.mark.parametrize("binding", [None, "wrong-binding"])
def test_callback_rejects_missing_or_wrong_binding_cookie(client, stubs, binding):
    stubs.outcome(user_id=1, mfa=True)
    state = stubs.begin(client, next_path="/tickets")
    client.cookies.delete("myportal_sso_binding")
    if binding:
        client.cookies.set("myportal_sso_binding", binding)
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _flash(r) == {"message": EXPIRED, "variant": "error"}
    assert stubs.sessions_created == []
    assert _binding_cleared(r)
    # The transaction was consumed and cannot be retried with the right cookie.
    assert stubs.transactions == {}


def test_callback_rejects_replayed_state(client, stubs):
    stubs.outcome(user_id=1, mfa=True)
    state = stubs.begin(client)
    binding = client.cookies.get("myportal_sso_binding")
    first = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert first.headers["location"] == "/"
    client.cookies.set("myportal_sso_binding", binding)
    second = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert second.status_code == 303 and second.headers["location"] == "/login"
    assert _flash(second)["message"] == EXPIRED
    assert stubs.sessions_created == [1]


def test_callback_microsoft_error_redirects_to_login(client, stubs):
    stubs.outcome(user_id=1, mfa=True)
    state = stubs.begin(client)
    r = client.get(
        f"/m365/callback?error=access_denied&error_description=AADSTS65004%3A+declined&state={state}",
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/login"
    flash = _flash(r)
    assert flash == {"message": MS_FAILED, "variant": "error"}
    assert "AADSTS" not in json.dumps(flash)
    assert stubs.sessions_created == [] and _binding_cleared(r)


def test_callback_redeem_failure_redirects_to_login(client, stubs):
    stubs.outcome(user_id=1, mfa=True)
    stubs.redeem_error = m365_service.M365Error("Microsoft identity nonce did not match")
    state = stubs.begin(client)
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert _flash(r)["message"] == MS_FAILED
    assert stubs.sessions_created == []


def test_callback_missing_code_redirects_to_login(client, stubs):
    stubs.outcome(user_id=1, mfa=True)
    state = stubs.begin(client)
    r = client.get(f"/m365/callback?state={state}", follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert _flash(r)["message"] == MS_FAILED
    assert stubs.sessions_created == []


def test_callback_failure_outcome_shows_generic_message(client, stubs):
    stubs.outcome(failure="no_link")
    state = stubs.begin(client)
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _flash(r) == {"message": sso_login.GENERIC_FAILURE, "variant": "error"}
    assert stubs.sessions_created == [] and _binding_cleared(r)


def test_callback_integrity_error_shows_generic_message(client, stubs):
    import sqlite3

    stubs.resolve_error = sqlite3.IntegrityError("UNIQUE constraint failed")
    state = stubs.begin(client)
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _flash(r)["message"] == sso_login.GENERIC_FAILURE
    assert stubs.sessions_created == []


def test_session_envelope_with_sso_flow_rejected(client, stubs):
    # Review Focus 5a: an sso_login transaction behind the session-bound
    # envelope is rejected even when its user/session fields match.
    stubs.sessions_by_token["token-2"] = _session(9, 2)
    client.cookies.set(SESSION_COOKIE, "token-2")
    for flow in ("sso_login", "sso_totp"):
        transaction_id = stubs.put_transaction(flow=flow, user_id=2, session_id=9, code_verifier="v")
        state = oauth_state_serializer.dumps({"transaction_id": transaction_id})
        r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/m365", flow
        assert "expired or was already used" in _flash(r)["message"]
    assert stubs.sessions_created == []


def test_sso_envelope_with_non_sso_flow_rejected(client, stubs):
    # Review Focus 5b: a session-bound flow behind the SSO envelope is
    # rejected even with a matching binding cookie.
    stubs.outcome(user_id=1, mfa=True)
    state = stubs.begin(client)
    envelope = oauth_state_serializer.loads(state)
    stubs.transactions[envelope["sso_transaction_id"]]["flow"] = "connect"
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _flash(r)["message"] == EXPIRED
    assert stubs.sessions_created == []


def test_existing_m365_callback_flows_unchanged(client, stubs, monkeypatch):
    stubs.sessions_by_token["token-2"] = _session(9, 2)
    client.cookies.set(SESSION_COOKIE, "token-2")
    reached: list[dict] = []

    async def fake_require_user(request):
        reached.append({"path": request.url.path})
        return None, main_module.RedirectResponse("/login", status_code=303)

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_user)
    transaction_id = stubs.put_transaction(
        flow="user_m365_contacts", user_id=2, session_id=9, code_verifier="v")
    state = oauth_state_serializer.dumps({"transaction_id": transaction_id})
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert reached, "session-bound flow must reach the existing handler"
    assert r.headers["location"] == "/admin/profile#integrations"
    assert stubs.sessions_created == []


def test_oauth_state_serializer_is_shared_with_main():
    assert main_module.oauth_state_serializer is oauth_state_serializer


def test_redirect_uri_builder_moved_to_m365_service(monkeypatch):
    monkeypatch.setattr(main_module.settings, "portal_url", None)
    request = SimpleNamespace(url_for=lambda name: "http://host.example/m365/callback")
    assert m365_service.build_m365_redirect_uri(request) == "https://host.example/m365/callback"
    assert main_module._build_m365_redirect_uri(request) == "https://host.example/m365/callback"


# --- MFA branches ----------------------------------------------------------


def _totp_flow(client, stubs, *, next_path="/tickets"):
    stubs.devices = [{"id": 5, "secret": pyotp.random_base32(), "last_used_step": None}]
    stubs.claims["amr"] = ["pwd"]
    stubs.outcome(user_id=1, mfa=False)
    state = stubs.begin(client, next_path=next_path)
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login/microsoft/verify?t=")
    assert stubs.sessions_created == []
    assert _binding_cleared(r)
    pending = _cookie_header(r, "myportal_sso_pending")
    assert "HttpOnly" in pending and "samesite=lax" in pending.lower() and "Max-Age=300" in pending
    token = parse_qs(urlparse(r.headers["location"]).query)["t"][0]
    return token


def test_no_mfa_with_totp_prompts_then_totp_completes(client, stubs):
    token = _totp_flow(client, stubs)
    stored = next(iter(stubs.transactions.values()))
    assert stored["flow"] == "sso_totp" and stored["user_id"] == 1 and stored["identity_id"] == 77
    assert "binding" not in json.dumps(stored).replace("binding_hash", "")

    page = client.get("/login/microsoft/verify?t=" + quote(token), follow_redirects=False)
    assert page.status_code == 200
    assert 'action="/auth/microsoft/totp"' in page.text and 'name="code"' in page.text

    code = pyotp.TOTP(stubs.devices[0]["secret"]).now()
    r = client.post("/auth/microsoft/totp", data={"t": token, "code": code}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tickets"
    assert stubs.sessions_created == [1]
    assert stubs.audit["auth.login.succeed"]["metadata"].items() >= {
        "authentication_method": "m365_sso", "mfa_satisfied_by": "myportal_totp"}.items()
    assert stubs.identity_logins and stubs.identity_logins[0]["id"] == 77
    assert _cookie_cleared(r, "myportal_sso_pending")

    replay = client.post("/auth/microsoft/totp", data={"t": token, "code": code}, follow_redirects=False)
    assert replay.headers["location"] == "/login" and stubs.sessions_created == [1]


def test_totp_wrong_code_counts_toward_lockout(client, stubs):
    token = _totp_flow(client, stubs)
    r = client.post("/auth/microsoft/totp", data={"t": token, "code": "not-a-code"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _flash(r) == {"message": "Invalid authenticator code.", "variant": "error"}
    assert stubs.counts.get("account:a@contoso.com") == 1
    assert stubs.sessions_created == []
    assert stubs.transactions == {}


def test_totp_locked_account_refused(client, stubs):
    token = _totp_flow(client, stubs)
    stubs.counts["account:a@contoso.com"] = auth_routes.ACCOUNT_LOCKOUT_ATTEMPTS
    code = pyotp.TOTP(stubs.devices[0]["secret"]).now()
    r = client.post("/auth/microsoft/totp", data={"t": token, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert stubs.sessions_created == []


def test_totp_requires_pending_cookie(client, stubs):
    token = _totp_flow(client, stubs)
    client.cookies.delete("myportal_sso_pending")
    code = pyotp.TOTP(stubs.devices[0]["secret"]).now()
    r = client.post("/auth/microsoft/totp", data={"t": token, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/login" and _flash(r)["message"] == EXPIRED
    assert stubs.sessions_created == []


def test_totp_rejects_expired_pending(client, stubs):
    token = _totp_flow(client, stubs)
    stored = next(iter(stubs.transactions.values()))
    stored["expires_at"] = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    code = pyotp.TOTP(stubs.devices[0]["secret"]).now()
    r = client.post("/auth/microsoft/totp", data={"t": token, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/login" and _flash(r)["message"] == EXPIRED
    assert stubs.sessions_created == []


def test_totp_rejects_sso_login_transaction(client, stubs):
    # A pending-TOTP token must not be forged from an sso_login transaction,
    # even one carrying every other field the TOTP step reads.
    stubs.devices = [{"id": 5, "secret": pyotp.random_base32(), "last_used_step": None}]
    stubs.outcome(user_id=1, mfa=False)
    state = stubs.begin(client)
    envelope = oauth_state_serializer.loads(state)
    stubs.transactions[envelope["sso_transaction_id"]].update(
        user_id=1, identity_id=77,
        expires_at=datetime(2100, 1, 1, tzinfo=timezone.utc).isoformat(),
    )
    token = oauth_state_serializer.dumps({"sso_pending_id": envelope["sso_transaction_id"]})
    client.cookies.set("myportal_sso_pending", client.cookies.get("myportal_sso_binding"))
    code = pyotp.TOTP(stubs.devices[0]["secret"]).now()
    r = client.post("/auth/microsoft/totp", data={"t": token, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert stubs.sessions_created == []


def test_verify_page_without_pending_cookie_redirects(client, stubs):
    token = oauth_state_serializer.dumps({"sso_pending_id": "tx1"})
    r = client.get("/login/microsoft/verify?t=" + quote(token), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_no_mfa_without_totp_forces_enrolment(client, stubs):
    stubs.devices = []
    stubs.outcome(user_id=1, mfa=False)
    state = stubs.begin(client, next_path="/tickets")
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/security/2fa"
    assert stubs.sessions_created == [1]
    assert stubs.audit["auth.login.succeed"]["metadata"].items() >= {
        "authentication_method": "m365_sso", "mfa_satisfied_by": "none"}.items()


def test_mfa_skips_enrolment_for_user_without_totp(client, stubs):
    stubs.devices = []
    stubs.outcome(user_id=1, mfa=True)
    state = stubs.begin(client, next_path="/tickets")
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tickets"


# --- discover --------------------------------------------------------------


def test_discover_returns_mode_and_rate_limits(client, stubs):
    r = client.post("/auth/microsoft/discover", json={"email": "a@contoso.com"})
    assert r.status_code == 200 and r.json() == {"sso": "optional"}
    # Calls 2..30 succeed; the 31st is refused.
    for _ in range(2, 31):
        assert client.post("/auth/microsoft/discover", json={"email": "a@contoso.com"}).status_code == 200
    r = client.post("/auth/microsoft/discover", json={"email": "a@contoso.com"})
    assert r.status_code == 429
    assert stubs.counts["sso-discover:testclient"] == 31
    assert any(key.startswith("sso-discover:") for key in stubs.counts)


def test_microsoft_paths_are_csrf_exempt():
    assert "/auth/microsoft/" in csrf.DEFAULT_EXEMPT_PREFIXES


# --- hardening -------------------------------------------------------------


def test_callback_unexpected_error_clears_binding_cookie(client, stubs):
    stubs.outcome(user_id=1, mfa=True)
    state = stubs.begin(client)
    stubs.consume_error = RuntimeError("redis down")
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _flash(r) == {"message": MS_FAILED, "variant": "error"}
    assert _binding_cleared(r)
    assert stubs.sessions_created == []


def test_totp_unexpected_error_clears_pending_cookie(client, stubs):
    token = _totp_flow(client, stubs)
    stubs.consume_error = RuntimeError("redis down")
    code = pyotp.TOTP(stubs.devices[0]["secret"]).now()
    r = client.post("/auth/microsoft/totp", data={"t": token, "code": code}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _flash(r) == {"message": MS_FAILED, "variant": "error"}
    assert _cookie_cleared(r, "myportal_sso_pending")
    assert stubs.sessions_created == []


def test_identity_login_recorded_only_after_session_created(client, stubs):
    stubs.outcome(user_id=1, mfa=True)
    stubs.session_error = RuntimeError("session store down")
    state = stubs.begin(client, next_path="/tickets")
    r = client.get(f"/m365/callback?code=c&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert stubs.identity_logins == []


def test_totp_identity_login_recorded_only_after_session_created(client, stubs):
    token = _totp_flow(client, stubs)
    stubs.session_error = RuntimeError("session store down")
    code = pyotp.TOTP(stubs.devices[0]["secret"]).now()
    r = client.post("/auth/microsoft/totp", data={"t": token, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert stubs.identity_logins == []


def test_totp_fails_closed_when_is_active_missing(client, stubs):
    token = _totp_flow(client, stubs)
    del stubs.user["is_active"]
    code = pyotp.TOTP(stubs.devices[0]["secret"]).now()
    r = client.post("/auth/microsoft/totp", data={"t": token, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert _flash(r)["message"] == sso_login.GENERIC_FAILURE
    assert stubs.sessions_created == []


@pytest.mark.parametrize("change", ["identity_removed", "identity_replaced", "company_removed"])
def test_totp_rechecks_identity_and_company(client, stubs, change):
    token = _totp_flow(client, stubs)
    if change == "identity_removed":
        stubs.identity = None
    elif change == "identity_replaced":
        stubs.identity = {"id": 78, "user_id": 1, "tenant_id": "tenant-1"}
    else:
        stubs.user_companies = {2}
    code = pyotp.TOTP(stubs.devices[0]["secret"]).now()
    r = client.post("/auth/microsoft/totp", data={"t": token, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert _flash(r)["message"] == sso_login.GENERIC_FAILURE
    assert stubs.sessions_created == [] and stubs.identity_logins == []
