from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timezone
from collections.abc import Mapping
from typing import Any

import aiomysql
import aiosqlite

from app.core.database import db
from app.core.logging import log_warning
from app.services import asset_types


def _normalise_username(value: Any) -> str:
    username = str(value or "").strip().casefold()
    if "\\" in username:
        username = username.rsplit("\\", 1)[-1]
    if "@" in username:
        username = username.split("@", 1)[0]
    return username


async def _refresh_auto_tags(asset_id: Any) -> None:
    """Re-apply the automatic tag rules after an asset's type or OS may have changed.

    Best effort: tagging must never stop a sync or a save.
    """
    if not asset_id:
        return
    from app.repositories import tags as tags_repo

    try:
        await tags_repo.refresh_asset_auto_tags(int(asset_id))
    except Exception as exc:  # pragma: no cover - logged and ignored
        log_warning("Could not refresh automatic asset tags", asset_id=asset_id, error=str(exc))


async def list_assets_for_ticket_requester(ticket_id: int) -> list[dict[str, Any]]:
    """Return company assets previously linked to, or last used by, a requester."""
    requester = await db.fetch_one(
        """
        SELECT t.company_id, t.requester_id, t.requester_staff_id,
               COALESCE(s.email, u.email) AS email,
               COALESCE(s.first_name, u.first_name) AS first_name,
               COALESCE(s.last_name, u.last_name) AS last_name
        FROM tickets t
        LEFT JOIN users u ON u.id = t.requester_id
        LEFT JOIN staff s ON s.id = t.requester_staff_id
        WHERE t.id = %s
        """,
        (ticket_id,),
    )
    if not requester or not requester.get("company_id"):
        return []

    linked_rows = await db.fetch_all(
        """
        SELECT DISTINCT ta.asset_id
        FROM ticket_assets ta
        INNER JOIN tickets other_ticket ON other_ticket.id = ta.ticket_id
        WHERE other_ticket.company_id = %s
          AND ((%s IS NOT NULL AND other_ticket.requester_id = %s)
            OR (%s IS NOT NULL AND other_ticket.requester_staff_id = %s))
        """,
        (
            requester["company_id"], requester.get("requester_id"), requester.get("requester_id"),
            requester.get("requester_staff_id"), requester.get("requester_staff_id"),
        ),
    )
    linked_ids = {int(row["asset_id"]) for row in (linked_rows or [])}
    candidates = {
        _normalise_username(value)
        for value in (
            str(requester.get("email") or "").split("@", 1)[0],
            ".".join(filter(None, (requester.get("first_name"), requester.get("last_name")))),
            requester.get("first_name"),
        )
        if _normalise_username(value)
    }

    matches = []
    for asset in await list_company_assets(int(requester["company_id"])):
        asset_id = int(asset["id"])
        last_user_match = _normalise_username(asset.get("last_user")) in candidates
        if asset_id in linked_ids or last_user_match:
            record = dict(asset)
            record["match_reasons"] = [
                reason
                for matched, reason in (
                    (asset_id in linked_ids, "Previously linked to requester"),
                    (last_user_match, "Last logged-in user"),
                )
                if matched
            ]
            matches.append(record)
    return matches


async def list_company_assets(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            id,
            company_id,
            name,
            type,
            asset_type,
            asset_type_source,
            machine_type,
            serial_number,
            status,
            os_name,
            cpu_name,
            ram_gb,
            hdd_size,
            last_sync,
            boot_time,
            motherboard_manufacturer,
            form_factor,
            last_user,
            approx_age,
            performance_score,
            warranty_status,
            warranty_end_date,
            syncro_asset_id,
            tactical_asset_id,
            mac_address,
            customer_visible,
            provenance,
            archived_at,
            location
        FROM assets
        WHERE company_id = %s
        ORDER BY name ASC, id ASC
        """,
        (company_id,),
    )
    return list(rows or [])


async def list_assets_for_knowledge_base_editor() -> list[dict[str, Any]]:
    """Return concise asset labels for the super-admin runbook editor."""
    rows = await db.fetch_all(
        """SELECT a.id, a.name, a.type, c.name AS company_name
           FROM assets a
           LEFT JOIN companies c ON c.id = a.company_id
           WHERE a.archived_at IS NULL
           ORDER BY c.name ASC, a.name ASC, a.id ASC"""
    )
    return list(rows or [])


async def get_asset_by_id(asset_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM assets WHERE id = %s",
        (asset_id,),
    )


async def create_manual_asset(
    *, company_id: int, name: str, type: str | None, status: str | None,
    serial_number: str | None, location: str | None, created_by: int,
    asset_type: str | None = None,
) -> int:
    """Create a human-owned canonical asset without integration identifiers."""
    asset_id = await db.execute_returning_lastrowid(
        """INSERT INTO assets
           (company_id, name, type, asset_type, asset_type_source, status,
            serial_number, location, provenance, manual_created_by)
           VALUES (%s, %s, %s, %s, 'manual', %s, %s, %s, 'manual', %s)""",
        (company_id, name, type, asset_type, status, serial_number, location, created_by),
    )
    await _refresh_auto_tags(asset_id)
    return asset_id


async def list_custom_asset_types(company_id: int) -> list[str]:
    """Return the distinct custom type names already used by a company's assets."""
    rows = await db.fetch_all(
        """SELECT DISTINCT type FROM assets
           WHERE company_id = %s AND asset_type = %s AND type IS NOT NULL AND type <> ''""",
        (company_id, asset_types.CUSTOM_KEY),
    )
    names = {str(row["type"]).strip() for row in rows if str(row.get("type") or "").strip()}
    return sorted(names, key=str.casefold)


async def update_manual_inventory(
    asset_id: int, *, name: str, type: str | None, status: str | None,
    serial_number: str | None, location: str | None, asset_type: str | None = None,
) -> None:
    """Update fields owned by a manual asset; integration-owned rows are excluded."""
    await db.execute(
        """UPDATE assets SET name = %s, type = %s, asset_type = %s,
           asset_type_source = 'manual', status = %s, serial_number = %s, location = %s
           WHERE id = %s AND provenance = 'manual'""",
        (name, type, asset_type, status, serial_number, location, asset_id),
    )
    await _refresh_auto_tags(asset_id)


async def set_asset_type(company_id: int, asset_id: int, asset_type: str | None) -> None:
    """Override a synced asset's catalogue type, or hand it back to sync (``None``).

    An override survives later syncs; clearing it re-derives the type from
    what the integration last reported.
    """
    if asset_type is None:
        record = await db.fetch_one(
            "SELECT type, form_factor, os_name, machine_type FROM assets WHERE id = %s AND company_id = %s",
            (asset_id, company_id),
        )
        if not record:
            return
        await db.execute(
            "UPDATE assets SET asset_type = %s, asset_type_source = 'auto' WHERE id = %s AND company_id = %s",
            (asset_types.derive(record.get("type"), form_factor=record.get("form_factor"),
                                os_name=record.get("os_name"), machine_type=record.get("machine_type")),
             asset_id, company_id),
        )
        await _refresh_auto_tags(asset_id)
        return
    await db.execute(
        "UPDATE assets SET asset_type = %s, asset_type_source = 'manual' WHERE id = %s AND company_id = %s",
        (asset_types.normalise(asset_type), asset_id, company_id),
    )
    await _refresh_auto_tags(asset_id)


async def list_reconciliation_candidates(company_id: int, asset_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """SELECT id, source, external_id, last_seen_at, last_error
           FROM asset_source_records
           WHERE company_id = %s AND asset_id = %s AND status = 'possible_match'
           ORDER BY last_seen_at DESC, id DESC""",
        (company_id, asset_id),
    )
    return list(rows or [])


async def list_asset_sources(company_id: int, asset_id: int) -> list[dict[str, Any]]:
    """Return human-readable provenance for one canonical asset."""
    rows = await db.fetch_all(
        """SELECT id, source, external_id, status, field_ownership_json,
                  last_seen_at, last_success_at
           FROM asset_source_records
           WHERE company_id = %s AND asset_id = %s
           ORDER BY source, external_id""",
        (company_id, asset_id),
    )
    result = []
    for row in rows or []:
        item = dict(row)
        try:
            fields = json.loads(item.get("field_ownership_json") or "[]")
        except (TypeError, ValueError):
            fields = []
        item["owned_fields"] = [str(field).replace("_", " ").title() for field in fields]
        result.append(item)
    return result


async def list_integration_health(company_id: int) -> list[dict[str, Any]]:
    """Build per-source health without ever returning another company's runs."""
    runs = await db.fetch_all(
        """SELECT id, source, status, records_processed, safe_error, started_at, completed_at
           FROM integration_sync_runs WHERE company_id = %s
           ORDER BY started_at DESC, id DESC""",
        (company_id,),
    )
    health: dict[str, dict[str, Any]] = {}
    for raw in runs or []:
        run = dict(raw)
        source = str(run.get("source") or "unknown")
        item = health.setdefault(source, {"source": source, "last_success": None,
                                          "last_failure": None, "latest": None})
        if item["latest"] is None:
            item["latest"] = run
        if run.get("status") == "succeeded" and item["last_success"] is None:
            item["last_success"] = run
        if run.get("status") == "failed" and item["last_failure"] is None:
            item["last_failure"] = run
    return list(health.values())


async def list_company_reconciliation_queue(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """SELECT sr.id, sr.asset_id, sr.source, sr.external_id, sr.status,
                  sr.last_seen_at, sr.last_error, a.name AS asset_name
           FROM asset_source_records sr
           LEFT JOIN assets a ON a.id = sr.asset_id AND a.company_id = sr.company_id
           WHERE sr.company_id = %s AND sr.status IN ('possible_match', 'quarantined')
           ORDER BY sr.last_seen_at DESC, sr.id DESC""",
        (company_id,),
    )
    return list(rows or [])


async def reject_reconciliation(company_id: int, source_record_id: int) -> bool:
    result = await db.execute(
        """UPDATE asset_source_records SET status = 'rejected', asset_id = NULL,
                  last_error = 'Match rejected by an authorised technician'
           WHERE id = %s AND company_id = %s
             AND status IN ('possible_match', 'quarantined')""",
        (source_record_id, company_id),
    )
    return bool(result)


async def approve_reconciliation(company_id: int, asset_id: int, source_record_id: int) -> bool:
    result = await db.execute(
        """UPDATE asset_source_records SET status = 'active', last_error = NULL
           WHERE id = %s AND company_id = %s AND asset_id = %s
             AND status = 'possible_match'""",
        (source_record_id, company_id, asset_id),
    )
    if result:
        await db.execute(
            "UPDATE assets SET provenance = 'integration' WHERE id = %s AND company_id = %s",
            (asset_id, company_id),
        )
    return bool(result)


async def get_asset_by_tactical_id(
    company_id: int, tactical_asset_id: str
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM assets WHERE company_id = %s AND tactical_asset_id = %s",
        (company_id, tactical_asset_id),
    )


async def delete_asset(asset_id: int) -> None:
    # target_id is polymorphic, so the database cannot declare this FK.  Remove
    # inbound asset links explicitly; outbound links cascade from source_asset_id.
    await db.execute(
        "DELETE FROM asset_relationships WHERE target_type = 'asset' AND target_id = %s",
        (asset_id,),
    )
    await db.execute("DELETE FROM assets WHERE id = %s", (asset_id,))


async def list_relationships_for_asset(
    company_id: int, asset_id: int
) -> list[dict[str, Any]]:
    """Return both directions of tenant-scoped relationships for an asset."""
    rows = await db.fetch_all(
        """
        SELECT r.*, 'outbound' AS direction
        FROM asset_relationships r
        WHERE r.company_id = %s AND r.source_asset_id = %s
        UNION ALL
        SELECT r.*, 'inbound' AS direction
        FROM asset_relationships r
        WHERE r.company_id = %s AND r.target_type = 'asset' AND r.target_id = %s
        ORDER BY created_at DESC, id DESC
        """,
        (company_id, asset_id, company_id, asset_id),
    )
    return list(rows or [])


async def create_relationship(
    *, company_id: int, source_asset_id: int, target_type: str,
    target_id: int, relationship_type: str, created_by: int | None,
) -> bool:
    """Create a relationship, returning False when that exact link exists."""
    existing = await db.fetch_one(
        """SELECT id FROM asset_relationships
           WHERE company_id = %s AND source_asset_id = %s AND target_type = %s
             AND target_id = %s AND relationship_type = %s""",
        (company_id, source_asset_id, target_type, target_id, relationship_type),
    )
    if existing:
        return False
    try:
        await db.execute(
            """INSERT INTO asset_relationships
               (company_id, source_asset_id, target_type, target_id, relationship_type, created_by)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (company_id, source_asset_id, target_type, target_id, relationship_type, created_by),
        )
    except (aiomysql.IntegrityError, aiosqlite.IntegrityError) as exc:
        # The unique key is the race-safe duplicate guard after the friendly
        # pre-check above. Do not misreport unrelated integrity failures.
        if (exc.args and exc.args[0] == 1062) or "UNIQUE constraint failed" in str(exc):
            return False
        raise
    return True


async def delete_relationship(company_id: int, relationship_id: int) -> bool:
    result = await db.execute(
        "DELETE FROM asset_relationships WHERE id = %s AND company_id = %s",
        (relationship_id, company_id),
    )
    return bool(result)


async def update_operational_documentation(
    asset_id: int,
    *,
    owner: str | None,
    support_contact: str | None,
    criticality: str | None,
    location: str | None,
    operational_notes: str | None,
    review_status: str,
    external_references_json: str | None,
) -> None:
    """Update only human-owned fields; sync-owned inventory values are untouched."""
    await db.execute(
        """
        UPDATE assets
        SET owner = %s, support_contact = %s, criticality = %s, location = %s,
            operational_notes = %s, review_status = %s,
            reviewed_at = CASE WHEN %s = 'reviewed' THEN UTC_TIMESTAMP() ELSE reviewed_at END,
            external_references_json = %s
        WHERE id = %s
        """,
        (owner, support_contact, criticality, location, operational_notes,
         review_status, review_status, external_references_json, asset_id),
    )


async def set_asset_archived(asset_id: int, archived: bool) -> None:
    await db.execute(
        "UPDATE assets SET archived_at = CASE WHEN %s THEN UTC_TIMESTAMP() ELSE NULL END WHERE id = %s",
        (archived, asset_id),
    )


async def set_customer_visible(asset_id: int, visible: bool) -> None:
    """Publish or withdraw an asset from its company's customer portal."""
    await db.execute(
        "UPDATE assets SET customer_visible = %s WHERE id = %s",
        (1 if visible else 0, asset_id),
    )


async def list_tickets_for_asset(asset_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT t.id, t.subject, t.status, t.updated_at
        FROM ticket_assets ta INNER JOIN tickets t ON t.id = ta.ticket_id
        WHERE ta.asset_id = %s ORDER BY t.updated_at DESC, t.id DESC LIMIT 100
        """,
        (asset_id,),
    )
    return list(rows or [])


async def list_required_fields(asset_type: str | None) -> list[str]:
    if not asset_type:
        return []
    rows = await db.fetch_all(
        "SELECT field_key FROM asset_type_required_fields WHERE asset_type = %s ORDER BY field_key",
        (asset_type,),
    )
    return [str(row["field_key"]) for row in (rows or [])]


async def list_required_field_rules() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT asset_type, field_key FROM asset_type_required_fields ORDER BY asset_type, field_key"
    )
    return list(rows or [])


async def replace_required_fields(asset_type: str, field_keys: list[str]) -> None:
    await db.execute("DELETE FROM asset_type_required_fields WHERE asset_type = %s", (asset_type,))
    for field_key in dict.fromkeys(field_keys):
        await db.execute(
            "INSERT INTO asset_type_required_fields (asset_type, field_key) VALUES (%s, %s)",
            (asset_type, field_key),
        )


def _ensure_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                dt = parsed
                break
            except ValueError:
                continue
        else:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _to_mysql_datetime(value: Any) -> str | None:
    dt = _ensure_datetime(value)
    if not dt:
        return None
    return dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _to_mysql_date(value: Any) -> str | None:
    dt = _ensure_datetime(value)
    if not dt:
        return None
    return dt.date().isoformat()


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        digits = ""
        for ch in text:
            if ch.isdigit() or ch in {"-", "."}:
                digits += ch
            elif digits:
                break
        if not digits:
            return None
        try:
            return float(digits)
        except ValueError:
            return None


_PLACEHOLDER_SERIALS = {
    "",
    "0",
    "00000000",
    "000000000000",
    "0123456789",
    "123456789",
    "1234567890",
    "default string",
    "none",
    "n/a",
    "na",
    "not applicable",
    "not specified",
    "null",
    "o.e.m.",
    "oem",
    "system serial number",
    "to be filled by o.e.m.",
    "to be filled by oem",
    "unknown",
}


def _is_placeholder_serial(serial: str | None) -> bool:
    """Return True for generic BIOS serials shared by many unrelated devices."""
    value = str(serial or "").strip().casefold()
    return value in _PLACEHOLDER_SERIALS or set(value) <= {"0", " ", "-"}


def _identity_conflicts(
    candidate: Mapping[str, Any] | None,
    sync_id: str | None,
    tactical_id: str | None,
) -> bool:
    """Return True when ``candidate`` is already bound to a different remote device."""
    if not candidate:
        return False
    existing_sync = str(candidate.get("syncro_asset_id") or "").strip()
    if sync_id and existing_sync and existing_sync != sync_id:
        return True
    existing_tactical = str(candidate.get("tactical_asset_id") or "").strip()
    if tactical_id and existing_tactical and existing_tactical != tactical_id:
        return True
    return False


async def upsert_asset(
    *,
    company_id: int,
    name: str,
    type: str | None = None,
    machine_type: str | None = None,
    serial_number: str | None = None,
    status: str | None = None,
    os_name: str | None = None,
    cpu_name: str | None = None,
    ram_gb: Any = None,
    hdd_size: str | None = None,
    last_sync: Any = None,
    boot_time: Any = None,
    motherboard_manufacturer: str | None = None,
    form_factor: str | None = None,
    last_user: str | None = None,
    approx_age: Any = None,
    performance_score: Any = None,
    warranty_status: str | None = None,
    warranty_end_date: Any = None,
    syncro_asset_id: str | None = None,
    tactical_asset_id: str | None = None,
    mac_address: str | None = None,
    match_name: bool = False,
    source: str | None = None,
    source_external_id: str | None = None,
    source_fields: list[str] | None = None,
) -> int | None:
    sync_id = str(syncro_asset_id) if syncro_asset_id else None
    tactical_id = str(tactical_asset_id) if tactical_asset_id else None
    ram_value = _coerce_float(ram_gb)
    approx_value = _coerce_float(approx_age)
    performance_value = _coerce_float(performance_score)
    last_sync_db = _to_mysql_datetime(last_sync)
    boot_time_db = _to_mysql_datetime(boot_time)
    warranty_end_db = _to_mysql_date(warranty_end_date)

    row = None
    source_key = str(source_external_id or "").strip() or None
    if source and source_key:
        link = await db.fetch_one(
            "SELECT asset_id, status FROM asset_source_records WHERE company_id = %s AND source = %s AND external_id = %s",
            (company_id, source, source_key),
        )
        if link and link.get("status") == "possible_match":
            await _record_asset_source(
                company_id, source, source_key, link.get("asset_id"), "possible_match",
                source_fields, "Awaiting authorised reconciliation review",
            )
            return None
        if link and link.get("asset_id"):
            linked = await db.fetch_one(
                "SELECT id, syncro_asset_id, tactical_asset_id FROM assets WHERE id = %s AND company_id = %s",
                (link["asset_id"], company_id),
            )
            # A link pointing at an asset that now carries a different remote
            # identity was created when two devices were wrongly merged; drop it
            # so this device gets its own asset again.
            if linked and not _identity_conflicts(linked, sync_id, tactical_id):
                row = {"id": linked["id"]}
    if not row and sync_id:
        row = await db.fetch_one(
            "SELECT id FROM assets WHERE company_id = %s AND syncro_asset_id = %s",
            (company_id, sync_id),
        )
    if not row and tactical_id:
        row = await db.fetch_one(
            "SELECT id FROM assets WHERE company_id = %s AND tactical_asset_id = %s",
            (company_id, tactical_id),
        )
    if not row and serial_number and not _is_placeholder_serial(serial_number):
        candidates = await db.fetch_all(
            "SELECT id, provenance, syncro_asset_id, tactical_asset_id FROM assets WHERE company_id = %s AND serial_number = %s",
            (company_id, serial_number),
        )
        candidates = [
            c for c in candidates or [] if not _identity_conflicts(c, sync_id, tactical_id)
        ]
        if len(candidates or []) == 1:
            candidate = candidates[0]
            if source and source_key and candidate.get("provenance") == "manual":
                await _record_asset_source(
                    company_id, source, source_key, int(candidate["id"]),
                    "possible_match", source_fields,
                    "Same-company manual asset has the supplied serial number; review required",
                )
                return None
            row = candidate
        elif len(candidates or []) > 1 and source and source_key:
            await _record_asset_source(
                company_id, source, source_key, None, "quarantined", source_fields,
                "Multiple assets share the supplied serial number",
            )
            return None
    if not row and name and (match_name or (source and source_key)):
        candidates = await db.fetch_all(
            "SELECT id, provenance, syncro_asset_id, tactical_asset_id FROM assets WHERE company_id = %s AND LOWER(name) = LOWER(%s)",
            (company_id, name),
        )
        candidates = [
            c for c in candidates or [] if not _identity_conflicts(c, sync_id, tactical_id)
        ]
        if len(candidates or []) == 1:
            candidate = candidates[0]
            if source and source_key and candidate.get("provenance") == "manual":
                await _record_asset_source(
                    company_id, source, source_key, int(candidate["id"]),
                    "possible_match", source_fields,
                    "Name-only match to a manual asset; review required",
                )
                return None
            if match_name:
                row = candidate
        elif len(candidates or []) > 1 and source and source_key:
            await _record_asset_source(
                company_id, source, source_key, None, "quarantined", source_fields,
                "Multiple assets share the supplied name",
            )
            return None

    derived_type = asset_types.derive(
        type, form_factor=form_factor, os_name=os_name, machine_type=machine_type
    )
    params = (
        name,
        type,
        derived_type,
        machine_type,
        status,
        os_name,
        cpu_name,
        ram_value,
        hdd_size,
        last_sync_db,
        boot_time_db,
        motherboard_manufacturer,
        form_factor,
        last_user,
        approx_value,
        performance_value,
        warranty_status,
        warranty_end_db,
        sync_id,
        tactical_id,
        serial_number,
        mac_address,
    )

    if row:
        await db.execute(
            """
            UPDATE assets
            SET name = %s,
                type = %s,
                asset_type = CASE WHEN asset_type_source = 'manual' THEN asset_type ELSE %s END,
                machine_type = COALESCE(%s, machine_type),
                status = %s,
                os_name = %s,
                cpu_name = %s,
                ram_gb = %s,
                hdd_size = %s,
                last_sync = %s,
                boot_time = COALESCE(%s, boot_time),
                motherboard_manufacturer = %s,
                form_factor = %s,
                last_user = %s,
                approx_age = %s,
                performance_score = %s,
                warranty_status = %s,
                warranty_end_date = %s,
                syncro_asset_id = COALESCE(%s, syncro_asset_id),
                tactical_asset_id = COALESCE(%s, tactical_asset_id),
                serial_number = %s,
                mac_address = %s
            WHERE id = %s
            """,
            params + (row["id"],),
        )
        asset_id = int(row["id"])
    else:
        asset_id = await db.execute_returning_lastrowid(
            """
            INSERT INTO assets (
                company_id,
                name,
                type,
                asset_type,
                machine_type,
                serial_number,
                status,
                os_name,
                cpu_name,
                ram_gb,
                hdd_size,
                last_sync,
                boot_time,
                motherboard_manufacturer,
                form_factor,
                last_user,
                approx_age,
                performance_score,
                warranty_status,
                warranty_end_date,
                syncro_asset_id,
                tactical_asset_id,
                mac_address,
                provenance
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'integration')
            """,
            (
                company_id,
                name,
                type,
                derived_type,
                machine_type,
                serial_number,
                status,
                os_name,
                cpu_name,
                ram_value,
                hdd_size,
                last_sync_db,
                boot_time_db,
                motherboard_manufacturer,
                form_factor,
                last_user,
                approx_value,
                performance_value,
                warranty_status,
                warranty_end_db,
                sync_id,
                tactical_id,
                mac_address,
            ),
        )
    if source and source_key:
        await _record_asset_source(
            company_id, source, source_key, asset_id, "active", source_fields, None
        )
    await _refresh_auto_tags(asset_id)
    return asset_id


async def _record_asset_source(
    company_id: int,
    source: str,
    external_id: str,
    asset_id: int | None,
    status: str,
    fields: list[str] | None,
    error: str | None,
) -> None:
    """Persist provenance without putting integration state on the canonical asset."""
    ownership = json.dumps(sorted(set(fields or [])))
    existing = await db.fetch_one(
        "SELECT id FROM asset_source_records WHERE company_id = %s AND source = %s AND external_id = %s",
        (company_id, source, external_id),
    )
    if existing:
        await db.execute(
            "UPDATE asset_source_records SET asset_id = %s, status = %s, field_ownership_json = %s, last_seen_at = UTC_TIMESTAMP(), last_success_at = CASE WHEN %s = 'active' THEN UTC_TIMESTAMP() ELSE last_success_at END, last_error = %s WHERE id = %s",
            (asset_id, status, ownership, status, error, existing["id"]),
        )
    else:
        await db.execute(
            "INSERT INTO asset_source_records (company_id, asset_id, source, external_id, status, field_ownership_json, last_seen_at, last_success_at, last_error) VALUES (%s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(), CASE WHEN %s = 'active' THEN UTC_TIMESTAMP() ELSE NULL END, %s)",
            (company_id, asset_id, source, external_id, status, ownership, status, error),
        )


async def start_sync_run(company_id: int, source: str) -> int:
    return await db.execute_returning_lastrowid(
        "INSERT INTO integration_sync_runs (company_id, source) VALUES (%s, %s)",
        (company_id, source),
    )


async def finish_sync_run(
    run_id: int, *, processed: int = 0, error: str | None = None
) -> None:
    safe_error = (error or "")[:500]
    safe_error = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", safe_error)
    safe_error = re.sub(
        r"(?i)(authorization|token|password|secret|api[-_ ]?key)(\s*[:=]\s*)\S+",
        r"\1\2[REDACTED]", safe_error,
    )
    await db.execute(
        "UPDATE integration_sync_runs SET status = %s, records_processed = %s, safe_error = %s, completed_at = UTC_TIMESTAMP() WHERE id = %s",
        ("failed" if error else "succeeded", processed, safe_error or None, run_id),
    )


async def count_active_assets(*, company_id: Any = None, since: Any = None) -> int:
    """Return the number of assets that have synced since the provided date."""

    filters: list[str] = ["1 = 1"]
    params: list[Any] = []

    def _coerce_company_id(value: Any) -> int | None:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return None
        if candidate < 0:
            return None
        return candidate

    company_value = _coerce_company_id(company_id)
    if company_value is not None:
        filters.append("company_id = %s")
        params.append(company_value)

    since_dt = _ensure_datetime(since)
    if since_dt:
        filters.append("last_sync IS NOT NULL")
        filters.append("last_sync >= %s")
        params.append(since_dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"))

    sql = "SELECT COUNT(*) AS total FROM assets WHERE " + " AND ".join(filters)  # nosec B608
    row = await db.fetch_one(sql, tuple(params) if params else None)
    if not row:
        return 0
    try:
        return int(row.get("total") or 0)
    except (TypeError, ValueError):
        return 0


async def count_active_assets_by_type(
    *,
    company_id: Any = None,
    since: Any = None,
    device_type: str | None = None,
) -> int:
    """Return the number of assets of a specific type that have synced since the provided date.

    Args:
        company_id: Company ID to filter by
        since: Only count assets that synced since this datetime
        device_type: Device type to filter by (e.g., 'Workstation', 'Server', 'User')

    Returns:
        Count of matching assets
    """
    filters: list[str] = ["1 = 1"]
    params: list[Any] = []

    def _coerce_company_id(value: Any) -> int | None:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return None
        if candidate < 0:
            return None
        return candidate

    company_value = _coerce_company_id(company_id)
    if company_value is not None:
        filters.append("company_id = %s")
        params.append(company_value)

    since_dt = _ensure_datetime(since)
    if since_dt:
        filters.append("last_sync IS NOT NULL")
        filters.append("last_sync >= %s")
        params.append(since_dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"))

    if device_type:
        filters.append("LOWER(type) = LOWER(%s)")
        params.append(device_type)

    sql = "SELECT COUNT(*) AS total FROM assets WHERE " + " AND ".join(filters)  # nosec B608
    row = await db.fetch_one(sql, tuple(params) if params else None)
    if not row:
        return 0
    try:
        return int(row.get("total") or 0)
    except (TypeError, ValueError):
        return 0
