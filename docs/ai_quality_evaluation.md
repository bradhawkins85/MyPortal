# AI quality evaluation

Run `python scripts/evaluate_ai_quality.py`. The versioned, content-free fixture records expected evidence identifiers, citations, permission/no-answer outcomes, and representative ticket, KB, order, asset and chat intents. CI fails if retrieval recall, citation validity, grounded-answer rate, or no-answer precision falls below 0.95.

Runtime analytics retain a SHA-256 query identifier, a short redacted diagnostic query, evidence identifiers, source types, model/provider, pipeline version, latency, confidence and outcome—never evidence bodies or full prompts. Only the response owner may submit feedback. Only super administrators may read or export de-identified aggregates through `/api/agent/quality/summary` and `/api/agent/quality/export.csv`.
