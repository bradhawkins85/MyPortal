"""Ad hoc commands a technician runs on one device from its asset page.

A command is a one-off script typed into MyPortal rather than synced from
Gitea. It is queued as an ordinary run (``run_source`` ``command``) so the
device's queue, live output, result view and run history all apply, and runs
as System or the logged-in user like any other script.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from app.repositories import rmm as rmm_repo
from app.security.encryption import encrypt_secret
from app.services import rmm_scripts
from app.services.rmm_scripts import RunRequestError

RUN_SOURCE = "command"
LANGUAGES = {"powershell": "PowerShell", "cmd": "Command Prompt", "bash": "Bash", "zsh": "Zsh"}
MAX_COMMAND_LENGTH = 65535
DEFAULT_TIMEOUT_SECONDS = 300
# Run history shows this much of the command; the device receives all of it.
SHOWN_COMMAND_LENGTH = 2000


async def queue_checkin(agent: Mapping[str, Any], requested_by_user_id: int | None) -> int:
    """Queue a one-off native task through the normal immediate RMM channel."""
    windows = str(agent.get("os") or "").lower() == "windows"
    text = ('throw "Update the MyPortal RMM agent to use Check in now."'
            if windows else 'echo "Update the MyPortal RMM agent to use Check in now." >&2; exit 1')
    script = {"id": None, "name": "Check in now", "path": "agent/checkin",
              "language": "powershell" if windows else "bash", "run_anyway": True,
              "content_sha256": hashlib.sha256(text.encode()).hexdigest()}
    run_id = await rmm_repo.create_run(
        script=script, content=text, agent_id=int(agent["id"]), company_id=int(agent["company_id"]),
        asset_id=int(agent["asset_id"]), requested_by_user_id=requested_by_user_id, inputs=[],
        payload_encrypted=encrypt_secret(json.dumps({"parameters": {}, "parameter_order": [], "env": {}, "files": []})),
        timeout_seconds=900, expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + rmm_scripts.QUEUE_LIFETIME,
        run_source=RUN_SOURCE, run_as="system",
    )
    rmm_scripts.notify_agent(int(agent["id"]))
    return run_id


def languages_for(agent: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """The shells offered for a device, the default first."""

    if not agent:
        return []
    if str(agent.get("os") or "").lower() == "windows":
        names = ["powershell", "cmd"]
    else:
        names = ["bash", "zsh"] + (["powershell"] if "pwsh" in str(agent.get("shells") or "") else [])
    return [{"value": name, "label": LANGUAGES[name]} for name in names]


def _device_problem(language: str, agent: Mapping[str, Any]) -> str | None:
    if language == "cmd":
        if str(agent.get("os") or "").lower() != "windows":
            return "Command Prompt commands run on Windows only."
        if "cmd" not in str(agent.get("shells") or "").split(","):
            return "Update the RMM agent before running Command Prompt commands."
        return None
    return rmm_scripts.device_problem({"language": language, "platforms": [rmm_scripts.normalise_platform(agent.get("os"))]}, agent)


async def queue_command(
    *,
    agent: Mapping[str, Any],
    language: Any,
    command: Any,
    run_as: Any,
    timeout_seconds: Any,
    requested_by_user_id: int | None,
) -> int:
    """Queue ``command`` on the agent's device and return the run ID."""

    errors: dict[str, str] = {}
    text = str(command or "")
    if not text.strip():
        errors["command"] = "Enter a command."
    elif len(text) > MAX_COMMAND_LENGTH:
        errors["command"] = "The command is too long."
    if language not in LANGUAGES:
        errors["language"] = "Choose a shell."
    try:
        run_as = rmm_scripts.clean_run_as(run_as)
    except RunRequestError as exc:
        errors.update(exc.errors)
    if errors:
        raise RunRequestError(errors)
    problem = _device_problem(str(language), agent)
    if problem:
        raise RunRequestError({"device": problem})
    script = {
        "id": None,
        "name": f"Command ({LANGUAGES[language]})",
        "path": f"command/{language}",
        "language": language,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    shown = text if len(text) <= SHOWN_COMMAND_LENGTH else text[:SHOWN_COMMAND_LENGTH] + "…"
    run_id = await rmm_repo.create_run(
        script=script,
        content=text,
        agent_id=int(agent["id"]),
        company_id=int(agent["company_id"]),
        asset_id=int(agent["asset_id"]) if agent.get("asset_id") is not None else None,
        requested_by_user_id=requested_by_user_id,
        inputs=[{"kind": "command", "name": "Command", "value": shown, "is_variable": False, "sensitive": False}],
        payload_encrypted=encrypt_secret(json.dumps({"parameters": {}, "parameter_order": [], "env": {}, "files": []})),
        timeout_seconds=rmm_scripts.clamp_timeout(timeout_seconds, DEFAULT_TIMEOUT_SECONDS),
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + rmm_scripts.QUEUE_LIFETIME,
        run_source=RUN_SOURCE,
        run_as=run_as,
    )
    rmm_scripts.notify_agent(int(agent["id"]))
    return run_id
