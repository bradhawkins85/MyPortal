"""Scheduled and onboarding RMM scripts, against the real migrations (SQLite)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_automation as automation_repo
from app.repositories import tags as tags_repo
from app.services import rmm_automation as automation
from app.services import rmm_scripts
from app.services.rmm_scripts import RunRequestError
from tests.test_rmm_scripts import POWERSHELL, _enrol, _sync, anyio_backend, sqlite_db  # noqa: F401 - fixtures

SECRET_SCRIPT = "param([Parameter(Mandatory)][string]$Site, [securestring]$Password)\nWrite-Output $env:TENANT\n"


async def _scripts(monkeypatch):
    await _sync(monkeypatch, {
        "rmm/Common/Setup.ps1": SECRET_SCRIPT,
        "rmm/Common/Second.ps1": "Write-Output 2\n",
        "rmm/Common/Third.ps1": "Write-Output 3\n",
        "rmm/Companies/Contoso/Local.ps1": "Write-Output local\n",
    })
    return {script["name"]: script for script in await rmm_repo.list_scripts()}


async def _make_due(fake, schedule_id):
    await fake.execute("UPDATE rmm_schedules SET next_run_at = '2000-01-01 00:00:00' WHERE id = %s", (schedule_id,))


def test_next_runs_follow_the_time_zone():
    after = datetime(2026, 1, 1, 0, 0)
    sydney = automation.next_runs("0 2 * * *", "Australia/Sydney", after=after, count=2)
    # 02:00 in Sydney (UTC+11 in January) is 15:00 UTC the day before.
    assert sydney == [datetime(2026, 1, 1, 15, 0), datetime(2026, 1, 2, 15, 0)]
    assert automation.next_run_after("*/15 * * * *", "UTC", after) == datetime(2026, 1, 1, 0, 15)
    with pytest.raises(RunRequestError) as exc:
        automation.validate_cron("61 * * * *")
    assert "cron" in exc.value.errors
    with pytest.raises(RunRequestError):
        automation.validate_timezone("Mars/Base")


@pytest.mark.anyio
async def test_schedule_validation_and_kept_secrets(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    with pytest.raises(RunRequestError) as exc:
        await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
            "name": "", "script_id": scripts["Setup"]["id"], "cron": "0 2 * * *", "target_mode": "assets",
        })
    assert {"name", "assets", "param:Site"} <= set(exc.value.errors)
    with pytest.raises(RunRequestError) as exc:
        await automation.save_schedule(schedule_id=None, company_id=None, user_id=9, payload={
            "name": "x", "script_id": scripts["Local"]["id"], "cron": "0 2 * * *",
        })
    assert "Common" in exc.value.errors["script"]
    with pytest.raises(RunRequestError) as exc:
        await automation.save_schedule(schedule_id=None, company_id=2, user_id=9, payload={
            "name": "x", "script_id": scripts["Local"]["id"], "cron": "0 2 * * *",
        })
    assert "script" in exc.value.errors

    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        "name": "Nightly", "script_id": scripts["Setup"]["id"], "cron": "0 2 * * *", "timezone": "UTC",
        "values": {"param:Site": "{{company.name}}", "param:Password": "hunter2"},
    })
    stored = await sqlite_db.fetch_one("SELECT * FROM rmm_schedules WHERE id = %s", (schedule_id,))
    assert "hunter2" not in stored["entries_encrypted"] and "hunter2" not in stored["inputs_json"]
    schedule = await automation_repo.get_schedule(schedule_id, with_secrets=True)
    assert schedule["next_run_at"] is not None and schedule["is_enabled"]
    shown = automation.editable_values(scripts["Setup"], automation.decrypt_entries(schedule["entries_encrypted"]))
    assert shown == {"param:Site": "{{company.name}}", "param:Password": rmm_scripts.SENSITIVE_MASK}

    # Saving the masked value keeps the secret; the other value changes.
    await automation.save_schedule(schedule_id=schedule_id, company_id=1, user_id=9, payload={
        "name": "Nightly", "script_id": scripts["Setup"]["id"], "cron": "0 3 * * *", "enabled": False,
        "values": {"param:Site": "HQ", "param:Password": rmm_scripts.SENSITIVE_MASK},
    })
    schedule = await automation_repo.get_schedule(schedule_id, with_secrets=True)
    entries = automation.decrypt_entries(schedule["entries_encrypted"])
    assert entries == {"param:Site": "HQ", "param:Password": "hunter2"}
    assert schedule["next_run_at"] is None and not schedule["is_enabled"] and schedule["cron"] == "0 3 * * *"
    assert [item["name"] for item in await automation_repo.list_schedules(company_id=1)] == ["Nightly"]
    assert await automation_repo.list_schedules(company_id=2) == []


@pytest.mark.anyio
async def test_due_schedule_queues_once_per_device_and_targets_tags(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    _r, first = await _enrol(sqlite_db)
    _r, second = await _enrol(sqlite_db, tray_id=6, asset_id=11, uid="agent-uid-0002")

    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        "name": "Every 15", "script_id": scripts["Second"]["id"], "cron": "*/15 * * * *",
    })
    assert await automation.run_due_schedules() == 0
    await _make_due(sqlite_db, schedule_id)
    assert await automation.run_due_schedules() == 1
    runs = await rmm_repo.list_runs(company_id=1)
    assert sorted(run["asset_id"] for run in runs) == [10, 11]
    assert {run["run_source"] for run in runs} == {"schedule"}
    assert {run["schedule_name"] for run in runs} == {"Every 15"}
    schedule = await automation_repo.get_schedule(schedule_id)
    assert schedule["last_run_summary"] == "Queued on 2 devices."
    assert str(schedule["next_run_at"]) > "2001"
    # A queued run expires by the next scheduled time, not after a full day.
    stored = await sqlite_db.fetch_one("SELECT expires_at FROM rmm_script_runs WHERE id = %s", (runs[0]["id"],))
    assert str(stored["expires_at"]) <= str(datetime.utcnow() + timedelta(minutes=16))

    # Devices still working on the last run are not given another.
    jobs = await rmm_scripts.wait_for_jobs(first, wait_seconds=0)
    await rmm_scripts.record_result(first, jobs[0]["id"], {"exit_code": 0})
    await _make_due(sqlite_db, schedule_id)
    assert await automation.run_due_schedules() == 1
    schedule = await automation_repo.get_schedule(schedule_id)
    assert schedule["last_run_summary"] == "Queued on 1 device; 1 still busy with the last run."

    # Tags pick devices; a company tag reaches every device of that company.
    server, _ = await tags_repo.get_or_create_tag("Server")
    await sqlite_db.execute("INSERT INTO asset_tags (asset_id, tag_id, source) VALUES (11, %s, 'manual')", (server["id"],))
    tagged = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        "name": "Servers", "script_id": scripts["Third"]["id"], "cron": "0 * * * *", "target_mode": "tags",
        "tag_ids": [server["id"], 999],
    })
    assert (await automation_repo.get_schedule(tagged))["tag_ids"] == [server["id"]]
    result = await automation.run_schedule(tagged)
    assert [item["asset_id"] for item in result["queued"]] == [11]

    chosen = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        "name": "One PC", "script_id": scripts["Third"]["id"], "cron": "0 * * * *", "target_mode": "assets",
        "asset_ids": [10],
    })
    assert [item["asset_id"] for item in (await automation.run_schedule(chosen))["queued"]] == [10]
    with pytest.raises(RunRequestError):
        await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
            "name": "Other company", "script_id": scripts["Third"]["id"], "cron": "0 * * * *",
            "target_mode": "assets", "asset_ids": [20],
        })

    # Every-company schedules reach other companies' devices too.
    await _enrol(sqlite_db, tray_id=7, asset_id=20, uid="agent-uid-0003")
    await sqlite_db.execute("UPDATE rmm_agents SET company_id = 2 WHERE agent_uid = 'agent-uid-0003'")
    everywhere = await automation.save_schedule(schedule_id=None, company_id=None, user_id=9, payload={
        "name": "Everywhere", "script_id": scripts["Third"]["id"], "cron": "0 * * * *",
    })
    assert sorted(item["asset_id"] for item in (await automation.run_schedule(everywhere))["queued"]) == [10, 11, 20]
    assert "Everywhere" in [item["name"] for item in await automation_repo.list_schedules(company_id=2)]


@pytest.mark.anyio
async def test_schedule_for_a_removed_script_records_why(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        "name": "Gone", "script_id": scripts["Second"]["id"], "cron": "0 * * * *",
    })
    await rmm_repo.deactivate_scripts([scripts["Second"]["path"]])
    result = await automation.run_schedule(schedule_id)
    assert result["queued"] == [] and "no longer" in result["summary"]


async def _tags(fake):
    workstation, _ = await tags_repo.get_or_create_tag("Workstation")
    server, _ = await tags_repo.get_or_create_tag("Server")
    await fake.execute("INSERT INTO asset_tags (asset_id, tag_id, source) VALUES (10, %s, 'auto')", (workstation["id"],))
    return workstation["id"], server["id"]


async def _onboarding_steps(fake, scripts, *, continue_second=False):
    workstation, server = await _tags(fake)
    await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
        "script_id": scripts["Setup"]["id"], "tag_ids": [workstation],
        "values": {"param:Site": "{{company.name}}", "param:Password": "pw"},
    })
    await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
        "script_id": scripts["Second"]["id"], "tag_ids": [server, workstation], "continue_on_failure": continue_second,
    })
    await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
        "script_id": scripts["Third"]["id"], "tag_ids": [server],
    })
    await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
        "script_id": scripts["Local"]["id"], "tag_ids": [workstation],
    })
    return workstation, server


async def _finish_current(agent, exit_code=0):
    jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert len(jobs) == 1, jobs
    await rmm_scripts.record_result(agent, jobs[0]["id"], {"exit_code": exit_code})
    await automation.run_finished(jobs[0]["id"])
    return jobs[0]


@pytest.mark.anyio
async def test_onboarding_runs_steps_one_at_a_time_on_first_enrolment(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    await _onboarding_steps(sqlite_db, scripts)
    result, agent = await _enrol(sqlite_db)
    assert result["created"] is True
    onboarding_id = await automation.on_agent_enrolled(result["agent_id"])
    assert onboarding_id

    first = await _finish_current(agent)
    assert first["name"] == "Setup" and first["parameters"] == {"Site": "Contoso", "Password": "pw"}
    second = await _finish_current(agent)
    assert second["name"] == "Second"
    third = await _finish_current(agent)  # Third is for servers only, so it is skipped.
    assert third["name"] == "Local"
    assert await rmm_scripts.wait_for_jobs(agent, wait_seconds=0) == []

    onboarding = await automation_repo.get_onboarding_run(onboarding_id)
    assert onboarding["status"] == "completed" and onboarding["failed_steps"] == 0
    assert [entry["status"] for entry in onboarding["plan"]] == ["completed", "completed", "skipped", "completed"]
    assert onboarding["plan"][2]["message"] == "The device has none of this step's tags."
    assert [entry["tags"] for entry in onboarding["plan"]][:3] == [["Workstation"], ["Server", "Workstation"], ["Server"]]
    runs = await rmm_repo.list_runs(company_id=1)
    assert {run["run_source"] for run in runs} == {"onboarding"}

    # Enrolling again (a reinstall) does not onboard the device a second time.
    again, _agent = await _enrol(sqlite_db)
    assert again["created"] is False


@pytest.mark.anyio
async def test_failed_onboarding_step_stops_unless_set_to_continue(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    await _onboarding_steps(sqlite_db, scripts, continue_second=True)
    _r, agent = await _enrol(sqlite_db)
    onboarding_id = await automation.start_onboarding(agent, started_by_user_id=9)
    with pytest.raises(ValueError):
        await automation.start_onboarding(agent)

    await _finish_current(agent)
    await _finish_current(agent, exit_code=1)  # Second continues on failure.
    await _finish_current(agent)
    onboarding = await automation_repo.get_onboarding_run(onboarding_id)
    assert onboarding["status"] == "completed" and onboarding["failed_steps"] == 1
    assert onboarding["error_message"] == "1 step failed."

    restarted = await automation.start_onboarding(agent)
    await _finish_current(agent, exit_code=3)  # Setup stops the sequence.
    onboarding = await automation_repo.get_onboarding_run(restarted)
    assert onboarding["status"] == "failed"
    assert onboarding["error_message"].startswith("Step 1 (Setup) failed")
    assert [entry["status"] for entry in onboarding["plan"]] == ["failed", "not_run", "not_run", "not_run"]
    assert await rmm_scripts.wait_for_jobs(agent, wait_seconds=0) == []


@pytest.mark.anyio
async def test_onboarding_moves_on_when_a_run_expires_and_can_be_cancelled(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    await _onboarding_steps(sqlite_db, scripts)
    _r, agent = await _enrol(sqlite_db)
    onboarding_id = await automation.start_onboarding(agent)
    onboarding = await automation_repo.get_onboarding_run(onboarding_id)
    await sqlite_db.execute(
        "UPDATE rmm_script_runs SET expires_at = '2000-01-01 00:00:00' WHERE id = %s", (onboarding["current_run_id"],)
    )
    await automation.tick.__wrapped__()
    onboarding = await automation_repo.get_onboarding_run(onboarding_id)
    assert onboarding["status"] == "failed" and "expired" in onboarding["error_message"]

    second_id = await automation.start_onboarding(agent)
    onboarding = await automation_repo.get_onboarding_run(second_id)
    assert await automation.cancel_onboarding(onboarding)
    assert (await rmm_repo.get_run(onboarding["current_run_id"]))["status"] == "cancelled"
    assert (await automation_repo.get_onboarding_run(second_id))["status"] == "cancelled"
    assert not await automation.cancel_onboarding(onboarding)


@pytest.mark.anyio
async def test_onboarding_skips_removed_steps_and_reorders(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    await _onboarding_steps(sqlite_db, scripts)
    steps = await automation_repo.list_steps(company_id=1)
    order = [steps[1]["id"], steps[0]["id"], steps[2]["id"], steps[3]["id"]]
    await automation.reorder_steps(1, order)
    assert [step["script_name"] for step in await automation_repo.list_steps(company_id=1)] == [
        "Second", "Setup", "Third", "Local"]
    with pytest.raises(RunRequestError):
        await automation.reorder_steps(1, [steps[0]["id"]])
    assert await automation_repo.list_steps(company_id=2) == []

    _r, agent = await _enrol(sqlite_db)
    onboarding_id = await automation.start_onboarding(agent)
    await automation_repo.delete_step(steps[0]["id"])  # Setup, now second in line.
    await _finish_current(agent)  # Second
    last = await _finish_current(agent)
    assert last["name"] == "Local"
    onboarding = await automation_repo.get_onboarding_run(onboarding_id)
    assert [entry["status"] for entry in onboarding["plan"]] == ["completed", "skipped", "skipped", "completed"]
    assert onboarding["plan"][1]["message"] == "Removed from the onboarding list."
    assert onboarding["status"] == "completed"


@pytest.mark.anyio
async def test_onboarding_steps_need_tags_and_company_tags_count(sqlite_db, monkeypatch):
    scripts = await _scripts(monkeypatch)
    workstation, server = await _tags(sqlite_db)
    with pytest.raises(RunRequestError) as exc:
        await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
            "script_id": scripts["Second"]["id"], "tag_ids": [999],
        })
    assert "tags" in exc.value.errors
    with pytest.raises(RunRequestError):
        await automation.save_step(step_id=None, company_id=2, user_id=9, payload={
            "script_id": scripts["Local"]["id"], "tag_ids": [server],
        })
    step_id = await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
        "script_id": scripts["Second"]["id"], "tag_ids": [server],
    })
    assert (await automation_repo.get_step(step_id))["tag_ids"] == [server]

    _r, agent = await _enrol(sqlite_db)
    assert not await automation.device_matches_tags(agent, [server])
    assert await automation.device_matches_tags(agent, [server, workstation])
    # A tag on the company counts for every one of its devices.
    await sqlite_db.execute("INSERT INTO company_tags (company_id, tag_id) VALUES (1, %s)", (server,))
    assert await automation.device_matches_tags(agent, [server])
    assert not await automation.device_matches_tags({**agent, "asset_id": None}, [server])
    onboarding_id = await automation.start_onboarding(agent)
    assert (await _finish_current(agent))["name"] == "Second"
    assert (await automation_repo.get_onboarding_run(onboarding_id))["status"] == "completed"


@pytest.mark.anyio
async def test_no_onboarding_without_steps(sqlite_db, monkeypatch):
    await _scripts(monkeypatch)
    result, _agent = await _enrol(sqlite_db)
    assert await automation.on_agent_enrolled(result["agent_id"]) is None
    assert json.loads(json.dumps(await automation_repo.list_onboarding_runs(company_id=1))) == []


# ---------------------------------------------------------------------------
# Page and permissions
# ---------------------------------------------------------------------------


def test_automation_page_renders_schedules_steps_and_history():
    from tests.test_rmm_scripts import _environment

    env = _environment()
    env.globals["static_url"] = lambda path: path
    html = env.get_template("rmm/automation.html").render(
        company={"name": "Contoso"},
        schedules=[
            {"id": 3, "name": "<i>Nightly</i>", "scope": "company", "script_name": "Check", "target_mode": "tags",
             "tag_ids": [1, 2], "asset_ids": [], "cron": "0 2 * * *", "timezone": "UTC", "is_enabled": True,
             "script_active": True, "next_run_at": "2026-10-11T02:00:00", "last_run_at": None},
            {"id": 4, "name": "Global", "scope": "all", "script_name": "Patch", "target_mode": "all", "tag_ids": [],
             "asset_ids": [], "cron": "*/5 * * * *", "timezone": "UTC", "is_enabled": False, "script_active": False,
             "next_run_at": None, "last_run_at": "2026-10-10T00:00:00", "last_run_summary": "Queued on 2 devices."},
        ],
        steps=[{"id": 1, "script_name": "Install", "script_path": "rmm/Common/Install.ps1", "script_active": True,
                "inputs": [], "continue_on_failure": False, "tag_names": ["Server", "<b>Lab</b>"]},
               {"id": 2, "script_name": "Old", "script_path": "rmm/Common/Old.ps1", "script_active": True,
                "inputs": [], "continue_on_failure": True, "tag_names": []}],
        onboarding_runs=[{"id": 8, "asset_id": 10, "asset_name": "PC-01", "status": "failed", "failed_steps": 1,
                          "error_message": "Step 1 (Install) failed, so the remaining steps were not run.",
                          "plan": [{"status": "failed"}, {"status": "not_run"}], "started_at": "2026-10-10T00:00:00"}],
        agents=[{"id": 2, "asset_name": "PC-01"}], tags=[{"id": 1, "name": "Server"}],
        can_run=True, is_super_admin=False, default_timezone="UTC",
    )
    assert "&lt;i&gt;Nightly&lt;/i&gt;" in html and "<i>Nightly</i>" not in html
    assert "[New schedule][Scripts]" in html
    assert "Every day at 2am" in html and "*/5 * * * *" in html and "2 tags" in html
    assert 'data-rmm-schedule-edit="3"' in html and 'data-rmm-schedule-edit="4"' not in html
    assert "Script removed from Gitea" in html and "Every company" in html
    assert "data-rmm-step-new" in html and 'data-rmm-step-edit="1"' in html
    assert "&lt;b&gt;Lab&lt;/b&gt;" in html and "No tags: skipped on every device" in html
    assert "1 of 2" in html and "Stopped" in html and 'data-rmm-onboarding-view="8"' in html
    assert '"tags": [{"id": 1, "name": "Server"}]' in html and 'id="rmm-run-modal"' in html


def test_scripts_page_links_to_automation_and_shows_run_sources():
    from tests.test_rmm_scripts import _environment

    env = _environment()
    env.globals["static_url"] = lambda path: path
    html = env.get_template("rmm/scripts.html").render(
        script_groups=[], agents=[], summary={"scripts": 0, "agents": 0, "running": 0, "failed": 0},
        runs=[
            {"id": 1, "script_name": "A", "asset_id": 10, "status": "completed", "run_source": "schedule",
             "schedule_name": "Nightly", "queued_at": "2026-10-10T00:00:00"},
            {"id": 2, "script_name": "B", "asset_id": 10, "status": "completed", "run_source": "onboarding",
             "queued_at": "2026-10-10T00:00:00"},
        ],
        can_run=False, can_sync=False, gitea_ready=True, gitea_message="", company=None,
    )
    assert "[Schedules and onboarding]" in html
    assert "Schedule: Nightly" in html and "Onboarding" in html


def test_every_company_items_need_a_super_admin():
    from fastapi import HTTPException

    from app.features.rmm import automation_routes as routes

    assert routes._scope_company("company", 4, {}) == 4
    assert routes._scope_company("all", 4, {"is_super_admin": True}) is None
    with pytest.raises(HTTPException) as exc:
        routes._scope_company("all", 4, {"is_super_admin": False})
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        routes._check_writable({"company_id": None}, {})
    routes._check_writable({"company_id": 4}, {})
    with pytest.raises(HTTPException) as exc:
        routes._check_visible({"company_id": 5}, 4, "Schedule")
    assert exc.value.status_code == 404
    assert routes._check_visible({"company_id": None}, 4, "Schedule")
    public = routes._schedule_public({"id": 1, "company_id": None, "entries_encrypted": "x"})
    assert "entries_encrypted" not in public and public["scope"] == "all"


def test_rmm_pack_runs_the_automation_loop():
    from app.features.rmm import PACK

    assert automation.automation_loop in PACK.background_jobs
    paths = {route.path for router in PACK.routers for route in router.routes}
    assert {"/rmm/automation", "/api/rmm/schedules", "/api/rmm/onboarding/steps", "/api/rmm/onboarding/runs"} <= paths
