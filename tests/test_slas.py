from datetime import datetime, timedelta, timezone

from app.services.slas import calculate_status


def _row(**overrides):
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    row = {"sla_id": 1, "sla_name": "Standard", "created_at": now - timedelta(minutes=30),
           "first_response_at": None, "closed_at": None, "status": "open",
           "response_minutes": 60, "resolution_minutes": 240}
    row.update(overrides)
    return now, row


def test_sla_on_track_before_targets():
    now, row = _row()
    assert calculate_status(row, now=now)["state"] == "on_track"


def test_sla_reports_response_breach():
    now, row = _row(created_at=datetime(2026, 1, 1, 10, tzinfo=timezone.utc))
    result = calculate_status(row, now=now)
    assert result["state"] == "breached"
    assert result["response_breached"] is True


def test_ticket_without_sla_is_not_applicable():
    assert calculate_status({"sla_id": None}) == {"state": "not_applicable", "label": "No SLA"}
