from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import ceil, floor
import json
from typing import Any, Mapping, Sequence

from app.core.config import get_settings
from app.core.module_capabilities import COMMANDS_BY_MODULE, modules_for_command
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.repositories import webhook_events as webhook_events_repo
from app.services import cron_calendar, modules as modules_service

_CREDENTIAL_EXPIRY_FIELDS: dict[str, str] = {
    "client_secret_expires_at": "Client secret",
    "token_expires_at": "Access token",
}
_KNOWN_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "hudu": ("base_url", "api_key"),
    "imap": ("host", "username", "password"),
    "m365-admin": ("client_id", "client_secret", "tenant_id"),
    "m365-mail": ("tenant_id",),
    "smtp2go": ("api_key", "webhook_secret"),
    "solidtime": ("base_url", "api_token", "organization_id"),
    "syncro": ("base_url", "api_key"),
    "tacticalrmm": ("base_url", "api_key"),
    "trello": ("api_key", "token"),
    "unifi-talk": ("remote_host", "username", "password"),
    "whisperx": ("base_url",),
    "xero": ("client_id", "client_secret", "tenant_id"),
}
_WEBHOOK_MATCH_HINTS: dict[str, tuple[str, ...]] = {
    "huntress": ("huntress",),
    "smtp2go": ("smtp2go",),
    "solidtime": ("solidtime",),
    "trello": ("trello",),
    "uptimekuma": ("uptimekuma", "uptime kuma"),
    "xero": ("xero",),
}
_DEFAULT_CREDENTIAL_WARNING_DAYS = 30
_SAFE_ACTION_URL_PREFIXES = ("/admin/", "/chat/")


def _to_aware_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _string_value(value: Any) -> str:
    return str(value or "").strip()


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _humanise_field(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _module_settings(module: Mapping[str, Any]) -> Mapping[str, Any]:
    settings = module.get("settings")
    return settings if isinstance(settings, Mapping) else {}


def _missing_required_fields(module: Mapping[str, Any]) -> list[str]:
    required = _KNOWN_REQUIRED_FIELDS.get(_string_value(module.get("slug")))
    if not required:
        return []
    settings = _module_settings(module)
    return [_humanise_field(field) for field in required if _is_blank(settings.get(field))]


def _credential_warnings(
    module: Mapping[str, Any], *, now: datetime, warning_window_days: int
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    settings = _module_settings(module)
    for key, label in _CREDENTIAL_EXPIRY_FIELDS.items():
        expires_at = _to_aware_utc(settings.get(key))
        if expires_at is None:
            continue
        delta_days = (expires_at - now).total_seconds() / 86400
        if delta_days > warning_window_days:
            continue
        warnings.append(
            {
                "field": key,
                "label": label,
                "expires_at": expires_at,
                "expires_at_iso": expires_at.isoformat(),
                "days_remaining": ceil(delta_days) if delta_days >= 0 else floor(delta_days),
                "severity": "danger" if delta_days <= 0 else "warning",
                "message": (
                    f"{label} expired"
                    if delta_days <= 0
                    else f"{label} expires in {ceil(delta_days)} day(s)"
                ),
            }
        )
    warnings.sort(key=lambda item: item["expires_at"])
    return warnings


def _event_belongs_to_module(event: Mapping[str, Any], module_slug: str) -> bool:
    metadata = event.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = None
    if isinstance(metadata, Mapping) and _string_value(metadata.get("module_slug")) == module_slug:
        return True
    haystack = " ".join(
        _string_value(event.get(key)).casefold()
        for key in ("name", "target_url", "source_url", "last_error")
    )
    for hint in _WEBHOOK_MATCH_HINTS.get(module_slug, (module_slug, module_slug.replace("-", " "))):
        if hint and hint.casefold() in haystack:
            return True
    return False


def _telemetry_summary(total: int, failures: int) -> dict[str, Any]:
    target_percent = 99
    if total <= 0:
        return {
            "target_percent": target_percent,
            "achieved_percent": None,
            "error_budget_total": 0,
            "error_budget_used": 0,
            "error_budget_overrun": 0,
            "error_budget_remaining": 0,
        }
    budget_total = max(1, ceil(total * (1 - (target_percent / 100))))
    achieved_percent = max(0.0, round(((total - failures) / total) * 100, 1))
    return {
        "target_percent": target_percent,
        "achieved_percent": achieved_percent,
        "error_budget_total": budget_total,
        "error_budget_used": min(failures, budget_total),
        "error_budget_overrun": max(0, failures - budget_total),
        "error_budget_remaining": max(0, budget_total - failures),
    }


def _safe_action_url(value: Any) -> str:
    candidate = _string_value(value)
    if any(candidate.startswith(prefix) for prefix in _SAFE_ACTION_URL_PREFIXES):
        return candidate
    return "/admin/modules"


def _command_matches_registered(command: str, registered: set[str]) -> bool:
    if command in registered:
        return True
    return any(
        item.endswith("*") and command.startswith(item[:-1])
        for item in registered
    )


def _company_label(company_id: Any) -> str:
    if company_id in (None, ""):
        return "All companies"
    return f"Company #{company_id}"


def _tasks_share_scope(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_company = left.get("company_id")
    right_company = right.get("company_id")
    return (
        left_company == right_company
        or left_company is None
        or right_company is None
    )


def _build_task_conflicts(
    tasks: Sequence[Mapping[str, Any]], *, timezone_name: str | None
) -> dict[str, Any]:
    active_tasks = [task for task in tasks if bool(task.get("active", True))]
    graph_rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    related_by_task: dict[int, set[str]] = defaultdict(set)
    conflict_counts: Counter[int] = Counter()
    prepared: list[dict[str, Any]] = []
    command_groups: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)

    for task in active_tasks:
        task_id = int(task.get("id") or 0)
        command = _string_value(task.get("command"))
        owners = sorted(modules_for_command(command))
        next_run = cron_calendar.calculate_next_run(
            dict(task), timezone_name=timezone_name
        )
        prepared_task = {
            "id": task_id,
            "name": _string_value(task.get("name")) or command or f"Task #{task_id}",
            "command": command,
            "company_id": task.get("company_id"),
            "company_name": _company_label(task.get("company_id")),
            "module_slugs": owners,
            "next_run": next_run,
            "next_run_iso": next_run.isoformat() if next_run else None,
        }
        prepared.append(prepared_task)
        command_groups[(task.get("company_id"), command)].append(prepared_task)

    for (company_id, command), group in command_groups.items():
        if len(group) < 2:
            continue
        names = ", ".join(item["name"] for item in group)
        conflicts.append(
            {
                "type": "duplicate_command",
                "severity": "danger",
                "summary": f"Duplicate schedule for {command or 'unknown command'}",
                "detail": f"{names} all target {_company_label(company_id)}.",
            }
        )
        for item in group:
            conflict_counts[item["id"]] += 1

    for index, left in enumerate(prepared):
        for right in prepared[index + 1 :]:
            shared_modules = sorted(set(left["module_slugs"]) & set(right["module_slugs"]))
            if not shared_modules:
                continue
            if not _tasks_share_scope(left, right):
                continue
            related_by_task[left["id"]].add(right["name"])
            related_by_task[right["id"]].add(left["name"])
            if not left["next_run"] or not right["next_run"]:
                continue
            delta_seconds = abs((left["next_run"] - right["next_run"]).total_seconds())
            if delta_seconds > 300:
                continue
            conflicts.append(
                {
                    "type": "schedule_overlap",
                    "severity": "warning",
                    "summary": f"Near-simultaneous {', '.join(shared_modules)} jobs",
                    "detail": (
                        f"{left['name']} and {right['name']} run within "
                        f"{int(delta_seconds // 60)} minute(s) for overlapping scope."
                    ),
                }
            )
            conflict_counts[left["id"]] += 1
            conflict_counts[right["id"]] += 1

    for task in prepared:
        graph_rows.append(
            {
                "id": task["id"],
                "name": task["name"],
                "command": task["command"],
                "company_name": task["company_name"],
                "module_slugs": task["module_slugs"],
                "next_run_iso": task["next_run_iso"],
                "related_tasks": sorted(related_by_task.get(task["id"], set())),
                "conflict_count": conflict_counts.get(task["id"], 0),
            }
        )

    graph_rows.sort(key=lambda item: (item["conflict_count"] == 0, item["name"].casefold()))
    return {"conflicts": conflicts, "graph": graph_rows}


async def build_operations_center(
    modules: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate integration telemetry for the admin operations center.

    When *modules* is omitted this falls back to the runtime module registry so
    callers can either reuse a preloaded module list or request a fresh snapshot.
    """
    module_rows = list(modules) if modules is not None else await modules_service.list_modules()
    tasks = await scheduled_tasks_repo.list_tasks(include_inactive=True)
    recent_runs = await scheduled_tasks_repo.list_recent_runs(limit=200)
    webhook_events = await webhook_events_repo.list_events(limit=500)
    now = datetime.now(timezone.utc)
    app_settings = get_settings()
    warning_window_days = max(
        1,
        int(
            getattr(
                app_settings,
                "integration_credential_warning_days",
                _DEFAULT_CREDENTIAL_WARNING_DAYS,
            )
            or _DEFAULT_CREDENTIAL_WARNING_DAYS
        ),
    )

    task_ids_by_module: dict[str, set[int]] = defaultdict(set)
    for task in tasks:
        if task.get("id") is None:
            continue
        command = _string_value(task.get("command"))
        for module_slug, registered_commands in COMMANDS_BY_MODULE.items():
            if _command_matches_registered(command, registered_commands):
                task_ids_by_module[module_slug].add(int(task["id"]))

    runs_by_task: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for run in recent_runs:
        if run.get("task_id") is not None:
            runs_by_task[int(run["task_id"])].append(run)

    module_health: list[dict[str, Any]] = []
    summary_counts: Counter[str] = Counter()
    credential_alerts: list[dict[str, Any]] = []
    setup_steps: list[dict[str, Any]] = []

    for module in module_rows:
        slug = _string_value(module.get("slug"))
        enabled = bool(module.get("enabled"))
        missing_fields = _missing_required_fields(module)
        warnings = _credential_warnings(
            module, now=now, warning_window_days=warning_window_days
        )
        module_events = [event for event in webhook_events if _event_belongs_to_module(event, slug)]
        task_ids = task_ids_by_module.get(slug, set())
        module_runs = [run for task_id in task_ids for run in runs_by_task.get(task_id, [])]
        run_failures = sum(1 for run in module_runs if _string_value(run.get("status")).lower() == "failed")
        webhook_failures = sum(
            1 for event in module_events if _string_value(event.get("status")).lower() == "failed"
        )
        telemetry_total = len(module_runs) + len(module_events)
        total_failures = run_failures + webhook_failures
        telemetry = _telemetry_summary(telemetry_total, total_failures)

        if not enabled:
            status_key = "disabled"
            status_label = "Disabled"
        elif missing_fields:
            status_key = "setup"
            status_label = "Setup required"
        elif any(item["severity"] == "danger" for item in warnings):
            status_key = "degraded"
            status_label = "Credential expired"
        elif warnings:
            status_key = "warning"
            status_label = "Credential warning"
        elif total_failures:
            status_key = "degraded"
            status_label = "Degraded"
        else:
            status_key = "healthy"
            status_label = "Healthy"

        summary_counts[status_key] += 1
        for warning in warnings:
            credential_alerts.append(
                {
                    "module_name": _string_value(module.get("name")) or slug,
                    "module_slug": slug,
                    "message": warning["message"],
                    "severity": warning["severity"],
                    "expires_at_iso": warning["expires_at_iso"],
                }
            )

        issues: list[str] = []
        if missing_fields:
            issues.append("Add " + ", ".join(missing_fields))
        if enabled and COMMANDS_BY_MODULE.get(slug) and not task_ids:
            issues.append("Create at least one scheduled task")
        if warnings and status_key == "setup":
            issues.append(warnings[0]["message"])
        if issues:
            settings = _module_settings(module)
            setup_steps.append(
                {
                    "module_name": _string_value(module.get("name")) or slug,
                    "module_slug": slug,
                    "enabled": enabled,
                    "issues": issues,
                    "action_url": _safe_action_url(settings.get("manage_url")),
                }
            )

        module_health.append(
            {
                "slug": slug,
                "name": _string_value(module.get("name")) or slug,
                "enabled": enabled,
                "status_key": status_key,
                "status_label": status_label,
                "scheduled_task_count": len(task_ids),
                "failed_run_count": run_failures,
                "webhook_event_count": len(module_events),
                "failed_webhook_count": webhook_failures,
                "telemetry_label": "Combined task + webhook telemetry",
                "slo_target_percent": telemetry["target_percent"],
                "slo_achieved_percent": telemetry["achieved_percent"],
                "error_budget_total": telemetry["error_budget_total"],
                "error_budget_used": telemetry["error_budget_used"],
                "error_budget_overrun": telemetry.get("error_budget_overrun", 0),
                "error_budget_remaining": telemetry["error_budget_remaining"],
                "warnings": warnings,
                "missing_fields": missing_fields,
            }
        )

    dependency_graph = _build_task_conflicts(
        tasks, timezone_name=app_settings.default_timezone
    )
    summary = {
        "enabled_modules": sum(1 for module in module_rows if bool(module.get("enabled"))),
        "healthy_modules": summary_counts.get("healthy", 0),
        "warning_modules": summary_counts.get("warning", 0),
        "setup_modules": summary_counts.get("setup", 0),
        "degraded_modules": summary_counts.get("degraded", 0),
        "disabled_modules": summary_counts.get("disabled", 0),
        "failed_webhooks": sum(
            1 for event in webhook_events if _string_value(event.get("status")).lower() == "failed"
        ),
        "pending_webhooks": sum(
            1
            for event in webhook_events
            if _string_value(event.get("status")).lower() in {"pending", "in_progress"}
        ),
        "credential_alerts": len(credential_alerts),
        "setup_steps": len(setup_steps),
        "task_conflicts": len(dependency_graph["conflicts"]),
    }

    module_health.sort(key=lambda item: (item["enabled"] is False, item["name"].casefold()))
    credential_alerts.sort(key=lambda item: (item["severity"] != "danger", item["module_name"].casefold()))
    setup_steps.sort(key=lambda item: (item["enabled"] is False, item["module_name"].casefold()))

    return {
        "summary": summary,
        "module_health": module_health,
        "credential_alerts": credential_alerts,
        "setup_steps": setup_steps,
        "dependency_graph": dependency_graph,
    }
