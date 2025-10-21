import asyncio
from datetime import datetime, timezone

from app.repositories import uptimekuma_alerts as alerts_repo


def test_create_alert_persists_payload(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_execute_returning_lastrowid(sql, params):
        captured["sql"] = sql.strip()
        captured["params"] = params
        return 42

    async def fake_fetch_one(sql, params):
        captured["fetch"] = (sql.strip(), params)
        if params == (42,):
            return {
                "id": 42,
                "event_uuid": "abc-123",
                "monitor_id": 8,
                "monitor_name": "Example",
                "monitor_url": "https://status.example.com",
                "monitor_type": "http",
                "monitor_hostname": "status.example.com",
                "monitor_port": "443",
                "status": "down",
                "previous_status": "up",
                "importance": 1,
                "alert_type": "incident",
                "reason": "status_code",
                "message": "Service unavailable",
                "duration_seconds": 30.5,
                "ping_ms": 120.0,
                "occurred_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
                "received_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
                "acknowledged_at": None,
                "acknowledged_by": None,
                "remote_addr": "203.0.113.5",
                "user_agent": "uptime-kuma",
                "payload": '{"status":"down"}',
            }
        return None

    monkeypatch.setattr(alerts_repo.db, "execute_returning_lastrowid", fake_execute_returning_lastrowid)
    monkeypatch.setattr(alerts_repo.db, "fetch_one", fake_fetch_one)

    occurred = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    record = asyncio.run(
        alerts_repo.create_alert(
            event_uuid="abc-123",
            monitor_id=8,
            monitor_name="Example",
            monitor_url="https://status.example.com",
            monitor_type="http",
            monitor_hostname="status.example.com",
            monitor_port="443",
            status="down",
            previous_status="up",
            importance=True,
            alert_type="incident",
            reason="status_code",
            message="Service unavailable",
            duration_seconds=30.5,
            ping_ms=120.0,
            occurred_at=occurred,
            remote_addr="203.0.113.5",
            user_agent="uptime-kuma",
            payload={"status": "down"},
        )
    )

    assert captured["params"]["status"] == "down"
    assert record["id"] == 42
    assert record["importance"] is True
    assert record["payload"] == {"status": "down"}
    assert record["occurred_at"].tzinfo == timezone.utc


def test_list_alerts_applies_filters(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "id": 2,
                "event_uuid": None,
                "monitor_id": 5,
                "monitor_name": "API",
                "monitor_url": None,
                "monitor_type": "http",
                "monitor_hostname": None,
                "monitor_port": None,
                "status": "up",
                "previous_status": "down",
                "importance": 0,
                "alert_type": None,
                "reason": None,
                "message": "Recovered",
                "duration_seconds": None,
                "ping_ms": None,
                "occurred_at": None,
                "received_at": datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc),
                "acknowledged_at": None,
                "acknowledged_by": None,
                "remote_addr": None,
                "user_agent": None,
                "payload": "{}",
            }
        ]

    monkeypatch.setattr(alerts_repo.db, "fetch_all", fake_fetch_all)

    records = asyncio.run(
        alerts_repo.list_alerts(
            status="up",
            monitor_id=5,
            importance=False,
            search="api",
            sort_by="monitor_name",
            sort_direction="asc",
            limit=10,
            offset=5,
        )
    )

    assert "status = %s" in captured["sql"]
    assert "monitor_id = %s" in captured["sql"]
    assert captured["params"][-2:] == (10, 5)
    assert records[0]["status"] == "up"
    assert records[0]["payload"] == {}
