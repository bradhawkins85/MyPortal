"""Regression coverage for the monthly 14/8-day renewal window migration."""

from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "542_monthly_renewal_reminder_14_day_invoice_8_day.sql"
)


def test_migration_updates_only_unedited_monthly_template():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "-- phase: data" in sql
    assert "WHERE slug = 'monthly-subscription-renewal-reminder'" in sql
    assert "This is your 14-day reminder" in sql
    assert "invoice 8 days before the renewal date" in sql
    # Only replace the original default copy, never admin-customised content.
    assert "AND content = '<p>Hi {{ recipient.name }},</p><p>This is your 30-day reminder" in sql


def test_migration_leaves_annual_template_untouched():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "'subscription-renewal-reminder'" not in sql
    assert "monthly subscription reminders/invoices inside 14/8 days" in sql
