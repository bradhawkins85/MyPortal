"""Workflow execution history: secret handling and reusable variables."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.features.staff import handlers
from app.services import staff_onboarding_workflows as workflows


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_redact_payload_scrubs_embedded_secret_values():
    payload = {
        "type": "reply_ticket",
        "body": "AD account ready. Password: Plain!Pass1 (change it)",
        "nested": [{"note": "again Plain!Pass1"}],
        "ad_password": "Plain!Pass1",
    }

    redacted = workflows._redact_payload(
        payload, secret_vars={"ad_password"}, secret_values=("Plain!Pass1",)
    )

    assert redacted["body"] == "AD account ready. Password: ***redacted*** (change it)"
    assert redacted["nested"][0]["note"] == "again ***redacted***"
    assert redacted["ad_password"] == "***redacted***"
    assert "Plain!Pass1" not in json.dumps(redacted)


def test_secret_value_texts_skip_short_and_structured_values():
    texts = workflows._secret_value_texts(
        {"a": "abc", "b": "longer-secret", "c": {"x": 1}, "d": True, "e": "abcd"},
        {"a", "b", "c", "d", "e", "missing"},
    )

    assert texts == ("longer-secret", "abcd")


@pytest.mark.anyio
async def test_webhook_secret_reaches_later_step_but_not_its_log(monkeypatch):
    stored = {
        "values": {"ad_username": "anjana.anjanappa", "ad_status": "pass"},
        "secret_values_encrypted": {
            "ad_password": workflows.encrypt_secret("Plain!Pass1"),
            "credential_link": workflows.encrypt_secret("https://vault.example/s/abc123"),
        },
    }
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
        workflows.workflow_repo,
        "list_external_checkpoints_for_execution_ids",
        AsyncMock(
            return_value={
                5: [{"status": "confirmed", "callback_payload_json": json.dumps(stored)}]
            }
        ),
    )
    monkeypatch.setattr(
        workflows,
        "_normalise_workflow_steps",
        lambda *_args, **_kwargs: [
            {
                "name": "Tell requester",
                "type": "email_requestor",
                "body": "User ${vars.ad_username} / ${vars.ad_password} / ${vars.credential_link}",
            }
        ],
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={5: []}),
    )
    monkeypatch.setattr(workflows.workflow_repo, "append_step_log", AsyncMock())
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", AsyncMock())
    executed: dict = {}

    async def fake_execute_policy_step(*, step, **_kwargs):
        executed["body"] = step["body"]
        return {"email_sent": True}

    monkeypatch.setattr(workflows, "_execute_policy_step", fake_execute_policy_step)
    real_attempt = workflows._attempt_step
    attempt_calls: list[dict] = []

    async def spy_attempt(**kwargs):
        attempt_calls.append(kwargs)
        return await real_attempt(**kwargs)

    monkeypatch.setattr(workflows, "_attempt_step", spy_attempt)

    await workflows._execute_policy_steps(
        execution_id=5,
        company_id=9,
        staff={"id": 700, "email": "new@example.com"},
        direction=workflows.DIRECTION_ONBOARDING,
        policy_config={},
        max_retries=0,
        waiting_external_state=workflows.STATE_WAITING_EXTERNAL,
    )

    # The step itself gets the real values ...
    assert executed["body"] == (
        "User anjana.anjanappa / Plain!Pass1 / https://vault.example/s/abc123"
    )
    # ... but nothing written to the step log contains them.
    logged = json.dumps(attempt_calls[0]["request_payload"])
    assert "Plain!Pass1" not in logged
    assert "vault.example" not in logged
    assert "anjana.anjanappa" in logged
    for call in workflows.workflow_repo.append_step_log.await_args_list:
        dumped = json.dumps(call.kwargs, default=str)
        assert "Plain!Pass1" not in dumped
        assert "vault.example" not in dumped
    context_logs = [
        call.kwargs
        for call in workflows.workflow_repo.append_step_log.await_args_list
        if call.kwargs["step_name"].endswith(":context")
    ]
    assert isinstance(context_logs[0]["response_payload"]["context_patch"]["secret_vars"], list)


@pytest.mark.anyio
async def test_context_log_keeps_protected_names_readable(monkeypatch):
    monkeypatch.setattr(
        workflows.staff_custom_fields_repo,
        "get_all_staff_field_values",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        workflows.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 9})
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_external_checkpoints_for_execution_ids",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        workflows,
        "_normalise_workflow_steps",
        lambda *_args, **_kwargs: [{"name": "Make password", "type": "generate_password"}],
    )
    monkeypatch.setattr(
        workflows.workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(return_value={6: []}),
    )
    append_log = AsyncMock()
    monkeypatch.setattr(workflows.workflow_repo, "append_step_log", append_log)
    monkeypatch.setattr(workflows.workflow_repo, "update_execution_state", AsyncMock())
    monkeypatch.setattr(
        workflows,
        "_attempt_step",
        AsyncMock(return_value={"generated_password": "Zz9!long-pass", "api_secret": "s3cr3t-value"}),
    )

    await workflows._execute_policy_steps(
        execution_id=6,
        company_id=9,
        staff={"id": 700, "email": "new@example.com"},
        direction=workflows.DIRECTION_ONBOARDING,
        policy_config={},
        max_retries=0,
        waiting_external_state=workflows.STATE_WAITING_EXTERNAL,
    )

    context = next(
        call.kwargs for call in append_log.await_args_list
        if call.kwargs["step_name"] == "Make password:context"
    )
    patch = context["response_payload"]["context_patch"]
    assert patch["secret_vars"] == ["api_secret", "generated_password"]
    dumped = json.dumps(context)
    assert "Zz9!long-pass" not in dumped
    assert "s3cr3t-value" not in dumped


def test_history_variables_ignore_legacy_redacted_name_lists():
    steps = [
        {
            "stepName": "Make password:context",
            "status": "success",
            "responsePayload": {
                "context_patch": {"vars": {"length": 16}, "secret_vars": "***redacted***"}
            },
        }
    ]

    names = [item["name"] for item in handlers._workflow_history_variables(steps)]

    assert names == ["length"]


def test_history_variables_include_webhook_values_without_secrets():
    steps = [
        {
            "stepName": "Create AD account",
            "status": "success",
            "responsePayload": {
                "webhook_public_id": "48ue4u7Dd25bicQ56D_cG1BVvX-Z_w3C",
                "outcome": "success",
                "values": {
                    "ad_status": "pass",
                    "ad_username": "anjana.anjanappa",
                    "ad_password": "***redacted***",
                    "credential_link": "***redacted***",
                },
            },
        },
        {
            "stepName": "Create ticket",
            "status": "success",
            "responsePayload": {"ticket_id": 4, "ticket_number": 4},
        },
        {
            "stepName": "Create ticket:context",
            "status": "success",
            "responsePayload": {
                "context_patch": {
                    "vars": {"ticket_id": 4, "ticket_number": 4},
                    "secret_vars": [],
                }
            },
        },
        {
            "stepName": "Generate password:context",
            "status": "success",
            "responsePayload": {
                "context_patch": {"vars": {}, "secret_vars": ["generated_password"]}
            },
        },
        {
            "stepName": "Broken:context",
            "status": "failed",
            "responsePayload": {"context_patch": {"vars": {"nope": 1}}},
        },
    ]

    variables = {item["name"]: item for item in handlers._workflow_history_variables(steps)}

    assert variables["ad_username"] == {
        "name": "ad_username",
        "value": "anjana.anjanappa",
        "secret": False,
        "step": "Create AD account",
        "source": "webhook",
    }
    for name in ("ad_password", "credential_link", "generated_password"):
        assert variables[name]["secret"] is True
        assert variables[name]["value"] is None
    assert variables["ticket_number"]["step"] == "Create ticket"
    assert variables["ticket_number"]["source"] == "step"
    assert "nope" not in variables


def test_history_redact_keeps_secret_name_lists():
    payload = {"context_patch": {"vars": {}, "secret_vars": ["generated_password"]}}

    assert handlers._history_redact(payload) == payload


def test_history_redact_hides_secret_named_keys_in_old_logs():
    payload = {"password": "old-plain", "values": {"api_token": "abc"}, "subject": "Hi"}

    redacted = handlers._history_redact(payload)

    assert redacted == {
        "password": "***redacted***",
        "values": {"api_token": "***redacted***"},
        "subject": "Hi",
    }


def test_history_recent_returns_variables_and_workflow_names(monkeypatch):
    async def fake_load_staff_context(request, **kwargs):
        return ({"id": 1, "is_super_admin": True}, None, {"name": "Acme"}, 3, 9, None)

    monkeypatch.setattr(handlers, "_load_staff_context", fake_load_staff_context)
    monkeypatch.setattr(
        handlers.staff_workflow_repo,
        "list_recent_executions_for_company",
        AsyncMock(
            return_value=[
                {
                    "id": 88,
                    "staff_id": 1001,
                    "direction": "onboarding",
                    "state": "completed",
                    "workflow_key": "starter",
                    "requested_at": datetime(2026, 11, 1, 9, 0),
                }
            ]
        ),
    )
    monkeypatch.setattr(
        handlers.staff_repo,
        "get_staff_by_id",
        AsyncMock(return_value={"first_name": "Anjana", "last_name": "Anjanappa"}),
    )
    monkeypatch.setattr(
        handlers.staff_workflow_repo,
        "list_step_logs_for_execution_ids",
        AsyncMock(
            return_value={
                88: [
                    {
                        "id": 1,
                        "step_name": "Create AD account",
                        "status": "success",
                        "attempt": 1,
                        "request_payload": "{}",
                        "response_payload": json.dumps(
                            {
                                "webhook_public_id": "x",
                                "outcome": "success",
                                "values": {"ad_username": "anjana", "ad_password": "leaked"},
                            }
                        ),
                    }
                ]
            }
        ),
    )

    async def fake_policies(company_id, *, direction):
        if direction == "onboarding":
            return [{"workflow_key": "starter", "workflow_name": "Standard new starter"}]
        return []

    monkeypatch.setattr(
        handlers.staff_workflow_repo, "list_company_workflow_policies", fake_policies
    )

    response = asyncio.run(
        handlers.staff_workflow_history_recent(SimpleNamespace(query_params={}))
    )
    payload = json.loads(response.body)
    execution = payload["executions"][0]

    assert execution["workflowName"] == "Standard new starter"
    assert "leaked" not in response.body.decode()
    names = {item["name"]: item for item in execution["variables"]}
    assert names["ad_username"]["value"] == "anjana"
    assert names["ad_password"]["secret"] is True
