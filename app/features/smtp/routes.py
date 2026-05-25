"""SMTP routes for the ``smtp`` feature pack."""

from __future__ import annotations

from app.api.routes.smtp2go_webhooks import router as smtp_router


router = smtp_router


__all__ = ["router"]
