"""Asset and company tags: automatic rules, the repository, the API guards and the picker."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader

from app.features.tags import PACK
from app.features.tags import routes as tags_routes
from app.repositories import tags as tags_repo
from app.services import tags as tag_rules

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# Automatic rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asset", "expected"),
    [
        ({"type": "server", "os_name": "Windows Server 2022 Standard"}, {"server", "os_windows"}),
        ({"type": "workstation", "form_factor": "Notebook", "os_name": "Windows 11 Pro"}, {"laptop", "os_windows"}),
        ({"type": "workstation", "form_factor": "Desktop", "os_name": "Windows 10 Pro"}, {"workstation", "os_windows"}),
        ({"type": "workstation", "machine_type": "virtual", "os_name": "Windows 11 Enterprise"},
         {"workstation", "virtual_machine", "os_windows"}),
        ({"type": "server", "machine_type": "virtual", "os_name": "Ubuntu 22.04 LTS"},
         {"server", "virtual_machine", "os_linux"}),
        ({"asset_type": "virtual_machine", "type": "Virtual server", "os_name": "Debian 12"},
         {"server", "virtual_machine", "os_linux"}),
        ({"type": "workstation", "form_factor": "Laptop", "os_name": "macOS 14.5 Sonoma"}, {"laptop", "os_macos"}),
        ({"asset_type": "laptop", "type": "workstation", "form_factor": "Desktop"}, {"laptop"}),
        ({"asset_type": "hypervisor", "os_name": "VMware ESXi 8"}, {"server"}),
        ({"type": "Managed Printer"}, set()),
    ],
)
def test_auto_tag_rules(asset, expected):
    assert tag_rules.auto_tag_keys(asset) == expected


def test_names_and_colours_are_normalised():
    assert tag_rules.clean_name("  Domain   controller ") == "Domain controller"
    assert tag_rules.slugify("Domain  CONTROLLER") == "domain controller"
    assert tag_rules.clean_colour("#ABCDEF") == "#abcdef"
    assert tag_rules.clean_colour("") is None
    with pytest.raises(ValueError):
        tag_rules.clean_name("   ")
    with pytest.raises(ValueError):
        tag_rules.clean_name("x" * 65)
    with pytest.raises(ValueError):
        tag_rules.clean_colour("red;background:url(x)")


# ---------------------------------------------------------------------------
# Repository against the real migration (SQLite)
# ---------------------------------------------------------------------------


class _SqliteDb:
    def __init__(self, conn):
        self.conn = conn

    @staticmethod
    def _sql(sql):
        return sql.replace("%s", "?")

    async def fetch_one(self, sql, params=None):
        cursor = await self.conn.execute(self._sql(sql), params or ())
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql, params=None):
        cursor = await self.conn.execute(self._sql(sql), params or ())
        return [dict(row) for row in await cursor.fetchall()]

    async def execute(self, sql, params=None):
        await self.conn.execute(self._sql(sql), params or ())

    async def execute_returning_lastrowid(self, sql, params=None):
        return (await self.conn.execute(self._sql(sql), params or ())).lastrowid

    @staticmethod
    def is_sqlite():
        return True


@pytest.fixture
async def sqlite_db(monkeypatch):
    from app.core.database import Database

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.executescript("""
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE assets (id INTEGER PRIMARY KEY, company_id INT, name TEXT, type TEXT, asset_type TEXT,
                             form_factor TEXT, os_name TEXT, machine_type TEXT, archived_at TEXT);
        INSERT INTO companies VALUES (1, 'Contoso'), (2, 'Fabrikam');
        INSERT INTO assets (id, company_id, name, type, form_factor, os_name, machine_type) VALUES
          (10, 1, 'DC01', 'server', NULL, 'Windows Server 2022', NULL),
          (11, 1, 'LAPTOP-01', 'workstation', 'Notebook', 'Windows 11 Pro', NULL),
          (12, 1, 'MAC-01', 'workstation', 'Laptop', 'macOS 14', NULL),
          (20, 2, 'WEB01', 'server', NULL, 'Ubuntu 22.04', 'virtual');
    """)
    for name in ("466_asset_company_tags.sql", "469_asset_tag_blocks.sql"):
        await conn.executescript(Database()._adapt_sql_for_sqlite((ROOT / "migrations" / name).read_text()))
    fake = _SqliteDb(conn)
    monkeypatch.setattr(tags_repo, "db", fake)
    yield fake
    await conn.close()


async def _names(asset_id):
    return {(tag["name"], tag["source"]) for tag in await tags_repo.list_asset_tags(asset_id)}


@pytest.mark.anyio
async def test_refresh_all_tags_existing_assets(sqlite_db):
    assert await tags_repo.refresh_all_auto_tags() == 4
    assert await _names(10) == {("Server", "auto"), ("Windows", "auto")}
    assert await _names(11) == {("Laptop", "auto"), ("Windows", "auto")}
    assert await _names(12) == {("Laptop", "auto"), ("macOS", "auto")}
    assert await _names(20) == {("Server", "auto"), ("Virtual machine", "auto"), ("Linux", "auto")}
    # Re-running is a no-op and creates no duplicate rule tags.
    await tags_repo.refresh_all_auto_tags(company_id=1)
    assert len([tag for tag in await tags_repo.list_tags() if tag["is_auto"]]) == len(tag_rules.AUTO_TAGS)


@pytest.mark.anyio
async def test_manual_tags_survive_rules_and_rules_follow_changes(sqlite_db):
    await tags_repo.refresh_all_auto_tags()
    finance, created = await tags_repo.get_or_create_tag("Finance team")
    assert created
    same, created_again = await tags_repo.get_or_create_tag("  finance   TEAM ")
    assert (same["id"], created_again) == (finance["id"], False)
    windows = await tags_repo.get_tag_by_name("windows")

    # Picking an automatic tag by hand does not duplicate it; saving manual tags leaves auto ones alone.
    await tags_repo.set_asset_manual_tags(11, [finance["id"], windows["id"], 999])
    assert await _names(11) == {("Laptop", "auto"), ("Windows", "auto"), ("Finance team", "manual")}

    # The laptop is reimaged as a Linux desktop: rule tags follow, the hand-picked tag stays.
    await sqlite_db.execute("UPDATE assets SET form_factor = 'Desktop', os_name = 'Ubuntu 24.04' WHERE id = 11")
    await tags_repo.refresh_asset_auto_tags(11)
    assert await _names(11) == {("Workstation", "auto"), ("Linux", "auto"), ("Finance team", "manual")}

    await tags_repo.set_asset_manual_tags(11, [])
    assert await _names(11) == {("Workstation", "auto"), ("Linux", "auto")}


@pytest.mark.anyio
async def test_renamed_rule_tag_keeps_working_and_cannot_be_deleted(sqlite_db):
    mapping = await tags_repo.ensure_auto_tags()
    await tags_repo.update_tag(mapping["server"], name="Servers", colour="#123456", description="All servers")
    await tags_repo.refresh_asset_auto_tags(10)
    assert ("Servers", "auto") in await _names(10)
    assert (await tags_repo.ensure_auto_tags())["server"] == mapping["server"]
    with pytest.raises(ValueError):
        await tags_repo.delete_tag(mapping["server"])
    with pytest.raises(ValueError):
        await tags_repo.update_tag(mapping["laptop"], name="servers", colour=None, description=None)


@pytest.mark.anyio
async def test_existing_hand_made_tag_is_adopted_by_rule(sqlite_db):
    existing, _ = await tags_repo.get_or_create_tag("server")
    mapping = await tags_repo.ensure_auto_tags()
    assert mapping["server"] == existing["id"]
    assert (await tags_repo.get_tag(existing["id"]))["is_auto"]


@pytest.mark.anyio
async def test_delete_tag_removes_assignments(sqlite_db):
    vip, _ = await tags_repo.get_or_create_tag("VIP")
    await tags_repo.set_asset_manual_tags(10, [vip["id"]])
    await tags_repo.set_company_tags(1, [vip["id"]])
    counts = {tag["name"]: tag for tag in await tags_repo.list_tags(with_counts=True)}
    assert (counts["VIP"]["asset_count"], counts["VIP"]["company_count"]) == (1, 1)
    assert await tags_repo.delete_tag(vip["id"])
    assert await tags_repo.list_asset_tags(10) == []
    assert await tags_repo.list_company_tags(1) == []


@pytest.mark.anyio
async def test_company_tags_and_filtering_assets_by_tag(sqlite_db):
    await tags_repo.refresh_all_auto_tags()
    managed, _ = await tags_repo.get_or_create_tag("Managed")
    await tags_repo.set_company_tags(1, [managed["id"]])
    assert [tag["name"] for tag in await tags_repo.list_company_tags(1)] == ["Managed"]
    server = (await tags_repo.ensure_auto_tags())["server"]

    # A company tag reaches every asset of that company.
    assert await tags_repo.list_asset_ids_with_tags([managed["id"]]) == [10, 11, 12]
    assert await tags_repo.list_asset_ids_with_tags([server]) == [10, 20]
    assert await tags_repo.list_asset_ids_with_tags([server, managed["id"]], match_all=True) == [10]
    assert await tags_repo.list_asset_ids_with_tags([server], company_id=2) == [20]
    await sqlite_db.execute("UPDATE assets SET archived_at = '2026-01-01' WHERE id = 20")
    assert await tags_repo.list_asset_ids_with_tags([server]) == [10]
    assert await tags_repo.list_asset_ids_with_tags([]) == []


@pytest.mark.anyio
async def test_blocked_tag_stays_off_the_asset_until_unblocked(sqlite_db):
    await tags_repo.refresh_all_auto_tags()
    mapping = await tags_repo.ensure_auto_tags()
    laptop = mapping["laptop"]
    assert ("Laptop", "auto") in await _names(11)

    # A laptop acting as a server: block Laptop, pick Server by hand.
    assert await tags_repo.block_asset_tag(11, laptop, blocked_by=5)
    await tags_repo.set_asset_manual_tags(11, [mapping["server"]])
    assert await _names(11) == {("Windows", "auto"), ("Server", "manual")}
    assert [tag["name"] for tag in await tags_repo.list_asset_blocked_tags(11)] == ["Laptop"]
    # Neither pass of the rules brings it back.
    await tags_repo.refresh_asset_auto_tags(11)
    await tags_repo.refresh_all_auto_tags()
    await tags_repo.refresh_all_auto_tags(company_id=1)
    assert ("Laptop", "auto") not in await _names(11)
    assert await tags_repo.list_asset_ids_with_tags([laptop]) == [12]
    assert await tags_repo.list_asset_ids_with_tags([mapping["server"]]) == [10, 11, 20]
    # Other assets keep the tag; unknown tags are refused.
    assert ("Laptop", "auto") in await _names(12)
    assert not await tags_repo.block_asset_tag(11, 999)

    await tags_repo.unblock_asset_tag(11, laptop)
    assert ("Laptop", "auto") in await _names(11)
    assert await tags_repo.list_asset_blocked_tags(11) == []


@pytest.mark.anyio
async def test_blocking_removes_manual_tags_and_picking_one_unblocks(sqlite_db):
    vip, _ = await tags_repo.get_or_create_tag("VIP")
    await tags_repo.set_asset_manual_tags(10, [vip["id"]])
    await tags_repo.block_asset_tag(10, vip["id"])
    assert await tags_repo.list_asset_tags(10) == []
    await tags_repo.set_asset_manual_tags(10, [vip["id"]])
    assert await _names(10) == {("VIP", "manual")}
    assert await tags_repo.list_asset_blocked_tags(10) == []
    await tags_repo.block_asset_tag(10, vip["id"])
    assert await tags_repo.delete_tag(vip["id"])
    assert await tags_repo.list_asset_blocked_tags(10) == []


@pytest.mark.anyio
async def test_blocked_tag_is_not_inherited_from_the_company(sqlite_db):
    managed, _ = await tags_repo.get_or_create_tag("Managed")
    await tags_repo.set_company_tags(1, [managed["id"]])
    await tags_repo.block_asset_tag(12, managed["id"])
    assert await tags_repo.list_asset_ids_with_tags([managed["id"]]) == [10, 11]
    await tags_repo.unblock_asset_tag(12, managed["id"])
    assert await tags_repo.list_asset_ids_with_tags([managed["id"]]) == [10, 11, 12]


@pytest.mark.anyio
async def test_search_tags(sqlite_db):
    await tags_repo.ensure_auto_tags()
    await tags_repo.get_or_create_tag("Domain controller")
    assert [tag["name"] for tag in await tags_repo.list_tags(search="DOMAIN")] == ["Domain controller"]
    assert [tag["name"] for tag in await tags_repo.list_tags(search="ac")] == ["macOS", "Virtual machine"]


# ---------------------------------------------------------------------------
# Routes and guards
# ---------------------------------------------------------------------------


def test_pack_routes():
    routes = {(method, route.path) for router in PACK.routers for route in router.routes for method in route.methods}
    assert {
        ("GET", "/api/tags"), ("POST", "/api/tags"),
        ("GET", "/api/assets/{asset_id}/tags"), ("PUT", "/api/assets/{asset_id}/tags"),
        ("POST", "/api/assets/{asset_id}/tags/{tag_id}/block"), ("DELETE", "/api/assets/{asset_id}/tags/{tag_id}/block"),
        ("GET", "/api/companies/{company_id}/tags"), ("PUT", "/api/companies/{company_id}/tags"),
        ("GET", "/admin/tags"), ("POST", "/admin/tags"), ("POST", "/admin/tags/refresh"),
        ("POST", "/admin/tags/{tag_id}"), ("POST", "/admin/tags/{tag_id}/delete"),
    } <= routes
    assert PACK.background_jobs == (tags_routes.refresh_auto_tags_job,)


@pytest.mark.anyio
async def test_asset_tags_are_edited_in_the_active_company_only(monkeypatch):
    async def editor(_request):
        return {"id": 5, "is_super_admin": False}, 1

    assets = {10: {"id": 10, "company_id": 1}, 20: {"id": 20, "company_id": 2}}

    async def get_asset(asset_id):
        return assets.get(asset_id)

    monkeypatch.setattr(tags_routes, "_asset_editor", editor)
    monkeypatch.setattr(tags_routes.asset_repo, "get_asset_by_id", get_asset)
    _user, asset = await tags_routes._editable_asset(None, 10)
    assert asset["id"] == 10
    for other in (20, 99):
        with pytest.raises(HTTPException) as exc:
            await tags_routes._editable_asset(None, other)
        assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_company_tags_need_super_admin(monkeypatch):
    async def technician(_request):
        return {"id": 5, "is_super_admin": False}

    monkeypatch.setattr(tags_routes, "_api_user", technician)
    with pytest.raises(HTTPException) as exc:
        await tags_routes.put_company_tags(None, 1, tags_routes.TagAssignment(tag_ids=[1]))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _env():
    return Environment(loader=FileSystemLoader(str(ROOT / "app" / "templates")), autoescape=True)


def test_picker_renders_chips_and_editor():
    template = _env().from_string('{% from "tags/_picker.html" import tag_picker %}{{ tag_picker(ctx, "p") }}')
    ctx = {
        "endpoint": "/api/assets/10/tags", "can_edit": True,
        "tags": [{"id": 1, "name": "Server", "colour": "#059669", "source": "auto", "description": ""},
                 {"id": 2, "name": "<VIP>", "colour": None, "source": "manual", "description": ""}],
    }
    html = template.render(ctx=ctx)
    assert 'data-endpoint="/api/assets/10/tags"' in html and "data-editable" in html
    assert 'role="combobox"' in html and 'aria-controls="p-list"' in html
    assert "Assigned automatically" in html
    assert "&lt;VIP&gt;" in html and "<VIP>" not in html.split("data-tag-picker-initial>")[0]

    read_only = template.render(ctx={**ctx, "can_edit": False, "tags": []})
    assert "data-editable" not in read_only and "combobox" not in read_only and "No tags yet." in read_only
    assert "data-blocking" not in html and "Blocked on this device" not in html

    blocking = template.render(ctx={**ctx, "blocked": [
        {"id": 3, "name": "Workstation", "colour": None, "source": "blocked", "description": ""}]})
    assert "data-blocking" in blocking and "data-tag-picker-initial-blocked" in blocking
    assert "Blocked on this device" in blocking and "tag-chip--blocked" in blocking
    empty = template.render(ctx={**ctx, "blocked": []})
    assert "data-blocking" in empty and "data-tag-picker-blocked hidden" in empty


def test_pages_include_tags_only_when_available():
    detail = (ROOT / "app/templates/assets/detail.html").read_text()
    assert '{% if asset_tags is not none %}' in detail and 'tag_picker(asset_tags' in detail
    assert "/static/js/tag_picker.js" in detail
    company = (ROOT / "app/templates/admin/company_edit.html").read_text()
    assert 'data-company-edit-section="tags"' in company and '("tags", "ce-tags", "Tags"' in company
    assets_routes = (ROOT / "app/features/assets/routes.py").read_text()
    assert '_feature_pack_available("tags")' in assets_routes


@pytest.mark.anyio
async def test_only_technicians_with_asset_write_access_edit_tags(monkeypatch):
    import app.main as main_module

    technicians = {7}

    async def is_technician(user, _request=None):
        return user["id"] in technicians

    monkeypatch.setattr(main_module, "_is_helpdesk_technician", is_technician)
    monkeypatch.setattr(main_module, "_membership_menu_can", lambda _user, membership, _key, write=False: membership["write"])
    assert await tags_routes.can_edit_tags(None, {"id": 1, "is_super_admin": True}, {"write": False})
    assert await tags_routes.can_edit_tags(None, {"id": 7}, {"write": True})
    # A customer user with asset write access, and a technician without it.
    assert not await tags_routes.can_edit_tags(None, {"id": 8}, {"write": True})
    assert not await tags_routes.can_edit_tags(None, {"id": 7}, {"write": False})
