"""Microsoft 365 Secure Score guidance service.

Microsoft's **Secure Score** is the primary KPI for a tenant's security
posture.  Its improvement activities are effectively Microsoft's own
best-practice checklist: each activity is worth a fixed number of points, and
the tenant's ``maxScore`` is *already scoped to the services licensed for that
tenant*.  In other words, a tenant on Business Premium has a different
achievable maximum than a tenant on E5, and every activity the Secure Score
API returns for a tenant is one that tenant can actually implement with the
licences it already holds.

This module turns that into actionable guidance:

* :func:`build_secure_score_guidance` is a pure function that takes the raw
  ``secureScores`` and ``secureScoreActivities`` Graph payloads and computes
  the tenant's current score, its **license-aware achievable maximum**, the
  gap between them, and a ranked list of *achievable goals* (the not-yet-applied
  improvement activities, ordered by the points they would add).
* :func:`get_secure_score_guidance` performs the Graph calls for a company and
  delegates to the pure builder.  It is deliberately defensive: any error
  returns an ``available: False`` payload with a human-readable reason instead
  of raising, so the Best Practices page keeps working.
* :func:`check_secure_score_max_reached` is the runner for the
  ``bp_secure_score_max_reached`` best-practice check, which passes only when
  the tenant has reached the maximum Secure Score possible for its licence.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.core.logging import log_error, log_info
from app.services.cis_benchmark import _fail, _pass, _unknown
from app.services.m365 import M365Error, _graph_get, _graph_get_all, acquire_access_token

# Microsoft Graph Secure Score endpoints.  Both are documented to require the
# ``SecurityEvents.Read.All`` application permission, which MyPortal already
# requests (see ``app/services/m365_access_baseline.py``).
_SECURE_SCORES_URL = "https://graph.microsoft.com/v1.0/security/secureScores"
_SECURE_SCORE_ACTIVITIES_URL = "https://graph.microsoft.com/v1.0/security/secureScoreActivities"

# The maximum Secure Score a tenant can earn for a given licence.  Because the
# Secure Score API only ever returns activities applicable to the tenant's
# licensed services, ``maxScore`` is the *achievable* maximum for that tenant.
# A tiny tolerance absorbs fractional scoring and rounding drift.
_MAXIMUM_SCORE_EPSILON = 0.5

# ---------------------------------------------------------------------------
# Secure Score activity status values (normalised to lowercase)
# ---------------------------------------------------------------------------
ACTIVITY_STATUS_APPLIED = "applied"
ACTIVITY_STATUS_PARTIAL = "partial"
ACTIVITY_STATUS_PENDING = "pending"

_ACTIVITY_STATUS_MAP = {
    "applied": ACTIVITY_STATUS_APPLIED,
    "partiallyapplied": ACTIVITY_STATUS_PARTIAL,
    "pending": ACTIVITY_STATUS_PENDING,
}

_ACTIVITY_STATUS_DISPLAY = {
    ACTIVITY_STATUS_APPLIED: "Applied",
    ACTIVITY_STATUS_PARTIAL: "Partially applied",
    ACTIVITY_STATUS_PENDING: "Pending",
}

# Well-known Secure Score activity categories.  The API returns a machine
# ``category`` token and a human ``categoryDisplayName``; we fall back to a
# title-cased token when the display name is absent.
_CATEGORY_DISPLAY_FALLBACK = {
    "identity": "Identity",
    "data": "Data",
    "app": "Applications",
    "device": "Devices",
    "network": "Network",
    "threats": "Threats",
    "automation": "Automation",
    "recovery": "Recovery",
    "other": "Other",
}


def _to_float(value: Any) -> float | None:
    """Best-effort conversion of a Secure Score numeric field to ``float``."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_activity(activity: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a single ``secureScoreActivity`` object defensively.

    Only the guaranteed fields (``id``, ``displayName``, ``score``, ``status``)
    are relied on for the arithmetic; ``category`` / ``categoryDisplayName``
    are optional presentation fields and degrade gracefully when absent.
    """
    activity_id = str(activity.get("id") or "").strip()
    name = str(activity.get("displayName") or activity_id or "Secure Score activity").strip()

    score = _to_float(activity.get("score"))
    score = max(0.0, score) if score is not None else 0.0

    raw_status = str(activity.get("status") or "").strip().lower()
    status = _ACTIVITY_STATUS_MAP.get(raw_status, ACTIVITY_STATUS_PENDING)

    raw_category = str(activity.get("category") or "").strip()
    category_display = (
        str(activity.get("categoryDisplayName") or "").strip()
        or _CATEGORY_DISPLAY_FALLBACK.get(raw_category.lower(), raw_category.title() if raw_category else "Other")
    )

    remediation = (
        f"Open the Microsoft 365 Defender portal, go to Secure Score, and implement the "
        f"'{name}' improvement. This activity is available with the services this tenant "
        "already has licensed, so it counts towards the tenant's achievable maximum score."
    )

    return {
        "id": activity_id,
        "name": name,
        "score": score,
        "status": status,
        "status_display": _ACTIVITY_STATUS_DISPLAY[status],
        "category": raw_category or None,
        "category_display": category_display,
        "remediation": remediation,
    }


def _build_by_category(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll the normalised activities up per category, biggest totals first."""
    rollup: dict[str, dict[str, Any]] = {}
    for activity in activities:
        key = activity["category"] or "other"
        bucket = rollup.setdefault(
            key,
            {
                "category": key,
                "category_display": activity["category_display"],
                "total": 0.0,
                "applied": 0.0,
                "pending": 0.0,
                "activity_count": 0,
                "pending_count": 0,
            },
        )
        bucket["total"] += activity["score"]
        bucket["activity_count"] += 1
        if activity["status"] == ACTIVITY_STATUS_APPLIED:
            bucket["applied"] += activity["score"]
        else:
            bucket["pending"] += activity["score"]
            bucket["pending_count"] += 1
    return sorted(rollup.values(), key=lambda item: (-item["total"], item["category_display"]))


def build_secure_score_guidance(
    score_payload: Mapping[str, Any] | None,
    activities_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compute license-aware Secure Score guidance from raw Graph payloads.

    ``score_payload`` is the response from ``GET /security/secureScores``
    (we use the latest object) and ``activities_payload`` is the response from
    ``GET /security/secureScoreActivities``.  Either may be ``None`` when the
    respective call failed; the function degrades to whatever it can compute
    and reports ``available: False`` only when there is no score data at all.
    """
    score_object: Mapping[str, Any] | None = None
    if isinstance(score_payload, Mapping):
        values = score_payload.get("value")
        if isinstance(values, list) and values:
            score_object = values[0]

    current = _to_float(score_object.get("currentScore")) if score_object else None
    maximum = _to_float(score_object.get("maxScore")) if score_object else None

    activities: list[dict[str, Any]] = []
    if current is None and maximum is None:
        # No score data at all – return an unavailable payload early.
        return {
            "available": False,
            "reason": (
                "No Secure Score data is available for this tenant yet. Re-run the checks, or "
                "confirm the app has the SecurityEvents.Read.All permission."
            ),
            "current": None,
            "maximum": None,
            "percentage": None,
            "gap": None,
            "at_maximum": False,
            "activity_count": 0,
            "applied_points": 0.0,
            "partial_points": 0.0,
            "pending_points": 0.0,
            "achievable_points": None,
            "activities": [],
            "recommendations": [],
            "by_category": [],
        }

    raw_activities: Any = activities_payload.get("value") if isinstance(activities_payload, Mapping) else None
    if isinstance(raw_activities, list):
        activities = [
            _normalise_activity(activity) for activity in raw_activities if isinstance(activity, Mapping)
        ]

    activities.sort(key=lambda item: (-item["score"], item["name"]))
    recommendations = [a for a in activities if a["status"] != ACTIVITY_STATUS_APPLIED]

    applied_points = sum(a["score"] for a in activities if a["status"] == ACTIVITY_STATUS_APPLIED)
    partial_points = sum(a["score"] for a in activities if a["status"] == ACTIVITY_STATUS_PARTIAL)
    pending_points = sum(a["score"] for a in activities if a["status"] == ACTIVITY_STATUS_PENDING)

    # The tenant's achievable maximum.  Prefer the authoritative ``maxScore``;
    # fall back to the sum of the activity points when only the activities are
    # available (e.g. the score endpoint is blocked but activities are not).
    achievable_maximum = maximum
    if achievable_maximum is None:
        achievable_maximum = sum(a["score"] for a in activities)

    if current is None and achievable_maximum is not None:
        current = 0.0
    if maximum is None and achievable_maximum is not None:
        maximum = achievable_maximum

    percentage: float | None = None
    gap: float | None = None
    at_maximum = False
    if maximum is not None and maximum > 0 and current is not None:
        percentage = round((current / maximum) * 100.0, 1)
        gap = max(0.0, maximum - current)
        at_maximum = current >= maximum - _MAXIMUM_SCORE_EPSILON

    return {
        "available": True,
        "reason": None,
        "current": current,
        "maximum": maximum,
        "percentage": percentage,
        "gap": gap,
        "at_maximum": at_maximum,
        "activity_count": len(activities),
        "applied_points": applied_points,
        "partial_points": partial_points,
        "pending_points": pending_points,
        "achievable_points": achievable_maximum,
        "activities": activities,
        "recommendations": recommendations,
        "by_category": _build_by_category(activities),
    }


def _unavailable_guidance(reason: str) -> dict[str, Any]:
    """Return an ``available: False`` guidance payload with a reason."""
    return {
        "available": False,
        "reason": reason,
        "current": None,
        "maximum": None,
        "percentage": None,
        "gap": None,
        "at_maximum": False,
        "activity_count": 0,
        "applied_points": 0.0,
        "partial_points": 0.0,
        "pending_points": 0.0,
        "achievable_points": None,
        "activities": [],
        "recommendations": [],
        "by_category": [],
    }


async def get_secure_score_guidance(company_id: int) -> dict[str, Any]:
    """Fetch and compute Secure Score guidance for ``company_id``.

    Never raises: on any failure it returns an ``available: False`` payload
    with a reason so the Best Practices page can degrade gracefully.
    """
    try:
        token = await acquire_access_token(company_id, force_client_credentials=True)
    except M365Error as exc:
        return _unavailable_guidance(f"Secure Score guidance is unavailable: {exc}")

    score_payload: Mapping[str, Any] | None = None
    activities_payload: Mapping[str, Any] | None = None
    try:
        score_payload = await _graph_get(token, f"{_SECURE_SCORES_URL}?%24top=1")
    except M365Error as exc:
        log_error(
            "M365 secure score: secureScores fetch failed",
            error_info=str(exc),
            company_id=company_id,
        )
        score_payload = None
    try:
        activities_value = await _graph_get_all(token, _SECURE_SCORE_ACTIVITIES_URL)
        activities_payload = {"value": activities_value}
    except M365Error as exc:
        log_error(
            "M365 secure score: secureScoreActivities fetch failed",
            error_info=str(exc),
            company_id=company_id,
        )
        activities_payload = None

    guidance = build_secure_score_guidance(score_payload, activities_payload)

    # Annotate a permission problem so the customer knows what to fix rather
    # than seeing an inexplicable empty panel.
    if not guidance["available"] and score_payload is None and activities_payload is None:
        guidance["reason"] = (
            "Unable to read the Secure Score for this tenant – the app may be missing the "
            "SecurityEvents.Read.All permission. Re-authorise portal access and re-run."
        )

    log_info(
        "M365 secure score guidance",
        company_id=company_id,
        available=guidance["available"],
        current=guidance.get("current"),
        maximum=guidance.get("maximum"),
        recommendations=len(guidance.get("recommendations") or []),
    )
    return guidance


async def check_secure_score_max_reached(token: str) -> dict[str, Any]:
    """Best-practice check: has the tenant reached its license-aware maximum?

    The Secure Score ``maxScore`` is scoped to the tenant's licensed services,
    so passing this check means the tenant has achieved the maximum Secure
    Score possible for the licence it holds.
    """
    check_id = "bp_secure_score_max_reached"
    check_name = "Secure Score has reached the tenant's maximum for its licensed services"
    try:
        data = await _graph_get(token, f"{_SECURE_SCORES_URL}?%24top=1")
        scores = data.get("value", [])
        if not scores:
            return _unknown(
                check_id,
                check_name,
                "No Microsoft Secure Score data available for this tenant yet.",
            )
        current = _to_float(scores[0].get("currentScore"))
        maximum = _to_float(scores[0].get("maxScore"))
        if current is None or maximum is None or maximum <= 0:
            return _unknown(check_id, check_name, "Secure Score values are missing or not numeric.")
        if current >= maximum - _MAXIMUM_SCORE_EPSILON:
            return _pass(
                check_id,
                check_name,
                f"Secure Score is {current:g}/{maximum:g} – the maximum achievable "
                "with this tenant's licensed services.",
            )
        percentage = round((current / maximum) * 100.0, 1)
        return _fail(
            check_id,
            check_name,
            f"Secure Score is {current:g}/{maximum:g} ({percentage}% of maximum). "
            "Complete the pending Secure Score improvement activities (see the "
            "'Achievable goals for your license' panel) to reach the maximum this "
            "licence allows.",
        )
    except M365Error as exc:
        err = str(exc).lower()
        if exc.http_status == 403 or "403" in err or "forbidden" in err or "permissions" in err:
            return _unknown(
                check_id,
                check_name,
                "Unable to retrieve Secure Score – the app may lack SecurityEvents.Read.All permission.",
            )
        return _unknown(check_id, check_name, f"Unable to retrieve Secure Score: {exc}")
