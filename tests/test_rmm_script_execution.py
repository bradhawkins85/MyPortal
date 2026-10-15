"""Execution modes reserve the per-device queue across overlapping job polls."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.features.rmm import routes
from app.repositories import rmm as repo
from app.services import rmm_scripts
from tests.test_rmm_scripts import sqlite_db, anyio_backend, _sync, _enrol  # noqa: F401


async def queue(script, agent, source="manual"):
    return await rmm_scripts.queue_for_agent(
        script=script, agent=agent, entries={}, inputs=[], timeout_seconds=60,
        requested_by_user_id=None, run_source=source,
    )


async def finish(agent, run_id, **result):
    await rmm_scripts.record_result(agent, run_id, {"exit_code": 0, **result})


@pytest.mark.anyio
async def test_defaults_are_exclusive_across_simultaneous_polls(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/a.ps1": "echo 1"})
    script = (await repo.list_scripts())[0]
    assert script["allow_parallel"] is False
    _, agent = await _enrol(sqlite_db)
    first = await queue(script, agent)
    second = await queue(script, agent, "schedule")
    batches = await asyncio.gather(*(rmm_scripts.claim_jobs(agent) for _ in range(6)))
    assert [job["id"] for batch in batches for job in batch] == [first]
    assert (await repo.get_run(second))["status"] == "queued"
    assert await rmm_scripts.mark_started(agent, first)
    assert await rmm_scripts.claim_jobs(agent) == []
    await finish(agent, first)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == [second]


@pytest.mark.anyio
async def test_parallel_runs_share_slots_but_cannot_pass_exclusive_barrier(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/a.ps1": "echo 1"})
    script = (await repo.list_scripts())[0]
    _, agent = await _enrol(sqlite_db)
    parallel = {**script, "allow_parallel": True}
    a = await queue(parallel, agent)
    b = await queue(parallel, agent, "schedule")
    exclusive = await queue(script, agent, "schedule")
    later = await queue(parallel, agent)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == [a, b]
    await finish(agent, a)
    assert await rmm_scripts.claim_jobs(agent) == []
    await finish(agent, b, exit_code=1)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == [exclusive]
    assert await rmm_scripts.claim_jobs(agent) == []
    await finish(agent, exclusive, timed_out=True)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == [later]


@pytest.mark.anyio
async def test_parallel_capacity_and_onboarding_override(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/a.ps1": "echo 1"})
    script = {**(await repo.list_scripts())[0], "allow_parallel": True}
    _, agent = await _enrol(sqlite_db)
    runs = [await queue(script, agent) for _ in range(5)]
    batches = await asyncio.gather(*(rmm_scripts.claim_jobs(agent) for _ in range(3)))
    assert sorted(job["id"] for batch in batches for job in batch) == runs[:4]
    for run_id in runs[:4]:
        await finish(agent, run_id)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == runs[4:]
    onboarding = await queue(script, agent, "onboarding")
    later = await queue(script, agent)
    stored = await sqlite_db.fetch_one("SELECT allow_parallel FROM rmm_script_runs WHERE id = %s", (onboarding,))
    assert not stored["allow_parallel"]
    assert await rmm_scripts.claim_jobs(agent) == []
    await finish(agent, runs[4])
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == [onboarding]
    assert (await repo.get_run(later))["status"] == "queued"
    await finish(agent, onboarding)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == [later]


@pytest.mark.anyio
async def test_devices_have_independent_queues_and_expiry_releases_reservation(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/a.ps1": "echo 1"})
    script = (await repo.list_scripts())[0]
    _, a = await _enrol(sqlite_db)
    _, b = await _enrol(sqlite_db, tray_id=8, asset_id=11, uid="agent-uid-0002")
    first = await queue(script, a)
    cancelled = await queue(script, a)
    last = await queue(script, a)
    other = await queue(script, b)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(a)] == [first]
    assert [job["id"] for job in await rmm_scripts.claim_jobs(b)] == [other]
    assert await repo.cancel_run(cancelled)
    await sqlite_db.execute("UPDATE rmm_script_runs SET expires_at = %s WHERE id = %s", (datetime.now() - timedelta(days=1), first))
    assert await repo.expire_stale_runs() == 1
    assert [job["id"] for job in await rmm_scripts.claim_jobs(a)] == [last]


@pytest.mark.anyio
async def test_sync_preserves_mode_and_queued_runs_keep_snapshot(sqlite_db, monkeypatch):
    path = "rmm/Common/a.ps1"
    await _sync(monkeypatch, {path: "echo 1"})
    script = (await repo.list_scripts())[0]
    _, agent = await _enrol(sqlite_db)
    first = await queue(script, agent)
    await repo.set_script_parallel(script["id"], True)
    await _sync(monkeypatch, {path: "echo 2"}, shas={path: "new-sha"})
    script = await repo.get_script(script["id"], with_content=True)
    assert script["allow_parallel"] is True
    assert routes._script_summary(script)["allow_parallel"] is True
    second = await queue(script, agent)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == [first]
    await finish(agent, first)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == [second]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["sequential", "parallel", "run_anyway"])
async def test_execution_setting_requires_admin_and_visible_script(monkeypatch, mode):
    context = AsyncMock(return_value=({"id": 9}, {}, 1, True))
    monkeypatch.setattr(routes, "_context", context)
    save = AsyncMock()
    monkeypatch.setattr(repo, "set_script_execution", save)
    with pytest.raises(HTTPException) as exc:
        await routes._save_script_execution(None, 7, mode)
    assert exc.value.status_code == 403
    save.assert_not_awaited()
    context.return_value = ({"id": 9, "is_super_admin": True}, {}, 1, True)
    lookup = AsyncMock(return_value={"id": 7, "is_active": True, "company_id": 2})
    monkeypatch.setattr(repo, "get_script", lookup)
    with pytest.raises(HTTPException) as exc:
        await routes._save_script_execution(None, 7, mode)
    assert exc.value.status_code == 404
    save.assert_not_awaited()
    lookup.return_value["company_id"] = None
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)
    result = await routes.update_script_execution_api(7, routes.ScriptExecutionRequest(execution_mode=mode), None)
    assert result == {"allow_parallel": mode == "parallel", "execution_mode": mode}
    save.assert_awaited_once_with(7, mode)
    assert audit.await_args.kwargs["metadata"] == {"execution_mode": mode}
    with pytest.raises(ValidationError):
        routes.ScriptExecutionRequest(allow_parallel="false")


async def enable_run_anyway(agent):
    await repo.record_agent_checkin(agent["id"], {"run_anyway_supported": True})
    return {**agent, "run_anyway_supported": True}


@pytest.mark.anyio
async def test_run_anyway_bypasses_active_exclusive_run_and_long_backlog(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/a.ps1": "echo 1"})
    script = (await repo.list_scripts())[0]
    _, agent = await _enrol(sqlite_db)
    agent = await enable_run_anyway(agent)
    active = await queue(script, agent)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == [active]
    backlog = [await queue(script, agent) for _ in range(8)]
    urgent = await queue({**script, "run_anyway": True}, agent)
    batches = await asyncio.gather(*(rmm_scripts.claim_jobs(agent) for _ in range(4)))
    jobs = [job for batch in batches for job in batch]
    assert [job["id"] for job in jobs] == [urgent]
    assert jobs[0]["run_anyway"] is True
    assert (await repo.get_run(backlog[0]))["status"] == "queued"
    await finish(agent, active)
    # Run Anyway does not block an ordinary sequential run while still active.
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == backlog[:1]


@pytest.mark.anyio
async def test_run_anyway_bypasses_full_parallel_capacity(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/a.ps1": "echo 1"})
    script = {**(await repo.list_scripts())[0], "allow_parallel": True}
    _, agent = await _enrol(sqlite_db)
    agent = await enable_run_anyway(agent)
    normal = [await queue(script, agent) for _ in range(5)]
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == normal[:4]
    urgent = [await queue({**script, "run_anyway": True}, agent) for _ in range(2)]
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == urgent
    await finish(agent, normal[0])
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == normal[4:]


@pytest.mark.anyio
async def test_run_anyway_never_overrides_onboarding_sequence(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/a.ps1": "echo 1"})
    script = (await repo.list_scripts())[0]
    _, agent = await _enrol(sqlite_db)
    agent = await enable_run_anyway(agent)
    active = await queue(script, agent)
    await rmm_scripts.claim_jobs(agent)
    onboarding = await queue({**script, "run_anyway": True}, agent, "onboarding")
    snapshot = await sqlite_db.fetch_one("SELECT run_anyway, allow_parallel FROM rmm_script_runs WHERE id = %s", (onboarding,))
    assert snapshot == {"run_anyway": 0, "allow_parallel": 0}
    assert await rmm_scripts.claim_jobs(agent) == []
    await finish(agent, active)
    jobs = await rmm_scripts.claim_jobs(agent)
    assert [job["id"] for job in jobs] == [onboarding]
    assert jobs[0]["run_anyway"] is False


@pytest.mark.anyio
async def test_run_anyway_requires_capable_agent_without_blocking_queue(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/a.ps1": "echo 1"})
    script = (await repo.list_scripts())[0]
    enrolment, agent = await _enrol(sqlite_db)
    urgent = await queue({**script, "run_anyway": True}, agent)
    normal = await queue(script, agent)
    assert [job["id"] for job in await rmm_scripts.claim_jobs(agent)] == [normal]
    run = await repo.get_run(urgent)
    assert run["status"] == "failed" and "Update the RMM agent" in run["error_message"]
    await enable_run_anyway(agent)
    # Polls preserve support; an actual downgrade clears it.
    await rmm_scripts.checkin(agent, {}, None)
    assert (await rmm_scripts.authenticate_agent(enrolment["auth_token"]))["run_anyway_supported"]
    await rmm_scripts.checkin(agent, {"agent_version": "old"}, None)
    assert not (await rmm_scripts.authenticate_agent(enrolment["auth_token"]))["run_anyway_supported"]


@pytest.mark.anyio
async def test_run_anyway_setting_survives_sync_and_can_be_changed(sqlite_db, monkeypatch):
    path = "rmm/Common/a.ps1"
    await _sync(monkeypatch, {path: "echo 1"})
    script = (await repo.list_scripts())[0]
    await repo.set_script_execution(script["id"], "run_anyway")
    await _sync(monkeypatch, {path: "echo 2"}, shas={path: "new-sha"})
    script = await repo.get_script(script["id"], with_content=True)
    assert script["run_anyway"] and not script["allow_parallel"]
    assert routes._script_summary(script)["execution_mode"] == "run_anyway"
    _, agent = await _enrol(sqlite_db)
    agent = await enable_run_anyway(agent)
    urgent = await queue(script, agent)
    await repo.set_script_parallel(script["id"], True)
    updated = await repo.get_script(script["id"])
    assert updated["execution_mode"] == "parallel" and not updated["run_anyway"]
    assert (await rmm_scripts.claim_jobs(agent))[0]["run_anyway"] is True
    assert (await repo.get_run(urgent))["status"] == "dispatched"


def test_execution_mode_api_validates_all_modes_and_legacy_boolean():
    for mode in ("sequential", "parallel", "run_anyway"):
        assert routes.ScriptExecutionRequest(execution_mode=mode).resolved_mode == mode
    assert routes.ScriptExecutionRequest(allow_parallel=True).resolved_mode == "parallel"
    assert routes.ScriptExecutionRequest(allow_parallel=False).resolved_mode == "sequential"
    for payload in ({}, {"execution_mode": "invalid"}, {"execution_mode": "run_anyway", "allow_parallel": True}):
        with pytest.raises(ValidationError):
            routes.ScriptExecutionRequest(**payload)
