import json
from pathlib import Path

from app.services import report_query_builder


def test_ai_prompt_is_schema_grounded_and_read_only(monkeypatch):
    monkeypatch.setattr(report_query_builder.db, "is_sqlite", lambda: False)
    schema = {
        "tables": [{"name": "tickets", "columns": [{"name": "id", "type": "int"}]}],
        "relations": [],
    }
    messages = report_query_builder.build_ai_messages(
        schema, "show tickets", "SELECT id FROM tickets"
    )
    assert "exactly one MySQL" in messages[0]["content"]
    assert "Never invent tables or columns" in messages[0]["content"]
    assert "{{current.company}}" in messages[0]["content"]
    assert "DATE_SUB(NOW(), INTERVAL 30 DAY)" in messages[0]["content"]
    assert "Current query to refine" in messages[1]["content"]
    assert "tickets(id int)" in messages[1]["content"]
    # The schema is sent once, not duplicated inside the untrusted envelope.
    assert messages[1]["content"].count("tickets(id int)") == 1
    assert messages[1]["content"].index("show tickets") < messages[1]["content"].index(
        "Database schema"
    )
    assert messages[1]["content"].rstrip().endswith("}.")


def test_ai_prompt_uses_sqlite_dialect_rules(monkeypatch):
    monkeypatch.setattr(report_query_builder.db, "is_sqlite", lambda: True)
    messages = report_query_builder.build_ai_messages({"tables": []}, "show tickets")
    assert "exactly one SQLite" in messages[0]["content"]
    assert "datetime('now')" in messages[0]["content"]


def test_ai_prompt_limits_large_schema_and_prioritises_requested_table(monkeypatch):
    monkeypatch.setattr(report_query_builder.db, "is_sqlite", lambda: False)
    monkeypatch.setattr(report_query_builder, "AI_SCHEMA_MAX_CHARS", 600)
    schema = {
        "tables": [
            {
                "name": f"table_{number}",
                "columns": [
                    {"name": f"column_{column}", "type": "varchar"}
                    for column in range(8)
                ],
            }
            for number in range(20)
        ],
        "relations": [],
    }

    schema_text = report_query_builder._schema_for_prompt(
        schema, "show column_1 from table_19"
    )
    lines = schema_text.splitlines()

    assert len("\n".join(lines[:-1])) <= 600
    assert lines[0].startswith("table_19(")
    assert lines[-1].endswith("less relevant tables omitted)")


def _crm_schema():
    return {
        "tables": [
            {
                "name": "companies",
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "name", "type": "varchar"},
                ],
            },
            {
                "name": "users",
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "email", "type": "varchar"},
                    {"name": "first_name", "type": "varchar"},
                ],
            },
            {
                "name": "tickets",
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "subject", "type": "varchar"},
                    {"name": "status", "type": "varchar"},
                    {"name": "company_id", "type": "int"},
                    {"name": "assigned_user_id", "type": "int"},
                    {"name": "created_at", "type": "datetime"},
                ],
            },
            {
                "name": "shop_orders",
                "columns": [{"name": "id", "type": "int"}],
            },
        ],
        "relations": [],
    }


def test_schema_prompt_infers_join_columns_and_singular_terms():
    schema_text = report_query_builder._schema_for_prompt(
        _crm_schema(), "open tickets per company"
    )
    lines = schema_text.splitlines()

    assert lines[0].startswith("tickets(")
    assert "company_id int -> companies.id" in lines[0]
    assert "assigned_user_id int -> users.id" in lines[0]
    # Join targets of relevant tables follow them, ahead of unrelated tables.
    assert [line.split("(")[0] for line in lines[:3]] == ["tickets", "companies", "users"]


def test_schema_prompt_ignores_sql_keywords_in_current_query():
    schema_text = report_query_builder._schema_for_prompt(
        _crm_schema(), "add the subject", "SELECT t.id FROM tickets t ORDER BY t.id"
    )
    assert schema_text.splitlines()[-1].startswith("shop_orders(")


def test_flatten_messages_keeps_system_rules_for_completion_providers():
    messages = report_query_builder.build_ai_messages(_crm_schema(), "show tickets")
    prompt = report_query_builder.flatten_messages(messages)
    assert prompt.startswith("SYSTEM:\n")
    assert "Never invent tables or columns" in prompt
    assert "USER:\n" in prompt


def test_retry_messages_include_previous_query_and_problems():
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"}]
    retry = report_query_builder.build_ai_retry_messages(
        messages, "SELECT t.nope FROM tickets t", ["Column `nope` does not exist."]
    )
    assert retry[:2] == messages
    assert retry[2] == {
        "role": "assistant",
        "content": '{"sql": "SELECT t.nope FROM tickets t"}',
    }
    assert "Column `nope` does not exist." in retry[3]["content"]


def test_check_query_accepts_valid_query_shapes():
    sql = (
        "WITH recent AS (SELECT t.id, t.company_id FROM tickets t "
        "WHERE t.created_at > DATE_SUB(NOW(), INTERVAL 30 DAY)) "
        "SELECT c.name AS company_name, COUNT(r.id) AS ticket_count, "
        "EXTRACT(YEAR FROM MAX(x.created_at)) AS latest_year, 'a.b' AS label "
        "FROM `companies` AS c LEFT JOIN recent r ON r.company_id = c.id, "
        "tickets x, (SELECT u.id FROM users u) AS staff "
        "WHERE c.id = {{current.company}} AND staff.id = x.assigned_user_id "
        "GROUP BY c.name ORDER BY ticket_count DESC"
    )
    assert report_query_builder.check_query_against_schema(
        sql, _crm_schema(), sqlite=False
    ) == []


def test_check_query_reports_invented_tables_and_columns():
    sql = (
        "SELECT t.subject, t.priority, c.title, z.id FROM tickets t "
        "JOIN company c ON c.id = t.company_id"
    )
    problems = report_query_builder.check_query_against_schema(
        sql, _crm_schema(), sqlite=False
    )
    assert problems[0] == "Table `company` does not exist. Did you mean: companies?"
    assert problems[1].startswith("Column `priority` does not exist in table `tickets`.")
    assert "Its columns are: id, subject, status" in problems[1]
    assert problems[2].startswith("`z.id` uses `z`")
    # c is the alias of the unknown table; it is not reported a second time.
    assert len(problems) == 3


def test_check_query_skips_validation_without_schema():
    assert report_query_builder.check_query_against_schema(
        "SELECT x.y FROM nowhere x", {"tables": []}
    ) == []


def test_dry_run_returns_database_error(monkeypatch):
    import asyncio
    import sqlite3

    executed = {}

    async def fetch_many(sql, size):
        executed["sql"] = sql
        executed["size"] = size
        raise sqlite3.OperationalError("no such column: t.nope")

    monkeypatch.setattr(report_query_builder.db, "fetch_many", fetch_many)
    error = asyncio.run(
        report_query_builder.dry_run_query(
            "SELECT t.nope FROM tickets t WHERE t.company_id = {{current.company}}"
        )
    )
    assert error == "no such column: t.nope"
    assert executed == {
        "sql": "SELECT t.nope FROM tickets t WHERE t.company_id = NULL",
        "size": 1,
    }


def test_dry_run_passes_valid_query(monkeypatch):
    import asyncio

    async def fetch_many(sql, size):
        return []

    monkeypatch.setattr(report_query_builder.db, "fetch_many", fetch_many)
    assert asyncio.run(report_query_builder.dry_run_query("SELECT 1")) is None


def test_extract_ai_sql_handles_reasoning_and_prose():
    response = {
        "response": (
            "<think>The user wants tickets {maybe}.</think>\n"
            'Sure! Here is the query:\n{"sql": "SELECT t.id FROM `tickets` t;", '
            '"summary": "Tickets"}\nLet me know.'
        )
    }
    assert report_query_builder.extract_ai_sql(response) == (
        "SELECT t.id FROM `tickets` t",
        "Tickets",
    )


def test_extract_ai_sql_handles_fenced_and_bare_sql():
    fenced = "Here you go:\n```sql\nSELECT id FROM tickets;\n```\nThis lists tickets."
    assert report_query_builder.extract_ai_sql({"response": fenced})[0] == (
        "SELECT id FROM tickets"
    )
    bare = "The query is SELECT id FROM tickets WHERE subject = 'a;b'; it lists tickets."
    assert report_query_builder.extract_ai_sql({"response": bare})[0] == (
        "SELECT id FROM tickets WHERE subject = 'a;b'"
    )
    assert report_query_builder.extract_ai_sql({"response": "I cannot help."}) == (
        "",
        "Query generated.",
    )


def test_extract_ai_sql_accepts_query_key():
    assert report_query_builder.extract_ai_sql(
        {"response": '{"query": "SELECT id FROM tickets", "explanation": "All"}'}
    ) == ("SELECT id FROM tickets", "All")


def test_configured_ai_context_tokens(monkeypatch):
    monkeypatch.delenv("REPORT_QUERY_BUILDER_NUM_CTX", raising=False)
    assert report_query_builder.configured_ai_context_tokens() == 8192
    monkeypatch.setenv("REPORT_QUERY_BUILDER_NUM_CTX", "16384")
    assert report_query_builder.configured_ai_context_tokens() == 16384
    monkeypatch.setenv("REPORT_QUERY_BUILDER_NUM_CTX", "lots")
    assert report_query_builder.configured_ai_context_tokens() == 8192


def test_extract_ai_sql_handles_module_json_response():
    response = {
        "response": {
            "response": '```json\n{"sql":"SELECT id FROM tickets","summary":"Open tickets"}\n```'
        }
    }
    assert report_query_builder.extract_ai_sql(response) == (
        "SELECT id FROM tickets",
        "Open tickets",
    )


def test_extract_ai_sql_handles_chat_message_content():
    response = {
        "response": {
            "message": {
                "role": "assistant",
                "content": '{"sql":"SELECT id FROM tickets","summary":"All tickets"}',
            }
        }
    }
    assert report_query_builder.extract_ai_sql(response) == (
        "SELECT id FROM tickets",
        "All tickets",
    )


def test_extract_ai_sql_handles_direct_json_payload():
    assert report_query_builder.extract_ai_sql(
        {"sql": "SELECT id FROM tickets", "summary": "Tickets"}
    ) == ("SELECT id FROM tickets", "Tickets")


def test_derive_table_group_groups_schema_by_feature_prefix():
    assert report_query_builder._derive_table_group("tickets_messages") == "tickets"
    assert report_query_builder._derive_table_group("company_notes") == "company"
    assert report_query_builder._derive_table_group("users") == "users"


def test_configured_ai_model_uses_report_query_builder_env(monkeypatch):
    monkeypatch.setenv("REPORT_QUERY_BUILDER_MODEL", "qwen2.5-coder:7b")
    assert report_query_builder.configured_ai_model() == "qwen2.5-coder:7b"


def test_configured_ai_model_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("REPORT_QUERY_BUILDER_MODEL", raising=False)
    assert report_query_builder.configured_ai_model() is None


def test_ai_query_builder_js_includes_csrf_fallback_and_detail_errors():
    source = (
        Path(__file__).resolve().parent.parent / "app/static/js/report-query-builder.js"
    ).read_text(encoding="utf-8")
    assert 'input[name="_csrf"]' in source
    assert "body.set('_csrf', token)" in source
    assert "new URLSearchParams()" in source
    assert "'Content-Type': 'application/x-www-form-urlencoded'" in source
    assert "Array.isArray(detail) && detail.length" in source
    assert "typeof data?.sql !== 'string' || !data.sql.trim()" in source
    assert "Server returned no SQL query." in source
    assert "groupedTables" in source
    assert "toggleTable" in source
    assert "expandedTables.add(table.name)" in source


def test_visual_builder_sql_round_trip_node_suite():
    """Run the Visual/AI/SQL mode-sync parser tests in Node.js."""
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to run the query builder SQL tests")
    suite = Path(__file__).parent / "test_report_query_sql.js"
    result = subprocess.run(
        [node, "--test", str(suite)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
