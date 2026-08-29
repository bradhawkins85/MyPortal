"""Defensive DMARC aggregate report ingestion (RFC 7489)."""
from __future__ import annotations

import gzip
import hashlib
import io
import re
import secrets
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from pathlib import PurePosixPath
from typing import Any

from defusedxml import ElementTree as ET

CODE_RE = re.compile(r"(?i)^DMARC\+([A-Za-z0-9_-]{16,32})@([A-Za-z0-9.-]+)$")


class DmarcInputError(ValueError):
    """An attachment is unsafe, malformed, or exceeds configured limits."""


@dataclass(frozen=True)
class IngestionLimits:
    compressed_bytes: int = 5 * 1024 * 1024
    expanded_bytes: int = 25 * 1024 * 1024
    attachments: int = 10
    xml_depth: int = 32
    records: int = 100_000


def generate_company_code() -> str:
    """Return 144 bits encoded with the URL/email-safe base64 alphabet."""
    return secrets.token_urlsafe(18)


def reporting_address(code: str, domain: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,32}", code or ""):
        raise ValueError("Invalid DMARC reporting code")
    domain = domain.strip().lower().rstrip(".")
    if not domain or "@" in domain:
        raise ValueError("Invalid DMARC reporting domain")
    return f"DMARC+{code}@{domain}"


def routing_code(recipient: str) -> str | None:
    match = CODE_RE.fullmatch(recipient.strip())
    return match.group(1) if match else None


async def company_reporting_address(company_id: int) -> str | None:
    """Return the address derived from the selected active M365 DMARC mailbox."""
    from app.repositories import dmarc as repo
    from app.repositories import m365_mail_accounts as mailbox_repo
    code = await repo.company_code(company_id)
    mailbox = await mailbox_repo.get_dmarc_account()
    if not code or not mailbox or not mailbox.get("active"):
        return None
    domain = str(mailbox.get("user_principal_name") or "").partition("@")[2]
    return reporting_address(code, domain) if domain else None


def _safe_xml(data: bytes, limits: IngestionLimits) -> bytes:
    if len(data) > limits.expanded_bytes:
        raise DmarcInputError("Expanded attachment exceeds limit")
    return data


def unpack_attachment(filename: str, payload: bytes, limits: IngestionLimits | None = None) -> list[tuple[str, bytes]]:
    limits = limits or IngestionLimits()
    if len(payload) > limits.compressed_bytes:
        raise DmarcInputError("Compressed attachment exceeds limit")
    lower = filename.lower()
    if lower.endswith(".xml"):
        return [(filename, _safe_xml(payload, limits))]
    if lower.endswith((".gz", ".gzip")):
        try:
            data = gzip.decompress(payload)
        except (OSError, EOFError) as exc:
            raise DmarcInputError("Invalid gzip attachment") from exc
        if data.startswith((b"PK\x03\x04", b"\x1f\x8b")):
            raise DmarcInputError("Nested archives are not accepted")
        return [(filename.rsplit(".", 1)[0], _safe_xml(data, limits))]
    if lower.endswith(".zip"):
        output: list[tuple[str, bytes]] = []
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                files = [item for item in archive.infolist() if not item.is_dir()]
                if len(files) > limits.attachments:
                    raise DmarcInputError("Attachment count exceeds limit")
                if sum(item.file_size for item in files) > limits.expanded_bytes:
                    raise DmarcInputError("Expanded archive exceeds limit")
                for item in files:
                    path = PurePosixPath(item.filename.replace("\\", "/"))
                    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
                        raise DmarcInputError("Unsafe archive path")
                    if not item.filename.lower().endswith(".xml"):
                        raise DmarcInputError("Nested/non-XML archive member")
                    data = archive.read(item)
                    if data.startswith((b"PK\x03\x04", b"\x1f\x8b")):
                        raise DmarcInputError("Nested archives are not accepted")
                    output.append((item.filename, _safe_xml(data, limits)))
        except zipfile.BadZipFile as exc:
            raise DmarcInputError("Invalid zip attachment") from exc
        return output
    raise DmarcInputError("Only XML, gzip, and zip attachments are accepted")


def _text(node: Any, path: str, *, required: bool = False) -> str | None:
    value = node.findtext(path)
    value = value.strip() if value else None
    if required and not value:
        raise DmarcInputError(f"Missing required DMARC field: {path}")
    return value


def parse_aggregate_xml(data: bytes, limits: IngestionLimits | None = None) -> dict[str, Any]:
    limits = limits or IngestionLimits()
    _safe_xml(data, limits)
    try:
        root = ET.fromstring(data)
    except Exception as exc:
        raise DmarcInputError("Malformed or unsafe XML") from exc
    if root.tag.split("}")[-1] != "feedback":
        raise DmarcInputError("Root element must be feedback")
    def depth(node: Any, level: int = 1) -> int:
        return max([level, *(depth(child, level + 1) for child in list(node))])
    if depth(root) > limits.xml_depth:
        raise DmarcInputError("XML depth exceeds limit")
    records = root.findall("record")
    if len(records) > limits.records:
        raise DmarcInputError("Record count exceeds limit")
    begin = int(_text(root, "report_metadata/date_range/begin", required=True) or 0)
    end = int(_text(root, "report_metadata/date_range/end", required=True) or 0)
    parsed: dict[str, Any] = {
        "reporter": _text(root, "report_metadata/org_name", required=True),
        "report_id": _text(root, "report_metadata/report_id", required=True),
        "date_begin": datetime.fromtimestamp(begin, timezone.utc),
        "date_end": datetime.fromtimestamp(end, timezone.utc),
        "domain": _text(root, "policy_published/domain", required=True),
        "adkim": _text(root, "policy_published/adkim"), "aspf": _text(root, "policy_published/aspf"),
        "policy": _text(root, "policy_published/p", required=True),
        "subdomain_policy": _text(root, "policy_published/sp"),
        "percentage": int(_text(root, "policy_published/pct") or 100), "records": [],
        "content_sha256": hashlib.sha256(data).hexdigest(),
    }
    for node in records:
        row = node.find("row")
        identifiers = node.find("identifiers")
        if row is None or identifiers is None:
            raise DmarcInputError("Record is missing row or identifiers")
        item = {"source_ip": _text(row, "source_ip", required=True),
                "message_count": int(_text(row, "count", required=True) or 0),
                "disposition": _text(row, "policy_evaluated/disposition", required=True),
                "dkim_result": _text(row, "policy_evaluated/dkim", required=True),
                "spf_result": _text(row, "policy_evaluated/spf", required=True),
                "header_from": _text(identifiers, "header_from", required=True),
                "envelope_from": _text(identifiers, "envelope_from"), "envelope_to": _text(identifiers, "envelope_to"),
                "auth_results": []}
        auth = node.find("auth_results")
        if auth is not None:
            for mechanism in ("dkim", "spf"):
                for result in auth.findall(mechanism):
                    item["auth_results"].append({"mechanism": mechanism, "domain": _text(result, "domain", required=True),
                        "selector": _text(result, "selector"), "scope": _text(result, "scope"),
                        "result": _text(result, "result", required=True), "human_result": _text(result, "human_result")})
        parsed["records"].append(item)
    return parsed


def attachments_from_message(message: Message, limits: IngestionLimits | None = None) -> list[tuple[str, bytes]]:
    limits = limits or IngestionLimits()
    parts = [part for part in message.walk() if part.get_filename()]
    if len(parts) > limits.attachments:
        raise DmarcInputError("Attachment count exceeds limit")
    result: list[tuple[str, bytes]] = []
    for part in parts:
        result.extend(unpack_attachment(str(part.get_filename()), part.get_payload(decode=True) or b"", limits))
    return result


async def ingest_attachment(*, recipient: str, message_id: str, filename: str, payload: bytes,
                            received_at: datetime, metadata: str | None = None,
                            limits: IngestionLimits | None = None) -> list[int]:
    """Persist then process one delivery; unresolved/malformed input is quarantined.

    The recipient is only a routing hint. Once resolved, every repository call
    carries the authoritative company primary key.
    """
    from app.repositories import dmarc as repo
    limits = limits or IngestionLimits()
    attachment_hash = hashlib.sha256(payload).hexdigest()
    code = routing_code(recipient)
    company = await repo.company_by_code(code) if code else None
    company_id = int(company["id"]) if company else None
    import_id = await repo.create_import(company_id=company_id, message_id=message_id,
        attachment_hash=attachment_hash, filename=filename, received_at=received_at.astimezone(timezone.utc), metadata=metadata)
    if company_id is None:
        await repo.mark_import(import_id, "quarantined", "Recipient could not be assigned")
        return []
    created: list[int] = []
    try:
        for _, xml in unpack_attachment(filename, payload, limits):
            report = parse_aggregate_xml(xml, limits)
            created.append(await repo.save_report(company_id, import_id, report, attachment_hash))
        await repo.mark_import(import_id, "processed", content_hash=hashlib.sha256(payload).hexdigest())
    except DmarcInputError as exc:
        await repo.mark_import(import_id, "quarantined", str(exc)[:1000])
    except Exception:
        # Recoverable infrastructure failures retain the persisted import. Do
        # not log the recipient, MIME body, XML, or credentials.
        await repo.mark_import(import_id, "retry", "Temporary processing failure")
        raise
    return created
