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
