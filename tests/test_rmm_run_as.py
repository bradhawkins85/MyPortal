"""Scripts run as System or as the user logged in to the device."""
from __future__ import annotations

from pathlib import Path

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.features.rmm import automation_routes, routes
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_automation as automation_repo
from app.services import rmm_automation as automation
from app.services import rmm_scripts
from app.services.rmm_scripts import RunRequestError
from tests.test_rmm_scripts import _enrol, _sync, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]


async def _script(monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/Whoami.ps1": "whoami\n"})
    return (await rmm_repo.list_scripts())[0]


async def _capable(agent):
    await rmm_repo.record_agent_checkin(agent["id"], {"run_as_user_supported": True})
    return await rmm_repo.get_agent(agent["id"])


@pytest.mark.anyio
async def test_manual_runs_capture_run_as_and_need_a_capable_agent(sqlite_db, monkeypatch):
    script = await _script(monkeypatch)
    _r, agent = await _enrol(sqlite_db)
    queue = dict(script_id=script["id"], company_id=1, asset_ids=[10], submitted={}, timeout_seconds=60,
                 requested_by_user_id=9)

    with pytest.raises(RunRequestError) as exc:
        await rmm_scripts.queue_runs(**queue, run_as="admin")
    assert "run_as" in exc.value.errors

    as_user = (await rmm_scripts.queue_runs(**queue, run_as="user"))["queued"][0]["run_id"]
    assert (await rmm_repo.get_run(as_user))["run_as"] == "user"
    # An agent that cannot switch users fails the run rather than running it as System.
    assert await rmm_scripts.claim_jobs(agent) == []
    failed = await rmm_repo.get_run(as_user)
    assert failed["status"] == "failed"
    assert failed["error_message"] == "Update the RMM agent before running scripts as the logged-in user."

    agent = await _capable(agent)
    retried = (await rmm_scripts.queue_runs(**queue, run_as="user"))["queued"][0]["run_id"]
    jobs = await rmm_scripts.claim_jobs(agent)
    assert [(job["id"], job["run_as"]) for job in jobs] == [(retried, "user")]
    await rmm_scripts.record_result(agent, retried, {"exit_code": 0})

    default = (await rmm_scripts.queue_runs(**queue))["queued"][0]["run_id"]
    assert (await rmm_repo.get_run(default))["run_as"] == "system"
    assert [job["run_as"] for job in await rmm_scripts.claim_jobs(agent)] == ["system"]


@pytest.mark.anyio
async def test_agents_report_run_as_user_support(sqlite_db):
    result, agent = await _enrol(sqlite_db)
    assert not agent["run_as_user_supported"]
    await rmm_scripts.checkin(agent, {"agent_version": "1.2.0", "run_as_user_supported": True}, None)
    assert (await rmm_repo.get_agent(result["agent_id"]))["run_as_user_supported"]
    # Job polls send no details and must not clear it.
    await rmm_scripts.checkin(agent, {}, None)
    assert (await rmm_repo.get_agent(result["agent_id"]))["run_as_user_supported"]
    # An agent version that does not report it is an older agent.
    await rmm_scripts.checkin(agent, {"agent_version": "1.0.0"}, None)
    assert not (await rmm_repo.get_agent(result["agent_id"]))["run_as_user_supported"]


@pytest.mark.anyio
async def test_schedules_save_run_as_and_queue_with_it(sqlite_db, monkeypatch):
    script = await _script(monkeypatch)
    _r, agent = await _enrol(sqlite_db)
    agent = await _capable(agent)
    payload = {"name": "Who", "script_id": script["id"], "cron": "0 * * * *"}
    with pytest.raises(RunRequestError) as exc:
        await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={**payload, "run_as": "root"})
    assert "run_as" in exc.value.errors

    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9,
                                                 payload={**payload, "run_as": "user"})
    assert (await automation_repo.get_schedule(schedule_id))["run_as"] == "user"
    await automation.run_schedule(schedule_id)
    assert [job["run_as"] for job in await rmm_scripts.claim_jobs(agent)] == ["user"]

    await automation.save_schedule(schedule_id=schedule_id, company_id=1, user_id=9, payload=payload)
    assert (await automation_repo.get_schedule(schedule_id))["run_as"] == "system"


@pytest.mark.anyio
async def test_onboarding_steps_save_run_as_and_queue_with_it(sqlite_db, monkeypatch):
    script = await _script(monkeypatch)
    step_id = await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
        "script_id": script["id"], "target_mode": "all", "run_as": "user",
    })
    assert (await automation_repo.get_step(step_id))["run_as"] == "user"
    result, agent = await _enrol(sqlite_db)
    agent = await _capable(agent)
    assert await automation.start_onboarding(agent, started_by_user_id=9)
    jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert [(job["name"], job["run_as"]) for job in jobs] == [("Whoami", "user")]


def test_requests_accept_only_system_or_user():
    # Leaving it out uses the script's default Run As.
    assert routes.RunRequest().run_as is None
    assert automation_routes.ScheduleRequest(script_id=1, cron="0 * * * *").run_as is None
    assert automation_routes.StepRequest().run_as is None
    assert routes.RunRequest(run_as="user").run_as == "user"
    assert automation_routes.ScheduleRequest(script_id=1, cron="0 * * * *", run_as="user").run_as == "user"
    assert automation_routes.StepRequest(run_as="user").run_as == "user"
    for model, extra in ((routes.RunRequest, {}), (automation_routes.ScheduleRequest, {"script_id": 1, "cron": "* * * * *"}),
                         (automation_routes.StepRequest, {})):
        with pytest.raises(ValidationError):
            model(run_as="administrator", **extra)


def test_editor_offers_system_and_logged_in_user():
    template = (ROOT / "app/templates/rmm/_run_modal.html").read_text()
    assert 'data-rmm-run-as' in template
    assert '<option value="system">System</option>' in template
    assert '<option value="user">Logged in user</option>' in template
    script = (ROOT / "app/static/js/rmm_scripts.js").read_text()
    assert script.count("run_as: runAsSelect.value") == 2  # manual runs and schedules/onboarding steps
    assert "run_as: run.run_as" in script  # retries keep the original choice


@pytest.mark.anyio
async def test_scripts_default_to_system_and_can_default_to_the_logged_in_user(sqlite_db, monkeypatch):
    script = await _script(monkeypatch)
    assert script["default_run_as"] == "system"
    assert routes._script_summary(script)["default_run_as"] == "system"
    _r, agent = await _enrol(sqlite_db)
    agent = await _capable(agent)
    queue = dict(script_id=script["id"], company_id=1, asset_ids=[10], submitted={}, timeout_seconds=60,
                 requested_by_user_id=9)

    await rmm_repo.set_script_run_as(script["id"], "user")
    script = await rmm_repo.get_script(script["id"], with_content=True)
    assert script["default_run_as"] == "user"
    assert routes._script_summary(script)["default_run_as"] == "user"
    with pytest.raises(ValueError):
        await rmm_repo.set_script_run_as(script["id"], "root")

    result = await rmm_scripts.queue_runs(**queue)
    assert result["run_as"] == "user"
    assert (await rmm_repo.get_run(result["queued"][0]["run_id"]))["run_as"] == "user"
    # A technician's explicit choice still wins over the script's default.
    chosen = (await rmm_scripts.queue_runs(**queue, run_as="system"))["queued"][0]["run_id"]
    assert (await rmm_repo.get_run(chosen))["run_as"] == "system"
    # Runs that do not choose, such as rules and tray menu items, use it too.
    implicit = await rmm_scripts.queue_for_agent(
        script=script, agent={**agent, "company_id": 1, "asset_id": 10}, entries={}, inputs=[],
        timeout_seconds=60, requested_by_user_id=None, run_source="event_log_rule",
    )
    assert (await rmm_repo.get_run(implicit))["run_as"] == "user"

    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        "name": "Who", "script_id": script["id"], "cron": "0 * * * *",
    })
    assert (await automation_repo.get_schedule(schedule_id))["run_as"] == "user"
    step_id = await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
        "script_id": script["id"], "target_mode": "all",
    })
    assert (await automation_repo.get_step(step_id))["run_as"] == "user"


@pytest.mark.anyio
async def test_default_run_as_setting_requires_admin_and_visible_script(monkeypatch):
    context = AsyncMock(return_value=({"id": 9}, {}, 1, True))
    monkeypatch.setattr(routes, "_context", context)
    save = AsyncMock()
    monkeypatch.setattr(rmm_repo, "set_script_run_as", save)
    with pytest.raises(HTTPException) as exc:
        await routes._save_script_run_as(None, 7, "user")
    assert exc.value.status_code == 403
    context.return_value = ({"id": 9, "is_super_admin": True}, {}, 1, True)
    lookup = AsyncMock(return_value={"id": 7, "is_active": True, "company_id": 2})
    monkeypatch.setattr(rmm_repo, "get_script", lookup)
    with pytest.raises(HTTPException) as exc:
        await routes._save_script_run_as(None, 7, "user")
    assert exc.value.status_code == 404
    lookup.return_value["company_id"] = None
    with pytest.raises(HTTPException) as exc:
        await routes._save_script_run_as(None, 7, "root")
    assert exc.value.status_code == 400
    save.assert_not_awaited()
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)
    assert await routes.update_script_run_as_api(7, routes.ScriptRunAsRequest(run_as="user"), None) == {"default_run_as": "user"}
    save.assert_awaited_once_with(7, "user")
    assert audit.await_args.kwargs["metadata"] == {"run_as": "user"}
    with pytest.raises(ValidationError):
        routes.ScriptRunAsRequest(run_as="admin")


def test_script_page_offers_a_default_run_as_and_the_run_form_starts_on_it():
    template = (ROOT / "app/templates/rmm/_script_detail.html").read_text()
    assert 'action="/rmm/scripts/{{ script.id }}/run-as"' in template
    assert 'name="run_as"' in template
    script = (ROOT / "app/static/js/rmm_scripts.js").read_text()
    assert "detail.script.default_run_as" in script
    migration = (ROOT / "migrations/530_rmm_script_default_run_as.sql").read_text()
    assert "-- phase: expand" in migration
    assert "ADD COLUMN IF NOT EXISTS default_run_as VARCHAR(16) NOT NULL DEFAULT 'system'" in migration
