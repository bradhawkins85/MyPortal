import asyncio

from app.services import notifications


def test_emit_notification_sends_email(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_get_preference(user_id: int, event_type: str):
        return {"channel_in_app": True, "channel_email": True, "channel_sms": False}

    async def fake_create_notification(**kwargs):
        captured["notification"] = kwargs

    async def fake_get_user_by_id(user_id: int):
        return {"id": user_id, "email": "user@example.com"}

    async def fake_send_email(**kwargs):
        captured["email"] = kwargs
        return True

    monkeypatch.setattr(notifications.preferences_repo, "get_preference", fake_get_preference)
    monkeypatch.setattr(notifications.notifications_repo, "create_notification", fake_create_notification)
    monkeypatch.setattr(notifications.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(notifications.email_service, "send_email", fake_send_email)

    asyncio.run(
        notifications.emit_notification(
            event_type="system.alert",
            message="Important update",
            user_id=5,
            metadata={"source": "test"},
        )
    )

    assert captured["notification"]["event_type"] == "system.alert"
    assert captured["notification"]["message"] == "Important update"
    assert captured["notification"]["user_id"] == 5
    assert captured["email"]["recipients"] == ["user@example.com"]
    assert "Important update" in captured["email"]["html_body"]
