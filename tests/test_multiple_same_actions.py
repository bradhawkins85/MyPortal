import pytest
import asyncio
from datetime import datetime, timezone
from typing import Any


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_multiple_same_module_actions_all_triggered(monkeypatch):
    """Reproduce the bug: multiple actions of the same type should all be triggered."""
    from app.services import automations as automations_service
    
    captured: list[tuple[str, dict]] = []

    async def fake_trigger_module(module_slug, payload, *, background=False):
        captured.append((module_slug, dict(payload)))
        return {"status": "succeeded"}

    async def fake_mark_started(*args, **kwargs):
        return None

    async def fake_record_run(**kwargs):
        return None

    async def fake_set_last_error(*args, **kwargs):
        return None

    async def fake_set_next_run(*args, **kwargs):
        return None

    monkeypatch.setattr(automations_service.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(automations_service.automation_repo, "mark_started", fake_mark_started)
    monkeypatch.setattr(automations_service.automation_repo, "record_run", fake_record_run)
    monkeypatch.setattr(automations_service.automation_repo, "set_last_error", fake_set_last_error)
    monkeypatch.setattr(automations_service.automation_repo, "set_next_run", fake_set_next_run)

    automation = {
        "id": 42,
        "kind": "event",
        "action_payload": {
            "actions": [
                {
                    "module": "smtp2go",
                    "payload": {
                        "recipients": ["admin@example.com"],
                        "subject": "Alert to admin",
                        "html": "<p>Hello admin</p>",
                    },
                },
                {
                    "module": "smtp2go",
                    "payload": {
                        "recipients": ["user@example.com"],
                        "subject": "Alert to user",
                        "html": "<p>Hello user</p>",
                    },
                },
            ]
        },
    }

    result = await automations_service._execute_automation(automation)

    assert result["status"] == "succeeded"
    assert len(captured) == 2, f"Expected 2 smtp2go actions to be triggered, got {len(captured)}"
    assert captured[0][0] == "smtp2go"
    assert captured[1][0] == "smtp2go"
    assert captured[0][1].get("subject") == "Alert to admin"
    assert captured[1][1].get("subject") == "Alert to user"
