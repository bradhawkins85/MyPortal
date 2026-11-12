"""Tests for the staff landline phone migration."""
from __future__ import annotations

from pathlib import Path

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "546_staff_landline_phone.sql"


def test_migration_adds_landline_phone_idempotently():
    content = MIGRATION.read_text(encoding="utf-8")

    assert content.startswith("-- phase: expand")
    assert "ALTER TABLE staff ADD COLUMN IF NOT EXISTS landline_phone VARCHAR(32)" in content
