from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from app.features.m365_admin import group_cleanup_routes as routes
from app.security.menu_permissions import MENU_PERMISSIONS
from app.services import m365_group_cleanup as service
from app.services.m365 import M365Error

GROUP_ID = "11111111-1111-1111-1111-111111111111"
ALICE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BOB = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CAROL = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _user(user_id, upn, enabled=True):
    return {"id": user_id, "displayName": upn.split("@")[0].title(), "userPrincipalName": upn,
            "mail": upn, "accountEnabled": enabled}


def test_describe_group_kinds_and_blocked_groups():
    assert service.describe_group({"groupTypes": ["Unified"], "mail": "a@x"})["method"] == "graph"
    dl = service.describe_group({"mailEnabled": True, "securityEnabled": False, "mail": "dl@x"})
    assert (dl["kind"], dl["method"], dl["can_clean"]) == ("Distribution list", "exchange", True)
    assert service.describe_group({"mailEnabled": True, "securityEnabled": True, "mail": "s@x"})["kind"] == "Mail-enabled security group"
    assert service.describe_group({"securityEnabled": True})["kind"] == "Security group"
    dynamic = service.describe_group({"groupTypes": ["Unified", "DynamicMembership"]})
    assert dynamic["can_clean"] is False and "dynamic" in dynamic["blocked_reason"]
    synced = service.describe_group({"securityEnabled": True, "onPremisesSyncEnabled": True})
    assert synced["can_clean"] is False and "on-premises" in synced["blocked_reason"]


def test_staff_reason_map_never_flags_an_address_with_an_active_record():
    reasons = service.build_staff_reason_map([
        {"email": "Gone@Example.com", "enabled": 1, "is_ex_staff": 1},
        {"email": "off@example.com", "enabled": 0, "is_ex_staff": 0},
        {"email": "rehired@example.com", "enabled": 1, "is_ex_staff": 1},
        {"email": "rehired@example.com", "enabled": 1, "is_ex_staff": 0},
        {"email": "active@example.com", "enabled": None, "is_ex_staff": 0},
    ])
    assert reasons == {"gone@example.com": service.REASON_EX_STAFF, "off@example.com": service.REASON_STAFF_DISABLED}


def test_removal_reasons_combines_disabled_account_and_ex_staff():
    staff = {"bob@example.com": service.REASON_EX_STAFF}
    assert service.removal_reasons(_user(BOB, "bob@example.com", enabled=False), staff) == [
        service.REASON_DISABLED, service.REASON_EX_STAFF,
    ]
    assert service.removal_reasons(_user(ALICE, "alice@example.com"), staff) == []


@pytest.mark.anyio("asyncio")
async def test_scan_groups_removable_members_by_group(monkeypatch):
    users = [_user(ALICE, "alice@example.com"), _user(BOB, "bob@example.com", enabled=False),
             _user(CAROL, "carol@example.com")]
    group = {"id": GROUP_ID, "displayName": "Sales", "mail": "sales@example.com",
             "mailEnabled": True, "securityEnabled": False, "groupTypes": []}
    calls = []

    async def fake_get_all(_token, url):
        calls.append(url)
        if "/users?" in url:
            return users
        return [group]

    monkeypatch.setattr(service.m365_service, "acquire_access_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(service.m365_service, "_graph_get_all", fake_get_all)
    monkeypatch.setattr(service.staff_repo, "list_staff_email_statuses", AsyncMock(return_value=[
        {"email": "carol@example.com", "enabled": 1, "is_ex_staff": 1},
    ]))
    save = AsyncMock()
    monkeypatch.setattr(service.cleanup_repo, "save_scan", save)

    result = await service.scan(5)

    assert result["removable_people"] == 2
    assert [g["display_name"] for g in result["groups"]] == ["Sales"]
    sales = result["groups"][0]
    assert sales["removable_count"] == 2 and sales["kind"] == "Distribution list"
    assert {m["id"] for m in sales["members"]} == {BOB, CAROL}
    # Only people who have left are looked up; active Alice is never queried.
    assert not any(ALICE in url for url in calls)
    assert save.await_args.args[0] == 5


def _patch_clean(monkeypatch, group, members, staff_rows=()):
    monkeypatch.setattr(service.m365_service, "acquire_access_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(service.m365_service, "_graph_get", AsyncMock(return_value=group))
    monkeypatch.setattr(service.m365_service, "_graph_get_all", AsyncMock(return_value=members))
    monkeypatch.setattr(service.staff_repo, "list_staff_email_statuses", AsyncMock(return_value=list(staff_rows)))
    monkeypatch.setattr(service.cleanup_repo, "get_scan", AsyncMock(return_value=None))
    delete = AsyncMock()
    exo = AsyncMock(return_value={})
    monkeypatch.setattr(service.m365_service, "_graph_delete", delete)
    monkeypatch.setattr(service.m365_service, "_exo_invoke_command", exo)
    monkeypatch.setattr(service.m365_service, "_acquire_exo_access_token", AsyncMock(return_value=("e", "tenant")))
    return delete, exo


@pytest.mark.anyio("asyncio")
async def test_clean_m365_group_rechecks_members_and_uses_graph(monkeypatch):
    group = {"id": GROUP_ID, "displayName": "Team", "groupTypes": ["Unified"], "mail": "team@example.com"}
    # Bob was re-enabled since the scan, so only Carol (disabled) may be removed.
    members = [_user(ALICE, "alice@example.com"), _user(BOB, "bob@example.com"),
               _user(CAROL, "carol@example.com", enabled=False)]
    delete, exo = _patch_clean(monkeypatch, group, members)

    outcome = await service.clean_group(5, GROUP_ID, [BOB, CAROL])

    assert [m["id"] for m in outcome["removed"]] == [CAROL]
    delete.assert_awaited_once()
    assert delete.await_args.args[1].endswith(f"/groups/{GROUP_ID}/members/{CAROL}/$ref")
    exo.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_clean_distribution_list_uses_exchange_and_reports_failures(monkeypatch):
    group = {"id": GROUP_ID, "displayName": "All", "mailEnabled": True, "securityEnabled": False,
             "groupTypes": [], "mail": "All@Example.com"}
    members = [_user(BOB, "bob@example.com", enabled=False), _user(CAROL, "carol@example.com", enabled=False)]
    delete, exo = _patch_clean(monkeypatch, group, members)
    exo.side_effect = [{}, M365Error("denied", http_status=403)]

    outcome = await service.clean_group(5, GROUP_ID)

    assert [m["id"] for m in outcome["removed"]] == [BOB]
    assert [m["id"] for m in outcome["failed"]] == [CAROL]
    assert "Exchange Administrator" in outcome["failed"][0]["error"]
    cmd, params = exo.await_args_list[0].args[2], exo.await_args_list[0].args[3]
    assert cmd == "Remove-DistributionGroupMember"
    assert params["Identity"] == "all@example.com" and params["Member"] == "bob@example.com"
    assert outcome["remaining_count"] == 1
    delete.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_clean_refuses_dynamic_groups_and_invalid_ids(monkeypatch):
    _patch_clean(monkeypatch, {"id": GROUP_ID, "groupTypes": ["DynamicMembership"]}, [])
    with pytest.raises(ValueError, match="dynamic"):
        await service.clean_group(5, GROUP_ID)
    with pytest.raises(M365Error):
        await service.clean_group(5, "../users")


@pytest.mark.anyio("asyncio")
async def test_update_cached_group_drops_cleaned_groups(monkeypatch):
    cached = {"result": {"groups": [{"id": GROUP_ID, "members": [{"id": BOB}], "removable_count": 1},
                                    {"id": "other", "members": [{"id": CAROL}], "removable_count": 1}]}}
    monkeypatch.setattr(service.cleanup_repo, "get_scan", AsyncMock(return_value=cached))
    save = AsyncMock()
    monkeypatch.setattr(service.cleanup_repo, "save_scan", save)

    await service._update_cached_group(5, GROUP_ID, [])

    saved = save.await_args.args[1]
    assert [g["id"] for g in saved["groups"]] == ["other"]


class _Form(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


class _Request:
    def __init__(self, form):
        self._form = form

    async def form(self):
        return self._form


@pytest.mark.anyio("asyncio")
async def test_clean_route_audits_removals(monkeypatch):
    monkeypatch.setattr(routes, "_context", AsyncMock(return_value=({"id": 9}, 5, None)))
    clean = AsyncMock(return_value={
        "group": {"id": GROUP_ID, "display_name": "Sales", "kind": "Distribution list"},
        "removed": [{"id": BOB, "user_principal_name": "bob@example.com"}],
        "failed": [], "remaining_count": 0,
    })
    monkeypatch.setattr(routes.cleanup_service, "clean_group", clean)
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)

    response = await routes.clean_group(GROUP_ID, _Request(_Form(scope="selected", member_ids=[BOB])))

    assert response.status_code == 303
    assert clean.await_args.args == (5, GROUP_ID, [BOB])
    assert audit.await_args.kwargs["action"] == "m365.group_cleanup.clean"
    assert audit.await_args.kwargs["after"]["removed"] == ["bob@example.com"]


@pytest.mark.anyio("asyncio")
async def test_clean_route_requires_a_selection(monkeypatch):
    monkeypatch.setattr(routes, "_context", AsyncMock(return_value=({"id": 9}, 5, None)))
    clean = AsyncMock()
    monkeypatch.setattr(routes.cleanup_service, "clean_group", clean)

    response = await routes.clean_group(GROUP_ID, _Request(_Form(scope="selected")))

    assert response.status_code == 303
    clean.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_clean_route_uses_write_permission(monkeypatch):
    calls = []

    async def fake_require(request, key, *, write=False, detail=""):
        calls.append((key, write))
        return {"id": 1, "company_id": 5}, None

    class _Main:
        _require_menu_page_access = staticmethod(fake_require)

    monkeypatch.setattr(routes, "_main", lambda: _Main)

    class _State:
        active_company_id = 5

    class _Req:
        state = _State()

    await routes._context(_Req(), write=True)
    assert calls == [("menu.m365.group_cleanup", True)]


def test_menu_permission_registered():
    keys = {p.key: p for p in MENU_PERMISSIONS}
    assert keys["menu.m365.group_cleanup"].group == "Office 365"


def test_migration_creates_scan_cache_table():
    sql = (Path(__file__).parent.parent / "migrations" / "538_m365_group_cleanup_scans.sql").read_text()
    assert sql.startswith("-- phase: expand")
    assert "CREATE TABLE IF NOT EXISTS m365_group_cleanup_scans" in sql


def test_template_renders_groups_and_member_modal():
    base = "".join("{% block " + b + " %}{% endblock %}" for b in ("header_actions", "styles", "content", "scripts"))
    env = Environment(loader=ChoiceLoader([DictLoader({"base.html": base}), FileSystemLoader("app/templates")]))
    template = env.get_template("m365/group_cleanup.html")
    html = template.render(
        has_credentials=True, can_write=True, scan={"result": {}}, scanned_at="2026-11-02T00:00:00+00:00",
        groups=[{
            "id": GROUP_ID, "display_name": "Sales", "mail": "sales@example.com", "kind": "Distribution list",
            "can_clean": True, "blocked_reason": None, "removable_count": 1,
            "members": [{"id": BOB, "display_name": "Bob", "user_principal_name": "bob@example.com",
                         "mail": "bob@example.com", "reason_labels": ["Ex-staff in MyPortal"]}],
        }],
        summary={"groups": 1, "cleanable_groups": 1, "removable_entries": 1, "removable_people": 1,
                 "users_checked": 3, "lookup_failures": 0},
        scan_running=False, scan_error=None, csrf_token="x", static_url=lambda p: p,
    )
    assert f"/m365/group-cleanup/{GROUP_ID}/clean" in html
    assert 'name="member_ids" value="' + BOB in html
    assert "Ex-staff in MyPortal" in html

    clean_form = html.split("data-gcl-clean>", 1)[1].split("</form>", 1)[0]
    assert 'name="scope" value="all"' in clean_form
    assert "data-gcl-open" not in clean_form
    assert 'class="stat-strip gcl-stats"' in html
    assert html.count('data-filter-options="multiple"') == 2
    assert "data-column-filter-disabled" in html
