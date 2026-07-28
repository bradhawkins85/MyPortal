"""Coverage for the separate Huntress Managed SAT company identifier."""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.anyio
async def test_sat_account_lookup_matches_jsonapi_account_name(monkeypatch):
    from app.services import company_id_lookup

    monkeypatch.setattr(
        company_id_lookup.huntress_service,
        "list_sat_accounts",
        AsyncMock(return_value=[{"id": "sat-42", "name": "Acme Corp"}]),
    )

    assert await company_id_lookup._lookup_huntress_sat_account_id("acme corp") == "sat-42"


@pytest.mark.anyio
async def test_refresh_company_uses_sat_id_only_for_sat_calls(monkeypatch):
    from app.services import huntress

    monkeypatch.setattr(huntress, "get_edr_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(huntress, "get_itdr_summary", AsyncMock(return_value=None))
    sat_summary = AsyncMock(return_value=None)
    sat_learners = AsyncMock(return_value=None)
    monkeypatch.setattr(huntress, "get_sat_summary", sat_summary)
    monkeypatch.setattr(huntress, "get_sat_learner_breakdown", sat_learners)
    monkeypatch.setattr(huntress, "get_siem_data_volume", AsyncMock(return_value=None))
    monkeypatch.setattr(huntress, "get_soc_event_count", AsyncMock(return_value=None))

    await huntress.refresh_company(
        {
            "id": 7,
            "huntress_organization_id": "edr-11",
            "huntress_sat_account_id": "sat-22",
        }
    )

    sat_summary.assert_awaited_once_with("sat-22")
    sat_learners.assert_awaited_once_with("sat-22")
    huntress.get_edr_summary.assert_awaited_once_with("edr-11")


def test_company_edit_exposes_sat_account_lookup():
    template = open("app/templates/admin/company_edit.html", encoding="utf-8").read()
    script = open("app/static/js/admin.js", encoding="utf-8").read()

    assert 'name="huntressSatAccountId"' in template
    assert "data-lookup-huntress-sat-id" in template
    assert "lookup-huntress-sat-id" in script
