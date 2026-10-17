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


async def replace_all(packages: list[dict], refreshed_at: datetime, *, matches: list[dict] | None = None) -> dict[str, int]:
    """Upsert a complete download, then remove packages no longer listed.

    Rows are never cleared first, so lookups keep working during a refresh.
    """
    upsert = _UPSERT_SQLITE if db.is_sqlite() else _UPSERT_MYSQL
    rows = [(p['package_id'], p['title'], p['version'], int(p.get('download_count') or 0), refreshed_at)
            for p in packages]
    for offset in range(0, len(rows), 500):
        await db.execute_many(upsert, rows[offset:offset + 500])
    # Upsert aliases before pruning old ones so lookups keep working throughout.
    match_upsert = _MATCH_UPSERT_SQLITE if db.is_sqlite() else _MATCH_UPSERT_MYSQL
    match_rows = [(item['name'], item['package_id']) for item in matches or []]
    for offset in range(0, len(match_rows), 500):
        await db.execute_many(match_upsert, match_rows[offset:offset + 500])
    names = {name for name, _ in match_rows}
    stale_matches = [(row['name'],) for row in await db.fetch_all('SELECT name FROM rmm_chocolatey_software_matches')
                     if row['name'] not in names]
    for offset in range(0, len(stale_matches), 500):
        await db.execute_many('DELETE FROM rmm_chocolatey_software_matches WHERE name = %s',
                              stale_matches[offset:offset + 500])
    current = {p['package_id'] for p in packages}
    stale = [(row['package_id'],) for row in await db.fetch_all('SELECT package_id FROM rmm_chocolatey_packages')
             if row['package_id'] not in current]
    for offset in range(0, len(stale), 500):
        await db.execute_many('DELETE FROM rmm_chocolatey_packages WHERE package_id = %s', stale[offset:offset + 500])
    return {'stored': len(rows), 'removed': len(stale)}
