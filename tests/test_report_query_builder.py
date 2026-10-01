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
    assert "exactly one MySQL SELECT" in messages[0]["content"]
    assert "Never invent tables or columns" in messages[0]["content"]
    assert "Current query to refine" in messages[1]["content"]
    assert '"tickets"' in messages[1]["content"]
    assert messages[1]["content"].index("show tickets") < messages[1]["content"].index(
        "Database schema:"
    )


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

    messages = report_query_builder.build_ai_messages(
        schema, "show column_1 from table_19"
    )
    schema_text = messages[1]["content"].split("Database schema:\n", 1)[1]
    prompt_schema = json.loads(schema_text)

    assert len(schema_text) <= 600
    assert prompt_schema["tables"][0]["name"] == "table_19"
    assert prompt_schema["omitted_table_count"] > 0
    assert messages[1]["content"].startswith(
        "Requested report or refinement:\nshow column_1 from table_19"
    )


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
