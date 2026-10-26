"""Repair the MyPortal Tray Agent on a device through its RMM agent.

The repair is a built-in PowerShell script (``rmm_builtin/tray_repair.ps1``)
queued as an ordinary run (``run_source`` ``tray_repair``), so the device's
queue, live output, result view and run history all apply. It stops the tray
service, deletes ``tray-state.json`` and ``tray-config.json`` from
``%ProgramData%\\MyPortal\\tray``, removes ``DeviceUID`` from
``HKLM\\SOFTWARE\\MyPortal\\Tray`` and starts the service again, which then
re-enrols with its saved install token. It works while the tray agent itself
is broken or disconnected, because only the RMM agent has to be online.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from app.repositories import rmm as rmm_repo
from app.security.encryption import encrypt_secret
from app.services import rmm_scripts
from app.services.rmm_scripts import RunRequestError

RUN_SOURCE = "tray_repair"
SCRIPT_NAME = "Repair tray agent"
SCRIPT_PATH = "builtin/tray_repair.ps1"
TIMEOUT_SECONDS = 300
_BUILTIN = Path(__file__).parent / "rmm_builtin" / "tray_repair.ps1"


@lru_cache(maxsize=1)
def _content() -> str:
    return _BUILTIN.read_text(encoding="utf-8")


def builtin_script() -> dict[str, Any]:
    content = _content()
    return {
        "id": None,
        "name": SCRIPT_NAME,
        "path": SCRIPT_PATH,
        "language": "powershell",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def supported(agent: Mapping[str, Any] | None) -> bool:
    """The repair edits the Windows registry and services, so Windows only."""

    return bool(agent) and str(agent.get("os") or "").lower() == "windows"


async def queue_repair(*, agent: Mapping[str, Any], requested_by_user_id: int | None) -> int:
    """Queue the tray repair on the agent's device and return the run ID."""

    if not supported(agent):
        raise RunRequestError({"device": "Tray agent repair runs on Windows devices only."})
    if await rmm_repo.has_active_source_run(int(agent["id"]), run_source=RUN_SOURCE):
        raise RunRequestError({"busy": "A tray agent repair is already queued or running on this device."})
    script = rmm_scripts.always_run(builtin_script(), agent)
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
