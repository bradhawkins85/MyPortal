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
        reports.report_sections_repo, "get_company_report_settings",
        new=AsyncMock(return_value={"auto_hide_empty": False, "section_order": None}),
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
                "id": 1,
                "display_name": "Business Basic",
                "count": 10,
                "allocated": 7,
                "expiry_date": None,
                "contract_term": "annual",
            },
        ]),
    ), patch.object(
        reports.licenses_repo, "list_staff_by_license_for_company",
        new=AsyncMock(return_value={
            1: [{"first_name": "Alice", "last_name": "Smith", "email": "alice@example.com"}],
        }),
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
    level_keys = [lvl["level"] for lvl in e8.data["levels"]]
    ml1 = next(level for level in e8.data["levels"] if level["level"] == "ml1")
    assert ml1["compliant"] == 2
    assert ml1["total"] == 2
    # ML2 has in_progress=1 so it should be included; ML3 has no progress so it must be omitted.
    assert "ml2" in level_keys
    assert "ml3" not in level_keys

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
    ), patch.object(
        reports.report_sections_repo, "get_detail_preferences",
        new=AsyncMock(return_value={}),
    ), patch.object(
        reports.report_sections_repo, "get_company_report_settings",
        new=AsyncMock(return_value={"auto_hide_empty": False, "section_order": None}),
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


@pytest.mark.asyncio
async def test_auto_hide_empty_hides_sections_with_no_data():
    """Sections with empty data are disabled when auto_hide_empty is True."""
    from app.services import reports

    company = {"id": 10, "name": "EmptyCo"}
    # All sections enabled but all builders return empty/zero data.
    preferences = {key: True for key in reports.SECTION_KEYS}

    with patch.object(
        reports.company_repo, "get_company_by_id",
        new=AsyncMock(return_value=company),
    ), patch.object(
        reports.report_sections_repo, "get_section_preferences",
        new=AsyncMock(return_value=preferences),
    ), patch.object(
        reports.report_sections_repo, "get_company_report_settings",
        new=AsyncMock(return_value={"auto_hide_empty": True, "section_order": None}),
    ), patch.object(
        reports.assets_repo, "count_active_assets", new=AsyncMock(return_value=0),
    ), patch.object(
        reports.assets_repo, "count_active_assets_by_type", new=AsyncMock(return_value=0),
    ), patch.object(
        reports.staff_repo, "count_staff", new=AsyncMock(return_value=0),
    ), patch.object(
        reports.m365_bp_repo, "list_results", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.shop_repo, "list_order_summaries", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.licenses_repo, "list_company_licenses", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.subscriptions_repo, "list_subscriptions", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.essential8_repo, "list_essential8_controls", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.essential8_repo, "get_per_maturity_statuses_for_company",
        new=AsyncMock(return_value={}),
    ), patch.object(
        reports.compliance_checks_repo, "get_assignment_summary",
        new=AsyncMock(return_value={"total": 0, "compliance_percentage": 0.0,
                                    "in_progress": 0, "not_started": 0,
                                    "overdue_count": 0, "due_soon_count": 0}),
    ), patch.object(
        reports.asset_custom_fields_repo, "list_field_definitions",
        new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.issues_repo, "list_issues_with_assignments",
        new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.db, "fetch_all", new=AsyncMock(return_value=[]),
    ):
        report = await reports.build_company_report(10)

    assert report.auto_hide_empty is True
    # Every section should be marked empty and therefore disabled.
    for section in report.sections:
        assert section.is_empty is True, f"{section.key} should be empty"
        assert section.enabled is False, f"{section.key} should be auto-hidden"


@pytest.mark.asyncio
async def test_auto_hide_disabled_leaves_empty_sections_visible():
    """When auto_hide_empty is False, empty sections remain enabled."""
    from app.services import reports

    company = {"id": 11, "name": "ShowAll"}
    preferences = {key: True for key in reports.SECTION_KEYS}

    with patch.object(
        reports.company_repo, "get_company_by_id",
        new=AsyncMock(return_value=company),
    ), patch.object(
        reports.report_sections_repo, "get_section_preferences",
        new=AsyncMock(return_value=preferences),
    ), patch.object(
        reports.report_sections_repo, "get_company_report_settings",
        new=AsyncMock(return_value={"auto_hide_empty": False, "section_order": None}),
    ), patch.object(
        reports.assets_repo, "count_active_assets", new=AsyncMock(return_value=0),
    ), patch.object(
        reports.assets_repo, "count_active_assets_by_type", new=AsyncMock(return_value=0),
    ), patch.object(
        reports.staff_repo, "count_staff", new=AsyncMock(return_value=0),
    ), patch.object(
        reports.m365_bp_repo, "list_results", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.shop_repo, "list_order_summaries", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.licenses_repo, "list_company_licenses", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.subscriptions_repo, "list_subscriptions", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.essential8_repo, "list_essential8_controls", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.essential8_repo, "get_per_maturity_statuses_for_company",
        new=AsyncMock(return_value={}),
    ), patch.object(
        reports.compliance_checks_repo, "get_assignment_summary",
        new=AsyncMock(return_value={"total": 0, "compliance_percentage": 0.0,
                                    "in_progress": 0, "not_started": 0,
                                    "overdue_count": 0, "due_soon_count": 0}),
    ), patch.object(
        reports.asset_custom_fields_repo, "list_field_definitions",
        new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.issues_repo, "list_issues_with_assignments",
        new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.db, "fetch_all", new=AsyncMock(return_value=[]),
    ):
        report = await reports.build_company_report(11)

    assert report.auto_hide_empty is False
    # All sections are enabled even though their data is empty.
    assert all(s.enabled is True for s in report.sections)


@pytest.mark.asyncio
async def test_build_company_report_respects_section_order():
    """Custom section_order is reflected in the report sections list."""
    from app.services import reports

    company = {"id": 12, "name": "Ordered"}
    preferences = {key: True for key in reports.SECTION_KEYS}
    # Request staff first, then assets.
    custom_order = ["staff", "assets"] + [
        s.key for s in reports.REPORT_SECTIONS if s.key not in ("staff", "assets")
    ]

    with patch.object(
        reports.company_repo, "get_company_by_id",
        new=AsyncMock(return_value=company),
    ), patch.object(
        reports.report_sections_repo, "get_section_preferences",
        new=AsyncMock(return_value=preferences),
    ), patch.object(
        reports.report_sections_repo, "get_company_report_settings",
        new=AsyncMock(return_value={"auto_hide_empty": False, "section_order": custom_order}),
    ), patch.object(
        reports.assets_repo, "count_active_assets", new=AsyncMock(return_value=5),
    ), patch.object(
        reports.assets_repo, "count_active_assets_by_type", new=AsyncMock(return_value=2),
    ), patch.object(
        reports.staff_repo, "count_staff", new=AsyncMock(return_value=3),
    ), patch.object(
        reports.m365_bp_repo, "list_results", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.shop_repo, "list_order_summaries", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.licenses_repo, "list_company_licenses", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.subscriptions_repo, "list_subscriptions", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.essential8_repo, "list_essential8_controls", new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.essential8_repo, "get_per_maturity_statuses_for_company",
        new=AsyncMock(return_value={}),
    ), patch.object(
        reports.compliance_checks_repo, "get_assignment_summary",
        new=AsyncMock(return_value={"total": 0, "compliance_percentage": 0.0,
                                    "in_progress": 0, "not_started": 0,
                                    "overdue_count": 0, "due_soon_count": 0}),
    ), patch.object(
        reports.asset_custom_fields_repo, "list_field_definitions",
        new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.issues_repo, "list_issues_with_assignments",
        new=AsyncMock(return_value=[]),
    ), patch.object(
        reports.db, "fetch_all", new=AsyncMock(return_value=[]),
    ):
        report = await reports.build_company_report(12)

    assert report.sections[0].key == "staff"
    assert report.sections[1].key == "assets"
    assert len(report.sections) == len(reports.REPORT_SECTIONS)


# ---------------------------------------------------------------------------
# Detailed report tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_section_detail_visibility_defaults_to_false():
    """get_section_detail_visibility returns False for every section when no prefs are stored."""
    from app.services import reports

    with patch.object(
        reports.report_sections_repo, "get_detail_preferences",
        new=AsyncMock(return_value={}),
    ):
        result = await reports.get_section_detail_visibility(99)

    assert set(result.keys()) == set(reports.SECTION_KEYS)
    assert all(v is False for v in result.values())


@pytest.mark.asyncio
async def test_save_section_detail_visibility_persists():
    """save_section_detail_visibility calls set_detail_preferences with cleaned values."""
    from app.services import reports

    captured: dict = {}

    async def fake_set(company_id, cleaned, *, valid_keys=None):
        captured["company_id"] = company_id
        captured["cleaned"] = dict(cleaned)
        captured["valid_keys"] = set(valid_keys) if valid_keys is not None else None

    with patch.object(
        reports.report_sections_repo, "set_detail_preferences",
        new=fake_set,
    ):
        result = await reports.save_section_detail_visibility(
            7,
            {"assets": True, "staff": False, "not_a_real_section": True},
        )

    assert set(result.keys()) == set(reports.SECTION_KEYS)
    assert result["assets"] is True
    assert result["staff"] is False
    assert "not_a_real_section" not in result
    assert captured["company_id"] == 7
    assert captured["cleaned"]["assets"] is True
    assert captured["cleaned"]["staff"] is False
    assert "not_a_real_section" not in captured["cleaned"]


def _make_full_patches(reports_module):
    """Return a list of (target, mock) tuples for all section builders."""
    return [
        (reports_module.assets_repo, "count_active_assets", AsyncMock(return_value=5)),
        (reports_module.assets_repo, "count_active_assets_by_type", AsyncMock(return_value=2)),
        (reports_module.staff_repo, "count_staff", AsyncMock(return_value=2)),
        (reports_module.m365_bp_repo, "list_results", AsyncMock(return_value=[])),
        (reports_module.shop_repo, "list_order_summaries", AsyncMock(return_value=[])),
        (reports_module.licenses_repo, "list_company_licenses", AsyncMock(return_value=[])),
        (reports_module.subscriptions_repo, "list_subscriptions", AsyncMock(return_value=[])),
        (reports_module.essential8_repo, "list_essential8_controls", AsyncMock(return_value=[])),
        (reports_module.essential8_repo, "get_per_maturity_statuses_for_company", AsyncMock(return_value={})),
        (reports_module.compliance_checks_repo, "get_assignment_summary", AsyncMock(return_value={
            "total": 0, "compliance_percentage": 0.0, "in_progress": 0,
            "not_started": 0, "overdue_count": 0, "due_soon_count": 0,
        })),
        (reports_module.asset_custom_fields_repo, "list_field_definitions", AsyncMock(return_value=[])),
        (reports_module.issues_repo, "list_issues_with_assignments", AsyncMock(return_value=[])),
        (reports_module.db, "fetch_all", AsyncMock(return_value=[])),
    ]


@pytest.mark.asyncio
async def test_build_company_report_populates_detail_data():
    """When detailed=True for a section, detail_data and detailed flag are set on SectionResult."""
    from app.services import reports
    from unittest.mock import AsyncMock, patch
    from contextlib import ExitStack

    company = {"id": 20, "name": "DetailCo"}
    preferences = {key: True for key in reports.SECTION_KEYS}
    # Only assets section is detailed.
    detail_prefs = {key: (key == "assets") for key in reports.SECTION_KEYS}

    patches = [
        patch.object(reports.company_repo, "get_company_by_id", new=AsyncMock(return_value=company)),
        patch.object(reports.report_sections_repo, "get_section_preferences", new=AsyncMock(return_value=preferences)),
        patch.object(reports.report_sections_repo, "get_detail_preferences", new=AsyncMock(return_value=detail_prefs)),
        patch.object(reports.report_sections_repo, "get_company_report_settings",
                     new=AsyncMock(return_value={"auto_hide_empty": False, "section_order": None})),
        # Detail builder for assets.
        patch.object(reports.assets_repo, "list_company_assets",
                     new=AsyncMock(return_value=[
                         {"id": 1, "name": "Server01", "type": "server", "os_name": "Windows Server 2022",
                          "status": "active", "serial_number": "SN123", "last_sync": None,
                          "last_user": None, "form_factor": None, "warranty_status": None,
                          "warranty_end_date": None},
                     ])),
    ]
    # Add all summary builder mocks.
    for (obj, attr, mock) in _make_full_patches(reports):
        patches.append(patch.object(obj, attr, new=mock))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        report = await reports.build_company_report(20)

    assets_section = report.section("assets")
    assert assets_section is not None
    assert assets_section.detailed is True
    assert assets_section.detail_data.get("total") == 1
    detail_assets = assets_section.detail_data.get("assets") or []
    assert len(detail_assets) == 1
    assert detail_assets[0]["name"] == "Server01"

    # Other sections should not be detailed.
    for section in report.sections:
        if section.key != "assets":
            assert section.detailed is False
            assert section.detail_data == {}


@pytest.mark.asyncio
async def test_build_company_report_disabled_section_not_detailed():
    """A disabled section must never have detailed=True even if detail prefs say so."""
    from app.services import reports
    from unittest.mock import AsyncMock, patch
    from contextlib import ExitStack

    company = {"id": 21, "name": "DisabledCo"}
    # All sections disabled.
    preferences = {key: False for key in reports.SECTION_KEYS}
    # All sections marked as detailed.
    detail_prefs = {key: True for key in reports.SECTION_KEYS}

    patches = [
        patch.object(reports.company_repo, "get_company_by_id", new=AsyncMock(return_value=company)),
        patch.object(reports.report_sections_repo, "get_section_preferences", new=AsyncMock(return_value=preferences)),
        patch.object(reports.report_sections_repo, "get_detail_preferences", new=AsyncMock(return_value=detail_prefs)),
        patch.object(reports.report_sections_repo, "get_company_report_settings",
                     new=AsyncMock(return_value={"auto_hide_empty": False, "section_order": None})),
    ]

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        report = await reports.build_company_report(21)

    for section in report.sections:
        assert section.enabled is False
        assert section.detailed is False
        assert section.detail_data == {}

