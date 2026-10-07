from typing import Any

import pytest

from app.repositories import tickets as tickets_repo
from app.services import automations as automations_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def audit_env(monkeypatch):
    created: list[dict[str, Any]] = []
    state: dict[str, Any] = {"latest": None, "ticket": {"id": 7, "status": "closed"}}

    async def fake_record_history(**kwargs):
        return None

    async def fake_create_reply(**kwargs):
        # Mirror the uq_ticket_replies_external unique key.
        key = (kwargs.get("ticket_id"), kwargs.get("external_reference"))
        if any((c["ticket_id"], c["external_reference"]) == key for c in created):
            raise RuntimeError("Duplicate entry for key uq_ticket_replies_external")
        created.append(kwargs)
        state["latest"] = {
            "external_reference": kwargs.get("external_reference"),
            "body": kwargs.get("body"),
        }
        return {"id": len(created), **kwargs}

    async def fake_latest_reply(ticket_id):
        return state["latest"]

    async def fake_get_ticket(ticket_id):
        return state["ticket"]

    monkeypatch.setattr(
        automations_service.automation_repo, "record_history", fake_record_history
    )
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(tickets_repo, "get_latest_reply", fake_latest_reply)
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    return created, state


AUTOMATION = {"id": 3, "name": "Close stale <tickets>", "kind": "scheduled"}
CONTEXT = {"ticket": {"id": 7, "ticket_number": "T-7"}}


@pytest.mark.anyio
async def test_successful_ticket_action_adds_internal_automation_note(audit_env):
    created, _ = audit_env

    await automations_service._record_action_history(
        AUTOMATION,
        action_name="Close ticket",
        action_module="update-ticket",
        status="succeeded",
        result={
            "module": "update-ticket",
            "status": "succeeded",
            "result": {
                "ticket_id": 7,
                "status": "succeeded",
                "updated_fields": ["status"],
                "previous_values": {"status": "open"},
            },
        },
        error_message=None,
        context=CONTEXT,
    )

    assert len(created) == 1
    note = created[0]
    assert note["ticket_id"] == 7
    assert note["is_internal"] is True
    assert note["author_id"] is None
    assert note["author_display_name"] == "Automation"
    assert note["external_reference"].startswith("automation:3:")
    assert "Close stale &lt;tickets&gt;" in note["body"]
    assert "Scheduled automation" in note["body"]
    assert "Close ticket (update-ticket)" in note["body"]
    assert "Status: open &rarr; closed" in note["body"]


@pytest.mark.anyio
async def test_failed_action_note_includes_error_and_is_not_repeated(audit_env):
    created, _ = audit_env
    kwargs = dict(
        action_name="add-ticket-reply",
        action_module="add-ticket-reply",
        status="failed",
        result={"module": "add-ticket-reply", "status": "failed", "error": "boom"},
        error_message="boom",
        context=CONTEXT,
    )

    await automations_service._record_action_history(AUTOMATION, **kwargs)
    await automations_service._record_action_history(AUTOMATION, **kwargs)

    assert len(created) == 1
    assert "failed" in created[0]["body"]
    assert "Error: boom" in created[0]["body"]


@pytest.mark.anyio
async def test_skipped_actions_and_non_ticket_contexts_add_no_note(audit_env):
    created, _ = audit_env

    await automations_service._record_action_history(
        AUTOMATION,
        action_name="Skipped outside business hours",
        action_module=None,
        status="skipped",
        result={"status": "skipped"},
        error_message=None,
        context=CONTEXT,
    )
    await automations_service._record_action_history(
        AUTOMATION,
        action_name="ntfy",
        action_module="ntfy",
        status="succeeded",
        result={"status": "succeeded"},
        error_message=None,
        context={"company": {"id": 1}},
    )

    assert created == []


@pytest.mark.anyio
async def test_event_automation_note_names_trigger_and_reply(audit_env):
    created, _ = audit_env

    await automations_service._record_action_history(
        {"id": 9, "name": "Acknowledge", "kind": "event", "trigger_event": "tickets.created"},
        action_name="add-ticket-reply",
        action_module="add-ticket-reply",
        status="succeeded",
        result={
            "module": "add-ticket-reply",
            "status": "succeeded",
            "result": {
                "ticket_id": 7,
                "status": "succeeded",
                "response": {"reply_id": 55, "is_internal": False},
            },
        },
        error_message=None,
        context=CONTEXT,
    )

    assert len(created) == 1
    body = created[0]["body"]
    assert "Event automation (tickets.created)" in body
    assert "Added reply #55 (public reply)" in body


@pytest.mark.anyio
async def test_audit_note_failure_does_not_break_automation(audit_env, monkeypatch):
    async def broken_create_reply(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(tickets_repo, "create_reply", broken_create_reply)

    await automations_service._record_action_history(
        AUTOMATION,
        action_name="update-ticket",
        action_module="update-ticket",
        status="succeeded",
        result={"status": "succeeded"},
        error_message=None,
        context=CONTEXT,
    )


@pytest.mark.anyio
async def test_each_note_gets_a_unique_external_reference(audit_env):
    created, _ = audit_env

    for module in ("update-ticket", "add-ticket-reply"):
        await automations_service._record_action_history(
            AUTOMATION,
            action_name=module,
            action_module=module,
            status="succeeded",
            result={"module": module, "status": "succeeded"},
            error_message=None,
            context=CONTEXT,
        )

    assert len(created) == 2
    references = {note["external_reference"] for note in created}
    assert len(references) == 2
    assert all(ref.startswith("automation:3:") for ref in references)


@pytest.mark.anyio
async def test_wrapped_action_result_ticket_id_targets_the_note(audit_env):
    created, _ = audit_env
    wrapped = {
        "module": "update-ticket",
        "status": "succeeded",
        "result": {"ticket_id": 42, "status": "succeeded"},
    }

    # Global scheduled automation with no ticket in context.
    await automations_service._record_action_history(
        AUTOMATION,
        action_name="update-ticket",
        action_module="update-ticket",
        status="succeeded",
        result=wrapped,
        error_message=None,
        context=None,
    )
    # Ticket event whose action targets a different ticket.
    await automations_service._record_action_history(
        {"id": 4, "name": "Escalate", "kind": "event"},
        action_name="update-ticket",
        action_module="update-ticket",
        status="succeeded",
        result=wrapped,
        error_message=None,
        context=CONTEXT,
    )

    assert [note["ticket_id"] for note in created] == [42, 42]


def test_context_ticket_number_not_borrowed_for_a_different_ticket():
    ticket_id, ticket_number = automations_service._context_ticket_identity(
        CONTEXT, {"module": "x", "result": {"ticket_id": 42}}
    )
    assert ticket_id == 42
    assert ticket_number is None


@pytest.mark.anyio
async def test_identical_actions_in_one_run_each_get_a_note(audit_env):
    created, _ = audit_env
    kwargs = dict(
        action_name="ntfy",
        action_module="ntfy",
        status="succeeded",
        result={"module": "ntfy", "status": "succeeded"},
        error_message=None,
        context=CONTEXT,
    )

    run_token = automations_service._audit_run_id.set("run-one")
    try:
        await automations_service._record_action_history(AUTOMATION, **kwargs)
        await automations_service._record_action_history(AUTOMATION, **kwargs)
    finally:
        automations_service._audit_run_id.reset(run_token)
    assert len(created) == 2

    # A later run repeating the identical outcome is collapsed.
    run_token = automations_service._audit_run_id.set("run-two")
    try:
        await automations_service._record_action_history(AUTOMATION, **kwargs)
    finally:
        automations_service._audit_run_id.reset(run_token)
    assert len(created) == 2
    assert all(
        note["external_reference"].startswith("automation:3:run-one:")
        for note in created
    )
