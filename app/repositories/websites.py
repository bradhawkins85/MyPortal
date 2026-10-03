from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
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


async def list_dns_changes(company_id: int, website_id: int, *, page: int = 1,
                           page_size: int = 25) -> list[dict[str, Any]]:
    """Return immutable events with tenant scoping enforced in the query."""
    offset = (max(page, 1) - 1) * page_size
    return list(await db.fetch_all(
        """SELECT c.* FROM website_dns_changes c
           INNER JOIN websites w ON w.id = c.website_id
           WHERE c.company_id = %s AND c.website_id = %s AND w.company_id = %s
           ORDER BY c.observed_at DESC, c.id DESC LIMIT %s OFFSET %s""",
        (company_id, website_id, company_id, page_size, offset),
    ) or [])
async def get_website_by_id(website_id: int) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT * FROM websites WHERE id = %s", (website_id,))


async def create_website(company_id: int, values: dict[str, Any], user_id: int) -> int:
    return await db.execute_returning_lastrowid(
        """INSERT INTO websites
        (company_id, name, url, owner, notes, monitor_availability, monitor_tls,
         collect_dns, collect_domain_expiry, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (company_id, values["name"], values["url"], values.get("owner"), values.get("notes"),
         values["monitor_availability"], values["monitor_tls"], values["collect_dns"],
         values["collect_domain_expiry"], user_id),
    )


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


CHECK_TYPES = {"website", "dns"}
_COMPONENT_FAILURE_SQL = {
    "certificate": (
        "UPDATE websites SET certificate_failure_at = %s, "
        "certificate_failure_message = %s WHERE id = %s"
    ),
    "registration": (
        "UPDATE websites SET registration_failure_at = %s, "
        "registration_failure_message = %s WHERE id = %s"
    ),
    "dns": (
        "UPDATE websites SET dns_failure_at = %s, "
        "dns_failure_message = %s WHERE id = %s"
    ),
}


async def enqueue_check(website_id: int, *, check_type: str = "website",
                        idempotency_key: str | None = None) -> int:
    if check_type not in CHECK_TYPES:
        raise ValueError("Unknown website check type")
    existing = await db.fetch_one(
        "SELECT id FROM website_check_jobs WHERE website_id = %s AND check_type = %s "
        "AND status IN ('pending', 'running') ORDER BY id LIMIT 1",
        (website_id, check_type),
    )
    if existing:
        return int(existing["id"])
    return await db.execute_returning_lastrowid(
        "INSERT INTO website_check_jobs (website_id, check_type, idempotency_key) VALUES (%s, %s, %s)",
        (website_id, check_type, idempotency_key),
    )


async def enqueue_scheduled_scope(*, command: str, company_id: int | None,
                                  task_id: int, due_window: str) -> dict[str, int]:
    """Queue one typed observation per eligible site, applying default overrides.

    The existence of a company task (including an inactive one) deliberately
    suppresses the all-company default for that command.
    """
    check_type = {"refresh_website_checks": "website", "refresh_dns_records": "dns"}.get(command)
    if not check_type:
        raise ValueError("Unknown website scheduled task command")
    clauses = []
    params: list[Any] = []
    if company_id is not None:
        clauses.append("w.company_id = %s")
        params.append(int(company_id))
    else:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM scheduled_tasks t WHERE t.company_id = w.company_id AND t.command = %s)"
        )
        params.append(command)
    if check_type == "dns":
        clauses.append("w.collect_dns = 1")
    else:
        clauses.append(
            "(w.monitor_availability = 1 OR w.monitor_tls = 1 OR "
            "w.collect_domain_expiry = 1)"
        )
    rows = await db.fetch_all(
        "SELECT w.id FROM websites w WHERE " + " AND ".join(clauses) + " ORDER BY w.company_id, w.id",  # nosec B608
        tuple(params),
    ) or []
    queued = skipped = 0
    for row in rows:
        before = await db.fetch_one(
            "SELECT id FROM website_check_jobs WHERE website_id = %s AND check_type = %s "
            "AND status IN ('pending', 'running') LIMIT 1", (row["id"], check_type),
        )
        key = f"scheduled:{check_type}:{task_id}:{due_window}:{row['id']}"[:191]
        try:
            job_id = await enqueue_check(int(row["id"]), check_type=check_type,
                                         idempotency_key=key)
            if before or not job_id:
                skipped += 1
            else:
                queued += 1
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                skipped += 1
            else:
                raise
    return {"queued": queued, "checked": 0, "changed": 0, "skipped": skipped, "failed": 0}


async def enqueue_due(now: datetime, limit: int) -> int:
    rows = await db.fetch_all(
        """SELECT w.id, w.next_check_at FROM websites w
           WHERE (w.next_check_at IS NULL OR w.next_check_at <= %s)
           AND (w.monitor_availability = 1 OR w.monitor_tls = 1 OR w.collect_domain_expiry = 1)
           AND NOT EXISTS (SELECT 1 FROM scheduled_tasks t
                           WHERE t.command = 'refresh_website_checks'
                           AND (t.company_id IS NULL OR t.company_id = w.company_id))
           ORDER BY w.next_check_at, w.id LIMIT %s""",
        (now, limit),
    ) or []
    created = 0
    for row in rows:
        before = await db.fetch_one(
            "SELECT id FROM website_check_jobs WHERE website_id = %s AND check_type = 'website' AND status IN ('pending', 'running') LIMIT 1",
            (row["id"],),
        )
        if not before:
            due = row.get("next_check_at") or "initial"
            key = ("website:" + str(row["id"]) + ":" + str(due))[:191]
            try:
                await db.execute("INSERT INTO website_check_jobs (website_id, check_type, available_at, idempotency_key) VALUES (%s, 'website', %s, %s)", (row["id"], now, key))
                created += 1
            except Exception as exc:
                # The unique idempotency key is the final arbiter when two
                # application instances schedule the same due observation.
                if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
                    raise
    return created


async def claim_jobs(*, owner: str, now: datetime, lease_seconds: int, limit: int,
                     company_limit: int) -> list[dict[str, Any]]:
    lease_until = now + timedelta(seconds=lease_seconds)
    # A worker may be terminated after claiming its final attempt.  Finalise
    # those abandoned jobs before looking for more work so they cannot be
    # reclaimed indefinitely (and display attempt counts such as 40/3).
    await db.execute(
        """UPDATE website_check_jobs SET status = 'failed', completed_at = %s,
        lease_owner = NULL, lease_expires_at = NULL,
        last_error = COALESCE(last_error, 'Website check timed out'), updated_at = %s
        WHERE status = 'running' AND lease_expires_at < %s
        AND attempt_count >= max_attempts""",
        (now, now, now),
    )
    candidates = await db.fetch_all(
        """SELECT j.*, w.company_id FROM website_check_jobs j JOIN websites w ON w.id = j.website_id
        WHERE j.available_at <= %s AND j.attempt_count < j.max_attempts
        AND (j.status = 'pending' OR (j.status = 'running' AND j.lease_expires_at < %s))
        ORDER BY j.available_at, j.id LIMIT %s""", (now, now, limit * max(company_limit, 1) * 2),
    ) or []
    claimed, company_counts = [], {}
    for candidate in candidates:
        company_id = int(candidate["company_id"])
        if company_counts.get(company_id, 0) >= company_limit:
            continue
        changed = await db.execute(
            """UPDATE website_check_jobs SET status = 'running', lease_owner = %s,
            lease_expires_at = %s, started_at = COALESCE(started_at, %s),
            attempt_count = attempt_count + 1, updated_at = %s
            WHERE id = %s AND attempt_count < max_attempts
            AND (status = 'pending' OR (status = 'running' AND lease_expires_at < %s))""",
            (owner, lease_until, now, now, candidate["id"], now),
        )
        if changed:
            row = await db.fetch_one("SELECT * FROM website_check_jobs WHERE id = %s AND lease_owner = %s", (candidate["id"], owner))
            if row:
                claimed.append(dict(row))
                company_counts[company_id] = company_counts.get(company_id, 0) + 1
        if len(claimed) >= limit:
            break
    return claimed


async def finish_job(job: dict[str, Any], *, owner: str, ok: bool, now: datetime,
                     interval_seconds: int, error: str | None = None) -> None:
    if ok:
        changed = await db.execute(
            "UPDATE website_check_jobs SET status = 'completed', completed_at = %s, lease_owner = NULL, lease_expires_at = NULL, last_error = NULL, updated_at = %s WHERE id = %s AND lease_owner = %s AND status = 'running'",
            (now, now, job["id"], owner),
        )
        if changed:
            await db.execute("UPDATE websites SET next_check_at = %s WHERE id = %s", (now + timedelta(seconds=interval_seconds), job["website_id"]))
        return
    attempts = int(job.get("attempt_count") or 0)
    exhausted = attempts >= int(job.get("max_attempts") or 3)
    delay = interval_seconds if exhausted else min(3600, 30 * (2 ** max(0, attempts - 1)))
    status = "failed" if exhausted else "pending"
    changed = await db.execute(
        "UPDATE website_check_jobs SET status = %s, available_at = %s, lease_owner = NULL, lease_expires_at = NULL, last_error = %s, completed_at = %s, updated_at = %s WHERE id = %s AND lease_owner = %s AND status = 'running'",
        (status, now + timedelta(seconds=delay), (error or "Website check failed")[:500], now if exhausted else None, now, job["id"], owner),
    )
    if exhausted and changed:
        await db.execute("UPDATE websites SET next_check_at = %s WHERE id = %s", (now + timedelta(seconds=interval_seconds), job["website_id"]))


async def job_health() -> dict[str, Any]:
    rows = await db.fetch_all("SELECT status, COUNT(*) count FROM website_check_jobs GROUP BY status") or []
    oldest = await db.fetch_one("SELECT MIN(available_at) oldest_ready_at FROM website_check_jobs WHERE status = 'pending'")
    return {"counts": {str(row["status"]): int(row["count"]) for row in rows},
            "oldest_ready_at": oldest and oldest.get("oldest_ready_at")}


def _rrsets(snapshot: dict[str, Any] | None) -> dict[tuple[str, str], list[str]]:
    return {(str(r["name"]).lower().rstrip(".") + ".", str(r["type"]).upper()):
            sorted({str(v).strip() for v in r.get("values", [])})
            for r in (snapshot or {}).get("records", [])}


async def _record_dns_snapshot(website_id: int, checked_at: datetime,
                               snapshot: dict[str, Any]) -> None:
    website = await get_website_by_id(website_id)
    if not website:
        return
    try:
        before_snapshot = json.loads(website.get("dns_facts_json") or "null")
    except (TypeError, json.JSONDecodeError):
        before_snapshot = None
    before, after = _rrsets(before_snapshot), _rrsets(snapshot)
    # The first observation is a baseline, not a flood of artificial additions.
    if before_snapshot is not None:
        for key in sorted(set(before) | set(after)):
            old, new = before.get(key), after.get(key)
            if old == new:
                continue
            kind = "added" if old is None else "removed" if new is None else "modified"
            material = json.dumps([website.get("dns_checked_at"), key, old, new],
                                  separators=(",", ":"), sort_keys=True, default=str)
            event_hash = hashlib.sha256(material.encode()).hexdigest()
            try:
                await db.execute(
                    """INSERT INTO website_dns_changes
                    (company_id, website_id, observed_at, source, coverage, record_name,
                     record_type, change_kind, before_json, after_json, event_hash)
                    SELECT company_id, id, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    FROM websites WHERE id = %s""",
                    (checked_at, snapshot["source"], snapshot["coverage"], key[0], key[1], kind,
                     json.dumps(old) if old is not None else None,
                     json.dumps(new) if new is not None else None, event_hash, website_id),
                )
            except Exception as exc:
                if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
                    raise


async def record_success(website_id: int, checked_at: datetime, http_status: int | None,
                         certificate: dict[str, Any] | None, dns_facts: dict[str, Any] | None,
                         registration: dict[str, Any] | None = None) -> None:
    if dns_facts is not None:
        await _record_dns_snapshot(website_id, checked_at, dns_facts)
    await db.execute(
        """UPDATE websites SET last_success_at = %s, last_http_status = %s,
        certificate_expires_at = COALESCE(%s, certificate_expires_at),
        certificate_expiry_source = COALESCE(%s, certificate_expiry_source),
        certificate_checked_at = COALESCE(%s, certificate_checked_at),
        certificate_facts_json = COALESCE(%s, certificate_facts_json),
        certificate_status = COALESCE(%s, certificate_status),
        dns_facts_json = COALESCE(%s, dns_facts_json), dns_checked_at = COALESCE(%s, dns_checked_at),
        dns_source = COALESCE(%s, dns_source), dns_coverage = COALESCE(%s, dns_coverage),
        registration_facts_json = COALESCE(%s, registration_facts_json),
        registration_status = COALESCE(%s, registration_status),
        domain_expires_at = COALESCE(%s, domain_expires_at),
        domain_expiry_source = COALESCE(%s, domain_expiry_source),
        domain_checked_at = COALESCE(%s, domain_checked_at)
        WHERE id = %s""",
        (checked_at, http_status, certificate and certificate.get("expires_at"),
         certificate and certificate.get("source"), checked_at if certificate else None,
         json.dumps(certificate, default=str) if certificate else None,
         certificate and certificate.get("status"),
         json.dumps(dns_facts) if dns_facts is not None else None,
         checked_at if dns_facts is not None else None,
         dns_facts and dns_facts.get("source"), dns_facts and dns_facts.get("coverage"),
         json.dumps(registration, default=str) if registration else None,
         registration and registration.get("status"),
         registration and registration.get("expires_at"),
         registration and registration.get("source"), checked_at if registration else None,
         website_id),
    )


async def record_dns_success(website_id: int, checked_at: datetime,
                             dns_facts: dict[str, Any]) -> None:
    """Persist a DNS-only observation without altering website availability."""
    await _record_dns_snapshot(website_id, checked_at, dns_facts)
    await db.execute(
        """UPDATE websites SET dns_facts_json = %s, dns_checked_at = %s,
        dns_source = %s, dns_coverage = %s WHERE id = %s""",
        (json.dumps(dns_facts), checked_at, dns_facts.get("source"),
         dns_facts.get("coverage"), website_id),
    )


async def record_component_failure(website_id: int, component: str, checked_at: datetime,
                                   message: str) -> None:
    statement = _COMPONENT_FAILURE_SQL.get(component)
    if statement is None:
        raise ValueError("Unknown observation component")
    await db.execute(statement, (checked_at, message[:500], website_id))


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
