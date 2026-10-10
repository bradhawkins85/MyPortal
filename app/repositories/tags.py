"""Tags on assets and companies.

``tags`` is one shared list. ``asset_tags`` rows are either ``manual`` (picked
by a technician) or ``auto`` (added by a rule in :mod:`app.services.tags`);
rules only ever add and remove their own rows, so a tag a technician picked
stays when the rule stops matching. ``company_tags`` rows are always manual.
``asset_tag_blocks`` keeps a tag off one asset: rules skip it, and the tag on
the asset's company does not count for that asset.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.core.database import db
from app.services import tags as tag_rules

SOURCES = ("auto", "manual")
_ASSET_RULE_COLUMNS = "id, company_id, type, asset_type, form_factor, os_name, machine_type"


def _ids(values: Iterable[Any]) -> list[int]:
    result: list[int] = []
    for value in values or ():
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return result


def _placeholders(values: list[Any]) -> str:
    return ", ".join(["%s"] * len(values))


def _insert_ignore() -> str:
    return "INSERT OR IGNORE" if db.is_sqlite() else "INSERT IGNORE"


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    tag = {
        "id": int(row["id"]),
        "name": row["name"],
        "colour": row.get("colour") or None,
        "description": row.get("description") or "",
        "auto_key": row.get("auto_key") or None,
        "is_auto": bool(row.get("auto_key")),
    }
    for key in ("source", "asset_count", "company_count"):
        if key in row:
            tag[key] = int(row[key] or 0) if key.endswith("_count") else row[key]
    return tag


# ---------------------------------------------------------------------------
# The tag list
# ---------------------------------------------------------------------------


async def list_tags(*, search: str | None = None, limit: int | None = None, with_counts: bool = False) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if search and search.strip():
        where = "WHERE t.slug LIKE %s"
        params.append("%" + tag_rules.slugify(search).replace("%", "") + "%")
    counts = ""
    if with_counts:
        counts = """,
            (SELECT COUNT(*) FROM asset_tags at WHERE at.tag_id = t.id) AS asset_count,
            (SELECT COUNT(*) FROM company_tags ct WHERE ct.tag_id = t.id) AS company_count"""
    # Only fixed fragments and %s placeholders are interpolated; values are bound.
    sql = f"SELECT t.id, t.name, t.colour, t.description, t.auto_key{counts} FROM tags t {where} ORDER BY t.slug"  # nosec B608
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    rows = await db.fetch_all(sql, tuple(params))
    return [_public(row) for row in rows or []]


async def get_tag(tag_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, name, colour, description, auto_key FROM tags WHERE id = %s", (int(tag_id),)
    )
    return _public(row) if row else None


async def get_tag_by_name(name: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, name, colour, description, auto_key FROM tags WHERE slug = %s", (tag_rules.slugify(name),)
    )
    return _public(row) if row else None


async def existing_tag_ids(tag_ids: Iterable[Any]) -> list[int]:
    ids = _ids(tag_ids)
    if not ids:
        return []
    rows = await db.fetch_all(f"SELECT id FROM tags WHERE id IN ({_placeholders(ids)})", tuple(ids))  # nosec B608
    found = {int(row["id"]) for row in rows or []}
    return [tag_id for tag_id in ids if tag_id in found]


async def get_or_create_tag(name: str, *, created_by: int | None = None, colour: str | None = None) -> tuple[dict[str, Any], bool]:
    """Return ``(tag, created)`` for a name, matching existing tags case-insensitively."""
    clean = tag_rules.clean_name(name)
    existing = await get_tag_by_name(clean)
    if existing:
        return existing, False
    try:
        tag_id = await db.execute_returning_lastrowid(
            "INSERT INTO tags (name, slug, colour, created_by_user_id) VALUES (%s, %s, %s, %s)",
            (clean, tag_rules.slugify(clean), tag_rules.clean_colour(colour), created_by),
        )
    except Exception:
        # Another request created the same tag first.
        existing = await get_tag_by_name(clean)
        if existing:
            return existing, False
        raise
    tag = await get_tag(int(tag_id))
    if tag is None:  # pragma: no cover - defensive
        raise RuntimeError("Tag was not saved")
    return tag, True


async def update_tag(tag_id: int, *, name: str, colour: str | None, description: str | None) -> dict[str, Any] | None:
    clean = tag_rules.clean_name(name)
    other = await get_tag_by_name(clean)
    if other and other["id"] != int(tag_id):
        raise ValueError(f"A tag named {other['name']} already exists")
    await db.execute(
        "UPDATE tags SET name = %s, slug = %s, colour = %s, description = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
        (clean, tag_rules.slugify(clean), tag_rules.clean_colour(colour),
         (str(description or "").strip()[:255] or None), int(tag_id)),
    )
    return await get_tag(tag_id)


async def delete_tag(tag_id: int) -> bool:
    """Delete a hand-made tag and its assignments. Rule tags cannot be deleted."""
    tag = await get_tag(tag_id)
    if not tag:
        return False
    if tag["is_auto"]:
        raise ValueError("Automatic tags can be renamed but not deleted")
    await db.execute("DELETE FROM asset_tags WHERE tag_id = %s", (int(tag_id),))
    await db.execute("DELETE FROM asset_tag_blocks WHERE tag_id = %s", (int(tag_id),))
    await db.execute("DELETE FROM company_tags WHERE tag_id = %s", (int(tag_id),))
    await db.execute("DELETE FROM tags WHERE id = %s", (int(tag_id),))
    return True


async def ensure_auto_tags() -> dict[str, int]:
    """Create any missing rule tags and return ``{auto_key: tag_id}``.

    A hand-made tag with the same name as a rule tag is adopted by the rule
    rather than duplicated.
    """
    rows = await db.fetch_all("SELECT id, auto_key FROM tags WHERE auto_key IS NOT NULL")
    mapping = {str(row["auto_key"]): int(row["id"]) for row in rows or []}
    for auto in tag_rules.AUTO_TAGS:
        if auto.key in mapping:
            continue
        slug = tag_rules.slugify(auto.name)
        existing = await db.fetch_one("SELECT id, auto_key FROM tags WHERE slug = %s", (slug,))
        if existing and not existing.get("auto_key"):
            await db.execute("UPDATE tags SET auto_key = %s WHERE id = %s", (auto.key, existing["id"]))
            mapping[auto.key] = int(existing["id"])
            continue
        if existing:
            # The name belongs to another rule's (renamed) tag; use a distinct one.
            slug = tag_rules.slugify(f"{auto.name} ({auto.key})")
        try:
            mapping[auto.key] = int(await db.execute_returning_lastrowid(
                "INSERT INTO tags (name, slug, colour, description, auto_key) VALUES (%s, %s, %s, %s, %s)",
                (auto.name if not existing else f"{auto.name} ({auto.key})", slug, auto.colour, auto.description, auto.key),
            ))
        except Exception:
            # Created concurrently by another worker.
            row = await db.fetch_one("SELECT id FROM tags WHERE auto_key = %s", (auto.key,))
            if not row:
                raise
            mapping[auto.key] = int(row["id"])
    return mapping


# ---------------------------------------------------------------------------
# Asset tags
# ---------------------------------------------------------------------------


async def list_asset_tags(asset_id: int) -> list[dict[str, Any]]:
    return (await list_tags_for_assets([asset_id])).get(int(asset_id), [])


async def list_tags_for_assets(asset_ids: Iterable[Any]) -> dict[int, list[dict[str, Any]]]:
    ids = _ids(asset_ids)
    if not ids:
        return {}
    marks = _placeholders(ids)
    sql = (
        "SELECT at.asset_id, at.source, t.id, t.name, t.colour, t.description, t.auto_key"
        " FROM asset_tags at INNER JOIN tags t ON t.id = at.tag_id"
        f" WHERE at.asset_id IN ({marks}) ORDER BY t.slug"  # nosec B608 - placeholders only
    )
    rows = await db.fetch_all(sql, tuple(ids))
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows or []:
        result.setdefault(int(row["asset_id"]), []).append(_public(row))
    return result


async def set_asset_manual_tags(asset_id: int, tag_ids: Iterable[Any]) -> None:
    """Make ``tag_ids`` the asset's hand-picked tags. Automatic tags are untouched.

    Picking a blocked tag by hand lifts its block.
    """
    wanted = await existing_tag_ids(tag_ids)
    if wanted:
        await db.execute(
            f"DELETE FROM asset_tag_blocks WHERE asset_id = %s AND tag_id IN ({_placeholders(wanted)})",  # nosec B608
            (int(asset_id), *wanted),
        )
    current = await db.fetch_all("SELECT tag_id, source FROM asset_tags WHERE asset_id = %s", (int(asset_id),))
    current_sources = {int(row["tag_id"]): str(row["source"]) for row in current or []}
    for tag_id, source in current_sources.items():
        if source == "manual" and tag_id not in wanted:
            await db.execute(
                "DELETE FROM asset_tags WHERE asset_id = %s AND tag_id = %s AND source = 'manual'",
                (int(asset_id), tag_id),
            )
    for tag_id in wanted:
        if tag_id not in current_sources:
            await db.execute(
                f"{_insert_ignore()} INTO asset_tags (asset_id, tag_id, source) VALUES (%s, %s, 'manual')",
                (int(asset_id), tag_id),
            )


async def _apply_auto_tags(asset: Mapping[str, Any], mapping: Mapping[str, int],
                           current: Mapping[int, str], blocked: Iterable[int] = ()) -> None:
    asset_id = int(asset["id"])
    wanted = {mapping[key] for key in tag_rules.auto_tag_keys(asset) if key in mapping} - set(blocked)
    rule_tag_ids = set(mapping.values())
    for tag_id, source in current.items():
        if source == "auto" and tag_id in rule_tag_ids and tag_id not in wanted:
            await db.execute(
                "DELETE FROM asset_tags WHERE asset_id = %s AND tag_id = %s AND source = 'auto'",
                (asset_id, tag_id),
            )
    for tag_id in wanted:
        if tag_id not in current:
            await db.execute(
                f"{_insert_ignore()} INTO asset_tags (asset_id, tag_id, source) VALUES (%s, %s, 'auto')",
                (asset_id, tag_id),
            )


async def refresh_asset_auto_tags(asset_id: int) -> None:
    """Re-apply the automatic tag rules to one asset."""
    asset = await db.fetch_one(f"SELECT {_ASSET_RULE_COLUMNS} FROM assets WHERE id = %s", (int(asset_id),))  # nosec B608
    if not asset:
        return
    mapping = await ensure_auto_tags()
    rows = await db.fetch_all("SELECT tag_id, source FROM asset_tags WHERE asset_id = %s", (int(asset_id),))
    blocks = await db.fetch_all("SELECT tag_id FROM asset_tag_blocks WHERE asset_id = %s", (int(asset_id),))
    await _apply_auto_tags(
        asset, mapping, {int(row["tag_id"]): str(row["source"]) for row in rows or []},
        {int(row["tag_id"]) for row in blocks or []},
    )


async def refresh_all_auto_tags(*, company_id: int | None = None) -> int:
    """Re-apply the automatic tag rules to every asset (or one company's). Returns the asset count."""
    mapping = await ensure_auto_tags()
    where, params = ("WHERE company_id = %s", (int(company_id),)) if company_id is not None else ("", ())
    assets = await db.fetch_all(f"SELECT {_ASSET_RULE_COLUMNS} FROM assets {where}", params)  # nosec B608
    join_where = "WHERE a.company_id = %s" if company_id is not None else ""
    rows = await db.fetch_all(
        f"SELECT at.asset_id, at.tag_id, at.source FROM asset_tags at INNER JOIN assets a ON a.id = at.asset_id {join_where}",  # nosec B608
        params,
    )
    current: dict[int, dict[int, str]] = {}
    for row in rows or []:
        current.setdefault(int(row["asset_id"]), {})[int(row["tag_id"])] = str(row["source"])
    block_rows = await db.fetch_all(
        f"SELECT b.asset_id, b.tag_id FROM asset_tag_blocks b INNER JOIN assets a ON a.id = b.asset_id {join_where}",  # nosec B608
        params,
    )
    blocked: dict[int, set[int]] = {}
    for row in block_rows or []:
        blocked.setdefault(int(row["asset_id"]), set()).add(int(row["tag_id"]))
    for asset in assets or []:
        asset_id = int(asset["id"])
        await _apply_auto_tags(asset, mapping, current.get(asset_id, {}), blocked.get(asset_id, ()))
    return len(assets or [])


async def list_asset_ids_with_tags(
    tag_ids: Iterable[Any], *, company_id: int | None = None, match_all: bool = False,
) -> list[int]:
    """Return the assets carrying any (or, with ``match_all``, every) one of ``tag_ids``.

    A tag on an asset's company counts as on the asset, so a script filtered
    to a "Managed" company tag reaches every device of those companies, except
    a device that has the tag blocked.
    """
    ids = _ids(tag_ids)
    if not ids:
        return []
    marks = _placeholders(ids)
    company_filter = "AND a.company_id = %s" if company_id is not None else ""
    params: list[Any] = [*ids, *ids]
    if company_id is not None:
        params.append(int(company_id))
    having = "HAVING COUNT(DISTINCT matched.tag_id) = %s" if match_all else ""
    if match_all:
        params.append(len(ids))
    # Only %s placeholders and fixed clauses are interpolated; values are bound.
    sql = (
        "SELECT matched.asset_id FROM ("
        f" SELECT at.asset_id, at.tag_id FROM asset_tags at WHERE at.tag_id IN ({marks})"  # nosec B608
        " UNION"
        " SELECT a2.id AS asset_id, ct.tag_id FROM company_tags ct"
        f" INNER JOIN assets a2 ON a2.company_id = ct.company_id WHERE ct.tag_id IN ({marks})"  # nosec B608
        " AND NOT EXISTS (SELECT 1 FROM asset_tag_blocks b WHERE b.asset_id = a2.id AND b.tag_id = ct.tag_id)"
        ") matched INNER JOIN assets a ON a.id = matched.asset_id"
        f" WHERE a.archived_at IS NULL {company_filter}"  # nosec B608
        f" GROUP BY matched.asset_id {having} ORDER BY matched.asset_id"  # nosec B608
    )
    rows = await db.fetch_all(sql, tuple(params))
    return [int(row["asset_id"]) for row in rows or []]


async def list_asset_blocked_tags(asset_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """SELECT t.id, t.name, t.colour, t.description, t.auto_key
           FROM asset_tag_blocks b INNER JOIN tags t ON t.id = b.tag_id
           WHERE b.asset_id = %s ORDER BY t.slug""",
        (int(asset_id),),
    )
    return [{**_public(row), "source": "blocked"} for row in rows or []]


async def block_asset_tag(asset_id: int, tag_id: int, *, blocked_by: int | None = None) -> bool:
    """Keep a tag off one asset, removing it if present. Returns False for an unknown tag."""
    if not await existing_tag_ids([tag_id]):
        return False
    await db.execute(
        f"{_insert_ignore()} INTO asset_tag_blocks (asset_id, tag_id, created_by_user_id) VALUES (%s, %s, %s)",
        (int(asset_id), int(tag_id), blocked_by),
    )
    await db.execute("DELETE FROM asset_tags WHERE asset_id = %s AND tag_id = %s", (int(asset_id), int(tag_id)))
    return True


async def unblock_asset_tag(asset_id: int, tag_id: int) -> None:
    """Lift a block; an automatic tag whose rule still matches comes straight back."""
    await db.execute(
        "DELETE FROM asset_tag_blocks WHERE asset_id = %s AND tag_id = %s", (int(asset_id), int(tag_id)),
    )
    await refresh_asset_auto_tags(asset_id)


# ---------------------------------------------------------------------------
# Company tags
# ---------------------------------------------------------------------------


async def list_company_tags(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """SELECT t.id, t.name, t.colour, t.description, t.auto_key
           FROM company_tags ct INNER JOIN tags t ON t.id = ct.tag_id
           WHERE ct.company_id = %s ORDER BY t.slug""",
        (int(company_id),),
    )
    return [{**_public(row), "source": "manual"} for row in rows or []]


async def set_company_tags(company_id: int, tag_ids: Iterable[Any]) -> None:
    wanted = await existing_tag_ids(tag_ids)
    rows = await db.fetch_all("SELECT tag_id FROM company_tags WHERE company_id = %s", (int(company_id),))
    current = {int(row["tag_id"]) for row in rows or []}
    for tag_id in current - set(wanted):
        await db.execute("DELETE FROM company_tags WHERE company_id = %s AND tag_id = %s", (int(company_id), tag_id))
    for tag_id in wanted:
        if tag_id not in current:
            await db.execute(
                f"{_insert_ignore()} INTO company_tags (company_id, tag_id) VALUES (%s, %s)",
                (int(company_id), tag_id),
            )
