"""Tests for notification event settings service – pure helper functions."""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.services.notification_event_settings import (
    _coerce_bool,
    _ensure_module_actions,
    _merge_setting,
    list_event_settings,
    get_event_setting,
    update_event_setting,
)
from app.repositories import notification_event_settings as settings_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# _coerce_bool
# ---------------------------------------------------------------------------


def test_coerce_bool_true_bool():
    assert _coerce_bool(True, False) is True


def test_coerce_bool_false_bool():
    assert _coerce_bool(False, True) is False


def test_coerce_bool_integer_truthy():
    assert _coerce_bool(1, False) is True
    assert _coerce_bool(42, False) is True


def test_coerce_bool_integer_falsy():
    assert _coerce_bool(0, True) is False


def test_coerce_bool_string_true_variants():
    for value in ("true", "True", "TRUE", "1", "yes", "YES", "on", "ON"):
        assert _coerce_bool(value, False) is True, f"expected True for {value!r}"


def test_coerce_bool_string_false_variants():
    for value in ("false", "False", "FALSE", "0", "no", "NO", "off", "OFF"):
        assert _coerce_bool(value, True) is False, f"expected False for {value!r}"


def test_coerce_bool_unknown_string_returns_default():
    assert _coerce_bool("maybe", True) is True
    assert _coerce_bool("maybe", False) is False


def test_coerce_bool_none_returns_default():
    assert _coerce_bool(None, True) is True
    assert _coerce_bool(None, False) is False


def test_coerce_bool_float_truthy():
    assert _coerce_bool(0.5, False) is True


def test_coerce_bool_float_zero():
    assert _coerce_bool(0.0, True) is False


# ---------------------------------------------------------------------------
# _ensure_module_actions
# ---------------------------------------------------------------------------


def test_ensure_module_actions_empty_input():
    assert _ensure_module_actions(None) == []
    assert _ensure_module_actions([]) == []
    assert _ensure_module_actions("") == []


def test_ensure_module_actions_single_mapping():
    result = _ensure_module_actions({"module": "slack", "payload": {"msg": "hi"}})
    assert result == [{"module": "slack", "payload": {"msg": "hi"}}]


def test_ensure_module_actions_list_of_mappings():
    actions = [
        {"module": "slack", "payload": {"channel": "#general"}},
        {"module": "email", "payload": {}},
    ]
    result = _ensure_module_actions(actions)
    assert len(result) == 2
    assert result[0]["module"] == "slack"
    assert result[1]["module"] == "email"


def test_ensure_module_actions_strips_module_whitespace():
    result = _ensure_module_actions([{"module": "  slack  ", "payload": {}}])
    assert result[0]["module"] == "slack"


def test_ensure_module_actions_skips_entries_without_module():
    actions = [
        {"module": "", "payload": {}},
        {"payload": {}},
        {"module": "email", "payload": {"to": "user@example.com"}},
    ]
    result = _ensure_module_actions(actions)
    assert len(result) == 1
    assert result[0]["module"] == "email"


def test_ensure_module_actions_none_payload_defaults_to_empty_dict():
    result = _ensure_module_actions([{"module": "sms", "payload": None}])
    assert result[0]["payload"] == {}


def test_ensure_module_actions_list_payload_preserved():
    result = _ensure_module_actions([{"module": "hook", "payload": [1, 2, 3]}])
    assert result[0]["payload"] == [1, 2, 3]


def test_ensure_module_actions_non_mapping_entries_ignored():
    result = _ensure_module_actions([42, "bad", {"module": "ok", "payload": {}}])
    assert len(result) == 1
    assert result[0]["module"] == "ok"


# ---------------------------------------------------------------------------
# _merge_setting
# ---------------------------------------------------------------------------


def test_merge_setting_unknown_event_type_uses_defaults():
    merged = _merge_setting("custom_event_xyz", None)
    assert merged["event_type"] == "custom_event_xyz"
    # Defaults from _BOOL_FIELDS
    assert merged["is_user_visible"] is True
    assert merged["allow_channel_in_app"] is True
    assert merged["default_channel_in_app"] is True
    assert merged["allow_channel_email"] is False
    assert merged["allow_channel_sms"] is False


def test_merge_setting_overrides_display_name():
    merged = _merge_setting("custom_event_xyz", {"display_name": "My Custom Event"})
    assert merged["display_name"] == "My Custom Event"


def test_merge_setting_overrides_message_template():
    merged = _merge_setting(
        "custom_event_xyz", {"message_template": "Hello {{ name }}"}
    )
    assert merged["message_template"] == "Hello {{ name }}"


def test_merge_setting_empty_message_template_falls_back_to_default():
    merged = _merge_setting("custom_event_xyz", {"message_template": ""})
    assert merged["message_template"] == "{{ message }}"


def test_merge_setting_overrides_bool_fields():
    merged = _merge_setting(
        "custom_event_xyz",
        {"allow_channel_email": True, "default_channel_email": True},
    )
    assert merged["allow_channel_email"] is True
    assert merged["default_channel_email"] is True


def test_merge_setting_overrides_description():
    merged = _merge_setting("custom_event_xyz", {"description": "A helpful note"})
    assert merged["description"] == "A helpful note"


def test_merge_setting_clears_description_when_empty_string():
    merged = _merge_setting("custom_event_xyz", {"description": ""})
    assert merged["description"] is None


def test_merge_setting_overrides_module_actions():
    overrides = {
        "module_actions": [{"module": "slack", "payload": {"channel": "#alerts"}}]
    }
    merged = _merge_setting("custom_event_xyz", overrides)
    assert merged["module_actions"] == [
        {"module": "slack", "payload": {"channel": "#alerts"}}
    ]


# ---------------------------------------------------------------------------
# Async service functions (list_event_settings, get_event_setting,
# update_event_setting) – tested with monkeypatched repos
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_event_settings_returns_merged_results(monkeypatch):
    import app.services.notification_event_settings as nes_module

    monkeypatch.setattr(
        settings_repo,
        "list_settings",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        nes_module,
        "DEFAULT_NOTIFICATION_EVENTS",
        {
            "ticket.created": {
                "display_name": "Ticket Created",
                "message_template": "{{ message }}",
            }
        },
    )
    monkeypatch.setattr(
        nes_module,
        "merge_event_types",
        lambda *args, **kwargs: ["ticket.created"],
    )

    results = await list_event_settings()
    assert len(results) == 1
    assert results[0]["event_type"] == "ticket.created"
    assert results[0]["display_name"] == "Ticket Created"


@pytest.mark.anyio
async def test_list_event_settings_hides_non_user_visible(monkeypatch):
    import app.services.notification_event_settings as nes_module

    monkeypatch.setattr(
        settings_repo,
        "list_settings",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        nes_module,
        "DEFAULT_NOTIFICATION_EVENTS",
        {
            "system.internal": {
                "display_name": "Internal",
                "is_user_visible": False,
            },
            "ticket.created": {"display_name": "Ticket Created"},
        },
    )
    monkeypatch.setattr(
        nes_module,
        "merge_event_types",
        lambda *args, **kwargs: ["system.internal", "ticket.created"],
    )

    results = await list_event_settings(include_hidden=False)
    event_types = [r["event_type"] for r in results]
    assert "system.internal" not in event_types
    assert "ticket.created" in event_types


@pytest.mark.anyio
async def test_get_event_setting_merges_stored_overrides(monkeypatch):
    import app.services.notification_event_settings as nes_module

    monkeypatch.setattr(
        settings_repo,
        "get_setting",
        AsyncMock(return_value={"display_name": "Overridden"}),
    )
    monkeypatch.setattr(
        nes_module,
        "DEFAULT_NOTIFICATION_EVENTS",
        {"ticket.created": {"display_name": "Ticket Created"}},
    )

    result = await get_event_setting("ticket.created")
    assert result["display_name"] == "Overridden"


@pytest.mark.anyio
async def test_update_event_setting_upserts_and_merges(monkeypatch):
    import app.services.notification_event_settings as nes_module

    upsert_mock = AsyncMock(
        return_value={"display_name": "New Label", "message_template": "{{ x }}"}
    )
    monkeypatch.setattr(settings_repo, "upsert_setting", upsert_mock)
    monkeypatch.setattr(
        nes_module,
        "DEFAULT_NOTIFICATION_EVENTS",
        {"ticket.created": {"display_name": "Ticket Created"}},
    )

    result = await update_event_setting(
        "ticket.created", {"display_name": "New Label"}
    )
    upsert_mock.assert_awaited_once_with(
        "ticket.created", {"display_name": "New Label"}
    )
    assert result["display_name"] == "New Label"
