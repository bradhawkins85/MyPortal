import json
import re
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


def test_redact_query_handles_trailing_email_punctuation():
    redacted = redact_query("Contact person@example.com.")
    assert redacted == "Contact [email]."


def test_redact_query_ignores_invalid_email_like_tokens():
    redacted = redact_query("Not an email: person@example.")
    assert redacted == "Not an email: person@example."


def test_redact_query_redacts_long_number_sequences():
    redacted = redact_query("Call +61 412 345 678 or 1234 5678")
    assert "[number]" in redacted
    assert "1234 5678" not in redacted


def test_redact_query_bounds_processing_for_large_adversarial_input():
    payload = ("a" * 3000) + ("@" * 3000) + ("-" * 3000) + ("9 " * 3000)
    redacted = redact_query(payload)
    assert len(redacted) == 500


def test_evidence_telemetry_contains_identifiers_not_content():
    identifiers, types = evidence_ids({"tickets": [{"source_id": 7, "summary": "secret"}]})
    assert identifiers == ["tickets:7"]
    assert types == ["tickets"]
    assert "secret" not in json.dumps(identifiers)


def test_quality_migration_foreign_keys_match_mysql_parent_types():
    """MySQL rejects FKs whose integer size/sign differs from the parent key."""
    initial_schema = Path("migrations/001_init.sql").read_text()
    quality_schema = Path("migrations/387_ai_quality_feedback.sql").read_text()

    assert re.search(r"CREATE TABLE IF NOT EXISTS users\s*\(\s*id INT ", initial_schema)
    assert re.search(r"CREATE TABLE IF NOT EXISTS ai_quality_responses\s*\(\s*id INT ", quality_schema)
    assert re.search(r"\buser_id INT NOT NULL", quality_schema)
    assert re.search(r"\bresponse_id INT NOT NULL", quality_schema)
    assert "user_id BIGINT" not in quality_schema
    assert "response_id BIGINT" not in quality_schema
