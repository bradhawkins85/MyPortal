"""Run script → Later: a one-off run held until a chosen date and time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.repositories import rmm as rmm_repo
from app.services import rmm_scripts
from tests.test_rmm_scripts import _enrol, _sync, sqlite_db  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "548_rmm_script_run_scheduled_for.sql"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def test_migration_is_an_idempotent_expand():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert sql.startswith("-- phase: expand")
    assert "ALTER TABLE rmm_script_runs ADD COLUMN IF NOT EXISTS scheduled_for DATETIME NULL;" in sql


def test_scheduled_time_parses_offsets_and_rejects_past_and_far_future():
    now = datetime(2026, 11, 9, 12, 0, 0)
    assert rmm_scripts.scheduled_time(None, now=now) is None
    assert rmm_scripts.scheduled_time("", now=now) is None
    # 4am in Brisbane (UTC+10) the next day is 18:00 UTC today.
    assert rmm_scripts.scheduled_time("2026-11-10T04:00:00+10:00", now=now) == datetime(2026, 11, 9, 18, 0, 0)
    assert rmm_scripts.scheduled_time("2026-11-09T18:00:00.123Z", now=now) == datetime(2026, 11, 9, 18, 0, 0)
    # A little clock skew is forgiven.
    assert rmm_scripts.scheduled_time("2026-11-09T11:59:30Z", now=now) == datetime(2026, 11, 9, 11, 59, 30)
    for bad, key in (("yesterday", "scheduled_for"), ("2026-11-09T11:00:00Z", "scheduled_for"),
                     ("2028-01-01T00:00:00Z", "scheduled_for")):
        with pytest.raises(rmm_scripts.RunRequestError) as exc:
            rmm_scripts.scheduled_time(bad, now=now)
        assert key in exc.value.errors


@pytest.mark.anyio
async def test_a_run_later_waits_for_its_time_without_blocking_the_queue(sqlite_db, monkeypatch):  # noqa: F811
    await _sync(monkeypatch, {"rmm/Common/reboot.ps1": "Restart-Computer -Force\n"})
    script = (await rmm_repo.list_scripts())[0]
    _r, agent = await _enrol(sqlite_db)
    later = _now() + timedelta(hours=6)

    scheduled = await rmm_scripts.queue_runs(
        script_id=script["id"], company_id=1, asset_ids=[10], submitted={}, timeout_seconds=60,
        requested_by_user_id=None, queue_timeout_seconds=3600, scheduled_for=later.isoformat() + "Z",
    )
    run_id = scheduled["queued"][0]["run_id"]
    assert scheduled["scheduled_for"] == later.isoformat() + "Z"
    run = await rmm_repo.get_run(run_id)
    assert run["status"] == "queued" and run["scheduled"] is True
    # The offline wait starts at the scheduled time.
    expires = run["expires_at"]
    expires = datetime.fromisoformat(expires) if isinstance(expires, str) else expires
    assert expires == later + timedelta(hours=1)
    assert [item["scheduled"] for item in await rmm_repo.list_runs(company_id=1)] == [True]

    # Nothing is handed to the device early, and a run queued now is not held behind it.
    now_run = await rmm_scripts.queue_runs(script_id=script["id"], company_id=1, asset_ids=[10], submitted={},
                                           timeout_seconds=60, requested_by_user_id=None)
    jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert [job["id"] for job in jobs] == [now_run["queued"][0]["run_id"]]
    await rmm_scripts.record_result(agent, now_run["queued"][0]["run_id"], {"exit_code": 0})
    assert await rmm_scripts.wait_for_jobs(agent, wait_seconds=0) == []
    assert (await rmm_repo.get_run(run_id))["status"] == "queued"

    # Once its time comes the device collects it.
    await sqlite_db.execute("UPDATE rmm_script_runs SET scheduled_for = %s WHERE id = %s",
                            (_now() - timedelta(seconds=5), run_id))
    assert (await rmm_repo.get_run(run_id))["scheduled"] is False
    jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert [job["id"] for job in jobs] == [run_id]


@pytest.mark.anyio
async def test_a_scheduled_run_can_be_cancelled_and_past_times_are_refused(sqlite_db, monkeypatch):  # noqa: F811
    await _sync(monkeypatch, {"rmm/Common/a.ps1": "Write-Output 1\n"})
    script = (await rmm_repo.list_scripts())[0]
    _r, agent = await _enrol(sqlite_db)

    with pytest.raises(rmm_scripts.RunRequestError) as exc:
        await rmm_scripts.queue_runs(script_id=script["id"], company_id=1, asset_ids=[10], submitted={},
                                     timeout_seconds=60, requested_by_user_id=None,
                                     scheduled_for=(_now() - timedelta(hours=1)).isoformat())
    assert "scheduled_for" in exc.value.errors
    assert await rmm_repo.list_runs(company_id=1) == []

    queued = await rmm_scripts.queue_runs(script_id=script["id"], company_id=1, asset_ids=[10], submitted={},
                                          timeout_seconds=60, requested_by_user_id=None,
                                          scheduled_for=_now() + timedelta(days=1))
    run_id = queued["queued"][0]["run_id"]
    assert await rmm_repo.cancel_run(run_id)
    await sqlite_db.execute("UPDATE rmm_script_runs SET scheduled_for = %s WHERE id = %s",
                            (_now() - timedelta(seconds=5), run_id))
    assert await rmm_scripts.wait_for_jobs(agent, wait_seconds=0) == []


def test_run_modal_offers_now_or_later():
    modal = (ROOT / "app/templates/rmm/_run_modal.html").read_text(encoding="utf-8")
    assert 'value="later" data-rmm-when' in modal
    assert 'type="datetime-local"' in modal and "data-rmm-scheduled-for" in modal
    assert 'data-rmm-error="scheduled_for"' in modal
    script = (ROOT / "app/static/js/rmm_scripts.js").read_text(encoding="utf-8")
    assert "scheduled_for: when ? when.toISOString() : null" in script
    macros = (ROOT / "app/templates/rmm/_macros.html").read_text(encoding="utf-8")
    assert '"scheduled": ("info", "Scheduled")' in macros
