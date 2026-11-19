"""How MyPortal's AI text features learn from technicians' 👍/👎.

Two kinds of learning reach a prompt:

* **Rated examples** — a few outputs of the same feature that technicians at
  the *same company* rated, passed as untrusted records the model may only
  use as a style and completeness reference. Examples never cross companies.
* **Lessons** — guidance lines built nightly from 👎 reason codes and counts
  across all companies (never from output text), added to the trusted
  instructions. Admins can undo a lesson.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.logging import logger
from app.repositories import ai_quality as quality_repo
from app.services.ai_prompt_security import UntrustedRecord

POSITIVE_EXAMPLES = 2
NEGATIVE_EXAMPLES = 1
EXAMPLE_MAX_CHARS = 600
EXAMPLE_WINDOW_DAYS = 180
LESSON_WINDOW_DAYS = 90
LESSON_MIN_VOTES = 3
LESSON_MIN_SHARE = 0.2
# Same shape as ai_prompt_security's citation check: copied citations from an
# old output would make a new draft cite records it was never given.
_CITATION_RE = re.compile(r"\[[A-Za-z][A-Za-z0-9 _-]*:[^\]\n]+\]")
EXAMPLE_ALLOWED_USE = (
    "Use only as a style and completeness reference; never copy facts, names, numbers or details from it"
)

REASON_GUIDANCE: dict[str, str] = {
    "incomplete": "{label} outputs were often rated incomplete: cover every point a technician needs, "
                  "including the current status and next step.",
    "incorrect": "{label} outputs were often rated incorrect: state only what the records support and say "
                 "when something is uncertain.",
    "unrelated": "{label} outputs were often rated unrelated: stay on this record's actual issue and leave out "
                 "loosely related material.",
    "irrelevant": "{label} outputs were often rated not useful: lead with what a technician can act on.",
    "uncited": "{label} outputs were often rated as missing sources: say which record each key point comes from.",
    "unsafe": "{label} outputs were often rated unsafe: never suggest destructive, insecure or irreversible "
              "steps without a clear warning.",
    "other": "",
}


@dataclass(frozen=True)
class TextLearning:
    guidance: tuple[str, ...] = ()
    records: tuple[UntrustedRecord, ...] = ()

    def instructions(self, base: str) -> str:
        if not self.guidance:
            return base
        return base + "\n\nLessons from technician feedback on recent outputs:\n- " + "\n- ".join(self.guidance)


EMPTY = TextLearning()


def _feature_label(feature: str) -> str:
    from app.services import llm_usage

    return llm_usage.feature_label(feature)


def guidance_for(feature: str, reason: str) -> str:
    template = REASON_GUIDANCE.get(reason) or ""
    return template.format(label=_feature_label(feature)) if template else ""


def _example_record(feature: str, row: dict) -> UntrustedRecord:
    helpful = row.get("rating") == "up"
    content = {"rating": "helpful" if helpful else "not helpful"}
    if not helpful:
        content["reason"] = row.get("reason")
    text = _CITATION_RE.sub("[citation]", str(row.get("output_text") or ""))
    content["text"] = text[:EXAMPLE_MAX_CHARS]
    return UntrustedRecord(
        f"rated-example:{row['output_id']}",
        f"a previous {_feature_label(feature)} that a technician at this company rated "
        f"{'helpful' if helpful else 'not helpful'}",
        content,
        EXAMPLE_ALLOWED_USE,
    )


async def context_for(feature: str, company_id: int | None, *, subject_id: str | int | None = None) -> TextLearning:
    """Learning for one prompt; any failure means no learning, never an error."""
    try:
        guidance = tuple(
            line for line in (guidance_for(feature, str(row["reason"])) for row in
                              await quality_repo.active_lessons(feature)) if line
        )
        records: list[UntrustedRecord] = []
        if company_id is not None:
            since = datetime.now(timezone.utc) - timedelta(days=EXAMPLE_WINDOW_DAYS)
            exclude = str(subject_id) if subject_id is not None else None
            for rating, limit in (("up", POSITIVE_EXAMPLES), ("down", NEGATIVE_EXAMPLES)):
                rows = await quality_repo.rated_examples(feature, int(company_id), exclude_subject_id=exclude,
                                                         rating=rating, limit=limit, since=since)
                records.extend(_example_record(feature, row) for row in rows)
        return TextLearning(guidance=guidance, records=tuple(records))
    except Exception as exc:
        logger.warning("Could not load AI text learning for {}; using the plain prompt: {}", feature, exc)
        return EMPTY


async def refresh_lessons(*, now: datetime | None = None) -> int:
    """Rebuild lessons from recent 👎 reasons (counts only) and prune orphaned ticket outputs."""
    now = now or datetime.now(timezone.utc)
    lessons = []
    for row in await quality_repo.downvote_reason_counts(now - timedelta(days=LESSON_WINDOW_DAYS)):
        votes, total = int(row["votes"]), int(row["feature_total"])
        share = votes / total if total else 0.0
        if votes < LESSON_MIN_VOTES or share < LESSON_MIN_SHARE:
            continue
        if not REASON_GUIDANCE.get(str(row["reason"])):
            continue
        lessons.append({"feature": row["feature"], "reason": row["reason"], "votes": votes,
                        "share": round(share, 4), "active": True})
    await quality_repo.replace_lessons(lessons)
    await quality_repo.prune_orphan_ticket_outputs()
    return len(lessons)
