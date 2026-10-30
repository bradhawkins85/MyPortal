"""Admin and profile pages for per-company Microsoft 365 sign-in."""
import base64
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.features.companies import handlers as company_handlers
from app.main import app, scheduler_service
from app.repositories import companies as company_repo
from app.repositories import m365 as m365_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import user_sso_identities as sso_identity_repo
from app.repositories import users as user_repo
from app.services import audit as audit_service
from app.services import sso_admin, sso_policy

TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OID = "11111111-2222-3333-4444-555555555555"
EDIT_URL = "/admin/companies/7/edit"
SECTION_URL = EDIT_URL + "#ce-microsoft-sign-in"

SUPER = {"id": 1, "email": "root@example.com", "is_super_admin": True, "company_id": None}
COMPANY_ADMIN = {"id": 2, "email": "admin@acme.test", "is_super_admin": False, "company_id": 7}
MEMBER = {"id": 3, "email": "member@acme.test", "is_super_admin": False, "company_id": 7}


@pytest.fixture(autouse=True)
def mock_database(monkeypatch):
    async def noop(*args, **kwargs):
        return None

    async def empty_list(*args, **kwargs):
        return []

    monkeypatch.setattr(db, "fetch_all", empty_list)
    monkeypatch.setattr(db, "fetch_one", noop)
    monkeypatch.setattr(db, "execute", noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


@pytest.fixture(scope="module")
def app_client():
    """One application start-up for the module: feature-pack routes mount in the lifespan."""

    async def noop(*args, **kwargs):
        return None

    async def empty_list(*args, **kwargs):
        return []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(db, "fetch_all", empty_list)
        patch.setattr(db, "fetch_one", noop)
        patch.setattr(db, "execute", noop)
        patch.setattr(db, "connect", noop)
        patch.setattr(db, "disconnect", noop)
        patch.setattr(db, "run_migrations", noop)
        patch.setattr(main_module.change_log_service, "sync_change_log_sources", noop)
        patch.setattr(main_module.modules_service, "ensure_default_modules", noop)
        patch.setattr(main_module.automations_service, "refresh_all_schedules", noop)
        patch.setattr(scheduler_service, "start", noop)
        patch.setattr(scheduler_service, "stop", noop)
        patch.setattr(main_module.settings, "enable_csrf", False)
        with TestClient(app, follow_redirects=False) as test_client:
            yield test_client


class World:
    def __init__(self) -> None:
        self.actor: dict = SUPER
        self.managed: set[int] = {7}
        self.companies = {
            7: {"id": 7, "name": "Acme", "sso_mode": "off", "csp_tenant_id": TENANT},
            9: {"id": 9, "name": "Other", "sso_mode": "off", "csp_tenant_id": None},
        }
        self.users = {
            1: SUPER,
            2: COMPANY_ADMIN,
            3: MEMBER,
        }
        self.members: list[dict] = [
            {"id": 2, "email": "admin@acme.test", "first_name": "Ada", "last_name": "Admin",
             "is_super_admin": False, "is_active": 1, "company_id": 7},
            {"id": 3, "email": "member@acme.test", "first_name": "Mia", "last_name": "Member",
             "is_super_admin": False, "is_active": 1, "company_id": 7},
        ]
        self.identities: dict[int, dict] = {}
        self.memberships: dict[int, set[int]] = {}
        self.audits: list[dict] = []
        self.list_for_users_calls: list[list[int]] = []
        self.mode_calls: list[tuple] = []
        self.link_calls: list[dict] = []
        self.unlink_calls: list[dict] = []
        self.link_error: Exception | None = None
        self.mode_error: Exception | None = None


def _identity(user_id: int, *, via: str = "auto", upn: str = "mia@acme.onmicrosoft.com", last=None) -> dict:
    return {
        "id": 100 + user_id,
        "user_id": user_id,
        "provider": "m365",
        "tenant_id": TENANT,
        "object_id": OID,
        "upn": upn,
        "email": upn,
        "linked_via": via,
        "last_login_at": last,
    }


@pytest.fixture
def world(monkeypatch):
    w = World()

    async def scope(request, user, include_archived=False):
        if user.get("is_super_admin"):
            return True, [{"id": cid} for cid in sorted(w.companies)], {}
        if not w.managed:
            raise HTTPException(status_code=403, detail="Access denied")
        return False, [{"id": cid} for cid in sorted(w.managed)], {}

    async def require_super(request):
        if not w.actor.get("is_super_admin"):
            raise HTTPException(status_code=403, detail="Super admin privileges required")
        return w.actor, None

    async def require_user(request):
        return w.actor, None

    async def get_company_by_id(company_id):
        return w.companies.get(company_id)

    async def get_credentials(company_id):
        return None

    async def get_user_by_id(user_id):
        return w.users.get(user_id)

    async def list_company_members(company_id):
        return [dict(row) for row in w.members]

    async def list_companies_for_user(user_id):
        return [{"company_id": cid} for cid in w.memberships.get(user_id, set())]

    async def list_for_users(user_ids, provider="m365"):
        w.list_for_users_calls.append(list(user_ids))
        return {uid: w.identities[uid] for uid in user_ids if uid in w.identities}

    async def get_for_user(user_id, provider="m365"):
        return w.identities.get(user_id)

    async def delete_for_user(user_id, provider="m365"):
        return w.identities.pop(user_id, None)

    async def record(**kwargs):
        w.audits.append(kwargs)

    async def set_mode(company_id, mode, *, actor, request):
        w.mode_calls.append((company_id, mode, actor["id"]))
        if w.mode_error:
            raise w.mode_error
        w.companies[company_id]["sso_mode"] = mode

    async def link(**kwargs):
        w.link_calls.append(kwargs)
        if w.link_error:
            raise w.link_error
        return {"id": 1}

    async def unlink(**kwargs):
        w.unlink_calls.append(kwargs)
        return {"id": 1}

    monkeypatch.setattr(company_handlers, "_get_company_management_scope", scope)
    monkeypatch.setattr(main_module, "_require_super_admin_page", require_super)
    monkeypatch.setattr(main_module, "_require_authenticated_user", require_user)
    monkeypatch.setattr(company_repo, "get_company_by_id", get_company_by_id)
    monkeypatch.setattr(m365_repo, "get_credentials", get_credentials)
    monkeypatch.setattr(user_repo, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(user_repo, "list_company_members", list_company_members)
    monkeypatch.setattr(user_company_repo, "list_companies_for_user", list_companies_for_user)
    monkeypatch.setattr(sso_identity_repo, "list_for_users", list_for_users)
    monkeypatch.setattr(sso_identity_repo, "get_for_user", get_for_user)
    monkeypatch.setattr(sso_identity_repo, "delete_for_user", delete_for_user)
    monkeypatch.setattr(audit_service, "record", record)
    monkeypatch.setattr(sso_policy, "set_company_sso_mode", set_mode)
    monkeypatch.setattr(sso_admin, "link_identity", link)
    monkeypatch.setattr(sso_admin, "unlink_identity", unlink)
    return w


@pytest.fixture
def client(world, app_client):
    app_client.cookies.clear()
    return app_client


def _flash(response) -> dict | None:
    raw = response.cookies.get("_flash")
    if not raw:
        return None
    signed = base64.b64decode(raw.strip('"').encode()).decode()
    return json.loads(signed.rsplit("|", 1)[0])


def _tag(html: str, needle: str) -> str:
    start = html.rindex("<", 0, html.index(needle))
    return html[start : html.index(">", start) + 1]


def _section(html: str) -> str:
    start = html.index('id="ce-microsoft-sign-in"')
    end = html.find('data-company-edit-section="staff-intake-fields"', start)
    return html[start : end if end != -1 else len(html)]


# --- edit company page -------------------------------------------------------

def test_edit_company_shows_sso_section_for_super_admin(world, client):
    html = client.get(EDIT_URL).text
    assert 'id="ce-microsoft-sign-in"' in html
    assert "data-sso-mode" in html
    assert 'data-ce-link="microsoft-sign-in"' in html
    assert "company_sso_admin.js" in html
    assert 'id="company-sso-data"' in html
    assert 'id="sso-link-modal"' in html
    assert 'role="dialog"' in _tag(html, 'id="sso-link-modal"')


def test_mode_control_lists_three_options_with_help(world, client):
    section = _section(client.get(EDIT_URL).text)
    for value in ("off", "optional", "required"):
        assert f'name="sso_mode" value="{value}"' in section
    assert "Only MyPortal passwords and passkeys." in section
    assert "People can choose Microsoft or their MyPortal password." in section
    assert "Linked people must use Microsoft. Unlinked people keep their password." in section
    assert 'action="/admin/companies/7/sso/mode"' in section


def test_every_sso_form_includes_the_csrf_partial():
    from pathlib import Path

    template = Path("app/templates/admin/company_edit.html").read_text()
    start = template.index('id="ce-microsoft-sign-in"')
    source = template[start : template.index('id="sso-link-modal"', start)]
    assert source.count("<form") == source.count('{% include "partials/csrf.html" %}') == 3


def test_current_mode_is_checked(world, client):
    world.companies[7]["sso_mode"] = "optional"
    section = _section(client.get(EDIT_URL).text)
    assert "checked" in _tag(section, 'value="optional"')
    assert "checked" not in _tag(section, 'value="off"')


def test_required_option_disabled_without_tenant(world, client):
    world.companies[7]["csp_tenant_id"] = None
    world.identities[3] = _identity(3)
    section = _section(client.get(EDIT_URL).text)
    assert "disabled" in _tag(section, 'value="required"')
    assert "Not connected" in section


def test_required_option_disabled_without_linked_users(world, client):
    section = _section(client.get(EDIT_URL).text)
    assert "disabled" in _tag(section, 'value="required"')


def test_required_option_enabled_with_tenant_and_link(world, client):
    world.identities[3] = _identity(3)
    section = _section(client.get(EDIT_URL).text)
    assert "disabled" not in _tag(section, 'value="required"')
    assert "Tenant connected" in section


def test_required_mode_warns_about_unlinked_members(world, client):
    world.companies[7]["sso_mode"] = "required"
    world.identities[3] = _identity(3)
    section = _section(client.get(EDIT_URL).text)
    warning = section[section.index("data-sso-unlinked-warning") :]
    assert "keep password sign-in" in warning
    assert "Ada Admin" in warning
    assert "hidden" not in _tag(section, "data-sso-unlinked-warning")


def test_warning_is_hidden_until_required_is_chosen(world, client):
    world.identities[3] = _identity(3)
    section = _section(client.get(EDIT_URL).text)
    assert "hidden" in _tag(section, "data-sso-unlinked-warning")


def test_members_show_status_pills_upn_and_last_sign_in(world, client):
    world.identities[3] = _identity(3, via="admin", last=datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc))
    section = _section(client.get(EDIT_URL).text)
    assert "Linked · admin" in section
    assert "Not linked" in section
    assert "mia@acme.onmicrosoft.com" in section
    assert 'data-utc="2026-10-01T09:30:00+00:00"' in section
    assert "status status--success" in section
    assert "status status--neutral" in section
    assert "strftime" not in section


def test_auto_linked_pill(world, client):
    world.identities[3] = _identity(3, via="auto")
    assert "Linked · auto" in _section(client.get(EDIT_URL).text)


def test_empty_state_when_nobody_linked(world, client):
    section = _section(client.get(EDIT_URL).text)
    assert "No one has signed in with Microsoft yet" in section


def test_no_javascript_link_form_takes_object_id(world, client):
    section = _section(client.get(EDIT_URL).text)
    assert 'action="/admin/companies/7/sso/link"' in section
    assert 'name="object_id"' in section
    assert 'name="user_id" value="3"' in section


def test_unlink_form_present_only_for_linked_members(world, client):
    world.identities[3] = _identity(3)
    section = _section(client.get(EDIT_URL).text)
    assert section.count('action="/admin/companies/7/sso/unlink"') == 1
    assert 'name="user_id" value="3"' in section[section.index('/sso/unlink"') :]


def test_members_are_resolved_with_one_identity_query(world, client):
    client.get(EDIT_URL)
    assert len(world.list_for_users_calls) == 1
    assert sorted(world.list_for_users_calls[0]) == [2, 3]


def test_nav_link_hidden_when_actor_cannot_manage(world, client):
    # The page itself is super-admin only; the nav entry still honours sso_can_manage.
    html = client.get(EDIT_URL).text
    assert 'data-ce-link="microsoft-sign-in"' in html


@pytest.mark.anyio
async def test_context_cannot_manage_unmanaged_company(world):
    context = await company_handlers._company_sso_context(
        COMPANY_ADMIN, 9, managed_company_ids={7}
    )
    assert context["sso_can_manage"] is False
    assert all(not member["can_manage"] for member in context["sso_members"])


@pytest.mark.anyio
async def test_context_company_admin_cannot_manage_super_admin_member(world):
    world.members.append(
        {"id": 1, "email": "root@example.com", "first_name": "Root", "last_name": "",
         "is_super_admin": True, "is_active": 1, "company_id": 7}
    )
    context = await company_handlers._company_sso_context(
        COMPANY_ADMIN, 7, managed_company_ids={7}
    )
    by_id = {member["user_id"]: member for member in context["sso_members"]}
    assert context["sso_can_manage"] is True
    assert by_id[3]["can_manage"] is True
    assert by_id[1]["can_manage"] is False
    assert by_id[1]["is_super_admin"] is True


@pytest.mark.anyio
async def test_context_includes_inactive_member_only_when_linked(world):
    world.members.append(
        {"id": 8, "email": "gone@acme.test", "first_name": "", "last_name": "",
         "is_super_admin": False, "is_active": 0, "company_id": 7}
    )
    world.members.append(
        {"id": 9, "email": "gone2@acme.test", "first_name": "", "last_name": "",
         "is_super_admin": False, "is_active": 0, "company_id": 7}
    )
    world.identities[9] = _identity(9)
    context = await company_handlers._company_sso_context(SUPER, 7, managed_company_ids=set())
    ids = {member["user_id"] for member in context["sso_members"]}
    assert 8 not in ids and 9 in ids
    assert context["sso_linked_count"] == 1


# --- mode form ----------------------------------------------------------------

def test_mode_form_post_redirects_with_flash(world, client):
    response = client.post("/admin/companies/7/sso/mode", data={"sso_mode": "optional"})
    assert response.status_code == 303
    assert response.headers["location"] == SECTION_URL
    assert world.mode_calls == [(7, "optional", SUPER["id"])]
    flash = _flash(response)
    assert flash["variant"] == "success"


def test_mode_form_error_is_flashed(world, client):
    world.mode_error = ValueError("Link at least one user before requiring Microsoft sign-in.")
    response = client.post("/admin/companies/7/sso/mode", data={"sso_mode": "required"})
    assert response.status_code == 303
    assert response.headers["location"] == SECTION_URL
    flash = _flash(response)
    assert flash == {
        "message": "Link at least one user before requiring Microsoft sign-in.",
        "variant": "error",
    }


def test_mode_form_requires_a_mode(world, client):
    response = client.post("/admin/companies/7/sso/mode", data={})
    assert response.status_code == 303
    assert _flash(response)["variant"] == "error"
    assert world.mode_calls == []


def test_company_admin_can_post_mode_for_managed_company(world, client):
    world.actor = COMPANY_ADMIN
    response = client.post("/admin/companies/7/sso/mode", data={"sso_mode": "optional"})
    assert response.status_code == 303
    assert world.mode_calls == [(7, "optional", COMPANY_ADMIN["id"])]


def test_company_admin_cannot_post_mode_for_other_company(world, client):
    world.actor = COMPANY_ADMIN
    response = client.post("/admin/companies/9/sso/mode", data={"sso_mode": "optional"})
    assert response.status_code == 403
    assert world.mode_calls == []


def test_member_without_scope_cannot_post_mode(world, client):
    world.actor = MEMBER
    world.managed = set()
    response = client.post("/admin/companies/7/sso/mode", data={"sso_mode": "optional"})
    assert response.status_code == 403
    assert world.mode_calls == []


# --- link / unlink forms ---------------------------------------------------------

def test_link_form_calls_service_and_redirects(world, client):
    world.actor = COMPANY_ADMIN
    response = client.post("/admin/companies/7/sso/link", data={"user_id": "3", "object_id": OID})
    assert response.status_code == 303
    assert response.headers["location"] == SECTION_URL
    assert _flash(response)["variant"] == "success"
    call = world.link_calls[0]
    assert call["target_user_id"] == 3
    assert call["company_id"] == 7
    assert call["object_id"] == OID
    assert call["actor"]["id"] == COMPANY_ADMIN["id"]
    assert call["managed_company_ids"] == {7}


def test_link_form_service_error_is_flashed(world, client):
    world.link_error = sso_admin.SsoAdminError(409, "That Microsoft account is already linked to x@y.test.")
    response = client.post("/admin/companies/7/sso/link", data={"user_id": "3", "object_id": OID})
    assert response.status_code == 303
    assert response.headers["location"] == SECTION_URL
    assert _flash(response) == {
        "message": "That Microsoft account is already linked to x@y.test.",
        "variant": "error",
    }


def test_link_form_rejects_bad_user_id(world, client):
    response = client.post("/admin/companies/7/sso/link", data={"user_id": "abc", "object_id": OID})
    assert response.status_code == 303
    assert _flash(response)["variant"] == "error"
    assert world.link_calls == []


def test_link_form_for_unmanaged_company_is_403(world, client):
    world.actor = COMPANY_ADMIN
    response = client.post("/admin/companies/9/sso/link", data={"user_id": "3", "object_id": OID})
    assert response.status_code == 403
    assert world.link_calls == []


def test_unlink_form_calls_service_and_redirects(world, client):
    world.identities[3] = _identity(3)
    response = client.post("/admin/companies/7/sso/unlink", data={"user_id": "3"})
    assert response.status_code == 303
    assert response.headers["location"] == SECTION_URL
    assert _flash(response)["variant"] == "success"
    call = world.unlink_calls[0]
    assert call["target_user_id"] == 3 and call["company_id"] == 7


def test_unlink_form_service_error_is_flashed(world, client, monkeypatch):
    async def boom(**kwargs):
        raise sso_admin.SsoAdminError(403, "You do not have permission to manage this user's Microsoft sign-in.")

    monkeypatch.setattr(sso_admin, "unlink_identity", boom)
    response = client.post("/admin/companies/7/sso/unlink", data={"user_id": "3"})
    assert response.status_code == 303
    assert _flash(response)["variant"] == "error"


def test_unlink_form_requires_csrf(world, client, monkeypatch):
    monkeypatch.setattr(main_module.settings, "enable_csrf", True)
    session = SimpleNamespace(csrf_token="expected-token", user_id=1)

    async def load_session(request, allow_inactive=True):
        return session

    monkeypatch.setattr(main_module.session_manager, "load_session", load_session)
    response = client.post("/admin/companies/7/sso/unlink", data={"user_id": "3"})
    assert response.status_code == 403
    assert world.unlink_calls == []
    ok = client.post(
        "/admin/companies/7/sso/unlink", data={"user_id": "3", "_csrf": "expected-token"}
    )
    assert ok.status_code == 303
    assert len(world.unlink_calls) == 1


# --- users page -----------------------------------------------------------------

USERS = [
    {"id": 1, "email": "root@example.com", "first_name": "Root", "last_name": "Admin",
     "company_id": None, "company_name": None, "is_super_admin": True, "last_login_at": None,
     "ai_opt_out": 0, "ai_opt_out_at": None},
    {"id": 2, "email": "ada@acme.test", "first_name": "Ada", "last_name": "A",
     "company_id": 7, "company_name": "Acme", "is_super_admin": False, "last_login_at": None,
     "ai_opt_out": 0, "ai_opt_out_at": None},
    {"id": 3, "email": "mia@acme.test", "first_name": "Mia", "last_name": "M",
     "company_id": 7, "company_name": "Acme", "is_super_admin": False, "last_login_at": None,
     "ai_opt_out": 0, "ai_opt_out_at": None},
    {"id": 4, "email": "bob@acme.test", "first_name": "Bob", "last_name": "B",
     "company_id": 7, "company_name": "Acme", "is_super_admin": False, "last_login_at": None,
     "ai_opt_out": 0, "ai_opt_out_at": None},
]


@pytest.fixture
def users_page(world, monkeypatch):
    async def list_active_users_for_admin():
        return [dict(row) for row in USERS]

    monkeypatch.setattr(user_repo, "list_active_users_for_admin", list_active_users_for_admin)


def test_users_page_shows_linked_pill(world, users_page, client):
    world.identities[2] = _identity(2, via="auto", upn="ada@acme.onmicrosoft.com")
    world.identities[3] = _identity(3, via="admin", upn="mia@acme.onmicrosoft.com")
    html = client.get("/admin/users").text
    assert "Linked · auto" in html
    assert "Linked · admin" in html
    assert "Not linked" in html
    assert "ada@acme.onmicrosoft.com" in html


def test_users_page_uses_one_identity_query(world, users_page, client):
    world.identities[2] = _identity(2)
    client.get("/admin/users")
    assert len(world.list_for_users_calls) == 1
    assert sorted(world.list_for_users_calls[0]) == [1, 2, 3, 4]


def test_users_page_unlink_form_only_for_linked_users(world, users_page, client):
    world.identities[2] = _identity(2)
    html = client.get("/admin/users").text
    assert html.count("/sso/unlink") == 1
    form = html[html.index('action="/admin/users/2/sso/unlink"') :]
    assert 'name="company_id"' not in form[: form.index("</form>")]


def test_users_unlink_post_derives_company_from_target_user(world, client):
    world.identities[2] = _identity(2)
    world.users[2] = {**COMPANY_ADMIN, "company_id": 7}
    response = client.post("/admin/users/2/sso/unlink", data={"company_id": "9"})
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users"
    assert _flash(response)["variant"] == "success"
    call = world.unlink_calls[0]
    assert call["target_user_id"] == 2 and call["company_id"] == 7
    assert call["actor"]["id"] == SUPER["id"]


def test_users_unlink_post_for_user_without_company(world, client):
    response = client.post("/admin/users/1/sso/unlink")
    assert response.status_code == 303
    assert world.unlink_calls[0]["company_id"] is None


def test_users_unlink_post_unknown_user_is_flashed(world, client):
    response = client.post("/admin/users/99/sso/unlink")
    assert response.status_code == 303
    assert _flash(response) == {"message": "User not found.", "variant": "error"}
    assert world.unlink_calls == []


def test_users_unlink_post_requires_super_admin(world, client):
    world.actor = COMPANY_ADMIN
    response = client.post("/admin/users/2/sso/unlink", data={"company_id": "7"})
    assert response.status_code == 403
    assert world.unlink_calls == []


# --- profile ----------------------------------------------------------------------

def test_profile_shows_linked_microsoft_account(world, client):
    world.actor = SUPER
    world.identities[1] = _identity(1, upn="root@root.onmicrosoft.com")
    html = client.get("/admin/profile").text
    assert "Microsoft sign-in" in html
    assert "root@root.onmicrosoft.com" in html
    assert 'action="/admin/profile/microsoft-sign-in/unlink"' in html


def test_profile_shows_not_linked(world, client):
    html = client.get("/admin/profile").text
    assert "Microsoft sign-in" in html
    assert "Not linked" in html
    assert "/admin/profile/microsoft-sign-in/unlink" not in html


def test_profile_unlink_removes_own_identity_and_audits(world, client):
    world.actor = MEMBER
    world.identities[3] = _identity(3, via="auto")
    world.companies[7]["sso_mode"] = "optional"
    response = client.post("/admin/profile/microsoft-sign-in/unlink")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/profile")
    assert _flash(response)["variant"] == "success"
    assert 3 not in world.identities
    audits = [a for a in world.audits if a["action"] == "auth.sso.unlink"]
    assert len(audits) == 1
    assert audits[0]["user_id"] == MEMBER["id"]
    assert audits[0]["metadata"]["linked_via"] == "auto"


def test_profile_unlink_refused_in_required_mode(world, client):
    world.actor = MEMBER
    world.identities[3] = _identity(3)
    world.companies[7]["sso_mode"] = "required"
    response = client.post("/admin/profile/microsoft-sign-in/unlink")
    assert response.status_code == 303
    assert _flash(response) == {
        "message": "Your organisation requires Microsoft sign-in, so this account must stay linked.",
        "variant": "error",
    }
    assert 3 in world.identities
    assert not [a for a in world.audits if a["action"] == "auth.sso.unlink"]


def test_profile_unlink_refused_when_any_company_requires(world, client):
    world.actor = MEMBER
    world.identities[3] = _identity(3)
    world.memberships[3] = {9}
    world.companies[9]["sso_mode"] = "required"
    response = client.post("/admin/profile/microsoft-sign-in/unlink")
    assert _flash(response)["variant"] == "error"
    assert 3 in world.identities


def test_profile_unlink_when_not_linked_is_info(world, client):
    world.actor = MEMBER
    response = client.post("/admin/profile/microsoft-sign-in/unlink")
    assert response.status_code == 303
    assert _flash(response)["variant"] == "info"


def test_profile_unlink_requires_csrf(world, client, monkeypatch):
    monkeypatch.setattr(main_module.settings, "enable_csrf", True)
    session = SimpleNamespace(csrf_token="expected-token", user_id=3)

    async def load_session(request, allow_inactive=True):
        return session

    monkeypatch.setattr(main_module.session_manager, "load_session", load_session)
    world.actor = MEMBER
    world.identities[3] = _identity(3)
    assert client.post("/admin/profile/microsoft-sign-in/unlink").status_code == 403
    assert 3 in world.identities


# --- SQLite string timestamps, required-mode edge cases ---------------------------

def test_edit_page_renders_string_last_login(world, client):
    world.identities[3] = _identity(3, last="2026-10-30 08:00:00")
    response = client.get(EDIT_URL)
    assert response.status_code == 200
    assert 'data-utc="2026-10-30T08:00:00+00:00"' in _section(response.text)


def test_edit_page_tolerates_unparseable_last_login(world, client):
    world.identities[3] = _identity(3, last="not a date")
    response = client.get(EDIT_URL)
    assert response.status_code == 200
    assert "signed in with microsoft yet" in _section(response.text).lower()


def test_profile_renders_string_last_login(world, client):
    world.identities[1] = _identity(1, last="2026-10-30 08:00:00")
    response = client.get("/admin/profile")
    assert response.status_code == 200
    assert 'data-utc="2026-10-30T08:00:00+00:00"' in response.text


def test_current_required_radio_stays_enabled_when_unavailable(world, client):
    world.companies[7]["sso_mode"] = "required"
    world.companies[7]["csp_tenant_id"] = None
    section = _section(client.get(EDIT_URL).text)
    tag = _tag(section, 'value="required"')
    assert "checked" in tag and "disabled" not in tag
    assert 'id="sso-required-help"' in section


def test_no_members_shows_only_one_empty_state(world, client):
    world.members = []
    section = _section(client.get(EDIT_URL).text)
    assert "No users in this company yet" in section
    assert "No one has signed in with Microsoft yet" not in section


def test_super_admin_may_unlink_own_account_even_when_required(world, client):
    world.actor = SUPER
    world.identities[1] = _identity(1)
    world.companies[7]["sso_mode"] = "required"
    world.memberships[1] = {7}
    response = client.post("/admin/profile/microsoft-sign-in/unlink")
    assert _flash(response)["variant"] == "success"
    assert 1 not in world.identities


def test_profile_hides_required_notice_for_super_admin(world, client):
    world.identities[1] = _identity(1)
    world.companies[7]["sso_mode"] = "required"
    world.memberships[1] = {7}
    html = client.get("/admin/profile").text
    assert "must stay linked" not in html
    assert 'action="/admin/profile/microsoft-sign-in/unlink"' in html


def test_profile_unlink_form_asks_for_confirmation(world, client):
    html = client.get("/admin/profile").text
    world.identities[1] = _identity(1)
    html = client.get("/admin/profile").text
    form = html[html.index('action="/admin/profile/microsoft-sign-in/unlink"') :]
    assert "confirm(" in form[: form.index(">")]


# --- static assets -------------------------------------------------------------------

def test_sso_javascript_uses_documented_hooks():
    from pathlib import Path

    script = Path("app/static/js/company_sso_admin.js").read_text()
    assert "company-sso-data" in script
    assert "X-CSRF-Token" in script
    assert "/sso/directory" in script
    assert "/sso-identity" in script
    assert "300" in script
    assert "data-sso-error" in script or "ssoError" in script


def test_sso_css_uses_prefixed_classes_and_tokens_only():
    from pathlib import Path

    css = Path("app/static/css/app.css").read_text()
    start = css.index("/* Microsoft sign-in (company edit)")
    block = css[start:]
    assert ".sso-" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block)
    assert "rgb(" not in block and "rgba(" not in block


def test_companies_pack_version_bumped():
    from app.features.companies import PACK

    assert PACK.version == "1.3.0"
