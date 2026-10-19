"""Persistence for the locally stored Chocolatey community catalog."""
from __future__ import annotations

from datetime import datetime

from app.core.database import db

_UPSERT_MYSQL = (
    'INSERT INTO rmm_chocolatey_packages (package_id, title, version, download_count, updated_at) '
    'VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE title = VALUES(title), version = VALUES(version), '
    'download_count = VALUES(download_count), updated_at = VALUES(updated_at)'
)
_UPSERT_SQLITE = (
    'INSERT INTO rmm_chocolatey_packages (package_id, title, version, download_count, updated_at) '
    'VALUES (%s, %s, %s, %s, %s) ON CONFLICT(package_id) DO UPDATE SET title = excluded.title, '
    'version = excluded.version, download_count = excluded.download_count, updated_at = excluded.updated_at'
)
_MATCH_UPSERT_MYSQL = (
    'INSERT INTO rmm_chocolatey_software_matches (name, package_id) VALUES (%s, %s) '
    'ON DUPLICATE KEY UPDATE name = VALUES(name), package_id = VALUES(package_id)'
)
_MATCH_UPSERT_SQLITE = (
    'INSERT INTO rmm_chocolatey_software_matches (name, package_id) VALUES (%s, %s) '
    'ON CONFLICT(name) DO UPDATE SET package_id = excluded.package_id'
)


def _like(term: str) -> str:
    return '%' + term.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'


async def has_packages() -> bool:
    return bool(await db.fetch_one('SELECT 1 AS present FROM rmm_chocolatey_packages LIMIT 1'))


async def last_updated() -> datetime | None:
    """Return when the stored catalog was last refreshed, or ``None`` when empty."""
    row = await db.fetch_one('SELECT MAX(updated_at) AS updated_at FROM rmm_chocolatey_packages')
    return row['updated_at'] if row and row['updated_at'] else None


async def search(term: str, limit: int) -> list[dict]:
    select = (
        'SELECT p.package_id, p.title, p.version, p.download_count, m.name AS display_name '
        'FROM rmm_chocolatey_packages p '
        'LEFT JOIN rmm_chocolatey_software_matches m ON m.package_id = p.package_id '
    )
    if not term:
        return await db.fetch_all(
            select + 'ORDER BY p.download_count DESC, p.title, m.name LIMIT %s', (limit,))
    pattern = _like(term)
    return await db.fetch_all(
        select + "WHERE p.package_id LIKE %s ESCAPE '!' OR p.title LIKE %s ESCAPE '!' "
        "OR m.name LIKE %s ESCAPE '!' "
        'ORDER BY p.download_count DESC, p.title, m.name LIMIT %s', (pattern, pattern, pattern, limit))


async def find_exact(name: str, limit: int) -> list[dict]:
    key = name.lower()
    return await db.fetch_all(
        'SELECT p.package_id, p.title, p.version, p.download_count, m.name AS display_name '
        'FROM rmm_chocolatey_packages p '
        'LEFT JOIN rmm_chocolatey_software_matches m ON m.package_id = p.package_id AND LOWER(m.name) = %s '
        'WHERE LOWER(p.package_id) = %s OR LOWER(p.title) = %s OR m.name IS NOT NULL '
        'ORDER BY p.package_id LIMIT %s', (key, key, key, limit))


_LOOKUP_UPSERT_MYSQL = (
    'INSERT INTO rmm_chocolatey_lookups (name, checked_at) VALUES (%s, %s) '
    'ON DUPLICATE KEY UPDATE checked_at = VALUES(checked_at)'
)
_LOOKUP_UPSERT_SQLITE = (
    'INSERT INTO rmm_chocolatey_lookups (name, checked_at) VALUES (%s, %s) '
    'ON CONFLICT(name) DO UPDATE SET checked_at = excluded.checked_at'
)


async def _execute_batches(sql: str, rows: list[tuple]) -> None:
    for offset in range(0, len(rows), 500):
        await db.execute_many(sql, rows[offset:offset + 500])


async def lookup_times() -> dict[str, datetime]:
    """When each Software name was last looked up, keyed by its casefolded name."""
    rows = await db.fetch_all('SELECT name, checked_at FROM rmm_chocolatey_lookups')
    return {str(row['name']).casefold(): row['checked_at'] for row in rows or []}


async def save_progress(packages: list[dict], matches: list[dict], *, cleared: list[str],
                        checked: list[str], checked_at: datetime) -> int:
    """Store one batch of lookups so an interrupted refresh keeps its results.

    ``matches`` link names to ``packages``; ``cleared`` names no longer have a
    unique package; ``checked`` names are recorded as looked up at ``checked_at``.
    """
    upsert = _UPSERT_SQLITE if db.is_sqlite() else _UPSERT_MYSQL
    await _execute_batches(upsert, [
        (p['package_id'], p['title'], p['version'], int(p.get('download_count') or 0), checked_at)
        for p in packages])
    match_upsert = _MATCH_UPSERT_SQLITE if db.is_sqlite() else _MATCH_UPSERT_MYSQL
    await _execute_batches(match_upsert, [(item['name'], item['package_id']) for item in matches])
    await _execute_batches('DELETE FROM rmm_chocolatey_software_matches WHERE name = %s',
                           [(name,) for name in cleared])
    lookup_upsert = _LOOKUP_UPSERT_SQLITE if db.is_sqlite() else _LOOKUP_UPSERT_MYSQL
    await _execute_batches(lookup_upsert, [(name, checked_at) for name in checked])
    return len(packages)


async def prune(names: list[str]) -> int:
    """After a complete pass, forget names no longer in the Software inventory
    and remove packages no remaining name matches. Returns packages removed."""
    current = {name.casefold() for name in names}
    for select, delete in (
        ('SELECT name FROM rmm_chocolatey_software_matches',
         'DELETE FROM rmm_chocolatey_software_matches WHERE name = %s'),
        ('SELECT name FROM rmm_chocolatey_lookups', 'DELETE FROM rmm_chocolatey_lookups WHERE name = %s'),
    ):
        stale = [(row['name'],) for row in await db.fetch_all(select)
                 if str(row['name']).casefold() not in current]
        await _execute_batches(delete, stale)
    stale_packages = [(row['package_id'],) for row in await db.fetch_all(
        'SELECT p.package_id FROM rmm_chocolatey_packages p '
        'LEFT JOIN rmm_chocolatey_software_matches m ON m.package_id = p.package_id '
        'WHERE m.package_id IS NULL')]
    await _execute_batches('DELETE FROM rmm_chocolatey_packages WHERE package_id = %s', stale_packages)
    return len(stale_packages)


async def get_package(package_id: str) -> dict | None:
    """A stored package with every Software name matched to it."""
    rows = await db.fetch_all(
        'SELECT p.package_id, p.title, p.version, m.name AS display_name FROM rmm_chocolatey_packages p '
        'LEFT JOIN rmm_chocolatey_software_matches m ON m.package_id = p.package_id '
        'WHERE p.package_id = %s ORDER BY m.name', (package_id.lower(),))
    if not rows:
        return None
    first = rows[0]
    return {'package_id': first['package_id'], 'title': first['title'], 'version': first['version'],
            'names': [row['display_name'] for row in rows if row['display_name']]}
