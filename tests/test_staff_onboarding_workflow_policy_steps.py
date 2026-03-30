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
