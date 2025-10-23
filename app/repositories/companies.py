from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, List, Optional, Sequence

from app.core.database import db
from app.services.company_domains import normalise_email_domains


def _normalise_company(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    if "is_vip" in normalised and normalised["is_vip"] is not None:
        normalised["is_vip"] = int(normalised["is_vip"])
    if "id" in normalised and normalised["id"] is not None:
        normalised["id"] = int(normalised["id"])
    return normalised


async def count_companies() -> int:
    row = await db.fetch_one("SELECT COUNT(*) AS count FROM companies")
    return int(row["count"]) if row else 0


async def get_company_by_id(company_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM companies WHERE id = %s", (company_id,))
    if not row:
        return None
    company = _normalise_company(row)
    company["email_domains"] = await get_email_domains_for_company(company["id"])
    return company


async def get_company_by_syncro_id(syncro_company_id: str) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        "SELECT * FROM companies WHERE syncro_company_id = %s",
        (syncro_company_id,),
    )
    if not row:
        return None
    company = _normalise_company(row)
    company["email_domains"] = await get_email_domains_for_company(company["id"])
    return company


async def get_company_by_name(name: str) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        "SELECT * FROM companies WHERE LOWER(name) = LOWER(%s) LIMIT 1",
        (name,),
    )
    if not row:
        return None
    company = _normalise_company(row)
    company["email_domains"] = await get_email_domains_for_company(company["id"])
    return company


async def get_company_by_email_domain(domain: str) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        """
        SELECT c.*
        FROM company_email_domains AS d
        INNER JOIN companies AS c ON c.id = d.company_id
        WHERE d.domain = %s
        LIMIT 1
        """,
        (domain,),
    )
    if not row:
        return None
    company = _normalise_company(row)
    company["email_domains"] = await get_email_domains_for_company(company["id"])
    return company


async def get_email_domains_for_company(company_id: int) -> list[str]:
    rows = await db.fetch_all(
        "SELECT domain FROM company_email_domains WHERE company_id = %s ORDER BY domain",
        (company_id,),
    )
    return [str(row["domain"]).strip().lower() for row in rows if row.get("domain")]


async def _bulk_email_domains(company_ids: Sequence[int]) -> dict[int, list[str]]:
    if not company_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(company_ids))
    rows = await db.fetch_all(
        f"""
        SELECT company_id, domain
        FROM company_email_domains
        WHERE company_id IN ({placeholders})
        ORDER BY company_id, domain
        """,
        tuple(company_ids),
    )
    grouped: dict[int, list[str]] = defaultdict(list)
    for row in rows:
        raw_company_id = row.get("company_id")
        domain = row.get("domain")
        if raw_company_id is None or not domain:
            continue
        try:
            company_id = int(raw_company_id)
        except (TypeError, ValueError):
            continue
        grouped[company_id].append(str(domain).strip().lower())
    return grouped


async def list_companies() -> List[dict[str, Any]]:
    rows = await db.fetch_all("SELECT * FROM companies ORDER BY name")
    companies = [_normalise_company(row) for row in rows]
    company_ids = [company["id"] for company in companies if company.get("id") is not None]
    domain_lookup = await _bulk_email_domains(company_ids)
    for company in companies:
        company_id = company.get("id")
        if company_id is None:
            company["email_domains"] = []
        else:
            company["email_domains"] = domain_lookup.get(int(company_id), [])
    return companies


async def create_company(**data: Any) -> dict[str, Any]:
    email_domains = data.pop("email_domains", None) or []
    email_domains = normalise_email_domains(email_domains)
    columns = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    await db.execute(
        f"INSERT INTO companies ({columns}) VALUES ({placeholders})",
        tuple(data.values()),
    )
    row = await db.fetch_one(
        "SELECT * FROM companies WHERE id = LAST_INSERT_ID()"
    )
    if not row:
        raise RuntimeError("Failed to create company")
    company = _normalise_company(row)
    if email_domains:
        await replace_company_email_domains(company["id"], email_domains)
        company["email_domains"] = email_domains
    else:
        company["email_domains"] = []
    return company


async def update_company(company_id: int, **updates: Any) -> dict[str, Any]:
    email_domains: Iterable[str] | None = updates.pop("email_domains", None)
    if not updates:
        company = await get_company_by_id(company_id)
        if not company:
            raise ValueError("Company not found")
        if email_domains is not None:
            normalised = normalise_email_domains(email_domains)
            await replace_company_email_domains(company_id, normalised)
            company["email_domains"] = normalised
        return company

    columns = ", ".join(f"{column} = %s" for column in updates.keys())
    params = list(updates.values()) + [company_id]
    await db.execute(f"UPDATE companies SET {columns} WHERE id = %s", tuple(params))
    updated = await get_company_by_id(company_id)
    if not updated:
        raise ValueError("Company not found after update")
    if email_domains is not None:
        normalised = normalise_email_domains(email_domains)
        await replace_company_email_domains(company_id, normalised)
        updated["email_domains"] = normalised
    return updated


async def delete_company(company_id: int) -> None:
    await db.execute("DELETE FROM companies WHERE id = %s", (company_id,))


async def replace_company_email_domains(company_id: int, domains: Iterable[str]) -> None:
    normalised = normalise_email_domains(domains)
    await db.execute(
        "DELETE FROM company_email_domains WHERE company_id = %s",
        (company_id,),
    )
    if not normalised:
        return
    values = ", ".join(["(%s, %s)"] * len(normalised))
    params: list[Any] = []
    for domain in normalised:
        params.extend([company_id, domain])
    await db.execute(
        f"INSERT INTO company_email_domains (company_id, domain) VALUES {values}",
        tuple(params),
    )
