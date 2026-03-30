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
