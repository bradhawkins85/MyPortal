"""Thresholded, tenant-safe Voice Monitor ticket incidents."""
from __future__ import annotations

import re
from typing import Any, Mapping

from app.repositories import tickets as tickets_repo
from app.repositories import voice_monitor as voice_monitor_repo
from app.services import tickets as tickets_service

_SECRET = re.compile(r"(?i)(authorization|api[-_ ]?key|token|password|secret)\s*[:=]\s*\S+")


def _safe(value: Any, limit: int = 600) -> str:
    text = " ".join(str(value or "Unavailable").split())
    return _SECRET.sub(r"\1=[REDACTED]", text)[:limit]


def incident_key(company_id: int, endpoint_id: int) -> str:
    return f"voice-monitor:{company_id}:{endpoint_id}"


def _description(endpoint: Mapping[str, Any], attempt: Mapping[str, Any]) -> str:
    destination = voice_monitor_repo._mask_destination(endpoint.get("destination_e164"))
    return "\n".join((
        f"Monitored endpoint: {_safe(endpoint.get('display_label'))}",
        f"Destination: {destination}",
        f"Queued: {_safe(attempt.get('queued_at'))}",
        f"Started: {_safe(attempt.get('started_at'))}",
        f"Completed: {_safe(attempt.get('completed_at'))}",
        f"Attempt/provider correlation: {_safe(attempt.get('provider_call_id') or attempt.get('id'))}",
        f"Failure category: {_safe(attempt.get('failure_category') or 'unknown')}",
        f"Response code: {_safe(attempt.get('provider_response_code'))}",
        f"Retry history: {_safe(attempt.get('retry_count') or 0)} retries",
        f"Diagnostics: {_safe(attempt.get('failure_detail'))}",
        "Security note: packet captures, provider credentials, media, and transcripts are intentionally not attached.",
    ))


async def handle_attempt_result(
    company_id: int, attempt_id: int, *, close_ticket_on_recovery: bool = True
) -> int | None:
    """Apply a terminal attempt to incident state and return its incident ticket ID."""
    attempt = await voice_monitor_repo.get_attempt(company_id, attempt_id)
    if not attempt or not attempt.get("endpoint_id"):
        return None
    endpoint = await voice_monitor_repo.get_endpoint(company_id, int(attempt["endpoint_id"]))
    if not endpoint:  # prevents deleted/cross-company configuration from disclosing history
        return None
    endpoint_id = int(endpoint["id"])
    if attempt.get("outcome_status") == "passed":
        incident = await voice_monitor_repo.recover_incident(company_id, endpoint_id)
        ticket_id = int(incident.get("ticket_id") or 0) if incident else 0
        if ticket_id and close_ticket_on_recovery:
            await tickets_repo.update_ticket(ticket_id, status="closed")
        return ticket_id or None
    if attempt.get("outcome_status") not in {"failed", "timed_out", "exhausted"}:
        return None
    if not endpoint.get("ticket_on_failure"):
        return None
    state = await voice_monitor_repo.record_incident_failure(
        company_id, endpoint_id, attempt_id, int(endpoint.get("ticket_failure_threshold") or 1)
    )
    if not state:
        return None
    existing = int(state.get("ticket_id") or 0)
    if existing:
        await voice_monitor_repo.link_ticket_once(company_id, attempt_id, existing)
        return existing
    if int(state.get("ticket_claim_attempt_id") or 0) != attempt_id:
        return None
    try:
        ticket = await tickets_service.create_ticket(
            subject=f"Voice Monitor failure: {_safe(endpoint.get('display_label'), 120)}",
            description=_description(endpoint, attempt), requester_id=None,
            company_id=company_id, assigned_user_id=None, priority="high", status="open",
            category="Voice Monitor", module_slug="voice-monitor",
            external_reference=incident_key(company_id, endpoint_id),
            send_creation_notification=False,
        )
        ticket_id = int(ticket["id"])
        if not await voice_monitor_repo.complete_incident_ticket_claim(company_id, endpoint_id, attempt_id, ticket_id):
            return None
        await voice_monitor_repo.link_ticket_once(company_id, attempt_id, ticket_id)
        return ticket_id
    except Exception:
        await voice_monitor_repo.release_incident_ticket_claim(company_id, endpoint_id, attempt_id)
        raise
