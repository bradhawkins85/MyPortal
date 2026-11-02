"""Tests for the staff onboarding/offboarding workflow designer metadata."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.features.staff import handlers
from app.services import staff_onboarding_workflows as workflows


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _all_catalog_types() -> set[str]:
    return {
        item["type"]
        for item in [*handlers._ONBOARDING_STEP_CATALOG, *handlers._OFFBOARDING_STEP_CATALOG]
    }


def test_every_catalog_step_has_designer_metadata():
    missing = _all_catalog_types() - set(handlers._WORKFLOW_STEP_DESIGNER_META)
    assert not missing, f"Add designer metadata for: {sorted(missing)}"

    category_keys = {item["key"] for item in handlers._WORKFLOW_STEP_CATEGORIES}
    for step_type, meta in handlers._WORKFLOW_STEP_DESIGNER_META.items():
        assert meta["category"] in category_keys, step_type


def test_designer_catalog_includes_category_and_outputs():
    catalog = {item["type"]: item for item in handlers._designer_step_catalog(handlers._ONBOARDING_STEP_CATALOG)}

    ticket = catalog["create_ticket"]
    assert ticket["category"] == "tickets"
    assert {output["name"] for output in ticket["outputs"]} == {"ticket_id", "ticket_number"}

    password = catalog["generate_password"]
    assert password["output_fields"] == [
        {"field": "output_var", "label": "Generated password", "secret": True}
    ]
    assert password["outputs"][0]["secret"] is True

    # The shared module-level catalog must not be mutated.
    assert "outputs" not in handlers._ONBOARDING_STEP_CATALOG[0]


def test_designer_form_schema_adds_widgets_and_normalises_options():
    schema = handlers._designer_step_form_schema()

    base_fields = {field["name"]: field for field in schema["*"]["fields"]}
    assert base_fields["conditions.department_in"]["widget"] == "tags"

    http_fields = {field["name"]: field for field in schema["http_post"]["fields"]}
    assert http_fields["store_json"]["widget"] == "mapping"
    assert http_fields["json_body"]["widget"] == "json"

    ticket_fields = {field["name"]: field for field in schema["create_ticket"]["fields"]}
    assert ticket_fields["priority"]["options"][0] == {"value": "low", "label": "Low"}

    update_fields = {field["name"]: field for field in schema["update_ticket"]["fields"]}
    assert update_fields["priority"]["options"][0] == {"value": "", "label": ""}

    # Raw schema keeps its original shape for the legacy policy endpoints.
    assert handlers._WORKFLOW_STEP_FORM_SCHEMA["create_ticket"]["fields"][2]["options"][0] == "low"
    assert "widget" not in handlers._WORKFLOW_STEP_FORM_SCHEMA["*"]["fields"][0]


def test_designer_schema_has_no_unresolvable_default_variables():
    """Shipped defaults should only reference variables the engine provides."""

    base_names = {
        "staff.first_name", "staff.last_name", "staff.full_name", "staff.email",
        "staff.email_local_part", "staff.job_title", "staff.department",
        "staff.office_location", "staff.mobile_phone", "staff.company_name",
        "requestor_email",
        "company_name",
        # Outputs of earlier steps the defaults are designed to follow.
        "ticket_number", "generated_password", "credential_id",
        # Backward-compatible engine aliases.
        "staff_id", "staff_email", "staff_first_name", "staff_last_name",
        "staff_full_name", "company_id",
    }
    pattern = workflows._VAR_PATTERN
    unresolved = [
        f"{step_type}.{field['name']} uses ${{vars.{name}}}"
        for step_type, schema in handlers._WORKFLOW_STEP_FORM_SCHEMA.items()
        for field in schema.get("fields", [])
        if isinstance(field.get("default"), str)
        for name in pattern.findall(field["default"])
        if name not in base_names
    ]
    assert not unresolved, unresolved


def test_variable_catalog_lists_custom_fields_and_offboarding_values(monkeypatch):
    monkeypatch.setattr(
        handlers.staff_custom_fields_repo,
        "list_field_definitions",
        AsyncMock(
            return_value=[
                {"name": "needs_laptop", "display_name": "Needs laptop", "field_type": "checkbox"},
                {"name": "shirt_size", "display_name": None, "field_type": "select"},
            ]
        ),
    )

    groups = asyncio.run(
        handlers._build_workflow_variable_catalog(
            9, direction="offboarding", company={"name": "Acme"}
        )
    )
    by_key = {group["key"]: group for group in groups}

    assert "offboarding" in by_key
    custom = {item["name"]: item for item in by_key["custom_fields"]["variables"]}
    assert custom["custom_fields.needs_laptop"]["label"] == "Needs laptop"
    assert custom["custom_fields.needs_laptop"]["field_type"] == "checkbox"
    assert custom["custom_fields.shirt_size"]["label"] == "shirt_size"
    staff_vars = {item["name"]: item for item in by_key["staff"]["variables"]}
    assert staff_vars["staff.company_name"]["sample"] == "Acme"


def test_variable_catalog_tolerates_custom_field_errors(monkeypatch):
    monkeypatch.setattr(
        handlers.staff_custom_fields_repo,
        "list_field_definitions",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    groups = asyncio.run(handlers._build_workflow_variable_catalog(9, direction="onboarding"))

    keys = [group["key"] for group in groups]
    assert "custom_fields" not in keys
    assert "offboarding" not in keys
    assert keys[0] == "staff"


def test_list_policies_returns_designer_payload(monkeypatch):
    async def fake_load_staff_context(request, **kwargs):
        return ({"id": 1, "is_super_admin": True}, None, {"name": "Acme"}, 3, 9, None)

    monkeypatch.setattr(handlers, "_load_staff_context", fake_load_staff_context)
    monkeypatch.setattr(
        handlers.staff_workflow_repo,
        "list_company_workflow_policies",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        handlers.staff_custom_fields_repo,
        "list_field_definitions",
        AsyncMock(return_value=[]),
    )

    monkeypatch.setattr(
        handlers.company_repo,
        "list_companies",
        AsyncMock(return_value=[{"id": 4, "name": "Beta"}, {"id": 9, "name": "Acme"}]),
    )

    response = asyncio.run(
        handlers.list_staff_workflow_policies("offboarding", SimpleNamespace(query_params={}))
    )
    payload = json.loads(response.body)

    assert payload["policies"] == []
    assert payload["company_id"] == 9
    assert [c["id"] for c in payload["companies"]] == [9, 4]
    assert payload["companies"][0]["is_current"] is True
    types = {item["type"] for item in payload["step_catalog"]}
    assert "offboard_account" in types
    assert all("category" in item for item in payload["step_catalog"])
    assert payload["step_categories"][0]["key"] == "m365"
    assert any(group["key"] == "offboarding" for group in payload["variables"])
    assert "widget" in payload["step_form_schema"]["*"]["fields"][0]


@pytest.mark.anyio
async def test_engine_provides_staff_company_name(monkeypatch):
    monkeypatch.setattr(
        workflows.staff_custom_fields_repo,
        "get_all_staff_field_values",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        workflows.company_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 9, "name": "Acme"}),
    )
    monkeypatch.setattr(
        workflows,
        "_normalise_workflow_steps",
        lambda *_args, **_kwargs: [
            {"name": "Ticket", "type": "create_ticket", "subject": "Welcome to ${vars.staff.company_name}"}
        ],
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={5: []}),
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_external_checkpoints_for_execution_ids",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(workflows.workflow_repo, "append_step_log", AsyncMock())
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", AsyncMock())
    attempt_mock = AsyncMock(return_value={"ticket_id": 1, "ticket_number": 1})
    monkeypatch.setattr(workflows, "_attempt_step", attempt_mock)

    await workflows._execute_policy_steps(
        execution_id=5,
        company_id=9,
        staff={"id": 700, "email": "new@example.com", "first_name": "New"},
        direction=workflows.DIRECTION_ONBOARDING,
        policy_config={},
        max_retries=0,
        waiting_external_state=workflows.STATE_WAITING_EXTERNAL,
    )

    request_payload = attempt_mock.await_args.kwargs["request_payload"]
    assert request_payload["subject"] == "Welcome to Acme"


class _JsonRequest(SimpleNamespace):
    def __init__(self, body):
        super().__init__(query_params={}, headers={})
        self._body = body

    async def json(self):
        return self._body


_SOURCE_POLICY = {
    "id": 5,
    "company_id": 9,
    "direction": "onboarding",
    "workflow_key": "staff_onboarding_standard",
    "workflow_name": "Standard",
    "delay_type": "scheduled",
    "is_enabled": True,
    "max_retries": 2,
    "sort_order": 0,
    "config": {"version": 1, "steps": [{"key": "create_ticket", "name": "Ticket", "config": {"type": "create_ticket"}}]},
}


def _patch_copy(monkeypatch, *, source=_SOURCE_POLICY, target=None, clash=None, existing=None):
    async def fake_load_staff_context(request, **kwargs):
        assert kwargs.get("require_super_admin") is True
        return ({"id": 1, "is_super_admin": True}, None, {"name": "Acme"}, 3, 9, None)

    monkeypatch.setattr(handlers, "_load_staff_context", fake_load_staff_context)
    get_by_id = AsyncMock(return_value=source)
    monkeypatch.setattr(handlers.staff_workflow_repo, "get_company_workflow_policy_by_id", get_by_id)
    monkeypatch.setattr(
        handlers.company_repo,
        "get_company_by_id",
        AsyncMock(return_value=target if target is not None else {"id": 4, "name": "Beta"}),
    )
    monkeypatch.setattr(
        handlers.staff_workflow_repo, "get_company_workflow_policy_by_key", AsyncMock(return_value=clash)
    )
    monkeypatch.setattr(
        handlers.staff_workflow_repo,
        "list_company_workflow_policies",
        AsyncMock(return_value=existing or []),
    )

    async def fake_upsert(**kwargs):
        return {
            "id": 77,
            "company_id": kwargs["company_id"],
            "direction": kwargs["direction"],
            "workflow_key": kwargs["workflow_key"],
            "workflow_name": kwargs["workflow_name"],
            "delay_type": kwargs["delay_type"],
            "is_enabled": kwargs["is_enabled"],
            "max_retries": kwargs["max_retries"],
            "sort_order": kwargs["sort_order"],
            "config": kwargs["config"],
        }

    upsert = AsyncMock(side_effect=fake_upsert)
    monkeypatch.setattr(handlers.staff_workflow_repo, "upsert_company_workflow_policy", upsert)
    audit = AsyncMock()
    monkeypatch.setattr(handlers.audit_service, "log_action", audit)
    return get_by_id, upsert, audit


def test_copy_workflow_to_another_company(monkeypatch):
    get_by_id, upsert, audit = _patch_copy(
        monkeypatch, existing=[{"sort_order": 0}, {"sort_order": 3}]
    )
    request = _JsonRequest(
        {
            "target_company_id": 4,
            "workflow_name": "Standard (copy)",
            "workflow_key": "staff_onboarding_standard",
            "enabled": False,
            "max_retries": 1,
            "delay_type": "immediate",
            "config": {"steps": [{"key": "evil", "name": "Ignored"}]},
        }
    )

    response = asyncio.run(handlers.copy_staff_workflow_policy_to_company("onboarding", 5, request))
    payload = json.loads(response.body)

    assert response.status_code == 201
    get_by_id.assert_awaited_once_with(5, 9)
    kwargs = upsert.await_args.kwargs
    assert kwargs["company_id"] == 4
    assert kwargs["direction"] == "onboarding"
    assert kwargs["is_enabled"] is False
    assert kwargs["delay_type"] == "immediate"
    assert kwargs["sort_order"] == 4
    assert [step["name"] for step in kwargs["config"]["steps"]] == ["Ticket"]
    assert payload["policy"]["company_id"] == 4
    assert payload["company"] == {"id": 4, "name": "Beta"}
    actions = [call.kwargs["action"] for call in audit.await_args_list]
    assert actions == [
        "staff.workflows.onboarding.policy.copied_out",
        "staff.workflows.onboarding.policy.copied_in",
    ]
    assert [call.kwargs["entity_id"] for call in audit.await_args_list] == [9, 4]


def test_copy_workflow_rejects_key_clash_in_target(monkeypatch):
    _, upsert, _ = _patch_copy(monkeypatch, clash={"id": 12})
    request = _JsonRequest({"target_company_id": 4, "workflow_name": "Standard", "workflow_key": "staff_onboarding_standard"})

    with pytest.raises(handlers.HTTPException) as excinfo:
        asyncio.run(handlers.copy_staff_workflow_policy_to_company("onboarding", 5, request))

    assert excinfo.value.status_code == 409
    upsert.assert_not_awaited()


@pytest.mark.parametrize(
    "body, source, target, expected",
    [
        ({"target_company_id": 9, "workflow_name": "Copy"}, _SOURCE_POLICY, None, 400),
        ({"target_company_id": 4, "workflow_name": "Copy"}, None, None, 404),
        ({"target_company_id": 4, "workflow_name": "Copy"}, {**_SOURCE_POLICY, "direction": "offboarding"}, None, 404),
        ({"target_company_id": 4, "workflow_name": "Copy"}, _SOURCE_POLICY, {"id": 4, "name": "Beta", "archived": 1}, 404),
        ({"workflow_name": "Copy"}, _SOURCE_POLICY, None, 400),
    ],
)
def test_copy_workflow_rejects_invalid_requests(monkeypatch, body, source, target, expected):
    _, upsert, _ = _patch_copy(monkeypatch, source=source, target=target)

    with pytest.raises(handlers.HTTPException) as excinfo:
        asyncio.run(handlers.copy_staff_workflow_policy_to_company("onboarding", 5, _JsonRequest(body)))

    assert excinfo.value.status_code == expected
    upsert.assert_not_awaited()
