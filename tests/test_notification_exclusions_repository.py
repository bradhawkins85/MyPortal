import asyncio

from app.repositories import notification_exclusions as exclusions_repo


def test_list_excluded_event_types(monkeypatch):
    async def fake_fetch_all(sql, params):
        assert "FROM notification_exclusions" in sql
        assert params == (7,)
        return [{"event_type": "system.alert"}, {"event_type": "order.shipped"}]

    monkeypatch.setattr(exclusions_repo.db, "fetch_all", fake_fetch_all)

    result = asyncio.run(exclusions_repo.list_excluded_event_types(7))

    assert result == ["system.alert", "order.shipped"]


def test_is_event_type_excluded(monkeypatch):
    async def fake_fetch_one(sql, params):
        assert "FROM notification_exclusions" in sql
        assert params == (7, "system.alert")
        return {"is_excluded": 1}

    monkeypatch.setattr(exclusions_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(exclusions_repo.is_event_type_excluded(7, "system.alert"))

    assert result is True


def test_exclude_and_undo_event_type(monkeypatch):
    calls: list[tuple[str, tuple[object, ...]]] = []

    async def fake_execute(sql, params):
        calls.append((sql, params))

    monkeypatch.setattr(exclusions_repo.db, "execute", fake_execute)

    excluded = asyncio.run(exclusions_repo.exclude_event_type(7, "system.alert"))
    included = asyncio.run(exclusions_repo.undo_exclude_event_type(7, "system.alert"))

    assert "INSERT INTO notification_exclusions" in calls[0][0]
    assert calls[0][1] == (7, "system.alert")
    assert "DELETE FROM notification_exclusions" in calls[1][0]
    assert calls[1][1] == (7, "system.alert")
    assert excluded == {"event_type": "system.alert", "is_excluded": True}
    assert included == {"event_type": "system.alert", "is_excluded": False}
