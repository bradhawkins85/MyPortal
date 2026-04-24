"""Tests for the company overview report service."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_build_company_report_assembles_all_sections():
    from app.services import reports

    company = {"id": 42, "name": "Acme Pty Ltd", "address": "1 Test St"}

    # Stub every repo/db call used by the report builder.
    with patch.object(
        reports.company_repo, "get_company_by_id",
        new=AsyncMock(return_value=company),
    ), patch.object(
        reports.report_sections_repo, "get_section_preferences",
        new=AsyncMock(return_value={}),  # all sections default to enabled
    ), patch.object(
        reports.assets_repo, "count_active_assets",
        new=AsyncMock(return_value=7),
    ), patch.object(
        reports.assets_repo, "count_active_assets_by_type",
        new=AsyncMock(side_effect=lambda *, company_id, since, device_type: (
            2 if device_type == "server" else 5
        )),
    ), patch.object(
        reports.staff_repo, "count_staff",
        new=AsyncMock(return_value=3),
    ), patch.object(
        reports.m365_bp_repo, "list_results",
        new=AsyncMock(return_value=[
            {"status": "pass", "run_at": datetime(2026, 4, 1, 10, 0, 0)},
            {"status": "fail", "run_at": datetime(2026, 4, 2, 10, 0, 0)},
        ]),
    ), patch.object(
        reports.shop_repo, "list_order_summaries",
        new=AsyncMock(return_value=[
            {
                "order_number": "ORD-1",
                "order_date": datetime.now(timezone.utc).date(),
                "status": "shipped",
                "shipping_status": None,
                "po_number": None,
            },
        ]),
    ), patch.object(
        reports.licenses_repo, "list_company_licenses",
        new=AsyncMock(return_value=[
            {
                "display_name": "Business Basic",
                "count": 10,
                "allocated": 7,
                "expiry_date": None,
                "contract_term": "annual",
            },
        ]),
    ), patch.object(
        reports.subscriptions_repo, "list_subscriptions",
        new=AsyncMock(return_value=[
            {
                "id": "sub-1",
                "product_name": "Support",
                "category_name": "Managed",
                "quantity": 1,
                "status": "active",
                "start_date": None,
                "end_date": None,
                "commitment_term": "monthly",
            },
        ]),
    ), patch.object(
        reports.essential8_repo, "list_essential8_controls",
        new=AsyncMock(return_value=[{"id": 1}, {"id": 2}]),
    ), patch.object(
        reports.essential8_repo, "get_per_maturity_statuses_for_company",
        new=AsyncMock(return_value={
            1: {"ml1": "compliant", "ml2": "in_progress", "ml3": "not_started"},
            2: {"ml1": "compliant", "ml2": "not_started", "ml3": "not_started"},
        }),
    ), patch.object(
        reports.compliance_checks_repo, "get_assignment_summary",
        new=AsyncMock(return_value={
            "total": 4,
            "compliance_percentage": 50.0,
            "in_progress": 1,
            "not_started": 1,
            "overdue_count": 1,
            "due_soon_count": 0,
        }),
    ), patch.object(
        reports.asset_custom_fields_repo, "list_field_definitions",
        new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.issues_repo, "list_issues_with_assignments",
        new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.db, "fetch_all",
        new=AsyncMock(return_value=[]),  # mailboxes + tickets queries
    ):
        report = await reports.build_company_report(42)

    assert report.company["name"] == "Acme Pty Ltd"
    # One section for every entry in REPORT_SECTIONS, all enabled.
    assert len(report.sections) == len(reports.REPORT_SECTIONS)
    assert all(s.enabled for s in report.sections)

    assets = report.section("assets")
    assert assets is not None and assets.data["total_synced"] == 7
    assert assets.data["servers"] == 2
    assert assets.data["workstations"] == 5
    assert "since" in assets.data

    staff = report.section("staff")
    assert staff is not None and staff.data["total_active"] == 3

    m365 = report.section("m365_best_practices")
    assert m365 is not None
    assert m365.data["counts"]["pass"] == 1
    assert m365.data["counts"]["fail"] == 1
    assert m365.data["counts"]["not_applicable"] == 0
    assert m365.data["total"] == 2
    assert m365.data["pass_percentage"] == 50.0

    orders = report.section("orders_current_month")
    assert orders is not None and orders.data["total"] == 1

    licenses = report.section("licenses")
    assert licenses is not None and licenses.data["licenses"][0]["allocated"] == 7

    e8 = report.section("essential8")
    assert e8 is not None
    ml1 = next(level for level in e8.data["levels"] if level["level"] == "ml1")
    assert ml1["compliant"] == 2
    assert ml1["total"] == 2

    tickets = report.section("tickets_last_month")
    assert tickets is not None and tickets.data["total"] == 0

    issues = report.section("issues")
    assert issues is not None and issues.data["total"] == 0


@pytest.mark.asyncio
async def test_build_company_report_respects_disabled_sections():
    from app.services import reports

    company = {"id": 1, "name": "Test"}
    preferences = {key: False for key in reports.SECTION_KEYS}

    with patch.object(
        reports.company_repo, "get_company_by_id",
        new=AsyncMock(return_value=company),
    ), patch.object(
        reports.report_sections_repo, "get_section_preferences",
        new=AsyncMock(return_value=preferences),
    ):
        report = await reports.build_company_report(1)

    assert all(s.enabled is False for s in report.sections)
    assert all(s.data == {} for s in report.sections)


@pytest.mark.asyncio
async def test_save_section_visibility_filters_invalid_keys():
    from app.services import reports

    captured: dict = {}

    async def fake_set(company_id, cleaned, *, valid_keys=None):
        captured["company_id"] = company_id
        captured["cleaned"] = dict(cleaned)
        captured["valid_keys"] = set(valid_keys) if valid_keys is not None else None

    with patch.object(
        reports.report_sections_repo, "set_section_preferences",
        new=fake_set,
    ):
        result = await reports.save_section_visibility(
            5,
            {"assets": "true", "not_a_section": "true", "staff": "no"},
        )

    # Every canonical section present; bogus key ignored.
    assert set(result.keys()) == set(reports.SECTION_KEYS)
    assert result["assets"] is True
    assert result["staff"] is False
    assert "not_a_section" not in result
    assert "not_a_section" not in captured["cleaned"]


@pytest.mark.asyncio
async def test_build_company_report_raises_for_missing_company():
    from app.services import reports

    with patch.object(
        reports.company_repo, "get_company_by_id",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(ValueError):
            await reports.build_company_report(999)
