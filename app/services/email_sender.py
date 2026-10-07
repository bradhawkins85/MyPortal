from __future__ import annotations

from email.utils import formataddr, parseaddr

from app.core.config import Settings


def resolve_sender(settings: Settings, sender: str | None = None) -> str | None:
    """Apply the organisation display name to the default SMTP sender."""
    if sender:
        return sender
    address = settings.smtp_from or settings.smtp_user
    if not address:
        return None
    if settings.company_name:
        return formataddr((settings.company_name, parseaddr(address)[1]))
    return address
