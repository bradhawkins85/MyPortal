from __future__ import annotations

import asyncio
import html
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urljoin

import bleach
import httpx

from app.core.config import get_settings
from app.core.logging import log_error
from app.repositories import chat as chat_repo
from app.repositories import knowledge_base as kb_repo
from app.services import audit as audit_service
from app.services import knowledge_base as knowledge_base_service
from app.services import matrix as matrix_service
from app.services import webhook_monitor
from app.services.realtime import refresh_notifier

_running = False
_stop = False
_WORD_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{2,}")
_ACK_MESSAGE = "Thanks — your request has been received. A technician will be assigned shortly."
_AI_DISCLAIMER = "This recommendation was generated using AI and may not apply to your specific issue."
_MONITOR_PROMPT_PREVIEW_LIMIT = 2000
_MONITOR_RESPONSE_PREVIEW_LIMIT = 2000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _truncate_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _monitor_payload(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in dict(payload or {}).items():
        if key in {"prompt", "transcript"}:
            # Prompts can include complete chat transcripts, so avoid persisting
            # customer-provided content in the webhook monitor.
            safe[f"{key}_length"] = len(str(value or ""))
        elif key in {"body", "message"}:
            safe[f"{key}_preview"] = _truncate_text(
                bleach.clean(str(value or ""), tags=[], strip=True),
                limit=_MONITOR_PROMPT_PREVIEW_LIMIT,
            )
        else:
            safe[key] = _json_safe(value)
    return safe


async def _create_monitor_event(
    name: str,
    target_url: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return await webhook_monitor.create_manual_event(
            name=name,
            target_url=target_url,
            payload=_monitor_payload(payload),
            max_attempts=1,
            backoff_seconds=0,
        )
    except Exception as exc:
        log_error(
            "AI waiting assistant webhook monitor create failed",
            name=name,
            error=str(exc),
        )
        return {}


async def _record_monitor_success(
    event: Mapping[str, Any] | None,
    *,
    response_status: int | None = None,
    response_body: Any = None,
    request_body: Mapping[str, Any] | None = None,
) -> None:
    event_id = (event or {}).get("id")
    if not event_id:
        return
    try:
        await webhook_monitor.record_manual_success(
            int(event_id),
            attempt_number=1,
            response_status=response_status,
            response_body=_truncate_text(
                response_body,
                limit=_MONITOR_RESPONSE_PREVIEW_LIMIT,
            ),
            request_body=_monitor_payload(request_body),
        )
    except Exception as exc:
        log_error(
            "AI waiting assistant webhook monitor success update failed",
            event_id=event_id,
            error=str(exc),
        )


async def _record_monitor_failure(
    event: Mapping[str, Any] | None,
    *,
    error_message: Any,
    response_status: int | None = None,
    response_body: Any = None,
    request_body: Mapping[str, Any] | None = None,
) -> None:
    event_id = (event or {}).get("id")
    if not event_id:
        return
    try:
        await webhook_monitor.record_manual_failure(
            int(event_id),
            attempt_number=1,
            status="error",
            error_message=_truncate_text(error_message, limit=1000),
            response_status=response_status,
            response_body=_truncate_text(
                response_body,
                limit=_MONITOR_RESPONSE_PREVIEW_LIMIT,
            ),
            request_body=_monitor_payload(request_body),
        )
    except Exception as exc:
        log_error(
            "AI waiting assistant webhook monitor failure update failed",
            event_id=event_id,
            error=str(exc),
        )


def _enabled() -> bool:
    settings = get_settings()
    return bool(
        settings.matrix_enabled
        and settings.matrixbot_ai_waiting_assistant_enabled
        and settings.matrixbot_ai_ollama_enabled
        and (settings.matrixbot_ai_ollama_url or settings.matrixbot_ai_ollama_model)
        and settings.matrixbot_ai_max_responses > 0
    )


def _normalise_tags(tags: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        text = str(tag or "").strip().lower()
        if not text or text in seen or len(text) > 80:
            continue
        seen.add(text)
        result.append(text)
    return result[:40]


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def _ollama_generate(prompt: str, *, json_format: bool = False) -> str:
    settings = get_settings()
    base_url = (settings.matrixbot_ai_ollama_url or "http://127.0.0.1:11434").rstrip("/")
    model = (settings.matrixbot_ai_ollama_model or "llama3").strip()
    body: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
    if json_format:
        body["format"] = "json"
    target_url = urljoin(f"{base_url}/", "api/generate")
    monitor_event = await _create_monitor_event(
        "MATRIXBOT_AI Ollama generate",
        target_url,
        {"model": model, "json_format": json_format, "prompt": prompt},
    )
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(target_url, json=body)
        response.raise_for_status()
        payload = response.json()
        result = str(payload.get("response") or "").strip()
        await _record_monitor_success(
            monitor_event,
            response_status=response.status_code,
            response_body=result,
            request_body={
                "model": model,
                "json_format": json_format,
                "prompt": prompt,
            },
        )
        return result
    except Exception as exc:
        await _record_monitor_failure(
            monitor_event,
            error_message=str(exc),
            request_body={
                "model": model,
                "json_format": json_format,
                "prompt": prompt,
            },
        )
        raise


async def _chat_transcript(room_id: int) -> str:
    messages = await chat_repo.get_messages(room_id, limit=200)
    lines: list[str] = []
    for msg in messages:
        body = bleach.clean(str(msg.get("body") or ""), tags=[], strip=True)
        if not body.strip():
            continue
        sender = msg.get("sender_display_name") or msg.get("sender_matrix_id") or "Participant"
        lines.append(f"{sender}: {' '.join(body.split())}")
    return "\n".join(lines)[-12000:]


def _room_kb_access_context(room: Mapping[str, Any]) -> knowledge_base_service.ArticleAccessContext:
    """Build the KB access context for a waiting-assistant chat room.

    The waiting assistant acts on behalf of the customer waiting in the room,
    not as a technician or background super-admin process.  Limit article
    recommendations to anonymous articles, articles explicitly available to
    the room creator, and company-scoped articles available to the room's
    company.  Company-admin-only articles are intentionally excluded because
    a chat room only carries company membership, not verified admin status.
    """

    user_id: int | None = None
    try:
        candidate_user_id = room.get("created_by_user_id")
        if candidate_user_id is not None:
            user_id = int(candidate_user_id)
    except (TypeError, ValueError):
        user_id = None

    memberships: dict[int, Mapping[str, Any]] = {}
    try:
        company_id = int(room.get("company_id"))
    except (TypeError, ValueError):
        company_id = 0
    if company_id > 0:
        memberships[company_id] = {"company_id": company_id, "is_admin": False}

    user = {"id": user_id, "is_super_admin": False} if user_id is not None else None
    return knowledge_base_service.ArticleAccessContext(
        user=user,
        user_id=user_id,
        is_super_admin=False,
        memberships=memberships,
    )


def _article_visible_to_room(article: Mapping[str, Any], room: Mapping[str, Any]) -> bool:
    return knowledge_base_service._article_visible(article, _room_kb_access_context(room))


async def _extract_keywords(transcript: str) -> list[str]:
    prompt = f"""Extract keywords, products, technologies, error messages, categories, and other concepts from this support chat.
Return JSON only with a key named keywords containing an array of lowercase strings. Do not include personal names, email addresses, phone numbers, or secrets.

Chat transcript:
{transcript}
"""
    text = await _ollama_generate(prompt, json_format=True)
    data = _extract_json_object(text)
    keywords = data.get("keywords") if isinstance(data, dict) else []
    if isinstance(keywords, list):
        parsed = _normalise_tags(keywords)
        if parsed:
            return parsed
    return _normalise_tags(_WORD_RE.findall(transcript.lower()))[:20]


async def _article_relevant(transcript: str, article: Mapping[str, Any]) -> bool:
    content = bleach.clean(str(article.get("content") or ""), tags=[], strip=True)
    prompt = f"""Decide whether this knowledge base article is genuinely relevant to the user's support issue.
Return JSON only: {{"relevant": true}} or {{"relevant": false}}.

Support chat:
{transcript[:6000]}

Article title: {article.get('title') or ''}
Article summary: {article.get('summary') or ''}
Article content excerpt: {content[:5000]}
"""
    text = await _ollama_generate(prompt, json_format=True)
    data = _extract_json_object(text)
    return bool(data.get("relevant"))


async def _summarise_article(article: Mapping[str, Any]) -> str:
    content = bleach.clean(str(article.get("content") or ""), tags=[], strip=True)
    prompt = f"""Summarize this knowledge base article in plain language for a user waiting for support.
Use no more than three concise sentences. Focus on the article purpose or resolution steps.

Title: {article.get('title') or ''}
Summary: {article.get('summary') or ''}
Content: {content[:6000]}
"""
    summary = await _ollama_generate(prompt)
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(summary.split()))
    return " ".join(sentences[:3]).strip()[:900]


async def _audit(action: str, room_id: int, value: Mapping[str, Any] | None = None) -> None:
    try:
        await audit_service.log_action(
            action=action,
            entity_type="chat_room",
            entity_id=room_id,
            user_id=None,
            new_value=dict(value or {}),
        )
    except Exception as exc:
        log_error("AI waiting assistant audit failed", action=action, room_id=room_id, error=str(exc))


async def _send_bot_message(
    room: Mapping[str, Any],
    body: str,
    *,
    formatted_body: str | None = None,
    response_count: int | None = None,
) -> bool:
    matrix_room_id = str(room["matrix_room_id"])
    monitor_event = await _create_monitor_event(
        "MATRIXBOT_AI Matrix message",
        f"matrix://{matrix_room_id}",
        {
            "room_id": room.get("id"),
            "matrix_room_id": matrix_room_id,
            "message": body,
        },
    )
    try:
        send_kwargs: dict[str, Any] = {}
        if formatted_body:
            send_kwargs["formatted_body"] = formatted_body
        response = await matrix_service.send_message(matrix_room_id, body, **send_kwargs)
        event_id = response.get("event_id")
        await _record_monitor_success(
            monitor_event,
            response_status=200,
            response_body={"event_id": event_id},
            request_body={
                "room_id": room.get("id"),
                "matrix_room_id": matrix_room_id,
                "message": body,
            },
        )
    except Exception as exc:
        await _record_monitor_failure(
            monitor_event,
            error_message=str(exc),
            request_body={
                "room_id": room.get("id"),
                "matrix_room_id": matrix_room_id,
                "message": body,
            },
        )
        log_error(
            "AI waiting assistant Matrix send failed",
            room_id=room.get("id"),
            error=str(exc),
        )
        return False
    now = _utcnow()
    msg = await chat_repo.add_message(
        room_id=int(room["id"]),
        matrix_event_id=event_id,
        sender_matrix_id=get_settings().matrix_bot_user_id or "@myportal-ai-waiting-assistant",
        body=body,
        sender_user_id=None,
        sender_display_name="AI Waiting Assistant",
        sent_at=now,
    )
    if response_count is None:
        await chat_repo.increment_ai_bot_response(int(room["id"]), now)
    await refresh_notifier.broadcast_refresh(
        topics=[f"chat:room:{int(room['id'])}"],
        data={"message": _json_safe({**msg, "sent_at": now.isoformat()}), "room_id": int(room["id"])},
    )
    return True


def _build_article_recommendation_messages(article: Mapping[str, Any], link: str, summary: str) -> list[tuple[str, str]]:
    title = str(article.get("title") or "Knowledge base article").strip() or "Knowledge base article"
    clean_link = str(link).strip()
    clean_summary = " ".join(str(summary or "").split()) or "No summary is available for this article."

    safe_title = html.escape(title)
    safe_link = html.escape(clean_link, quote=True)
    messages = [
        (
            f"While you wait, this article may help: {title}",
            f"<p>While you wait, this article may help: {safe_title}</p>",
        ),
        (
            clean_link,
            f'<p><a href="{safe_link}">{safe_link}</a></p>',
        ),
    ]
    messages.append((clean_summary, f"<p>{html.escape(clean_summary)}</p>"))
    messages.append((_AI_DISCLAIMER, f"<p>{html.escape(_AI_DISCLAIMER)}</p>"))
    return messages


# Backwards-compatible helper for callers/tests that need a single Matrix payload.
def _build_article_recommendation_message(article: Mapping[str, Any], link: str, summary: str) -> tuple[str, str]:
    messages = _build_article_recommendation_messages(article, link, summary)
    body = "\n\n".join(part[0] for part in messages)
    formatted_body = "".join(part[1] for part in messages)
    return body, formatted_body


async def handle_user_message(room_id: int, sent_at: datetime | None = None) -> None:
    await chat_repo.mark_user_activity(room_id, sent_at)
    await chat_repo.cancel_active_ai_queue_for_room(room_id, "new_user_message_timer_reset")
    await _audit("matrix_ai_waiting_assistant.user_activity", room_id)


async def handle_chat_opened(room_id: int, opened_at: datetime | None = None) -> None:
    """Start the waiting-assistant timer when a customer chat opens.

    Some Matrix-backed rooms are created when a tray popup or customer chat
    window opens, before the customer sends a first message.  Treat that open
    event as customer activity so the acknowledgement can be sent when no
    technician has replied. Auto-assignment alone must not suppress the waiting
    assistant because assigned technicians may not have responded yet.
    """
    room = await chat_repo.get_room(room_id)
    if not room or room.get("status") != "open" or await chat_repo.has_technician_message(room_id):
        return
    await chat_repo.mark_user_activity(room_id, opened_at)
    await chat_repo.cancel_active_ai_queue_for_room(room_id, "chat_opened_timer_reset")
    await _audit("matrix_ai_waiting_assistant.chat_opened", room_id)


async def handle_technician_takeover(room_id: int, user_id: int | None = None) -> None:
    await chat_repo.cancel_active_ai_queue_for_room(room_id, "technician_assigned")
    await _audit("matrix_ai_waiting_assistant.technician_takeover", room_id, {"user_id": user_id})


async def handle_chat_closed(room_id: int) -> None:
    await chat_repo.cancel_active_ai_queue_for_room(room_id, "chat_closed")
    await _audit("matrix_ai_waiting_assistant.queue_cancelled", room_id, {"reason": "chat_closed"})


async def _eligible_room(room: Mapping[str, Any]) -> bool:
    if not _enabled():
        return False
    room_id = int(room.get("id") or 0)
    if room.get("status") != "open" or not room_id:
        return False
    if await chat_repo.has_technician_message(room_id):
        return False
    return int(room.get("ai_bot_response_count") or 0) < get_settings().matrixbot_ai_max_responses


async def scan_waiting_rooms_once() -> None:
    if not _enabled():
        return
    settings = get_settings()
    due_before = _utcnow() - timedelta(minutes=settings.matrixbot_ai_response_delay_minutes)
    rooms = await chat_repo.list_ai_waiting_candidate_rooms(due_before)
    for room in rooms:
        if not await _eligible_room(room):
            continue
        count = int(room.get("ai_bot_response_count") or 0)
        if count == 0:
            reserved = await chat_repo.reserve_ai_bot_response(int(room["id"]), expected_count=0, when=_utcnow())
            if not reserved:
                continue
            if await _send_bot_message(room, _ACK_MESSAGE, response_count=1):
                await _audit("matrix_ai_waiting_assistant.acknowledgement_sent", int(room["id"]))
            else:
                await chat_repo.release_ai_bot_response_reservation(int(room["id"]), reserved_count=1)
            continue
        if count == 1 and settings.matrixbot_ai_max_responses >= 2:
            active = await chat_repo.get_active_ai_queue_item(int(room["id"]))
            if active:
                continue
            now = _utcnow()
            queue = await chat_repo.create_ai_queue_item(
                chat_room_id=int(room["id"]),
                queue_identifier=uuid.uuid4().hex,
                expires_at=now + timedelta(minutes=settings.matrixbot_ai_queue_timeout_minutes),
                next_attempt_at=now,
                created_for_response_number=2,
            )
            await _audit("matrix_ai_waiting_assistant.analysis_queued", int(room["id"]), {"queue_id": queue.get("id")})


async def _process_queue_item(item: Mapping[str, Any]) -> None:
    room_id = int(item["chat_room_id"])
    room = await chat_repo.get_room(room_id)
    settings = get_settings()
    now = _utcnow()
    if not room or not await _eligible_room(room):
        await chat_repo.update_ai_queue_item(int(item["id"]), status="cancelled", cancellation_reason="room_no_longer_eligible")
        await _audit("matrix_ai_waiting_assistant.queue_cancelled", room_id, {"reason": "room_no_longer_eligible"})
        return
    expires_at = _as_datetime(item.get("expires_at"))
    if expires_at and expires_at <= now:
        await chat_repo.update_ai_queue_item(int(item["id"]), status="timed_out", cancellation_reason="maximum_lifetime_exceeded")
        await _audit("matrix_ai_waiting_assistant.queue_timed_out", room_id, {"queue_id": item.get("id")})
        return
    await chat_repo.update_ai_queue_item(int(item["id"]), status="processing", last_attempt_at=now, retry_count=int(item.get("retry_count") or 0) + 1)
    await _audit("matrix_ai_waiting_assistant.analysis_requested", room_id, {"queue_id": item.get("id")})
    try:
        transcript = await _chat_transcript(room_id)
        keywords = await _extract_keywords(transcript)
        keyword_set = set(keywords)
        candidates: list[dict[str, Any]] = []
        for article in await kb_repo.list_articles(include_unpublished=False):
            if not _article_visible_to_room(article, room):
                continue
            tags = _normalise_tags(article.get("ai_tags") or [])
            if not tags:
                continue
            matched = sorted(keyword_set & set(tags))
            confidence_base = max(1, min(len(tags), len(keyword_set))) if tags else 1
            confidence = min(100.0, (len(matched) / confidence_base) * 100) if tags else 0.0
            if confidence >= settings.matrixbot_ai_kb_confidence_threshold:
                relevant = await _article_relevant(transcript, article)
                candidates.append({
                    "id": article.get("id"), "slug": article.get("slug"), "title": article.get("title"),
                    "article_tags": tags, "matched_tags": matched, "confidence": round(confidence, 2),
                    "semantic_relevant": relevant, "sent": False, "analysed_at": now.isoformat(),
                })
        eligible = [c for c in candidates if c["semantic_relevant"]]
        eligible.sort(key=lambda c: c["confidence"], reverse=True)
        sent = False
        if eligible and int(room.get("ai_bot_response_count") or 0) < settings.matrixbot_ai_max_responses:
            already = chat_repo.decode_ai_json_field(room.get("ai_matched_articles")) or []
            sent_ids = {str(a.get("id") or a.get("slug")) for a in already if a.get("sent")}
            selected = next((c for c in eligible if str(c.get("id") or c.get("slug")) not in sent_ids), None)
            if selected:
                article = await kb_repo.get_article_by_id(int(selected["id"]))
                if article and _article_visible_to_room(article, room):
                    summary = await _summarise_article(article)
                    base = str(settings.public_base_url or settings.portal_url or "").rstrip("/")
                    path = f"/knowledge-base/articles/{article['slug']}"
                    link = f"{base}{path}" if base else path
                    messages = _build_article_recommendation_messages(article, link, summary)
                    expected_count = int(room.get("ai_bot_response_count") or 0)
                    reserved_count = expected_count + 1
                    reserved = await chat_repo.reserve_ai_bot_response(room_id, expected_count=expected_count, when=_utcnow())
                    sent_all = False
                    if reserved:
                        sent_all = True
                        for message, formatted_message in messages:
                            if not await _send_bot_message(
                                room,
                                message,
                                formatted_body=formatted_message,
                                response_count=reserved_count,
                            ):
                                sent_all = False
                                break
                    if reserved and sent_all:
                        selected["sent"] = True
                        selected["sent_at"] = _utcnow().isoformat()
                        sent = True
                        await _audit("matrix_ai_waiting_assistant.article_recommendation_sent", room_id, selected)
                    elif reserved:
                        await chat_repo.release_ai_bot_response_reservation(room_id, reserved_count=reserved_count)
        latest_confidence = eligible[0]["confidence"] if eligible else (max((c["confidence"] for c in candidates), default=None))
        await chat_repo.update_ai_analysis(room_id, extracted_keywords=keywords, matched_articles=candidates, confidence=latest_confidence)
        await chat_repo.update_ai_queue_item(int(item["id"]), status="completed", result_payload={"keywords": keywords, "matches": candidates, "sent": sent})
        await _audit("matrix_ai_waiting_assistant.analysis_completed", room_id, {"matches": len(candidates), "sent": sent})
    except Exception as exc:
        log_error("AI waiting assistant Ollama analysis failed", room_id=room_id, error=str(exc))
        retry_at = now + timedelta(minutes=settings.matrixbot_ai_queue_retry_minutes)
        if expires_at and retry_at >= expires_at:
            await chat_repo.update_ai_queue_item(int(item["id"]), status="timed_out", cancellation_reason="ollama_unavailable_timeout")
            await _audit("matrix_ai_waiting_assistant.queue_timed_out", room_id, {"error": str(exc)[:200]})
        else:
            await chat_repo.update_ai_queue_item(int(item["id"]), status="queued", next_attempt_at=retry_at)
            await _audit("matrix_ai_waiting_assistant.ollama_retry_queued", room_id, {"error": str(exc)[:200], "retry_at": retry_at.isoformat()})


async def process_queue_once() -> None:
    if not _enabled():
        return
    due = await chat_repo.list_due_ai_queue_items(_utcnow(), limit=25)
    await asyncio.gather(*(_process_queue_item(item) for item in due))


async def run_worker_loop() -> None:
    global _running, _stop
    if _running:
        return
    _running = True
    _stop = False
    try:
        while not _stop:
            try:
                if not _enabled():
                    await chat_repo.cancel_all_active_ai_queue("feature_or_ollama_disabled")
                    await asyncio.sleep(60)
                    continue
                await scan_waiting_rooms_once()
                await process_queue_once()
            except Exception as exc:
                log_error("AI waiting assistant worker error", error=str(exc))
            await asyncio.sleep(60)
    finally:
        _running = False


def stop_worker_loop() -> None:
    global _stop
    _stop = True
