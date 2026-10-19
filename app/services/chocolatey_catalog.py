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
from app.core.logging import log_warning
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
# The community feed rejects some search terms (for example names containing
# '&') with 406 InvalidQueryParameters. Such a rejection only affects that name.
REJECTED_STATUS = {400, 406}
REJECTED_NAMES_REPORTED = 20
# Save progress regularly so a long refresh survives restarts.
SAVE_EVERY = 25
# Stop (keeping progress) when the feed looks down rather than failing slowly.
MAX_CONSECUTIVE_FAILURES = 5


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
        package, version = value('Id').lower(), value('Version')
        title = value('Title') or package
        if (re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', package) and version and len(version) <= 64
                and len(title) <= 255 and value('IsPrerelease').lower() != 'true'):
            downloads = value('DownloadCount')
            result.append({'package_id': package, 'title': title, 'version': version,
                           'download_count': int(downloads) if downloads.isdigit() else 0})
    return len(entries), result


def _public(package: dict) -> dict:
    result = {key: package[key] for key in ('package_id', 'title', 'version')}
    if package.get('display_name'):
        result['display_name'] = package['display_name']
    return result


def _application_name(name: str) -> str:
    """Remove packaging decorations, preserving editions, channels and products.

    Registry names often include a release and architecture, while Chocolatey
    titles may end in '(Install)'. Do not use substrings or similarity scores:
    the remaining product name must still match in full and be unambiguous.
    """
    name = ' '.join(name.split())
    previous = None
    while name != previous:
        previous = name
        name = re.sub(
            r'\s*\((?:install|(?:x64|x86|amd64|arm64|32[ -]bit|64[ -]bit)'
            r'(?:\s+[a-z]{2}(?:-[a-z]{2})?)?)\)$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+(?:version\s+|v)?\d+(?:\.\d+)+(?:\s*\((?:x64|x86)\))?$', '', name,
                      flags=re.IGNORECASE)
    return name.strip()


def _package_matches(name: str, packages: list[dict]) -> list[dict]:
    # Prefer literal matches; normalized matches may not override an exact one.
    exact = [p for p in packages if name.casefold() in
             {p['package_id'].casefold(), p['title'].casefold()}]
    key = _application_name(name).casefold()
    candidates = exact or [p for p in packages if key and key in
                          {_application_name(p['package_id']).casefold(), _application_name(p['title']).casefold()}]
    return list({p['package_id']: p for p in candidates}.values())


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
            'the stored match was kept.')


def _lookup_order(names: list[str], checked: dict[str, datetime]) -> list[str]:
    """Never-checked names first, then those checked longest ago."""
    # ISO-style text sorts chronologically for both MySQL datetimes and SQLite text.
    return sorted(names, key=lambda name: (name.casefold() in checked,
                                            str(checked.get(name.casefold()) or '')))


async def refresh() -> dict:
    """Refresh verified Chocolatey matches for current Windows Software inventory.

    Application names are deduplicated across devices and versions. Results are
    saved every ``SAVE_EVERY`` names, starting with the names checked longest
    ago, so a run cut short by a restart or the feed's rate limit keeps its work
    and the next run carries on. Missing and ambiguous names are skipped rather
    than guessed. A name whose lookup is rejected or keeps failing keeps its
    previously stored match. The refresh stops early (keeping saved progress)
    after ``MAX_CONSECUTIVE_FAILURES`` failed lookups in a row or a permanent
    feed error; it fails when every lookup is rejected. Names no longer in the
    inventory are only forgotten after a complete pass.
    """
    names = list({name.strip().casefold(): name.strip()
                  for name in await inventory_repo.list_catalog_software_names()
                  if name.strip()}.values())
    names = _lookup_order(names, await catalog_repo.lookup_times())
    packages: dict[str, dict] = {}
    software_matches: list[dict] = []
    cleared: list[str] = []
    checked: list[str] = []
    unmatched = ambiguous = matched = stored = 0
    rejected: list[str] = []
    failed: list[str] = []
    first_rejection = ''
    consecutive_failures = 0

    async def save() -> None:
        nonlocal stored, packages, software_matches, cleared, checked
        if not (packages or software_matches or cleared or checked):
            return
        stored += await catalog_repo.save_progress(
            list(packages.values()), software_matches, cleared=cleared, checked=checked,
            checked_at=datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0))
        packages, software_matches, cleared, checked = {}, [], [], []

    def summary(**extra) -> dict:
        return {'source': COMMUNITY, 'applications': len(names), 'matched': matched,
                'unmatched': unmatched, 'ambiguous': ambiguous, 'rejected': len(rejected),
                'rejected_names': rejected[:REJECTED_NAMES_REPORTED], 'failed': len(failed),
                'failed_names': failed[:REJECTED_NAMES_REPORTED], 'stored': stored, **extra}

    async def stop(reason: str) -> CatalogUnavailable:
        await save()
        done = matched + unmatched + ambiguous + len(rejected)
        log_warning('Chocolatey app list refresh stopped early; progress was saved',
                    reason=reason, **summary())
        return CatalogUnavailable(f'{reason} Saved {done} of {len(names)} application lookups; '
                                  'the next run continues with the rest.')

    async with monitored_client(httpx.AsyncClient, timeout=60, follow_redirects=False) as client:
        for index, name in enumerate(names):
            if index:
                await asyncio.sleep(PAGE_DELAY)
                if index % SAVE_EVERY == 0:
                    await save()
            escaped = _application_name(name).replace("'", "''")
            params = {'$filter': 'IsLatestVersion', '$orderby': 'Id',
                      'searchTerm': f"'{escaped}'", 'targetFramework': "''",
                      'includePrerelease': 'false', '$skip': '0', '$top': str(RESULT_LIMIT),
                      'semVerLevel': '2.0.0'}
            try:
                _, rows = _parse(await _request_packages(client, params))
            except httpx.HTTPStatusError as exc:
                error = _lookup_error(exc.response, name)
                status = exc.response.status_code
                if status in REJECTED_STATUS:
                    # Only this name's search is unusable; keep its stored match.
                    log_warning('Chocolatey rejected a software lookup; keeping its stored match',
                                application=name, status=status)
                    first_rejection = first_rejection or error
                    rejected.append(name)
                    checked.append(name)
                    consecutive_failures = 0
                    continue
                if status not in RETRYABLE_STATUS:
                    raise await stop(error) from None
                failed.append(name)
                consecutive_failures += 1
                log_warning('Chocolatey software lookup kept failing; keeping its stored match',
                            application=name, status=status)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise await stop(error) from None
                continue
            except Exception as exc:
                failed.append(name)
                consecutive_failures += 1
                log_warning('Chocolatey software lookup failed; keeping its stored match',
                            application=name, error=type(exc).__name__)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise await stop(f'Chocolatey software lookup failed ({type(exc).__name__}).') from None
                continue
            consecutive_failures = 0
            checked.append(name)
            matches = _package_matches(name, rows)
            if len(matches) == 1:
                packages.setdefault(matches[0]['package_id'], matches[0])
                software_matches.append({'name': name, 'package_id': matches[0]['package_id']})
                matched += 1
            else:
                cleared.append(name)
                if matches:
                    ambiguous += 1
                else:
                    unmatched += 1
    if names and len(rejected) == len(names):
        await save()
        raise CatalogUnavailable(first_rejection)
    await save()
    removed = await catalog_repo.prune(names)
    return summary(removed=removed)


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
    # Only saved aliases or literal titles/IDs may authorize an upgrade. Runtime
    # lookups must not invent matches from a different application's search hits.
    matches = [p for p in await query(name, exact=True)
               if name.casefold() in {p['title'].casefold(), p['package_id'].casefold(),
                                      str(p.get('display_name') or '').casefold()}]
    return matches[0] if len(matches) == 1 else None
