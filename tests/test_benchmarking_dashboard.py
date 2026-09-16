from __future__ import annotations

import pytest

from app.services import benchmarking_dashboard


@pytest.mark.asyncio
async def test_build_cross_company_summary_calculates_pass_percentages(monkeypatch):
    async def fake_fetch_all(_query, _params):
        return [
            {
                "company_id": 7,
                "company_name": "Acme",
                "snapshot_date": "2026-09-15",
                "pass_count": 9,
                "fail_count": 3,
                "unknown_count": 1,
                "not_applicable_count": 2,
                "secure_score_percentage": 81.5,
            }
        ]

    monkeypatch.setattr(benchmarking_dashboard.db, "fetch_all", fake_fetch_all)

    rows = await benchmarking_dashboard.build_cross_company_summary()

    assert rows == [
        {
            "company_id": 7,
            "company_name": "Acme",
            "snapshot_date": "2026-09-15",
            "pass_count": 9,
            "fail_count": 3,
            "unknown_count": 1,
            "not_applicable_count": 2,
            "rated_total": 12,
            "pass_percentage": 75.0,
            "secure_score_percentage": 81.5,
        }
    ]
