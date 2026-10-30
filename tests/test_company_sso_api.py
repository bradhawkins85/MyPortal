"""Admin REST API for per-company Microsoft 365 sign-in and identity links."""
from urllib.parse import unquote

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.api.routes import company_sso
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import companies as company_repo
from app.repositories import m365 as m365_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import user_sso_identities as sso_identity_repo
from app.repositories import users as user_repo
from app.security.session import session_manager
from app.services import audit as audit_service
from app.services import m365 as m365_service
from tests.conftest import TEST_CSRF_TOKEN, csrf_session, reset_rate_limiters

OID = "11111111-2222-3333-4444-555555555555"
OTHER_OID = "99999999-8888-7777-6666-555555555555"
TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

SUPER = {"id": 1, "email": "root@example.com", "is_super_admin": True, "company_id": None}
COMPANY_ADMIN = {"id": 2, "email": "admin@acme.test", "is_super_admin": False, "company_id": 7}
MEMBER = {"id": 3, "email": "member@acme.test", "is_super_admin": False, "company_id": 7}


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def noop():
        return None

    monkeypatch.setattr(db, "connect", noop)
    monkeypatch.setattr(db, "disconnect", noop)
    monkeypatch.setattr(db, "run_migrations", noop)
    monkeypatch.setattr(scheduler_service, "start", noop)
    monkeypatch.setattr(scheduler_service, "stop", noop)

    # Send a real CSRF token so the tests pass whichever Settings object the
    # CSRF middleware holds.
    async def load_session(request, **kwargs):
        return csrf_session()

    monkeypatch.setattr(session_manager, "load_session", load_session)
    reset_rate_limiters()


class World:
    def __init__(self) -> None:
        self.actor: dict = SUPER
        self.managed: set[int] = {7}
        self.users: dict[int, dict] = {
            1: SUPER,
            2: COMPANY_ADMIN,
            3: MEMBER,
            4: {"id": 4, "email": "dual@acme.test", "is_super_admin": False, "company_id": 7},
            5: {"id": 5, "email": "other@else.test", "is_super_admin": False, "company_id": 9},
        }
        self.memberships: dict[int, set[int]] = {4: {7, 9}}
        self.companies: dict[int, dict] = {
            7: {"id": 7, "sso_mode": "off", "csp_tenant_id": TENANT},
            8: {"id": 8, "sso_mode": "off", "csp_tenant_id": None},
            9: {"id": 9, "sso_mode": "off", "csp_tenant_id": None},
        }
        self.identities: dict[int, dict] = {}
        self.company_rows: list[dict] = []
        self.audits: list[dict] = []
        self.graph_urls: list[str] = []
        self.graph_headers: list[dict | None] = []
        self.graph_tokens: list[tuple[int, str]] = []
        self.graph_responses: list = []
        self.next_identity_id = 100

    @property
    def graph_calls(self) -> int:
        return len(self.graph_urls)


@pytest.fixture
def world(monkeypatch):
    w = World()

    async def scope(request, user, include_archived=False):
        if user.get("is_super_admin"):
            return True, [{"id": cid} for cid in sorted(w.companies)], {}
        if not w.managed:
            raise HTTPException(status_code=403, detail="Access denied")
        return False, [{"id": cid} for cid in sorted(w.managed)], {}

    async def get_user_by_id(user_id):
        return w.users.get(user_id)

    async def list_companies_for_user(user_id):
        return [{"company_id": cid} for cid in w.memberships.get(user_id, set())]

    async def get_company_by_id(company_id):
        return w.companies.get(company_id)

    async def update_company(company_id, **updates):
        w.companies[company_id].update(updates)
        return w.companies[company_id]

    async def get_credentials(company_id):
        return None

    async def get_by_subject(provider, tenant_id, object_id):
        for row in w.identities.values():
            if (row["provider"], row["tenant_id"], row["object_id"]) == (
                provider, tenant_id.lower(), object_id.lower()
            ):
                return row
        return None

    async def get_for_user(user_id, provider="m365"):
        return w.identities.get(user_id)

    async def replace_for_user(**kwargs):
        w.next_identity_id += 1
        row = {
            "id": w.next_identity_id,
            **kwargs,
            "tenant_id": kwargs["tenant_id"].lower(),
            "object_id": kwargs["object_id"].lower(),
        }
        w.identities[kwargs["user_id"]] = row
        return row

    async def delete_for_user(user_id, provider="m365"):
        return w.identities.pop(user_id, None)

    async def count_for_company(company_id, provider="m365"):
        return len(w.identities)

    async def list_for_company(company_id, provider="m365"):
        return w.company_rows

    async def record(**kwargs):
        w.audits.append(kwargs)

    async def acquire_access_token(company_id, **kwargs):
        w.graph_tokens.append((company_id, "token"))
        return "token"

    async def graph_get(access_token, url, *, extra_headers=None):
        w.graph_urls.append(url)
        w.graph_headers.append(extra_headers)
        response = w.graph_responses.pop(0) if w.graph_responses else {}
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(company_sso.company_handlers, "_get_company_management_scope", scope)
    monkeypatch.setattr(user_repo, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(user_company_repo, "list_companies_for_user", list_companies_for_user)
    monkeypatch.setattr(company_repo, "get_company_by_id", get_company_by_id)
    monkeypatch.setattr(company_repo, "update_company", update_company)
    monkeypatch.setattr(m365_repo, "get_credentials", get_credentials)
    monkeypatch.setattr(sso_identity_repo, "get_by_subject", get_by_subject)
    monkeypatch.setattr(sso_identity_repo, "get_for_user", get_for_user)
    monkeypatch.setattr(sso_identity_repo, "replace_for_user", replace_for_user)
    monkeypatch.setattr(sso_identity_repo, "delete_for_user", delete_for_user)
    monkeypatch.setattr(sso_identity_repo, "count_for_company", count_for_company)
    monkeypatch.setattr(sso_identity_repo, "list_for_company", list_for_company)
    monkeypatch.setattr(audit_service, "record", record)
    monkeypatch.setattr(m365_service, "acquire_access_token", acquire_access_token)
    monkeypatch.setattr(m365_service, "_graph_get", graph_get)
    return w


def _client(world: World, actor: dict) -> TestClient:
    world.actor = actor

    async def override_user():
        return actor

    async def override_db():
        return None

    app.dependency_overrides[auth_dependencies.get_current_user] = override_user
    app.dependency_overrides[database_dependencies.require_database] = override_db
    return TestClient(app, headers={"X-CSRF-Token": TEST_CSRF_TOKEN})


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def as_super(world):
    return _client(world, SUPER)


@pytest.fixture
def as_company_admin(world):
    return _client(world, COMPANY_ADMIN)


@pytest.fixture
def as_member(world):
    world.managed = set()
    return _client(world, MEMBER)


def _graph_user(oid=OID, upn="alice@acme.onmicrosoft.com", mail="alice@acme.test"):
    return {"id": oid, "userPrincipalName": upn, "mail": mail}


def _audit(world, action):
    return [a for a in world.audits if a["action"] == action]


# --- summary ---------------------------------------------------------------

def test_get_requires_management_scope(as_member):
    assert as_member.get("/api/companies/7/sso").status_code == 403


def test_get_summary(world, as_company_admin):
    world.companies[7]["sso_mode"] = "optional"
    world.company_rows = [
        {"user_id": 3, "email": "member@acme.test", "upn": "m@acme.onmicrosoft.com",
         "linked_via": "admin", "last_login_at": None},
    ]
    response = as_company_admin.get("/api/companies/7/sso")
    assert response.status_code == 200
    assert response.json() == {
        "mode": "optional",
        "tenant_connected": True,
        "identities": world.company_rows,
    }


def test_company_admin_cannot_get_other_company_summary(world, as_company_admin):
    assert as_company_admin.get("/api/companies/9/sso").status_code == 403


# --- mode ------------------------------------------------------------------

def test_company_admin_sets_optional(world, as_company_admin):
    response = as_company_admin.put("/api/companies/7/sso", json={"mode": "optional"})
    assert response.status_code == 200
    assert response.json()["mode"] == "optional"
    audits = _audit(world, "company.sso_mode.update")
    assert len(audits) == 1
    assert audits[0]["user_id"] == COMPANY_ADMIN["id"]
    assert audits[0]["after"] == {"sso_mode": "optional"}


def test_required_without_links_is_400(world, as_company_admin):
    response = as_company_admin.put("/api/companies/7/sso", json={"mode": "required"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Link at least one user before requiring Microsoft sign-in."
    assert world.companies[7]["sso_mode"] == "off"


def test_unknown_mode_is_422(world, as_company_admin):
    assert as_company_admin.put("/api/companies/7/sso", json={"mode": "bogus"}).status_code == 422
    assert world.companies[7]["sso_mode"] == "off" and world.audits == []


def test_mode_schema_lists_the_three_modes():
    schema = company_sso.SsoModeUpdate.model_json_schema()
    assert schema["properties"]["mode"]["enum"] == ["off", "optional", "required"]


@pytest.mark.parametrize(
    ("path", "method", "codes"),
    [
        ("/api/companies/{company_id}/sso", "get", {"403"}),
        ("/api/companies/{company_id}/sso", "put", {"400", "403"}),
        ("/api/companies/{company_id}/sso/directory", "get", {"400", "403", "502"}),
        ("/api/users/{user_id}/sso-identity", "put", {"400", "403", "404", "409", "502"}),
        ("/api/users/{user_id}/sso-identity", "delete", {"403", "404"}),
    ],
)
def test_company_sso_routes_document_error_responses(path, method, codes):
    responses = app.openapi()["paths"][path][method]["responses"]
    for code in codes:
        assert code in responses and responses[code]["description"]


def test_mode_put_for_unmanaged_company_is_403(world, as_company_admin):
    response = as_company_admin.put("/api/companies/9/sso", json={"mode": "optional"})
    assert response.status_code == 403
    assert world.companies[9]["sso_mode"] == "off"
    assert not world.audits


def test_mode_put_requires_management_scope(world, as_member):
    assert as_member.put("/api/companies/7/sso", json={"mode": "optional"}).status_code == 403


# --- directory -------------------------------------------------------------

def test_directory_search_escapes_quotes_and_limits(world, as_super):
    world.graph_responses = [{"value": [
        {"id": OID, "displayName": "Alice", "userPrincipalName": "alice@t.com", "mail": "a@t.com",
         "extra": "ignored"},
    ]}]
    response = as_super.get('/api/companies/7/sso/directory?q=al"ice')
    assert response.status_code == 200
    assert response.json() == [
        {"object_id": OID, "display_name": "Alice", "upn": "alice@t.com", "mail": "a@t.com"}
    ]
    url = world.graph_urls[-1]
    assert '"displayName:alice"' in unquote(url)
    assert '"userPrincipalName:alice"' in unquote(url)
    assert "$top=25" in url
    assert url.startswith("https://graph.microsoft.com/v1.0/users?$search=")
    assert world.graph_headers[-1] == {"ConsistencyLevel": "eventual"}
    assert world.graph_tokens == [(7, "token")]


def test_directory_query_length_is_validated(world, as_super):
    assert as_super.get("/api/companies/7/sso/directory?q=a").status_code == 422
    assert as_super.get("/api/companies/7/sso/directory?q=" + "a" * 101).status_code == 422
    assert world.graph_calls == 0


def test_directory_query_is_url_quoted(world, as_super):
    world.graph_responses = [{"value": []}]
    as_super.get("/api/companies/7/sso/directory?q=a%26top%3D999%20b")
    url = world.graph_urls[-1]
    assert "&top=999" not in url
    assert url.count("$top=") == 1


def test_directory_graph_failure_is_502(world, as_super):
    world.graph_responses = [m365_service.M365Error("boom", http_status=500)]
    response = as_super.get("/api/companies/7/sso/directory?q=alice")
    assert response.status_code == 502
    assert response.json()["detail"] == "Microsoft 365 could not be reached. Try again."


def test_directory_other_company_is_403(world, as_company_admin):
    assert as_company_admin.get("/api/companies/9/sso/directory?q=alice").status_code == 403
    assert world.graph_calls == 0


def test_directory_without_tenant_is_400(world, as_super):
    response = as_super.get("/api/companies/8/sso/directory?q=alice")
    assert response.status_code == 400
    assert world.graph_calls == 0


# --- link ------------------------------------------------------------------

def test_link_stores_identity_and_audits(world, as_company_admin):
    world.graph_responses = [_graph_user()]
    response = as_company_admin.put(
        "/api/users/3/sso-identity", json={"company_id": 7, "object_id": OID.upper()}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == 3 and body["linked_via"] == "admin"
    assert body["upn"] == "alice@acme.onmicrosoft.com"
    stored = world.identities[3]
    assert stored["tenant_id"] == TENANT
    assert stored["object_id"] == OID
    assert stored["linked_via"] == "admin"
    assert stored["linked_by_user_id"] == COMPANY_ADMIN["id"]
    assert stored["email"] == "alice@acme.test"
    url = world.graph_urls[-1]
    assert url.startswith("https://graph.microsoft.com/v1.0/users/" + OID + "?")
    assert "$select=id,userPrincipalName,mail" in url
    audits = _audit(world, "auth.sso.link")
    assert len(audits) == 1
    assert audits[0]["user_id"] == COMPANY_ADMIN["id"]
    assert audits[0]["entity_type"] == "user_sso_identity"
    assert audits[0]["metadata"] == {
        "linked_via": "admin",
        "tenant_id": TENANT,
        "object_id_hash": "666ff6ccaa5b3c07",
        "company_id": 7,
    }
    assert OID not in str(audits[0])


def test_link_object_id_hash_matches_sha256(world, as_super):
    import hashlib

    world.graph_responses = [_graph_user()]
    as_super.put("/api/users/3/sso-identity", json={"company_id": 7, "object_id": OID})
    metadata = _audit(world, "auth.sso.link")[0]["metadata"]
    assert metadata["object_id_hash"] == hashlib.sha256(OID.encode()).hexdigest()[:16]


def test_link_conflict_returns_409(world, as_super):
    world.identities[4] = {
        "id": 50, "user_id": 4, "provider": "m365", "tenant_id": TENANT, "object_id": OID,
    }
    world.graph_responses = [_graph_user()]
    response = as_super.put(
        "/api/users/3/sso-identity", json={"company_id": 7, "object_id": OID}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "That Microsoft account is already linked to dual@acme.test."
    assert 3 not in world.identities
    assert not _audit(world, "auth.sso.link")


def test_relink_same_user_same_oid_is_allowed(world, as_super):
    world.identities[3] = {
        "id": 50, "user_id": 3, "provider": "m365", "tenant_id": TENANT, "object_id": OID,
    }
    world.graph_responses = [_graph_user()]
    response = as_super.put("/api/users/3/sso-identity", json={"company_id": 7, "object_id": OID})
    assert response.status_code == 200


def test_company_admin_cannot_link_super_admin(world, as_company_admin):
    response = as_company_admin.put(
        "/api/users/1/sso-identity", json={"company_id": 7, "object_id": OID}
    )
    assert response.status_code == 403
    assert world.graph_calls == 0
    assert not world.identities


def test_company_admin_cannot_link_user_outside_scope(world, as_company_admin):
    for user_id in (4, 5):
        response = as_company_admin.put(
            f"/api/users/{user_id}/sso-identity", json={"company_id": 7, "object_id": OID}
        )
        assert response.status_code == 403
    assert world.graph_calls == 0
    assert not world.identities


def test_company_admin_cannot_link_via_unmanaged_company(world, as_company_admin):
    response = as_company_admin.put(
        "/api/users/5/sso-identity", json={"company_id": 9, "object_id": OID}
    )
    assert response.status_code == 403
    assert world.graph_calls == 0


def test_link_missing_user_is_404(world, as_super):
    response = as_super.put("/api/users/999/sso-identity", json={"company_id": 7, "object_id": OID})
    assert response.status_code == 404
    assert world.graph_calls == 0


def test_link_requires_management_scope(world, as_member):
    response = as_member.put("/api/users/3/sso-identity", json={"company_id": 7, "object_id": OID})
    assert response.status_code == 403
    assert world.graph_calls == 0


@pytest.mark.parametrize("bad", [
    "not-a-guid",
    OID + "/../me",
    OID[:-1],
    OID + "?x=1",
    "1111111122223333444455555555555555555",
    "",
    "../../me",
])
def test_link_rejects_non_guid_object_id(world, as_super, bad):
    response = as_super.put("/api/users/3/sso-identity", json={"company_id": 7, "object_id": bad})
    assert response.status_code == 422
    assert world.graph_calls == 0
    assert not world.identities


def test_link_without_tenant_is_400(world, as_super):
    response = as_super.put("/api/users/3/sso-identity", json={"company_id": 8, "object_id": OID})
    assert response.status_code == 400
    assert response.json()["detail"] == "Connect this company's Microsoft 365 tenant first."
    assert world.graph_calls == 0


def test_link_graph_not_found_is_404(world, as_super):
    world.graph_responses = [m365_service.M365Error("gone", http_status=404)]
    response = as_super.put("/api/users/3/sso-identity", json={"company_id": 7, "object_id": OID})
    assert response.status_code == 404
    assert response.json()["detail"] == "That Microsoft user was not found in this company's tenant."
    assert not world.identities


def test_link_graph_failure_is_502(world, as_super):
    world.graph_responses = [m365_service.M365Error("boom", http_status=500)]
    response = as_super.put("/api/users/3/sso-identity", json={"company_id": 7, "object_id": OID})
    assert response.status_code == 502
    assert not world.identities
    assert not world.audits


# --- unlink ----------------------------------------------------------------

def test_unlink_audits(world, as_company_admin):
    world.identities[3] = {
        "id": 50, "user_id": 3, "provider": "m365", "tenant_id": TENANT, "object_id": OID,
        "linked_via": "auto",
    }
    response = as_company_admin.delete("/api/users/3/sso-identity?company_id=7")
    assert response.status_code == 204
    assert 3 not in world.identities
    audits = _audit(world, "auth.sso.unlink")
    assert len(audits) == 1
    assert audits[0]["user_id"] == COMPANY_ADMIN["id"]
    assert audits[0]["metadata"]["linked_via"] == "auto"
    assert audits[0]["metadata"]["tenant_id"] == TENANT
    assert audits[0]["metadata"]["company_id"] == 7
    assert len(audits[0]["metadata"]["object_id_hash"]) == 16
    assert OID not in str(audits[0])


def test_unlink_without_identity_is_204_and_not_audited(world, as_company_admin):
    response = as_company_admin.delete("/api/users/3/sso-identity?company_id=7")
    assert response.status_code == 204
    assert not world.audits


def test_company_admin_cannot_unlink_super_admin(world, as_company_admin):
    world.identities[1] = {"id": 51, "user_id": 1, "provider": "m365", "tenant_id": TENANT,
                           "object_id": OID, "linked_via": "admin"}
    assert as_company_admin.delete("/api/users/1/sso-identity?company_id=7").status_code == 403
    assert 1 in world.identities


def test_company_admin_cannot_unlink_user_with_outside_membership(world, as_company_admin):
    world.identities[4] = {"id": 52, "user_id": 4, "provider": "m365", "tenant_id": TENANT,
                           "object_id": OID, "linked_via": "admin"}
    assert as_company_admin.delete("/api/users/4/sso-identity?company_id=7").status_code == 403
    assert 4 in world.identities


def test_unlink_missing_user_is_404(world, as_super):
    assert as_super.delete("/api/users/999/sso-identity?company_id=7").status_code == 404


def test_unlink_requires_company_id(world, as_super):
    assert as_super.delete("/api/users/3/sso-identity").status_code == 422


def test_unlink_requires_management_scope(world, as_member):
    assert as_member.delete("/api/users/3/sso-identity?company_id=7").status_code == 403


def test_swagger_documents_company_sso_routes():
    schema = app.openapi()
    expected = {
        ("/api/companies/{company_id}/sso", "get"),
        ("/api/companies/{company_id}/sso", "put"),
        ("/api/companies/{company_id}/sso/directory", "get"),
        ("/api/users/{user_id}/sso-identity", "put"),
        ("/api/users/{user_id}/sso-identity", "delete"),
    }
    for path, method in expected:
        assert "Company SSO" in schema["paths"][path][method]["tags"]
