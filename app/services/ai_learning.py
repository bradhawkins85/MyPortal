"""How MyPortal learns from technicians' 👍/👎 on linked items.

A lesson is one technician vote on a linked item (a Related item on a
ticket), remembered with what that ticket was about (its mean RAG
embedding). When MyPortal later scores the same item for another ticket or
search, votes from similar tickets nudge the score: 👎 down, 👍 up. Only the
adjustment number crosses companies; no text does.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from app.core.logging import logger
from app.repositories import ai_quality as quality_repo
from app.repositories import rag_index as rag_index_repo
from app.repositories import rag_relationships as rel_repo

SIMILARITY_THRESHOLD = 0.8
STEP = 0.05
MAX_PENALTY = 0.3
MAX_BOOST = 0.15
HALF_LIFE_DAYS = 180
LESSON_CACHE_SECONDS = 60
# Votes older than three half-lives count for under 1/8; don't load them.
LESSON_WINDOW_DAYS = 3 * HALF_LIFE_DAYS
# A voted ticket with no RAG document is retried at most this often.
MISSING_EMBEDDING_RETRY_SECONDS = 3600

LessonKey = tuple[str, str]


@dataclass(frozen=True)
class LinkLesson:
    target_type: str
    target_id: str
    vote: int  # +1 for 👍, -1 for 👎
    embedding: tuple[float, ...] | None
    voted_at: datetime


_cache: dict[LessonKey, list[LinkLesson]] | None = None
_cache_loaded_at = 0.0
_reload_lock = asyncio.Lock()
_missing_embeddings: dict[tuple[int, str], float] = {}


def invalidate_cache() -> None:
    global _cache
    _cache = None


def link_adjustment(
    lessons: Sequence[LinkLesson], subject_embedding: Sequence[float] | None, *, now: datetime
) -> float:
    """Bounded score change from votes on subjects similar to this one."""
    if not subject_embedding:
        return 0.0
    total = 0.0
    for lesson in lessons:
        if not lesson.embedding or len(lesson.embedding) != len(subject_embedding):
            continue
        similarity = sum(a * b for a, b in zip(lesson.embedding, subject_embedding))
        if similarity < SIMILARITY_THRESHOLD:
            continue
        age_days = max(0, (now - lesson.voted_at).days)
        total += lesson.vote * STEP * similarity * 0.5 ** (age_days / HALF_LIFE_DAYS)
    return round(max(-MAX_PENALTY, min(MAX_BOOST, total)), 4)


def _current_embedding_model() -> str:
    from app.services import rag_index

    return rag_index.embedding_model()


def _as_utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        return datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _stored_vector(value: Any) -> tuple[float, ...] | None:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        vector = tuple(float(item) for item in parsed or ())
    except (TypeError, ValueError):
        return None
    return vector or None


async def _subject_embedding(vote: dict[str, Any], model: str) -> tuple[float, ...] | None:
    if vote.get("subject_embedding_model") == model:
        stored = _stored_vector(vote.get("subject_embedding_json"))
        if stored:
            return stored
    if vote.get("subject_type") != "ticket":
        return None
    miss_key = (int(vote["output_id"]), model)
    retry_at = _missing_embeddings.get(miss_key)
    if retry_at is not None and time.monotonic() < retry_at:
        return None
    document = await rag_index_repo.get_document_by_source("tickets", str(vote.get("subject_id")), model)
    vector = (
        await rel_repo.document_mean_embedding(int(document["id"]), embedding_model=model) if document else None
    )
    if not vector:
        _missing_embeddings[miss_key] = time.monotonic() + MISSING_EMBEDDING_RETRY_SECONDS
        return None
    _missing_embeddings.pop(miss_key, None)
    await quality_repo.store_subject_embedding(int(vote["output_id"]), vector, model)
    return tuple(vector)


def _fresh_cache() -> dict[LessonKey, list[LinkLesson]] | None:
    if _cache is not None and time.monotonic() - _cache_loaded_at < LESSON_CACHE_SECONDS:
        return _cache
    return None


async def load_link_lessons() -> dict[LessonKey, list[LinkLesson]]:
    cached = _fresh_cache()
    if cached is not None:
        return cached
    async with _reload_lock:
        # Another request may have reloaded while this one waited.
        cached = _fresh_cache()
        if cached is not None:
            return cached
        return await _reload_link_lessons()


async def _reload_link_lessons() -> dict[LessonKey, list[LinkLesson]]:
    global _cache, _cache_loaded_at
    model = _current_embedding_model()
    since = datetime.now(timezone.utc) - timedelta(days=LESSON_WINDOW_DAYS)
    lessons: dict[LessonKey, list[LinkLesson]] = {}
    for vote in await quality_repo.list_link_votes(since=since):
        key = (str(vote["target_type"]), str(vote["target_id"]))
        lessons.setdefault(key, []).append(LinkLesson(
            target_type=key[0],
            target_id=key[1],
            vote=-1 if vote.get("rating") == "down" else 1,
            embedding=await _subject_embedding(vote, model),
            voted_at=_as_utc(vote.get("voted_at")),
        ))
    _cache, _cache_loaded_at = lessons, time.monotonic()
    return lessons


async def lessons_or_empty() -> dict[LessonKey, list[LinkLesson]]:
    """Lessons for hot paths: any failure means no learning, never an error."""
    try:
        return await load_link_lessons()
    except Exception as exc:
        logger.warning("Could not load AI link lessons; scoring without them: {}", exc)
        return {}


async def adjustment_for(target_type: str, target_id: str, subject_embedding: Sequence[float] | None) -> float:
    lessons = (await lessons_or_empty()).get((str(target_type), str(target_id)))
    if not lessons:
        return 0.0
    return link_adjustment(lessons, subject_embedding, now=datetime.now(timezone.utc))
