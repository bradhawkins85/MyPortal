import gzip
import io
from pathlib import Path
import zipfile
import pytest

from app.services.dmarc import DmarcInputError, IngestionLimits, parse_aggregate_xml, reporting_address, unpack_attachment

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
