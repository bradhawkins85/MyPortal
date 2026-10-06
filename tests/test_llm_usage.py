"""Tests for LLM usage tracking and the Administration > LLM Usage page."""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.core.database import Database, db
from app.repositories import llm_usage as usage_repo
from app.services import llm_usage
from app.services import modules
from app.services import webhook_monitor as webhook_monitor_service

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "460_llm_usage_events.sql"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_extract_token_usage_reads_ollama_and_openai_payloads():
    assert llm_usage.extract_token_usage({"prompt_eval_count": 12, "eval_count": 30}) == (12, 30)
    assert llm_usage.extract_token_usage(
        {"usage": {"prompt_tokens": 5, "completion_tokens": 7}}
    ) == (5, 7)
    assert llm_usage.extract_token_usage(
        {"usage": {"input_tokens": 3, "output_tokens": 4}}
    ) == (3, 4)
    assert llm_usage.extract_token_usage({"usage": None}) is None
    assert llm_usage.extract_token_usage({"response": "hi"}) is None
    assert llm_usage.extract_token_usage("not json") is None


def test_feature_for_trigger_uses_caller_stack_and_module_slug():
    # Simulate trigger_module("ollama", ...) being called from
    # app.services.tickets.refresh_ticket_ai_summary.
    namespace = {"__name__": "app.services.tickets", "llm_usage": llm_usage}
    exec(
        "def refresh_ticket_ai_summary():\n"
        "    return llm_usage.feature_for_trigger('ollama', {'prompt': 'x'})\n",
        namespace,
    )
    assert namespace["refresh_ticket_ai_summary"]() == "ticket_ai_summary"

    assert llm_usage.feature_for_trigger("ai-rename-ticket", {}) == "ai_rename_ticket"
    assert llm_usage.feature_for_trigger("ollama", {"usage_feature": "custom"}) == "custom"
    assert llm_usage.feature_for_trigger("smtp", {}) is None
    assert llm_usage.feature_for_trigger("ollama", {}) is None


def test_current_feature_prefers_context_variable():
    assert llm_usage.current_feature() == llm_usage.UNATTRIBUTED_FEATURE
    token = llm_usage.set_current_feature("agent")
    try:
        assert llm_usage.current_feature() == "agent"
    finally:
        llm_usage.reset_current_feature(token)
    assert llm_usage.current_feature() == llm_usage.UNATTRIBUTED_FEATURE


def test_resolve_date_range_defaults_and_swaps():
    today = date(2026, 10, 6)
    assert llm_usage.resolve_date_range(None, None, today=today) == (date(2026, 9, 7), today)
    assert llm_usage.resolve_date_range("2026-10-05", "2026-10-01", today=today) == (
        date(2026, 10, 1),
        date(2026, 10, 5),
    )
    assert llm_usage.resolve_date_range("bad", "2026-10-03", today=today) == (
        date(2026, 9, 4),
        date(2026, 10, 3),
    )


def test_build_report_calculates_token_share_and_lists_unused_functions(monkeypatch):
    async def fake_summarise(start, end):
        return {
            "requests": 4, "input_tokens": 600, "output_tokens": 400,
            "failed_requests": 1, "estimated_requests": 0,
        }

    async def fake_by_feature(start, end):
        return [
            {"feature": "agent", "requests": 3, "input_tokens": 500, "output_tokens": 250,
             "failed_requests": 0, "last_used_at": "2026-10-02 09:30:00"},
            {"feature": "legacy_caller", "requests": 1, "input_tokens": 100, "output_tokens": 150,
             "failed_requests": 1, "last_used_at": datetime(2026, 10, 1, 8, 0)},
        ]

    async def fake_by_model(start, end):
        return [{"provider": "ollama", "model": "llama3", "requests": 4,
                 "input_tokens": 600, "output_tokens": 400}]

    async def fake_by_day(start, end):
        return [{"day": "2026-10-02", "requests": 3, "input_tokens": 500, "output_tokens": 250}]

    monkeypatch.setattr(usage_repo, "summarise", fake_summarise)
    monkeypatch.setattr(usage_repo, "usage_by_feature", fake_by_feature)
    monkeypatch.setattr(usage_repo, "usage_by_model", fake_by_model)
    monkeypatch.setattr(usage_repo, "usage_by_day", fake_by_day)

    report = asyncio.run(llm_usage.build_report(date(2026, 10, 1), date(2026, 10, 3)))

    assert report["totals"]["total_tokens"] == 1000
    features = {row["feature"]: row for row in report["features"]}
    assert features["agent"]["share"] == 75.0
    assert features["agent"]["label"] == "MyPortal Agent"
    assert features["agent"]["last_used_at"] == "2026-10-02T09:30:00Z"
    assert features["legacy_caller"]["share"] == 25.0
    assert features["legacy_caller"]["last_used_at"] == "2026-10-01T08:00:00Z"
    # Every known LLM function is listed even with no usage in the range.
    assert set(llm_usage.FEATURES_BY_KEY) <= set(features)
    assert features["ticket_ai_summary"]["requests"] == 0
    assert features["ticket_ai_summary"]["share"] == 0.0
    assert report["features"][0]["feature"] == "agent"
    assert report["models"][0]["share"] == 100.0
    assert [day["day"] for day in report["days"]] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert report["days"][1]["input_tokens"] == 500
    assert report["days"][0]["requests"] == 0


class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self.text = json.dumps(payload)
        self.request = httpx.Request("POST", "http://example.com")

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.posts: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        self.posts.append(json)
        return self._response


def _patch_webhook_monitor(monkeypatch):
    state = {"id": 9, "status": "pending", "attempt_count": 0}

    async def fake_create_event(**kwargs):
        return dict(state)

    async def fake_noop(*args, **kwargs):
        return None

    async def fake_mark_completed(event_id, *, attempt_number, response_status, response_body):
        state.update(status="succeeded", response_status=response_status, response_body=response_body)

    async def fake_get_event(event_id):
        return dict(state)

    monkeypatch.setattr(webhook_monitor_service, "create_manual_event", fake_create_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_noop)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_noop)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)


def _capture_usage(monkeypatch) -> list[dict]:
    recorded: list[dict] = []

    async def fake_record_event(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(usage_repo, "record_event", fake_record_event)
    return recorded


def test_invoke_ollama_records_reported_tokens_for_current_feature(monkeypatch):
    _patch_webhook_monitor(monkeypatch)
    recorded = _capture_usage(monkeypatch)
    client = _FakeClient(_FakeResponse({"response": "Done", "prompt_eval_count": 42, "eval_count": 7}))
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client)

    async def run():
        token = llm_usage.set_current_feature("ticket_ai_tags")
        try:
            return await modules._invoke_ollama(
                {"base_url": "http://127.0.0.1:11434", "model": "llama3"}, {"prompt": "Tag this"}
            )
        finally:
            llm_usage.reset_current_feature(token)

    result = asyncio.run(run())

    assert result["status"] == "succeeded"
    assert len(recorded) == 1
    event = recorded[0]
    assert event["feature"] == "ticket_ai_tags"
    assert event["provider"] == "ollama"
    assert event["model"] == "llama3"
    assert event["status"] == "succeeded"
    assert (event["input_tokens"], event["output_tokens"]) == (42, 7)
    assert event["tokens_estimated"] is False
    assert event["webhook_event_id"] == 9


def test_invoke_ollama_estimates_tokens_when_provider_omits_usage(monkeypatch):
    _patch_webhook_monitor(monkeypatch)
    recorded = _capture_usage(monkeypatch)
    client = _FakeClient(
        _FakeResponse({"choices": [{"message": {"content": "abcdefgh"}}]})
    )
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client)

    asyncio.run(
        modules._invoke_ollama(
            {"provider": "llamacpp", "base_url": "http://127.0.0.1:8080", "model": "qwen"},
            {"prompt": "x" * 40},
        )
    )

    assert recorded[0]["feature"] == llm_usage.UNATTRIBUTED_FEATURE
    assert recorded[0]["tokens_estimated"] is True
    assert (recorded[0]["input_tokens"], recorded[0]["output_tokens"]) == (10, 2)


def test_trigger_module_attributes_ai_ticket_action(monkeypatch):
    seen: list[str] = []

    async def fake_handler(settings, payload, *, event_future=None):
        seen.append(llm_usage.current_feature())
        return {"status": "succeeded"}

    async def fake_get_module(slug):
        return {"slug": slug, "enabled": True, "settings": {}}

    async def fake_consent(slug, payload):
        return None

    monkeypatch.setattr(modules, "_invoke_ai_classify_ticket", fake_handler)
    monkeypatch.setattr(modules.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules, "_ai_consent_block", fake_consent)
    monkeypatch.setattr(modules, "_get_always_on_ticket_action_module", lambda slug: None)

    asyncio.run(modules.trigger_module("ai-classify-ticket", {"ticket_id": 1}, background=False))

    assert seen == ["ai_classify_ticket"]
    # The context variable does not leak into the caller after the module returns.
    assert llm_usage.current_feature() == llm_usage.UNATTRIBUTED_FEATURE


@pytest.mark.anyio
async def test_repository_aggregates_on_sqlite(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Database()
        test_db._settings = Settings(
            SESSION_SECRET="test-secret", TOTP_ENCRYPTION_KEY="A" * 64,
            DB_HOST=None, DB_USER=None, DB_PASSWORD=None, DB_NAME=None,
        )
        test_db._use_sqlite = True
        db_path = Path(tmpdir) / "usage.db"
        test_db._get_sqlite_path = lambda: db_path
        await test_db.connect()
        try:
            sql = test_db._adapt_sql_for_sqlite(MIGRATION.read_text())
            for statement in test_db._split_sql_statements(sql):
                await test_db.execute(statement)
            monkeypatch.setattr(usage_repo, "db", test_db)

            for occurred_at, feature, status, tokens in (
                (datetime(2026, 10, 1, 9), "agent", "succeeded", (100, 50)),
                (datetime(2026, 10, 1, 23, 59), "agent", "failed", (0, 0)),
                (datetime(2026, 10, 2, 10), "ticket_ai_tags", "succeeded", (30, 20)),
                (datetime(2026, 10, 4, 0, 0), "agent", "succeeded", (999, 999)),
            ):
                await usage_repo.record_event(
                    occurred_at=occurred_at, feature=feature, provider="ollama",
                    model="llama3", status=status, input_tokens=tokens[0],
                    output_tokens=tokens[1], tokens_estimated=False,
                    duration_ms=10, webhook_event_id=None,
                )

            start, end = datetime(2026, 10, 1), datetime(2026, 10, 4)
            totals = await usage_repo.summarise(start, end)
            assert totals == {
                "requests": 3, "input_tokens": 130, "output_tokens": 70,
                "failed_requests": 1, "estimated_requests": 0,
            }
            by_feature = {row["feature"]: row for row in await usage_repo.usage_by_feature(start, end)}
            assert by_feature["agent"]["requests"] == 2
            assert by_feature["ticket_ai_tags"]["input_tokens"] == 30
            days = await usage_repo.usage_by_day(start, end)
            assert [(row["day"], row["requests"]) for row in days] == [
                ("2026-10-01", 2), ("2026-10-02", 1),
            ]
            models = await usage_repo.usage_by_model(start, end)
            assert models[0]["requests"] == 3
        finally:
            await test_db.disconnect()


@pytest.fixture
def page_client(monkeypatch):
    async def fake_async_none(*args, **kwargs):
        return None

    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    monkeypatch.setattr(db, "connect", fake_async_none)
    monkeypatch.setattr(db, "disconnect", fake_async_none)
    monkeypatch.setattr(db, "run_migrations", fake_async_none)
    monkeypatch.setattr(main_module.scheduler_service, "start", fake_async_none)
    monkeypatch.setattr(main_module.scheduler_service, "stop", fake_async_none)
    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)

    async def fake_build_report(start_date, end_date):
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "totals": {"requests": 2, "input_tokens": 1500, "output_tokens": 500,
                       "total_tokens": 2000, "failed_requests": 0, "estimated_requests": 1},
            "features": [
                {"feature": "agent", "label": "MyPortal Agent", "description": "Agent chat.",
                 "requests": 2, "input_tokens": 1500, "output_tokens": 500, "total_tokens": 2000,
                 "failed_requests": 0, "share": 100.0, "last_used_at": "2026-10-02T09:30:00Z"},
                {"feature": "ticket_ai_tags", "label": "Ticket AI tags", "description": "Tags.",
                 "requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                 "failed_requests": 0, "share": 0.0, "last_used_at": None},
            ],
            "models": [{"provider": "ollama", "model": "llama3", "requests": 2,
                        "input_tokens": 1500, "output_tokens": 500, "total_tokens": 2000,
                        "share": 100.0}],
            "days": [
                {"day": "2026-10-01", "requests": 0, "input_tokens": 0, "output_tokens": 0},
                {"day": "2026-10-02", "requests": 2, "input_tokens": 1500, "output_tokens": 500},
            ],
        }

    monkeypatch.setattr(llm_usage, "build_report", fake_build_report)
    # Not used as a context manager so the app's startup workers do not run.
    yield TestClient(main_module.app, follow_redirects=False)


def test_llm_usage_page_renders_date_range_and_function_share(page_client):
    response = page_client.get("/admin/llm-usage?start=2026-10-01&end=2026-10-02")

    assert response.status_code == 200
    body = response.text
    assert 'name="start" value="2026-10-01"' in body
    assert 'name="end" value="2026-10-02"' in body
    assert "MyPortal Agent" in body
    assert "Ticket AI tags" in body
    assert "100.0%" in body
    assert "1,500" in body
    assert "estimated" in body
