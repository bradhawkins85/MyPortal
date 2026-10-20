"""Timeout and platform policies enforced across manual and automated scripts."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.features.rmm import routes
from app.repositories import rmm as repo
from app.repositories import rmm_automation as automation_repo
from app.services import rmm_automation as automation
from app.services import rmm_scripts
from tests.test_rmm_scripts import _enrol, _sync, _render_library, _detail, anyio_backend, sqlite_db  # noqa: F401


@pytest.mark.parametrize('value,expected', [(0, 10), (60, 60), (1, 10), (99999, 14400), (None, 600)])
def test_execution_timeout_keeps_existing_limits(value, expected):
    assert rmm_scripts.clamp_timeout(value, 600) == expected


@pytest.mark.anyio
async def test_offline_wait_forever_is_separate_from_execution(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi'})
    script = (await repo.list_scripts())[0]
    assert script['default_queue_timeout_seconds'] == 0
    assert script['default_timeout_seconds'] == 600
    _, agent = await _enrol(sqlite_db)
    queued = await rmm_scripts.queue_runs(script_id=script['id'], company_id=1, asset_ids=[10],
                                          submitted={}, timeout_seconds=60, requested_by_user_id=9)
    run_id = queued['queued'][0]['run_id']
    run = await repo.get_run(run_id)
    assert run['timeout_seconds'] == 60 and run['queue_timeout_seconds'] == 0
    assert run['expires_at'] is None
    assert await repo.expire_stale_runs(datetime.now() + timedelta(days=3)) == 0
    # An agent returning days later still receives the script and its original execution limit.
    await sqlite_db.execute("UPDATE rmm_script_runs SET queued_at = %s WHERE id = %s",
                            (datetime.now() - timedelta(days=3), run_id))
    jobs = await rmm_scripts.claim_jobs(agent)
    assert jobs[0]['timeout_seconds'] == 60
    run = await repo.get_run(run_id)
    expiry = datetime.fromisoformat(str(run['expires_at']))
    assert datetime.now() + timedelta(minutes=5) < expiry < datetime.now() + timedelta(minutes=7)
    assert await repo.get_run_file_payload(run_id, agent['id']) is not None
    await rmm_scripts.record_result(agent, run_id, {'exit_code': 0})
    assert (await repo.get_run(run_id))['status'] == 'completed'
    assert await repo.get_run_file_payload(run_id, agent['id']) is None


@pytest.mark.anyio
async def test_script_defaults_and_schedule_overrides_are_independent(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi'})
    script = (await repo.list_scripts())[0]
    await repo.set_script_queue_timeout(script['id'], 5 * 3600)
    _, agent = await _enrol(sqlite_db)
    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        'name': 'Daily', 'script_id': script['id'], 'cron': '0 2 * * *', 'target_mode': 'all', 'timeout_seconds': 120})
    schedule = await automation_repo.get_schedule(schedule_id)
    assert schedule['queue_timeout_seconds'] is None and schedule['timeout_seconds'] == 120
    result = await automation.run_schedule(schedule_id)
    run = await repo.get_run(result['queued'][0]['run_id'])
    assert run['queue_timeout_seconds'] == 18000 and run['timeout_seconds'] == 120
    assert datetime.fromisoformat(str(run['expires_at'])) > datetime.now() + timedelta(hours=4, minutes=59)
    await repo.cancel_run(run['id'])
    # Schedules without an override use changes to the script's default.
    await repo.set_script_queue_timeout(script['id'], 0)
    result = await automation.run_schedule(schedule_id)
    run = await repo.get_run(result['queued'][0]['run_id'])
    assert run['expires_at'] is None and run['timeout_seconds'] == 120
    await repo.cancel_run(run['id'])
    await automation.save_schedule(schedule_id=schedule_id, company_id=1, user_id=9, payload={
        'name': 'Daily', 'script_id': script['id'], 'cron': '0 2 * * *', 'target_mode': 'all',
        'timeout_seconds': 120, 'queue_timeout_seconds': 2 * 86400})
    result = await automation.run_schedule(schedule_id)
    run = await repo.get_run(result['queued'][0]['run_id'])
    assert run['queue_timeout_seconds'] == 172800 and run['timeout_seconds'] == 120
    assert datetime.fromisoformat(str(run['expires_at'])) > datetime.now() + timedelta(hours=47)
    step_id = await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
        'script_id': script['id'], 'target_mode': 'all', 'queue_timeout_seconds': 86400})
    step = await automation_repo.get_step(step_id)
    assert step['queue_timeout_seconds'] == 86400 and step['timeout_seconds'] == 600


@pytest.mark.anyio
async def test_platform_filtering_manual_scheduled_and_onboarding(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi', 'rmm/Common/unix.sh': 'echo unix'})
    scripts = {s['language']: s for s in await repo.list_scripts()}
    win = scripts['powershell']
    unix = scripts['bash']
    _, windows = await _enrol(sqlite_db)
    _, mac = await _enrol(sqlite_db, tray_id=8, asset_id=11, uid='agent-uid-mac', os_name='darwin')
    # PowerShell being installed does not override the script's platform policy.
    mac = {**mac, 'shells': 'pwsh,bash'}
    assert rmm_scripts.device_problem(win, mac)
    assert not rmm_scripts.device_problem(win, windows)
    queued = await rmm_scripts.queue_runs(script_id=win['id'], company_id=1, asset_ids=[11],
                                          submitted={}, timeout_seconds=60, queue_timeout_seconds=0, requested_by_user_id=9)
    assert not queued['queued'] and queued['problems']
    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        'name': 'Daily', 'script_id': win['id'], 'cron': '0 2 * * *', 'target_mode': 'all', 'timeout_seconds': 60, 'queue_timeout_seconds': 0})
    result = await automation.run_schedule(schedule_id)
    assert [r['asset_id'] for r in result['queued']] == [10]
    assert not result['problems']
    assert (await repo.get_run(result['queued'][0]['run_id']))['expires_at'] is None

    for script in (win, unix):
        await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
            'script_id': script['id'], 'target_mode': 'all', 'timeout_seconds': 60, 'queue_timeout_seconds': 0})
    onboarding_id = await automation.start_onboarding(mac)
    state = await automation_repo.get_onboarding_run(onboarding_id)
    assert state['plan'][0]['status'] == 'skipped'
    jobs = await rmm_scripts.claim_jobs(mac)
    assert len(jobs) == 1 and jobs[0]['language'] == 'bash'
    await rmm_scripts.record_result(mac, jobs[0]['id'], {'exit_code': 0})
    await automation.run_finished(jobs[0]['id'])
    assert (await automation_repo.get_onboarding_run(onboarding_id))['status'] == 'completed'


@pytest.mark.anyio
async def test_platform_edits_survive_sync_and_are_rechecked_at_dispatch(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi'})
    script = (await repo.list_scripts())[0]
    _, agent = await _enrol(sqlite_db)
    run = await rmm_scripts.queue_for_agent(script=script, agent=agent, entries={}, inputs=[],
                                            timeout_seconds=600, requested_by_user_id=9)
    await repo.set_script_platforms(script['id'], ['linux'])
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo changed'}, {'rmm/Common/setup.ps1': 'new-sha'})
    script = await repo.get_script(script['id'])
    assert script['platforms'] == ['linux']
    assert await rmm_scripts.claim_jobs(agent) == []
    assert (await repo.get_run(run))['status'] == 'cancelled'
    await repo.set_script_platforms(script['id'], [])
    assert not rmm_scripts.platform_allowed(await repo.get_script(script['id']), agent)


@pytest.mark.anyio
async def test_device_picker_and_policy_permissions(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi', 'rmm/Common/unix.sh': 'echo unix'})
    _, mac = await _enrol(sqlite_db, tray_id=8, asset_id=11, uid='agent-uid-mac', os_name='darwin')
    context = AsyncMock(return_value=({'id': 9}, {}, 1, True))
    monkeypatch.setattr(routes, '_context', context)
    monkeypatch.setattr(routes.audit_service, 'record', AsyncMock())
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://portal') as api:
        result = await api.get('/api/rmm/scripts?asset_id=11')
        assert result.status_code == 200
        assert [s['language'] for s in result.json()['scripts']] == ['bash']
        assert (await api.get('/api/rmm/scripts?asset_id=999')).status_code == 404
        script = (await repo.list_scripts())[0]
        assert (await api.post(f'/rmm/scripts/{script["id"]}/queue-timeout', data={'queue_timeout_minutes': '0'})).status_code == 403
        assert (await api.post(f'/rmm/scripts/{script["id"]}/platforms', data={'platforms': 'linux'})).status_code == 403
        context.return_value = ({'id': 9, 'is_super_admin': True}, {}, 1, True)
        assert (await api.post(f'/rmm/scripts/{script["id"]}/queue-timeout', data={'queue_timeout_minutes': '0'})).status_code == 303
        assert (await repo.get_script(script['id']))['default_queue_timeout_seconds'] == 0
        assert (await api.post(f'/rmm/scripts/{script["id"]}/queue-timeout', data={'queue_timeout_minutes': '-1'})).status_code == 400
        assert (await api.post(f'/rmm/scripts/{script["id"]}/platforms', data={'platforms': 'linux'})).status_code == 303
        assert (await repo.get_script(script['id']))['platforms'] == ['linux']
        assert (await api.post(f'/rmm/scripts/{script["id"]}/platforms', data={'platforms': 'invalid'})).status_code == 400


def test_policy_forms_keep_queue_and_execution_separate():
    html = _render_library(is_super_admin=True, selected_script=_detail(default_queue_timeout_seconds=18000))
    assert 'Global script timeout' not in html
    assert 'Save offline wait timeout' in html and 'Save platforms' in html
    assert 'name="queue_timeout_minutes"' in html and 'value="300"' in html
    assert 'data-rmm-queue-timeout' in html and 'max="240"' in html
    html = _render_library(is_super_admin=False, selected_script=_detail())
    assert 'Save offline wait timeout' not in html and 'Save platforms' not in html


@pytest.mark.anyio
async def test_expired_offline_run_is_not_dispatched(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi'})
    script = (await repo.list_scripts())[0]
    _, agent = await _enrol(sqlite_db)
    run_id = await rmm_scripts.queue_for_agent(script=script, agent=agent, entries={}, inputs=[],
                                               timeout_seconds=60, requested_by_user_id=9,
                                               expires_at=datetime.now() - timedelta(minutes=1))
    assert await rmm_scripts.claim_jobs(agent) == []
    assert (await repo.get_run(run_id))['status'] == 'expired'


@pytest.mark.anyio
async def test_explicit_cross_platform_powershell_needs_shell(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi'})
    script = (await repo.list_scripts())[0]
    await repo.set_script_platforms(script['id'], ['windows', 'darwin', 'linux'])
    script = await repo.get_script(script['id'])
    _, mac = await _enrol(sqlite_db, tray_id=8, asset_id=11, uid='agent-uid-mac', os_name='darwin')
    assert 'PowerShell is not installed' in rmm_scripts.device_problem(script, mac)
    mac = {**mac, 'shells': 'pwsh,bash'}
    assert rmm_scripts.device_problem(script, mac) is None
    run_id = await rmm_scripts.queue_for_agent(script=script, agent=mac, entries={}, inputs=[],
                                               timeout_seconds=60, requested_by_user_id=9)
    assert [job['id'] for job in await rmm_scripts.claim_jobs(mac)] == [run_id]


@pytest.mark.anyio
async def test_ad_hoc_powershell_command_still_runs_on_mac_with_pwsh(sqlite_db):
    from app.services import rmm_commands
    _, mac = await _enrol(sqlite_db, tray_id=8, asset_id=11, uid='agent-uid-mac', os_name='darwin')
    await repo.record_agent_checkin(mac['id'], {'shells': 'pwsh,bash'})
    mac = await repo.get_agent(mac['id'])
    run_id = await rmm_commands.queue_command(agent=mac, language='powershell', command='Get-Date',
                                               run_as='system', timeout_seconds=60, requested_by_user_id=9)
    assert [job['id'] for job in await rmm_scripts.claim_jobs(mac)] == [run_id]


@pytest.mark.parametrize('value', [-1, 1, 59, 'invalid'])
def test_invalid_offline_windows_are_rejected(value):
    with pytest.raises(rmm_scripts.RunRequestError):
        rmm_scripts.queue_timeout(value)


@pytest.mark.anyio
async def test_manual_five_hour_window_and_execution_deadline(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi'})
    script = (await repo.list_scripts())[0]
    await repo.set_script_queue_timeout(script['id'], 18000)
    _, agent = await _enrol(sqlite_db)
    queued = await rmm_scripts.queue_runs(script_id=script['id'], company_id=1, asset_ids=[10],
        submitted={}, timeout_seconds=120, requested_by_user_id=9)
    run_id = queued['queued'][0]['run_id']
    run = await repo.get_run(run_id)
    assert run['queue_timeout_seconds'] == 18000 and run['timeout_seconds'] == 120
    expiry = datetime.fromisoformat(str(run['expires_at']))
    assert timedelta(hours=4, minutes=59) < expiry - datetime.now() < timedelta(hours=5, minutes=1)
    # Collection replaces the queue deadline with the execution deadline plus result grace.
    await rmm_scripts.claim_jobs(agent)
    expiry = datetime.fromisoformat(str((await repo.get_run(run_id))['expires_at']))
    assert timedelta(minutes=6) < expiry - datetime.now() < timedelta(minutes=8)
    await rmm_scripts.record_result(agent, run_id, {'exit_code': 0})
    # A run-level Forever override ignores the script's five-hour queue default.
    queued = await rmm_scripts.queue_runs(script_id=script['id'], company_id=1, asset_ids=[10],
        submitted={}, timeout_seconds=120, queue_timeout_seconds=0, requested_by_user_id=9)
    run = await repo.get_run(queued['queued'][0]['run_id'])
    assert run['expires_at'] is None and run['timeout_seconds'] == 120
    # Changing the per-script wait survives content synchronization.
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo changed'}, {'rmm/Common/setup.ps1': 'changed'})
    assert (await repo.get_script(script['id']))['default_queue_timeout_seconds'] == 18000


@pytest.mark.anyio
async def test_scheduled_wait_is_not_shortened_by_next_occurrence(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi'})
    script = (await repo.list_scripts())[0]
    _, agent = await _enrol(sqlite_db)
    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        'name': 'Hourly', 'script_id': script['id'], 'cron': '0 * * * *', 'target_mode': 'all',
        'timeout_seconds': 60, 'queue_timeout_seconds': 18000})
    result = await automation.run_schedule(schedule_id)
    run_id = result['queued'][0]['run_id']
    run = await repo.get_run(run_id)
    assert datetime.fromisoformat(str(run['expires_at'])) > datetime.now() + timedelta(hours=4)
    assert not (await automation.run_schedule(schedule_id))['queued']
    # A device returning after this window cannot collect the stale script.
    await sqlite_db.execute('UPDATE rmm_script_runs SET expires_at = %s WHERE id = %s',
                            (datetime.now() - timedelta(seconds=1), run_id))
    assert await rmm_scripts.claim_jobs(agent) == []
    assert (await repo.get_run(run_id))['status'] == 'expired'


@pytest.mark.anyio
async def test_onboarding_wait_override_does_not_change_execution(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi'})
    script = (await repo.list_scripts())[0]
    await repo.set_script_queue_timeout(script['id'], 18000)
    _, agent = await _enrol(sqlite_db)
    await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
        'script_id': script['id'], 'target_mode': 'all', 'queue_timeout_seconds': 172800, 'timeout_seconds': 120})
    onboarding_id = await automation.start_onboarding(agent)
    run_id = (await automation_repo.get_onboarding_run(onboarding_id))['current_run_id']
    run = await repo.get_run(run_id)
    assert run['queue_timeout_seconds'] == 172800 and run['timeout_seconds'] == 120
    assert datetime.fromisoformat(str(run['expires_at'])) > datetime.now() + timedelta(hours=47)
