import json

import pytest
from starlette.datastructures import FormData

from app.main import _parse_automation_form_submission


def test_parse_event_form_allows_missing_optional_fields():
    form = FormData(
        [
            ("name", "Ticket webhook automation"),
            ("kind", "event"),
            ("status", "active"),
            ("triggerEvent", "tickets.created"),
        ]
    )

    data, form_state, error_message, status_code = _parse_automation_form_submission(
        form, kind="event"
    )

    assert error_message is None
    assert status_code == 200
    assert data["trigger_filters"] is None
    assert data["action_payload"] is None
    assert form_state["triggerFiltersRaw"] == ""
    assert form_state["actionPayloadRaw"] == ""


def _make_action_form(name: str, action_payload: dict) -> FormData:
    return FormData(
        [
            ("name", name),
            ("kind", "event"),
            ("status", "active"),
            ("triggerEvent", "tickets.created"),
            ("actionPayload", json.dumps(action_payload)),
        ]
    )


def test_parse_form_preserves_action_note():
    payload = {
        "actions": [
            {"order": 0, "module": "email", "payload": {}, "note": "Send confirmation email"},
        ]
    }
    form = _make_action_form("Test note automation", payload)

    data, form_state, error_message, status_code = _parse_automation_form_submission(
        form, kind="event"
    )

    assert error_message is None
    assert status_code == 200
    saved_actions = data["action_payload"]["actions"]
    assert saved_actions[0].get("note") == "Send confirmation email"


def test_parse_form_preserves_action_order():
    payload = {
        "actions": [
            {"order": 5, "module": "webhook", "payload": {}},
        ]
    }
    form = _make_action_form("Test order automation", payload)

    data, form_state, error_message, status_code = _parse_automation_form_submission(
        form, kind="event"
    )

    assert error_message is None
    assert status_code == 200
    saved_actions = data["action_payload"]["actions"]
    assert saved_actions[0].get("order") == 5


def test_parse_form_omits_empty_note():
    payload = {
        "actions": [
            {"order": 0, "module": "email", "payload": {}, "note": ""},
        ]
    }
    form = _make_action_form("Test empty note automation", payload)

    data, form_state, error_message, status_code = _parse_automation_form_submission(
        form, kind="event"
    )

    assert error_message is None
    assert status_code == 200
    saved_actions = data["action_payload"]["actions"]
    assert "note" not in saved_actions[0]
