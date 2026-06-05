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


def test_list_exclusions(monkeypatch):
    async def fake_fetch_all(sql, params):
        assert "FROM notification_exclusions" in sql
        assert params == (7,)
        return [
            {"id": 1, "event_type": "general", "message_pattern": "Left menu preferences saved.", "excluded_at": None},
            {"id": 2, "event_type": "system.alert", "message_pattern": "", "excluded_at": None},
        ]

    monkeypatch.setattr(exclusions_repo.db, "fetch_all", fake_fetch_all)

    result = asyncio.run(exclusions_repo.list_exclusions(7))

    assert len(result) == 2
    assert result[0]["id"] == 1
    assert result[0]["event_type"] == "general"
    assert result[0]["message_pattern"] == "Left menu preferences saved."
    assert result[1]["id"] == 2
    assert result[1]["message_pattern"] == ""


def test_is_event_type_excluded(monkeypatch):
    async def fake_fetch_one(sql, params):
        assert "FROM notification_exclusions" in sql
        assert params == (7, "system.alert")
        return {"is_excluded": 1}

    monkeypatch.setattr(exclusions_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(exclusions_repo.is_event_type_excluded(7, "system.alert"))

    assert result is True


def test_is_notification_excluded_by_event_type(monkeypatch):
    async def fake_fetch_one(sql, params):
        assert "FROM notification_exclusions" in sql
        assert params == (7, "system.alert", "Some message")
        return {"is_excluded": 1}

    monkeypatch.setattr(exclusions_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(exclusions_repo.is_notification_excluded(7, "system.alert", "Some message"))

    assert result is True


def test_is_notification_excluded_not_found(monkeypatch):
    async def fake_fetch_one(sql, params):
        return None

    monkeypatch.setattr(exclusions_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(exclusions_repo.is_notification_excluded(7, "general", "Some other message"))

    assert result is False


def test_exclude_notification_with_message_pattern(monkeypatch):
    calls: list[tuple[str, tuple[object, ...]]] = []

    async def fake_execute(sql, params):
        calls.append((sql, params))

    monkeypatch.setattr(exclusions_repo.db, "execute", fake_execute)

    result = asyncio.run(
        exclusions_repo.exclude_notification(7, "general", "Left menu preferences saved.")
    )

    assert "INSERT INTO notification_exclusions" in calls[0][0]
    assert calls[0][1] == (7, "general", "Left menu preferences saved.")
    assert result == {
        "event_type": "general",
        "message_pattern": "Left menu preferences saved.",
        "is_excluded": True,
    }


def test_exclude_event_type(monkeypatch):
    calls: list[tuple[str, tuple[object, ...]]] = []

    async def fake_execute(sql, params):
        calls.append((sql, params))

    monkeypatch.setattr(exclusions_repo.db, "execute", fake_execute)

    result = asyncio.run(exclusions_repo.exclude_event_type(7, "system.alert"))

    assert "INSERT INTO notification_exclusions" in calls[0][0]
    assert calls[0][1] == (7, "system.alert", "")
    assert result == {"event_type": "system.alert", "message_pattern": "", "is_excluded": True}


def test_delete_exclusion(monkeypatch):
    async def fake_execute(sql, params):
        assert "DELETE FROM notification_exclusions" in sql
        assert params == (42, 7)
        return 1

    monkeypatch.setattr(exclusions_repo.db, "execute", fake_execute)

    result = asyncio.run(exclusions_repo.delete_exclusion(7, 42))

    assert result is True


def test_delete_exclusion_not_found(monkeypatch):
    async def fake_execute(sql, params):
        return 0

    monkeypatch.setattr(exclusions_repo.db, "execute", fake_execute)

    result = asyncio.run(exclusions_repo.delete_exclusion(7, 99))

    assert result is False


def test_undo_exclude_event_type(monkeypatch):
    calls: list[tuple[str, tuple[object, ...]]] = []

    async def fake_execute(sql, params):
        calls.append((sql, params))

    monkeypatch.setattr(exclusions_repo.db, "execute", fake_execute)

    included = asyncio.run(exclusions_repo.undo_exclude_event_type(7, "system.alert"))

    assert "DELETE FROM notification_exclusions" in calls[0][0]
    assert calls[0][1] == (7, "system.alert")
    assert included == {"event_type": "system.alert", "is_excluded": False}
