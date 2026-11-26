"""RMM maintenance windows and alert snoozes.

A maintenance window says "planned work is happening here, don't alert". While
one covers a device:

* its checks still run and record results (the asset and checks pages show the
  real state), but hold their actions: no ticket, script or Chocolatey install
  (:func:`app.services.rmm_checks.record_result`). If a check is still failing
  when the window ends, its actions run on its next result, so nothing that
  stays broken is lost.
* event log rules raise no ticket for it (:mod:`app.services.rmm_event_log_rules`).

A window covers every company (super admins only), one company or one device,
and either all checks or a single check. It is one-off (a UTC start and end)
or weekly (days of the week, a local start time and a duration in a time zone,
which may run past midnight). Snoozing a device or one of its checks from the
asset page creates a one-off window flagged as a snooze; ended snoozes are
tidied away after :data:`SNOOZE_RETENTION`.

Lookups run for every check result, so the enabled windows are cached for
:data:`CACHE_SECONDS` and the cache is cleared whenever a window changes.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.repositories import rmm_maintenance as repo

SCHEDULE_TYPES = {"once": "One-off", "weekly": "Every week"}
WEEKDAYS: tuple[tuple[str, str], ...] = (
    ("mon", "Mon"), ("tue", "Tue"), ("wed", "Wed"), ("thu", "Thu"), ("fri", "Fri"), ("sat", "Sat"), ("sun", "Sun"),
)
MAX_NAME_LENGTH = 255
MAX_REASON_LENGTH = 2000
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 7 * 24 * 60
MAX_ONCE_DAYS = 90
SNOOZE_CHOICES: tuple[tuple[int, str], ...] = (
    (60, "1 hour"), (240, "4 hours"), (480, "8 hours"), (1440, "1 day"), (4320, "3 days"),
)
SNOOZE_RETENTION = timedelta(days=30)
CACHE_SECONDS = 30.0

_cache: dict[str, Any] = {"at": 0.0, "windows": None}


class WindowError(Exception):
    """The window's form has problems; ``errors`` maps field names to messages."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(errors.values()))
        self.errors = errors


@dataclass
class Coverage:
    """The windows covering a device right now."""

    windows: list[dict[str, Any]] = field(default_factory=list)
    until: datetime | None = None

    def __bool__(self) -> bool:
        return bool(self.windows)

    @property
    def names(self) -> str:
        return ", ".join(str(window.get("name") or "Maintenance") for window in self.windows)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str) and value.strip():
        try:
            return _naive_utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _zone(name: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def parse_weekdays(value: Any) -> list[str]:
    if isinstance(value, str):
        items: Iterable[Any] = value.split(",")
    elif isinstance(value, Iterable):
        items = value
    else:
        items = ()
    wanted = {str(item).strip().lower()[:3] for item in items}
    return [key for key, _label in WEEKDAYS if key in wanted]


def _parse_time(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1][:2].isdigit():
        return None
    hour, minute = int(parts[0]), int(parts[1][:2])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return hour, minute


# --------------------------------------------------------------------------- #
# When a window is active
# --------------------------------------------------------------------------- #


def _weekly_occurrences(window: Mapping[str, Any], around: datetime) -> list[tuple[datetime, datetime]]:
    """Weekly occurrences (UTC start, end) that could cover ``around`` or start soon after."""

    days = parse_weekdays(window.get("weekdays"))
    start = _parse_time(window.get("start_time"))
    duration = int(window.get("duration_minutes") or 0)
    if not days or start is None or duration <= 0:
        return []
    zone = _zone(window.get("timezone"))
    local_now = around.replace(tzinfo=timezone.utc).astimezone(zone)
    look_back = duration // (24 * 60) + 1
    occurrences = []
    for offset in range(-look_back, 8):
        day: date = local_now.date() + timedelta(days=offset)
        if WEEKDAYS[day.weekday()][0] not in days:
            continue
        local_start = datetime(day.year, day.month, day.day, start[0], start[1], tzinfo=zone)
        utc_start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
        occurrences.append((utc_start, utc_start + timedelta(minutes=duration)))
    return occurrences


def active_until(window: Mapping[str, Any], now: datetime | None = None) -> datetime | None:
    """When the window's current occurrence ends (UTC), or ``None`` when it is not active."""

    if not window.get("is_enabled", True):
        return None
    now = now or _utcnow()
    if window.get("schedule_type") == "weekly":
        ends = [end for start, end in _weekly_occurrences(window, now) if start <= now < end]
        return max(ends) if ends else None
    starts_at, ends_at = _naive_utc(window.get("starts_at")), _naive_utc(window.get("ends_at"))
    if starts_at is None or ends_at is None or not (starts_at <= now < ends_at):
        return None
    return ends_at


def next_start(window: Mapping[str, Any], now: datetime | None = None) -> datetime | None:
    """When the window next starts (UTC), if it is not active and starts again."""

    if not window.get("is_enabled", True):
        return None
    now = now or _utcnow()
    if window.get("schedule_type") == "weekly":
        starts = [start for start, _end in _weekly_occurrences(window, now) if start > now]
        return min(starts) if starts else None
    starts_at = _naive_utc(window.get("starts_at"))
    return starts_at if starts_at is not None and starts_at > now else None


def state(window: Mapping[str, Any], now: datetime | None = None) -> str:
    """``active``, ``upcoming``, ``recurring`` (weekly, between occurrences), ``ended`` or ``off``."""

    now = now or _utcnow()
    if not window.get("is_enabled", True):
        return "off"
    if active_until(window, now):
        return "active"
    if window.get("schedule_type") == "weekly":
        return "recurring"
    return "upcoming" if next_start(window, now) else "ended"


def covers(window: Mapping[str, Any], *, company_id: Any, asset_id: Any, check_id: Any = None) -> bool:
    """Whether the window's scope reaches this device (and check, when given)."""

    if window.get("company_id") is not None and (company_id is None or int(window["company_id"]) != int(company_id)):
        return False
    if window.get("asset_id") is not None and (asset_id is None or int(window["asset_id"]) != int(asset_id)):
        return False
    if window.get("check_id") is not None and (check_id is None or int(window["check_id"]) != int(check_id)):
        return False
    return True


async def _enabled_windows() -> list[dict[str, Any]]:
    now = time.monotonic()
    if _cache["windows"] is None or now - float(_cache["at"]) > CACHE_SECONDS:
        _cache["windows"] = await repo.list_enabled()
        _cache["at"] = now
    return list(_cache["windows"])


def clear_cache() -> None:
    _cache["windows"] = None
    _cache["at"] = 0.0


async def coverage(*, company_id: Any, asset_id: Any, check_id: Any = None,
                   now: datetime | None = None) -> Coverage:
    """The windows holding alerts for a device (and check) right now."""

    now = now or _utcnow()
    found = Coverage()
    for window in await _enabled_windows():
        if not covers(window, company_id=company_id, asset_id=asset_id, check_id=check_id):
            continue
        until = active_until(window, now)
        if until is None:
            continue
        found.windows.append(window)
        found.until = until if found.until is None else max(found.until, until)
    return found


async def agent_coverage(agent: Mapping[str, Any], check_id: Any = None, now: datetime | None = None) -> Coverage:
    return await coverage(company_id=agent.get("company_id"), asset_id=agent.get("asset_id"),
                          check_id=check_id, now=now)


def held_message(found: Coverage) -> str:
    until = f" until {found.until.isoformat(timespec='minutes')} UTC" if found.until else ""
    return f"Actions held: the device is in maintenance ({found.names}){until}."


# --------------------------------------------------------------------------- #
# Describing windows
# --------------------------------------------------------------------------- #


def _format_minutes(minutes: int) -> str:
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return f"{days} day{'s' if days != 1 else ''}"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if minutes > 60:
        return f"{minutes // 60} h {minutes % 60} min"
    return f"{minutes} minutes"


def describe_schedule(window: Mapping[str, Any]) -> str:
    if window.get("schedule_type") == "weekly":
        days = parse_weekdays(window.get("weekdays"))
        labels = dict(WEEKDAYS)
        if len(days) == 7:
            when = "Every day"
        elif days == ["mon", "tue", "wed", "thu", "fri"]:
            when = "Weekdays"
        elif days == ["sat", "sun"]:
            when = "Weekends"
        else:
            when = "Every " + ", ".join(labels[day] for day in days)
        return (f"{when} from {window.get('start_time') or '?'} for "
                f"{_format_minutes(int(window.get('duration_minutes') or 0))} ({window.get('timezone') or 'UTC'})")
    starts_at, ends_at = _naive_utc(window.get("starts_at")), _naive_utc(window.get("ends_at"))
    if starts_at and ends_at:
        return f"From {starts_at.isoformat(timespec='minutes')} to {ends_at.isoformat(timespec='minutes')} UTC"
    return "One-off"


def describe_scope(window: Mapping[str, Any]) -> str:
    if window.get("asset_id") is not None:
        where = f"Device: {window.get('asset_name') or 'Asset ' + str(window['asset_id'])}"
    elif window.get("company_id") is not None:
        where = f"Company: {window.get('company_name') or window['company_id']}"
    else:
        where = "Every company"
    if window.get("check_id") is not None:
        where += f" · Check: {window.get('check_name') or window['check_id']}"
    else:
        where += " · All checks"
    return where


def summarise(window: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or _utcnow()
    current = state(window, now)
    return {
        **dict(window),
        "state": current,
        "active_until": active_until(window, now),
        "next_start": next_start(window, now),
        "schedule_label": describe_schedule(window),
        "scope_label": describe_scope(window),
        "weekday_list": parse_weekdays(window.get("weekdays")),
    }


def can_edit(window: Mapping[str, Any], *, company_id: int, is_super_admin: bool) -> bool:
    if window.get("company_id") is None:
        return is_super_admin
    return int(window["company_id"]) == int(company_id)


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _local_to_utc(value: Any, zone: ZoneInfo) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _enabled(form: Mapping[str, Any]) -> bool:
    """The editor posts a hidden ``0`` before the checkbox's ``1``; any true value wins."""

    raw = form.getlist("is_enabled") if hasattr(form, "getlist") else [form.get("is_enabled")]
    raw = [value for value in raw if value is not None]
    if not raw:
        return True
    return any(str(value).lower() in {"1", "true", "on", "yes"} for value in raw)


def parse_form(form: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Validate the editor's fields. Raises :class:`WindowError`.

    Scope (company, device and check) is checked by the caller, which knows
    which company the technician is working in."""

    now = now or _utcnow()
    errors: dict[str, str] = {}
    name = " ".join(str(form.get("name") or "").split())[:MAX_NAME_LENGTH]
    if not name:
        errors["name"] = "Give the window a name, such as “Monthly patching”."
    reason = str(form.get("reason") or "").strip()[:MAX_REASON_LENGTH] or None
    schedule_type = str(form.get("schedule_type") or "once")
    if schedule_type not in SCHEDULE_TYPES:
        errors["schedule_type"] = "Choose one-off or every week."
        schedule_type = "once"
    tz_name = str(form.get("timezone") or "UTC").strip() or "UTC"
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        errors["timezone"] = "Choose a time zone from the list."
        zone, tz_name = ZoneInfo("UTC"), "UTC"
    values: dict[str, Any] = {
        "name": name, "reason": reason, "schedule_type": schedule_type, "timezone": tz_name,
        "starts_at": None, "ends_at": None, "weekdays": None, "start_time": None, "duration_minutes": None,
        "is_enabled": _enabled(form),
        "is_snooze": False,
    }
    if schedule_type == "once":
        starts_at = _local_to_utc(form.get("starts_at"), zone)
        ends_at = _local_to_utc(form.get("ends_at"), zone)
        if starts_at is None:
            errors["starts_at"] = "Enter when the maintenance starts."
        if ends_at is None:
            errors["ends_at"] = "Enter when the maintenance ends."
        if starts_at and ends_at:
            if ends_at <= starts_at:
                errors["ends_at"] = "The end must be after the start."
            elif ends_at - starts_at > timedelta(days=MAX_ONCE_DAYS):
                errors["ends_at"] = f"A one-off window can last up to {MAX_ONCE_DAYS} days."
            elif ends_at <= now and not form.get("id"):
                errors["ends_at"] = "The end is in the past. Choose a time still to come."
        values.update(starts_at=starts_at, ends_at=ends_at)
    else:
        days = parse_weekdays(form.getlist("weekdays") if hasattr(form, "getlist") else form.get("weekdays"))
        if not days:
            errors["weekdays"] = "Pick at least one day."
        start = _parse_time(form.get("start_time"))
        if start is None:
            errors["start_time"] = "Enter a start time such as 22:00."
        duration = _int_or_none(form.get("duration_minutes"))
        if duration is None or not (MIN_DURATION_MINUTES <= duration <= MAX_DURATION_MINUTES):
            errors["duration_minutes"] = (
                f"Enter how long it lasts, between {MIN_DURATION_MINUTES} minutes and 7 days."
            )
        values.update(
            weekdays=",".join(days) or None,
            start_time=f"{start[0]:02d}:{start[1]:02d}" if start else None,
            duration_minutes=duration,
        )
    if errors:
        raise WindowError(errors)
    return values


def snooze_values(*, name: str, company_id: int, asset_id: int, check_id: int | None, minutes: int,
                  reason: str | None, now: datetime | None = None) -> dict[str, Any]:
    allowed = {value for value, _label in SNOOZE_CHOICES}
    if minutes not in allowed:
        raise WindowError({"minutes": "Choose how long to snooze for."})
    now = now or _utcnow()
    return {
        "name": name[:MAX_NAME_LENGTH], "reason": (reason or "").strip()[:MAX_REASON_LENGTH] or None,
        "company_id": company_id, "asset_id": asset_id, "check_id": check_id, "schedule_type": "once",
        "starts_at": now, "ends_at": now + timedelta(minutes=minutes), "weekdays": None, "start_time": None,
        "duration_minutes": None, "timezone": "UTC", "is_snooze": True, "is_enabled": True,
    }


async def create(values: dict[str, Any], *, user_id: int | None) -> int:
    window_id = await repo.create(values, created_by_user_id=user_id)
    clear_cache()
    return window_id


async def update(window_id: int, values: dict[str, Any]) -> None:
    await repo.update(window_id, values)
    clear_cache()


async def end_now(window_id: int) -> None:
    await repo.end_now(window_id, _utcnow())
    clear_cache()


async def delete(window_id: int) -> None:
    await repo.delete(window_id)
    clear_cache()


async def tick() -> None:
    """Tidy away snoozes that ended a while ago (run on the RMM automation tick)."""

    if await repo.delete_ended_snoozes(_utcnow() - SNOOZE_RETENTION):
        clear_cache()


def group_for_page(windows: Iterable[Mapping[str, Any]], now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    now = now or _utcnow()
    groups: dict[str, list[dict[str, Any]]] = {"active": [], "upcoming": [], "recurring": [], "ended": []}
    for window in windows:
        summary = summarise(window, now)
        key = summary["state"]
        groups["ended" if key == "off" else key].append(summary)
    groups["active"].sort(key=lambda item: item["active_until"] or now)
    groups["upcoming"].sort(key=lambda item: item["next_start"] or now)
    groups["recurring"].sort(key=lambda item: (item["next_start"] or datetime.max, item["name"].casefold()))
    return groups
