"""Deployment-level availability policy for feature packs and modules.

Database flags describe operational state.  This service is the single place
where that state is combined with the deployment's immutable kill switches.
It never writes either source, so removing a slug from the environment restores
the previously stored configuration and history.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.core.module_capabilities import (
    feature_pack_for_module,
    module_for_feature_pack,
    modules_for_command,
    modules_for_route,
    modules_for_service,
    modules_for_ui_feature,
)


class AvailabilityConfigurationError(ValueError):
    """Raised when a deployment names a component that does not exist."""


def parse_slug_list(value: object) -> tuple[str, ...]:
    """Parse a comma-separated slug list, trimming and de-duplicating it."""

    if isinstance(value, (tuple, list, set, frozenset)):
        parts = value
    else:
        parts = str(value or "").split(",")
    return tuple(
        dict.fromkeys(str(part).strip() for part in parts if str(part).strip())
    )


@dataclass(frozen=True)
class ComponentAvailability:
    """Authoritative deployment availability policy."""

    disabled_feature_packs: frozenset[str] = frozenset()
    disabled_modules: frozenset[str] = frozenset()

    def feature_pack_available(self, slug: str) -> bool:
        owner = module_for_feature_pack(slug)
        return slug not in self.disabled_feature_packs and (
            owner is None or owner not in self.disabled_modules
        )

    def module_available(self, slug: str) -> bool:
        pack = feature_pack_for_module(slug)
        return slug not in self.disabled_modules and (
            pack is None or pack not in self.disabled_feature_packs
        )

    def module_enabled(self, module: Mapping[str, object]) -> bool:
        return self.module_available(str(module.get("slug") or "")) and bool(
            module.get("enabled")
        )

    def _owned_capability_available(self, owners: Iterable[str]) -> bool:
        owners = tuple(owners)
        # Unowned core capabilities are unaffected. Shared capabilities are
        # removed only when every owning module is excluded.
        return not owners or any(self.module_available(owner) for owner in owners)

    def command_available(self, command: str) -> bool:
        return self._owned_capability_available(modules_for_command(command))

    def route_available(self, route: str) -> bool:
        return self._owned_capability_available(modules_for_route(route))

    def service_available(self, service: str) -> bool:
        return self._owned_capability_available(modules_for_service(service))

    def ui_feature_available(self, feature: str) -> bool:
        return self._owned_capability_available(modules_for_ui_feature(feature))


_availability = ComponentAvailability()


def configure_component_availability(
    *,
    disabled_feature_packs: object = (),
    disabled_modules: object = (),
    known_feature_packs: Iterable[str],
    known_modules: Iterable[str],
) -> ComponentAvailability:
    """Validate and install the process-wide deployment policy."""

    packs = frozenset(parse_slug_list(disabled_feature_packs))
    modules = frozenset(parse_slug_list(disabled_modules))
    unknown_packs = sorted(packs - set(known_feature_packs))
    unknown_modules = sorted(modules - set(known_modules))
    errors: list[str] = []
    if unknown_packs:
        errors.append(
            "DISABLED_FEATURE_PACKS contains unknown slug(s): "
            + ", ".join(unknown_packs)
        )
    if unknown_modules:
        errors.append(
            "DISABLED_MODULES contains unknown slug(s): " + ", ".join(unknown_modules)
        )
    if errors:
        raise AvailabilityConfigurationError("; ".join(errors))

    global _availability
    _availability = ComponentAvailability(packs, modules)
    return _availability


def get_component_availability() -> ComponentAvailability:
    return _availability


__all__ = [
    "AvailabilityConfigurationError",
    "ComponentAvailability",
    "configure_component_availability",
    "get_component_availability",
    "parse_slug_list",
]
