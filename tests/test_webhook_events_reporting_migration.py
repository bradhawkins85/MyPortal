"""Regression coverage for the webhook-events reporting catalogue entry."""

from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "401_webhook_events_reporting_query.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_webhook_report_is_an_idempotent_system_catalogue_entry() -> None:
    sql = _sql()

    assert "INSERT IGNORE INTO reporting_queries" in sql
    assert "'webhook-events-by-name-and-response-code'" in sql
    assert sql.rstrip().endswith("1\n);")


def test_webhook_report_filters_attempts_by_name_and_response_code() -> None:
    sql = _sql()

    assert "INNER JOIN webhook_event_attempts a ON a.event_id = e.id" in sql
    assert "e.name LIKE ''%webhook name%''" in sql
    assert "a.response_status = 200" in sql


def test_webhook_report_includes_requested_audit_data() -> None:
    sql = _sql()

    assert "COALESCE(e.source_url, e.target_url) AS url" in sql
    assert "a.request_body AS request_data" in sql
    assert "a.response_body AS response_data" in sql
    assert "a.response_status AS response_code" in sql
    assert "ORDER BY a.attempted_at DESC, a.id DESC" in sql
