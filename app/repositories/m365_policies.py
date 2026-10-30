"""Repository for Microsoft 365 standards policies and their tenant data."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from app.core.database import db

_BATCH_SIZE = 400


def _json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _marks(values: tuple[int, ...]) -> str:
    return ", ".join("%s" for _ in values)


def _batches(values: Iterable[int]) -> Iterable[tuple[int, ...]]:
    ordered = sorted(set(values))
    for index in range(0, len(ordered), _BATCH_SIZE):
        yield tuple(ordered[index:index + _BATCH_SIZE])


def _normalise_policy(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row.get("name") or ""),
        "description": row.get("description") or "",
        "enabled": bool(row.get("enabled")),
        "target_rules": _json(row.get("target_rules")),
        "created_by": row.get("created_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "standards": [],
        "overrides": [],
    }


async def list_policies() -> list[dict[str, Any]]:
    """Return every policy with its standards and company overrides."""
    rows = await db.fetch_all(
        "SELECT id, name, description, enabled, target_rules, created_by, created_at, updated_at "
        "FROM m365_policies ORDER BY name, id"
    )
    policies = {int(row["id"]): _normalise_policy(row) for row in rows or []}
    if not policies:
        return []
    for row in await db.fetch_all(
        "SELECT policy_id, standard_id, mode, settings FROM m365_policy_standards "
        "ORDER BY policy_id, standard_id"
    ) or []:
        policy = policies.get(int(row["policy_id"]))
        if policy is not None:
            policy["standards"].append({
                "standard_id": str(row["standard_id"]),
                "mode": str(row["mode"]),
                "settings": _json(row.get("settings")) or {},
            })
    for row in await db.fetch_all(
        "SELECT policy_id, company_id, action FROM m365_policy_company_overrides "
        "ORDER BY policy_id, company_id"
    ) or []:
        policy = policies.get(int(row["policy_id"]))
        if policy is not None:
            policy["overrides"].append({"company_id": int(row["company_id"]), "action": str(row["action"])})
    return list(policies.values())


async def get_policy(policy_id: int) -> dict[str, Any] | None:
    for policy in await list_policies():
        if policy["id"] == policy_id:
            return policy
    return None


async def create_policy(
    *,
    name: str,
    description: str,
    enabled: bool,
    target_rules: dict[str, Any] | None,
    created_by: int | None,
) -> int:
    return await db.execute_returning_lastrowid(
        "INSERT INTO m365_policies (name, description, enabled, target_rules, created_by, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (name, description, 1 if enabled else 0,
         json.dumps(target_rules) if target_rules is not None else None, created_by),
    )


async def update_policy(
    policy_id: int,
    *,
    name: str,
    description: str,
    enabled: bool,
    target_rules: dict[str, Any] | None,
) -> None:
    await db.execute(
        "UPDATE m365_policies SET name = %s, description = %s, enabled = %s, target_rules = %s, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
        (name, description, 1 if enabled else 0,
         json.dumps(target_rules) if target_rules is not None else None, policy_id),
    )


async def delete_policy(policy_id: int) -> None:
    # Child rows are removed explicitly as SQLite may not enforce cascades.
    await db.execute("DELETE FROM m365_policy_standards WHERE policy_id = %s", (policy_id,))
    await db.execute("DELETE FROM m365_policy_company_overrides WHERE policy_id = %s", (policy_id,))
    await db.execute("DELETE FROM m365_policies WHERE id = %s", (policy_id,))


async def replace_standards(policy_id: int, standards: list[dict[str, Any]]) -> None:
    await db.execute("DELETE FROM m365_policy_standards WHERE policy_id = %s", (policy_id,))
    for standard in standards:
        await db.execute(
            "INSERT INTO m365_policy_standards (policy_id, standard_id, mode, settings) VALUES (%s, %s, %s, %s)",
            (policy_id, standard["standard_id"], standard["mode"], json.dumps(standard.get("settings") or {})),
        )


async def replace_overrides(policy_id: int, overrides: list[dict[str, Any]]) -> None:
    await db.execute("DELETE FROM m365_policy_company_overrides WHERE policy_id = %s", (policy_id,))
    for override in overrides:
        await db.execute(
            "INSERT INTO m365_policy_company_overrides (policy_id, company_id, action) VALUES (%s, %s, %s)",
            (policy_id, override["company_id"], override["action"]),
        )


async def tenant_contexts(company_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """Return filter data for active companies, keyed by company ID.

    Contexts hold only what policy filters read: the company name, tags,
    variable values and the licence SKUs the tenant has purchased.
    """
    contexts: dict[int, dict[str, Any]] = {}
    for batch in _batches(company_ids):
        marks = _marks(batch)
        for row in await db.fetch_all(
            "SELECT id, name FROM companies WHERE id IN (" + marks + ") "  # nosec B608 - placeholders only, IDs are bound
            "AND COALESCE(archived, 0) = 0", batch,
        ) or []:
            contexts[int(row["id"])] = {
                "company_id": int(row["id"]),
                "company_name": str(row.get("name") or ""),
                "company_tags": [],
                "company_variables": {},
                "license_skus": [],
                "license_names": [],
            }
        for row in await db.fetch_all(
            "SELECT ct.company_id, t.name FROM company_tags ct JOIN tags t ON t.id = ct.tag_id "
            "WHERE ct.company_id IN (" + marks + ")", batch,  # nosec B608 - placeholders only, IDs are bound
        ) or []:
            context = contexts.get(int(row["company_id"]))
            if context is not None:
                context["company_tags"].append(str(row["name"]))
        for row in await db.fetch_all(
            "SELECT v.company_id, d.name, v.value FROM company_variable_values v "
            "JOIN company_variable_definitions d ON d.id = v.variable_id "
            "WHERE v.company_id IN (" + marks + ")", batch,  # nosec B608 - placeholders only, IDs are bound
        ) or []:
            context = contexts.get(int(row["company_id"]))
            if context is not None:
                context["company_variables"][str(row["name"])] = str(row.get("value") or "")
        for row in await db.fetch_all(
            "SELECT l.company_id, l.name, l.platform, f.friendly_name FROM licenses l "
            "LEFT JOIN license_sku_friendly_names f ON f.sku = l.platform "
            "WHERE l.company_id IN (" + marks + ") AND l.count > 0", batch,  # nosec B608 - placeholders only, IDs are bound
        ) or []:
            context = contexts.get(int(row["company_id"]))
            if context is None:
                continue
            sku = str(row.get("platform") or "").strip()
            if sku and sku not in context["license_skus"]:
                context["license_skus"].append(sku)
            for name in (row.get("friendly_name"), row.get("name")):
                name = str(name or "").strip()
                if name and name not in context["license_names"]:
                    context["license_names"].append(name)
    return contexts


_RESULT_COLUMNS = (
    "company_id", "standard_id", "policy_id", "mode", "status", "details", "affected",
    "checked_at", "drift_since", "remediated_at",
)


def _normalise_result(row: dict[str, Any]) -> dict[str, Any]:
    result = {column: row.get(column) for column in _RESULT_COLUMNS}
    result["company_id"] = int(row["company_id"])
    result["policy_id"] = int(row["policy_id"]) if row.get("policy_id") is not None else None
    result["affected"] = _json(row.get("affected")) or []
    return result


_RESULT_SELECT = (
    "SELECT company_id, standard_id, policy_id, mode, status, details, affected, "
    "checked_at, drift_since, remediated_at FROM m365_standard_results"
)
_RESULT_UPSERT_SQLITE = (
    "INSERT INTO m365_standard_results (company_id, standard_id, policy_id, mode, status, "
    "details, affected, checked_at, drift_since, remediated_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT(company_id, standard_id) DO UPDATE SET policy_id = excluded.policy_id, "
    "mode = excluded.mode, status = excluded.status, details = excluded.details, "
    "affected = excluded.affected, checked_at = excluded.checked_at, "
    "drift_since = excluded.drift_since, remediated_at = excluded.remediated_at"
)
_RESULT_UPSERT_MYSQL = (
    "INSERT INTO m365_standard_results (company_id, standard_id, policy_id, mode, status, "
    "details, affected, checked_at, drift_since, remediated_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON DUPLICATE KEY UPDATE policy_id = VALUES(policy_id), mode = VALUES(mode), "
    "status = VALUES(status), details = VALUES(details), affected = VALUES(affected), "
    "checked_at = VALUES(checked_at), drift_since = VALUES(drift_since), "
    "remediated_at = VALUES(remediated_at)"
)


async def list_results(company_id: int | None = None) -> list[dict[str, Any]]:
    """Return the latest standard results, for one company or every company."""
    if company_id is None:
        rows = await db.fetch_all(_RESULT_SELECT + " ORDER BY company_id, standard_id")
    else:
        rows = await db.fetch_all(_RESULT_SELECT + " WHERE company_id = %s ORDER BY standard_id", (company_id,))
    return [_normalise_result(row) for row in rows or []]


async def upsert_result(result: dict[str, Any]) -> None:
    values = tuple(
        json.dumps(result.get("affected") or []) if column == "affected" else result.get(column)
        for column in _RESULT_COLUMNS
    )
    await db.execute(_RESULT_UPSERT_SQLITE if db.is_sqlite() else _RESULT_UPSERT_MYSQL, values)


async def delete_stale_results(company_id: int, keep_standard_ids: Iterable[str]) -> None:
    """Remove results for standards no policy assigns to the company any more."""
    keep = tuple(sorted(set(keep_standard_ids)))
    if not keep:
        await db.execute("DELETE FROM m365_standard_results WHERE company_id = %s", (company_id,))
        return
    await db.execute(
        "DELETE FROM m365_standard_results WHERE company_id = %s AND standard_id NOT IN ("
        + ", ".join("%s" for _ in keep) + ")",  # nosec B608 - placeholders only, values are bound
        (company_id, *keep),
    )
