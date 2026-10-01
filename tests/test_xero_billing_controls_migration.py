from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "362_xero_billing_controls.sql"
)


def test_xero_billing_controls_migration_adds_invoice_control_columns():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS approval_required" in sql
    assert "ADD COLUMN IF NOT EXISTS approved_at" in sql
    assert "ADD COLUMN IF NOT EXISTS billing_adjustment_amount" in sql
    assert "ADD COLUMN IF NOT EXISTS xero_sync_error" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_invoices_approval_required" in sql


def test_xero_billing_controls_migration_seeds_revenue_leakage_report():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "'revenue-leakage-unbilled-time'" in sql
    assert "'Revenue Leakage - Unbilled Time'" in sql
    assert "SUM(tr.minutes_spent - COALESCE(billed.billed_minutes, 0)) AS unbilled_minutes" in sql
    assert "ROUND(SUM((tr.minutes_spent - COALESCE(billed.billed_minutes, 0)) * COALESCE(lt.rate, 0)), 2) AS unbilled_amount" in sql
    assert "CASE WHEN TIMESTAMPDIFF(DAY, COALESCE(t.closed_at, t.created_at), CURRENT_TIMESTAMP) >= 30" in sql
