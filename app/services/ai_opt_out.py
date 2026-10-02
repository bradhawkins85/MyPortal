"""Store a user's AI opt-out and remove their content from the AI index.

Kept apart from :mod:`app.services.ai_consent`, which the RAG indexers import,
so that the consent checks do not depend on the indexing services.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.core.logging import log_error, log_info
from app.repositories import ai_consent as ai_consent_repo
from app.repositories import rag_index as rag_repo
from app.repositories import users as user_repo
from app.services import ai_consent, rag_outbox


async def purge_user_from_rag_index(user_id: int) -> dict[str, int]:
    """Remove an opted-out user's existing content from the RAG index."""

    counts = {"tickets": 0, "ticket_comments": 0, "chats": 0, "reindexed": 0}
    try:
        identifier = int(user_id)
    except (TypeError, ValueError):
        return counts
    if identifier <= 0:
        return counts
    requested = await ai_consent_repo.list_requested_ticket_ids(identifier)
    counts["tickets"] = await rag_repo.delete_documents_for_sources(
        "tickets", [str(ticket_id) for ticket_id in requested]
    )
    replies = await ai_consent_repo.list_ticket_replies_involving_user(identifier)
    counts["ticket_comments"] = await rag_repo.delete_documents_for_sources(
        "ticket_comments",
        [f"{row['ticket_id']}:{row['reply_id']}" for row in replies],
    )
    chats = await ai_consent_repo.list_created_chat_room_ids(identifier)
    counts["chats"] = await rag_repo.delete_documents_for_sources(
        "chats", [str(room_id) for room_id in chats]
    )
    # Replies the user wrote on other people's tickets are stripped when those
    # tickets are re-indexed.
    requested_ids = set(requested)
    for ticket_id in sorted(
        {int(row["ticket_id"]) for row in replies} - requested_ids
    ):
        try:
            await rag_outbox.enqueue("tickets", ticket_id)
            counts["reindexed"] += 1
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error("AI opt-out re-index enqueue failed", ticket_id=ticket_id, error=str(exc))
    log_info("Removed opted-out user's content from the AI index", user_id=identifier, **counts)
    return counts


def opt_out_updates(opt_out: bool, previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the user columns to store for a change of the opt-out flag."""

    if not opt_out:
        return {"ai_opt_out": 0, "ai_opt_out_at": None}
    if ai_consent.is_opted_out_record(previous) and (previous or {}).get("ai_opt_out_at"):
        return {"ai_opt_out": 1}
    return {"ai_opt_out": 1, "ai_opt_out_at": datetime.now(timezone.utc).replace(tzinfo=None)}


async def set_user_ai_opt_out(
    target: Mapping[str, Any],
    opt_out: bool,
) -> dict[str, Any] | None:
    """Store a change of the opt-out flag and purge the AI index on opt-out.

    Returns the updated user, or None when the flag already had that value.
    Callers record the audit entry, since they hold the request and actor.
    """

    if ai_consent.is_opted_out_record(target) == bool(opt_out):
        return None
    user_id = int(target["id"])
    updated = await user_repo.update_user(user_id, **opt_out_updates(bool(opt_out), target))
    if opt_out:
        try:
            await purge_user_from_rag_index(user_id)
        except Exception as exc:  # pragma: no cover - the opt-out itself is saved
            log_error("Failed to purge opted-out user from the AI index", user_id=user_id, error=str(exc))
    return updated


def audit_snapshot(user: Mapping[str, Any] | None) -> dict[str, Any]:
    user = user or {}
    return {"ai_opt_out": ai_consent.is_opted_out_record(user), "ai_opt_out_at": user.get("ai_opt_out_at")}
