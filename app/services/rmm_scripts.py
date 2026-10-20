"""RMM scripting: load scripts from Gitea, push them to RMM agents, record results.

The flow for a manual run:

1. :func:`sync_from_gitea` loads ``.ps1``/``.sh``/``.zsh`` files and stores the
   parameters and environment variables :mod:`rmm_script_parser` finds. Scripts
   live in ``Common/`` (every company) or ``Companies/<company>/`` (that
   company's devices only); :func:`ensure_folders` creates those folders.
2. A technician fills in a field for each one, with a typed value or a MyPortal
   variable such as ``{{company.variables.TenantId}}`` or
   ``{{asset.custom.BitLocker}}``. :func:`queue_runs` resolves those per device,
   encrypts the resolved values and queues one run per device.
3. The device's RMM agent collects the run (:func:`claim_jobs`), executes it and
   reports back (:func:`record_result`). Custom values the script returns update
   the asset's custom fields and the company's variables.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Iterable, Mapping

from app.core.logging import log_info, log_warning
from app.repositories import asset_custom_fields as asset_fields_repo
from app.repositories import assets as assets_repo
from app.repositories import companies as companies_repo
from app.repositories import company_variables as company_variables_repo
from app.repositories import rmm as rmm_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import gitea
from app.services import gitea_sign_in
from app.services import rmm_script_parser as parser
from app.services.rmm_output import readable_output
from app.services import tray as tray_service
from app.services import value_templates
from app.services.singleton_jobs import singleton_run
from app.services.rmm_script_layout import (
    _create_missing,
    _display_name,
    _folder,
    _relative,
    read_layout,
    script_scope,
)

MIN_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 4 * 60 * 60
MAX_QUEUE_TIMEOUT_SECONDS = 2_147_483_640
SCRIPT_PLATFORMS = {
    "windows": "Windows", "darwin": "Mac", "linux": "Linux",
    "freebsd": "FreeBSD", "openbsd": "OpenBSD", "netbsd": "NetBSD",
}
# How long a queued run waits for an offline device before it expires.
QUEUE_LIFETIME = timedelta(hours=24)
# Extra time after the script's own timeout for the agent to report back.
RESULT_GRACE = timedelta(minutes=5)
MAX_OUTPUT_CHARS = 1_000_000
MAX_CUSTOM_VALUES = 200
MAX_TARGETS_PER_RUN = 500
POLL_WAIT_SECONDS = 25
# Agents long-poll for jobs every POLL_WAIT_SECONDS, so one that has missed a
# few polls has lost its connection.
AGENT_CONNECTED_WINDOW = timedelta(seconds=POLL_WAIT_SECONDS * 4)
SENSITIVE_MASK = "••••••"
# rmm_script_runs.run_source for a run started from a tray menu item.
TRAY_RUN_SOURCE = "tray"
# Who a script runs as on the device: the agent's own account (LocalSystem on
# Windows, root elsewhere) or the user logged in to it.
RUN_AS_OPTIONS = ("system", "user")
CUSTOM_VALUE_SCOPES = ("asset", "company", "session")

# Built-in jobs adjust their own runs through these hooks, registered by their
# modules so this one need not import them (they import it): a dispatch hook
# can add to a job as the device collects it, and output hooks can take
# reported data out of live and final output. Each is keyed by name.
DispatchHook = Callable[[Mapping[str, Any], Mapping[str, Any], dict[str, Any]], Awaitable[None]]
LiveOutputHook = Callable[[Mapping[str, Any], int, str], Awaitable[str]]
ResultHook = Callable[[Mapping[str, Any], Mapping[str, Any], str], Awaitable[str]]
_DISPATCH_HOOKS: dict[str, DispatchHook] = {}
_LIVE_OUTPUT_HOOKS: dict[str, LiveOutputHook] = {}
_RESULT_HOOKS: dict[str, ResultHook] = {}


def register_run_hooks(name: str, *, dispatch: DispatchHook | None = None, live_output: LiveOutputHook | None = None,
                       result: ResultHook | None = None) -> None:
    for hooks, hook in ((_DISPATCH_HOOKS, dispatch), (_LIVE_OUTPUT_HOOKS, live_output), (_RESULT_HOOKS, result)):
        if hook is not None:
            hooks[name] = hook

_TRUE = {"1", "true", "yes", "on", "y", "$true"}
_FALSE = {"0", "false", "no", "off", "n", "$false", ""}
_TOKEN = re.compile(r"\{\{")
# Custom field and variable names may contain spaces, which the general
# template engine's tokens do not allow, so these are resolved first.
_NAMED_VALUE_TOKEN = re.compile(r"\{\{\s*(asset\.custom|company\.variables)\.([^{}]+?)\s*\}\}")

# Wakes an agent's long-poll on this worker when a run is queued for it.
_agent_events: dict[int, asyncio.Event] = {}
# Runs queued or finished on another worker are found by one queue check per
# worker for all of its waiting agents, rather than by each long-poll querying
# the database itself, so many idle agents do not crowd out the web UI.
QUEUE_WATCH_SECONDS = 2.0
# Each agent's queue state from the latest check, and as of its last claim.
_queue_states: dict[int, tuple[int, int, int]] = {}
_queue_seen: dict[int, tuple[int, int, int] | None] = {}
_queue_watcher: asyncio.Task | None = None
# Job polls refresh last_seen_utc at most this often; well inside
# AGENT_CONNECTED_WINDOW, as one poll may follow the refresh by a full wait.
HEARTBEAT_INTERVAL = timedelta(seconds=45)


class RunRequestError(ValueError):
    """The submitted values cannot be used to run the script."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{key}: {message}" for key, message in errors.items()))
        self.errors = errors


@dataclass
class SyncSummary:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    folders_created: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "folders_created": self.folders_created,
            "skipped": self.skipped,
            "warnings": self.warnings,
        }

    def message(self) -> str:
        parts = [f"{self.added} added", f"{self.updated} updated", f"{self.unchanged} unchanged"]
        if self.removed:
            parts.append(f"{self.removed} removed")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        if self.folders_created:
            parts.append(f"{self.folders_created} folder{'' if self.folders_created == 1 else 's'} created")
        text = "Scripts synced from Gitea: " + ", ".join(parts) + "."
        return " ".join([text, *self.warnings])


# --------------------------------------------------------------------------- #
# Gitea sync
# --------------------------------------------------------------------------- #


FOLDER_CHECK_INTERVAL_SECONDS = 600


async def ensure_folders() -> int:
    """Create shared and company folders for scripts and ``Files/`` storage.
    Returns the number of files created."""

    settings = await gitea.load_settings()
    files = await gitea.list_files(settings)
    companies = await companies_repo.list_companies(include_archived=True)
    return await _create_missing(settings, await read_layout(settings, files, companies))


@singleton_run("rmm_gitea_folders", ttl_seconds=300)
async def _ensure_folders_once() -> None:
    try:
        await ensure_folders()
    except gitea.GiteaError as exc:
        log_info("RMM script folders not checked", reason=str(exc))
    try:
        await gitea_sign_in.reconcile_accounts()
    except gitea.GiteaError as exc:
        log_info("Script repository access not checked", reason=str(exc))


async def folder_maintenance_loop() -> None:
    """Keep a folder for every company, including companies added later, and
    keep technicians' repository access in step with their roles."""

    await asyncio.sleep(30 + random.uniform(0, 30))  # nosec B311 - start-up jitter, not security
    while True:
        try:
            await _ensure_folders_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - logged and retried
            log_warning("RMM script folder check failed", error=str(exc))
        await asyncio.sleep(FOLDER_CHECK_INTERVAL_SECONDS)


async def sync_from_gitea() -> SyncSummary:
    """Create any missing folders, then load every script in ``Common/`` and
    the company folders in ``Companies/``."""

    settings = await gitea.load_settings()
    files = await gitea.list_files(settings)
    companies = await companies_repo.list_companies(include_archived=True)
    layout = await read_layout(settings, files, companies)
    existing = await rmm_repo.script_source_index()
    summary = SyncSummary()
    try:
        summary.folders_created = await _create_missing(settings, layout)
    except gitea.GiteaError as exc:
        summary.warnings.append(str(exc))
    seen: set[str] = set()
    unchanged: list[str] = []
    downloads: dict[str, tuple[gitea.GiteaFile, str, str, int | None]] = {}

    for item in files:
        language = parser.language_for_path(item.path)
        relative = _relative(item.path, settings.path)
        if not language or relative is None:
            continue
        usable, company_id, reason = script_scope(relative, layout)
        if not usable:
            summary.skipped.append({"path": item.path, "reason": reason})
            continue
        seen.add(item.path)
        if item.size > parser.MAX_SCRIPT_BYTES:
            summary.skipped.append({"path": item.path, "reason": "Larger than 1 MB"})
            continue
        stored = existing.get(item.path)
        if (
            stored
            and stored.get("source_sha") == item.sha
            and stored.get("is_active")
            and stored.get("company_id") == company_id
        ):
            unchanged.append(item.path)
            summary.unchanged += 1
            continue
        downloads[item.path] = (item, language, relative, company_id)

    async with contextlib.aclosing(gitea.file_batches(settings, downloads)) as batches:
        async for batch in batches:
            for path, raw in batch:
                item, language, relative, company_id = downloads[path]
                try:
                    content = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    summary.skipped.append({"path": item.path, "reason": "Not UTF-8 text"})
                    continue
                parsed = parser.parse_script(content, language)
                await rmm_repo.upsert_script(
                    path=item.path,
                    name=_display_name(item.path),
                    folder=_folder(relative),
                    language=language,
                    description=parsed.description[:2000],
                    content=content,
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    source_sha=item.sha,
                    parameters=parsed.to_dict()["parameters"],
                    env_vars=parsed.to_dict()["env_vars"],
                    company_id=company_id,
                )
                if path in existing:
                    summary.updated += 1
                else:
                    summary.added += 1
    await rmm_repo.touch_scripts(unchanged)
    removed = [path for path, row in existing.items() if path not in seen and row.get("is_active")]
    await rmm_repo.deactivate_scripts(removed)
    summary.removed = len(removed)
    log_info("RMM scripts synced from Gitea", **summary.to_dict())
    return summary


async def script_source_url(script: Mapping[str, Any]) -> str | None:
    """Link to the script in Gitea, when Gitea is configured."""

    try:
        settings = await gitea.load_settings()
    except gitea.GiteaError:
        return None
    return gitea.web_url(settings, str(script.get("path") or ""))


# --------------------------------------------------------------------------- #
# Fields and variables
# --------------------------------------------------------------------------- #


def script_fields(script: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return one form field per parameter and environment variable."""

    fields: list[dict[str, Any]] = []
    for item in script.get("parameters") or []:
        fields.append({
            "kind": "param",
            "key": "param:" + str(item.get("name")),
            "name": str(item.get("name")),
            "type": item.get("type") or "string",
            "mandatory": bool(item.get("mandatory")),
            "default": item.get("default"),
            "choices": list(item.get("choices") or []),
            "help": item.get("help") or "",
            "sensitive": bool(item.get("sensitive")),
        })
    for item in script.get("env_vars") or []:
        fields.append({
            "kind": "env",
            "key": "env:" + str(item.get("name")),
            "name": str(item.get("name")),
            "type": "secret" if item.get("sensitive") else "string",
            "mandatory": False,
            "default": item.get("default"),
            "choices": [],
            "help": item.get("help") or "",
            "sensitive": bool(item.get("sensitive")),
        })
    return fields


async def available_variables() -> list[dict[str, str]]:
    """MyPortal variables a technician can insert into a script field."""

    variables = [
        {"group": "Device", "label": "Asset name", "token": "{{asset.name}}"},
        {"group": "Device", "label": "Serial number", "token": "{{asset.serial_number}}"},
        {"group": "Device", "label": "Asset ID", "token": "{{asset.id}}"},
        {"group": "Company", "label": "Company name", "token": "{{company.name}}"},
        {"group": "Company", "label": "Company ID", "token": "{{company.id}}"},
    ]
    for definition in await asset_fields_repo.list_field_definitions():
        if definition.get("field_type") == "image":
            continue
        name = str(definition.get("name") or "")
        if not name:
            continue
        variables.append({
            "group": "Asset custom fields",
            "label": str(definition.get("display_name") or name),
            "token": "{{asset.custom." + name + "}}",
        })
    for row in await company_variables_repo.list_definitions():
        name = str(row.get("name") or "")
        if name:
            variables.append({"group": "Company variables", "label": name, "token": "{{company.variables." + name + "}}"})
    return variables


def _scalar(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


async def _asset_custom_values(asset_id: int) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for row in await asset_fields_repo.get_asset_field_values(asset_id):
        field_type = row.get("field_type")
        if field_type == "checkbox":
            value: Any = "true" if row.get("value_boolean") else "false"
        elif field_type == "date":
            value = _scalar(row.get("value_date")) or ""
        else:
            value = row.get("value_text") or ""
        values[str(row.get("field_name"))] = value
    return values


async def build_template_context(company_id: int, asset_id: int | None) -> dict[str, Any]:
    company = await companies_repo.get_company_by_id(company_id) or {}
    context: dict[str, Any] = {
        "company": {
            "id": company_id,
            "name": company.get("name") or "",
            "variables": await company_variables_repo.value_map(company_id),
        },
        "asset": {"custom": {}},
    }
    if asset_id is not None:
        asset = await assets_repo.get_asset_by_id(asset_id) or {}
        asset_context = {key: _scalar(value) for key, value in asset.items() if _scalar(value) is not None}
        asset_context["custom"] = await _asset_custom_values(asset_id)
        context["asset"] = asset_context
    return context


def _named_value(context: Mapping[str, Any], scope: str, name: str) -> str:
    owner, key = scope.split(".")
    values = (context.get(owner) or {}).get(key) or {}
    if name in values:
        return str(values[name] if values[name] is not None else "")
    lowered = name.casefold()
    for candidate, value in values.items():
        if str(candidate).casefold() == lowered:
            return str(value if value is not None else "")
    return ""


async def render_value(template: str, context: Mapping[str, Any]) -> str:
    """Fill MyPortal variables in a technician's value for one device."""

    text = _NAMED_VALUE_TOKEN.sub(lambda match: _named_value(context, match.group(1), match.group(2)), template)
    if not _TOKEN.search(text):
        return text
    rendered = await value_templates.render_string_async(text, context, include_templates=False)
    return "" if rendered is None else str(rendered)


def _coerce(field_def: Mapping[str, Any], raw: str) -> tuple[Any, str | None]:
    """Convert a resolved value to the parameter's type."""

    field_type = field_def.get("type") or "string"
    text = raw.strip() if field_type not in {"string", "secret"} else raw
    if field_type == "integer":
        try:
            return int(text), None
        except ValueError:
            return None, "Enter a whole number."
    if field_type == "number":
        try:
            return float(text), None
        except ValueError:
            return None, "Enter a number."
    if field_type in {"boolean", "switch"}:
        lowered = text.lower()
        if lowered in _TRUE:
            return True, None
        if lowered in _FALSE:
            return False, None
        return None, "Choose yes or no."
    if field_type == "list":
        return [part.strip() for part in re.split(r"[\n,]", text) if part.strip()], None
    if field_type == "choice":
        choices = [str(choice) for choice in field_def.get("choices") or []]
        if choices and text not in choices:
            matches = [choice for choice in choices if choice.lower() == text.lower()]
            if not matches:
                return None, "Choose one of: " + ", ".join(choices) + "."
            text = matches[0]
        return text, None
    return raw, None


def _stored_input(field_def: Mapping[str, Any], entered: str) -> dict[str, Any]:
    is_template = bool(_TOKEN.search(entered))
    display = entered
    if field_def.get("sensitive") and entered and not is_template:
        display = SENSITIVE_MASK
    return {
        "kind": field_def["kind"],
        "name": field_def["name"],
        "value": display,
        "is_variable": is_template,
        "sensitive": bool(field_def.get("sensitive")),
    }


def validate_entries(script: Mapping[str, Any], submitted: Mapping[str, Any]) -> dict[str, str]:
    """Return the entered value per field key, or raise when a required one is missing."""

    errors: dict[str, str] = {}
    entries: dict[str, str] = {}
    for field_def in script_fields(script):
        value = submitted.get(field_def["key"])
        text = "" if value is None else str(value)
        if len(text) > 65535:
            errors[field_def["key"]] = "This value is too long."
            continue
        if not text.strip() and field_def["mandatory"] and field_def["type"] != "switch":
            errors[field_def["key"]] = "This field is required."
            continue
        entries[field_def["key"]] = text
    if errors:
        raise RunRequestError(errors)
    return entries


async def resolve_entries(
    script: Mapping[str, Any],
    entries: Mapping[str, str],
    context: Mapping[str, Any],
    rendered: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Resolve variables for one device and convert parameters to their types.

    A blank field whose script default is a MyPortal variable, such as
    ``{{ COMPANY_NAME }}``, is filled from that default; left to the script it
    would receive the variable's text. When given, ``rendered`` collects each
    variable resolved, keyed like ``entries``, as ``template`` and ``value``."""

    errors: dict[str, str] = {}
    params: dict[str, Any] = {}
    env: dict[str, str] = {}
    order: list[str] = []
    for field_def in script_fields(script):
        entered = entries.get(field_def["key"], "")
        from_default = False
        if not entered.strip() and _TOKEN.search(str(field_def.get("default") or "")):
            entered = str(field_def["default"])
            from_default = True
        if _TOKEN.search(entered):
            resolved = await render_value(entered, context)
            if rendered is not None:
                rendered[field_def["key"]] = {"template": entered, "value": resolved}
        else:
            resolved = entered
        if from_default and not resolved.strip() and field_def["type"] in {"string", "secret"}:
            # The script's own default would be the variable's text, so send the empty value instead.
            if field_def["kind"] == "env":
                env[field_def["name"]] = ""
            else:
                params[field_def["name"]] = ""
                order.append(field_def["name"])
            continue
        if not resolved.strip():
            if field_def["mandatory"] and field_def["type"] != "switch":
                errors[field_def["key"]] = "The variable has no value for this device."
            continue
        if field_def["kind"] == "env":
            env[field_def["name"]] = resolved
            continue
        value, error = _coerce(field_def, resolved)
        if error:
            errors[field_def["key"]] = error
            continue
        params[field_def["name"]] = value
        order.append(field_def["name"])
    if errors:
        raise RunRequestError(errors)
    return {"parameters": params, "parameter_order": order, "env": env}


# --------------------------------------------------------------------------- #
# Queueing runs
# --------------------------------------------------------------------------- #


def clamp_timeout(value: Any, default: int) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = default
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, seconds))


def queue_timeout(value: Any, default: int = 0) -> int:
    """Offline queue lifetime: zero means forever; execution uses clamp_timeout."""
    if value is None or value == "":
        return default
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise RunRequestError({"queue_timeout_seconds": "Enter minutes, or 0 for Forever."}) from None
    if seconds != 0 and not 60 <= seconds <= MAX_QUEUE_TIMEOUT_SECONDS:
        raise RunRequestError({"queue_timeout_seconds": "Choose at least 1 minute, or 0 for Forever."})
    return seconds


def queue_timeout_override(value: Any) -> int | None:
    return None if value is None or value == "" else queue_timeout(value)


def normalise_platform(value: Any) -> str:
    name = str(value or "").strip().lower()
    return {"mac": "darwin", "macos": "darwin", "osx": "darwin", "win32": "windows", "win": "windows"}.get(name, name)


def platform_allowed(script: Mapping[str, Any], agent: Mapping[str, Any]) -> bool:
    platforms = script.get("platforms")
    if platforms is None:
        platforms = rmm_repo.default_script_platforms(script.get("language"))
    return normalise_platform(agent.get("os")) in platforms


def clean_run_as(value: Any) -> str:
    """``system`` or ``user``; missing means ``system``, anything else is an error."""

    if value is None or value == "":
        return "system"
    if value not in RUN_AS_OPTIONS:
        raise RunRequestError({"run_as": "Choose System or Logged in user."})
    return str(value)


def notify_agent(agent_id: int) -> None:
    event = _agent_events.get(agent_id)
    if event is not None:
        event.set()


def device_problem(script: Mapping[str, Any], agent: Mapping[str, Any]) -> str | None:
    """Why ``script`` cannot run on ``agent``'s device, or ``None`` when it can."""

    os_name = normalise_platform(agent.get("os"))
    if script["language"] in {"bash", "zsh"} and os_name == "windows":
        return "Shell scripts cannot run on Windows."
    if not platform_allowed(script, agent):
        label = SCRIPT_PLATFORMS.get(normalise_platform(agent.get("os")), "Unknown")
        return f"This script is not enabled for the device's platform ({label})."
    if script["language"] == "powershell" and os_name not in {"windows", ""}:
        if "pwsh" not in str(agent.get("shells") or ""):
            return "PowerShell is not installed on this device."
    return None


def stored_inputs(script: Mapping[str, Any], entries: Mapping[str, str]) -> list[dict[str, Any]]:
    """The values entered, as shown in run history (secrets masked)."""

    inputs = [_stored_input(field_def, entries.get(field_def["key"], "")) for field_def in script_fields(script)]
    return [item for item in inputs if item["value"]]


def _run_inputs(
    script: Mapping[str, Any], inputs: list[dict[str, Any]], rendered: Mapping[str, Mapping[str, str]]
) -> list[dict[str, Any]]:
    """``inputs`` with the value each variable resolved to on this device.

    Script defaults that were MyPortal variables are added, marked
    ``script_default``, so run history shows what the device was sent."""

    result: list[dict[str, Any]] = []
    shown: set[str] = set()
    for item in inputs:
        key = f"{item['kind']}:{item['name']}"
        shown.add(key)
        item = dict(item)
        if key in rendered:
            item["resolved"] = SENSITIVE_MASK if item.get("sensitive") and rendered[key]["value"] else rendered[key]["value"]
        result.append(item)
    for field_def in script_fields(script):
        key = field_def["key"]
        if key in rendered and key not in shown:
            item = _stored_input(field_def, rendered[key]["template"])
            value = rendered[key]["value"]
            item["resolved"] = SENSITIVE_MASK if field_def.get("sensitive") and value else value
            item["script_default"] = True
            result.append(item)
    return result


async def queue_for_agent(
    *,
    script: Mapping[str, Any],
    agent: Mapping[str, Any],
    entries: Mapping[str, str],
    inputs: list[dict[str, Any]],
    timeout_seconds: int,
    requested_by_user_id: int | None,
    run_source: str = "manual",
    schedule_id: int | None = None,
    onboarding_run_id: int | None = None,
    expires_at: datetime | None = None,
    notify: bool = True,
    extra_context: Mapping[str, Any] | None = None,
    run_as: str = "system",
    queue_timeout_seconds: Any = None,
) -> int:
    """Resolve the values for one device and queue the run. Raises
    :class:`RunRequestError` with a ``device`` message when it cannot run there.

    ``extra_context`` adds variables for this run only, such as ``event`` for a
    run started by an event log rule (``{{event.message}}``). ``run_as`` is
    ``system`` or ``user`` (the user logged in to the device)."""

    run_as = clean_run_as(run_as)
    queue_wait = queue_timeout(queue_timeout_seconds, int(script.get("default_queue_timeout_seconds") or 0))
    if expires_at is None and queue_wait:
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=queue_wait)
    problem = device_problem(script, agent)
    if problem:
        raise RunRequestError({"device": problem})
    company_id = int(agent["company_id"])
    asset_id = int(agent["asset_id"]) if agent.get("asset_id") is not None else None
    context = await build_template_context(company_id, asset_id)
    if extra_context:
        context.update(extra_context)
    rendered: dict[str, dict[str, str]] = {}
    payload = await resolve_entries(script, entries, context, rendered)
    inputs = _run_inputs(script, inputs, rendered)
    from app.services import rmm_script_files

    try:
        payload["files"] = await rmm_script_files.prepare_files(script, agent)
    except (rmm_script_files.ScriptFileError, gitea.GiteaError) as exc:
        raise RunRequestError({"files": str(exc)}) from exc
    run_id = await rmm_repo.create_run(
        script=dict(script),
        content=str(script.get("content") or ""),
        agent_id=int(agent["id"]),
        company_id=company_id,
        asset_id=asset_id,
        requested_by_user_id=requested_by_user_id,
        inputs=inputs,
        payload_encrypted=encrypt_secret(json.dumps(payload)),
        timeout_seconds=timeout_seconds,
        expires_at=expires_at,
        queue_timeout_seconds=queue_wait,
        run_source=run_source,
        schedule_id=schedule_id,
        onboarding_run_id=onboarding_run_id,
        run_as=run_as,
    )
    if notify:
        notify_agent(int(agent["id"]))
    return run_id


async def queue_runs(
    *,
    script_id: int,
    company_id: int,
    asset_ids: Iterable[int],
    submitted: Mapping[str, Any],
    timeout_seconds: Any,
    requested_by_user_id: int | None,
    run_as: Any = "system",
    queue_timeout_seconds: Any = None,
) -> dict[str, Any]:
    """Queue the script on each device. Returns the queued run ids and per-device problems."""

    script = await rmm_repo.get_script(script_id, with_content=True)
    if not rmm_repo.script_available_to(script, company_id):
        raise RunRequestError({"script": "That script is no longer available. Sync scripts and try again."})
    targets = list(dict.fromkeys(int(asset_id) for asset_id in asset_ids))
    if not targets:
        raise RunRequestError({"assets": "Choose at least one device."})
    if len(targets) > MAX_TARGETS_PER_RUN:
        raise RunRequestError({"assets": f"Choose at most {MAX_TARGETS_PER_RUN} devices at a time."})
    entries = validate_entries(script, submitted)
    run_as = clean_run_as(run_as)
    timeout = clamp_timeout(timeout_seconds, int(script.get("default_timeout_seconds") or 600))
    queue_wait = queue_timeout(queue_timeout_seconds, int(script.get("default_queue_timeout_seconds") or 0))
    inputs = stored_inputs(script, entries)

    queued: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    for asset_id in targets:
        asset = await assets_repo.get_asset_by_id(asset_id)
        if not asset or int(asset.get("company_id") or 0) != int(company_id):
            problems.append({"asset_id": asset_id, "message": "Device not found."})
            continue
        agent = await rmm_repo.get_asset_agent(asset_id)
        if not agent:
            problems.append({"asset_id": asset_id, "asset_name": asset.get("name"), "message": "No RMM agent is installed."})
            continue
        try:
            run_id = await queue_for_agent(
                script=script,
                agent={**agent, "company_id": int(company_id), "asset_id": asset_id},
                entries=entries,
                inputs=inputs,
                timeout_seconds=timeout,
                queue_timeout_seconds=queue_wait,
                requested_by_user_id=requested_by_user_id,
                run_as=run_as,
            )
        except RunRequestError as exc:
            problem: dict[str, Any] = {
                "asset_id": asset_id,
                "asset_name": asset.get("name"),
                "message": "; ".join(exc.errors.values()),
            }
            if "device" not in exc.errors:
                problem["errors"] = exc.errors
            problems.append(problem)
            continue
        queued.append({"run_id": run_id, "asset_id": asset_id, "asset_name": asset.get("name")})
    return {"queued": queued, "problems": problems, "script": {"id": script["id"], "name": script["name"]}}


async def queue_tray_run(*, device: Mapping[str, Any], node: Mapping[str, Any]) -> dict[str, Any]:
    """Queue the script a tray menu item names on the tray device's own computer.

    ``node`` is the ``rmm_script`` item from the device's resolved tray menu.
    Its ``entries`` hold the values an administrator set for the script's
    fields, keyed like the run form (``param:Name`` / ``env:NAME``). Raises
    :class:`RunRequestError` with a message the person at the computer can read.
    """

    company_id = int(device.get("company_id") or 0)
    asset_id = int(device.get("asset_id") or 0)
    if not company_id or not asset_id:
        raise RunRequestError({"device": "This computer is not linked to a device in MyPortal yet."})
    script = await rmm_repo.get_script(int(node.get("script_id") or 0), with_content=True)
    if not rmm_repo.script_available_to(script, company_id):
        raise RunRequestError({"script": "This script is no longer available."})
    agent = await rmm_repo.get_asset_agent(asset_id)
    if not agent or int(agent.get("company_id") or 0) != company_id:
        raise RunRequestError({"device": "The MyPortal RMM agent is not installed on this computer."})
    if await rmm_repo.has_active_run(int(agent["id"]), int(script["id"]), run_source=TRAY_RUN_SOURCE):
        raise RunRequestError({"busy": "This is already queued or running on this computer."})
    raw_entries = node.get("entries")
    submitted = {str(key): "" if value is None else str(value)
                 for key, value in (raw_entries.items() if isinstance(raw_entries, Mapping) else [])}
    try:
        entries = validate_entries(script, submitted)
        run_id = await queue_for_agent(
            script=script,
            agent={**agent, "company_id": company_id, "asset_id": asset_id},
            entries=entries,
            inputs=stored_inputs(script, entries),
            timeout_seconds=clamp_timeout(None, int(script.get("default_timeout_seconds", 600))),
            requested_by_user_id=None,
            run_source=TRAY_RUN_SOURCE,
        )
    except RunRequestError as exc:
        if "device" in exc.errors:
            raise
        log_warning("Tray menu RMM script is missing values", script_id=script["id"], fields=sorted(exc.errors))
        raise RunRequestError({"setup": "This menu item is not set up correctly. Please contact support."}) from exc
    return {"run_id": run_id, "script": {"id": script["id"], "name": script["name"]}}


# --------------------------------------------------------------------------- #
# Agent side
# --------------------------------------------------------------------------- #


def _without_windows_temperatures(thermal: Any, os_name: str | None) -> Any:
    """PawnIO, which Windows agents used for temperatures, crashed systems (0x7E).

    Readings that older Windows agents still send are replaced with an empty
    snapshot so the previous readings are cleared and none are stored or shown.
    """

    if thermal is None or os_name != "windows":
        return thermal
    return {"collected_at": thermal.get("collected_at"), "status": "unsupported", "sensors": []}


def _agent_details(payload: Mapping[str, Any], client_ip: str | None) -> dict[str, Any]:
    def text(key: str, limit: int) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        cleaned = str(value).strip()[:limit]
        return cleaned or None

    shells = payload.get("shells")
    if isinstance(shells, (list, tuple)):
        shells = ",".join(str(item).strip() for item in shells if str(item).strip())
    os_name = (text("os", 32) or "").lower() or None
    return {
        "thermal": _without_windows_temperatures(payload.get("thermal"), os_name),
        "hostname": text("hostname", 255),
        "os": os_name,
        "os_version": text("os_version", 128),
        "arch": text("arch", 32),
        "agent_version": text("agent_version", 32),
        "script_files_supported": bool(payload.get("script_files_supported")) if payload.get("agent_version") is not None else payload.get("script_files_supported"),
        "run_anyway_supported": bool(payload.get("run_anyway_supported")) if payload.get("agent_version") is not None else payload.get("run_anyway_supported"),
        "run_as_user_supported": bool(payload.get("run_as_user_supported")) if payload.get("agent_version") is not None else payload.get("run_as_user_supported"),
        "shells": (str(shells).strip()[:255] or None) if shells else None,
        "last_ip": (client_ip or "")[:64] or None,
    }


async def enrol_agent(
    *, tray_device: Mapping[str, Any], payload: Mapping[str, Any], client_ip: str | None
) -> dict[str, Any]:
    """Enrol (or re-enrol) the RMM agent installed alongside a tray device.

    ``created`` in the result is true the first time this agent enrols, which
    is when its onboarding scripts start (see :mod:`app.services.rmm_automation`).
    """

    company_id = tray_device.get("company_id")
    if company_id is None:
        raise PermissionError("This tray device is not linked to a company yet.")
    agent_uid = str(payload.get("agent_uid") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,64}", agent_uid):
        raise ValueError("agent_uid must be 8-64 letters, digits, dots, dashes, colons or underscores.")
    token = tray_service.generate_auth_token()
    token_hash = tray_service.hash_token(token)
    details = _agent_details(payload, client_ip)
    existing = await rmm_repo.get_agent_by_uid(agent_uid)
    common = {
        "tray_device_id": int(tray_device["id"]),
        "company_id": int(company_id),
        "asset_id": tray_device.get("asset_id"),
        "auth_token_hash": token_hash,
        "auth_token_prefix": tray_service.token_prefix(token),
        "details": details,
    }
    if existing:
        if existing.get("tray_device_id") not in (None, int(tray_device["id"])):
            raise PermissionError("This RMM agent is enrolled to a different device.")
        await rmm_repo.reenrol_agent(int(existing["id"]), **common)
        agent_id = int(existing["id"])
    else:
        agent_id = await rmm_repo.create_agent(agent_uid=agent_uid, **common)
    log_info("RMM agent enrolled", agent_id=agent_id, tray_device_id=tray_device["id"], company_id=company_id)
    return {
        "agent_id": agent_id,
        "agent_uid": agent_uid,
        "auth_token": token,
        "poll_wait_seconds": POLL_WAIT_SECONDS,
        "created": existing is None,
    }


async def authenticate_agent(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    return await rmm_repo.get_agent_by_token_hash(tray_service.hash_token(token))


async def checkin(agent: Mapping[str, Any], payload: Mapping[str, Any], client_ip: str | None) -> None:
    details = _agent_details(payload, client_ip)
    details["thermal"] = _without_windows_temperatures(details["thermal"], details["os"] or agent.get("os"))
    await rmm_repo.record_agent_checkin(int(agent["id"]), details)
    if details["thermal"] is not None:
        from app.services.rmm_stats import ingest_thermal
        await ingest_thermal(agent, details["thermal"])


def _job_payload(row: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        values = json.loads(decrypt_secret(str(row.get("payload_encrypted") or ""), allow_plaintext=False))
    except Exception:  # noqa: BLE001 - a corrupt payload must not stop other jobs
        log_warning("RMM run payload could not be decrypted", run_id=row.get("id"))
        return None
    return {
        "id": int(row["id"]),
        "name": row.get("script_name"),
        "path": row.get("script_path"),
        "language": row.get("language"),
        "script": row.get("script_content") or "",
        "sha256": row.get("content_sha256"),
        "timeout_seconds": int(row.get("timeout_seconds") or 600),
        "run_anyway": bool(row.get("run_anyway")) and row.get("run_source") != "onboarding",
        "run_as": "user" if row.get("run_as") == "user" else "system",
        "parameters": values.get("parameters") or {},
        "parameter_order": values.get("parameter_order") or [],
        "env": values.get("env") or {},
        "files": [
            {key: item[key] for key in ("source_path", "destination", "md5", "sha256", "size")}
            for item in values.get("files") or []
        ],
    }


def agent_state(agent: Mapping[str, Any] | None, *, now: datetime | None = None) -> str:
    """``connected``, ``offline`` (installed, not polling) or ``missing`` (no RMM agent)."""

    if not agent:
        return "missing"
    last_seen = agent.get("last_seen_utc")
    if isinstance(last_seen, str):
        try:
            last_seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        except ValueError:
            last_seen = None
    if not isinstance(last_seen, datetime):
        return "offline"
    if last_seen.tzinfo is not None:
        last_seen = last_seen.astimezone(timezone.utc).replace(tzinfo=None)
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return "connected" if now - last_seen <= AGENT_CONNECTED_WINDOW else "offline"


async def claim_jobs(agent: Mapping[str, Any]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    agent_id = int(agent["id"])
    for row in await rmm_repo.next_jobs_for_agent(agent_id):
        # Recheck current restrictions: a queued job may predate a platform edit.
        script = await rmm_repo.get_script(int(row["script_id"])) if row.get("script_id") else None
        if row.get("run_source") != "command" and not platform_allowed(script or row, agent):
            await rmm_repo.cancel_run(int(row["id"]))
            continue
        if row.get("expires_at") is not None or row.get("run_source") == "patch":
            queue_deadline = row.get("expires_at")
            if isinstance(queue_deadline, str):
                queue_deadline = datetime.fromisoformat(queue_deadline)
            if not queue_deadline or queue_deadline <= datetime.now(timezone.utc).replace(tzinfo=None):
                if row.get("run_source") == "patch":
                    await rmm_repo.cancel_run(int(row["id"]))
                else:
                    await rmm_repo.expire_stale_runs()
                continue
        # Collection ends the offline wait; execution gets its own full deadline.
        timeout = clamp_timeout(row.get("timeout_seconds"), 600)
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=timeout) + RESULT_GRACE
        if not await rmm_repo.mark_dispatched(int(row["id"]), agent_id, expires_at=expires_at):
            continue
        if row.get("run_anyway") and row.get("run_source") != "onboarding" and not agent.get("run_anyway_supported"):
            await rmm_repo.finish_run(
                int(row["id"]), status="failed", exit_code=None, stdout="", stderr="", custom_values=[],
                error_message="Update the RMM agent before using Run Anyway execution mode.",
            )
            continue
        if row.get("run_as") == "user" and not agent.get("run_as_user_supported"):
            await rmm_repo.finish_run(
                int(row["id"]), status="failed", exit_code=None, stdout="", stderr="", custom_values=[],
                error_message="Update the RMM agent before running scripts as the logged-in user.",
            )
            continue
        job = _job_payload(row)
        if job is None:
            await rmm_repo.finish_run(
                int(row["id"]), status="failed", exit_code=None, stdout="", stderr="",
                custom_values=[], error_message="The run's values could not be decrypted.",
            )
            continue
        if row.get("run_source") == "patch":
            from app.services import rmm_patching
            if not await rmm_patching.job_is_current(agent, job):
                await rmm_repo.finish_run(
                    int(row["id"]), status="cancelled", exit_code=None, stdout="", stderr="", custom_values=[],
                    error_message="Patch policy or device targeting changed before dispatch.",
                )
                continue
        if row.get("run_source") == "patch":
            # Source toggles and ENV credentials are resolved again at dispatch.
            from app.services import chocolatey_sources
            config = json.loads(job["env"]["MYPORTAL_PATCH_PLAN"])
            if config.get("apps"):
                try:
                    config["sources"] = await chocolatey_sources.for_company(int(agent["company_id"]), secrets=True)
                    if not any(source["enabled"] for source in config["sources"]):
                        raise ValueError("No Chocolatey sources enabled")
                except ValueError:
                    await rmm_repo.finish_run(int(row["id"]), status="cancelled", exit_code=None, stdout="", stderr="",
                        custom_values=[], error_message="Chocolatey sources are unavailable or disabled for this company.")
                    continue
                job["env"]["MYPORTAL_PATCH_PLAN"] = json.dumps(config)
        for hook in list(_DISPATCH_HOOKS.values()):
            await hook(agent, row, job)
        if job["files"] and not agent.get("script_files_supported"):
            await rmm_repo.finish_run(
                int(row["id"]), status="failed", exit_code=None, stdout="", stderr="", custom_values=[],
                error_message="Update the RMM agent before running a script with associated files.",
            )
            continue
        jobs.append(job)
    return jobs


async def _watch_queue() -> None:
    global _queue_states
    while _agent_events:
        try:
            _queue_states = await rmm_repo.queue_states()
        except Exception as exc:  # noqa: BLE001 - keep watching; polls still claim when they start
            log_warning("RMM job queue check failed", error=str(exc))
        else:
            for agent_id, event in list(_agent_events.items()):
                state = _queue_states.get(agent_id)
                if state is not None and state != _queue_seen.get(agent_id):
                    event.set()
        await asyncio.sleep(QUEUE_WATCH_SECONDS)


def _ensure_queue_watcher() -> None:
    global _queue_watcher
    loop = asyncio.get_running_loop()
    if _queue_watcher is None or _queue_watcher.done() or _queue_watcher.get_loop() is not loop:
        _queue_watcher = loop.create_task(_watch_queue())


async def heartbeat(agent: Mapping[str, Any]) -> None:
    """Record a job poll as a check-in, skipping the write when one is recent."""

    last_seen = agent.get("last_seen_utc")
    if isinstance(last_seen, str):
        with contextlib.suppress(ValueError):
            last_seen = datetime.fromisoformat(last_seen)
    if isinstance(last_seen, datetime):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if timedelta(0) <= now - last_seen.replace(tzinfo=None) < HEARTBEAT_INTERVAL:
            return
    await rmm_repo.record_agent_checkin(int(agent["id"]), {})


async def wait_for_jobs(agent: Mapping[str, Any], *, wait_seconds: float) -> list[dict[str, Any]]:
    """Long-poll: return queued jobs as soon as there are any, or ``[]`` after ``wait_seconds``."""

    agent_id = int(agent["id"])
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, min(float(wait_seconds), POLL_WAIT_SECONDS))
    event = _agent_events.setdefault(agent_id, asyncio.Event())
    try:
        while True:
            event.clear()
            # Runs that stay queued (waiting behind a sequential run) wake
            # this poll again only when the agent's queue changes.
            _queue_seen[agent_id] = _queue_states.get(agent_id)
            jobs = await claim_jobs(agent)
            remaining = deadline - loop.time()
            if jobs or remaining <= 0:
                return jobs
            _ensure_queue_watcher()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=remaining)
    finally:
        if _agent_events.get(agent_id) is event and not event.is_set():
            _agent_events.pop(agent_id, None)
            _queue_seen.pop(agent_id, None)


async def mark_started(agent: Mapping[str, Any], run_id: int) -> bool:
    run = await rmm_repo.get_agent_run(run_id, int(agent["id"]))
    if not run:
        return False
    await rmm_repo.mark_started(run_id)
    return True


def _truncate(value: Any) -> str:
    text = "" if value is None else str(value)
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\n… output truncated by MyPortal …"
    return text


def _parse_bool(value: str) -> bool | None:
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE - {""}:
        return False
    return None


def _take_session_markers(text: str) -> tuple[str, dict[str, str]]:
    markers: dict[str, str] = {}
    cleaned_lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("::session:") and "=" in line:
            body = line[len("::session:"):]
            key, value = body.split("=", 1)
            key = key.strip()
            if key:
                markers[key] = value.strip()
            continue
        cleaned_lines.append(raw_line)
    return ("\n".join(cleaned_lines), markers)


async def _apply_asset_value(asset_id: int | None, name: str, value: str, definitions: list[dict[str, Any]]) -> str | None:
    if asset_id is None:
        return "The run is not linked to an asset."
    lowered = name.lower()
    definition = next(
        (item for item in definitions if str(item.get("name") or "").lower() == lowered),
        None,
    ) or next(
        (item for item in definitions if str(item.get("display_name") or "").lower() == lowered),
        None,
    )
    if not definition:
        return "No asset custom field has this name."
    field_type = definition.get("field_type")
    if field_type == "image":
        return "Image fields cannot be set by scripts."
    if field_type == "checkbox":
        parsed = _parse_bool(value)
        if parsed is None:
            return "Checkbox fields need true or false."
        await asset_fields_repo.set_asset_field_value(asset_id, int(definition["id"]), value_boolean=parsed)
        return None
    if field_type == "date":
        try:
            parsed_date = date.fromisoformat(value.strip()[:10])
        except ValueError:
            return "Date fields need a YYYY-MM-DD date."
        await asset_fields_repo.set_asset_field_value(asset_id, int(definition["id"]), value_date=parsed_date.isoformat())
        return None
    await asset_fields_repo.set_asset_field_value(asset_id, int(definition["id"]), value_text=value)
    return None


async def _apply_company_value(company_id: int, name: str, value: str, definitions: list[dict[str, Any]]) -> str | None:
    lowered = name.lower()
    definition = next((item for item in definitions if str(item.get("name") or "").lower() == lowered), None)
    if not definition:
        return "No company variable has this name."
    await company_variables_repo.set_value(company_id, int(definition["id"]), value)
    return None


def normalise_custom_values(raw: Any) -> list[dict[str, str]]:
    """Accept ``[{scope, name, value}]`` or ``{"asset": {...}, "company": {...}}``.

    ``session`` values are what a remote control activation script reports
    (see :mod:`app.services.rmm_remote_control`)."""

    items: list[dict[str, str]] = []
    if isinstance(raw, Mapping):
        for scope in CUSTOM_VALUE_SCOPES:
            values = raw.get(scope)
            if isinstance(values, Mapping):
                items.extend({"scope": scope, "name": str(key), "value": values[key]} for key in values)
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, Mapping):
                items.append({"scope": entry.get("scope"), "name": entry.get("name"), "value": entry.get("value")})
    cleaned: list[dict[str, str]] = []
    for item in items[:MAX_CUSTOM_VALUES]:
        scope = str(item.get("scope") or "").strip().lower()
        name = str(item.get("name") or "").strip()[:255]
        value = item.get("value")
        if isinstance(value, bool):
            value = "true" if value else "false"
        value = "" if value is None else str(value)[:65535]
        if scope in CUSTOM_VALUE_SCOPES and name:
            cleaned.append({"scope": scope, "name": name, "value": value})
    return cleaned


async def apply_custom_values(run: Mapping[str, Any], values: list[dict[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not values:
        return results
    asset_definitions = await asset_fields_repo.list_field_definitions()
    company_definitions = await company_variables_repo.list_definitions()
    for item in values:
        if item["scope"] == "asset":
            problem = await _apply_asset_value(run.get("asset_id"), item["name"], item["value"], asset_definitions)
        else:
            problem = await _apply_company_value(int(run["company_id"]), item["name"], item["value"], company_definitions)
        results.append({**item, "applied": problem is None, "message": problem or "Updated"})
    return results


async def record_output(agent: Mapping[str, Any], run_id: int, payload: Mapping[str, Any]) -> bool:
    """Store live output without applying custom values or finishing the run."""
    stdout, _ = _take_session_markers(readable_output(payload.get("stdout")))
    for hook in list(_LIVE_OUTPUT_HOOKS.values()):
        stdout = await hook(agent, run_id, stdout)
    stderr, _ = _take_session_markers(readable_output(payload.get("stderr")))
    return await rmm_repo.update_run_output(
        run_id, int(agent["id"]), stdout=_truncate(stdout), stderr=_truncate(stderr),
    )


async def record_result(agent: Mapping[str, Any], run_id: int, payload: Mapping[str, Any]) -> dict[str, Any] | None:
    run = await rmm_repo.get_agent_run(run_id, int(agent["id"]))
    if not run:
        return None
    if run["status"] not in rmm_repo.ACTIVE_RUN_STATUSES:
        return {"status": run["status"], "custom_values": []}
    exit_code = payload.get("exit_code")
    try:
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    error = str(payload.get("error") or "").strip()[:2000] or None
    if payload.get("timed_out"):
        status = "timed_out"
        error = error or "The script ran longer than its timeout and was stopped."
    elif error or exit_code is None or exit_code != 0:
        status = "failed"
    else:
        status = "completed"
    from app.services import rmm_remote_control

    values = normalise_custom_values(payload.get("custom_values"))
    # Session values (a remote access ID and password) never stay in the output.
    stdout, session_values = rmm_remote_control.take_session_markers(readable_output(payload.get("stdout")))
    for hook in list(_RESULT_HOOKS.values()):
        stdout = await hook(agent, {**run, "id": run_id}, stdout)
    session_values.update({item["name"].lower(): item["value"] for item in values if item["scope"] == "session"})
    custom_values = await apply_custom_values(run, [item for item in values if item["scope"] != "session"])
    if session_values:
        custom_values.extend(await rmm_remote_control.capture({**run, "id": run_id}, session_values))
    stderr = rmm_remote_control.take_session_markers(readable_output(payload.get("stderr")))[0]
    await rmm_repo.finish_run(
        run_id,
        status=status,
        exit_code=exit_code,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        custom_values=custom_values,
        error_message=error,
    )
    if run.get("run_source") == "ticket":
        from app.services import rmm_ticket_scripts

        await rmm_ticket_scripts.record_result(
            run_id, status=status, exit_code=exit_code, stdout=stdout, stderr=stderr, error=error,
        )
    return {"status": status, "custom_values": custom_values}
