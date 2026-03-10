import asyncio
import json
import sys
from contextlib import contextmanager

import pytest

from app.services import modules


async def _noop(*args, **kwargs):
    return None


def _make_fake_event_state(event_id: int = 1) -> dict:
    return {"id": event_id, "status": "pending", "attempt_count": 0}


def _make_fake_webhook_hooks(event_state: dict):
    async def fake_create_manual_event(**kwargs):
        return dict(event_state)

    async def fake_record_attempt(**kwargs):
        pass

    async def fake_mark_event_completed(event_id, *, attempt_number, response_status, response_body):
        event_state.update(
            {
                "status": "succeeded",
                "attempt_count": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    async def fake_mark_event_failed(event_id, *, attempt_number, error_message, response_status, response_body):
        event_state.update(
            {
                "status": "failed",
                "attempt_count": attempt_number,
                "error_message": error_message,
            }
        )

    async def fake_get_event(event_id):
        return dict(event_state)

    return fake_create_manual_event, fake_record_attempt, fake_mark_event_completed, fake_mark_event_failed, fake_get_event


def _make_apprise_module(*, notify_return=True, notify_raises=None):
    """Build a fake apprise module with a tracked Apprise class."""
    added_urls: list[str] = []
    notified: list[dict] = []

    class FakeApprise:
        def add(self, url):
            added_urls.append(url)

        def notify(self, *, body, title):
            notified.append({"body": body, "title": title})
            if notify_raises is not None:
                raise notify_raises
            return notify_return

    class FakeAppriseModule:
        Apprise = FakeApprise

    return FakeAppriseModule(), added_urls, notified


@contextmanager
def _mock_apprise(fake_module):
    sys.modules["apprise"] = fake_module
    try:
        yield
    finally:
        sys.modules.pop("apprise", None)


# ---------------------------------------------------------------------------
# _coerce_settings tests
# ---------------------------------------------------------------------------


def test_coerce_settings_apprise_urls_as_list():
    result = modules._coerce_settings(
        "apprise",
        {"urls": ["slack://tokenA/tokenB", "discord://wid/wtoken"], "title": "Alerts"},
    )
    assert result["urls"] == ["slack://tokenA/tokenB", "discord://wid/wtoken"]
    assert result["title"] == "Alerts"


def test_coerce_settings_apprise_urls_as_newline_string():
    result = modules._coerce_settings(
        "apprise",
        {"urls": "slack://tokenA/tokenB\ndiscord://wid/wtoken\n", "title": ""},
    )
    assert result["urls"] == ["slack://tokenA/tokenB", "discord://wid/wtoken"]
    assert result["title"] == ""


def test_coerce_settings_apprise_strips_blank_urls():
    result = modules._coerce_settings(
        "apprise",
        {"urls": ["slack://tokenA/tokenB", "  ", "", "discord://wid/wtoken"], "title": ""},
    )
    assert result["urls"] == ["slack://tokenA/tokenB", "discord://wid/wtoken"]


def test_coerce_settings_apprise_defaults_to_empty():
    result = modules._coerce_settings("apprise", {})
    assert result["urls"] == []
    assert result["title"] == ""


def test_coerce_settings_apprise_trims_title():
    result = modules._coerce_settings("apprise", {"urls": [], "title": "  My Portal  "})
    assert result["title"] == "My Portal"


# ---------------------------------------------------------------------------
# _invoke_apprise tests
# ---------------------------------------------------------------------------


def test_invoke_apprise_raises_when_no_urls():
    fake_module, _, _ = _make_apprise_module()
    with _mock_apprise(fake_module):
        with pytest.raises(ValueError, match="At least one Apprise notification URL"):
            asyncio.run(
                modules._invoke_apprise({"urls": [], "title": ""}, {"message": "Hello"})
            )


def test_invoke_apprise_success(monkeypatch):
    event_state = _make_fake_event_state(event_id=42)
    fake_create, fake_record_attempt, fake_completed, fake_failed, fake_get = _make_fake_webhook_hooks(event_state)
    fake_module, added_urls, notified = _make_apprise_module(notify_return=True)

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get)

    with _mock_apprise(fake_module):
        result = asyncio.run(
            modules._invoke_apprise(
                {"urls": ["slack://tokenA/tokenB"], "title": "MyPortal"},
                {"message": "Test notification", "title": "Alert"},
            )
        )

    assert result["status"] == "succeeded"
    assert result["event_id"] == 42
    assert notified[0]["body"] == "Test notification"
    assert notified[0]["title"] == "Alert"


def test_invoke_apprise_failure_returns_failed_status(monkeypatch):
    event_state = _make_fake_event_state(event_id=7)
    fake_create, fake_record_attempt, fake_completed, fake_failed, fake_get = _make_fake_webhook_hooks(event_state)
    fake_module, _, _ = _make_apprise_module(notify_return=False)

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get)

    with _mock_apprise(fake_module):
        result = asyncio.run(
            modules._invoke_apprise(
                {"urls": ["slack://bad/token"], "title": ""},
                {"message": "Test"},
            )
        )

    assert result["status"] == "failed"


def test_invoke_apprise_exception_returns_failed_status(monkeypatch):
    event_state = _make_fake_event_state(event_id=9)
    fake_create, fake_record_attempt, fake_completed, fake_failed, fake_get = _make_fake_webhook_hooks(event_state)
    fake_module, _, _ = _make_apprise_module(notify_raises=RuntimeError("Connection refused"))

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get)

    with _mock_apprise(fake_module):
        result = asyncio.run(
            modules._invoke_apprise(
                {"urls": ["slack://bad/token"], "title": ""},
                {"message": "Test"},
            )
        )

    assert result["status"] == "failed"


def test_invoke_apprise_uses_default_title_from_settings(monkeypatch):
    event_state = _make_fake_event_state(event_id=10)
    fake_create, fake_record_attempt, fake_completed, fake_failed, fake_get = _make_fake_webhook_hooks(event_state)
    fake_module, _, notified = _make_apprise_module()

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get)

    with _mock_apprise(fake_module):
        asyncio.run(
            modules._invoke_apprise(
                {"urls": ["slack://tokenA/tokenB"], "title": "Custom Default"},
                {"message": "Hello"},  # No title in payload
            )
        )

    assert notified[0]["title"] == "Custom Default"


def test_invoke_apprise_falls_back_to_myportal_title(monkeypatch):
    event_state = _make_fake_event_state(event_id=11)
    fake_create, fake_record_attempt, fake_completed, fake_failed, fake_get = _make_fake_webhook_hooks(event_state)
    fake_module, _, notified = _make_apprise_module()

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get)

    with _mock_apprise(fake_module):
        asyncio.run(
            modules._invoke_apprise(
                {"urls": ["slack://tokenA/tokenB"], "title": ""},
                {"message": "Hello"},  # No title in payload or settings
            )
        )

    assert notified[0]["title"] == "MyPortal"


def test_invoke_apprise_adds_all_configured_urls(monkeypatch):
    event_state = _make_fake_event_state(event_id=12)
    fake_create, fake_record_attempt, fake_completed, fake_failed, fake_get = _make_fake_webhook_hooks(event_state)
    fake_module, added_urls, _ = _make_apprise_module()

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get)

    with _mock_apprise(fake_module):
        asyncio.run(
            modules._invoke_apprise(
                {
                    "urls": ["slack://tokenA/tokenB", "discord://wid/wtoken"],
                    "title": "",
                },
                {"message": "Multi-target"},
            )
        )

    assert added_urls == ["slack://tokenA/tokenB", "discord://wid/wtoken"]


# ---------------------------------------------------------------------------
# DEFAULT_MODULES contains apprise
# ---------------------------------------------------------------------------


def test_default_modules_contains_apprise():
    apprise_module = next(
        (m for m in modules.DEFAULT_MODULES if m["slug"] == "apprise"), None
    )
    assert apprise_module is not None
    assert apprise_module["name"] == "Apprise"
    assert "urls" in apprise_module["settings"]
    assert "title" in apprise_module["settings"]


# ---------------------------------------------------------------------------
# trigger_module dispatches to apprise handler
# ---------------------------------------------------------------------------


def test_trigger_module_dispatches_to_apprise(monkeypatch):
    event_state = _make_fake_event_state(event_id=20)
    fake_create, fake_record_attempt, fake_completed, fake_failed, fake_get = _make_fake_webhook_hooks(event_state)
    fake_module, _, notified = _make_apprise_module()

    async def fake_get_module(slug: str):
        return {
            "slug": "apprise",
            "enabled": True,
            "settings": {"urls": ["slack://tokenA/tokenB"], "title": "Test"},
        }

    monkeypatch.setattr(modules.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get)

    with _mock_apprise(fake_module):
        result = asyncio.run(
            modules.trigger_module(
                "apprise",
                {"message": "Triggered!"},
                background=False,
            )
        )

    assert result["status"] == "succeeded"
    assert notified[0]["body"] == "Triggered!"
