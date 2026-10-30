import copy

import pytest

from app.services import m365_access_baseline
from app.services import m365_standards as standards
from app.services import m365_template_library as library
from app.services.m365 import M365Error

CA = library.CA_TEMPLATES[1]  # Azure management MFA: all users, excludes directory sync
INTUNE = library.INTUNE_TEMPLATES[0]
library.register_templates()

BREAK_GLASS = [{"id": "bg-1", "upn": "myportal-emergency-admin-1@x.com"}]


@pytest.fixture
def graph(monkeypatch):
    state = {"ca": [], "compliance": [], "posts": [], "patches": [], "security_defaults": False,
             "break_glass": list(BREAK_GLASS), "fail": False}

    async def token(company_id, **kwargs):
        if state["fail"]:
            raise M365Error("No M365 credentials found for company")
        return "token"

    async def get_all(access_token, url):
        if url.startswith(library.COMPLIANCE_POLICIES_URL):
            assert url.endswith("?$expand=assignments")
            return copy.deepcopy(state["compliance"])
        return copy.deepcopy(state["ca"])

    async def post(access_token, url, payload):
        state["posts"].append((url, copy.deepcopy(payload)))
        return {"id": "new-id"}

    async def patch(access_token, url, payload, **kwargs):
        state["patches"].append((url, copy.deepcopy(payload)))
        return {}

    async def safe_get(access_token, url):
        return {"isEnabled": state["security_defaults"]}

    async def break_glass(access_token):
        return state["break_glass"]

    monkeypatch.setattr(library, "acquire_access_token", token)
    monkeypatch.setattr(library, "_graph_get_all", get_all)
    monkeypatch.setattr(library, "_graph_post", post)
    monkeypatch.setattr(library, "_graph_patch", patch)
    monkeypatch.setattr(library, "_safe_graph_get", safe_get)
    monkeypatch.setattr(library, "_myportal_break_glass_accounts", break_glass)
    return state


def _ca_policy(state="enabled", **changes):
    policy = copy.deepcopy(CA.payload) | {"id": "ca-1", "state": state}
    policy["conditions"]["users"]["excludeUsers"] = ["bg-1"]
    policy.update(changes)
    return policy


def test_every_template_is_a_remediable_standard():
    for category, templates in ((library.CA_CATEGORY, library.CA_TEMPLATES),
                                (library.INTUNE_CATEGORY, library.INTUNE_TEMPLATES)):
        for template in templates:
            standard = standards.get(template.id)
            assert standard is not None and standard.category == category
            assert standard.modes == standards.MODES
            assert template.payload["displayName"].startswith("MyPortal – ")
    assert library.register_templates() == 0


def test_intune_write_permission_is_requested():
    assert m365_access_baseline.GRAPH_APPLICATION_PERMISSION_IDS[
        "DeviceManagementConfiguration.ReadWrite.All"] == "9241abd9-d0e6-425a-bd4f-47ba86e767a4"


def test_differences_ignore_order_case_and_extra_fields():
    expected = {"a": ["X", "y"], "b": {"c": "One,Two"}, "@odata.type": "#t"}
    assert library.differences(expected, {"a": ["Y", "x"], "b": {"c": "two, one", "d": 1}}) == []
    assert library.differences(expected, {"a": ["x"], "b": {}}) == ["a", "b.c"]


@pytest.mark.asyncio
async def test_ca_check_fails_when_policy_missing(graph):
    result = await standards.get(CA.id).check(1, {})
    assert result["status"] == "fail"
    assert "is not in the tenant" in result["details"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state,status,phrase", [
    ("enabled", "pass", "is On"),
    (library.REPORT_ONLY, "pass", "report-only"),
    ("disabled", "fail", "turned off"),
])
async def test_ca_check_states(graph, state, status, phrase):
    graph["ca"] = [_ca_policy(state)]
    result = await standards.get(CA.id).check(1, {})
    assert result["status"] == status
    assert phrase in result["details"]


@pytest.mark.asyncio
async def test_ca_check_ignores_admin_exclusions_but_flags_changed_grant(graph):
    policy = _ca_policy()
    policy["conditions"]["users"]["excludeGroups"] = ["contractors"]
    graph["ca"] = [policy]
    assert (await standards.get(CA.id).check(1, {}))["status"] == "pass"
    policy["grantControls"] = {"operator": "OR", "builtInControls": ["block"]}
    result = await standards.get(CA.id).check(1, {})
    assert result["status"] == "fail"
    assert "grantControls.builtInControls" in result["affected"]


@pytest.mark.asyncio
async def test_ca_check_is_unknown_without_access(graph):
    graph["fail"] = True
    assert (await standards.get(CA.id).check(1, {}))["status"] == "unknown"


@pytest.mark.asyncio
async def test_ca_apply_creates_report_only_policy_excluding_break_glass(graph):
    result = await standards.get(CA.id).apply(1, {})
    assert result["status"] == "remediated"
    [(url, payload)] = graph["posts"]
    assert url == library._CA_POLICIES_URL
    assert payload["state"] == library.REPORT_ONLY
    assert payload["conditions"]["users"]["excludeUsers"] == ["bg-1"]
    assert payload["displayName"] == CA.payload["displayName"]


@pytest.mark.asyncio
async def test_ca_apply_restores_settings_and_keeps_exclusions_and_state(graph):
    policy = _ca_policy("enabled", grantControls={"operator": "OR", "builtInControls": ["block"]})
    policy["conditions"]["users"].update(excludeUsers=["someone"], excludeGroups=["contractors"])
    graph["ca"] = [policy]
    result = await standards.get(CA.id).apply(1, {})
    assert result["status"] == "remediated"
    [(url, update)] = graph["patches"]
    assert url.endswith("/ca-1")
    assert "state" not in update
    assert update["grantControls"] == CA.payload["grantControls"]
    users = update["conditions"]["users"]
    assert users["excludeUsers"] == ["someone", "bg-1"]
    assert users["excludeGroups"] == ["contractors"]
    assert users["excludeRoles"] == CA.payload["conditions"]["users"]["excludeRoles"]


@pytest.mark.asyncio
async def test_ca_apply_moves_disabled_policy_to_report_only(graph):
    graph["ca"] = [_ca_policy("disabled")]
    await standards.get(CA.id).apply(1, {})
    assert graph["patches"][0][1]["state"] == library.REPORT_ONLY


@pytest.mark.asyncio
@pytest.mark.parametrize("security_defaults,break_glass,phrase", [
    (True, BREAK_GLASS, "Security Defaults"),
    (False, [], "emergency access"),
])
async def test_ca_apply_refuses_unsafe_tenants(graph, security_defaults, break_glass, phrase):
    graph["security_defaults"] = security_defaults
    graph["break_glass"] = break_glass
    result = await standards.get(CA.id).apply(1, {})
    assert result["status"] == "failed"
    assert phrase in result["details"]
    assert graph["posts"] == [] and graph["patches"] == []


def _compliance_policy(**changes):
    return copy.deepcopy(INTUNE.payload) | {"id": "cp-1", "assignments": [{"id": "a"}]} | changes


@pytest.mark.asyncio
@pytest.mark.parametrize("policies,status,phrase", [
    ([], "fail", "is not in Intune"),
    ([_compliance_policy()], "pass", "matches the template"),
    ([_compliance_policy(assignments=[])], "fail", "not assigned"),
    ([_compliance_policy(bitLockerEnabled=False)], "fail", "bitLockerEnabled"),
])
async def test_intune_check(graph, policies, status, phrase):
    graph["compliance"] = policies
    result = await standards.get(INTUNE.id).check(1, {})
    assert result["status"] == status
    assert phrase in result["details"]


@pytest.mark.asyncio
async def test_intune_apply_creates_and_assigns_to_all_devices(graph):
    result = await standards.get(INTUNE.id).apply(1, {})
    assert result["status"] == "remediated"
    (create_url, payload), (assign_url, assignment) = graph["posts"]
    assert create_url == library.COMPLIANCE_POLICIES_URL
    assert payload["@odata.type"] == "#microsoft.graph.windows10CompliancePolicy"
    action = payload["scheduledActionsForRule"][0]["scheduledActionConfigurations"][0]
    assert action["actionType"] == "block" and action["gracePeriodHours"] == 72
    assert assign_url == library.COMPLIANCE_POLICIES_URL + "/new-id/assign"
    assert assignment["assignments"][0]["target"]["@odata.type"] == "#microsoft.graph.allDevicesAssignmentTarget"


@pytest.mark.asyncio
async def test_intune_apply_patches_existing_assigned_policy(graph):
    graph["compliance"] = [_compliance_policy(bitLockerEnabled=False)]
    result = await standards.get(INTUNE.id).apply(1, {})
    assert result["status"] == "remediated"
    [(url, payload)] = graph["patches"]
    assert url == library.COMPLIANCE_POLICIES_URL + "/cp-1"
    assert payload["bitLockerEnabled"] is True and payload["@odata.type"]
    assert graph["posts"] == []


@pytest.mark.asyncio
async def test_intune_apply_reports_graph_errors(graph):
    graph["fail"] = True
    assert (await standards.get(INTUNE.id).apply(1, {}))["status"] == "failed"
