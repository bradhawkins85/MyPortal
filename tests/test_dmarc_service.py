import asyncio
import gzip
import io
from pathlib import Path
from unittest.mock import AsyncMock
import zipfile
import pytest

from app.services import dmarc
from app.services.dmarc import DmarcInputError, IngestionLimits, parse_aggregate_xml, parse_forensic_report, reporting_address, unpack_attachment

XML = (Path(__file__).parent / "fixtures/dmarc/valid.xml").read_bytes()

def test_valid_xml_and_utc_normalization():
    report = parse_aggregate_xml(XML)
    assert report["date_begin"].utcoffset().total_seconds() == 0
    assert report["records"][0]["message_count"] == 5
    assert report["records"][0]["dkim_result"] == "pass"


def test_policy_published_nonexistent_domain_policy_is_parsed():
    xml = XML.replace(b"<pct>100</pct>", b"<pct>75</pct><np>reject</np>")
    report = parse_aggregate_xml(xml)

    assert report["percentage"] == 75
    assert report["nonexistent_policy"] == "reject"

def test_gzip_and_zip():
    assert unpack_attachment("report.xml.gz", gzip.compress(XML))[0][1] == XML
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive: archive.writestr("report.xml", XML)
    assert unpack_attachment("report.zip", stream.getvalue())[0][1] == XML

def test_nested_and_unsafe_archives_rejected():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive: archive.writestr("../report.xml", XML)
    with pytest.raises(DmarcInputError): unpack_attachment("report.zip", stream.getvalue())
    with pytest.raises(DmarcInputError): unpack_attachment("report.gz", gzip.compress(gzip.compress(XML)))

def test_malformed_missing_and_oversized_rejected():
    with pytest.raises(DmarcInputError): parse_aggregate_xml(b"<feedback>")
    with pytest.raises(DmarcInputError): parse_aggregate_xml(b"<feedback/>")
    with pytest.raises(DmarcInputError): unpack_attachment("a.xml", XML, IngestionLimits(compressed_bytes=10))

def test_reporting_address_does_not_expose_company_id():
    assert reporting_address("abcdefghijklmnop", "Reports.Example") == "DMARC+abcdefghijklmnop@reports.example"


def test_company_reporting_addresses_returns_all_active_distinct_mailboxes(monkeypatch):
    monkeypatch.setattr(
        "app.repositories.m365_mail_accounts.list_dmarc_accounts",
        AsyncMock(return_value=[
            {"active": True, "user_principal_name": "reports@one.example"},
            {"active": True, "user_principal_name": "REPORTS@one.example"},
            {"active": False, "user_principal_name": "disabled@example.com"},
            {"active": True, "user_principal_name": "reports@two.example"},
        ]),
    )
    assert asyncio.run(dmarc.company_reporting_addresses(42)) == [
        "reports@one.example", "reports@two.example"
    ]


def test_forensic_arf_extracts_applicable_metadata():
    report = parse_forensic_report(b"""Feedback-Type: auth-failure
Version: 1
User-Agent: receiver.example
Arrival-Date: Fri, 28 Aug 2026 12:30:00 +0000
Source-IP: 192.0.2.10
Reported-Domain: sender.example
Delivery-Result: reject
Auth-Failure: dmarc
Authentication-Results: receiver.example; dmarc=fail
Original-Mail-From: <sender@sender.example>
Original-Rcpt-To: <recipient@example.net>
DKIM-Domain: sender.example
DKIM-Selector: mail
Identity-Alignment: dkim, spf

""")
    assert report["feedback_type"] == "auth-failure"
    assert report["source_ip"] == "192.0.2.10"
    assert report["reported_domain"] == "sender.example"
    assert report["arrival_at"].isoformat() == "2026-08-28T12:30:00+00:00"
    assert "content_sha256" in report


def test_forensic_report_rejects_non_authentication_feedback():
    with pytest.raises(DmarcInputError, match="not an authentication failure"):
        parse_forensic_report(b"Feedback-Type: abuse\nSource-IP: 192.0.2.1\n\n")


def test_policy_domain_candidates_include_parent_domains():
    assert dmarc._policy_domain_candidates("mail.example.com.") == [
        "mail.example.com",
        "example.com",
    ]


def test_ingest_attachment_matches_company_by_policy_domain(monkeypatch):
    create_import = AsyncMock(return_value=31)
    mark_import = AsyncMock()
    save_report = AsyncMock(return_value=44)
    set_import_company = AsyncMock()
    company_by_code = AsyncMock(return_value=None)
    lookup_company = AsyncMock(side_effect=lambda domain: {"id": 77} if domain == "example.com" else None)
    monkeypatch.setattr("app.repositories.dmarc.create_import", create_import)
    monkeypatch.setattr("app.repositories.dmarc.mark_import", mark_import)
    monkeypatch.setattr("app.repositories.dmarc.save_report", save_report)
    monkeypatch.setattr("app.repositories.dmarc.set_import_company", set_import_company)
    monkeypatch.setattr("app.repositories.dmarc.company_by_code", company_by_code)
    monkeypatch.setattr("app.repositories.companies.get_company_by_email_domain", lookup_company)

    xml = XML.replace(b"<domain>example.com</domain>", b"<domain>mail.example.com</domain>")
    created = asyncio.run(
        dmarc.ingest_attachment(
            recipient="dmarc@example.com",
            message_id="msg-1",
            filename="report.xml",
            payload=xml,
            received_at=dmarc.datetime.now(dmarc.timezone.utc),
            company_id=None,
        )
    )

    assert created == [44]
    assert save_report.await_args.args[0] == 77
    assert lookup_company.await_args_list[0].args[0] == "mail.example.com"
    assert lookup_company.await_args_list[1].args[0] == "example.com"
    set_import_company.assert_awaited_once_with(31, 77)
    mark_import.assert_awaited_once()
    assert mark_import.await_args.args[1] == "processed"


def test_ingest_attachment_falls_back_to_mailbox_company(monkeypatch):
    create_import = AsyncMock(return_value=31)
    mark_import = AsyncMock()
    save_report = AsyncMock(return_value=45)
    set_import_company = AsyncMock()
    company_by_code = AsyncMock(return_value=None)
    lookup_company = AsyncMock(return_value=None)
    monkeypatch.setattr("app.repositories.dmarc.create_import", create_import)
    monkeypatch.setattr("app.repositories.dmarc.mark_import", mark_import)
    monkeypatch.setattr("app.repositories.dmarc.save_report", save_report)
    monkeypatch.setattr("app.repositories.dmarc.set_import_company", set_import_company)
    monkeypatch.setattr("app.repositories.dmarc.company_by_code", company_by_code)
    monkeypatch.setattr("app.repositories.companies.get_company_by_email_domain", lookup_company)

    created = asyncio.run(
        dmarc.ingest_attachment(
            recipient="dmarc@example.com",
            message_id="msg-2",
            filename="report.xml",
            payload=XML,
            received_at=dmarc.datetime.now(dmarc.timezone.utc),
            company_id=42,
        )
    )

    assert created == [45]
    assert save_report.await_args.args[0] == 42
    set_import_company.assert_awaited_once_with(31, 42)


def test_ingest_attachment_quarantines_unassigned_policy_domain(monkeypatch):
    create_import = AsyncMock(return_value=31)
    mark_import = AsyncMock()
    save_report = AsyncMock(return_value=45)
    set_import_company = AsyncMock()
    company_by_code = AsyncMock(return_value=None)
    lookup_company = AsyncMock(return_value=None)
    monkeypatch.setattr("app.repositories.dmarc.create_import", create_import)
    monkeypatch.setattr("app.repositories.dmarc.mark_import", mark_import)
    monkeypatch.setattr("app.repositories.dmarc.save_report", save_report)
    monkeypatch.setattr("app.repositories.dmarc.set_import_company", set_import_company)
    monkeypatch.setattr("app.repositories.dmarc.company_by_code", company_by_code)
    monkeypatch.setattr("app.repositories.companies.get_company_by_email_domain", lookup_company)

    created = asyncio.run(
        dmarc.ingest_attachment(
            recipient="dmarc@example.com",
            message_id="msg-3",
            filename="report.xml",
            payload=XML,
            received_at=dmarc.datetime.now(dmarc.timezone.utc),
            company_id=None,
        )
    )

    assert created == []
    save_report.assert_not_awaited()
    set_import_company.assert_awaited_once_with(31, None)
    assert mark_import.await_args.args[1] == "quarantined"
    assert "Policy domain could not be assigned" in (mark_import.await_args.args[2] or "")
