"""Ad hoc commands run from an asset page's RMM menu."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader
from pydantic import ValidationError

from app.features.rmm import routes
from app.repositories import rmm as rmm_repo
from app.services import rmm_commands, rmm_scripts
from app.services.rmm_scripts import RunRequestError
from tests.test_rmm_scripts import _enrol, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]


def test_shells_offered_follow_the_device():
    assert [item["value"] for item in rmm_commands.languages_for({"os": "windows", "shells": "powershell"})] == ["powershell", "cmd"]
    assert [item["value"] for item in rmm_commands.languages_for({"os": "linux", "shells": "bash"})] == ["bash", "zsh"]
    assert [item["label"] for item in rmm_commands.languages_for({"os": "darwin", "shells": "zsh,pwsh"})] == ["Bash", "Zsh", "PowerShell"]
    assert rmm_commands.languages_for(None) == []


async def _agent(sqlite_db, shells="powershell,cmd,bash"):
    _r, agent = await _enrol(sqlite_db)
    await rmm_repo.record_agent_checkin(agent["id"], {"shells": shells, "run_as_user_supported": True})
    return await rmm_repo.get_agent(agent["id"])


@pytest.mark.anyio
async def test_command_is_queued_as_a_run_with_its_shell_and_run_as(sqlite_db):
    agent = await _agent(sqlite_db)
    run_id = await rmm_commands.queue_command(
        agent=agent, language="cmd", command="whoami\nexit /b 3", run_as="user", timeout_seconds=120,
        requested_by_user_id=None,
    )
    run = await rmm_repo.get_run(run_id)
    assert run["run_source"] == "command" and run["run_as"] == "user" and run["script_id"] is None
    assert run["script_name"] == "Command (Command Prompt)" and run["timeout_seconds"] == 120
    assert run["inputs"] == [{"kind": "command", "name": "Command", "value": "whoami\nexit /b 3",
                              "is_variable": False, "sensitive": False}]
    jobs = await rmm_scripts.claim_jobs(agent)
    assert [(job["language"], job["script"], job["run_as"]) for job in jobs] == [("cmd", "whoami\nexit /b 3", "user")]
    # The checksum the agent verifies matches the command.
    import hashlib
    assert jobs[0]["sha256"] == hashlib.sha256(b"whoami\nexit /b 3").hexdigest()


@pytest.mark.anyio
async def test_command_validation_and_device_problems(sqlite_db):
    agent = await _agent(sqlite_db, shells="powershell,bash")
    queue = dict(agent=agent, run_as="system", timeout_seconds=None, requested_by_user_id=None)
    with pytest.raises(RunRequestError) as exc:
        await rmm_commands.queue_command(language="python", command=" ", **{**queue, "run_as": "admin"})
    assert set(exc.value.errors) == {"command", "language", "run_as"}
    with pytest.raises(RunRequestError) as exc:
        await rmm_commands.queue_command(language="cmd", command="dir", **queue)
    assert exc.value.errors == {"device": "Update the RMM agent before running Command Prompt commands."}
    with pytest.raises(RunRequestError) as exc:
        await rmm_commands.queue_command(language="bash", command="ls", **queue)
    assert exc.value.errors == {"device": "Shell scripts cannot run on Windows."}
    with pytest.raises(RunRequestError) as exc:
        await rmm_commands.queue_command(language="cmd", command="dir", **{**queue, "agent": {**agent, "os": "linux"}})
    assert exc.value.errors == {"device": "Command Prompt commands run on Windows only."}
    assert await rmm_repo.list_runs(company_id=1) == []


@pytest.mark.anyio
async def test_command_api_needs_the_devices_agent_and_is_audited(sqlite_db, monkeypatch):
    agent = await _agent(sqlite_db)
    monkeypatch.setattr(routes, "_context", AsyncMock(return_value=({"id": None}, {}, 1, True)))
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)
    payload = routes.CommandRequest(language="powershell", command="Get-Date", run_as="user")
    with pytest.raises(HTTPException) as exc:
        await routes.run_command_api(99, payload, None)
    assert exc.value.status_code == 404
    result = await routes.run_command_api(10, payload, None)
    assert (await rmm_repo.get_run(result["run_id"]))["agent_id"] == agent["id"]
    metadata = audit.await_args.kwargs["metadata"]
    assert metadata["command"] == "Get-Date" and metadata["run_as"] == "user" and metadata["language"] == "powershell"
    bad = await routes.run_command_api(10, routes.CommandRequest(language="cmd", command="  "), None)
    assert bad.status_code == 400
    with pytest.raises(ValidationError):
        routes.CommandRequest(language="python", command="x")
    with pytest.raises(ValidationError):
        routes.CommandRequest(language="cmd", command="x", run_as="root")
    context = await routes.asset_rmm_context(1, 10, can_run=True)
    assert [item["value"] for item in context["command_languages"]] == ["powershell", "cmd"]
    assert (await routes.asset_rmm_context(1, 10, can_run=False))["command_languages"] == []


def test_asset_rmm_menu_offers_execute_command_with_its_modal():
    env = Environment(loader=FileSystemLoader(ROOT / "app/templates"))
    menu = env.from_string('{% import "rmm/_macros.html" as rmm_ui %}{{ rmm_ui.asset_menu(7, "PC", [], run_command=on) }}')
    html = menu.render(on=True)
    assert "Execute command" in html and 'aria-controls="rmm-command-modal"' in html
    assert "Execute command" not in menu.render(on=False)
    modal = env.get_template("rmm/_command_modal.html").render(
        asset={"id": 10}, asset_display_name="PC",
        rmm={"command_languages": [{"value": "powershell", "label": "PowerShell"}, {"value": "cmd", "label": "Command Prompt"}]})
    assert 'data-rmm-command-asset="10"' in modal
    assert '<option value="cmd">Command Prompt</option>' in modal
    assert '<option value="system">System</option>' in modal and '<option value="user">Logged in user</option>' in modal
    source = (ROOT / "app/templates/assets/detail.html").read_text()
    assert "run_command=" in source and "rmm/_command_modal.html" in source and "rmm_command.js" in source
