"""Huntress API client + daily snapshot refresh service.

The Huntress integration has no UI settings — credentials are read from the
environment (``HUNTRESS_API_KEY`` / ``HUNTRESS_API_SECRET`` /
``HUNTRESS_BASE_URL`` plus ``CURRICULA_API_KEY`` /
``CURRICULA_API_SECRET`` / ``CURRICULA_BASE_URL`` for Managed SAT)
and the module is gated by the standard ``integration_modules``
enable/disable toggle.

The service exposes thin wrappers per Huntress endpoint family and a
``refresh_company`` orchestrator that pulls every product (EDR, ITDR, SAT,
SIEM, SOC), normalises the values, and writes a snapshot to the
``huntress_*`` tables. Report rendering reads exclusively from those snapshots
so it never makes live API calls.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import httpx

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import companies as company_repo
from app.repositories import huntress as huntress_repo
from app.services import modules as modules_service


class HuntressConfigurationError(RuntimeError):
    """Raised when Huntress credentials are missing or the module is disabled."""


REQUEST_TIMEOUT = 30.0
# Huntress publishes a 60 req/min limit; keep a small buffer between calls.
_REQUEST_INTERVAL_SECONDS = 1.1
_request_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Configuration / client helpers
# ---------------------------------------------------------------------------


def _redact_url(url: str) -> str:
    """Return ``url`` without query string for safe logging."""
    if not url:
        return ""
    return url.split("?", 1)[0]


def _get_credentials() -> dict[str, str] | None:
    settings = get_settings()
    api_key = (settings.huntress_api_key or "").strip()
    api_secret = (settings.huntress_api_secret or "").strip()
    base_url = (settings.huntress_base_url or "").strip().rstrip("/")
    if not api_key or not api_secret or not base_url:
        return None
    return {"api_key": api_key, "api_secret": api_secret, "base_url": base_url}



def _get_curricula_credentials() -> dict[str, str] | None:
    settings = get_settings()
    api_key = (settings.curricula_api_key or "").strip()
    api_secret = (settings.curricula_api_secret or "").strip()
    base_url = (settings.curricula_base_url or "").strip().rstrip("/")
    if not api_key or not api_secret or not base_url:
        return None
    return {"api_key": api_key, "api_secret": api_secret, "base_url": base_url}


def credentials_status() -> dict[str, bool]:
    """Lightweight, non-secret status used by the modules admin page."""
    settings = get_settings()
    return {
        "api_key_present": bool((settings.huntress_api_key or "").strip()),
        "api_secret_present": bool((settings.huntress_api_secret or "").strip()),
        "base_url_present": bool((settings.huntress_base_url or "").strip()),
        "curricula_api_key_present": bool((settings.curricula_api_key or "").strip()),
        "curricula_api_secret_present": bool(
            (settings.curricula_api_secret or "").strip()
        ),
        "curricula_base_url_present": bool(
            (settings.curricula_base_url or "").strip()
        ),
    }


async def is_module_enabled() -> bool:
    try:
        module = await modules_service.get_module("huntress", redact=True)
    except Exception:  # pragma: no cover - defensive
        return False
    return bool(module and module.get("enabled"))


def _client(credentials: Mapping[str, str]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=credentials["base_url"],
        auth=(credentials["api_key"], credentials["api_secret"]),
        timeout=REQUEST_TIMEOUT,
        headers={"Accept": "application/json"},
    )


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    params: Mapping[str, Any] | None = None,
    *,
    allow_not_found: bool = False,
) -> Any:
    """GET ``path`` and return decoded JSON, applying a small per-call rate-limit.

    When *allow_not_found* is ``True`` a 404 response is treated as "feature
    not available for this account" and ``None`` is returned instead of
    raising an exception.
    """

    async with _request_lock:
        try:
            response = await client.get(path, params=dict(params or {}))
        except httpx.HTTPError as exc:  # pragma: no cover - network failure
            log_error(
                "Huntress request failed",
                url=_redact_url(path),
                error=str(exc),
            )
            raise
        await asyncio.sleep(_REQUEST_INTERVAL_SECONDS)

    if response.status_code == 404 and allow_not_found:
        log_info(
            "Huntress feature not available for this account",
            url=_redact_url(str(response.request.url)),
        )
        return None
    if response.status_code >= 400:
        log_error(
            "Huntress API returned error",
            status_code=response.status_code,
            url=_redact_url(str(response.request.url)),
        )
        response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        log_error(
            "Huntress response was not valid JSON",
            url=_redact_url(str(response.request.url)),
            error=str(exc),
        )
        raise


# ---------------------------------------------------------------------------
# Endpoint wrappers
# ---------------------------------------------------------------------------


async def list_organizations() -> list[dict[str, Any]]:
    """Return Huntress organisations available to the configured account."""
    credentials = _get_credentials()
    if not credentials:
        raise HuntressConfigurationError(
            "Huntress credentials are not configured (set HUNTRESS_API_KEY and HUNTRESS_API_SECRET)."
        )

    organisations: list[dict[str, Any]] = []
    async with _client(credentials) as client:
        page_token: str | None = None
        # Cap pagination so we never loop indefinitely on misconfigured tenants.
        for _ in range(50):
            params: dict[str, Any] = {"limit": 100}
            if page_token:
                params["page_token"] = page_token
            payload = await _get_json(client, "/organizations", params)
            chunk = _extract_list(payload, key="organizations")
            if not chunk:
                break
            organisations.extend(chunk)
            page_token = _extract_next_page_token(payload)
            if not page_token:
                break
    return organisations




async def get_latest_summary_report(org_id: str, report_type: str = "monthly_summary") -> dict[str, Any] | None:
    """Return the most recent summary report for an organisation."""
    credentials = _get_credentials()
    if not credentials:
        raise HuntressConfigurationError("Huntress credentials are not configured.")

    async with _client(credentials) as client:
        payload = await _get_json(
            client,
            "/reports",
            {"organization_id": org_id, "type": report_type, "limit": 1},
            allow_not_found=True,
        )
    reports = _extract_list(payload, key="reports")
    for report in reports:
        if isinstance(report, Mapping):
            return dict(report)
    if isinstance(payload, Mapping):
        data = payload.get("data")
        if isinstance(data, Mapping):
            return dict(data)
    return None


async def get_edr_summary(org_id: str) -> dict[str, int]:
    """Return EDR counters from the latest Huntress summary report."""
    report = await get_latest_summary_report(org_id)
    payload = report if isinstance(report, Mapping) else {}
    return {
        "active_incidents": _coerce_int(payload.get("incidents_reported")),
        "resolved_incidents": _coerce_int(payload.get("incidents_resolved")),
        "signals_investigated": _coerce_int(payload.get("signals_investigated")),
    }


async def get_itdr_summary(org_id: str) -> dict[str, int]:
    """Return ITDR investigations completed from the latest summary report."""
    report = await get_latest_summary_report(org_id)
    payload = report if isinstance(report, Mapping) else {}
    return {"signals_investigated": _coerce_int(payload.get("itdr_investigations_completed"))}


async def get_sat_summary(org_id: str) -> dict[str, Any] | None:
    """Return Huntress Managed SAT learner and progress rollups for an account.

    Curricula/Huntress Managed SAT uses the JSON:API REST API at
    ``https://mycurricula.com/api/v1`` with client-credentials API clients. The
    useful billing/reporting metric exposed by third-party reconciliation docs
    is the active learner count; where assignment progress is returned, we also
    calculate average completion and score from the learner rows.
    """
    rows = await get_sat_learner_breakdown(org_id)
    if rows is None:
        return None

    learner_ids = {
        str(row.get("learner_external_id") or row.get("learner_email") or "").strip()
        for row in rows
        if row.get("learner_external_id") or row.get("learner_email")
    }
    completions = [float(row.get("completion_percent") or 0) for row in rows]
    scores = [
        float(row.get("score") or 0)
        for row in rows
        if row.get("score") is not None
    ]
    return {
        "enrolled_learners": len(learner_ids) or len(rows),
        "avg_completion_rate": (
            (sum(completions) / len(completions)) if completions else 0
        ),
        "avg_score": (sum(scores) / len(scores)) if scores else 0,
        "phishing_clicks": sum(
            1 for row in rows if float(row.get("click_rate") or 0) > 0
        ),
        "phishing_compromises": sum(
            1 for row in rows if float(row.get("compromise_rate") or 0) > 0
        ),
        "phishing_reports": sum(
            1 for row in rows if float(row.get("report_rate") or 0) > 0
        ),
    }


async def get_sat_learner_breakdown(org_id: str) -> list[dict[str, Any]] | None:
    """Return per-learner SAT progress rows from the Curricula JSON:API.

    The public Stoplight docs describe Curricula as a JSON:API REST API for
    channel partners. Tenant responses can vary by API version, so this parser
    accepts common JSON:API shapes and normalises learner/account assignment
    fields into the snapshot table schema used by reports.
    """
    credentials = _get_curricula_credentials()
    if not credentials:
        raise HuntressConfigurationError(
            "Curricula credentials are not configured (set CURRICULA_API_KEY and "
            "CURRICULA_API_SECRET)."
        )

    async with _client(credentials) as client:
        payload = await _get_json(
            client,
            f"/accounts/{org_id}/learners",
            {"include": "assignments,progress", "page[size]": 100},
            allow_not_found=True,
        )
    if payload is None:
        return None
    learners = _extract_list(payload, key="learners")
    return [_normalise_sat_learner(row) for row in learners if isinstance(row, Mapping)]


async def get_siem_data_volume(org_id: str, days: int = 30) -> dict[str, Any] | None:
    """Return SIEM log-volume counters from the latest Huntress summary report."""
    report = await get_latest_summary_report(org_id)
    payload = report if isinstance(report, Mapping) else {}
    total_logs = _coerce_int(payload.get("siem_total_logs") or payload.get("siem_ingested_logs"))
    if total_logs <= 0:
        return None
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return {
        "data_collected_bytes_30d": total_logs,
        "window_start": start.replace(tzinfo=None),
        "window_end": end.replace(tzinfo=None),
    }


async def get_soc_event_count(org_id: str) -> dict[str, int] | None:
    """Return SOC event-analysis counters from the latest summary report."""
    report = await get_latest_summary_report(org_id)
    payload = report if isinstance(report, Mapping) else {}
    total = _coerce_int(payload.get("events_analyzed"))
    if total <= 0:
        return None
    return {"total_events_analysed": total}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def refresh_company(company: Mapping[str, Any]) -> dict[str, Any]:
    """Refresh every Huntress product snapshot for one company.

    Each product is pulled inside its own ``try/except`` so a single failing
    endpoint does not blank the rest of the dashboard.
    """

    company_id_raw = company.get("id")
    org_id = (company.get("huntress_organization_id") or "").strip() if isinstance(
        company.get("huntress_organization_id"), str
    ) else company.get("huntress_organization_id")
    if company_id_raw is None or not org_id:
        return {
            "company_id": company_id_raw,
            "status": "skipped",
            "reason": "Missing company id or Huntress organisation id",
        }
    company_id = int(company_id_raw)
    org_id = str(org_id)
    snapshot_at = datetime.utcnow()
    summary: dict[str, Any] = {
        "company_id": company_id,
        "huntress_organization_id": org_id,
        "errors": {},
    }

    async def _safe(name: str, coro):
        try:
            return await coro
        except HuntressConfigurationError:
            raise
        except Exception as exc:  # noqa: BLE001 - log and continue
            log_error(
                "Huntress sync step failed",
                company_id=company_id,
                step=name,
                error=str(exc),
            )
            summary["errors"][name] = str(exc)
            return None

    edr = await _safe("edr", get_edr_summary(org_id))
    if edr is not None:
        await huntress_repo.upsert_edr_stats(
            company_id,
            active_incidents=edr["active_incidents"],
            resolved_incidents=edr["resolved_incidents"],
            signals_investigated=edr["signals_investigated"],
            snapshot_at=snapshot_at,
        )
        summary["edr"] = edr

    itdr = await _safe("itdr", get_itdr_summary(org_id))
    if itdr is not None:
        await huntress_repo.upsert_itdr_stats(
            company_id,
            signals_investigated=itdr["signals_investigated"],
            snapshot_at=snapshot_at,
        )
        summary["itdr"] = itdr

    sat = await _safe("sat", get_sat_summary(org_id))
    if sat is not None:
        await huntress_repo.upsert_sat_stats(
            company_id,
            enrolled_learners=sat.get("enrolled_learners", 0),
            avg_completion_rate=sat["avg_completion_rate"],
            avg_score=sat["avg_score"],
            phishing_clicks=sat["phishing_clicks"],
            phishing_compromises=sat["phishing_compromises"],
            phishing_reports=sat["phishing_reports"],
            snapshot_at=snapshot_at,
        )
        summary["sat"] = sat

    sat_rows = await _safe("sat_learners", get_sat_learner_breakdown(org_id))
    if sat_rows is not None:
        await huntress_repo.replace_sat_learner_progress(
            company_id, sat_rows, snapshot_at=snapshot_at
        )
        summary["sat_learner_rows"] = len(sat_rows)

    siem = await _safe("siem", get_siem_data_volume(org_id, days=30))
    if siem is not None:
        await huntress_repo.upsert_siem_stats(
            company_id,
            data_collected_bytes_30d=siem["data_collected_bytes_30d"],
            window_start=siem["window_start"],
            window_end=siem["window_end"],
            snapshot_at=snapshot_at,
        )
        summary["siem"] = {
            "data_collected_bytes_30d": siem["data_collected_bytes_30d"],
        }

    soc = await _safe("soc", get_soc_event_count(org_id))
    if soc is not None:
        await huntress_repo.upsert_soc_stats(
            company_id,
            total_events_analysed=soc["total_events_analysed"],
            snapshot_at=snapshot_at,
        )
        summary["soc"] = soc

    summary["status"] = "ok" if not summary["errors"] else "partial"
    return summary


async def refresh_all_companies() -> dict[str, Any]:
    """Refresh Huntress snapshots for every linked company."""

    if not _get_credentials():
        log_info("Huntress credentials missing; skipping refresh")
        return {"status": "skipped", "reason": "credentials_missing", "companies": []}
    if not await is_module_enabled():
        log_info("Huntress module disabled; skipping refresh")
        return {"status": "skipped", "reason": "module_disabled", "companies": []}

    companies = await company_repo.list_companies()
    results: list[dict[str, Any]] = []
    refreshed = 0
    skipped = 0
    failed = 0
    for company in companies:
        if not company.get("huntress_organization_id"):
            skipped += 1
            continue
        try:
            result = await refresh_company(company)
            results.append(result)
            if result.get("status") == "ok":
                refreshed += 1
            elif result.get("status") == "partial":
                refreshed += 1
            else:
                skipped += 1
        except HuntressConfigurationError as exc:
            log_error("Huntress credentials missing during refresh", error=str(exc))
            return {"status": "skipped", "reason": "credentials_missing", "companies": results}
        except Exception as exc:  # noqa: BLE001
            log_error(
                "Huntress refresh raised an unexpected error",
                company_id=company.get("id"),
                error=str(exc),
            )
            failed += 1
            results.append(
                {
                    "company_id": company.get("id"),
                    "status": "failed",
                    "error": str(exc),
                }
            )

    summary = {
        "status": "ok",
        "refreshed": refreshed,
        "skipped": skipped,
        "failed": failed,
        "companies": results,
    }
    log_info(
        "Huntress refresh completed",
        refreshed=refreshed,
        skipped=skipped,
        failed=failed,
    )
    return summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_list(payload: Any, *, key: str) -> list[Any]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, (dict, list))]
    if isinstance(payload, Mapping):
        for candidate_key in (key, "data", "results", "items"):
            value = payload.get(candidate_key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, (dict, list))]
    return []



def _jsonapi_attrs(row: Mapping[str, Any]) -> dict[str, Any]:
    attrs = row.get("attributes")
    if isinstance(attrs, Mapping):
        merged = dict(attrs)
        if row.get("id") is not None:
            merged.setdefault("id", row.get("id"))
        return merged
    return dict(row)


def _first_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) is not None:
            return payload.get(key)
    return None


def _normalise_sat_learner(row: Mapping[str, Any]) -> dict[str, Any]:
    data = _jsonapi_attrs(row)
    learner_id = _first_value(
        data, "learner_external_id", "learner_id", "user_id", "id", "uuid"
    )
    assignment_id = _first_value(
        data, "assignment_id", "training_assignment_id", "campaign_id", "course_id"
    )
    if not assignment_id:
        assignment_id = "learner-summary"
    completion = _first_value(
        data,
        "completion_percent",
        "progress",
        "progress_percent",
        "percent_complete",
        "completion_rate",
    )
    return {
        "learner_external_id": str(learner_id or data.get("email") or "").strip(),
        "learner_email": _first_value(data, "learner_email", "email", "user_email"),
        "learner_name": _first_value(
            data, "learner_name", "name", "full_name", "display_name"
        ),
        "assignment_id": str(assignment_id),
        "assignment_name": _first_value(
            data, "assignment_name", "training_name", "course_name", "campaign_name"
        ),
        "status": _first_value(data, "status", "state", "enrollment_status"),
        "completion_percent": _coerce_float(completion),
        "score": _coerce_float(_first_value(data, "score", "average_score", "quiz_score")),
        "click_rate": _coerce_float(_first_value(data, "click_rate", "phishing_click_rate")),
        "compromise_rate": _coerce_float(_first_value(data, "compromise_rate", "phishing_compromise_rate")),
        "report_rate": _coerce_float(_first_value(data, "report_rate", "phishing_report_rate")),
    }

def _extract_next_page_token(payload: Any) -> str | None:
    """Return the ``next_page_token`` from a Huntress paginated response, or ``None``."""
    if not isinstance(payload, Mapping):
        return None
    pagination = payload.get("pagination")
    if isinstance(pagination, Mapping):
        token = pagination.get("next_page_token")
        if token:
            return str(token)
    return None


def _extract_total(payload: Any, list_key: str) -> int:
    if isinstance(payload, Mapping):
        for key in ("total", "total_count", "count"):
            value = payload.get(key)
            if value is not None:
                return _coerce_int(value)
        nested = _extract_list(payload, key=list_key)
        return len(nested)
    if isinstance(payload, list):
        return len(payload)
    return 0


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _coerce_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "HuntressConfigurationError",
    "credentials_status",
    "get_latest_summary_report",
    "get_edr_summary",
    "get_itdr_summary",
    "get_sat_summary",
    "get_sat_learner_breakdown",
    "get_siem_data_volume",
    "get_soc_event_count",
    "is_module_enabled",
    "list_organizations",
    "refresh_all_companies",
    "refresh_company",
]
