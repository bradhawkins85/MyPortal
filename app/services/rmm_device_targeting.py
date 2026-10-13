"""Shared device targeting for RMM automation and event rules."""
from __future__ import annotations

import json
from typing import Any, Mapping

from app.repositories import rmm_targeting as targeting_repo
from app.services import rmm_targeting


def parse_rules(payload: Mapping[str, Any], errors: dict[str, str]) -> dict[str, Any] | None:
    if payload.get("target_mode") != "rules":
        return None
    try:
        raw = payload.get("target_rules")
        if isinstance(raw, str):
            raw = json.loads(raw)
        return rmm_targeting.validate_rules(raw)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        errors["target_rules"] = str(exc) or "Add at least one valid device rule."
        return None


async def filter_agents(agents: list[dict[str, Any]], rules: Any) -> list[dict[str, Any]]:
    """Use current inventory; invalid saved rules match no devices."""
    try:
        matches = rmm_targeting.compile_rules(rules)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return []
    contexts = await targeting_repo.contexts_for_agents(agents)
    return [agent for agent in agents if matches(contexts.get(int(agent["id"])))]


async def device_matches(agent: Mapping[str, Any], rules: Any) -> bool:
    return bool(await filter_agents([dict(agent)], rules))
