# AI Feedback Link Learning (PR 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A technician's 👎 on a linked item (e.g. a KB article in a ticket's Related panel) lowers that item's score for *similar* tickets and searches, a 👍 raises it, and admins can see and undo what was learned.

**Architecture:** Lessons are the technician votes PR 1 already stores for feature `rag_relationships` (`ai_outputs` subject = ticket, target = linked RAG document). Each lesson carries the subject ticket's mean RAG embedding (filled lazily). A pure function turns lessons + the current subject/query embedding into a bounded score adjustment; it is applied in `rag_retrieval.retrieve_candidates` (Related panel, AI search evidence, ticket deflection), in the relationship evaluator's match decision, and in KB keyword search. `/admin/ai-quality` gains *Learned: links* with Undo/Restore (soft-ignore of votes).

**Tech Stack:** FastAPI, async MySQL/MariaDB with SQLite fallback, Jinja2, pytest/anyio.

**Spec:** `docs/superpowers/specs/2026-11-19-ai-feedback-learning-design.md` (§3 *Link learning*, decisions 1–2, *Who teaches*). PR 1 plan for context: `docs/superpowers/plans/2026-11-19-ai-feedback-foundation.md`.

## Global Constraints

- Only votes with `ai_output_feedback.is_technician = 1` and `learning_ignored_at IS NULL` teach; customer votes never change scores.
- Votes cross companies only as the adjustment number — never text, titles or comments in any prompt or response to another company.
- Adjustment constants (module-level in `app/services/ai_learning.py`, not settings): `SIMILARITY_THRESHOLD = 0.8`, `STEP = 0.05`, `MAX_PENALTY = 0.3`, `MAX_BOOST = 0.15`, `HALF_LIFE_DAYS = 180`, `LESSON_CACHE_SECONDS = 60`.
- Learning must never break a request: any error loading lessons → adjustment `0.0` and a logged warning.
- A vote still hides its item on its own ticket immediately (unchanged PR 1 behaviour via `rag_relationship_feedback`).
- SQL: parameterised only, no f-strings; IN-lists built from static `?` placeholders. Migrations via `/new-migration`, idempotent, MariaDB + SQLite; new string columns inherit `utf8mb4_unicode_ci` from `ai_outputs`.
- UI per `docs/wiki/developer/Design Guidelines.md`; Undo/Restore are `<form method="post">` with `_csrf`, super admin only, audited.
- Change log via `/changelog-entry`; bump `PACK.version` of any feature pack whose files change.

## Review Focus

1. **Embedding model changed** (stored lesson vector length ≠ query vector length, or different `embedding_model`) → that lesson is ignored, no crash. Task 2.
2. **Voted ticket has no RAG document or chunks** → its lessons carry no embedding and are ignored. Task 2.
3. **Undo then a new 👎 on the same item** → old votes stay ignored, the new vote counts. Task 1.
4. **Lesson loading raises** (DB down) → retrieval, evaluator and KB search return their normal results with no adjustment. Tasks 3–5.
5. **Ticket unrelated to the voted ones** (cosine < 0.8) → no adjustment at all, even with many 👎. Task 2.

---

### Task 1: Schema and repository for lessons

**Files:**
- Create: `migrations/565_ai_link_learning.sql` (via `/new-migration`, phase `expand`)
- Modify: `app/repositories/ai_quality.py`
- Test: `tests/test_ai_link_learning_repo.py`

**Interfaces:**
- Produces:
  - Columns: `ai_outputs.subject_embedding_json LONGTEXT NULL`, `ai_outputs.subject_embedding_model VARCHAR(128) NULL`, `ai_output_feedback.learning_ignored_at DATETIME(6) NULL`.
  - `LINK_FEATURE = "rag_relationships"` in `ai_quality`.
  - `async def list_link_votes() -> list[dict]` — teaching votes only (technician, not ignored, `feature = LINK_FEATURE`, `target_type <> ''`), keys: `output_id, subject_type, subject_id, target_type, target_id, subject_embedding_json, subject_embedding_model, rating, voted_at` (`voted_at` = `f.updated_at`).
  - `async def store_subject_embedding(output_id: int, embedding: list[float], model: str) -> None`.
  - `async def set_link_learning_ignored(target_type: str, target_id: str, *, ignored: bool) -> int` — sets/clears `learning_ignored_at` on technician votes for `LINK_FEATURE` outputs with that target; returns rows changed. Ignoring only touches votes not already ignored; restoring clears all.
  - `async def learned_links() -> list[dict]` — one row per target with any technician vote: `target_type, target_id, title` (from `rag_documents` by `source_type`/`source_id`, may be None), `unhelpful, helpful, tickets` (distinct subjects), `ignored` (bool: every technician vote on it is ignored), `last_voted_at` (UTC ISO). Ordered by `unhelpful DESC`.

- [ ] **Step 1: Create the migration** with `/new-migration` (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`, as 561 does).
- [ ] **Step 2: Write failing tests** with an aiosqlite fixture applying migrations `563` and `565`, plus stub `users` and a `rag_documents (id, source_type, source_id, title)` stub:
  - `test_list_link_votes_only_returns_teaching_votes`: technician 👎 listed; customer vote not; vote on another feature not; vote with `learning_ignored_at` set not.
  - `test_undo_and_restore`: two technician 👎 on target `knowledge_base:vpn` → `set_link_learning_ignored(..., ignored=True) == 2`, `list_link_votes()` empty; then a new technician 👎 from a third user is listed (Review Focus 3); `ignored=False` restores all three.
  - `test_learned_links_summary`: rows for `vpn` (2 👎 on 2 tickets, title "VPN") and `printer` (1 👍); `ignored` flips to True after undo; `last_voted_at` ends with `+00:00`.
  - `test_store_subject_embedding_round_trips`.
- [ ] **Step 3: Run** `python -m pytest tests/test_ai_link_learning_repo.py -q -p no:warnings` — FAIL (missing functions/columns).
- [ ] **Step 4: Implement.** `learned_links` joins `rag_documents d ON d.source_type = o.target_type AND d.source_id = o.target_id` (both `utf8mb4_unicode_ci`).
- [ ] **Step 5: Run** the same — PASS. Also `python -m pytest tests/test_ai_quality_outputs.py -q -p no:warnings` — PASS.
- [ ] **Step 6: Replay 563–565 on MariaDB** if available (`mariadbd` was installed in the PR 1 session; reinstall with `apt-get install -y mariadb-server` if needed): migrations succeed and re-run cleanly.
- [ ] **Step 7: Commit** `feat(ai-learning): store link lessons and allow undo`.

### Task 2: The learning service

**Files:**
- Create: `app/services/ai_learning.py`
- Modify: `app/repositories/rag_relationships.py` (add `document_mean_embedding`), `app/repositories/ai_quality.py::save_feedback` (call `ai_learning.invalidate_cache()` after a write — lazily imported)
- Test: `tests/test_ai_learning.py`

**Interfaces:**
- Consumes: Task 1 `list_link_votes`, `store_subject_embedding`.
- Produces:
  - `@dataclass(frozen=True) class LinkLesson: target_type: str; target_id: str; vote: int  # +1 / -1; embedding: tuple[float, ...] | None; voted_at: datetime`
  - `def link_adjustment(lessons: Sequence[LinkLesson], subject_embedding: Sequence[float] | None, *, now: datetime) -> float` — pure.
  - `async def load_link_lessons() -> dict[tuple[str, str], list[LinkLesson]]` — cached `LESSON_CACHE_SECONDS`; fills missing/stale embeddings lazily (subject `ticket` → `rag_documents` row `source_type='tickets', source_id=subject_id` → `document_mean_embedding`; store via `store_subject_embedding` with `rag_index.embedding_model()`); lessons whose stored model ≠ current `rag_index.embedding_model()` get recomputed; lessons with no embedding keep `embedding=None`.
  - `async def adjustment_for(target_type: str, target_id: str, subject_embedding: Sequence[float] | None) -> float` — never raises (Global Constraints).
  - `async def lessons_or_empty() -> dict[tuple[str, str], list[LinkLesson]]` — `load_link_lessons()` that returns `{}` and logs on any error (used by hot paths that look up many targets).
  - `def invalidate_cache() -> None`
  - `async def document_mean_embedding(document_id: int) -> list[float] | None` in `rag_relationships` repo — mean of active chunk vectors (current embedding model only), L2-normalised; None when no chunks.

`link_adjustment` algorithm (the tests pin it):

```text
total = 0
for lesson with embedding of same length as subject_embedding:
    cos = dot(lesson.embedding, subject_embedding)      # vectors are L2-normalised
    if cos < SIMILARITY_THRESHOLD: continue
    age_days = max(0, (now - lesson.voted_at).days)
    total += lesson.vote * STEP * cos * 0.5 ** (age_days / HALF_LIFE_DAYS)
return round(clamp(total, -MAX_PENALTY, MAX_BOOST), 4)
```

- [ ] **Step 1: Write failing tests** (pure function first, unit vectors `a=(1,0)`, `b=(0.6,0.8)` → cos 0.6, `same=(1,0)`):
  - one 👎 identical subject, today → `-0.05`; four 👎 → `-0.2`; ten 👎 → `-0.3` (cap); six 👍 → `0.15` (cap).
  - 👎 on subject with cos 0.6 → `0.0` (Review Focus 5).
  - one 👎 voted 180 days ago → `-0.025`.
  - lesson embedding length 3 vs subject length 2 → ignored → `0.0`; `embedding=None` → ignored; `subject_embedding=None` → `0.0` (Review Focus 1, 2).
  - one 👎 + one 👍 identical → `0.0`.
  - `load_link_lessons` with monkeypatched repo: groups by `(target_type, target_id)`, fills a missing embedding once via `document_mean_embedding` and calls `store_subject_embedding`; a ticket with no RAG doc → `embedding None`, no store call; second call within cache window does not hit the repo; `invalidate_cache()` forces reload.
  - `adjustment_for` returns `0.0` when `list_link_votes` raises.
  - `document_mean_embedding` (aiosqlite, `rag_chunks` stub): mean of `[1,0]` and `[0,1]` → `[0.7071, 0.7071]` (rounded), no chunks → None.
- [ ] **Step 2: Run** `python -m pytest tests/test_ai_learning.py -q -p no:warnings` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; plus `tests/test_ai_quality_outputs.py` still PASS.
- [ ] **Step 5: Commit** `feat(ai-learning): turn technician link votes into score adjustments`.

### Task 3: Learning in RAG retrieval (Related panel, AI search evidence, deflection)

**Files:**
- Modify: `app/services/rag_retrieval.py::retrieve_candidates`
- Test: `tests/test_rag_retrieval.py`

**Interfaces:**
- Consumes: Task 2 `lessons_or_empty`, `link_adjustment`.
- Produces: each candidate dict gains `"learning_adjustment": float` (0.0 when none). The adjustment is added to `final` **before** the threshold check (so enough 👎 drop the item: "the link is not made") and after the source weight.

- [ ] **Step 1: Write failing tests** following `test_retrieve_candidates_demotes_other_company_evidence_in_stored_score` (monkeypatch `embed_text` → `[1.0, 0.0]`, `_load_authorised_rows`, policy):
  - `test_technician_downvotes_on_similar_tickets_drop_a_candidate`: a row for `knowledge_base:vpn` scoring just above threshold; `lessons_or_empty` → 4 👎 lessons with embedding `(1.0, 0.0)` → candidate absent.
  - `test_downvotes_on_unrelated_tickets_change_nothing`: same lessons with embedding `(0.0, 1.0)` → candidate present, `learning_adjustment == 0.0`, score unchanged.
  - `test_learning_failure_does_not_break_retrieval`: `lessons_or_empty` patched to the real function with `list_link_votes` raising → normal results (Review Focus 4).
- [ ] **Step 2: Run** `python -m pytest tests/test_rag_retrieval.py -q -p no:warnings` — new tests FAIL.
- [ ] **Step 3: Implement**: call `lessons_or_empty()` once per `retrieve_candidates`; skip all work when it is empty.
- [ ] **Step 4: Run** `tests/test_rag_retrieval.py tests/test_ticket_deflection.py` — PASS (no regressions in either).
- [ ] **Step 5: Commit** `feat(ai-learning): apply link lessons in RAG retrieval`.

### Task 4: Learning in the relationship evaluator

**Files:**
- Modify: `app/services/rag_relationships.py` (`evaluate_next_batch`, new pure helper)
- Test: `tests/test_rag_relationships.py`

**Interfaces:**
- Consumes: Task 2 `adjustment_for`, `document_mean_embedding`.
- Produces: `def apply_learning(parsed: dict, adjustment: float, *, min_score: float) -> dict` — returns a new dict: `relevance_score = clamp01(score + adjustment)` (rounded 4); `match_status`/`relationship_type` recomputed with the same rule as `parse_relationship_response` (positive type and score ≥ `min_score`, else `NO_MATCH` + `NOT_RELEVANT`; keep the original type in the reason); when `adjustment != 0` the reason is prefixed `"Technician feedback on similar tickets adjusted the score by -0.10. "` (sign and two decimals). `evaluate_next_batch` applies it after parsing when exactly one of source/target is a `tickets` document: subject = that ticket document's mean embedding, target = the other document's `(source_type, source_id)`.

- [ ] **Step 1: Write failing tests** for `apply_learning`: MATCH 0.60 + (-0.10) with min 0.55 → NO_MATCH, NOT_RELEVANT, score 0.5, reason starts with the prefix and mentions the original type; NO_MATCH 0.50 + 0.10 (positive original type) → MATCH 0.6; adjustment 0 → identical dict. Plus one `evaluate_next_batch` test reusing that file's existing evaluator mocks where the stored relationship is NO_MATCH because `adjustment_for` returns -0.2.
- [ ] **Step 2: Run** `python -m pytest tests/test_rag_relationships.py -q -p no:warnings` — new tests FAIL.
- [ ] **Step 3: Implement.** Note `parse_relationship_response` sets type NOT_RELEVANT on NO_MATCH; to allow a 👍 to lift a NO_MATCH, keep the model's raw type in `parsed["model_relationship_type"]` (add it in `parse_relationship_response`; `store_relationship` ignores unknown keys — verify) and recompute from it.
- [ ] **Step 4: Run** the same file — PASS.
- [ ] **Step 5: Commit** `feat(ai-learning): apply link lessons to relationship decisions`.

### Task 5: Learning in KB keyword search

**Files:**
- Modify: `app/services/knowledge_base.py::search_articles`
- Test: `tests/test_knowledge_base_search_learning.py`

**Interfaces:**
- Consumes: Task 2 `lessons_or_empty`, `link_adjustment`; `rag_index.embed_text`.
- Produces: when any scored article has lessons (key `("knowledge_base", <article slug or id as stored in rag_documents.source_id>)` — check `rag_index` KB source registration for which field is `source_id`), embed the query once and multiply that article's keyword score by `(1 + adjustment / MAX_PENALTY * 0.9)` — i.e. the full penalty removes 90% of the score — then re-apply `min_score_threshold`. No lessons → no embedding call.

- [ ] **Step 1: Write failing tests**: two articles both scoring above threshold; four technician 👎 lessons on article A with embedding equal to the patched query embedding → A drops below threshold and is absent; lessons on unrelated embeddings → order and results unchanged; no lessons → `embed_text` not awaited; `lessons_or_empty` returning `{}` after an error → results unchanged.
- [ ] **Step 2: Run** `python -m pytest tests/test_knowledge_base_search_learning.py -q -p no:warnings` — FAIL.
- [ ] **Step 3: Implement.** Bump the knowledge base feature pack version if `search_articles` lives in a pack (it lives in `app/services`, so likely none).
- [ ] **Step 4: Run** it plus existing KB search tests (`grep -l search_articles tests/*.py`) — PASS.
- [ ] **Step 5: Commit** `feat(ai-learning): apply link lessons to KB search`.

### Task 6: *Learned: links* on `/admin/ai-quality` with Undo

**Files:**
- Modify: `app/main.py` (page context + two POST routes), `app/templates/admin/ai_quality.html`
- Test: `tests/test_admin_ai_quality_page.py`

**Interfaces:**
- Consumes: Task 1 `learned_links`, `set_link_learning_ignored`; Task 2 `invalidate_cache`.
- Produces:
  - Page context `learned_links` (Task 1 rows plus `label` = title or `"<target_type> <target_id>"`).
  - `POST /admin/ai-quality/links/ignore` and `POST /admin/ai-quality/links/restore` — form fields `target_type`, `target_id`, `_csrf`; super admin only (`_require_super_admin_page`); call `set_link_learning_ignored`, `ai_learning.invalidate_cache()`, `audit_service.record(action="ai_quality.link_learning.ignore|restore", entity_type="ai_link_target", metadata={"target_type","target_id","votes"})`; respond `flash_redirect("/admin/ai-quality#ai-quality-learned-links", "<message>", "success")`. Messages: "MyPortal will stop using feedback on VPN setup." / "MyPortal will use feedback on VPN setup again."
  - New nav item `("learned-links", "Learned: links", count)` in a group "What MyPortal learned"; section copy: title *Learned: links*, subtitle "Items technicians marked as unrelated or useful. MyPortal lowers or raises them for similar tickets and searches. Undo stops MyPortal using those votes; new votes count again." Each row: item label, chips "N not helpful", "N helpful", "on N tickets", status pill `status--neutral` "Ignored" when ignored; *Undo* (or *Restore*) button. Empty state: "Nothing learned yet. When technicians mark a Related item 👍 or 👎, it appears here."
- [ ] **Step 1: Write failing tests**: page with two learned rows shows labels, chips, an Undo form posting to `/admin/ai-quality/links/ignore` with hidden `target_type`/`target_id`; ignored row shows *Ignored* and a Restore form; empty state text; route tests (call the route function with a fake request, monkeypatched `_require_super_admin_page`, repo, audit, `invalidate_cache`) → 303 to `/admin/ai-quality#ai-quality-learned-links`, `set_link_learning_ignored` called with `ignored=True` / `False`, audit action names as above; non-admin (redirect returned by `_require_super_admin_page`) → that redirect, repo not called.
- [ ] **Step 2: Run** `python -m pytest tests/test_admin_ai_quality_page.py -q -p no:warnings` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; render the page with sample data in headless Chromium at 1400px and 390px (as in PR 1): no horizontal overflow, no script errors.
- [ ] **Step 5: Commit** `feat(ai-learning): show and undo learned links`.

### Task 7: Wrap-up

- [ ] **Step 1:** `/changelog-entry` — `Feature`, e.g. "When technicians mark a Related item as unrelated, MyPortal now shows it less for similar tickets and searches, and admins can see and undo what it learned on the AI Quality page." Commit with `git add -f`.
- [ ] **Step 2:** Audit SQL in touched files (Global Constraints).
- [ ] **Step 3: Run** `python -m pytest tests/test_ai_link_learning_repo.py tests/test_ai_learning.py tests/test_rag_retrieval.py tests/test_ticket_deflection.py tests/test_rag_relationships.py tests/test_knowledge_base_search_learning.py tests/test_admin_ai_quality_page.py tests/test_ai_quality_outputs.py tests/test_rag_relationship_feedback.py -q -p no:warnings` — all PASS; then the full suite compared with `main` (0 new failures).
- [ ] **Step 4:** Final whole-branch review, fix Critical/Important, push, open PR, subscribe.
