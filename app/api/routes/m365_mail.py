"""Compatibility wrapper for M365 mail routes.

The M365 mail route implementation now lives in ``app.features.m365_mail``
so the feature-pack code is self-contained.
"""

from __future__ import annotations

from app.features.m365_mail.api_routes import (
    clone_account,
    create_account,
    delete_account,
    disconnect_account,
    get_account,
    list_accounts,
    router,
    sync_account,
    update_account,
)

__all__ = [
    "router",
    "list_accounts",
    "create_account",
    "get_account",
    "update_account",
    "delete_account",
    "sync_account",
    "clone_account",
    "disconnect_account",
]
