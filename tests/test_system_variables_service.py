from datetime import datetime, timezone

import pytest

from app.services import system_variables


def test_system_variables_include_expected_tokens(monkeypatch):
    monkeypatch.setenv("APP_PUBLIC_SAMPLE", "demo")
    variables = system_variables.get_system_variables()

    assert variables["APP_NAME"]
    assert variables["APP_ENVIRONMENT"]
    assert variables["SYSTEM_HOSTNAME"]
    assert variables["APP_PUBLIC_SAMPLE"] == "demo"

    now_utc = variables["NOW_UTC"]
    parsed = datetime.fromisoformat(now_utc)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)

    assert int(variables["APP_ALLOWED_ORIGIN_COUNT"]) >= 0
    assert "SECRET_KEY" not in variables
    assert "SYNCRO_API_KEY" not in variables
    assert "VERIFY_API_KEY" not in variables


def test_system_variables_filter_sensitive_env(monkeypatch):
    monkeypatch.setenv("MY_SECRET_KEY", "nope")
    monkeypatch.setenv("DB_PASSWORD", "dbpass")

    variables = system_variables.get_system_variables()

    assert "MY_SECRET_KEY" not in variables
    assert "DB_PASSWORD" not in variables


@pytest.mark.parametrize(
    "ticket",
    [
        {
            "id": 321,
            "subject": "Printer offline",
            "labels": ["urgent", "onsite"],
            "priority": "high",
            "requester": {"email": "user@example.com"},
        }
    ],
)
def test_system_variables_include_ticket_tokens(ticket):
    variables = system_variables.get_system_variables(ticket=ticket)

    assert variables["TICKET_ID"] == "321"
    assert variables["TICKET_SUBJECT"] == "Printer offline"
    # Subject tokens should alias to summary tokens for backwards compatibility.
    assert variables["TICKET_SUMMARY"] == "Printer offline"
    assert variables["TICKET_LABELS_0"] == "urgent"
    assert variables["TICKET_LABELS_1"] == "onsite"
    assert variables["TICKET_REQUESTER_EMAIL"] == "user@example.com"
