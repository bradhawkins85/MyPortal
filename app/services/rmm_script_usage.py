"""Where an RMM script is used, so admins can see whether it is still needed.

:func:`script_usage` gathers every place that runs a script for the Scripts
page's *Usage* section: schedules, onboarding steps, checks (as the test or as
an action), event log rules, script result rules (as the follow-up script),
ticket automations, tray menu items, health check fixes and remote control
activation. With it come how often the script ran recently and when it last
ran, so a script that is linked nowhere and has not run is easy to spot.

``company_id`` limits what is shown to links that reach that company's
devices (every-company links included); ``None`` shows every company's, for
super admins.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from app.repositories import rmm_script_usage as usage_repo
from app.services import rmm_action_steps

RECENT_DAYS = 90
ALL_COMPANIES = "All companies"

# (key, heading, one-line explanation) in the order the page lists them.
GROUPS: tuple[tuple[str, str, str], ...] = (
    ("schedules", "Schedules", "Runs on a timetable."),
    ("onboarding", "Onboarding", "Runs when a new device enrols."),
    ("checks", "Checks", "Tests a device, or runs when a check fails."),
    ("event_log_rules", "Event log rules", "Runs when a matching event is logged."),
    ("result_rules", "Script result rules", "Runs after another script finishes."),
    ("automations", "Ticket automations", "Runs on a ticket's devices."),
    ("tray_menus", "Tray menus", "Offered to users in the tray app."),
    ("health_checks", "Health check fixes", "Runs when a health check finds a problem."),
    ("remote_control", "Remote control", "Switches on remote control on a device."),
)
_PROVIDER_LABELS = {"rustdesk": "RustDesk", "meshcentral": "MeshCentral"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _decode(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _visible(owner_company_id: Any, company_id: int | None) -> bool:
    return company_id is None or owner_company_id is None or int(owner_company_id) == int(company_id)


def _scope(row: Mapping[str, Any]) -> str:
    """Plain-English reach of a link: a device, a company or every company."""

    if row.get("asset_id"):
        device = row.get("asset_name") or f"Device {row['asset_id']}"
        company = row.get("asset_company_name") or row.get("company_name")
        return f"{device} ({company})" if company else device
    if row.get("company_id"):
        return row.get("company_name") or f"Company {row['company_id']}"
    return ALL_COMPANIES


def _item(name: str, scope: str, enabled: bool | None, *, detail: str = "", href: str = "",
          when: Any = None, when_label: str = "") -> dict[str, Any]:
    return {"name": name, "scope": scope, "enabled": enabled, "detail": detail, "href": href,
            "when": _iso(when), "when_label": when_label}


def _uses_script(steps: Iterable[Mapping[str, Any]], script_id: int) -> list[str]:
    """Labels of the actions in ``steps`` that run ``script_id``."""

    labels = []
    for step in steps:
        if step.get("type") not in rmm_action_steps.SCRIPT_TYPES:
            continue
        try:
            if int(step.get("script_id") or 0) == script_id:
                labels.append(rmm_action_steps.TYPES[step["type"]].lower())
        except (TypeError, ValueError):
            continue
    return labels


def _action_steps(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    stored = row.get("actions_json")
    data = dict(row)
    data["actions"] = None if stored is None else _decode(stored, [])
    if data["actions"] is not None and not isinstance(data["actions"], list):
        data["actions"] = []
    return rmm_action_steps.steps(data)


def tray_items_for(nodes: Any, script_id: int) -> list[str]:
    """Labels of tray menu items (at any depth) that run ``script_id``."""

    labels: list[str] = []
    if not isinstance(nodes, list):
        return labels
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        if str(node.get("type") or "").strip().lower() == "rmm_script":
            try:
                if int(node.get("script_id") or 0) == script_id:
                    labels.append(str(node.get("label") or "").strip() or "Untitled item")
            except (TypeError, ValueError):
                # A malformed script_id names no script; its children are still checked below.
                pass
        labels.extend(tray_items_for(node.get("children"), script_id))
    return labels


def automation_references(payload: Any, action_module: Any) -> list[Mapping[str, Any]]:
    """The RMM script action payloads of one automation."""

    payload = _decode(payload, {})
    found: list[Mapping[str, Any]] = []
    if str(action_module or "") == "rmm-script" and isinstance(payload, Mapping):
        found.append(payload)
    actions = payload.get("actions") if isinstance(payload, Mapping) else None
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, Mapping) and str(action.get("module") or "").strip() == "rmm-script":
                inner = action.get("payload")
                if isinstance(inner, Mapping):
                    found.append(inner)
    return found


def reference_matches(reference: Any, script: Mapping[str, Any]) -> bool:
    """True when an automation's script reference (ID, Gitea path or name) names ``script``."""

    text = str(reference if reference is not None else "").strip()
    if not text:
        return False
    if text.isdigit():
        return int(text) == int(script["id"])
    wanted = text.casefold()
    return wanted in {str(script.get("path") or "").casefold(), str(script.get("name") or "").casefold()}


async def _schedules(script_id: int, company_id: int | None) -> list[dict[str, Any]]:
    items = []
    for row in await usage_repo.list_schedules(script_id):
        if not _visible(row.get("company_id"), company_id):
            continue
        detail = "Runs once" if row.get("run_once_at") else (f"Cron {row['cron']}" if row.get("cron") else "")
        if row.get("target_mode") == "assets":
            count = len(_decode(row.get("asset_ids_json"), []) or [])
            detail = (detail + " · " if detail else "") + f"{count} chosen device{'' if count == 1 else 's'}"
        enabled = bool(row.get("is_enabled"))
        items.append(_item(row.get("name") or f"Schedule {row['id']}", _scope(row), enabled, detail=detail,
                           href="/rmm/automation", when=row.get("next_run_at") if enabled else None,
                           when_label="Next run"))
    return items


async def _onboarding(script_id: int, company_id: int | None) -> list[dict[str, Any]]:
    return [
        _item(f"Onboarding step {int(row.get('position') or 0) + 1}", _scope(row), True, href="/rmm/automation")
        for row in await usage_repo.list_onboarding_steps(script_id)
        if _visible(row.get("company_id"), company_id)
    ]


async def _checks(script_id: int, company_id: int | None) -> list[dict[str, Any]]:
    items = []
    for row in await usage_repo.list_checks(script_id):
        if not _visible(row.get("owner_company_id"), company_id):
            continue
        uses = []
        if row.get("check_script_id") and int(row["check_script_id"]) == script_id:
            uses.append("tests the device with this script")
        uses += [f"{label} when it fails" for label in _uses_script(_action_steps(row), script_id)]
        if not uses:
            continue
        items.append(_item(row.get("name") or f"Check {row['id']}", _scope(row), bool(row.get("is_enabled")),
                           detail="; ".join(dict.fromkeys(uses)).capitalize(), href=f"/rmm/checks?check={row['id']}"))
    return items


async def _event_log_rules(script_id: int, company_id: int | None) -> list[dict[str, Any]]:
    items = []
    for row in await usage_repo.list_event_log_rules(script_id):
        if not _visible(row.get("owner_company_id"), company_id):
            continue
        uses = _uses_script(_action_steps(row), script_id)
        if not uses:
            continue
        items.append(_item(row.get("name") or f"Rule {row['id']}", _scope(row), bool(row.get("is_enabled")),
                           detail="; ".join(dict.fromkeys(uses)).capitalize() + " when it matches",
                           href=f"/rmm/event-logs/rules?rule={row['id']}", when=row.get("last_matched_at"),
                           when_label="Last matched"))
    return items


async def _result_rules(script_id: int, company_id: int | None) -> list[dict[str, Any]]:
    items = []
    for row in await usage_repo.list_result_rules(script_id):
        if not _visible(row.get("company_id"), company_id):
            continue
        after = row.get("watched_script_name") or ("any script" if row.get("script_id") is None else "a removed script")
        items.append(_item(row.get("name") or f"Rule {row['id']}", _scope(row), bool(row.get("is_enabled")),
                           detail=f"Runs after {after}", href=f"/rmm/scripts/result-rules?rule={row['id']}",
                           when=row.get("last_matched_at"), when_label="Last matched"))
    return items


async def _automations(script: Mapping[str, Any], is_super_admin: bool) -> list[dict[str, Any]]:
    items = []
    for row in await usage_repo.list_script_automations():
        references = automation_references(row.get("action_payload"), row.get("action_module"))
        if not any(reference_matches(payload.get("script"), script) for payload in references):
            continue
        from app.services.automations import TRIGGER_EVENTS

        events = {option["value"]: option["label"] for option in TRIGGER_EVENTS}
        if row.get("kind") == "scheduled":
            kind = "Runs on a schedule"
        else:
            event = str(row.get("trigger_event") or "")
            kind = f"When: {events.get(event, event or 'an event')}"
        items.append(_item(row.get("name") or f"Automation {row['id']}", ALL_COMPANIES,
                           str(row.get("status") or "") == "active", detail=kind,
                           href=f"/admin/automations/{row['id']}/edit" if is_super_admin else ""))
    return items


async def _tray_menus(script_id: int, company_id: int | None, is_super_admin: bool) -> list[dict[str, Any]]:
    items = []
    for row in await usage_repo.list_tray_menus():
        if not _visible(row.get("owner_company_id"), company_id):
            continue
        labels = tray_items_for(_decode(row.get("payload_json"), []), script_id)
        if not labels:
            continue
        if row.get("scope") == "device":
            scope = _scope({"asset_id": row.get("scope_ref_id"), "asset_name": row.get("asset_name"),
                            "company_name": row.get("company_name")})
        elif row.get("scope") == "company":
            scope = _scope({"company_id": row.get("scope_ref_id"), "company_name": row.get("company_name")})
        else:
            scope = ALL_COMPANIES
        items.append(_item(row.get("name") or f"Tray menu {row['id']}", scope, bool(row.get("enabled")),
                           detail="Menu item " + ", ".join(f"“{label}”" for label in labels),
                           href=f"/admin/tray/configurations/{row['id']}/edit" if is_super_admin else ""))
    return items


async def _health_checks(script_id: int, company_id: int | None) -> list[dict[str, Any]]:
    from app.services.health_check_detections import DETECTIONS

    items = []
    for row in await usage_repo.list_health_check_fixes(script_id):
        if not _visible(row.get("company_id"), company_id):
            continue
        key = str(row.get("detection_key") or "")
        label = (DETECTIONS.get(key) or {}).get("label") or key.replace("_", " ").capitalize()
        items.append(_item(label, _scope(row), bool(row.get("is_enabled")), detail="Fixes this detection",
                           href="/health-checks/detections", when=row.get("last_run_at"), when_label="Last ran"))
    return items


async def _remote_control(script_id: int, is_super_admin: bool) -> list[dict[str, Any]]:
    return [
        _item(_PROVIDER_LABELS.get(str(row["provider"]), str(row["provider"])), ALL_COMPANIES,
              bool(row.get("is_enabled")), detail="Activation script",
              href="/rmm/remote-control" if is_super_admin else "")
        for row in await usage_repo.list_remote_control_providers(script_id)
    ]


async def script_usage(script: Mapping[str, Any], *, company_id: int | None, is_super_admin: bool) -> dict[str, Any]:
    """Every link to ``script`` the viewer may see, grouped, with its recent run activity."""

    script_id = int(script["id"])
    found = {
        "schedules": await _schedules(script_id, company_id),
        "onboarding": await _onboarding(script_id, company_id),
        "checks": await _checks(script_id, company_id),
        "event_log_rules": await _event_log_rules(script_id, company_id),
        "result_rules": await _result_rules(script_id, company_id),
        "automations": await _automations(script, is_super_admin),
        "tray_menus": await _tray_menus(script_id, company_id, is_super_admin),
        "health_checks": await _health_checks(script_id, company_id),
        "remote_control": await _remote_control(script_id, is_super_admin),
    }
    groups = [
        {"key": key, "label": label, "help": help_text, "items": found[key]}
        for key, label, help_text in GROUPS
        if found[key]
    ]
    links = sum(len(group["items"]) for group in groups)
    active = sum(1 for group in groups for item in group["items"] if item["enabled"])
    activity = await usage_repo.run_activity(
        script_id, company_id=company_id, since=_utcnow() - timedelta(days=RECENT_DAYS)
    )
    return {
        "groups": groups,
        "links": links,
        "active_links": active,
        "recent_runs": activity["recent_runs"],
        "recent_days": RECENT_DAYS,
        "last_run_at": _iso(activity["last_run_at"]),
        # Nothing runs it and nothing has: a candidate for removal.
        "unused": active == 0 and activity["recent_runs"] == 0,
        "all_companies": company_id is None,
    }
