"""Chocolatey's stable public NuGet v2 catalog.

The ``update_chocolatey_catalog`` scheduled task stores the catalog locally and
lookups use that copy. Until the first refresh completes, lookups fall back to
bounded, cached queries of the public feed.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import re
import time

import httpx
from defusedxml import ElementTree
from app.repositories import chocolatey_catalog as catalog_repo
from app.services.chocolatey_sources import COMMUNITY
from app.services.monitored_http import monitored_client

_CACHE: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()
_LIMIT = asyncio.Semaphore(4)
NS = {'a': 'http://www.w3.org/2005/Atom', 'm': 'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata',
      'd': 'http://schemas.microsoft.com/ado/2007/08/dataservices'}
STABLE = 'IsLatestVersion eq true and IsPrerelease eq false'
RESULT_LIMIT = 30
PAGE_SIZE = 100
# The community feed is rate limited; pace full downloads and bound their size.
PAGE_DELAY = 1.0
MAX_PAGES = 1000


class CatalogUnavailable(Exception):
    pass


def _parse(content: bytes) -> tuple[int, list[dict]]:
    """Return the feed's entry count and its valid stable packages."""
    if len(content) > 4 * 1024 * 1024:
        raise ValueError('Catalog response too large')
    entries = ElementTree.fromstring(content).findall('a:entry', NS)
    result = []
    for entry in entries:
        props = entry.find('m:properties', NS)
        if props is None:
            props = entry.find('a:content/m:properties', NS)
        if props is None:
            continue
        def value(name):
            return props.findtext('d:' + name, default='', namespaces=NS).strip()
        package, version = value('Id'), value('Version')
        title = value('Title') or package
        if (re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', package) and version and len(version) <= 64
                and len(title) <= 255 and value('IsPrerelease').lower() != 'true'):
            downloads = value('DownloadCount')
            result.append({'package_id': package, 'title': title, 'version': version,
                           'download_count': int(downloads) if downloads.isdigit() else 0})
    return len(entries), result


def _public(package: dict) -> dict:
    return {key: package[key] for key in ('package_id', 'title', 'version')}


async def query(term: str, *, exact: bool = False) -> list[dict]:
    term = term.strip()[:255]
    stored = await (catalog_repo.find_exact if exact else catalog_repo.search)(term, RESULT_LIMIT)
    if stored or await catalog_repo.has_packages():
        return [_public(row) for row in stored]
    key = (term.casefold(), exact)
    async with _LIMIT:
        cached = _CACHE.get(key)
        if cached and cached[0] > time.monotonic():
            _CACHE.move_to_end(key)
            return [dict(item) for item in cached[1]]
        escaped = term.replace("'", "''")
        params = {'$top': str(RESULT_LIMIT), '$filter': STABLE}
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
            result = [_public(item) for item in _parse(response.content)[1]]
        except Exception:
            raise CatalogUnavailable('Chocolatey catalog is unavailable. Please try again.') from None
        _CACHE[key] = (time.monotonic() + 3600, result)
        _CACHE.move_to_end(key)
        while len(_CACHE) > 2000:
            _CACHE.popitem(last=False)
        return [dict(item) for item in result]


async def refresh() -> dict:
    """Download every stable package and replace the stored app list.

    The stored list is only changed after the whole catalog downloads, so a
    failed or rate-limited run keeps the previous list.
    """
    packages: dict[str, dict] = {}
    skip = 0
    async with monitored_client(httpx.AsyncClient, timeout=60, follow_redirects=False) as client:
        for page in range(MAX_PAGES):
            if page:
                await asyncio.sleep(PAGE_DELAY)
            params = {'$filter': STABLE, '$orderby': 'Id', '$top': str(PAGE_SIZE), '$skip': str(skip)}
            try:
                response = await client.get(COMMUNITY + 'Packages()', params=params,
                                            headers={'Accept': 'application/atom+xml'})
                response.raise_for_status()
                count, rows = _parse(response.content)
            except httpx.HTTPStatusError as exc:
                raise CatalogUnavailable(f'Chocolatey catalog download failed with HTTP {exc.response.status_code}; '
                                         'the stored app list was kept.') from None
            except Exception as exc:
                raise CatalogUnavailable(f'Chocolatey catalog download failed ({type(exc).__name__}); '
                                         'the stored app list was kept.') from None
            if not count:
                break
            for row in rows:
                packages.setdefault(row['package_id'], row)
            # Servers may cap page sizes, so advance by what was actually returned.
            skip += count
        else:
            raise CatalogUnavailable('Chocolatey catalog exceeded the download limit; the stored app list was kept.')
    if not packages:
        raise CatalogUnavailable('Chocolatey catalog returned no packages; the stored app list was kept.')
    refreshed_at = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    summary = await catalog_repo.replace_all(list(packages.values()), refreshed_at)
    _CACHE.clear()
    return {'source': COMMUNITY, 'pages': page + 1, **summary}


async def last_updated() -> str | None:
    value = await catalog_repo.last_updated()
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()
    return str(value) if value else None


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
