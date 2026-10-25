"""Monthly health checks: chosen computers with the RMM agent get one a month, at a random day and time.

Which computers are checked is set by monthly check groups
(``ai_health_check_groups``): a computer is checked when it matches any
enabled group. A group applies to one company, or to every company (super
admins), and narrows that to the devices matching the same nested AND/OR
device rules as RMM checks (:mod:`app.services.rmm_targeting`), for example
VIP company workstations or Windows 11 computers. The default "All computers"
group checks every computer until admins narrow it or turn it off.

Each matching asset with an active RMM agent in its own company has a row in
``ai_health_check_schedules`` with a random time in a calendar month (UTC):
the first within the next 30 days, then one in each following month, so
checks spread evenly over the month instead of all starting together. On the
RMM automation tick (:func:`tick`):

* new agents get their first time (:func:`first_slot`);
* a due computer whose tray app is online gets an ordinary health check
  (:func:`app.services.ai_health_check.start_health_check`,
  ``run_source = 'scheduled'``) and a random time in the next month
  (:func:`next_slot`);
* one whose tray app is offline is tried again every
  :data:`OFFLINE_RETRY` until its month ends, when it moves on to the next
  month; one with a health check already running waits :data:`BUSY_RETRY`;
* a month in which the computer already had a completed health check (a
  technician ran one) is skipped.

Assets that lose their RMM agent, move company, are archived or stop
matching every group lose their schedule; they get a new one if they match
again. Turn monthly checks off
with ``HEALTH_CHECK_MONTHLY_ENABLED=false``; nothing runs while no language
model is configured.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import get_settings
from app.core.logging import log_info, log_warning
from app.repositories import ai_health_check_groups as groups_repo
from app.repositories import ai_health_check_schedules as repo
from app.repositories import ai_health_checks as checks_repo
from app.repositories import assets as assets_repo
from app.repositories import rmm as rmm_repo
from app.repositories import tray as tray_repo
from app.services import ai_health_check
from app.services import ai_troubleshooter
from app.services import audit as audit_service
from app.services import rmm_device_targeting, rmm_targeting
from app.services import tray as tray_service
from app.services.component_availability import get_component_availability

RUN_SOURCE = ai_health_check.RUN_SOURCE_SCHEDULED
FIRST_WINDOW = timedelta(days=30)
OFFLINE_RETRY = timedelta(minutes=30)
BUSY_RETRY = timedelta(hours=1)
SEED_INTERVAL = timedelta(minutes=10)
STARTS_PER_TICK = 5
SCOPES = ("company", "global")
OPERATOR_LABELS = {
    "equals": "is", "not_equals": "is not", "contains": "contains", "not_contains": "does not contain",
    "starts_with": "starts with", "ends_with": "ends with", "in": "is one of", "not_in": "is not one of",
    "is_set": "is set", "is_not_set": "is not set", "gt": "is greater than", "gte": "is at least",
    "lt": "is less than", "lte": "is at most", "between": "is between", "version_gte": "is version at least",
    "version_lte": "is version at most", "in_subnet": "is in subnet", "not_in_subnet": "is outside subnet",
}

# When computers were last checked against the groups (seeding is throttled).
_state: dict[str, datetime | None] = {"last_seeded": None}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _as_datetime(value: Any) -> datetime | None:
    return ai_troubleshooter._as_naive_utc(value)


def enabled() -> bool:
    return bool(getattr(get_settings(), "health_check_monthly_enabled", True))


def month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month_start(value: datetime) -> datetime:
    start = month_start(value)
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


def random_time(start: datetime, end: datetime) -> datetime:
    """A random second in ``[start, end)``."""

    seconds = int((end - start).total_seconds())
    return start + timedelta(seconds=secrets.randbelow(seconds) if seconds > 0 else 0)


def first_slot(now: datetime) -> tuple[datetime, datetime]:
    """``(run_at, window_ends_at)`` for a computer that has no schedule yet."""

    run_at = random_time(now, now + FIRST_WINDOW)
    return run_at, next_month_start(run_at)


def next_slot(window_ends_at: datetime, now: datetime) -> tuple[datetime, datetime]:
    """A random time in the month after the window, or in the current month when that has passed."""

    start = window_ends_at if window_ends_at > month_start(now) else month_start(now)
    end = next_month_start(start)
    return random_time(max(start, now), end), end


def month_label(window_ends_at: datetime) -> str:
    return (window_ends_at - timedelta(seconds=1)).strftime("%B %Y")


async def schedule_for(asset_id: int) -> dict[str, Any] | None:
    """The asset's monthly health check, for its asset page."""

    if not enabled():
        return None
    try:
        return await repo.get(int(asset_id))
    except Exception as exc:  # noqa: BLE001 - the asset page still renders
        log_warning("Could not read the monthly health check schedule", asset_id=asset_id, error=str(exc)[:300])
        return None


# --------------------------------------------------------------------------- #
# Groups: which computers get monthly checks
# --------------------------------------------------------------------------- #


class GroupError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def describe_rules(rules: Any) -> str:
    """Device rules in words, such as "Asset operating system contains Windows 11 and Company VIP status is Yes"."""

    if rules is None:
        return "Every computer"
    labels = {field["key"]: field["label"] for field in rmm_targeting.field_catalogue()}

    def text(node: Any, top: bool = False) -> str:
        if not isinstance(node, Mapping):
            return "?"
        if isinstance(node.get("children"), list):
            parts = [text(child) for child in node["children"]]
            joined = (" and " if node.get("operator") != "or" else " or ").join(parts) or "nothing"
            return joined if top or len(parts) == 1 else f"({joined})"
        label = labels.get(str(node.get("field")), str(node.get("field")))
        if node.get("key"):
            label += f" “{node['key']}”"
        words = f"{label} {OPERATOR_LABELS.get(str(node.get('operator')), str(node.get('operator')))}"
        value = node.get("value")
        if isinstance(value, bool):
            value = "Yes" if value else "No"
        if isinstance(value, list):
            value = " and ".join(str(item) for item in value) if node.get("operator") == "between" else ", ".join(
                str(item) for item in value)
        return words if value is None or value == "" else f"{words} {value}"

    described = text(rules, top=True)
    return described if len(described) <= 400 else described[:399].rstrip() + "…"


def can_edit_group(group: Mapping[str, Any], *, company_id: int, is_super_admin: bool) -> bool:
    if group.get("company_id") is None:
        return is_super_admin
    return int(group["company_id"]) == int(company_id)


async def save_group(
    *, group_id: int | None, company_id: int, form: Mapping[str, Any], user_id: int | None, is_super_admin: bool,
) -> int:
    """Create or update a group from its form (``scope``, ``name``, ``target_mode``, ``target_rules``, ``is_enabled``).

    ``company_id`` is the company the admin is working in.
    """

    errors: dict[str, str] = {}
    name = str(form.get("name") or "").strip()[:255]
    if not name:
        errors["name"] = "Give the group a name."
    scope = str(form.get("scope") or "company")
    group_company: int | None = int(company_id)
    if scope == "global":
        if not is_super_admin:
            errors["scope"] = "Only super admins can add groups for every company."
        group_company = None
    elif scope not in SCOPES:
        errors["scope"] = "Choose who the group applies to."
    if str(form.get("target_mode") or "all") not in {"all", "rules"}:
        errors["target_mode"] = "Choose every computer or device rules."
    rules = rmm_device_targeting.parse_rules(form, errors)
    if errors:
        raise GroupError(errors)
    values = {"name": name, "company_id": group_company, "target_rules": rules,
              "is_enabled": str(form.get("is_enabled") or "").lower() in {"1", "on", "true", "yes"}, "user_id": user_id}
    if group_id is None:
        return await groups_repo.create(**values)
    await groups_repo.update(int(group_id), **values)
    return int(group_id)


async def eligible_agent_ids(agents: list[dict[str, Any]], groups: list[dict[str, Any]] | None = None) -> set[int]:
    """The agents that match at least one enabled group in their company."""

    groups = await groups_repo.list_enabled() if groups is None else [g for g in groups if g.get("is_enabled")]
    matched: set[int] = set()
    for group in groups:
        candidates = [
            agent for agent in agents if int(agent["id"]) not in matched
            and (group.get("company_id") is None or int(group["company_id"]) == int(agent.get("company_id") or 0))
        ]
        if not candidates:
            continue
        if group.get("target_rules") is None:
            matched.update(int(agent["id"]) for agent in candidates)
        else:
            found = await rmm_device_targeting.filter_agents(candidates, group["target_rules"])
            matched.update(int(agent["id"]) for agent in found)
    return matched


async def seed(now: datetime) -> int:
    """Give a random first time to every computer in a group that has none."""

    agents = await repo.list_unscheduled_agents()
    eligible = await eligible_agent_ids(agents) if agents else set()
    added = 0
    for row in agents:
        if int(row["id"]) not in eligible:
            continue
        run_at, window_ends_at = first_slot(now)
        await repo.create(asset_id=int(row["asset_id"]), company_id=int(row["company_id"]),
                          next_run_at=run_at, window_ends_at=window_ends_at)
        added += 1
    if added:
        log_info("Monthly health checks scheduled", assets=added)
    return added


async def resync() -> int:
    """After groups change: drop the schedules of computers no group matches any more.

    Newly matching computers are scheduled on the next tick. Returns how many were dropped.
    """

    _state["last_seeded"] = None
    agents = await repo.list_scheduled_agents()
    eligible = await eligible_agent_ids(agents) if agents else set()
    dropped = 0
    for agent in agents:
        if int(agent["id"]) not in eligible:
            await repo.delete(int(agent["asset_id"]))
            dropped += 1
    if dropped:
        log_info("Monthly health checks unscheduled for computers outside every group", assets=dropped)
    return dropped


async def _online(device: Mapping[str, Any]) -> bool:
    uid = str(device.get("device_uid") or "")
    if uid and tray_service.is_device_connected(uid):
        return True
    full = await tray_repo.get_device_by_id(int(device["id"]))
    return bool(full) and tray_service.websocket_recently_seen(dict(full))


async def _run_due(row: Mapping[str, Any], now: datetime) -> bool:
    """Start the due health check if it can run now. Returns True when one started."""

    asset_id = int(row["asset_id"])
    window_ends_at = _as_datetime(row.get("window_ends_at")) or next_month_start(now)
    asset = await assets_repo.get_asset_by_id(asset_id)
    company_id = int((asset or {}).get("company_id") or 0)
    agent = await rmm_repo.get_asset_agent(asset_id)
    if (not asset or asset.get("archived_at") or company_id != int(row.get("company_id") or 0)
            or not agent or int(agent.get("company_id") or 0) != company_id
            or int(agent["id"]) not in await eligible_agent_ids([dict(agent)])):
        await repo.delete(asset_id)
        return False
    if now >= window_ends_at:
        run_at, ends = next_slot(window_ends_at, now)
        await repo.reschedule(asset_id, next_run_at=run_at, window_ends_at=ends,
                              result=f"Not run in {month_label(window_ends_at)}: the computer's tray app was not online.")
        return False
    done = await checks_repo.last_completed_since(asset_id, month_start(window_ends_at - timedelta(seconds=1)))
    if done:
        run_at, ends = next_slot(window_ends_at, now)
        await repo.reschedule(asset_id, next_run_at=run_at, window_ends_at=ends,
                              result=f"Skipped {month_label(window_ends_at)}: health check #{done['id']} already ran.",
                              health_check_id=int(done["id"]))
        return False
    active = await checks_repo.get_active_for_asset(asset_id)
    if active:
        await repo.postpone(asset_id, next_run_at=min(now + BUSY_RETRY, window_ends_at),
                            result=f"Waiting for health check #{active['id']} to finish.")
        return False
    device = await checks_repo.find_tray_device_for_asset(asset_id)
    if not device or not await _online(device):
        await repo.postpone(asset_id, next_run_at=min(now + OFFLINE_RETRY, window_ends_at),
                            result="Waiting for the computer's tray app to come online.")
        return False
    run_at, ends = next_slot(window_ends_at, now)
    try:
        started = await ai_health_check.start_health_check(
            asset=asset, device=device, requested_by=None, run_source=RUN_SOURCE,
        )
    except ai_health_check.HealthCheckError as exc:
        await repo.reschedule(asset_id, next_run_at=run_at, window_ends_at=ends,
                              result=f"Not started in {month_label(window_ends_at)}: {exc}")
        return False
    await repo.reschedule(asset_id, next_run_at=run_at, window_ends_at=ends, started_at=now,
                          health_check_id=int(started["id"]), result=f"Started health check #{started['id']}.")
    try:
        await audit_service.log_action(
            action="health_check.start", user_id=None, entity_type="ai_health_check", entity_id=int(started["id"]),
            new_value={"asset_id": asset_id, "run_source": RUN_SOURCE, "allow_pawnio": started["allow_pawnio"]},
        )
    except Exception as exc:  # noqa: BLE001 - the check has started either way
        log_warning("Could not audit a monthly health check", health_check_id=started["id"], error=str(exc)[:300])
    return True


async def tick(now: datetime | None = None) -> int:
    """Schedule new computers and start the monthly health checks that are due."""

    if not enabled() or not get_component_availability().feature_pack_available("health_check"):
        return 0
    if not tray_service.build_troubleshoot_llm_config()["model"]:
        return 0
    now = now or _utcnow()
    last_seeded = _state["last_seeded"]
    if last_seeded is None or now - last_seeded >= SEED_INTERVAL or now < last_seeded:
        _state["last_seeded"] = now
        await seed(now)
    started = 0
    for row in await repo.list_due(now, limit=STARTS_PER_TICK * 4):
        if started >= STARTS_PER_TICK:
            break
        try:
            started += 1 if await _run_due(row, now) else 0
        except Exception as exc:  # noqa: BLE001 - one computer must not stop the others
            log_warning("Could not start a monthly health check", asset_id=row.get("asset_id"), error=str(exc)[:300])
            run_at, ends = next_slot(_as_datetime(row.get("window_ends_at")) or now, now)
            await repo.reschedule(int(row["asset_id"]), next_run_at=run_at, window_ends_at=ends,
                                  result=f"Could not start: {str(exc)[:300]}")
    return started


__all__ = [
    "GroupError",
    "RUN_SOURCE",
    "can_edit_group",
    "describe_rules",
    "eligible_agent_ids",
    "enabled",
    "first_slot",
    "next_slot",
    "resync",
    "save_group",
    "schedule_for",
    "seed",
    "tick",
]
