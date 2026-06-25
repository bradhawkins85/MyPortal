# PRD Addendum: Multi-Stage Conversational RAG With Cross-Type Evidence Diversity

## Problem

After moving to RAG-only context, retrieval is now over-selecting chats because many chat records contain duplicate or near-duplicate content.

This creates a new failure mode:

- many relevant chats are returned
- other relevant source types are hidden
- tickets, KBs, products, assets, orders, and issues are underrepresented
- duplicate evidence consumes the context budget
- the user gets a narrow answer instead of a complete view

The issue is no longer “too much unrelated context”.

The issue is “too many similar results from one source type”.

## Required Behaviour

MyPortal should retrieve as much relevant information as possible across all relevant content types, while deduplicating repeated content and returning results to the UI in stages.

The LLM interaction should support multiple internal prompts per user query, allowing MyPortal to:

1. classify the query
2. search relevant source types
3. deduplicate repeated content
4. summarise each source category
5. ask a final synthesis prompt
6. stream or return staged results to the UI

## Functional Requirements

### 1. Source-Type Diversity

Retrieval must not allow one source type to dominate the result set.

Implement per-source caps after relevance filtering.

Example default caps:

```python
SOURCE_TYPE_CAPS = {
    "tickets": 4,
    "ticket_comments": 6,
    "knowledge_base": 4,
    "chats": 4,
    "products": 4,
    "assets": 3,
    "orders": 3,
    "issues": 3,
    "best_practices": 2,
}
```

If 20 matching chats exist, only the top diverse chat results should be included.

### 2. Duplicate and Near-Duplicate Detection

Deduplicate by:

- exact content hash
- normalized text hash
- source URL
- linked ticket ID
- canonical article slug
- semantic similarity above threshold

Example:

```python
if cosine_similarity(candidate.embedding, existing.embedding) > 0.94:
    mark_as_duplicate(candidate, duplicate_of=existing.id)
```

Do not delete duplicates entirely.

Instead expose them as grouped evidence:

```json
{
  "primary": "[Chat:#30]",
  "duplicates": ["[Chat:#31]", "[Chat:#44]", "[Chat:#45]"],
  "duplicate_count": 3
}
```

### 3. Evidence Grouping

The UI and LLM should receive grouped evidence, not repeated snippets.

Example:

```text
[Chat:#30]
Summary: User was shown a Lenovo touchpad article.
Also found in 4 similar chats: #31, #44, #45, #62.
```

This preserves visibility without wasting tokens.

### 4. Cross-Type Retrieval Buckets

Run retrieval independently per source type.

Example:

```python
retrieval_plan = {
    "tickets": search_tickets(query),
    "knowledge_base": search_kb(query),
    "chats": search_chats(query),
    "products": search_products(query),
    "assets": search_assets(query),
}
```

Then merge results using:

```text
final_score =
    relevance_score
    + exact_match_boost
    + source_priority_boost
    + diversity_boost
    - duplicate_penalty
```

This ensures chats do not crowd out tickets or KBs.

### 5. Multi-Stage LLM Interaction

Replace the single giant prompt with a staged pipeline.

#### Stage 1: Query Understanding

Prompt the LLM or deterministic parser to return:

```json
{
  "intent": "ticket_related_hardware_purchase",
  "entities": {
    "ticket_ids": [24425],
    "people": ["Brad Hawkins", "Jimmi Nolan"],
    "products": ["CMOS battery"],
    "systems": ["Trello", "eBay"],
    "compatibility_terms": ["smartcard-inline"]
  },
  "preferred_sources": ["tickets", "ticket_comments", "knowledge_base", "products", "chats"],
  "blocked_sources": ["best_practices", "mailboxes", "reports"]
}
```

#### Stage 2: Retrieval

Use the Stage 1 plan to search each source type separately.

Return partial UI event:

```json
{
  "stage": "retrieval_started",
  "message": "Searching tickets, KB articles, products, and chats..."
}
```

#### Stage 3: Evidence Review

Send retrieved candidates to the LLM in compact form and ask it to classify each as:

```json
{
  "source": "[Ticket:#24425]",
  "relevance": "direct | supporting | duplicate | unrelated",
  "reason": "Mentions CMOS battery purchase and expired eBay listing",
  "keep": true
}
```

Unrelated results are removed before final answer generation.

#### Stage 4: Category Summaries

Summarise evidence by source type:

```text
Tickets:
- Ticket 24425 is directly relevant.
Chats:
- 6 duplicate chat references mention the same related issue.
Products:
- No exact CMOS battery product match found.
Knowledge Base:
- No released article directly covers this CMOS battery compatibility question.
```

Return this to the UI as an intermediate result.

#### Stage 5: Final Answer

Generate final answer from the curated evidence only.

## UI Requirements

The UI should support staged results.

Example states:

1. Understanding query
2. Searching tickets
3. Searching knowledge base
4. Searching products
5. Grouping duplicates
6. Preparing answer
7. Final answer

The UI should display:

```text
Found:
- 1 directly relevant ticket
- 3 supporting chats
- 0 matching KB articles
- 0 matching products
```

Then show the final answer.

## API Response Shape

Update the agent endpoint to optionally return staged responses.

For non-streaming:

```json
{
  "answer": "...",
  "stages": [
    {
      "name": "query_understanding",
      "status": "complete",
      "data": {}
    },
    {
      "name": "retrieval",
      "status": "complete",
      "data": {
        "tickets": 1,
        "chats": 8,
        "knowledge_base": 0,
        "products": 0
      }
    },
    {
      "name": "deduplication",
      "status": "complete",
      "data": {
        "duplicates_grouped": 6
      }
    },
    {
      "name": "final_answer",
      "status": "complete"
    }
  ],
  "evidence": {
    "tickets": [],
    "knowledge_base": [],
    "products": [],
    "chats": []
  }
}
```

For streaming:

Use server-sent events or chunked JSON:

```jsonl
{ "event": "stage", "name": "query_understanding", "status": "complete" }
{ "event": "stage", "name": "retrieval", "status": "running", "source": "tickets" }
{ "event": "evidence", "source_type": "tickets", "count": 1 }
{ "event": "stage", "name": "deduplication", "status": "complete" }
{ "event": "answer_delta", "text": "I found the related ticket..." }
{ "event": "done" }
```

## Prompting Requirements

### Retrieval Review Prompt

```text
You are reviewing retrieved MyPortal evidence.
User query:
{{ query }}
Candidate evidence:
{{ candidates }}
Classify each item as:
- direct
- supporting
- duplicate
- unrelated
Keep only evidence that directly helps answer the query.
Return JSON only.
```

### Final Answer Prompt

```text
You are the MyPortal Agent.
Answer using only the curated evidence below.
Do not mention unrelated sources.
Do not suggest unrelated products or articles.
If the curated evidence does not contain the answer, say so.
Curated evidence:
{{ evidence }}
```

## Acceptance Criteria

- Duplicate chats are grouped, not repeated.
- Chats cannot consume the entire result set.
- Relevant tickets, KBs, products, assets, and issues can still appear.
- Retrieval happens per source type before merging.
- The UI can show progress and partial findings.
- The final LLM prompt contains curated evidence only.
- The user can see when no KB/product match was found rather than silently missing those sources.

## Expected Outcome

For the CMOS battery example, MyPortal should return something like:

```text
Found:
- 1 directly relevant ticket
- 5 similar chats grouped into 1 chat evidence group
- 0 matching KB articles
- 0 matching product records
- 0 relevant best-practice records
Final answer:
I found the related ticket about CMOS battery purchasing, but no released KB article or matching product record confirming compatibility...
```

This gives the user a complete, transparent answer while preventing duplicate chats from dominating the context.
