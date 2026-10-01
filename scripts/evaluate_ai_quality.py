#!/usr/bin/env python3
"""Deterministic, network-free quality gate for versioned evaluation fixtures."""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def evaluate(cases):
    recall_scores, citation_scores, grounded, no_answer = [], [], [], []
    for case in cases:
        retrieved, expected = set(case["retrieved"]), set(case["expected"])
        if expected:
            recall_scores.append(len(retrieved & expected) / len(expected))
        citations = set(case["citations"])
        citation_scores.append(1.0 if not citations else len(citations & retrieved) / len(citations))
        grounded.append(float(not case["answer"] or citations.issubset(retrieved)))
        if not case["expect_answer"]:
            forbidden = set(case.get("forbidden", []))
            no_answer.append(float(not case["answer"] and not (retrieved & forbidden)))
    mean = lambda values: sum(values) / len(values) if values else 1.0
    return {"retrieval_recall":mean(recall_scores), "citation_validity":mean(citation_scores),
            "grounded_answer_rate":mean(grounded), "no_answer_precision":mean(no_answer)}


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else 1.0


def evaluate_related_feedback(labels, *, min_score=0.0, min_confidence=0.0):
    """Score technician 👍/👎 labels on ticket Related items.

    Each label carries the relevance score and confidence the relationship had
    when it was judged. A candidate threshold "shows" a label when both values
    clear it, so the metrics say how a stricter (or looser) gate would have
    matched the technicians' judgements:

    - ``precision``: share of shown items technicians marked relevant.
    - ``relevant_retention``: share of relevant items still shown.
    - ``rejected_suppression``: share of 👎 items the threshold would hide.
    """
    shown = [
        label for label in labels
        if float(label["relevance_score"] or 0) >= min_score
        and float(label["confidence"] or 0) >= min_confidence
    ]
    relevant = [label for label in labels if label["relevant"]]
    rejected = [label for label in labels if not label["relevant"]]
    shown_relevant = sum(1 for label in shown if label["relevant"])
    shown_rejected = len(shown) - shown_relevant
    by_model = defaultdict(lambda: [0, 0])
    for label in shown:
        counts = by_model[label.get("evaluated_model") or "unknown"]
        counts[0] += int(bool(label["relevant"]))
        counts[1] += 1
    return {
        "labels": len(labels),
        "shown": len(shown),
        "precision": _ratio(shown_relevant, len(shown)),
        "relevant_retention": _ratio(shown_relevant, len(relevant)),
        "rejected_suppression": _ratio(len(rejected) - shown_rejected, len(rejected)),
        "precision_by_model": {
            model: _ratio(good, total) for model, (good, total) in sorted(by_model.items())
        },
    }


def sweep_related_feedback(labels, thresholds):
    return [
        {"min_score": threshold, **evaluate_related_feedback(labels, min_score=threshold)}
        for threshold in thresholds
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evals/ai_quality/v1.json")
    parser.add_argument("--threshold", type=float, default=0.95)
    parser.add_argument("--related-labels", help="related-item feedback export (see docs/ai_quality_evaluation.md)")
    parser.add_argument("--min-score", type=float, default=0.0, help="candidate relevance threshold to evaluate")
    parser.add_argument("--min-confidence", type=float, default=0.0, help="candidate confidence threshold to evaluate")
    parser.add_argument("--related-min-precision", type=float, default=None,
                        help="fail when related-item precision falls below this value")
    parser.add_argument("--sweep", action="store_true", help="report related-item metrics across score thresholds")
    args = parser.parse_args()
    data = json.loads(Path(args.dataset).read_text())
    metrics = evaluate(data["cases"])
    report = {"dataset_version":data["version"], "metrics":metrics}
    failed = any(value < args.threshold for value in metrics.values())
    if args.related_labels:
        related = json.loads(Path(args.related_labels).read_text())
        related_metrics = evaluate_related_feedback(
            related["labels"], min_score=args.min_score, min_confidence=args.min_confidence
        )
        report["related_feedback"] = {
            "dataset_version": related["version"],
            "min_score": args.min_score,
            "min_confidence": args.min_confidence,
            "metrics": related_metrics,
        }
        if args.sweep:
            report["related_feedback"]["sweep"] = sweep_related_feedback(
                related["labels"], [round(0.5 + step * 0.05, 2) for step in range(10)]
            )
        if args.related_min_precision is not None:
            failed = failed or related_metrics["precision"] < args.related_min_precision
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__": main()
