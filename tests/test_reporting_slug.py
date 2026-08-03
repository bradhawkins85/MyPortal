"""Regression coverage for stable, automatically generated report slugs."""

from app.features.reporting.handlers import _reporting_slug


def test_reporting_slug_is_generated_from_name():
    assert _reporting_slug("  Monthly Résumé / Totals  ") == "monthly-resume-totals"


def test_reporting_slug_removes_unsupported_characters_and_limits_length():
    assert _reporting_slug("Revenue & Cost!!!") == "revenue-cost"
    assert len(_reporting_slug("A" * 200)) == 120
