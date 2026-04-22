"""Tests for the Customer Compliance Checks module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import db
from app.repositories import compliance_checks as repo
from app.schemas.compliance_checks import CheckStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_company(name: str) -> int:
    return await db.execute("INSERT INTO companies (name) VALUES (%(name)s)", {"name": name})


async def _cleanup_company(company_id: int) -> None:
    await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_categories_includes_seeded():
    """Seeded GMP, GLP and CUSTOM categories should exist after migrations."""
    await db.connect()
    try:
        categories = await repo.list_categories()
        codes = {c["code"] for c in categories}
        assert "GMP" in codes
        assert "GLP" in codes
        assert "CUSTOM" in codes
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_create_and_get_category():
    """Creating a custom category and retrieving it works."""
    await db.connect()
    try:
        cat = await repo.create_category(
            code="TEST_CAT_CC",
            name="Test Category CC",
            description="For test purposes",
            is_system=False,
        )
        assert cat["id"] is not None
        assert cat["code"] == "TEST_CAT_CC"
        assert cat["name"] == "Test Category CC"

        fetched = await repo.get_category(cat["id"])
        assert fetched is not None
        assert fetched["id"] == cat["id"]
    finally:
        # Cleanup (only non-system categories can be deleted)
        await repo.delete_category(cat["id"])
        await db.disconnect()


@pytest.mark.asyncio
async def test_update_category():
    """Updating a category name works."""
    await db.connect()
    try:
        cat = await repo.create_category(code="UPD_CAT_CC", name="Original Name CC")
        updated = await repo.update_category(cat["id"], name="Updated Name CC")
        assert updated["name"] == "Updated Name CC"
    finally:
        await repo.delete_category(cat["id"])
        await db.disconnect()


# ---------------------------------------------------------------------------
# Library checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_checks_includes_predefined():
    """Predefined GMP and GLP checks should be present after seed migration."""
    await db.connect()
    try:
        checks = await repo.list_checks()
        codes = {c["code"] for c in checks}
        assert "GMP-001" in codes
        assert "GMP-010" in codes
        assert "GLP-001" in codes
        assert "GLP-010" in codes
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_create_and_get_check():
    """Creating a library check and retrieving it works."""
    await db.connect()
    try:
        # Get a non-system category ID
        cats = await repo.list_categories()
        custom_cat = next(c for c in cats if c["code"] == "CUSTOM")
        check = await repo.create_check(
            category_id=custom_cat["id"],
            code="TST-CUSTOM-001",
            title="Test custom check",
            description="A check for testing",
            guidance="Follow this guidance",
            default_review_interval_days=90,
        )
        assert check["id"] is not None
        assert check["code"] == "TST-CUSTOM-001"
        assert check["default_review_interval_days"] == 90
        assert check["category"]["code"] == "CUSTOM"

        fetched = await repo.get_check(check["id"])
        assert fetched is not None
        assert fetched["title"] == "Test custom check"
    finally:
        await db.execute("DELETE FROM compliance_checks WHERE code = 'TST-CUSTOM-001'")
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_checks_filtered_by_category():
    """list_checks can be filtered by category_id."""
    await db.connect()
    try:
        cats = await repo.list_categories()
        gmp_cat = next(c for c in cats if c["code"] == "GMP")
        glp_cat = next(c for c in cats if c["code"] == "GLP")

        gmp_checks = await repo.list_checks(category_id=gmp_cat["id"])
        assert all(c["category_id"] == gmp_cat["id"] for c in gmp_checks)
        assert len(gmp_checks) >= 10

        glp_checks = await repo.list_checks(category_id=glp_cat["id"])
        assert all(c["category_id"] == glp_cat["id"] for c in glp_checks)
        assert len(glp_checks) >= 10
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_seed_is_idempotent():
    """Re-running the seed should not create duplicate checks (INSERT IGNORE on code)."""
    await db.connect()
    try:
        checks_before = await repo.list_checks()
        gmp_before = [c for c in checks_before if c["code"].startswith("GMP-")]

        # Attempt to insert the same codes again - they should be silently ignored
        cats = await repo.list_categories()
        gmp_cat = next(c for c in cats if c["code"] == "GMP")
        try:
            await db.execute(
                "INSERT IGNORE INTO compliance_checks "
                "(category_id, code, title, default_review_interval_days, is_predefined, is_active, sort_order) "
                "VALUES (%(cat)s, 'GMP-001', 'Duplicate', 365, 1, 1, 99)",
                {"cat": gmp_cat["id"]},
            )
        except Exception:
            pass  # Some DB drivers raise on duplicate even with IGNORE

        checks_after = await repo.list_checks()
        gmp_after = [c for c in checks_after if c["code"].startswith("GMP-")]
        assert len(gmp_after) == len(gmp_before)
    finally:
        await db.disconnect()


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get_assignment():
    """Creating and retrieving an assignment works."""
    await db.connect()
    try:
        company_id = await _make_company("CC Test Company 1")
        checks = await repo.list_checks(is_active=True)
        check = checks[0]

        assignment = await repo.create_assignment(
            company_id=company_id,
            check_id=check["id"],
        )
        assert assignment["id"] is not None
        assert assignment["company_id"] == company_id
        assert assignment["check_id"] == check["id"]
        assert assignment["status"] == CheckStatus.NOT_STARTED.value

        fetched = await repo.get_assignment(company_id, assignment["id"])
        assert fetched is not None
        assert fetched["id"] == assignment["id"]
    finally:
        await _cleanup_company(company_id)
        await db.disconnect()


@pytest.mark.asyncio
async def test_list_assignments():
    """list_assignments returns all non-archived assignments for a company."""
    await db.connect()
    try:
        company_id = await _make_company("CC Test Company 2")
        checks = await repo.list_checks(is_active=True)
        for check in checks[:3]:
            await repo.create_assignment(company_id=company_id, check_id=check["id"])

        assignments = await repo.list_assignments(company_id)
        assert len(assignments) == 3
    finally:
        await _cleanup_company(company_id)
        await db.disconnect()


@pytest.mark.asyncio
async def test_update_assignment_status_stamps_last_checked_and_next_review():
    """Updating status should set last_checked_at and compute next_review_at."""
    await db.connect()
    try:
        company_id = await _make_company("CC Test Company 3")
        checks = await repo.list_checks(is_active=True)
        check = checks[0]
        interval = check["default_review_interval_days"]

        assignment = await repo.create_assignment(company_id=company_id, check_id=check["id"])
        assert assignment["last_checked_at"] is None
        assert assignment["next_review_at"] is None

        before = datetime.now(timezone.utc)
        updated = await repo.update_assignment(
            company_id,
            assignment["id"],
            user_id=None,
            status=CheckStatus.COMPLIANT.value,
        )
        after = datetime.now(timezone.utc)

        assert updated["status"] == CheckStatus.COMPLIANT.value
        assert updated["last_checked_at"] is not None
        assert updated["next_review_at"] is not None

        # next_review_at should be approximately last_checked_at + interval days
        last_checked = datetime.fromisoformat(updated["last_checked_at"].replace("Z", "+00:00"))
        next_review = datetime.fromisoformat(updated["next_review_at"].replace("Z", "+00:00"))
        expected_delta = timedelta(days=interval)
        actual_delta = next_review - last_checked
        assert abs((actual_delta - expected_delta).total_seconds()) < 5
    finally:
        await _cleanup_company(company_id)
        await db.disconnect()


@pytest.mark.asyncio
async def test_overdue_and_due_soon_flags():
    """is_overdue and is_due_soon flags are computed correctly."""
    await db.connect()
    try:
        company_id = await _make_company("CC Test Company 4")
        checks = await repo.list_checks(is_active=True)
        check = checks[0]

        assignment = await repo.create_assignment(
            company_id=company_id,
            check_id=check["id"],
            review_interval_days=1,
        )

        # Manually set next_review_at to 2 days ago to force overdue
        past = datetime.now(timezone.utc) - timedelta(days=2)
        await db.execute(
            "UPDATE company_compliance_check_assignments SET next_review_at = %(dt)s WHERE id = %(id)s",
            {"dt": past, "id": assignment["id"]},
        )

        fetched = await repo.get_assignment(company_id, assignment["id"])
        assert fetched["is_overdue"] is True
        assert fetched["is_due_soon"] is False

        # Set next_review_at to 3 days from now — should be due_soon but not overdue
        future_soon = datetime.now(timezone.utc) + timedelta(days=3)
        await db.execute(
            "UPDATE company_compliance_check_assignments SET next_review_at = %(dt)s WHERE id = %(id)s",
            {"dt": future_soon, "id": assignment["id"]},
        )
        fetched2 = await repo.get_assignment(company_id, assignment["id"])
        assert fetched2["is_overdue"] is False
        assert fetched2["is_due_soon"] is True

        # Set next_review_at to 30 days from now — neither
        future_far = datetime.now(timezone.utc) + timedelta(days=30)
        await db.execute(
            "UPDATE company_compliance_check_assignments SET next_review_at = %(dt)s WHERE id = %(id)s",
            {"dt": future_far, "id": assignment["id"]},
        )
        fetched3 = await repo.get_assignment(company_id, assignment["id"])
        assert fetched3["is_overdue"] is False
        assert fetched3["is_due_soon"] is False
    finally:
        await _cleanup_company(company_id)
        await db.disconnect()


@pytest.mark.asyncio
async def test_bulk_assign_by_category():
    """bulk_assign_by_category creates assignments for all active checks in a category."""
    await db.connect()
    try:
        company_id = await _make_company("CC Test Company 5")
        cats = await repo.list_categories()
        gmp_cat = next(c for c in cats if c["code"] == "GMP")

        created = await repo.bulk_assign_by_category(company_id, gmp_cat["id"])
        assert created >= 10

        # Re-running should not create duplicates
        created2 = await repo.bulk_assign_by_category(company_id, gmp_cat["id"])
        assert created2 == 0
    finally:
        await _cleanup_company(company_id)
        await db.disconnect()


@pytest.mark.asyncio
async def test_assignment_summary():
    """get_assignment_summary returns accurate counts."""
    await db.connect()
    try:
        company_id = await _make_company("CC Test Company 6")
        checks = await repo.list_checks(is_active=True)
        a1 = await repo.create_assignment(company_id=company_id, check_id=checks[0]["id"])
        a2 = await repo.create_assignment(company_id=company_id, check_id=checks[1]["id"])

        # Update one to compliant
        await repo.update_assignment(company_id, a1["id"], user_id=None, status=CheckStatus.COMPLIANT.value)

        summary = await repo.get_assignment_summary(company_id)
        assert summary["total"] == 2
        assert summary["compliant"] == 1
        assert summary["not_started"] == 1
        assert summary["compliance_percentage"] == 50.0
    finally:
        await _cleanup_company(company_id)
        await db.disconnect()


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_and_list_evidence():
    """Evidence items can be added and listed for an assignment."""
    await db.connect()
    try:
        company_id = await _make_company("CC Test Company 7")
        checks = await repo.list_checks(is_active=True)
        assignment = await repo.create_assignment(company_id=company_id, check_id=checks[0]["id"])

        ev = await repo.add_evidence(
            assignment_id=assignment["id"],
            evidence_type="text",
            title="Test evidence",
            content="Evidence content",
        )
        assert ev["id"] is not None
        assert ev["title"] == "Test evidence"

        items = await repo.list_evidence(assignment["id"])
        assert len(items) == 1
        assert items[0]["id"] == ev["id"]
    finally:
        await _cleanup_company(company_id)
        await db.disconnect()


@pytest.mark.asyncio
async def test_delete_evidence():
    """Evidence items can be deleted."""
    await db.connect()
    try:
        company_id = await _make_company("CC Test Company 8")
        checks = await repo.list_checks(is_active=True)
        assignment = await repo.create_assignment(company_id=company_id, check_id=checks[0]["id"])

        ev = await repo.add_evidence(
            assignment_id=assignment["id"],
            evidence_type="url",
            title="Link evidence",
            content="https://example.com",
        )
        await repo.delete_evidence(assignment["id"], ev["id"])

        items = await repo.list_evidence(assignment["id"])
        assert len(items) == 0
    finally:
        await _cleanup_company(company_id)
        await db.disconnect()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_record_on_status_update():
    """An audit record is created when status is updated."""
    await db.connect()
    try:
        company_id = await _make_company("CC Test Company 9")
        checks = await repo.list_checks(is_active=True)
        assignment = await repo.create_assignment(company_id=company_id, check_id=checks[0]["id"])

        await repo.update_assignment(
            company_id, assignment["id"], user_id=None, status=CheckStatus.IN_PROGRESS.value
        )
        await repo.update_assignment(
            company_id, assignment["id"], user_id=None, status=CheckStatus.COMPLIANT.value
        )

        audit = await repo.list_audit(assignment["id"])
        assert len(audit) == 2
        actions = [e["action"] for e in audit]
        assert all(a == "status_update" for a in actions)
    finally:
        await _cleanup_company(company_id)
        await db.disconnect()


@pytest.mark.asyncio
async def test_manual_audit_append():
    """append_audit writes a record that list_audit returns."""
    await db.connect()
    try:
        company_id = await _make_company("CC Test Company 10")
        checks = await repo.list_checks(is_active=True)
        assignment = await repo.create_assignment(company_id=company_id, check_id=checks[0]["id"])

        await repo.append_audit(
            assignment_id=assignment["id"],
            company_id=company_id,
            user_id=None,
            action="manual_note",
            change_summary="Manual note added",
        )

        audit = await repo.list_audit(assignment["id"])
        assert len(audit) == 1
        assert audit[0]["action"] == "manual_note"
        assert audit[0]["change_summary"] == "Manual note added"
    finally:
        await _cleanup_company(company_id)
        await db.disconnect()


# ---------------------------------------------------------------------------
# Regression tests for assignment bug fixes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_assignment_returns_full_record():
    """create_assignment must return the full assignment dict (regression: used db.execute
    instead of execute_returning_lastrowid, causing a RuntimeError on lookup)."""
    await db.connect()
    try:
        company_id = await db.execute_returning_lastrowid(
            "INSERT INTO companies (name) VALUES (%(name)s)", {"name": "Regression Co 1"}
        )
        checks = await repo.list_checks(is_active=True)
        assignment = await repo.create_assignment(
            company_id=company_id, check_id=checks[0]["id"]
        )
        assert assignment is not None
        assert assignment["id"] is not None
        assert assignment["company_id"] == company_id
        assert assignment["check_id"] == checks[0]["id"]
    finally:
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
        await db.disconnect()


@pytest.mark.asyncio
async def test_create_assignment_archived_check_unarchives():
    """Re-assigning an archived check should unarchive it instead of returning 409.
    (Regression: get_assignment_by_check did not filter archived=0, causing the API
    to return 409 Conflict for any previously archived check.)"""
    await db.connect()
    try:
        company_id = await db.execute_returning_lastrowid(
            "INSERT INTO companies (name) VALUES (%(name)s)", {"name": "Regression Co 2"}
        )
        checks = await repo.list_checks(is_active=True)
        check_id = checks[0]["id"]

        # Create and then archive the assignment
        first = await repo.create_assignment(company_id=company_id, check_id=check_id)
        await repo.update_assignment(company_id, first["id"], user_id=None, archived=True)

        archived = await repo.get_assignment(company_id, first["id"])
        assert archived is not None
        assert archived.get("archived") in (True, 1)

        # get_assignment_by_check should NOT return the archived assignment
        active_only = await repo.get_assignment_by_check(company_id, check_id)
        assert active_only is None

        # Re-assigning should succeed (unarchive) rather than raise 409
        reactivated = await repo.create_assignment(
            company_id=company_id, check_id=check_id, status=CheckStatus.IN_PROGRESS
        )
        assert reactivated is not None
        assert reactivated["id"] == first["id"]
        assert reactivated.get("archived") in (False, 0)
        assert reactivated["status"] == CheckStatus.IN_PROGRESS.value
    finally:
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
        await db.disconnect()


@pytest.mark.asyncio
async def test_bulk_assign_skips_archived():
    """bulk_assign_by_category should skip checks that are already assigned (archived or not)."""
    await db.connect()
    try:
        company_id = await db.execute_returning_lastrowid(
            "INSERT INTO companies (name) VALUES (%(name)s)", {"name": "Regression Co 3"}
        )
        cats = await repo.list_categories()
        gmp_cat = next(c for c in cats if c["code"] == "GMP")

        # First bulk assign
        created = await repo.bulk_assign_by_category(company_id, gmp_cat["id"])
        assert created > 0

        # Archive one assignment
        assignments = await repo.list_assignments(company_id, category_id=gmp_cat["id"])
        await repo.update_assignment(company_id, assignments[0]["id"], user_id=None, archived=True)

        # Re-running bulk assign should still create 0 (archived checks are skipped)
        created2 = await repo.bulk_assign_by_category(company_id, gmp_cat["id"])
        assert created2 == 0
    finally:
        await db.execute("DELETE FROM companies WHERE id = %(id)s", {"id": company_id})
        await db.disconnect()
