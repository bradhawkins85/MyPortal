"""Tenant-scoped persistence for DMARC reporting."""

from __future__ import annotations
from typing import Any
from app.core.database import db


async def company_by_code(code: str) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, dmarc_reporting_code FROM companies WHERE dmarc_reporting_code = %s",
        (code,),
    )


async def company_code(company_id: int) -> str | None:
    row = await db.fetch_one(
        "SELECT dmarc_reporting_code FROM companies WHERE id = %s", (company_id,)
    )
    return (
        str(row["dmarc_reporting_code"])
        if row and row.get("dmarc_reporting_code")
        else None
    )


async def set_company_code(company_id: int, code: str) -> None:
    await db.execute(
        "UPDATE companies SET dmarc_reporting_code = %s WHERE id = %s",
        (code, company_id),
    )


async def create_import(
    *,
    company_id: int | None,
    message_id: str,
    attachment_hash: str,
    filename: str,
    received_at: Any,
    metadata: str | None = None,
) -> int:
    existing = await db.fetch_one(
        "SELECT id FROM dmarc_imports WHERE mailbox_message_id=%s AND attachment_sha256=%s",
        (message_id, attachment_hash),
    )
    if existing:
        return int(existing["id"])
    return await db.execute_returning_lastrowid(
        "INSERT INTO dmarc_imports (company_id,mailbox_message_id,attachment_sha256,source_filename,received_at,raw_metadata) VALUES (%s,%s,%s,%s,%s,%s)",
        (
            company_id,
            message_id[:512],
            attachment_hash,
            filename[:255],
            received_at,
            metadata,
        ),
    )


async def mark_import(
    import_id: int,
    status: str,
    reason: str | None = None,
    content_hash: str | None = None,
) -> None:
    await db.execute(
        "UPDATE dmarc_imports SET status=%s,failure_reason=%s,content_sha256=%s,attempts=attempts+1,updated_at=UTC_TIMESTAMP(6) WHERE id=%s",
        (status, reason, content_hash, import_id),
    )


async def set_import_company(import_id: int, company_id: int | None) -> None:
    await db.execute(
        "UPDATE dmarc_imports SET company_id=%s,updated_at=UTC_TIMESTAMP(6) WHERE id=%s",
        (company_id, import_id),
    )


async def list_quarantine(*, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT id,company_id,source_filename,received_at,status,failure_reason,attempts FROM dmarc_imports WHERE status IN ('quarantined','retry') ORDER BY received_at DESC LIMIT %s OFFSET %s",
        (min(max(limit, 1), 250), max(offset, 0)),
    )


async def save_report(
    company_id: int, import_id: int, report: dict[str, Any], attachment_hash: str
) -> int:
    # A report ID identifies a report within a tenant.  Providers sometimes
    # resend a corrected report with a different attachment/content hash; in
    # that case replace the old aggregate and its cascading detail rows rather
    # than counting both versions.
    existing = await db.fetch_one(
        "SELECT id FROM dmarc_reports WHERE company_id=%s AND report_id=%s",
        (company_id, report["report_id"]),
    )
    if existing:
        await db.execute(
            "DELETE FROM dmarc_reports WHERE company_id=%s AND id=%s",
            (company_id, int(existing["id"])),
        )
    report_pk = await db.execute_returning_lastrowid(
        "INSERT INTO dmarc_reports (company_id,import_id,reporter,report_id,date_begin,date_end,domain,adkim,aspf,policy,subdomain_policy,nonexistent_policy,percentage,attachment_sha256,content_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            company_id,
            import_id,
            report["reporter"],
            report["report_id"],
            report["date_begin"],
            report["date_end"],
            report["domain"],
            report["adkim"],
            report["aspf"],
            report["policy"],
            report["subdomain_policy"],
            report.get("nonexistent_policy"),
            report["percentage"],
            attachment_hash,
            report["content_sha256"],
        ),
    )
    for record in report["records"]:
        record_pk = await db.execute_returning_lastrowid(
            "INSERT INTO dmarc_records (company_id,report_id,source_ip,message_count,disposition,dkim_result,spf_result,header_from,envelope_from,envelope_to) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                company_id,
                report_pk,
                record["source_ip"],
                record["message_count"],
                record["disposition"],
                record["dkim_result"],
                record["spf_result"],
                record["header_from"],
                record["envelope_from"],
                record["envelope_to"],
            ),
        )
        for auth in record["auth_results"]:
            await db.execute(
                "INSERT INTO dmarc_auth_results (company_id,record_id,mechanism,domain,selector,scope,result,human_result) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    company_id,
                    record_pk,
                    auth["mechanism"],
                    auth["domain"],
                    auth["selector"],
                    auth["scope"],
                    auth["result"],
                    auth["human_result"],
                ),
            )
    return report_pk


async def organization_summary(
    company_id: int, start: Any, end: Any, policy_domain: str | None = None
) -> list[dict[str, Any]]:
    """Summarise aggregate message authentication by reporting organization."""
    rows = await db.fetch_all(
        """SELECT p.reporter organization,COALESCE(SUM(r.message_count),0) volume,
        COALESCE(SUM(CASE WHEN r.dkim_result='pass' OR r.spf_result='pass'
          THEN r.message_count ELSE 0 END),0) compliant,
        COALESCE(SUM(CASE WHEN r.dkim_result<>'pass' AND r.spf_result<>'pass'
          THEN r.message_count ELSE 0 END),0) non_compliant,
        COALESCE(SUM(CASE WHEN r.spf_result='pass' THEN r.message_count ELSE 0 END),0) spf_pass,
        COALESCE(SUM(CASE WHEN r.spf_result<>'pass' THEN r.message_count ELSE 0 END),0) spf_fail,
        COALESCE(SUM(CASE WHEN r.dkim_result='pass' THEN r.message_count ELSE 0 END),0) dkim_pass,
        COALESCE(SUM(CASE WHEN r.dkim_result<>'pass' THEN r.message_count ELSE 0 END),0) dkim_fail,
        COALESCE(SUM(CASE WHEN r.dkim_result<>'pass' AND r.spf_result<>'pass'
          THEN r.message_count ELSE 0 END),0) unauthenticated
        FROM dmarc_reports p JOIN dmarc_records r ON r.report_id=p.id
        WHERE p.company_id=%s AND p.date_begin < %s AND p.date_end >= %s
          AND (%s IS NULL OR p.domain=%s)
        GROUP BY p.reporter ORDER BY p.reporter""",
        (company_id, end, start, policy_domain, policy_domain),
    )
    result = []
    for row in rows:
        item = dict(row)
        volume = int(item.get("volume") or 0)
        item["compliance_rate"] = (
            (int(item.get("compliant") or 0) / volume * 100) if volume else 0.0
        )
        result.append(item)
    return result


async def policy_domains(company_id: int, start: Any, end: Any) -> list[dict[str, Any]]:
    """Return the most recently reported policy for every domain in the range."""
    return await db.fetch_all(
        """SELECT p.domain,p.adkim,p.aspf,p.policy,p.subdomain_policy,
        p.nonexistent_policy,p.percentage
        FROM dmarc_reports p
        WHERE p.company_id=%s AND p.date_begin < %s AND p.date_end >= %s
        AND NOT EXISTS (SELECT 1 FROM dmarc_reports newer
          WHERE newer.company_id=p.company_id AND newer.domain=p.domain
          AND newer.date_begin < %s AND newer.date_end >= %s
          AND (newer.date_end > p.date_end OR
            (newer.date_end=p.date_end AND newer.id > p.id)))
        ORDER BY p.domain""",
        (company_id, end, start, end, start),
    )


async def save_forensic_report(
    company_id: int, import_id: int, report: dict[str, Any], attachment_hash: str
) -> int:
    existing = await db.fetch_one(
        "SELECT id FROM dmarc_forensic_reports WHERE company_id=%s AND attachment_sha256=%s AND content_sha256=%s",
        (company_id, attachment_hash, report["content_sha256"]),
    )
    if existing:
        return int(existing["id"])
    return await db.execute_returning_lastrowid(
        """INSERT INTO dmarc_forensic_reports
        (company_id,import_id,feedback_type,user_agent,report_version,arrival_at,source_ip,reported_domain,
         delivery_result,auth_failure,authentication_results,original_mail_from,original_rcpt_to,
         dkim_domain,dkim_selector,identity_alignment,attachment_sha256,content_sha256)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            company_id,
            import_id,
            report["feedback_type"],
            report["user_agent"],
            report["version"],
            report["arrival_at"],
            report["source_ip"],
            report["reported_domain"],
            report["delivery_result"],
            report["auth_failure"],
            report["authentication_results"],
            report["original_mail_from"],
            report["original_rcpt_to"],
            report["dkim_domain"],
            report["dkim_selector"],
            report["identity_alignment"],
            attachment_hash,
            report["content_sha256"],
        ),
    )


async def overview(
    company_id: int, start: Any, end: Any, policy_domain: str | None = None
) -> dict[str, Any]:
    row = await db.fetch_one(
        """SELECT COALESCE(SUM(r.message_count),0) total_messages,
      COALESCE(SUM(CASE WHEN r.dkim_result='pass' OR r.spf_result='pass' THEN r.message_count ELSE 0 END),0) dmarc_pass,
      COALESCE(SUM(CASE WHEN r.dkim_result='pass' THEN r.message_count ELSE 0 END),0) dkim_pass,
      COALESCE(SUM(CASE WHEN r.spf_result='pass' THEN r.message_count ELSE 0 END),0) spf_pass,
      COALESCE(SUM(CASE WHEN r.disposition='none' THEN r.message_count ELSE 0 END),0) disposition_none,
      COALESCE(SUM(CASE WHEN r.disposition='quarantine' THEN r.message_count ELSE 0 END),0) disposition_quarantine,
      COALESCE(SUM(CASE WHEN r.disposition='reject' THEN r.message_count ELSE 0 END),0) disposition_reject,
      COALESCE(SUM(CASE WHEN r.disposition NOT IN ('none','quarantine','reject') THEN r.message_count ELSE 0 END),0) disposition_other
      FROM dmarc_records r JOIN dmarc_reports p ON p.id=r.report_id
      WHERE r.company_id=%s AND p.date_begin < %s AND p.date_end >= %s
      AND (%s IS NULL OR p.domain=%s)""",
        (company_id, end, start, policy_domain, policy_domain),
    )
    result = dict(row or {})
    forensic = await db.fetch_one(
        """SELECT COUNT(*) forensic_reports,
        COALESCE(SUM(CASE WHEN reported_domain IS NOT NULL AND reported_domain<>'' THEN 1 ELSE 0 END),0) forensic_with_reported_domain,
        COALESCE(SUM(CASE WHEN source_ip IS NOT NULL AND source_ip<>'' THEN 1 ELSE 0 END),0) forensic_with_source_ip,
        COALESCE(SUM(CASE WHEN delivery_result IS NOT NULL AND delivery_result<>'' THEN 1 ELSE 0 END),0) forensic_with_delivery_result,
        COALESCE(SUM(CASE WHEN authentication_results IS NOT NULL AND authentication_results<>'' THEN 1 ELSE 0 END),0) forensic_with_authentication_results,
        COALESCE(SUM(CASE WHEN original_mail_from IS NOT NULL AND original_mail_from<>'' THEN 1 ELSE 0 END),0) forensic_with_original_mail_from,
        COALESCE(SUM(CASE WHEN original_rcpt_to IS NOT NULL AND original_rcpt_to<>'' THEN 1 ELSE 0 END),0) forensic_with_original_rcpt_to,
        COALESCE(SUM(CASE WHEN (dkim_domain IS NOT NULL AND dkim_domain<>'') OR (dkim_selector IS NOT NULL AND dkim_selector<>'') THEN 1 ELSE 0 END),0) forensic_with_dkim_details
        FROM dmarc_forensic_reports
        WHERE company_id=%s AND COALESCE(arrival_at,created_at) >= %s AND COALESCE(arrival_at,created_at) < %s""",
        (company_id, start, end),
    )
    for key in (
        "forensic_reports",
        "forensic_with_reported_domain",
        "forensic_with_source_ip",
        "forensic_with_delivery_result",
        "forensic_with_authentication_results",
        "forensic_with_original_mail_from",
        "forensic_with_original_rcpt_to",
        "forensic_with_dkim_details",
    ):
        result[key] = int((forensic or {}).get(key) or 0)
    return result


async def list_forensic_reports(
    company_id: int, *, start: Any, end: Any, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        """SELECT id,feedback_type,user_agent,report_version,arrival_at,source_ip,reported_domain,
        delivery_result,auth_failure,authentication_results,original_mail_from,original_rcpt_to,
        dkim_domain,dkim_selector,identity_alignment,created_at
        FROM dmarc_forensic_reports WHERE company_id=%s
        AND COALESCE(arrival_at,created_at) >= %s AND COALESCE(arrival_at,created_at) < %s
        ORDER BY COALESCE(arrival_at,created_at) DESC,id DESC LIMIT %s OFFSET %s""",
        (company_id, start, end, min(max(limit, 1), 250), max(offset, 0)),
    )


async def list_records(
    company_id: int,
    *,
    start: Any,
    end: Any,
    limit: int = 50,
    offset: int = 0,
    domain: str | None = None,
    disposition: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["r.company_id=%s", "p.date_begin < %s", "p.date_end >= %s"]
    params: list[Any] = [company_id, end, start]
    if domain:
        clauses.append("r.header_from=%s")
        params.append(domain)
    if disposition:
        clauses.append("r.disposition=%s")
        params.append(disposition)
    params.extend([min(max(limit, 1), 250), max(offset, 0)])
    where = " AND ".join(clauses)
    # nosec B608 – `where` is built exclusively from hardcoded literal strings;
    # all user-supplied values are passed as parameterised placeholders (%s).
    query = (
        "SELECT r.*,p.report_id external_report_id,p.date_begin,p.date_end"
        " FROM dmarc_records r JOIN dmarc_reports p ON p.id=r.report_id"
        " WHERE "
        + where  # nosec B608
        + " ORDER BY p.date_begin DESC,r.id DESC LIMIT %s OFFSET %s"
    )
    return await db.fetch_all(query, tuple(params))


async def get_record(company_id: int, record_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT r.*,p.report_id external_report_id FROM dmarc_records r JOIN dmarc_reports p ON p.id=r.report_id WHERE r.company_id=%s AND r.id=%s",
        (company_id, record_id),
    )
