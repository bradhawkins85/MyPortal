"""Chocolatey package matches for applications in the Software inventory.

Only the scheduled refresh contacts Chocolatey, using targeted searches for
installed application names. UI lookups always use the stored matches.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
from urllib.parse import quote, urlencode

import httpx
from defusedxml import ElementTree
from app.repositories import chocolatey_catalog as catalog_repo, rmm_inventory as inventory_repo
from app.services.chocolatey_sources import COMMUNITY
from app.services.monitored_http import monitored_client

NS = {'a': 'http://www.w3.org/2005/Atom', 'm': 'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata',
      'd': 'http://schemas.microsoft.com/ado/2007/08/dataservices'}
RESULT_LIMIT = 30
# The community feed is rate limited; pace targeted lookups.
PAGE_DELAY = 1.0
PAGE_ATTEMPTS = 4
RETRY_DELAY = 2.0
MAX_RETRY_DELAY = 60.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class CatalogUnavailable(Exception):
    pass


def _parse(content: bytes) -> tuple[int, list[dict]]:
    """Return the feed's entry count and its valid stable packages."""
    if len(content) > 4 * 1024 * 1024:
        raise ValueError('Catalog response too large')
    root = ElementTree.fromstring(content)
    if root.tag != '{' + NS['a'] + '}feed':
        raise ValueError('Expected an Atom package feed')
    entries = root.findall('a:entry', NS)
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
    """Search only the inventory-derived package list, never the public catalog."""
    term = term.strip()[:255]
    stored = await (catalog_repo.find_exact if exact else catalog_repo.search)(term, RESULT_LIMIT)
    return [_public(row) for row in stored]


async def _request_packages(client: httpx.AsyncClient, params: dict) -> bytes:
    """Retry a targeted search without changing its application name."""
    # Match NuGet's V2FeedQueryBuilder/UrlEncodeOdataParameter: literal OData
    # keys and quotes, percent-encoded values, and %20 rather than form '+' spaces.
    # Do not pass params= here: HTTPX would re-encode this OData query as a form.
    url = COMMUNITY + 'Search()?' + urlencode(params, quote_via=quote, safe="$'")
    for attempt in range(PAGE_ATTEMPTS):
        delay = min(RETRY_DELAY * 2 ** attempt, MAX_RETRY_DELAY)
        try:
            response = await client.get(url,
                                        headers={'Accept': 'application/atom+xml'})
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in RETRYABLE_STATUS or attempt == PAGE_ATTEMPTS - 1:
                raise
            # Respect numeric Retry-After values without allowing an unbounded wait.
            retry_after = exc.response.headers.get('Retry-After', '')
            if retry_after.isdigit():
                delay = min(max(delay, float(retry_after)), MAX_RETRY_DELAY)
        except httpx.TransportError:
            if attempt == PAGE_ATTEMPTS - 1:
                raise
        await asyncio.sleep(delay)
    raise AssertionError('Catalog page attempts exhausted')


def _lookup_error(response: httpx.Response, name: str) -> str:
    """Include a bounded upstream OData message, without echoing arbitrary HTML."""
    reason = ''
    if len(response.content) <= 16_384:
        try:
            root = ElementTree.fromstring(response.content)
            if root.tag == '{' + NS['m'] + '}error':
                message = root.findtext('m:message', default='', namespaces=NS)
                reason = ' '.join(message.split())[:300]
        except Exception:
            pass
    detail = f': {reason}' if reason else ''
    return (f'Chocolatey software lookup failed with HTTP {response.status_code} for {name!r}{detail}; '
            'the stored app list was kept.')


async def refresh() -> dict:
    """Refresh exact Chocolatey matches for current Windows Software inventory.

    Application names are deduplicated across devices and versions. All lookups
    must succeed before replacing the stored matches; failures keep the old list.
    Missing and ambiguous names are skipped rather than guessed.
    """
    names = list({name.strip().casefold(): name.strip()
                  for name in await inventory_repo.list_catalog_software_names()
                  if name.strip()}.values())
    packages: dict[str, dict] = {}
    unmatched = ambiguous = matched = 0
    async with monitored_client(httpx.AsyncClient, timeout=60, follow_redirects=False) as client:
        for index, name in enumerate(names):
            if index:
                await asyncio.sleep(PAGE_DELAY)
            escaped = name.replace("'", "''")
            params = {'$filter': 'IsLatestVersion', '$orderby': 'Id',
                      'searchTerm': f"'{escaped}'", 'targetFramework': "''",
                      'includePrerelease': 'false', '$skip': '0', '$top': str(RESULT_LIMIT),
                      'semVerLevel': '2.0.0'}
            try:
                _, rows = _parse(await _request_packages(client, params))
            except httpx.HTTPStatusError as exc:
                raise CatalogUnavailable(_lookup_error(exc.response, name)) from None
            except Exception as exc:
                raise CatalogUnavailable(f'Chocolatey software lookup failed ({type(exc).__name__}); '
                                         'the stored app list was kept.') from None
            matches = [row for row in rows if name.casefold() in
                       {row['package_id'].casefold(), row['title'].casefold()}]
            if len(matches) == 1:
                packages.setdefault(matches[0]['package_id'], matches[0])
                matched += 1
            elif matches:
                ambiguous += 1
            else:
                unmatched += 1
    refreshed_at = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    summary = await catalog_repo.replace_all(list(packages.values()), refreshed_at)
    return {'source': COMMUNITY, 'applications': len(names), 'matched': matched,
            'unmatched': unmatched, 'ambiguous': ambiguous, **summary}


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
