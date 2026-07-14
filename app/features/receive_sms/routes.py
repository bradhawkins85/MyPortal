from __future__ import annotations

import base64
import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.dependencies.api_keys import require_api_key
from app.core.database import db
from app.core.logging import log_error
from app.repositories import tickets as tickets_repo
from app.services import tickets as tickets_service
from app.services.sanitization import sanitize_rich_text

router = APIRouter(prefix="/api/integration-modules/receive-sms", tags=["Receive SMS"])


class ReceiveSMSPayload(BaseModel):
    type: str = Field(..., description="Webhook type; must be SMSIn.")
    from_number: str = Field(..., alias="from", description="Sender phone number.")
    name: str | None = Field(default=None, description="Sender display name supplied by the phone.")
    message: str = Field(..., description="Base64 encoded SMS body.")
    date: str | None = Field(default=None, description="SMS sent date from the Android device. Defaults to current UTC date when omitted.")
    time: str | None = Field(default=None, description="SMS sent time from the Android device. Defaults to current UTC time when omitted.")


def _normalise_phone(value: str) -> str:
    return re.sub(r"\D+", "", (value or "").strip())


def _parse_sms_datetime(
    date_value: str | None,
    time_value: str | None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, date]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    raw_date = (date_value or "").strip()
    raw_time = (time_value or "").strip()
    if not raw_date and not raw_time:
        return current, current.date()

    if not raw_date:
        raw_date = current.date().isoformat()

    candidates = []
    if raw_time:
        candidates.extend([f"{raw_date} {raw_time}", f"{raw_date}T{raw_time}"])
    candidates.append(raw_date)
    formats = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
    ]
    for candidate in candidates:
        for fmt in formats:
            try:
                parsed = datetime.strptime(candidate, fmt)
            except ValueError:
                continue
            parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed, parsed.date()
    try:
        parsed = datetime.fromisoformat((f"{raw_date}T{raw_time}" if raw_time else raw_date).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        return parsed, parsed.date()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid SMS date/time") from exc


def _decode_message(value: str) -> str:
    try:
        decoded = base64.b64decode(value, validate=True)
        return decoded.decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Message must be valid base64 encoded UTF-8") from exc


async def _find_contact(phone: str) -> dict[str, Any]:
    normalised = _normalise_phone(phone)
    if not normalised:
        return {"requester_id": None, "company_id": None}
    like = f"%{normalised}%"
    user = await db.fetch_one(
        """
        SELECT id, company_id FROM users
        WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(mobile_phone, ''), ' ', ''), '-', ''), '(', ''), ')', ''), '+', '') LIKE %s
        ORDER BY id ASC LIMIT 1
        """,
        (like,),
    )
    if user:
        return {"requester_id": int(user["id"]), "company_id": user.get("company_id")}
    staff = await db.fetch_one(
        """
        SELECT id, company_id FROM staff
        WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(mobile_phone, ''), ' ', ''), '-', ''), '(', ''), ')', ''), '+', '') LIKE %s
        ORDER BY id ASC LIMIT 1
        """,
        (like,),
    )
    if staff:
        return {"requester_staff_id": int(staff["id"]), "company_id": staff.get("company_id"), "requester_id": None}
    return {"requester_id": None, "requester_staff_id": None, "company_id": None}


async def _find_sms_ticket(normalised_phone: str, sms_day: date) -> dict[str, Any] | None:
    open_row = await db.fetch_one(
        """
        SELECT t.id FROM tickets t
        INNER JOIN sms_ticket_links l ON l.ticket_id = t.id
        WHERE l.from_number_normalized = %s
          AND LOWER(COALESCE(t.status, '')) NOT IN ('closed', 'resolved')
        ORDER BY t.updated_at DESC, t.id DESC LIMIT 1
        """,
        (normalised_phone,),
    )
    if open_row:
        return await tickets_repo.get_ticket(int(open_row["id"]))

    row = await db.fetch_one(
        """
        SELECT t.id FROM tickets t
        INNER JOIN sms_ticket_links l ON l.ticket_id = t.id
        WHERE l.from_number_normalized = %s AND l.sms_date = %s
        ORDER BY t.id DESC LIMIT 1
        """,
        (normalised_phone, sms_day),
    )
    if not row:
        return None
    return await tickets_repo.get_ticket(int(row["id"]))


async def _refresh_sms_ticket_ai(ticket_id: int) -> None:
    """Run the same AI enrichment pipeline used by standard ticket creation/reply flows."""

    try:
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
    except RuntimeError as exc:
        log_error("Failed to refresh AI summary for SMS ticket", ticket_id=ticket_id, error=str(exc))
    await tickets_service.refresh_ticket_ai_tags(ticket_id)


@router.post("/inbound", status_code=status.HTTP_201_CREATED)
async def receive_sms(payload: ReceiveSMSPayload, request: Request, api_key_record: dict = Depends(require_api_key)) -> dict[str, Any]:
    if payload.type != "SMSIn":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported SMS webhook type")
    message_text = _decode_message(payload.message).strip()
    if not message_text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Decoded message cannot be empty")
    sms_at, sms_day = _parse_sms_datetime(payload.date, payload.time)
    normalised_phone = _normalise_phone(payload.from_number)
    if not normalised_phone:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Sender phone number is required")

    body = sanitize_rich_text(message_text).html
    ticket = await _find_sms_ticket(normalised_phone, sms_day)
    action = "created"
    reply: dict[str, Any] | None = None
    try:
        if ticket:
            if str(ticket.get("status") or "").lower() in {"closed", "resolved"}:
                reopened_status = await tickets_service.resolve_status_or_default("open")
                ticket = await tickets_repo.set_ticket_status(int(ticket["id"]), reopened_status)
                action = "reopened"
            else:
                action = "appended"
            author_id = ticket.get("requester_id")
            if author_id is None:
                contact = await _find_contact(payload.from_number)
                author_id = contact.get("requester_id")
            reply = await tickets_repo.create_reply(
                ticket_id=int(ticket["id"]), author_id=author_id, body=body,
                is_internal=False, external_reference=f"sms:{normalised_phone}:{sms_at.isoformat()}", created_at=sms_at,
                author_display_name=(payload.name or payload.from_number) if author_id is None else None,
            )
        else:
            contact = await _find_contact(payload.from_number)
            status_value = await tickets_service.resolve_status_or_default(None)
            subject_name = (payload.name or payload.from_number).strip()
            ticket = await tickets_service.create_ticket(
                subject=f"SMS from {subject_name}", description=body,
                requester_id=contact.get("requester_id"), requester_staff_id=contact.get("requester_staff_id"),
                company_id=contact.get("company_id"), assigned_user_id=None, priority="normal", status=status_value,
                category="SMS", module_slug="receive_sms", external_reference=f"sms:{normalised_phone}:{sms_day.isoformat()}",
                trigger_automations=True, initial_reply_author_id=contact.get("requester_id"),
            )
            await db.execute(
                """
                INSERT INTO sms_ticket_links (ticket_id, from_number, from_number_normalized, sms_date, created_at, updated_at)
                VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
                """,
                (ticket["id"], payload.from_number, normalised_phone, sms_day),
            )
        ticket_id = int(ticket["id"])
        await _refresh_sms_ticket_ai(ticket_id)
        if reply is not None:
            await tickets_service.emit_ticket_replied_event(
                ticket,
                actor_type="requester",
                reply=reply,
            )
            await tickets_service.emit_ticket_updated_event(
                ticket,
                actor_type="requester",
                reply=reply,
            )
    except Exception as exc:
        log_error("Failed to process inbound SMS", error=str(exc), from_number=payload.from_number)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to process inbound SMS") from exc
    return {"status": action, "ticket_id": ticket.get("id"), "sms_date": sms_day.isoformat()}
