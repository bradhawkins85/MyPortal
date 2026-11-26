"""Fail2ban status feature pack.

Owns the super-admin, view-only status page:

* ``GET  /admin/fail2ban``
* ``HEAD /admin/fail2ban``

It reads the snapshot written by the root ``myportal-fail2ban-snapshot``
timer and the end of ``FAIL2BAN_LOG_PATH``. Nothing here can ban, unban or
reconfigure Fail2ban.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router


PACK = FeaturePack(
    slug="fail2ban",
    version="1.0.1",
    routers=(router,),
)


__all__ = ["PACK"]
