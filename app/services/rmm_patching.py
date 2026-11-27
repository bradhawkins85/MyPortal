"""Scoped, inventory-targeted Windows/Chocolatey patching through built-in RMM jobs."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from loguru import logger
from app.repositories import rmm as rmm_repo, rmm_automation as automation_repo, rmm_patching as repo
from app.security.encryption import encrypt_secret
from app.services import rmm_automation as automation, rmm_device_targeting as targeting, rmm_scripts, rmm_targeting
from app.repositories import rmm_targeting as targeting_repo
from app.services.rmm_scripts import RunRequestError
from app.services import chocolatey_sources

PACKAGE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
UPDATE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CATEGORIES = {"Security Updates", "Critical Updates", "Update Rollups", "Updates", "Definition Updates"}
DEFAULTS = {"name": "", "scope": "company", "kind": "windows", "package_id": "", "display_name": "",
            "cron": "0 2 * * 0", "timezone": "", "target_mode": "all", "target_rules": None,
            "is_enabled": True, "categories": ["Security Updates", "Critical Updates"], "deferral_days": 0,
            "reboot_mode": "never", "active_start": 8, "active_end": 18, "reboot_delay_minutes": 15,
            "install_missing": False, "target_release": ""}

# Microsoft publishes the minimum cumulative update for each enablement package.
# These baselines apply to enablement offers; the update source can also offer a full upgrade.
FEATURE_RELEASES = (
    {"version": "24H2", "build": 26100, "full_upgrade_prerequisite_builds": [22621, 22631],
     "full_upgrade_minimum_ubr": 3672, "full_upgrade_prerequisite_kb": "KB5037853"},
    {"version": "25H2", "build": 26200, "enablement_kb": "KB5054156", "prerequisite_versions": ["24H2"],
     "minimum_ubr": 5074, "prerequisite_kb": "KB5064081"},
    # KB5124010 shipped as builds 26100.9550 and 26200.9550.
    {"version": "26H2", "build": 26300, "enablement_kb": "KB5121794", "prerequisite_versions": ["24H2", "25H2"],
     "minimum_ubr": 9550, "prerequisite_kb": "KB5124010"},
)
FEATURE_RELEASE_OPTIONS = [{"version": release["version"], "label": f"Windows 11 {release['version']}"}
                           for release in FEATURE_RELEASES]

POLICY_TEMPLATES = {
    "security-updates": {
        "name": "Security updates",
        "description": "Install security, critical and definition updates weekly. Leave restarts to users.",
        "values": {"categories": ["Security Updates", "Critical Updates", "Definition Updates"]},
    },
    "workstation-maintenance": {
        "name": "Workstation maintenance",
        "description": "Install quality updates weekly after a 7-day deferral. Restart after hours when no one is signed in.",
        "values": {"categories": sorted(CATEGORIES), "deferral_days": 7, "reboot_mode": "no_users"},
    },
    "windows-feature-rollout": {
        "name": "Windows 11 feature rollout",
        "description": "Check daily for Windows 11 26H2 and its prerequisites. Restart after hours when no one is signed in.",
        "values": {"target_release": "26H2", "cron": "0 2 * * *", "reboot_mode": "no_users"},
    },
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def flag(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true", "on"}


def validate(payload: Mapping[str, Any], company_id: int, super_admin: bool) -> dict[str, Any]:
    errors: dict[str, str] = {}
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 255:
        errors["name"] = "Enter a policy name (up to 255 characters)."
    scope = payload.get("scope", "company")
    if scope not in {"company", "global"} or (scope == "global" and not super_admin):
        errors["scope"] = "Only super admins can manage policies for every company."
    kind = payload.get("kind", "windows")
    if kind not in {"windows", "chocolatey"}:
        errors["kind"] = "Choose Windows Update or Chocolatey."
    package = str(payload.get("package_id") or "").strip().lower() if kind == "chocolatey" else ""
    display_name = str(payload.get("display_name") or "").strip()
    if kind == "chocolatey" and not PACKAGE.fullmatch(package):
        errors["package_id"] = "Enter an exact Chocolatey package ID, such as googlechrome."
    if kind == "chocolatey" and (not display_name or len(display_name) > 255):
        errors["display_name"] = "Enter the exact installed application display name."
    if payload.get("target_mode", "all") not in {"all", "rules"}:
        errors["target_mode"] = "Choose all devices or advanced rules."
    rules = targeting.parse_rules(payload, errors)
    settings: dict[str, Any] = {}
    if kind == "windows":
        target_release = str(payload.get("target_release") or "").strip().upper()
        if target_release:
            if not any(release["version"] == target_release for release in FEATURE_RELEASES):
                errors["target_release"] = "Choose a supported Windows 11 release."
            else:
                settings["target_release"] = target_release
        categories = payload.get("categories", DEFAULTS["categories"])
        if (not isinstance(categories, list) or any(not isinstance(c, str) or c not in CATEGORIES for c in categories)
                or (not categories and not settings.get("target_release"))):
            errors["categories"] = "Select an update category or a target Windows release."
        settings["categories"] = categories
        for key, low, high in (("deferral_days", 0, 90), ("active_start", 0, 23), ("active_end", 0, 23),
                               ("reboot_delay_minutes", 5, 120)):
            try:
                number = int(str(payload.get(key, DEFAULTS[key])))
                if not low <= number <= high:
                    raise ValueError()
                settings[key] = number
            except (ValueError, TypeError):
                errors[key] = f"Enter a whole number from {low} to {high}."
        mode = payload.get("reboot_mode", "never")
        if mode not in {"never", "no_users", "always"}:
            errors["reboot_mode"] = "Choose a reboot mode."
        elif mode == "never" and settings.get("target_release"):
            errors["reboot_mode"] = "Allow policy restarts to complete Windows feature upgrades."
        if settings.get("active_start") == settings.get("active_end"):
            errors["active_end"] = "Active hours must have different start and end hours."
        settings["reboot_mode"] = mode
    else:
        settings = {"display_name": display_name, "install_missing": flag(payload.get("install_missing"))}
    try:
        cron = automation.validate_cron(payload.get("cron"))
        tz = automation.validate_timezone(payload.get("timezone"))
        next_at = automation.next_run_after(cron, tz)
        if next_at is None:
            errors["cron"] = "This schedule has no future run."
    except RunRequestError as exc:
        errors.update(exc.errors)
        cron, tz, next_at = "", "", None
    if errors:
        raise RunRequestError(errors)
    enabled = flag(payload.get("is_enabled"))
    return {"name": name, "company_id": None if scope == "global" else company_id, "kind": kind,
            "package_id": package, "settings": settings, "target_rules": rules, "cron": cron, "timezone": tz,
            "is_enabled": enabled, "next_run_at": next_at if enabled else None}


def valid_saved(policy: Mapping[str, Any]) -> bool:
    try:
        checked = validate({**policy, **policy["settings"],
                            "scope": "global" if policy.get("company_id") is None else "company",
                            "target_mode": "rules" if policy.get("target_rules") is not None else "all"},
                           int(policy.get("company_id") or 1), True)
        return checked["settings"] == policy["settings"] and checked["package_id"] == policy["package_id"]
    except (RunRequestError, KeyError, TypeError, ValueError, RecursionError):
        return False


async def effective_for_agents(policies: list[dict[str, Any]], agents: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Load inventory and compile rule trees once for a fleet, including disabled overrides."""
    contexts = await targeting_repo.contexts_for_agents(agents) if any(
        p.get("target_rules") is not None for p in policies) else {}
    prepared = []
    for policy in sorted(policies, key=lambda p: (p.get("company_id") is not None, int(p["id"]))):
        try:
            matches = rmm_targeting.compile_rules(policy["target_rules"]) if policy.get("target_rules") is not None else None
        except (ValueError, TypeError, OverflowError, RecursionError):
            continue
        prepared.append((policy, matches, valid_saved(policy)))
    results = {}
    for agent in agents:
        winners: dict[tuple[str, str], tuple[dict[str, Any], bool]] = {}
        if str(agent.get("os") or "").lower() == "windows" and agent.get("status") == "active":
            for policy, matches, valid in prepared:
                if policy.get("company_id") not in {None, agent.get("company_id")}:
                    continue
                if matches is not None and not matches(contexts.get(int(agent["id"]))):
                    continue
                winners[(policy["kind"], policy["package_id"].lower())] = (policy, valid)
        results[int(agent["id"])] = [p for p, valid in winners.values() if p["is_enabled"] and valid]
    return results


async def effective(policies: list[dict[str, Any]], agent: Mapping[str, Any]) -> list[dict[str, Any]]:
    return (await effective_for_agents(policies, [dict(agent)]))[int(agent["id"])]


def stamp(policy: Mapping[str, Any]) -> dict[str, int]:
    return {"id": int(policy["id"]), "revision": int(policy["revision"])}


def plan(policies: list[dict[str, Any]], selected: set[int]) -> dict[str, Any]:
    windows = next((p for p in policies if p["kind"] == "windows"), None)
    windows_settings = windows["settings"] if windows and int(windows["id"]) in selected else None
    config = {
        "windows": windows_settings,
        "apps": [{"package_id": p["package_id"], **p["settings"]} for p in policies
                 if p["kind"] == "chocolatey" and int(p["id"]) in selected],
        "reboot": windows["settings"] if windows else {"reboot_mode": "never"},
        "policies": [stamp(p) for p in policies if int(p["id"]) in selected],
        "reboot_policy": stamp(windows) if windows else None,
    }
    if windows_settings and windows_settings.get("target_release"):
        config["feature_upgrade"] = {"target": windows_settings["target_release"], "releases": FEATURE_RELEASES}
    return config


def script_content() -> str:
    # Windows Update functions skip blocked updates and report the device's new
    # update status after installing (see app.services.rmm_windows_updates).
    builtin = Path(__file__).parent / "rmm_builtin"
    files = ("windows_updates.ps1", "windows_feature_upgrade.ps1", "patch.ps1")
    catalog = f"$myPortalFeatureReleases = '{json.dumps(FEATURE_RELEASES)}' | ConvertFrom-Json"
    return "\n".join([chocolatey_sources.script(), catalog] + [
        (builtin / name).read_text(encoding="utf-8") for name in files
    ])


def chosen_updates_current(agent: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    """A job installing updates a technician chose still applies at dispatch on
    a Windows device; the device skips any update since installed or blocked."""

    manual = config.get("manual_windows")
    return (str(agent.get("os") or "").lower() == "windows" and isinstance(manual, Mapping)
            and bool(manual.get("update_ids"))
            and all(UPDATE_ID.fullmatch(str(value or "")) for value in manual["update_ids"])
            and not config.get("apps") and config.get("windows") is None)


def manual_apps_current(agent: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    """A job managing apps a technician chose still applies at
    dispatch: the device is an active Windows agent and the plan holds only
    the packages that were chosen (see app.services.rmm_software_updates)."""

    manual = config["manual_apps"]
    ids = manual.get("package_ids") or []
    apps = config.get("apps") or []
    return bool(agent.get("status") == "active" and str(agent.get("os") or "").lower() == "windows"
                and manual.get("mode") in {"install", "upgrade", "uninstall"} and apps
                and isinstance(manual.get("force", False), bool)
                and isinstance(manual.get("ignore_pending_reboots", True), bool)
                and isinstance(manual.get("ignore_checksums", False), bool)
                and all(PACKAGE.fullmatch(str(i)) for i in ids)
                and all(a.get("package_id") in ids and a.get("display_name")
                        and (manual["mode"] == "install" or not a.get("install_missing")) for a in apps)
                and config.get("windows") is None and config.get("reboot") == {"reboot_mode": "never"})


async def queue(agent: Mapping[str, Any], policies: list[dict[str, Any]], selected: set[int],
                expires_at: datetime, user_id: int | None = None) -> int | None:
    config = plan(policies, selected)
    if not config["policies"] or await repo.has_active_run(int(agent["id"])):
        return None
    if config["apps"]:
        config["sources"] = await chocolatey_sources.for_company(int(agent["company_id"]), secrets=True)
    content = script_content()
    script = {"id": None, "name": "Patch management", "path": "builtin/patch.ps1", "language": "powershell",
              "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
    problem = rmm_scripts.device_problem(script, agent)
    if problem:
        raise RunRequestError({"device": problem})
    inputs = [{"key": "policies", "label": "Policies",
               "value": ", ".join(p["name"] for p in policies if int(p["id"]) in selected)}]
    feature_upgrade = config.get("feature_upgrade")
    if feature_upgrade:
        inputs.append({"key": "target_release", "label": "Target Windows release",
                       "value": f"Windows 11 {feature_upgrade['target']}"})
    run_id = await rmm_repo.create_run(script=script, content=content, agent_id=int(agent["id"]),
        company_id=int(agent["company_id"]), asset_id=agent.get("asset_id"), requested_by_user_id=user_id,
        inputs=inputs,
        payload_encrypted=encrypt_secret(json.dumps({"env": {"MYPORTAL_PATCH_PLAN": json.dumps(config)}})),
        timeout_seconds=rmm_scripts.MAX_TIMEOUT_SECONDS if feature_upgrade else 7200,
        expires_at=expires_at, run_source="patch")
    rmm_scripts.notify_agent(int(agent["id"]))
    return run_id


async def job_is_current(agent: Mapping[str, Any], job: Mapping[str, Any]) -> bool:
    """Re-evaluate inventory, scopes and revisions immediately before dispatch."""
    try:
        config = json.loads(job["env"]["MYPORTAL_PATCH_PLAN"])
        if "manual_windows" in config:
            return chosen_updates_current(agent, config)
        if "manual_apps" in config:
            return manual_apps_current(agent, config)
        if "manual_upgrade" in config:
            from app.services import rmm_software_updates
            from app.services.chocolatey_catalog import CatalogUnavailable
            try:
                return await rmm_software_updates.is_current(dict(agent), config)
            except CatalogUnavailable:
                return False
        policies = await effective(await repo.list_policies(int(agent["company_id"])), agent)
        current = [stamp(p) for p in policies]
        return bool(config["policies"]) and all(p in current for p in config["policies"]) and (
            config["reboot_policy"] == next((stamp(p) for p in policies if p["kind"] == "windows"), None))
    except (ValueError, KeyError, TypeError):
        return False


async def tick() -> None:
    now = utcnow()
    policies = await repo.list_policies()
    due = set()
    # A window's run stays collectable until the policy's next window, so an
    # offline device catches up once and never builds a backlog of repeats.
    deadlines: list[datetime] = []
    for p in policies:
        next_at = p.get("next_run_at")
        if isinstance(next_at, str):
            next_at = datetime.fromisoformat(next_at)
        if p["is_enabled"] and next_at and next_at <= now:
            following = automation.next_run_after(p["cron"], p["timezone"], after=now)
            if await repo.claim(p, following):
                due.add(int(p["id"]))
                deadlines.append(following or now + timedelta(days=1))
    if not due:
        return
    expires_at = min(deadlines)
    agents = await automation_repo.list_active_agents(company_id=None)
    applied_by_agent = await effective_for_agents(policies, agents)
    for agent in agents:
        try:
            applied = applied_by_agent[int(agent["id"])]
            await queue(agent, applied, due, expires_at)
        except Exception:
            logger.exception("Could not queue patches for agent {}", agent["id"])
