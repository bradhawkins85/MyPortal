import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.repositories import dmarc


def _report():
    return {
        "reporter": "Receiver Inc",
        "report_id": "duplicate-1",
        "date_begin": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "date_end": datetime(2026, 8, 2, tzinfo=timezone.utc),
        "domain": "example.com",
        "adkim": "r",
        "aspf": "r",
        "policy": "none",
        "subdomain_policy": "none",
        "percentage": 100,
        "content_sha256": "b" * 64,
        "records": [],
    }


def test_save_report_replaces_existing_report_id(monkeypatch):
    fetch_one = AsyncMock(return_value={"id": 17})
    execute = AsyncMock()
    insert = AsyncMock(return_value=18)
    monkeypatch.setattr(dmarc.db, "fetch_one", fetch_one)
    monkeypatch.setattr(dmarc.db, "execute", execute)
    monkeypatch.setattr(dmarc.db, "execute_returning_lastrowid", insert)

    assert asyncio.run(dmarc.save_report(42, 9, _report(), "a" * 64)) == 18
    fetch_one.assert_awaited_once_with(
        "SELECT id FROM dmarc_reports WHERE company_id=%s AND report_id=%s",
        (42, "duplicate-1"),
    )
    execute.assert_awaited_once_with(
        "DELETE FROM dmarc_reports WHERE company_id=%s AND id=%s", (42, 17)
    )


def test_organization_summary_calculates_compliance_rate(monkeypatch):
    fetch_all = AsyncMock(
        return_value=[
            {
                "organization": "Receiver Inc",
                "volume": 8,
                "compliant": 6,
                "non_compliant": 2,
                "spf_pass": 5,
                "spf_fail": 3,
                "dkim_pass": 4,
                "dkim_fail": 4,
                "unauthenticated": 2,
            }
        ]
    )
    monkeypatch.setattr(dmarc.db, "fetch_all", fetch_all)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)

    rows = asyncio.run(dmarc.organization_summary(42, start, end))

    assert rows[0]["compliance_rate"] == 75.0
    assert fetch_all.await_args.args[1] == (42, end, start)
