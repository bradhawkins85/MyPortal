"""Regression tests for reporting AI query handler payload parsing."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.features.reporting import handlers as reporting_handlers


def test_admin_reporting_ai_query_returns_json_error_for_unreadable_form(monkeypatch):
    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def broken_form():
        raise RuntimeError("multipart parser unavailable")

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require_super_admin_page
        ),
    )

    request = SimpleNamespace(form=broken_form)
    response = asyncio.run(reporting_handlers.admin_reporting_ai_query(request))

    assert response.status_code == 400
    assert json.loads(response.body) == {
        "error": "Unable to read the query request payload."
    }


def test_admin_reporting_ai_query_reports_disabled_module_reason(monkeypatch):
    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def form():
        return {"instruction": "show tickets", "current_sql": ""}

    async def describe_schema():
        return {"tables": [], "relations": []}

    async def trigger_module(*_args, **_kwargs):
        return {"status": "skipped", "reason": "Module disabled"}

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require_super_admin_page
        ),
    )
    monkeypatch.setattr(
        "app.services.report_query_builder.describe_schema", describe_schema
    )
    monkeypatch.setattr("app.services.modules.trigger_module", trigger_module)

    response = asyncio.run(
        reporting_handlers.admin_reporting_ai_query(SimpleNamespace(form=form))
    )

    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "Module disabled"}


def test_admin_reporting_ai_query_redacts_raw_module_error(monkeypatch):
    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def form():
        return {"instruction": "show tickets", "current_sql": ""}

    async def describe_schema():
        return {"tables": [], "relations": []}

    async def trigger_module(*_args, **_kwargs):
        return {
            "status": "failed",
            "last_error": "Traceback: ****** select * from users",
        }

    logged = {}

    def fake_log_error(message, **kwargs):
        logged["message"] = message
        logged["kwargs"] = kwargs

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require_super_admin_page
        ),
    )
    monkeypatch.setattr(
        "app.services.report_query_builder.describe_schema", describe_schema
    )
    monkeypatch.setattr("app.services.modules.trigger_module", trigger_module)
    monkeypatch.setattr(reporting_handlers, "log_error", fake_log_error)

    response = asyncio.run(
        reporting_handlers.admin_reporting_ai_query(SimpleNamespace(form=form))
    )

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "error": "The AI query service is unavailable. Check that an LLM module is enabled and configured."
    }
    assert logged["message"] == "Reporting AI query module failed"
    assert logged["kwargs"]["status"] == "failed"
    assert logged["kwargs"]["reason_type"] == "str"
    assert logged["kwargs"]["reason_length"] > 0
    assert "select * from users" not in json.dumps(logged["kwargs"])


def test_admin_reporting_ai_query_preserves_validation_message(monkeypatch):
    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def form():
        return {"instruction": "show tickets", "current_sql": ""}

    async def describe_schema():
        return {"tables": [], "relations": []}

    async def trigger_module(*_args, **_kwargs):
        return {"status": "succeeded", "response": '{"sql": "delete from tickets"}'}

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require_super_admin_page
        ),
    )
    monkeypatch.setattr(
        "app.services.report_query_builder.describe_schema", describe_schema
    )
    monkeypatch.setattr("app.services.modules.trigger_module", trigger_module)

    response = asyncio.run(
        reporting_handlers.admin_reporting_ai_query(SimpleNamespace(form=form))
    )

    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "Only SELECT statements are allowed."}


def test_admin_reporting_ai_query_redacts_unrecognized_validation_error(monkeypatch):
    from app.services import reporting as reporting_service

    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def form():
        return {"instruction": "show tickets", "current_sql": ""}

    async def describe_schema():
        return {"tables": [], "relations": []}

    async def trigger_module(*_args, **_kwargs):
        return {"status": "succeeded", "response": '{"sql": "select 1"}'}

    secret = "password=database-secret\nTraceback: SELECT * FROM credentials"

    def fail_validation(_sql):
        raise reporting_service.ReportingQueryError(secret)

    logged = {}

    def fake_log_error(message, **kwargs):
        logged["message"] = message
        logged["kwargs"] = kwargs

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require_super_admin_page
        ),
    )
    monkeypatch.setattr(
        "app.services.report_query_builder.describe_schema", describe_schema
    )
    monkeypatch.setattr("app.services.modules.trigger_module", trigger_module)
    monkeypatch.setattr("app.services.reporting.validate_select_query", fail_validation)
    monkeypatch.setattr(reporting_handlers, "log_error", fake_log_error)

    response = asyncio.run(
        reporting_handlers.admin_reporting_ai_query(SimpleNamespace(form=form))
    )

    assert response.status_code == 400
    assert json.loads(response.body) == {
        "error": "The generated SQL query did not pass validation."
    }
    assert logged == {
        "message": "Reporting AI query validation failed with an unrecognized code",
        "kwargs": {"error_type": "ReportingQueryError"},
    }
    assert secret not in response.body.decode()
    assert secret not in json.dumps(logged)


def test_admin_reporting_ai_query_reports_missing_skipped_reason_safely(monkeypatch):
    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def form():
        return {"instruction": "show tickets", "current_sql": ""}

    async def describe_schema():
        return {"tables": [], "relations": []}

    async def trigger_module(*_args, **_kwargs):
        return {"status": "skipped"}

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require_super_admin_page
        ),
    )
    monkeypatch.setattr(
        "app.services.report_query_builder.describe_schema", describe_schema
    )
    monkeypatch.setattr("app.services.modules.trigger_module", trigger_module)

    response = asyncio.run(
        reporting_handlers.admin_reporting_ai_query(SimpleNamespace(form=form))
    )

    assert response.status_code == 400
    assert json.loads(response.body) == {
        "error": "The configured LLM module did not generate a query."
    }


_RETRY_SCHEMA = {
    "tables": [
        {
            "name": "tickets",
            "columns": [
                {"name": "id", "type": "int"},
                {"name": "subject", "type": "varchar"},
            ],
        }
    ],
    "relations": [],
}


def _patch_ai_query(monkeypatch, responses, *, dry_run=None):
    async def fake_require_super_admin_page(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def describe_schema():
        return _RETRY_SCHEMA

    calls = []

    async def trigger_module(name, payload, **_kwargs):
        calls.append((name, payload))
        return responses[min(len(calls), len(responses)) - 1]

    async def default_dry_run(_sql):
        return None

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require_super_admin_page
        ),
    )
    monkeypatch.setattr(
        "app.services.report_query_builder.describe_schema", describe_schema
    )
    monkeypatch.setattr(
        "app.services.report_query_builder.dry_run_query", dry_run or default_dry_run
    )
    monkeypatch.setattr("app.services.report_query_builder.db.is_sqlite", lambda: False)
    monkeypatch.setattr("app.services.modules.trigger_module", trigger_module)
    return calls


def _run_ai_query(instruction="show ticket subjects"):
    async def form():
        return {"instruction": instruction, "current_sql": ""}

    return asyncio.run(
        reporting_handlers.admin_reporting_ai_query(SimpleNamespace(form=form))
    )


def test_admin_reporting_ai_query_sends_rules_to_completion_providers(monkeypatch):
    calls = _patch_ai_query(
        monkeypatch,
        [{"status": "succeeded", "response": '{"sql": "SELECT t.subject FROM tickets t", "summary": "Subjects"}'}],
    )

    response = _run_ai_query()

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "sql": "SELECT t.subject FROM tickets t",
        "summary": "Subjects",
    }
    name, payload = calls[0]
    assert name == "ollama"
    assert payload["prompt"].startswith("SYSTEM:\n")
    assert "Never invent tables or columns" in payload["prompt"]
    assert payload["messages"][0]["role"] == "system"
    assert payload["temperature"] == 0.1
    assert payload["num_ctx"] >= 8192
    assert payload["format"] == "json"


def test_admin_reporting_ai_query_repairs_invented_column(monkeypatch):
    calls = _patch_ai_query(
        monkeypatch,
        [
            {"status": "succeeded", "response": '{"sql": "SELECT t.title FROM tickets t"}'},
            {"status": "succeeded", "response": '{"sql": "SELECT t.subject FROM tickets t", "summary": "Fixed"}'},
        ],
    )

    response = _run_ai_query()

    assert response.status_code == 200
    assert json.loads(response.body)["sql"] == "SELECT t.subject FROM tickets t"
    assert len(calls) == 2
    feedback = calls[1][1]["messages"][-1]["content"]
    assert "Column `title` does not exist in table `tickets`" in feedback
    assert calls[1][1]["messages"][-2]["role"] == "assistant"
    assert "Column `title` does not exist" in calls[1][1]["prompt"]


def test_admin_reporting_ai_query_repairs_database_error(monkeypatch):
    async def dry_run(sql):
        return None if "ORDER BY t.id" in sql else "(1064, 'You have an error in your SQL syntax')"

    calls = _patch_ai_query(
        monkeypatch,
        [
            {"status": "succeeded", "response": '{"sql": "SELECT t.id FROM tickets t ORDER t.id"}'},
            {"status": "succeeded", "response": '{"sql": "SELECT t.id FROM tickets t ORDER BY t.id"}'},
        ],
        dry_run=dry_run,
    )

    response = _run_ai_query()

    assert response.status_code == 200
    assert json.loads(response.body)["sql"] == "SELECT t.id FROM tickets t ORDER BY t.id"
    assert "The database rejected it: (1064" in calls[1][1]["messages"][-1]["content"]


def test_admin_reporting_ai_query_returns_best_effort_query_with_warning(monkeypatch):
    async def dry_run(_sql):
        return "(1146, \"Table 'db.tickets' doesn't exist\")"

    calls = _patch_ai_query(
        monkeypatch,
        [{"status": "succeeded", "response": '{"sql": "SELECT t.id FROM tickets t"}'}],
        dry_run=dry_run,
    )

    response = _run_ai_query()

    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["sql"] == "SELECT t.id FROM tickets t"
    assert body["warnings"] == [reporting_handlers._AI_QUERY_REJECTED_WARNING]
    # The raw database error is fed back to the model but never to the client.
    assert "1146" not in response.body.decode()
    assert len(calls) == 3


def test_admin_reporting_ai_query_keeps_query_when_repair_request_fails(monkeypatch):
    _patch_ai_query(
        monkeypatch,
        [
            {"status": "succeeded", "response": '{"sql": "SELECT t.title FROM tickets t"}'},
            {"status": "failed", "last_error": "HTTP 500"},
        ],
    )

    response = _run_ai_query()

    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["sql"] == "SELECT t.title FROM tickets t"
    assert body["warnings"][0].startswith("Column `title` does not exist")


def test_admin_reporting_ai_query_warns_when_dry_run_unavailable(monkeypatch):
    async def dry_run(_sql):
        raise RuntimeError("Database pool not initialised")

    calls = _patch_ai_query(
        monkeypatch,
        [{"status": "succeeded", "response": '{"sql": "SELECT t.id FROM tickets t"}'}],
        dry_run=dry_run,
    )

    response = _run_ai_query()

    assert json.loads(response.body)["warnings"] == [
        reporting_handlers._AI_QUERY_UNVERIFIED_WARNING
    ]
    assert len(calls) == 1
