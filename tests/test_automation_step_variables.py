"""Ticket automation steps pass results to later steps and run conditionally."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import automations as automations_service
from app.services import modules


def _run(actions, context, results, monkeypatch):
    """Run ``actions`` with ``trigger_module`` returning ``results`` per module."""

    calls: list[tuple[str, dict]] = []

    async def _trigger(slug, payload, background=False):
        calls.append((slug, payload))
        outcome = results[slug]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    history = AsyncMock()
    monkeypatch.setattr(automations_service.module_dispatch, "trigger_module", _trigger)
    monkeypatch.setattr(automations_service, "_record_action_history", history)
    automation = {"id": 1, "name": "Triage", "action_payload": {"actions": actions}}
    result, error = asyncio.run(
        automations_service._invoke_automation_actions_for_context(
            automation, context=context
        )
    )
    return calls, result, error, history


def test_later_steps_render_earlier_step_outputs(monkeypatch):
    actions = [
        {"module": "ai-custom-prompt", "payload": {}, "output_var": "triage"},
        {
            "module": "smtp",
            "payload": {
                "subject": "Ticket {{ticket.id}} looks like {{vars.triage.choice}}",
                "body": "<p>{{vars.triage.text}}</p>",
            },
        },
    ]
    calls, _, error, _ = _run(
        actions,
        {"ticket": {"id": 42}},
        {
            "ai-custom-prompt": {
                "status": "succeeded",
                "choice": "R&D",
                "text": "<a href='https://evil.example'>click</a>",
                "response": "raw model output",
            },
            "smtp": {"status": "succeeded"},
        },
        monkeypatch,
    )

    assert error is None
    smtp_payload = calls[1][1]
    assert smtp_payload["subject"] == "Ticket 42 looks like R&D"
    # Free AI text is HTML-escaped before it can reach an email body.
    assert "<a href" not in smtp_payload["body"]
    assert "&lt;a href=" in smtp_payload["body"]
    stored = smtp_payload["context"]["vars"]["triage"]
    assert stored["status"] == "succeeded"
    assert "response" not in stored
    assert "context" not in stored


def test_run_if_skips_steps_whose_conditions_do_not_match(monkeypatch):
    actions = [
        {"module": "ai-custom-prompt", "payload": {}, "output_var": "triage"},
        {
            "module": "add-tag",
            "payload": {},
            "run_if": {
                "match": "all",
                "conditions": [
                    {"field": "vars.triage.choice", "operator": "equals", "value": "billing"}
                ],
            },
        },
        {
            "module": "smtp",
            "payload": {},
            "run_if": {
                "match": "any",
                "conditions": [
                    {"field": "vars.triage.choice", "operator": "equals", "value": "Outage"},
                    {"field": "ticket.priority", "operator": "equals", "value": "high"},
                ],
            },
        },
    ]
    calls, result, error, history = _run(
        actions,
        {"ticket": {"id": 1, "priority": "low"}},
        {
            "ai-custom-prompt": {"status": "succeeded", "choice": "outage"},
            "add-tag": {"status": "succeeded"},
            "smtp": {"status": "succeeded"},
        },
        monkeypatch,
    )

    assert error is None
    assert [slug for slug, _ in calls] == ["ai-custom-prompt", "smtp"]
    assert result[1]["status"] == "skipped"
    assert result[1]["reason"] == "Run-if conditions not met"
    statuses = [call.kwargs["status"] for call in history.await_args_list]
    assert statuses == ["succeeded", "skipped", "succeeded"]


def test_skipped_and_failed_steps_expose_their_status(monkeypatch):
    actions = [
        {"module": "first", "payload": {}, "output_var": "first", "run_if": {
            "match": "all",
            "conditions": [{"field": "ticket.status", "operator": "equals", "value": "closed"}],
        }},
        {"module": "second", "payload": {}, "output_var": "second"},
        {"module": "notify", "payload": {}, "run_if": {
            "match": "all",
            "conditions": [
                {"field": "vars.first.status", "operator": "equals", "value": "skipped"},
                {"field": "vars.second.status", "operator": "equals", "value": "failed"},
            ],
        }},
    ]
    calls, _, error, _ = _run(
        actions,
        {"ticket": {"status": "open"}},
        {"second": RuntimeError("boom"), "notify": {"status": "succeeded"}},
        monkeypatch,
    )

    assert error == "boom"
    assert [slug for slug, _ in calls] == ["second", "notify"]
    assert calls[1][1]["context"]["vars"]["second"]["error"] == "boom"


@pytest.mark.parametrize(
    "operator, value, actual, expected",
    [
        ("is_empty", None, "", True),
        ("is_empty", None, [], True),
        ("is_not_empty", None, ["a"], True),
        ("is_not_empty", None, None, False),
        ("contains", "refund", "Customer wants a refund", True),
        ("not_contains", "refund", None, True),
        ("in", "billing, accounts", "Accounts", True),
        ("greater_than", "3", 5, True),
        ("not_equals", "billing", None, True),
        ("equals", "billing", None, False),
    ],
)
def test_step_condition_operators(operator, value, actual, expected):
    condition = {"field": "vars.step.value", "operator": operator}
    if value is not None:
        condition["value"] = value
    run_if = {"match": "all", "conditions": [condition]}
    context = {"vars": {"step": {"value": actual}}}
    assert automations_service._step_condition_matches(run_if, context) is expected


def test_invalid_run_if_never_runs_the_step(monkeypatch):
    actions = [
        {"module": "smtp", "payload": {}, "run_if": {"match": "sometimes", "conditions": []}},
    ]
    calls, result, _, _ = _run(actions, {}, {"smtp": {"status": "succeeded"}}, monkeypatch)

    assert calls == []
    assert result[0]["status"] == "skipped"
    assert "invalid" in result[0]["reason"].lower()


def test_normalise_actions_keeps_output_var_and_run_if():
    run_if = {
        "match": "ANY",
        "conditions": [{"field": " vars.a.choice ", "operator": "equals", "value": "x"}],
    }
    result = automations_service._normalise_actions(
        [{"module": "m", "payload": {}, "output_var": " a ", "run_if": run_if}]
    )
    assert result[0]["output_var"] == "a"
    assert result[0]["run_if"] == {
        "match": "any",
        "conditions": [{"field": "vars.a.choice", "operator": "equals", "value": "x"}],
    }


@pytest.mark.parametrize("name", ["Bad Name", "1st", "ticket", "vars", "x" * 41, "a-b"])
def test_output_var_rejects_unsafe_names(name):
    with pytest.raises(ValueError):
        automations_service.normalise_step_output_var(name)


@pytest.mark.parametrize(
    "run_if",
    [
        "always",
        {"match": "all", "conditions": "x"},
        {"match": "all", "conditions": [{"field": "vars.a", "operator": "explodes"}]},
        {"match": "all", "conditions": [{"field": "vars.a b", "operator": "equals"}]},
        {"match": "all", "conditions": [{"field": "vars.a", "operator": "equals"}] * 11},
    ],
)
def test_run_if_rejects_invalid_conditions(run_if):
    with pytest.raises(ValueError):
        automations_service.normalise_step_condition(run_if)


def test_validate_step_flow_rejects_forward_and_duplicate_references():
    with pytest.raises(ValueError, match="before"):
        automations_service.validate_step_flow(
            [
                {"module": "a", "run_if": {"match": "all", "conditions": [
                    {"field": "vars.later.choice", "operator": "equals", "value": "x"}
                ]}},
                {"module": "b", "output_var": "later"},
            ]
        )
    with pytest.raises(ValueError, match="more than once"):
        automations_service.validate_step_flow(
            [{"module": "a", "output_var": "x"}, {"module": "b", "output_var": "x"}]
        )


def test_ai_output_modules_cover_every_ai_ticket_action():
    assert modules._AI_TICKET_MODULE_SLUGS <= automations_service._AI_OUTPUT_MODULE_SLUGS


def test_api_rejects_invalid_step_flow():
    from fastapi import HTTPException

    from app.api.routes import automations as automation_routes

    with pytest.raises(HTTPException) as excinfo:
        automation_routes._reject_invalid_step_flow(
            {"action_payload": {"actions": [
                {"module": "a", "payload": {}, "output_var": "x"},
                {"module": "b", "payload": {}, "output_var": "x"},
            ]}}
        )
    assert excinfo.value.status_code == 422
    automation_routes._reject_invalid_step_flow(
        {"action_payload": {"actions": [{"module": "a", "payload": {}, "output_var": "x"}]}}
    )
