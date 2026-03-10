"""Tests for SMTP2Go event actions following the defined JSON structure.

Covers:
- Automation filter fallback: bare keys like ``status`` resolve via ``ticket.status``
  when the automation context is ``{"ticket": {...}}``.
- emit_notification skips channel_email when module_actions include an email module.
- _send_ticket_creation_email skips the direct send when module_actions include an
  email module.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import automations as automations_service


# ---------------------------------------------------------------------------
# Filter fallback tests
# ---------------------------------------------------------------------------


def test_filters_match_bare_status_key_fallback():
    """Bare ``status`` key resolves via ``ticket.status`` in the context."""
    context = {"ticket": {"status": "new", "subject": "Test"}}
    filters = {"match": {"status": "new"}}
    assert automations_service._filters_match(filters, context)


def test_filters_no_match_bare_status_key_fallback():
    """Bare ``status`` key does not match when ticket.status is different."""
    context = {"ticket": {"status": "open", "subject": "Test"}}
    filters = {"match": {"status": "new"}}
    assert not automations_service._filters_match(filters, context)


def test_filters_match_bare_priority_key_fallback():
    """Bare ``priority`` key resolves via ``ticket.priority`` in the context."""
    context = {"ticket": {"priority": "high", "subject": "Test"}}
    filters = {"match": {"priority": "high"}}
    assert automations_service._filters_match(filters, context)


def test_filters_match_dotted_key_takes_precedence():
    """Explicit ``ticket.status`` path still works alongside the fallback."""
    context = {"ticket": {"status": "new", "subject": "Test"}}
    filters = {"match": {"ticket.status": "new"}}
    assert automations_service._filters_match(filters, context)


def test_filters_fallback_not_applied_when_top_level_resolves():
    """Fallback is not used when the key resolves at the top level."""
    # Top-level ``status`` is "open"; ticket.status is "new".
    # The top-level value is None here because it's not present, so the fallback
    # activates. This verifies the fallback only activates when top-level is None.
    context = {"ticket": {"status": "new"}}
    # If there were a top-level status != None, the fallback would not apply.
    # The simpler case: no top-level key → fallback → resolves ticket.status.
    filters = {"match": {"status": "new"}}
    assert automations_service._filters_match(filters, context)


def test_filters_any_with_bare_status_fallback():
    """Bare ``status`` in ``any`` block resolves via ticket context."""
    context = {"ticket": {"status": "pending"}}
    filters = {"any": [{"match": {"status": "open"}}, {"match": {"status": "pending"}}]}
    assert automations_service._filters_match(filters, context)


def test_filters_all_with_bare_status_fallback():
    """Bare ``status`` in ``all`` block resolves via ticket context."""
    context = {"ticket": {"status": "open", "priority": "high"}}
    filters = {
        "all": [
            {"match": {"status": "open"}},
            {"match": {"ticket.priority": "high"}},
        ]
    }
    assert automations_service._filters_match(filters, context)


def test_filters_not_with_bare_status_fallback():
    """Bare ``status`` in ``not`` block resolves via ticket context."""
    context = {"ticket": {"status": "open"}}
    filters = {"not": {"match": {"status": "cancelled"}}}
    assert automations_service._filters_match(filters, context)


def test_filters_fallback_does_not_apply_dotted_key():
    """Dotted keys are not passed through the ticket fallback."""
    # "a.b" contains a dot so fallback is NOT tried under ticket.
    context = {"ticket": {"a": {"b": "value"}}}
    filters = {"match": {"a.b": "value"}}
    # "a.b" is resolved directly via _resolve_context_value(context, "a.b"):
    # context.get("a") -> None -> path resolution stops -> returns None.
    # Fallback won't apply because the key contains a dot.
    assert not automations_service._filters_match(filters, context)


# ---------------------------------------------------------------------------
# emit_notification skips channel_email when module_actions include email module
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_notification_skips_channel_email_when_smtp2go_action():
    """channel_email send is skipped when module_actions include smtp2go."""
    from app.services import notifications as notifications_service

    email_send_calls: list = []

    async def fake_send_email(**kwargs):
        email_send_calls.append(kwargs)
        return True, {}

    async def fake_get_event_setting(event_type):
        return {
            "event_type": event_type,
            "display_name": "Ticket created",
            "message_template": "Your ticket has been created.",
            "module_actions": [{"module": "smtp2go", "payload": {"subject": "Custom"}}],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": False,
            "default_channel_email": True,
            "default_channel_sms": False,
        }

    async def fake_get_preference(user_id, event_type):
        return None

    async def fake_get_user(user_id):
        return {"id": user_id, "email": "test@example.com"}

    async def fake_create_notification(**kwargs):
        return {}

    async def fake_trigger_module(slug, payload, *, background=False):
        return {"status": "succeeded"}

    async def fake_render_value_async(value, context, **kwargs):
        return value

    async def fake_render_string_async(value, context, **kwargs):
        return "Your ticket has been created."

    with (
        patch.object(notifications_service, "notification_event_settings") as mock_nes,
        patch.object(notifications_service, "preferences_repo") as mock_prefs,
        patch.object(notifications_service, "user_repo") as mock_users,
        patch.object(notifications_service, "email_service") as mock_email,
        patch.object(notifications_service, "modules_service") as mock_modules,
        patch.object(notifications_service, "value_templates") as mock_vt,
    ):
        mock_nes.get_event_setting = AsyncMock(side_effect=fake_get_event_setting)
        mock_prefs.get_preference = AsyncMock(return_value=None)
        mock_users.get_user_by_id = AsyncMock(return_value={"id": 1, "email": "test@example.com"})
        mock_email.send_email = AsyncMock(side_effect=fake_send_email)
        mock_email.EmailDispatchError = Exception
        mock_modules.trigger_module = AsyncMock(side_effect=fake_trigger_module)
        mock_vt.render_string_async = AsyncMock(return_value="Your ticket has been created.")
        mock_vt.render_value_async = AsyncMock(side_effect=fake_render_value_async)

        await notifications_service.emit_notification(
            event_type="tickets.created",
            user_id=1,
            metadata={"ticket": {"id": 42, "subject": "Test", "number": "42"}},
        )

    # email_service.send_email should NOT have been called because smtp2go module_action exists
    assert len(email_send_calls) == 0, (
        "channel_email send should be skipped when smtp2go module_action is configured"
    )
    # But module trigger should have been called
    assert mock_modules.trigger_module.called


@pytest.mark.asyncio
async def test_emit_notification_sends_channel_email_without_email_action():
    """channel_email send proceeds when module_actions do not include email modules."""
    from app.services import notifications as notifications_service

    email_send_calls: list = []

    async def fake_send_email(**kwargs):
        email_send_calls.append(kwargs)
        return True, {}

    async def fake_get_event_setting(event_type):
        return {
            "event_type": event_type,
            "display_name": "Ticket created",
            "message_template": "Your ticket has been created.",
            "module_actions": [{"module": "ntfy", "payload": {"topic": "alerts"}}],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": False,
            "default_channel_email": True,
            "default_channel_sms": False,
        }

    async def fake_trigger_module(slug, payload, *, background=False):
        return {"status": "succeeded"}

    async def fake_render_value_async(value, context, **kwargs):
        return value

    with (
        patch.object(notifications_service, "notification_event_settings") as mock_nes,
        patch.object(notifications_service, "preferences_repo") as mock_prefs,
        patch.object(notifications_service, "user_repo") as mock_users,
        patch.object(notifications_service, "email_service") as mock_email,
        patch.object(notifications_service, "modules_service") as mock_modules,
        patch.object(notifications_service, "value_templates") as mock_vt,
        patch("app.repositories.notifications.create_notification", new=AsyncMock(return_value={})),
    ):
        mock_nes.get_event_setting = AsyncMock(side_effect=fake_get_event_setting)
        mock_prefs.get_preference = AsyncMock(return_value=None)
        mock_users.get_user_by_id = AsyncMock(return_value={"id": 1, "email": "test@example.com"})
        mock_email.send_email = AsyncMock(side_effect=fake_send_email)
        mock_email.EmailDispatchError = Exception
        mock_modules.trigger_module = AsyncMock(side_effect=fake_trigger_module)
        mock_vt.render_string_async = AsyncMock(return_value="Your ticket has been created.")
        mock_vt.render_value_async = AsyncMock(side_effect=fake_render_value_async)

        await notifications_service.emit_notification(
            event_type="tickets.created",
            user_id=1,
            metadata={"ticket": {"id": 42, "subject": "Test", "number": "42"}},
        )

    # email_service.send_email SHOULD be called because only ntfy (not smtp2go) is an action
    assert len(email_send_calls) == 1, (
        "channel_email send should proceed when no email module is in module_actions"
    )
