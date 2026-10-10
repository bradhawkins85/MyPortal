"""RMM scripting: load scripts from Gitea, push them to RMM agents, record results.

The flow for a manual run:

1. :func:`sync_from_gitea` loads ``.ps1``/``.sh``/``.zsh`` files and stores the
   parameters and environment variables :mod:`rmm_script_parser` finds.
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
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping

from app.core.logging import log_info, log_warning
from app.repositories import asset_custom_fields as asset_fields_repo
from app.repositories import assets as assets_repo
from app.repositories import companies as companies_repo
from app.repositories import company_variables as company_variables_repo
from app.repositories import rmm as rmm_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import gitea
from app.services import rmm_script_parser as parser
from app.services import tray as tray_service
from app.services import value_templates

MIN_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 4 * 60 * 60
# How long a queued run waits for an offline device before it expires.
QUEUE_LIFETIME = timedelta(hours=24)
# Extra time after the script's own timeout for the agent to report back.
RESULT_GRACE = timedelta(minutes=5)
MAX_OUTPUT_CHARS = 1_000_000
MAX_CUSTOM_VALUES = 200
MAX_TARGETS_PER_RUN = 500
POLL_WAIT_SECONDS = 25
SENSITIVE_MASK = "••••••"

_TRUE = {"1", "true", "yes", "on", "y", "$true"}
_FALSE = {"0", "false", "no", "off", "n", "$false", ""}
_TOKEN = re.compile(r"\{\{")
# Custom field and variable names may contain spaces, which the general
# template engine's tokens do not allow, so these are resolved first.
_NAMED_VALUE_TOKEN = re.compile(r"\{\{\s*(asset\.custom|company\.variables)\.([^{}]+?)\s*\}\}")

# Wakes an agent's long-poll on this worker when a run is queued for it.
_agent_events: dict[int, asyncio.Event] = {}


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
    skipped: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "skipped": self.skipped,
        }

    def message(self) -> str:
        parts = [f"{self.added} added", f"{self.updated} updated", f"{self.unchanged} unchanged"]
        if self.removed:
            parts.append(f"{self.removed} removed")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        return "Scripts synced from Gitea: " + ", ".join(parts) + "."


# --------------------------------------------------------------------------- #
# Gitea sync
# --------------------------------------------------------------------------- #


def _display_name(path: str) -> str:
    filename = path.rsplit("/", 1)[-1]
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def _folder(path: str, root: str) -> str:
    relative = path[len(root) + 1:] if root and path.startswith(root + "/") else path
    return relative.rsplit("/", 1)[0] if "/" in relative else ""


async def sync_from_gitea() -> SyncSummary:
    """Load every supported script from the configured Gitea repository."""

    settings = await gitea.load_settings()
    files = await gitea.list_files(settings)
    existing = await rmm_repo.script_source_index()
    summary = SyncSummary()
    seen: set[str] = set()
    unchanged: list[str] = []

    for item in files:
        language = parser.language_for_path(item.path)
        if not language:
            continue
        seen.add(item.path)
        if item.size > parser.MAX_SCRIPT_BYTES:
            summary.skipped.append({"path": item.path, "reason": "Larger than 1 MB"})
            continue
        stored = existing.get(item.path)
        if stored and stored.get("source_sha") == item.sha and stored.get("is_active"):
            unchanged.append(item.path)
            summary.unchanged += 1
            continue
        raw = await gitea.fetch_file(settings, item.path)
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            summary.skipped.append({"path": item.path, "reason": "Not UTF-8 text"})
            continue
        parsed = parser.parse_script(content, language)
        await rmm_repo.upsert_script(
            path=item.path,
            name=_display_name(item.path),
            folder=_folder(item.path, settings.path),
            language=language,
            description=parsed.description[:2000],
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            source_sha=item.sha,
            parameters=parsed.to_dict()["parameters"],
            env_vars=parsed.to_dict()["env_vars"],
        )
        if stored:
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
    script: Mapping[str, Any], entries: Mapping[str, str], context: Mapping[str, Any]
) -> dict[str, Any]:
    """Resolve variables for one device and convert parameters to their types."""

    errors: dict[str, str] = {}
    params: dict[str, Any] = {}
    env: dict[str, str] = {}
    order: list[str] = []
    for field_def in script_fields(script):
        entered = entries.get(field_def["key"], "")
        if _TOKEN.search(entered):
            resolved = await render_value(entered, context)
        else:
            resolved = entered
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


def _clamp_timeout(value: Any, default: int) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = default
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, seconds))


def notify_agent(agent_id: int) -> None:
    event = _agent_events.get(agent_id)
    if event is not None:
        event.set()


async def queue_runs(
    *,
    script_id: int,
    company_id: int,
    asset_ids: Iterable[int],
    submitted: Mapping[str, Any],
    timeout_seconds: Any,
    requested_by_user_id: int | None,
) -> dict[str, Any]:
    """Queue the script on each device. Returns the queued run ids and per-device problems."""

    script = await rmm_repo.get_script(script_id, with_content=True)
    if not script or not script.get("is_active"):
        raise RunRequestError({"script": "That script is no longer available. Sync scripts and try again."})
    targets = list(dict.fromkeys(int(asset_id) for asset_id in asset_ids))
    if not targets:
        raise RunRequestError({"assets": "Choose at least one device."})
    if len(targets) > MAX_TARGETS_PER_RUN:
        raise RunRequestError({"assets": f"Choose at most {MAX_TARGETS_PER_RUN} devices at a time."})
    entries = validate_entries(script, submitted)
    timeout = _clamp_timeout(timeout_seconds, int(script.get("default_timeout_seconds") or 600))
    inputs = [_stored_input(field_def, entries.get(field_def["key"], "")) for field_def in script_fields(script)]
    inputs = [item for item in inputs if item["value"]]

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
        if script["language"] == "powershell" and str(agent.get("os") or "").lower() not in {"windows", ""}:
            shells = str(agent.get("shells") or "")
            if "pwsh" not in shells:
                problems.append({"asset_id": asset_id, "asset_name": asset.get("name"), "message": "PowerShell is not installed on this device."})
                continue
        if script["language"] in {"bash", "zsh"} and str(agent.get("os") or "").lower() == "windows":
            problems.append({"asset_id": asset_id, "asset_name": asset.get("name"), "message": "Shell scripts cannot run on Windows."})
            continue
        context = await build_template_context(company_id, asset_id)
        try:
            payload = await resolve_entries(script, entries, context)
        except RunRequestError as exc:
            problems.append({
                "asset_id": asset_id,
                "asset_name": asset.get("name"),
                "message": "; ".join(exc.errors.values()),
                "errors": exc.errors,
            })
            continue
        run_id = await rmm_repo.create_run(
            script=script,
            content=str(script.get("content") or ""),
            agent_id=int(agent["id"]),
            company_id=int(company_id),
            asset_id=asset_id,
            requested_by_user_id=requested_by_user_id,
            inputs=inputs,
            payload_encrypted=encrypt_secret(json.dumps(payload)),
            timeout_seconds=timeout,
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + QUEUE_LIFETIME,
        )
        notify_agent(int(agent["id"]))
        queued.append({"run_id": run_id, "asset_id": asset_id, "asset_name": asset.get("name")})
    return {"queued": queued, "problems": problems, "script": {"id": script["id"], "name": script["name"]}}


# --------------------------------------------------------------------------- #
# Agent side
# --------------------------------------------------------------------------- #


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
    return {
        "hostname": text("hostname", 255),
        "os": (text("os", 32) or "").lower() or None,
        "os_version": text("os_version", 128),
        "arch": text("arch", 32),
        "agent_version": text("agent_version", 32),
        "shells": (str(shells).strip()[:255] or None) if shells else None,
        "last_ip": (client_ip or "")[:64] or None,
    }


async def enrol_agent(
    *, tray_device: Mapping[str, Any], payload: Mapping[str, Any], client_ip: str | None
) -> dict[str, Any]:
    """Enrol (or re-enrol) the RMM agent installed alongside a tray device."""

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
    return {"agent_id": agent_id, "agent_uid": agent_uid, "auth_token": token, "poll_wait_seconds": POLL_WAIT_SECONDS}


async def authenticate_agent(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    return await rmm_repo.get_agent_by_token_hash(tray_service.hash_token(token))


async def checkin(agent: Mapping[str, Any], payload: Mapping[str, Any], client_ip: str | None) -> None:
    await rmm_repo.record_agent_checkin(int(agent["id"]), _agent_details(payload, client_ip))


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
        "parameters": values.get("parameters") or {},
        "parameter_order": values.get("parameter_order") or [],
        "env": values.get("env") or {},
    }


async def claim_jobs(agent: Mapping[str, Any]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    agent_id = int(agent["id"])
    for row in await rmm_repo.next_jobs_for_agent(agent_id):
        expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(seconds=int(row.get("timeout_seconds") or 600))
            + RESULT_GRACE
        )
        if not await rmm_repo.mark_dispatched(int(row["id"]), agent_id, expires_at=expires_at):
            continue
        job = _job_payload(row)
        if job is None:
            await rmm_repo.finish_run(
                int(row["id"]), status="failed", exit_code=None, stdout="", stderr="",
                custom_values=[], error_message="The run's values could not be decrypted.",
            )
            continue
        jobs.append(job)
    return jobs


async def wait_for_jobs(agent: Mapping[str, Any], *, wait_seconds: float) -> list[dict[str, Any]]:
    """Long-poll: return queued jobs as soon as there are any, or ``[]`` after ``wait_seconds``."""

    agent_id = int(agent["id"])
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, min(float(wait_seconds), POLL_WAIT_SECONDS))
    event = _agent_events.setdefault(agent_id, asyncio.Event())
    try:
        while True:
            event.clear()
            jobs = await claim_jobs(agent)
            remaining = deadline - loop.time()
            if jobs or remaining <= 0:
                return jobs
            # Runs queued on another worker are found on the next pass.
            try:
                await asyncio.wait_for(event.wait(), timeout=min(5.0, remaining))
            except asyncio.TimeoutError:
                pass
    finally:
        if _agent_events.get(agent_id) is event and not event.is_set():
            _agent_events.pop(agent_id, None)


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
    """Accept ``[{scope, name, value}]`` or ``{"asset": {...}, "company": {...}}``."""

    items: list[dict[str, str]] = []
    if isinstance(raw, Mapping):
        for scope in ("asset", "company"):
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
        if scope in {"asset", "company"} and name:
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
    custom_values = await apply_custom_values(run, normalise_custom_values(payload.get("custom_values")))
    await rmm_repo.finish_run(
        run_id,
        status=status,
        exit_code=exit_code,
        stdout=_truncate(payload.get("stdout")),
        stderr=_truncate(payload.get("stderr")),
        custom_values=custom_values,
        error_message=error,
    )
    return {"status": status, "custom_values": custom_values}
