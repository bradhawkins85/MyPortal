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
