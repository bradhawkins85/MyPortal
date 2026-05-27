"""Compatibility wrapper for Uptime Kuma routes.

The Uptime Kuma implementation now lives in ``app.features.uptimekuma.routes``
to keep feature-pack code self-contained.
"""

from __future__ import annotations

from app.features.uptimekuma.routes import get_alert, list_alerts, logger, receive_alert, router

__all__ = ["router", "receive_alert", "list_alerts", "get_alert", "logger"]
