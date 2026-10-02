"""Per-user opt-out from AI processing.

The Privacy Policy ("Your choices") lets a person ask that their requests are
not processed by AI features. Every AI entry point asks this module before
sending a person's content to a language model, an embedding service or a
transcription service, and the RAG indexers use it to keep that content out of
the search index.

Lookups fail closed: if the preference cannot be read the content is treated
as opted out, so a database fault never leaks content to an AI provider.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.core.logging import log_error, log_info
from app.repositories import ai_consent as ai_consent_repo
from app.repositories import users as user_repo

AGENT_OPTED_OUT_MESSAGE = (
    "You've asked us not to use AI to process your requests, so the AI assistant "
    "is turned off for you. You can change this under AI features on your profile."
)
TICKET_OPTED_OUT_REASON = (
    "The requester of this ticket has asked not to have their requests processed by AI."
)
CHAT_OPTED_OUT_REASON = (
    "The customer in this chat has asked not to have their requests processed by AI."
)
CALL_OPTED_OUT_REASON = (
    "The caller has asked not to have their requests processed by AI."
)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _user_id(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        identifier = int(value)
    except (TypeError, ValueError):
        return None
    return identifier if identifier > 0 else None


def is_opted_out_record(user: Mapping[str, Any] | None) -> bool:
    """Return True when an already-loaded user record has opted out."""

    return bool(user) and _truthy(user.get("ai_opt_out"))


async def opted_out_user_ids(user_ids: Iterable[Any]) -> set[int]:
    """Return the subset of ``user_ids`` that opted out (fails closed)."""

    identifiers = {uid for uid in (_user_id(value) for value in user_ids) if uid}
    if not identifiers:
        return set()
    try:
        return set(await user_repo.list_ai_opted_out_user_ids(identifiers))
    except Exception as exc:  # pragma: no cover - defensive, fails closed
        log_error("AI consent lookup failed; treating users as opted out", error=str(exc))
        return identifiers


async def filter_ai_allowed(user_ids: Iterable[Any]) -> set[int]:
    """Return the subset of ``user_ids`` whose content may be processed by AI."""

    identifiers = {uid for uid in (_user_id(value) for value in user_ids) if uid}
    return identifiers - await opted_out_user_ids(identifiers)


async def is_ai_allowed_for_user(user_id: Any) -> bool:
    """Return False when the user opted out of AI processing.

    Content without an identifiable portal user (``None``) is allowed.
    """

    identifier = _user_id(user_id)
    if identifier is None:
        return True
    return identifier not in await opted_out_user_ids([identifier])


async def is_ai_allowed_for_email(email: str | None) -> bool:
    if not str(email or "").strip():
        return True
    try:
        return not await user_repo.ai_opt_out_exists_for_email(email)
    except Exception as exc:  # pragma: no cover - defensive, fails closed
        log_error("AI consent email lookup failed; treating contact as opted out", error=str(exc))
        return False


async def is_ai_allowed_for_staff(staff_id: Any) -> bool:
    """Staff records map to portal users by email address."""

    identifier = _user_id(staff_id)
    if identifier is None:
        return True
    from app.repositories import staff as staff_repo

    try:
        staff = await staff_repo.get_staff_by_id(identifier)
    except Exception as exc:  # pragma: no cover - defensive, fails closed
        log_error("AI consent staff lookup failed; treating contact as opted out", error=str(exc))
        return False
    return await is_ai_allowed_for_email((staff or {}).get("email"))


async def is_ai_allowed_for_ticket(ticket: Mapping[str, Any] | None) -> bool:
    """Return False when the ticket's requester opted out of AI processing."""

    if not ticket:
        return True
    if not await is_ai_allowed_for_user(ticket.get("requester_id")):
        return False
    return await is_ai_allowed_for_staff(ticket.get("requester_staff_id"))


async def is_ai_allowed_for_ticket_id(ticket_id: Any) -> bool:
    identifier = _user_id(ticket_id)
    if identifier is None:
        return True
    try:
        ticket = await ai_consent_repo.get_ticket_requester(identifier)
    except Exception as exc:  # pragma: no cover - defensive, fails closed
        log_error("AI consent ticket lookup failed; treating requester as opted out", error=str(exc))
        return False
    return await is_ai_allowed_for_ticket(ticket)


async def is_ai_allowed_for_chat(room: Mapping[str, Any] | None) -> bool:
    """Return False when the chat's customer (its creator) opted out."""

    if not room:
        return True
    return await is_ai_allowed_for_user(room.get("created_by_user_id"))


async def is_ai_allowed_for_call(recording: Mapping[str, Any] | None) -> bool:
    """Return False when an identifiable party to a call opted out."""

    if not recording:
        return True
    for key in ("caller_email", "callee_email"):
        if not await is_ai_allowed_for_email(recording.get(key)):
            return False
    for key in ("caller_staff_id", "callee_staff_id"):
        if recording.get(key) and not await is_ai_allowed_for_staff(recording.get(key)):
            return False
    phone = str(recording.get("phone_number") or "").strip()
    if phone:
        try:
            user = await user_repo.get_user_by_phone(phone)
        except Exception as exc:  # pragma: no cover - defensive, fails closed
            log_error("AI consent phone lookup failed; treating caller as opted out", error=str(exc))
            return False
        if is_opted_out_record(user):
            return False
    return True


async def filter_replies_for_ai(
    replies: Sequence[Mapping[str, Any]] | None,
) -> list[Mapping[str, Any]]:
    """Drop replies written by people who opted out of AI processing."""

    items = [reply for reply in replies or [] if isinstance(reply, Mapping)]
    blocked = await opted_out_user_ids(reply.get("author_id") for reply in items)
    if not blocked:
        return list(items)
    return [reply for reply in items if _user_id(reply.get("author_id")) not in blocked]


async def prepare_rag_source(
    source_type: str, item: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """Return ``item`` safe to index, or None when it must stay out of the index.

    Tickets and chats belonging to an opted-out user are excluded entirely, and
    replies an opted-out user wrote on someone else's ticket are removed.
    """

    normalised = str(source_type or "").strip()
    if normalised == "tickets":
        if not await is_ai_allowed_for_ticket(item):
            return None
    elif normalised == "ticket_comments":
        if item.get("requester_id") is not None and not await is_ai_allowed_for_user(
            item.get("requester_id")
        ):
            return None
    elif normalised == "chats":
        if not await is_ai_allowed_for_chat(item):
            return None
    else:
        return item
    replies = item.get("replies")
    if not replies:
        return item
    kept = await filter_replies_for_ai(replies)
    if len(kept) == len(replies):
        return item
    if normalised == "ticket_comments" and not kept:
        return None
    cleaned = dict(item)
    cleaned["replies"] = kept
    return cleaned


async def filter_agent_sources(sources: Mapping[str, Any]) -> dict[str, Any]:
    """Remove opted-out users' tickets, notes and chats from agent evidence."""

    filtered: dict[str, Any] = dict(sources)
    for source_type in ("tickets", "ticket_comments", "chats"):
        kept: list[Any] = []
        for item in sources.get(source_type) or []:
            if not isinstance(item, Mapping):
                continue
            prepared = await prepare_rag_source(source_type, item)
            if prepared is not None:
                kept.append(prepared)
        if source_type in sources:
            filtered[source_type] = kept
    return filtered


async def purge_user_from_rag_index(user_id: int) -> dict[str, int]:
    """Remove an opted-out user's existing content from the RAG index."""

    from app.repositories import rag_index as rag_repo
    from app.services import rag_outbox

    identifier = _user_id(user_id)
    counts = {"tickets": 0, "ticket_comments": 0, "chats": 0, "reindexed": 0}
    if identifier is None:
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
    if is_opted_out_record(previous) and (previous or {}).get("ai_opt_out_at"):
        return {"ai_opt_out": 1}
    return {"ai_opt_out": 1, "ai_opt_out_at": datetime.now(timezone.utc).replace(tzinfo=None)}


async def set_user_ai_opt_out(
    target: Mapping[str, Any],
    opt_out: bool,
    *,
    actor_user_id: int,
    request: Any = None,
    source: str,
) -> dict[str, Any] | None:
    """Store a change of the opt-out flag, audit it and purge the AI index.

    Returns the updated user, or None when the flag already had that value.
    """

    from app.services import audit as audit_service

    if is_opted_out_record(target) == bool(opt_out):
        return None
    user_id = int(target["id"])
    updated = await user_repo.update_user(user_id, **opt_out_updates(bool(opt_out), target))
    await audit_service.record(
        action="user.ai_opt_out" if opt_out else "user.ai_opt_in",
        request=request,
        user_id=int(actor_user_id),
        entity_type="user",
        entity_id=user_id,
        before={"ai_opt_out": bool(target.get("ai_opt_out")), "ai_opt_out_at": target.get("ai_opt_out_at")},
        after={"ai_opt_out": bool(updated.get("ai_opt_out")), "ai_opt_out_at": updated.get("ai_opt_out_at")},
        metadata={"changed_by_self": int(actor_user_id) == user_id, "source": source},
    )
    if opt_out:
        try:
            await purge_user_from_rag_index(user_id)
        except Exception as exc:  # pragma: no cover - the opt-out itself is saved
            log_error("Failed to purge opted-out user from the AI index", user_id=user_id, error=str(exc))
    return updated
