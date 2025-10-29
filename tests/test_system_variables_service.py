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
            "requester": {
                "id": 7,
                "email": "user@example.com",
                "first_name": "Jordan",
                "last_name": "Casey",
                "display_name": "Jordan Casey",
            },
            "status": "open",
            "category": "hardware",
            "external_reference": "RMM-42",
            "ai_summary": "Printer offline awaiting vendor response",
            "ai_tags": ["printer", "hardware"],
            "company": {"id": 7, "name": "Acme Corp"},
            "company_name": "Acme Corp",
            "assigned_user": {
                "id": 9,
                "email": "tech@example.com",
                "first_name": "Taylor",
                "last_name": "Nguyen",
            },
            "assigned_user_email": "tech@example.com",
            "assigned_user_display_name": "Taylor Nguyen",
            "requester_email": "user@example.com",
            "requester_display_name": "Jordan Casey",
            "watchers": [
                {
                    "id": 77,
                    "ticket_id": 321,
                    "user_id": 15,
                    "email": "watcher@example.com",
                    "display_name": "Casey Lee",
                    "user": {
                        "id": 15,
                        "email": "watcher@example.com",
                        "display_name": "Casey Lee",
                    },
                }
            ],
            "watchers_count": 1,
            "watcher_emails": ["watcher@example.com"],
            "latest_reply": {
                "id": 555,
                "ticket_id": 321,
                "author_id": 9,
                "body": "We rebooted the printer.",
                "is_internal": False,
                "author_email": "tech@example.com",
                "author_display_name": "Taylor Nguyen",
                "author": {
                    "id": 9,
                    "email": "tech@example.com",
                    "display_name": "Taylor Nguyen",
                },
            },
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
    assert variables["TICKET_COMPANY_NAME"] == "Acme Corp"
    assert variables["COMPANY_NAME"] == "Acme Corp"
    assert variables["TICKET_ASSIGNED_USER_EMAIL"] == "tech@example.com"
    assert variables["TICKET_ASSIGNED_USER_DISPLAY_NAME"] == "Taylor Nguyen"
    assert variables["TICKET_REQUESTER_EMAIL"] == "user@example.com"
    assert variables["TICKET_REQUESTER_DISPLAY_NAME"] == "Jordan Casey"
    assert variables["TICKET_AI_SUMMARY"] == "Printer offline awaiting vendor response"
    assert variables["TICKET_AI_TAGS_0"] == "printer"
    assert variables["TICKET_STATUS"] == "open"
    assert variables["TICKET_CATEGORY"] == "hardware"
    assert variables["TICKET_PRIORITY"] == "high"
    assert variables["TICKET_EXTERNAL_REFERENCE"] == "RMM-42"
    assert variables["TICKET_WATCHERS_COUNT"] == "1"
    assert variables["TICKET_WATCHERS_0_EMAIL"] == "watcher@example.com"
    assert variables["TICKET_WATCHERS_0_USER_EMAIL"] == "watcher@example.com"
    assert variables["TICKET_LATEST_REPLY_BODY"] == "We rebooted the printer."
    assert variables["TICKET_LATEST_REPLY_AUTHOR_EMAIL"] == "tech@example.com"
