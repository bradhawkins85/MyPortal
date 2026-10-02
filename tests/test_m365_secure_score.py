"""Tests for the Microsoft 365 Secure Score guidance service."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365_secure_score as ss
from app.services.m365 import M365Error


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------
def _score_payload(current=42.5, maximum=80):
    return {"value": [{"id": "1", "currentScore": current, "maxScore": maximum}]}


def _activities_payload():
    return {
        "value": [
            {
                "id": "a1",
                "displayName": "Enforce MFA for admins",
                "score": 5,
                "status": "Applied",
                "category": "identity",
                "categoryDisplayName": "Identity",
            },
            {
                "id": "a2",
                "displayName": "Enable DLP policies",
                "score": 20,
                "status": "Pending",
                "category": "data",
                "categoryDisplayName": "Data",
            },
            {
                "id": "a3",
                "displayName": "Enable Conditional Access",
                "score": 15,
                "status": "PartiallyApplied",
                "category": "identity",
                "categoryDisplayName": "Identity",
            },
            {
                "id": "a4",
                "displayName": "Enable EDR",
                "score": 10,
                "status": "Pending",
                "category": "device",
                "categoryDisplayName": "Devices",
            },
        ]
    }


# ---------------------------------------------------------------------------
# build_secure_score_guidance (pure)
# ---------------------------------------------------------------------------
def test_build_secure_score_guidance_computes_license_aware_values():
    guidance = ss.build_secure_score_guidance(_score_payload(), _activities_payload())

    assert guidance["available"] is True
    assert guidance["current"] == 42.5
    assert guidance["maximum"] == 80.0
    assert guidance["percentage"] == 53.1
    assert guidance["gap"] == 37.5
    assert guidance["at_maximum"] is False
    assert guidance["applied_points"] == 5.0
    assert guidance["partial_points"] == 15.0
    assert guidance["pending_points"] == 30.0
    # Recommendations exclude applied activities and rank by points (descending).
    assert [r["id"] for r in guidance["recommendations"]] == ["a2", "a3", "a4"]
    assert [r["score"] for r in guidance["recommendations"]] == [20.0, 15.0, 10.0]


def test_build_secure_score_guidance_at_maximum():
    guidance = ss.build_secure_score_guidance(_score_payload(80, 80), {"value": []})

    assert guidance["available"] is True
    assert guidance["at_maximum"] is True
    assert guidance["gap"] == 0.0
    assert guidance["percentage"] == 100.0
    assert guidance["recommendations"] == []


def test_build_secure_score_guidance_without_score_reports_unavailable():
    # Without the score object we cannot know the tenant's current score, so
    # the panel reports unavailable rather than guessing (per the builder's
    # documented contract). Activities alone are not enough to be "available".
    guidance = ss.build_secure_score_guidance(None, _activities_payload())

    assert guidance["available"] is False
    assert "No Secure Score data" in guidance["reason"]
    assert guidance["activities"] == []


def test_build_secure_score_guidance_uses_activity_sum_when_max_missing():
    # A score object that reports a current score but is missing maxScore
    # falls back to the sum of the (non-applied-relevant) activity points for
    # the achievable maximum. 5 + 20 + 15 + 10 = 50.
    guidance = ss.build_secure_score_guidance(
        {"value": [{"id": "1", "currentScore": 10}]}, _activities_payload()
    )

    assert guidance["available"] is True
    assert guidance["current"] == 10.0
    assert guidance["maximum"] == 50.0
    assert guidance["gap"] == 40.0


def test_build_secure_score_guidance_unavailable_without_score_or_activities():
    guidance = ss.build_secure_score_guidance(None, None)

    assert guidance["available"] is False
    assert guidance["reason"]
    assert guidance["activities"] == []


def test_build_secure_score_guidance_unknown_status_counts_as_achievable():
    payload = {"value": [{"id": "x", "displayName": "X", "score": 3, "status": "Weird"}]}
    guidance = ss.build_secure_score_guidance(_score_payload(), payload)

    assert guidance["activities"][0]["status"] == "pending"
    assert any(r["id"] == "x" for r in guidance["recommendations"])


def test_build_secure_score_guidance_category_rollup():
    guidance = ss.build_secure_score_guidance(_score_payload(), _activities_payload())
    cats = {c["category_display"]: c for c in guidance["by_category"]}

    assert cats["Identity"]["total"] == 20.0
    assert cats["Identity"]["applied"] == 5.0
    assert cats["Identity"]["pending"] == 15.0
    assert cats["Identity"]["activity_count"] == 2
    assert cats["Identity"]["pending_count"] == 1
    assert cats["Data"]["pending"] == 20.0
    assert cats["Devices"]["pending"] == 10.0
    totals = [c["total"] for c in guidance["by_category"]]
    assert totals == sorted(totals, reverse=True)


def test_build_secure_score_guidance_remediation_mentions_activity():
    guidance = ss.build_secure_score_guidance(_score_payload(), _activities_payload())
    rec = next(r for r in guidance["recommendations"] if r["id"] == "a2")

    assert "Enable DLP policies" in rec["remediation"]
    assert rec["category_display"] == "Data"


# ---------------------------------------------------------------------------
# check_secure_score_max_reached (catalog runner)
# ---------------------------------------------------------------------------
@pytest.mark.anyio("asyncio")
async def test_check_secure_score_max_reached_pass():
    with patch.object(
        ss,
        "_graph_get",
        new_callable=AsyncMock,
        return_value={"value": [{"currentScore": 80, "maxScore": 80}]},
    ):
        result = await ss.check_secure_score_max_reached("token")

    assert result["status"] == "pass"
    assert result["check_id"] == "bp_secure_score_max_reached"


@pytest.mark.anyio("asyncio")
async def test_check_secure_score_max_reached_fail_mentions_panel():
    with patch.object(
        ss,
        "_graph_get",
        new_callable=AsyncMock,
        return_value={"value": [{"currentScore": 40, "maxScore": 80}]},
    ):
        result = await ss.check_secure_score_max_reached("token")

    assert result["status"] == "fail"
    assert "Achievable goals for your license" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_secure_score_max_reached_unknown_when_no_scores():
    with patch.object(
        ss, "_graph_get", new_callable=AsyncMock, return_value={"value": []}
    ):
        result = await ss.check_secure_score_max_reached("token")

    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_secure_score_max_reached_unknown_on_403():
    with patch.object(
        ss,
        "_graph_get",
        new_callable=AsyncMock,
        side_effect=M365Error("403 Forbidden", http_status=403),
    ):
        result = await ss.check_secure_score_max_reached("token")

    assert result["status"] == "unknown"
    assert "SecurityEvents.Read.All" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_secure_score_max_reached_unknown_on_generic_error():
    with patch.object(
        ss, "_graph_get", new_callable=AsyncMock, side_effect=M365Error("boom")
    ):
        result = await ss.check_secure_score_max_reached("token")

    assert result["status"] == "unknown"
    assert "boom" in result["details"]


# ---------------------------------------------------------------------------
# get_secure_score_guidance (Graph fetch + compute)
# ---------------------------------------------------------------------------
@pytest.mark.anyio("asyncio")
async def test_get_secure_score_guidance_success():
    with patch.object(
        ss, "acquire_access_token", new_callable=AsyncMock, return_value="tok"
    ), patch.object(
        ss, "_graph_get", new_callable=AsyncMock, return_value=_score_payload()
    ), patch.object(
        ss,
        "_graph_get_all",
        new_callable=AsyncMock,
        return_value=_activities_payload()["value"],
    ):
        guidance = await ss.get_secure_score_guidance(1)

    assert guidance["available"] is True
    assert guidance["current"] == 42.5
    assert guidance["maximum"] == 80.0
    assert len(guidance["recommendations"]) == 3


@pytest.mark.anyio("asyncio")
async def test_get_secure_score_guidance_token_failure():
    with patch.object(
        ss,
        "acquire_access_token",
        new_callable=AsyncMock,
        side_effect=M365Error("no credentials"),
    ):
        guidance = await ss.get_secure_score_guidance(1)

    assert guidance["available"] is False
    assert guidance["reason"]


@pytest.mark.anyio("asyncio")
async def test_get_secure_score_guidance_both_fetches_fail_permission_reason():
    with patch.object(
        ss, "acquire_access_token", new_callable=AsyncMock, return_value="tok"
    ), patch.object(
        ss,
        "_graph_get",
        new_callable=AsyncMock,
        side_effect=M365Error("403", http_status=403),
    ), patch.object(
        ss,
        "_graph_get_all",
        new_callable=AsyncMock,
        side_effect=M365Error("403", http_status=403),
    ):
        guidance = await ss.get_secure_score_guidance(1)

    assert guidance["available"] is False
    assert "SecurityEvents.Read.All" in guidance["reason"]


# ---------------------------------------------------------------------------
# Catalog wiring
# ---------------------------------------------------------------------------
def test_catalog_includes_secure_score_max_check_enabled_by_default():
    from app.services import m365_best_practices as bp

    entry = next(
        e for e in bp._BEST_PRACTICES if e["id"] == "bp_secure_score_max_reached"
    )

    assert entry["default_enabled"] is True
    assert entry["source"] is ss.check_secure_score_max_reached

