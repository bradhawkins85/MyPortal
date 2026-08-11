import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import modules


def test_extract_ai_ticket_subject_accepts_supported_response_shapes():
    assert (
        modules._extract_ai_ticket_subject(
            {"response": "Office Printer Repeatedly Jams During Duplex Printing"}
        )
        == "Office Printer Repeatedly Jams During Duplex Printing"
    )
    assert (
        modules._extract_ai_ticket_subject(
            {"message": '{"subject": "VPN Authentication Fails After Password Reset"}'}
        )
        == "VPN Authentication Fails After Password Reset"
    )


@pytest.mark.parametrize(
    "subject",
    [
        "Too short",
        "one two three four five six seven eight nine ten eleven twelve thirteen",
    ],
)
def test_extract_ai_ticket_subject_rejects_out_of_range_results(subject):
    assert modules._extract_ai_ticket_subject(subject) is None


def test_ai_rename_ticket_uses_subject_and_initial_description(monkeypatch):
    ticket = {
        "id": 42,
        "ticket_number": "TKT-42",
        "subject": "Printer issue",
        "description": "The upstairs office printer jams whenever duplex mode is used.",
    }
    monkeypatch.setattr(
        modules.tickets_repo, "get_ticket", AsyncMock(return_value=ticket)
    )
    update = AsyncMock(
        return_value={
            **ticket,
            "subject": "Upstairs Printer Jams During Duplex Printing",
        }
    )
    monkeypatch.setattr(modules.tickets_repo, "update_ticket", update)
    monkeypatch.setattr(
        modules.module_repo,
        "get_module",
        AsyncMock(return_value={"slug": "ollama", "enabled": True, "settings": {}}),
    )
    invoke_ai = AsyncMock(
        return_value={
            "status": "succeeded",
            "response": {"response": "Upstairs Printer Jams During Duplex Printing"},
        }
    )
    monkeypatch.setattr(modules, "_invoke_ollama", invoke_ai)
    emit = AsyncMock()
    monkeypatch.setattr(modules.tickets_service, "emit_ticket_updated_event", emit)

    result = asyncio.run(
        modules._invoke_ai_rename_ticket({}, {"context": {"ticket": {"id": 42}}})
    )

    prompt = invoke_ai.await_args.args[1]["prompt"]
    assert "Printer issue" in prompt
    assert ticket["description"] in prompt
    assert invoke_ai.await_args.args[1]["max_tokens"] == 512
    update.assert_awaited_once_with(
        42, subject="Upstairs Printer Jams During Duplex Printing"
    )
    emit.assert_awaited_once_with(
        42, actor_type="automation", trigger_automations=False
    )
    assert result["previous_values"] == {"subject": "Printer issue"}


def test_ai_rename_ticket_reports_invalid_ai_subject_without_masking_error(monkeypatch):
    monkeypatch.setattr(
        modules.tickets_repo,
        "get_ticket",
        AsyncMock(
            return_value={"id": 7, "subject": "Help", "description": "Cannot log in"}
        ),
    )
    monkeypatch.setattr(
        modules.module_repo,
        "get_module",
        AsyncMock(return_value={"slug": "ollama", "enabled": True, "settings": {}}),
    )
    monkeypatch.setattr(
        modules,
        "_invoke_ollama",
        AsyncMock(
            return_value={"status": "succeeded", "response": {"response": "Login"}}
        ),
    )
    update = AsyncMock()
    monkeypatch.setattr(modules.tickets_repo, "update_ticket", update)

    result = asyncio.run(modules._invoke_ai_rename_ticket({}, {"ticket_id": 7}))

    assert result["status"] == "error"
    assert "between 3 and 12 words" in result["error"]
    update.assert_not_awaited()
