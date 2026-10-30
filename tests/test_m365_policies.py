from pathlib import Path

import pytest

from app.repositories import m365_policies as policy_repo
from app.services import m365_policies as policies
from app.services import m365_policy_targeting as targeting
from app.services import m365_standards as standards


async def _check(company_id, settings):
    return {"status": "pass"}


async def _apply(company_id, settings):
    return {"status": "remediated"}


@pytest.fixture
def registered():
    items = [
        standards.Standard("test_fixable", "Fixable setting", "Exchange", "Can be fixed.",
                           _check, _apply, {"enabled": True}),
        standards.Standard("test_report_only", "Report-only setting", "Entra", "Cannot be fixed.", _check),
    ]
    for item in items:
        standards.register(item)
    yield items
    for item in items:
        standards.unregister(item.id)


def _rules(*children, operator="and"):
    return {"operator": operator, "children": list(children)}


BUSINESS_PREMIUM = _rules({"field": "m365.license_sku", "operator": "in", "value": "SPB, O365_BUSINESS_PREMIUM"})

ACME = {"company_id": 1, "company_name": "Acme", "company_tags": ["Managed"],
        "company_variables": {"Tier": "Gold"}, "license_skus": ["SPB"], "license_names": ["Microsoft 365 Business Premium"]}
GLOBEX = {"company_id": 2, "company_name": "Globex", "company_tags": [],
          "company_variables": {}, "license_skus": ["O365_BUSINESS_ESSENTIALS"], "license_names": []}


def test_migration_creates_policy_tables():
    sql = Path("migrations/533_m365_standards_policies.sql").read_text(encoding="utf-8")
    assert sql.startswith("-- phase: expand")
    for table in ("m365_policies", "m365_policy_standards", "m365_policy_company_overrides"):
        assert "CREATE TABLE IF NOT EXISTS " + table in sql
    assert "mode IN ('report', 'alert', 'remediate')" in sql


def test_licence_filter_matches_any_listed_sku_case_insensitively():
    matches = targeting.compile_rules(_rules({"field": "m365.license_sku", "operator": "in", "value": ["spb"]}))
    assert matches(ACME)
    assert not matches(GLOBEX)


def test_negative_licence_filter_matches_tenant_without_that_licence():
    matches = targeting.compile_rules(_rules({"field": "m365.license_sku", "operator": "not_in", "value": ["SPB"]}))
    assert not matches(ACME)
    assert matches(GLOBEX)
    assert matches({**GLOBEX, "license_skus": []})


def test_variable_filter_accepts_pasted_placeholder_and_ignores_case():
    rules = _rules({"field": "company.variable", "key": "{{ company.variables.TIER }}", "operator": "equals", "value": "gold"})
    assert targeting.validate_rules(rules)["children"][0]["key"] == "TIER"
    matches = targeting.compile_rules(rules)
    assert matches(ACME)
    assert not matches(GLOBEX)


def test_missing_variable_only_matches_is_not_set():
    unset = targeting.compile_rules(_rules({"field": "company.variable", "key": "Tier", "operator": "is_not_set"}))
    differs = targeting.compile_rules(_rules({"field": "company.variable", "key": "Tier", "operator": "not_equals", "value": "Gold"}))
    assert unset(GLOBEX) and not unset(ACME)
    assert not differs(GLOBEX)


def test_or_group_and_licence_name_contains():
    matches = targeting.compile_rules(_rules(
        {"field": "m365.license_name", "operator": "contains", "value": "business premium"},
        {"field": "company.tags", "operator": "equals", "value": "VIP"},
        operator="or",
    ))
    assert matches(ACME)
    assert not matches(GLOBEX)


def test_no_rules_targets_every_tenant():
    assert targeting.validate_rules(None) is None
    assert targeting.compile_rules(None)(GLOBEX)


@pytest.mark.parametrize("rules", [
    {"field": "m365.license_sku", "operator": "in", "value": ["SPB"]},
    _rules({"field": "company.secret", "operator": "equals", "value": "x"}),
    _rules({"field": "company.name", "operator": "gt", "value": "x"}),
    _rules({"field": "company.variable", "operator": "equals", "value": "x"}),
    _rules({"field": "company.name", "key": "x", "operator": "equals", "value": "x"}),
    _rules({"field": "m365.license_sku", "operator": "in", "value": " , "}),
    _rules(),
])
def test_invalid_rules_are_rejected(rules):
    with pytest.raises(ValueError):
        targeting.validate_rules(rules)


def test_validate_policy_merges_defaults_and_checks_modes(registered):
    policy = policies.validate_policy({
        "name": " Baseline ",
        "target_rules": BUSINESS_PREMIUM,
        "standards": [{"standard_id": "test_fixable", "mode": "Remediate", "settings": {"extra": 1}}],
        "overrides": [{"company_id": "7", "action": "exclude"}],
    })
    assert policy["name"] == "Baseline"
    assert policy["standards"] == [{"standard_id": "test_fixable", "mode": "remediate",
                                    "settings": {"enabled": True, "extra": 1}}]
    assert policy["overrides"] == [{"company_id": 7, "action": "exclude"}]

    with pytest.raises(ValueError, match="cannot run in 'remediate'"):
        policies.validate_policy({"name": "x", "standards": [{"standard_id": "test_report_only", "mode": "remediate"}]})
    with pytest.raises(ValueError, match="Unknown standard"):
        policies.validate_policy({"name": "x", "standards": [{"standard_id": "nope"}]})
    with pytest.raises(ValueError, match="both included and excluded"):
        policies.validate_policy({"name": "x", "overrides": [
            {"company_id": 1, "action": "include"}, {"company_id": 1, "action": "exclude"}]})
    with pytest.raises(ValueError, match="name"):
        policies.validate_policy({"name": "  "})


def _policy(policy_id, standards_list, *, rules=None, overrides=(), enabled=True):
    return {"id": policy_id, "name": f"Policy {policy_id}", "enabled": enabled, "target_rules": rules,
            "standards": list(standards_list), "overrides": list(overrides)}


def test_resolve_targets_by_filter_and_overrides(registered):
    stored = [
        _policy(1, [{"standard_id": "test_fixable", "mode": "report", "settings": {}}], rules=BUSINESS_PREMIUM,
                overrides=[{"company_id": 2, "action": "include"}]),
        _policy(2, [{"standard_id": "test_report_only", "mode": "alert", "settings": {}}],
                overrides=[{"company_id": 1, "action": "exclude"}]),
        _policy(3, [{"standard_id": "test_report_only", "mode": "alert", "settings": {}}], enabled=False),
    ]
    result = policies.resolve_assignments(stored, {1: ACME, 2: GLOBEX})
    assert set(result[1]) == {"test_fixable"}
    assert set(result[2]) == {"test_fixable", "test_report_only"}


def test_strictest_mode_wins_and_ties_keep_oldest_policy(registered):
    stored = [
        _policy(5, [{"standard_id": "test_fixable", "mode": "remediate", "settings": {"v": "newer"}}]),
        _policy(2, [{"standard_id": "test_fixable", "mode": "alert", "settings": {"v": "older"}}]),
        _policy(3, [{"standard_id": "test_fixable", "mode": "remediate", "settings": {"v": "middle"}}]),
    ]
    assignment = policies.resolve_assignments(stored, {1: ACME})[1]["test_fixable"]
    assert assignment["mode"] == "remediate"
    assert assignment["policy_id"] == 3
    assert assignment["settings"] == {"v": "middle"}
    assert [source["policy_id"] for source in assignment["sources"]] == [2, 3, 5]


def test_resolve_skips_unknown_standards_and_invalid_rules(registered):
    stored = [
        _policy(1, [{"standard_id": "removed_standard", "mode": "report", "settings": {}}]),
        _policy(2, [{"standard_id": "test_fixable", "mode": "report", "settings": {}}],
                rules={"operator": "and", "children": []}),
        _policy(3, [{"standard_id": "test_report_only", "mode": "remediate", "settings": {}}]),
    ]
    result = policies.resolve_assignments(stored, {1: ACME})
    assert set(result[1]) == {"test_report_only"}
    assert result[1]["test_report_only"]["mode"] == "alert"


def test_registry_rejects_duplicate_ids(registered):
    with pytest.raises(ValueError):
        standards.register(standards.Standard("test_fixable", "Other", "Exchange", "", _check))
    assert [item["id"] for item in standards.catalogue() if item["id"].startswith("test_")] == [
        "test_report_only", "test_fixable"]
    assert standards.get("test_report_only").modes == ("report", "alert")


@pytest.mark.asyncio
async def test_tenant_contexts_build_filter_data(monkeypatch):
    calls = []

    async def fake_fetch_all(sql, params=None):
        calls.append((sql, params))
        if "FROM companies" in sql:
            return [{"id": 1, "name": "Acme"}]
        if "FROM company_tags" in sql:
            return [{"company_id": 1, "name": "Managed"}, {"company_id": 9, "name": "Other"}]
        if "FROM company_variable_values" in sql:
            return [{"company_id": 1, "name": "Tier", "value": "Gold"}]
        if "FROM licenses" in sql:
            return [
                {"company_id": 1, "name": "SPB", "platform": "SPB", "friendly_name": "Microsoft 365 Business Premium"},
                {"company_id": 1, "name": "SPB", "platform": "SPB", "friendly_name": None},
            ]
        return []

    monkeypatch.setattr(policy_repo.db, "fetch_all", fake_fetch_all)
    contexts = await policy_repo.tenant_contexts([1])
    assert contexts == {1: {
        "company_id": 1, "company_name": "Acme", "company_tags": ["Managed"],
        "company_variables": {"Tier": "Gold"}, "license_skus": ["SPB"],
        "license_names": ["Microsoft 365 Business Premium", "SPB"],
    }}
    assert all(params == (1,) for _, params in calls)
    assert await policy_repo.tenant_contexts([]) == {}


@pytest.mark.asyncio
async def test_save_policy_stores_validated_parts(monkeypatch, registered):
    stored = {}

    async def create_policy(**fields):
        stored["policy"] = fields
        return 42

    async def replace_standards(policy_id, items):
        stored["standards"] = (policy_id, items)

    async def replace_overrides(policy_id, items):
        stored["overrides"] = (policy_id, items)

    monkeypatch.setattr(policy_repo, "create_policy", create_policy)
    monkeypatch.setattr(policy_repo, "replace_standards", replace_standards)
    monkeypatch.setattr(policy_repo, "replace_overrides", replace_overrides)

    policy_id = await policies.save_policy(
        {"name": "Baseline", "standards": [{"standard_id": "test_fixable", "mode": "alert"}]}, user_id=3)
    assert policy_id == 42
    assert stored["policy"]["created_by"] == 3
    assert stored["policy"]["target_rules"] is None
    assert stored["standards"] == (42, [{"standard_id": "test_fixable", "mode": "alert", "settings": {"enabled": True}}])
    assert stored["overrides"] == (42, [])


@pytest.mark.asyncio
async def test_effective_standards_only_targets_connected_tenants(monkeypatch, registered):
    async def provisioned():
        return {1}

    async def contexts(company_ids):
        assert set(company_ids) == {1}
        return {1: ACME}

    async def list_policies():
        return [_policy(1, [{"standard_id": "test_fixable", "mode": "report", "settings": {}}])]

    monkeypatch.setattr(policies.m365_repo, "list_provisioned_company_ids", provisioned)
    monkeypatch.setattr(policy_repo, "tenant_contexts", contexts)
    monkeypatch.setattr(policy_repo, "list_policies", list_policies)
    assert set(await policies.effective_standards()) == {1}
