"""Source policy and feed/upgrade behavior, with no network or package changes."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader

from app.services import chocolatey_sources as sources, chocolatey_catalog as catalog
from app.repositories import chocolatey_catalog as catalog_repo
from app.services import rmm_software_updates as updates, rmm_patching as patch, rmm_scripts
from app.repositories import rmm as rmm_repo, rmm_inventory as inventory_repo
from app.features.rmm import patch_routes, inventory_routes
from app.features.companies import handlers
from tests.test_rmm_scripts import sqlite_db, anyio_backend, _enrol  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_sources(monkeypatch, tmp_path, request):
    monkeypatch.setattr(sources, 'ENV_FILE', tmp_path / '.env')
    for key in list(sources.os.environ):
        if sources.KEY.fullmatch(key):
            monkeypatch.delenv(key)
    monkeypatch.setattr(catalog, 'PAGE_DELAY', 0)
    if 'sqlite_db' not in request.fixturenames:
        # Empty local inventory-derived catalog.
        monkeypatch.setattr(catalog_repo, 'search', AsyncMock(return_value=[]))
        monkeypatch.setattr(catalog_repo, 'find_exact', AsyncMock(return_value=[]))
        monkeypatch.setattr(catalog_repo, 'has_packages', AsyncMock(return_value=False))


def test_numbered_env_definitions_and_safe_errors(monkeypatch):
    monkeypatch.setenv('CHOCO_SOURCE_CUSTOM', '-n=nexus -s="https://repo.example.com/" -u username -p "pass word"')
    monkeypatch.setenv('CHOCO_SOURCE_CUSTOM3', '-n=three -s=https://three.example.com')
    monkeypatch.setenv('CHOCO_SOURCE_CUSTOM12', '-n=twelve -s=https://twelve.example.com')
    configured = sources.definitions()
    assert [s['key'] for s in configured] == ['DEFAULT', 'CUSTOM', 'CUSTOM3', 'CUSTOM12']
    assert configured[1]['name'] == 'nexus' and configured[1]['password'] == 'pass word'
    monkeypatch.setenv('CHOCO_SOURCE_CUSTOM1', '-n=bad -s="https://repo.example.com" -p secret-value -x exploit')
    with pytest.raises(ValueError) as exc:
        sources.definitions()
    assert str(exc.value) == 'Invalid Chocolatey source definition in CHOCO_SOURCE_CUSTOM1.'
    assert 'secret-value' not in str(exc.value)


def test_dotenv_numbered_values_and_environment_precedence(monkeypatch, tmp_path):
    source_file = tmp_path / '.env'
    source_file.write_text('CHOCO_SOURCE_CUSTOM2=\'-n=dotenv -s="https://repo.example.com" -u "user" -p "$password"\'\n')
    monkeypatch.setattr(sources, 'ENV_FILE', source_file)
    assert sources.definitions()[1]['password'] == '$password'
    monkeypatch.setenv('CHOCO_SOURCE_CUSTOM2', '-n=override -s=https://override.example.com')
    assert sources.definitions()[1]['name'] == 'override'


@pytest.mark.anyio
async def test_company_source_defaults_isolation_and_no_secret_views(sqlite_db, monkeypatch):
    monkeypatch.setenv('CHOCO_SOURCE_CUSTOM1', '-n=nexus -s=https://repo.example.com -u user -p secret-value')
    assert await sources.for_company(1) == [
        {'key': 'DEFAULT', 'name': 'chocolatey', 'enabled': True},
        {'key': 'CUSTOM1', 'name': 'nexus', 'enabled': False}]
    await sources.save(1, ['CUSTOM1'])
    assert [s['enabled'] for s in await sources.for_company(1)] == [True, True]
    assert [s['enabled'] for s in await sources.for_company(2)] == [True, False]
    assert (await sources.for_company(1, secrets=True))[1]['password'] == 'secret-value'
    assert 'secret-value' not in json.dumps(await sources.for_company(1))
    with pytest.raises(ValueError):
        await sources.save(1, ['MISSING'])
    await sources.save(1, [])
    assert [s['enabled'] for s in await sources.for_company(1)] == [True, False]


def feed(entries):
    return '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">''' + ''.join(
        f'<entry><m:properties><d:Id>{entry[0]}</d:Id><d:Title>{entry[1]}</d:Title><d:Version>{entry[2]}</d:Version><d:IsPrerelease>false</d:IsPrerelease>'
        + (f'<d:DownloadCount>{entry[3]}</d:DownloadCount>' if len(entry) > 3 else '') + '</m:properties></entry>'
        for entry in entries) + '</feed>'


def mock_feed(monkeypatch, request):
    monkeypatch.setattr(catalog, 'monitored_client', lambda *a, **kw: httpx.AsyncClient(transport=httpx.MockTransport(request)))


@pytest.mark.anyio
async def test_catalog_uses_stored_matches_and_parses_stable_feed(monkeypatch):
    count, rows = catalog._parse(feed([('googlechrome', 'Google Chrome', '150.0'),
                                     ('bad;id', 'Bad', '1')]).encode())
    assert count == 2
    assert rows == [{'package_id': 'googlechrome', 'title': 'Google Chrome', 'version': '150.0',
                     'download_count': 0}]
    monkeypatch.setattr(catalog_repo, 'find_exact', AsyncMock(return_value=rows))
    mock_feed(monkeypatch, _no_network)
    assert await catalog.query('Google Chrome', exact=True) == [
        {'package_id': 'googlechrome', 'title': 'Google Chrome', 'version': '150.0'}]
    assert await catalog.match('Google Chrome') == {'package_id': 'googlechrome', 'title': 'Google Chrome', 'version': '150.0'}
    assert await catalog.match('Chrome') is None


@pytest.mark.anyio
async def test_empty_catalog_does_not_query_public_repository(monkeypatch):
    mock_feed(monkeypatch, _no_network)
    assert await catalog.query('') == []
    assert await catalog.match('Google Chrome') is None


@pytest.mark.parametrize('installed,latest,expected', [('1.0', '2.0', True), ('2', '2.0.0', False),
    ('3', '2', False), ('unknown', '2', False), ('1.2-beta', '2', False)])
@pytest.mark.anyio
async def test_inventory_upgrade_comparison(monkeypatch, installed, latest, expected):
    monkeypatch.setattr(inventory_repo, 'list_software', AsyncMock(return_value=[{'name': 'App', 'version': installed, 'source': 'machine'}]))
    monkeypatch.setattr(catalog, 'match', AsyncMock(return_value={'package_id': 'app', 'title': 'App', 'version': latest}))
    result = await updates.upgrade_for({'id': 1, 'status': 'active', 'os': 'windows'}, 'App')
    assert result['can_upgrade'] is expected
    assert await updates.upgrade_for({'id': 1, 'status': 'active', 'os': 'linux'}, 'App') is None


@pytest.mark.anyio
async def test_duplicate_and_user_installations_do_not_upgrade(monkeypatch):
    agent = {'id': 1, 'status': 'active', 'os': 'windows'}
    for software in [[{'name': 'App', 'version': '1', 'source': 'user'}],
                     [{'name': 'App', 'version': '1'}, {'name': 'App', 'version': '2'}]]:
        monkeypatch.setattr(inventory_repo, 'list_software', AsyncMock(return_value=software))
        assert await updates.upgrade_for(agent, 'App') is None


@pytest.mark.anyio
async def test_manual_upgrade_encrypted_job_and_inventory_revalidation(sqlite_db, monkeypatch):
    _, agent = await _enrol(sqlite_db)
    installed = AsyncMock(return_value=[{'name': 'App', 'version': '1', 'source': 'machine'}])
    monkeypatch.setattr(inventory_repo, 'list_software', installed)
    monkeypatch.setattr(catalog, 'match', AsyncMock(return_value={'package_id': 'app', 'title': 'App', 'version': '2'}))
    monkeypatch.setenv('CHOCO_SOURCE_CUSTOM1', '-n=nexus -s=https://repo.example.com -u user -p secret-value')
    await sources.save(1, ['CUSTOM1'])
    run_id = await updates.queue(agent, 'App', 9)
    row = (await rmm_repo.next_jobs_for_agent(agent['id']))[0]
    assert 'secret-value' not in json.dumps(row, default=str)
    job = rmm_scripts._job_payload(row)
    assert await patch.job_is_current(agent, job)
    # Source changes made after queueing take effect at dispatch.
    await sources.save(1, ['DEFAULT'])
    dispatched = await rmm_scripts.claim_jobs(agent)
    config = json.loads(dispatched[0]['env']['MYPORTAL_PATCH_PLAN'])
    assert [s['enabled'] for s in config['sources']] == [True, False]
    assert 'Sync-ChocolateySources' in dispatched[0]['script']
    installed.return_value[0]['version'] = '2'
    assert not await patch.job_is_current(agent, job)
    assert (await rmm_repo.get_run(run_id))['status'] == 'dispatched'


@pytest.mark.anyio
async def test_upgrade_api_company_boundary(monkeypatch):
    monkeypatch.setattr(patch_routes, '_context', AsyncMock(return_value=({'id': 9}, {}, 2, True)))
    monkeypatch.setattr(rmm_repo, 'get_asset_agent', AsyncMock(return_value={'id': 7, 'company_id': 1}))
    with pytest.raises(HTTPException) as exc:
        await patch_routes.software_upgrade_info(10, SimpleNamespace(), 'App')
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_source_changes_require_company_admin(monkeypatch):
    main = SimpleNamespace(_require_authenticated_user=AsyncMock(return_value=({'id': 9}, None)))
    monkeypatch.setattr(handlers, '_main', lambda: main)
    monkeypatch.setattr(handlers, '_get_company_management_scope', AsyncMock(return_value=(False, [], {1: {'is_admin': False}})))
    with pytest.raises(HTTPException) as exc:
        await handlers.admin_company_chocolatey_sources(1, SimpleNamespace())
    assert exc.value.status_code == 403


def test_software_upgrade_action_is_a_peer_table_cell():
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'))
    html = env.get_template('rmm/_asset_software.html').render(asset={'id': 10}, csrf_token='token',
        rmm={'inventory': {'inventory': {'received_at': 'today', 'details': {}}, 'software_apps': [
            {'name': 'App', 'version': '1', 'source': 'machine', 'installed': True, 'history': []}], 'software': [1]}})
    assert html.count('data-choco-upgrade ') == 1
    assert html.index('</details></td>') < html.index('data-choco-version')
    assert 'name="_csrf"' in html


@pytest.mark.anyio
async def test_agent_source_response_is_private_and_contains_reconciler(sqlite_db, monkeypatch):
    monkeypatch.setenv('CHOCO_SOURCE_CUSTOM2', '-n=nexus -s=https://repo.example.com -u user -p secret-value')
    await sources.save(2, ['CUSTOM2'])
    response = await patch_routes.agent_sources({'company_id': 2, 'os': 'windows'})
    assert response.headers['cache-control'] == 'no-store'
    config = json.loads(response.body)
    assert [s['enabled'] for s in config['sources']] == [True, True]
    assert config['sources'][1]['password'] == 'secret-value'
    assert 'Sync-ChocolateySources' in config['script'] and 'MyPortalPatchManagement' in config['script']
    assert 'secret-value' not in config['script']
    with pytest.raises(HTTPException) as exc:
        await patch_routes.agent_sources({'company_id': 2, 'os': 'linux'})
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_all_sources_disabled_cancels_queued_app_job(sqlite_db, monkeypatch):
    _, agent = await _enrol(sqlite_db)
    values = patch.validate({**patch.DEFAULTS, 'name': 'App patches', 'timezone': 'UTC',
                             'kind': 'chocolatey', 'package_id': 'app', 'display_name': 'App'}, 1, True)
    policy_id = await patch.repo.save(None, {**values, 'updated_by_user_id': 9})
    policy = await patch.repo.get_policy(policy_id)
    from datetime import timedelta
    run_id = await patch.queue(agent, [policy], {policy_id}, patch.utcnow() + timedelta(minutes=15))
    # Companies cannot disable the default source; only removing it from the ENV leaves none enabled.
    monkeypatch.setenv('CHOCO_SOURCE_DEFAULT', '')
    assert await rmm_scripts.claim_jobs(agent) == []
    assert (await rmm_repo.get_run(run_id))['status'] == 'cancelled'


@pytest.mark.anyio
async def test_numbered_sources_prioritize_numeric_order_default_last(sqlite_db, monkeypatch):
    for suffix in ['10', '2', '', '1']:
        monkeypatch.setenv('CHOCO_SOURCE_CUSTOM' + suffix,
                          f'-n=custom{suffix or "unnumbered"} -s=https://repo{suffix}.example.com')
    await sources.save(1, ['DEFAULT', 'CUSTOM', 'CUSTOM1', 'CUSTOM2', 'CUSTOM10'])
    configured = await sources.for_company(1, secrets=True)
    ordered = sorted(configured, key=lambda source: source['priority'])
    assert [s['key'] for s in ordered] == ['CUSTOM1', 'CUSTOM2', 'CUSTOM10', 'CUSTOM', 'DEFAULT']
    assert [s['priority'] for s in ordered] == [1, 2, 3, 4, 5]
    # Disabling a preferred source keeps the remaining relative order intact.
    await sources.save(1, ['DEFAULT', 'CUSTOM1', 'CUSTOM10'])
    enabled = sorted((s for s in await sources.for_company(1, secrets=True) if s['enabled']),
                     key=lambda source: source['priority'])
    assert [s['key'] for s in enabled] == ['CUSTOM1', 'CUSTOM10', 'DEFAULT']
    await sources.save(1, ['DEFAULT'])
    assert [s['key'] for s in await sources.for_company(1, secrets=True) if s['enabled']] == ['DEFAULT']


async def _stored(db):
    return await db.fetch_all('SELECT package_id, title, version, download_count FROM rmm_chocolatey_packages ORDER BY package_id')


def _no_network(req):
    raise AssertionError('The stored app list should be used')


@pytest.mark.anyio
async def test_refresh_looks_up_only_current_software_and_replaces_stored_list(sqlite_db, monkeypatch):
    monkeypatch.setattr(catalog_repo, 'db', sqlite_db)
    monkeypatch.setattr(inventory_repo, 'db', sqlite_db)
    _, agent = await _enrol(sqlite_db)
    await sqlite_db.execute("INSERT INTO rmm_chocolatey_packages (package_id, title, version) VALUES ('retired', 'Retired', '1')")
    for name, version in [('Google Chrome', '1'), ('Google Chrome', '2'), ('VLC', '3'), ('No Match', '1')]:
        await sqlite_db.execute("INSERT INTO rmm_device_software (agent_id, name, version) VALUES (%s, %s, %s)",
                               (agent['id'], name, version))
    calls = []
    def request(req):
        term = req.url.params['searchTerm']
        calls.append(term)
        entries = {"'Google Chrome'": [('googlechrome', 'Google Chrome', '150.0', 5000)],
                   "'VLC'": [('vlc', 'VLC', '3.0.21', 700)], "'No Match'": []}
        return httpx.Response(200, text=feed(entries[term]))
    mock_feed(monkeypatch, request)
    result = await catalog.refresh()
    assert set(calls) == {"'Google Chrome'", "'VLC'", "'No Match'"} and len(calls) == 3
    assert result == {'source': catalog.COMMUNITY, 'applications': 3, 'matched': 2,
                      'unmatched': 1, 'ambiguous': 0, 'rejected': 0, 'rejected_names': [],
                      'failed': 0, 'failed_names': [], 'stored': 2, 'removed': 1}
    assert [p['package_id'] for p in await _stored(sqlite_db)] == ['googlechrome', 'vlc']
    assert await catalog.last_updated()
    mock_feed(monkeypatch, _no_network)
    assert await catalog.match('google chrome') == {'package_id': 'googlechrome', 'title': 'Google Chrome',
                                                   'version': '150.0', 'display_name': 'Google Chrome'}
    assert await catalog.query('not installed') == []


@pytest.mark.anyio
async def test_decorated_registry_names_reach_patch_picker_and_software_upgrade(sqlite_db, monkeypatch):
    monkeypatch.setattr(catalog_repo, 'db', sqlite_db)
    monkeypatch.setattr(inventory_repo, 'db', sqlite_db)
    _, agent = await _enrol(sqlite_db)
    installed = [('7-Zip 24.09 (x64)', '24.09'), ('7-Zip 23.01 (x64)', '23.01'),
                 ('Mozilla Firefox (x64 en-US)', '130.0'), ('Google Chrome', '129.0')]
    for name, version in installed:
        await sqlite_db.execute("INSERT INTO rmm_device_software (agent_id, name, version, source) "
                                "VALUES (%s, %s, %s, 'machine')", (agent['id'], name, version))

    def request(req):
        entries = {
            "'7-Zip'": [('7zip.install', '7-Zip (Install)', '25.01', 100)],
            "'Mozilla Firefox'": [('Firefox', 'Mozilla Firefox', '143.0', 200)],
            "'Google Chrome'": [('GoogleChrome', 'Google Chrome', '150.0', 300)],
        }
        return httpx.Response(200, text=feed(entries[req.url.params['searchTerm']]))

    mock_feed(monkeypatch, request)
    summary = await catalog.refresh()
    assert summary['matched'] == 4 and summary['stored'] == 3
    mock_feed(monkeypatch, _no_network)

    async def context(*args, **kwargs):
        return {'id': 9}, {}, 1, True

    monkeypatch.setattr(patch_routes, '_context', context)
    picker = await patch_routes.packages(SimpleNamespace(), q='')
    assert len(picker['packages']) == 4
    assert {p['display_name'] for p in picker['packages']} == {name for name, _ in installed}
    assert len(await catalog.query('24.09')) == 1
    # Every original registry name resolves to the verified package after refresh.
    # Two versions of the same product must not overwrite each other's mapping.
    for name, version in installed:
        info = await patch_routes.software_upgrade_info(10, SimpleNamespace(), name=name)
        assert info['application']['display_name'] == name
        assert info['application']['installed_version'] == version
        assert info['application']['can_upgrade'] is True

    # Refreshes and rejected lookups preserve the mapping, but removed apps lose it.
    mock_feed(monkeypatch, lambda req: httpx.Response(406) if '7-Zip' in req.url.params['searchTerm']
              else request(req))
    assert (await catalog.refresh())['rejected'] == 2
    assert (await catalog.match(installed[0][0]))['package_id'] == '7zip.install'
    await sqlite_db.execute("DELETE FROM rmm_device_software WHERE name = %s", (installed[0][0],))
    mock_feed(monkeypatch, request)
    await catalog.refresh()
    assert await catalog.match(installed[0][0]) is None
    assert (await catalog.match(installed[1][0]))['package_id'] == '7zip.install'


@pytest.mark.anyio
async def test_failed_lookup_keeps_that_apps_match_and_saves_the_rest(sqlite_db, monkeypatch):
    monkeypatch.setattr(catalog_repo, 'db', sqlite_db)
    await sqlite_db.execute("INSERT INTO rmm_chocolatey_packages (package_id, title, version) VALUES ('vlc', 'VLC', '3')")
    await sqlite_db.execute("INSERT INTO rmm_chocolatey_software_matches (name, package_id) VALUES ('VLC', 'vlc')")
    monkeypatch.setattr(inventory_repo, 'list_catalog_software_names', AsyncMock(return_value=['Chrome', 'VLC']))
    monkeypatch.setattr(catalog.asyncio, 'sleep', AsyncMock())
    def request(req):
        if req.url.params['searchTerm'] == "'Chrome'":
            return httpx.Response(200, text=feed([('googlechrome', 'Chrome', '150.0')]))
        return httpx.Response(429)
    mock_feed(monkeypatch, request)
    result = await catalog.refresh()
    assert result['failed_names'] == ['VLC'] and result['matched'] == 1
    assert [p['package_id'] for p in await _stored(sqlite_db)] == ['googlechrome', 'vlc']
    mock_feed(monkeypatch, _no_network)
    assert (await catalog.match('VLC'))['package_id'] == 'vlc'


@pytest.mark.anyio
async def test_catalog_candidates_exclude_removed_and_non_windows_agents(sqlite_db, monkeypatch):
    monkeypatch.setattr(inventory_repo, 'db', sqlite_db)
    _, active = await _enrol(sqlite_db)
    _, revoked = await _enrol(sqlite_db, tray_id=7, asset_id=11, uid='revoked-agent')
    _, linux = await _enrol(sqlite_db, tray_id=8, asset_id=20, uid='linux-agent', os_name='linux')
    await sqlite_db.execute("UPDATE rmm_agents SET status = 'revoked' WHERE id = %s", (revoked['id'],))
    for agent, name in [(active, 'Active App'), (active, 'Active App'), (active, ' '),
                        (revoked, 'Removed App'), (linux, 'Linux App')]:
        await sqlite_db.execute("INSERT INTO rmm_device_software (agent_id, name) VALUES (%s, %s)", (agent['id'], name))
    assert await inventory_repo.list_catalog_software_names() == ['Active App']


@pytest.mark.anyio
async def test_scheduler_dispatches_chocolatey_catalog_refresh(monkeypatch):
    from app.services.scheduler import SchedulerService

    class Lock:
        async def __aenter__(self):
            return True

        async def __aexit__(self, *_args):
            return None

    refresh = AsyncMock(return_value={'stored': 10})
    record = AsyncMock()
    monkeypatch.setattr(catalog, 'refresh', refresh)
    monkeypatch.setattr('app.services.scheduler.scheduled_tasks_repo.record_task_run', record)
    monkeypatch.setattr('app.services.scheduler.scheduled_tasks_repo.has_run_since', AsyncMock(return_value=False))
    monkeypatch.setattr('app.services.scheduler.db.acquire_lock', lambda *_args, **_kwargs: Lock())
    await SchedulerService()._run_task({'id': 5, 'command': 'update_chocolatey_catalog'})
    refresh.assert_awaited_once_with()
    assert record.await_args.kwargs['status'] == 'succeeded'
    assert '"stored": 10' in record.await_args.kwargs['details']


@pytest.mark.anyio
async def test_default_task_is_seeded_once_and_keeps_admin_changes():
    import aiosqlite
    from app.core.database import Database
    sql = Database()._adapt_sql_for_sqlite((ROOT / 'migrations/497_chocolatey_catalog_scheduled_task.sql').read_text())
    async with aiosqlite.connect(':memory:') as conn:
        await conn.execute('CREATE TABLE scheduled_tasks (id INTEGER PRIMARY KEY, company_id INT, name TEXT, command TEXT, '
                           'cron TEXT, active INT, description TEXT, max_retries INT, retry_backoff_seconds INT)')
        await conn.executescript(sql)
        rows = await (await conn.execute('SELECT name, command, cron, active, company_id FROM scheduled_tasks')).fetchall()
        assert [tuple(row) for row in rows] == [('Update Chocolatey app list', 'update_chocolatey_catalog', '23 4 * * *', 1, None)]
        await conn.execute("UPDATE scheduled_tasks SET cron = '0 */6 * * *'")
        await conn.executescript(sql)
        assert [tuple(row) for row in await (await conn.execute('SELECT cron FROM scheduled_tasks')).fetchall()] == [('0 */6 * * *',)]


@pytest.mark.anyio
async def test_admins_can_create_chocolatey_catalog_task(monkeypatch):
    from app import main
    captured = {}

    async def render(template, request, user, extra):
        captured.update(extra)
        return 'rendered'

    monkeypatch.setattr(main, '_require_super_admin_page', AsyncMock(return_value=({'id': 1, 'is_super_admin': True}, None)))
    monkeypatch.setattr(main.scheduled_tasks_repo, 'list_tasks', AsyncMock(return_value=[]))
    monkeypatch.setattr(main.company_repo, 'list_companies', AsyncMock(return_value=[]))
    monkeypatch.setattr(main.modules_service, 'list_modules', AsyncMock(return_value=[]))
    monkeypatch.setattr(main, '_render_template', render)
    await main.admin_scheduled_tasks(SimpleNamespace(), show_inactive=False)
    assert {'value': 'update_chocolatey_catalog', 'label': 'Update Chocolatey app list',
            'group': 'Staff and assets'} in captured['command_options']


@pytest.mark.anyio
async def test_pushed_install_uses_inventory_names_and_revalidates_at_dispatch(sqlite_db, monkeypatch):
    monkeypatch.setattr(catalog_repo, 'db', sqlite_db)
    _, agent = await _enrol(sqlite_db)
    await sqlite_db.execute("INSERT INTO rmm_chocolatey_packages (package_id, title, version) VALUES "
                            "('googlechrome', 'Google Chrome', '150.0'), ('7zip', '7-Zip', '25.01')")
    await sqlite_db.execute("INSERT INTO rmm_chocolatey_software_matches (name, package_id) VALUES "
                            "('Google Chrome', 'googlechrome')")
    monkeypatch.setattr(inventory_repo, 'list_software', AsyncMock(return_value=[
        {'name': 'Google Chrome', 'version': '149.0', 'source': 'machine'}]))
    ids = updates.package_ids([' GoogleChrome ', '7zip', 'vlc', 'googlechrome'])
    assert ids == ['googlechrome', '7zip', 'vlc']
    run_id = await updates.queue_apps(agent, ids, 'install', 9)
    row = (await rmm_repo.next_jobs_for_agent(agent['id']))[0]
    job = rmm_scripts._job_payload(row)
    config = json.loads(job['env']['MYPORTAL_PATCH_PLAN'])
    # Installed apps keep the device's exact name so current versions are skipped.
    assert config['apps'] == [
        {'package_id': 'googlechrome', 'display_name': 'Google Chrome', 'install_missing': True},
        {'package_id': '7zip', 'display_name': '7-Zip', 'install_missing': True},
        {'package_id': 'vlc', 'display_name': 'vlc', 'install_missing': True}]
    assert config['windows'] is None and config['reboot'] == {'reboot_mode': 'never'}
    assert await patch.job_is_current(agent, job)
    assert not await patch.job_is_current({**agent, 'os': 'linux'}, job)
    tampered = {**config, 'apps': config['apps'] + [{'package_id': 'other', 'display_name': 'Other'}]}
    assert not await patch.job_is_current(agent, {'env': {'MYPORTAL_PATCH_PLAN': json.dumps(tampered)}})
    with pytest.raises(rmm_scripts.RunRequestError, match='already waiting'):
        await updates.queue_apps(agent, ['7zip'], 'install', 9)
    assert (await rmm_repo.get_run(run_id))['status'] == 'queued'


@pytest.mark.anyio
async def test_upgrade_uses_device_state_even_when_portal_inventory_is_empty(sqlite_db, monkeypatch):
    monkeypatch.setattr(catalog_repo, 'db', sqlite_db)
    _, agent = await _enrol(sqlite_db)
    monkeypatch.setattr(inventory_repo, 'list_software', AsyncMock(return_value=[]))
    assert await updates.queue_apps(agent, ['7zip'], 'upgrade', 9)
    row = (await rmm_repo.next_jobs_for_agent(agent['id']))[0]
    config = json.loads(rmm_scripts._job_payload(row)['env']['MYPORTAL_PATCH_PLAN'])
    assert config['manual_apps']['mode'] == 'upgrade'
    assert config['apps'] == [{'package_id': '7zip', 'display_name': '7zip', 'install_missing': False}]
    with pytest.raises(rmm_scripts.RunRequestError):
        updates.package_ids(['bad id;'])
    with pytest.raises(rmm_scripts.RunRequestError):
        updates.package_ids([f'app{n}' for n in range(updates.MAX_PUSH_PACKAGES + 1)])
    with pytest.raises(rmm_scripts.RunRequestError, match='Windows'):
        await updates.queue_apps({**agent, 'os': 'linux'}, ['7zip'], 'install', 9)


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['install', 'upgrade', 'uninstall'])
@pytest.mark.parametrize('force,ignore_reboots', [(False, True), (True, False)])
async def test_package_options_survive_queue_and_dispatch(sqlite_db, monkeypatch, mode, force, ignore_reboots):
    monkeypatch.setattr(catalog_repo, 'db', sqlite_db)
    _, agent = await _enrol(sqlite_db)
    monkeypatch.setattr(inventory_repo, 'list_software', AsyncMock(return_value=[]))
    if mode == 'uninstall':
        # Removal must work even when no repository is enabled and the package
        # is absent from the catalog and the portal's last inventory.
        await sources.save(int(agent['company_id']), [])
    run_id = await updates.queue_apps(agent, ['private-app'], mode, 9,
        force=force, ignore_pending_reboots=ignore_reboots)
    jobs = await rmm_scripts.claim_jobs(agent)
    assert len(jobs) == 1
    config = json.loads(jobs[0]['env']['MYPORTAL_PATCH_PLAN'])
    assert config['manual_apps'] == {'mode': mode, 'package_ids': ['private-app'],
                                     'force': force, 'ignore_pending_reboots': ignore_reboots}
    assert await patch.job_is_current(agent, jobs[0])
    assert (await rmm_repo.get_run(run_id))['status'] == 'dispatched'
    for field in ('force', 'ignore_pending_reboots'):
        config['manual_apps'][field] = 'false'
        assert not patch.manual_apps_current(agent, config)
        config['manual_apps'][field] = force if field == 'force' else ignore_reboots


@pytest.mark.anyio
@pytest.mark.parametrize('mode,force,ignore_reboots', [('install', False, True), ('upgrade', True, False), ('uninstall', True, True)])
async def test_asset_package_form_passes_selected_options(monkeypatch, mode, force, ignore_reboots):
    from starlette.datastructures import FormData
    monkeypatch.setattr(patch_routes, '_context', AsyncMock(return_value=({'id': 9}, {}, 1, True)))
    agent = {'id': 7, 'company_id': 1, 'os': 'windows', 'status': 'active'}
    monkeypatch.setattr(inventory_routes, '_company_agent', AsyncMock(return_value=agent))
    queue = AsyncMock(return_value=12)
    monkeypatch.setattr(updates, 'queue_apps', queue)
    audit = AsyncMock()
    monkeypatch.setattr(patch_routes.audit, 'record', audit)
    values = [('package_id', '7zip'), ('mode', mode), ('force', 'false'), ('ignore_pending_reboots', 'false')]
    if force:
        values.append(('force', 'true'))
    if ignore_reboots:
        values.append(('ignore_pending_reboots', 'true'))
    request = SimpleNamespace(form=AsyncMock(return_value=FormData(values)))
    response = await patch_routes.software_install(10, request)
    assert response.status_code == 303
    queue.assert_awaited_once_with(agent, ['7zip'], mode, 9, force=force, ignore_pending_reboots=ignore_reboots)
    assert audit.call_args.kwargs['action'] == f'rmm.software.{mode}'


@pytest.mark.anyio
async def test_push_api_passes_force_and_unchecked_reboot_option(monkeypatch):
    monkeypatch.setattr(patch_routes, '_context', AsyncMock(return_value=({'id': 9}, {}, 1, True)))
    agent = {'id': 7, 'company_id': 1, 'os': 'windows', 'status': 'active'}
    monkeypatch.setattr(patch_routes.agents_repo, 'list_active_agents', AsyncMock(return_value=[agent]))
    queue = AsyncMock(return_value=12)
    monkeypatch.setattr(updates, 'queue_apps', queue)
    monkeypatch.setattr(patch_routes.audit, 'record', AsyncMock())
    response = await patch_routes.push_apps(SimpleNamespace(), patch_routes.AppPushRequest(
        package_ids=['7zip'], mode='uninstall', force=True, ignore_pending_reboots=False, all_devices=True))
    assert response['queued'] == 1
    queue.assert_awaited_once_with(agent, ['7zip'], 'uninstall', 9, force=True, ignore_pending_reboots=False)


@pytest.mark.anyio
async def test_push_api_targets_only_this_companys_windows_devices(monkeypatch):
    monkeypatch.setattr(patch_routes, '_context', AsyncMock(return_value=({'id': 9}, {}, 1, True)))
    agents = [{'id': 1, 'os': 'windows', 'hostname': 'PC1'}, {'id': 2, 'os': 'linux', 'hostname': 'L1'},
              {'id': 3, 'os': 'Windows', 'hostname': 'PC3'}, {'id': 4, 'os': 'windows', 'hostname': 'PC4'}]
    listed = AsyncMock(return_value=agents)
    monkeypatch.setattr(patch_routes.agents_repo, 'list_active_agents', listed)
    queued = []
    async def queue_apps(agent, ids, mode, user_id, **options):
        queued.append((agent['id'], ids, mode))
        assert options == {'force': False, 'ignore_pending_reboots': True}
        if agent['id'] == 3:
            raise rmm_scripts.RunRequestError({'device': 'A patch job is already waiting or running on this device.'})
        return None if agent['id'] == 4 else 100 + agent['id']
    monkeypatch.setattr(updates, 'queue_apps', queue_apps)
    monkeypatch.setattr(patch_routes.audit, 'record', AsyncMock())
    result = await patch_routes.push_apps(SimpleNamespace(), patch_routes.AppPushRequest(
        package_ids=['7zip'], mode='upgrade', all_devices=True))
    listed.assert_awaited_once_with(company_id=1)
    assert [q[0] for q in queued] == [1, 3, 4]
    assert result['queued'] == 1 and result['not_needed'] == 1 and result['problems'][0]['hostname'] == 'PC3'
    queued.clear()
    await patch_routes.push_apps(SimpleNamespace(), patch_routes.AppPushRequest(
        package_ids=['7zip'], agent_ids=[2, 4, 99]))
    assert queued == [(4, ['7zip'], 'install')]
    response = await patch_routes.push_apps(SimpleNamespace(), patch_routes.AppPushRequest(
        package_ids=['7zip'], agent_ids=[2]))
    assert response.status_code == 400
    response = await patch_routes.push_apps(SimpleNamespace(), patch_routes.AppPushRequest(package_ids=['bad id']))
    assert response.status_code == 400


def test_asset_install_form_only_for_windows_agents_with_run_access():
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'))
    template = env.get_template('rmm/_asset_software.html')
    inventory = {'inventory': None, 'software_apps': [], 'software': []}
    html = template.render(asset={'id': 10}, csrf_token='token', rmm={'inventory': {**inventory, 'can_install_apps': True}})
    assert 'action="/rmm/assets/10/software/install"' in html and 'name="package_id"' in html
    html = template.render(asset={'id': 10}, csrf_token='token', rmm={'inventory': inventory})
    assert 'software/install' not in html


@pytest.mark.anyio
async def test_inventory_context_allows_installs_only_on_active_windows_agents(monkeypatch):
    from app.services import rmm_inventory
    monkeypatch.setattr(rmm_inventory.repo, 'get_for_agent', AsyncMock(return_value=None))
    windows = {'id': 1, 'status': 'active', 'os': 'Windows'}
    assert (await rmm_inventory.asset_inventory_context(windows, can_refresh=True))['can_install_apps']
    assert not (await rmm_inventory.asset_inventory_context(windows, can_refresh=False))['can_install_apps']
    linux = {**windows, 'os': 'linux'}
    assert not (await rmm_inventory.asset_inventory_context(linux, can_refresh=True))['can_install_apps']


def test_asset_rmm_menu_offers_install_choco_package_with_modal():
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'))
    menu = env.from_string('{% import "rmm/_macros.html" as rmm_ui %}'
                           '{{ rmm_ui.asset_menu(7, "PC", [], run_script=True, choco_install=choco) }}')
    html = menu.render(choco=True)
    assert 'Install Choco Package' in html and 'aria-controls="choco-install-modal"' in html
    assert 'Install Choco Package' not in menu.render(choco=False)
    # The menu still appears for the Chocolatey action alone.
    alone = env.from_string('{% import "rmm/_macros.html" as rmm_ui %}{{ rmm_ui.asset_menu(7, "PC", [], choco_install=True) }}')
    assert 'data-choco-modal-open' in alone.render()
    modal = env.get_template('rmm/_choco_install_modal.html').render(
        asset={'id': 10}, asset_display_name='PC', csrf_token='token')
    assert 'id="choco-install-modal"' in modal and 'action="/rmm/assets/10/software/install"' in modal
    assert 'name="package_id"' in modal and 'name="_csrf"' in modal
    assert 'value="install"' in modal and 'value="upgrade"' in modal and 'value="uninstall"' in modal
    assert 'name="force" value="true"' in modal
    assert 'name="ignore_pending_reboots" value="true" checked' in modal


def test_asset_page_shows_choco_install_only_for_windows_agents_with_run_access():
    source = (ROOT / 'app/templates/assets/detail.html').read_text()
    assert "data-choco-modal-open" in source
    assert source.count("rmm.can_run and rmm.agent and (rmm.agent.os or '') | lower == 'windows'") == 2
    assert 'rmm_choco_install.js' in source
