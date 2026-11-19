# AI feedback that MyPortal learns from

Date: 2026-11-19
Status: Draft for approval

## Intent and why

Every piece of AI output MyPortal shows can be rated 👍/👎 by the person
reading it, and those ratings **change what MyPortal does next time**. The
admin page `/admin/ai-quality` is how an admin sees what was rated and what
has been learned; it is not the goal in itself.

The motivating case: a KB article is linked to a ticket as a *Related* item,
but it is completely unrelated. The technician gives it 👎. Today that hides
it on that one ticket only; the relationship evaluator never reads feedback,
so the same bad link keeps appearing on similar tickets. MyPortal should learn
that the article is not useful for tickets like this.

## Goals

- A consistent 👍/👎 control (with an optional reason and comment) on every
  AI output a person sees, technicians and customers alike, within the
  company scoping they already have.
- Ratings feed back into behaviour:
  - **Links and search results** — vote-adjusted scores on similar subjects.
  - **Generated text** — rated examples and lessons added to prompts.
  - **AI that changes fields** — removals and 👎 become corrections.
- `/admin/ai-quality` shows ratings for every feature, what has been learned,
  and lets a super admin undo a learned lesson.
- Existing ratings (AI search answers, ticket Related items) carry over.

## Non-goals

- Fine-tuning or retraining models. Providers are Ollama, OpenAI and
  llama.cpp; learning happens at run time through scoring and prompts.
- Rating embeddings (nothing visible) or the Matrix waiting assistant (no
  portal surface to rate in).
- Per-user personalisation. Learning is per company (examples) and global
  (lessons), not per technician.

## Anti-goals (failure even if it "works")

- **Cross-company leakage.** Company A's ticket text, summaries or article
  content must never appear in a prompt or output for company B.
- **One vote burying good content.** A single 👎 must not remove an article
  everywhere; it nudges.
- **Silent, irreversible learning.** Every learned effect is visible on the
  admin page and can be undone.
- **Prompt-injection through feedback.** Rated text and comments are untrusted
  and must go through `app/services/ai_prompt_security.build_prompt` as
  `UntrustedRecord`s, never into trusted instructions.
- Slower page loads: rating must not block rendering, and learning lookups
  must stay cheap on the hot path.

## Decisions made with the user

1. **Reach of a 👎 on a link:** hide it on that ticket immediately, *and*
   lower that item's score on similar tickets. More agreeing votes move it
   further; 👍 pushes the other way. (Not: ticket-only; not: blocked
   everywhere after N votes.)
2. **Cross-company learning for generated text:** full rated examples are
   reused only within the same company; across companies only de-identified
   lessons travel (e.g. "summaries often rated *incomplete*").
3. **Approach A** (below), delivered as four independently shippable PRs in
   the order listed under *Delivery*.

## Approaches considered

- **A. One shared rating and learning system (chosen).** One output record,
  one rating control, one endpoint, one learning layer with two mechanisms
  (scores for links, prompt examples/lessons for text). One admin view.
- **B. Per-feature feedback tables** like `rag_relationship_feedback` today.
  ~26 copies, no shared learning or admin view. Rejected.
- **C. Shared ratings, learning only via prompt guidance.** Cannot stop a bad
  link from being made, so it fails the motivating case. Rejected.

## Design

### 1. Recording AI outputs

Generalise `ai_quality_responses` (migration 387) rather than add a parallel
table. A new migration adds nullable columns:

| Column | Purpose |
|---|---|
| `subject_type`, `subject_id` | What the output is about (`ticket`, `kb_article`, `rmm_finding`, `product`, …) |
| `target_type`, `target_id` | For links: the linked item (`kb_article:12`) |
| `output_text` | The generated text (company-scoped; used for same-company examples) |
| `output_hash` | De-duplicate re-renders of the same output |
| `prompt_version` | Short string per feature, bumped when its prompt changes |

`query_hash`/`query_redacted`/`evidence_identifiers`/`source_types` become
optional for non-search features (default empty JSON / empty string), so the
existing AI search path is unchanged. `feature` uses the keys in
`app/services/llm_usage.py::FEATURES` so ratings line up with usage stats.

A helper `ai_quality.record_output(feature=…, company_id=…, subject=…,
target=…, output_text=…, model=…, prompt_version=…) -> response_id` is
called wherever an output is produced or first shown. For outputs stored on
a record (ticket summary, resolution steps), record at generation time and
store the `response_id` alongside the field so the page can render the
control without a lookup per render.

`output_text` is stored only for features that use examples; it is deleted
with the subject (cascade by subject where a FK is possible, otherwise a
cleanup job) and is never exported. Retention: follow the existing ticket
retention; no new retention setting.

### 2. Rating

- `ai_quality_feedback` keeps one row per (response, user). It gains
  `scope_key` (see §3) so a vote can be found quickly by learning lookups.
- **Endpoint:** generalise `POST /api/agent/feedback` into
  `POST /api/ai-quality/feedback` (keep the old path as an alias). Auth: the
  user must be able to see the subject (reuse each feature's existing access
  check via a small per-subject-type resolver), not only "owns the response"
  as today — a technician rates a summary they did not trigger.
- Reasons: existing set (`incorrect`, `uncited`, `irrelevant`, `incomplete`,
  `unsafe`, `other`) plus `unrelated` for links. Optional comment ≤1000.
- **UI control:** a Jinja macro `ai_rating(response_id, …)` in
  `templates/macros/` plus `app/static/js/ai_rating.js`. 👍/👎 buttons with
  `aria-pressed`; 👎 opens a small reason picker (chips) and optional comment;
  works as a plain `<form method="post">` with CSRF before JS loads. Status
  text in `role="status" aria-live="polite"`. Follows the Design Guidelines;
  no hard-coded colours.
- **Related items** keep their current per-ticket hide behaviour; their votes
  are written to the shared tables and `rag_relationship_feedback` is
  migrated in (one data migration, idempotent) and then read through the new
  path. Keep the old table until PR 2 has shipped, then drop in a later
  contract migration.

### 3. Learning

A new service `app/services/ai_learning.py` owns how ratings become
behaviour. Two mechanisms:

**Link learning (PR 2)** — Related items, KB search, AI search evidence.

- A *lesson* is (target item, subject embedding, vote ±1, company, voter,
  time). The subject embedding is the ticket document's vector from the
  existing RAG index (mean of its chunk `embedding_json`).
- When scoring a candidate link (target T for subject S), find past votes on
  T whose subject embedding is similar to S (cosine ≥ a threshold), weight
  each by similarity and recency, and apply a bounded adjustment:
  `adjusted = score + clamp(Σ wᵢ·voteᵢ · step, −max_penalty, +max_boost)`.
  A single vote nudges; several agreeing votes can push below the match
  threshold (`min_score` in `_parse_evaluation`) so the link is not made.
- Applied in three places: after the relationship evaluator returns a score
  (`rag_relationships` match decision), in KB search ranking, and in agent
  evidence ranking.
- Votes cross companies for links only as the adjustment number — no text is
  shared — so this is consistent with decision 2.
- Learned effects are listed on the admin page per target ("VPN setup
  article: marked unrelated on 4 printer tickets") with **Undo**, which marks
  those lessons ignored (soft delete) rather than deleting votes.

**Text learning (PR 3)** — summaries, resolution steps, insights, reply
suggestions, troubleshooter, health check, minidump, RMM findings, script
summaries, call summaries, marketing, product descriptions, reporting query.

- **Same-company examples:** when building the prompt, include up to N (e.g.
  2 positive, 1 negative with its reason) recent rated outputs of the same
  feature from the same company, chosen by subject similarity when an
  embedding exists, else recency. Passed as `UntrustedRecord`s labelled as
  examples of rated output, with an explicit allowed-use ("style and
  completeness reference only; do not copy facts").
- **Cross-company lessons:** a periodic job (existing scheduler) aggregates
  👎 reasons per feature across all companies into short, de-identified
  guidance lines, e.g. "Recent ticket summaries were often rated
  *incomplete*: include the current status and next step." Lessons are built
  only from reason codes and counts, never from text or comments. They go into
  the trusted instructions for that feature, and are listed on the admin page
  with **Undo**.
- Token budget: examples and lessons share a capped slice of each feature's
  prompt budget; truncate examples first.

**Field-changing AI (PR 4)** — AI tags, ticket rename, classify, KB article
tags.

- Show a small "AI" marker with the rating control next to the changed field.
- A technician removing an AI-added tag records a 👎 for that tag on that
  subject; renaming an AI-renamed title records a 👎 for the rename.
- Learning uses the text-learning mechanism (same-company examples of
  accepted/rejected tags or titles; cross-company reason lessons).

### 4. Admin page

`/admin/ai-quality` becomes a settings-workspace page (asset edit page
pattern, `.ce-*`, `asset_edit_sections.js`):

- **Overview** chips: ratings this month, % helpful, lessons active.
- **By feature** — one row per feature: outputs, helpful, unhelpful, top
  reasons, trend. Replaces the two current tables.
- **Learned: links** — targets with a net effect, with an example subject
  title (the admin is a super admin; they may see titles) and **Undo**.
- **Learned: lessons** — current cross-company guidance lines per feature
  with **Undo** / **Restore**.
- Existing exports keep working.

## Delivery

1. **Foundation** — migration for new columns; `record_output`; generalised
   feedback endpoint and access resolver; `ai_rating` macro + JS; migrate
   Related-item and AI search ratings; admin page "By feature". Ratings appear
   for features as they are wired in later PRs.
2. **Link learning** — `ai_learning` link mechanism wired into Related
   items, KB search, AI search evidence; "Learned: links" with Undo.
3. **Text learning** — record + rate each text feature listed above;
   same-company examples; cross-company lesson job; "Learned: lessons".
4. **Field-changing AI** — markers on AI tags/rename/classify/KB tags;
   removals as 👎; learning via PR 3 mechanism.

Each PR: migrations via `/new-migration` (idempotent, phase header),
change-log entry via `/changelog-entry`, focused tests, SQL audited for
injection (parameterised only).

## Left to the builder

- Exact similarity threshold, recency half-life, step, and caps for link
  learning — start conservative (e.g. cosine ≥ 0.8, step 0.05/vote,
  max penalty 0.3) and expose them as constants, not settings.
- Number of examples per feature and token-budget split.
- Per-subject-type access resolvers for the feedback endpoint.
- Whether to cache document mean embeddings.
- Exact wording of generated lessons.

## Calls I made (not discussed)

- Generalise `ai_quality_responses` instead of a new table.
- Learning is per company and global, never per user.
- Undo is a soft delete of lessons; raw votes are kept.
- `output_text` stored only for example-using features and never exported.
- Field-changing AI learns through prompt examples, not a separate model.
