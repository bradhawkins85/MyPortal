"""Regression tests: BCP child objects must belong to the active company's plan.

Every BCP route that mutates a plan child object by id must return 404 (and
must not touch the repository mutator) when the object belongs to another
company's plan.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status

from app.api.routes import bcp

OWN_PLAN_ID = 9
FOREIGN_PLAN_ID = 99
COMPANY_ID = 101


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# (handler name, getter, mutator, kwargs)
CASES = [
    ("update_contact_page", "get_contact_by_id", "update_contact",
     dict(contact_id=5, kind="Internal", person_or_org="x", phones=None, email=None,
          responsibility_or_agency=None)),
    ("delete_contact_page", "get_contact_by_id", "delete_contact", dict(contact_id=5)),
    ("update_training_item_endpoint", "get_training_item_by_id", "update_training_item",
     dict(training_id=5, training_date="2026-01-01", training_type=None, training_status="Scheduled",
          participants_count=None, score_percent=None, comments=None, lessons_learned=None,
          follow_up_actions=None)),
    ("delete_training_item_endpoint", "get_training_item_by_id", "delete_training_item",
     dict(training_id=5)),
    ("update_review_item_endpoint", "get_review_item_by_id", "update_review_item",
     dict(review_id=5, review_date="2026-01-01", version_label=None, review_approval_status="Draft",
          reviewed_by_user_id=None, approved_by_user_id=None, reason=None, changes_made=None,
          approval_snapshot=None)),
    ("delete_review_item_endpoint", "get_review_item_by_id", "delete_review_item",
     dict(review_id=5)),
    ("assign_user_to_role", "get_role_by_id", "create_role_assignment",
     dict(role_id=5, user_id=1, collaborator_role="Executor", is_alternate=False, contact_info=None)),
    ("delete_objective", "get_objective_by_id", "delete_objective", dict(objective_id=5)),
    ("update_risk", "get_risk_by_id", "update_risk",
     dict(risk_id=5, description="d", likelihood=1, impact=1, preventative_actions=None,
          contingency_plans=None)),
    ("delete_risk", "get_risk_by_id", "delete_risk", dict(risk_id=5)),
    ("delete_distribution_entry", "get_distribution_entry_by_id", "delete_distribution_entry",
     dict(entry_id=5)),
    ("update_insurance_policy", "get_insurance_policy_by_id", "update_insurance_policy",
     dict(policy_id=5, policy_type="t", coverage=None, exclusions=None, insurer=None, contact=None,
          last_review_date=None, payment_terms=None)),
    ("delete_insurance_policy", "get_insurance_policy_by_id", "delete_insurance_policy",
     dict(policy_id=5)),
    ("update_backup_item", "get_backup_item_by_id", "update_backup_item",
     dict(backup_id=5, data_scope="s", frequency=None, medium=None, owner=None, steps=None)),
    ("delete_backup_item", "get_backup_item_by_id", "delete_backup_item", dict(backup_id=5)),
    ("update_critical_activity_endpoint", "get_critical_activity_by_id", "update_critical_activity",
     dict(activity_id=5, name="n", description=None, priority=None, supplier_dependency=None,
          importance=None, notes=None, losses_financial=None, losses_increased_costs=None,
          losses_staffing=None, losses_product_service=None, losses_reputation=None, fines=None,
          legal_liability=None, rto_hours=None, losses_comments=None)),
    ("delete_critical_activity_endpoint", "get_critical_activity_by_id", "delete_critical_activity",
     dict(activity_id=5)),
    ("create_dependency_mapping_endpoint", "get_critical_activity_by_id", "create_dependency_mapping",
     dict(critical_activity_id=5, dependency_type="Vendor", dependency_name="n", owner_name=None,
          rto_hours=None, notes=None)),
    ("delete_dependency_mapping_endpoint", "get_dependency_mapping_by_id", "delete_dependency_mapping",
     dict(dependency_id=5)),
    ("toggle_checklist_item", "get_checklist_tick_by_id", "toggle_checklist_tick", dict(tick_id=5)),
    ("update_contact_endpoint", "get_contact_by_id", "update_contact",
     dict(contact_id=5, kind="Internal", person_or_org="x", phones=None, email=None,
          responsibility_or_agency=None)),
    ("delete_contact_endpoint", "get_contact_by_id", "delete_contact", dict(contact_id=5)),
    ("update_emergency_kit_item_endpoint", "get_emergency_kit_item_by_id", "update_emergency_kit_item",
     dict(item_id=5, category="Document", name="n", notes=None)),
    ("mark_emergency_kit_item_checked_endpoint", "get_emergency_kit_item_by_id",
     "mark_emergency_kit_item_checked", dict(item_id=5)),
    ("delete_emergency_kit_item_endpoint", "get_emergency_kit_item_by_id", "delete_emergency_kit_item",
     dict(item_id=5)),
    ("mark_recovery_action_complete_endpoint", "get_recovery_action_by_id",
     "mark_recovery_action_complete", dict(action_id=5)),
    ("delete_recovery_action_endpoint", "get_recovery_action_by_id", "delete_recovery_action",
     dict(action_id=5)),
    ("update_recovery_contact_endpoint", "get_recovery_contact_by_id", "update_recovery_contact",
     dict(contact_id=5, org_name="o", contact_name=None, title=None, phone=None)),
    ("delete_recovery_contact_endpoint", "get_recovery_contact_by_id", "delete_recovery_contact",
     dict(contact_id=5)),
    ("update_insurance_claim_endpoint", "get_insurance_claim_by_id", "update_insurance_claim",
     dict(claim_id=5, insurer="i", claim_date=None, details=None, follow_up_actions=None)),
    ("delete_insurance_claim_endpoint", "get_insurance_claim_by_id", "delete_insurance_claim",
     dict(claim_id=5)),
    ("update_market_change_endpoint", "get_market_change_by_id", "update_market_change",
     dict(change_id=5, change="c", impact=None, options=None)),
    ("delete_market_change_endpoint", "get_market_change_by_id", "delete_market_change",
     dict(change_id=5)),
]


def _patch_auth(monkeypatch):
    auth = AsyncMock(return_value=({"id": 7, "name": "Editor"}, COMPANY_ID))
    monkeypatch.setattr(bcp, "_require_bcp_edit", auth)
    monkeypatch.setattr(bcp, "_require_bcp_incident_run", auth)
    monkeypatch.setattr(
        bcp.bcp_repo,
        "get_plan_by_company",
        AsyncMock(return_value={"id": OWN_PLAN_ID, "company_id": COMPANY_ID}),
    )


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("handler,getter,mutator,kwargs", CASES, ids=[c[0] for c in CASES])
async def test_foreign_plan_object_returns_404_without_mutation(
    monkeypatch, handler, getter, mutator, kwargs
):
    _patch_auth(monkeypatch)
    foreign = {
        "id": 5,
        "plan_id": FOREIGN_PLAN_ID,
        "category": "Document",
        "is_done": False,
    }
    monkeypatch.setattr(bcp.bcp_repo, getter, AsyncMock(return_value=foreign))
    mutate = AsyncMock(return_value={"id": 5})
    monkeypatch.setattr(bcp.bcp_repo, mutator, mutate)

    with pytest.raises(HTTPException) as exc_info:
        await getattr(bcp, handler)(MagicMock(), **kwargs)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    mutate.assert_not_awaited()


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("handler", ["update_role_assignment_endpoint", "delete_role_assignment_endpoint"])
async def test_foreign_role_assignment_returns_404(monkeypatch, handler):
    _patch_auth(monkeypatch)
    monkeypatch.setattr(
        bcp.bcp_repo,
        "get_role_assignment_by_id",
        AsyncMock(return_value={"id": 5, "role_id": 3}),
    )
    monkeypatch.setattr(
        bcp.bcp_repo, "get_role_by_id", AsyncMock(return_value={"id": 3, "plan_id": FOREIGN_PLAN_ID})
    )
    update = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(bcp.bcp_repo, "update_role_assignment", update)
    monkeypatch.setattr(bcp.bcp_repo, "delete_role_assignment", delete)

    kwargs = {"assignment_id": 5}
    if handler.startswith("update"):
        kwargs.update(user_id=1, collaborator_role="Executor", is_alternate=False, contact_info=None)
    with pytest.raises(HTTPException) as exc_info:
        await getattr(bcp, handler)(MagicMock(), **kwargs)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Assignment not found"
    update.assert_not_awaited()
    delete.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_own_plan_object_is_mutated(monkeypatch):
    _patch_auth(monkeypatch)
    monkeypatch.setattr(
        bcp.bcp_repo, "get_contact_by_id", AsyncMock(return_value={"id": 5, "plan_id": OWN_PLAN_ID})
    )
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(bcp.bcp_repo, "delete_contact", delete)

    response = await bcp.delete_contact_page(MagicMock(), contact_id=5)

    delete.assert_awaited_once_with(5)
    assert response.status_code == status.HTTP_303_SEE_OTHER
