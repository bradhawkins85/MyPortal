"""SMB1001 compliance tracking.

SMB1001 is a cumulative, five-tier cyber security certification standard for
small and medium businesses (Bronze, Silver, Gold, Platinum, Diamond).  A
company holds a tier once every control at that tier and every lower tier is
compliant or not applicable.  It replaces the retiring Essential 8 framework;
existing Essential 8 progress can be converted into SMB1001 progress.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from app.core.database import db
from app.repositories import essential8 as essential8_repo

STATUSES: tuple[str, ...] = (
    "not_started",
    "in_progress",
    "compliant",
    "non_compliant",
    "not_applicable",
)
DONE_STATUSES = frozenset({"compliant", "not_applicable"})
# Statuses where offering technician help makes sense.
HELP_STATUSES = frozenset({"not_started", "in_progress", "non_compliant"})
MAX_TIER = 5

DOMAINS: dict[str, str] = {
    "technology": "Technology management",
    "access": "Access management",
    "backup": "Backup and recovery",
    "policies": "Policies, processes and plans",
    "education": "Education and training",
}

# SMB1001 control code -> Essential 8 (control_order, maturity level) pairs
# that evidence the same practice.  Every listed pair must be met for the
# SMB1001 control to be carried over as compliant.  Essential 8 control order:
# 1 application control, 2 patch applications, 3 Office macros, 4 user
# application hardening, 5 restrict admin privileges, 6 patch operating
# systems, 7 multi-factor authentication, 8 regular backups.
ESSENTIAL8_MAPPINGS: dict[str, tuple[tuple[int, str], ...]] = {
    "TM-04": ((2, "ml1"), (6, "ml1")),
    "TM-05": ((2, "ml1"), (6, "ml1")),
    "BR-01": ((8, "ml1"),),
    "AM-03": ((7, "ml1"),),
    "AM-05": ((5, "ml1"),),
    "AM-08": ((5, "ml1"),),
    "TM-09": ((3, "ml1"),),
    "TM-10": ((4, "ml1"),),
    "BR-03": ((8, "ml1"),),
    "BR-02": ((8, "ml2"),),
    "AM-07": ((7, "ml2"),),
    "TM-11": ((2, "ml2"), (6, "ml2")),
    "AM-09": ((7, "ml3"),),
    "TM-13": ((1, "ml2"),),
}

_E8_CONTROL_NAMES = {
    1: "Application control",
    2: "Patch applications",
    3: "Configure Microsoft Office macro settings",
    4: "User application hardening",
    5: "Restrict administrative privileges",
    6: "Patch operating systems",
    7: "Multi-factor authentication",
    8: "Regular backups",
}

_EDITABLE_FIELDS = (
    "status",
    "evidence",
    "notes",
    "owner_user_id",
    "last_reviewed_date",
    "target_compliance_date",
)


def _format_datetime(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _format_date(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _normalise_record(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["created_at"] = _format_datetime(item.get("created_at"))
    item["updated_at"] = _format_datetime(item.get("updated_at"))
    item["last_reviewed_date"] = _format_date(item.get("last_reviewed_date"))
    item["target_compliance_date"] = _format_date(item.get("target_compliance_date"))
    return item


def clamp_tier(value: Any) -> int:
    try:
        tier = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_TIER, tier))


async def list_tiers() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT id, tier_level, code, name, description, attestation
        FROM smb1001_tiers
        ORDER BY tier_level
        """
    )
    return [dict(row) for row in rows]


async def list_controls(tier_level: Optional[int] = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    where = ""
    if tier_level is not None:
        where = "WHERE tier_level = %(tier_level)s"
        params["tier_level"] = tier_level
    rows = await db.fetch_all(
        f"""
        SELECT id, code, tier_level, domain, control_order, name, description, verification
        FROM smb1001_controls
        {where}
        ORDER BY tier_level, domain, control_order
        """,  # nosec B608 - fixed clause
        params,
    )
    controls = []
    for row in rows:
        item = dict(row)
        item["domain_label"] = DOMAINS.get(item.get("domain") or "", item.get("domain") or "")
        controls.append(item)
    domain_order = {domain: index for index, domain in enumerate(DOMAINS)}
    controls.sort(
        key=lambda item: (
            int(item["tier_level"]),
            domain_order.get(item.get("domain") or "", len(domain_order)),
            int(item["control_order"]),
        )
    )
    return controls


async def get_control(control_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        """
        SELECT id, code, tier_level, domain, control_order, name, description, verification
        FROM smb1001_controls
        WHERE id = %(control_id)s
        """,
        {"control_id": control_id},
    )
    if not row:
        return None
    item = dict(row)
    item["domain_label"] = DOMAINS.get(item.get("domain") or "", item.get("domain") or "")
    return item


async def get_profile(company_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        """
        SELECT company_id, target_tier, essential8_imported_at, created_at, updated_at
        FROM company_smb1001_profile
        WHERE company_id = %(company_id)s
        """,
        {"company_id": company_id},
    )
    if not row:
        return None
    item = dict(row)
    for key in ("essential8_imported_at", "created_at", "updated_at"):
        item[key] = _format_datetime(item.get(key))
    return item


async def create_profile(company_id: int, target_tier: int = 1) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO company_smb1001_profile (company_id, target_tier)
        VALUES (%(company_id)s, %(target_tier)s)
        """,
        {"company_id": company_id, "target_tier": clamp_tier(target_tier)},
    )
    return await get_profile(company_id) or {"company_id": company_id, "target_tier": clamp_tier(target_tier)}


async def set_target_tier(company_id: int, target_tier: int) -> dict[str, Any]:
    tier = clamp_tier(target_tier)
    if await get_profile(company_id) is None:
        return await create_profile(company_id, tier)
    await db.execute(
        "UPDATE company_smb1001_profile SET target_tier = %(tier)s WHERE company_id = %(company_id)s",
        {"tier": tier, "company_id": company_id},
    )
    return await get_profile(company_id) or {"company_id": company_id, "target_tier": tier}


async def _mark_essential8_imported(company_id: int) -> None:
    await db.execute(
        """
        UPDATE company_smb1001_profile
        SET essential8_imported_at = %(now)s
        WHERE company_id = %(company_id)s
        """,
        {"company_id": company_id, "now": datetime.now(timezone.utc).replace(tzinfo=None)},
    )


async def list_company_compliance(company_id: int) -> dict[int, dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT id, company_id, control_id, status, evidence, notes, owner_user_id,
               last_reviewed_date, target_compliance_date, source, created_at, updated_at
        FROM company_smb1001_compliance
        WHERE company_id = %(company_id)s
        """,
        {"company_id": company_id},
    )
    return {int(row["control_id"]): _normalise_record(row) for row in rows}


async def get_company_control_compliance(company_id: int, control_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        """
        SELECT id, company_id, control_id, status, evidence, notes, owner_user_id,
               last_reviewed_date, target_compliance_date, source, created_at, updated_at
        FROM company_smb1001_compliance
        WHERE company_id = %(company_id)s AND control_id = %(control_id)s
        """,
        {"company_id": company_id, "control_id": control_id},
    )
    return _normalise_record(row) if row else None


async def append_audit(
    *,
    company_id: int,
    control_id: int,
    user_id: Optional[int],
    action: str,
    from_status: Optional[str],
    to_status: Optional[str],
    change_summary: Optional[str] = None,
) -> None:
    await db.execute(
        """
        INSERT INTO company_smb1001_audit
            (company_id, control_id, user_id, action, from_status, to_status, change_summary)
        VALUES
            (%(company_id)s, %(control_id)s, %(user_id)s, %(action)s,
             %(from_status)s, %(to_status)s, %(change_summary)s)
        """,
        {
            "company_id": company_id,
            "control_id": control_id,
            "user_id": user_id,
            "action": action,
            "from_status": from_status,
            "to_status": to_status,
            "change_summary": change_summary,
        },
    )


async def list_control_audit(company_id: int, control_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT id, company_id, control_id, user_id, action, from_status, to_status,
               change_summary, created_at
        FROM company_smb1001_audit
        WHERE company_id = %(company_id)s AND control_id = %(control_id)s
        ORDER BY created_at DESC, id DESC
        LIMIT %(limit)s
        """,
        {"company_id": company_id, "control_id": control_id, "limit": limit},
    )
    result = []
    for row in rows:
        item = dict(row)
        item["created_at"] = _format_datetime(item.get("created_at"))
        result.append(item)
    return result


async def save_company_control_compliance(
    company_id: int,
    control_id: int,
    *,
    user_id: Optional[int] = None,
    source: str = "manual",
    action: str = "update",
    change_summary: Optional[str] = None,
    **fields: Any,
) -> dict[str, Any]:
    """Create or update a company's record for one control and audit the change.

    Only keys present in ``fields`` are written, so callers can clear a value
    by passing ``None`` explicitly.
    """

    unknown = set(fields) - set(_EDITABLE_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported SMB1001 fields: {', '.join(sorted(unknown))}")
    status = fields.get("status")
    if status is not None and status not in STATUSES:
        raise ValueError(f"Invalid SMB1001 status: {status}")
    values = dict(fields)
    for key in ("last_reviewed_date", "target_compliance_date"):
        if key in values:
            values[key] = _format_date(values[key])

    existing = await get_company_control_compliance(company_id, control_id)
    params: dict[str, Any] = {"company_id": company_id, "control_id": control_id, "source": source, **values}
    if existing is None:
        params.setdefault("status", "not_started")
        columns = ["company_id", "control_id", "source", *values.keys()]
        if "status" not in values:
            columns.append("status")
        placeholders = ", ".join(f"%({column})s" for column in columns)
        await db.execute(
            f"INSERT INTO company_smb1001_compliance ({', '.join(columns)}) VALUES ({placeholders})",  # nosec B608 - whitelisted columns
            params,
        )
        from_status = None
    else:
        set_clauses = [f"{key} = %({key})s" for key in values]
        set_clauses.append("source = %(source)s")
        await db.execute(
            f"""
            UPDATE company_smb1001_compliance
            SET {', '.join(set_clauses)}
            WHERE company_id = %(company_id)s AND control_id = %(control_id)s
            """,  # nosec B608 - whitelisted columns
            params,
        )
        from_status = existing.get("status")

    record = await get_company_control_compliance(company_id, control_id) or {
        "company_id": company_id,
        "control_id": control_id,
        **values,
    }
    await append_audit(
        company_id=company_id,
        control_id=control_id,
        user_id=user_id,
        action=action,
        from_status=from_status,
        to_status=record.get("status"),
        change_summary=change_summary,
    )
    return record


def build_tier_progress(
    tiers: Iterable[dict[str, Any]],
    controls: Iterable[dict[str, Any]],
    compliance_map: dict[int, dict[str, Any]],
    *,
    target_tier: int = 1,
) -> dict[str, Any]:
    """Summarise progress per tier and work out the tier currently achieved.

    Tiers are cumulative: a tier is achieved only when it and every lower tier
    have all controls compliant or not applicable.
    """

    controls = list(controls)
    tier_rows: list[dict[str, Any]] = []
    achieved_level = 0
    chain_intact = True
    for tier in sorted(tiers, key=lambda item: int(item["tier_level"])):
        level = int(tier["tier_level"])
        counts = {status: 0 for status in STATUSES}
        for control in controls:
            if int(control["tier_level"]) != level:
                continue
            status = (compliance_map.get(int(control["id"])) or {}).get("status") or "not_started"
            counts[status if status in counts else "not_started"] += 1
        total = sum(counts.values())
        done = counts["compliant"] + counts["not_applicable"]
        complete = done == total
        if chain_intact and complete:
            achieved_level = level
        else:
            chain_intact = False
        tier_rows.append(
            {
                **tier,
                "total": total,
                "done": done,
                "remaining": total - done,
                "percentage": round(done / total * 100, 1) if total else 100.0,
                "counts": counts,
                "complete": complete,
                "achieved": False,
            }
        )
    for row in tier_rows:
        row["achieved"] = int(row["tier_level"]) <= achieved_level

    achieved = next((row for row in tier_rows if int(row["tier_level"]) == achieved_level), None)
    next_tier = next((row for row in tier_rows if int(row["tier_level"]) == achieved_level + 1), None)
    target = clamp_tier(target_tier)
    target_rows = [row for row in tier_rows if int(row["tier_level"]) <= target]
    target_total = sum(row["total"] for row in target_rows)
    target_done = sum(row["done"] for row in target_rows)
    all_total = sum(row["total"] for row in tier_rows)
    all_done = sum(row["done"] for row in tier_rows)
    return {
        "tiers": tier_rows,
        "achieved_level": achieved_level,
        "achieved_tier": achieved,
        "next_tier": next_tier,
        "target_tier": target,
        "target_total": target_total,
        "target_done": target_done,
        "target_remaining": target_total - target_done,
        "target_percentage": round(target_done / target_total * 100, 1) if target_total else 100.0,
        "overall_total": all_total,
        "overall_done": all_done,
        "overall_percentage": round(all_done / all_total * 100, 1) if all_total else 0.0,
    }


def derive_status_from_essential8(level_statuses: Iterable[str]) -> Optional[str]:
    """Combine the Essential 8 statuses that evidence one SMB1001 control."""

    statuses = [status or "not_started" for status in level_statuses]
    if not statuses:
        return None
    if all(status == "compliant" for status in statuses):
        return "compliant"
    if any(status in {"compliant", "in_progress"} for status in statuses):
        return "in_progress"
    return None


async def count_importable_essential8_controls(company_id: int) -> int:
    """Return how many SMB1001 controls can currently import E8 progress."""

    e8_controls = await essential8_repo.list_essential8_controls()
    control_id_by_order = {int(row["control_order"]): int(row["id"]) for row in e8_controls}
    e8_levels = await essential8_repo.get_per_maturity_statuses_for_company(company_id)
    controls = await list_controls()
    compliance_map = await list_company_compliance(company_id)

    importable = 0
    for control in controls:
        sources = ESSENTIAL8_MAPPINGS.get(control["code"])
        if not sources:
            continue
        existing = compliance_map.get(int(control["id"]))
        if existing and (existing.get("status") or "not_started") != "not_started":
            continue
        statuses = [
            (e8_levels.get(control_id_by_order.get(order)) or {}).get(level, "not_started")
            for order, level in sources
        ]
        if derive_status_from_essential8(statuses):
            importable += 1
    return importable


async def import_essential8_progress(company_id: int, *, user_id: Optional[int] = None) -> dict[str, Any]:
    """Convert a company's Essential 8 progress into SMB1001 control statuses.

    Only controls that have not been started are touched, so work already done
    in SMB1001 is never overwritten.  Imported records are marked with source
    ``essential8`` and a note naming the Essential 8 evidence used.
    """

    e8_controls = await essential8_repo.list_essential8_controls()
    control_id_by_order = {int(row["control_order"]): int(row["id"]) for row in e8_controls}
    e8_levels = await essential8_repo.get_per_maturity_statuses_for_company(company_id)
    controls = await list_controls()
    compliance_map = await list_company_compliance(company_id)

    updated = 0
    for control in controls:
        sources = ESSENTIAL8_MAPPINGS.get(control["code"])
        if not sources:
            continue
        existing = compliance_map.get(int(control["id"]))
        if existing and (existing.get("status") or "not_started") != "not_started":
            continue
        statuses = []
        for order, level in sources:
            e8_control_id = control_id_by_order.get(order)
            statuses.append((e8_levels.get(e8_control_id) or {}).get(level, "not_started"))
        new_status = derive_status_from_essential8(statuses)
        if not new_status:
            continue
        evidence_names = ", ".join(
            f"{_E8_CONTROL_NAMES.get(order, f'Control {order}')} {level.upper()}" for order, level in sources
        )
        note = f"Converted from Essential 8 ({evidence_names}). Review against the SMB1001 check before attesting."
        previous_notes = (existing or {}).get("notes")
        await save_company_control_compliance(
            company_id,
            int(control["id"]),
            user_id=user_id,
            source="essential8",
            action="essential8_import",
            change_summary=f"Imported from Essential 8: {evidence_names}",
            status=new_status,
            notes=f"{previous_notes}\n{note}" if previous_notes else note,
        )
        updated += 1

    if await get_profile(company_id) is None:
        await create_profile(company_id)
    await _mark_essential8_imported(company_id)
    return {"company_id": company_id, "updated_count": updated}


async def ensure_company_profile(company_id: int, *, user_id: Optional[int] = None) -> dict[str, Any]:
    """Create the company's SMB1001 profile on first use, converting Essential 8 progress."""

    profile = await get_profile(company_id)
    if profile is not None:
        return profile
    try:
        await create_profile(company_id)
    except Exception:
        # Another request created the profile first; it runs the import.
        profile = await get_profile(company_id)
        if profile is None:
            raise
        return profile
    await import_essential8_progress(company_id, user_id=user_id)
    return await get_profile(company_id) or {"company_id": company_id, "target_tier": 1}


async def get_company_overview(company_id: int) -> dict[str, Any]:
    tiers = await list_tiers()
    controls = await list_controls()
    compliance_map = await list_company_compliance(company_id)
    profile = await get_profile(company_id) or {"company_id": company_id, "target_tier": 1}
    for control in controls:
        record = compliance_map.get(int(control["id"]))
        control["compliance"] = record
        control["status"] = (record or {}).get("status") or "not_started"
        control["essential8_mapped"] = control["code"] in ESSENTIAL8_MAPPINGS
    progress = build_tier_progress(
        tiers,
        controls,
        compliance_map,
        target_tier=profile.get("target_tier") or 1,
    )
    return {"profile": profile, "tiers": tiers, "controls": controls, "progress": progress}


# ---------------------------------------------------------------------------
# Evidence files
# ---------------------------------------------------------------------------

_EVIDENCE_COLUMNS = """
    id, company_id, control_id, version_number, title, description, file_name,
    content_type, file_path, file_size_bytes, uploaded_by, uploaded_at, is_current
"""


def _normalise_evidence(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["uploaded_at"] = _format_datetime(item.get("uploaded_at"))
    item["is_current"] = bool(item.get("is_current"))
    return item


async def add_evidence(
    *,
    company_id: int,
    control_id: int,
    title: str,
    file_name: str,
    file_path: str,
    uploaded_by: Optional[int] = None,
    description: Optional[str] = None,
    content_type: Optional[str] = None,
    file_size_bytes: Optional[int] = None,
) -> dict[str, Any]:
    """Store a new evidence version; it becomes the current version for the control."""

    params: dict[str, Any] = {"company_id": company_id, "control_id": control_id}
    latest = await db.fetch_one(
        """
        SELECT COALESCE(MAX(version_number), 0) AS latest_version
        FROM company_smb1001_evidence
        WHERE company_id = %(company_id)s AND control_id = %(control_id)s
        """,
        params,
    )
    version = int((latest or {}).get("latest_version") or 0) + 1
    await db.execute(
        """
        UPDATE company_smb1001_evidence SET is_current = 0
        WHERE company_id = %(company_id)s AND control_id = %(control_id)s
        """,
        params,
    )
    await db.execute(
        """
        INSERT INTO company_smb1001_evidence
            (company_id, control_id, version_number, title, description, file_name,
             content_type, file_path, file_size_bytes, uploaded_by, is_current)
        VALUES
            (%(company_id)s, %(control_id)s, %(version_number)s, %(title)s, %(description)s, %(file_name)s,
             %(content_type)s, %(file_path)s, %(file_size_bytes)s, %(uploaded_by)s, 1)
        """,
        {
            **params,
            "version_number": version,
            "title": title,
            "description": description,
            "file_name": file_name,
            "content_type": content_type,
            "file_path": file_path,
            "file_size_bytes": file_size_bytes,
            "uploaded_by": uploaded_by,
        },
    )
    row = await db.fetch_one(
        f"""
        SELECT {_EVIDENCE_COLUMNS}
        FROM company_smb1001_evidence
        WHERE company_id = %(company_id)s AND control_id = %(control_id)s AND version_number = %(version_number)s
        """,  # nosec B608 - fixed column list
        {**params, "version_number": version},
    )
    await append_audit(
        company_id=company_id,
        control_id=control_id,
        user_id=uploaded_by,
        action="evidence_upload",
        from_status=None,
        to_status=None,
        change_summary=f"Uploaded evidence version {version}: {title}",
    )
    return _normalise_evidence(row) if row else {}


async def get_evidence(company_id: int, evidence_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        f"""
        SELECT {_EVIDENCE_COLUMNS}
        FROM company_smb1001_evidence
        WHERE company_id = %(company_id)s AND id = %(evidence_id)s
        """,  # nosec B608 - fixed column list
        {"company_id": company_id, "evidence_id": evidence_id},
    )
    return _normalise_evidence(row) if row else None


async def list_evidence_map(company_id: int, *, control_id: Optional[int] = None) -> dict[int, list[dict[str, Any]]]:
    """Evidence for a company grouped by control, newest version first."""

    params: dict[str, Any] = {"company_id": company_id}
    control_clause = ""
    if control_id is not None:
        control_clause = " AND control_id = %(control_id)s"
        params["control_id"] = control_id
    rows = await db.fetch_all(
        f"""
        SELECT {_EVIDENCE_COLUMNS}
        FROM company_smb1001_evidence
        WHERE company_id = %(company_id)s{control_clause}
        ORDER BY control_id, version_number DESC
        """,  # nosec B608 - fixed clauses
        params,
    )
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        item = _normalise_evidence(row)
        result.setdefault(int(item["control_id"]), []).append(item)
    return result


async def delete_evidence(company_id: int, evidence_id: int, *, user_id: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Delete one evidence version, promoting the newest remaining version to current.

    Returns the deleted record so the caller can remove the stored file.
    """

    evidence = await get_evidence(company_id, evidence_id)
    if not evidence:
        return None
    await db.execute(
        "DELETE FROM company_smb1001_evidence WHERE company_id = %(company_id)s AND id = %(evidence_id)s",
        {"company_id": company_id, "evidence_id": evidence_id},
    )
    control_id = int(evidence["control_id"])
    if evidence.get("is_current"):
        latest = await db.fetch_one(
            """
            SELECT MAX(version_number) AS latest_version
            FROM company_smb1001_evidence
            WHERE company_id = %(company_id)s AND control_id = %(control_id)s
            """,
            {"company_id": company_id, "control_id": control_id},
        )
        if latest and latest.get("latest_version") is not None:
            await db.execute(
                """
                UPDATE company_smb1001_evidence SET is_current = 1
                WHERE company_id = %(company_id)s AND control_id = %(control_id)s
                  AND version_number = %(version_number)s
                """,
                {"company_id": company_id, "control_id": control_id, "version_number": int(latest["latest_version"])},
            )
    await append_audit(
        company_id=company_id,
        control_id=control_id,
        user_id=user_id,
        action="evidence_delete",
        from_status=None,
        to_status=None,
        change_summary=f"Deleted evidence version {evidence.get('version_number')}: {evidence.get('title')}",
    )
    return evidence


# ---------------------------------------------------------------------------
# Recommended product / service help links
# ---------------------------------------------------------------------------


def resolve_help_url(link: Optional[dict[str, Any]]) -> str:
    """External link wins; otherwise a published portal marketing page."""

    if not link:
        return ""
    if link.get("external_url"):
        return str(link["external_url"])
    if link.get("marketing_page_slug") and link.get("marketing_page_is_published"):
        return f"/marketing/{link['marketing_page_slug']}"
    return ""


async def list_help_links() -> dict[int, dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            link.control_id,
            link.marketing_page_id,
            link.recommendation_name,
            link.external_url,
            page.slug AS marketing_page_slug,
            page.title AS marketing_page_title,
            page.is_published AS marketing_page_is_published
        FROM smb1001_control_marketing_pages AS link
        LEFT JOIN marketing_pages AS page ON page.id = link.marketing_page_id
        ORDER BY link.control_id
        """
    )
    links: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = {
            "control_id": int(row["control_id"]),
            "marketing_page_id": int(row["marketing_page_id"]) if row.get("marketing_page_id") else None,
            "recommendation_name": str(row.get("recommendation_name") or "").strip(),
            "external_url": str(row.get("external_url") or "").strip(),
            "marketing_page_slug": str(row.get("marketing_page_slug") or "").strip(),
            "marketing_page_title": str(row.get("marketing_page_title") or "").strip(),
            "marketing_page_is_published": bool(int(row.get("marketing_page_is_published") or 0)),
        }
        item["help_url"] = resolve_help_url(item)
        links[item["control_id"]] = item
    return links


async def replace_help_links(links: dict[int, dict[str, Any]]) -> None:
    """Replace the recommendation configured for each given control."""

    for control_id, link in links.items():
        params = {
            "control_id": int(control_id),
            "marketing_page_id": link.get("marketing_page_id"),
            "recommendation_name": str(link.get("recommendation_name") or "").strip() or None,
            "external_url": str(link.get("external_url") or "").strip() or None,
        }
        await db.execute(
            "DELETE FROM smb1001_control_marketing_pages WHERE control_id = %(control_id)s",
            {"control_id": params["control_id"]},
        )
        if params["marketing_page_id"] or params["recommendation_name"] or params["external_url"]:
            await db.execute(
                """
                INSERT INTO smb1001_control_marketing_pages
                    (control_id, marketing_page_id, recommendation_name, external_url)
                VALUES
                    (%(control_id)s, %(marketing_page_id)s, %(recommendation_name)s, %(external_url)s)
                """,
                params,
            )


async def list_recommendations(company_id: int) -> dict[str, Any]:
    """Outstanding controls up to the target tier (or the next tier, if beyond it) with recommendations."""

    overview = await get_company_overview(company_id)
    progress = overview["progress"]
    horizon = max(int(progress["target_tier"]), int(progress["achieved_level"]) + 1)
    tier_names = {int(tier["tier_level"]): tier["name"] for tier in overview["tiers"]}
    links = await list_help_links()
    rows: list[dict[str, Any]] = []
    for control in overview["controls"]:
        if int(control["tier_level"]) > horizon or control["status"] in DONE_STATUSES:
            continue
        link = links.get(int(control["id"])) or {}
        rows.append(
            {
                "control_id": control["id"],
                "code": control["code"],
                "control": control["name"],
                "tier": tier_names.get(int(control["tier_level"]), f"Tier {control['tier_level']}"),
                "tier_level": int(control["tier_level"]),
                "status": control["status"],
                "recommendation": link.get("recommendation_name") or "Contact us for assistance",
                "url": link.get("help_url") or "",
            }
        )
    return {"recommendations": rows, "total": len(rows), "horizon_tier": tier_names.get(horizon)}


__all__ = [
    "DOMAINS",
    "DONE_STATUSES",
    "ESSENTIAL8_MAPPINGS",
    "HELP_STATUSES",
    "MAX_TIER",
    "STATUSES",
    "add_evidence",
    "append_audit",
    "delete_evidence",
    "get_evidence",
    "list_evidence_map",
    "list_help_links",
    "list_recommendations",
    "replace_help_links",
    "resolve_help_url",
    "build_tier_progress",
    "clamp_tier",
    "create_profile",
    "derive_status_from_essential8",
    "ensure_company_profile",
    "get_company_control_compliance",
    "get_company_overview",
    "get_control",
    "get_profile",
    "import_essential8_progress",
    "count_importable_essential8_controls",
    "list_company_compliance",
    "list_control_audit",
    "list_controls",
    "list_tiers",
    "save_company_control_compliance",
    "set_target_tier",
]
