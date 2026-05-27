"""SMS Gateway feature pack.

This pack makes the ``sms-gateway`` integration-module action a
first-class hot-reloadable feature-pack unit. Delivery is handled by
``app.services.modules._invoke_sms_gateway``, so no standalone routes
are required in this pack.
"""

from __future__ import annotations

from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="sms_gateway",
    version="1.0.0",
)


__all__ = ["PACK"]
