"""Fail-closed, last-mile authorisation for every outbound call."""
from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
import os
from zoneinfo import ZoneInfo

import phonenumbers

from app.core.database import db


class DialDenied(PermissionError):
    """The current entitlement or endpoint policy does not permit a call."""


def _clock(value: object, default: time) -> time:
    if isinstance(value, time):
        return value
    try:
        return time.fromisoformat(str(value))
    except (TypeError, ValueError):
        return default


async def authorize_attempt(attempt: dict, *, global_limit: int, tenant_limit: int,
                            now: datetime | None = None) -> None:
    """Re-verify mutable policy immediately before originate; any uncertainty denies."""
    endpoint_id, company_id = attempt.get("endpoint_id"), attempt.get("company_id")
    if not endpoint_id or not company_id:
        raise DialDenied("missing endpoint ownership")
    row = await db.fetch_one(
        "SELECT e.*,s.status subscription_status,s.start_date,s.end_date,c.name category_name "
        "FROM voice_monitor_endpoints e JOIN subscriptions s ON s.id=e.subscription_id "
        "JOIN subscription_categories c ON c.id=s.subscription_category_id "
        "WHERE e.id=%s AND e.company_id=%s", (endpoint_id, company_id),
    )
    if not row or not row.get("enabled") or not row.get("consent_granted") or row.get("consent_revoked_at"):
        raise DialDenied("destination consent is absent or revoked")
    if not row.get("consent_actor_id") or not row.get("consent_at") or not row.get("consent_policy_version"):
        raise DialDenied("consent evidence is incomplete")
    if not row.get("caller_id_verified"):
        raise DialDenied("caller ID is not verified")
    allowlist = {int(code) for code in os.getenv("VOICE_MONITOR_ALLOWED_COUNTRY_CODES", "").split(",") if code.strip().isdigit()}
    try:
        country_code = phonenumbers.parse(str(row["destination_e164"]), None).country_code
    except Exception as exc:
        raise DialDenied("destination country cannot be verified") from exc
    if not allowlist or country_code not in allowlist:
        raise DialDenied("destination country is not allowlisted")
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(str(row.get("timezone") or "UTC")))
    start, end = _clock(row.get("quiet_hours_start"), time(20)), _clock(row.get("quiet_hours_end"), time(8))
    quiet = start <= local.time() < end if start < end else local.time() >= start or local.time() < end
    if quiet:
        raise DialDenied("quiet hours are active")
    counts = await db.fetch_one(
        "SELECT COUNT(*) total,SUM(CASE WHEN company_id=%s THEN 1 ELSE 0 END) tenant "
        "FROM voice_monitor_attempts WHERE completed_at IS NULL AND outcome_status IN ('dialing','answered')",
        (company_id,),
    ) or {}
    if int(counts.get("total") or 0) >= global_limit or int(counts.get("tenant") or 0) >= tenant_limit:
        raise DialDenied("call concurrency limit reached")
    daily = await db.fetch_one(
        "SELECT COUNT(*) count FROM voice_monitor_attempts WHERE endpoint_id=%s AND queued_at>=UTC_DATE()",
        (endpoint_id,),
    ) or {}
    if int(daily.get("count") or 0) > int(row.get("daily_attempt_limit") or 0):
        raise DialDenied("daily attempt limit reached")
    cap = int(row.get("monetary_cap_minor") or 0)
    spend = await db.fetch_one(
        "SELECT COALESCE(SUM(attempt_units*attempt_price+connected_minutes*minute_price+"
        "transcription_units*transcription_price),0) amount FROM voice_monitor_usage_ledger "
        "WHERE company_id=%s AND occurred_at>=UTC_DATE()", (company_id,),
    ) or {}
    if cap <= 0 or Decimal(str(spend.get("amount") or 0)) * 100 >= cap:
        raise DialDenied("monetary usage cap reached or unavailable")
