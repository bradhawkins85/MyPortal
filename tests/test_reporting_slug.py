"""Regression coverage for stable, automatically generated report slugs."""

import asyncio

from app.features.reporting.handlers import _reporting_slug
from app.repositories import reporting as reporting_repo


def test_reporting_slug_is_generated_from_name():
    assert _reporting_slug("  Monthly Résumé / Totals  ") == "monthly-resume-totals"


def test_reporting_slug_removes_unsupported_characters_and_limits_length():
    assert _reporting_slug("Revenue & Cost!!!") == "revenue-cost"
    assert len(_reporting_slug("A" * 200)) == 120


def test_updating_existing_report_does_not_write_its_slug(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_execute(sql, params):
        captured["sql"] = sql
        captured["params"] = params

    monkeypatch.setattr(reporting_repo.db, "execute", fake_execute)

    asyncio.run(
        reporting_repo.update_query(
            41,
            name="Renamed report",
            description="Updated without changing its existing slug",
            sql_query="SELECT 1",
        )
    )

    assert "slug" not in captured["sql"].lower()
    assert captured["params"] == (
        "Renamed report",
        "Updated without changing its existing slug",
        "SELECT 1",
        41,
    )
