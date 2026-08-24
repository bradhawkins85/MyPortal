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


def test_priority_target_changes_ticket_deadlines():
    now, critical = _row(response_minutes=15, resolution_minutes=240)
    _, low = _row(response_minutes=480, resolution_minutes=7200)

    assert calculate_status(critical, now=now)["state"] == "breached"
    assert calculate_status(low, now=now)["state"] == "on_track"


def test_custom_priority_target_uses_the_same_sla_calculation():
    now, scheduled = _row(
        sla_name="Custom priorities",
        response_minutes=1440,
        resolution_minutes=10080,
    )

    result = calculate_status(scheduled, now=now)

    assert result["name"] == "Custom priorities"
    assert result["state"] == "on_track"


def test_paused_time_extends_response_and_resolution_deadlines():
    now, row = _row(
        created_at=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
        paused_seconds=90 * 60,
        response_paused_seconds=90 * 60,
        sla_pause_status="waiting_on_client",
    )

    result = calculate_status(row, now=now)

    assert result["paused"] is True
    assert result["state"] == "on_track"
    assert result["response_due_at"] == datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)


def test_resolved_ticket_does_not_report_active_pause():
    now, row = _row(status="resolved", sla_pause_status="resolved")
    row["closed_at"] = now - timedelta(minutes=5)

    assert calculate_status(row, now=now)["paused"] is False
