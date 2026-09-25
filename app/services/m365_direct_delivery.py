"""Microsoft 365 notification operations.

Graph's create-message endpoint always creates a draft, even when the Inbox is
named as the destination.  ``inbox_item`` therefore deliberately creates an
unrouted mailbox item; ``send_mail`` submits a real message through Exchange.
Neither operation is evidence of final transport delivery.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from app.services import m365

MAX_INLINE_ATTACHMENT_BYTES = 3 * 1024 * 1024


class DirectDeliveryError(RuntimeError):
    """The requested Graph operation did not complete with usable evidence."""


class AmbiguousDeliveryError(DirectDeliveryError):
    """Graph may have accepted the POST; retrying could create a duplicate."""


def _recipient(address: str) -> dict[str, Any]:
    return {"emailAddress": {"address": address}}


def _message(*, recipient: str, subject: str, html_body: str,
             text_body: str | None, sender: str | None, reply_to: str | None,
             attachments: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "HTML" if html_body else "Text",
                 "content": html_body or text_body or ""},
        "toRecipients": [_recipient(recipient)],
    }
    if sender:
        message["from"] = _recipient(sender)
        message["sender"] = _recipient(sender)
    if reply_to:
        message["replyTo"] = [_recipient(reply_to)]
    graph_attachments: list[dict[str, Any]] = []
    total = 0
    for item in attachments or ():
        name = str(item.get("filename") or item.get("name") or "attachment")
        content = item.get("content")
        if isinstance(content, str):
            try:
                raw = base64.b64decode(content, validate=True)
            except ValueError as exc:
                raise ValueError(f"Attachment {name!r} is not valid base64") from exc
            encoded = content
        elif isinstance(content, (bytes, bytearray)):
            raw = bytes(content)
            encoded = base64.b64encode(raw).decode("ascii")
        else:
            raise ValueError(f"Attachment {name!r} has no content")
        total += len(raw)
        if total > MAX_INLINE_ATTACHMENT_BYTES:
            raise ValueError("M365 inline attachments exceed the 3 MiB module limit")
        graph_attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment", "name": name,
            "contentType": str(item.get("mime_type") or item.get("content_type") or
                               "application/octet-stream"), "contentBytes": encoded,
        })
    if graph_attachments:
        message["attachments"] = graph_attachments
    return message


async def deliver_message(*, company_id: int, recipient: str, subject: str,
                          html_body: str, text_body: str | None = None,
                          sender: str | None = None, reply_to: str | None = None,
                          attachments: Sequence[Mapping[str, Any]] | None = None,
                          mode: str = "inbox_item") -> dict[str, Any]:
    """Create a draft Inbox item or submit mail, reporting only that operation.

    POST is intentionally attempted once. Retrying an ambiguous timeout can
    duplicate a message because Graph provides no idempotency key for sendMail.
    The caller may safely retry only when it has persisted a terminal result.
    """
    if mode not in {"inbox_item", "send_mail"}:
        raise ValueError("M365 delivery_mode must be 'inbox_item' or 'send_mail'")
    address = recipient.strip()
    if mode == "send_mail" and not sender:
        raise DirectDeliveryError("send_mail requires a configured sender_address")
    token = await m365.acquire_access_token(company_id, force_client_credentials=True)
    message = _message(recipient=address, subject=subject, html_body=html_body,
                       text_body=text_body, sender=sender, reply_to=reply_to,
                       attachments=attachments)
    if mode == "send_mail":
        user = quote(sender.strip(), safe="")
        try:
            await m365._graph_post(token,
                f"https://graph.microsoft.com/v1.0/users/{user}/sendMail",
                {"message": message, "saveToSentItems": True})
        except m365.M365Error as exc:
            if "timed out" in str(exc).lower() or "network error" in str(exc).lower():
                raise AmbiguousDeliveryError(
                    "sendMail result is unknown; automatic retry is suppressed"
                ) from exc
            raise
        return {"message_id": None, "internet_message_id": None,
                "recipient": address, "operation": "send_mail",
                "state": "submitted", "created_at": datetime.now(timezone.utc)}

    message["isRead"] = False
    user = quote(address, safe="")
    result = await m365._graph_post(token,
        f"https://graph.microsoft.com/v1.0/users/{user}/mailFolders/inbox/messages",
        message)
    message_id = str(result.get("id") or "")
    if not message_id:
        raise DirectDeliveryError("Graph created a draft but returned no message id")
    # The documented create operation creates a draft. Do not manufacture a
    # delivered state if Graph omits isDraft from a partial response.
    if result.get("isDraft") is False:
        raise DirectDeliveryError("Graph create response unexpectedly was not a draft")
    return {"message_id": message_id, "internet_message_id": result.get("internetMessageId"),
            "recipient": address, "operation": "inbox_item", "state": "created",
            "is_draft": True, "created_at": datetime.now(timezone.utc)}


async def deposit_message(**kwargs: Any) -> dict[str, Any]:
    """Backward-compatible name for the explicit ``inbox_item`` operation."""
    return await deliver_message(mode="inbox_item", **kwargs)


async def get_read_status(*, company_id: int, recipient: str, message_id: str) -> bool:
    """Return current Graph ``isRead``; failures mean unknown, never unread."""
    token = await m365.acquire_access_token(company_id, force_client_credentials=True)
    user, message = quote(recipient.strip(), safe=""), quote(message_id, safe="")
    result = await m365._graph_get(token,
        f"https://graph.microsoft.com/v1.0/users/{user}/messages/{message}?$select=isRead")
    if "isRead" not in result:
        raise DirectDeliveryError("Graph did not return read status")
    return bool(result["isRead"])
