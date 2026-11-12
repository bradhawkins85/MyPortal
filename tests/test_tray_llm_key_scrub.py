"""Migration 552 removes the LLM API key old troubleshoot commands logged."""

import json
import sqlite3
from pathlib import Path

from app.core.database import Database

MIGRATION = Path(__file__).resolve().parents[1] / "migrations/552_scrub_tray_llm_keys.sql"


def test_migration_is_a_data_migration():
    assert MIGRATION.read_text().startswith("-- phase: data\n")


def test_old_troubleshoot_commands_lose_the_llm_key():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tray_command_log (id INTEGER PRIMARY KEY, command TEXT, payload_json TEXT)")
    rows = [
        ("troubleshoot", json.dumps({"ticket_id": 1, "llm_api_key": "sk-secret", "llm_model": "m"})),
        ("troubleshoot", "not json but mentions llm_api_key"),
        ("wol", json.dumps({"mac": "aa"})),
        ("troubleshoot", None),
    ]
    conn.executemany("INSERT INTO tray_command_log (command, payload_json) VALUES (?, ?)", rows)
    sql = Database.__new__(Database)._adapt_sql_for_sqlite(MIGRATION.read_text())
    conn.executescript(sql)
    conn.executescript(sql)  # idempotent

    payloads = [row[0] for row in conn.execute("SELECT payload_json FROM tray_command_log ORDER BY id")]
    assert json.loads(payloads[0]) == {"ticket_id": 1, "llm_model": "m"}
    assert payloads[1] == "not json but mentions llm_api_key"
    assert json.loads(payloads[2]) == {"mac": "aa"}
    assert payloads[3] is None
