"""Checks device rules are enforced by event rules and script automation."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from app.features.rmm import check_routes
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_automation as automation_repo
from app.repositories import rmm_event_log_rules as rules_repo
from app.services import rmm_automation as automation
from app.services import rmm_event_log_rules as event_rules
from app.services.rmm_scripts import RunRequestError
from tests.test_rmm_event_logs import event_db  # noqa: F401
from tests.test_rmm_event_log_rules import _entry
from tests.test_rmm_scripts import _enrol, _sync, anyio_backend, sqlite_db  # noqa: F401
from tests.test_rmm_targeting_integration import target_db, condition, group  # noqa: F401


async def setup(monkeypatch, fake):
    await _sync(monkeypatch, {"rmm/Common/Setup.ps1": "Write-Output ready\n"})
    script = (await rmm_repo.list_scripts())[0]
    _, first = await _enrol(fake)
    _, second = await _enrol(fake, tray_id=6, asset_id=11, uid="agent-uid-0002")
    return script, first, second


def device_rules(first):
    return group("and", condition("agent.id", value=first["id"]),
                 group("or", condition("company.id", value=1), condition("asset.ram_gb", "gte", 32)))


@pytest.mark.anyio
async def test_schedule_round_trip_and_actual_targets(target_db, monkeypatch):
    script, first, second = await setup(monkeypatch, target_db)
    payload = {"name": "Device rules", "script_id": script["id"], "cron": "0 2 * * *", "timezone": "UTC",
               "target_mode": "rules", "target_rules": device_rules(first)}
    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, payload=payload, user_id=9)
    saved = await automation_repo.get_schedule(schedule_id)
    assert saved["target_rules"] == payload["target_rules"]
    assert [agent["id"] for agent in await automation.schedule_targets(saved, script)] == [first["id"]]
    await automation.save_schedule(schedule_id=schedule_id, company_id=1, user_id=9,
                                   payload={**payload, "target_mode": "all"})
    saved = await automation_repo.get_schedule(schedule_id)
    assert saved["target_rules"] is None
    assert {agent["id"] for agent in await automation.schedule_targets(saved, script)} == {first["id"], second["id"]}
    # Malformed persisted targeting cannot broaden a scheduled run.
    await target_db.execute("UPDATE rmm_schedules SET target_rules_json = 'broken' WHERE id = %s", (schedule_id,))
    assert await automation.schedule_targets(await automation_repo.get_schedule(schedule_id), script) == []


@pytest.mark.anyio
async def test_onboarding_rules_skip_nonmatching_devices_and_can_switch_to_all(target_db, monkeypatch):
    script, first, second = await setup(monkeypatch, target_db)
    payload = {"script_id": script["id"], "target_mode": "rules", "target_rules": device_rules(first)}
    step_id = await automation.save_step(step_id=None, company_id=1, payload=payload, user_id=9)
    assert (await automation_repo.get_step(step_id))["target_rules"] == payload["target_rules"]
    other_run = await automation.start_onboarding(second, started_by_user_id=9)
    saved = await automation_repo.get_onboarding_run(other_run)
    assert saved["plan"][0]["status"] == "skipped"
    first_run = await automation.start_onboarding(first, started_by_user_id=9)
    saved = await automation_repo.get_onboarding_run(first_run)
    assert saved["current_run_id"] is not None
    await automation.save_step(step_id=step_id, company_id=1, payload={**payload, "target_mode": "all"}, user_id=9)
    assert (await automation_repo.get_step(step_id))["target_rules"] is None
    assert (await automation_repo.get_step(step_id))["target_mode"] == "all"


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["schedule", "onboarding", "event"])
async def test_invalid_rules_are_rejected_on_save(target_db, monkeypatch, kind):
    script, first, _ = await setup(monkeypatch, target_db)
    payload = {"name": "Invalid", "script_id": script["id"], "cron": "0 2 * * *", "target_mode": "rules",
               "target_rules": group("and", condition("unknown.field", value="x")), "action": "drop"}
    error_type = event_rules.RuleError if kind == "event" else RunRequestError
    with pytest.raises(error_type) as exc:
        if kind == "schedule":
            await automation.save_schedule(schedule_id=None, company_id=1, payload=payload, user_id=9)
        elif kind == "onboarding":
            await automation.save_step(step_id=None, company_id=1, payload=payload, user_id=9)
        else:
            await event_rules.save_rule(rule_id=None, company_id=1, form=payload,
                                       conditions=[{"field": "event_id", "operator": "is", "value": "7"}],
                                       user_id=9, is_super_admin=False)
    assert "target_rules" in exc.value.errors


@pytest.mark.anyio
async def test_event_drop_targets_live_and_stored_entries(target_db, event_db, monkeypatch):
    _, first, second = await setup(monkeypatch, target_db)
    tree = device_rules(first)
    rule_id = await event_rules.save_rule(rule_id=None, company_id=1,
        form={"name": "Noise", "action": "drop", "is_enabled": "1", "target_mode": "rules", "target_rules": json.dumps(tree)},
        conditions=[{"field": "event_id", "operator": "is", "value": "7"}], user_id=9, is_super_admin=False)
    rule = await rules_repo.get_rule(rule_id)
    assert rule["target_rules"] == tree
    entries = [_entry(1, event_id=7)]
    assert (await event_rules.apply(first, entries))[0] == []
    assert (await event_rules.apply(second, entries))[0] == entries
    from app.services import rmm_event_logs
    await rules_repo.set_rule_enabled(rule_id, False, 9)
    await rmm_event_logs.ingest(first, entries)
    await rmm_event_logs.ingest(second, entries)
    assert await event_rules.drop_stored(rule, delete=False) == 1
    assert await event_rules.drop_stored(rule, delete=True) == 1
    assert await event_rules.drop_stored(rule, delete=False) == 0


@pytest.mark.anyio
async def test_delayed_event_action_rechecks_device_rules(target_db, monkeypatch):
    script, first, _ = await setup(monkeypatch, target_db)
    form = {"name": "Later", "action": "schedule_script", "script_id": script["id"], "is_enabled": "1",
            "target_mode": "rules", "target_rules": device_rules(first), "delay_minutes": "5"}
    rule_id = await event_rules.save_rule(rule_id=None, company_id=1, form=form,
        conditions=[{"field": "event_id", "operator": "is", "value": "7"}], user_id=9, is_super_admin=False)
    _, stats = await event_rules.apply(first, [_entry(1, event_id=7)])
    assert stats["scheduled"] == 1
    await target_db.execute("UPDATE rmm_event_log_rule_pending SET run_at = '2000-01-01 00:00:00'")
    await target_db.execute("UPDATE rmm_event_log_rules SET target_rules_json = %s WHERE id = %s",
        (json.dumps(group("and", condition("agent.id", value=999999))), rule_id))
    assert await event_rules.run_due_scheduled() == 0
    assert await target_db.fetch_all("SELECT id FROM rmm_script_runs") == []


@pytest.mark.anyio
async def test_shared_preview_scope_and_script_eligibility(target_db, monkeypatch):
    script, first, _ = await setup(monkeypatch, target_db)
    async def context(request, **kwargs):
        return {"id": 9, "is_super_admin": False}, {"id": 1}, 1, True
    monkeypatch.setattr(check_routes, "_context", context)
    app = FastAPI()
    app.include_router(check_routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/rmm/targeting/fields")
        assert "company.variable" in {field["key"] for field in response.json()["fields"]}
        payload = {"scope": "company", "rules": device_rules(first), "script_id": script["id"]}
        response = await client.post("/api/rmm/targeting/preview", json=payload)
        assert response.status_code == 200
        assert response.json()["matched_count"] == 1
        response = await client.post("/api/rmm/targeting/preview", json={**payload, "scope": "global"})
        assert response.status_code == 403


def test_rule_and_all_onboarding_labels_do_not_warn_about_missing_tags():
    from tests.test_rmm_scripts import _environment
    env = _environment()
    env.globals['static_url'] = lambda path: path
    base = {"script_name": "Setup", "script_path": "rmm/Common/Setup.ps1", "script_active": True,
            "inputs": [], "continue_on_failure": False, "tag_names": []}
    html = env.get_template('rmm/automation.html').render(
        company={"name": "Contoso"}, schedules=[], global_steps=[], onboarding_runs=[], agents=[], tags=[],
        steps=[{**base, "id": 1, "target_mode": "rules"}, {**base, "id": 2, "target_mode": "all"}],
        can_run=True, is_super_admin=False, default_timezone='UTC')
    assert 'Device rules' in html and 'Every device' in html
    assert 'No tags: skipped on every device' not in html
