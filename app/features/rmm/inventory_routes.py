"""RMM device inventory (:mod:`app.services.rmm_inventory`).

Technician side (company scoped, ``menu.rmm_scripts``; write access asks for
a refresh):

* each asset page's Hardware and Software pages (:func:`asset_inventory_context`)
* ``POST /rmm/inventory/assets/{id}/refresh`` ask the device for a new snapshot
* ``GET /api/rmm/assets/{id}/inventory`` the latest snapshot and software as JSON

Agent side (``Authorization: Bearer`` token):

* ``GET /api/rmm/agent/inventory/config`` whether a snapshot is due
* ``POST /api/rmm/agent/inventory`` a snapshot
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.repositories import rmm as rmm_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import rmm_inventory, rmm_commands

from .routes import _context, _serialise, get_current_rmm_agent

router = APIRouter(tags=["RMM inventory"])


async def _company_agent(company_id: int, asset_id: int) -> dict[str, Any]:
    agent = await rmm_repo.get_asset_agent(asset_id)
    if not agent or agent.get("company_id") is None or int(agent["company_id"]) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This device has no RMM agent")
    return agent


async def asset_inventory_context(company_id: int, asset_id: int, *, can_refresh: bool) -> dict[str, Any] | None:
    """The asset page's Hardware and Software pages, or None without an agent."""

    agent = await rmm_repo.get_asset_agent(asset_id)
    if agent and agent.get("company_id") is not None and int(agent["company_id"]) != int(company_id):
        agent = None
    return await rmm_inventory.asset_inventory_context(agent, can_refresh=can_refresh)


@router.post("/rmm/inventory/assets/{asset_id:int}/refresh", summary="Ask a device for a new inventory snapshot")
async def refresh_asset_inventory(asset_id: int, request: Request):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, can_run = context
    if not can_run:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission to run scripts is required")
    agent = await _company_agent(company_id, asset_id)
    await rmm_inventory.request_refresh(agent, user.get("id"))
    await rmm_commands.queue_checkin({**agent, "asset_id": asset_id}, user.get("id"))
    await audit_service.record(
        action="rmm.inventory.refresh", request=request, user_id=user.get("id"), entity_type="asset",
        entity_id=asset_id,
    )
    form = await request.form()
    section = "software" if form.get("section") == "software" else "hardware"
    return flash_redirect(
        f"/assets/{asset_id}#{section}",
        "A full check-in task is queued to refresh this inventory without waiting for the next scheduled report.",
        "success",
    )


@router.get("/api/rmm/assets/{asset_id:int}/inventory", summary="A device's latest hardware and software snapshot")
async def asset_inventory_api(asset_id: int, request: Request):
    _user, _company, company_id, _can_run = await _context(request, api=True)
    agent = await _company_agent(company_id, asset_id)
    context = await rmm_inventory.asset_inventory_context(agent, can_refresh=False)
    inventory = context["inventory"] if context else None
    return {
        "inventory": _serialise(inventory),
        "software": [_serialise(item) for item in (context or {}).get("software", [])],
        "refresh_pending": bool(context and context["refresh_pending"]),
    }


# --------------------------------------------------------------------------- #
# Agent API
# --------------------------------------------------------------------------- #


class _Loose(BaseModel):
    # Text is trimmed and cut to size when stored, so long values do not
    # reject the whole snapshot.
    model_config = ConfigDict(extra="ignore")


class AgentDisk(_Loose):
    name: str = ""
    label: str | None = None
    file_system: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    free_bytes: int = Field(default=0, ge=0)
    system: bool = False


class AgentPhysicalDisk(_Loose):
    model: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    media_type: str | None = None
    bus_type: str | None = None
    serial: str | None = None


class AgentMemoryModule(_Loose):
    slot: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    speed_mhz: int | None = Field(default=None, ge=0)
    manufacturer: str | None = None
    part_number: str | None = None


class AgentNetworkAdapter(_Loose):
    name: str = ""
    mac: str | None = None
    addresses: list[str] = Field(default_factory=list, max_length=64)


class AgentProgram(_Loose):
    name: str = ""
    version: str | None = None
    publisher: str | None = None
    install_date: str | None = None
    source: str | None = None


class AgentInventory(_Loose):
    collected_at: str | None = None
    hostname: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    bios_version: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    os_build: str | None = None
    arch: str | None = None
    domain: str | None = None
    cpu_model: str | None = None
    cpu_cores: int | None = Field(default=None, ge=0)
    cpu_logical_processors: int | None = Field(default=None, ge=0)
    ram_bytes: int | None = Field(default=None, ge=0)
    last_boot_at: str | None = None
    disks: list[AgentDisk] = Field(default_factory=list, max_length=128)
    physical_disks: list[AgentPhysicalDisk] = Field(default_factory=list, max_length=128)
    memory_modules: list[AgentMemoryModule] = Field(default_factory=list, max_length=128)
    gpus: list[str] = Field(default_factory=list, max_length=128)
    network_adapters: list[AgentNetworkAdapter] = Field(default_factory=list, max_length=128)
    software: list[AgentProgram] = Field(default_factory=list, max_length=rmm_inventory.MAX_SOFTWARE)
    # A Proxmox VE host's state; cleaned and capped by rmm_proxmox.normalise.
    proxmox: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list, max_length=128)


@router.get("/api/rmm/agent/inventory/config", summary="Whether the RMM agent should send an inventory snapshot")
async def agent_inventory_config(agent: dict = Depends(get_current_rmm_agent)):
    return await rmm_inventory.agent_config(agent)


@router.post("/api/rmm/agent/inventory", summary="Send a hardware and software inventory snapshot")
async def agent_inventory(payload: AgentInventory, agent: dict = Depends(get_current_rmm_agent)):
    return await rmm_inventory.ingest(agent, payload.model_dump())
