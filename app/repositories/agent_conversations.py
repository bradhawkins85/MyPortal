from __future__ import annotations

import json
from typing import Any

from app.core.database import db

_VALID_ROLES = {"user", "agent"}


def _clean_filters(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list[str] = []
    for item in value:
        text = str(item or "").strip().lower()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _parse_payload(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


async def get(
    conversation_id: str,
    *,
    user_id: int,
    company_id: int | None,
) -> dict[str, Any] | None:
    """Return a conversation and its ordered turns, or None when unknown.

    A conversation is only returned when it belongs to the requesting user and
    (when a company scope is set) the same company, so switching companies does
    not leak another scope's context.
    """
    cid = str(conversation_id or "").strip()
    if not cid:
        return None
    row = await db.fetch_one(
        """
        SELECT id, user_id, company_id, original_query, source_filters, status
        FROM agent_conversations
        WHERE id = ? AND user_id = ?
        LIMIT 1
        """,
        (cid, user_id),
    )
    if not row:
        return None
    stored_company = row.get("company_id")
    if (
        company_id is not None
        and stored_company is not None
        and int(stored_company) != int(company_id)
    ):
        return None
    turn_rows = await db.fetch_all(
        """
        SELECT turn_number, role, content, payload
        FROM agent_conversation_turns
        WHERE conversation_id = ?
        ORDER BY turn_number ASC, id ASC
        """,
        (cid,),
    )
    turns: list[dict[str, Any]] = []
    for turn in turn_rows or []:
        role = str(turn.get("role") or "")
        if role not in _VALID_ROLES:
            continue
        turns.append(
            {
                "turn_number": int(turn.get("turn_number") or 0),
                "role": role,
                "content": str(turn.get("content") or ""),
                "payload": _parse_payload(turn.get("payload")),
            }
        )
    return {
        "conversation_id": str(row.get("id") or cid),
        "original_query": str(row.get("original_query") or ""),
        "source_filters": _clean_filters(row.get("source_filters")),
        "status": str(row.get("status") or "active"),
        "turns": turns,
    }


async def create(
    conversation_id: str,
    *,
    user_id: int,
    company_id: int | None,
    original_query: str,
    source_filters: list[str] | None = None,
) -> None:
    """Create a conversation row; a no-op when it already exists."""
    cid = str(conversation_id or "").strip()
    if not cid:
        return
    existing = await db.fetch_one(
        "SELECT id FROM agent_conversations WHERE id = ? LIMIT 1", (cid,)
    )
    if existing:
        return
    filters = _clean_filters(source_filters)
    await db.execute(
        """
        INSERT INTO agent_conversations (id, user_id, company_id, original_query, source_filters, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        """,
        (
            cid,
            user_id,
            company_id,
            str(original_query or ""),
            json.dumps(filters, ensure_ascii=False) if filters else None,
        ),
    )


async def append_turn(
    conversation_id: str,
    *,
    turn_number: int,
    role: str,
    content: str,
    payload: dict[str, Any] | None = None,
) -> None:
    cid = str(conversation_id or "").strip()
    if not cid:
        return
    if role not in _VALID_ROLES:
        raise ValueError("Invalid conversation role")
    await db.execute(
        """
        INSERT INTO agent_conversation_turns (conversation_id, turn_number, role, content, payload)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            cid,
            int(turn_number),
            role,
            str(content or ""),
            json.dumps(payload, ensure_ascii=False) if payload else None,
        ),
    )
