"""Canonical, presentation-safe state for the per-company M365 admin journey."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


STATE_LABELS = {
    "legacy": "Legacy connection",
    "pending_consent": "Consent required",
    "partially_ready": "Partially ready",
    "healthy": "Connected",
    "degraded": "Degraded",
    "reconnect_required": "Reconnect required",
}


def build_connection_health(
    credentials: Mapping[str, Any] | None,
    *,
    active: Mapping[str, Any] | None = None,
    pending: Mapping[str, Any] | None = None,
    permission_results: Sequence[Mapping[str, Any]] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the single capability/health model used by every setup entry point.

    Merely storing credentials deliberately never yields ``healthy``.  A managed
    connection must have a recent successful tenant, workload and renewal
    verification; permission failures then downgrade it to ``degraded``.
    """
    now = now or datetime.now(timezone.utc)
    has_credentials = bool(credentials)
    record = pending or active
    tenant_ok = bool(record and record.get("verification_tenant"))
    workload_ok = bool(record and record.get("verification_workload"))
    renewal_ok = bool(record and record.get("verification_renewal"))
    verified_at = record.get("verified_at") if record else None
    expiry = (record or {}).get("client_secret_expires_at") or (
        credentials or {}
    ).get("client_secret_expires_at")
    expiry_utc = expiry.replace(tzinfo=timezone.utc) if isinstance(expiry, datetime) and not expiry.tzinfo else expiry
    verified_utc = (
        verified_at.replace(tzinfo=timezone.utc)
        if isinstance(verified_at, datetime) and not verified_at.tzinfo
        else verified_at
    )
    # A successful live verification is stronger evidence than stale expiry
    # metadata.  In particular, older compatibility credential rows can retain
    # an expired date while a newly activated managed connection has just
    # authenticated successfully.  Do not immediately send that connection
    # back through the reconnect journey when verification happened at or
    # after the recorded expiry.
    expired = bool(
        isinstance(expiry_utc, datetime)
        and expiry_utc <= now
        and not (
            isinstance(verified_utc, datetime)
            and verified_utc >= expiry_utc
        )
    )
    permissions_ok = bool(permission_results) and all(
        bool(item.get("all_ok")) for item in permission_results
    )

    if expired or (record and record.get("verification_error") and not tenant_ok):
        state, action, action_label = "reconnect_required", "/m365/discover", "Reconnect tenant"
    elif pending:
        # Consent has already produced a staged application.  Sending an admin
        # through consent again does not complete the staged cutover; the
        # candidate must be verified and activated instead.
        state, action, action_label = "partially_ready", "/m365", "Complete verification"
    elif active and active.get("mode") == "managed" and all((tenant_ok, workload_ok, renewal_ok, verified_at)):
        if permissions_ok:
            state, action, action_label = "healthy", "/m365/diagnostics", "View diagnostics"
        else:
            state, action, action_label = "degraded", "/m365/diagnostics", "Repair connection"
    elif has_credentials and (credentials or {}).get("refresh_token"):
        state, action, action_label = "legacy", "/m365/discover", "Migrate connection"
    elif has_credentials:
        state, action, action_label = "pending_consent", "/m365/connect", "Grant Microsoft consent"
    else:
        state, action, action_label = "pending_consent", "/m365/discover", "Connect Microsoft 365"

    return {
        "state": state,
        "label": STATE_LABELS[state],
        "variant": {"healthy": "success", "legacy": "info", "degraded": "warning"}.get(state, "danger" if state == "reconnect_required" else "warning"),
        "next_action_url": action,
        "next_action_label": action_label,
        "has_credentials": has_credentials,
        "tenant_id": (credentials or {}).get("tenant_id") or (record or {}).get("tenant_id"),
        "last_verified_at": verified_at,
        "renewal_at": expiry,
        "has_pending_candidate": bool(pending),
        "verification_error": (record or {}).get("verification_error"),
        "workloads": [
            {
                "name": "Tenant identity",
                "ready": tenant_ok,
                "help": "Confirms the enterprise app signs in to the expected tenant.",
            },
            {
                "name": "Enabled Microsoft 365 workloads",
                "ready": workload_ok,
                "help": "Confirms the app can read the Graph workloads used by MyPortal.",
            },
            {
                "name": "Automatic credential renewal",
                "ready": renewal_ok,
                "help": "Confirms MyPortal can access the app registration before its secret expires.",
            },
        ],
        "message": {
            "legacy": "The existing connection remains active. Migrate it with rollback protection.",
            "pending_consent": "Confirm the tenant and review all requested access before granting consent.",
            "partially_ready": "A staged connection still needs verification and activation before setup is complete.",
            "healthy": "Tenant, enabled workloads, permissions and automatic renewal are verified.",
            "degraded": "The tenant is verified, but one or more permissions or workloads need repair.",
            "reconnect_required": "The tenant identity or credential is no longer valid.",
        }[state],
    }
