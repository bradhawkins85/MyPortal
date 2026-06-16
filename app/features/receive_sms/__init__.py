"""Receive SMS feature pack."""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router


PACK = FeaturePack(
    slug="receive_sms",
    version="1.0.0",
    routers=(router,),
    description="Receives authenticated Android SMS webhooks and converts them into tickets.",
)


__all__ = ["PACK"]
