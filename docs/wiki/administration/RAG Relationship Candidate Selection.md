# RAG relationship candidate selection

Relationship evaluation uses a deterministic candidate funnel before invoking
the configured LLM. Candidates must pass the source compatibility matrix and
company boundary, then are ranked by shared identifiers/tags, lexical overlap,
and similarity of their indexed embeddings. Only the configured top-K is
queued, so model work is bounded for each changed document.

## Settings

* `RAG_RELATIONSHIP_CANDIDATE_LIMIT` is the hard per-document top-K (default
  `24`, maximum `100`).
* `RAG_RELATIONSHIP_TICKET_CANDIDATE_LIMIT` caps ticket targets within that set
  (default `5`, maximum `20`).
* `ENABLE_TICKET_RELATIONSHIPS` is the explicit administrator opt-in for
  ticket-to-ticket discovery and defaults to `false`. It does **not** disable
  ticket-to-knowledge-base or ticket-to-asset discovery.

Tickets are assigned a higher queue priority than background non-ticket pairs.
A company document can only be compared with documents from the same company,
or a company-less document whose stored permission scope explicitly declares
`anonymous` or `authenticated` visibility. This check occurs before queueing
and is repeated immediately before enqueueing.

The relationship metrics response includes cumulative candidate-funnel counts
for eligible documents, prefiltered pairs, queued evaluations, and positive
matches.
