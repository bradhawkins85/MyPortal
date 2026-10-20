"""Repairing the tray agent from an asset page's RMM menu."""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader

from app.features.rmm import routes
from app.repositories import rmm as rmm_repo
from app.services import rmm_scripts, rmm_tray_repair
from app.services.rmm_scripts import RunRequestError
from tests.test_rmm_scripts import _enrol, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]


def test_repair_script_clears_the_tray_enrolment_and_restarts_the_service():
    script = rmm_tray_repair.builtin_script()
    content = script["content"]
    assert script["language"] == "powershell" and script["id"] is None
    assert script["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert "Stop-Service -Name $serviceName" in content and "Start-Service -Name $serviceName" in content
    assert "$serviceName = 'MyPortalTrayService'" in content
    assert "'MyPortal\\tray'" in content
    assert "@('tray-state.json', 'tray-config.json')" in content
    assert "OpenSubKey('SOFTWARE\\MyPortal\\Tray', $true)" in content and "DeleteValue('DeviceUID')" in content
    # The steps run in order: stop, delete files, clear the device ID, start.
    order = [content.index(step) for step in ("Stop-Service", "Remove-Item", "DeleteValue", "Start-Service")]
    assert order == sorted(order)


def test_repair_is_offered_for_windows_agents_only():
    assert rmm_tray_repair.supported({"os": "windows"})
    assert not rmm_tray_repair.supported({"os": "darwin"})
    assert not rmm_tray_repair.supported(None)


@pytest.mark.anyio
async def test_repair_is_queued_once_as_a_built_in_run(sqlite_db):
    _r, agent = await _enrol(sqlite_db)
    agent = await rmm_repo.get_agent(agent["id"])
    run_id = await rmm_tray_repair.queue_repair(agent=agent, requested_by_user_id=None)
    run = await rmm_repo.get_run(run_id)
    assert run["run_source"] == "tray_repair" and run["script_id"] is None and run["run_as"] == "system"
    assert run["script_name"] == "Repair tray agent" and run["script_path"] == "builtin/tray_repair.ps1"
    with pytest.raises(RunRequestError) as exc:
        await rmm_tray_repair.queue_repair(agent=agent, requested_by_user_id=None)
    assert "busy" in exc.value.errors
    jobs = await rmm_scripts.claim_jobs(agent)
    assert [(job["language"], job["script"]) for job in jobs] == [("powershell", rmm_tray_repair.builtin_script()["content"])]
    with pytest.raises(RunRequestError) as exc:
        await rmm_tray_repair.queue_repair(agent={**agent, "os": "linux"}, requested_by_user_id=None)
    assert exc.value.errors == {"device": "Tray agent repair runs on Windows devices only."}


@pytest.mark.anyio
async def test_repair_api_needs_the_devices_agent_and_is_audited(sqlite_db, monkeypatch):
    _r, agent = await _enrol(sqlite_db)
    monkeypatch.setattr(routes, "_context", AsyncMock(return_value=({"id": None}, {}, 1, True)))
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)
    with pytest.raises(HTTPException) as exc:
        await routes.tray_repair_api(99, None)
    assert exc.value.status_code == 404
    result = await routes.tray_repair_api(10, None)
    assert (await rmm_repo.get_run(result["run_id"]))["agent_id"] == agent["id"]
    assert audit.await_args.kwargs["action"] == "rmm.tray_repair.run"
    assert (await routes.tray_repair_api(10, None)).status_code == 400
    assert (await routes.asset_rmm_context(1, 10, can_run=True))["tray_repair"] is True
    assert (await routes.asset_rmm_context(1, 10, can_run=False))["tray_repair"] is False


def test_asset_rmm_menu_offers_repair_tray_agent():
    env = Environment(loader=FileSystemLoader(ROOT / "app/templates"))
    menu = env.from_string('{% import "rmm/_macros.html" as rmm_ui %}{{ rmm_ui.asset_menu(7, "PC", [], tray_repair=on) }}')
    html = menu.render(on=True)
    assert "Repair tray agent" in html and 'data-rmm-tray-repair="7"' in html
    assert "Repair tray agent" not in menu.render(on=False)
    source = (ROOT / "app/templates/assets/detail.html").read_text()
    assert "tray_repair=rmm.tray_repair" in source and "rmm_tray_repair.js" in source
