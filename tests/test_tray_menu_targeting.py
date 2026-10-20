"""Device-specific tray menus: advanced rule targeting with scope fallback."""

from __future__ import annotations

import json

import pytest

from app.services import tray as svc

pytestmark = pytest.mark.anyio("asyncio")


AUTOCAD = {"operator": "and", "children": [
    {"field": "software.name", "operator": "contains", "value": "AutoCAD"},
]}
BRISBANE = {"operator": "and", "children": [
    {"field": "asset.location", "operator": "equals", "value": "Brisbane"},
]}


def _cfg(cfg_id, scope, *, ref=None, rules=None, priority=100, enabled=True, label=None):
    return {
        "id": cfg_id,
        "name": label or f"cfg-{cfg_id}",
        "scope": scope,
        "scope_ref_id": ref,
        "enabled": 1 if enabled else 0,
        "target_rules": json.dumps(rules) if isinstance(rules, dict) else rules,
        "priority": priority,
        "payload_json": json.dumps([{"type": "label", "label": label or f"cfg-{cfg_id}"}]),
        "version": 1,
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
def context_calls(monkeypatch):
    calls: list[dict] = []
    state = {"context": _context(software=["AutoCAD 2025"], location="Brisbane")}

    async def fake_context(device):
        calls.append(device)
        return state["context"]

    monkeypatch.setattr(svc, "device_rule_context", fake_context)
    return calls, state


DEVICE = {"id": 1, "company_id": 5, "asset_id": 50}


async def test_matching_rules_beat_company_and_global(context_calls):
    configs = [_cfg(1, "global"), _cfg(2, "company", ref=5), _cfg(3, "global", rules=AUTOCAD)]
    chosen = await svc.select_config_for_device(configs, DEVICE)
    assert chosen["id"] == 3


async def test_non_matching_rules_fall_back_to_existing_order(context_calls):
    _calls, state = context_calls
    state["context"] = _context(software=["Excel"], location="Sydney")
    configs = [_cfg(1, "global"), _cfg(2, "company", ref=5), _cfg(3, "global", rules=AUTOCAD)]
    chosen = await svc.select_config_for_device(configs, DEVICE)
    assert chosen["id"] == 2


async def test_lowest_priority_wins_among_matches(context_calls):
    configs = [
        _cfg(1, "global", rules=AUTOCAD, priority=50),
        _cfg(2, "company", ref=5, rules=BRISBANE, priority=10),
    ]
    assert (await svc.select_config_for_device(configs, DEVICE))["id"] == 2


async def test_equal_priority_prefers_more_specific_scope(context_calls):
    configs = [_cfg(1, "global", rules=AUTOCAD), _cfg(2, "company", ref=5, rules=BRISBANE)]
    assert (await svc.select_config_for_device(configs, DEVICE))["id"] == 2


async def test_rules_respect_scope(context_calls):
    configs = [_cfg(1, "global"), _cfg(2, "company", ref=99, rules=AUTOCAD)]
    assert (await svc.select_config_for_device(configs, DEVICE))["id"] == 1


async def test_device_scope_without_rules_still_wins(context_calls):
    calls, _state = context_calls
    configs = [_cfg(1, "global", rules=AUTOCAD, priority=0), _cfg(2, "device", ref=50)]
    assert (await svc.select_config_for_device(configs, DEVICE))["id"] == 2
    assert calls == []  # inventory is not loaded when it cannot change the result


async def test_untargeted_configs_never_load_inventory(context_calls):
    calls, _state = context_calls
    configs = [_cfg(1, "global"), _cfg(2, "company", ref=5)]
    assert (await svc.select_config_for_device(configs, DEVICE))["id"] == 2
    assert calls == []


async def test_corrupt_rules_never_apply_to_every_device(context_calls):
    configs = [_cfg(1, "global"), _cfg(2, "global", rules="{not json", priority=0)]
    assert (await svc.select_config_for_device(configs, DEVICE))["id"] == 1


async def test_disabled_targeted_config_is_ignored(context_calls):
    configs = [_cfg(1, "global"), _cfg(2, "global", rules=AUTOCAD, enabled=False)]
    assert (await svc.select_config_for_device(configs, DEVICE))["id"] == 1


async def test_unknown_inventory_falls_back(context_calls):
    _calls, state = context_calls
    state["context"] = None
    configs = [_cfg(1, "global"), _cfg(2, "global", rules=AUTOCAD)]
    assert (await svc.select_config_for_device(configs, DEVICE))["id"] == 1


async def test_device_rule_context_requires_company():
    assert await svc.device_rule_context({"company_id": None, "asset_id": 1}) is None


async def test_resolve_config_uses_rule_targeted_menu(context_calls, monkeypatch):
    configs = [_cfg(1, "global", label="everyone"), _cfg(2, "global", rules=BRISBANE, label="brisbane")]

    async def fake_list():
        return configs

    async def fake_name():
        return None

    async def passthrough(payload, *_args):
        return payload

    monkeypatch.setattr(svc.tray_repo, "list_menu_configs", fake_list)
    monkeypatch.setattr(svc.site_settings_repo, "get_tray_icon_tooltip_name", fake_name)
    monkeypatch.setattr(svc, "_expand_menu_url_variables", passthrough)
    monkeypatch.setattr(svc, "_filter_rmm_scripts_for_platform", passthrough)

    resolved = await svc.resolve_config_for_device(DEVICE)
    assert resolved["menu"][0]["label"] == "brisbane"

    _calls, state = context_calls
    state["context"] = _context(location="Sydney")
    resolved = await svc.resolve_config_for_device(DEVICE)
    assert resolved["menu"][0]["label"] == "everyone"


def test_form_rules_validation():
    from app.main import _tray_form_rules

    assert _tray_form_rules({"target_mode": "all", "target_rules": json.dumps(AUTOCAD)}) == (None, None)
    stored, error = _tray_form_rules({"target_mode": "rules", "target_rules": json.dumps(AUTOCAD)})
    assert error is None and json.loads(stored) == AUTOCAD
    stored, error = _tray_form_rules({"target_mode": "rules", "target_rules": "null"})
    assert stored is None and error
    stored, error = _tray_form_rules({"target_mode": "rules", "target_rules": json.dumps(
        {"operator": "and", "children": [{"field": "nope", "operator": "equals", "value": "x"}]})})
    assert stored is None and "supported targeting field" in error
