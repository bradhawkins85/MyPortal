import json
from pathlib import Path

from app.repositories.ai_quality import evidence_ids, redact_query
from scripts.evaluate_ai_quality import evaluate


def test_versioned_quality_set_passes_thresholds():
    dataset = json.loads(Path("evals/ai_quality/v1.json").read_text())
    assert dataset["version"] == 1
    assert min(evaluate(dataset["cases"]).values()) >= 0.95


def test_diagnostics_redact_sensitive_query_values():
    redacted = redact_query("Email person@example.com or 0412 345 678 about VPN")
    assert "person@example.com" not in redacted
    assert "0412" not in redacted


def test_evidence_telemetry_contains_identifiers_not_content():
    identifiers, types = evidence_ids({"tickets": [{"source_id": 7, "summary": "secret"}]})
    assert identifiers == ["tickets:7"]
    assert types == ["tickets"]
    assert "secret" not in json.dumps(identifiers)
