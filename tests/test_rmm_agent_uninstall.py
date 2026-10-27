"""Uninstalling the tray and RMM agent from a device, after an optional offboarding script."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader

from app.features.rmm import routes
from app.repositories import rmm as rmm_repo
from app.services import rmm_agent_uninstall, rmm_scripts
from app.services.rmm_scripts import RunRequestError
from tests.test_rmm_scripts import _enrol, _sync, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]


def test_scripts_hand_the_removal_to_a_process_outside_the_agent():
    windows = rmm_agent_uninstall.builtin_script({"os": "windows"})
    assert windows["language"] == "powershell" and windows["path"] == "builtin/agent_uninstall.ps1"
    content = windows["content"]
    assert "schtasks.exe /Create" in content and "/RU SYSTEM" in content and "schtasks.exe /Run" in content
    # The tray goes first, so it cannot reinstall the RMM agent, then the RMM agent.
    order = [content.index(step) for step in ("Start-Sleep -Seconds 20", "msiexec.exe", "Remove-Service 'MyPortalTrayService'",
                                              "& $rmm uninstall", "Remove-Service 'MyPortalRMMAgent'")]
    assert order == sorted(order)
    for path in ("'MyPortalTray'", "'MyPortal\\tray'", "'MyPortal RMM'", "HKLM:\\SOFTWARE\\MyPortal\\Tray"):
        assert path in content

    for os_name in ("linux", "darwin", "macos"):
        script = rmm_agent_uninstall.builtin_script({"os": os_name})
        assert script["language"] == "bash" and script["path"] == "builtin/agent_uninstall.sh"
    content = rmm_agent_uninstall.builtin_script({"os": "linux"})["content"]
    assert "systemd-run" in content and "launchctl bootstrap system" in content
    for path in ("/opt/myportal-tray", "/var/lib/myportal-tray", "/opt/myportal-rmm", "/var/lib/myportal-rmm",
                 "/Library/MyPortal/Tray", "/Library/Application Support/MyPortal RMM"):
        assert path in content


def test_uninstall_is_offered_where_the_agents_install():
    assert all(rmm_agent_uninstall.supported({"os": name}) for name in ("windows", "linux", "darwin", "macos"))
    assert not rmm_agent_uninstall.supported({"os": "freebsd"})
    assert not rmm_agent_uninstall.supported(None)


@pytest.mark.anyio
async def test_uninstall_without_offboarding_queues_the_removal_once(sqlite_db, monkeypatch):
    _r, agent = await _enrol(sqlite_db)
    agent = await rmm_repo.get_agent(agent["id"])
    queued = await rmm_agent_uninstall.queue_uninstall(agent=agent, requested_by_user_id=9)
    assert queued["stage"] == "uninstall"
    run = await rmm_repo.get_run(queued["run_id"])
    assert run["run_source"] == "agent_uninstall" and run["script_id"] is None and run["run_as"] == "system"
    assert run["script_name"] == "Uninstall MyPortal agents"
    with pytest.raises(RunRequestError) as exc:
        await rmm_agent_uninstall.queue_uninstall(agent=agent, requested_by_user_id=9)
    assert "busy" in exc.value.errors
    with pytest.raises(RunRequestError) as exc:
        await rmm_agent_uninstall.queue_uninstall(agent={**agent, "os": "freebsd"}, requested_by_user_id=9)
    assert "device" in exc.value.errors

    jobs = await rmm_scripts.claim_jobs(agent)
    assert [job["script"] for job in jobs] == [rmm_agent_uninstall.builtin_script(agent)["content"]]
    revoke = AsyncMock()
    monkeypatch.setattr(rmm_agent_uninstall.tray_repo, "revoke_device", revoke)
    await rmm_scripts.record_result(agent, queued["run_id"], {"exit_code": 0})
    assert await rmm_agent_uninstall.run_finished(queued["run_id"]) is None
    # Neither agent's token is accepted again.
    assert (await rmm_repo.get_agent(agent["id"]))["status"] == "uninstalled"
    revoke.assert_awaited_once_with(5)


@pytest.mark.anyio
async def test_removal_waits_for_the_offboarding_script_to_succeed(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/Offboard.ps1": "Write-Output 'cleaning up'\n"})
    script_id = (await rmm_repo.list_scripts())[0]["id"]
    _r, agent = await _enrol(sqlite_db)
    agent = await rmm_repo.get_agent(agent["id"])

    with pytest.raises(RunRequestError) as exc:
        await rmm_agent_uninstall.queue_uninstall(agent=agent, requested_by_user_id=9, offboarding_script_id=999)
    assert "offboarding_script_id" in exc.value.errors

    queued = await rmm_agent_uninstall.queue_uninstall(agent=agent, requested_by_user_id=9, offboarding_script_id=script_id)
    assert queued["stage"] == "offboarding"
    offboarding = await rmm_repo.get_run(queued["run_id"])
    assert offboarding["run_source"] == "offboarding" and offboarding["script_id"] == script_id
    # Still one uninstall at a time while the offboarding script is pending.
    with pytest.raises(RunRequestError):
        await rmm_agent_uninstall.queue_uninstall(agent=agent, requested_by_user_id=9)

    # A failed offboarding run leaves the agents installed.
    await rmm_scripts.claim_jobs(agent)
    await rmm_scripts.record_result(agent, queued["run_id"], {"exit_code": 1})
    assert await rmm_agent_uninstall.run_finished(queued["run_id"]) is None
    assert not await rmm_repo.has_active_source_run(agent["id"], run_source="agent_uninstall")

    queued = await rmm_agent_uninstall.queue_uninstall(agent=agent, requested_by_user_id=9, offboarding_script_id=script_id)
    await rmm_scripts.claim_jobs(agent)
    await rmm_scripts.record_result(agent, queued["run_id"], {"exit_code": 0})
    removal_id = await rmm_agent_uninstall.run_finished(queued["run_id"])
    removal = await rmm_repo.get_run(removal_id)
    assert removal["run_source"] == "agent_uninstall" and removal["requested_by_user_id"] == 9
    assert removal["asset_id"] == 10
    assert (await rmm_repo.get_agent(agent["id"]))["status"] == "active"


@pytest.mark.anyio
async def test_uninstall_api_is_for_super_admins_and_audited(sqlite_db, monkeypatch):
    _r, agent = await _enrol(sqlite_db)
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)
    payload = routes.UninstallRequest()
    monkeypatch.setattr(routes, "_context", AsyncMock(return_value=({"id": 9}, {}, 1, True)))
    with pytest.raises(HTTPException) as exc:
        await routes.uninstall_agents_api(10, payload, None)
    assert exc.value.status_code == 403

    monkeypatch.setattr(routes, "_context", AsyncMock(return_value=({"id": 9, "is_super_admin": True}, {}, 1, True)))
    with pytest.raises(HTTPException) as exc:
        await routes.uninstall_agents_api(99, payload, None)
    assert exc.value.status_code == 404
    result = await routes.uninstall_agents_api(10, payload, None)
    assert result["stage"] == "uninstall"
    assert (await rmm_repo.get_run(result["run_id"]))["agent_id"] == agent["id"]
    assert audit.await_args.kwargs["action"] == "rmm.agent_uninstall.run"
    assert (await routes.uninstall_agents_api(10, payload, None)).status_code == 400
    assert (await routes.asset_rmm_context(1, 10, can_run=True))["uninstall"] is True
    assert (await routes.asset_rmm_context(1, 10, can_run=False))["uninstall"] is False


def test_asset_pages_offer_uninstall_and_delete():
    for page in ("index.html", "detail.html"):
        source = (ROOT / "app/templates/assets" / page).read_text()
        assert "data-asset-uninstall" in source and "assets/_uninstall_modal.html" in source
        assert "asset_uninstall.js" in source and "data-asset-delete" in source
    assert "asset_delete.js" in (ROOT / "app/templates/assets/detail.html").read_text()
    env = Environment(loader=FileSystemLoader(ROOT / "app/templates"))
    html = env.get_template("assets/_uninstall_modal.html").render()
    assert 'id="asset-uninstall-modal"' in html and "data-asset-uninstall-script" in html
    assert "data-asset-uninstall-confirm" in html


@pytest.mark.anyio
async def test_deleting_an_asset_keeps_its_agents_enrolled_to_register_again(sqlite_db):
    from app.repositories import assets as assets_repo

    await sqlite_db.execute("CREATE TABLE asset_relationships (id INTEGER PRIMARY KEY, target_type TEXT, target_id INT)")
    await sqlite_db.execute("INSERT INTO tray_devices (id, company_id, asset_id, device_uid) VALUES (5, 1, 10, 'uid')")
    _r, agent = await _enrol(sqlite_db)
    await assets_repo.delete_asset(10)
    assert await assets_repo.get_asset_by_id(10) is None
    # No asset, so their next check-in provisions a new one; their tokens still work.
    assert (await sqlite_db.fetch_one("SELECT asset_id, status FROM tray_devices WHERE id = 5")) == {"asset_id": None, "status": "active"}
    remaining = await rmm_repo.get_agent(agent["id"])
    assert remaining["asset_id"] is None and remaining["status"] == "active"
