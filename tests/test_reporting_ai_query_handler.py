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
        lambda: SimpleNamespace(_require_super_admin_page=fake_require_super_admin_page),
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
        lambda: SimpleNamespace(_require_super_admin_page=fake_require_super_admin_page),
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
