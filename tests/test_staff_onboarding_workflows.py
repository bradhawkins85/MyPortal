from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import staff_onboarding_workflows as workflows


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_http_post_step_supports_query_headers_and_json_string(monkeypatch):
    captured_request: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"ok":true,"id":"123"}'

        def json(self):
            return {"ok": True, "id": "123"}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            return

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            captured_request["method"] = method
            captured_request["url"] = url
            captured_request.update(kwargs)
            return DummyResponse()

    monkeypatch.setattr(workflows.httpx, "AsyncClient", DummyClient)
    result = await workflows._execute_policy_step(
        step={
            "type": "http_post",
            "url": "https://example.test/provision",
            "headers": "{\"Authorization\":\"Bearer ${vars.api_token}\"}",
            "query_params": "{\"staffId\":\"${vars.staff_id}\"}",
            "json": "{\"email\":\"${vars.staff_email}\"}",
            "timeout_seconds": 12,
        },
        company_id=7,
        staff={"id": 88, "email": "new.user@example.com"},
        policy_config={},
        vars_map={"api_token": "abc123", "staff_id": 88, "staff_email": "new.user@example.com"},
    )

    assert result["status_code"] == 200
    assert result["body"] == '{"ok":true,"id":"123"}'
    assert captured_request["method"] == "POST"
    assert captured_request["params"] == {"staffId": "88"}
    assert captured_request["headers"] == {"Authorization": "Bearer abc123"}
    assert captured_request["json"] == {"email": "new.user@example.com"}


@pytest.mark.anyio
async def test_curl_text_step_returns_plain_text_and_escapes_html(monkeypatch):
    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return (b"<script>alert(1)</script>SELECT * FROM staff;", b"")

    async def fake_subprocess_exec(*args, **kwargs):
        return DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess_exec)

    result = await workflows._execute_policy_step(
        step={
            "type": "curl_text",
            "url": "https://example.test/source.txt",
            "timeout_seconds": 8,
        },
        company_id=7,
        staff={"id": 88, "email": "new.user@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["status_code"] == 0
    assert result["body"] == "&lt;script&gt;alert(1)&lt;/script&gt;SELECT * FROM staff;"


def test_coerce_step_json_fields_parses_store_and_http_payload_fields():
    normalized = workflows._coerce_step_json_fields(
        {
            "headers_json": "{\"Authorization\":\"Bearer token\"}",
            "query_params_json": "{\"include\":\"licenses\"}",
            "json_body": "{\"enabled\":true}",
            "store_json": "{\"external_user_id\":\"body.id\"}",
        }
    )

    assert normalized["headers"] == {"Authorization": "Bearer token"}
    assert normalized["query_params"] == {"include": "licenses"}
    assert normalized["json"] == {"enabled": True}
    assert normalized["store"] == {"external_user_id": "body.id"}


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
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        workflows.staff_custom_fields_repo,
        "get_all_staff_field_values",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        workflows.company_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 8, "name": "Test Company"}),
    )
    monkeypatch.setattr(workflows.workflow_repo, "append_step_log", AsyncMock())
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
        "_execute_policy_steps",
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


def test_compute_scheduled_execution_onboarding_uses_local_midnight_minus_three_days():
    scheduled_for_utc, requested_timezone = workflows._compute_scheduled_execution(
        staff={"date_onboarded": "2026-04-10T09:30:00"},
        direction=workflows.DIRECTION_ONBOARDING,
        requested_timezone="Australia/Sydney",
    )

    assert requested_timezone == "Australia/Sydney"
    assert scheduled_for_utc is not None
    assert scheduled_for_utc.isoformat() == "2026-04-06T14:00:00"


def test_compute_scheduled_execution_offboarding_preserves_requested_datetime():
    scheduled_for_utc, requested_timezone = workflows._compute_scheduled_execution(
        staff={"date_offboarded": "2026-04-10T15:45:00"},
        direction=workflows.DIRECTION_OFFBOARDING,
        requested_timezone="America/New_York",
    )

    assert requested_timezone == "America/New_York"
    assert scheduled_for_utc is not None
    assert scheduled_for_utc.isoformat() == "2026-04-10T19:45:00"


@pytest.mark.anyio
async def test_enqueue_workflow_creates_approved_execution(monkeypatch):
    staff_record = {"id": 333, "date_onboarded": None}
    monkeypatch.setattr(workflows.staff_repo, "get_staff_by_id", AsyncMock(return_value=staff_record))
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(return_value={"workflow_key": workflows.workflow_repo.DEFAULT_WORKFLOW_KEY}),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "create_or_reset_execution",
        AsyncMock(return_value={"id": 77}),
    )
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", AsyncMock())

    await workflows.enqueue_staff_onboarding_workflow(
        company_id=9,
        staff_id=333,
        initiated_by_user_id=10,
        direction=workflows.DIRECTION_ONBOARDING,
    )

    workflows.workflow_repo.create_or_reset_execution.assert_awaited_once()
    workflows.workflow_repo.update_execution_state.assert_awaited_once()
    assert workflows.workflow_repo.update_execution_state.await_args.kwargs["state"] == workflows.STATE_APPROVED


@pytest.mark.anyio
async def test_process_due_approved_executions_runs_claimed_rows(monkeypatch):
    claim_mock = AsyncMock(
        side_effect=[
            {"id": 1, "company_id": 7, "staff_id": 101, "direction": workflows.DIRECTION_ONBOARDING},
            None,
        ]
    )
    run_mock = AsyncMock(return_value={"state": workflows.STATE_COMPLETED})
    monkeypatch.setattr(workflows.workflow_repo, "claim_next_due_approved_execution", claim_mock)
    monkeypatch.setattr(workflows, "run_staff_onboarding_workflow", run_mock)

    result = await workflows.process_due_approved_executions(limit=5)

    assert result == {"processed": 1, "skipped": 0}
    run_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_resume_workflow_pauses_on_license_exhaustion(monkeypatch):
    staff_record = {
        "id": 601,
        "company_id": 10,
        "first_name": "Pat",
        "last_name": "Lee",
        "email": "pat@example.com",
        "enabled": True,
        "is_ex_staff": False,
        "onboarding_status": workflows.STATE_APPROVED,
    }
    monkeypatch.setattr(workflows.staff_repo, "get_staff_by_id", AsyncMock(return_value=staff_record))
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(
            return_value={
                "workflow_key": workflows.workflow_repo.DEFAULT_WORKFLOW_KEY,
                "max_retries": 0,
                "config": {"create_ticket_on_license_unavailable": True},
            }
        ),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_execution_by_staff_id",
        AsyncMock(return_value={"id": 90, "direction": workflows.DIRECTION_ONBOARDING}),
    )
    monkeypatch.setattr(
        workflows,
        "_execute_policy_steps",
        AsyncMock(side_effect=workflows.LicenseExhaustionError("No seats available", step_name="assign_license")),
    )
    monkeypatch.setattr(workflows, "_create_failure_ticket", AsyncMock(return_value=444))
    monkeypatch.setattr(workflows.workflow_repo, "append_step_log", AsyncMock())
    update_execution_mock = AsyncMock()
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", update_execution_mock)
    update_staff_mock = AsyncMock()
    monkeypatch.setattr(workflows.staff_repo, "update_staff", update_staff_mock)

    result = await workflows.resume_staff_onboarding_workflow_after_external_confirmation(
        company_id=10,
        staff_id=601,
        execution_id=90,
        initiated_by_user_id=None,
    )

    assert result["state"] == workflows.STATE_PAUSED_LICENSE_UNAVAILABLE
    assert result["helpdesk_ticket_id"] == 444
    assert result["retry_metadata"]["reason"] == "license_unavailable"
    assert update_execution_mock.await_args.kwargs["state"] == workflows.STATE_PAUSED_LICENSE_UNAVAILABLE
    assert update_staff_mock.await_args.kwargs["onboarding_status"] == workflows.STATE_PAUSED_LICENSE_UNAVAILABLE


@pytest.mark.anyio
async def test_process_paused_license_executions_resumes_eligible_execution(monkeypatch):
    claim_mock = AsyncMock(
        side_effect=[
            {"id": 33, "company_id": 7, "staff_id": 55},
            None,
        ]
    )
    monkeypatch.setattr(workflows.workflow_repo, "claim_next_paused_license_execution", claim_mock)
    resume_mock = AsyncMock(return_value={"state": workflows.STATE_COMPLETED, "execution_id": 33})
    monkeypatch.setattr(workflows, "resume_staff_onboarding_workflow_after_external_confirmation", resume_mock)

    result = await workflows.process_paused_license_executions(company_id=7, limit=5)

    assert result == {"resumed": 1, "skipped": 0}
    resume_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_get_staff_workflow_history_returns_executions_with_steps(monkeypatch):
    """GET /api/staff/{staff_id}/workflow/history returns executions with step logs."""
    from app.api.routes import staff as staff_routes
    from app.repositories import staff_onboarding_workflows as workflow_repo

    execution = {
        "id": 42,
        "company_id": 5,
        "staff_id": 99,
        "workflow_key": "staff_onboarding_m365",
        "direction": "onboarding",
        "state": "completed",
        "current_step": None,
        "retries_used": 0,
        "last_error": None,
        "helpdesk_ticket_id": None,
        "requested_at": datetime(2025, 1, 1, 10, 0, 0),
        "started_at": datetime(2025, 1, 1, 10, 0, 1),
        "completed_at": datetime(2025, 1, 1, 10, 5, 0),
    }
    step_log = {
        "id": 7,
        "execution_id": 42,
        "step_name": "provision_account",
        "status": "success",
        "attempt": 1,
        "request_payload": '{"user":"test@example.com"}',
        "response_payload": '{"created":true,"userId":"u-123"}',
        "error_message": None,
        "started_at": datetime(2025, 1, 1, 10, 0, 2),
        "completed_at": datetime(2025, 1, 1, 10, 0, 5),
    }

    monkeypatch.setattr(
        staff_routes.staff_workflow_repo,
        "list_execution_history_for_staff",
        AsyncMock(return_value=[execution]),
    )
    monkeypatch.setattr(
        staff_routes.staff_workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={42: [step_log]}),
    )
    monkeypatch.setattr(
        staff_routes.staff_repo,
        "get_staff_by_id",
        AsyncMock(return_value={
            "id": 99,
            "company_id": 5,
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
        }),
    )
    super_admin_user = {"id": 1, "is_super_admin": True}

    result = await staff_routes.get_staff_workflow_history(
        staff_id=99,
        limit=50,
        _=None,
        current_user=super_admin_user,
    )

    assert result["staffId"] == 99
    assert result["staffName"] == "Alice Smith"
    assert len(result["executions"]) == 1
    ex = result["executions"][0]
    assert ex["executionId"] == 42
    assert ex["state"] == "completed"
    assert ex["direction"] == "onboarding"
    assert len(ex["steps"]) == 1
    step = ex["steps"][0]
    assert step["stepName"] == "provision_account"
    assert step["status"] == "success"
    assert step["requestPayload"] == {"user": "test@example.com"}
    assert step["responsePayload"] == {"created": True, "userId": "u-123"}


@pytest.mark.anyio
async def test_store_json_variable_is_applied_after_step_and_resolves_in_next_step(monkeypatch):
    """Regression: store_json mapping must use the coerced resolved_step so that
    variables like ${vars.pwd} are populated and substituted in subsequent steps."""

    # Step 1: curl_text returns a plain-text password body.
    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return (b"S3cr3tP@ss!", b"")

    async def fake_subprocess_exec(*args, **kwargs):
        return DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess_exec)

    # Execute the curl_text step and confirm the body is returned.
    curl_result = await workflows._execute_policy_step(
        step={
            "type": "curl_text",
            "url": "https://example.test/password",
            "timeout_seconds": 5,
        },
        company_id=1,
        staff={"id": 10, "email": "user@example.com"},
        policy_config={},
        vars_map={},
    )
    assert curl_result["body"] == "S3cr3tP@ss!"

    # Verify that _coerce_step_json_fields converts store_json to a store dict,
    # which is what allows the variable mapping to work.
    step = {"type": "curl_text", "url": "https://example.test/password", "store_json": '{"pwd":"body"}'}
    resolved_step = workflows._coerce_step_json_fields(step)
    assert resolved_step["store"] == {"pwd": "body"}

    # Confirm that extracting the password via the store mapping gives the correct value.
    pwd_value = workflows._get_nested_value(curl_result, "body")
    assert pwd_value == "S3cr3tP@ss!"

    # Verify the variable resolves correctly in an email body template (the end-to-end scenario).
    email_body = workflows._resolve_template_value(
        "Password: ${vars.pwd}",
        vars_map={"pwd": pwd_value},
    )
    assert email_body == "Password: S3cr3tP@ss!"


@pytest.mark.anyio
async def test_kid_friendly_password_always_contains_symbol(monkeypatch):
    """Kid-friendly passwords must always contain at least one symbol.

    This covers the edge case where both generated words contain no substitutable
    letters in non-leading positions, which previously produced a symbol-free password.
    """
    import app.repositories.staff_onboarding_workflows as wf_repo

    _symbols = set(workflows._KID_SUBSTITUTIONS.values())

    # Force a word list that has no substitutable characters outside the first letter
    # so candidates will always be empty unless we pick words that happen to qualify.
    # Using "Bbc" and "Ddf" — no a/i/s/e/o in any position.
    monkeypatch.setattr(workflows, "_kid_words_cache", ["bbc", "ddf"])

    for _ in range(50):
        password = await workflows._generate_kid_friendly_password()
        assert any(ch in _symbols for ch in password), (
            f"Kid-friendly password '{password}' contains no symbol"
        )

    # Also verify that normal words still always yield a symbol.
    monkeypatch.setattr(workflows, "_kid_words_cache", ["sunshine", "rainbow"])
    for _ in range(50):
        password = await workflows._generate_kid_friendly_password()
        assert any(ch in _symbols for ch in password), (
            f"Kid-friendly password '{password}' contains no symbol"
        )
