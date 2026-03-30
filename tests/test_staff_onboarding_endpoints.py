from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_staff_onboarding_routes_include_new_paths():
    from app.api.routes import staff

    paths = {route.path for route in staff.router.routes}
    assert "/api/staff/{staff_id}/onboarding/approve" in paths
    assert "/api/staff/{staff_id}/onboarding/deny" in paths
    assert "/api/staff/{staff_id}/offboarding/approve" in paths
    assert "/api/staff/{staff_id}/offboarding/deny" in paths
    assert "/api/staff/{staff_id}/onboarding/external-confirm" in paths


@pytest.mark.anyio
async def test_external_confirm_replays_idempotent_response(monkeypatch):
    from app.api.routes import staff
    from app.schemas.staff import StaffExternalCheckpointCallback

    payload = StaffExternalCheckpointCallback(
        companyId=11,
        staffId=44,
        confirmationToken="a" * 24,
        source="hris",
    )
    monkeypatch.setattr(
        staff.staff_workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(return_value={"config": {}}),
    )
    monkeypatch.setattr(
        staff.staff_workflow_repo,
        "try_create_external_confirmation_idempotency",
        AsyncMock(return_value=False),
    )
    fingerprint = staff._external_confirmation_fingerprint(path_staff_id=44, api_key_id=7, payload=payload)
    monkeypatch.setattr(
        staff.staff_workflow_repo,
        "get_external_confirmation_idempotency",
        AsyncMock(
            return_value={
                "request_fingerprint": fingerprint,
                "response_status": 202,
                "response_payload": {
                    "state": "provisioning",
                    "executionId": 55,
                    "staffId": 44,
                    "companyId": 11,
                },
            }
        ),
    )
    workflow_mock = AsyncMock()
    monkeypatch.setattr(
        staff.staff_onboarding_workflow_service,
        "confirm_external_checkpoint_and_resume",
        workflow_mock,
    )

    result = await staff._confirm_external_checkpoint(
        staff_id=44,
        payload=payload,
        idempotency_key="idem-12345678",
        api_key_record={"id": 7},
        _=None,
    )

    assert result.execution_id == 55
    assert result.state == "provisioning"
    workflow_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_external_confirm_rejects_path_body_staff_mismatch(monkeypatch):
    from app.api.routes import staff
    from app.schemas.staff import StaffExternalCheckpointCallback

    payload = StaffExternalCheckpointCallback(
        companyId=11,
        staffId=99,
        confirmationToken="a" * 24,
        source="hris",
    )
    monkeypatch.setattr(
        staff.staff_workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(return_value={"config": {}}),
    )

    with pytest.raises(HTTPException) as exc:
        await staff._confirm_external_checkpoint(
            staff_id=44,
            payload=payload,
            idempotency_key="idem-12345678",
            api_key_record={"id": 7},
            _=None,
        )
    assert exc.value.status_code == 400
    assert "mismatch" in str(exc.value.detail).lower()


@pytest.mark.anyio
async def test_offboarding_approve_persists_decision_and_audits(monkeypatch):
    from app.api.routes import staff
    from app.schemas.staff import StaffApprovalDecision

    monkeypatch.setattr(
        staff.staff_repo,
        "get_staff_by_id",
        AsyncMock(
            return_value={
                "id": 22,
                "company_id": 4,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "enabled": True,
                "is_ex_staff": False,
                "account_action": "Offboard Requested",
                "onboarding_complete": False,
            }
        ),
    )
    update_mock = AsyncMock(
        return_value={
            "id": 22,
            "company_id": 4,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "enabled": False,
            "is_ex_staff": True,
            "account_action": "Offboard Approved",
            "approval_status": "approved",
            "approved_by_user_id": 99,
            "approval_notes": "HR validated",
            "custom_fields": {},
        }
    )
    monkeypatch.setattr(staff.staff_repo, "update_staff", update_mock)
    workflow_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(staff.staff_onboarding_workflow_service, "get_staff_workflow_status", workflow_mock)
    enqueue_mock = AsyncMock()
    monkeypatch.setattr(staff.staff_onboarding_workflow_service, "enqueue_staff_onboarding_workflow", enqueue_mock)
    audit_mock = AsyncMock()
    monkeypatch.setattr(staff.audit_service, "log_action", audit_mock)

    result = await staff.approve_staff_offboarding(
        staff_id=22,
        payload=StaffApprovalDecision(comment="HR validated"),
        _=None,
        current_user={"id": 99, "is_super_admin": True},
    )

    assert result.approval_status == "approved"
    assert result.approved_by_user_id == 99
    assert result.approval_notes == "HR validated"
    assert result.account_action == "Offboard Approved"
    kwargs = update_mock.await_args.kwargs
    assert kwargs["approved_by_user_id"] == 99
    assert kwargs["approved_at"] is not None
    assert kwargs["approval_notes"] == "HR validated"
    assert kwargs["enabled"] is False
    assert kwargs["is_ex_staff"] is True
    assert kwargs["onboarding_status"] == staff.staff_onboarding_workflow_service.STATE_OFFBOARDING_APPROVED
    audit_mock.assert_awaited_once()
    assert audit_mock.await_args.kwargs["action"] == "staff.offboarding.approved"
    enqueue_mock.assert_awaited_once()
    assert enqueue_mock.await_args.kwargs["direction"] == staff.staff_onboarding_workflow_service.DIRECTION_OFFBOARDING


@pytest.mark.anyio
async def test_offboarding_deny_requires_reason(monkeypatch):
    from app.api.routes import staff
    from app.schemas.staff import StaffApprovalDecision

    monkeypatch.setattr(
        staff.staff_repo,
        "get_staff_by_id",
        AsyncMock(
            return_value={
                "id": 22,
                "company_id": 4,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "enabled": True,
                "is_ex_staff": False,
                "account_action": "Offboard Requested",
                "onboarding_complete": False,
            }
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await staff.deny_staff_offboarding(
            staff_id=22,
            payload=StaffApprovalDecision(comment=None, reason=None),
            _=None,
            current_user={"id": 99, "is_super_admin": True},
        )

    assert exc.value.status_code == 400
    assert "reason" in str(exc.value.detail).lower()
