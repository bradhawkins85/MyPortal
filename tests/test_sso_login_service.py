import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app.repositories import users as user_repo
from app.services import sso_login
from app.services.m365 import M365Error

pytestmark = pytest.mark.asyncio

CLAIMS = {
    "tid": "Tenant-A",
    "oid": "OID-1",
    "email": "alice@contoso.com",
    "preferred_username": "alice@contoso.com",
    "amr": ["pwd", "mfa"],
}


class Fakes:
    def __init__(self) -> None:
        self.sso_companies: list[int] = [7]
        self.identity: dict | None = None
        self.identities_by_user: dict[int, dict] = {}
        self.user: dict | None = {"id": 1, "email": "alice@contoso.com", "company_id": 7, "is_active": 1}
        self.memberships: dict[int, set[int]] = {}
        self.users: list[dict] = []
        self.created: list[dict] = []
        self.audits: list[dict] = []
        self.candidate_queries: list[tuple] = []
        self.domains: set[str] = {"contoso.com"}
        self.domain_queries: list[list[int]] = []

    @property
    def audit_actions(self) -> list[str]:
        return [entry["action"] for entry in self.audits]


@pytest.fixture
def fakes(monkeypatch):
    f = Fakes()

    async def companies_for_tenant(tenant_id):
        f.tenant_seen = tenant_id
        return f.sso_companies

    async def user_company_ids(user):
        ids = {int(user["company_id"])} if user.get("company_id") is not None else set()
        return ids | f.memberships.get(int(user["id"]), set())

    async def get_by_subject(provider, tenant_id, object_id):
        f.subject_seen = (provider, tenant_id, object_id)
        return f.identity

    async def get_for_user(user_id, provider="m365"):
        return f.identities_by_user.get(user_id)

    async def create(**kwargs):
        f.created.append(kwargs)
        return {"id": 55, **kwargs}

    async def get_user_by_id(user_id):
        return f.user

    async def list_active(email, company_ids):
        f.candidate_queries.append((email, list(company_ids)))
        wanted = set(company_ids)
        return [
            u for u in f.users
            if (u["email"] or "").lower() == email
            and (u["company_id"] in wanted or f.memberships.get(int(u["id"]), set()) & wanted)
        ]

    async def list_email_domains_for_companies(company_ids):
        f.domain_queries.append(list(company_ids))
        return set(f.domains)

    async def record(**kwargs):
        f.audits.append(kwargs)

    monkeypatch.setattr(sso_login.sso_policy, "companies_for_tenant", companies_for_tenant)
    monkeypatch.setattr(sso_login.sso_policy, "user_company_ids", user_company_ids)
    monkeypatch.setattr(sso_login.sso_identity_repo, "get_by_subject", get_by_subject)
    monkeypatch.setattr(sso_login.sso_identity_repo, "get_for_user", get_for_user)
    monkeypatch.setattr(sso_login.sso_identity_repo, "create", create)
    monkeypatch.setattr(sso_login.user_repo, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(sso_login.user_repo, "list_active_users_by_email_in_companies", list_active)
    monkeypatch.setattr(
        sso_login.company_repo, "list_email_domains_for_companies", list_email_domains_for_companies
    )
    monkeypatch.setattr(sso_login.audit_service, "record", record)
    return f


async def test_linked_user_signs_in(fakes):
    fakes.identity = {"id": 9, "user_id": 1}
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure is None and out.user["id"] == 1 and out.mfa_satisfied
    assert out.identity == {"id": 9, "user_id": 1} and out.message == ""
    assert fakes.subject_seen == ("m365", "tenant-a", "oid-1")
    assert fakes.created == [] and fakes.audits == []


async def test_linked_user_without_mfa_claim(fakes):
    fakes.identity = {"id": 9, "user_id": 1}
    out = await sso_login.resolve({**CLAIMS, "amr": ["pwd"]}, request=None)
    assert out.failure is None and out.mfa_satisfied is False


async def test_linked_user_in_secondary_company_signs_in(fakes):
    fakes.identity = {"id": 9, "user_id": 1}
    fakes.user = {"id": 1, "email": "alice@contoso.com", "company_id": 3, "is_active": 1}
    fakes.memberships = {1: {7}}
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure is None and out.user["id"] == 1


async def test_tenant_without_sso_company_fails(fakes):
    fakes.sso_companies = []
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "tenant_not_enabled"
    assert out.user is None
    assert out.message == "Microsoft sign-in is not enabled for your organisation."
    assert fakes.audits == []


async def test_linked_user_outside_tenant_companies_fails(fakes):
    fakes.identity = {"id": 9, "user_id": 1}
    fakes.user = {"id": 1, "email": "alice@contoso.com", "company_id": 99, "is_active": 1}
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "company_mismatch"
    assert out.user is None and out.message == sso_login.GENERIC_FAILURE
    assert fakes.audits[0]["action"] == "auth.sso.fail"
    assert fakes.audits[0]["metadata"]["reason"] == "company_mismatch"


async def test_linked_inactive_user_fails(fakes):
    fakes.identity = {"id": 9, "user_id": 1}
    fakes.user = {"id": 1, "email": "alice@contoso.com", "company_id": 7, "is_active": 0}
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "account_disabled"
    assert out.user is None and out.message == sso_login.GENERIC_FAILURE
    assert fakes.audit_actions == ["auth.sso.fail"]
    assert fakes.audits[0]["user_id"] == 1
    assert fakes.audits[0]["entity_type"] == "user" and fakes.audits[0]["entity_id"] == 1
    assert fakes.audits[0]["metadata"] == {
        "reason": "account_disabled",
        "tenant_id": "tenant-a",
        "authentication_method": "m365_sso",
    }


async def test_linked_user_missing_is_no_link(fakes):
    fakes.identity = {"id": 9, "user_id": 1}
    fakes.user = None
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "no_link" and out.user is None
    assert fakes.created == [] and fakes.audits == []


async def test_missing_object_id_is_no_link(fakes):
    claims = {k: v for k, v in CLAIMS.items() if k != "oid"}
    out = await sso_login.resolve(claims, request=None)
    assert out.failure == "no_link" and fakes.created == []


async def test_auto_link_single_match_creates_identity(fakes):
    fakes.users = [{"id": 1, "email": "alice@contoso.com", "company_id": 7, "is_active": 1}]
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.user["id"] == 1 and out.failure is None
    assert out.identity["id"] == 55
    assert fakes.created[0].items() >= {
        "user_id": 1,
        "provider": "m365",
        "tenant_id": "tenant-a",
        "object_id": "oid-1",
        "linked_via": "auto",
        "linked_by_user_id": None,
    }.items()
    assert "auth.sso.link" in fakes.audit_actions
    link = fakes.audits[0]
    assert link["user_id"] == 1
    assert link["entity_type"] == "user_sso_identity" and link["entity_id"] == 55
    assert link["metadata"]["linked_via"] == "auto"
    assert link["metadata"]["tenant_id"] == "tenant-a"
    assert len(link["metadata"]["object_id_hash"]) == 16
    assert "oid-1" not in json.dumps(link)


@pytest.mark.parametrize(
    "claims",
    [
        {**CLAIMS, "email": "  ALICE@Contoso.com ", "xms_edov": True},
        {**CLAIMS, "preferred_username": " Alice@CONTOSO.com  "},
        {k: v for k, v in CLAIMS.items() if k != "email"},
    ],
)
async def test_auto_link_normalises_email_and_falls_back_to_upn(fakes, claims):
    fakes.users = [{"id": 1, "email": "alice@contoso.com", "company_id": 7, "is_active": 1}]
    out = await sso_login.resolve(claims, request=None)
    assert out.user["id"] == 1
    assert fakes.candidate_queries == [("alice@contoso.com", [7])]


async def test_no_email_no_upn_is_no_link(fakes):
    claims = {k: v for k, v in CLAIMS.items() if k not in ("email", "preferred_username")}
    out = await sso_login.resolve(claims, request=None)
    assert out.failure == "no_link" and fakes.candidate_queries == [] and fakes.created == []


async def test_no_auto_link_when_user_already_has_identity(fakes):
    fakes.users = [{"id": 1, "email": "alice@contoso.com", "company_id": 7, "is_active": 1}]
    fakes.identities_by_user = {1: {"id": 3, "user_id": 1, "object_id": "other"}}
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "no_link" and out.message == sso_login.GENERIC_FAILURE
    assert out.user is None and fakes.created == [] and fakes.audits == []


async def test_no_auto_link_when_user_not_in_tenant_company(fakes):
    fakes.users = [{"id": 1, "email": "alice@contoso.com", "company_id": 99, "is_active": 1}]
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "no_link" and fakes.created == []


async def test_no_auto_link_when_tenant_shared_and_two_companies_match(fakes):
    fakes.sso_companies = [7, 8]
    fakes.users = [
        {"id": 1, "email": "alice@contoso.com", "company_id": 7, "is_active": 1},
        {"id": 2, "email": "alice@contoso.com", "company_id": 8, "is_active": 1},
    ]
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "no_link" and out.user is None
    assert fakes.created == [] and fakes.audits == []


ALICE = {"id": 1, "email": "alice@contoso.com", "company_id": 7, "is_active": 1}
BOB = {"id": 2, "email": "bob@contoso.com", "company_id": 7, "is_active": 1}


async def test_auto_link_ignores_unverified_email_and_uses_upn(fakes):
    fakes.users = [dict(ALICE), dict(BOB)]
    claims = {**CLAIMS, "email": "alice@contoso.com", "preferred_username": "bob@contoso.com"}
    out = await sso_login.resolve(claims, request=None)
    assert out.failure is None and out.user["id"] == 2
    assert fakes.candidate_queries == [("bob@contoso.com", [7])]


@pytest.mark.parametrize("flag", [False, "false", "0", "", None, "yes"])
async def test_auto_link_without_true_xms_edov_uses_upn(fakes, flag):
    fakes.users = [dict(ALICE), dict(BOB)]
    claims = {**CLAIMS, "email": "alice@contoso.com", "preferred_username": "bob@contoso.com",
              "xms_edov": flag}
    out = await sso_login.resolve(claims, request=None)
    assert out.user["id"] == 2
    assert fakes.candidate_queries == [("bob@contoso.com", [7])]


@pytest.mark.parametrize("flag", [True, "true", "True", "1"])
async def test_auto_link_with_verified_email_uses_email(fakes, flag):
    fakes.users = [dict(ALICE), dict(BOB)]
    claims = {**CLAIMS, "email": "alice@contoso.com", "preferred_username": "bob@contoso.com",
              "xms_edov": flag}
    out = await sso_login.resolve(claims, request=None)
    assert out.failure is None and out.user["id"] == 1
    assert fakes.candidate_queries == [("alice@contoso.com", [7])]


async def test_verified_email_flag_without_email_is_no_link(fakes):
    fakes.users = [dict(ALICE)]
    claims = {k: v for k, v in CLAIMS.items() if k != "email"}
    out = await sso_login.resolve({**claims, "xms_edov": True}, request=None)
    assert out.failure == "no_link" and fakes.candidate_queries == [] and fakes.created == []


@pytest.mark.parametrize("address", ["alice", "alice@@contoso.com", "a@b@contoso.com", "@", "alice@"])
async def test_auto_link_requires_exactly_one_at_sign(fakes, address):
    fakes.users = [dict(ALICE)]
    out = await sso_login.resolve({**CLAIMS, "preferred_username": address}, request=None)
    assert out.failure == "no_link" and out.message == sso_login.GENERIC_FAILURE
    assert fakes.candidate_queries == [] and fakes.created == [] and fakes.audits == []


async def test_auto_link_refused_when_domain_is_not_a_company_domain(fakes):
    fakes.users = [{**ALICE, "email": "alice@fabrikam.com"}]
    claims = {**CLAIMS, "email": "alice@fabrikam.com", "preferred_username": "alice@fabrikam.com"}
    out = await sso_login.resolve(claims, request=None)
    assert out.failure == "no_link" and out.user is None
    assert out.message == sso_login.GENERIC_FAILURE
    assert fakes.domain_queries == [[7]]
    assert fakes.created == [] and fakes.audits == []


async def test_auto_link_accepts_domain_of_any_tenant_sso_company(fakes):
    fakes.sso_companies = [7, 8]
    fakes.domains = {"contoso.com", "fabrikam.com"}
    fakes.users = [{**ALICE, "email": "alice@fabrikam.com", "company_id": 8}]
    claims = {**CLAIMS, "preferred_username": "Alice@Fabrikam.com"}
    out = await sso_login.resolve(claims, request=None)
    assert out.failure is None and out.user["id"] == 1
    assert fakes.domain_queries == [[7, 8]]


async def test_auto_link_refused_without_any_company_domains(fakes):
    fakes.domains = set()
    fakes.users = [dict(ALICE)]
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "no_link" and fakes.created == []


@pytest.mark.parametrize("flag", [True, 1])
async def test_auto_link_refused_for_super_admin(fakes, flag):
    fakes.users = [{**ALICE, "is_super_admin": flag}]
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "no_link" and out.user is None
    assert out.message == sso_login.GENERIC_FAILURE
    assert fakes.created == [] and fakes.audits == []


async def test_auto_link_refused_when_candidate_belongs_outside_tenant_companies(fakes):
    fakes.users = [dict(ALICE)]
    fakes.memberships = {1: {7, 99}}
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "no_link" and out.user is None
    assert fakes.created == [] and fakes.audits == []


async def test_auto_link_refused_when_primary_company_outside_but_member_inside(fakes):
    fakes.users = [{**ALICE, "company_id": 99}]
    fakes.memberships = {1: {7}}
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure == "no_link" and fakes.created == []


async def test_auto_link_membership_only_candidate_inside_tenant_companies_links(fakes):
    fakes.sso_companies = [7, 8]
    fakes.users = [{**ALICE, "company_id": None}]
    fakes.memberships = {1: {7, 8}}
    out = await sso_login.resolve(CLAIMS, request=None)
    assert out.failure is None and out.user["id"] == 1
    assert fakes.created[0]["linked_via"] == "auto"
    assert fakes.audit_actions == ["auth.sso.link"]


async def test_auto_link_stores_both_claims_on_the_identity(fakes):
    fakes.users = [dict(ALICE)]
    claims = {**CLAIMS, "email": "alice.smith@contoso.com", "preferred_username": "alice@contoso.com"}
    out = await sso_login.resolve(claims, request=None)
    assert out.failure is None
    assert fakes.created[0]["upn"] == "alice@contoso.com"
    assert fakes.created[0]["email"] == "alice.smith@contoso.com"


def test_mfa_satisfied_accepts_list_and_string():
    assert sso_login.mfa_satisfied({"amr": ["mfa"]}) and sso_login.mfa_satisfied({"amr": "pwd mfa"})
    assert not sso_login.mfa_satisfied({"amr": ["pwd"]}) and not sso_login.mfa_satisfied({})
    assert not sso_login.mfa_satisfied({"amr": None}) and not sso_login.mfa_satisfied({"amr": "mfax"})


def test_binding_matches():
    transaction = {"binding_hash": sso_login.hashlib.sha256(b"abc").hexdigest()}
    assert sso_login.binding_matches(transaction, "abc")
    assert not sso_login.binding_matches(transaction, "abd")
    assert not sso_login.binding_matches(transaction, None)
    assert not sso_login.binding_matches(transaction, "")
    assert not sso_login.binding_matches({}, "abc")


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("bad json")
        return self._payload


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.posts: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, **kwargs):
        self.posts.append((url, data))
        if self.error:
            raise self.error
        return self.response


TRANSACTION = {
    "flow": "sso_login",
    "code_verifier": "verifier",
    "nonce": "nonce-1",
    "client_id": "client-1",
    "redirect_uri": "https://p/m365/callback",
}


def _patch_redeem(monkeypatch, client, claims):
    seen = {}

    def factory(cls, **kwargs):
        seen["kwargs"] = kwargs
        return client

    async def validate(id_token, *, client_id):
        seen["validated"] = (id_token, client_id)
        if isinstance(claims, Exception):
            raise claims
        return claims

    monkeypatch.setattr(sso_login, "monitored_client", factory)
    monkeypatch.setattr(sso_login.m365_service, "validate_microsoft_id_token", validate)
    return seen


async def test_redeem_code_returns_claims(monkeypatch):
    client = FakeClient(FakeResponse(200, {"id_token": "jwt"}))
    seen = _patch_redeem(monkeypatch, client, {"nonce": "nonce-1", "oid": "o", "tid": "t"})
    claims = await sso_login.redeem_code(TRANSACTION, "the-code")
    assert claims["oid"] == "o"
    url, data = client.posts[0]
    assert url == "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
    assert data == {
        "client_id": "client-1",
        "grant_type": "authorization_code",
        "code": "the-code",
        "redirect_uri": "https://p/m365/callback",
        "code_verifier": "verifier",
        "scope": "openid profile email",
    }
    assert seen["validated"] == ("jwt", "client-1")
    assert seen["kwargs"] == {"timeout": 15}


async def test_redeem_code_rejects_nonce_mismatch(monkeypatch):
    client = FakeClient(FakeResponse(200, {"id_token": "jwt"}))
    _patch_redeem(monkeypatch, client, {"nonce": "other", "oid": "o", "tid": "t"})
    with pytest.raises(M365Error):
        await sso_login.redeem_code(TRANSACTION, "code")


async def test_redeem_code_rejects_non_ascii_nonce(monkeypatch):
    client = FakeClient(FakeResponse(200, {"id_token": "jwt"}))
    _patch_redeem(monkeypatch, client, {"nonce": "\u00e9", "oid": "o", "tid": "t"})
    with pytest.raises(M365Error):
        await sso_login.redeem_code({**TRANSACTION, "nonce": "nonce-1"}, "code")
    _patch_redeem(monkeypatch, client, {"nonce": "nonce-1", "oid": "o", "tid": "t"})
    with pytest.raises(M365Error):
        await sso_login.redeem_code({**TRANSACTION, "nonce": "\u00e9"}, "code")


async def test_redeem_code_rejects_missing_nonce(monkeypatch):
    client = FakeClient(FakeResponse(200, {"id_token": "jwt"}))
    _patch_redeem(monkeypatch, client, {"oid": "o", "tid": "t"})
    with pytest.raises(M365Error):
        await sso_login.redeem_code(TRANSACTION, "code")


async def test_redeem_code_rejects_missing_oid(monkeypatch):
    client = FakeClient(FakeResponse(200, {"id_token": "jwt"}))
    _patch_redeem(monkeypatch, client, {"nonce": "nonce-1", "tid": "t"})
    with pytest.raises(M365Error):
        await sso_login.redeem_code(TRANSACTION, "code")


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(400, {"error": "invalid_grant"}),
        FakeResponse(200, {}),
        FakeResponse(200, {"id_token": ""}),
        FakeResponse(200, json_error=True),
    ],
)
async def test_redeem_code_rejects_bad_token_responses(monkeypatch, response):
    _patch_redeem(monkeypatch, FakeClient(response), {"nonce": "nonce-1", "oid": "o", "tid": "t"})
    with pytest.raises(M365Error):
        await sso_login.redeem_code(TRANSACTION, "code")


async def test_redeem_code_wraps_http_errors(monkeypatch):
    import httpx

    _patch_redeem(monkeypatch, FakeClient(error=httpx.ConnectError("boom")), {})
    with pytest.raises(M365Error):
        await sso_login.redeem_code(TRANSACTION, "code")


async def test_redeem_code_propagates_validation_failure(monkeypatch):
    client = FakeClient(FakeResponse(200, {"id_token": "jwt"}))
    _patch_redeem(monkeypatch, client, M365Error("bad token"))
    with pytest.raises(M365Error):
        await sso_login.redeem_code(TRANSACTION, "code")


@pytest.fixture
def start_fakes(monkeypatch):
    captured: dict = {}

    async def create(**context):
        captured.update(context)
        return "txn-123"

    async def client_id(*, redirect_uri=None, provision=True):
        captured["client_id_redirect"] = redirect_uri
        captured["client_id_provision"] = provision
        return "client-1"

    monkeypatch.setattr(sso_login.m365_oauth_transactions, "create", create)
    monkeypatch.setattr(sso_login.m365_service, "get_effective_pkce_client_id", client_id)
    monkeypatch.setattr(sso_login.m365_service, "generate_pkce_pair", lambda: ("verifier", "challenge"))
    return captured


async def test_start_stores_binding_hash_not_binding(start_fakes):
    result = await sso_login.start(
        redirect_uri="https://p/m365/callback", login_hint="a@b.com", next_path="/tickets"
    )
    stored = start_fakes
    assert stored["flow"] == "sso_login"
    assert result.binding not in json.dumps(stored)
    assert stored["binding_hash"] == sso_login.hashlib.sha256(result.binding.encode()).hexdigest()
    assert stored["code_verifier"] == "verifier"
    assert stored["client_id"] == "client-1" and stored["next_path"] == "/tickets"
    assert stored["login_hint"] == "a@b.com"
    assert stored["redirect_uri"] == "https://p/m365/callback"
    assert start_fakes["client_id_provision"] is False
    assert result.state_transaction_id == "txn-123"
    assert "login_hint=a%40b.com" in result.authorize_url
    assert "scope=openid+profile+email" in result.authorize_url
    query = parse_qs(urlsplit(result.authorize_url).query)
    assert result.authorize_url.startswith(
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?"
    )
    assert query["response_type"] == ["code"] and query["response_mode"] == ["query"]
    assert query["code_challenge"] == ["challenge"] and query["code_challenge_method"] == ["S256"]
    assert query["prompt"] == ["select_account"] and query["client_id"] == ["client-1"]
    assert query["state"] == ["txn-123"] and query["nonce"] == [stored["nonce"]]
    assert query["redirect_uri"] == ["https://p/m365/callback"]


async def test_start_wraps_state_and_omits_login_hint(start_fakes):
    result = await sso_login.start(
        redirect_uri="https://p/m365/callback",
        login_hint=None,
        next_path=None,
        state_from_transaction=lambda txn: "signed." + txn,
    )
    query = parse_qs(urlsplit(result.authorize_url).query)
    assert query["state"] == ["signed.txn-123"]
    assert "login_hint" not in query
    assert result.state_transaction_id == "txn-123"


@pytest.fixture
def real_client_id(monkeypatch):
    from types import SimpleNamespace

    state = {"admin": None, "configured": "", "provision_calls": 0, "transactions": 0}

    async def admin_credentials():
        return state["admin"]

    async def provision(**kwargs):
        state["provision_calls"] += 1
        return "provisioned-client"

    async def create(**context):
        state["transactions"] += 1
        return "txn-1"

    monkeypatch.setattr(sso_login.m365_service, "get_admin_m365_credentials", admin_credentials)
    monkeypatch.setattr(sso_login.m365_service, "_auto_provision_and_get_pkce_client_id", provision)
    monkeypatch.setattr(
        sso_login.m365_service,
        "get_settings",
        lambda: SimpleNamespace(m365_pkce_client_id=state["configured"]),
    )
    monkeypatch.setattr(sso_login.m365_oauth_transactions, "create", create)
    return state


async def test_start_never_provisions_a_pkce_app(real_client_id):
    with pytest.raises(M365Error):
        await sso_login.start(redirect_uri="https://p/m365/callback", login_hint=None, next_path=None)
    assert real_client_id["provision_calls"] == 0
    assert real_client_id["transactions"] == 0


async def test_start_uses_stored_pkce_client_without_provisioning(real_client_id):
    real_client_id["admin"] = {"pkce_client_id": "stored-client"}
    result = await sso_login.start(
        redirect_uri="https://p/m365/callback", login_hint=None, next_path=None
    )
    assert parse_qs(urlsplit(result.authorize_url).query)["client_id"] == ["stored-client"]
    assert real_client_id["provision_calls"] == 0


async def test_start_uses_configured_pkce_client(real_client_id):
    real_client_id["configured"] = "configured-client"
    result = await sso_login.start(
        redirect_uri="https://p/m365/callback", login_hint=None, next_path=None
    )
    assert parse_qs(urlsplit(result.authorize_url).query)["client_id"] == ["configured-client"]
    assert real_client_id["provision_calls"] == 0


@pytest.mark.parametrize("fallback", [sso_login.m365_service.AZURE_CLI_CLIENT_ID, ""])
async def test_start_refuses_azure_cli_fallback_or_empty_client(start_fakes, monkeypatch, fallback):
    async def client_id(*, redirect_uri=None, provision=True):
        return fallback

    async def no_transaction(**context):
        raise AssertionError("no transaction may be created")

    monkeypatch.setattr(sso_login.m365_service, "get_effective_pkce_client_id", client_id)
    monkeypatch.setattr(sso_login.m365_oauth_transactions, "create", no_transaction)
    with pytest.raises(M365Error):
        await sso_login.start(redirect_uri="https://p/m365/callback", login_hint=None, next_path=None)


async def test_get_effective_pkce_client_id_still_provisions_by_default(real_client_id):
    from app.services import m365 as m365_module

    client = await m365_module.get_effective_pkce_client_id(redirect_uri="https://p/m365/callback")
    assert client == "provisioned-client" and real_client_id["provision_calls"] == 1


class FakeDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.rows: list[dict] = [{"id": 1}]

    async def fetch_all(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return self.rows


async def test_list_active_users_by_email_in_companies_is_parameterised(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(user_repo, "db", fake)
    rows = await user_repo.list_active_users_by_email_in_companies("alice@contoso.com", [7, 8])
    assert rows == [{"id": 1}]
    sql, params = fake.calls[0]
    assert "alice" not in sql and "7" not in sql
    assert sql.count("%s") == 5 and params == ("alice@contoso.com", 7, 8, 7, 8)
    assert "LOWER(u.email) = %s" in sql and "u.is_active = 1" in sql
    assert "DISTINCT" in sql and "user_companies" in sql


async def test_list_active_users_by_email_in_companies_empty_skips_query(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(user_repo, "db", fake)
    assert await user_repo.list_active_users_by_email_in_companies("a@b.com", []) == []
    assert await user_repo.list_active_users_by_email_in_companies("", [7]) == []
    assert fake.calls == []


def test_monitored_http_redacts_oauth_code_and_verifier():
    from app.services import monitored_http

    assert monitored_http._is_sensitive("code")
    assert monitored_http._is_sensitive("code_verifier")
