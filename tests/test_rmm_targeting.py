"""Check targeting must be expressive without widening on missing inventory."""

import copy
import math

import pytest

from app.services.rmm_targeting import (
    MAX_DEPTH,
    MAX_LIST_VALUES,
    MAX_NODES,
    MAX_VALUE_LENGTH,
    compile_rules,
    field_catalogue,
    matches_rules,
    validate_rules,
)


def condition(field, operator="equals", value=None, key=None):
    result = {"field": field, "operator": operator}
    if value is not None:
        result["value"] = value
    if key is not None:
        result["key"] = key
    return result


def group(*children, operator="and"):
    return {"operator": operator, "children": list(children)}


@pytest.fixture
def context():
    return {
        "asset": {"id": 5, "name": "SYD-SRV-01", "catalogue_type": "server",
                  "type": "Windows Server", "cpu_name": "Intel Xeon Silver",
                  "ram_gb": 32, "disk_gb": 512, "os_version": "10.0.20348",
                  "ip_address": ["10.20.30.40", "2001:db8::7"]},
        "agent": {"id": 10, "hostname": "SYD-SRV-01", "architecture": "amd64",
                  "version": "1.10.0", "status": "online"},
        "company": {"id": 9, "name": "Example Industries", "vip": True, "agent_count": 18},
        "software": ["WireGuard 0.5.3", "Microsoft Edge", "Microsoft Defender"],
        "asset_tags": ["Production", "Server"],
        "company_tags": ["VIP"],
        "asset_custom": {"VPN checks enabled": "1", "15": "1", "Environment": "Production",
                         "Disk threshold": "90", "Services": ["DNS", "DHCP"]},
        "company_variables": {"monitor_vpn": "true", "Minimum RAM": "16", "12": "16"},
    }


@pytest.mark.parametrize("operator,value", [("not_in_subnet", "192.168.0.0/16"),
    ("not_equals", "192.168.0.1"), ("not_in", ["192.168.0.1"])])
def test_incomplete_ip_inventory_allows_known_positive_matches_but_blocks_exclusions(context, operator, value):
    context["incomplete_fields"] = ["asset.ip_address"]
    assert matches_rules(group(condition("asset.ip_address", "in_subnet", "10.0.0.0/8")), context)
    assert not matches_rules(group(condition("asset.ip_address", operator, value)), context)


def test_nested_groups_cover_vip_servers_or_wireguard_with_custom_field(context):
    rules = group(
        group(condition("asset.catalogue_type", value="server"), condition("company.vip", value="true")),
        group(condition("software.name", "contains", "wireguard"),
              condition("asset.custom", value="true", key="VPN checks enabled")),
        operator="or",
    )
    assert matches_rules(rules, context)
    context["company"]["vip"] = False
    assert matches_rules(rules, context)
    context["asset_custom"]["VPN checks enabled"] = "0"
    assert not matches_rules(rules, context)
    context["company"]["vip"] = True
    context["asset"]["catalogue_type"] = "workstation"
    assert not matches_rules(rules, context)


def test_cross_scope_hardware_counts_custom_variables_and_subnets(context):
    rules = group(
        condition("company.variable", "equals", "true", key="monitor_vpn"),
        condition("company.agent_count", "between", "10, 20"),
        condition("asset.ram_gb", "gte", "32"),
        condition("asset.disk_gb", "gt", "500"),
        condition("asset.cpu_name", "contains", "xeon"),
        condition("asset.ip_address", "in_subnet", "10.20.0.0/16"),
        condition("asset.os_version", "version_gte", "10.0.19045"),
    )
    assert matches_rules(rules, context)
    context["company"]["agent_count"] = 21
    assert not matches_rules(rules, context)


def test_native_inventory_hardware_supports_cpu_model_cores_and_disk_space(context):
    context["asset"].update({"model": "PowerEdge R750", "cpu_cores": 16,
                             "cpu_logical_processors": 32, "disk_free_gb": 90.5,
                             "os_build": "20348", "domain": "example.local", "uptime_days": 10})
    rules = group(condition("asset.model", "contains", "PowerEdge"),
                  condition("asset.cpu_cores", "gte", 8),
                  condition("asset.cpu_logical_processors", "gte", 32),
                  condition("asset.disk_free_gb", "lt", 100),
                  condition("asset.os_build", "version_gte", "20348"),
                  condition("asset.domain", "equals", "EXAMPLE.LOCAL"),
                  condition("asset.uptime_days", "between", [7, 14]))
    assert matches_rules(rules, context)


@pytest.mark.parametrize("operator,value,expected", [
    ("equals", "syd-srv-01", True), ("not_equals", "SYD-SRV-01", False),
    ("contains", "SRV", True), ("not_contains", "srv", False),
    ("starts_with", "syd", True), ("ends_with", "-01", True),
    ("in", "SYD-SRV-02, syd-srv-01", True), ("not_in", ["syd-srv-01"], False),
    ("is_set", None, True), ("is_not_set", None, False),
])
def test_case_insensitive_text_operators(context, operator, value, expected):
    assert matches_rules(group(condition("asset.name", operator, value)), context) is expected


@pytest.mark.parametrize("operator,value,expected", [
    ("equals", "32.0", True), ("not_equals", 32, False), ("gt", "31", True),
    ("gte", "32", True), ("lt", 32, False), ("lte", 32, True),
    ("between", [32, 64], True), ("between", [1, 31], False),
    ("in", "16,32,64", True), ("not_in", [16, 64], True),
])
def test_numeric_comparisons(context, operator, value, expected):
    assert matches_rules(group(condition("asset.ram_gb", operator, value)), context) is expected


@pytest.mark.parametrize("raw,expected", [("true", True), ("yes", True), ("1", True),
                                          ("false", False), ("no", False), ("0", False)])
def test_boolean_editor_values_are_normalized(raw, expected):
    tree = validate_rules(group(condition("company.vip", value=raw)))
    assert tree["children"][0]["value"] is expected


@pytest.mark.parametrize("field,key,operator,value,expected", [
    ("asset.custom", "VPN checks enabled", "equals", True, True),
    ("asset.custom", "15", "equals", True, True),
    ("asset.custom", "Disk threshold", "gte", "90", True),
    ("asset.custom", "Disk threshold", "lt", 80, False),
    ("asset.custom", "Environment", "equals", "production", True),
    ("asset.custom", "Services", "equals", "dns", True),
    ("asset.custom", "Services", "not_equals", "dhcp", False),
    ("company.variable", "Minimum RAM", "between", "8, 32", True),
    ("company.variable", "12", "equals", "16.0", True),
    ("company.variable", "monitor_vpn", "equals", True, True),
])
def test_keyed_fields_accept_names_ids_numbers_and_booleans(context, field, key, operator, value, expected):
    assert matches_rules(group(condition(field, operator, value, key)), context) is expected


def test_dynamic_key_lookup_is_exact_and_not_template_expanded(context):
    assert not matches_rules(group(condition("company.variable", value=True, key="Monitor_VPN")), context)
    assert not matches_rules(group(condition("company.variable", value=True, key="{{monitor_vpn}}")), context)


def test_software_versions_and_publishers_are_correlated_with_package_name(context):
    context["software_versions"] = {"WireGuard": ["0.5.3"], "Microsoft Edge": ["140.0"]}
    context["software_publishers"] = {"WireGuard": ["WireGuard LLC"], "Microsoft Edge": ["Microsoft"]}
    assert matches_rules(group(condition("software.version", "version_gte", "0.5.0", key="wireguard")), context)
    assert not matches_rules(group(condition("software.version", "version_gte", "1.0", key="WireGuard")), context)
    assert matches_rules(group(condition("software.publisher", "contains", "wireguard", key="WIREGUARD")), context)
    assert not matches_rules(group(condition("software.publisher", "contains", "microsoft", key="WireGuard")), context)


@pytest.mark.parametrize("metadata", [None, {}, {"WireGuard": []}])
def test_unreported_software_metadata_never_satisfies_negative_filter(context, metadata):
    context["software_versions"] = metadata
    assert not matches_rules(group(condition("software.version", "not_equals", "1.0", key="WireGuard")), context)


def test_multiple_reported_software_versions_use_collection_negative_semantics(context):
    context["software_versions"] = {"WireGuard": ["0.5.3", "0.5.2"]}
    assert not matches_rules(group(condition("software.version", "not_equals", "0.5.2", key="WireGuard")), context)
    assert matches_rules(group(condition("software.version", "not_equals", "1.0", key="WireGuard")), context)


@pytest.mark.parametrize("field", ["software.name", "asset.tags", "company.tags"])
@pytest.mark.parametrize("operator", ["not_equals", "not_contains", "not_in"])
def test_collection_negatives_require_no_matching_item(context, field, operator):
    names = {"software.name": "WireGuard 0.5.3", "asset.tags": "Production", "company.tags": "VIP"}
    value = [names[field]] if operator == "not_in" else names[field]
    assert not matches_rules(group(condition(field, operator, value)), context)
    absent = ["Never installed"] if operator == "not_in" else "Never installed"
    assert matches_rules(group(condition(field, operator, absent)), context)


@pytest.mark.parametrize("operator,value", [
    ("equals", "WireGuard"), ("contains", "WireGuard"), ("not_equals", "WireGuard"),
    ("not_contains", "WireGuard"), ("not_in", ["WireGuard"]),
])
def test_unknown_software_inventory_never_matches(context, operator, value):
    context["software"] = None
    assert not matches_rules(group(condition("software.name", operator, value)), context)
    del context["software"]
    assert not matches_rules(group(condition("software.name", operator, value)), context)


def test_known_empty_software_can_match_negative_filters(context):
    context["software"] = []
    assert matches_rules(group(condition("software.name", "not_contains", "WireGuard")), context)
    assert not matches_rules(group(condition("software.name", "contains", "WireGuard")), context)
    assert matches_rules(group(condition("software.name", "is_not_set")), context)
    assert not matches_rules(group(condition("software.name", "is_set")), context)


@pytest.mark.parametrize("bad", [[True], [42], ["Microsoft Edge", {}], "Microsoft Edge"])
def test_corrupt_collection_entries_fail_closed(context, bad):
    context["software"] = bad
    assert not matches_rules(group(condition("software.name", "not_contains", "WireGuard")), context)


@pytest.mark.parametrize("missing", [None, "", "  "])
@pytest.mark.parametrize("operator,value", [
    ("equals", "server"), ("not_equals", "server"), ("not_contains", "server"),
    ("not_in", ["server"]), ("is_set", None),
])
def test_missing_scalars_do_not_widen_negative_predicates(context, missing, operator, value):
    context["asset"]["name"] = missing
    assert not matches_rules(group(condition("asset.name", operator, value)), context)
    assert matches_rules(group(condition("asset.name", "is_not_set")), context)


def test_unavailable_or_secret_variable_cannot_match_negative_filter(context):
    rules = group(condition("company.variable", "not_equals", "enabled", key="Secret token"))
    assert not matches_rules(rules, context)
    context["company_variables"] = None
    assert not matches_rules(rules, context)


@pytest.mark.parametrize("operator,value,expected", [
    ("in_subnet", "10.20.0.0/16", True), ("in_subnet", "2001:db8::/64", True),
    ("not_in_subnet", "10.20.0.0/16", False), ("not_in_subnet", "192.168.0.0/16", True),
    ("equals", "2001:0db8:0:0:0:0:0:7", True), ("not_equals", "10.20.30.40", False),
    ("in", ["2001:db8::7", "10.0.0.1"], True),
])
def test_dual_stack_ip_collections(context, operator, value, expected):
    assert matches_rules(group(condition("asset.ip_address", operator, value)), context) is expected


def test_subnet_validation_normalizes_host_bits():
    tree = validate_rules(group(condition("asset.ip_address", "in_subnet", "10.20.30.40/16")))
    assert tree["children"][0]["value"] == "10.20.0.0/16"


@pytest.mark.parametrize("current,operator,target,expected", [
    ("1.10", "version_gte", "1.9", True), ("1.9", "version_gte", "1.10", False),
    ("1.2", "version_gte", "1.2.0", True), ("v1.2.0", "version_lte", "1.2", True),
    ("10.0.20348", "version_gte", "10.0.19045", True),
    ("Windows 11", "version_gte", "10.0", False),
])
def test_versions_compare_numeric_components(context, current, operator, target, expected):
    context["agent"]["version"] = current
    assert matches_rules(group(condition("agent.version", operator, target)), context) is expected


@pytest.mark.parametrize("field,operator,value,bad", [
    ("asset.ram_gb", "not_equals", 32, "unknown"),
    ("company.vip", "not_equals", True, "unknown"),
    ("asset.ip_address", "not_in_subnet", "10.0.0.0/8", ["not an IP"]),
    ("asset.ip_address", "not_equals", "10.20.30.40", ["192.168.1.1", "broken"]),
    ("asset.ram_gb", "not_equals", 32, [64]),
])
def test_malformed_actual_values_fail_closed(context, field, operator, value, bad):
    scope, key = field.split(".")
    context[scope][key] = bad
    assert not matches_rules(group(condition(field, operator, value)), context)


@pytest.mark.parametrize("rules,error", [
    (None, "start"), ([], "start"), (condition("company.vip", value=True), "start"),
    (group(), "at least one"), ({"operator": "xor", "children": []}, "and or or"),
    (group("bad"), "object"), (group(condition("unknown", value="x")), "supported targeting field"),
    (group(condition("company.vip", "contains", "x")), "not supported"),
    (group(condition("company.vip", "x" * 33, True)), "supported operator"),
    (group(condition("asset.ram_gb", value="lots")), "number"),
    (group(condition("company.vip", value="maybe")), "boolean"),
    (group(condition("asset.name", "contains", "")), "non-empty"),
    (group(condition("asset.custom", value="" , key="test")), "non-empty"),
    (group(condition("asset.custom", value="x")), "key is required"),
    (group(condition("asset.custom", value="x", key=" ")), "key is required"),
    (group(condition("asset.name", value="x", key="wrong")), "only custom"),
    (group(condition("asset.name", "in", "a,,b")), "must not be empty"),
    (group(condition("asset.name", "in", [])), "between 1"),
    (group(condition("asset.ram_gb", "between", [3])), "exactly two"),
    (group(condition("asset.ram_gb", "between", [5, 3])), "lower bound"),
    (group(condition("asset.ip_address", "in_subnet", "bad/24")), "CIDR"),
    (group(condition("asset.ip_address", "in_subnet", "10.0.0.1")), "CIDR"),
    (group(condition("asset.ip_address", value="300.1.2.3")), "IPv4"),
    (group(condition("agent.version", "version_gte", "1.2-beta")), "dotted numeric"),
    (group(condition("asset.name", value={"nested": "x"})), "text value"),
    (group(condition("asset.name", value=True)), "text value"),
    (group(condition("asset.name", value=123)), "text value"),
    ({"operator": "and", "children": [condition("company.vip", value=True)], "field": "x"}, "groups may only"),
])
def test_validation_rejects_bad_rule_shapes_and_values(rules, error):
    with pytest.raises(ValueError, match=error):
        validate_rules(rules)
    assert not matches_rules(rules, {})


@pytest.mark.parametrize("number", ["nan", "inf", "-Infinity", math.nan, math.inf, "1e309", "1e1000000", "1e-9999", True])
def test_nonfinite_extreme_and_boolean_numeric_values_are_rejected(number):
    with pytest.raises(ValueError):
        validate_rules(group(condition("asset.ram_gb", "gte", number)))


def test_validation_rejects_full_corrupt_tree_before_short_circuit(context):
    corrupt_or = group(condition("company.vip", value=True), condition("private.secret", value="x"), operator="or")
    assert not matches_rules(corrupt_or, context)


def test_rule_limits_cover_depth_nodes_text_keys_and_lists():
    tree = condition("company.vip", value=True)
    for _ in range(MAX_DEPTH - 1):
        tree = group(tree)
    validate_rules(tree)
    with pytest.raises(ValueError, match="levels deep"):
        validate_rules(group(tree))
    with pytest.raises(ValueError, match="at most"):
        validate_rules(group(*[condition("company.vip", value=True) for _ in range(MAX_NODES)]))
    with pytest.raises(ValueError, match="characters"):
        validate_rules(group(condition("asset.name", value="x" * (MAX_VALUE_LENGTH + 1))))
    with pytest.raises(ValueError, match="custom keys"):
        validate_rules(group(condition("asset.custom", value="x", key="x" * 257)))
    with pytest.raises(ValueError, match="between 1"):
        validate_rules(group(condition("asset.name", "in", ["x"] * (MAX_LIST_VALUES + 1))))


def test_cyclic_rule_data_fails_closed():
    cycle = group()
    cycle["children"].append(cycle)
    assert not matches_rules(cycle, {})


def test_normalization_does_not_mutate_user_data():
    tree = group(condition("company.vip", value=" true "), condition("asset.ram_gb", "between", ["16", "32"]))
    original = copy.deepcopy(tree)
    normalized = validate_rules(tree)
    assert tree == original
    assert normalized["children"][0]["value"] is True
    assert normalized["children"][1]["value"] == [16, 32]
    normalized["children"][1]["value"].append(64)
    assert tree == original


def test_catalogue_has_typed_keyed_fields_without_inventory_values():
    catalogue = field_catalogue()
    by_key = {item["key"]: item for item in catalogue}
    assert len(by_key) == len(catalogue)
    assert by_key["company.vip"]["type"] == "boolean"
    assert by_key["asset.disk_gb"]["type"] == "number"
    assert by_key["software.name"]["type"] == "list"
    assert by_key["company.variable"]["keyed"] is True
    assert "gte" in by_key["company.variable"]["operators"]
    assert all("value" not in item for item in catalogue)
    by_key["company.vip"]["operators"].clear()
    assert field_catalogue()[0]["operators"]
    assert {item["key"]: item for item in field_catalogue()}["company.vip"]["operators"]


def test_invalid_context_is_rejected():
    assert not matches_rules(group(condition("company.vip", value=True)), None)
    assert not matches_rules(group(condition("company.vip", value=True)), [])


def test_compiled_selector_is_independent_of_later_rule_changes(context):
    rules = group(condition("company.vip", value=True))
    matcher = compile_rules(rules)
    rules["children"][0]["value"] = False
    assert matcher(context)
    context["company"]["vip"] = False
    assert not matcher(context)
    assert not matcher(None)


def test_compiled_selector_does_not_repeat_tree_validation(context, monkeypatch):
    from app.services import rmm_targeting

    matcher = compile_rules(group(condition("software.name", "contains", "WireGuard")))
    monkeypatch.setattr(rmm_targeting, "validate_rules", lambda _rules: pytest.fail("validation repeated"))
    assert matcher(context)
    context["software"] = None
    assert not matcher(context)


def test_compilation_rejects_corrupt_or_branch(context):
    rules = group(condition("company.vip", value=True), condition("unknown", value="x"), operator="or")
    with pytest.raises(ValueError):
        compile_rules(rules)
