"""Huntress API client + daily snapshot refresh service.

The Huntress integration has no UI settings — credentials are read from the
environment (``HUNTRESS_API_KEY`` / ``HUNTRESS_API_SECRET`` /
``HUNTRESS_BASE_URL``) and the module is gated by the standard
``integration_modules`` enable/disable toggle.

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


def credentials_status() -> dict[str, bool]:
    """Lightweight, non-secret status used by the modules admin page."""
    settings = get_settings()
    return {
        "api_key_present": bool((settings.huntress_api_key or "").strip()),
        "api_secret_present": bool((settings.huntress_api_secret or "").strip()),
        "base_url_present": bool((settings.huntress_base_url or "").strip()),
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
    client: httpx.AsyncClient, path: str, params: Mapping[str, Any] | None = None
) -> Any:
    """GET ``path`` and return decoded JSON, applying a small per-call rate-limit."""

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
        page = 1
        # Cap pagination so we never loop indefinitely on misconfigured tenants.
        while page <= 50:
            payload = await _get_json(client, "/organizations", {"page": page, "limit": 100})
            chunk = _extract_list(payload, key="organizations")
            if not chunk:
                break
            organisations.extend(chunk)
            if len(chunk) < 100:
                break
            page += 1
    return organisations


async def get_edr_summary(org_id: str) -> dict[str, int]:
    """Return ``{active_incidents, resolved_incidents, signals_investigated}``."""
    credentials = _get_credentials()
    if not credentials:
        raise HuntressConfigurationError("Huntress credentials are not configured.")

    async with _client(credentials) as client:
        active_payload = await _get_json(
            client,
            "/incident_reports",
            {"organization_id": org_id, "status": "open", "limit": 1},
        )
        resolved_payload = await _get_json(
            client,
            "/incident_reports",
            {"organization_id": org_id, "status": "closed", "limit": 1},
        )
        signals_payload = await _get_json(
            client,
            "/signals",
            {"organization_id": org_id, "product": "edr", "limit": 1},
        )

    return {
        "active_incidents": _extract_total(active_payload, "incident_reports"),
        "resolved_incidents": _extract_total(resolved_payload, "incident_reports"),
        "signals_investigated": _extract_total(signals_payload, "signals"),
    }


async def get_itdr_summary(org_id: str) -> dict[str, int]:
    """Return ``{signals_investigated}`` for the ITDR product."""
    credentials = _get_credentials()
    if not credentials:
        raise HuntressConfigurationError("Huntress credentials are not configured.")

    async with _client(credentials) as client:
        payload = await _get_json(
            client,
            "/signals",
            {"organization_id": org_id, "product": "itdr", "limit": 1},
        )
    return {"signals_investigated": _extract_total(payload, "signals")}


async def get_sat_summary(org_id: str) -> dict[str, Any]:
    """Return aggregated SAT statistics across every learner."""
    credentials = _get_credentials()
    if not credentials:
        raise HuntressConfigurationError("Huntress credentials are not configured.")

    async with _client(credentials) as client:
        summary = await _get_json(
            client, "/sat/learners/summary", {"organization_id": org_id}
        )
        phishing = await _get_json(
            client, "/sat/phishing/summary", {"organization_id": org_id}
        )

    summary_payload = summary if isinstance(summary, Mapping) else {}
    phishing_payload = phishing if isinstance(phishing, Mapping) else {}

    return {
        "avg_completion_rate": _coerce_float(summary_payload.get("average_completion_rate")),
        "avg_score": _coerce_float(summary_payload.get("average_score")),
        "phishing_clicks": _coerce_int(phishing_payload.get("clicks")),
        "phishing_compromises": _coerce_int(phishing_payload.get("compromises")),
        "phishing_reports": _coerce_int(phishing_payload.get("reports")),
    }


async def get_sat_learner_breakdown(org_id: str) -> list[dict[str, Any]]:
    """Return per-learner per-assignment progress + phishing rates."""
    credentials = _get_credentials()
    if not credentials:
        raise HuntressConfigurationError("Huntress credentials are not configured.")

    rows: list[dict[str, Any]] = []
    async with _client(credentials) as client:
        page = 1
        while page <= 100:
            payload = await _get_json(
                client,
                "/sat/learners",
                {"organization_id": org_id, "page": page, "limit": 100},
            )
            learners = _extract_list(payload, key="learners")
            if not learners:
                break
            for learner in learners:
                if not isinstance(learner, Mapping):
                    continue
                learner_id = str(learner.get("id") or learner.get("external_id") or "").strip()
                if not learner_id:
                    continue
                learner_email = (learner.get("email") or "").strip() or None
                learner_name = (learner.get("name") or "").strip() or None
                phishing = learner.get("phishing") if isinstance(learner.get("phishing"), Mapping) else {}
                click_rate = _coerce_float(phishing.get("click_rate"))
                compromise_rate = _coerce_float(phishing.get("compromise_rate"))
                report_rate = _coerce_float(phishing.get("report_rate"))
                assignments = learner.get("assignments")
                if not isinstance(assignments, list):
                    continue
                for assignment in assignments:
                    if not isinstance(assignment, Mapping):
                        continue
                    assignment_id = str(assignment.get("id") or "").strip()
                    if not assignment_id:
                        continue
                    rows.append(
                        {
                            "learner_external_id": learner_id,
                            "learner_email": learner_email,
                            "learner_name": learner_name,
                            "assignment_id": assignment_id,
                            "assignment_name": assignment.get("name"),
                            "status": assignment.get("status"),
                            "completion_percent": _coerce_float(
                                assignment.get("completion_percent")
                            ),
                            "score": _coerce_float(assignment.get("score")),
                            "click_rate": click_rate,
                            "compromise_rate": compromise_rate,
                            "report_rate": report_rate,
                        }
                    )
            if len(learners) < 100:
                break
            page += 1
    return rows


async def get_siem_data_volume(org_id: str, days: int = 30) -> dict[str, Any]:
    """Return total SIEM data ingested over the trailing ``days`` window."""
    credentials = _get_credentials()
    if not credentials:
        raise HuntressConfigurationError("Huntress credentials are not configured.")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    async with _client(credentials) as client:
        payload = await _get_json(
            client,
            "/siem/usage",
            {
                "organization_id": org_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
    payload_map = payload if isinstance(payload, Mapping) else {}
    bytes_total = _coerce_int(
        payload_map.get("total_bytes")
        or payload_map.get("bytes")
        or payload_map.get("data_bytes")
    )
    return {
        "data_collected_bytes_30d": bytes_total,
        "window_start": start.replace(tzinfo=None),
        "window_end": end.replace(tzinfo=None),
    }


async def get_soc_event_count(org_id: str) -> dict[str, int]:
    """Return the SOC ``total_events_analysed`` counter."""
    credentials = _get_credentials()
    if not credentials:
        raise HuntressConfigurationError("Huntress credentials are not configured.")

    async with _client(credentials) as client:
        payload = await _get_json(
            client, "/soc/summary", {"organization_id": org_id}
        )
    payload_map = payload if isinstance(payload, Mapping) else {}
    return {
        "total_events_analysed": _coerce_int(
            payload_map.get("total_events_analysed")
            or payload_map.get("events_analysed")
            or payload_map.get("total_events")
        )
    }


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
