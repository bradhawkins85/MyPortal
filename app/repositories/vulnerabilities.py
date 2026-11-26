"""Vulnerability management storage (:mod:`app.services.vulnerabilities`)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from app.core.database import db


def _placeholders(count: int) -> str:
    return ", ".join(["%s"] * count)


# --------------------------------------------------------------------------- #
# Installed software
# --------------------------------------------------------------------------- #


async def list_installed_software(excluded_sources: Sequence[str]) -> list[dict[str, Any]]:
    """Every program on an active agent, with the agent's company and asset.

    Software from the excluded sources (Linux distribution package managers,
    whose security fixes are backported without changing the upstream version)
    is left out."""

    sql = (
        "SELECT s.agent_id, s.name, s.version, s.publisher, s.source, g.company_id, g.asset_id "
        "FROM rmm_device_software s JOIN rmm_agents g ON g.id = s.agent_id "
        "WHERE g.status = 'active'"
    )
    params: list[Any] = []
    if excluded_sources:
        sql += " AND (s.source IS NULL OR s.source NOT IN (" + _placeholders(len(excluded_sources)) + "))"
        params.extend(excluded_sources)
    rows = await db.fetch_all(sql, tuple(params))
    return [dict(row) for row in rows or []]


async def list_unmapped_candidates(excluded_sources: Sequence[str], limit: int = 200) -> list[dict[str, Any]]:
    """The most installed program names, for suggesting new mappings."""

    sql = (
        "SELECT s.name, MAX(s.publisher) AS publisher, COUNT(DISTINCT s.agent_id) AS devices "
        "FROM rmm_device_software s JOIN rmm_agents g ON g.id = s.agent_id WHERE g.status = 'active'"
    )
    params: list[Any] = []
    if excluded_sources:
        sql += " AND (s.source IS NULL OR s.source NOT IN (" + _placeholders(len(excluded_sources)) + "))"
        params.extend(excluded_sources)
    sql += " GROUP BY s.name ORDER BY devices DESC, s.name LIMIT %s"
    params.append(limit)
    rows = await db.fetch_all(sql, tuple(params))
    return [dict(row) for row in rows or []]


# --------------------------------------------------------------------------- #
# Mappings
# --------------------------------------------------------------------------- #


async def list_mappings() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, match_type, pattern, vendor, product, is_enabled, created_by_user_id, created_at "
        "FROM vuln_software_mappings ORDER BY pattern, id"
    )
    return [{**dict(row), "id": int(row["id"]), "is_enabled": bool(row["is_enabled"])} for row in rows or []]


async def get_mapping(mapping_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, match_type, pattern, vendor, product, is_enabled FROM vuln_software_mappings WHERE id = %s",
        (mapping_id,),
    )
    return {**dict(row), "id": int(row["id"]), "is_enabled": bool(row["is_enabled"])} if row else None


async def create_mapping(values: dict[str, Any], *, user_id: int | None) -> int:
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO vuln_software_mappings (match_type, pattern, vendor, product, is_enabled, created_by_user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (values["match_type"], values["pattern"], values["vendor"], values["product"],
         1 if values.get("is_enabled", True) else 0, user_id),
    ))


async def update_mapping(mapping_id: int, values: dict[str, Any]) -> None:
    await db.execute(
        "UPDATE vuln_software_mappings SET match_type = %s, pattern = %s, vendor = %s, product = %s, is_enabled = %s "
        "WHERE id = %s",
        (values["match_type"], values["pattern"], values["vendor"], values["product"],
         1 if values.get("is_enabled", True) else 0, mapping_id),
    )


async def delete_mapping(mapping_id: int) -> None:
    await db.execute("DELETE FROM vuln_software_mappings WHERE id = %s", (mapping_id,))


# --------------------------------------------------------------------------- #
# CVEs and products
# --------------------------------------------------------------------------- #


async def list_products() -> dict[tuple[str, str], dict[str, Any]]:
    rows = await db.fetch_all("SELECT vendor, product, cve_count, synced_at, last_error FROM vuln_products")
    return {(row["vendor"], row["product"]): dict(row) for row in rows or []}


async def save_product(vendor: str, product: str, *, synced_at: datetime | None, error: str | None) -> None:
    count_row = await db.fetch_one(
        "SELECT COUNT(DISTINCT cve_id) AS total FROM vuln_cve_products WHERE vendor = %s AND product = %s",
        (vendor, product),
    )
    total = int((count_row or {}).get("total") or 0)
    updated = await db.execute_rowcount(
        "UPDATE vuln_products SET cve_count = %s, synced_at = COALESCE(%s, synced_at), last_error = %s "
        "WHERE vendor = %s AND product = %s",
        (total, synced_at, error, vendor, product),
    )
    if not updated:
        await db.execute(
            "INSERT INTO vuln_products (vendor, product, cve_count, synced_at, last_error) VALUES (%s, %s, %s, %s, %s)",
            (vendor, product, total, synced_at, error),
        )


async def existing_cve_ids(cve_ids: Sequence[str]) -> set[str]:
    if not cve_ids:
        return set()
    rows = await db.fetch_all(
        "SELECT cve_id FROM vuln_cves WHERE cve_id IN (" + _placeholders(len(cve_ids)) + ")", tuple(cve_ids)
    )
    return {row["cve_id"] for row in rows or []}


async def save_cves(cves: Sequence[dict[str, Any]], *, vendor: str, product: str) -> None:
    """Insert or update CVEs and replace their affected ranges for one product."""

    if not cves:
        return
    known = await existing_cve_ids([cve["cve_id"] for cve in cves])
    fields = ("published_at", "modified_at", "cvss_score", "cvss_version", "severity", "summary")
    inserts = [tuple([cve["cve_id"]] + [cve.get(key) for key in fields]) for cve in cves if cve["cve_id"] not in known]
    updates = [tuple([cve.get(key) for key in fields] + [cve["cve_id"]]) for cve in cves if cve["cve_id"] in known]
    if inserts:
        await db.execute_many(
            "INSERT INTO vuln_cves (cve_id, published_at, modified_at, cvss_score, cvss_version, severity, summary) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            inserts,
        )
    if updates:
        await db.execute_many(
            "UPDATE vuln_cves SET published_at = %s, modified_at = %s, cvss_score = %s, cvss_version = %s, "
            "severity = %s, summary = %s WHERE cve_id = %s",
            updates,
        )
    await db.execute_many(
        "DELETE FROM vuln_cve_products WHERE cve_id = %s AND vendor = %s AND product = %s",
        [(cve["cve_id"], vendor, product) for cve in cves],
    )
    ranges = [
        (cve["cve_id"], vendor, product, item.get("version_exact"), item.get("start_including"),
         item.get("start_excluding"), item.get("end_including"), item.get("end_excluding"))
        for cve in cves for item in cve.get("ranges") or []
    ]
    if ranges:
        await db.execute_many(
            "INSERT INTO vuln_cve_products (cve_id, vendor, product, version_exact, start_including, start_excluding, "
            "end_including, end_excluding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            ranges,
        )


async def list_product_ranges(products: Iterable[tuple[str, str]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    wanted = sorted(set(products))
    found: dict[tuple[str, str], list[dict[str, Any]]] = {key: [] for key in wanted}
    if not wanted:
        return found
    clauses = " OR ".join(["(p.vendor = %s AND p.product = %s)"] * len(wanted))
    params = tuple(value for pair in wanted for value in pair)
    rows = await db.fetch_all(
        "SELECT p.cve_id, p.vendor, p.product, p.version_exact, p.start_including, p.start_excluding, "
        "p.end_including, p.end_excluding FROM vuln_cve_products p WHERE " + clauses,
        params,
    )
    for row in rows or []:
        found.setdefault((row["vendor"], row["product"]), []).append(dict(row))
    return found


async def replace_kev(entries: Sequence[dict[str, Any]]) -> None:
    await db.execute("DELETE FROM vuln_kev")
    if entries:
        await db.execute_many(
            "INSERT INTO vuln_kev (cve_id, vendor_project, product, name, date_added, due_date, known_ransomware, "
            "required_action) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(item["cve_id"], item.get("vendor_project"), item.get("product"), item.get("name"),
              item.get("date_added"), item.get("due_date"), 1 if item.get("known_ransomware") else 0,
              item.get("required_action")) for item in entries],
        )


async def counts() -> dict[str, int]:
    row = await db.fetch_one(
        "SELECT (SELECT COUNT(*) FROM vuln_cves) AS cves, (SELECT COUNT(*) FROM vuln_kev) AS kev, "
        "(SELECT COUNT(*) FROM vuln_products) AS products, "
        "(SELECT COUNT(*) FROM vuln_findings WHERE status = 'open') AS open_findings"
    )
    return {key: int((row or {}).get(key) or 0) for key in ("cves", "kev", "products", "open_findings")}


# --------------------------------------------------------------------------- #
# Sync state
# --------------------------------------------------------------------------- #


async def get_state() -> dict[str, Any]:
    row = await db.fetch_one(
        "SELECT nvd_synced_at, kev_synced_at, matched_at, requested_at, running_since, last_error "
        "FROM vuln_sync_state WHERE id = 1"
    )
    return dict(row) if row else {}


_STATE_ASSIGNMENTS = {
    "nvd_synced_at": "nvd_synced_at = %s",
    "kev_synced_at": "kev_synced_at = %s",
    "matched_at": "matched_at = %s",
    "requested_at": "requested_at = %s",
    "running_since": "running_since = %s",
    "last_error": "last_error = %s",
}


async def update_state(**fields: Any) -> None:
    chosen = [key for key in _STATE_ASSIGNMENTS if key in fields]
    if not chosen:
        return
    assignments = ", ".join(_STATE_ASSIGNMENTS[key] for key in chosen)
    updated = await db.execute_rowcount(
        "UPDATE vuln_sync_state SET " + assignments + " WHERE id = 1",
        tuple(fields[key] for key in chosen),
    )
    if not updated:
        await db.execute("INSERT INTO vuln_sync_state (id) VALUES (1)")
        await db.execute("UPDATE vuln_sync_state SET " + assignments + " WHERE id = 1",
                         tuple(fields[key] for key in chosen))


async def claim_run(now: datetime, stale_before: datetime) -> bool:
    """Mark a sync as running unless one started recently on another instance."""

    claimed = await db.execute_rowcount(
        "UPDATE vuln_sync_state SET running_since = %s, requested_at = NULL "
        "WHERE id = 1 AND (running_since IS NULL OR running_since < %s)",
        (now, stale_before),
    )
    return bool(claimed)


# --------------------------------------------------------------------------- #
# Findings and exceptions
# --------------------------------------------------------------------------- #


async def list_current_findings() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, agent_id, cve_id, software_name, software_version, status, asset_id, company_id "
        "FROM vuln_findings WHERE status IN ('open', 'accepted')"
    )
    return [dict(row) for row in rows or []]


async def list_finding_keys() -> dict[tuple[int, str, str], dict[str, Any]]:
    rows = await db.fetch_all("SELECT id, agent_id, cve_id, software_name, status FROM vuln_findings")
    return {(int(row["agent_id"]), row["cve_id"], row["software_name"]): dict(row) for row in rows or []}


async def insert_findings(rows: Sequence[tuple[Any, ...]]) -> None:
    if rows:
        await db.execute_many(
            "INSERT INTO vuln_findings (agent_id, asset_id, company_id, cve_id, vendor, product, software_name, "
            "software_version, status, first_seen_at, last_seen_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )


async def refresh_findings(rows: Sequence[tuple[Any, ...]]) -> None:
    """(asset_id, company_id, software_version, status, last_seen_at, id) for findings still present."""

    if rows:
        await db.execute_many(
            "UPDATE vuln_findings SET asset_id = %s, company_id = %s, software_version = %s, status = %s, "
            "last_seen_at = %s, resolved_at = NULL WHERE id = %s",
            rows,
        )


async def resolve_findings(ids: Sequence[int], when: datetime) -> None:
    if ids:
        await db.execute_many(
            "UPDATE vuln_findings SET status = 'resolved', resolved_at = %s WHERE id = %s",
            [(when, finding_id) for finding_id in ids],
        )


_FINDING_SELECT = (
    "SELECT f.id, f.agent_id, f.asset_id, f.company_id, f.cve_id, f.vendor, f.product, f.software_name, "
    "f.software_version, f.status, f.first_seen_at, f.last_seen_at, f.resolved_at, "
    "c.cvss_score, c.severity, c.summary, c.published_at, "
    "k.date_added AS kev_added, k.due_date AS kev_due, k.known_ransomware, k.required_action AS kev_action, "
    "a.name AS asset_name, g.hostname "
    "FROM vuln_findings f "
    "JOIN vuln_cves c ON c.cve_id = f.cve_id "
    "LEFT JOIN vuln_kev k ON k.cve_id = f.cve_id "
    "LEFT JOIN assets a ON a.id = f.asset_id "
    "LEFT JOIN rmm_agents g ON g.id = f.agent_id"
)


async def list_findings(*, company_id: int | None = None, asset_id: int | None = None,
                        statuses: Sequence[str] = ("open", "accepted")) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if company_id is not None:
        conditions.append("f.company_id = %s")
        params.append(company_id)
    if asset_id is not None:
        conditions.append("f.asset_id = %s")
        params.append(asset_id)
    if statuses:
        conditions.append("f.status IN (" + _placeholders(len(statuses)) + ")")
        params.extend(statuses)
    sql = _FINDING_SELECT + (" WHERE " + " AND ".join(conditions) if conditions else "")
    sql += " ORDER BY c.cvss_score DESC, f.cve_id, f.id"
    rows = await db.fetch_all(sql, tuple(params))
    return [dict(row) for row in rows or []]


async def list_exceptions(company_id: int | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT e.id, e.company_id, e.vendor, e.product, e.cve_id, e.reason, e.expires_at, e.created_by_user_id, "
           "e.created_at, u.email AS created_by_email FROM vuln_exceptions e "
           "LEFT JOIN users u ON u.id = e.created_by_user_id")
    params: tuple[Any, ...] = ()
    if company_id is not None:
        sql += " WHERE e.company_id = %s"
        params = (company_id,)
    rows = await db.fetch_all(sql + " ORDER BY e.created_at DESC", params)
    return [dict(row) for row in rows or []]


async def get_exception(exception_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, company_id, vendor, product, cve_id, reason, expires_at FROM vuln_exceptions WHERE id = %s",
        (exception_id,),
    )
    return dict(row) if row else None


async def create_exception(*, company_id: int, vendor: str, product: str, cve_id: str | None, reason: str,
                           expires_at: datetime | None, user_id: int | None) -> int:
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO vuln_exceptions (company_id, vendor, product, cve_id, reason, expires_at, created_by_user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (company_id, vendor, product, cve_id, reason, expires_at, user_id),
    ))


async def delete_exception(exception_id: int) -> None:
    await db.execute("DELETE FROM vuln_exceptions WHERE id = %s", (exception_id,))


async def set_findings_status(*, company_id: int, vendor: str, product: str, cve_id: str | None,
                              from_status: str, to_status: str) -> int:
    sql = ("UPDATE vuln_findings SET status = %s WHERE company_id = %s AND vendor = %s AND product = %s "
           "AND status = %s")
    params: list[Any] = [to_status, company_id, vendor, product, from_status]
    if cve_id:
        sql += " AND cve_id = %s"
        params.append(cve_id)
    return await db.execute_rowcount(sql, tuple(params))
