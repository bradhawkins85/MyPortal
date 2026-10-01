# AI quality evaluation

Run `python scripts/evaluate_ai_quality.py`. The versioned, content-free fixture records expected evidence identifiers, citations, permission/no-answer outcomes, and representative ticket, KB, order, asset and chat intents. CI fails if retrieval recall, citation validity, grounded-answer rate, or no-answer precision falls below 0.95.

Runtime analytics retain a SHA-256 query identifier, a short redacted diagnostic query, evidence identifiers, source types, model/provider, pipeline version, latency, confidence and outcome—never evidence bodies or full prompts. Only the response owner may submit feedback. Only super administrators may read or export de-identified aggregates through `/api/agent/quality/summary` and `/api/agent/quality/export.csv`.

## Technician feedback on Related items

Technicians can mark each item in a ticket's **Related** panel 👍 or 👎. Votes are stored in `rag_relationship_feedback` against the `rag_relationships` row, scoped to the ticket they were cast from. Any 👎 hides that item from that ticket (in the stored list, on Refresh, and as a source for Suggest reply); Undo clears the vote. Each vote snapshots the relationship type, relevance score, confidence and evaluating model so a label still describes what the technician saw after the row is re-evaluated.

Export the labels as a content-free dataset (identifiers, scores, types and model names only), either as a super administrator from **AI Quality → Export related labels** (`/api/agent/quality/related-labels.json`) or on the server:

```
python manage.py export-related-feedback --output evals/ai_quality/related_feedback.json
```

A relationship voted from one ticket becomes one label; it is relevant only when nobody voted it down. Measure a candidate threshold or model against the labels:

```
python scripts/evaluate_ai_quality.py --related-labels evals/ai_quality/related_feedback.json --min-score 0.7 --sweep
```

The report gives `precision` (shown items marked relevant), `relevant_retention` (relevant items still shown), `rejected_suppression` (👎 items the threshold would hide) and precision per evaluating model; `--sweep` repeats it across score thresholds 0.50–0.95. Add `--related-min-precision 0.8` to fail when precision drops below a floor. CI runs the synthetic fixture `evals/ai_quality/related_feedback_v1.json` to keep the evaluator honest.

