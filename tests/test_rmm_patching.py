"""Policy persistence, tenant boundaries, dynamic targeting and actual queued jobs."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jinja2 import Environment, FileSystemLoader
from fastapi import HTTPException
from starlette.datastructures import FormData

from app.core.database import Database
from app.repositories import rmm_patching as repo, rmm as rmm_repo
from app.services import rmm_patching as patch, rmm_scripts
from app.services.rmm_scripts import RunRequestError
from app.features.rmm import patch_routes
from tests.test_rmm_scripts import sqlite_db, anyio_backend, _enrol  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def patch_db(sqlite_db, monkeypatch):
    await sqlite_db.conn.executescript(Database()._adapt_sql_for_sqlite(
        (ROOT / 'migrations/491_rmm_patch_policies.sql').read_text()))
    monkeypatch.setattr(repo, 'db', sqlite_db)
    return sqlite_db


def payload(**values):
    return {**patch.DEFAULTS, 'name': 'Patch PCs', 'timezone': 'Australia/Brisbane', **values}


async def save(**values):
    data = patch.validate(payload(**values), 1, True)
    policy_id = await repo.save(None, {**data, 'updated_by_user_id': 9})
    return await repo.get_policy(policy_id)


def test_validation_rejects_unsafe_inputs_and_global_access():
    for data, key in [(payload(scope='global'), 'scope'),
                      (payload(kind='chocolatey', package_id='x; shutdown', display_name='App'), 'package_id'),
                      (payload(target_mode='rules', target_rules='broken'), 'target_rules'),
                      (payload(reboot_mode='now'), 'reboot_mode'),
                      (payload(active_start=8, active_end=8), 'active_end'),
                      (payload(reboot_delay_minutes=0), 'reboot_delay_minutes'),
                      (payload(cron='invalid'), 'cron'), (payload(timezone='invalid'), 'timezone'),
                      (payload(categories=['Drivers']), 'categories'),
                      (payload(categories=[]), 'categories'),
                      (payload(target_release='26H1'), 'target_release'),
                      (payload(target_release='26H2'), 'reboot_mode')]:
        with pytest.raises(RunRequestError) as exc:
            patch.validate(data, 1, False)
        assert key in exc.value.errors


@pytest.mark.anyio
async def test_policy_template_prefills_company_policy_without_saving(patch_db, monkeypatch):
    request = SimpleNamespace()
    context = ({'id': 9}, {}, 1, True)
    get_context = AsyncMock(return_value=context)
    render = AsyncMock()
    monkeypatch.setattr(patch_routes, '_context', get_context)
    monkeypatch.setattr(patch_routes, 'render', render)
    monkeypatch.setattr(patch_routes.rmm_automation, 'default_timezone', lambda: 'Australia/Brisbane')

    for key, sample in patch.POLICY_TEMPLATES.items():
        await patch_routes.page(request, policy=None, template=key)
        editing = render.await_args.args[2]
        assert editing['name'] == sample['name'] and 'id' not in editing
        values = patch.validate(editing, 1, False)
        assert values['company_id'] == 1 and values['is_enabled']
        assert values['timezone'] == 'Australia/Brisbane'
    assert editing['target_release'] == '26H2' and editing['cron'] == '0 2 * * *'
    assert await repo.list_policies(1) == []

    with pytest.raises(HTTPException) as exc:
        await patch_routes.page(request, policy=None, template='unrecognised')
    assert exc.value.status_code == 404
    get_context.return_value = (*context[:3], False)
    await patch_routes.page(request, policy=None, template='windows-feature-rollout')
    assert render.await_args.args[2] is None


@pytest.mark.anyio
async def test_feature_only_policy_round_trips_and_queues_the_release_catalog(patch_db, monkeypatch):
    _, agent = await _enrol(patch_db)
    monkeypatch.setattr(patch_routes, '_context', AsyncMock(return_value=({'id': 9}, {}, 1, True)))
    monkeypatch.setattr(patch_routes.audit, 'record', AsyncMock())
    form = payload(target_release=' 26h2 ', categories=[], reboot_mode='no_users')
    request = SimpleNamespace(form=AsyncMock(return_value=FormData({k: v for k, v in form.items() if k != 'categories'})))

    response = await patch_routes.create(request)
    assert response.status_code == 303
    policy, = await repo.list_policies(1)
    assert policy['settings']['target_release'] == '26H2' and policy['settings']['categories'] == []
    assert patch_routes.form_values(policy)['target_release'] == '26H2'
    assert await patch.effective([policy], agent) == [policy]

    await patch.queue(agent, [policy], {policy['id']}, patch.utcnow() + timedelta(minutes=15))
    row, = await rmm_repo.next_jobs_for_agent(agent['id'])
    job = rmm_scripts._job_payload(row)
    assert job['timeout_seconds'] == 4 * 60 * 60
    config = json.loads(job['env']['MYPORTAL_PATCH_PLAN'])
    assert config['feature_upgrade']['target'] == '26H2'
    releases = {release['version']: release for release in config['feature_upgrade']['releases']}
    assert releases['26H2']['prerequisite_versions'] == ['24H2', '25H2']
    assert releases['26H2']['prerequisite_kb'] == 'KB5124010'
    assert await patch.job_is_current(agent, job)


@pytest.mark.anyio
async def test_scope_overrides_disabled_and_packages(patch_db):
    _, agent = await _enrol(patch_db)
    global_win = await save(scope='global')
    company_win = await save(is_enabled=False)
    global_app = await save(scope='global', kind='chocolatey', package_id='googlechrome', display_name='Google Chrome')
    company_app = await save(kind='chocolatey', package_id='googlechrome', display_name='Google Chrome')
    other_app = await save(kind='chocolatey', package_id='7zip', display_name='7-Zip')
    policies = await repo.list_policies(1)
    assert [p['id'] for p in await patch.effective(policies, agent)] == [company_app['id'], other_app['id']]
    other = {**agent, 'company_id': 2}
    assert [p['id'] for p in await patch.effective(policies, other)] == [global_win['id'], global_app['id']]
    assert await patch.effective(policies, {**agent, 'os': 'linux'}) == []
    assert await patch.effective(policies, {**agent, 'status': 'revoked'}) == []
    with pytest.raises(HTTPException) as exc:
        await patch_routes.editable(company_app['id'], 2, {'is_super_admin': True})
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await patch_routes.editable(global_win['id'], 1, {})
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_inventory_targeting_and_corrupt_rules_fail_closed(patch_db):
    _, agent = await _enrol(patch_db)
    global_policy = await save(scope='global')
    rules = {'operator': 'and', 'children': [{'field': 'asset.name', 'operator': 'equals', 'value': 'PC-01'}]}
    company_policy = await save(target_mode='rules', target_rules=rules)
    policies = await repo.list_policies(1)
    assert [p['id'] for p in await patch.effective(policies, agent)] == [company_policy['id']]
    await patch_db.execute("UPDATE assets SET name = 'changed' WHERE id = 10")
    assert [p['id'] for p in await patch.effective(policies, agent)] == [global_policy['id']]
    await patch_db.execute("UPDATE rmm_patch_policies SET target_rules_json = 'bad' WHERE id = %s", (company_policy['id'],))
    assert [p['id'] for p in await patch.effective(await repo.list_policies(1), agent)] == [global_policy['id']]


@pytest.mark.anyio
async def test_jobs_pin_policy_and_cancel_on_edit(patch_db):
    _, agent = await _enrol(patch_db)
    policy = await save()
    policies = [policy]
    run = await patch.queue(agent, policies, {policy['id']}, patch.utcnow() + timedelta(minutes=15))
    assert run
    assert await patch.queue(agent, policies, {policy['id']}, patch.utcnow() + timedelta(minutes=15)) is None
    rows = await rmm_repo.next_jobs_for_agent(agent['id'])
    job = rmm_scripts._job_payload(rows[0])
    assert job['language'] == 'powershell' and job['timeout_seconds'] == 7200
    config = json.loads(job['env']['MYPORTAL_PATCH_PLAN'])
    assert config['windows']['reboot_mode'] == 'never'
    assert await patch.job_is_current(agent, job)
    values = patch.validate(payload(name='Edited'), 1, True)
    await repo.save(policy['id'], {**values, 'updated_by_user_id': 9})
    assert not await patch.job_is_current(agent, job)
    assert await rmm_scripts.claim_jobs(agent) == []
    assert (await rmm_repo.get_run(run))['status'] == 'cancelled'


@pytest.mark.anyio
async def test_app_run_reboot_policy_and_expiry(patch_db):
    _, agent = await _enrol(patch_db)
    win = await save(reboot_mode='no_users', target_release='26H2')
    app = await save(kind='chocolatey', package_id='7zip', display_name='7-Zip')
    policies = await repo.list_policies(1)
    run = await patch.queue(agent, policies, {app['id']}, patch.utcnow() - timedelta(seconds=1))
    job = rmm_scripts._job_payload((await rmm_repo.next_jobs_for_agent(agent['id']))[0])
    config = json.loads(job['env']['MYPORTAL_PATCH_PLAN'])
    assert config['windows'] is None
    assert 'feature_upgrade' not in config
    assert config['reboot']['reboot_mode'] == 'no_users'
    assert config['apps'][0]['package_id'] == '7zip'
    await repo.delete(win['id'])
    assert not await patch.job_is_current(agent, job)
    assert await rmm_scripts.claim_jobs(agent) == []
    assert (await rmm_repo.get_run(run))['status'] == 'cancelled'


@pytest.mark.anyio
async def test_scheduler_claims_once_and_skips_old_windows(patch_db, monkeypatch):
    _, agent = await _enrol(patch_db)
    policy = await save()
    now = datetime(2026, 10, 14, 12)
    monkeypatch.setattr(patch, 'utcnow', lambda: now)
    await patch_db.execute('UPDATE rmm_patch_policies SET next_run_at = %s WHERE id = %s',
                           (now - timedelta(minutes=1), policy['id']))
    await patch.tick()
    assert len(await rmm_repo.next_jobs_for_agent(agent['id'])) == 1
    await patch.tick()
    assert len(await rmm_repo.next_jobs_for_agent(agent['id'])) == 1
    for row in await rmm_repo.next_jobs_for_agent(agent['id']):
        await rmm_repo.cancel_run(row['id'])
    await patch_db.execute('UPDATE rmm_patch_policies SET next_run_at = %s WHERE id = %s',
                           (now - timedelta(hours=2), policy['id']))
    await patch.tick()
    assert await rmm_repo.next_jobs_for_agent(agent['id']) == []


def test_template_renders_both_forms_and_permissions():
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'))
    # Render the real content block, avoiding unrelated base-template context.
    template = env.get_template('rmm/patching.html')
    for kind in ['windows', 'chocolatey']:
        context = template.new_context({'editing': payload(kind=kind), 'errors': {}, 'categories': sorted(patch.CATEGORIES),
                                       'csrf_token': 'test', 'is_super_admin': True, 'policies': []})
        html = ''.join(template.blocks['content'](context))
        assert 'data-rmm-rule-builder' in html and 'name="csrf_token"' in html
        assert 'name="reboot_mode"' in html and 'name="display_name"' in html


@pytest.mark.anyio
async def test_preview_limits_to_windows_and_company(patch_db, monkeypatch):
    _, agent = await _enrol(patch_db)
    await _enrol(patch_db, asset_id=11, uid='linux-agent', tray_id=6, os_name='linux')
    monkeypatch.setattr(patch_routes, '_context', AsyncMock(return_value=({'id': 9}, {}, 1, True)))
    from app.features.rmm.check_routes import TargetingPreviewRequest
    rules = {'operator': 'and', 'children': [{'field': 'company.id', 'operator': 'equals', 'value': 1}]}
    result = await patch_routes.preview(SimpleNamespace(), TargetingPreviewRequest(rules=rules))
    assert result['matched_count'] == result['eligible_count'] == 1
    assert result['agents'][0]['id'] == agent['id']
    with pytest.raises(HTTPException) as exc:
        await patch_routes.preview(SimpleNamespace(), TargetingPreviewRequest(scope='global', rules=rules))
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_disabled_deleted_overridden_and_corrupt_policy_cancel(patch_db):
    _, agent = await _enrol(patch_db)
    policy = await save(scope='global')
    config = patch.plan([policy], {policy['id']})
    job = {'env': {'MYPORTAL_PATCH_PLAN': json.dumps(config)}}
    await save(is_enabled=False)
    assert not await patch.job_is_current(agent, job)
    await patch_db.execute('DELETE FROM rmm_patch_policies WHERE company_id = 1')
    assert await patch.job_is_current(agent, job)
    await patch_db.execute("UPDATE rmm_patch_policies SET settings_json = '{}' WHERE id = %s", (policy['id'],))
    assert await patch.effective(await repo.list_policies(1), agent) == []
    assert not await patch.job_is_current(agent, job)
    await repo.delete(policy['id'])
    assert not await patch.job_is_current(agent, job)


def test_app_policy_ignore_options_are_saved_only_when_on():
    base = payload(kind='chocolatey', package_id='googlechrome', display_name='Google Chrome')
    assert patch.validate(base, 1, False)['settings'] == {'display_name': 'Google Chrome', 'install_missing': False}
    checked = patch.validate({**base, 'ignore_pending_reboots': '1', 'ignore_checksums': '1'}, 1, False)
    assert checked['settings'] == {'display_name': 'Google Chrome', 'install_missing': False,
                                   'ignore_pending_reboots': True, 'ignore_checksums': True}
    # Windows Update policies do not take the application-only options.
    assert 'ignore_checksums' not in patch.validate(payload(ignore_checksums='1'), 1, False)['settings']


def test_app_policies_saved_before_the_ignore_options_stay_valid():
    policy = {**payload(kind='chocolatey', package_id='googlechrome'), 'company_id': 1,
              'settings': {'display_name': 'Google Chrome', 'install_missing': False}}
    assert patch.valid_saved(policy)
    policy['settings'] = {**policy['settings'], 'ignore_checksums': True}
    assert patch.valid_saved(policy)


@pytest.mark.anyio
async def test_app_policy_ignore_options_reach_the_device_plan(patch_db):
    _, agent = await _enrol(patch_db)
    app = await save(kind='chocolatey', package_id='googlechrome', display_name='Google Chrome',
                     ignore_pending_reboots='1', ignore_checksums='1')
    await patch.queue(agent, await repo.list_policies(1), {app['id']}, patch.utcnow() + timedelta(minutes=15))
    job = rmm_scripts._job_payload((await rmm_repo.next_jobs_for_agent(agent['id']))[0])
    config = json.loads(job['env']['MYPORTAL_PATCH_PLAN'])
    assert config['apps'][0]['ignore_pending_reboots'] is True
    assert config['apps'][0]['ignore_checksums'] is True
    assert await patch.job_is_current(agent, job)


def test_policy_editor_shows_app_ignore_options():
    template = (ROOT / 'app/templates/rmm/patching.html').read_text()
    assert 'name="ignore_pending_reboots"' in template and 'name="ignore_checksums"' in template
