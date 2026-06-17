from types import SimpleNamespace

import asyncio

from app.services import chat_ntfy_notifications


def test_new_chat_ntfy_uses_subject_and_title(monkeypatch):
    calls = []

    async def fake_trigger(slug, payload, *, background=True):
        calls.append((slug, payload, background))
        return {"status": "queued"}

    monkeypatch.setattr(
        chat_ntfy_notifications,
        "get_settings",
        lambda: SimpleNamespace(ntfy_chat_new_enabled=True, ntfy_chat_reply_enabled=False),
    )
    monkeypatch.setattr(chat_ntfy_notifications.modules_service, "trigger_module", fake_trigger)

    asyncio.run(chat_ntfy_notifications.notify_new_chat(
        room={"id": 1, "subject": "Cannot print"},
        actor={"display_name": "Jane Customer", "email": "jane@example.com"},
    ))

    assert calls == [
        (
            "ntfy",
            {
                "title": "New Chat",
                "message": "Jane Customer started a new chat: Cannot print",
                "priority": "default",
                "tags": "speech_balloon",
            },
            True,
        )
    ]


def test_chat_reply_ntfy_excludes_body_and_skips_technicians(monkeypatch):
    calls = []

    async def fake_trigger(slug, payload, *, background=True):
        calls.append(payload)
        return {"status": "queued"}

    monkeypatch.setattr(
        chat_ntfy_notifications,
        "get_settings",
        lambda: SimpleNamespace(ntfy_chat_new_enabled=False, ntfy_chat_reply_enabled=True),
    )
    monkeypatch.setattr(chat_ntfy_notifications.modules_service, "trigger_module", fake_trigger)

    asyncio.run(chat_ntfy_notifications.notify_chat_reply(
        room={"id": 2, "subject": "VPN issue"},
        message={"body": "secret reply contents", "sender_display_name": "Jane Customer"},
        actor={"display_name": "Jane Customer", "is_helpdesk_technician": False},
    ))
    asyncio.run(chat_ntfy_notifications.notify_chat_reply(
        room={"id": 2, "subject": "VPN issue"},
        message={"body": "tech response", "sender_display_name": "Tech"},
        actor={"display_name": "Tech", "is_helpdesk_technician": True},
    ))

    assert len(calls) == 1
    assert calls[0]["title"] == "Chat Reply"
    assert calls[0]["message"] == "Jane Customer replied to chat: VPN issue"
    assert "secret reply contents" not in calls[0]["message"]
