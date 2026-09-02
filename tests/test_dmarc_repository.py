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
    assert fetch_all.await_args.args[1] == (42, end, start, None, None)


def test_organization_summary_filters_by_policy_domain(monkeypatch):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(dmarc.db, "fetch_all", fetch_all)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)

    asyncio.run(dmarc.organization_summary(42, start, end, "mail.example"))

    assert fetch_all.await_args.args[1] == (
        42,
        end,
        start,
        "mail.example",
        "mail.example",
    )


def test_policy_domains_returns_latest_policy_rows(monkeypatch):
    expected = [{"domain": "example.com", "policy": "reject"}]
    fetch_all = AsyncMock(return_value=expected)
    monkeypatch.setattr(dmarc.db, "fetch_all", fetch_all)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)

    assert asyncio.run(dmarc.policy_domains(42, start, end)) == expected
    assert fetch_all.await_args.args[1] == (42, end, start, end, start)


def test_overview_includes_disposition_and_forensic_detail_counts(monkeypatch):
    fetch_one = AsyncMock(
        side_effect=[
            {
                "total_messages": 20,
                "dmarc_pass": 12,
                "dkim_pass": 10,
                "spf_pass": 11,
                "disposition_none": 9,
                "disposition_quarantine": 6,
                "disposition_reject": 4,
                "disposition_other": 1,
            },
            {
                "forensic_reports": 7,
                "forensic_with_reported_domain": 7,
                "forensic_with_source_ip": 6,
                "forensic_with_delivery_result": 5,
                "forensic_with_authentication_results": 4,
                "forensic_with_original_mail_from": 3,
                "forensic_with_original_rcpt_to": 2,
                "forensic_with_dkim_details": 1,
            },
        ]
    )
    monkeypatch.setattr(dmarc.db, "fetch_one", fetch_one)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)

    metrics = asyncio.run(dmarc.overview(42, start, end))

    assert metrics["disposition_none"] == 9
    assert metrics["disposition_quarantine"] == 6
    assert metrics["disposition_reject"] == 4
    assert metrics["disposition_other"] == 1
    assert metrics["forensic_reports"] == 7
    assert metrics["forensic_with_reported_domain"] == 7
    assert metrics["forensic_with_source_ip"] == 6
    assert metrics["forensic_with_delivery_result"] == 5
    assert metrics["forensic_with_authentication_results"] == 4
    assert metrics["forensic_with_original_mail_from"] == 3
    assert metrics["forensic_with_original_rcpt_to"] == 2
    assert metrics["forensic_with_dkim_details"] == 1
