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
