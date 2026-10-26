"""Run MyPortal RMM scripts from ticket automations.

The "My Portal RMM Script" automation action (module ``rmm-script``) lets an
automation try to fix a problem before a technician touches the device: for
example, a *Ticket created* automation for low disk space tickets can run the
disk cleanup script on the ticket's device.

:func:`run_for_ticket` queues the Gitea script on the ticket's linked devices
that have the MyPortal RMM agent, as ordinary RMM runs (``run_source =
'ticket'``), so they show in the device's run history like any other. Each run
is recorded against its ticket (``rmm_ticket_script_runs``). When the device
reports back, :func:`record_result` posts the outcome and the end of the
output to the ticket as an internal note and, when the action asked for it,
sets the ticket's status once every run it queued has completed successfully.
Runs that end without a result (expired, cancelled) are posted by :func:`tick`
on the RMM automation tick.

Script values are resolved per device like a schedule's, so they may use
``{{asset.*}}`` and ``{{company.variables.*}}`` as well as ``{{ticket.*}}``.
"""

from __future__ import annotations

import html
import json
import uuid
from typing import Any, Mapping

from app.core.logging import log_info, log_warning
from app.repositories import assets as assets_repo
from app.repositories import rmm as rmm_repo
from app.services import rmm_scripts
from app.services.rmm_scripts import RunRequestError

RUN_SOURCE = "ticket"
# Ticket automations run on the ticket's own devices; this keeps a ticket
# with a long asset list from flooding the queue.
MAX_TICKET_TARGETS = 20
NOTE_OUTPUT_CHARS = 4000
_FINISH_WORDS = {
    "completed": "completed",
    "failed": "failed",
    "timed_out": "timed out",
    "cancelled": "was cancelled",
    "aborted": "was aborted",
    "expired": "expired before the device collected it",
}


def _tickets_repo():
    from app.repositories import tickets as tickets_repo

    return tickets_repo


# --------------------------------------------------------------------------- #
# Choosing the script and its values
# --------------------------------------------------------------------------- #


async def find_script(reference: Any, company_id: int) -> dict[str, Any] | None:
    """The script ``reference`` names for ``company_id``'s devices, with content.

    ``reference`` is a script ID, a Gitea path (``rmm/Common/Cleanup.ps1``) or
    a script name (``Cleanup``). A name in the company's own folder wins over
    the same name in ``Common``."""

    text = str(reference if reference is not None else "").strip()
    if not text:
        return None
    script_id: int | None = int(text) if text.isdigit() else None
    if script_id is None:
        wanted = text.casefold()
        scripts = await rmm_repo.list_scripts(company_id=company_id)
        matches = [script for script in scripts if str(script.get("path") or "").casefold() == wanted]
        if not matches:
            matches = [script for script in scripts if str(script.get("name") or "").casefold() == wanted]
        matches.sort(key=lambda script: script.get("company_id") is None)
        if not matches:
            return None
        script_id = int(matches[0]["id"])
    script = await rmm_repo.get_script(script_id, with_content=True)
    return script if rmm_repo.script_available_to(script, company_id) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def submitted_values(script: Mapping[str, Any], values: Any) -> dict[str, str]:
    """Map the action's ``values`` onto the script's fields.

    Keys may be the run form's (``param:Drive`` / ``env:SITE_URL``) or a bare
    parameter or environment variable name (``Drive``), matched ignoring case."""

    if values in (None, ""):
        return {}
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            raise RunRequestError({"values": "Values must be a JSON object."}) from None
    if not isinstance(values, Mapping):
        raise RunRequestError({"values": "Values must be a JSON object."})
    fields = rmm_scripts.script_fields(script)
    keys: dict[str, str] = {}
    # Parameters come last so they win over an environment variable of the same name.
    for field in sorted(fields, key=lambda field: field["kind"] == "param"):
        keys[field["name"].casefold()] = field["key"]
    keys.update({field["key"].casefold(): field["key"] for field in fields})
    submitted: dict[str, str] = {}
    unknown: list[str] = []
    for name, value in values.items():
        key = keys.get(str(name).strip().casefold())
        if key is None:
            unknown.append(str(name))
            continue
        submitted[key] = _text(value)
    if unknown:
        raise RunRequestError({"values": f"{script['name']} has no field named {', '.join(sorted(unknown))}."})
    return submitted


# --------------------------------------------------------------------------- #
# Queueing
# --------------------------------------------------------------------------- #


async def _targets(ticket_id: int, asset_id: int | None) -> tuple[list[dict[str, Any]], str | None]:
    linked = await _tickets_repo().list_ticket_assets(ticket_id)
    if asset_id is not None:
        linked = [asset for asset in linked if asset.get("asset_id") == asset_id]
        if not linked:
            return [], f"Asset {asset_id} is not linked to the ticket."
    return linked[:MAX_TICKET_TARGETS], None


async def run_for_ticket(
    *,
    ticket: Mapping[str, Any],
    script: Any,
    asset_id: int | None = None,
    values: Any = None,
    timeout_seconds: Any = None,
    run_as: Any = None,
    success_status: str | None = None,
    ticket_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue ``script`` on the ticket's linked devices with the RMM agent.

    ``asset_id`` limits it to one linked asset. ``success_status`` is the
    ticket status to set once every queued run completes successfully.
    Returns an automation result: ``succeeded`` when at least one run was
    queued, ``skipped`` when no device could take it (none linked, no agent,
    already running, wrong platform) and ``error`` when the action itself is
    wrong (unknown script, bad values or status) or no device could be sent
    the values."""

    ticket_id = int(ticket["id"])
    ticket_company = int(ticket["company_id"]) if ticket.get("company_id") is not None else None
    if not str(script if script is not None else "").strip():
        return {"status": "error", "error": "Choose the RMM script to run.", "ticket_id": ticket_id}
    if success_status:
        from app.services import tickets as tickets_service

        try:
            success_status = await tickets_service.validate_status_choice(success_status)
        except ValueError as exc:
            return {"status": "error", "error": f"Success status: {exc}", "ticket_id": ticket_id}
    try:
        run_as = rmm_scripts.clean_run_as(run_as)
    except RunRequestError as exc:
        return {"status": "error", "error": "; ".join(exc.errors.values()), "ticket_id": ticket_id}

    linked, problem = await _targets(ticket_id, asset_id)
    if problem:
        return {"status": "error", "error": problem, "ticket_id": ticket_id}
    if not linked:
        return {"status": "skipped", "reason": "No asset is linked to the ticket.", "ticket_id": ticket_id}

    extra_context = {"ticket": dict(ticket_context or ticket)}
    batch_id = str(uuid.uuid4())
    scripts: dict[int, dict[str, Any] | None] = {}
    queued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for link in linked:
        target_id = int(link["asset_id"])
        name = link.get("name") or f"asset {target_id}"
        asset = await assets_repo.get_asset_by_id(target_id)
        company_id = int(asset["company_id"]) if asset and asset.get("company_id") is not None else None
        if company_id is None or (ticket_company is not None and company_id != ticket_company):
            skipped.append({"asset_id": target_id, "asset_name": name, "message": "The device belongs to another company."})
            continue
        if company_id not in scripts:
            scripts[company_id] = await find_script(script, company_id)
        chosen = scripts[company_id]
        if not chosen:
            return {
                "status": "error",
                "error": f"RMM script {script!r} was not found, or is not available to this company. Sync scripts and try again.",
                "ticket_id": ticket_id,
            }
        try:
            entries = rmm_scripts.validate_entries(chosen, submitted_values(chosen, values))
        except RunRequestError as exc:
            return {"status": "error", "error": "; ".join(exc.errors.values()), "ticket_id": ticket_id}
        agent = await rmm_repo.get_asset_agent(target_id)
        if not agent:
            skipped.append({"asset_id": target_id, "asset_name": name, "message": "No MyPortal RMM agent is installed."})
            continue
        if await rmm_repo.has_active_run(int(agent["id"]), int(chosen["id"]), run_source=RUN_SOURCE):
            skipped.append({"asset_id": target_id, "asset_name": name, "message": "Already queued or running."})
            continue
        try:
            run_id = await rmm_scripts.queue_for_agent(
                script=chosen,
                agent={**agent, "company_id": company_id, "asset_id": target_id},
                entries=entries,
                inputs=rmm_scripts.stored_inputs(chosen, entries),
                timeout_seconds=rmm_scripts.clamp_timeout(
                    timeout_seconds, int(chosen.get("default_timeout_seconds") or 600)
                ),
                requested_by_user_id=None,
                run_source=RUN_SOURCE,
                extra_context=extra_context,
                run_as=run_as,
            )
        except RunRequestError as exc:
            # A device problem skips the device; anything else (a variable with
            # no value on this device, a missing file) fails it.
            problem = {"asset_id": target_id, "asset_name": name, "message": "; ".join(exc.errors.values())}
            (skipped if "device" in exc.errors else failed).append(problem)
            continue
        await rmm_repo.record_ticket_run(
            run_id=run_id, ticket_id=ticket_id, batch_id=batch_id, success_status=success_status
        )
        queued.append({"run_id": run_id, "asset_id": target_id, "asset_name": name, "script": chosen["name"]})

    result: dict[str, Any] = {"ticket_id": ticket_id, "queued": queued, "skipped": skipped, "failed": failed}
    if not queued:
        reasons = "; ".join(f"{item['asset_name']}: {item['message']}" for item in failed + skipped)
        if failed:
            return {**result, "status": "error", "error": f"Not run on any device. {reasons}"}
        return {**result, "status": "skipped", "reason": f"Not run on any device. {reasons}".strip()}
    log_info("Ticket automation queued an RMM script", ticket_id=ticket_id,
             run_ids=[item["run_id"] for item in queued])
    return {**result, "status": "succeeded"}


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


def _output_tail(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= NOTE_OUTPUT_CHARS:
        return text
    return "…" + text[-NOTE_OUTPUT_CHARS:]


def result_note(
    run: Mapping[str, Any], *, status: str, exit_code: int | None, stdout: str, stderr: str, error: str | None,
) -> str:
    device = run.get("asset_name") or f"asset {run.get('asset_id')}"
    outcome = _FINISH_WORDS.get(status, status.replace("_", " "))
    body = (
        f"<p><strong>RMM script {html.escape(outcome)}:</strong> {html.escape(str(run.get('script_name') or 'script'))}"
        f" on {html.escape(str(device))}"
    )
    if exit_code is not None:
        body += f" (exit code {exit_code})"
    body += ". The full output is in <a href=\"/rmm/scripts\">Scripts</a> run history.</p>"
    if error:
        body += f"<p>{html.escape(error)}</p>"
    for label, text in (("Output", stdout), ("Errors", stderr)):
        tail = _output_tail(text)
        if tail:
            body += f"<p>{label}:</p><pre>{html.escape(tail)}</pre>"
    return body


async def _apply_success_status(ticket_run: Mapping[str, Any]) -> None:
    status = ticket_run.get("success_status")
    if not status:
        return
    statuses = await rmm_repo.ticket_batch_statuses(str(ticket_run["batch_id"]))
    if not statuses or any(value != "completed" for value in statuses):
        return
    tickets_repo = _tickets_repo()
    ticket = await tickets_repo.get_ticket(int(ticket_run["ticket_id"]))
    if not ticket or ticket.get("status") == status:
        return
    await tickets_repo.update_ticket(int(ticket_run["ticket_id"]), status=status)
    log_info("RMM script set the ticket status", ticket_id=ticket_run["ticket_id"], status=status)


async def _post(run_id: int, ticket_run: Mapping[str, Any], *, status: str, exit_code: int | None,
                stdout: str, stderr: str, error: str | None) -> None:
    tickets_repo = _tickets_repo()
    if not await tickets_repo.get_ticket(int(ticket_run["ticket_id"])):
        return
    run = await rmm_repo.get_run(run_id) or {"id": run_id}
    await tickets_repo.create_reply(
        ticket_id=int(ticket_run["ticket_id"]),
        author_id=None,
        body=result_note(run, status=status, exit_code=exit_code, stdout=stdout, stderr=stderr, error=error),
        is_internal=True,
        external_reference=f"rmm-run:{run_id}",
    )
    if status == "completed":
        await _apply_success_status(ticket_run)


async def record_result(
    run_id: int, *, status: str, exit_code: int | None, stdout: str, stderr: str, error: str | None,
) -> None:
    """Post a finished run's outcome to its ticket. Never raises: a failure
    here must not stop the agent's result from being stored."""

    try:
        ticket_run = await rmm_repo.get_ticket_run(run_id)
        if not ticket_run or not await rmm_repo.claim_ticket_run_report(run_id):
            return
        await _post(run_id, ticket_run, status=status, exit_code=exit_code, stdout=stdout, stderr=stderr, error=error)
    except Exception as exc:  # noqa: BLE001 - the run result is already stored
        log_warning("Could not post an RMM script result to its ticket", run_id=run_id, error=str(exc))


async def tick() -> None:
    """Post the outcome of ticket runs that ended without a result from the
    device: it never collected the script, it never reported back, or a
    technician cancelled or aborted the run."""

    for run_id in await rmm_repo.unreported_ticket_runs():
        try:
            ticket_run = await rmm_repo.get_ticket_run(run_id)
            run = await rmm_repo.get_run(run_id)
            if not ticket_run or not run or not await rmm_repo.claim_ticket_run_report(run_id):
                continue
            await _post(run_id, ticket_run, status=str(run["status"]), exit_code=run.get("exit_code"),
                        stdout="", stderr="", error=run.get("error_message"))
        except Exception as exc:  # noqa: BLE001 - one bad run must not stop the rest
            log_warning("Could not post an RMM script result to its ticket", run_id=run_id, error=str(exc))
