#!/usr/bin/env python3
"""Deterministic, network-free quality gate for versioned evaluation fixtures."""
import argparse
import json
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evals/ai_quality/v1.json")
    parser.add_argument("--threshold", type=float, default=0.95)
    args = parser.parse_args()
    data = json.loads(Path(args.dataset).read_text())
    metrics = evaluate(data["cases"])
    print(json.dumps({"dataset_version":data["version"], "metrics":metrics}, sort_keys=True))
    raise SystemExit(1 if any(value < args.threshold for value in metrics.values()) else 0)


if __name__ == "__main__": main()
