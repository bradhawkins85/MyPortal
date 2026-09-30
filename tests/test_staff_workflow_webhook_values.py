from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import staff as staff_routes
from app.security.encryption import decrypt_secret
from app.services import staff_onboarding_workflows as workflows


POST_KEY = "secret-post-key-abcdefghijklmnop"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _checkpoint(**overrides):
    record = {
        "id": 12,
        "company_id": 9,
        "staff_id": 1001,
        "execution_id": 88,
        "webhook_post_key_hash": workflows.hash_api_key(POST_KEY),
        "created_at": datetime(2026, 9, 30, 1, 0, 0),
        "execution_direction": "onboarding",
        "execution_workflow_key": "staff_onboarding_m365",
        "execution_state": workflows.STATE_WAITING_EXTERNAL,
        "execution_current_step": "1:Create AD account",
        "execution_requested_at": datetime(2026, 9, 29, 23, 0, 0),
    }
    record.update(overrides)
    return record


@pytest.fixture
def confirm_mocks(monkeypatch):
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_pending_external_checkpoint_by_webhook_id",
        AsyncMock(return_value=_checkpoint()),
    )
    confirm = AsyncMock()
    monkeypatch.setattr(workflows.workflow_repo, "confirm_external_checkpoint", confirm)
    monkeypatch.setattr(workflows.audit_service, "log_action", AsyncMock())
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_execution_by_id",
        AsyncMock(return_value={"id": 88, "current_step": "1:Create AD account"}),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={88: []}),
    )
    append_log = AsyncMock()
    monkeypatch.setattr(workflows.workflow_repo, "append_step_log", append_log)
    resume = AsyncMock(return_value={"state": workflows.STATE_COMPLETED, "execution_id": 88})
    monkeypatch.setattr(
        workflows, "resume_staff_onboarding_workflow_after_external_confirmation", resume
    )
    return {"confirm": confirm, "append_log": append_log, "resume": resume}


@pytest.mark.anyio
async def test_webhook_values_are_stored_with_secrets_encrypted(confirm_mocks):
    result = await workflows.confirm_webhook_checkpoint_and_resume(
        webhook_public_id="public-id",
        post_key=POST_KEY,
        source="dc-script",
        callback_payload={"host": "dc01"},
        staff_id=1001,
        values={"samAccountName": "jstarter", "ad_password": "Plain!Pass1"},
        secret_values={"initial_secret": "S3cret!"},
    )

    assert result["state"] == workflows.STATE_COMPLETED
    stored = confirm_mocks["confirm"].await_args.kwargs["callback_payload"]
    assert stored["payload"] == {"host": "dc01"}
    assert stored["outcome"] == "success"
    assert stored["values"] == {"samAccountName": "jstarter"}
    encrypted = stored["secret_values_encrypted"]
    assert set(encrypted) == {"ad_password", "initial_secret"}
    assert "Plain!Pass1" not in json.dumps(stored)
    assert decrypt_secret(encrypted["ad_password"]) == "Plain!Pass1"
    assert decrypt_secret(encrypted["initial_secret"]) == "S3cret!"

    log_kwargs = confirm_mocks["append_log"].await_args.kwargs
    assert log_kwargs["status"] == "success"
    assert log_kwargs["step_name"] == "Create AD account"
    assert log_kwargs["response_payload"]["values"] == {
        "samAccountName": "jstarter",
        "ad_password": "***redacted***",
        "initial_secret": "***redacted***",
    }
    assert confirm_mocks["resume"].await_args.kwargs["forced_failure"] is None


@pytest.mark.anyio
async def test_webhook_failed_outcome_fails_workflow(confirm_mocks):
    await workflows.confirm_webhook_checkpoint_and_resume(
        webhook_public_id="public-id",
        post_key=POST_KEY,
        source="dc-script",
        callback_payload=None,
        staff_id=1001,
        outcome="failed",
        error_message="Username already exists",
    )

    log_kwargs = confirm_mocks["append_log"].await_args.kwargs
    assert log_kwargs["status"] == "failed"
    assert log_kwargs["error_message"] == "Username already exists"
    failure = confirm_mocks["resume"].await_args.kwargs["forced_failure"]
    assert isinstance(failure, workflows.WorkflowStepError)
    assert str(failure) == "Username already exists"
    assert failure.step_name == "Create AD account"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "values",
    [{"staff.email": "x"}, {"company_id": 1}, {"bad name": 1}, {"custom_fields.a": 1}],
)
async def test_webhook_rejects_reserved_or_invalid_value_names(confirm_mocks, values):
    with pytest.raises(ValueError):
        await workflows.confirm_webhook_checkpoint_and_resume(
            webhook_public_id="public-id",
            post_key=POST_KEY,
            source="dc-script",
            callback_payload=None,
            values=values,
        )
    confirm_mocks["confirm"].assert_not_awaited()


@pytest.mark.anyio
async def test_webhook_rejects_wrong_post_key(confirm_mocks):
    with pytest.raises(ValueError, match="Invalid webhook POST key"):
        await workflows.confirm_webhook_checkpoint_and_resume(
            webhook_public_id="public-id",
            post_key="wrong-post-key-abcdefghijklmnop",
            source="dc-script",
            callback_payload=None,
        )
    confirm_mocks["confirm"].assert_not_awaited()


@pytest.mark.anyio
async def test_callback_values_are_loaded_into_workflow_vars(monkeypatch):
    stored = {
        "values": {"samAccountName": "jstarter", "dc_status": "pass"},
        "secret_values_encrypted": {
            "ad_password": workflows.encrypt_secret("Plain!Pass1")
        },
    }
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_external_checkpoints_for_execution_ids",
        AsyncMock(
            return_value={
                88: [
                    {"status": "pending", "callback_payload_json": None},
                    {"status": "confirmed", "callback_payload_json": json.dumps(stored)},
                ]
            }
        ),
    )
    vars_map: dict = {}
    secret_vars: set[str] = set()

    await workflows._apply_webhook_callback_values(
        execution_id=88, vars_map=vars_map, secret_vars=secret_vars
    )

    assert vars_map == {
        "samAccountName": "jstarter",
        "dc_status": "pass",
        "ad_password": "Plain!Pass1",
    }
    assert secret_vars == {"ad_password"}
    assert workflows._resolve_template_value(
        "${vars.samAccountName}/${vars.ad_password}", vars_map=vars_map
    ) == "jstarter/Plain!Pass1"


@pytest.fixture
def listing_mocks(monkeypatch):
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_pending_external_checkpoints_by_webhook_id",
        AsyncMock(return_value=[_checkpoint()]),
    )
    monkeypatch.setattr(
        workflows.staff_custom_fields_repo,
        "get_all_staff_field_values",
        AsyncMock(return_value={1001: {"needs_laptop": True, "cost_centre": "OPS"}}),
    )
    monkeypatch.setattr(
        workflows.company_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 9, "name": "Test Company"}),
    )
    monkeypatch.setattr(
        workflows.staff_repo,
        "get_staff_by_id",
        AsyncMock(
            return_value={
                "id": 1001,
                "company_id": 9,
                "first_name": "Jane",
                "last_name": "Starter",
                "email": "jane.starter@example.com",
                "department": "Operations",
                "job_title": "Analyst",
                "date_onboarded": datetime(2026, 10, 5, 0, 0, 0),
            }
        ),
    )


@pytest.mark.anyio
async def test_list_pending_webhook_checkpoints_returns_request_fields(listing_mocks):
    items = await workflows.list_pending_webhook_checkpoints(
        webhook_public_id="public-id", post_key=POST_KEY
    )

    assert len(items) == 1
    item = items[0]
    assert item["executionId"] == 88
    assert item["staffId"] == 1001
    assert item["companyName"] == "Test Company"
    assert item["stepName"] == "Create AD account"
    assert item["resumeUrl"].endswith("/api/staff/workflow-webhooks/public-id")
    assert item["staff"]["firstName"] == "Jane"
    assert item["staff"]["jobTitle"] == "Analyst"
    assert item["staff"]["dateOnboarded"].startswith("2026-10-05")
    assert item["staff"]["customFields"] == {"needs_laptop": True, "cost_centre": "OPS"}
    assert "offboarding" not in item["staff"]


@pytest.mark.anyio
async def test_list_pending_webhook_route_rejects_wrong_key(listing_mocks):
    with pytest.raises(HTTPException) as exc_info:
        await staff_routes.list_pending_workflow_webhooks(
            webhook_public_id="public-id",
            post_key="wrong-post-key-abcdefghijklmnop",
            limit=100,
            _=None,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_list_pending_webhook_route_serialises_camel_case(listing_mocks):
    items = await staff_routes.list_pending_workflow_webhooks(
        webhook_public_id="public-id", post_key=POST_KEY, limit=100, _=None
    )
    dumped = items[0].model_dump(by_alias=True)
    assert dumped["executionId"] == 88
    assert dumped["resumeUrl"].endswith("/public-id")
    assert dumped["staff"]["customFields"]["cost_centre"] == "OPS"


@pytest.mark.anyio
async def test_list_pending_webhook_returns_empty_without_pending(monkeypatch):
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_pending_external_checkpoints_by_webhook_id",
        AsyncMock(return_value=[]),
    )
    assert (
        await workflows.list_pending_webhook_checkpoints(
            webhook_public_id="public-id", post_key="anything-at-all-123456789"
        )
        == []
    )
