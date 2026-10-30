from pathlib import Path

import pytest

from app.services import m365_best_practices as best_practices
from app.services import m365_standard_library as library
from app.services import m365_standards as standards
from app.services.m365 import M365Error


@pytest.fixture
def single_check(monkeypatch):
    state = {"result": {"status": "pass", "details": "", "affected_accounts": []}, "calls": []}

    async def run(company_id, check_id, **kwargs):
        state["calls"].append((company_id, check_id, kwargs))
        if isinstance(state["result"], Exception):
            raise state["result"]
        return dict(state["result"])

    monkeypatch.setattr(best_practices, "run_single_check", run)
    return state


def _first(has_remediation):
    return next(bp for bp in best_practices.list_best_practices() if bool(bp.get("has_remediation")) is has_remediation)


def test_every_best_practice_is_a_standard():
    for bp in best_practices.list_best_practices():
        standard = standards.get(bp["id"])
        assert standard is not None, bp["id"]
        assert standard.name == bp["name"]
        assert ("remediate" in standard.modes) is bool(bp.get("has_remediation"))


def test_registration_is_idempotent():
    assert library.register_best_practices() == 0


def test_categories_group_best_practices_and_intune_benchmarks():
    categories = {standards.get(bp["id"]).category for bp in best_practices.list_best_practices()}
    assert "Best Practices" in categories
    assert "Microsoft 365" not in categories
    assert any(category.startswith("CIS Intune Benchmark") for category in categories)


@pytest.mark.asyncio
async def test_check_runs_without_tickets_remediation_or_enabled_gate(single_check):
    bp = _first(False)
    single_check["result"] = {"status": "fail", "details": "Two admins lack MFA.",
                               "affected_accounts": [{"id": "1", "name": "a@x.com"}, {"id": "2", "name": ""}]}
    result = await standards.get(bp["id"]).check(7, {})
    assert result == {"status": "fail", "details": "Two admins lack MFA.", "affected": ["a@x.com", "2"]}
    assert single_check["calls"] == [(7, bp["id"], {
        "allow_auto_remediation": False, "emit_ticket_on_fail": False, "require_enabled": False,
    })]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["not_licensed", "not_applicable", "permission_missing", "assessment_failed", "odd"])
async def test_other_statuses_are_unknown_with_a_reason(single_check, status):
    single_check["result"] = {"status": status, "details": "Detail.", "affected_accounts": [{"name": "x"}]}
    result = await standards.get(_first(False)["id"]).check(7, {})
    assert result["status"] == "unknown"
    assert result["details"].endswith("Detail.")
    assert len(result["details"]) > len("Detail.")
    assert result["affected"] == []


@pytest.mark.asyncio
async def test_check_errors_become_unknown(single_check):
    single_check["result"] = M365Error("No M365 credentials found for company")
    result = await standards.get(_first(False)["id"]).check(7, {})
    assert result["status"] == "unknown"
    assert "No M365 credentials" in result["details"]


@pytest.mark.asyncio
@pytest.mark.parametrize("success,status", [(True, "remediated"), (False, "failed")])
async def test_apply_maps_remediation_outcome(monkeypatch, success, status):
    bp = _first(True)
    calls = []

    async def remediate(company_id, check_id):
        calls.append((company_id, check_id))
        return {"success": success, "message": "Done." if success else "Denied."}

    monkeypatch.setattr(best_practices, "remediate_check", remediate)
    result = await standards.get(bp["id"]).apply(7, {})
    assert result == {"status": status, "details": "Done." if success else "Denied."}
    assert calls == [(7, bp["id"])]


@pytest.mark.asyncio
async def test_run_single_check_still_requires_enabled_by_default(monkeypatch):
    async def enabled():
        return set()

    monkeypatch.setattr(best_practices, "get_enabled_check_ids", enabled)
    with pytest.raises(ValueError, match="not enabled"):
        await best_practices.run_single_check(1, best_practices.list_best_practices()[0]["id"])


def test_standards_editor_is_grouped_and_searchable():
    template = Path("app/templates/m365/policies.html").read_text()
    assert "groupby('category')" in template
    assert "data-mpol-standard-search" in template
    assert "data-mpol-standard-empty" in template
