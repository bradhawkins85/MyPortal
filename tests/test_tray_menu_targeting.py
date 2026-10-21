"""Device-specific tray menu items: advanced device rules on each item."""

from __future__ import annotations

import json

import pytest

from app.services import tray as svc
from tests.test_tray_app import run, tray_db, tray_event_loop  # noqa: F401 - fixtures

pytestmark = pytest.mark.anyio("asyncio")


AUTOCAD = {"operator": "and", "children": [
    {"field": "software.name", "operator": "contains", "value": "AutoCAD"},
]}
BRISBANE = {"operator": "and", "children": [
    {"field": "asset.location", "operator": "equals", "value": "Brisbane"},
]}
DEVICE = {"id": 1, "company_id": 5, "asset_id": 50}


def _cfg(cfg_id, scope, *, ref=None, enabled=True, payload=None, label=None):
    return {
        "id": cfg_id,
        "name": label or f"cfg-{cfg_id}",
        "scope": scope,
        "scope_ref_id": ref,
        "enabled": 1 if enabled else 0,
        "payload_json": json.dumps(payload if payload is not None else [{"type": "label", "label": label or f"cfg-{cfg_id}"}]),
        "version": 1,
        # Menu-level rules from the previous design are ignored.
        "target_rules": json.dumps(AUTOCAD) if cfg_id == 99 else None,
    }


def _context(*, software=None, location=None):
    return {
        "agent": {}, "company": {"id": 5, "name": "Acme"},
        "asset": {"location": location},
        "software": software, "software_versions": None, "software_publishers": None,
        "asset_tags": [], "company_tags": [], "asset_custom": {}, "company_variables": {},
        "incomplete_fields": [],
    }


@pytest.fixture()
def resolve(monkeypatch):
    """resolve_config_for_device against in-memory configs and a fake inventory."""
    state = {"configs": [], "context": _context(software=["AutoCAD 2025"], location="Brisbane"), "calls": 0}

    async def fake_list():
        return state["configs"]

    async def fake_name():
        return None

    async def fake_context(device):
        state["calls"] += 1
        return state["context"]

    async def passthrough(payload, *_args):
        return payload

    monkeypatch.setattr(svc.tray_repo, "list_menu_configs", fake_list)
    monkeypatch.setattr(svc.site_settings_repo, "get_tray_icon_tooltip_name", fake_name)
    monkeypatch.setattr(svc, "device_rule_context", fake_context)
    monkeypatch.setattr(svc, "_expand_menu_url_variables", passthrough)
    monkeypatch.setattr(svc, "_filter_rmm_scripts_for_platform", passthrough)

    async def _resolve(device=DEVICE):
        return await svc.resolve_config_for_device(device)

    _resolve.state = state
    return _resolve


MENU = [
    {"type": "label", "label": "everyone"},
    {"type": "link", "label": "CAD help", "url": "https://cad.example", "target_rules": AUTOCAD},
    {"type": "submenu", "label": "Brisbane office", "target_rules": BRISBANE, "children": [
        {"type": "link", "label": "Printers", "url": "https://print.example"},
    ]},
    {"type": "submenu", "label": "Tools", "children": [
        {"type": "label", "label": "always"},
        {"type": "label", "label": "CAD only", "target_rules": AUTOCAD},
    ]},
]


def _labels(nodes):
    return [node.get("label") for node in nodes]


async def test_matching_items_are_shown_and_rules_are_not_sent(resolve):
    resolve.state["configs"] = [_cfg(1, "global", payload=MENU)]
    menu = (await resolve())["menu"]
    assert _labels(menu) == ["everyone", "CAD help", "Brisbane office", "Tools"]
    assert _labels(menu[3]["children"]) == ["always", "CAD only"]
    assert "target_rules" not in json.dumps(menu)


async def test_non_matching_items_are_hidden(resolve):
    resolve.state["configs"] = [_cfg(1, "global", payload=MENU)]
    resolve.state["context"] = _context(software=["Excel"], location="Sydney")
    menu = (await resolve())["menu"]
    assert _labels(menu) == ["everyone", "Tools"]
    assert _labels(menu[1]["children"]) == ["always"]


async def test_submenu_emptied_by_rules_is_removed(resolve):
    payload = [{"type": "label", "label": "everyone"},
               {"type": "submenu", "label": "CAD", "children": [
                   {"type": "label", "label": "CAD only", "target_rules": AUTOCAD}]}]
    resolve.state["configs"] = [_cfg(1, "global", payload=payload)]
    resolve.state["context"] = _context(software=[])
    assert _labels((await resolve())["menu"]) == ["everyone"]


async def test_unknown_inventory_hides_rule_items_only(resolve):
    resolve.state["configs"] = [_cfg(1, "global", payload=MENU)]
    resolve.state["context"] = None
    menu = (await resolve())["menu"]
    assert _labels(menu) == ["everyone", "Tools"]


async def test_corrupt_rules_hide_the_item(resolve):
    payload = [{"type": "label", "label": "everyone"},
               {"type": "label", "label": "broken", "target_rules": "{not json"},
               {"type": "label", "label": "bad tree", "target_rules": {"operator": "and", "children": [
                   {"field": "nope", "operator": "equals", "value": "x"}]}}]
    resolve.state["configs"] = [_cfg(1, "global", payload=payload)]
    assert _labels((await resolve())["menu"]) == ["everyone"]


async def test_menus_without_item_rules_never_load_inventory(resolve):
    resolve.state["configs"] = [_cfg(1, "global")]
    await resolve()
    assert resolve.state["calls"] == 0


async def test_menu_selection_is_scope_order_and_ignores_menu_level_rules(resolve):
    resolve.state["configs"] = [_cfg(1, "global"), _cfg(2, "company", ref=5), _cfg(99, "global", label="legacy")]
    assert _labels((await resolve())["menu"]) == ["cfg-2"]
    resolve.state["configs"] = [_cfg(1, "global"), _cfg(2, "company", ref=5), _cfg(3, "device", ref=50)]
    assert _labels((await resolve())["menu"]) == ["cfg-3"]


async def test_company_and_rule_conditions_combine(resolve):
    payload = [{"type": "label", "label": "acme CAD", "visible_company_ids": [5], "target_rules": AUTOCAD},
               {"type": "label", "label": "other CAD", "visible_company_ids": [7], "target_rules": AUTOCAD}]
    resolve.state["configs"] = [_cfg(1, "global", payload=payload)]
    assert _labels((await resolve())["menu"]) == ["acme CAD"]


async def test_device_rule_context_requires_company():
    assert await svc.device_rule_context({"company_id": None, "asset_id": 1}) is None


def test_normalise_menu_rules_validates_nested_items():
    nodes = [{"type": "label", "label": "a", "target_rules": json.dumps(AUTOCAD)},
             {"type": "submenu", "label": "s", "children": [{"type": "label", "label": "b", "target_rules": {}}]}]
    svc.normalise_menu_rules(nodes)
    assert nodes[0]["target_rules"] == AUTOCAD
    assert "target_rules" not in nodes[1]["children"][0]
    with pytest.raises(ValueError, match=r"Menu item 2\.1 \(b\): .*supported targeting field"):
        svc.normalise_menu_rules([{"type": "label"}, {"type": "submenu", "children": [
            {"type": "label", "label": "b", "target_rules": {"operator": "and", "children": [
                {"field": "nope", "operator": "equals", "value": "x"}]}}]}])


def test_api_rejects_invalid_item_rules():
    from fastapi import HTTPException

    from app.api.routes.tray import _menu_payload_json
    from app.schemas.tray import TrayMenuNode

    stored = json.loads(_menu_payload_json([TrayMenuNode(type="label", label="x", target_rules=AUTOCAD)]))
    assert stored == [{"type": "label", "label": "x", "target_rules": AUTOCAD}]
    with pytest.raises(HTTPException) as excinfo:
        _menu_payload_json([TrayMenuNode(type="label", label="x", target_rules={"operator": "and", "children": []})])
    assert excinfo.value.status_code == 422


def test_device_responses_never_carry_rules():
    from app.schemas.tray import TrayConfigResponse

    response = TrayConfigResponse(version=1, menu=[{"type": "label", "label": "x"}])
    assert "target_rules" not in response.model_dump(exclude_none=True)["menu"][0]


def test_migration_disables_configs_with_menu_level_rules():
    from pathlib import Path

    sql = Path("migrations/514_tray_menu_item_rules.sql").read_text()
    assert "UPDATE tray_menu_configs SET enabled = 0" in sql
    assert "target_rules IS NOT NULL" in sql


async def test_preview_rejects_invalid_rules_without_echoing_details():
    from app.api.routes.tray import menu_config_targeting_preview
    from app.schemas.tray import TrayMenuTargetingPreview

    response = await menu_config_targeting_preview(
        TrayMenuTargetingPreview(rules={"operator": "and", "children": [
            {"field": "nope", "operator": "equals", "value": "x"}]}),
        current_user={"id": 1},
    )
    assert response.status_code == 400
    assert json.loads(response.body) == {
        "detail": "Invalid target rules.", "errors": {"target_rules": "Invalid target rules."},
    }


def test_saving_clears_legacy_menu_rules(tray_db, run):
    from app.repositories import tray as repo

    record = run(repo.create_menu_config(
        name="legacy", scope="global", scope_ref_id=None, payload_json="[]", display_text=None,
        env_allowlist=None, branding_icon_url=None, enabled=False, created_by_user_id=None,
    ))
    run(tray_db.execute("UPDATE tray_menu_configs SET target_rules = ? WHERE id = ?",
                        (json.dumps(AUTOCAD), record["id"])))
    run(repo.update_menu_config(record["id"], name="legacy", enabled=True))
    assert run(repo.get_menu_config(record["id"]))["target_rules"]  # untouched by API-style updates
    run(repo.update_menu_config(record["id"], name="legacy", clear_legacy_rules=True))
    assert run(repo.get_menu_config(record["id"]))["target_rules"] is None
