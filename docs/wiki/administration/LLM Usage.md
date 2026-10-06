# LLM Usage

**Administration → Automation & AI → LLM Usage** (`/admin/llm-usage`, super
admins only) shows how much MyPortal uses its LLM providers.

## What the page shows

- **Date range** – pick a *From* and *To* day (inclusive, UTC) or use the
  *Last 7 / 30 / 90 days* shortcuts. The default is the last 30 days.
- **Totals** – requests, input tokens, output tokens, total tokens and failed
  requests in the range.
- **Tokens per day** – stacked input/output bars, with the daily figures in an
  expandable table.
- **Token share by MyPortal function** – every MyPortal function that calls the
  LLM (ticket AI summaries, tags, insights, reply suggestions, the Agent, AI
  automation actions, knowledge base search, call summaries, RAG embeddings and
  so on), with its requests, tokens and percentage of total tokens. Functions
  that did not run in the range are listed with 0%.
- **Usage by model** – the same totals per provider and model.

The same data is available as JSON from `GET /api/admin/llm-usage?start=YYYY-MM-DD&end=YYYY-MM-DD`.

## How usage is recorded

Each request sent through the Ollama module (Ollama, OpenAI and llama.cpp
providers), the Matrix chat waiting assistant and the RAG embedding provider
writes one row to `llm_usage_events`. Only metadata is stored: the MyPortal
function, provider, model, status, token counts, duration and the related
webhook monitor event. Prompts and responses are never stored.

Token counts come from the provider response (`prompt_eval_count`/`eval_count`
for Ollama, `usage` for OpenAI-compatible APIs). When a provider does not report
usage, tokens are estimated at roughly four characters per token and the page
says how many requests were estimated. Failed requests are recorded with zero
tokens.

Usage is recorded from the time this feature is installed; earlier requests are
not backfilled.

## Disabling

Add `llm_usage` to `DISABLED_FEATURE_PACKS` to hide the page and API. Usage is
still recorded so the history is complete if the page is re-enabled.
