"""What RMM automations do when they fire: run a script on the device, or
raise a ticket for it.

Event log rules (:mod:`app.services.rmm_event_log_rules`) and checks
(:mod:`app.services.rmm_checks`) share these, so a script run or ticket looks
the same whichever started it. Callers keep their own cooldowns and remember
the ticket per device; these functions only act and say what they did.
"""

from __future__ import annotations

import html
from typing import Any, Mapping

from app.core.logging import log_info
from app.repositories import rmm as rmm_repo
from app.services import rmm_automation, rmm_scripts
from app.services.rmm_scripts import RunRequestError

TICKET_SUBJECT_LENGTH = 255


def device_label(agent: Mapping[str, Any]) -> str:
    return str(agent.get("hostname") or f"device {agent.get('asset_id') or agent.get('id')}")


async def run_script(
    *,
    script_id: Any,
    entries_encrypted: Any,
    inputs: list[dict[str, Any]] | None,
    timeout_seconds: Any,
    agent: Mapping[str, Any],
    run_source: str,
    extra_context: Mapping[str, Any] | None = None,
) -> tuple[int | None, str, bool]:
    """Queue a Gitea script on the device as an ordinary RMM run.

    Returns the run ID (None when it could not be queued), a summary of what
    happened, and whether it was attempted at all (False when the script is no
    longer available)."""

    device = device_label(agent)
    script = await rmm_repo.get_script(int(script_id), with_content=True) if script_id else None
    if not script or not script.get("is_active") or not rmm_repo.script_available_to(script, int(agent["company_id"])):
        return None, "Not run: the script is no longer in Gitea.", False
    try:
        run_id = await rmm_scripts.queue_for_agent(
            script=script,
            agent=agent,
            entries=rmm_automation.decrypt_entries(entries_encrypted),
            inputs=inputs or [],
            timeout_seconds=rmm_scripts.clamp_timeout(timeout_seconds, 600),
            requested_by_user_id=None,
            run_source=run_source,
            extra_context=extra_context,
        )
    except RunRequestError as exc:
        return None, f"Could not run on {device}: {'; '.join(exc.errors.values())}", True
    return run_id, f"Ran {script['name']} on {device}", True


async def open_ticket(ticket_id: int | None) -> dict[str, Any] | None:
    """The ticket when it still exists and is not closed."""

    from app.repositories import tickets as tickets_repo

    ticket = await tickets_repo.get_ticket(ticket_id) if ticket_id is not None else None
    if ticket and str(ticket.get("status") or "").casefold() != "closed":
        return ticket
    return None


def ticket_label(ticket: Mapping[str, Any]) -> str:
    return f"#{ticket.get('ticket_number') or ticket['id']}"


async def add_ticket_update(ticket: Mapping[str, Any], *, body: str, reference: str) -> None:
    """Add an internal update to a ticket an automation raised."""

    from app.repositories import tickets as tickets_repo

    await tickets_repo.create_reply(
        ticket_id=int(ticket["id"]), author_id=None, body=body, is_internal=True, external_reference=reference,
    )


async def raise_ticket(
    *, agent: Mapping[str, Any], subject: str, description: str, reference: str,
) -> dict[str, Any]:
    """Raise a ticket for the device's company with the device's asset linked."""

    from app.services import tickets as tickets_service

    company_id = int(agent["company_id"]) if agent.get("company_id") is not None else None
    asset_id = int(agent["asset_id"]) if agent.get("asset_id") is not None else None
    created = await tickets_service.create_ticket(
        subject=subject[:TICKET_SUBJECT_LENGTH],
        description=description,
        requester_id=None,
        company_id=company_id,
        assigned_user_id=None,
        priority="normal",
        status="open",
        category=None,
        module_slug="rmm",
        external_reference=reference,
        asset_ids=[asset_id] if asset_id is not None else None,
    )
    log_info("RMM automation raised a ticket", reference=reference, ticket_id=created["id"])
    return created


def escape(value: Any) -> str:
    return html.escape(str(value))
