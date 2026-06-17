from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import staff_onboarding_workflows as workflows
from app.features.staff.handlers import _normalise_workflow_config


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _mock_staff_custom_fields(monkeypatch):
    monkeypatch.setattr(
        workflows.staff_custom_fields_repo,
        "get_all_staff_field_values",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        workflows.company_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 9, "name": "Test Company"}),
    )
    monkeypatch.setattr(
        workflows.user_repo,
        "get_user_by_id",
        AsyncMock(return_value=None),
    )


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


def test_normalise_workflow_steps_disabled_step_is_skipped():
    policy_config = {
        "steps": [
            {"name": "Enabled step", "type": "provision_account", "enabled": True},
            {"name": "Disabled step", "type": "m365_assign_license", "enabled": False},
        ]
    }

    steps = workflows._normalise_workflow_steps(policy_config, direction=workflows.DIRECTION_ONBOARDING)

    assert len(steps) == 1
    assert steps[0]["type"] == "provision_account"


def test_normalise_workflow_steps_no_enabled_key_defaults_to_enabled():
    # Steps without an 'enabled' key should be treated as enabled (backward compat)
    policy_config = {
        "steps": [
            {"name": "Old format step", "type": "provision_account"},
        ]
    }

    steps = workflows._normalise_workflow_steps(policy_config, direction=workflows.DIRECTION_ONBOARDING)

    assert len(steps) == 1
    assert steps[0]["type"] == "provision_account"


def test_normalise_workflow_steps_inner_config_enabled_false_does_not_skip_step():
    # enabled in the inner config dict must NOT cause the step to be skipped;
    # only the top-level 'enabled' field controls step execution.
    policy_config = {
        "steps": [
            {
                "name": "Step with config enabled false",
                "type": "provision_account",
                # No top-level 'enabled' key — must default to True
                "config": {"type": "provision_account", "enabled": False},
            }
        ]
    }

    steps = workflows._normalise_workflow_steps(policy_config, direction=workflows.DIRECTION_ONBOARDING)

    assert len(steps) == 1, "Step should execute when only inner config has enabled=False"
    assert steps[0]["type"] == "provision_account"



def test_normalise_workflow_config_adds_key_from_type_for_old_format_steps():
    # Old-format steps (saved before WorkflowStepDefinition required 'key') must not
    # raise a validation error.  The key should be derived from 'type' automatically.
    raw_config = {
        "steps": [
            {"name": "Provision account", "type": "provision_account"},
            {"name": "Assign license", "type": "m365_assign_license"},
        ],
        "offboarding_steps": [
            {"name": "Offboard account", "type": "offboard_account"},
        ],
    }

    result = _normalise_workflow_config(raw_config)

    assert len(result["steps"]) == 2
    assert result["steps"][0]["key"] == "provision_account"
    assert result["steps"][1]["key"] == "m365_assign_license"
    assert len(result["offboarding_steps"]) == 1
    assert result["offboarding_steps"][0]["key"] == "offboard_account"
    # enabled must default to True so steps are not accidentally disabled
    assert result["steps"][0]["enabled"] is True
    assert result["offboarding_steps"][0]["enabled"] is True


def test_normalise_workflow_config_preserves_existing_key():
    # Steps that already have a 'key' should keep it unchanged.
    raw_config = {
        "steps": [
            {"key": "my_custom_key", "name": "Step", "type": "provision_account"},
        ]
    }

    result = _normalise_workflow_config(raw_config)

    assert result["steps"][0]["key"] == "my_custom_key"


def test_normalise_custom_field_group_mappings_supports_dict_and_list_inputs():
    from_dict = workflows._normalise_custom_field_group_mappings(
        {"custom_field_group_mappings": {"field_one": ["group-a", "group-b"], "field_two": "group-c,group-d"}}
    )
    from_list = workflows._normalise_custom_field_group_mappings(
        {
            "customFieldGroupMappings": [
                {"field_name": "field_three", "group_ids": ["group-e"]},
                {"field": "field_four", "group_id": "group-f"},
            ]
        }
    )

    assert from_dict == {
        "field_one": ["group-a", "group-b"],
        "field_two": ["group-c", "group-d"],
    }
    assert from_list == {
        "field_three": ["group-e"],
        "field_four": ["group-f"],
    }


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


@pytest.mark.anyio
async def test_execute_policy_steps_pauses_when_step_failure_mode_pause(monkeypatch):
    monkeypatch.setattr(
        workflows,
        "_normalise_workflow_steps",
        lambda *_args, **_kwargs: [
            {
                "name": "Create user",
                "type": "create_user",
                "failure_policy": {"mode": "pause", "create_ticket_on_failure": True},
            }
        ],
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={81: []}),
    )
    update_mock = AsyncMock()
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", update_mock)
    monkeypatch.setattr(
        workflows,
        "_attempt_step",
        AsyncMock(side_effect=workflows.WorkflowStepError("create failed", request_payload={"user": "new.user@example.com"})),
    )

    result = await workflows._execute_policy_steps(
        execution_id=81,
        company_id=9,
        staff={"id": 703, "email": "new.user@example.com"},
        direction=workflows.DIRECTION_ONBOARDING,
        policy_config={},
        max_retries=0,
        waiting_external_state=workflows.STATE_WAITING_EXTERNAL,
    )

    assert result["paused"] is True
    assert result["pause_reason"] == "step_failure"
    assert result["create_ticket_on_pause"] is True
    assert result["step_name"] == "Create user"
    assert result["request_payload"] == {"user": "new.user@example.com"}
    assert update_mock.await_args.kwargs["state"] == workflows.STATE_WAITING_EXTERNAL


@pytest.mark.anyio
async def test_execute_policy_steps_assigns_groups_from_selected_custom_fields(monkeypatch):
    monkeypatch.setattr(workflows, "_normalise_workflow_steps", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={79: []}),
    )
    monkeypatch.setattr(workflows.workflow_repo, "append_step_log", AsyncMock())
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", AsyncMock())
    monkeypatch.setattr(
        workflows.staff_custom_fields_repo,
        "get_all_staff_field_values",
        AsyncMock(return_value={702: {"entra_sales": True, "entra_ops": False}}),
    )
    attempt_mock = AsyncMock(return_value={"added": True})
    monkeypatch.setattr(workflows, "_attempt_step", attempt_mock)
    monkeypatch.setattr(workflows, "_resolve_staff_m365_user", AsyncMock(return_value={"id": "user-123"}))

    result = await workflows._execute_policy_steps(
        execution_id=79,
        company_id=9,
        staff={"id": 702, "email": "new.user@example.com"},
        direction=workflows.DIRECTION_ONBOARDING,
        policy_config={
            "custom_field_group_mappings": {
                "entra_sales": ["group-sales", "group-announce"],
                "entra_ops": ["group-ops"],
            }
        },
        max_retries=3,
        waiting_external_state=workflows.STATE_WAITING_EXTERNAL,
    )

    assert result["paused"] is False
    assert attempt_mock.await_count == 2
    step_names = [call.kwargs["step_name"] for call in attempt_mock.await_args_list]
    assert step_names == [
        "custom_field_group:entra_sales:group-sales",
        "custom_field_group:entra_sales:group-announce",
    ]


@pytest.mark.anyio
async def test_execute_policy_step_adds_user_to_teams_groups(monkeypatch):
    monkeypatch.setattr(workflows, "_resolve_staff_m365_user", AsyncMock(return_value={"id": "user-1"}))
    monkeypatch.setattr(workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token"))
    graph_post = AsyncMock(return_value={})
    monkeypatch.setattr(workflows.m365_service, "_graph_post", graph_post)

    result = await workflows._execute_policy_step(
        step={"type": "m365_add_teams_group_member", "group_ids_csv": "group-a,group-b"},
        company_id=9,
        staff={"id": 703, "email": "new.user@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["operation"] == "add"
    assert result["group_ids"] == ["group-a", "group-b"]
    assert graph_post.await_count == 2


@pytest.mark.anyio
async def test_execute_policy_step_removes_user_from_sharepoint_sites(monkeypatch):
    monkeypatch.setattr(workflows, "_resolve_staff_m365_user", AsyncMock(return_value={"id": "user-2"}))
    monkeypatch.setattr(workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_get",
        AsyncMock(return_value={"value": [{"id": "perm-1", "grantedToIdentitiesV2": [{"user": {"id": "user-2"}}]}]}),
    )
    graph_delete = AsyncMock(return_value={})
    monkeypatch.setattr(workflows.m365_service, "_graph_delete", graph_delete)

    result = await workflows._execute_policy_step(
        step={"type": "m365_remove_sharepoint_site_member", "site_ids_csv": "site-a"},
        company_id=9,
        staff={"id": 704, "email": "offboard.user@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["operation"] == "remove"
    assert result["site_ids"] == ["site-a"]
    graph_delete.assert_awaited_once()


@pytest.mark.anyio
async def test_run_offboarding_step_removes_calendar_events_when_enabled(monkeypatch):
    monkeypatch.setattr(workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-2", "userPrincipalName": "offboard.user@example.com"}),
    )
    remove_calendar_events_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(workflows.m365_service, "remove_calendar_events", remove_calendar_events_mock)

    result = await workflows._run_offboarding_step(
        company_id=9,
        staff={"id": 704, "email": "offboard.user@example.com"},
        policy_config={},
        step_config={
            "disable_sign_in": False,
            "revoke_licenses": False,
            "remove_from_groups": False,
            "remove_calendar_events": True,
        },
        vars_map={},
    )

    remove_calendar_events_mock.assert_awaited_once_with(9, "offboard.user@example.com")
    assert "remove_calendar_events" in result["steps_executed"]


@pytest.mark.anyio
async def test_run_offboarding_step_skips_calendar_event_removal_when_disabled(monkeypatch):
    monkeypatch.setattr(workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-2", "userPrincipalName": "offboard.user@example.com"}),
    )
    remove_calendar_events_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(workflows.m365_service, "remove_calendar_events", remove_calendar_events_mock)

    result = await workflows._run_offboarding_step(
        company_id=9,
        staff={"id": 704, "email": "offboard.user@example.com"},
        policy_config={},
        step_config={
            "disable_sign_in": False,
            "revoke_licenses": False,
            "remove_from_groups": False,
            "remove_calendar_events": False,
        },
        vars_map={},
    )

    remove_calendar_events_mock.assert_not_awaited()
    assert "remove_calendar_events" not in result["steps_executed"]


@pytest.mark.anyio
async def test_run_offboarding_step_disables_mailbox_rules_when_enabled(monkeypatch):
    monkeypatch.setattr(workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-2", "userPrincipalName": "offboard.user@example.com"}),
    )
    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_get_all",
        AsyncMock(
            return_value=[
                {"id": "rule-1", "isEnabled": True},
                {"id": "rule-2", "isEnabled": False},
                {"id": "rule-3"},
            ]
        ),
    )
    graph_patch = AsyncMock(return_value={})
    monkeypatch.setattr(workflows, "_graph_patch", graph_patch)

    result = await workflows._run_offboarding_step(
        company_id=9,
        staff={"id": 704, "email": "offboard.user@example.com"},
        policy_config={},
        step_config={
            "disable_sign_in": False,
            "revoke_licenses": False,
            "remove_from_groups": False,
            "disable_mailbox_rules": True,
        },
        vars_map={},
    )

    assert "disable_mailbox_rules" in result["steps_executed"]
    assert result["mailbox_rules_disabled"] == 2
    assert graph_patch.await_count == 2
    patched_urls = [call.args[1] for call in graph_patch.await_args_list]
    assert patched_urls[0].endswith("/mailFolders/inbox/messageRules/rule-1")
    assert patched_urls[1].endswith("/mailFolders/inbox/messageRules/rule-3")


@pytest.mark.anyio
async def test_run_offboarding_step_continues_when_mailbox_rule_patch_forbidden(monkeypatch):
    monkeypatch.setattr(workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-2", "userPrincipalName": "offboard.user@example.com"}),
    )
    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_get_all",
        AsyncMock(return_value=[{"id": "rule-1", "isEnabled": True}]),
    )
    monkeypatch.setattr(
        workflows,
        "_graph_patch",
        AsyncMock(side_effect=workflows.WorkflowStepError("Forbidden", http_status=403)),
    )

    result = await workflows._run_offboarding_step(
        company_id=9,
        staff={"id": 704, "email": "offboard.user@example.com"},
        policy_config={},
        step_config={
            "disable_sign_in": False,
            "revoke_licenses": False,
            "remove_from_groups": False,
            "disable_mailbox_rules": True,
        },
        vars_map={},
    )

    assert "disable_mailbox_rules" not in result["steps_executed"]
    assert result["mailbox_rules_disabled"] == 0


@pytest.mark.anyio
async def test_run_offboarding_step_continues_when_mailbox_rules_list_forbidden(monkeypatch):
    monkeypatch.setattr(workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-2", "userPrincipalName": "offboard.user@example.com"}),
    )
    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_get_all",
        AsyncMock(side_effect=workflows.M365Error("Forbidden", http_status=403)),
    )
    graph_patch = AsyncMock(return_value={})
    monkeypatch.setattr(workflows, "_graph_patch", graph_patch)

    result = await workflows._run_offboarding_step(
        company_id=9,
        staff={"id": 704, "email": "offboard.user@example.com"},
        policy_config={},
        step_config={
            "disable_sign_in": False,
            "revoke_licenses": False,
            "remove_from_groups": False,
            "disable_mailbox_rules": True,
        },
        vars_map={},
    )

    assert "disable_mailbox_rules" not in result["steps_executed"]
    assert result["mailbox_rules_disabled"] == 0
    graph_patch.assert_not_awaited()


@pytest.mark.anyio
async def test_run_offboarding_step_skips_mailbox_rule_disable_when_mailbox_missing(monkeypatch):
    monkeypatch.setattr(workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-2", "userPrincipalName": "offboard.user@example.com"}),
    )
    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_get_all",
        AsyncMock(side_effect=workflows.M365Error("No mailbox", http_status=404)),
    )
    graph_patch = AsyncMock(return_value={})
    monkeypatch.setattr(workflows, "_graph_patch", graph_patch)

    result = await workflows._run_offboarding_step(
        company_id=9,
        staff={"id": 704, "email": "offboard.user@example.com"},
        policy_config={},
        step_config={
            "disable_sign_in": False,
            "revoke_licenses": False,
            "remove_from_groups": False,
            "disable_mailbox_rules": True,
        },
        vars_map={},
    )

    assert "disable_mailbox_rules" not in result["steps_executed"]
    assert result["mailbox_rules_disabled"] == 0
    graph_patch.assert_not_awaited()


@pytest.mark.anyio
async def test_execute_policy_step_delete_staff_record(monkeypatch):
    delete_staff = AsyncMock(return_value=None)
    monkeypatch.setattr(workflows.staff_repo, "delete_staff", delete_staff)

    result = await workflows._execute_policy_step(
        step={"type": "delete_staff_record"},
        company_id=9,
        staff={"id": 801, "email": "duplicate@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["deleted"] is True
    assert result["staff_id"] == 801
    delete_staff.assert_awaited_once_with(801)


@pytest.mark.anyio
async def test_execute_policy_step_delete_staff_record_missing_id(monkeypatch):
    delete_staff = AsyncMock(return_value=None)
    monkeypatch.setattr(workflows.staff_repo, "delete_staff", delete_staff)

    with pytest.raises(workflows.WorkflowStepError, match="staff ID is not available"):
        await workflows._execute_policy_step(
            step={"type": "delete_staff_record"},
            company_id=9,
            staff={"email": "noid@example.com"},
            policy_config={},
            vars_map={},
        )

    delete_staff.assert_not_awaited()


@pytest.mark.anyio
async def test_execute_policy_step_disable_myportal_account(monkeypatch):
    portal_user = {"id": 55, "email": "leaver@example.com", "is_active": 1}
    get_user = AsyncMock(return_value=portal_user)
    update_user = AsyncMock(return_value={**portal_user, "is_active": 0})
    monkeypatch.setattr(workflows.user_repo, "get_user_by_email", get_user)
    monkeypatch.setattr(workflows.user_repo, "update_user", update_user)

    result = await workflows._execute_policy_step(
        step={"type": "disable_myportal_account"},
        company_id=9,
        staff={"id": 801, "email": "leaver@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["disabled"] is True
    assert result["user_id"] == 55
    assert result["email"] == "leaver@example.com"
    get_user.assert_awaited_once_with("leaver@example.com")
    update_user.assert_awaited_once_with(55, is_active=0)


@pytest.mark.anyio
async def test_execute_policy_step_disable_myportal_account_no_portal_user(monkeypatch):
    get_user = AsyncMock(return_value=None)
    update_user = AsyncMock()
    monkeypatch.setattr(workflows.user_repo, "get_user_by_email", get_user)
    monkeypatch.setattr(workflows.user_repo, "update_user", update_user)

    result = await workflows._execute_policy_step(
        step={"type": "disable_myportal_account"},
        company_id=9,
        staff={"id": 801, "email": "noportal@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["disabled"] is False
    assert result["reason"] == "no_portal_account"
    update_user.assert_not_awaited()


@pytest.mark.anyio
async def test_execute_policy_step_disable_myportal_account_missing_email(monkeypatch):
    get_user = AsyncMock()
    monkeypatch.setattr(workflows.user_repo, "get_user_by_email", get_user)

    with pytest.raises(workflows.WorkflowStepError, match="staff email is not available"):
        await workflows._execute_policy_step(
            step={"type": "disable_myportal_account"},
            company_id=9,
            staff={"id": 801},
            policy_config={},
            vars_map={},
        )

    get_user.assert_not_awaited()


@pytest.mark.anyio
async def test_run_offboarding_step_converts_mailbox_before_license_removal(monkeypatch):
    monkeypatch.setattr(workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-2", "userPrincipalName": "offboard.user@example.com"}),
    )
    call_order: list[str] = []

    async def _convert(company_id: int, upn: str) -> None:
        call_order.append("convert")
        assert company_id == 9
        assert upn == "offboard.user@example.com"

    async def _graph_get(_token: str, url: str) -> dict[str, object]:
        call_order.append("get_licenses")
        assert url.endswith("/users/user-2/licenseDetails")
        return {"value": [{"skuId": "sku-1"}]}

    async def _graph_post(_token: str, url: str, payload: dict[str, object]) -> dict[str, object]:
        call_order.append("remove_licenses")
        assert url.endswith("/users/user-2/assignLicense")
        assert payload == {"addLicenses": [], "removeLicenses": ["sku-1"]}
        return {}

    monkeypatch.setattr(workflows.m365_service, "convert_mailbox_to_shared", AsyncMock(side_effect=_convert))
    monkeypatch.setattr(workflows.m365_service, "_graph_get", AsyncMock(side_effect=_graph_get))
    monkeypatch.setattr(workflows.m365_service, "_graph_post", AsyncMock(side_effect=_graph_post))

    result = await workflows._run_offboarding_step(
        company_id=9,
        staff={"id": 704, "email": "offboard.user@example.com"},
        policy_config={},
        step_config={
            "disable_sign_in": False,
            "convert_to_shared_mailbox": True,
            "revoke_licenses": True,
            "remove_from_groups": False,
        },
        vars_map={},
    )

    assert call_order == ["convert", "get_licenses", "remove_licenses"]
    assert "convert_to_shared_mailbox" in result["steps_executed"]
    assert result["converted_to_shared_mailbox"] is True
    assert result["licenses_removed"] == 1
