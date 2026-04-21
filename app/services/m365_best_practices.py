"""Microsoft 365 Best Practices service.

Implements a curated set of Microsoft 365 best-practice checks for tenant
configurations.  Modelled after the CIS Benchmark service (see
``app/services/cis_benchmark.py``) but with two key differences:

* Best Practices are enabled/disabled **globally** by super administrators
  (a single switch per check applied across all companies), rather than per
  company exclusions.
* Each company gets its own set of evaluation results stored in the
  ``m365_best_practice_results`` table.

Several of the underlying Graph queries reuse helper functions from
``cis_benchmark`` to avoid duplication.  Each best-practice check has its
own ``bp_*`` identifier and its own user-facing name and remediation text
suitable for the Best Practices page.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.logging import log_error, log_info
from app.repositories import m365_best_practices as bp_repo
from app.services.cis_benchmark import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNKNOWN,
    STATUS_NOT_APPLICABLE,
    _check_admin_mfa,
    _check_audit_log_enabled,
    _check_global_admin_count,
    _check_guest_access_restricted,
    _check_legacy_auth_blocked,
    _check_mfa_conditional_access,
    _check_password_never_expires,
    _check_security_defaults,
    _check_sspr_enabled,
)
from app.services.m365 import M365Error, acquire_access_token


# ---------------------------------------------------------------------------
# Best Practice catalog
# ---------------------------------------------------------------------------
#
# Each entry describes a Microsoft 365 best-practice check.  The ``source``
# callable is an existing CIS-benchmark Graph helper that produces a result
# dict with its own ``check_id``/``check_name``; we re-key the result to use
# the ``bp_*`` id and Best-Practices-specific name when persisting.

BestPracticeRunner = Callable[[str], Awaitable[dict[str, Any]]]


_BEST_PRACTICES: list[dict[str, Any]] = [
    {
        "id": "bp_security_defaults",
        "name": "Enable Security Defaults",
        "description": (
            "Microsoft recommends Security Defaults as the baseline identity "
            "security configuration for tenants without Conditional Access."
        ),
        "remediation": (
            "Enable Security Defaults: Azure portal → Azure Active Directory → "
            "Properties → Manage security defaults → Enable."
        ),
        "source": _check_security_defaults,
        "default_enabled": True,
    },
    {
        "id": "bp_block_legacy_auth",
        "name": "Block legacy authentication",
        "description": (
            "Block legacy authentication protocols (POP, IMAP, SMTP basic auth) "
            "to prevent password-spray and credential-stuffing attacks."
        ),
        "remediation": (
            "Create a Conditional Access policy that targets all users and "
            "blocks 'Other clients' / 'Exchange ActiveSync clients'."
        ),
        "source": _check_legacy_auth_blocked,
        "default_enabled": True,
    },
    {
        "id": "bp_mfa_for_all_users",
        "name": "Require MFA for all users",
        "description": (
            "Microsoft recommends multi-factor authentication for every user "
            "to defend against compromised credentials."
        ),
        "remediation": (
            "Create a Conditional Access policy assigned to All users that "
            "requires multi-factor authentication under Grant controls."
        ),
        "source": _check_mfa_conditional_access,
        "default_enabled": True,
    },
    {
        "id": "bp_admin_mfa",
        "name": "Require MFA for administrators",
        "description": (
            "Privileged accounts must always require strong authentication."
        ),
        "remediation": (
            "Ensure all admin role holders are registered for MFA and have it "
            "enforced via Conditional Access or per-user MFA."
        ),
        "source": _check_admin_mfa,
        "default_enabled": True,
    },
    {
        "id": "bp_global_admin_count",
        "name": "Maintain 2–4 Global Administrators",
        "description": (
            "Microsoft recommends between two and four Global Administrators "
            "to balance availability and minimise blast radius."
        ),
        "remediation": (
            "Adjust Global Administrator role assignments via Azure AD → "
            "Roles and administrators → Global Administrator."
        ),
        "source": _check_global_admin_count,
        "default_enabled": True,
    },
    {
        "id": "bp_audit_log_enabled",
        "name": "Enable unified audit log",
        "description": (
            "The unified audit log captures activity across Microsoft 365 "
            "workloads and is required for incident investigations."
        ),
        "remediation": (
            "Enable in the Compliance portal: Audit → Start recording user "
            "and admin activity."
        ),
        "source": _check_audit_log_enabled,
        "default_enabled": True,
    },
    {
        "id": "bp_self_service_password_reset",
        "name": "Enable Self-Service Password Reset",
        "description": (
            "SSPR reduces help-desk load and improves user experience while "
            "maintaining strong identity hygiene."
        ),
        "remediation": (
            "Azure AD → Password reset → Properties → Self-service password "
            "reset enabled = All."
        ),
        "source": _check_sspr_enabled,
        "default_enabled": True,
    },
    {
        "id": "bp_password_never_expires",
        "name": "Disable password expiration",
        "description": (
            "Microsoft and NIST recommend not expiring passwords when MFA is "
            "in place; forced rotations weaken password quality."
        ),
        "remediation": (
            "Microsoft 365 admin center → Settings → Org Settings → Security "
            "& privacy → Password expiration policy → Set passwords to never "
            "expire."
        ),
        "source": _check_password_never_expires,
        "default_enabled": True,
    },
    {
        "id": "bp_guest_access_restricted",
        "name": "Restrict guest user access",
        "description": (
            "Limit what external guest accounts can see and do in your "
            "directory to reduce data-exposure risk."
        ),
        "remediation": (
            "Azure AD → External Identities → External collaboration "
            "settings → Guest user access restrictions → Restricted."
        ),
        "source": _check_guest_access_restricted,
        "default_enabled": True,
    },
]


def list_best_practices() -> list[dict[str, Any]]:
    """Return the best-practice catalog (without runner callables)."""
    return [
        {k: v for k, v in bp.items() if k != "source"}
        for bp in _BEST_PRACTICES
    ]


def _catalog_map() -> dict[str, dict[str, Any]]:
    return {bp["id"]: bp for bp in _BEST_PRACTICES}


def get_remediation(check_id: str) -> str:
    bp = _catalog_map().get(check_id)
    if bp:
        return bp["remediation"]
    return "Consult Microsoft 365 documentation for remediation guidance."


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


async def get_enabled_check_ids() -> set[str]:
    """Return the set of best-practice check_ids currently enabled globally.

    Checks that have never been recorded in the settings table fall back to
    their ``default_enabled`` value from the catalog.
    """
    settings = await bp_repo.get_settings_map()
    enabled: set[str] = set()
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        if check_id in settings:
            if settings[check_id]:
                enabled.add(check_id)
        elif bp.get("default_enabled", True):
            enabled.add(check_id)
    return enabled


async def list_settings_with_catalog() -> list[dict[str, Any]]:
    """Return the catalog merged with the current global enabled flag.

    Each item contains the catalog metadata plus an ``enabled`` boolean
    representing the current global setting (defaulting to the catalog's
    ``default_enabled`` value when no row exists yet).
    """
    settings = await bp_repo.get_settings_map()
    out: list[dict[str, Any]] = []
    for bp in _BEST_PRACTICES:
        entry = {k: v for k, v in bp.items() if k != "source"}
        entry["enabled"] = settings.get(bp["id"], bool(bp.get("default_enabled", True)))
        out.append(entry)
    return out


async def set_enabled_checks(enabled_check_ids: set[str]) -> None:
    """Persist the global enabled flag for every catalog check.

    For checks toggled off, any previously-stored per-company results are
    cleared so they no longer appear on company pages.
    """
    catalog = _catalog_map()
    enabled_filtered = {cid for cid in enabled_check_ids if cid in catalog}
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        is_enabled = check_id in enabled_filtered
        await bp_repo.upsert_setting(check_id=check_id, enabled=is_enabled)
        if not is_enabled:
            await bp_repo.delete_result_for_check(check_id)
    log_info(
        "M365 Best Practice settings updated",
        enabled_count=len(enabled_filtered),
        total=len(_BEST_PRACTICES),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_best_practices(company_id: int) -> list[dict[str, Any]]:
    """Run all globally-enabled best-practice checks for ``company_id``.

    Returns the list of result dicts (one per check) and persists each result
    in the ``m365_best_practice_results`` table.
    """
    token = await acquire_access_token(company_id)
    enabled = await get_enabled_check_ids()
    run_at = datetime.now(timezone.utc).replace(tzinfo=None)

    results: list[dict[str, Any]] = []
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        if check_id not in enabled:
            continue
        check_name = bp["name"]
        runner: BestPracticeRunner = bp["source"]
        try:
            raw = await runner(token)
            status = raw.get("status", STATUS_UNKNOWN)
            details = raw.get("details") or ""
        except M365Error as exc:
            log_error(
                "M365 best practice check failed",
                company_id=company_id,
                check_id=check_id,
                error=str(exc),
            )
            status = STATUS_UNKNOWN
            details = f"Unable to evaluate check: {exc}"

        await bp_repo.upsert_result(
            company_id=company_id,
            check_id=check_id,
            check_name=check_name,
            status=status,
            details=details,
            run_at=run_at,
        )
        results.append({
            "check_id": check_id,
            "check_name": check_name,
            "status": status,
            "details": details,
            "run_at": run_at,
            "remediation": get_remediation(check_id) if status == STATUS_FAIL else None,
        })

    log_info(
        "M365 best practices run",
        company_id=company_id,
        check_count=len(results),
    )
    return results


async def get_last_results(company_id: int) -> list[dict[str, Any]]:
    """Return the most recent stored best-practice results for ``company_id``.

    Only checks that are currently globally enabled are returned; results for
    disabled checks are filtered out (and are also cleared by
    :func:`set_enabled_checks`).  Each entry is enriched with remediation
    guidance for failed checks and with the catalog metadata.
    """
    rows = await bp_repo.list_results(company_id)
    enabled = await get_enabled_check_ids()
    catalog = _catalog_map()

    out: list[dict[str, Any]] = []
    for row in rows:
        check_id = row["check_id"]
        if check_id not in enabled:
            continue
        bp_meta = catalog.get(check_id, {})
        status = row.get("status") or STATUS_UNKNOWN
        out.append({
            "check_id": check_id,
            "check_name": row.get("check_name") or bp_meta.get("name", check_id),
            "description": bp_meta.get("description", ""),
            "status": status,
            "details": row.get("details") or "",
            "run_at": row.get("run_at"),
            "remediation": get_remediation(check_id) if status == STATUS_FAIL else None,
        })
    return out


# Status constants re-exported for convenience.
__all__ = [
    "STATUS_PASS",
    "STATUS_FAIL",
    "STATUS_UNKNOWN",
    "STATUS_NOT_APPLICABLE",
    "list_best_practices",
    "list_settings_with_catalog",
    "get_enabled_check_ids",
    "set_enabled_checks",
    "run_best_practices",
    "get_last_results",
    "get_remediation",
]
