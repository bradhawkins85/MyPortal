"""Environment-defined Chocolatey sources with company-specific enablement.

Credentials are returned only to authenticated agents or encrypted job payloads.
Never include raw source definitions in errors, templates, or audit metadata.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from app.core.database import db

COMMUNITY = 'https://community.chocolatey.org/api/v2/'
ENV_FILE = Path(__file__).resolve().parents[2] / '.env'
KEY = re.compile(r'^CHOCO_SOURCE_(DEFAULT|CUSTOM(?:[1-9][0-9]*)?)$')
NAME = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$')


def definitions() -> list[dict]:
    values = {**dotenv_values(ENV_FILE, interpolate=False), **os.environ}
    values.setdefault('CHOCO_SOURCE_DEFAULT', f'-n=chocolatey -s="{COMMUNITY}"')
    sources, names = [], set()
    keys = sorted((key for key in values if KEY.fullmatch(key)),
                  key=lambda k: (k != 'CHOCO_SOURCE_DEFAULT', int(k.removeprefix('CHOCO_SOURCE_CUSTOM') or '0')
                                 if k.startswith('CHOCO_SOURCE_CUSTOM') else 0))
    for key in keys:
        if not values[key]:
            continue
        try:
            tokens = shlex.split(str(values[key]))
            args = {}
            i = 0
            while i < len(tokens):
                option, sep, value = tokens[i].partition('=')
                if option not in {'-n', '-s', '-u', '-p'} or option in args:
                    raise ValueError()
                if not sep:
                    i += 1
                    value = tokens[i]
                args[option] = value
                i += 1
            url = urlsplit(args.get('-s', ''))
            name = args.get('-n', '')
            if (not NAME.fullmatch(name) or name.casefold() in names or url.scheme not in {'http', 'https'}
                    or not url.hostname or url.username or url.password or url.query or url.fragment
                    or any('\x00' in value or '\n' in value or '\r' in value for value in args.values())
                    or bool(args.get('-u')) != bool(args.get('-p'))):
                raise ValueError()
        except (ValueError, IndexError):
            raise ValueError(f'Invalid Chocolatey source definition in {key}.') from None
        names.add(name.casefold())
        sources.append({'key': key.removeprefix('CHOCO_SOURCE_'), 'name': name, 'url': args['-s'],
                        'username': args.get('-u', ''), 'password': args.get('-p', '')})
    # Chocolatey treats lower positive priorities as preferred (zero is last).
    # Keep display ordering stable while assigning execution order explicitly.
    def order(source):
        key = source['key']
        if key == 'DEFAULT':
            return (2, 0)
        if key == 'CUSTOM':
            return (1, 0)
        return (0, int(key.removeprefix('CUSTOM')))
    for priority, source in enumerate(sorted(sources, key=order), start=1):
        source['priority'] = priority
    return sources


async def for_company(company_id: int, *, secrets: bool = False) -> list[dict]:
    sources = definitions()
    row = await db.fetch_one('SELECT enabled_json FROM rmm_chocolatey_sources WHERE company_id = %s', (company_id,))
    try:
        enabled = json.loads(row['enabled_json']) if row else {}
        if not isinstance(enabled, dict) or any(type(value) is not bool for value in enabled.values()):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Company Chocolatey source settings are invalid.') from None
    result = []
    for source in sources:
        # The default source is always enabled as the lowest-priority fallback;
        # only custom sources are opt-in per company.
        item = {**source, 'enabled': source['key'] == 'DEFAULT' or enabled.get(source['key']) is True}
        if not secrets:
            item = {k: item[k] for k in ('key', 'name', 'enabled')}
        result.append(item)
    return result


async def save(company_id: int, keys: list[str]) -> None:
    sources = definitions()
    if set(keys) - {source['key'] for source in sources}:
        raise ValueError('A selected Chocolatey source is no longer configured. Reload this page.')
    values = json.dumps({source['key']: source['key'] == 'DEFAULT' or source['key'] in keys for source in sources})
    if db.is_sqlite():
        suffix = 'ON CONFLICT(company_id) DO UPDATE SET enabled_json = excluded.enabled_json'
    else:
        suffix = 'ON DUPLICATE KEY UPDATE enabled_json = VALUES(enabled_json)'
    await db.execute('INSERT INTO rmm_chocolatey_sources (company_id, enabled_json) VALUES (%s, %s) ' + suffix,
                     (company_id, values))


def script() -> str:
    return (Path(__file__).parent / 'rmm_builtin/chocolatey_sources.ps1').read_text(encoding='utf-8')
