from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.services import staff_onboarding_workflows as workflows
from app.features.staff import handlers as staff_handlers
from app.features.staff.handlers import _normalise_workflow_config
from app.schemas.staff_onboarding_workflows import WorkflowStepDefinition


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
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_external_checkpoints_for_execution_ids",
        AsyncMock(return_value={}),
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

    steps = workflows._normalise_workflow_steps(
        policy_config, direction=workflows.DIRECTION_ONBOARDING
    )

    assert len(steps) == 1
    assert steps[0]["name"] == "Create user"
    assert steps[0]["type"] == "m365_create_user"


def test_normalise_workflow_steps_preserves_hudu_password_label_config():
    policy_config = {
        "steps": [
            {
                "name": "Push password to Hudu",
                "enabled": True,
                "config": {
                    "type": "hudu_push_password",
                    "name": "${vars.staff.full_name} - M365 Password",
                    "password": "${vars.generated_password}",
                },
            }
        ]
    }

    steps = workflows._normalise_workflow_steps(
        policy_config, direction=workflows.DIRECTION_ONBOARDING
    )

    assert len(steps) == 1
    assert steps[0]["_workflow_step_name"] == "Push password to Hudu"
    assert steps[0]["name"] == "${vars.staff.full_name} - M365 Password"
    assert steps[0]["type"] == "hudu_push_password"


def test_myportal_credential_is_available_in_both_catalogs_with_form_help():
    assert any(s["type"] == "create_myportal_credential" for s in staff_handlers._ONBOARDING_STEP_CATALOG)
    assert any(s["type"] == "create_myportal_credential" for s in staff_handlers._OFFBOARDING_STEP_CATALOG)
    fields = staff_handlers._WORKFLOW_STEP_FORM_SCHEMA["create_myportal_credential"]["fields"]
    assert {field["name"] for field in fields} >= {
        "credential_name", "username", "credential_class", "secret_source",
        "owner", "asset_id", "ticket_id", "expires_on", "review_on",
        "credential_output_var",
    }


def test_myportal_credential_rejects_literal_secret_in_policy():
    with pytest.raises(ValidationError, match="literal passwords are not allowed"):
        WorkflowStepDefinition(
            key="create_myportal_credential",
            name="Store account",
            config={"type": "create_myportal_credential", "secret_source": "not-a-variable"},
        )


@pytest.mark.anyio
async def test_myportal_credential_step_returns_only_non_secret_metadata(monkeypatch):
    from app.services import workflow_vault

    create = AsyncMock(return_value={"credential_id": 71, "credential_version": 1})
    monkeypatch.setattr(workflow_vault, "create_for_staff_workflow", create)
    result = await workflows._execute_policy_step(
        step={
            "type": "create_myportal_credential",
            "credential_name": "New account",
            "credential_class": "user",
            "secret_source": "resolved-secret",
            "credential_output_var": "new_credential_id",
        },
        company_id=9,
        staff={"id": 4},
        policy_config={},
        vars_map={},
        execution_id=22,
        step_name="store-vault",
    )
    assert result == {
        "credential_id": 71,
        "credential_version": 1,
        "new_credential_id": 71,
    }
    assert create.await_args.kwargs["plaintext"] == "resolved-secret"


def test_normalise_workflow_steps_uses_offboarding_steps_for_offboarding_direction():
    policy_config = {
        "steps": [{"name": "Onboarding only", "type": "provision_account"}],
        "offboarding_steps": [{"name": "Hide from GAL", "type": "m365_hide_from_gal"}],
    }

    steps = workflows._normalise_workflow_steps(
        policy_config, direction=workflows.DIRECTION_OFFBOARDING
    )

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

    steps = workflows._normalise_workflow_steps(
        policy_config, direction=workflows.DIRECTION_ONBOARDING
    )

    assert len(steps) == 1
    assert steps[0]["type"] == "provision_account"


def test_normalise_workflow_steps_no_enabled_key_defaults_to_enabled():
    # Steps without an 'enabled' key should be treated as enabled (backward compat)
    policy_config = {
        "steps": [
            {"name": "Old format step", "type": "provision_account"},
        ]
    }

    steps = workflows._normalise_workflow_steps(
        policy_config, direction=workflows.DIRECTION_ONBOARDING
    )

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

    steps = workflows._normalise_workflow_steps(
        policy_config, direction=workflows.DIRECTION_ONBOARDING
    )

    assert (
        len(steps) == 1
    ), "Step should execute when only inner config has enabled=False"
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


def test_workflow_step_catalogs_include_pause_for_webhook():
    onboarding_types = {
        step["type"]: step for step in staff_handlers._ONBOARDING_STEP_CATALOG
    }
    offboarding_types = {
        step["type"]: step for step in staff_handlers._OFFBOARDING_STEP_CATALOG
    }

    assert onboarding_types["wait_for_webhook"]["name"] == "Pause For Webhook"
    assert offboarding_types["wait_for_webhook"]["name"] == "Pause For Webhook"


def test_workflow_policy_response_includes_pause_for_webhook_preview(monkeypatch):
    monkeypatch.setattr(
        staff_handlers.settings, "public_base_url", "https://portal.example.test"
    )
    monkeypatch.setattr(staff_handlers.settings, "portal_url", None)

    response = staff_handlers._normalise_workflow_policy_response(
        {
            "id": 10,
            "company_id": 7,
            "direction": "onboarding",
            "workflow_key": "starter",
            "workflow_name": "Starter",
            "config": {
                "steps": [
                    {
                        "type": "wait_for_webhook",
                        "name": "Pause For Webhook",
                        "config": {"type": "wait_for_webhook"},
                    },
                ],
            },
        }
    )

    preview = response["config_json"]["steps"][0]["webhook_preview"]
    assert preview["webhook_url"].startswith(
        "https://portal.example.test/api/staff/workflow-webhooks/"
    )
    assert len(preview["post_key"]) == 43


def test_normalise_custom_field_group_mappings_supports_dict_and_list_inputs():
    from_dict = workflows._normalise_custom_field_group_mappings(
        {
            "custom_field_group_mappings": {
                "field_one": ["group-a", "group-b"],
                "field_two": "group-c,group-d",
            }
        }
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
        lambda *_args, **_kwargs: [
            {"name": "Checkpoint", "type": "wait_external_checkpoint"}
        ],
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={88: []}),
    )
    checkpoint_mock = AsyncMock()
    update_state_mock = AsyncMock()
    monkeypatch.setattr(
        workflows.workflow_repo, "create_external_checkpoint", checkpoint_mock
    )
    monkeypatch.setattr(
        workflows.workflow_repo, "update_execution_state", update_state_mock
    )

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
async def test_execute_policy_steps_continues_when_step_failure_mode_continue(
    monkeypatch,
):
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
        AsyncMock(
            side_effect=[workflows.WorkflowStepError("rename failed"), {"hidden": True}]
        ),
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
        AsyncMock(
            side_effect=workflows.WorkflowStepError(
                "create failed", request_payload={"user": "new.user@example.com"}
            )
        ),
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
async def test_execute_policy_steps_assigns_groups_from_selected_custom_fields(
    monkeypatch,
):
    monkeypatch.setattr(
        workflows, "_normalise_workflow_steps", lambda *_args, **_kwargs: []
    )
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
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(return_value={"id": "user-123"}),
    )

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
    monkeypatch.setattr(
        workflows, "_resolve_staff_m365_user", AsyncMock(return_value={"id": "user-1"})
    )
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    graph_post = AsyncMock(return_value={})
    monkeypatch.setattr(workflows.m365_service, "_graph_post", graph_post)

    result = await workflows._execute_policy_step(
        step={
            "type": "m365_add_teams_group_member",
            "group_ids_csv": "group-a,group-b",
        },
        company_id=9,
        staff={"id": 703, "email": "new.user@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["operation"] == "add"
    assert result["group_ids"] == ["group-a", "group-b"]
    assert graph_post.await_count == 2


@pytest.mark.anyio
async def test_execute_policy_step_removes_user_from_all_teams_groups_when_ids_blank(
    monkeypatch,
):
    monkeypatch.setattr(
        workflows, "_resolve_staff_m365_user", AsyncMock(return_value={"id": "user-1"})
    )
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    async def fake_graph_get_all(_token: str, url: str):
        if "/ownedObjects/" in url:
            return []
        return [
            {"id": "group-a", "groupTypes": ["Unified"]},
            {"id": "security-group", "groupTypes": [], "securityEnabled": True},
            {"id": "group-dynamic", "groupTypes": ["Unified", "DynamicMembership"]},
            {"id": "group-b", "groupTypes": ["Unified"]},
        ]

    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_get_all",
        AsyncMock(side_effect=fake_graph_get_all),
    )
    graph_delete = AsyncMock(return_value={})
    monkeypatch.setattr(workflows.m365_service, "_graph_delete", graph_delete)

    result = await workflows._execute_policy_step(
        step={"type": "m365_remove_teams_group_member", "group_ids_csv": ""},
        company_id=9,
        staff={"id": 703, "email": "old.user@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["operation"] == "remove"
    assert result["group_ids"] == ["group-a", "group-b"]
    assert graph_delete.await_count == 2


@pytest.mark.anyio
async def test_execute_policy_step_removes_user_from_sharepoint_sites(monkeypatch):
    monkeypatch.setattr(
        workflows, "_resolve_staff_m365_user", AsyncMock(return_value={"id": "user-2"})
    )
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    async def fake_graph_get(_token: str, url: str):
        if url.endswith("/sites/site-a/permissions"):
            return {
                "value": [
                    {
                        "id": "perm-1",
                        "grantedToIdentitiesV2": [{"user": {"id": "user-2"}}],
                    }
                ]
            }
        return {"value": []}

    monkeypatch.setattr(
        workflows.m365_service, "_graph_get", AsyncMock(side_effect=fake_graph_get)
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
async def test_execute_policy_step_removes_user_from_all_sharepoint_sites_when_ids_blank(
    monkeypatch,
):
    monkeypatch.setattr(
        workflows, "_resolve_staff_m365_user", AsyncMock(return_value={"id": "user-2"})
    )
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )

    graph_get_all = AsyncMock(return_value=[{"id": "site-a"}, {"id": "site-b"}])
    monkeypatch.setattr(workflows.m365_service, "_graph_get_all", graph_get_all)

    async def fake_graph_get(_token: str, url: str):
        if url.endswith("/sites/site-a/permissions"):
            return {
                "value": [
                    {
                        "id": "perm-1",
                        "grantedToIdentitiesV2": [{"user": {"id": "user-2"}}],
                    }
                ]
            }
        return {"value": [{"id": "perm-2", "grantedToIdentitiesV2": []}]}

    monkeypatch.setattr(
        workflows.m365_service, "_graph_get", AsyncMock(side_effect=fake_graph_get)
    )
    graph_delete = AsyncMock(return_value={})
    monkeypatch.setattr(workflows.m365_service, "_graph_delete", graph_delete)

    result = await workflows._execute_policy_step(
        step={"type": "m365_remove_sharepoint_site_member", "site_ids_csv": ""},
        company_id=9,
        staff={"id": 704, "email": "offboard.user@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["operation"] == "remove"
    assert result["site_ids"] == ["site-a", "site-b"]
    graph_delete.assert_awaited_once()


@pytest.mark.anyio
async def test_remove_all_teams_groups_skips_unmanageable_and_failed_groups(
    monkeypatch,
):
    monkeypatch.setattr(
        workflows, "_resolve_staff_m365_user", AsyncMock(return_value={"id": "user-1"})
    )
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )

    async def fake_graph_get_all(_token: str, url: str):
        if "/ownedObjects/" in url:
            return [{"id": "team-owned", "groupTypes": ["Unified"]}]
        return [
            {"id": "team-a", "groupTypes": ["Unified"], "mailEnabled": True},
            {"id": "synced", "groupTypes": ["Unified"], "onPremisesSyncEnabled": True},
            {"id": "dist-list", "groupTypes": [], "mailEnabled": True},
            {"id": "gone", "groupTypes": ["Unified"]},
            {"id": "locked", "groupTypes": ["Unified"]},
        ]

    async def fake_graph_delete(_token: str, url: str):
        if "/groups/gone/" in url:
            raise workflows.M365Error("not found", http_status=404)
        if "/groups/locked/" in url or "/owners/" in url:
            raise workflows.M365Error("bad request", http_status=400)
        return None

    monkeypatch.setattr(
        workflows.m365_service, "_graph_get_all", AsyncMock(side_effect=fake_graph_get_all)
    )
    graph_delete = AsyncMock(side_effect=fake_graph_delete)
    monkeypatch.setattr(workflows.m365_service, "_graph_delete", graph_delete)

    result = await workflows._execute_policy_step(
        step={"type": "m365_remove_teams_group_member", "group_ids_csv": ""},
        company_id=9,
        staff={"id": 703, "email": "old.user@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["group_ids"] == ["team-a"]
    assert result["failed_groups"] == [{"group_id": "locked", "http_status": 400}]
    skipped = {item["group_id"]: item["reason"] for item in result["skipped_groups"]}
    assert skipped == {
        "synced": "not_manageable",
        "gone": "not_a_member",
        "team-owned": "owner_not_removed",
    }
    deleted_urls = [call.args[1] for call in graph_delete.await_args_list]
    assert not any("/groups/synced/" in url or "/groups/dist-list/" in url for url in deleted_urls)


@pytest.mark.anyio
async def test_remove_explicit_teams_group_failure_raises(monkeypatch):
    monkeypatch.setattr(
        workflows, "_resolve_staff_m365_user", AsyncMock(return_value={"id": "user-1"})
    )
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_delete",
        AsyncMock(side_effect=workflows.M365Error("forbidden", http_status=403)),
    )

    with pytest.raises(workflows.WorkflowStepError, match="group-a"):
        await workflows._execute_policy_step(
            step={"type": "m365_remove_teams_group_member", "group_ids_csv": "group-a"},
            company_id=9,
            staff={"id": 703, "email": "old.user@example.com"},
            policy_config={},
            vars_map={},
        )


@pytest.mark.anyio
async def test_remove_all_sharepoint_sites_tolerates_unreadable_sites(monkeypatch):
    monkeypatch.setattr(
        workflows, "_resolve_staff_m365_user", AsyncMock(return_value={"id": "user-2"})
    )
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_get_all",
        AsyncMock(
            return_value=[
                {"id": "site-a", "webUrl": "https://contoso.sharepoint.com/sites/a"},
                {"id": "site-locked", "webUrl": "https://contoso.sharepoint.com/sites/b"},
                {"id": "onedrive", "webUrl": "https://contoso-my.sharepoint.com/personal/x"},
            ]
        ),
    )

    async def fake_graph_get(_token: str, url: str):
        if "/sites/site-locked/" in url or "/sites/onedrive/" in url:
            raise workflows.M365Error("forbidden", http_status=403)
        if url.endswith("/sites/site-a/permissions"):
            raise workflows.M365Error("forbidden", http_status=403)
        return {
            "value": [
                {"id": "direct", "grantedToV2": {"user": {"id": "user-2"}}},
                {
                    "id": "inherited",
                    "grantedToV2": {"user": {"id": "user-2"}},
                    "inheritedFrom": {"id": "parent"},
                },
                {"id": "members", "grantedToV2": {"siteGroup": {"id": "3"}}},
            ]
        }

    monkeypatch.setattr(
        workflows.m365_service, "_graph_get", AsyncMock(side_effect=fake_graph_get)
    )
    graph_delete = AsyncMock(return_value=None)
    monkeypatch.setattr(workflows.m365_service, "_graph_delete", graph_delete)

    result = await workflows._execute_policy_step(
        step={"type": "m365_remove_sharepoint_site_member", "site_ids_csv": ""},
        company_id=9,
        staff={"id": 704, "email": "offboard.user@example.com"},
        policy_config={},
        vars_map={},
    )

    assert result["site_ids"] == ["site-a"]
    assert result["failed_sites"] == [{"site_id": "site-locked", "http_status": 403}]
    graph_delete.assert_awaited_once()
    assert graph_delete.await_args.args[1].endswith(
        "/sites/site-a/drive/root/permissions/direct"
    )


def _patch_distribution_group_env(monkeypatch, *, memberships, contacts=None, contact_groups=None):
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(
            return_value={
                "id": "user-3",
                "mail": "leaver@example.com",
                "userPrincipalName": "leaver@example.com",
            }
        ),
    )
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        workflows.m365_service, "_graph_get_all", AsyncMock(return_value=memberships)
    )
    graph_delete = AsyncMock(return_value=None)
    monkeypatch.setattr(workflows.m365_service, "_graph_delete", graph_delete)
    session = AsyncMock(return_value=("exo-token", "tenant"))
    monkeypatch.setattr(workflows.m365_service, "acquire_exchange_session", session)
    find_contacts = AsyncMock(return_value=contacts or [])
    monkeypatch.setattr(
        workflows.m365_service, "find_mail_contacts_for_addresses", find_contacts
    )
    monkeypatch.setattr(
        workflows.m365_service,
        "list_distribution_groups_with_member",
        AsyncMock(return_value=contact_groups or []),
    )
    remove_member = AsyncMock(return_value=None)
    monkeypatch.setattr(
        workflows.m365_service, "remove_distribution_group_member", remove_member
    )
    return graph_delete, remove_member, find_contacts, session


_DISTRIBUTION_MEMBERSHIPS = [
    {"id": "team", "groupTypes": ["Unified"], "mailEnabled": True, "securityEnabled": False},
    {"id": "dl", "displayName": "All Staff", "mail": "all@example.com", "groupTypes": [], "mailEnabled": True, "securityEnabled": False},
    {"id": "mesg", "displayName": "Finance", "mail": "finance@example.com", "groupTypes": [], "mailEnabled": True, "securityEnabled": True},
    {"id": "sec", "displayName": "VPN Users", "groupTypes": [], "mailEnabled": False, "securityEnabled": True},
    {"id": "synced", "displayName": "AD Group", "groupTypes": [], "mailEnabled": False, "securityEnabled": True, "onPremisesSyncEnabled": True},
    {"id": "dyn", "displayName": "Dynamic", "groupTypes": ["DynamicMembership"], "mailEnabled": False, "securityEnabled": True},
]


@pytest.mark.anyio
async def test_remove_distribution_groups_handles_lists_security_groups_and_contacts(
    monkeypatch,
):
    graph_delete, remove_member, find_contacts, session = _patch_distribution_group_env(
        monkeypatch,
        memberships=_DISTRIBUTION_MEMBERSHIPS,
        contacts=[
            {"distinguished_name": "CN=Leaver Contact", "display_name": "Leaver (personal)", "email": "leaver@example.com"}
        ],
        contact_groups=[
            {"identity": "suppliers@example.com", "display_name": "Suppliers", "synced": False},
            {"identity": "onprem@example.com", "display_name": "On-prem list", "synced": True},
        ],
    )

    result = await workflows._execute_policy_step(
        step={"type": "m365_remove_distribution_groups"},
        company_id=9,
        staff={"id": 705, "email": "leaver@example.com"},
        policy_config={},
        vars_map={},
    )

    removed = {(item["group_id"], item["kind"]) for item in result["removed"]}
    assert removed == {
        ("dl", "distribution_list"),
        ("mesg", "security_group"),
        ("sec", "security_group"),
        ("suppliers@example.com", "contact_membership"),
    }
    skipped = {item["group_id"]: item["reason"] for item in result["skipped"]}
    assert skipped == {
        "synced": "managed_on_premises",
        "dyn": "dynamic_membership",
        "onprem@example.com": "managed_on_premises",
    }
    assert result["groups_removed"] == 3
    assert result["contact_memberships_removed"] == 1
    graph_delete.assert_awaited_once()
    assert "/groups/sec/members/user-3/$ref" in graph_delete.await_args.args[1]
    exo_calls = {(call.args[1], call.args[2]) for call in remove_member.await_args_list}
    assert exo_calls == {
        ("all@example.com", "leaver@example.com"),
        ("finance@example.com", "leaver@example.com"),
        ("suppliers@example.com", "CN=Leaver Contact"),
    }
    assert set(find_contacts.await_args.args[1]) == {"leaver@example.com"}
    session.assert_awaited_once()


@pytest.mark.anyio
async def test_remove_distribution_groups_respects_disabled_options(monkeypatch):
    graph_delete, remove_member, find_contacts, session = _patch_distribution_group_env(
        monkeypatch, memberships=_DISTRIBUTION_MEMBERSHIPS
    )

    result = await workflows._execute_policy_step(
        step={
            "type": "m365_remove_distribution_groups",
            "remove_distribution_lists": True,
            "remove_security_groups": False,
            "remove_mail_contacts": "false",
        },
        company_id=9,
        staff={"id": 705, "email": "leaver@example.com"},
        policy_config={},
        vars_map={},
    )

    assert [item["group_id"] for item in result["removed"]] == ["dl"]
    graph_delete.assert_not_awaited()
    find_contacts.assert_not_awaited()


@pytest.mark.anyio
async def test_remove_distribution_groups_reports_partial_failures(monkeypatch):
    _graph_delete, remove_member, _find, _session = _patch_distribution_group_env(
        monkeypatch, memberships=_DISTRIBUTION_MEMBERSHIPS
    )
    remove_member.side_effect = workflows.M365Error("forbidden", http_status=403)

    result = await workflows._execute_policy_step(
        step={"type": "m365_remove_distribution_groups"},
        company_id=9,
        staff={"id": 705, "email": "leaver@example.com"},
        policy_config={},
        vars_map={},
    )

    assert [item["group_id"] for item in result["removed"]] == ["sec"]
    assert {item["group_id"] for item in result["failed"]} == {"dl", "mesg"}


@pytest.mark.anyio
async def test_remove_distribution_groups_fails_when_nothing_could_be_removed(
    monkeypatch,
):
    graph_delete, remove_member, find_contacts, _session = _patch_distribution_group_env(
        monkeypatch, memberships=_DISTRIBUTION_MEMBERSHIPS[:3]
    )
    remove_member.side_effect = workflows.M365Error("forbidden", http_status=403)
    find_contacts.side_effect = workflows.M365Error("forbidden", http_status=403)

    with pytest.raises(workflows.WorkflowStepError, match="All Staff"):
        await workflows._execute_policy_step(
            step={"type": "m365_remove_distribution_groups"},
            company_id=9,
            staff={"id": 705, "email": "leaver@example.com"},
            policy_config={},
            vars_map={},
        )


@pytest.mark.anyio
async def test_find_mail_contacts_matches_external_address(monkeypatch):
    monkeypatch.setattr(
        workflows.m365_service,
        "_exo_invoke_command",
        AsyncMock(
            return_value={
                "value": [
                    {"DistinguishedName": "CN=Match", "DisplayName": "Leaver", "ExternalEmailAddress": "SMTP:Leaver@Example.com"},
                    {"DistinguishedName": "CN=Other", "ExternalEmailAddress": "SMTP:someone@else.com", "EmailAddresses": ["X500:/o=leaver@example.com"]},
                ]
            }
        ),
    )

    contacts = await workflows.m365_service.find_mail_contacts_for_addresses(
        9, ["leaver@example.com"], exo_session=("token", "tenant")
    )

    assert contacts == [
        {"distinguished_name": "CN=Match", "display_name": "Leaver", "email": "leaver@example.com"}
    ]


@pytest.mark.anyio
async def test_list_distribution_groups_with_member_escapes_filter(monkeypatch):
    invoke = AsyncMock(return_value={"value": {"PrimarySmtpAddress": "list@example.com", "IsDirSynced": False}})
    monkeypatch.setattr(workflows.m365_service, "_exo_invoke_command", invoke)

    groups = await workflows.m365_service.list_distribution_groups_with_member(
        9, "CN=O'Brien,OU=Contacts", exo_session=("token", "tenant")
    )

    assert groups[0]["identity"] == "list@example.com"
    assert invoke.await_args.args[3]["Filter"] == "Members -eq 'CN=O''Brien,OU=Contacts'"


@pytest.mark.anyio
async def test_run_offboarding_step_removes_calendar_events_when_enabled(monkeypatch):
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(
            return_value={
                "id": "user-2",
                "userPrincipalName": "offboard.user@example.com",
            }
        ),
    )
    remove_calendar_events_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        workflows.m365_service, "remove_calendar_events", remove_calendar_events_mock
    )

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
async def test_run_offboarding_step_skips_calendar_event_removal_when_disabled(
    monkeypatch,
):
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(
            return_value={
                "id": "user-2",
                "userPrincipalName": "offboard.user@example.com",
            }
        ),
    )
    remove_calendar_events_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        workflows.m365_service, "remove_calendar_events", remove_calendar_events_mock
    )

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
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(
            return_value={
                "id": "user-2",
                "userPrincipalName": "offboard.user@example.com",
            }
        ),
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
async def test_run_offboarding_step_continues_when_mailbox_rule_patch_forbidden(
    monkeypatch,
):
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(
            return_value={
                "id": "user-2",
                "userPrincipalName": "offboard.user@example.com",
            }
        ),
    )
    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_get_all",
        AsyncMock(return_value=[{"id": "rule-1", "isEnabled": True}]),
    )
    monkeypatch.setattr(
        workflows,
        "_graph_patch",
        AsyncMock(
            side_effect=workflows.WorkflowStepError("Forbidden", http_status=403)
        ),
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
async def test_run_offboarding_step_continues_when_mailbox_rules_list_forbidden(
    monkeypatch,
):
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(
            return_value={
                "id": "user-2",
                "userPrincipalName": "offboard.user@example.com",
            }
        ),
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
async def test_run_offboarding_step_skips_mailbox_rule_disable_when_mailbox_missing(
    monkeypatch,
):
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(
            return_value={
                "id": "user-2",
                "userPrincipalName": "offboard.user@example.com",
            }
        ),
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

    with pytest.raises(
        workflows.WorkflowStepError, match="staff email is not available"
    ):
        await workflows._execute_policy_step(
            step={"type": "disable_myportal_account"},
            company_id=9,
            staff={"id": 801},
            policy_config={},
            vars_map={},
        )

    get_user.assert_not_awaited()


@pytest.mark.anyio
async def test_run_offboarding_step_converts_mailbox_before_license_removal(
    monkeypatch,
):
    monkeypatch.setattr(
        workflows.m365_service, "acquire_access_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        workflows,
        "_resolve_staff_m365_user",
        AsyncMock(
            return_value={
                "id": "user-2",
                "userPrincipalName": "offboard.user@example.com",
            }
        ),
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

    async def _graph_post(
        _token: str, url: str, payload: dict[str, object]
    ) -> dict[str, object]:
        call_order.append("remove_licenses")
        assert url.endswith("/users/user-2/assignLicense")
        assert payload == {"addLicenses": [], "removeLicenses": ["sku-1"]}
        return {}

    monkeypatch.setattr(
        workflows.m365_service,
        "convert_mailbox_to_shared",
        AsyncMock(side_effect=_convert),
    )
    monkeypatch.setattr(
        workflows.m365_service, "_graph_get", AsyncMock(side_effect=_graph_get)
    )
    monkeypatch.setattr(
        workflows.m365_service, "_graph_post", AsyncMock(side_effect=_graph_post)
    )

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


@pytest.mark.anyio
async def test_confirm_webhook_checkpoint_marks_wait_step_success_before_resume(
    monkeypatch,
):
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_pending_external_checkpoint_by_webhook_id",
        AsyncMock(
            return_value={
                "id": 12,
                "company_id": 9,
                "staff_id": 1001,
                "execution_id": 88,
                "webhook_post_key_hash": workflows.hash_api_key("secret-post-key"),
            }
        ),
    )
    monkeypatch.setattr(
        workflows.workflow_repo, "confirm_external_checkpoint", AsyncMock()
    )
    monkeypatch.setattr(workflows.audit_service, "log_action", AsyncMock())
    monkeypatch.setattr(
        workflows.workflow_repo,
        "get_execution_by_id",
        AsyncMock(return_value={"id": 88, "current_step": "2:Pause for webhook"}),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={88: []}),
    )
    append_log = AsyncMock()
    monkeypatch.setattr(workflows.workflow_repo, "append_step_log", append_log)
    resume = AsyncMock(
        return_value={"state": workflows.STATE_COMPLETED, "execution_id": 88}
    )
    monkeypatch.setattr(
        workflows,
        "resume_staff_onboarding_workflow_after_external_confirmation",
        resume,
    )

    result = await workflows.confirm_webhook_checkpoint_and_resume(
        webhook_public_id="public-id",
        post_key="secret-post-key",
        source="test-webhook",
        callback_payload={"ok": True},
    )

    assert result["state"] == workflows.STATE_COMPLETED
    append_log.assert_awaited_once()
    assert append_log.await_args.kwargs["execution_id"] == 88
    assert append_log.await_args.kwargs["step_name"] == "Pause for webhook"
    assert append_log.await_args.kwargs["status"] == "success"
    resume.assert_awaited_once()


@pytest.mark.anyio
async def test_create_ticket_step_uses_requester_and_returns_ticket_number(monkeypatch):
    monkeypatch.setattr(
        workflows.tickets_service,
        "resolve_status_or_default",
        AsyncMock(return_value="open"),
    )
    create_ticket = AsyncMock(return_value={"id": 123, "ticket_number": "HD-123"})
    monkeypatch.setattr(workflows.tickets_service, "create_ticket", create_ticket)

    result = await workflows._execute_policy_step(
        step={
            "type": "create_ticket",
            "subject": "Onboard ${vars.staff.full_name}",
            "description": "Started",
            "priority": "high",
            "assigned_user_id": "44",
        },
        company_id=9,
        staff={"id": 5, "requested_by_user_id": 17},
        policy_config={},
        vars_map={"staff.full_name": "Jane Starter"},
        step_name="Create Ticket",
    )

    assert result == {"ticket_id": 123, "ticket_number": "HD-123"}
    create_ticket.assert_awaited_once()
    assert create_ticket.await_args.kwargs["requester_id"] == 17
    assert create_ticket.await_args.kwargs["initial_reply_author_id"] == 17
    assert create_ticket.await_args.kwargs["assigned_user_id"] == 44


@pytest.mark.anyio
async def test_update_and_reply_ticket_steps_use_created_ticket_variable(monkeypatch):
    ticket = {
        "id": 123,
        "ticket_number": "HD-123",
        "assigned_user_id": 44,
    }
    monkeypatch.setattr(
        workflows.tickets_repo,
        "get_ticket_by_number_or_id",
        AsyncMock(return_value=ticket),
    )
    monkeypatch.setattr(
        workflows.tickets_service,
        "resolve_status_or_default",
        AsyncMock(return_value="in_progress"),
    )
    update_ticket = AsyncMock(return_value={**ticket, "status": "in_progress"})
    monkeypatch.setattr(workflows.tickets_repo, "update_ticket", update_ticket)
    emit_updated = AsyncMock()
    monkeypatch.setattr(
        workflows.tickets_service, "emit_ticket_updated_event", emit_updated
    )
    create_reply = AsyncMock(return_value={"id": 77, "is_internal": True})
    monkeypatch.setattr(workflows.tickets_repo, "create_reply", create_reply)
    monkeypatch.setattr(
        workflows.user_repo,
        "get_user_by_id",
        AsyncMock(return_value={"id": 44, "email": "tech@example.com"}),
    )
    emit_replied = AsyncMock()
    monkeypatch.setattr(
        workflows.tickets_service, "emit_ticket_replied_event", emit_replied
    )

    update_result = await workflows._execute_policy_step(
        step={
            "type": "update_ticket",
            "ticket_number": "${vars.ticket_number}",
            "status": "In Progress",
            "priority": "high",
        },
        company_id=9,
        staff={"id": 5},
        policy_config={},
        vars_map={"ticket_number": "HD-123"},
    )
    reply_result = await workflows._execute_policy_step(
        step={
            "type": "reply_ticket",
            "ticket_number": "${vars.ticket_number}",
            "body": "Done",
            "is_internal": True,
        },
        company_id=9,
        staff={"id": 5},
        policy_config={},
        vars_map={"ticket_number": "HD-123"},
    )

    assert update_result["updated"] == {"status": "in_progress", "priority": "high"}
    update_ticket.assert_awaited_once_with(123, status="in_progress", priority="high")
    assert reply_result == {
        "ticket_id": 123,
        "ticket_number": "HD-123",
        "reply_id": 77,
        "is_internal": True,
    }
    reply_kwargs = create_reply.await_args.kwargs
    assert reply_kwargs["ticket_id"] == 123
    assert reply_kwargs["author_id"] == 44
    assert reply_kwargs["body"] == "Done"
    assert reply_kwargs["is_internal"] is True
    assert reply_kwargs["author_display_name"] is None


@pytest.mark.anyio
async def test_reply_ticket_step_is_authored_by_approving_technician(monkeypatch):
    ticket = {
        "id": 123,
        "ticket_number": "HD-123",
        "requester_id": 17,
        "assigned_user_id": 44,
    }
    monkeypatch.setattr(
        workflows.tickets_repo,
        "get_ticket_by_number_or_id",
        AsyncMock(return_value=ticket),
    )
    approver = {"id": 61, "email": "approver@example.com"}
    monkeypatch.setattr(
        workflows.user_repo, "get_user_by_id", AsyncMock(return_value=approver)
    )
    create_reply = AsyncMock(return_value={"id": 78, "is_internal": False})
    monkeypatch.setattr(workflows.tickets_repo, "create_reply", create_reply)
    emit_replied = AsyncMock()
    emit_updated = AsyncMock()
    monkeypatch.setattr(
        workflows.tickets_service, "emit_ticket_replied_event", emit_replied
    )
    monkeypatch.setattr(
        workflows.tickets_service, "emit_ticket_updated_event", emit_updated
    )

    await workflows._execute_policy_step(
        step={
            "type": "reply_ticket",
            "ticket_number": "${vars.ticket_number}",
            "body": "Username / Email: ${vars.staff.email}\n\nPassword Link: ${vars.link}",
        },
        company_id=9,
        staff={"id": 5, "requested_by_user_id": 17, "approved_by_user_id": 61},
        policy_config={},
        vars_map={
            "ticket_number": "HD-123",
            "staff.email": "jane@example.com",
            "link": "https://pw.example.com/p/abc",
        },
    )

    kwargs = create_reply.await_args.kwargs
    assert kwargs["author_id"] == 61
    assert kwargs["author_display_name"] is None
    assert kwargs["is_internal"] is False
    assert kwargs["external_reference"].startswith(
        workflows.tickets_repo.WORKFLOW_TECHNICIAN_REPLY_REFERENCE_PREFIX
    )
    # Plain-text bodies keep their line breaks once rendered as HTML email.
    assert kwargs["body"] == (
        "Username / Email: jane@example.com<br /><br />"
        "Password Link: https://pw.example.com/p/abc"
    )
    for emitted in (emit_replied, emit_updated):
        assert emitted.await_args.kwargs["actor_type"] == "technician"
        assert emitted.await_args.kwargs["actor"] == approver


@pytest.mark.anyio
async def test_create_user_step_exposes_generated_upn_as_staff_email(monkeypatch):
    monkeypatch.setattr(
        workflows.m365_service,
        "acquire_access_token",
        AsyncMock(return_value="token"),
    )
    monkeypatch.setattr(
        workflows.m365_service,
        "_graph_post",
        AsyncMock(return_value={"id": "new-user-id"}),
    )
    vars_map = {
        "staff.first_name": "john",
        "staff.last_name": "smith",
        "staff.email": None,
    }

    result = await workflows._execute_policy_step(
        step={
            "type": "create_user",
            "user_principal_name": "${vars.staff.first_name}.${vars.staff.last_name}@example.com",
        },
        company_id=9,
        staff={"id": 5, "first_name": "John", "last_name": "Smith", "email": None},
        policy_config={},
        vars_map=vars_map,
    )

    assert result["staff.email"] == "john.smith@example.com"
    assert result["staff.email_local_part"] == "john.smith"
    assert vars_map["staff.email"] == "john.smith@example.com"
    assert workflows._resolve_template_value(
        "Username / Email: ${vars.staff.email}", vars_map=vars_map
    ) == "Username / Email: john.smith@example.com"
