from pathlib import Path


def test_webhook_request_body_migration_expands_attempt_logging_columns():
    migration = Path("migrations/294_expand_webhook_attempt_request_body.sql")

    assert migration.exists()
    sql = migration.read_text(encoding="utf-8")

    assert "MODIFY COLUMN request_body LONGTEXT NULL" in sql
    assert "MODIFY COLUMN request_headers LONGTEXT NULL" in sql
    assert "MODIFY COLUMN response_headers LONGTEXT NULL" in sql
