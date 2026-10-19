"""One-off Chocolatey application jobs through patch run history.

* inventory-verified upgrades of one installed application (:func:`queue`)
* installs and updates of chosen packages pushed from the asset page or
  Script automation (:func:`queue_apps`), without writing a script
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from app.repositories import chocolatey_catalog as catalog_repo, rmm as rmm_repo, rmm_inventory as inventory_repo, rmm_patching as patch_repo
from app.security.encryption import encrypt_secret
from app.services import chocolatey_catalog as catalog, chocolatey_sources as sources, rmm_patching as patch, rmm_scripts
from app.services.rmm_scripts import RunRequestError


MAX_PUSH_PACKAGES = 20
MODES = {'install', 'upgrade'}


def _windows(agent: dict) -> bool:
    return agent.get('status') == 'active' and str(agent.get('os') or '').lower() == 'windows'


async def upgrade_for(agent: dict, name: str) -> dict | None:
    if not _windows(agent):
        return None
    installed = [item for item in await inventory_repo.list_software(int(agent['id']))
                 if item['name'] == name and item.get('source') != 'user']
    if len(installed) != 1:
        return None
    package = await catalog.match(name)
    if package is None:
        return None
    current, latest = catalog.version_key(installed[0].get('version') or ''), catalog.version_key(package['version'])
    return {**package, 'display_name': name, 'installed_version': installed[0].get('version'),
            'can_upgrade': current is not None and latest is not None and current < latest}


async def queue(agent: dict, name: str, user_id: int) -> int:
    app = await upgrade_for(agent, name)
    if not app or not app['can_upgrade']:
        raise RunRequestError({'software': 'This application has no verified newer Chocolatey version.'})
    if await patch_repo.has_active_run(int(agent['id'])):
        raise RunRequestError({'software': 'A patch job is already waiting or running on this device.'})
    config = {'manual_upgrade': {'name': name, 'package_id': app['package_id']},
              'apps': [{'package_id': app['package_id'], 'display_name': name, 'install_missing': False}],
              'windows': None, 'reboot': {'reboot_mode': 'never'},
              'sources': await sources.for_company(int(agent['company_id']), secrets=True)}
    if not any(s['enabled'] for s in config['sources']):
        raise RunRequestError({'sources': 'Enable a Chocolatey source for this company first.'})
    content = patch.script_content()
    script = {'id': None, 'name': f'Upgrade {name}', 'path': 'builtin/patch.ps1', 'language': 'powershell',
              'content_sha256': hashlib.sha256(content.encode()).hexdigest()}
    problem = rmm_scripts.device_problem(script, agent)
    if problem:
        raise RunRequestError({'device': problem})
    run_id = await rmm_repo.create_run(script=script, content=content, agent_id=int(agent['id']),
        company_id=int(agent['company_id']), asset_id=agent.get('asset_id'), requested_by_user_id=user_id,
        inputs=[{'key': 'application', 'label': 'Application', 'value': name}],
        payload_encrypted=encrypt_secret(json.dumps({'env': {'MYPORTAL_PATCH_PLAN': json.dumps(config)}})),
        timeout_seconds=7200, expires_at=patch.utcnow() + timedelta(minutes=15), run_source='patch')
    rmm_scripts.notify_agent(int(agent['id']))
    return run_id


async def is_current(agent: dict, config: dict) -> bool:
    manual = config['manual_upgrade']
    app = await upgrade_for(agent, manual['name'])
    return bool(app and app['can_upgrade'] and app['package_id'] == manual['package_id']
                and config.get('apps') == [{'package_id': app['package_id'], 'display_name': manual['name'], 'install_missing': False}]
                and config.get('windows') is None and config.get('reboot') == {'reboot_mode': 'never'})


def package_ids(values) -> list[str]:
    """Validated, de-duplicated Chocolatey package IDs, in the order given."""
    ids = list(dict.fromkeys(str(value or '').strip().lower() for value in values or []))
    if not ids or any(not patch.PACKAGE.fullmatch(value) for value in ids):
        raise RunRequestError({'package_id': 'Enter exact Chocolatey package IDs, such as googlechrome.'})
    if len(ids) > MAX_PUSH_PACKAGES:
        raise RunRequestError({'package_id': f'Choose up to {MAX_PUSH_PACKAGES} applications at a time.'})
    return ids


async def plan_apps(agent: dict, ids: list[str], mode: str) -> list[dict]:
    """The patch plan's apps for one device.

    An app the Software inventory shows is installed is named exactly as the
    device reports it, so the patch script can compare its version and skip it
    when current. ``upgrade`` skips apps that are not installed; ``install``
    installs them from the company's enabled sources.
    """
    installed = [item['name'] for item in await inventory_repo.list_software(int(agent['id']))
                 if item.get('source') != 'user']
    apps = []
    for package_id in ids:
        stored = await catalog_repo.get_package(package_id)
        known = {name.casefold() for name in (stored or {}).get('names', [])}
        on_device = sorted({name for name in installed if name.casefold() in known})
        if on_device:
            apps.append({'package_id': package_id, 'display_name': on_device[0], 'install_missing': mode == 'install'})
        elif mode == 'install':
            # A name no registry entry uses, so only Chocolatey's own records decide.
            apps.append({'package_id': package_id, 'display_name': (stored or {}).get('title') or package_id,
                         'install_missing': True})
    return apps


async def queue_apps(agent: dict, ids: list[str], mode: str, user_id: int) -> int | None:
    """Queue one patch job installing or updating ``ids``; ``None`` when nothing applies."""
    if mode not in MODES:
        raise RunRequestError({'mode': 'Choose install or update.'})
    if not _windows(agent):
        raise RunRequestError({'device': 'Chocolatey apps need an active Windows device.'})
    apps = await plan_apps(agent, ids, mode)
    if not apps:
        return None
    if await patch_repo.has_active_run(int(agent['id'])):
        raise RunRequestError({'device': 'A patch job is already waiting or running on this device.'})
    config = {'manual_apps': {'mode': mode, 'package_ids': ids}, 'apps': apps,
              'windows': None, 'reboot': {'reboot_mode': 'never'},
              'sources': await sources.for_company(int(agent['company_id']), secrets=True)}
    if not any(s['enabled'] for s in config['sources']):
        raise RunRequestError({'sources': 'Enable a Chocolatey source for this company first.'})
    content = patch.script_content()
    label = ', '.join(app['package_id'] for app in apps)
    name = f"{'Install' if mode == 'install' else 'Update'} {label}"[:255]
    script = {'id': None, 'name': name, 'path': 'builtin/patch.ps1', 'language': 'powershell',
              'content_sha256': hashlib.sha256(content.encode()).hexdigest()}
    problem = rmm_scripts.device_problem(script, agent)
    if problem:
        raise RunRequestError({'device': problem})
    run_id = await rmm_repo.create_run(script=script, content=content, agent_id=int(agent['id']),
        company_id=int(agent['company_id']), asset_id=agent.get('asset_id'), requested_by_user_id=user_id,
        inputs=[{'key': 'applications', 'label': 'Applications', 'value': label},
                {'key': 'mode', 'label': 'Action', 'value': 'Install or update' if mode == 'install' else 'Update only'}],
        payload_encrypted=encrypt_secret(json.dumps({'env': {'MYPORTAL_PATCH_PLAN': json.dumps(config)}})),
        timeout_seconds=7200, expires_at=patch.utcnow() + timedelta(minutes=15), run_source='patch')
    rmm_scripts.notify_agent(int(agent['id']))
    return run_id


def apps_current(agent: dict, config: dict) -> bool:
    """A pushed install/update job still applies at dispatch: the device is an
    active Windows agent and the plan holds only the packages that were chosen."""
    manual = config['manual_apps']
    ids = manual.get('package_ids') or []
    apps = config.get('apps') or []
    return bool(_windows(agent) and manual.get('mode') in MODES and apps
                and all(patch.PACKAGE.fullmatch(str(i)) for i in ids)
                and all(a.get('package_id') in ids and a.get('display_name')
                        and (manual['mode'] == 'install' or not a.get('install_missing')) for a in apps)
                and config.get('windows') is None and config.get('reboot') == {'reboot_mode': 'never'})
