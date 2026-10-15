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
from app.services import rmm_software_updates as updates, rmm_patching as patch, rmm_scripts
from app.repositories import rmm as rmm_repo, rmm_inventory as inventory_repo
from app.features.rmm import patch_routes, inventory_routes
from app.features.companies import handlers
from tests.test_rmm_scripts import sqlite_db, anyio_backend, _enrol  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, 'ENV_FILE', tmp_path / '.env')
    for key in list(sources.os.environ):
        if sources.KEY.fullmatch(key):
            monkeypatch.delenv(key)
    catalog._CACHE.clear()


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
    assert [s['enabled'] for s in await sources.for_company(1)] == [False, True]
    assert [s['enabled'] for s in await sources.for_company(2)] == [True, False]
    assert (await sources.for_company(1, secrets=True))[1]['password'] == 'secret-value'
    assert 'secret-value' not in json.dumps(await sources.for_company(1))
    with pytest.raises(ValueError):
        await sources.save(1, ['MISSING'])
    await sources.save(1, [])
    assert not any(s['enabled'] for s in await sources.for_company(1))


def feed(entries):
    return '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">''' + ''.join(
        f'<entry><m:properties><d:Id>{id}</d:Id><d:Title>{title}</d:Title><d:Version>{version}</d:Version><d:IsPrerelease>false</d:IsPrerelease></m:properties></entry>'
        for id, title, version in entries) + '</feed>'


@pytest.mark.anyio
async def test_catalog_parses_stable_feed_caches_and_escapes_queries(monkeypatch):
    calls = []
    def request(req):
        calls.append(req)
        return httpx.Response(200, text=feed([('googlechrome', 'Google Chrome', '150.0'), ('bad;id', 'Bad', '1')]))
    transport = httpx.MockTransport(request)
    monkeypatch.setattr(catalog, 'monitored_client', lambda *a, **kw: httpx.AsyncClient(transport=transport))
    rows = await catalog.query("Vendor's App", exact=True)
    assert rows == [{'package_id': 'googlechrome', 'title': 'Google Chrome', 'version': '150.0'}]
    assert "Vendor''s App" in calls[0].url.params['$filter']
    await catalog.query("Vendor's App", exact=True)
    assert len(calls) == 1
    assert await catalog.match('Google Chrome') == rows[0]
    assert await catalog.match('Chrome') is None


@pytest.mark.anyio
async def test_catalog_unavailable_does_not_cache_false_no_match(monkeypatch):
    def request(req):
        return httpx.Response(503)
    monkeypatch.setattr(catalog, 'monitored_client', lambda *a, **kw: httpx.AsyncClient(transport=httpx.MockTransport(request)))
    with pytest.raises(catalog.CatalogUnavailable):
        await catalog.match('Google Chrome')
    assert not catalog._CACHE


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
    assert [s['enabled'] for s in config['sources']] == [False, True]
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
    await sources.save(1, [])
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
