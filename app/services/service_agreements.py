"""Service contracts and agreements.

An agreement says what a company's contract covers:

* **Managed (unlimited)**: all support time is covered.
* **Hours each period**: a number of hours each month, quarter or year
  (periods are calendar months and quarters; years run from the start date).
* **Prepaid block**: a number of hours for the whole agreement, drawn down
  until used up.

An agreement can be limited to some labour types (for example remote support
only); otherwise it covers every labour type. Usage is worked out from ticket
time entries on the company's tickets, so agreements never change invoicing:
they show coverage, drawdown, the balance left, overage beyond the allowance
and, with a fee, the effective hourly rate against the labour types' rates.

When several agreements could cover the same time entry, unlimited agreements
cover it first, then hours each period, then prepaid blocks, so block hours
are only used when nothing else covers the work.

A background job raises the ``agreements.usage_threshold`` automation event
when an agreement's usage reaches its alert percentage, ``agreements.exhausted``
when its allowance is used up and ``agreements.expiring`` 30 days before it
ends, each once per period.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from loguru import logger

from app.repositories import service_agreements as repo

AGREEMENT_TYPES = {
    "unlimited": ("Managed (unlimited)", "All support time is covered."),
    "hours": ("Hours each period", "A number of hours every month, quarter or year."),
    "block": ("Prepaid block", "A block of hours used up over the agreement."),
}
PERIODS = {"monthly": "Monthly", "quarterly": "Quarterly", "annual": "Yearly"}
TYPE_PRIORITY = {"unlimited": 0, "hours": 1, "block": 2}
EXPIRY_NOTICE_DAYS = 30
MAX_HOURS = 100_000
JOB_INTERVAL_SECONDS = 3600
JOB_STARTUP_DELAY_SECONDS = 120
HISTORY_PERIODS = 12


class AgreementError(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(errors.values()))
        self.errors = errors


@dataclass
class Usage:
    period_start: date
    period_end: date  # exclusive
    allowance_minutes: int | None
    covered_minutes: int = 0
    overage_minutes: int = 0
    value: float = 0.0
    entries: list[dict[str, Any]] = field(default_factory=list)

    @property
    def used_minutes(self) -> int:
        return self.covered_minutes + self.overage_minutes

    @property
    def percent(self) -> int | None:
        if not self.allowance_minutes:
            return None
        return round(100 * self.used_minutes / self.allowance_minutes)

    @property
    def remaining_minutes(self) -> int | None:
        if self.allowance_minutes is None:
            return None
        return max(0, self.allowance_minutes - self.used_minutes)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def hours_text(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    hours = minutes / 60
    return f"{hours:.1f}".rstrip("0").rstrip(".") + " h"


# --------------------------------------------------------------------------- #
# Periods
# --------------------------------------------------------------------------- #


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    days_in_month = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, days_in_month))


def period_for(agreement: Mapping[str, Any], on: date) -> tuple[date, date]:
    """The period containing ``on``: (start, end exclusive), clipped to the agreement's dates."""

    start_date: date = agreement["start_date"]
    end_date: date | None = agreement.get("end_date")
    kind = agreement.get("agreement_type")
    if kind == "block":
        start, end = start_date, (end_date + timedelta(days=1)) if end_date else date.max
    else:
        period = agreement.get("period") or "monthly"
        if period == "monthly":
            start = date(on.year, on.month, 1)
            end = _add_months(start, 1)
        elif period == "quarterly":
            start = date(on.year, 3 * ((on.month - 1) // 3) + 1, 1)
            end = _add_months(start, 3)
        else:
            years = on.year - start_date.year - (1 if (on.month, on.day) < (start_date.month, start_date.day) else 0)
            start = _add_months(start_date, 12 * max(0, years))
            end = _add_months(start_date, 12 * (max(0, years) + 1))
        start = max(start, start_date)
        if end_date is not None:
            end = min(end, end_date + timedelta(days=1))
    return start, end


def recent_periods(agreement: Mapping[str, Any], *, today: date | None = None, count: int = HISTORY_PERIODS
                   ) -> list[tuple[date, date]]:
    """The current period and the ones before it, newest first."""

    today = today or _today()
    last = min(today, agreement["end_date"]) if agreement.get("end_date") else today
    if last < agreement["start_date"]:
        return []
    periods: list[tuple[date, date]] = []
    cursor = last
    while len(periods) < count and cursor >= agreement["start_date"]:
        start, end = period_for(agreement, cursor)
        if periods and periods[-1][0] == start:
            break
        periods.append((start, end))
        cursor = start - timedelta(days=1)
    return periods


def status(agreement: Mapping[str, Any], *, today: date | None = None) -> str:
    """``active``, ``upcoming``, ``expiring`` (within 30 days), ``ended`` or ``cancelled``."""

    today = today or _today()
    if agreement.get("is_cancelled"):
        return "cancelled"
    if agreement["start_date"] > today:
        return "upcoming"
    end = agreement.get("end_date")
    if end is not None and end < today:
        return "ended"
    if end is not None and (end - today).days <= EXPIRY_NOTICE_DAYS:
        return "expiring"
    return "active"


def _active_on(agreement: Mapping[str, Any], day: date) -> bool:
    if agreement.get("is_cancelled") or day < agreement["start_date"]:
        return False
    return agreement.get("end_date") is None or day <= agreement["end_date"]


def _covers_labour(agreement: Mapping[str, Any], labour_type_id: Any) -> bool:
    allowed = agreement.get("labour_type_ids") or []
    return not allowed or (labour_type_id is not None and int(labour_type_id) in allowed)


def _allowance_minutes(agreement: Mapping[str, Any]) -> int | None:
    if agreement.get("agreement_type") == "unlimited" or agreement.get("hours_allowance") is None:
        return None
    return int(round(float(agreement["hours_allowance"]) * 60))


def _entry_date(entry: Mapping[str, Any]) -> date | None:
    value = entry.get("created_at")
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #


def assign_entries(agreements: Sequence[Mapping[str, Any]], entries: Iterable[Mapping[str, Any]]
                   ) -> dict[tuple[int, date], Usage]:
    """Spread time entries across a company's agreements, period by period.

    Entries are taken oldest first. Each goes to the first agreement (by
    :data:`TYPE_PRIORITY`, then oldest) active on its date that covers its
    labour type and still has allowance left; when every candidate is used
    up it counts as overage on the last one. Returns usage per (agreement,
    period start)."""

    ordered = sorted((agreement for agreement in agreements if not agreement.get("is_cancelled")),
                     key=lambda item: (TYPE_PRIORITY.get(item.get("agreement_type"), 9), item["start_date"], item["id"]))
    usage: dict[tuple[int, date], Usage] = {}

    def bucket(agreement: Mapping[str, Any], day: date) -> Usage:
        start, end = period_for(agreement, day)
        key = (int(agreement["id"]), start)
        if key not in usage:
            usage[key] = Usage(period_start=start, period_end=end, allowance_minutes=_allowance_minutes(agreement))
        return usage[key]

    for entry in sorted(entries, key=lambda item: (str(item.get("created_at")), int(item.get("id") or 0))):
        day = _entry_date(entry)
        minutes = int(entry.get("minutes_spent") or 0)
        if day is None or minutes <= 0:
            continue
        candidates = [agreement for agreement in ordered
                      if int(agreement["company_id"]) == int(entry.get("company_id") or 0)
                      and _active_on(agreement, day) and _covers_labour(agreement, entry.get("labour_type_id"))]
        if not candidates:
            continue
        chosen = next((agreement for agreement in candidates
                       if (bucket(agreement, day).remaining_minutes is None or bucket(agreement, day).remaining_minutes > 0)),
                      candidates[-1])
        target = bucket(chosen, day)
        remaining = target.remaining_minutes
        covered = minutes if remaining is None else min(minutes, remaining)
        target.covered_minutes += covered
        target.overage_minutes += minutes - covered
        rate = float(entry.get("labour_rate") or 0)
        target.value += covered / 60 * rate
        target.entries.append({**dict(entry), "covered_minutes": covered, "overage_minutes": minutes - covered})
    return usage


async def company_usage(agreements: Sequence[Mapping[str, Any]], *, today: date | None = None,
                        periods: int = 1) -> dict[tuple[int, date], Usage]:
    """Usage of the agreements over their recent periods (the current one by default)."""

    today = today or _today()
    windows = [window for agreement in agreements if not agreement.get("is_cancelled")
               for window in recent_periods(agreement, today=today, count=periods)]
    if not windows:
        return {}
    # Earlier periods of a block or another agreement can hold the allowance used
    # before the window, so read from the earliest start any agreement needs.
    starts = [window[0] for window in windows]
    starts += [agreement["start_date"] for agreement in agreements if agreement.get("agreement_type") == "block"]
    start = min(starts)
    end = max(window[1] for window in windows)
    end = min(end, today + timedelta(days=1))
    company_ids = sorted({int(agreement["company_id"]) for agreement in agreements})
    entries = await repo.list_time_entries(company_ids, datetime.combine(start, time.min),
                                           datetime.combine(end, time.min))
    return assign_entries(agreements, entries)


def summary(agreement: Mapping[str, Any], usage: Usage | None, *, today: date | None = None) -> dict[str, Any]:
    """An agreement as the list shows it, with its current period's usage."""

    today = today or _today()
    current = status(agreement, today=today)
    start, end = period_for(agreement, max(agreement["start_date"], min(today, agreement.get("end_date") or today)))
    if usage is None:
        usage = Usage(period_start=start, period_end=end, allowance_minutes=_allowance_minutes(agreement))
    label, _help = AGREEMENT_TYPES.get(agreement.get("agreement_type"), ("Agreement", ""))
    fee = agreement.get("fee_amount")
    covered_hours = usage.covered_minutes / 60
    flags = []
    if usage.percent is not None and usage.percent >= 100:
        flags.append("exhausted")
    elif usage.percent is not None and usage.percent >= int(agreement.get("alert_percent") or 80):
        flags.append("near_limit")
    if usage.overage_minutes:
        flags.append("overage")
    return {
        **dict(agreement),
        "status": current,
        "type_label": label,
        "period_label": "Whole agreement" if agreement.get("agreement_type") == "block"
        else PERIODS.get(agreement.get("period"), "Monthly"),
        "usage": usage,
        "used_text": hours_text(usage.used_minutes),
        "covered_text": hours_text(usage.covered_minutes),
        "allowance_text": hours_text(usage.allowance_minutes) if usage.allowance_minutes is not None else "Unlimited",
        "remaining_text": hours_text(usage.remaining_minutes),
        "overage_text": hours_text(usage.overage_minutes),
        "percent": usage.percent,
        "meter": min(100, usage.percent or 0),
        "value": round(usage.value, 2),
        "effective_rate": round(float(fee) / covered_hours, 2) if fee and covered_hours else None,
        "flags": flags,
        "period_start": usage.period_start,
        "period_end": None if usage.period_end == date.max else usage.period_end - timedelta(days=1),
    }


async def list_summaries(*, company_id: int | None = None, today: date | None = None) -> list[dict[str, Any]]:
    today = today or _today()
    agreements = await repo.list_agreements(company_id=company_id)
    by_company: dict[int, list[dict[str, Any]]] = {}
    for agreement in agreements:
        by_company.setdefault(int(agreement["company_id"]), []).append(agreement)
    summaries = []
    for items in by_company.values():
        usage = await company_usage(items, today=today)
        for agreement in items:
            on = max(agreement["start_date"], min(today, agreement.get("end_date") or today))
            start, _end = period_for(agreement, on)
            summaries.append(summary(agreement, usage.get((int(agreement["id"]), start)), today=today))
    return summaries


async def agreement_detail(agreement: Mapping[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """An agreement with its recent periods and the current period's time entries."""

    today = today or _today()
    siblings = await repo.list_agreements(company_id=int(agreement["company_id"]))
    usage = await company_usage(siblings, today=today, periods=HISTORY_PERIODS)
    history = []
    for start, end in recent_periods(agreement, today=today):
        item = usage.get((int(agreement["id"]), start)) or Usage(
            period_start=start, period_end=end, allowance_minutes=_allowance_minutes(agreement))
        history.append({
            "start": start, "end": None if end == date.max else end - timedelta(days=1),
            "covered_text": hours_text(item.covered_minutes),
            "overage_text": hours_text(item.overage_minutes), "percent": item.percent,
            "value": round(item.value, 2), "entries": len(item.entries),
        })
    on = max(agreement["start_date"], min(today, agreement.get("end_date") or today))
    start, _end = period_for(agreement, on)
    current = summary(agreement, usage.get((int(agreement["id"]), start)), today=today)
    return {"agreement": current, "history": history,
            "entries": list(reversed(current["usage"].entries))[:200]}


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #


def _decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def parse_form(form: Any, *, valid_labour_type_ids: Iterable[int]) -> dict[str, Any]:
    errors: dict[str, str] = {}
    name = " ".join(str(form.get("name") or "").split())[:255]
    if not name:
        errors["name"] = "Give the agreement a name, such as “Managed services 2027”."
    kind = str(form.get("agreement_type") or "hours")
    if kind not in AGREEMENT_TYPES:
        errors["agreement_type"] = "Choose what the agreement covers."
        kind = "hours"
    hours = None
    if kind != "unlimited":
        hours = _decimal(form.get("hours_allowance"))
        if hours is None or hours <= 0 or hours > MAX_HOURS:
            errors["hours_allowance"] = "Enter how many hours are included, such as 10."
    period = str(form.get("period") or "monthly")
    if kind == "hours" and period not in PERIODS:
        errors["period"] = "Choose monthly, quarterly or yearly."
    if kind != "hours":
        period = "monthly"
    start = _parse_date(form.get("start_date"))
    if start is None:
        errors["start_date"] = "Enter when the agreement starts."
    end = _parse_date(form.get("end_date")) if str(form.get("end_date") or "").strip() else None
    if str(form.get("end_date") or "").strip() and end is None:
        errors["end_date"] = "Enter a valid end date, or leave it blank for no end."
    elif start and end and end < start:
        errors["end_date"] = "The end date must be on or after the start date."
    fee = _decimal(form.get("fee_amount"))
    if str(form.get("fee_amount") or "").strip() and (fee is None or fee < 0):
        errors["fee_amount"] = "Enter the fee as an amount, such as 1500."
    try:
        alert = int(str(form.get("alert_percent") or "80"))
    except ValueError:
        alert = -1
    if not 1 <= alert <= 100:
        errors["alert_percent"] = "Choose a percentage from 1 to 100."
    allowed = {int(value) for value in valid_labour_type_ids}
    raw_types = form.getlist("labour_type_ids") if hasattr(form, "getlist") else form.get("labour_type_ids") or []
    labour_type_ids = []
    for value in raw_types:
        try:
            labour_type_id = int(str(value))
        except ValueError:
            continue
        if labour_type_id not in allowed:
            errors["labour_type_ids"] = "Choose labour types from the list."
        else:
            labour_type_ids.append(labour_type_id)
    if errors:
        raise AgreementError(errors)
    return {
        "name": name, "agreement_type": kind, "hours_allowance": hours, "period": period,
        "start_date": start, "end_date": end, "fee_amount": fee, "alert_percent": alert,
        "notes": str(form.get("notes") or "").strip()[:4000] or None,
        "is_cancelled": str(form.get("is_cancelled") or "").lower() in {"1", "on", "true", "yes"},
        "labour_type_ids": sorted(set(labour_type_ids)),
    }


def describe(values: Mapping[str, Any], labour_names: Mapping[int, str] | None = None) -> str:
    kind = values.get("agreement_type")
    labour = ""
    ids = values.get("labour_type_ids") or []
    if ids and labour_names:
        labour = " for " + ", ".join(labour_names.get(int(item), str(item)) for item in ids) + " work"
    if kind == "unlimited":
        return f"All support time{labour} is covered."
    hours = values.get("hours_allowance")
    amount = f"{float(hours):g} hours" if hours is not None else "Some hours"
    if kind == "block":
        return f"{amount}{labour} are prepaid and used up over the agreement."
    unit = {"monthly": "month", "quarterly": "quarter", "annual": "year"}.get(values.get("period"), "month")
    return f"{amount}{labour} are included each {unit}."


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #

ALERT_EVENTS = {
    "usage_threshold": "agreements.usage_threshold",
    "exhausted": "agreements.exhausted",
    "expiring": "agreements.expiring",
}


async def check_alerts(*, today: date | None = None) -> int:
    """Fire each agreement's due alerts once. Returns how many fired."""

    from app.services import automations as automations_service

    today = today or _today()
    fired = 0
    for item in await list_summaries(today=today):
        if item["status"] in ("cancelled", "ended", "upcoming"):
            continue
        due: list[tuple[str, date]] = []
        if "exhausted" in item["flags"]:
            due.append(("exhausted", item["period_start"]))
        elif "near_limit" in item["flags"]:
            due.append(("usage_threshold", item["period_start"]))
        if item["status"] == "expiring" and item.get("end_date"):
            due.append(("expiring", item["end_date"]))
        for kind, period_start in due:
            if not await repo.record_alert(int(item["id"]), kind, period_start):
                continue
            context = {"agreement": {
                "id": item["id"], "name": item["name"], "company_id": item["company_id"],
                "company_name": item.get("company_name"), "type": item["type_label"],
                "used": item["used_text"], "allowance": item["allowance_text"], "remaining": item["remaining_text"],
                "overage": item["overage_text"], "percent": item["percent"],
                "end_date": item["end_date"].isoformat() if item.get("end_date") else None,
                "url": f"/admin/agreements/{item['id']}",
            }}
            try:
                await automations_service.handle_event(ALERT_EVENTS[kind], context)
                fired += 1
            except Exception as exc:  # noqa: BLE001 - one failing automation must not stop the rest
                logger.warning("Agreement alert automation failed for {}: {}", item["id"], exc)
    return fired


async def alert_job() -> None:
    from app.services.singleton_jobs import singleton_run

    @singleton_run("service_agreement_alerts", ttl_seconds=JOB_INTERVAL_SECONDS)
    async def _run_once() -> None:
        await check_alerts()

    await asyncio.sleep(JOB_STARTUP_DELAY_SECONDS)
    while True:
        try:
            await _run_once()
        except Exception as exc:  # pragma: no cover - retried on the next pass
            logger.warning("Service agreement alert job failed: {}", exc)
        await asyncio.sleep(JOB_INTERVAL_SECONDS)
