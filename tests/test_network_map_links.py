"""Interfaces, links and the network map routes' access rules."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app.features.network_map import routes
from app.repositories import network_map as repo
from app.security.menu_permissions import normalize_menu_permissions

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_wired_interfaces_drop_radio_settings():
    cleaned = repo.clean_interface({"name": "eth0", "kind": "ethernet", "frequency_mhz": "5800",
                                    "radio_mode": "ap", "speed_mbps": "1000"})
    assert cleaned["frequency_mhz"] is None and cleaned["radio_mode"] is None
    assert cleaned["speed_mbps"] == 1000
    radio = repo.clean_interface({"name": "radio0", "kind": "radio", "frequency_mhz": "5800",
                                  "radio_mode": "ptp_master", "azimuth_deg": "45"})
    assert (radio["frequency_mhz"], radio["radio_mode"], radio["azimuth_deg"]) == (5800, "ptp_master", 45)


@pytest.mark.parametrize("values", [
    {"name": "", "kind": "ethernet"},
    {"name": "x", "kind": "carrier-pigeon"},
    {"name": "x", "kind": "radio", "radio_mode": "loud"},
    {"name": "x", "kind": "radio", "azimuth_deg": "400"},
    {"name": "x", "kind": "ethernet", "speed_mbps": "fast"},
])
def test_invalid_interfaces_are_rejected(values):
    with pytest.raises(ValueError):
        repo.clean_interface(values)


def test_endpoints_parse_only_known_kinds():
    assert str(repo.Endpoint.parse("interface:4")) == "interface:4"
    assert repo.Endpoint.parse("rack_port:9") == repo.Endpoint("rack_port", 9)
    for bad in ("asset:1", "interface:x", "", None):
        with pytest.raises(ValueError):
            repo.Endpoint.parse(bad)


def _fake_db(monkeypatch, interfaces, *, existing_links=()):
    """Interfaces keyed by id: (asset_id, kind)."""
    async def fetch_one(sql, params=None):
        if "FROM asset_interfaces" in sql:
            row = interfaces.get(params[0])
            return {"id": params[0], "asset_id": row[0], "kind": row[1]} if row else None
        if "FROM network_links" in sql:
            return None
        return None

    async def fetch_all(sql, params=None):
        if "FROM network_links" in sql:
            return [{"id": 1}] if (params[0], params[1]) in existing_links else []
        return []

    insert = AsyncMock(return_value=77)
    monkeypatch.setattr(repo.db, "fetch_one", fetch_one)
    monkeypatch.setattr(repo.db, "fetch_all", fetch_all)
    monkeypatch.setattr(repo.db, "execute", AsyncMock())
    monkeypatch.setattr(repo.db, "execute_returning_lastrowid", insert)
    return insert


@pytest.mark.anyio
async def test_wireless_link_between_two_radios(monkeypatch):
    insert = _fake_db(monkeypatch, {1: (10, "radio"), 2: (11, "radio")})
    link_id = await repo.create_link(4, repo.Endpoint("interface", 1), repo.Endpoint("interface", 2),
                                     {"medium": "wireless", "frequency_mhz": "5800", "distance_m": "4200",
                                      "signal_dbm": "-54"})
    assert link_id == 77
    params = insert.await_args.args[1]
    assert params[:6] == (4, "interface", 1, "interface", 2, "wireless")
    assert params[8:11] == (4200, 5800, -54)


@pytest.mark.anyio
@pytest.mark.parametrize(("interfaces", "medium", "message"), [
    ({1: (10, "radio"), 2: (11, "ethernet")}, "wireless", "two radio"),
    ({1: (10, "radio"), 2: (11, "radio")}, "copper", "only carry wireless"),
    ({1: (10, "ethernet"), 2: (10, "ethernet")}, "copper", "same device"),
])
async def test_link_rules(monkeypatch, interfaces, medium, message):
    _fake_db(monkeypatch, interfaces)
    with pytest.raises(ValueError, match=message):
        await repo.create_link(4, repo.Endpoint("interface", 1), repo.Endpoint("interface", 2), {"medium": medium})


@pytest.mark.anyio
async def test_a_wired_port_takes_one_link_but_a_radio_takes_many(monkeypatch):
    _fake_db(monkeypatch, {1: (10, "ethernet"), 2: (11, "ethernet")},
             existing_links={("interface", 1)})
    with pytest.raises(ValueError, match="one link"):
        await repo.create_link(4, repo.Endpoint("interface", 1), repo.Endpoint("interface", 2), {"medium": "copper"})
    _fake_db(monkeypatch, {1: (10, "radio"), 2: (11, "radio")}, existing_links={("interface", 1)})
    assert await repo.create_link(4, repo.Endpoint("interface", 1), repo.Endpoint("interface", 2),
                                  {"medium": "wireless"}) == 77


@pytest.mark.anyio
async def test_link_to_a_foreign_interface_is_rejected(monkeypatch):
    _fake_db(monkeypatch, {1: (10, "ethernet")})
    with pytest.raises(ValueError, match="not found"):
        await repo.create_link(4, repo.Endpoint("interface", 1), repo.Endpoint("interface", 2), {"medium": "copper"})


def test_network_map_permission_follows_network_devices_for_legacy_roles():
    assert normalize_menu_permissions({"menu.network_devices": "read"})["menu.network_map"] == "read"
    assert normalize_menu_permissions(None)["menu.network_map"] == "none"


def test_next_url_stays_on_site():
    assert routes._next_url({"next": "/assets/4#interfaces"}, "/x") == "/assets/4#interfaces"
    for bad in ("//evil.example", "https://evil.example", ""):
        assert routes._next_url({"next": bad}, "/network-map") == "/network-map"


def _request():
    return SimpleNamespace(query_params={}, url=SimpleNamespace(path="/network-map"))


@pytest.mark.anyio
async def test_read_only_roles_cannot_write(monkeypatch):
    from app.features.assets import routes as asset_routes

    monkeypatch.setattr(asset_routes, "_load_asset_context", AsyncMock(
        return_value=({"id": 1}, {"role": "x"}, {"id": 4, "name": "Acme"}, 4, None)))
    monkeypatch.setattr(asset_routes._main(), "_membership_menu_can", lambda *args, write=False, **kw: not write)
    context = await routes._context(_request())
    assert context[-1] is False
    with pytest.raises(HTTPException) as error:
        await routes._context(_request(), write=True)
    assert error.value.status_code == 403


@pytest.mark.anyio
async def test_users_without_access_are_redirected(monkeypatch):
    from app.features.assets import routes as asset_routes

    redirect = RedirectResponse("/", status_code=303)
    monkeypatch.setattr(asset_routes, "_load_asset_context", AsyncMock(return_value=({"id": 1}, {}, None, 4, redirect)))
    assert await routes.network_map_page(_request()) is redirect
    assert await routes.export_svg(_request()) is redirect


def test_pdf_svg_scales_to_the_page():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 800" width="1200" height="800" class="network-map-svg">'
    assert routes._svg_size(svg) == (1200.0, 800.0)
    fitted = routes._fit_svg(svg)
    assert 'width="1200"' not in fitted and 'class="pdf-map"' in fitted


def test_migration_adds_network_map_tables():
    sql = (ROOT / "migrations/438_network_map.sql").read_text()
    for fragment in ("ADD COLUMN asset_type ", "ADD COLUMN asset_type_source", "CREATE TABLE IF NOT EXISTS asset_interfaces",
                     "CREATE TABLE IF NOT EXISTS network_links", "-- phase: expand"):
        assert fragment in sql


def test_sidebar_and_page_wiring():
    base = (ROOT / "app/templates/base.html").read_text()
    assert 'href="/network-map"' in base and "menu.network_map" in base
    page = (ROOT / "app/templates/network_map/index.html").read_text()
    assert "/network-map/export.pdf" in page and 'name="detail"' in page and 'name="types"' in page


@pytest.mark.anyio
async def test_sync_derives_type_but_keeps_a_technician_override(monkeypatch):
    from app.repositories import assets

    monkeypatch.setattr(assets.db, "fetch_one", AsyncMock(return_value={"id": 5}))
    monkeypatch.setattr(assets.db, "fetch_all", AsyncMock(return_value=[]))
    execute = AsyncMock()
    monkeypatch.setattr(assets.db, "execute", execute)
    await assets.upsert_asset(company_id=1, name="PC", type="workstation", form_factor="Notebook",
                              tactical_asset_id="agent-5")
    sql, params = execute.await_args_list[0].args
    assert "asset_type = CASE WHEN asset_type_source = 'manual' THEN asset_type ELSE %s END" in sql
    assert params[1:3] == ("workstation", "laptop")


@pytest.mark.anyio
async def test_clearing_an_override_re_derives_from_sync(monkeypatch):
    from app.repositories import assets

    monkeypatch.setattr(assets.db, "fetch_one", AsyncMock(return_value={
        "type": "server", "form_factor": None, "os_name": None, "machine_type": None}))
    execute = AsyncMock()
    monkeypatch.setattr(assets.db, "execute", execute)
    await assets.set_asset_type(1, 5, None)
    assert execute.await_args.args[1] == ("server", 5, 1)
    await assets.set_asset_type(1, 5, "hypervisor")
    assert "asset_type_source = 'manual'" in execute.await_args.args[0]
    with pytest.raises(ValueError):
        await assets.set_asset_type(1, 5, "toaster")


@pytest.mark.anyio
async def test_removing_rack_items_removes_links_to_their_ports(monkeypatch):
    from app.repositories import infrastructure

    monkeypatch.setattr(infrastructure.db, "fetch_all", AsyncMock(return_value=[{"id": 31}, {"id": 32}]))
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute", execute)
    await infrastructure.delete_record("rack_equipment", 8, 1)
    link_delete, record_delete = (call.args for call in execute.await_args_list)
    assert "DELETE FROM network_links" in link_delete[0] and link_delete[1] == (31, 32, 31, 32)
    assert record_delete[0].startswith("DELETE FROM rack_equipment")


def test_network_map_is_its_own_feature_pack():
    from app.core.features import discover_builtin_feature_pack_slugs
    from app.features.network_map import PACK

    assert "network_map" in discover_builtin_feature_pack_slugs()
    assert PACK.slug == "network_map"
    paths = {route.path for router in PACK.routers for route in router.routes}
    assert {"/network-map", "/network-map/export.pdf", "/api/network-map/links"} <= paths


def _render_sidebar(monkeypatch, *disabled):
    import app.main as main_module
    from app.services.component_availability import ComponentAvailability

    policy = ComponentAvailability(disabled_feature_packs=frozenset(disabled))
    monkeypatch.setattr(main_module, "get_component_availability", lambda: policy)
    return main_module.templates.env.get_template("base.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/", query="")), app_name="MyPortal",
        current_user={"id": 1, "is_super_admin": True}, is_super_admin=True, active_membership={},
        active_company_id=1, available_companies=[], module_enabled={}, enabled_module_slugs=[])


@pytest.mark.parametrize(("disabled", "shown"), [((), True), (("network_map",), False), (("assets",), False)])
def test_disabling_the_pack_removes_the_menu_entry(monkeypatch, disabled, shown):
    assert ('href="/network-map"' in _render_sidebar(monkeypatch, *disabled)) is shown


@pytest.mark.anyio
async def test_map_is_unavailable_without_the_assets_pack(monkeypatch):
    import app.main as main_module
    from app.services.component_availability import ComponentAvailability

    policy = ComponentAvailability(disabled_feature_packs=frozenset({"assets"}))
    monkeypatch.setattr(main_module, "get_component_availability", lambda: policy)
    with pytest.raises(HTTPException) as error:
        await routes._context(_request())
    assert error.value.status_code == 404


def test_settings_accept_the_pack_slug(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("DISABLED_FEATURE_PACKS", "network_map")
    assert Settings().disabled_feature_packs == "network_map"
