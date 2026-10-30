"""Microsoft 365 standards policies: validation, saving and tenant resolution.

A policy groups standards (see ``app.services.m365_standards``), each with a
mode, and targets tenants by filter rules (see
``app.services.m365_policy_targeting``) plus per-company include or exclude
overrides. Only companies with a connected Microsoft 365 tenant are targeted.

When several policies apply the same standard to one tenant, the strictest
mode wins (remediate, then alert, then report). Ties go to the oldest policy,
whose settings are used. Every contributing policy is listed in ``sources``.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any

from app.core.database import db
from app.core.logging import log_error, log_info, log_warning
from app.repositories import company_variables as company_variables_repo
from app.repositories import m365 as m365_repo
from app.repositories import m365_policies as policy_repo
from app.services import audit as audit_service
from app.services import m365_policy_targeting as targeting
from app.services import m365_standard_library  # noqa: F401 - registers built-in standards
from app.services import m365_standards as standards
from app.services import notifications as notifications_service

MAX_NAME_LENGTH = 150
MAX_DESCRIPTION_LENGTH = 2000
OVERRIDE_ACTIONS = ("include", "exclude")
DRIFT_EVENT = "m365.standard_drift"
_DRIFTED = ("drift", "failed")


def _company_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("company IDs must be whole numbers")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("company IDs must be whole numbers") from None
    if number <= 0:
        raise ValueError("company IDs must be positive")
    return number


def validate_policy(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a normalised policy, raising ``ValueError`` with a plain message."""
    if not isinstance(payload, dict):
        raise ValueError("Policy details are required")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Give the policy a name")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"Policy names may contain at most {MAX_NAME_LENGTH} characters")
    description = str(payload.get("description") or "").strip()
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(f"Descriptions may contain at most {MAX_DESCRIPTION_LENGTH} characters")

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload.get("standards") or []:
        if not isinstance(item, dict):
            raise ValueError("Each standard must be an object")
        standard_id = str(item.get("standard_id") or "").strip()
        standard = standards.get(standard_id)
        if standard is None:
            raise ValueError(f"Unknown standard {standard_id!r}")
        if standard_id in seen:
            raise ValueError(f"{standard.name} is listed more than once")
        seen.add(standard_id)
        mode = str(item.get("mode") or "report").strip().lower()
        if mode not in standard.modes:
            raise ValueError(f"{standard.name} cannot run in {mode!r} mode")
        settings = item.get("settings") or {}
        if not isinstance(settings, dict):
            raise ValueError(f"Settings for {standard.name} must be an object")
        selected.append({"standard_id": standard_id, "mode": mode,
                         "settings": {**standard.default_settings, **settings}})

    overrides: dict[int, str] = {}
    for item in payload.get("overrides") or []:
        if not isinstance(item, dict):
            raise ValueError("Each company override must be an object")
        company_id = _company_id(item.get("company_id"))
        action = str(item.get("action") or "").strip().lower()
        if action not in OVERRIDE_ACTIONS:
            raise ValueError("Company overrides must include or exclude the company")
        if overrides.get(company_id, action) != action:
            raise ValueError("A company cannot be both included and excluded")
        overrides[company_id] = action

    return {
        "name": name,
        "description": description,
        "enabled": bool(payload.get("enabled", True)),
        "target_rules": targeting.validate_rules(payload.get("target_rules")),
        "standards": selected,
        "overrides": [{"company_id": key, "action": value} for key, value in sorted(overrides.items())],
    }


async def save_policy(
    payload: dict[str, Any], *, policy_id: int | None = None, user_id: int | None = None
) -> int:
    """Validate and store a policy, returning its ID."""
    policy = validate_policy(payload)
    fields = {key: policy[key] for key in ("name", "description", "enabled", "target_rules")}
    if policy_id is None:
        policy_id = await policy_repo.create_policy(**fields, created_by=user_id)
    else:
        if await policy_repo.get_policy(policy_id) is None:
            raise LookupError("Policy not found")
        await policy_repo.update_policy(policy_id, **fields)
    await policy_repo.replace_standards(policy_id, policy["standards"])
    await policy_repo.replace_overrides(policy_id, policy["overrides"])
    return policy_id


def _effective_mode(standard: standards.Standard, mode: str) -> str:
    # A standard that lost its apply action since the policy was saved alerts instead.
    return mode if mode in standard.modes else standard.modes[-1]


def matching_company_ids(
    target_rules: Any, overrides: list[dict[str, Any]] | None, contexts: dict[int, dict[str, Any]]
) -> list[int]:
    """Return the tenants a policy targets: its filter, then include/exclude overrides.

    Raises ``ValueError`` when the filter rules are invalid.
    """
    matches = targeting.compile_rules(target_rules)
    actions = {_company_id(item.get("company_id")): str(item.get("action")) for item in overrides or []}
    return [
        company_id for company_id, context in contexts.items()
        if actions.get(company_id) == "include"
        or (actions.get(company_id) != "exclude" and matches(context))
    ]


def resolve_assignments(
    policies: list[dict[str, Any]], contexts: dict[int, dict[str, Any]]
) -> dict[int, dict[str, dict[str, Any]]]:
    """Return ``{company_id: {standard_id: assignment}}`` for matching tenants.

    Pure: callers supply the stored policies and the tenant contexts. Disabled
    policies, unknown standards and policies with invalid rules are skipped.
    """
    result: dict[int, dict[str, dict[str, Any]]] = {}
    for policy in sorted(policies, key=lambda item: item["id"]):
        if not policy.get("enabled"):
            continue
        try:
            targets = matching_company_ids(policy.get("target_rules"), policy.get("overrides"), contexts)
        except ValueError as exc:
            log_warning("Skipping M365 policy with invalid filter rules", policy_id=policy["id"], error=str(exc))
            continue
        for company_id in targets:
            assigned = result.setdefault(company_id, {})
            for item in policy.get("standards") or []:
                standard = standards.get(item["standard_id"])
                if standard is None:
                    continue
                mode = _effective_mode(standard, item["mode"])
                source = {"policy_id": policy["id"], "policy_name": policy["name"], "mode": mode}
                current = assigned.get(standard.id)
                if current is None:
                    assigned[standard.id] = {
                        "standard_id": standard.id,
                        "mode": mode,
                        "settings": copy.deepcopy(item.get("settings") or {}),
                        "policy_id": policy["id"],
                        "policy_name": policy["name"],
                        "sources": [source],
                    }
                    continue
                current["sources"].append(source)
                if standards.MODE_RANK[mode] > standards.MODE_RANK[current["mode"]]:
                    current.update(
                        mode=mode,
                        settings=copy.deepcopy(item.get("settings") or {}),
                        policy_id=policy["id"],
                        policy_name=policy["name"],
                    )
    return {company_id: assigned for company_id, assigned in result.items() if assigned}


async def tenant_contexts(company_ids: list[int] | None = None) -> dict[int, dict[str, Any]]:
    """Return filter contexts for connected tenants, optionally limited to some companies."""
    provisioned = await m365_repo.list_provisioned_company_ids()
    if company_ids is not None:
        provisioned &= set(company_ids)
    return await policy_repo.tenant_contexts(provisioned)


async def effective_standards(company_id: int | None = None) -> dict[int, dict[str, dict[str, Any]]]:
    """Resolve which standards apply to each connected tenant, and in which mode."""
    contexts = await tenant_contexts(None if company_id is None else [company_id])
    return resolve_assignments(await policy_repo.list_policies(), contexts)


async def preview_targets(target_rules: Any, overrides: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return the connected tenants a draft filter would target, for live previews."""
    targeting.validate_rules(target_rules)
    contexts = await tenant_contexts()
    matched = [
        {"company_id": company_id, "company_name": contexts[company_id]["company_name"]}
        for company_id in matching_company_ids(target_rules, overrides, contexts)
    ]
    matched.sort(key=lambda item: item["company_name"].casefold())
    return {"matched_count": len(matched), "eligible_count": len(contexts), "tenants": matched}


def _audit_view(policy: dict[str, Any] | None) -> dict[str, Any] | None:
    if policy is None:
        return None
    return {key: policy.get(key) for key in ("name", "description", "enabled", "target_rules", "standards", "overrides")}


async def save_policy_audited(
    payload: dict[str, Any], *, policy_id: int | None = None, user_id: int | None = None, request: Any = None
) -> int:
    """Save a policy and record the change in the audit log."""
    before = await policy_repo.get_policy(policy_id) if policy_id is not None else None
    if policy_id is not None and before is None:
        raise LookupError("Policy not found")
    saved_id = await save_policy(payload, policy_id=policy_id, user_id=user_id)
    await audit_service.record(
        action="m365.policy.update" if policy_id is not None else "m365.policy.create",
        request=request,
        user_id=user_id,
        entity_type="m365_policy",
        entity_id=saved_id,
        before=_audit_view(before),
        after=_audit_view(await policy_repo.get_policy(saved_id)),
    )
    return saved_id


async def delete_policy(policy_id: int, *, user_id: int | None = None, request: Any = None) -> dict[str, Any]:
    """Delete a policy and audit it, raising ``LookupError`` if it does not exist."""
    policy = await policy_repo.get_policy(policy_id)
    if policy is None:
        raise LookupError("Policy not found")
    await policy_repo.delete_policy(policy_id)
    await audit_service.record_delete(
        action="m365.policy.delete",
        before=_audit_view(policy),
        request=request,
        user_id=user_id,
        entity_type="m365_policy",
        entity_id=policy_id,
    )
    return policy


async def page_data() -> dict[str, Any]:
    """Everything the Policies page shows: policies, results and editor choices."""
    contexts = await tenant_contexts()
    stored = await policy_repo.list_policies()
    catalogue = {item["id"]: item for item in standards.catalogue()}
    names = {company_id: context["company_name"] for company_id, context in contexts.items()}
    for policy in stored:
        try:
            targets = matching_company_ids(policy.get("target_rules"), policy.get("overrides"), contexts)
            policy["rules_error"] = None
        except ValueError as exc:
            targets, policy["rules_error"] = [], str(exc)
        policy["target_count"] = len(targets)
        policy["target_summary"] = (
            "These filter rules can't be read: " + policy["rules_error"] if policy["rules_error"]
            else targeting.describe_rules(policy.get("target_rules"))
        )
        modes: dict[str, int] = {}
        for item in policy["standards"]:
            modes[item["mode"]] = modes.get(item["mode"], 0) + 1
            item["name"] = catalogue.get(item["standard_id"], {}).get("name", item["standard_id"])
        policy["mode_counts"] = modes
        for item in policy["overrides"]:
            item["company_name"] = names.get(item["company_id"], f"Company {item['company_id']}")
    policy_names = {policy["id"]: policy["name"] for policy in stored}
    results = []
    for row in await policy_repo.list_results():
        if row["company_id"] not in names:
            continue
        row["company_name"] = names[row["company_id"]]
        row["standard_name"] = catalogue.get(row["standard_id"], {}).get("name", row["standard_id"])
        row["policy_name"] = policy_names.get(row["policy_id"], "")
        results.append(row)
    results.sort(key=lambda row: (row["company_name"].casefold(), row["standard_name"].casefold()))
    status_counts: dict[str, int] = {}
    for row in results:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    licence_skus = sorted({sku for context in contexts.values() for sku in context["license_skus"]})
    licence_names = sorted({name for context in contexts.values() for name in context["license_names"]})
    variables = sorted({str(row["name"]) for row in await company_variables_repo.list_definitions()})
    return {
        "policies": stored,
        "results": results,
        "status_counts": status_counts,
        "standards": list(catalogue.values()),
        "fields": targeting.field_catalogue(),
        "tenants": sorted(
            ({"company_id": key, "company_name": value} for key, value in names.items()),
            key=lambda item: item["company_name"].casefold(),
        ),
        "suggestions": {"m365.license_sku": licence_skus, "m365.license_name": licence_names,
                        "company.variable": variables},
    }


_background_runs: set[asyncio.Task[Any]] = set()


async def run_standards_locked() -> bool:
    """Run every standard unless another worker already is; return whether it ran."""
    async with db.acquire_lock("m365_standards_run", timeout=5) as lock_acquired:
        if not lock_acquired:
            log_info("M365 standards run already running on another worker, skipping")
            return False
        await run_standards()
        return True


def start_background_run() -> None:
    """Start a standards run without waiting for it, for the Run now action."""
    async def runner() -> None:
        try:
            await run_standards_locked()
        except Exception as exc:  # noqa: BLE001 - logged, never raised to the page
            log_error("M365 standards run failed", error=str(exc))

    task = asyncio.create_task(runner(), name="m365-standards-run")
    _background_runs.add(task)
    task.add_done_callback(_background_runs.discard)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _safe_call(call: Any, company_id: int, settings: dict[str, Any], failure: str) -> dict[str, Any]:
    try:
        outcome = await call(company_id, copy.deepcopy(settings))
    except Exception as exc:  # noqa: BLE001 - one standard must never stop the run
        log_error("M365 standard raised an error", company_id=company_id, error=str(exc))
        return {"status": failure, "details": f"The standard could not run: {exc}"}
    return outcome if isinstance(outcome, dict) else {"status": failure, "details": "The standard returned no result."}


async def _alert(company_name: str, standard: standards.Standard, assignment: dict[str, Any], result: dict[str, Any]) -> None:
    verb = "could not be fixed" if result["status"] == "failed" else "has drifted"
    message = f"{company_name}: {standard.name} {verb} (policy {assignment['policy_name']}). {result['details']}"
    try:
        await notifications_service.emit_notification(
            event_type=DRIFT_EVENT,
            message=message,
            metadata={
                "company_id": result["company_id"],
                "company_name": company_name,
                "standard_id": standard.id,
                "standard_name": standard.name,
                "policy_id": assignment["policy_id"],
                "policy_name": assignment["policy_name"],
                "mode": assignment["mode"],
                "status": result["status"],
                "details": result["details"],
            },
        )
    except Exception as exc:  # noqa: BLE001 - alerts must not stop the run
        log_error("Failed to send M365 standard drift alert", standard_id=standard.id, error=str(exc))


async def run_standard(
    company_id: int,
    company_name: str,
    assignment: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Check one assigned standard, apply it in remediate mode, and store the result.

    Alerts fire once when a tenant first drifts (alert mode) or when
    remediation fails (remediate mode), not again on every run while the
    drift persists.
    """
    standard = standards.get(assignment["standard_id"])
    if standard is None:
        return None
    now = now or _now()
    settings = assignment.get("settings") or {}
    check = await _safe_call(standard.check, company_id, settings, "unknown")
    outcome = str(check.get("status"))
    status = "compliant" if outcome == "pass" else "drift" if outcome == "fail" else "unknown"
    details = str(check.get("details") or "")
    affected = check.get("affected") or []
    remediated_at = previous.get("remediated_at") if previous else None

    if status == "drift" and assignment["mode"] == "remediate" and standard.apply is not None:
        applied = await _safe_call(standard.apply, company_id, settings, "failed")
        await audit_service.log_action(
            action="m365.standard.remediate",
            user_id=None,
            entity_type="company",
            entity_id=company_id,
            previous_value={"status": "drift", "details": details, "affected": affected},
            new_value={"status": applied.get("status"), "details": applied.get("details")},
            metadata={"company_id": company_id, "standard_id": standard.id, "policy_id": assignment["policy_id"]},
        )
        recheck = await _safe_call(standard.check, company_id, settings, "unknown")
        if recheck.get("status") == "pass":
            status, remediated_at = "remediated", now
            details = str(applied.get("details") or recheck.get("details") or "")
            affected = []
        else:
            status = "failed"
            details = str(applied.get("details") or "") + (" " + str(recheck.get("details") or "")).rstrip()
            affected = recheck.get("affected") or affected

    already_drifted = bool(previous) and previous.get("status") in _DRIFTED
    result = {
        "company_id": company_id,
        "standard_id": standard.id,
        "policy_id": assignment["policy_id"],
        "mode": assignment["mode"],
        "status": status,
        "details": details.strip(),
        "affected": affected,
        "checked_at": now,
        "drift_since": ((previous.get("drift_since") if already_drifted else None) or now)
        if status in _DRIFTED else None,
        "remediated_at": remediated_at,
    }
    await policy_repo.upsert_result(result)
    if status in _DRIFTED and not already_drifted and assignment["mode"] in ("alert", "remediate"):
        await _alert(company_name, standard, assignment, result)
    return result


async def run_standards(company_id: int | None = None) -> dict[str, int]:
    """Run every assigned standard for connected tenants and return status counts."""
    contexts = await tenant_contexts(None if company_id is None else [company_id])
    assignments = resolve_assignments(await policy_repo.list_policies(), contexts)
    previous = {
        (row["company_id"], row["standard_id"]): row
        for row in await policy_repo.list_results(company_id)
    }
    counts: dict[str, int] = {}
    now = _now()
    for target_id, items in assignments.items():
        name = contexts[target_id]["company_name"]
        for standard_id, assignment in items.items():
            result = await run_standard(target_id, name, assignment, previous.get((target_id, standard_id)), now=now)
            if result is not None:
                counts[result["status"]] = counts.get(result["status"], 0) + 1
        await policy_repo.delete_stale_results(target_id, items.keys())
    for stale_id in {key[0] for key in previous} - set(assignments):
        await policy_repo.delete_stale_results(stale_id, [])
    log_info("M365 standards run completed", company_id=company_id, **counts)
    return counts
