"""An authorized manual check-in uses the fast job channel, not a timer flag."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader

from app.features.rmm import routes
from app.repositories import rmm as repo, tray as tray_repo
from app.services import rmm_commands, rmm_scripts, tray
from tests.test_rmm_scripts import _enrol, anyio_backend, sqlite_db  # noqa: F401


@pytest.mark.anyio
async def test_checkin_is_an_immediate_audited_job(sqlite_db):
    _, agent = await _enrol(sqlite_db)
    agent = {**await repo.get_agent(agent["id"]), "run_anyway_supported": True}
    run_id = await rmm_commands.queue_checkin(agent, None)
    run = await repo.get_run(run_id)
    assert run["script_path"] == "agent/checkin"
    assert run["run_source"] == "command"
    assert run["run_as"] == "system"
    jobs = await rmm_scripts.claim_jobs(agent)
    assert len(jobs) == 1 and jobs[0]["id"] == run_id
    assert jobs[0]["path"] == "agent/checkin" and jobs[0]["run_anyway"]
    # Older agents run the fallback and show an actionable upgrade error.
    assert "Update the MyPortal RMM agent" in jobs[0]["script"]


@pytest.mark.anyio
async def test_checkin_route_enforces_company_and_write_access(sqlite_db, monkeypatch):
    await _enrol(sqlite_db)
    context = AsyncMock(return_value=({"id": None}, {}, 1, True))
    audit = AsyncMock()
    monkeypatch.setattr(routes, "_context", context)
    monkeypatch.setattr(routes.audit_service, "record", audit)
    monkeypatch.setattr(tray_repo, "list_active_devices_by_asset_ids", AsyncMock(return_value={10: {"id": 5}}))
    wake = AsyncMock()
    monkeypatch.setattr(tray, "dispatch_device_event", wake)
    response = await routes.checkin_now(10, None)
    assert response.status_code == 303 and "/assets/10" in response.headers["location"]
    context.assert_awaited_once_with(None, write=True)
    wake.assert_awaited_once_with(5, "checkin_now", initiated_by_user_id=None)
    assert audit.await_args.kwargs["action"] == "rmm.checkin.queued"
    context.return_value = ({"id": None}, {}, 2, True)
    with pytest.raises(HTTPException) as exc:
        await routes.checkin_now(10, None)
    assert exc.value.status_code == 404
    context.return_value = RedirectResponse("/login", status_code=303)
    assert (await routes.checkin_now(10, None)).headers["location"] == "/login"


@pytest.mark.anyio
async def test_tray_wakeup_is_durable_when_another_worker_owns_socket(monkeypatch):
    monkeypatch.setattr(tray.tray_repo, "get_device_by_id", AsyncMock(return_value={"id": 5, "device_uid": "device", "status": "active"}))
    pending = AsyncMock(return_value=[])
    monkeypatch.setattr(tray.tray_repo, "get_queued_commands_for_device", pending)
    save = AsyncMock(return_value=9)
    complete = AsyncMock()
    monkeypatch.setattr(tray.tray_repo, "log_command", save)
    monkeypatch.setattr(tray.tray_repo, "mark_command_delivered", complete)
    monkeypatch.setattr(tray, "send_to_device", AsyncMock(return_value=False))
    assert not await tray.dispatch_device_event(5, "defender_commands")
    assert save.await_args.kwargs["status"] == "queued"
    complete.assert_not_awaited()
    pending.return_value = [{"command": "defender_commands"}]
    assert not await tray.dispatch_device_event(5, "defender_commands")
    save.assert_awaited_once()


def test_asset_checkin_action_uses_existing_header_and_csrf_form():
    root = Path(__file__).parents[1] / "app/templates"
    source = (root / "assets/detail.html").read_text()
    fragment = source.split("{% block header_actions %}", 1)[1].split("{% endblock %}", 1)[0]
    env = Environment(loader=FileSystemLoader(root))
    template = env.from_string('{% from "macros/header.html" import page_header_actions with context %}' + fragment)
    agent = {"os": "windows"}
    values = {"asset": {"id": 10, "company_id": 1}, "rmm": {"can_run": True, "agent": agent}, "can_edit": False, "customer_safe": False}
    html = template.render(**values)
    assert "Check in now" in html and 'form="asset-checkin-form"' in html
    assert "Check in now" not in template.render(**{**values, "customer_safe": True})
    assert "Check in now" not in template.render(**{**values, "rmm": {"can_run": False, "agent": agent}})
    # The check-in needs a deployed agent.
    assert "Check in now" not in template.render(**{**values, "rmm": {"can_run": True, "agent": None}})
    form = source[source.index('<form id="asset-checkin-form"'):].split("</form>", 1)[0] + "</form>"
    rendered = env.from_string(form).render(asset={"id": 10}, csrf_token="test-csrf")
    assert 'method="post"' in rendered and '/rmm/assets/10/checkin' in rendered
    assert "test-csrf" in rendered
