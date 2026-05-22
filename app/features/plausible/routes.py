"""Plausible routes for the ``plausible`` feature pack."""

from __future__ import annotations

from app.api.routes.email_tracking import router as plausible_router


router = plausible_router


__all__ = ["router"]
