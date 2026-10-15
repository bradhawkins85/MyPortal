"""Inventory-verified one-off application upgrades through patch run history."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from app.repositories import rmm as rmm_repo, rmm_inventory as inventory_repo, rmm_patching as patch_repo
from app.security.encryption import encrypt_secret
from app.services import chocolatey_catalog as catalog, chocolatey_sources as sources, rmm_patching as patch, rmm_scripts
from app.services.rmm_scripts import RunRequestError


async def upgrade_for(agent: dict, name: str) -> dict | None:
    if agent.get('status') != 'active' or str(agent.get('os') or '').lower() != 'windows':
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
