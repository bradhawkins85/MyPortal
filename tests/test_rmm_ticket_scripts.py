"""My Portal RMM Script ticket automation action: queueing, results and ticket notes."""
from __future__ import annotations

import pytest

from app.repositories import rmm as rmm_repo
from app.services import automations, modules, rmm_scripts
from app.services import rmm_ticket_scripts as ticket_scripts
from tests.test_rmm_scripts import _enrol, _sync, anyio_backend, sqlite_db  # noqa: F401 - fixtures

CLEANUP = (
    "param([Parameter(Mandatory = $true)] [string]$Drive, [string]$Computer, [string]$Reason)\n"
    "Write-Output \"Cleaned $Drive on $Computer\"\n"
)


@pytest.fixture
async def tickets(sqlite_db, monkeypatch):
    """Tickets, linked assets, replies and statuses kept in memory."""

    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    state = {
        "tickets": {42: {"id": 42, "ticket_number": "1042", "company_id": 1, "status": "open",
                         "subject": "Low disk space on PC-01"}},
        "assets": {42: [{"asset_id": 10, "name": "PC-01"}, {"asset_id": 11, "name": "PC-02"}]},
        "replies": [],
    }

    async def get_ticket(ticket_id):
        return state["tickets"].get(ticket_id)

    async def list_ticket_assets(ticket_id):
        return state["assets"].get(ticket_id, [])

    async def create_reply(**kwargs):
        state["replies"].append(kwargs)
        return kwargs

    async def update_ticket(ticket_id, **fields):
        state["tickets"][ticket_id].update(fields)
        return state["tickets"][ticket_id]

    async def validate_status_choice(value, *, allow_hidden=True):
        if value not in {"resolved", "closed"}:
            raise ValueError("Select a valid status to apply.")
        return value

    monkeypatch.setattr(tickets_repo, "get_ticket", get_ticket)
    monkeypatch.setattr(tickets_repo, "list_ticket_assets", list_ticket_assets)
    monkeypatch.setattr(tickets_repo, "create_reply", create_reply)
    monkeypatch.setattr(tickets_repo, "update_ticket", update_ticket)
    monkeypatch.setattr(tickets_service, "validate_status_choice", validate_status_choice)
    await _sync(monkeypatch, {
        "rmm/Common/Cleanup-Disk.ps1": CLEANUP,
        "rmm/Companies/Contoso/Cleanup-Disk.ps1": CLEANUP,
        "rmm/Common/linux/tidy.sh": "echo tidy\n",
    }, created={})
    return state


async def _script(path):
    return next(script for script in await rmm_repo.list_scripts() if script["path"] == path)


@pytest.mark.anyio
async def test_find_script_by_path_name_or_id_prefers_the_company_folder(tickets):
    common = await _script("rmm/Common/Cleanup-Disk.ps1")
    own = await _script("rmm/Companies/Contoso/Cleanup-Disk.ps1")
    assert (await ticket_scripts.find_script("cleanup-disk", 1))["id"] == own["id"]
    assert (await ticket_scripts.find_script("rmm/Common/Cleanup-Disk.ps1", 1))["id"] == common["id"]
    assert (await ticket_scripts.find_script(str(common["id"]), 2))["id"] == common["id"]
    assert (await ticket_scripts.find_script("Cleanup-Disk", 2))["id"] == common["id"]
    assert await ticket_scripts.find_script(str(own["id"]), 2) is None  # another company's script
    assert await ticket_scripts.find_script("missing", 1) is None
    assert "content" in await ticket_scripts.find_script("Cleanup-Disk", 1)


@pytest.mark.anyio
async def test_values_map_by_field_name_or_form_key(tickets):
    script = await _script("rmm/Common/Cleanup-Disk.ps1")
    assert ticket_scripts.submitted_values(script, {"drive": "C", "param:Computer": "PC"}) == {
        "param:Drive": "C", "param:Computer": "PC",
    }
    assert ticket_scripts.submitted_values(script, '{"Reason": true}') == {"param:Reason": "true"}
    assert ticket_scripts.submitted_values(script, None) == {}
    with pytest.raises(rmm_scripts.RunRequestError, match="no field named Nope"):
        ticket_scripts.submitted_values(script, {"Nope": 1})
    with pytest.raises(rmm_scripts.RunRequestError, match="JSON object"):
        ticket_scripts.submitted_values(script, "[1]")


@pytest.mark.anyio
async def test_action_queues_on_linked_agents_and_posts_the_result(tickets, sqlite_db):
    _enrolment, agent = await _enrol(sqlite_db)
    result = await modules._invoke_rmm_script({}, {
        "ticket_id": "42",
        "script": "Cleanup-Disk",
        "values": {"Drive": "C", "Computer": "{{asset.name}}", "Reason": "{{ticket.subject}}"},
        "success_status": "resolved",
    })
    assert result["status"] == "succeeded"
    assert [item["asset_id"] for item in result["queued"]] == [10]
    assert result["skipped"] == [{"asset_id": 11, "asset_name": "PC-02", "message": "No MyPortal RMM agent is installed."}]
    run_id = result["queued"][0]["run_id"]
    run = await rmm_repo.get_run(run_id)
    assert run["run_source"] == "ticket" and run["script_path"] == "rmm/Companies/Contoso/Cleanup-Disk.ps1"
    assert (await rmm_repo.get_ticket_run(run_id))["ticket_id"] == 42

    job = (await rmm_scripts.claim_jobs(agent))[0]
    assert job["parameters"] == {"Drive": "C", "Computer": "PC-01", "Reason": "Low disk space on PC-01"}

    # A second ticket event while the run is active does not queue it again.
    again = await modules._invoke_rmm_script({}, {"ticket_id": 42, "script": "Cleanup-Disk", "values": {"Drive": "C"}})
    assert again["status"] == "skipped" and "Already queued or running" in again["reason"]

    await rmm_scripts.record_result(agent, run_id, {"exit_code": 0, "stdout": "Freed <5 GB>", "stderr": ""})
    note = tickets["replies"][-1]
    assert note["ticket_id"] == 42 and note["is_internal"] and note["external_reference"] == f"rmm-run:{run_id}"
    assert "RMM script completed" in note["body"] and "PC-01" in note["body"] and "exit code 0" in note["body"]
    assert "Freed &lt;5 GB&gt;" in note["body"]
    assert tickets["tickets"][42]["status"] == "resolved"

    # The agent retrying its report does not post a second note.
    await ticket_scripts.record_result(run_id, status="completed", exit_code=0, stdout="", stderr="", error=None)
    assert len(tickets["replies"]) == 1


@pytest.mark.anyio
async def test_failed_run_posts_a_note_and_leaves_the_status(tickets, sqlite_db):
    _enrolment, agent = await _enrol(sqlite_db)
    result = await ticket_scripts.run_for_ticket(
        ticket=tickets["tickets"][42], script="Cleanup-Disk", asset_id=10, values={"Drive": "C"},
        success_status="resolved",
    )
    run_id = result["queued"][0]["run_id"]
    await rmm_scripts.claim_jobs(agent)
    await rmm_scripts.record_result(agent, run_id, {"exit_code": 3, "stdout": "", "stderr": "Access denied"})
    body = tickets["replies"][-1]["body"]
    assert "RMM script failed" in body and "exit code 3" in body and "Access denied" in body
    assert tickets["tickets"][42]["status"] == "open"


@pytest.mark.anyio
async def test_cancelled_run_is_posted_on_the_tick(tickets, sqlite_db):
    await _enrol(sqlite_db)
    result = await ticket_scripts.run_for_ticket(
        ticket=tickets["tickets"][42], script="Cleanup-Disk", values={"Drive": "C"},
    )
    run_id = result["queued"][0]["run_id"]
    await ticket_scripts.tick()
    assert tickets["replies"] == []  # still queued
    assert await rmm_repo.cancel_run(run_id)
    await ticket_scripts.tick()
    await ticket_scripts.tick()
    assert len(tickets["replies"]) == 1 and "was cancelled" in tickets["replies"][0]["body"]


@pytest.mark.anyio
@pytest.mark.parametrize("payload, status, message", [
    ({"script": "missing"}, "error", "was not found"),
    ({"script": ""}, "error", "Choose the RMM script"),
    ({"script": "Cleanup-Disk"}, "error", "required"),
    ({"script": "Cleanup-Disk", "values": {"Nope": 1}}, "error", "no field named"),
    ({"script": "Cleanup-Disk", "values": {"Drive": "C"}, "success_status": "bogus"}, "error", "Success status"),
    ({"script": "Cleanup-Disk", "values": {"Drive": "C"}, "run_as": "admin"}, "error", "System or Logged in user"),
    ({"script": "Cleanup-Disk", "values": {"Drive": "C"}, "asset_id": 99}, "error", "not linked"),
    ({"script": "tidy"}, "skipped", "Shell scripts cannot run on Windows"),
])
async def test_action_reports_problems(tickets, sqlite_db, payload, status, message):
    await _enrol(sqlite_db)
    result = await modules._invoke_rmm_script({}, {"ticket_id": 42, **payload})
    assert result["status"] == status
    assert message in (result.get("error") or result.get("reason"))


@pytest.mark.anyio
async def test_a_variable_without_a_value_fails_only_that_device(tickets, sqlite_db):
    await _enrol(sqlite_db)
    await _enrol(sqlite_db, tray_id=6, asset_id=11, uid="agent-uid-0002")
    await sqlite_db.execute("UPDATE assets SET serial_number = NULL WHERE id = 11")
    payload = {"ticket_id": 42, "script": "Cleanup-Disk", "values": {"Drive": "{{asset.serial_number}}"}}
    result = await modules._invoke_rmm_script({}, payload)
    assert result["status"] == "succeeded" and [item["asset_id"] for item in result["queued"]] == [10]
    assert result["failed"][0]["asset_id"] == 11 and "no value" in result["failed"][0]["message"]

    tickets["assets"][42] = [{"asset_id": 11, "name": "PC-02"}]
    result = await modules._invoke_rmm_script({}, payload)
    assert result["status"] == "error" and "PC-02" in result["error"]


@pytest.mark.anyio
async def test_ticket_without_linked_assets_is_skipped(tickets):
    tickets["assets"][42] = []
    result = await modules._invoke_rmm_script({}, {"ticket_id": 42, "script": "Cleanup-Disk"})
    assert result == {"status": "skipped", "reason": "No asset is linked to the ticket.", "ticket_id": 42}


@pytest.mark.anyio
async def test_automation_leaves_script_values_for_each_device():
    rendered = await automations._render_action_payload(
        "rmm-script",
        {"ticket_id": "{{ ticket.id }}", "values": {"Computer": "{{asset.name}}"}},
        {"ticket": {"id": 42}},
    )
    assert rendered == {"ticket_id": 42, "values": {"Computer": "{{asset.name}}"}}
    other = await automations._render_action_payload(
        "add-ticket-reply", {"values": "{{ ticket.id }}"}, {"ticket": {"id": 42}}
    )
    assert other == {"values": 42}


@pytest.mark.anyio
async def test_action_is_listed_with_scripts_and_hidden_without_rmm(tickets, monkeypatch):
    from app.services import component_availability

    async def no_modules():
        return []

    monkeypatch.setattr(modules.module_repo, "list_modules", no_modules)
    listed = {module["slug"]: module for module in await modules.list_trigger_action_modules()}
    script_field = next(field for field in listed["rmm-script"]["payload_schema"]["fields"] if field["name"] == "script")
    assert "rmm/Common/Cleanup-Disk.ps1" in script_field["enum"]
    assert listed["rmm-script"]["name"] == "My Portal RMM Script"
    assert "enum" not in next(
        field for field in modules.get_action_payload_schema("rmm-script")["fields"] if field["name"] == "script"
    )

    disabled = component_availability.ComponentAvailability(disabled_feature_packs=frozenset({"rmm"}))
    monkeypatch.setattr(modules, "get_component_availability", lambda: disabled)
    assert "rmm-script" not in {module["slug"] for module in await modules.list_trigger_action_modules()}
    result = await modules.trigger_module("rmm-script", {"ticket_id": 42}, background=False)
    assert result["status"] == "skipped"
