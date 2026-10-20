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


@pytest.mark.parametrize('value,expected', [(0, 0), (60, 60), (1, 60), (99999, 99999), (None, 0)])
def test_timeout_range_includes_forever_and_long_scripts(value, expected):
    assert rmm_scripts.clamp_timeout(value, 0) == expected


@pytest.mark.anyio
async def test_global_default_override_and_forever_lifecycle(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi'})
    await repo.set_global_script_timeout(0)
    script = (await repo.list_scripts())[0]
    assert script['default_timeout_seconds'] == 0
    _, agent = await _enrol(sqlite_db)
    queued = await rmm_scripts.queue_runs(script_id=script['id'], company_id=1, asset_ids=[10],
                                          submitted={}, timeout_seconds=None, requested_by_user_id=9)
    run_id = queued['queued'][0]['run_id']
    run = await repo.get_run(run_id)
    assert run['timeout_seconds'] == 0 and run['expires_at'] is None
    assert await repo.expire_stale_runs(datetime.now() + timedelta(days=10000)) == 0
    jobs = await rmm_scripts.claim_jobs(agent)
    assert jobs[0]['timeout_seconds'] == 0
    assert (await repo.get_run(run_id))['expires_at'] is None
    assert await repo.get_run_file_payload(run_id, agent['id']) is not None
    assert await repo.expire_stale_runs(datetime.now() + timedelta(days=10000)) == 0
    await rmm_scripts.record_result(agent, run_id, {'exit_code': 0})
    assert (await repo.get_run(run_id))['status'] == 'completed'
    assert await repo.get_run_file_payload(run_id, agent['id']) is None

    # A technician's explicit value takes precedence over the global default.
    queued = await rmm_scripts.queue_runs(script_id=script['id'], company_id=1, asset_ids=[10],
                                          submitted={}, timeout_seconds=172800, requested_by_user_id=9)
    run = await repo.get_run(queued['queued'][0]['run_id'])
    assert run['timeout_seconds'] == 172800
    assert datetime.fromisoformat(str(run['expires_at'])) > datetime.now() + timedelta(hours=47)
    await rmm_scripts.claim_jobs(agent)
    run = await repo.get_run(run['id'])
    assert datetime.fromisoformat(str(run['expires_at'])) > datetime.now() + timedelta(hours=48)


@pytest.mark.anyio
async def test_defaults_propagate_to_new_automation_and_overrides_are_saved(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {'rmm/Common/setup.ps1': 'echo hi'})
    await repo.set_global_script_timeout(0)
    script = (await repo.list_scripts())[0]
    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        'name': 'Daily', 'script_id': script['id'], 'cron': '0 2 * * *', 'target_mode': 'all'})
    assert (await automation_repo.get_schedule(schedule_id))['timeout_seconds'] == 0
    step_id = await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
        'script_id': script['id'], 'target_mode': 'all', 'timeout_seconds': 86400})
    assert (await automation_repo.get_step(step_id))['timeout_seconds'] == 86400
    # Existing saved values remain stable after changing the global default.
    await repo.set_global_script_timeout(3600)
    assert (await repo.get_script(script['id']))['default_timeout_seconds'] == 3600
    assert (await automation_repo.get_schedule(schedule_id))['timeout_seconds'] == 0


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
                                          submitted={}, timeout_seconds=0, requested_by_user_id=9)
    assert not queued['queued'] and queued['problems']
    schedule_id = await automation.save_schedule(schedule_id=None, company_id=1, user_id=9, payload={
        'name': 'Daily', 'script_id': win['id'], 'cron': '0 2 * * *', 'target_mode': 'all', 'timeout_seconds': 0})
    result = await automation.run_schedule(schedule_id)
    assert [r['asset_id'] for r in result['queued']] == [10]
    assert not result['problems']
    assert (await repo.get_run(result['queued'][0]['run_id']))['expires_at'] is None

    for script in (win, unix):
        await automation.save_step(step_id=None, company_id=1, user_id=9, payload={
            'script_id': script['id'], 'target_mode': 'all', 'timeout_seconds': 0})
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
        assert (await api.post('/rmm/scripts/timeout', data={'timeout_minutes': '0'})).status_code == 403
        assert (await api.post(f'/rmm/scripts/{script["id"]}/platforms', data={'platforms': 'linux'})).status_code == 403
        context.return_value = ({'id': 9, 'is_super_admin': True}, {}, 1, True)
        assert (await api.post('/rmm/scripts/timeout', data={'timeout_minutes': '0'})).status_code == 303
        assert await repo.get_global_script_timeout() == 0
        assert (await api.post('/rmm/scripts/timeout', data={'timeout_minutes': '-1'})).status_code == 400
        assert (await api.post(f'/rmm/scripts/{script["id"]}/platforms', data={'platforms': 'linux'})).status_code == 303
        assert (await repo.get_script(script['id']))['platforms'] == ['linux']
        assert (await api.post(f'/rmm/scripts/{script["id"]}/platforms', data={'platforms': 'invalid'})).status_code == 400


def test_policy_forms_use_existing_accessible_controls():
    html = _render_library(is_super_admin=True, global_timeout_seconds=0,
                           selected_script=_detail(default_timeout_seconds=0))
    assert 'Save global timeout' in html and 'Save platforms' in html and 'Forever' in html
    assert 'name="timeout_minutes"' in html and 'name="platforms" value="windows" checked' in html
    assert 'max="240"' not in html
    html = _render_library(is_super_admin=False, selected_script=_detail())
    assert 'Save global timeout' not in html and 'Save platforms' not in html


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
