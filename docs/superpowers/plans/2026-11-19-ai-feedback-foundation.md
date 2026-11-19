# AI Feedback Foundation (PR 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One shared way to record any AI output, rate it 👍/👎 from any page, and see ratings for every AI feature on `/admin/ai-quality` — the base the learning PRs build on.

**Architecture:** Generalise the existing `ai_quality_responses`/`ai_quality_feedback` tables with subject/target/output columns and a voter-is-technician snapshot. A new `/api/ai-quality/feedback` endpoint (JSON and no-JS form) checks access per subject type. A Jinja macro + one script render the control anywhere. Ticket Related-item votes are dual-written into the shared tables and backfilled; their per-ticket hide behaviour is unchanged. No learning happens in this PR.

**Tech Stack:** FastAPI, async MySQL with SQLite fallback (`app.core.database.db`), Jinja2, vanilla JS, pytest + pytest-asyncio/anyio.

**Spec:** `docs/superpowers/specs/2026-11-19-ai-feedback-learning-design.md`

## Global Constraints

- SQL: parameterised only; no f-strings/`.format`/`%` in SQL; dynamic parts only static literals (Bandit B608).
- Migrations: created with `/new-migration` (next free number on branch and `origin/main`, phase header), idempotent, valid on MySQL and via `db._adapt_sql_for_sqlite`.
- Store times in UTC; render with `<span data-utc="…">`.
- UI follows `docs/wiki/developer/Design Guidelines.md`: CSS tokens only, no hard-coded colours, `<form method="post">` + `_csrf` works without JS, `role="status" aria-live="polite"` for status, `aria-pressed` on toggles.
- `feature` values are keys of `app/services/llm_usage.py::FEATURES` (`FEATURES_BY_KEY`); labels via `llm_usage.feature_label(key)`.
- Only technicians' votes will teach (spec *Who teaches*): every vote snapshots `is_technician`.
- Change-log entry via `/changelog-entry`; one per PR.
- Existing `/api/agent/feedback`, `/api/agent/quality/summary`, `/api/agent/quality/export.csv`, `/api/agent/quality/related-labels.json` keep working.

## Review Focus

1. **Rating someone else's output you can see** — a technician rates a ticket summary another user's action produced → allowed; a customer who is not requester/watcher of that ticket → 404 (not 403, to avoid confirming existence). Task 2.
2. **Clearing a vote** — `rating: null` deletes the caller's vote and the counts drop; repeated 👍 is idempotent (one row). Task 1.
3. **Backfill run twice** — re-running the Related-item backfill migration creates no duplicate responses or feedback. Task 4.
4. **No-JS form post with a hostile `return_to`** — `https://evil.example`, `//evil.example`, `/\evil` → redirect falls back to `/`. Task 3.
5. **Feature key not in `FEATURES`** — `record_output(feature="nope")` raises `ValueError`; admin page shows unknown legacy keys with their raw key rather than crashing. Tasks 1 and 6.

---

### Task 1: Schema and repository for shared outputs and votes

**Files:**
- Create: `migrations/<NNN>_ai_quality_outputs.sql` (via `/new-migration`, phase `expand`)
- Modify: `app/repositories/ai_quality.py`
- Test: `tests/test_ai_quality_outputs.py`

**Interfaces:**
- Produces:
  - Columns on `ai_quality_responses`: `subject_type VARCHAR(40) NULL`, `subject_id VARCHAR(64) NULL`, `target_type VARCHAR(40) NULL`, `target_id VARCHAR(64) NULL`, `output_text MEDIUMTEXT NULL`, `output_hash CHAR(64) NULL`, `prompt_version VARCHAR(40) NULL`; index `idx_ai_quality_response_subject (subject_type, subject_id, feature)` and `idx_ai_quality_response_target (target_type, target_id)`.
  - Column on `ai_quality_feedback`: `is_technician TINYINT(1) NOT NULL DEFAULT 0`.
  - `REASONS` gains `"unrelated"`.
  - `async def record_output(*, feature: str, user_id: int, company_id: int | None, subject_type: str | None = None, subject_id: str | int | None = None, target_type: str | None = None, target_id: str | int | None = None, output_text: str | None = None, model: str | None = None, prompt_version: str | None = None, latency_ms: int = 0, outcome: str = "answered") -> int` — returns the existing id when a row with the same `(feature, subject_type, subject_id, target_type, target_id, output_hash)` exists; raises `ValueError` for a feature not in `FEATURES_BY_KEY`. Fills legacy NOT NULL columns with `query_hash` = 64 `"0"`, `query_redacted` = `""`, `evidence_identifiers`/`source_types` = `"[]"`, `pipeline_version` = `"output-v1"`.
  - `async def get_response(response_id: int) -> dict | None`
  - `async def save_feedback(*, response_id: int, user_id: int, rating: str | None, reason: str | None, comment: str | None, is_technician: bool = False) -> None` — `rating=None` deletes the vote.

- [ ] **Step 1: Create the migration** with `/new-migration`. Use `ALTER TABLE … ADD COLUMN` guarded the way other recent migrations add columns idempotently (check `migrations/` for the house pattern, e.g. `grep -l "ADD COLUMN" migrations | tail`), and `CREATE INDEX` guarded likewise.

- [ ] **Step 2: Write failing tests** in `tests/test_ai_quality_outputs.py` using an in-memory aiosqlite fixture that applies migrations 387 and `<NNN>` through `Database()._split_sql_statements(Database()._adapt_sql_for_sqlite(sql))` (same pattern as `tests/test_rag_relationship_feedback.py::feedback_db`, with `users`/`companies` stub tables):

```python
async def test_record_output_dedupes_same_output(db_fixture):
    a = await repo.record_output(feature="ticket_summary", user_id=1, company_id=2,
                                 subject_type="ticket", subject_id=7, output_text="Printer offline")
    b = await repo.record_output(feature="ticket_summary", user_id=3, company_id=2,
                                 subject_type="ticket", subject_id=7, output_text="Printer offline")
    c = await repo.record_output(feature="ticket_summary", user_id=1, company_id=2,
                                 subject_type="ticket", subject_id=7, output_text="Printer back online")
    assert a == b and c != a

async def test_record_output_rejects_unknown_feature(db_fixture):
    with pytest.raises(ValueError):
        await repo.record_output(feature="nope", user_id=1, company_id=None)

async def test_feedback_upsert_and_clear(db_fixture):
    rid = await repo.record_output(feature="ticket_summary", user_id=1, company_id=2,
                                   subject_type="ticket", subject_id=7, output_text="x")
    await repo.save_feedback(response_id=rid, user_id=5, rating="up", reason=None, comment=None, is_technician=True)
    await repo.save_feedback(response_id=rid, user_id=5, rating="down", reason="unrelated", comment=" ", is_technician=True)
    rows = await fetch_all("SELECT rating, reason, comment, is_technician FROM ai_quality_feedback")
    assert [tuple(r) for r in rows] == [("down", "unrelated", None, 1)]
    await repo.save_feedback(response_id=rid, user_id=5, rating=None, reason=None, comment=None)
    assert await fetch_all("SELECT id FROM ai_quality_feedback") == []
```

Use the actual feature key for ticket summaries from `llm_usage.FEATURES` (look it up; the test must use a real key).

- [ ] **Step 3: Run** `pytest tests/test_ai_quality_outputs.py -q` — expect FAIL (`record_output` missing).

- [ ] **Step 4: Implement** the Interfaces above in `app/repositories/ai_quality.py`. `output_hash` = sha256 of `output_text or ""`. Keep `record_response` unchanged.

- [ ] **Step 5: Run** `pytest tests/test_ai_quality_outputs.py tests/test_ai_quality_evaluation.py -q` — expect PASS.

- [ ] **Step 6: Commit** `feat(ai-quality): record any AI output and technician-flagged votes`.

### Task 2: Who may rate an output

**Files:**
- Create: `app/services/ai_quality_access.py`
- Test: `tests/test_ai_quality_access.py`

**Interfaces:**
- Consumes: `ai_quality.get_response` (Task 1).
- Produces: `async def can_rate(user: Mapping[str, Any], response: Mapping[str, Any], *, is_technician: bool) -> bool`. Rules: the response's own `user_id` → True; `subject_type == "ticket"` → `is_technician` or user is the ticket's requester or a watcher (`tickets_repo.get_ticket`, `tickets_repo.is_ticket_watcher`), and for non-technicians the ticket must exist; any other or missing `subject_type` → `is_technician`. Also `async def is_technician(user: Mapping[str, Any]) -> bool` that wraps the same permission check as `app/api/routes/tickets.py::_has_helpdesk_permission` (super admin, or `helpdesk.technician` permission) — move that check into this module and make the tickets route call it, rather than duplicating it.

- [ ] **Step 1: Write failing tests** with `monkeypatch` on `tickets_repo.get_ticket`/`is_ticket_watcher`: owner→True; technician on ticket→True; requester→True; watcher→True; unrelated customer→False; non-ticket subject with customer→False; ticket missing for customer→False.
- [ ] **Step 2: Run** `pytest tests/test_ai_quality_access.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_ai_quality_access.py tests/test_ticket_tasks*.py -q` (whichever existing tests cover `_has_helpdesk_permission`; find with `grep -l _has_helpdesk_permission tests`) — PASS.
- [ ] **Step 5: Commit** `feat(ai-quality): access rules for rating AI output`.

### Task 3: Feedback endpoint (JSON + no-JS form)

**Files:**
- Create: `app/api/routes/ai_quality.py`
- Modify: `app/main.py` (include router next to `agent.router`), `app/api/routes/agent.py` (`submit_feedback` delegates), `app/schemas/agent.py` (`AgentFeedbackRequest.rating` allows null)
- Test: `tests/test_ai_quality_feedback_api.py`

**Interfaces:**
- Consumes: Task 1 `get_response`, `save_feedback`, `REASONS`; Task 2 `can_rate`, `is_technician`.
- Produces:
  - `POST /api/ai-quality/feedback`, JSON body `AiFeedbackRequest {response_id: int>0, rating: "up"|"down"|null, reason: str|None ≤40, comment: str|None ≤1000}` → `200 {"response_id", "rating", "helpful", "unhelpful"}` (counts for that response, so the control can update). Unknown reason → 422. Missing or not rateable → 404. Swagger tag `AI Quality`, summary and description set.
  - Same path accepts `application/x-www-form-urlencoded` (`response_id`, `rating`, `reason`, `comment`, `return_to`, `_csrf`) → `303` to `return_to` if it is a same-site relative path (starts with `/`, not `//` or `/\`), else `/`.
  - `async def submit_rating(user, *, response_id, rating, reason, comment) -> dict` in the route module, used by both content types and by `/api/agent/feedback` (which keeps returning 204).
  - Audit via `audit_service.record(action="ai_quality.feedback", entity_type="ai_output", entity_id=response_id, metadata={"rating", "reason", "feature"})`.

- [ ] **Step 1: Write failing tests** with `TestClient(app)` (no context manager, as in `tests/test_admin_ai_quality_page.py`), overriding `get_current_user` via `app.dependency_overrides` and monkeypatching repo/access functions: JSON up → 200 with counts; null clears; bad reason → 422; `can_rate` False → 404; form post with `return_to="/tickets/7"` → 303 `/tickets/7`; each of `https://evil.example`, `//evil.example`, `/\\evil` → 303 `/`; `/api/agent/feedback` still → 204 and calls `submit_rating`. Check how CSRF middleware treats test clients (see an existing form-post test, e.g. `grep -l "_csrf" tests | head`) and follow it.
- [ ] **Step 2: Run** `pytest tests/test_ai_quality_feedback_api.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_ai_quality_feedback_api.py tests/test_ai_quality_outputs.py -q` — PASS; open `/docs` schema in the test (`client.get("/openapi.json")`) and assert the path is present.
- [ ] **Step 5: Commit** `feat(ai-quality): shared feedback endpoint`.

### Task 4: Ticket Related items write to the shared tables

**Files:**
- Create: `migrations/<NNN+1>_ai_quality_related_backfill.sql` (via `/new-migration`, phase `expand`)
- Modify: `app/features/tickets/admin_routes.py::admin_ticket_related_feedback`, `app/features/tickets/__init__.py` (bump `PACK.version` patch)
- Test: `tests/test_rag_relationship_feedback.py`

**Interfaces:**
- Consumes: Task 1 `record_output`, `save_feedback`.
- Produces: each Related vote also exists as `ai_quality_responses(feature=<llm_usage key for rag_relationships.evaluate_next_batch>, subject_type="ticket", subject_id=<ticket_id>, target_type=<target rag_documents.source_type>, target_id=<target rag_documents.source_id>, user_id=<voter>, company_id=<ticket company>, model=<snapshot evaluated_model>)` plus an `ai_quality_feedback` row with `is_technician=1`. `rag_relationship_feedback` stays the source for per-ticket hiding in this PR.

- [ ] **Step 1: Write failing tests:** (a) after `admin_ticket_related_feedback` saves a 👎 (call the repo-level helper you add, `rag_relationships.mirror_feedback_to_quality(relationship, ticket_id, user_id, rating, company_id)`), one shared response with the target fields above and one `down` feedback exists; clearing removes the feedback. (b) Backfill: seed two `rag_relationship_feedback` rows, run the migration SQL twice through the SQLite adapter, assert exactly two responses and two feedback rows.
- [ ] **Step 2: Run** `pytest tests/test_rag_relationship_feedback.py -q -k "mirror or backfill"` — FAIL.
- [ ] **Step 3: Implement** the mirror helper and call it after `save_relationship_feedback` in the route (failures logged, never break the vote). Write the backfill as `INSERT … SELECT … WHERE NOT EXISTS (…)` joining `rag_relationship_feedback → rag_relationships → rag_documents` (the target is whichever side is not the ticket's document), then the feedback insert joined back on the same keys with `NOT EXISTS`. Literal 64-`0` string for `query_hash`; `'[]'` for the JSON columns.
- [ ] **Step 4: Run** `pytest tests/test_rag_relationship_feedback.py -q` — the new tests PASS (two tests in this file were already failing before this work because the fixture's `rag_documents` has no `company_id`; add that column to the fixture so the whole file passes).
- [ ] **Step 5: Commit** `feat(ai-quality): mirror Related-item votes into shared feedback`.

### Task 5: Rating control

**Files:**
- Create: `app/templates/macros/ai_rating.html`, `app/static/js/ai_rating.js`
- Modify: `app/static/css/app.css` (`.air-*` block), `app/templates/base.html` (load `ai_rating.js` with `defer` beside the other shared scripts)
- Test: `tests/test_ai_rating_macro.py`

**Interfaces:**
- Consumes: Task 3 endpoint.
- Produces: macro `ai_rating(response_id, current_rating=None, return_to="", csrf_token="", label="AI output")`. Renders `<form class="air" method="post" action="/api/ai-quality/feedback" data-ai-rating>` with hidden `response_id`, `return_to`, `_csrf`; two submit buttons `name="rating" value="up|down"` with `aria-pressed` reflecting `current_rating` and `sr-only` text "Helpful"/"Not helpful" (`aria-label` includes `label`); a hidden-by-default reason group (chips for each reason in `REASONS`, labelled in plain English: Incorrect, Unrelated, Incomplete, Missing sources, Irrelevant, Unsafe, Other) and an optional comment; a `<span role="status" aria-live="polite" data-ai-rating-status>`. Renders nothing when `response_id` is falsy. `ai_rating.js` enhances every `[data-ai-rating]`: posts JSON, toggles `aria-pressed`, pressing the active button again sends `rating: null`, 👎 reveals reasons, choosing a reason re-posts with it; status text "Thanks — saved" / "Couldn't save, try again".

- [ ] **Step 1: Write failing tests** rendering the macro via the app's Jinja env (`app.main.templates.env` or however `_render_template` gets it): with `response_id=0` → empty; with `current_rating="down"` → the down button has `aria-pressed="true"`; hidden `_csrf` present; every reason in `REASONS` appears as a chip value.
- [ ] **Step 2: Run** `pytest tests/test_ai_rating_macro.py -q` — FAIL.
- [ ] **Step 3: Implement** macro, JS, CSS (tokens only; respect `prefers-reduced-motion`).
- [ ] **Step 4: Run** the test — PASS. Then run the app with `/run` or a Playwright script against a page using the macro (Task 6 page is fine) and confirm 👍 toggles and the status updates; screenshot for the PR.
- [ ] **Step 5: Commit** `feat(ai-quality): reusable rating control`.

### Task 6: `/admin/ai-quality` by feature

**Files:**
- Modify: `app/repositories/ai_quality.py` (add `aggregate_by_feature`), `app/main.py::admin_ai_quality_page`, `app/templates/admin/ai_quality.html`, `app/api/routes/agent.py` (`quality_summary`, `quality_export`)
- Test: `tests/test_admin_ai_quality_page.py`, `tests/test_ai_quality_outputs.py`

**Interfaces:**
- Consumes: Task 1 schema.
- Produces: `async def aggregate_by_feature() -> list[dict]` with keys `feature`, `outputs`, `helpful`, `unhelpful`, `technician_votes`, `customer_votes`, `top_reason`, `last_rated_at` (UTC ISO string or None), ordered by `unhelpful DESC, outputs DESC`. Page context `feature_groups` (each row plus `label` from `llm_usage.feature_label`, falling back to the raw key) and `overview` (`ratings_30d`, `helpful_pct` int or None, `features_rated`). The previous `quality_groups`/`related_feedback_groups` tables are replaced; `relationship_feedback_aggregate` stays for the summary API.

- [ ] **Step 1: Write failing tests:** repo test seeds two features with mixed technician/customer votes and asserts the aggregate; page test (monkeypatched aggregate) asserts the overview chips, a row per feature with its label, an unknown key `legacy_x` rendered as-is, and the empty state "No AI output has been rated yet. Ratings appear here as people use 👍/👎 on AI content."
- [ ] **Step 2: Run** `pytest tests/test_admin_ai_quality_page.py tests/test_ai_quality_outputs.py -q` — FAIL.
- [ ] **Step 3: Implement.** Page uses the settings-workspace pattern (`data-ae-workspace`, `.ce-*`, `asset_edit_sections.js`) with sections *Overview* and *By feature* in a "Ratings" group, so PR 2/3 can add *Learned: links* and *Learned: lessons*. Table via `templates/macros/tables.html` (`data_table`, `table_toolbar`, `empty_state`). `quality_summary` returns `{"features": …, "groups": …, "related_feedback_groups": …, "pipeline_version": …}`; CSV export switches to the by-feature fields.
- [ ] **Step 4: Run** the same tests — PASS.
- [ ] **Step 5: Commit** `feat(ai-quality): admin view of ratings for every AI feature`.

### Task 7: Wrap-up

- [ ] **Step 1:** `/changelog-entry` — `Feature`, e.g. "AI output can now be rated with 👍/👎 through one shared control, and the AI Quality page shows ratings for every AI feature."
- [ ] **Step 2:** Audit every SQL statement in touched files for injection (Global Constraints).
- [ ] **Step 3: Run** `pytest tests/test_ai_quality_outputs.py tests/test_ai_quality_access.py tests/test_ai_quality_feedback_api.py tests/test_ai_rating_macro.py tests/test_admin_ai_quality_page.py tests/test_rag_relationship_feedback.py tests/test_ai_quality_evaluation.py tests/test_core_component_availability.py -q` — all pass except the pre-existing `test_essential8_report_queries_hidden` (fails on `main`; note in PR).
- [ ] **Step 4: Commit, push, open PR, subscribe to it.**
