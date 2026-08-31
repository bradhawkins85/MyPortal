from unittest.mock import AsyncMock

import pytest

from app.services import reports


@pytest.mark.anyio("asyncio")
async def test_all_maturity_report_always_contains_three_levels(monkeypatch):
    monkeypatch.setattr(reports.essential8_repo, "list_essential8_controls", AsyncMock(return_value=[{"id": 1}]))
    monkeypatch.setattr(reports.essential8_repo, "get_per_maturity_statuses_for_company", AsyncMock(return_value={1: {"ml1": "compliant"}}))

    result = await reports._build_essential8(7)

    assert [row["level"] for row in result["levels"]] == ["ml1", "ml2", "ml3"]
    assert [row["percentage"] for row in result["levels"]] == [100.0, 0.0, 0.0]


@pytest.mark.anyio("asyncio")
async def test_recommendations_report_lists_current_level_gap(monkeypatch):
    monkeypatch.setattr(reports.essential8_repo, "list_company_compliance", AsyncMock(return_value=[{
        "control_id": 1, "maturity_level": "ml2", "control": {"name": "Patch applications"},
    }]))
    monkeypatch.setattr(reports.essential8_repo, "list_essential8_requirements", AsyncMock(return_value=[
        {"id": 11, "control_id": 1, "maturity_level": "ml1", "description": "Old level"},
        {"id": 12, "control_id": 1, "maturity_level": "ml2", "description": "Patch quickly"},
    ]))
    monkeypatch.setattr(reports.essential8_repo, "list_company_requirement_compliance", AsyncMock(return_value=[
        {"requirement_id": 12, "status": "non_compliant"},
    ]))
    monkeypatch.setattr(reports.essential8_repo, "list_requirement_marketing_page_links", AsyncMock(return_value=[{
        "requirement_id": 12, "recommendation_name": "Managed patching", "external_url": "https://example.com/patching",
    }]))

    result = await reports._build_essential8_recommendations(7)

    assert result["total"] == 1
    assert result["recommendations"][0]["control"] == "Patch applications"
    assert result["recommendations"][0]["url"] == "https://example.com/patching"
