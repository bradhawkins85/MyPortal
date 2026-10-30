"""Registry of Microsoft 365 standards that policies can apply to tenants.

A standard is one tenant setting with a desired state. Its ``check`` reports
whether a tenant complies and its optional ``apply`` brings the tenant into
line. Policies reference standards by ``id`` and choose a mode for each:

* ``report`` records compliance only;
* ``alert`` records compliance and raises a notification on drift;
* ``remediate`` applies the desired state when the tenant drifts.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

MODES = ("report", "alert", "remediate")
MODE_RANK = {mode: rank for rank, mode in enumerate(MODES)}

StandardCheck = Callable[[int, dict[str, Any]], Awaitable[dict[str, Any]]]
StandardApply = Callable[[int, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Standard:
    id: str
    name: str
    category: str
    description: str
    check: StandardCheck
    apply: StandardApply | None = None
    default_settings: dict[str, Any] = field(default_factory=dict)

    @property
    def modes(self) -> tuple[str, ...]:
        return MODES if self.apply is not None else MODES[:2]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "modes": list(self.modes),
            "default_settings": copy.deepcopy(self.default_settings),
        }


_REGISTRY: dict[str, Standard] = {}


def register(standard: Standard) -> Standard:
    if standard.id in _REGISTRY and _REGISTRY[standard.id] is not standard:
        raise ValueError(f"Standard {standard.id!r} is already registered")
    _REGISTRY[standard.id] = standard
    return standard


def unregister(standard_id: str) -> None:
    _REGISTRY.pop(standard_id, None)


def get(standard_id: str) -> Standard | None:
    return _REGISTRY.get(standard_id)


def catalogue() -> list[dict[str, Any]]:
    """Return every registered standard, grouped by category then name."""
    return [
        standard.summary()
        for standard in sorted(_REGISTRY.values(), key=lambda item: (item.category, item.name))
    ]
