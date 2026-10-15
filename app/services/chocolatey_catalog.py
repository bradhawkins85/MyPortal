"""Bounded, cached queries of Chocolatey's stable public NuGet v2 catalog."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import re
import time

import httpx
from defusedxml import ElementTree
from app.services.chocolatey_sources import COMMUNITY
from app.services.monitored_http import monitored_client

_CACHE: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()
_LIMIT = asyncio.Semaphore(4)
NS = {'a': 'http://www.w3.org/2005/Atom', 'm': 'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata',
      'd': 'http://schemas.microsoft.com/ado/2007/08/dataservices'}


class CatalogUnavailable(Exception):
    pass


async def query(term: str, *, exact: bool = False) -> list[dict]:
    term = term.strip()[:255]
    key = (term.casefold(), exact)
    async with _LIMIT:
        cached = _CACHE.get(key)
        if cached and cached[0] > time.monotonic():
            _CACHE.move_to_end(key)
            return [dict(item) for item in cached[1]]
        escaped = term.replace("'", "''")
        params = {'$top': '30', '$filter': 'IsLatestVersion eq true and IsPrerelease eq false'}
        if exact:
            params['$filter'] += f" and (Id eq '{escaped}' or Title eq '{escaped}')"
            endpoint = 'Packages'
        else:
            endpoint = 'Search()'
            params.update(searchTerm=f"'{escaped}'", targetFramework="''", includePrerelease='false', **{'$skip': '0'})
        try:
            async with monitored_client(httpx.AsyncClient, timeout=15, follow_redirects=False) as client:
                response = await client.get(COMMUNITY + endpoint, params=params, headers={'Accept': 'application/atom+xml'})
                response.raise_for_status()
            if len(response.content) > 4 * 1024 * 1024:
                raise ValueError('Catalog response too large')
            root = ElementTree.fromstring(response.content)
            result = []
            for entry in root.findall('a:entry', NS):
                props = entry.find('m:properties', NS)
                if props is None:
                    props = entry.find('a:content/m:properties', NS)
                if props is None:
                    continue
                def value(name):
                    return props.findtext('d:' + name, default='', namespaces=NS).strip()
                package, version = value('Id'), value('Version')
                if re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', package) and version and value('IsPrerelease').lower() != 'true':
                    result.append({'package_id': package, 'title': value('Title') or package, 'version': version})
        except Exception:
            raise CatalogUnavailable('Chocolatey catalog is unavailable. Please try again.') from None
        _CACHE[key] = (time.monotonic() + 3600, result)
        _CACHE.move_to_end(key)
        while len(_CACHE) > 2000:
            _CACHE.popitem(last=False)
        return [dict(item) for item in result]


def version_key(value: str) -> tuple[int, ...] | None:
    value = value.strip().lower().removeprefix('v')
    if not re.fullmatch(r'\d+(\.\d+)*', value):
        return None
    parts = [int(part) for part in value.split('.')]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


async def match(name: str) -> dict | None:
    # Never guess from fuzzy search: a wrong match could install a different app.
    matches = [p for p in await query(name, exact=True)
               if name.casefold() in {p['title'].casefold(), p['package_id'].casefold()}]
    return matches[0] if len(matches) == 1 else None
