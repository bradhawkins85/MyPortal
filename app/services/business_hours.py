"""Business hours: global and per-company opening schedules.

A company without its own schedule inherits the global schedule. When no
global schedule has been configured, every time is treated as open so that
automations and SLA timers keep their pre-existing behaviour.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

from app.repositories import business_hours as business_hours_repo

WEEKDAYS: tuple[tuple[str, str], ...] = (
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
    ("sat", "Saturday"),
    ("sun", "Sunday"),
)
WEEKDAY_KEYS = tuple(key for key, _ in WEEKDAYS)

DEFAULT_WEEKLY_HOURS: dict[str, list[dict[str, str]]] = {
    key: ([{"start": "08:30", "end": "17:00"}] if index < 5 else [])
    for index, key in enumerate(WEEKDAY_KEYS)
}

AUTOMATION_MODES = ("pause", "skip")
AUTOMATION_SOURCES = ("company", "global")

_SEARCH_DAYS = 400
_CACHE_SECONDS = 60.0
_cache: dict[int | None, tuple[float, "BusinessHoursSchedule | None"]] = {}


@dataclass(frozen=True)
class BusinessHoursSchedule:
    timezone_name: str
    weekly: dict[int, tuple[tuple[int, int], ...]]
    closures: frozenset[date] = field(default_factory=frozenset)
    source: str = "global"
    company_id: int | None = None

    @property
    def zone(self) -> ZoneInfo:
        return resolve_zone(self.timezone_name)

    def has_open_time(self) -> bool:
        return any(self.weekly.get(day) for day in range(7))


def resolve_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "UTC").strip() or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_valid_timezone(name: str | None) -> bool:
    try:
        ZoneInfo(str(name or "").strip())
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def parse_clock(value: Any) -> int | None:
    """Return minutes after midnight for ``HH:MM`` (``24:00`` allowed)."""

    text = str(value or "").strip()
    if not text or ":" not in text:
        return None
    hours_text, minutes_text = text.split(":", 1)
    try:
        hours, minutes = int(hours_text), int(minutes_text[:2])
    except ValueError:
        return None
    if not (0 <= minutes < 60) or not (0 <= hours <= 24):
        return None
    total = hours * 60 + minutes
    return total if total <= 24 * 60 else None


def format_clock(minutes: int) -> str:
    """Format minutes after midnight; end-of-day (1440) is stored as ``24:00``."""

    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def normalise_weekly_hours(raw: Any) -> dict[int, tuple[tuple[int, int], ...]]:
    """Convert stored weekly JSON into sorted, merged minute windows."""

    weekly: dict[int, tuple[tuple[int, int], ...]] = {}
    source = raw if isinstance(raw, Mapping) else {}
    for index, key in enumerate(WEEKDAY_KEYS):
        windows: list[tuple[int, int]] = []
        entries = source.get(key) or []
        if not isinstance(entries, list):
            entries = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            start, end = parse_clock(entry.get("start")), parse_clock(entry.get("end"))
            if start is None or end is None or end <= start:
                continue
            windows.append((start, end))
        windows.sort()
        merged: list[tuple[int, int]] = []
        for start, end in windows:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        weekly[index] = tuple(merged)
    return weekly


def weekly_hours_to_json(weekly: Mapping[int, tuple[tuple[int, int], ...]]) -> dict[str, list[dict[str, str]]]:
    return {
        key: [
            {"start": format_clock(start), "end": format_clock(end)}
            for start, end in weekly.get(index, ())
        ]
        for index, key in enumerate(WEEKDAY_KEYS)
    }


def build_schedule(
    record: Mapping[str, Any],
    closures: set[date] | frozenset[date] = frozenset(),
    *,
    source: str = "global",
) -> BusinessHoursSchedule:
    return BusinessHoursSchedule(
        timezone_name=str(record.get("timezone") or "UTC"),
        weekly=normalise_weekly_hours(record.get("weekly_hours")),
        closures=frozenset(closures),
        source=source,
        company_id=record.get("company_id"),
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _local_moment(day: date, minutes: int, zone: ZoneInfo) -> datetime:
    if minutes >= 24 * 60:
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    return datetime.combine(day, time(minutes // 60, minutes % 60), tzinfo=zone).astimezone(timezone.utc)


def _day_intervals(schedule: BusinessHoursSchedule, day: date) -> list[tuple[datetime, datetime]]:
    if day in schedule.closures:
        return []
    zone = schedule.zone
    intervals = []
    for start, end in schedule.weekly.get(day.weekday(), ()):
        start_at, end_at = _local_moment(day, start, zone), _local_moment(day, end, zone)
        if end_at > start_at:
            intervals.append((start_at, end_at))
    return intervals


def iter_open_intervals(
    schedule: BusinessHoursSchedule, start: datetime, *, max_days: int = _SEARCH_DAYS
) -> Iterator[tuple[datetime, datetime]]:
    """Yield open UTC intervals ending after ``start`` in chronological order."""

    start = _utc(start)
    day = start.astimezone(schedule.zone).date() - timedelta(days=1)
    pending: tuple[datetime, datetime] | None = None
    for _ in range(max_days + 2):
        for interval_start, interval_end in _day_intervals(schedule, day):
            if interval_end <= start:
                continue
            # Join windows that touch across midnight (e.g. 24-hour days).
            if pending and interval_start <= pending[1]:
                pending = (pending[0], max(pending[1], interval_end))
                continue
            if pending:
                yield pending
            pending = (interval_start, interval_end)
        day += timedelta(days=1)
    if pending:
        yield pending


def is_open(schedule: BusinessHoursSchedule | None, at: datetime | None = None) -> bool:
    if schedule is None:
        return True
    moment = _utc(at or datetime.now(timezone.utc))
    for interval_start, interval_end in iter_open_intervals(schedule, moment, max_days=2):
        return interval_start <= moment < interval_end
    return False


def next_open(schedule: BusinessHoursSchedule | None, at: datetime | None = None) -> datetime | None:
    """Return ``at`` when open, otherwise the next opening time (``None`` if never)."""

    moment = _utc(at or datetime.now(timezone.utc))
    if schedule is None:
        return moment
    for interval_start, _ in iter_open_intervals(schedule, moment):
        return max(interval_start, moment)
    return None


def next_close(schedule: BusinessHoursSchedule | None, at: datetime | None = None) -> datetime | None:
    if schedule is None:
        return None
    moment = _utc(at or datetime.now(timezone.utc))
    for interval_start, interval_end in iter_open_intervals(schedule, moment, max_days=2):
        if interval_start <= moment:
            return interval_end
        return None
    return None


def business_seconds_between(
    schedule: BusinessHoursSchedule | None, start: datetime, end: datetime
) -> float:
    start, end = _utc(start), _utc(end)
    if end <= start:
        return 0.0
    if schedule is None:
        return (end - start).total_seconds()
    total = 0.0
    max_days = (end - start).days + 3
    for interval_start, interval_end in iter_open_intervals(schedule, start, max_days=max_days):
        if interval_start >= end:
            break
        total += (min(interval_end, end) - max(interval_start, start)).total_seconds()
    return total


def add_business_seconds(
    schedule: BusinessHoursSchedule | None, start: datetime, seconds: float
) -> datetime:
    """Return the moment ``seconds`` of open time after ``start``."""

    start = _utc(start)
    if schedule is None or not schedule.has_open_time():
        return start + timedelta(seconds=seconds)
    remaining = max(0.0, float(seconds))
    last_end = start
    for interval_start, interval_end in iter_open_intervals(schedule, start, max_days=3660):
        begin = max(interval_start, start)
        available = (interval_end - begin).total_seconds()
        if remaining <= available:
            return begin + timedelta(seconds=remaining)
        remaining -= available
        last_end = interval_end
    return last_end + timedelta(seconds=remaining)


def invalidate_cache(company_id: int | None = None) -> None:
    if company_id is None:
        _cache.clear()
    else:
        _cache.pop(company_id, None)


async def _load_schedule(company_id: int | None) -> BusinessHoursSchedule | None:
    global_record = await business_hours_repo.get_schedule(None)
    global_closures = (
        {c["closure_date"] for c in await business_hours_repo.list_closures(None) if c.get("closure_date")}
        if global_record or company_id is not None
        else set()
    )
    if company_id is not None:
        company_record = await business_hours_repo.get_schedule(company_id)
        if company_record:
            closures = {
                c["closure_date"]
                for c in await business_hours_repo.list_closures(company_id)
                if c.get("closure_date")
            }
            if company_record.get("include_global_closures"):
                closures |= global_closures
            return build_schedule(company_record, closures, source="company")
    if not global_record:
        return None
    return build_schedule(global_record, global_closures, source="global")


async def get_schedule(company_id: int | None = None) -> BusinessHoursSchedule | None:
    """Return the effective schedule for a company (or the global schedule)."""

    cached = _cache.get(company_id)
    now = monotonic()
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    try:
        schedule = await _load_schedule(company_id)
    except Exception as exc:  # pragma: no cover - business hours must not break callers
        logger.warning("Failed to load business hours", company_id=company_id, error=str(exc))
        return None
    _cache[company_id] = (now, schedule)
    return schedule


def describe(schedule: BusinessHoursSchedule | None, at: datetime | None = None) -> dict[str, Any]:
    """Summarise open/closed state for templates and automation context."""

    moment = _utc(at or datetime.now(timezone.utc))
    if schedule is None:
        return {
            "configured": False,
            "open": True,
            "label": "No business hours set",
            "source": None,
            "timezone": None,
            "local_time": None,
            "next_change_at": None,
        }
    open_now = is_open(schedule, moment)
    next_change = next_close(schedule, moment) if open_now else next_open(schedule, moment)
    local_now = moment.astimezone(schedule.zone)
    return {
        "configured": True,
        "open": open_now,
        "label": "Open" if open_now else "Closed",
        "source": schedule.source,
        "timezone": schedule.timezone_name,
        "local_time": local_now.strftime("%a %H:%M"),
        "next_change_at": next_change,
        "next_change_local": (
            next_change.astimezone(schedule.zone).strftime("%a %d %b %H:%M") if next_change else None
        ),
    }


async def status_for_company(company_id: int | None, at: datetime | None = None) -> dict[str, Any]:
    return describe(await get_schedule(company_id), at)


def company_id_from_context(context: Mapping[str, Any] | None) -> int | None:
    if not isinstance(context, Mapping):
        return None
    candidates: list[Any] = []
    ticket = context.get("ticket")
    if isinstance(ticket, Mapping):
        candidates.append(ticket.get("company_id"))
    company = context.get("company")
    if isinstance(company, Mapping):
        candidates.append(company.get("id"))
    candidates.append(context.get("company_id"))
    for value in candidates:
        try:
            company_id = int(value)
        except (TypeError, ValueError):
            continue
        if company_id > 0:
            return company_id
    return None


def ticket_id_from_context(context: Mapping[str, Any] | None) -> int | None:
    ticket = context.get("ticket") if isinstance(context, Mapping) else None
    if not isinstance(ticket, Mapping):
        return None
    try:
        return int(ticket.get("id"))
    except (TypeError, ValueError):
        return None


async def automation_gate(
    automation: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    *,
    at: datetime | None = None,
) -> dict[str, Any] | None:
    """Return ``None`` when the automation may run now.

    Otherwise returns ``{"action": "skip"|"pause", "run_after": datetime|None}``
    describing what the automation's business hours setting requires.
    """

    mode = str(automation.get("business_hours_mode") or "").strip().lower()
    if mode not in AUTOMATION_MODES:
        return None
    source = str(automation.get("business_hours_source") or "company").strip().lower()
    company_id = company_id_from_context(context) if source == "company" else None
    schedule = await get_schedule(company_id)
    moment = _utc(at or datetime.now(timezone.utc))
    if is_open(schedule, moment):
        return None
    run_after = next_open(schedule, moment) if mode == "pause" else None
    if mode == "pause" and run_after is None:
        mode = "skip"
    return {
        "action": mode,
        "run_after": run_after,
        "source": schedule.source if schedule else None,
        "company_id": company_id,
    }


async def business_hours_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Open/closed flags exposed to automation filters and templates."""

    company_id = company_id_from_context(context)
    global_status = await status_for_company(None)
    company_status = await status_for_company(company_id) if company_id else global_status
    return {
        "company_open": bool(company_status.get("open")),
        "global_open": bool(global_status.get("open")),
        "company": company_status,
        "global": global_status,
    }


def _form_clock(minutes: int) -> str:
    return "00:00" if minutes >= 24 * 60 else format_clock(minutes)


def weekly_form_rows(weekly_json: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Rows for the business hours form (up to two windows per day)."""

    weekly = normalise_weekly_hours(weekly_json if weekly_json is not None else DEFAULT_WEEKLY_HOURS)
    rows = []
    for index, (key, label) in enumerate(WEEKDAYS):
        windows = list(weekly.get(index, ()))
        first = windows[0] if windows else None
        second = windows[1] if len(windows) > 1 else None
        rows.append(
            {
                "key": key,
                "label": label,
                "open": bool(windows),
                "start": format_clock(first[0]) if first else "08:30",
                "end": _form_clock(first[1]) if first else "17:00",
                "start2": format_clock(second[0]) if second else "",
                "end2": _form_clock(second[1]) if second else "",
            }
        )
    return rows


def parse_weekly_form(form: Mapping[str, Any]) -> tuple[dict[str, list[dict[str, str]]], str | None]:
    """Parse ``day_<key>_*`` form fields into weekly hours JSON."""

    weekly: dict[str, list[dict[str, str]]] = {}
    for key, label in WEEKDAYS:
        weekly[key] = []
        if not form.get(f"day_{key}_open"):
            continue
        for suffix in ("", "2"):
            start_raw = str(form.get(f"day_{key}_start{suffix}") or "").strip()
            end_raw = str(form.get(f"day_{key}_end{suffix}") or "").strip()
            if suffix and not start_raw and not end_raw:
                continue
            start, end = parse_clock(start_raw), parse_clock(end_raw)
            if end == 0:
                end = 24 * 60  # a closing time of midnight means end of day
            if start is None or end is None or end <= start:
                return weekly, f"{label}: enter a start time before the end time."
            weekly[key].append({"start": format_clock(start), "end": format_clock(end)})
        if len(weekly[key]) == 2:
            first, second = weekly[key]
            if parse_clock(second["start"]) < parse_clock(first["end"]):
                return weekly, f"{label}: the second period must start after the first ends."
    return weekly, None


def timezone_options() -> list[str]:
    from zoneinfo import available_timezones

    return sorted(zone for zone in available_timezones() if "/" in zone or zone == "UTC")
