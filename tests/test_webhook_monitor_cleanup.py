from __future__ import annotations

import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone

from app.services import webhook_monitor


def test_purge_completed_events_logs_deleted(monkeypatch):
    captured: dict[str, datetime] = {}

    async def fake_delete(cutoff: datetime) -> int:
        captured["cutoff"] = cutoff
        return 5

    log_calls: list[tuple[str, dict[str, int]]] = []

    def fake_log_info(message: str, **context: int) -> None:
        log_calls.append((message, context))

    monkeypatch.setattr(webhook_monitor, "log_info", fake_log_info)
    monkeypatch.setattr(webhook_monitor.webhook_repo, "delete_succeeded_before", fake_delete)

    deleted = asyncio.run(webhook_monitor.purge_completed_events(retention=timedelta(hours=6)))

    assert deleted == 5
    assert "cutoff" in captured
    assert captured["cutoff"].tzinfo == timezone.utc
    assert log_calls == [("Purged delivered webhook events", {"count": 5})]


def test_purge_completed_events_skips_logging_when_empty(monkeypatch):
    async def fake_delete(_: datetime) -> int:
        return 0

    log_calls: list[tuple[str, dict[str, int]]] = []

    def fake_log_info(message: str, **context: int) -> None:
        log_calls.append((message, context))

    monkeypatch.setattr(webhook_monitor, "log_info", fake_log_info)
    monkeypatch.setattr(webhook_monitor.webhook_repo, "delete_succeeded_before", fake_delete)

    deleted = asyncio.run(webhook_monitor.purge_completed_events(retention=timedelta(hours=6)))

    assert deleted == 0
    assert log_calls == []


def test_webhook_retry_action_enabled_for_succeeded_events():
    template = Path("app/templates/admin/webhooks.html").read_text()
    retry_button = template.split("data-webhook-retry", 1)[1].split(">", 1)[0]

    assert "event.status == 'succeeded'" not in retry_button
    assert "disabled" not in retry_button
