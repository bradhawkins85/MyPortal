import asyncio

from app.repositories import notification_preferences as preferences_repo


def test_upsert_preferences_persists_and_returns_normalised(monkeypatch):
    executed_sql = []

    async def fake_execute(sql, params):
        executed_sql.append((sql, params))

    async def fake_list_preferences(user_id):
        return [
            {
                "event_type": "general",
                "channel_in_app": True,
                "channel_email": False,
                "channel_sms": False,
            },
            {
                "event_type": "custom.event",
                "channel_in_app": False,
                "channel_email": True,
                "channel_sms": False,
            },
        ]

    monkeypatch.setattr(preferences_repo.db, "execute", fake_execute)
    monkeypatch.setattr(preferences_repo, "list_preferences", fake_list_preferences)

    result = asyncio.run(
        preferences_repo.upsert_preferences(
            5,
            [
                {
                    "event_type": "general",
                    "channel_in_app": True,
                    "channel_email": False,
                    "channel_sms": False,
                },
                {
                    "event_type": "custom.event",
                    "channel_in_app": False,
                    "channel_email": True,
                    "channel_sms": False,
                },
            ],
        )
    )

    assert executed_sql
    assert "INSERT INTO notification_preferences" in executed_sql[0][0]
    assert executed_sql[0][1][0] == 5
    assert "DELETE FROM notification_preferences" in executed_sql[-1][0]
    assert any(item["event_type"] == "custom.event" for item in result)


def test_list_preferences_converts_truthy_values(monkeypatch):
    async def fake_fetch_all(sql, params):
        return [
            {
                "event_type": "general",
                "channel_in_app": 1,
                "channel_email": 0,
                "channel_sms": 0,
            },
            {
                "event_type": "custom.event",
                "channel_in_app": 0,
                "channel_email": 1,
                "channel_sms": "1",
            },
        ]

    monkeypatch.setattr(preferences_repo.db, "fetch_all", fake_fetch_all)

    result = asyncio.run(preferences_repo.list_preferences(7))

    general = next(item for item in result if item["event_type"] == "general")
    custom = next(item for item in result if item["event_type"] == "custom.event")
    assert general["channel_in_app"] is True
    assert general["channel_email"] is False
    assert custom["channel_email"] is True
    assert custom["channel_sms"] is True
