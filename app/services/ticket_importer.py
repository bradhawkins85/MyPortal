from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape, unescape
import mimetypes
from pathlib import PurePosixPath
import re
import json
from urllib.parse import unquote, urlparse
from typing import Any

from app.core.logging import log_error, log_info
from app.repositories import assets as assets_repo
from app.repositories import companies as company_repo
from app.repositories import tickets as tickets_repo
from app.repositories import ticket_attachments as attachments_repo
from app.repositories import users as user_repo
from app.repositories import webhook_events as webhook_events_repo
from app.services.sanitization import sanitize_rich_text
from app.services import (
    syncro,
    email_recipients,
    ticket_attachments as attachments_service,
    tickets as tickets_service,
    webhook_monitor,
)

_ALLOWED_PRIORITIES = {"urgent", "high", "normal", "low"}
_ALLOWED_STATUSES = {"open", "in_progress", "pending", "resolved", "closed"}
_DEFAULT_PRIORITY = "normal"
_DEFAULT_STATUS = "open"


async def _get_status_context() -> tuple[set[str], str, dict[str, str]]:
    definitions = await tickets_service.list_status_definitions()
    if definitions:
        slugs = [definition.tech_status for definition in definitions]
        default = next(
            (
                definition.tech_status
                for definition in definitions
                if definition.is_default
            ),
            slugs[0],
        )
    else:
        slugs = list(_ALLOWED_STATUSES)
        default = _DEFAULT_STATUS
    mappings = await _get_syncro_status_mappings(set(slugs))
    return set(slugs), default, mappings


async def _get_syncro_status_mappings(
    allowed_statuses: Collection[str],
) -> dict[str, str]:
    try:
        module = await syncro._load_module_settings()
    except Exception as exc:  # pragma: no cover - defensive fallback
        log_error("Failed to load Syncro ticket status mappings", error=str(exc))
        return {}
    configured = (module or {}).get("ticket_status_mappings") or []
    mappings: dict[str, str] = {}
    if not isinstance(configured, list):
        return mappings
    for item in configured:
        if not isinstance(item, dict):
            continue
        syncro_status = _clean_text(
            item.get("syncro_status") or item.get("syncroStatus")
        )
        myportal_status = _clean_text(
            item.get("myportal_status") or item.get("myportalStatus")
        )
        if not syncro_status or not myportal_status:
            continue
        normalized_myportal = myportal_status.lower().replace(" ", "_")
        if normalized_myportal in allowed_statuses:
            mappings[syncro_status.casefold()] = normalized_myportal
    return mappings


@dataclass(slots=True)
class TicketImportSummary:
    mode: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    skipped_reasons: list[str] | None = None

    def record_skip(self, reason: str) -> None:
        self.skipped += 1
        reason_text = _clean_text(reason) or "Unknown reason"
        if self.skipped_reasons is None:
            self.skipped_reasons = []
        self.skipped_reasons.append(reason_text)

    def record(self, outcome: str, reason: str | None = None) -> None:
        if outcome == "created":
            self.created += 1
        elif outcome == "updated":
            self.updated += 1
        else:
            if reason is None and outcome.startswith("skipped:"):
                reason = outcome.split(":", 1)[1].strip()
            self.record_skip(reason or "Ticket could not be imported")

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "skipped_reasons": self.skipped_reasons or [],
        }


_HTML_NEWLINE_TAGS = re.compile(
    r"<\s*(?:br\s*/?|/(?:p|div|li|tr|table|thead|tbody|tfoot|section|article|header|footer|h[1-6]))\b[^>]*>",
    flags=re.IGNORECASE,
)
_HTML_TAGS = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    normalised = unescape(text.replace("\r\n", "\n")).replace("\xa0", " ")
    normalised = _HTML_NEWLINE_TAGS.sub("\n", normalised)
    normalised = _HTML_TAGS.sub("", normalised)
    normalised = re.sub(r"[\t ]*\n[\t ]*", "\n", normalised)
    normalised = re.sub(r"\n{2,}", "\n", normalised)
    normalised = normalised.strip()
    return normalised or None


def _normalise_status(
    value: Any,
    allowed_statuses: Collection[str],
    default_status: str,
    status_mappings: dict[str, str] | None = None,
) -> str:
    if isinstance(value, dict):
        for key in ("name", "status", "label"):
            text_value = value.get(key)
            if text_value:
                value = text_value
                break
    text = _clean_text(value)
    if not text:
        return default_status
    mapped = (status_mappings or {}).get(text.casefold())
    if mapped in allowed_statuses:
        return mapped
    normalized = text.lower().replace(" ", "_")
    if normalized in allowed_statuses:
        return normalized
    for candidate in allowed_statuses:
        if "progress" in normalized and "progress" in candidate:
            return candidate
    for candidate in allowed_statuses:
        if ("pend" in normalized or "wait" in normalized) and (
            "pend" in candidate or "wait" in candidate
        ):
            return candidate
    for candidate in allowed_statuses:
        if (
            "resolv" in normalized or "complete" in normalized
        ) and "resolv" in candidate:
            return candidate
    for candidate in allowed_statuses:
        if "clos" in normalized and "clos" in candidate:
            return candidate
    return default_status


def _normalise_priority(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "priority", "label"):
            text_value = value.get(key)
            if text_value:
                value = text_value
                break
    text = _clean_text(value)
    if not text:
        return _DEFAULT_PRIORITY
    normalized = text.lower().replace(" ", "_")
    if normalized in _ALLOWED_PRIORITIES:
        return normalized
    if "emer" in normalized or "crit" in normalized:
        return "urgent"
    if "high" in normalized:
        return "high"
    if "low" in normalized:
        return "low"
    return _DEFAULT_PRIORITY


def _parse_datetime(value: Any) -> datetime | None:
    if value in {None, "", 0}:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _ticket_updated_at(ticket: dict[str, Any]) -> datetime | None:
    return _parse_datetime(
        ticket.get("updated_at")
        or ticket.get("updated_on")
        or ticket.get("updated")
        or ticket.get("updated_at_utc")
    )


def _existing_syncro_updated_at(existing: dict[str, Any]) -> datetime | None:
    """Return the last Syncro update timestamp stored for an imported ticket.

    Older imports predate ``syncro_updated_at`` and stored Syncro's
    ``updated_at`` in the main ticket timestamp, so fall back to that value once.
    """
    return _parse_datetime(existing.get("syncro_updated_at")) or _parse_datetime(
        existing.get("updated_at")
    )


def _syncro_ticket_is_unchanged(
    ticket: dict[str, Any], existing: dict[str, Any] | None
) -> bool:
    if not existing:
        return False
    syncro_updated_at = _ticket_updated_at(ticket)
    if syncro_updated_at is None:
        return False
    last_imported_syncro_updated_at = _existing_syncro_updated_at(existing)
    if last_imported_syncro_updated_at is None:
        return False
    return syncro_updated_at <= last_imported_syncro_updated_at


def _normalise_comment_comparison_text(value: str | None) -> str:
    """Return comment text normalised for rich-preview/body comparisons."""

    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip().casefold()


def _append_full_plain_body_to_rich_preview(
    rich_html: str, rich_text: str, plain_body: str | None
) -> str:
    """Ensure a truncated Syncro rich preview is supplemented with full text.

    Syncro's ``rich_text_preview`` can preserve useful HTML, including images,
    but it is sometimes only a preview of the full comment.  When the plain
    ``body`` contains additional text, keep the rich preview and append the full
    body as escaped preformatted text so no ticket content is lost.
    """

    if not plain_body:
        return rich_html

    normalised_rich = _normalise_comment_comparison_text(rich_text)
    normalised_plain = _normalise_comment_comparison_text(plain_body)
    if not normalised_plain or normalised_plain in normalised_rich:
        return rich_html

    escaped_body = escape(plain_body)
    return (
        f"{rich_html}"
        '<hr />'
        '<div class="syncro-full-body">'
        '<p><strong>Full ticket body from Syncro:</strong></p>'
        f"<pre>{escaped_body}</pre>"
        '</div>'
    )


def _extract_comment_body(comment: dict[str, Any]) -> str | None:
    """Return the best available formatted Syncro comment body.

    Syncro comments can include ``rich_text_preview`` with the original HTML
    formatting and inline image tags, while ``body`` contains the full plain
    text.  Keep useful rich content when it is present, but append the full
    plain-text body whenever the rich preview is truncated so the import never
    loses ticket content.
    """

    plain_body = _clean_text(
        comment.get("body")
        or comment.get("comment")
        or comment.get("text")
        or comment.get("content")
    )
    for rich_key in ("rich_text_preview", "richTextPreview"):
        rich_body = comment.get(rich_key)
        if rich_body is not None:
            text = unescape(str(rich_body).replace("\r\n", "\n")).replace("\xa0", " ")
            sanitized = sanitize_rich_text(text)
            if sanitized.has_rich_content:
                return _append_full_plain_body_to_rich_preview(
                    sanitized.html, sanitized.text_content, plain_body
                )
    return plain_body


_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
}
_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


_COMMENT_IMAGE_URL_KEYS = (
    "url",
    "download_url",
    "downloadUrl",
    "file_url",
    "fileUrl",
    "attachment_url",
    "attachmentUrl",
    "content_url",
    "contentUrl",
    "mapped_content_url",
    "mappedContentUrl",
    "inline_url",
    "inlineUrl",
    "original_url",
    "originalUrl",
    "full_url",
    "fullUrl",
    "href",
)
_COMMENT_IMAGE_FILENAME_KEYS = (
    "filename",
    "file_name",
    "fileName",
    "name",
    "title",
    "alt",
)
_COMMENT_IMAGE_MIME_KEYS = ("content_type", "contentType", "mime_type", "mimeType")


def _extract_comment_image_candidates(
    comment: dict[str, Any],
) -> list[dict[str, str | None]]:
    """Return image attachment candidates embedded in a Syncro comment."""
    candidates: list[dict[str, str | None]] = []
    seen: set[tuple[str, str]] = set()

    def add(url: Any, filename: Any = None, content_type: Any = None) -> None:
        url_text = str(url or "").strip()
        if not url_text:
            return
        mime = _clean_text(content_type)
        if mime and ";" in mime:
            mime = mime.split(";", 1)[0].strip().lower()
        if mime and not mime.startswith("image/"):
            return
        filename_text = _clean_text(filename)
        guessed_type, _ = mimetypes.guess_type(filename_text or url_text)
        if not mime and guessed_type and guessed_type.startswith("image/"):
            mime = guessed_type
        if not mime and filename_text and "." in filename_text:
            guessed_from_name, _ = mimetypes.guess_type(filename_text)
            if guessed_from_name and not guessed_from_name.startswith("image/"):
                return
        key = (url_text, filename_text or "")
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            {"url": url_text, "filename": filename_text, "mime_type": mime}
        )

    def scan(value: Any) -> None:
        if isinstance(value, dict):
            url = next(
                (value.get(key) for key in _COMMENT_IMAGE_URL_KEYS if value.get(key)),
                None,
            )
            if url:
                filename = next(
                    (
                        value.get(key)
                        for key in _COMMENT_IMAGE_FILENAME_KEYS
                        if value.get(key)
                    ),
                    None,
                )
                content_type = next(
                    (
                        value.get(key)
                        for key in _COMMENT_IMAGE_MIME_KEYS
                        if value.get(key)
                    ),
                    None,
                )
                add(url, filename, content_type)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    scan(nested)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add(item)
                elif isinstance(item, (dict, list)):
                    scan(item)

    for key in (
        "attachments",
        "files",
        "uploads",
        "images",
        "inline_images",
        "inlineImages",
    ):
        scan(comment.get(key))

    for body_key in (
        "rich_text_preview",
        "richTextPreview",
        "body",
        "html_body",
        "htmlBody",
        "comment",
        "text",
        "content",
    ):
        body = comment.get(body_key)
        if not isinstance(body, str) or "<img" not in body.lower():
            continue
        for match in re.finditer(
            r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>",
            body,
            flags=re.IGNORECASE,
        ):
            tag = match.group(0)
            filename_match = re.search(
                r"\b(?:alt|title)=[\"']([^\"']+)[\"']",
                tag,
                flags=re.IGNORECASE,
            )
            add(
                unescape(match.group(1)),
                unescape(filename_match.group(1)) if filename_match else None,
            )
    return candidates


def _filename_for_image_candidate(
    candidate: dict[str, str | None], content_type: str | None, index: int
) -> str:
    filename = (candidate.get("filename") or "").strip()
    if filename and filename.lower() not in {"[embedded image]", "embedded image"}:
        name = PurePosixPath(unquote(urlparse(filename).path)).name or filename
    else:
        parsed_name = PurePosixPath(
            unquote(urlparse(candidate.get("url") or "").path)
        ).name
        name = parsed_name or f"syncro-comment-image-{index}"
    if "." not in name:
        ext = _IMAGE_EXTENSIONS.get(
            (content_type or candidate.get("mime_type") or "").lower(), ".img"
        )
        name = f"{name}{ext}"
    return name[:255]


async def _import_comment_images(
    ticket_id: int, comment: dict[str, Any], author_id: int | None
) -> list[dict[str, Any]]:
    candidates = _extract_comment_image_candidates(comment)
    imported: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        try:
            contents, downloaded_type = await syncro.download_file(
                candidate["url"] or ""
            )
            content_type = (downloaded_type or candidate.get("mime_type") or "").split(
                ";", 1
            )[0].strip().lower() or None
            if content_type not in _IMAGE_MIME_TYPES:
                guessed, _ = mimetypes.guess_type(candidate.get("url") or "")
                content_type = guessed if guessed in _IMAGE_MIME_TYPES else content_type
            if content_type not in _IMAGE_MIME_TYPES:
                log_error(
                    "Skipping Syncro embedded image with unsupported MIME type",
                    ticket_id=ticket_id,
                    mime_type=content_type,
                )
                continue
            attachment = await attachments_service.save_file_bytes(
                ticket_id=ticket_id,
                contents=contents,
                original_filename=_filename_for_image_candidate(
                    candidate, content_type, index
                ),
                mime_type=content_type,
                access_level="closed",
                uploaded_by_user_id=author_id,
            )
            if attachment:
                imported_attachment = dict(attachment)
                imported_attachment["syncro_source_url"] = candidate.get("url")
                imported.append(imported_attachment)
        except (
            Exception
        ) as exc:  # pragma: no cover - import should continue if an image fails
            log_error(
                "Failed to import Syncro embedded image",
                ticket_id=ticket_id,
                url=candidate.get("url"),
                error=str(exc),
            )
    return imported


def _build_imported_image_markup(
    ticket_id: int, attachments: list[dict[str, Any]]
) -> str:
    """Build inline image HTML for imported Syncro image attachments."""
    image_tags: list[str] = []
    for attachment in attachments:
        try:
            attachment_id = int(attachment.get("id"))
        except (TypeError, ValueError):
            continue
        mime_type = str(attachment.get("mime_type") or "").split(";", 1)[0].lower()
        if mime_type not in _IMAGE_MIME_TYPES:
            continue
        alt_text = escape(
            str(attachment.get("original_filename") or "Syncro embedded image"),
            quote=True,
        )
        src = f"/api/tickets/{ticket_id}/attachments/{attachment_id}/download"
        image_tags.append(
            f'<figure class="syncro-embedded-image"><img src="{src}" '
            f'alt="{alt_text}" loading="lazy"></figure>'
        )
    if not image_tags:
        return ""
    return '<div class="syncro-embedded-images">' + "".join(image_tags) + "</div>"


def _attachment_download_src(ticket_id: int, attachment: dict[str, Any]) -> str | None:
    try:
        attachment_id = int(attachment.get("id"))
    except (TypeError, ValueError):
        return None
    return f"/api/tickets/{ticket_id}/attachments/{attachment_id}/download"


def _replace_imported_image_sources(
    body: str, ticket_id: int, attachments: list[dict[str, Any]]
) -> tuple[str, set[int]]:
    """Replace Syncro-hosted inline image URLs with local attachment URLs."""
    source_map = {
        str(attachment.get("syncro_source_url")): (index, attachment)
        for index, attachment in enumerate(attachments)
        if attachment.get("syncro_source_url")
    }
    replaced_indexes: set[int] = set()

    def replace_src(match: re.Match[str]) -> str:
        src = unescape(match.group(2))
        mapped = source_map.get(src)
        if not mapped:
            return match.group(0)
        index, attachment = mapped
        local_src = _attachment_download_src(ticket_id, attachment)
        if not local_src:
            return match.group(0)
        replaced_indexes.add(index)
        return f"{match.group(1)}{escape(local_src, quote=True)}{match.group(3)}"

    replaced = re.sub(
        r'(<img\b[^>]*\bsrc=["\'])([^"\']+)(["\'])',
        replace_src,
        body,
        flags=re.IGNORECASE,
    )
    return replaced, replaced_indexes


def _append_imported_image_markup(
    body: str, ticket_id: int, attachments: list[dict[str, Any]]
) -> str:
    if not attachments:
        return body
    body_with_local_images, replaced_indexes = _replace_imported_image_sources(
        body, ticket_id, attachments
    )
    remaining = [
        attachment
        for index, attachment in enumerate(attachments)
        if index not in replaced_indexes
    ]
    image_markup = _build_imported_image_markup(ticket_id, remaining)
    cleaned = re.sub(
        r"\[embedded image\]", "", body_with_local_images, flags=re.IGNORECASE
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip() or body_with_local_images
    if not image_markup:
        return cleaned
    separator = "\n\n" if cleaned else ""
    return f"{cleaned}{separator}{image_markup}"


def _extract_comment_author_name(comment: dict[str, Any]) -> str | None:
    """Return the display name for the comment author."""
    for key in (
        "tech_name",
        "techName",
        "author_name",
        "authorName",
        "user_name",
        "userName",
    ):
        name = _clean_text(comment.get(key))
        if name:
            return name
    tech = _clean_text(comment.get("tech"))
    if tech and tech.lower() != "customer-reply":
        return tech
    for key in ("user", "author", "created_by", "creator"):
        nested = comment.get(key)
        if isinstance(nested, dict):
            name = _clean_text(
                nested.get("name")
                or nested.get("full_name")
                or nested.get("display_name")
            )
            if name:
                return name
    return None


def _extract_time_worked_minutes(comment: dict[str, Any]) -> int | None:
    """Parse time_worked (HH:MM or HH:MM:SS) or time_cost_hours into whole minutes."""
    time_worked = comment.get("time_worked") or comment.get("timeWorked")
    if time_worked:
        text = str(time_worked).strip()
        match = re.match(r"^(\d+):(\d{2})(?::(\d{2}))?$", text)
        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            seconds = int(match.group(3) or 0)
            total = hours * 60 + minutes + (1 if seconds >= 30 else 0)
            if total > 0:
                return total
    for key in ("time_cost_hours", "timeCostHours", "hours_worked", "hoursWorked"):
        value = comment.get(key)
        if value is not None:
            try:
                total = round(float(value) * 60)
                if total > 0:
                    return total
            except (TypeError, ValueError):
                continue
    return None


def _resolve_comment_billable(
    comment: dict[str, Any],
    timer_billable: dict[str, bool] | None = None,
) -> bool:
    """Return whether a comment's time entry is billable.

    Checks the ticket_timers mapping first (keyed by comment ID) because
    Syncro stores the authoritative billable flag on the labor log (timer)
    entry rather than on the comment object itself.  Falls back to the
    comment's own fields when no timer entry exists for this comment.
    """
    if timer_billable:
        comment_id = comment.get("id")
        if comment_id is not None:
            timer_val = timer_billable.get(str(comment_id))
            if timer_val is not None:
                return timer_val
    for key in ("billable", "is_billable"):
        val = comment.get(key)
        if val is not None:
            return _coerce_bool(val)
    return False


def _build_timer_billable_map(ticket: dict[str, Any]) -> dict[str, bool]:
    """Build a {comment_id_str: billable} mapping from ticket_timers."""
    timers = ticket.get("ticket_timers")
    if not isinstance(timers, list):
        return {}
    result: dict[str, bool] = {}
    for timer in timers:
        if not isinstance(timer, dict):
            continue
        comment_id = timer.get("comment_id")
        if comment_id is None:
            continue
        billable = timer.get("billable")
        if billable is not None:
            result[str(comment_id)] = _coerce_bool(billable)
    return result


def _build_timer_time_map(ticket: dict[str, Any]) -> dict[str, int]:
    """Build a {comment_id_str: minutes} mapping from ticket_timers billable_time.

    Used as a fallback when a comment object does not carry its own time_worked
    field.  Syncro stores the billable (chargeable) duration on the labor log
    (timer) entry as whole seconds; the value is converted to whole minutes
    (seconds >= 30 round up to the next minute).
    """
    timers = ticket.get("ticket_timers")
    if not isinstance(timers, list):
        return {}
    result: dict[str, int] = {}
    for timer in timers:
        if not isinstance(timer, dict):
            continue
        comment_id = timer.get("comment_id")
        if comment_id is None:
            continue
        billable_time = timer.get("billable_time")
        if billable_time is not None:
            try:
                total_seconds = int(billable_time)
                minutes = total_seconds // 60 + (1 if total_seconds % 60 >= 30 else 0)
                if minutes > 0:
                    result[str(comment_id)] = minutes
            except (TypeError, ValueError):
                continue
    return result


def _build_comment_body_with_header(
    comment: dict[str, Any],
    body: str,
    *,
    minutes: int | None = None,
) -> str:
    """Prepend author and time metadata to a comment body."""
    header_parts: list[str] = []
    author_name = _extract_comment_author_name(comment)
    if author_name:
        header_parts.append(f"Author: {author_name}")
    if minutes is None:
        minutes = _extract_time_worked_minutes(comment)
    if minutes is not None:
        hours, mins = divmod(minutes, 60)
        if hours and mins:
            time_str = f"{hours}h {mins}m"
        elif hours:
            time_str = f"{hours}h"
        else:
            time_str = f"{mins}m"
        header_parts.append(f"Time: {time_str}")
    if not header_parts:
        return body
    return "\n".join(header_parts) + "\n---\n" + body


def _extract_contact_info(ticket: dict[str, Any]) -> dict[str, Any]:
    """Extract contact details from a Syncro ticket."""
    info: dict[str, Any] = {}
    contact = ticket.get("contact")
    if isinstance(contact, dict):
        for field in ("name", "email", "phone", "mobile", "address", "address_2"):
            value = _clean_text(contact.get(field))
            if value:
                info[field] = value
    if "name" not in info:
        for key in ("contact_name", "contactName", "requester_name"):
            value = _clean_text(ticket.get(key))
            if value:
                info["name"] = value
                break
    if "phone" not in info:
        for key in ("contact_phone", "contactPhone"):
            value = _clean_text(ticket.get(key))
            if value:
                info["phone"] = value
                break
    return info


def _extract_custom_fields(ticket: dict[str, Any]) -> list[dict[str, str]]:
    """Extract custom fields from a Syncro ticket."""
    for key in ("custom_fields", "customFields"):
        fields = ticket.get(key)
        if isinstance(fields, list):
            result: list[dict[str, str]] = []
            for field in fields:
                if not isinstance(field, dict):
                    continue
                label = _clean_text(
                    field.get("name") or field.get("label") or field.get("key")
                )
                value = _clean_text(field.get("value") or field.get("val"))
                if label and value:
                    result.append({"label": label, "value": value})
            return result
    return []


def _extract_ticket_assets(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract asset records embedded in a Syncro ticket payload."""
    for key in ("assets", "asset_list", "assetList"):
        assets = ticket.get(key)
        if isinstance(assets, list) and assets:
            result: list[dict[str, Any]] = []
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                name = _clean_text(asset.get("name"))
                asset_tag = _clean_text(asset.get("asset_tag") or asset.get("assetTag"))
                serial = _clean_text(
                    asset.get("serial_number") or asset.get("serialNumber")
                )
                if name or asset_tag or serial:
                    result.append(
                        {"name": name, "asset_tag": asset_tag, "serial_number": serial}
                    )
            return result
    return []


async def _link_syncro_ticket_assets(
    ticket_db_id: int,
    ticket: dict[str, Any],
    company_id: int | None,
) -> None:
    """Match Syncro ticket assets to MyPortal assets by name and link them to the ticket."""
    syncro_assets = _extract_ticket_assets(ticket)
    if not syncro_assets or company_id is None:
        return

    company_assets = await assets_repo.list_company_assets(company_id)
    if not company_assets:
        return

    asset_by_name: dict[str, int] = {
        str(a.get("name") or "").strip().lower(): int(a["id"])
        for a in company_assets
        if a.get("id") and a.get("name")
    }

    matched_ids: list[int] = []
    for syncro_asset in syncro_assets:
        name = (syncro_asset.get("name") or "").strip().lower()
        if name and name in asset_by_name:
            asset_id = asset_by_name[name]
            if asset_id not in matched_ids:
                matched_ids.append(asset_id)

    if matched_ids:
        await tickets_repo.replace_ticket_assets(ticket_db_id, matched_ids)


def _build_ticket_metadata_note(ticket: dict[str, Any]) -> str | None:
    """Build a formatted system note with contact info, assets, and custom fields."""
    lines: list[str] = []

    contact_info = _extract_contact_info(ticket)
    if contact_info:
        lines.append("=== Assigned Contact ===")
        if contact_info.get("name"):
            lines.append(f"Name: {contact_info['name']}")
        if contact_info.get("email"):
            lines.append(f"Email: {contact_info['email']}")
        if contact_info.get("phone"):
            lines.append(f"Phone: {contact_info['phone']}")
        if contact_info.get("mobile"):
            lines.append(f"Mobile: {contact_info['mobile']}")
        addr = contact_info.get("address")
        if addr:
            if contact_info.get("address_2"):
                addr = f"{addr}, {contact_info['address_2']}"
            lines.append(f"Address: {addr}")

    assets = _extract_ticket_assets(ticket)
    if assets:
        lines.append("")
        lines.append("=== Associated Assets ===")
        for asset in assets:
            parts: list[str] = []
            if asset.get("name"):
                parts.append(asset["name"])
            if asset.get("asset_tag"):
                parts.append(f"Tag: {asset['asset_tag']}")
            if asset.get("serial_number"):
                parts.append(f"S/N: {asset['serial_number']}")
            if parts:
                lines.append(" | ".join(parts))

    custom_fields = _extract_custom_fields(ticket)
    if custom_fields:
        lines.append("")
        lines.append("=== Custom Fields ===")
        for field in custom_fields:
            lines.append(f"{field['label']}: {field['value']}")

    if not lines:
        return None
    return "\n".join(lines)


def _extract_ticket_attachment_candidates(
    ticket: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract top-level Syncro ticket attachment download candidates.

    Syncro exposes multiple URLs for a single attachment (original, thumb, and
    main).  Only the original ``file.url`` is imported so thumbnail/preview URLs
    are not saved as duplicate attachments.
    """
    attachments = ticket.get("attachments")
    if not isinstance(attachments, list):
        return []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        file_payload = attachment.get("file")
        url: Any = None
        if isinstance(file_payload, dict):
            url = file_payload.get("url")
        elif isinstance(file_payload, str):
            url = file_payload
        url_text = str(url or "").strip()
        if not url_text:
            continue
        filename = _clean_text(
            attachment.get("file_name")
            or attachment.get("filename")
            or attachment.get("name")
        )
        if not filename:
            filename = (
                PurePosixPath(unquote(urlparse(url_text).path)).name
                or "syncro-attachment"
            )
        content_type = _clean_text(
            attachment.get("content_type")
            or attachment.get("contentType")
            or attachment.get("mime_type")
            or attachment.get("mimeType")
        )
        if content_type and ";" in content_type:
            content_type = content_type.split(";", 1)[0].strip().lower()
        file_size: int | None = None
        try:
            raw_size = (
                attachment.get("file_size")
                or attachment.get("fileSize")
                or attachment.get("size")
            )
            if raw_size is not None:
                file_size = int(raw_size)
        except (TypeError, ValueError):
            file_size = None
        key = (
            str(attachment.get("id") or attachment.get("md5") or ""),
            filename.casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "url": url_text,
                "filename": filename[:255],
                "content_type": content_type,
                "file_size": file_size,
                "syncro_id": attachment.get("id"),
            }
        )
    return candidates


async def _sync_ticket_attachments(ticket_id: int, ticket: dict[str, Any]) -> None:
    """Import top-level Syncro ticket attachments without duplicate copies."""
    candidates = _extract_ticket_attachment_candidates(ticket)
    if not candidates:
        return
    try:
        existing = await attachments_repo.list_attachments(ticket_id)
    except (
        Exception
    ) as exc:  # pragma: no cover - import should continue when lookup fails
        log_error(
            "Failed to fetch existing ticket attachments",
            ticket_id=ticket_id,
            error=str(exc),
        )
        existing = []
    existing_keys = {
        (
            str(item.get("original_filename") or "").casefold(),
            int(item.get("file_size") or 0),
        )
        for item in existing
    }
    imported_keys = set(existing_keys)
    for candidate in candidates:
        candidate_size = int(candidate.get("file_size") or 0)
        pre_download_key = (
            str(candidate.get("filename") or "").casefold(),
            candidate_size,
        )
        if candidate_size and pre_download_key in imported_keys:
            continue
        try:
            contents, downloaded_type = await syncro.download_file(
                candidate["url"] or ""
            )
            content_type = (
                downloaded_type or candidate.get("content_type") or ""
            ).split(";", 1)[0].strip().lower() or None
            post_download_key = (
                str(candidate.get("filename") or "").casefold(),
                len(contents),
            )
            if post_download_key in imported_keys:
                continue
            attachment = await attachments_service.save_file_bytes(
                ticket_id=ticket_id,
                contents=contents,
                original_filename=str(candidate.get("filename") or "syncro-attachment"),
                mime_type=content_type,
                access_level="closed",
                uploaded_by_user_id=None,
            )
            if attachment:
                imported_keys.add(post_download_key)
        except (
            Exception
        ) as exc:  # pragma: no cover - import should continue if an attachment fails
            log_error(
                "Failed to import Syncro ticket attachment",
                ticket_id=ticket_id,
                syncro_attachment_id=candidate.get("syncro_id"),
                filename=candidate.get("filename"),
                error=str(exc),
            )


def _extract_comment_subject(comment: dict[str, Any]) -> str | None:
    for key in ("subject", "title", "summary"):
        candidate = _clean_text(comment.get(key))
        if candidate:
            return candidate
    return None


def _extract_description(ticket: dict[str, Any]) -> str | None:
    for key in ("problem", "description", "issue", "body", "notes"):
        candidate = _clean_text(ticket.get(key))
        if candidate:
            return candidate
    for comment in _extract_comments(ticket):
        subject = _extract_comment_subject(comment)
        if subject and subject.lower() == "initial issue":
            body = _extract_comment_body(comment)
            if body:
                return body
    return None


def _extract_ticket_number(ticket: dict[str, Any]) -> str | None:
    candidates = [
        ticket.get("ticket_number"),
        ticket.get("ticketNumber"),
        ticket.get("number"),
        ticket.get("ticket_no"),
        ticket.get("ticketNo"),
    ]
    for candidate in candidates:
        text = _clean_text(candidate)
        if text:
            return text
    fallback = ticket.get("id")
    return str(fallback) if fallback is not None else None


def _extract_numeric_ticket_id(ticket: dict[str, Any]) -> int | None:
    """
    Extract a numeric ticket ID from the Syncro ticket data.
    This is used to set the database ID when importing Syncro tickets.
    """
    # First try the 'number' field which is the actual ticket number from Syncro
    ticket_number = _extract_ticket_number(ticket)
    if ticket_number:
        try:
            # Try to parse as integer
            return int(ticket_number)
        except (ValueError, TypeError):
            # If the ticket number is not purely numeric, try to extract digits
            import re

            digits = re.sub(r"\D", "", ticket_number)
            if digits:
                try:
                    return int(digits)
                except (ValueError, TypeError):
                    pass

    # Fall back to the Syncro ID if number parsing fails
    syncro_id = ticket.get("id")
    if syncro_id is not None:
        try:
            return int(syncro_id)
        except (ValueError, TypeError):
            pass

    return None


def _iter_company_name_candidates(ticket: dict[str, Any]):
    fields = [
        ticket.get("customer_business_then_name"),
        ticket.get("business_then_name"),
        ticket.get("customer_business_name"),
        ticket.get("customer_name"),
    ]
    customer = ticket.get("customer")
    if isinstance(customer, dict):
        fields.extend(
            [
                customer.get("business_then_name"),
                customer.get("business_name"),
                customer.get("name"),
                customer.get("company_name"),
            ]
        )
    for field in fields:
        text = _clean_text(field)
        if not text:
            continue
        yield text
        segments = [
            segment.strip()
            for segment in re.split(r"\s*[-–—]\s*", text)
            if segment.strip()
        ]
        if segments:
            yield segments[0]


def _extract_syncro_company_ids(ticket: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    syncro_ids: list[str] = []
    keys = ("customer_id", "customerId", "customerid", "client_id")
    for key in keys:
        value = ticket.get(key)
        text = _clean_text(value)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            syncro_ids.append(text)
    customer = ticket.get("customer")
    if isinstance(customer, dict):
        for key in ("id", "customer_id"):
            value = customer.get(key)
            text = _clean_text(value)
            if not text:
                continue
            if text not in seen:
                seen.add(text)
                syncro_ids.append(text)
    return syncro_ids


def _normalise_email(value: Any) -> str | None:
    text = _clean_text(value)
    if not text or "@" not in text:
        return None
    return text


def _extract_contact_email(ticket: dict[str, Any]) -> str | None:
    contact = ticket.get("contact")
    if isinstance(contact, dict):
        for key in ("email", "primary_email", "contact_email"):
            email = _normalise_email(contact.get(key))
            if email:
                return email
    for key in ("contact_email", "contactEmail", "customer_email", "email"):
        email = _normalise_email(ticket.get(key))
        if email:
            return email
    return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = _clean_text(value)
    if not text:
        return False
    return text.lower() in {"1", "true", "yes", "y", "t"}


def _extract_comments(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("comments", "ticket_comments", "ticketComments"):
        comments = ticket.get(key)
        if isinstance(comments, list):
            return [comment for comment in comments if isinstance(comment, dict)]
    return []


def _extract_destination_emails(comment: dict[str, Any]) -> set[str]:
    raw = comment.get("destination_emails") or comment.get("destinationEmails")
    emails: set[str] = set()

    def _add(candidate: Any) -> None:
        email = _normalise_email(candidate)
        if email:
            emails.add(email)

    if isinstance(raw, str):
        for segment in re.split(r"[,;\s]+", raw):
            _add(segment)
    elif isinstance(raw, dict):
        for key in ("email", "address", "value"):
            _add(raw.get(key))
    elif isinstance(raw, (list, tuple, set)):
        for item in raw:
            if isinstance(item, dict):
                for key in ("email", "address", "value"):
                    _add(item.get(key))
            else:
                _add(item)
    return emails


def _gather_comment_watchers(comments: list[dict[str, Any]]) -> set[str]:
    watchers: dict[str, str] = {}
    for comment in comments:
        for email in _extract_destination_emails(comment):
            key = email.lower()
            if key not in watchers:
                watchers[key] = email
    return set(watchers.values())


def _should_comment_be_internal(comment: dict[str, Any]) -> bool:
    tech = _clean_text(comment.get("tech"))
    if tech and tech.lower() == "customer-reply":
        return False
    return _coerce_bool(comment.get("hidden"))


async def _resolve_user_id_by_email(email: str | None) -> int | None:
    if not email:
        return None
    try:
        user = await user_repo.get_user_by_email(email)
    except RuntimeError as exc:  # pragma: no cover - defensive logging
        log_error("Failed to resolve user from email", email=email, error=str(exc))
        return None
    if not user or user.get("id") is None:
        return None
    try:
        return int(user["id"])
    except (TypeError, ValueError):
        return None


def _extract_comment_author_email(comment: dict[str, Any]) -> str | None:
    for key in (
        "email_sender",
        "emailSender",
        "user_email",
        "userEmail",
        "author_email",
        "authorEmail",
        "from_email",
        "fromEmail",
        "email",
        "sender",
        "sender_email",
        "senderEmail",
        "reply_to",
        "replyTo",
        "tech_email",
        "techEmail",
    ):
        email = _normalise_email(comment.get(key))
        if email:
            return email
    tech_email = _normalise_email(comment.get("tech"))
    if tech_email:
        return tech_email
    for key in ("user", "author", "created_by", "creator"):
        nested = comment.get(key)
        if isinstance(nested, dict):
            for nested_key in ("email", "user_email", "address", "value"):
                email = _normalise_email(nested.get(nested_key))
                if email:
                    return email
    return None


async def _resolve_comment_author_id(
    comment: dict[str, Any],
    *,
    requester_id: int | None,
    contact_email: str | None,
    cache: dict[str, int | None],
) -> int | None:
    tech = _clean_text(comment.get("tech"))
    if tech and tech.lower() == "customer-reply":
        email = _extract_comment_author_email(comment)
        if email:
            key = email.lower()
            if key not in cache:
                cache[key] = await _resolve_user_id_by_email(email)
            if cache[key] is not None:
                return cache[key]
        if requester_id is not None:
            return requester_id
        if contact_email:
            key = contact_email.lower()
            if key not in cache:
                cache[key] = await _resolve_user_id_by_email(contact_email)
            return cache[key]
        return None
    email = _extract_comment_author_email(comment)
    if not email:
        return None
    key = email.lower()
    if key not in cache:
        cache[key] = await _resolve_user_id_by_email(email)
    return cache[key]


async def _record_imported_reply_recipients(
    reply: dict[str, Any] | None,
    comment: dict[str, Any],
    sent_at: datetime | None,
) -> None:
    """Persist Syncro destination_emails as ticket reply recipients."""
    reply_id = (reply or {}).get("id")
    if reply_id is None:
        return
    recipients = sorted(
        _extract_destination_emails(comment), key=lambda value: value.lower()
    )
    if not recipients:
        return
    try:
        await email_recipients.record_recipients(
            reply_id=int(reply_id),
            tracking_id=None,
            smtp2go_message_id=None,
            to=recipients,
            sent_at=sent_at,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to record imported Syncro reply recipients",
            reply_id=reply_id,
            error=str(exc),
        )


async def _sync_ticket_replies(
    ticket_id: int,
    comments: list[dict[str, Any]],
    *,
    requester_id: int | None,
    contact_email: str | None,
    timer_billable: dict[str, bool] | None = None,
    timer_time: dict[str, int] | None = None,
) -> None:
    if not comments:
        return
    try:
        existing = await tickets_repo.list_replies(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to fetch existing ticket replies",
            ticket_id=ticket_id,
            error=str(exc),
        )
        existing = []
    known_refs = {
        str(reply.get("external_reference"))
        for reply in existing
        if reply.get("external_reference") is not None
    }
    author_cache: dict[str, int | None] = {}
    for comment in comments:
        body = _extract_comment_body(comment)
        if not body:
            continue
        external_ref_raw = (
            comment.get("id")
            or comment.get("comment_id")
            or comment.get("commentId")
            or comment.get("guid")
        )
        external_ref = str(external_ref_raw) if external_ref_raw is not None else None
        if external_ref and external_ref in known_refs:
            continue
        created_at = _parse_datetime(
            comment.get("created_at")
            or comment.get("created_on")
            or comment.get("created")
            or comment.get("updated_at")
        )
        is_internal = _should_comment_be_internal(comment)
        author_id = await _resolve_comment_author_id(
            comment,
            requester_id=requester_id,
            contact_email=contact_email,
            cache=author_cache,
        )
        minutes_spent = _extract_time_worked_minutes(comment)
        if minutes_spent is None and timer_time:
            minutes_spent = timer_time.get(str(comment.get("id")))
        is_billable = _resolve_comment_billable(comment, timer_billable)
        imported_images = await _import_comment_images(ticket_id, comment, author_id)
        body_with_images = _append_imported_image_markup(
            body, ticket_id, imported_images
        )
        enhanced_body = _build_comment_body_with_header(
            comment, body_with_images, minutes=minutes_spent
        )
        reply = await tickets_repo.create_reply(
            ticket_id=ticket_id,
            author_id=author_id,
            body=enhanced_body,
            is_internal=is_internal,
            is_billable=is_billable,
            minutes_spent=minutes_spent,
            external_reference=external_ref,
            created_at=created_at,
        )
        await _record_imported_reply_recipients(reply, comment, created_at)
        if external_ref:
            known_refs.add(external_ref)


async def _sync_ticket_watchers(
    ticket_id: int,
    comments: list[dict[str, Any]],
    contact_email: str | None,
) -> None:
    watchers = _gather_comment_watchers(comments)
    if contact_email:
        watchers = {
            email for email in watchers if email.lower() != contact_email.lower()
        }
    watchers = {
        email
        for email in watchers
        if email.lower() != "support@hawkinsitsolutions.com.au"
    }
    if not watchers:
        return
    try:
        existing = await tickets_repo.list_watchers(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to fetch existing ticket watchers",
            ticket_id=ticket_id,
            error=str(exc),
        )
        existing = []
    existing_ids = {
        int(watcher["user_id"])
        for watcher in existing
        if watcher.get("user_id") is not None
    }
    for email in sorted(watchers, key=lambda value: value.lower()):
        user_id = await _resolve_user_id_by_email(email)
        if user_id is None or user_id in existing_ids:
            continue
        try:
            await tickets_repo.add_watcher(ticket_id, user_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to add ticket watcher",
                ticket_id=ticket_id,
                email=email,
                error=str(exc),
            )
            continue
        existing_ids.add(user_id)


async def _resolve_company_id(ticket: dict[str, Any]) -> int | None:
    syncro_ids = _extract_syncro_company_ids(ticket)
    for syncro_id in syncro_ids:
        try:
            company = await company_repo.get_company_by_syncro_id(syncro_id)
        except RuntimeError as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to resolve company from Syncro ID",
                syncro_id=syncro_id,
                error=str(exc),
            )
            continue
        if company and company.get("id") is not None:
            try:
                return int(company["id"])
            except (TypeError, ValueError):
                continue
    name_candidates = list(_iter_company_name_candidates(ticket))
    for name in name_candidates:
        try:
            company = await company_repo.get_company_by_name(name)
        except RuntimeError as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to resolve company from name", company_name=name, error=str(exc)
            )
            continue
        if company and company.get("id") is not None:
            try:
                return int(company["id"])
            except (TypeError, ValueError):
                continue
    primary_name = name_candidates[0] if name_candidates else None
    primary_syncro_id = syncro_ids[0] if syncro_ids else None
    if not primary_name and primary_syncro_id:
        primary_name = f"Syncro Customer {primary_syncro_id}"
    if not primary_name:
        return None
    payload: dict[str, Any] = {"name": primary_name}
    if primary_syncro_id:
        payload["syncro_company_id"] = primary_syncro_id
    try:
        created = await company_repo.create_company(**payload)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to auto-create company from Syncro ticket",
            company_name=primary_name,
            syncro_company_id=primary_syncro_id,
            error=str(exc),
        )
        return None
    created_id = created.get("id") if isinstance(created, dict) else None
    if created_id is None:
        return None
    log_info(
        "Auto-created company from Syncro ticket",
        company_id=created_id,
        company_name=primary_name,
        syncro_company_id=primary_syncro_id,
    )
    try:
        return int(created_id)
    except (TypeError, ValueError):
        return None
    return None


async def _upsert_ticket_metadata_note(
    ticket_db_id: int,
    ticket: dict[str, Any],
    *,
    created_at: Any = None,
) -> None:
    """Create or skip a system note containing contact info, assets, and custom fields."""
    note_body = _build_ticket_metadata_note(ticket)
    if not note_body:
        return
    metadata_ref = f"syncro_metadata_{ticket.get('id', ticket_db_id)}"
    try:
        existing = await tickets_repo.list_replies(ticket_db_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to fetch replies for metadata note check",
            ticket_id=ticket_db_id,
            error=str(exc),
        )
        return
    known_refs = {
        str(reply.get("external_reference"))
        for reply in existing
        if reply.get("external_reference") is not None
    }
    if metadata_ref in known_refs:
        return
    try:
        await tickets_repo.create_reply(
            ticket_id=ticket_db_id,
            author_id=None,
            body=note_body,
            is_internal=True,
            external_reference=metadata_ref,
            created_at=created_at,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to create ticket metadata note",
            ticket_id=ticket_db_id,
            error=str(exc),
        )


async def _upsert_ticket(
    ticket: dict[str, Any],
    allowed_statuses: Collection[str],
    default_status: str,
    status_mappings: dict[str, str] | None = None,
) -> str:
    status_choices = set(allowed_statuses)
    syncro_id = ticket.get("id")
    if syncro_id is None:
        return "skipped: Syncro ticket payload did not include an id"
    external_reference = str(syncro_id)
    subject = _clean_text(
        ticket.get("subject") or ticket.get("title") or ticket.get("summary")
    )
    if not subject:
        subject = f"Syncro Ticket {external_reference}"
    description = _extract_description(ticket)
    status = _normalise_status(
        ticket.get("status_name") or ticket.get("status"),
        status_choices,
        default_status,
        status_mappings,
    )
    priority = _normalise_priority(ticket.get("priority"))
    category = _clean_text(ticket.get("type") or ticket.get("category"))
    ticket_number = _extract_ticket_number(ticket)
    contact_email = _extract_contact_email(ticket)
    requester_id = await _resolve_user_id_by_email(contact_email)

    created_at = _parse_datetime(
        ticket.get("created_at")
        or ticket.get("created_on")
        or ticket.get("created")
        or ticket.get("created_at_utc")
    )
    updated_at = _ticket_updated_at(ticket)
    closed_at = _parse_datetime(
        ticket.get("resolved_at")
        or ticket.get("closed_at")
        or ticket.get("completed_at")
        or ticket.get("date_resolved")
    )
    resolved_slug = "resolved" if "resolved" in status_choices else None
    closed_slug = "closed" if "closed" in status_choices else None
    if closed_at and status not in {resolved_slug, closed_slug}:
        if resolved_slug:
            status = resolved_slug
        elif closed_slug:
            status = closed_slug
        else:
            status = default_status

    company_id = await _resolve_company_id(ticket)

    existing = await tickets_repo.get_ticket_by_external_reference(external_reference)
    description_value: str | None = description or None
    category_value: str | None = category or None

    if existing and _syncro_ticket_is_unchanged(ticket, existing):
        return f"skipped: Syncro ticket {external_reference} has not changed since last import"

    if existing:
        updates: dict[str, Any] = {
            "subject": subject,
            "description": description_value,
            "status": status,
            "priority": priority,
            "ticket_number": ticket_number,
            "requester_id": requester_id,
        }
        if category_value is not None:
            updates["category"] = category_value
        if company_id is not None:
            updates["company_id"] = company_id
        if closed_at is not None:
            updates["closed_at"] = closed_at
        if created_at is not None:
            updates["created_at"] = created_at
        if updated_at is not None:
            updates["updated_at"] = updated_at
            updates["syncro_updated_at"] = updated_at
        await tickets_repo.update_ticket(int(existing["id"]), **updates)
        await tickets_service.emit_ticket_updated_event(
            int(existing["id"]),
            actor_type="automation",
        )
        ticket_db_id = int(existing["id"])
        outcome = "updated"
    else:
        # Extract the numeric ID for the ticket - this will be used as the database ID
        # to ensure Syncro ticket numbers match the database ticket IDs
        ticket_id_to_use = _extract_numeric_ticket_id(ticket)

        created = await tickets_service.create_ticket(
            subject=subject,
            description=description_value,
            requester_id=requester_id,
            company_id=company_id,
            assigned_user_id=None,
            priority=priority,
            status=status,
            category=category_value,
            module_slug="syncro",
            external_reference=external_reference,
            ticket_number=ticket_number,
            trigger_automations=False,
            send_creation_notification=False,
            record_initial_reply=False,
            id=ticket_id_to_use,
        )
        created_id = created.get("id")
        ticket_db_id = int(created_id) if created_id is not None else None
        if created_id is not None and any((created_at, updated_at, closed_at)):
            timestamp_updates: dict[str, Any] = {}
            if created_at is not None:
                timestamp_updates["created_at"] = created_at
            if updated_at is not None:
                timestamp_updates["updated_at"] = updated_at
                timestamp_updates["syncro_updated_at"] = updated_at
            if closed_at is not None:
                timestamp_updates["closed_at"] = closed_at
            await tickets_repo.update_ticket(int(created_id), **timestamp_updates)
        outcome = "created"

    comments = _extract_comments(ticket)
    timer_billable = _build_timer_billable_map(ticket)
    timer_time = _build_timer_time_map(ticket)
    if ticket_db_id is not None:
        await _upsert_ticket_metadata_note(ticket_db_id, ticket, created_at=created_at)
        await _link_syncro_ticket_assets(ticket_db_id, ticket, company_id)
        await _sync_ticket_attachments(ticket_db_id, ticket)
        await _sync_ticket_replies(
            ticket_db_id,
            comments,
            requester_id=requester_id,
            contact_email=contact_email,
            timer_billable=timer_billable,
            timer_time=timer_time,
        )
        await _sync_ticket_watchers(ticket_db_id, comments, contact_email)

    return outcome


async def import_ticket_by_id(
    ticket_id: int,
    *,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> TicketImportSummary:
    limiter = rate_limiter or await syncro.get_rate_limiter()
    summary = TicketImportSummary(mode="single")
    log_info("Starting Syncro ticket import", mode="single", ticket_id=ticket_id)
    ticket = await syncro.get_ticket(ticket_id, rate_limiter=limiter)
    if not ticket:
        summary.record_skip(
            f"Syncro ticket {ticket_id} was not returned by the Syncro API"
        )
        log_info(
            "Syncro ticket import completed",
            mode="single",
            fetched=summary.fetched,
            created=0,
            updated=0,
            skipped=1,
            skipped_reasons=summary.skipped_reasons,
        )
        return summary
    summary.fetched = 1
    allowed_statuses, default_status, status_mappings = await _get_status_context()
    outcome = await _upsert_ticket(
        ticket, allowed_statuses, default_status, status_mappings
    )
    summary.record(outcome)
    log_info(
        "Syncro ticket import completed",
        mode="single",
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        skipped=summary.skipped,
    )
    return summary


async def import_ticket_range(
    start_id: int,
    end_id: int,
    *,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> TicketImportSummary:
    limiter = rate_limiter or await syncro.get_rate_limiter()
    summary = TicketImportSummary(mode="range")
    log_info(
        "Starting Syncro ticket import", mode="range", start_id=start_id, end_id=end_id
    )
    allowed_statuses, default_status, status_mappings = await _get_status_context()
    for identifier in range(start_id, end_id + 1):
        ticket = await syncro.get_ticket(identifier, rate_limiter=limiter)
        if not ticket:
            summary.record_skip(
                f"Syncro ticket {identifier} was not returned by the Syncro API"
            )
            continue
        summary.fetched += 1
        outcome = await _upsert_ticket(
            ticket, allowed_statuses, default_status, status_mappings
        )
        summary.record(outcome)
    log_info(
        "Syncro ticket import completed",
        mode="range",
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        skipped=summary.skipped,
    )
    return summary


def _extract_total_pages(meta: dict[str, Any]) -> int | None:
    candidates = [meta.get("total_pages"), meta.get("totalPages"), meta.get("total")]
    for candidate in candidates:
        try:
            if candidate is None:
                continue
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


async def _fetch_ticket_detail_for_import(
    ticket: dict[str, Any],
    *,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> dict[str, Any]:
    """Fetch a detailed ticket payload when a list item lacks import-only fields.

    Syncro list endpoints can return ticket summaries that omit heavy nested
    collections such as attachments and comments.  The per-ticket endpoint is
    used to hydrate those summaries before importing so attachments are not
    silently skipped during bulk imports.
    """
    if ticket.get("attachments") is not None and ticket.get("comments") is not None:
        return ticket
    ticket_id = ticket.get("id")
    if ticket_id is None:
        return ticket
    try:
        detail = await syncro.get_ticket(ticket_id, rate_limiter=rate_limiter)
    except Exception as exc:  # pragma: no cover - caller handles import with summary
        log_error(
            "Failed to hydrate Syncro ticket detail",
            syncro_id=ticket_id,
            error=str(exc),
        )
        return ticket
    if not detail:
        return ticket
    merged = dict(ticket)
    merged.update(detail)
    return merged


async def import_all_tickets(
    *,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> TicketImportSummary:
    limiter = rate_limiter or await syncro.get_rate_limiter()
    summary = TicketImportSummary(mode="all")
    log_info("Starting Syncro ticket import", mode="all")
    allowed_statuses, default_status, status_mappings = await _get_status_context()
    page = 1
    total_pages: int | None = None
    while True:
        tickets, meta = await syncro.list_tickets(page=page, rate_limiter=limiter)
        if not tickets:
            break
        summary.fetched += len(tickets)
        for ticket in tickets:
            try:
                syncro_id = ticket.get("id")
                existing = (
                    await tickets_repo.get_ticket_by_external_reference(str(syncro_id))
                    if syncro_id is not None
                    else None
                )
                if _syncro_ticket_is_unchanged(ticket, existing):
                    outcome = f"skipped: Syncro ticket {syncro_id} has not changed since last import"
                else:
                    ticket_detail = await _fetch_ticket_detail_for_import(
                        ticket, rate_limiter=limiter
                    )
                    outcome = await _upsert_ticket(
                        ticket_detail, allowed_statuses, default_status, status_mappings
                    )
            except Exception as exc:  # pragma: no cover - defensive logging
                reason = f"Syncro ticket {ticket.get('id') or 'unknown'} failed to import: {exc}"
                log_error(
                    "Failed to import Syncro ticket",
                    syncro_id=ticket.get("id"),
                    error=str(exc),
                )
                summary.record_skip(reason)
                continue
            summary.record(outcome)
        if total_pages is None:
            total_pages = _extract_total_pages(meta)
        if total_pages is not None and page >= total_pages:
            break
        page += 1
    log_info(
        "Syncro ticket import completed",
        mode="all",
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        skipped=summary.skipped,
    )
    return summary


def _build_import_target(
    mode: str, ticket_id: int | None, start_id: int | None, end_id: int | None
) -> str:
    base = f"syncro://tickets/import?mode={mode}"
    if mode == "single" and ticket_id is not None:
        return f"{base}&ticketId={ticket_id}"
    if mode == "range":
        params: list[str] = []
        if start_id is not None:
            params.append(f"startId={start_id}")
        if end_id is not None:
            params.append(f"endId={end_id}")
        if params:
            return f"{base}&{'&'.join(params)}"
    return base


async def import_from_request(
    *,
    mode: str,
    ticket_id: int | None = None,
    start_id: int | None = None,
    end_id: int | None = None,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> TicketImportSummary:
    mode_lower = mode.lower()
    payload: dict[str, Any] = {"mode": mode_lower}
    if ticket_id is not None:
        payload["ticketId"] = ticket_id
    if start_id is not None:
        payload["startId"] = start_id
    if end_id is not None:
        payload["endId"] = end_id

    event_id: int | None = None
    using_monitor: bool = False
    target_url = _build_import_target(mode_lower, ticket_id, start_id, end_id)
    log_info(
        "Initialising Syncro ticket import workflow",
        mode=mode_lower,
        target_url=target_url,
        payload_keys=sorted(payload.keys()),
        has_rate_limiter=bool(rate_limiter),
    )

    def _coerce_event_id(raw_id: Any) -> int | None:
        try:
            return int(raw_id)
        except (TypeError, ValueError):  # pragma: no cover - defensive casting
            return None

    try:
        event = await webhook_monitor.create_manual_event(
            name="syncro.ticket.import",
            target_url=target_url,
            payload=payload,
            max_attempts=1,
            backoff_seconds=0,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to record Syncro ticket import in webhook monitor",
            mode=mode_lower,
            error=str(exc),
        )
        event = None
    else:
        event_id = _coerce_event_id(event.get("id")) if event else None
        using_monitor = event_id is not None
        log_info(
            "Syncro ticket import monitor event recorded",
            mode=mode_lower,
            event_id=event_id,
            using_monitor=using_monitor,
        )

    if event_id is None:
        try:
            fallback_event = await webhook_events_repo.create_event(
                name="syncro.ticket.import",
                target_url=target_url,
                payload=payload,
                max_attempts=1,
                backoff_seconds=0,
            )
        except Exception as fallback_exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to create fallback Syncro ticket import event",
                mode=mode_lower,
                error=str(fallback_exc),
            )
            fallback_event = None
        else:
            fallback_raw_id = fallback_event.get("id") if fallback_event else None
            event_id = _coerce_event_id(fallback_raw_id)
            if event_id is not None:
                try:
                    await webhook_events_repo.mark_in_progress(event_id)
                except Exception as mark_exc:  # pragma: no cover - defensive logging
                    log_error(
                        "Failed to mark fallback Syncro ticket import event in progress",
                        event_id=event_id,
                        error=str(mark_exc),
                    )
                    event_id = None
                else:
                    using_monitor = False
                    log_info(
                        "Syncro ticket import fallback event initialised",
                        mode=mode_lower,
                        event_id=event_id,
                    )
            else:
                log_error(
                    "Syncro ticket import fallback event returned without identifier",
                    mode=mode_lower,
                )
    else:
        log_info(
            "Syncro ticket import monitor event will track execution",
            mode=mode_lower,
            event_id=event_id,
        )

    attempt_number = 1

    async def _record_failure(error: Exception) -> None:
        if event_id is None:
            log_error(
                "Syncro ticket import failure without event tracking",
                mode=mode_lower,
                error=str(error),
            )
            return
        try:
            if using_monitor:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=attempt_number,
                    status="failed",
                    error_message=str(error),
                    response_status=None,
                    response_body=None,
                )
            else:
                await webhook_events_repo.record_attempt(
                    event_id=event_id,
                    attempt_number=attempt_number,
                    status="failed",
                    response_status=None,
                    response_body=None,
                    error_message=str(error),
                )
                await webhook_events_repo.mark_event_failed(
                    event_id,
                    attempt_number=attempt_number,
                    error_message=str(error),
                    response_status=None,
                    response_body=None,
                )
        except Exception as record_exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to record Syncro ticket import failure",
                event_id=event_id,
                error=str(record_exc),
            )
        else:
            log_error(
                "Syncro ticket import execution failed",
                mode=mode_lower,
                event_id=event_id,
                error=str(error),
                attempt=attempt_number,
            )

    try:
        if mode_lower == "single":
            if ticket_id is None:
                raise ValueError("ticket_id is required for single imports")
            summary = await import_ticket_by_id(ticket_id, rate_limiter=rate_limiter)
        elif mode_lower == "range":
            if start_id is None or end_id is None:
                raise ValueError("start_id and end_id are required for range imports")
            if end_id < start_id:
                raise ValueError("end_id must be greater than or equal to start_id")
            summary = await import_ticket_range(
                start_id, end_id, rate_limiter=rate_limiter
            )
        elif mode_lower == "all":
            summary = await import_all_tickets(rate_limiter=rate_limiter)
        else:
            raise ValueError("mode must be one of 'single', 'range', or 'all'")
    except Exception as exc:
        await _record_failure(exc)
        raise

    if event_id is not None:
        response_body = json.dumps(summary.as_dict())
        try:
            if using_monitor:
                await webhook_monitor.record_manual_success(
                    event_id,
                    attempt_number=attempt_number,
                    response_status=200,
                    response_body=response_body,
                )
            else:
                await webhook_events_repo.record_attempt(
                    event_id=event_id,
                    attempt_number=attempt_number,
                    status="succeeded",
                    response_status=200,
                    response_body=response_body,
                    error_message=None,
                )
                await webhook_events_repo.mark_event_completed(
                    event_id,
                    attempt_number=attempt_number,
                    response_status=200,
                    response_body=response_body,
                )
        except Exception as record_exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to record Syncro ticket import success",
                event_id=event_id,
                error=str(record_exc),
            )
        else:
            log_info(
                "Syncro ticket import execution recorded",
                mode=mode_lower,
                event_id=event_id,
                attempt=attempt_number,
                using_monitor=using_monitor,
            )
    return summary


# TEST CHANG
