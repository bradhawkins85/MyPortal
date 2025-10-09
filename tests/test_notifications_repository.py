import asyncio
from datetime import datetime, timezone

from app.repositories import notifications as notifications_repo


def test_list_notifications_applies_filters(monkeypatch):
    captured = {}

    async def fake_fetch_all(sql, params):
        captured['sql'] = sql
        captured['params'] = params
        return [
            {
                'id': 1,
                'user_id': 42,
                'event_type': 'system',
                'message': 'hello world',
                'metadata': None,
                'created_at': datetime.now(timezone.utc),
                'read_at': None,
            }
        ]

    monkeypatch.setattr(notifications_repo.db, 'fetch_all', fake_fetch_all)

    records = asyncio.run(
        notifications_repo.list_notifications(
            user_id=42,
            read_state='unread',
            event_types=['system'],
            search='Error',
            created_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            created_to=datetime(2025, 1, 2, tzinfo=timezone.utc),
            sort_by='event_type',
            sort_direction='asc',
            limit=25,
            offset=10,
        )
    )

    assert captured['sql'].startswith('SELECT id, user_id, event_type')
    assert 'event_type IN' in captured['sql']
    assert captured['params'][0] == 42
    assert tuple(captured['params'][-2:]) == (25, 10)
    assert records[0]['metadata'] is None


def test_mark_read_bulk_preserves_requested_order(monkeypatch):
    executed = {}

    async def fake_execute(sql, params):
        executed['sql'] = sql
        executed['params'] = params

    async def fake_fetch_all(sql, params):
        return [
            {
                'id': 2,
                'user_id': 1,
                'event_type': 'alert',
                'message': 'two',
                'metadata': None,
                'created_at': datetime.now(timezone.utc),
                'read_at': datetime.now(timezone.utc),
            },
            {
                'id': 1,
                'user_id': 1,
                'event_type': 'alert',
                'message': 'one',
                'metadata': None,
                'created_at': datetime.now(timezone.utc),
                'read_at': datetime.now(timezone.utc),
            },
        ]

    monkeypatch.setattr(notifications_repo.db, 'execute', fake_execute)
    monkeypatch.setattr(notifications_repo.db, 'fetch_all', fake_fetch_all)

    results = asyncio.run(notifications_repo.mark_read_bulk([1, 2, 2]))

    assert [item['id'] for item in results] == [1, 2]
    assert 'UPDATE notifications SET read_at' in executed['sql']
    assert tuple(executed['params'][1:]) == (1, 2)


def test_count_notifications_returns_integer(monkeypatch):
    async def fake_fetch_one(sql, params):
        return {'count': '5'}

    monkeypatch.setattr(notifications_repo.db, 'fetch_one', fake_fetch_one)

    result = asyncio.run(notifications_repo.count_notifications(user_id=10))

    assert result == 5
