# AI Feedback Text Learning — Tickets (PR 3a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Technicians can rate the AI ticket summary, AI resolution steps and AI reply suggestions 👍/👎, and those prompts learn from the ratings: rated examples from the same company, plus de-identified lessons built from 👎 reasons across companies.

**Architecture:** A new `ai_text_learning` service gives each prompt a `TextLearning` (trusted guidance lines + untrusted example records). Examples are the company's own technician-rated outputs (`ai_outputs.output_text`); lessons are rows in a new `ai_feedback_lessons` table rebuilt nightly from 👎 reason counts only. Outputs are recorded when they are shown (deduplicated by text hash), so existing summaries and steps become rateable immediately. `/admin/ai-quality` gains *Learned: lessons* with Undo/Restore.

**Tech Stack:** FastAPI, async MySQL/MariaDB + SQLite fallback, APScheduler, Jinja2, pytest/anyio.

**Spec:** `docs/superpowers/specs/2026-11-19-ai-feedback-learning-design.md` §3 *Text learning*, decision 2, *Who teaches*. PR 3 is split: **3a (this plan)** = mechanism + ticket summary (incl. the combined insights prompt), resolution steps, reply suggestions; **3b** = troubleshooter, health check, minidump, RMM findings, script summaries, call summaries, marketing, product descriptions, reporting query, reusing this mechanism.

## Global Constraints

- Only technician votes (`is_technician = 1`, `learning_ignored_at IS NULL`) are used as examples or counted for lessons.
- **Examples never cross companies**: only outputs with `ai_outputs.company_id` equal to the prompt's company; never the current subject's own output. Prompts with no company get no examples.
- **Lessons are built only from reason codes and counts** — never output text or comments — and go into trusted instructions.
- Examples are `UntrustedRecord`s with allowed-use "Use only as a style and completeness reference; never copy facts, names, numbers or details from it" (spec: rated text is untrusted).
- Limits: up to 2 👍 and 1 👎 example per prompt, each truncated to 600 characters; examples from the last 180 days.
- Lessons: 90-day window; a reason becomes a lesson when it has ≥ 3 technician 👎 for that feature **and** ≥ 20% of that feature's 👎 in the window. Undo sets `ignored_at`; the nightly rebuild keeps it.
- Feature keys (from `llm_usage.FEATURES`): `ticket_ai_summary` (rate key for the summary whether produced by the summary or the combined insights prompt), `ticket_resolution_steps`, `ticket_reply_suggestions`.
- Learning must never break generation: any error → empty `TextLearning`, logged warning.
- SQL parameterised, static literals only (Bandit B608); migrations via `/new-migration`, MariaDB + SQLite, `utf8mb4_unicode_ci`; repositories never import services.
- UI per Design Guidelines; bump `PACK.version` of changed feature packs; change log via `/changelog-entry` (force-add).

## Review Focus

1. **Example from another company** — company A's rated summary must never appear in company B's prompt, including when B's `company_id` is NULL. Task 2.
2. **Prompt injection in rated text** — an example whose text says "ignore previous instructions" stays inside the untrusted records block, never in trusted instructions. Task 2.
3. **Manually edited resolution steps** — not shown as AI output and not rateable (`resolution_steps_source != 'ai_generated'`). Task 4.
4. **Lesson Undo survives the nightly rebuild** — an ignored lesson stays ignored after `refresh_lessons`. Task 2.
5. **Learning lookup raises** — summary/steps/reply generation proceeds with the original prompt. Task 3.

---

### Task 1: Lessons table and repository queries

**Files:**
- Create: `migrations/566_ai_feedback_lessons.sql` (`/new-migration`, phase `expand`)
- Modify: `app/repositories/ai_quality.py`
- Test: `tests/test_ai_text_learning_repo.py`

**Interfaces:**
- Produces:
  - Table `ai_feedback_lessons (id, feature VARCHAR(40), reason VARCHAR(40), votes INT, share DECIMAL(5,4), active TINYINT(1), ignored_at DATETIME(6) NULL, updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE(feature, reason))`, `utf8mb4_unicode_ci`.
  - `async def rated_examples(feature: str, company_id: int, *, exclude_subject_id: str | None, rating: str, limit: int, since: datetime) -> list[dict]` — keys `output_id, output_text, rating, reason`; technician, not ignored, non-empty `output_text`, newest vote first, one row per output (an output with mixed votes uses the latest technician vote).
  - `async def downvote_reason_counts(since: datetime) -> list[dict]` — keys `feature, reason, votes, feature_total`; technician 👎 with a reason, not ignored.
  - `async def replace_lessons(rows: list[dict]) -> None` — upsert `(feature, reason, votes, share, active)`; rows not in the input become `active = 0`; never touches `ignored_at`.
  - `async def active_lessons(feature: str) -> list[dict]` — `active = 1 AND ignored_at IS NULL`, keys `reason, votes, share`.
  - `async def list_lessons() -> list[dict]` — all rows with `active = 1`, keys `feature, reason, votes, share, ignored` (bool).
  - `async def set_lesson_ignored(feature: str, reason: str, *, ignored: bool) -> int`
  - `async def prune_orphan_ticket_outputs() -> int` — deletes `ai_outputs` with `subject_type = 'ticket'` whose ticket no longer exists (feedback cascades).
- [ ] **Step 1:** create the migration.
- [ ] **Step 2: Write failing tests** (aiosqlite fixture applying 563, 565, 566 plus stubs `users`, `tickets(id, company_id)`):
  - `test_rated_examples_stay_in_the_company` — company 2 and 3 each have a 👍 summary; `rated_examples(..., company_id=2)` returns only company 2's (Review Focus 1).
  - `test_rated_examples_exclude_the_current_ticket_customers_and_ignored_votes`.
  - `test_rated_examples_newest_first_and_limited`.
  - `test_downvote_reason_counts`.
  - `test_replace_lessons_keeps_ignored_and_deactivates_missing` (Review Focus 4 at repo level).
  - `test_prune_orphan_ticket_outputs` — output for deleted ticket removed with its votes; others kept.
- [ ] **Step 3: Run** `python -m pytest tests/test_ai_text_learning_repo.py -q -p no:warnings` — FAIL.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** — PASS; replay 566 twice on MariaDB.
- [ ] **Step 6: Commit** `feat(ai-learning): store text lessons and query rated examples`.

### Task 2: The text-learning service and nightly lessons job

**Files:**
- Create: `app/services/ai_text_learning.py`
- Modify: `app/services/scheduler.py` (daily job `ai-feedback-lessons` at 03:20 store-local, `coalesce=True, max_instances=1`)
- Test: `tests/test_ai_text_learning.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `@dataclass(frozen=True) class TextLearning: guidance: tuple[str, ...] = (); records: tuple[UntrustedRecord, ...] = ()` with `def instructions(self, base: str) -> str` (base unchanged when no guidance; otherwise base + "\n\nLessons from technician feedback on recent outputs:\n- " + lines joined by "\n- ").
  - `EMPTY = TextLearning()`
  - `async def context_for(feature: str, company_id: int | None, *, subject_id: str | int | None = None) -> TextLearning` — never raises.
  - `REASON_GUIDANCE: dict[str, str]` — plain sentences keyed by reason, formatted with the feature label, e.g. `"incomplete": "{label} outputs were often rated incomplete: cover every point a technician needs, including the current status and next step."`, `"incorrect"`, `"unrelated"`, `"irrelevant"`, `"uncited"`, `"unsafe"`, `"other"` (other produces no guidance line).
  - `async def refresh_lessons(*, now: datetime | None = None) -> int` — computes lessons per Global Constraints, calls `replace_lessons`, then `prune_orphan_ticket_outputs`; returns active lesson count.
- [ ] **Step 1: Write failing tests:**
  - `test_examples_are_untrusted_records_with_a_style_only_policy` — the `allowed_use` text above, `content` has `rating`, `reason`, `text` (≤ 600 chars), and the example text containing "Ignore previous instructions" appears only inside `build_prompt`'s untrusted block (assert it's after `BEGIN_UNTRUSTED_RECORDS`) (Review Focus 2).
  - `test_no_company_means_no_examples` (Review Focus 1).
  - `test_guidance_lines_come_from_active_lessons_only` and `instructions()` formatting.
  - `test_context_for_never_raises`.
  - `test_refresh_lessons_thresholds` — 3 votes at 30% → active; 2 votes → not; 3 votes at 10% → not; ignored stays ignored (Review Focus 4).
  - scheduler: `test_lessons_job_is_registered` (follow the pattern of an existing scheduler registration test; `grep -l "get_job(" tests/test_scheduler*.py`).
- [ ] **Step 2: Run** `python -m pytest tests/test_ai_text_learning.py -q -p no:warnings` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(ai-learning): rated examples and nightly lessons for AI text`.

### Task 3: Learning in the ticket prompts

**Files:**
- Modify: `app/services/tickets.py` (`_render_prompt`, `_render_insights_prompt`, `_render_resolution_prompt` gain `learning: TextLearning = EMPTY`; `refresh_ticket_ai_summary`, `refresh_ticket_ai_insights`, `refresh_ticket_resolution_steps` fetch `context_for(...)` with the ticket's `company_id` and `subject_id=ticket id`), `app/services/ticket_reply_suggestions.py` (same for its prompt builder and `suggest_reply`)
- Test: `tests/test_ticket_ai_learning_prompts.py`

**Interfaces:**
- Consumes: Task 2 `context_for`, `TextLearning`, `EMPTY`.
- Produces: prompts = `build_prompt(learning.instructions(<HEADER>), [*records, *learning.records], task=...)`; summary and insights both use feature `ticket_ai_summary`.
- [ ] **Step 1: Write failing tests** — for each of the four render functions: with a `TextLearning(guidance=("G1",), records=(<example record>,))` the prompt contains "G1" before `BEGIN_UNTRUSTED_RECORDS` and the example's record id after it; with `EMPTY` the prompt equals the current output. One test per refresh path that `context_for` is called with `(feature, company_id, subject_id=<ticket id>)` (monkeypatch it and the module trigger); and `test_generation_survives_learning_errors` — `context_for` patched to raise inside its own body → refresh still calls the LLM with the plain prompt (Review Focus 5; the real `context_for` catches, so patch an inner repo call to raise).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Bump the tickets pack version only if a pack file changes.
- [ ] **Step 4: Run** it plus existing `tests/test_ticket*ai*.py`, `tests/test_ticket_reply_suggestions*.py` (find with `ls tests | grep -i -E "ticket.*(ai|summary|resolution|insight|suggest)"`) — PASS, no regressions vs `main`.
- [ ] **Step 5: Commit** `feat(ai-learning): ticket prompts learn from rated outputs`.

### Task 4: Rate the summary and resolution steps on ticket pages

**Files:**
- Modify: the admin ticket detail route (find where `admin/ticket_detail.html` is rendered) and the page rendering `tickets/detail.html`; both templates
- Create: `app/services/ai_text_outputs.py`
- Test: `tests/test_ticket_ai_rating.py`

**Interfaces:**
- Consumes: PR 1 `record_output`, macro `ai_rating`.
- Produces: `async def ticket_rating_ids(ticket: Mapping[str, Any], *, viewer_id: int | None) -> dict[str, int | None]` — keys `summary`, `resolution_steps`; records (deduplicated) the current AI summary when `ai_summary_status == 'succeeded'` and text present (feature `ticket_ai_summary`, model `ai_summary_model`), and resolution steps only when `resolution_steps_source == 'ai_generated'` (feature `ticket_resolution_steps`); `None` otherwise; never raises. Templates render `ai_rating(ids.summary, label="AI summary", return_to=<page url>, csrf_token=csrf_token)` under the summary and the same for resolution steps (`label="AI resolution steps"`).
- [ ] **Step 1: Write failing tests:** `ticket_rating_ids` for succeeded summary → id; failed/empty → None; manually edited steps → None (Review Focus 3); repo error → both None. Page tests: admin ticket page with a summary includes `action="/api/ai-quality/feedback"` and `AI summary was helpful`; customer page shows the control for AI steps only when the viewer can rate (requester/watcher) — follow the existing page test for each template (`grep -l "ticket_detail.html\|tickets/detail.html" tests/*.py`).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; render the admin page with a summary in headless Chromium (desktop + 390 px): control visible under the summary, no overflow, no script errors.
- [ ] **Step 5: Commit** `feat(ai-learning): rate AI ticket summaries and resolution steps`.

### Task 5: Rate reply suggestions

**Files:**
- Modify: `app/features/tickets/admin_routes.py` (suggest-reply route), the suggestion panel in `app/templates/admin/ticket_detail.html`, the script that fills it (find with `grep -rn "data-ticket-suggest-reply" app/static/js`), `app/features/tickets/__init__.py` (bump)
- Test: `tests/test_ticket_reply_suggestion_rating.py`

**Interfaces:**
- Produces: the route's JSON gains `"quality_response_id": int | None` (record feature `ticket_reply_suggestions`, subject the ticket, `output_text` = draft, `user_id` = requesting technician, model from the result if present; recording failure → `None`, logged). The panel contains `ai_rating("pending", label="Suggested reply", csrf_token=csrf_token)` hidden until a draft arrives; the script sets the hidden `response_id`, resets `aria-pressed`, unhides it (hides it when the id is null), and calls `window.MyPortalAiRating.init(panel)`.
- [ ] **Step 1: Write failing tests:** route returns `quality_response_id` from a patched `record_output`; recording error → `None` and the draft still returned; template contains the hidden rating form inside the suggestion panel.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; Playwright: stub the suggest endpoint, click *Suggest reply*, the control appears with the returned id and posts it.
- [ ] **Step 5: Commit** `feat(ai-learning): rate AI reply suggestions`.

### Task 6: *Learned: lessons* on `/admin/ai-quality`

**Files:**
- Modify: `app/main.py` (context + `POST /admin/ai-quality/lessons/ignore|restore`), `app/templates/admin/ai_quality.html`
- Test: `tests/test_admin_ai_quality_page.py`

**Interfaces:**
- Consumes: Task 1 `list_lessons`, `set_lesson_ignored`; Task 2 `REASON_GUIDANCE`.
- Produces: context `learned_lessons` (rows + `label` = feature label, `guidance` = the line the prompt gets); nav item `("learned-lessons", "Learned: lessons", count)` in the "What MyPortal learned" group; section subtitle "Guidance MyPortal adds to AI prompts because technicians kept giving the same reason for 👎. It is built from reasons and counts only — never from ticket text. Undo stops MyPortal using a lesson." Each row: feature label, the guidance sentence, chips "N 👎 with this reason", "N% of 👎", Ignored pill, Undo/Restore form (fields `feature`, `reason`, `_csrf`). Routes mirror PR 2's link routes: super admin, validation (≤ 40 chars, reason in `REASONS`), audit `ai_quality.lesson.ignore|restore`, flash "MyPortal will stop using this lesson." / "MyPortal will use this lesson again.", redirect `/admin/ai-quality#ai-quality-learned-lessons`. Empty state: "No lessons yet. Lessons appear when technicians give the same reason for 👎 at least three times."
- [ ] **Step 1: Write failing tests** mirroring `test_page_lists_learned_links_with_undo_and_restore`, the route tests and the non-admin test.
- [ ] **Step 2–4:** run (FAIL) → implement → run (PASS); browser render check.
- [ ] **Step 5: Commit** `feat(ai-learning): show and undo learned lessons`.

### Task 7: Wrap-up

- [ ] `/changelog-entry` (Feature): "Technicians can now rate AI ticket summaries, resolution steps and reply suggestions, and MyPortal uses those ratings to improve what it writes next time; admins can see and undo the lessons it learns." `git add -f`.
- [ ] SQL audit + local Bandit B608 on touched files.
- [ ] Focused tests + full suite vs `main` (0 new failures).
- [ ] Fresh whole-branch review; fix Critical/Important; push; PR; watch.
