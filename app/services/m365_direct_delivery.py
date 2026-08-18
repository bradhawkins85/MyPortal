"""Directly create notification messages in Microsoft 365 inboxes.

Unlike Graph ``sendMail``, this uses the message-create endpoint for the
recipient's Inbox.  Exchange therefore does not route the message through an
outbound transport or a third-party relay.  ``Mail.ReadWrite`` application
permission is sufficient and also lets us query ``isRead`` later.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from app.services import m365


def _recipient(address: str) -> dict[str, Any]:
    return {"emailAddress": {"address": address}}


async def deposit_message(
    *,
    company_id: int,
    recipient: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    sender: str | None = None,
    reply_to: str | None = None,
    attachments: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create one unread message in *recipient*'s Inbox and return its metadata."""
    token = await m365.acquire_access_token(company_id, force_client_credentials=True)
    address = recipient.strip()
    message: dict[str, Any] = {
        "subject": subject,
        "body": {
            "contentType": "HTML" if html_body else "Text",
            "content": html_body or text_body or "",
        },
        "toRecipients": [_recipient(address)],
        "isRead": False,
    }
    if sender:
        message["from"] = _recipient(sender)
        message["sender"] = _recipient(sender)
    if reply_to:
        message["replyTo"] = [_recipient(reply_to)]
    graph_attachments: list[dict[str, Any]] = []
    for item in attachments or ():
        name = str(item.get("filename") or item.get("name") or "attachment")
        content = item.get("content")
        if isinstance(content, str):
            encoded = content
        elif isinstance(content, (bytes, bytearray)):
            encoded = base64.b64encode(bytes(content)).decode("ascii")
        else:
            raise ValueError(f"Attachment {name!r} has no content")
        graph_attachments.append(
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": name,
                "contentType": str(item.get("mime_type") or item.get("content_type") or "application/octet-stream"),
                "contentBytes": encoded,
            }
        )
    if graph_attachments:
        message["attachments"] = graph_attachments

    user = quote(address, safe="")
    result = await m365._graph_post(
        token,
        f"https://graph.microsoft.com/v1.0/users/{user}/mailFolders/inbox/messages",
        message,
    )
    return {
        "message_id": str(result.get("id") or ""),
        "internet_message_id": result.get("internetMessageId"),
        "recipient": address,
        "created_at": datetime.now(timezone.utc),
    }


async def get_read_status(*, company_id: int, recipient: str, message_id: str) -> bool:
    """Return Graph's current ``isRead`` value for a deposited message."""
    token = await m365.acquire_access_token(company_id, force_client_credentials=True)
    user = quote(recipient.strip(), safe="")
    message = quote(message_id, safe="")
    result = await m365._graph_get(
        token,
        f"https://graph.microsoft.com/v1.0/users/{user}/messages/{message}?$select=isRead",
    )
    return bool(result.get("isRead"))
