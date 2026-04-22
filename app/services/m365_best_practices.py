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

Some checks (e.g. ``bp_disable_direct_send``) query Exchange Online via the
REST InvokeCommand API instead of Microsoft Graph; these are marked with
``"source_type": "exo"`` in the catalog and their runner callables accept
``(exo_token, tenant_id)`` rather than a single Graph token string.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Union

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
    _check_monitor_app_credential_expiry,
    _check_monitor_ca_report_only_policies,
    _check_monitor_cloud_admin_accounts,
    _check_monitor_mfa_registration_policy,
    _check_monitor_named_locations,
    _check_monitor_risky_users,
    _check_monitor_secure_score,
    _check_monitor_sign_in_logs,
    _check_monitor_sign_in_risk_policy,
    _check_monitor_user_risk_policy,
    _check_password_never_expires,
    _check_security_defaults,
    _check_sspr_enabled,
)
from app.services.m365 import M365Error, _acquire_exo_access_token, _exo_invoke_command, acquire_access_token


# ---------------------------------------------------------------------------
# Best Practice catalog
# ---------------------------------------------------------------------------
#
# Each entry describes a Microsoft 365 best-practice check.  The ``source``
# callable is an existing CIS-benchmark Graph helper that produces a result
# dict with its own ``check_id``/``check_name``; we re-key the result to use
# the ``bp_*`` id and Best-Practices-specific name when persisting.
#
# Entries with ``"source_type": "exo"`` call an Exchange Online InvokeCommand
# runner that takes ``(exo_token: str, tenant_id: str)`` instead of a single
# Graph token string.
#
# Entries with ``"has_remediation": True`` support one-click automated
# remediation via the ``remediate_check`` service function.

# Runner types
GraphRunner = Callable[[str], Awaitable[dict[str, Any]]]
ExoRunner = Callable[[str, str], Awaitable[dict[str, Any]]]
BestPracticeRunner = Union[GraphRunner, ExoRunner]

# Keys that are implementation details and must not be exposed in the public catalog
_INTERNAL_KEYS = frozenset({"source", "source_type", "remediation_cmdlet", "remediation_params"})


# ---------------------------------------------------------------------------
# EXO-based check and remediation helpers
# ---------------------------------------------------------------------------


async def _check_direct_send(exo_token: str, tenant_id: str) -> dict[str, Any]:
    """Check whether Direct Send (anonymous relay) is disabled via Exchange Online.

    Calls ``Get-OrganizationConfig`` and inspects the ``RejectDirectSend``
    property.  Requires the app to have ``Exchange.ManageAsApp`` and an
    Exchange Administrator RBAC role.
    """
    check_id = "bp_disable_direct_send"
    check_name = "Direct Send is disabled"
    try:
        data = await _exo_invoke_command(exo_token, tenant_id, "Get-OrganizationConfig")
        value = data.get("value") or []
        config: dict[str, Any] = value[0] if isinstance(value, list) and value else {}
        reject = config.get("RejectDirectSend")
        if reject is True:
            return {
                "check_id": check_id,
                "check_name": check_name,
                "status": STATUS_PASS,
                "details": "Direct Send (anonymous relay) is disabled.",
            }
        if reject is False:
            return {
                "check_id": check_id,
                "check_name": check_name,
                "status": STATUS_FAIL,
                "details": (
                    "Direct Send is enabled. External senders can relay mail "
                    "through your tenant without authentication."
                ),
            }
        return {
            "check_id": check_id,
            "check_name": check_name,
            "status": STATUS_UNKNOWN,
            "details": "Unable to determine Direct Send status from organization config.",
        }
    except M365Error as exc:
        return {
            "check_id": check_id,
            "check_name": check_name,
            "status": STATUS_UNKNOWN,
            "details": f"Unable to query Exchange Online organization config: {exc}",
        }


async def _run_direct_send_remediation(exo_token: str, tenant_id: str) -> bool:
    """Execute ``Set-OrganizationConfig -RejectDirectSend $true`` via EXO REST API.

    Returns ``True`` on success, ``False`` on failure.
    """
    try:
        await _exo_invoke_command(
            exo_token,
            tenant_id,
            "Set-OrganizationConfig",
            {"RejectDirectSend": True},
        )
        return True
    except M365Error:
        return False


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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
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
        "has_remediation": False,
    },
    # ------------------------------------------------------------------
    # Exchange Online checks
    # ------------------------------------------------------------------
    {
        "id": "bp_disable_direct_send",
        "name": "Disable Direct Send",
        "description": (
            "Direct Send allows external senders to relay mail through your "
            "Exchange Online tenant without authentication. Disabling it "
            "prevents unauthorized mail relay and reduces spam/phishing risk."
        ),
        "remediation": (
            "Run the following Exchange Online PowerShell command to disable "
            "Direct Send: Set-OrganizationConfig -RejectDirectSend $true"
        ),
        "source": _check_direct_send,
        "source_type": "exo",
        "has_remediation": True,
        "remediation_cmdlet": "Set-OrganizationConfig",
        "remediation_params": {"RejectDirectSend": True},
        "default_enabled": True,
    },
    # ------------------------------------------------------------------
    # Monitoring best practices
    # ------------------------------------------------------------------
    {
        "id": "bp_monitor_sign_in_logs",
        "name": "Sign-in audit logs accessible",
        "description": (
            "Sign-in logs are essential for incident investigation and "
            "monitoring potentially compromised accounts."
        ),
        "remediation": (
            "Ensure the Microsoft Graph sign-in logs API is accessible: grant "
            "AuditLog.Read.All and verify the tenant has an Azure AD Premium "
            "P1 or P2 license."
        ),
        "source": _check_monitor_sign_in_logs,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_risky_users",
        "name": "No active high-risk users",
        "description": (
            "Microsoft Entra ID Protection flags users whose credentials may "
            "be compromised; risky users should be investigated promptly."
        ),
        "remediation": (
            "Investigate and remediate risky users in the Entra portal → "
            "Protection → Identity Protection → Risky users."
        ),
        "source": _check_monitor_risky_users,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_sign_in_risk_policy",
        "name": "Sign-in risk policy enabled",
        "description": (
            "Enabling the sign-in risk policy automatically challenges or "
            "blocks risky sign-ins detected by Entra ID Protection."
        ),
        "remediation": (
            "Entra portal → Protection → Identity Protection → Sign-in risk "
            "policy → assign to All users, set risk level to Medium and above, "
            "and require MFA."
        ),
        "source": _check_monitor_sign_in_risk_policy,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_user_risk_policy",
        "name": "User risk policy enabled",
        "description": (
            "The user risk policy automatically blocks or requires a password "
            "reset for accounts considered compromised."
        ),
        "remediation": (
            "Entra portal → Protection → Identity Protection → User risk "
            "policy → assign to All users, set risk level to High, and "
            "require secure password change."
        ),
        "source": _check_monitor_user_risk_policy,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_named_locations",
        "name": "Named locations configured for Conditional Access",
        "description": (
            "Defining trusted named locations enables Conditional Access "
            "policies to use location as a strong signal."
        ),
        "remediation": (
            "Entra portal → Protection → Conditional Access → Named "
            "locations → add trusted IP ranges or country lists."
        ),
        "source": _check_monitor_named_locations,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_ca_report_only_policies",
        "name": "No core security CA policies stuck in report-only",
        "description": (
            "Conditional Access policies in report-only mode provide no "
            "protection; security-critical policies should be fully enabled."
        ),
        "remediation": (
            "Entra portal → Protection → Conditional Access → policy → set "
            "state to 'On' after reviewing the report-only insights."
        ),
        "source": _check_monitor_ca_report_only_policies,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_app_credential_expiry",
        "name": "No app registration credentials expiring within 30 days",
        "description": (
            "Expired application secrets cause authentication failures and "
            "service outages; rotate credentials proactively."
        ),
        "remediation": (
            "Entra portal → App registrations → application → Certificates & "
            "secrets → create a new secret/certificate and update dependent "
            "services before the existing one expires."
        ),
        "source": _check_monitor_app_credential_expiry,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_cloud_admin_accounts",
        "name": "Privileged accounts are cloud-only (not hybrid-synced)",
        "description": (
            "Microsoft strongly recommends that Global Administrator accounts "
            "are cloud-only identities to prevent on-premises compromise from "
            "escalating to the cloud."
        ),
        "remediation": (
            "Create dedicated cloud admin accounts in Entra ID and remove the "
            "Global Administrator role from any synced accounts."
        ),
        "source": _check_monitor_cloud_admin_accounts,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_secure_score",
        "name": "Microsoft Secure Score is at or above 50%",
        "description": (
            "Microsoft Secure Score is the primary KPI for overall M365 "
            "security posture; tracking it ensures continuous improvement."
        ),
        "remediation": (
            "Microsoft 365 Defender portal → Secure Score → review and "
            "implement recommended improvement actions."
        ),
        "source": _check_monitor_secure_score,
        "default_enabled": True,
        "has_remediation": False,
    },
    {
        "id": "bp_monitor_mfa_registration_policy",
        "name": "Authentication methods policy is configured",
        "description": (
            "Microsoft recommends explicitly configuring the authentication "
            "methods policy with modern methods such as Microsoft "
            "Authenticator and FIDO2 keys."
        ),
        "remediation": (
            "Entra portal → Protection → Authentication methods → Policies → "
            "enable Microsoft Authenticator, FIDO2 security keys, and other "
            "modern methods."
        ),
        "source": _check_monitor_mfa_registration_policy,
        "default_enabled": True,
        "has_remediation": False,
    },
]


def list_best_practices() -> list[dict[str, Any]]:
    """Return the best-practice catalog (without internal runner keys)."""
    return [
        {k: v for k, v in bp.items() if k not in _INTERNAL_KEYS}
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
            if settings[check_id]["enabled"]:
                enabled.add(check_id)
        elif bp.get("default_enabled", True):
            enabled.add(check_id)
    return enabled


async def get_auto_remediate_check_ids() -> set[str]:
    """Return the set of check_ids that have auto-remediation enabled globally.

    Only checks that declare ``has_remediation: True`` in the catalog can
    meaningfully appear in this set; others are excluded even if the settings
    row has ``auto_remediate=True``.
    """
    settings = await bp_repo.get_settings_map()
    auto_remediate: set[str] = set()
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        if (
            bp.get("has_remediation")
            and check_id in settings
            and settings[check_id].get("auto_remediate")
        ):
            auto_remediate.add(check_id)
    return auto_remediate


async def list_settings_with_catalog() -> list[dict[str, Any]]:
    """Return the catalog merged with the current global enabled and auto-remediate flags.

    Each item contains the catalog metadata plus:
    - ``enabled`` boolean (global on/off, defaulting to ``default_enabled``)
    - ``auto_remediate`` boolean (auto-remediation after each evaluation)
    """
    settings = await bp_repo.get_settings_map()
    out: list[dict[str, Any]] = []
    for bp in _BEST_PRACTICES:
        entry = {k: v for k, v in bp.items() if k not in _INTERNAL_KEYS}
        row = settings.get(bp["id"])
        entry["enabled"] = row["enabled"] if row else bool(bp.get("default_enabled", True))
        entry["auto_remediate"] = row["auto_remediate"] if row else False
        out.append(entry)
    return out


async def set_enabled_checks(
    enabled_check_ids: set[str],
    auto_remediate_check_ids: set[str] | None = None,
) -> None:
    """Persist the global enabled and auto-remediate flags for every catalog check.

    ``enabled_check_ids`` controls which checks are active globally.
    ``auto_remediate_check_ids`` controls which checks trigger automated
    remediation immediately after evaluation (only honoured for checks that
    declare ``has_remediation: True`` in the catalog).

    For checks toggled off, any previously-stored per-company results are
    cleared so they no longer appear on company pages.
    """
    catalog = _catalog_map()
    enabled_filtered = {cid for cid in enabled_check_ids if cid in catalog}
    auto_remediate_filtered: set[str] = set()
    if auto_remediate_check_ids is not None:
        auto_remediate_filtered = {
            cid
            for cid in auto_remediate_check_ids
            if cid in catalog and catalog[cid].get("has_remediation")
        }
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        is_enabled = check_id in enabled_filtered
        is_auto_remediate = check_id in auto_remediate_filtered
        await bp_repo.upsert_setting(
            check_id=check_id,
            enabled=is_enabled,
            auto_remediate=is_auto_remediate,
        )
        if not is_enabled:
            await bp_repo.delete_result_for_check(check_id)
    log_info(
        "M365 Best Practice settings updated",
        enabled_count=len(enabled_filtered),
        auto_remediate_count=len(auto_remediate_filtered),
        total=len(_BEST_PRACTICES),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_best_practices(company_id: int) -> list[dict[str, Any]]:
    """Run all globally-enabled best-practice checks for ``company_id``.

    Returns the list of result dicts (one per check) and persists each result
    in the ``m365_best_practice_results`` table.

    After evaluation, any check that both:
    - returned ``STATUS_FAIL``, and
    - has ``auto_remediate`` enabled globally (and ``has_remediation: True``)

    will have automated remediation triggered immediately.

    Graph-based checks receive the Graph access token; Exchange-Online-based
    checks (``source_type == "exo"``) receive the EXO token and tenant ID
    acquired once lazily.
    """
    graph_token = await acquire_access_token(company_id)
    enabled = await get_enabled_check_ids()
    auto_remediate_ids = await get_auto_remediate_check_ids()
    run_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # EXO token/tenant – acquired lazily on first EXO check
    exo_token: str | None = None
    exo_tenant_id: str | None = None

    results: list[dict[str, Any]] = []
    for bp in _BEST_PRACTICES:
        check_id = bp["id"]
        if check_id not in enabled:
            continue
        check_name = bp["name"]
        source_type = bp.get("source_type", "graph")
        runner: BestPracticeRunner = bp["source"]
        try:
            if source_type == "exo":
                if exo_token is None:
                    exo_token, exo_tenant_id = await _acquire_exo_access_token(company_id)
                raw = await runner(exo_token, exo_tenant_id)  # type: ignore[call-arg]
            else:
                raw = await runner(graph_token)  # type: ignore[call-arg]
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

        # Auto-remediate if the check failed and auto-remediation is enabled
        if status == STATUS_FAIL and check_id in auto_remediate_ids:
            log_info(
                "M365 best practice auto-remediation triggered",
                company_id=company_id,
                check_id=check_id,
            )
            await remediate_check(company_id=company_id, check_id=check_id)

        results.append({
            "check_id": check_id,
            "check_name": check_name,
            "status": status,
            "details": details,
            "run_at": run_at,
            "remediation": get_remediation(check_id) if status == STATUS_FAIL else None,
            "has_remediation": bool(bp.get("has_remediation")),
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
            "has_remediation": bool(bp_meta.get("has_remediation")),
            "remediation_status": row.get("remediation_status"),
            "remediated_at": row.get("remediated_at"),
        })
    return out


async def remediate_check(company_id: int, check_id: str) -> dict[str, Any]:
    """Attempt automated remediation for a single best-practice check.

    Looks up the remediation command from the catalog, executes it via the
    Exchange Online REST API (for EXO-type checks), records the outcome in the
    database, and returns a result dict with ``success`` (bool) and ``message``
    (str) keys.

    Currently only EXO-type checks that declare ``has_remediation: True``
    support automated remediation.  For all other check IDs the function
    returns a failure result without making any external calls.
    """
    bp = _catalog_map().get(check_id)
    if not bp or not bp.get("has_remediation"):
        return {
            "success": False,
            "message": "Automated remediation is not available for this check.",
        }

    source_type = bp.get("source_type", "graph")
    remediated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    if source_type == "exo":
        try:
            exo_token, tenant_id = await _acquire_exo_access_token(company_id)
        except M365Error as exc:
            log_error(
                "M365 best practice remediation – EXO token acquisition failed",
                company_id=company_id,
                check_id=check_id,
                error=str(exc),
            )
            await bp_repo.update_remediation_status(
                company_id=company_id,
                check_id=check_id,
                remediation_status="failed",
                remediated_at=remediated_at,
            )
            return {
                "success": False,
                "message": f"Unable to acquire Exchange Online token: {exc}",
            }

        cmdlet = bp.get("remediation_cmdlet", "")
        params = bp.get("remediation_params") or {}
        try:
            await _exo_invoke_command(exo_token, tenant_id, cmdlet, params)
            success = True
        except M365Error as exc:
            log_error(
                "M365 best practice remediation command failed",
                company_id=company_id,
                check_id=check_id,
                cmdlet=cmdlet,
                error=str(exc),
            )
            success = False
    else:
        success = False

    remediation_status = "success" if success else "failed"
    await bp_repo.update_remediation_status(
        company_id=company_id,
        check_id=check_id,
        remediation_status=remediation_status,
        remediated_at=remediated_at,
    )

    log_info(
        "M365 best practice remediation attempted",
        company_id=company_id,
        check_id=check_id,
        success=success,
    )

    if success:
        return {
            "success": True,
            "message": (
                f"Remediation command executed successfully. "
                f"Re-evaluate the check to confirm the change took effect."
            ),
        }
    return {
        "success": False,
        "message": "Remediation command failed. Check that the app has the required Exchange Online permissions.",
    }


# Status constants re-exported for convenience.
__all__ = [
    "STATUS_PASS",
    "STATUS_FAIL",
    "STATUS_UNKNOWN",
    "STATUS_NOT_APPLICABLE",
    "list_best_practices",
    "list_settings_with_catalog",
    "get_enabled_check_ids",
    "get_auto_remediate_check_ids",
    "set_enabled_checks",
    "run_best_practices",
    "get_last_results",
    "get_remediation",
    "remediate_check",
]
