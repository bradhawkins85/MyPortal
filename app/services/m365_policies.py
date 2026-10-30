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

import copy
from typing import Any

from app.core.logging import log_warning
from app.repositories import m365 as m365_repo
from app.repositories import m365_policies as policy_repo
from app.services import m365_policy_targeting as targeting
from app.services import m365_standards as standards

MAX_NAME_LENGTH = 150
MAX_DESCRIPTION_LENGTH = 2000
OVERRIDE_ACTIONS = ("include", "exclude")


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
            matches = targeting.compile_rules(policy.get("target_rules"))
        except ValueError as exc:
            log_warning("Skipping M365 policy with invalid filter rules", policy_id=policy["id"], error=str(exc))
            continue
        overrides = {item["company_id"]: item["action"] for item in policy.get("overrides") or []}
        for company_id, context in contexts.items():
            action = overrides.get(company_id)
            if action == "exclude" or (action != "include" and not matches(context)):
                continue
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


async def preview_targets(target_rules: Any, overrides: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return the connected tenants a draft filter would target, for live previews."""
    matches = targeting.compile_rules(target_rules)
    actions = {_company_id(item.get("company_id")): str(item.get("action")) for item in overrides or []}
    contexts = await tenant_contexts()
    matched = [
        {"company_id": company_id, "company_name": context["company_name"]}
        for company_id, context in contexts.items()
        if actions.get(company_id) == "include"
        or (actions.get(company_id) != "exclude" and matches(context))
    ]
    return sorted(matched, key=lambda item: item["company_name"].casefold())
