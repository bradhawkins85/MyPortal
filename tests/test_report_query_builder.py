from app.services import report_query_builder
from pathlib import Path


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


def test_derive_table_group_groups_schema_by_feature_prefix():
    assert report_query_builder._derive_table_group("tickets_messages") == "tickets"
    assert report_query_builder._derive_table_group("company_notes") == "company"
    assert report_query_builder._derive_table_group("users") == "users"


def test_ai_query_builder_js_includes_csrf_fallback_and_detail_errors():
    source = (Path(__file__).resolve().parent.parent / "app/static/js/report-query-builder.js").read_text(encoding="utf-8")
    assert 'input[name="_csrf"]' in source
    assert "body.set('_csrf', token)" in source
    assert "Array.isArray(detail) && detail.length" in source
    assert "typeof data?.sql !== 'string' || !data.sql.trim()" in source
    assert "Server returned no SQL query." in source
    assert "groupedTables" in source
    assert "toggleTable" in source
    assert "expandedTables.add(table.name)" in source
