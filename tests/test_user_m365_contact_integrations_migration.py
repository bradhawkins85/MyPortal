"""Tests for the per-user Microsoft 365 contacts migration."""
from __future__ import annotations

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "308_user_m365_contact_integrations.sql"
)


def test_user_id_matches_referenced_users_id_type():
    """The foreign-key column must match the INT type of users.id in MySQL."""
    content = MIGRATION.read_text(encoding="utf-8")

    column = re.search(r"\buser_id\s+(INT|BIGINT)\b", content, re.IGNORECASE)
    assert column, "Could not find the user_id column definition"
    assert column.group(1).upper() == "INT", (
        "user_m365_contact_integrations.user_id must be INT to match users.id"
    )

    foreign_key = re.search(
        r"FOREIGN\s+KEY\s*\(user_id\)\s+REFERENCES\s+users\s*\(id\)",
        content,
        re.IGNORECASE,
    )
    assert foreign_key, "Could not find the foreign key from user_id to users.id"
