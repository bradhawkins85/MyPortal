from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest

from app.repositories import essential8 as essential8_repo
from app.schemas.essential8 import ApprovalStatus, ComplianceStatus


@pytest.mark.anyio("asyncio")
async def test_bulk_update_requirement_compliance_updates_and_audits(monkeypatch):
    monkeypatch.setattr(
        essential8_repo,
        "get_essential8_requirement",
        AsyncMock(side_effect=[
            {"id": 11, "control_id": 7},
            {"id": 12, "control_id": 7},
        ]),
    )
    monkeypatch.setattr(
        essential8_repo,
        "get_company_requirement_compliance",
        AsyncMock(side_effect=[
            {"requirement_id": 11, "status": "in_progress"},
            None,
        ]),
    )
    monkeypatch.setattr(
        essential8_repo,
        "update_company_requirement_compliance",
        AsyncMock(return_value={"requirement_id": 11, "status": "compliant", "approval_status": "approved"}),
    )
    monkeypatch.setattr(
        essential8_repo,
        "create_company_requirement_compliance",
        AsyncMock(return_value={"requirement_id": 12, "status": "compliant", "approval_status": "approved"}),
    )
    append_audit = AsyncMock()
    monkeypatch.setattr(essential8_repo, "append_requirement_audit", append_audit)
    auto_update = AsyncMock()
    monkeypatch.setattr(essential8_repo, "auto_update_control_compliance_from_requirements", auto_update)

    result = await essential8_repo.bulk_update_company_requirement_compliance(
        company_ids=[3],
        requirement_ids=[11, 12],
        status=ComplianceStatus.COMPLIANT,
        approval_status=ApprovalStatus.APPROVED,
        user_id=99,
    )

    assert result == {
        "updated_count": 2,
        "audit_count": 2,
        "company_count": 1,
        "requirement_count": 2,
    }
    assert append_audit.await_count == 2
    auto_update.assert_awaited_once_with(company_id=3, control_id=7)


@pytest.mark.anyio("asyncio")
async def test_requirement_reminder_summary_counts_due_and_overdue(monkeypatch):
    today = date.today()
    monkeypatch.setattr(
        essential8_repo,
        "list_company_requirement_compliance",
        AsyncMock(return_value=[
            {
                "approval_status": "pending_approval",
                "target_compliance_date": (today + timedelta(days=2)).isoformat(),
                "reminder_days_before": 7,
                "overdue_alert_enabled": True,
            },
            {
                "approval_status": "draft",
                "target_compliance_date": (today - timedelta(days=1)).isoformat(),
                "reminder_days_before": 7,
                "overdue_alert_enabled": True,
            },
        ]),
    )

    summary = await essential8_repo.get_requirement_reminder_summary(5, as_of=today)

    assert summary["pending_approval_count"] == 1
    assert summary["reminder_due_count"] == 1
    assert summary["overdue_count"] == 1


@pytest.mark.anyio("asyncio")
async def test_export_bundle_includes_default_status_for_untracked_requirements(monkeypatch):
    monkeypatch.setattr(
        essential8_repo,
        "list_essential8_requirements",
        AsyncMock(return_value=[
            {"id": 11, "control_id": 1, "maturity_level": "ml1", "requirement_order": 1, "description": "Tracked"},
            {"id": 12, "control_id": 1, "maturity_level": "ml1", "requirement_order": 2, "description": "Untouched"},
        ]),
    )
    monkeypatch.setattr(
        essential8_repo,
        "list_company_requirement_compliance",
        AsyncMock(return_value=[
            {
                "requirement_id": 11,
                "status": "in_progress",
                "approval_status": "pending_approval",
                "target_compliance_date": "2026-01-02",
                "notes": "Working",
                "owner_user_id": 44,
            }
        ]),
    )
    monkeypatch.setattr(
        essential8_repo,
        "list_requirement_evidence",
        AsyncMock(side_effect=[
            [{"version_number": 1, "title": "Tracked evidence", "file_name": "tracked.pdf", "file_path": "compliance/essential8/tracked.pdf"}],
            [],
        ]),
    )

    bundle = await essential8_repo.build_requirement_export_bundle(8, control_id=1)

    assert len(bundle["requirements"]) == 2
    tracked = bundle["requirements"][0]
    untouched = bundle["requirements"][1]
    assert tracked["evidence_reference_count"] == 1
    assert untouched["status"] == ComplianceStatus.NOT_STARTED.value
    assert untouched["approval_status"] == ApprovalStatus.DRAFT.value
