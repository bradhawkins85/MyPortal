from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core.database import db


async def list_websites(company_id: int) -> list[dict[str, Any]]:
    return list(await db.fetch_all(
        "SELECT * FROM websites WHERE company_id = %s ORDER BY name, id", (company_id,)
    ) or [])


async def list_for_asset(company_id: int, asset_id: int) -> list[dict[str, Any]]:
    return list(await db.fetch_all(
        """SELECT w.id, w.name, w.url FROM website_asset_links l
           INNER JOIN websites w ON w.id = l.website_id
           INNER JOIN assets a ON a.id = l.asset_id
           WHERE l.asset_id = %s AND w.company_id = %s AND a.company_id = %s
           ORDER BY w.name""", (asset_id, company_id, company_id)
    ) or [])


async def get_website(company_id: int, website_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM websites WHERE company_id = %s AND id = %s", (company_id, website_id)
    )


async def get_links(company_id: int, website_id: int) -> dict[str, list[dict[str, Any]]]:
    """Return linked records, retaining company scoping as defence in depth."""
    assets = await db.fetch_all(
        """SELECT a.id, a.name, a.type FROM website_asset_links l
           INNER JOIN assets a ON a.id = l.asset_id
           WHERE l.website_id = %s AND a.company_id = %s ORDER BY a.name""",
        (website_id, company_id),
    )
    articles = await db.fetch_all(
        """SELECT a.id, a.title, a.slug FROM website_kb_links l
           INNER JOIN knowledge_base_articles a ON a.id = l.article_id
           INNER JOIN knowledge_base_article_companies c ON c.article_id = a.id
           WHERE l.website_id = %s AND c.company_id = %s ORDER BY a.title""",
        (website_id, company_id),
    )
    return {"assets": list(assets or []), "articles": list(articles or [])}


async def list_check_jobs(company_id: int, website_id: int, limit: int = 20) -> list[dict[str, Any]]:
    return list(await db.fetch_all(
        """SELECT j.id, j.status, j.attempt_count, j.max_attempts, j.last_error,
                  j.created_at, j.completed_at
           FROM website_check_jobs j INNER JOIN websites w ON w.id = j.website_id
           WHERE j.website_id = %s AND w.company_id = %s
           ORDER BY j.id DESC LIMIT %s""",
        (website_id, company_id, limit),
    ) or [])


async def create_website(company_id: int, values: dict[str, Any], user_id: int) -> int:
    return int(await db.execute(
        """INSERT INTO websites
        (company_id, name, url, owner, notes, monitor_availability, monitor_tls,
         collect_dns, collect_domain_expiry, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (company_id, values["name"], values["url"], values.get("owner"), values.get("notes"),
         values["monitor_availability"], values["monitor_tls"], values["collect_dns"],
         values["collect_domain_expiry"], user_id),
    ))


async def update_website(company_id: int, website_id: int, values: dict[str, Any]) -> bool:
    result = await db.execute(
        """UPDATE websites SET name = %s, url = %s, owner = %s, notes = %s,
        monitor_availability = %s, monitor_tls = %s, collect_dns = %s,
        collect_domain_expiry = %s, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s AND company_id = %s""",
        (values["name"], values["url"], values.get("owner"), values.get("notes"),
         values["monitor_availability"], values["monitor_tls"], values["collect_dns"],
         values["collect_domain_expiry"], website_id, company_id),
    )
    return bool(result)


async def delete_website(company_id: int, website_id: int) -> bool:
    return bool(await db.execute(
        "DELETE FROM websites WHERE id = %s AND company_id = %s", (website_id, company_id)
    ))


async def replace_links(company_id: int, website_id: int, asset_ids: list[int], article_ids: list[int]) -> None:
    # Scope checks prevent a caller linking records belonging to another company.
    for table, ids in (("assets", asset_ids), ("knowledge_base_articles", article_ids)):
        for record_id in ids:
            if table == "assets":
                sql = "SELECT id FROM assets WHERE id = %s AND company_id = %s"
            else:
                sql = ("SELECT a.id FROM knowledge_base_articles a INNER JOIN "
                       "knowledge_base_article_companies c ON c.article_id = a.id "
                       "WHERE a.id = %s AND c.company_id = %s")
            found = await db.fetch_one(sql, (record_id, company_id))
            if not found:
                raise ValueError("Linked record does not belong to the active company")
    await db.execute("DELETE FROM website_asset_links WHERE website_id = %s", (website_id,))
    await db.execute("DELETE FROM website_kb_links WHERE website_id = %s", (website_id,))
    for asset_id in dict.fromkeys(asset_ids):
        await db.execute("INSERT INTO website_asset_links (website_id, asset_id) VALUES (%s, %s)", (website_id, asset_id))
    for article_id in dict.fromkeys(article_ids):
        await db.execute("INSERT INTO website_kb_links (website_id, article_id) VALUES (%s, %s)", (website_id, article_id))


async def enqueue_check(website_id: int) -> int:
    return int(await db.execute(
        "INSERT INTO website_check_jobs (website_id) VALUES (%s)", (website_id,)
    ))


async def record_success(website_id: int, checked_at: datetime, http_status: int | None,
                         certificate: dict[str, Any] | None, dns_facts: dict[str, Any] | None) -> None:
    await db.execute(
        """UPDATE websites SET last_success_at = %s, last_http_status = %s,
        certificate_expires_at = COALESCE(%s, certificate_expires_at),
        certificate_expiry_source = COALESCE(%s, certificate_expiry_source),
        certificate_checked_at = COALESCE(%s, certificate_checked_at),
        dns_facts_json = COALESCE(%s, dns_facts_json), dns_checked_at = COALESCE(%s, dns_checked_at)
        WHERE id = %s""",
        (checked_at, http_status, certificate and certificate.get("expires_at"),
         certificate and certificate.get("source"), checked_at if certificate else None,
         json.dumps(dns_facts) if dns_facts is not None else None,
         checked_at if dns_facts is not None else None, website_id),
    )


async def record_failure(website_id: int, checked_at: datetime, message: str) -> None:
    # Deliberately preserve the last success, HTTP state and expiry facts: a checker
    # failure is not evidence that the site is down or its certificate has expired.
    await db.execute(
        "UPDATE websites SET last_failure_at = %s, last_failure_message = %s WHERE id = %s",
        (checked_at, message[:500], website_id),
    )


async def record_domain_expiry(website_id: int, checked_at: datetime,
                               expires_at: datetime, source: str) -> None:
    await db.execute(
        """UPDATE websites SET domain_expires_at = %s, domain_expiry_source = %s,
        domain_checked_at = %s WHERE id = %s""",
        (expires_at, source, checked_at, website_id),
    )
