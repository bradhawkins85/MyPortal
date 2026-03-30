from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import staff_onboarding_workflows as workflows


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_normalise_workflow_steps_uses_step_type_from_config():
    policy_config = {
        "steps": [
            {
                "name": "Create user",
                "enabled": True,
                "config": {
                    "type": "m365_create_user",
                    "store": {"generated_password": "generated_password"},
                },
            }
        ]
    }

    steps = workflows._normalise_workflow_steps(policy_config, direction=workflows.DIRECTION_ONBOARDING)

    assert len(steps) == 1
    assert steps[0]["name"] == "Create user"
    assert steps[0]["type"] == "m365_create_user"


def test_normalise_workflow_steps_uses_offboarding_steps_for_offboarding_direction():
    policy_config = {
        "steps": [{"name": "Onboarding only", "type": "provision_account"}],
        "offboarding_steps": [{"name": "Hide from GAL", "type": "m365_hide_from_gal"}],
    }

    steps = workflows._normalise_workflow_steps(policy_config, direction=workflows.DIRECTION_OFFBOARDING)

    assert len(steps) == 1
    assert steps[0]["name"] == "Hide from GAL"
    assert steps[0]["type"] == "m365_hide_from_gal"


@pytest.mark.anyio
async def test_execute_policy_steps_wait_checkpoint_returns_pause(monkeypatch):
    monkeypatch.setattr(
        workflows,
        "_normalise_workflow_steps",
        lambda *_args, **_kwargs: [{"name": "Checkpoint", "type": "wait_external_checkpoint"}],
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={88: []}),
    )
    checkpoint_mock = AsyncMock()
    update_state_mock = AsyncMock()
    monkeypatch.setattr(workflows.workflow_repo, "create_external_checkpoint", checkpoint_mock)
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", update_state_mock)

    result = await workflows._execute_policy_steps(
        execution_id=88,
        company_id=9,
        staff={"id": 1001, "email": "new.user@example.com"},
        direction=workflows.DIRECTION_ONBOARDING,
        policy_config={},
        max_retries=0,
        waiting_external_state=workflows.STATE_WAITING_EXTERNAL,
    )

    assert result["paused"] is True
    assert isinstance(result["confirmation_token"], str)
    checkpoint_mock.assert_awaited_once()
    update_state_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_execute_policy_steps_uses_per_step_retry_policy(monkeypatch):
    monkeypatch.setattr(
        workflows,
        "_normalise_workflow_steps",
        lambda *_args, **_kwargs: [
            {
                "name": "Hide from GAL",
                "type": "m365_hide_from_gal",
                "retry_policy": {"max_retries": 5},
            }
        ],
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={77: []}),
    )
    monkeypatch.setattr(workflows.workflow_repo, "append_step_log", AsyncMock())
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", AsyncMock())
    attempt_mock = AsyncMock(return_value={"hidden": True})
    monkeypatch.setattr(workflows, "_attempt_step", attempt_mock)

    await workflows._execute_policy_steps(
        execution_id=77,
        company_id=9,
        staff={"id": 700, "email": "offboard@example.com"},
        direction=workflows.DIRECTION_OFFBOARDING,
        policy_config={},
        max_retries=0,
        waiting_external_state=workflows.STATE_OFFBOARDING_WAITING_EXTERNAL,
    )

    assert attempt_mock.await_args.kwargs["max_retries"] == 5


@pytest.mark.anyio
async def test_execute_policy_steps_continues_when_step_failure_mode_continue(monkeypatch):
    monkeypatch.setattr(
        workflows,
        "_normalise_workflow_steps",
        lambda *_args, **_kwargs: [
            {
                "name": "Rename identity",
                "type": "m365_rename_upn_display_name",
                "failure_policy": {"mode": "continue"},
            },
            {
                "name": "Hide from GAL",
                "type": "m365_hide_from_gal",
            },
        ],
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={78: []}),
    )
    monkeypatch.setattr(workflows.workflow_repo, "append_step_log", AsyncMock())
    update_mock = AsyncMock()
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", update_mock)
    monkeypatch.setattr(
        workflows,
        "_attempt_step",
        AsyncMock(side_effect=[workflows.WorkflowStepError("rename failed"), {"hidden": True}]),
    )

    result = await workflows._execute_policy_steps(
        execution_id=78,
        company_id=9,
        staff={"id": 701, "email": "offboard@example.com"},
        direction=workflows.DIRECTION_OFFBOARDING,
        policy_config={},
        max_retries=0,
        waiting_external_state=workflows.STATE_OFFBOARDING_WAITING_EXTERNAL,
    )

    assert result["paused"] is False
    assert workflows._attempt_step.await_count == 2
    assert update_mock.await_count >= 1
