"""Checks and event log rules take any combination of actions, in the order listed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.features.rmm import check_routes
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_checks as checks_repo
from app.repositories import rmm_event_log_rules as rules_repo
from app.services import rmm_action_steps
from app.services import rmm_actions
from app.services import rmm_checks as checks
from app.services import rmm_event_log_rules as rules
from app.services import rmm_event_logs as event_logs
from tests.test_rmm_checks import _clock, _save, stats_db, tickets  # noqa: F401 - fixtures
from tests.test_rmm_event_log_rules import _entry
from tests.test_rmm_event_logs import event_db  # noqa: F401 - fixture
from tests.test_rmm_scripts import _enrol, _sync, anyio_backend, sqlite_db  # noqa: F401 - fixtures

FIX = "param([string]$Reason)\nWrite-Output fix\n"
REPORT = "param([string]$Reason)\nWrite-Output report\n"
LOW = {"disks": [{"name": "C:", "total_bytes": 100 * checks.GB, "free_bytes": 2 * checks.GB}]}
FINE = {"disks": [{"name": "C:", "total_bytes": 100 * checks.GB, "free_bytes": 50 * checks.GB}]}
DISK = {"name": "Disk", "scope": "asset", "asset_id": "10", "check_type": "disk", "disk_drive": "C:",
        "disk_metric": "free_gb", "disk_operator": "below", "disk_threshold": "10"}


def _actions(*steps):
    """The action list's fields, as the form posts them: each step is (key, type, fields)."""

    form = {"action_key": [key for key, _kind, _fields in steps]}
    for key, kind, fields in steps:
        form[f"action:{key}:type"] = kind
        form.update({f"action:{key}:{name}": value for name, value in fields.items()})
    return form


async def _scripts(monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/Fix.ps1": FIX, "rmm/Common/Report.ps1": REPORT})
    return {script["name"]: script for script in await rmm_repo.list_scripts()}


def _ordered(runs):
    return [run["script_name"] for run in sorted(runs, key=lambda run: run["id"])]


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_actions_are_validated_and_kept_in_order(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    fix, report = str(scripts["Fix"]["id"]), str(scripts["Report"]["id"])

    with pytest.raises(checks.CheckError) as exc:
        await _save({**DISK, **_actions(
            ("t1", "create_ticket", {}), ("t2", "create_ticket", {}),
            ("r1", "run_script", {"script_id": ""}),
            ("x1", "bogus", {}),
            ("c1", "install_choco", {"package_id": "not a package!"}),
            ("s1", "schedule_script", {"script_id": report, "schedule_mode": "time", "run_time": "25:00"}),
        )})
    errors = exc.value.errors
    assert "once" in errors["actions"]
    assert {"action-r1-script", "action-x1-type", "action-c1-package_id", "action-s1-run_time"} <= set(errors)

    check_id = await _save({**DISK, **_actions(
        ("s1", "schedule_script", {"script_id": report, "schedule_mode": "delay", "delay_minutes": "90"}),
        ("t1", "create_ticket", {"auto_close_when_resolved": "1"}),
        ("r1", "run_script", {"script_id": fix, "timeout_seconds": "120"}),
        ("c1", "install_choco", {"package_id": " GoogleChrome "}),
        ("BAD KEY", "run_script", {"script_id": fix}),  # not a key the list makes: ignored
        ("n1", "", {}),  # the empty "add an action" row without JavaScript
    )})
    check = await checks_repo.get_check(check_id)
    assert [(step["key"], step["type"]) for step in check["actions"]] == [
        ("s1", "schedule_script"), ("t1", "create_ticket"), ("r1", "run_script"), ("c1", "install_choco")]
    assert check["actions"][2]["timeout_seconds"] == 120 and check["actions"][3]["package_id"] == "googlechrome"
    # The older single-action columns follow the first action of each kind, for an earlier release.
    assert (check["action"], check["script_id"], check["schedule_mode"], check["delay_minutes"]) == (
        "schedule_script", scripts["Report"]["id"], "delay", 90)
    assert check["auto_close_when_resolved"] is True and check["settings"]["package_id"] == "googlechrome"

    summary = check_routes.check_summary((await rmm_action_steps.with_script_names([check]))[0])
    assert summary["action_steps"][0] == "Run Report 90 minutes later"
    assert summary["action_label"] == ("Run Report 90 minutes later, then create a ticket and close it when resolved, "
                                       "then run Fix, then install Chocolatey package googlechrome")
    assert summary["repeats"] and summary["action_types"][1] == "create_ticket"
    values = check_routes.form_values(check)
    assert [step["key"] for step in values["actions"]] == ["s1", "t1", "r1", "c1"]

    # Reordered and one removed (the removal box of the form without JavaScript).
    await _save({**DISK, **_actions(
        ("r1", "run_script", {"script_id": fix}), ("t1", "create_ticket", {}),
        ("s1", "schedule_script", {"script_id": report, "remove": "1"}),
    )}, check_id=check_id)
    check = await checks_repo.get_check(check_id)
    assert [step["key"] for step in check["actions"]] == ["r1", "t1"]
    assert (check["action"], check["schedule_mode"]) == ("run_script", None)

    # Removing every action leaves the result on the device only.
    await _save({**DISK}, check_id=check_id)
    check = await checks_repo.get_check(check_id)
    assert check["actions"] == [] and check["action"] == "none"
    assert check_routes.check_summary(check)["action_label"] == checks.ACTIONS["none"]


@pytest.mark.anyio
async def test_each_action_keeps_its_own_script_values(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    form = {**DISK, **_actions(
        ("r1", "run_script", {"script_id": str(scripts["Fix"]["id"]), "entry:param:Reason": "fix {{check.result}}"}),
        ("r2", "run_script", {"script_id": str(scripts["Report"]["id"]), "entry:param:Reason": "report"}),
    )}
    check_id = await _save(form)
    saved = await checks_repo.get_check(check_id)
    # A newly chosen script's values are filled in after saving.
    assert await check_routes._needs_script_values(saved, None)
    assert all(step.get("entries_encrypted") is None for step in (await checks_repo.get_check(check_id, with_secrets=True))["actions"])
    await _save(form, check_id=check_id)
    stored = await checks_repo.get_check(check_id, with_secrets=True)
    assert not await check_routes._needs_script_values(stored, saved)
    assert checks.script_values(stored, scripts["Fix"], action_key="r1")[0]["value"] == "fix {{check.result}}"
    assert checks.script_values(stored, scripts["Report"], action_key="r2")[0]["value"] == "report"
    details = await rmm_action_steps.script_details(check_routes.form_values(stored)["actions"], stored)
    assert details["r2"]["saved"] and details["r2"]["fields"][0]["value"] == "report"
    # Values are never sent back without secrets.
    assert "entries_encrypted" not in (await checks_repo.get_check(check_id))["actions"][0]


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_failing_check_takes_every_action_in_order(sqlite_db, stats_db, tickets, monkeypatch):
    scripts = await _scripts(monkeypatch)
    _result, agent = await _enrol(sqlite_db)
    form = {**DISK, "cooldown_minutes": "60", **_actions(
        ("t1", "create_ticket", {}),
        ("r1", "run_script", {"script_id": str(scripts["Fix"]["id"])}),
        ("r2", "run_script", {"script_id": str(scripts["Report"]["id"])}),
        ("s1", "schedule_script", {"script_id": str(scripts["Report"]["id"]), "schedule_mode": "delay",
                                   "delay_minutes": "120"}),
        ("s2", "schedule_script", {"script_id": str(scripts["Fix"]["id"]), "schedule_mode": "delay",
                                   "delay_minutes": "240"}),
    )}
    check_id = await _save(form)
    start = datetime(2026, 10, 12, 9, 0)
    now = _clock(monkeypatch, start)

    await checks.ingest_report(agent, LOW)
    assert len(tickets["tickets"]) == 1
    assert _ordered(await rmm_repo.list_runs(company_id=1)) == ["Fix", "Report"]
    summary = (await checks_repo.get_check(check_id))["last_summary"]
    order = ["Raised ticket #100", "Ran Fix", "Ran Report", "Scheduled Report", "Scheduled Fix"]
    assert [summary.index(text) for text in order] == sorted(summary.index(text) for text in order)
    state = await checks_repo.get_state(check_id, agent["id"])
    assert state["status"] == "failing" and state["scheduled_at"] == start + timedelta(hours=2)

    # Still failing after the cooldown: only the scripts run again, in order.
    now["value"] = start + timedelta(minutes=61)
    await checks.ingest_report(agent, LOW)
    assert _ordered(await rmm_repo.list_runs(company_id=1)) == ["Fix", "Report", "Fix", "Report"]
    assert len(tickets["tickets"]) == 1 and not tickets["replies"]

    # Each scheduled script runs at its own time.
    assert await checks.run_due_scheduled_checks(start + timedelta(hours=2)) == 1
    assert _ordered(await rmm_repo.list_runs(company_id=1))[-1] == "Report"
    assert (await checks_repo.get_state(check_id, agent["id"]))["scheduled_at"] == start + timedelta(hours=4)
    assert await checks.run_due_scheduled_checks(start + timedelta(hours=3)) == 0

    # Passing again drops the script still waiting and notes it on the ticket.
    now["value"] = start + timedelta(hours=3)
    await checks.ingest_report(agent, FINE)
    summary = (await checks_repo.get_check(check_id))["last_summary"]
    assert "Dropped the scheduled script" in summary and "Noted on ticket #100" in summary
    assert await checks.run_due_scheduled_checks(start + timedelta(hours=5)) == 0
    assert len(await rmm_repo.list_runs(company_id=1)) == 5


@pytest.mark.anyio
async def test_one_failed_action_does_not_stop_the_next(sqlite_db, stats_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    _result, agent = await _enrol(sqlite_db)

    async def broken(**_kwargs):
        raise RuntimeError("ticketing is down")

    monkeypatch.setattr(rmm_actions, "raise_ticket", broken)
    check_id = await _save({**DISK, **_actions(
        ("t1", "create_ticket", {}), ("r1", "run_script", {"script_id": str(scripts["Fix"]["id"])}),
    )})
    await checks.ingest_report(agent, LOW)
    assert _ordered(await rmm_repo.list_runs(company_id=1)) == ["Fix"]
    summary = (await checks_repo.get_check(check_id))["last_summary"]
    assert "Create a ticket failed." in summary and "Ran Fix" in summary


@pytest.mark.anyio
async def test_a_run_scheduled_by_an_earlier_release_still_runs(sqlite_db, stats_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    _result, agent = await _enrol(sqlite_db)
    check_id = await _save({**DISK, "action": "schedule_script", "script_id": str(scripts["Report"]["id"]),
                            "schedule_mode": "delay", "delay_minutes": "60"})
    # An earlier release kept one action in the old columns and its run on the device's result.
    await sqlite_db.execute("UPDATE rmm_checks SET actions_json = NULL WHERE id = %s", (check_id,))
    check = await checks_repo.get_check(check_id)
    assert check["actions"] is None and rmm_action_steps.steps(check)[0]["key"] == rmm_action_steps.LEGACY_KEY
    due = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    await checks_repo.save_state(check_id, agent["id"], {**checks._blank_state(), "status": "failing",
                                                         "value_text": "C: 2 GB free (2%)"})
    await sqlite_db.execute("UPDATE rmm_check_states SET scheduled_at = %s WHERE check_id = %s", (due, check_id))
    assert (await checks_repo.get_state(check_id, agent["id"]))["scheduled_at"] == due

    assert await checks.run_due_scheduled_checks() == 1
    assert _ordered(await rmm_repo.list_runs(company_id=1)) == ["Report"]
    assert (await checks_repo.get_state(check_id, agent["id"]))["scheduled_at"] is None
    assert await checks.run_due_scheduled_checks() == 0


# --------------------------------------------------------------------------- #
# Event log rules
# --------------------------------------------------------------------------- #

COMMON = {"user_id": 9, "company_id": 1, "is_super_admin": False}
CONDITIONS = [{"field": "level", "operator": "at_least", "value": "Error"}]


@pytest.mark.anyio
async def test_rule_must_drop_or_take_at_least_one_action(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    with pytest.raises(rules.RuleError) as exc:
        await rules.save_rule(rule_id=None, conditions=CONDITIONS, form={"name": "x", "action": "actions"}, **COMMON)
    assert set(exc.value.errors) == {"actions"}
    # Dropping ignores any actions still in the form.
    rule_id = await rules.save_rule(rule_id=None, conditions=CONDITIONS, form={
        "name": "Noise", "action": "drop", **_actions(("r1", "run_script", {"script_id": str(scripts["Fix"]["id"])}))},
        **COMMON)
    rule = await rules_repo.get_rule(rule_id)
    assert rule["action"] == "drop" and rule["actions"] is None and rmm_action_steps.steps(rule) == []


@pytest.mark.anyio
async def test_matching_rule_takes_every_action_in_order(sqlite_db, event_db, tickets, monkeypatch):
    scripts = await _scripts(monkeypatch)
    _result, agent = await _enrol(sqlite_db)
    form = {"name": "Disk errors", "scope": "asset", "asset_id": "10", "action": "actions", "is_enabled": "1",
            "cooldown_minutes": "30", **_actions(
                ("t1", "create_ticket", {}),
                ("r1", "run_script", {"script_id": str(scripts["Fix"]["id"])}),
                ("r2", "run_script", {"script_id": str(scripts["Report"]["id"])}),
                ("s1", "schedule_script", {"script_id": str(scripts["Report"]["id"]), "schedule_mode": "delay",
                                           "delay_minutes": "30"}),
                ("s2", "schedule_script", {"script_id": str(scripts["Fix"]["id"]), "schedule_mode": "delay",
                                           "delay_minutes": "60"}),
            )}
    rule_id = await rules.save_rule(rule_id=None, conditions=CONDITIONS, form=form, **COMMON)
    rule = await rules_repo.get_rule(rule_id)
    assert rule["action"] == "create_ticket" and [step["key"] for step in rule["actions"]] == ["t1", "r1", "r2", "s1", "s2"]

    stats = await event_logs.ingest(agent, [_entry(1, level=2, event_id=7), _entry(2, level=2, event_id=7)])
    assert (stats["tickets"], stats["runs"], stats["scheduled"]) == (1, 2, 2)
    assert _ordered(await rmm_repo.list_runs(company_id=1)) == ["Fix", "Report"]
    rule = await rules_repo.get_rule(rule_id)
    assert rule["match_count"] == 2  # counted once for the batch, not once per action
    summary = rule["last_run_summary"]
    order = ["Raised ticket #100", "Ran Fix", "Ran Report", "Scheduled Report", "Scheduled Fix"]
    assert [summary.index(text) for text in order] == sorted(summary.index(text) for text in order)
    assert len((await rules_repo.list_pending_for_rules([rule_id]))[rule_id]) == 2

    # Within the cooldown the ticket is updated, but scripts do not run and waiting runs do not stack.
    stats = await event_logs.ingest(agent, [_entry(3, level=2, event_id=7)])
    assert (stats["tickets"], stats["runs"], stats["scheduled"]) == (0, 0, 0)
    assert len(tickets["replies"]) == 1 and len(await rmm_repo.list_runs(company_id=1)) == 2

    later = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=31)
    assert await rules.run_due_scheduled(later) == 1
    assert await rules.run_due_scheduled(later + timedelta(minutes=30)) == 1
    assert _ordered(await rmm_repo.list_runs(company_id=1)) == ["Fix", "Report", "Report", "Fix"]
    assert await rules_repo.list_pending_for_rules([rule_id]) == {}

    # Removing a scheduled action drops its waiting run instead of running it.
    await event_logs.ingest(agent, [_entry(4, level=2, event_id=7)])
    await rules.save_rule(rule_id=rule_id, conditions=CONDITIONS, form={
        **form, **_actions(("t1", "create_ticket", {}), ("s1", "schedule_script", {
            "script_id": str(scripts["Report"]["id"]), "schedule_mode": "delay", "delay_minutes": "30"}))}, **COMMON)
    assert await rules.run_due_scheduled(later + timedelta(hours=2)) == 1  # s1 only; s2 no longer exists


@pytest.mark.anyio
async def test_rule_run_scheduled_by_an_earlier_release_still_runs_and_is_not_doubled(sqlite_db, event_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    _result, agent = await _enrol(sqlite_db)
    rule_id = await rules.save_rule(rule_id=None, conditions=CONDITIONS, form={
        "name": "Later", "scope": "asset", "asset_id": "10", "action": "schedule_script", "is_enabled": "1",
        "script_id": str(scripts["Report"]["id"]), "schedule_mode": "delay", "delay_minutes": "30"}, **COMMON)
    await sqlite_db.execute("UPDATE rmm_event_log_rules SET actions_json = NULL WHERE id = %s", (rule_id,))
    due = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    await rules_repo.add_pending(rule_id, agent["id"], due, None)  # the single run an earlier release kept

    # A match while that run waits does not schedule a second one.
    assert (await event_logs.ingest(agent, [_entry(1, level=2, event_id=7)]))["scheduled"] == 0
    assert await rules.run_due_scheduled() == 1
    assert _ordered(await rmm_repo.list_runs(company_id=1)) == ["Report"]
    assert await rules_repo.get_pending(rule_id, agent["id"]) is None
