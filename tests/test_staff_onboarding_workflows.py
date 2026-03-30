from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import staff_onboarding_workflows as workflows


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_run_workflow_ignores_waiting_external_within_timeout(monkeypatch):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    monkeypatch.setattr(
        workflows.staff_repo,
        "get_staff_by_id",
        AsyncMock(
            return_value={
                "id": 99,
                "company_id": 7,
                "first_name": "Sam",
                "last_name": "Case",
                "email": "sam@example.com",
                "onboarding_status": workflows.STATE_WAITING_EXTERNAL,
                "updated_at": (now - timedelta(hours=2)).isoformat(),
                "requested_at": (now - timedelta(hours=2)).isoformat(),
                "created_at": (now - timedelta(hours=4)).isoformat(),
            }
        ),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(
            return_value={
                "is_enabled": True,
                "workflow_key": workflows.workflow_repo.DEFAULT_WORKFLOW_KEY,
                "max_retries": 0,
                "config": {
                    "waiting_external_timeout_hours": 24,
                },
            }
        ),
    )
    monkeypatch.setattr(workflows, "_create_failure_ticket", AsyncMock(return_value=321))

    result = await workflows.run_staff_onboarding_workflow(
        company_id=7,
        staff_id=99,
        initiated_by_user_id=5,
    )

    assert result["state"] == "ignored"
    assert result["reason"] == "within_timeout_window"
    workflows._create_failure_ticket.assert_not_awaited()


@pytest.mark.anyio
async def test_run_workflow_escalates_stale_awaiting_approval(monkeypatch):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_iso = (now - timedelta(hours=80)).isoformat()
    monkeypatch.setattr(
        workflows.staff_repo,
        "get_staff_by_id",
        AsyncMock(
            return_value={
                "id": 100,
                "company_id": 7,
                "first_name": "Alex",
                "last_name": "Jones",
                "email": "alex@example.com",
                "onboarding_status": workflows.STATE_AWAITING_APPROVAL,
                "updated_at": stale_iso,
                "requested_at": stale_iso,
                "created_at": stale_iso,
            }
        ),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(
            return_value={
                "is_enabled": True,
                "workflow_key": workflows.workflow_repo.DEFAULT_WORKFLOW_KEY,
                "max_retries": 1,
                "config": {
                    "awaiting_approval_timeout_hours": 48,
                },
            }
        ),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_execution_by_staff_id",
        AsyncMock(return_value={"id": 45, "helpdesk_ticket_id": None}),
    )
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", AsyncMock())
    monkeypatch.setattr(workflows.audit_service, "log_action", AsyncMock())
    monkeypatch.setattr(workflows, "_create_failure_ticket", AsyncMock(return_value=7001))

    result = await workflows.run_staff_onboarding_workflow(
        company_id=7,
        staff_id=100,
        initiated_by_user_id=6,
    )

    assert result["state"] == "escalated"
    assert result["reason"] == "stale_non_actionable_state"
    assert result["helpdesk_ticket_id"] == 7001
    workflows._create_failure_ticket.assert_awaited_once()
    workflows.workflow_repo.update_execution_state.assert_awaited_once()


@pytest.mark.anyio
async def test_run_workflow_allows_provisioning_state(monkeypatch):
    staff_record = {
        "id": 120,
        "company_id": 7,
        "first_name": "Taylor",
        "last_name": "Ng",
        "email": "taylor@example.com",
        "mobile_phone": None,
        "date_onboarded": None,
        "date_offboarded": None,
        "enabled": True,
        "is_ex_staff": False,
        "street": None,
        "city": None,
        "state": None,
        "postcode": None,
        "country": None,
        "department": None,
        "job_title": None,
        "org_company": None,
        "manager_name": None,
        "account_action": None,
        "syncro_contact_id": None,
        "onboarding_status": workflows.STATE_PROVISIONING,
    }
    monkeypatch.setattr(workflows.staff_repo, "get_staff_by_id", AsyncMock(return_value=staff_record))
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(
            return_value={
                "is_enabled": True,
                "workflow_key": workflows.workflow_repo.DEFAULT_WORKFLOW_KEY,
                "max_retries": 0,
                "config": {},
            }
        ),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "create_or_reset_execution",
        AsyncMock(return_value={"id": 11}),
    )
    monkeypatch.setattr(
        workflows,
        "resume_staff_onboarding_workflow_after_external_confirmation",
        AsyncMock(return_value={"state": workflows.STATE_COMPLETED, "execution_id": 11}),
    )

    result = await workflows.run_staff_onboarding_workflow(
        company_id=7,
        staff_id=120,
        initiated_by_user_id=None,
    )

    assert result["state"] == workflows.STATE_COMPLETED
    workflows.workflow_repo.create_or_reset_execution.assert_awaited_once()


@pytest.mark.anyio
async def test_run_workflow_allows_offboarding_approved_state(monkeypatch):
    staff_record = {
        "id": 121,
        "company_id": 7,
        "first_name": "Jamie",
        "last_name": "Wong",
        "email": "jamie@example.com",
        "mobile_phone": None,
        "date_onboarded": None,
        "date_offboarded": None,
        "enabled": False,
        "is_ex_staff": True,
        "street": None,
        "city": None,
        "state": None,
        "postcode": None,
        "country": None,
        "department": None,
        "job_title": None,
        "org_company": None,
        "manager_name": None,
        "account_action": "Offboard Approved",
        "syncro_contact_id": None,
        "onboarding_status": workflows.STATE_OFFBOARDING_APPROVED,
    }
    monkeypatch.setattr(workflows.staff_repo, "get_staff_by_id", AsyncMock(return_value=staff_record))
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(
            return_value={
                "is_enabled": True,
                "workflow_key": workflows.workflow_repo.DEFAULT_WORKFLOW_KEY,
                "max_retries": 0,
                "config": {},
            }
        ),
    )
    create_execution_mock = AsyncMock(return_value={"id": 12})
    monkeypatch.setattr(workflows.workflow_repo, "create_or_reset_execution", create_execution_mock)
    monkeypatch.setattr(
        workflows,
        "resume_staff_onboarding_workflow_after_external_confirmation",
        AsyncMock(return_value={"state": workflows.STATE_OFFBOARDING_COMPLETED, "execution_id": 12}),
    )

    result = await workflows.run_staff_onboarding_workflow(
        company_id=7,
        staff_id=121,
        initiated_by_user_id=None,
        direction=workflows.DIRECTION_OFFBOARDING,
    )

    assert result["state"] == workflows.STATE_OFFBOARDING_COMPLETED
    assert create_execution_mock.await_args.kwargs["direction"] == workflows.DIRECTION_OFFBOARDING


@pytest.mark.anyio
async def test_resume_offboarding_marks_staff_disabled_only_after_success(monkeypatch):
    staff_record = {
        "id": 501,
        "company_id": 8,
        "first_name": "Remy",
        "last_name": "Park",
        "email": "remy@example.com",
        "enabled": True,
        "is_ex_staff": False,
        "onboarding_status": workflows.STATE_OFFBOARDING_APPROVED,
    }
    monkeypatch.setattr(workflows.staff_repo, "get_staff_by_id", AsyncMock(return_value=staff_record))
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(return_value={"workflow_key": "staff_onboarding_m365", "max_retries": 0, "config": {}}),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_execution_by_staff_id",
        AsyncMock(return_value={"id": 71, "direction": workflows.DIRECTION_OFFBOARDING}),
    )
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", AsyncMock())
    update_mock = AsyncMock(return_value=staff_record)
    monkeypatch.setattr(workflows.staff_repo, "update_staff", update_mock)
    monkeypatch.setattr(workflows, "_attempt_step", AsyncMock(return_value={"offboarded": True}))
    monkeypatch.setattr(workflows.audit_service, "log_action", AsyncMock())

    result = await workflows.resume_staff_onboarding_workflow_after_external_confirmation(
        company_id=8,
        staff_id=501,
        execution_id=71,
        initiated_by_user_id=22,
    )

    assert result["state"] == workflows.STATE_OFFBOARDING_COMPLETED
    final_kwargs = update_mock.await_args_list[-1].kwargs
    assert final_kwargs["enabled"] is False
    assert final_kwargs["is_ex_staff"] is True
    assert final_kwargs["date_offboarded"] is not None


@pytest.mark.anyio
async def test_resume_offboarding_failure_creates_ticket_with_step_payload(monkeypatch):
    staff_record = {
        "id": 502,
        "company_id": 8,
        "first_name": "Kai",
        "last_name": "Lee",
        "email": "kai@example.com",
        "enabled": True,
        "is_ex_staff": False,
        "onboarding_status": workflows.STATE_OFFBOARDING_APPROVED,
    }
    monkeypatch.setattr(workflows.staff_repo, "get_staff_by_id", AsyncMock(return_value=staff_record))
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(return_value={"workflow_key": "staff_onboarding_m365", "max_retries": 0, "config": {}}),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_execution_by_staff_id",
        AsyncMock(return_value={"id": 72, "direction": workflows.DIRECTION_OFFBOARDING}),
    )
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", AsyncMock())
    monkeypatch.setattr(workflows.staff_repo, "update_staff", AsyncMock(return_value=staff_record))
    monkeypatch.setattr(
        workflows,
        "_attempt_step",
        AsyncMock(
            side_effect=workflows.WorkflowStepError(
                "group removal failed",
                step_name="remove_groups",
                request_payload={"group_id": "abc"},
            )
        ),
    )
    ticket_mock = AsyncMock(return_value=9911)
    monkeypatch.setattr(workflows, "_create_failure_ticket", ticket_mock)
    monkeypatch.setattr(workflows.audit_service, "log_action", AsyncMock())

    result = await workflows.resume_staff_onboarding_workflow_after_external_confirmation(
        company_id=8,
        staff_id=502,
        execution_id=72,
        initiated_by_user_id=22,
    )

    assert result["state"] == workflows.STATE_OFFBOARDING_FAILED
    context = ticket_mock.await_args.kwargs["error_context"]
    assert context["step"] == "remove_groups"
    assert context["payload"] == {"group_id": "abc"}
