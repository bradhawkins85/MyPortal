"""Compatibility wrapper for SMTP2Go webhook routes.

The SMTP2Go webhook implementation now lives in ``app.features.smtp.routes``
to keep the feature-pack code self-contained.
"""

from __future__ import annotations

from app.features.smtp.routes import (
    _parse_timestamp_signature,
    router,
    smtp2go_webhook,
    verify_webhook_signature,
)

__all__ = ["router", "verify_webhook_signature", "_parse_timestamp_signature", "smtp2go_webhook"]
