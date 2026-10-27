"""Uninstall the MyPortal Tray Agent and RMM agent from a device.

The removal is a built-in script queued as an ordinary run (``run_source``
``agent_uninstall``): ``rmm_builtin/agent_uninstall.ps1`` on Windows and
``agent_uninstall.sh`` on Linux and macOS. The RMM agent cannot remove itself
while it runs the script, so the script starts the removal outside the agent
(a SYSTEM scheduled task, a transient systemd unit or a one-off LaunchDaemon)
and exits; the removal waits for the agent to report the run, removes the tray
and then the RMM agent.

An offboarding script from the script library can run first. It is queued as
its own run (``run_source`` ``offboarding``) and the removal is queued only when
it completes with exit code 0 (see :func:`run_finished`), so a failed, timed
out, aborted or expired offboarding run leaves both agents installed.

When the removal run completes, the device's RMM agent is marked
``uninstalled`` and its tray device revoked, so neither token is accepted
again. The asset stays, with its history; delete it separately.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from app.core.logging import log_info, log_warning
from app.repositories import rmm as rmm_repo
from app.repositories import tray as tray_repo
from app.security.encryption import encrypt_secret
from app.services import rmm_scripts
from app.services.rmm_scripts import RunRequestError

RUN_SOURCE = "agent_uninstall"
OFFBOARDING_SOURCE = "offboarding"
SCRIPT_NAME = "Uninstall MyPortal agents"
TIMEOUT_SECONDS = 300
_BUILTIN = Path(__file__).parent / "rmm_builtin"
_SCRIPTS = {
    "windows": ("agent_uninstall.ps1", "powershell"),
    "linux": ("agent_uninstall.sh", "bash"),
    "darwin": ("agent_uninstall.sh", "bash"),
}


@lru_cache(maxsize=None)
def _content(file_name: str) -> str:
    return (_BUILTIN / file_name).read_text(encoding="utf-8")


def _platform(agent: Mapping[str, Any] | None) -> str:
    return rmm_scripts.normalise_platform((agent or {}).get("os"))


def supported(agent: Mapping[str, Any] | None) -> bool:
    """Windows, Linux and macOS, the platforms the tray and RMM agent install on."""

    return bool(agent) and _platform(agent) in _SCRIPTS


def builtin_script(agent: Mapping[str, Any]) -> dict[str, Any]:
    file_name, language = _SCRIPTS[_platform(agent)]
    content = _content(file_name)
    return {
        "id": None,
        "name": SCRIPT_NAME,
        "path": f"builtin/{file_name}",
        "language": language,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


async def _busy(agent_id: int) -> bool:
    for source in (RUN_SOURCE, OFFBOARDING_SOURCE):
        if await rmm_repo.has_active_source_run(agent_id, run_source=source):
            return True
    return False


async def _queue_removal(agent: Mapping[str, Any], requested_by_user_id: int | None) -> int:
    script = rmm_scripts.always_run(builtin_script(agent), agent)
    run_id = await rmm_repo.create_run(
        script=script,
        content=script["content"],
        agent_id=int(agent["id"]),
        company_id=int(agent["company_id"]),
        asset_id=int(agent["asset_id"]) if agent.get("asset_id") is not None else None,
        requested_by_user_id=requested_by_user_id,
        inputs=[],
        payload_encrypted=encrypt_secret(json.dumps({"parameters": {}, "parameter_order": [], "env": {}, "files": []})),
        timeout_seconds=TIMEOUT_SECONDS,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + rmm_scripts.QUEUE_LIFETIME,
        run_source=RUN_SOURCE,
    )
    rmm_scripts.notify_agent(int(agent["id"]))
    return run_id


async def queue_uninstall(
    *,
    agent: Mapping[str, Any],
    requested_by_user_id: int | None,
    offboarding_script_id: int | None = None,
) -> dict[str, Any]:
    """Queue the offboarding script, when one is chosen, or else the removal.

    Returns ``{"run_id", "stage"}`` where ``stage`` is ``offboarding`` or
    ``uninstall``. Raises :class:`RunRequestError` when it cannot be queued."""

    if not supported(agent):
        raise RunRequestError({"device": "Uninstall runs on Windows, Linux and macOS devices only."})
    if await _busy(int(agent["id"])):
        raise RunRequestError({"busy": "An uninstall is already queued or running on this device."})
    if offboarding_script_id is None:
        return {"run_id": await _queue_removal(agent, requested_by_user_id), "stage": "uninstall"}

    script = await rmm_repo.get_script(int(offboarding_script_id), with_content=True)
    if not rmm_repo.script_available_to(script, int(agent["company_id"])):
        raise RunRequestError({"offboarding_script_id": "That script is no longer available. Sync scripts and try again."})
    try:
        entries = rmm_scripts.validate_entries(script, {})
    except RunRequestError as exc:
        raise RunRequestError({
            "offboarding_script_id": "The offboarding script needs values entered; give its inputs defaults to use it here.",
        }) from exc
    run_id = await rmm_scripts.queue_for_agent(
        script=script,
        agent=agent,
        entries=entries,
        inputs=rmm_scripts.stored_inputs(script, entries),
        timeout_seconds=rmm_scripts.clamp_timeout(None, int(script.get("default_timeout_seconds") or 600)),
        requested_by_user_id=requested_by_user_id,
        run_source=OFFBOARDING_SOURCE,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + rmm_scripts.QUEUE_LIFETIME,
    )
    return {"run_id": run_id, "stage": "offboarding"}


async def run_finished(run_id: int) -> int | None:
    """Move an uninstall on after one of its runs finished.

    A completed offboarding run queues the removal and returns its run ID; a
    completed removal retires the device's agents. Anything else stops here."""

    run = await rmm_repo.get_run(run_id)
    if not run or run.get("status") != "completed" or run.get("agent_id") is None:
        return None
    if run.get("run_source") == OFFBOARDING_SOURCE:
        agent = await rmm_repo.get_agent(int(run["agent_id"]))
        if not agent or agent.get("status") != "active":
            return None
        try:
            removal_id = await _queue_removal(
                {**agent, "company_id": int(run["company_id"]), "asset_id": run.get("asset_id")},
                int(run["requested_by_user_id"]) if run.get("requested_by_user_id") is not None else None,
            )
        except Exception as exc:  # noqa: BLE001 - the offboarding result is already stored
            log_warning("Could not queue the agent uninstall after offboarding", run_id=run_id, error=str(exc))
            return None
        log_info("Offboarding finished; agent uninstall queued", run_id=run_id, uninstall_run_id=removal_id)
        return removal_id
    if run.get("run_source") == RUN_SOURCE:
        agent = await rmm_repo.get_agent(int(run["agent_id"]))
        if agent:
            await rmm_repo.set_agent_status(int(agent["id"]), "uninstalled")
            if agent.get("tray_device_id") is not None:
                await tray_repo.revoke_device(int(agent["tray_device_id"]))
        log_info("MyPortal agents uninstalled", run_id=run_id, agent_id=run["agent_id"], asset_id=run.get("asset_id"))
    return None
