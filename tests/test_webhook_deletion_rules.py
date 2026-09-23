import asyncio
from datetime import datetime, timedelta, timezone

from app.services import webhook_deletion_rules as service


def test_rule_matches_top_level_and_nested_webhook_fields():
    event = {"status": "failed", "response_status": 503, "payload": {"customer": {"tier": "Gold"}}}
    assert service.rule_matches(event, [
        {"field": "status", "operator": "equals", "value": "FAILED"},
        {"field": "payload.customer.tier", "operator": "contains", "value": "old"},
        {"field": "response_status", "operator": "greater_than", "value": 499},
    ])
    assert not service.rule_matches(event, [{"field": "direction", "operator": "equals", "value": "incoming"}])
    assert not service.rule_matches(event, [])


def test_event_rules_delete_only_first_matching_event(monkeypatch):
    deleted = []
    async def rules(*, enabled_only=False):
        return [{"id": 4, "execution_type": "event", "conditions": [{"field": "status", "operator": "equals", "value": "failed"}]}]
    async def delete(event_id): deleted.append(event_id)
    monkeypatch.setattr(service.rules_repo, "list_rules", rules)
    monkeypatch.setattr(service.events_repo, "delete_event", delete)
    assert asyncio.run(service.apply_event_rules({"id": 9, "status": "failed"})) is True
    assert deleted == [9]
    assert asyncio.run(service.apply_event_rules({"id": 10, "status": "succeeded"})) is False


def test_scheduled_rules_run_before_final_retention(monkeypatch):
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    calls = []
    async def rules(*, enabled_only=False):
        return [{"id": 1, "execution_type": "scheduled", "cron_expression": "0 * * * *", "next_run_at": now - timedelta(minutes=1), "conditions": [{"field": "status", "operator": "equals", "value": "failed"}]}]
    async def events(limit=5000): return [{"id": 7, "status": "failed"}, {"id": 8, "status": "succeeded"}]
    async def delete(event_id): calls.append(("rule", event_id))
    async def mark(rule_id, *, next_run_at): calls.append(("mark", rule_id))
    async def retention(): return {"enabled": True, "retention_days": 30}
    async def old(cutoff): calls.append(("retention", cutoff)); return 2
    monkeypatch.setattr(service.rules_repo, "list_rules", rules)
    monkeypatch.setattr(service.rules_repo, "mark_run", mark)
    monkeypatch.setattr(service.rules_repo, "get_retention", retention)
    monkeypatch.setattr(service.events_repo, "list_events", events)
    monkeypatch.setattr(service.events_repo, "delete_event", delete)
    monkeypatch.setattr(service.events_repo, "delete_before", old)
    assert asyncio.run(service.run_scheduled_rules(now)) == 3
    assert calls[0] == ("rule", 7)
    assert calls[-1][0] == "retention"
