from datetime import datetime
from pathlib import Path

import jinja2
import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData

from app.features.m365_policies import PACK
from app.features.m365_policies import api_routes
from app.features.m365_policies import routes as page_routes
from app.repositories import m365_policies as policy_repo
from app.schemas.m365_policies import PolicyPreviewRequest, PolicyWrite
from app.services import m365_policies as policies
from app.services import m365_policy_targeting as targeting
from app.services import m365_standard_library as library

ACME = {"company_id": 1, "company_name": "Acme", "company_tags": [], "company_variables": {"Tier": "Gold"},
        "license_skus": ["SPB"], "license_names": ["Microsoft 365 Business Premium"]}
GLOBEX = {"company_id": 2, "company_name": "Globex", "company_tags": [], "company_variables": {},
          "license_skus": ["O365_BUSINESS_ESSENTIALS"], "license_names": []}
SPB_RULES = {"operator": "and", "children": [{"field": "m365.license_sku", "operator": "in", "value": ["SPB"]}]}
ADMIN = {"id": 7, "is_super_admin": True}


def test_describe_rules_reads_as_plain_english():
    assert targeting.describe_rules(None) == "Every connected tenant"
    assert targeting.describe_rules(SPB_RULES) == "Tenants where Licence SKU is one of SPB"
    nested = {"operator": "or", "children": [
        {"field": "company.variable", "key": "Tier", "operator": "equals", "value": "Gold"},
        {"operator": "and", "children": [
            {"field": "company.tags", "operator": "equals", "value": "VIP"},
            {"field": "m365.license_name", "operator": "is_set"},
        ]},
    ]}
    assert targeting.describe_rules(nested) == (
        "Tenants where Company variable Tier is “Gold” or (Company tag is “VIP” and Licence name is set)")


def test_matching_company_ids_applies_overrides_after_filter():
    contexts = {1: ACME, 2: GLOBEX}
    assert policies.matching_company_ids(SPB_RULES, [], contexts) == [1]
    assert policies.matching_company_ids(SPB_RULES, [{"company_id": 2, "action": "include"},
                                                     {"company_id": 1, "action": "exclude"}], contexts) == [2]
    with pytest.raises(ValueError):
        policies.matching_company_ids({"operator": "and", "children": []}, [], contexts)


def test_policy_from_form_reads_editor_fields():
    form = FormData([
        ("name", "Baseline"), ("description", "Core"), ("enabled", "1"),
        ("standard", library.DELEGATED_SENT_ITEMS), ("mode_" + library.DELEGATED_SENT_ITEMS, "remediate"),
        ("target_rules_json", '{"operator": "and", "children": []}'),
        ("overrides_json", '[{"company_id": 2, "action": "exclude"}]'),
    ])
    payload = page_routes.policy_from_form(form)
    assert payload["enabled"] is True
    assert payload["standards"] == [{"standard_id": library.DELEGATED_SENT_ITEMS, "mode": "remediate"}]
    assert payload["target_rules"] == {"operator": "and", "children": []}
    assert payload["overrides"] == [{"company_id": 2, "action": "exclude"}]

    empty = page_routes.policy_from_form(FormData([("name", "x")]))
    assert empty["enabled"] is False and empty["target_rules"] is None and empty["overrides"] == []
    with pytest.raises(ValueError):
        page_routes.policy_from_form(FormData([("name", "x"), ("target_rules_json", "{not json")]))
    with pytest.raises(ValueError):
        page_routes.policy_from_form(FormData([("name", "x"), ("overrides_json", '{"company_id": 1}')]))


def test_filter_results_searches_filters_and_pages():
    rows = [{"company_name": f"Tenant {index}", "standard_name": "Sent Items", "policy_name": "Baseline",
             "details": "", "status": "drift" if index % 2 else "compliant"} for index in range(120)]
    page = page_routes.filter_results(rows, status="drift", page=2)
    assert page["total"] == 60 and page["total_pages"] == 2 and len(page["rows"]) == 10
    assert page_routes.filter_results(rows, q="tenant 11", page=99)["total"] == 11
    assert page_routes.filter_results([], page=5)["page"] == 1


@pytest.mark.asyncio
async def test_page_data_summarises_policies_and_results(monkeypatch):
    async def contexts(company_ids=None):
        return {1: ACME, 2: GLOBEX}

    async def list_policies():
        return [
            {"id": 1, "name": "Baseline", "description": "", "enabled": True, "target_rules": SPB_RULES,
             "standards": [{"standard_id": library.DELEGATED_SENT_ITEMS, "mode": "remediate", "settings": {}}],
             "overrides": [{"company_id": 2, "action": "include"}]},
            {"id": 2, "name": "Broken", "description": "", "enabled": True,
             "target_rules": {"operator": "and", "children": []}, "standards": [], "overrides": []},
        ]

    async def list_results(company_id=None):
        return [
            {"company_id": 2, "standard_id": library.DELEGATED_SENT_ITEMS, "policy_id": 1, "mode": "remediate",
             "status": "remediated", "details": "", "affected": [], "checked_at": datetime(2026, 10, 30)},
            {"company_id": 9, "standard_id": library.DELEGATED_SENT_ITEMS, "policy_id": 1, "mode": "report",
             "status": "drift", "details": "", "affected": [], "checked_at": datetime(2026, 10, 30)},
        ]

    async def definitions():
        return [{"id": 1, "name": "Tier"}]

    monkeypatch.setattr(policies, "tenant_contexts", contexts)
    monkeypatch.setattr(policy_repo, "list_policies", list_policies)
    monkeypatch.setattr(policy_repo, "list_results", list_results)
    monkeypatch.setattr(policies.company_variables_repo, "list_definitions", definitions)

    data = await policies.page_data()
    baseline, broken = data["policies"]
    assert baseline["target_count"] == 2
    assert baseline["target_summary"] == "Tenants where Licence SKU is one of SPB"
    assert baseline["mode_counts"] == {"remediate": 1}
    assert baseline["standards"][0]["name"] == "Keep delegate-sent mail in Sent Items"
    assert baseline["overrides"][0]["company_name"] == "Globex"
    assert broken["target_count"] == 0 and broken["target_summary"].startswith("These filter rules can't be read")
    assert [row["company_name"] for row in data["results"]] == ["Globex"]
    assert data["status_counts"] == {"remediated": 1}
    assert data["suggestions"] == {"m365.license_sku": ["O365_BUSINESS_ESSENTIALS", "SPB"],
                                   "m365.license_name": ["Microsoft 365 Business Premium"],
                                   "company.variable": ["Tier"]}
    assert [tenant["company_name"] for tenant in data["tenants"]] == ["Acme", "Globex"]


@pytest.mark.asyncio
async def test_save_and_delete_are_audited(monkeypatch):
    stored = {"policy": None}
    audits = []

    async def get_policy(policy_id):
        return stored["policy"]

    async def save_policy(payload, *, policy_id=None, user_id=None):
        stored["policy"] = {"id": 5, **payload}
        return 5

    async def delete_policy(policy_id):
        stored["policy"] = None

    async def record(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(policy_repo, "get_policy", get_policy)
    monkeypatch.setattr(policies, "save_policy", save_policy)
    monkeypatch.setattr(policy_repo, "delete_policy", delete_policy)
    monkeypatch.setattr(policies.audit_service, "record", record)
    monkeypatch.setattr(policies.audit_service, "record_delete", record)

    assert await policies.save_policy_audited({"name": "Baseline"}, user_id=7) == 5
    assert audits[0]["action"] == "m365.policy.create" and audits[0]["before"] is None
    assert audits[0]["after"]["name"] == "Baseline"
    await policies.save_policy_audited({"name": "Renamed"}, policy_id=5, user_id=7)
    assert audits[1]["action"] == "m365.policy.update" and audits[1]["before"]["name"] == "Baseline"
    await policies.delete_policy(5, user_id=7)
    assert audits[2]["action"] == "m365.policy.delete" and audits[2]["entity_id"] == 5
    with pytest.raises(LookupError):
        await policies.delete_policy(5)
    with pytest.raises(LookupError):
        await policies.save_policy_audited({"name": "x"}, policy_id=5)


@pytest.mark.asyncio
async def test_api_maps_errors_to_http_status(monkeypatch):
    async def missing(policy_id):
        return None

    async def invalid(*args, **kwargs):
        raise ValueError("Give the policy a name")

    async def not_found(*args, **kwargs):
        raise LookupError("Policy not found")

    monkeypatch.setattr(policy_repo, "get_policy", missing)
    with pytest.raises(HTTPException) as exc:
        await api_routes.get_policy(3, user=ADMIN)
    assert exc.value.status_code == 404

    monkeypatch.setattr(policies, "save_policy_audited", invalid)
    with pytest.raises(HTTPException) as exc:
        await api_routes.create_policy(PolicyWrite(name="x"), request=None, user=ADMIN)
    assert exc.value.status_code == 422

    monkeypatch.setattr(policies, "delete_policy", not_found)
    with pytest.raises(HTTPException) as exc:
        await api_routes.delete_policy(3, request=None, user=ADMIN)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await api_routes.preview(PolicyPreviewRequest(target_rules={"operator": "and", "children": []}), user=ADMIN)
    assert exc.value.status_code == 422


def test_api_routes_require_super_admin_and_are_documented():
    routes = {(route.path, tuple(sorted(route.methods))): route for route in api_routes.router.routes}
    expected = {
        ("/api/m365/policies", ("GET",)), ("/api/m365/policies", ("POST",)),
        ("/api/m365/policies/{policy_id}", ("GET",)), ("/api/m365/policies/{policy_id}", ("PUT",)),
        ("/api/m365/policies/{policy_id}", ("DELETE",)), ("/api/m365/policies/standards", ("GET",)),
        ("/api/m365/policies/fields", ("GET",)), ("/api/m365/policies/preview", ("POST",)),
        ("/api/m365/policies/results", ("GET",)), ("/api/m365/policies/run", ("POST",)),
    }
    assert expected <= set(routes)
    for route in routes.values():
        assert route.summary
        assert any(dep.call is api_routes.require_super_admin for dep in route.dependant.dependencies)
    paths = [route.path for route in api_routes.router.routes]
    assert paths.index("/api/m365/policies/standards") < paths.index("/api/m365/policies/{policy_id}")
    assert PACK.slug == "m365_policies"


def test_template_compiles_and_follows_page_conventions():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader("app/templates"))
    env.get_template("m365/policies.html")
    template = Path("app/templates/m365/policies.html").read_text(encoding="utf-8")
    assert '{% block header_title %}Policies{% endblock %}' in template
    assert 'class="modal scf-modal mpol-modal" id="mpol-modal" role="dialog"' in template
    assert '<script type="application/json" id="mpol-data">' in template
    assert 'data-utc=' in template and 'strftime' not in template
    assert "partials/csrf.html" in template
    base = Path("app/templates/base.html").read_text(encoding="utf-8")
    assert base.count('href="/m365/policies"') == 2
    script = Path("app/static/js/m365_policies.js").read_text(encoding="utf-8")
    assert "X-CSRF-Token" in script and "/api/m365/policies/preview" in script


def _page_env() -> jinja2.Environment:
    class _Loader(jinja2.FileSystemLoader):
        def get_source(self, environment, template):
            if template == "base.html":
                source = ("{% block styles %}{% endblock %}{% block header_actions %}{% endblock %}"
                          "{% block content %}{% endblock %}{% block modals %}{% endblock %}"
                          "{% block scripts %}{% endblock %}")
                return source, None, lambda: True
            return super().get_source(environment, template)

    env = jinja2.Environment(loader=_Loader("app/templates"), autoescape=True)
    env.globals["static_url"] = lambda path: path
    return env


def test_page_renders_policies_results_and_editor_data():
    import json
    import re

    from app.services import m365_standards as standards

    policy = {"id": 1, "name": "Baseline <BP>", "description": "Core", "enabled": True, "target_rules": SPB_RULES,
              "standards": [{"standard_id": library.DELEGATED_SENT_ITEMS, "mode": "remediate", "settings": {},
                             "name": "Keep delegate-sent mail in Sent Items"}],
              "overrides": [{"company_id": 2, "action": "include", "company_name": "Globex"}],
              "target_count": 2, "target_summary": "Tenants where Licence SKU is one of SPB",
              "mode_counts": {"remediate": 1}, "rules_error": None}
    result = {"company_id": 2, "company_name": "Globex", "standard_name": "Keep delegate-sent mail in Sent Items",
              "policy_name": "Baseline <BP>", "mode": "remediate", "status": "failed", "details": "Could not update",
              "checked_at": datetime(2026, 10, 30, 4, 0)}
    html = _page_env().get_template("m365/policies.html").render(
        policies=[policy], results=[result], status_counts={"failed": 1},
        standards=standards.catalogue(), fields=targeting.field_catalogue(),
        tenants=[{"company_id": 1, "company_name": "Acme"}, {"company_id": 2, "company_name": "Globex"}],
        suggestions={"m365.license_sku": ["SPB"], "m365.license_name": [], "company.variable": ["Tier"]},
        result_page={"rows": [result], "total": 1, "page": 1, "total_pages": 1},
        result_filters={"q": "a b&c", "status": "failed"}, result_statuses=page_routes.RESULT_STATUSES,
    )
    assert "Baseline &lt;BP&gt;" in html and "<BP>" not in html.split('id="mpol-data">')[0]
    assert "Targets 2 tenants" in html and "1 remediate" in html and "1 exception" in html
    assert 'data-mpol-edit="1"' in html and 'data-mpol-duplicate="1"' in html
    assert '<span class="status status--error">Fix failed</span>' in html
    assert 'data-utc="2026-10-30T04:00:00"' in html
    assert 'name="mode_' + library.DELEGATED_SENT_ITEMS + '" value="remediate"' in html
    assert '<datalist id="mpol-suggest-m365-license_sku"><option value="SPB"></option></datalist>' in html
    data = json.loads(re.search(r'id="mpol-data">(.*?)</script>', html, re.S).group(1))
    assert data["policies"][0]["target_rules"] == SPB_RULES
    assert {item["id"] for item in data["standards"]} >= {library.DELEGATED_SENT_ITEMS}


def test_empty_page_offers_first_policy():
    from app.services import m365_standards as standards

    html = _page_env().get_template("m365/policies.html").render(
        policies=[], results=[], status_counts={}, standards=standards.catalogue(),
        fields=targeting.field_catalogue(), tenants=[], suggestions={},
        result_page={"rows": [], "total": 0, "page": 1, "total_pages": 1},
        result_filters={"q": "", "status": ""}, result_statuses=page_routes.RESULT_STATUSES,
    )
    assert "No policies yet" in html and "+ Add your first policy" in html
    assert "No results yet" in html
