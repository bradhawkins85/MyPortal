"""Tests for user sidebar preferences migration definitions."""
from __future__ import annotations

import re
from pathlib import Path


def test_user_sidebar_preferences_user_id_matches_users_id_type():
    """Ensure FK column type matches users.id INT type for MySQL compatibility."""
    migration_file = Path(__file__).resolve().parent.parent / "migrations" / "155_user_sidebar_preferences.sql"
    content = migration_file.read_text(encoding="utf-8")

    col_match = re.search(r"\buser_id\s+(INT|BIGINT)\b", content, re.IGNORECASE)
    assert col_match, "Could not find user_id column definition"
    assert col_match.group(1).upper() == "INT", (
        "user_sidebar_preferences.user_id must be INT to match users.id and satisfy MySQL FK rules"
    )

    fk_match = re.search(
        r"FOREIGN\s+KEY\s*\(user_id\)\s+REFERENCES\s+users\(id\)",
        content,
        re.IGNORECASE,
    )
    assert fk_match, "Could not find foreign key from user_sidebar_preferences.user_id to users.id"
