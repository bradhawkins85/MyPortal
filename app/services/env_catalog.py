"""Value-free environment schema for the browser-only admin formatter.

Never read the live .env, os.environ, or resolved Settings values here.
"""
from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

from app.core.config import Settings
from app.core.core_components import CORE_COMPONENTS
from app.core.features import discover_builtin_feature_pack_slugs
from app.services.component_availability import get_component_availability
from app.services.modules import DEFAULT_MODULES


@lru_cache(maxsize=1)
def environment_schema() -> dict[str, tuple[str, ...]]:
    """Collect declared aliases and literal environment reads from shipped code."""
    schema: dict[str, tuple[str, ...]] = {}
    for name, field in Settings.model_fields.items():
        alias = field.validation_alias
        aliases = (alias,) if isinstance(alias, str) else tuple(getattr(alias, "choices", ()))
        aliases = tuple(key for key in aliases if isinstance(key, str)) or (name.upper(),)
        for key in aliases:
            schema[key] = aliases
    root = Path(__file__).resolve().parents[2]
    for path in (root / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            is_env_read = (
                isinstance(func, ast.Attribute) and func.attr in {"getenv", "environ"}
            ) or (
                isinstance(func, ast.Attribute) and func.attr == "get"
                and isinstance(func.value, ast.Attribute) and func.value.attr == "environ"
            ) or (isinstance(func, ast.Name) and func.id == "getenv")
            if is_env_read and isinstance(node.args[0], ast.Constant):
                key = node.args[0].value
                if isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                    schema.setdefault(key, (key,))
            # Also recognise os.environ["KEY"].
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "environ":
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    key = node.slice.value
                    schema.setdefault(key, (key,))
    example = root / ".env.example"
    if example.exists():
        for key in re.findall(r"^\s*(?:#\s*)?([A-Z][A-Z0-9_]*)=", example.read_text(encoding="utf-8"), re.M):
            schema.setdefault(key, (key,))
    # Internal paths are not deployment environment variables.
    for key in ("STATIC_PATH", "TEMPLATE_PATH"):
        schema.pop(key, None)
    return schema


def build_environment_catalog(modules: list[dict], loaded_packs: list[dict]) -> list[dict]:
    availability = get_component_availability()
    owners: dict[str, tuple[str, bool]] = {}
    module_states = {module["slug"]: module for module in modules}
    known_modules = {module["slug"]: module for module in DEFAULT_MODULES}
    known_modules.update(module_states)
    for slug, definition in known_modules.items():
        module = module_states.get(slug, {**definition, "enabled": False})
        owners[slug.upper().replace("-", "_") + "_"] = (
            "Module: " + str(module.get("name") or slug), availability.module_enabled(module)
        )
    loaded = {item["slug"] for item in loaded_packs}
    core_components = {component.slug: component for component in CORE_COMPONENTS}
    for slug in (*discover_builtin_feature_pack_slugs(), *core_components):
        component = core_components.get(slug)
        mounted = slug in loaded or (
            component is not None
            and (component.parent_pack is None or component.parent_pack in loaded)
        )
        owners.setdefault(slug.upper() + "_", (
            "Feature pack: " + slug.replace("_", " "),
            availability.feature_pack_available(slug) and mounted,
        ))
    # Environment prefixes which differ from their component identifiers.
    prefix_owners = {
        "MATRIX_": "chat", "MATRIXBOT_": "chat", "RAG_": "rag_index",
        "BCP_": "continuity", "TICKET_": "tickets", "WEBSITE_": "service_status",
        "CURRICULA_": "huntress", "TRMM_": "tacticalrmm", "M365_": "m365_admin",
        "SIP_": "voice_monitor", "BACKUP_": "backups", "ASSET_": "assets",
        "OPNFORM_": "forms", "REPORT_": "reports",
    }
    for prefix, slug in prefix_owners.items():
        owner_prefix = slug.upper() + "_"
        if owner_prefix in owners:
            owners[prefix] = owners[owner_prefix]
    core_groups = {
        "DB_": "Database", "EVENT_LOG_": "RMM event logs", "STATS_": "RMM statistics",
        "REDIS_": "Redis", "SESSION_": "Security and sessions", "TOTP_": "Security and sessions",
        "VAULT_": "Credential vault", "LOG_": "Logging", "APP_LOG_": "Logging",
        "BACKUP_": "Backups", "PASSKEY_": "Security and sessions",
    }
    catalog = []
    for key, aliases in sorted(environment_schema().items()):
        canonical_key = aliases[0]
        group, active = "Core application", True
        for prefix in sorted(owners, key=len, reverse=True):
            if canonical_key.startswith(prefix):
                group, active = owners[prefix]
                break
        else:
            for prefix, label in core_groups.items():
                if canonical_key.startswith(prefix):
                    group = label
                    break
        catalog.append({"key": key, "aliases": aliases, "group": group, "active": active})
    return catalog
