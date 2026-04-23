"""Tests for the Microsoft 365 Best Practices service."""
from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365_best_practices as bp_service
from app.services.m365 import M365Error

_GUEST_ROLE_ID_MOST_RESTRICTIVE = bp_service._GUEST_ROLE_ID_MOST_RESTRICTIVE


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_catalog_entries_have_required_fields():
    catalog = bp_service.list_best_practices()
    assert catalog, "best-practice catalog must not be empty"
    for entry in catalog:
        assert entry["id"].startswith("bp_")
        assert entry["name"]
        assert entry["description"]
        assert entry["remediation"]
        assert "default_enabled" in entry
        # "source" must NOT be exposed via list_best_practices
        assert "source" not in entry


def test_catalog_check_ids_are_unique():
    ids = [bp["id"] for bp in bp_service.list_best_practices()]
    assert len(ids) == len(set(ids))


def test_get_remediation_known_check():
    catalog = bp_service.list_best_practices()
    bp = catalog[0]
    assert bp_service.get_remediation(bp["id"]) == bp["remediation"]


def test_get_remediation_unknown_check_returns_default():
    text = bp_service.get_remediation("bp_does_not_exist")
    assert "Microsoft" in text


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_enabled_check_ids_uses_defaults_when_empty():
    """With no settings rows stored, default_enabled controls the result."""
    with patch(
        "app.services.m365_best_practices.bp_repo.get_settings_map",
        new_callable=AsyncMock,
    ) as mock_map:
        mock_map.return_value = {}
        enabled = await bp_service.get_enabled_check_ids()

    expected = {bp["id"] for bp in bp_service.list_best_practices() if bp.get("default_enabled", True)}
    assert enabled == expected


@pytest.mark.anyio("asyncio")
async def test_get_enabled_check_ids_respects_stored_overrides():
    """Stored settings rows always override the default."""
    catalog = bp_service.list_best_practices()
    first_id = catalog[0]["id"]
    second_id = catalog[1]["id"]

    with patch(
        "app.services.m365_best_practices.bp_repo.get_settings_map",
        new_callable=AsyncMock,
    ) as mock_map:
        mock_map.return_value = {
            first_id: {"enabled": False, "auto_remediate": False},
            second_id: {"enabled": True, "auto_remediate": False},
        }
        enabled = await bp_service.get_enabled_check_ids()

    assert first_id not in enabled  # explicitly disabled
    assert second_id in enabled  # explicitly enabled


@pytest.mark.anyio("asyncio")
async def test_set_enabled_checks_persists_each_check_and_clears_disabled_results():
    catalog = bp_service.list_best_practices()
    keep_id = catalog[0]["id"]
    drop_id = catalog[1]["id"]

    with (
        patch(
            "app.services.m365_best_practices.bp_repo.upsert_setting",
            new_callable=AsyncMock,
        ) as mock_upsert,
        patch(
            "app.services.m365_best_practices.bp_repo.delete_result_for_check",
            new_callable=AsyncMock,
        ) as mock_delete,
    ):
        await bp_service.set_enabled_checks({keep_id})

    # upsert called for every catalog entry exactly once
    assert mock_upsert.await_count == len(catalog)
    # delete_result_for_check is called for every disabled check
    deleted_ids = {call.kwargs.get("check_id") or call.args[0] for call in mock_delete.await_args_list}
    # keep_id should NOT be in the delete set; drop_id SHOULD be
    assert keep_id not in deleted_ids
    assert drop_id in deleted_ids


@pytest.mark.anyio("asyncio")
async def test_set_enabled_checks_ignores_unknown_check_ids():
    """Unknown check_ids in the input are silently ignored."""
    with (
        patch(
            "app.services.m365_best_practices.bp_repo.upsert_setting",
            new_callable=AsyncMock,
        ) as mock_upsert,
        patch(
            "app.services.m365_best_practices.bp_repo.delete_result_for_check",
            new_callable=AsyncMock,
        ),
    ):
        await bp_service.set_enabled_checks({"bp_does_not_exist"})

    # Every check is treated as disabled because the unknown id is filtered.
    catalog = bp_service.list_best_practices()
    assert mock_upsert.await_count == len(catalog)
    for call in mock_upsert.await_args_list:
        assert call.kwargs["enabled"] is False


@pytest.mark.anyio("asyncio")
async def test_list_settings_with_catalog_merges_defaults():
    with patch(
        "app.services.m365_best_practices.bp_repo.get_settings_map",
        new_callable=AsyncMock,
    ) as mock_map:
        mock_map.return_value = {}
        rows = await bp_service.list_settings_with_catalog()

    catalog = bp_service.list_best_practices()
    assert len(rows) == len(catalog)
    for entry in rows:
        assert entry["enabled"] is True


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_only_runs_enabled_checks_and_persists():
    catalog = bp_service.list_best_practices()
    enabled_id = catalog[0]["id"]
    target = next(bp for bp in bp_service._BEST_PRACTICES if bp["id"] == enabled_id)

    fake_result = {
        "check_id": "anything",
        "check_name": "anything",
        "status": "pass",
        "details": "all good",
    }

    upserts: list[dict] = []

    async def fake_upsert(**kwargs):
        upserts.append(kwargs)

    real_source = target["source"]
    fake_source = AsyncMock(return_value=fake_result)
    target["source"] = fake_source
    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
            ) as mock_enabled,
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                side_effect=fake_upsert,
            ),
        ):
            mock_token.return_value = "fake-token"
            mock_enabled.return_value = {enabled_id}
            results = await bp_service.run_best_practices(company_id=42)
    finally:
        target["source"] = real_source

    # Only one check should have been run and persisted
    assert len(results) == 1
    assert results[0]["check_id"] == enabled_id
    assert results[0]["status"] == "pass"
    assert len(upserts) == 1
    assert upserts[0]["company_id"] == 42
    assert upserts[0]["check_id"] == enabled_id
    assert upserts[0]["status"] == "pass"
    assert upserts[0]["check_name"] == target["name"]
    fake_source.assert_awaited_once_with("fake-token")


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_handles_check_error_gracefully():
    catalog = bp_service.list_best_practices()
    enabled_id = catalog[0]["id"]
    target = next(bp for bp in bp_service._BEST_PRACTICES if bp["id"] == enabled_id)

    real_source = target["source"]
    fake_source = AsyncMock(side_effect=M365Error("boom"))
    target["source"] = fake_source
    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
            ) as mock_enabled,
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                new_callable=AsyncMock,
            ),
        ):
            mock_token.return_value = "tok"
            mock_enabled.return_value = {enabled_id}
            results = await bp_service.run_best_practices(company_id=1)
    finally:
        target["source"] = real_source

    assert len(results) == 1
    assert results[0]["status"] == "unknown"
    assert "boom" in results[0]["details"]


# ---------------------------------------------------------------------------
# run_single_check
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_run_single_check_runs_and_persists():
    catalog = bp_service.list_best_practices()
    enabled_id = catalog[0]["id"]
    target = next(bp for bp in bp_service._BEST_PRACTICES if bp["id"] == enabled_id)

    fake_result = {"check_id": enabled_id, "check_name": target["name"], "status": "pass", "details": "ok"}
    upserts: list[dict] = []

    async def fake_upsert(**kwargs):
        upserts.append(kwargs)

    real_source = target["source"]
    fake_source = AsyncMock(return_value=fake_result)
    target["source"] = fake_source
    try:
        with (
            patch("app.services.m365_best_practices.acquire_access_token", new_callable=AsyncMock) as mock_token,
            patch("app.services.m365_best_practices.get_enabled_check_ids", new_callable=AsyncMock) as mock_enabled,
            patch("app.services.m365_best_practices.get_auto_remediate_check_ids", new_callable=AsyncMock, return_value=set()),
            patch("app.services.m365_best_practices.bp_repo.upsert_result", side_effect=fake_upsert),
        ):
            mock_token.return_value = "fake-token"
            mock_enabled.return_value = {enabled_id}
            result = await bp_service.run_single_check(company_id=42, check_id=enabled_id)
    finally:
        target["source"] = real_source

    assert result["check_id"] == enabled_id
    assert result["status"] == "pass"
    assert len(upserts) == 1
    assert upserts[0]["company_id"] == 42
    assert upserts[0]["check_id"] == enabled_id


@pytest.mark.anyio("asyncio")
async def test_run_single_check_raises_for_unknown_id():
    with pytest.raises(ValueError, match="Unknown"):
        await bp_service.run_single_check(company_id=1, check_id="bp_does_not_exist")


@pytest.mark.anyio("asyncio")
async def test_run_single_check_raises_when_not_enabled():
    catalog = bp_service.list_best_practices()
    check_id = catalog[0]["id"]
    with (
        patch("app.services.m365_best_practices.get_enabled_check_ids", new_callable=AsyncMock) as mock_enabled,
    ):
        mock_enabled.return_value = set()
        with pytest.raises(ValueError, match="not enabled"):
            await bp_service.run_single_check(company_id=1, check_id=check_id)


# ---------------------------------------------------------------------------
# get_last_results
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_last_results_filters_disabled_checks():
    """Stored results for currently-disabled checks are excluded from output."""
    catalog = bp_service.list_best_practices()
    enabled_id = catalog[0]["id"]
    disabled_id = catalog[1]["id"]

    rows = [
        {
            "check_id": enabled_id,
            "check_name": catalog[0]["name"],
            "status": "fail",
            "details": "not configured",
            "run_at": datetime(2026, 1, 1, 10, 0, 0),
        },
        {
            "check_id": disabled_id,
            "check_name": catalog[1]["name"],
            "status": "pass",
            "details": "ok",
            "run_at": datetime(2026, 1, 1, 10, 0, 0),
        },
    ]

    with (
        patch(
            "app.services.m365_best_practices.bp_repo.list_results",
            new_callable=AsyncMock,
        ) as mock_list,
        patch(
            "app.services.m365_best_practices.get_enabled_check_ids",
            new_callable=AsyncMock,
        ) as mock_enabled,
        patch(
            "app.services.m365_best_practices.bp_repo.get_company_exclusions",
            new_callable=AsyncMock,
            return_value=set(),
        ),
    ):
        mock_list.return_value = rows
        mock_enabled.return_value = {enabled_id}
        out = await bp_service.get_last_results(company_id=1)

    assert len(out) == 1
    assert out[0]["check_id"] == enabled_id
    # Failed checks include remediation guidance
    assert out[0]["remediation"] is not None
    # Description from the catalog is merged into the result
    assert out[0]["description"] == catalog[0]["description"]


# ---------------------------------------------------------------------------
# Permissions wiring
# ---------------------------------------------------------------------------


def test_permission_field_registered():
    """can_view_m365_best_practices must be in the permission field set."""
    from app.repositories.user_companies import (
        _BOOLEAN_FIELDS,
        _PERMISSION_FIELDS,
        _PERMISSION_MAPPING,
    )

    assert "can_view_m365_best_practices" in _BOOLEAN_FIELDS
    assert "can_view_m365_best_practices" in _PERMISSION_FIELDS
    assert _PERMISSION_MAPPING.get("m365_best_practices.access") == "can_view_m365_best_practices"


# ---------------------------------------------------------------------------
# Disable Direct Send check
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_direct_send_pass_when_rejected():
    from app.services.m365_best_practices import _check_direct_send

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
    ) as mock_cmd:
        mock_cmd.return_value = {"value": [{"RejectDirectSend": True}]}
        result = await _check_direct_send("token", "tenant-id")

    assert result["status"] == "pass"
    assert "disabled" in result["details"].lower()


@pytest.mark.anyio("asyncio")
async def test_check_direct_send_fail_when_not_rejected():
    from app.services.m365_best_practices import _check_direct_send

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
    ) as mock_cmd:
        mock_cmd.return_value = {"value": [{"RejectDirectSend": False}]}
        result = await _check_direct_send("token", "tenant-id")

    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_direct_send_unknown_on_error():
    from app.services.m365_best_practices import _check_direct_send

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("EXO error"),
    ):
        result = await _check_direct_send("token", "tenant-id")

    assert result["status"] == "unknown"
    assert "EXO error" in result["details"]


def test_direct_send_in_catalog():
    """bp_disable_direct_send must be present in the public catalog."""
    catalog = bp_service.list_best_practices()
    ids = {bp["id"] for bp in catalog}
    assert "bp_disable_direct_send" in ids


def test_direct_send_catalog_entry_has_has_remediation():
    """bp_disable_direct_send must advertise automated remediation support."""
    catalog = bp_service.list_best_practices()
    entry = next(bp for bp in catalog if bp["id"] == "bp_disable_direct_send")
    assert entry.get("has_remediation") is True
    # Internal implementation keys must not be exposed
    assert "source" not in entry
    assert "remediation_cmdlet" not in entry
    assert "remediation_params" not in entry


def test_guest_access_restricted_catalog_entry_has_remediation():
    """bp_guest_access_restricted must advertise automated remediation support."""
    catalog = bp_service.list_best_practices()
    entry = next(bp for bp in catalog if bp["id"] == "bp_guest_access_restricted")
    assert entry.get("has_remediation") is True
    # Internal implementation keys must not be exposed in the public catalog
    assert "source" not in entry
    assert "remediation_url" not in entry
    assert "remediation_payload" not in entry


@pytest.mark.anyio("asyncio")
async def test_remediate_check_guest_access_restricted_success():
    """Successful Graph PATCH remediation for bp_guest_access_restricted updates DB and returns success."""
    upserts: list[dict] = []
    patched_urls: list[str] = []
    patched_payloads: list[dict] = []

    async def fake_graph_patch(token: str, url: str, payload: dict) -> None:
        patched_urls.append(url)
        patched_payloads.append(payload)

    with (
        patch(
            "app.services.m365_best_practices.acquire_access_token",
            new_callable=AsyncMock,
            return_value="graph-token",
        ),
        patch(
            "app.services.m365_best_practices._graph_patch",
            side_effect=fake_graph_patch,
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.update_remediation_status",
            side_effect=lambda **kw: upserts.append(kw) or None,
        ),
    ):
        result = await bp_service.remediate_check(
            company_id=5, check_id="bp_guest_access_restricted"
        )

    assert result["success"] is True
    assert len(upserts) == 1
    assert upserts[0]["company_id"] == 5
    assert upserts[0]["check_id"] == "bp_guest_access_restricted"
    assert upserts[0]["remediation_status"] == "success"
    # Verify the correct Graph endpoint and most-restrictive payload were used
    assert len(patched_urls) == 1
    assert "authorizationPolicy" in patched_urls[0]
    assert patched_payloads[0]["guestUserRoleId"] == _GUEST_ROLE_ID_MOST_RESTRICTIVE
    assert patched_payloads[0]["allowInvitesFrom"] == "adminsAndGuestInviters"


@pytest.mark.anyio("asyncio")
async def test_remediate_check_guest_access_restricted_failure_on_graph_error():
    """If the Graph PATCH fails, remediation status is recorded as 'failed'."""
    upserts: list[dict] = []

    with (
        patch(
            "app.services.m365_best_practices.acquire_access_token",
            new_callable=AsyncMock,
            return_value="graph-token",
        ),
        patch(
            "app.services.m365_best_practices._graph_patch",
            side_effect=M365Error("PATCH authorizationPolicy failed"),
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.update_remediation_status",
            side_effect=lambda **kw: upserts.append(kw) or None,
        ),
    ):
        result = await bp_service.remediate_check(
            company_id=5, check_id="bp_guest_access_restricted"
        )

    assert result["success"] is False
    assert upserts[0]["remediation_status"] == "failed"


# ---------------------------------------------------------------------------
# EXO runner in run_best_practices
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_exo_runner_uses_exo_token():
    """EXO-type checks must be called with the EXO token and tenant_id."""
    bp_entry = next(bp for bp in bp_service._BEST_PRACTICES if bp["id"] == "bp_disable_direct_send")
    real_source = bp_entry["source"]
    fake_source = AsyncMock(return_value={"status": "pass", "details": "ok"})
    bp_entry["source"] = fake_source
    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
                return_value="graph-token",
            ),
            patch(
                "app.services.m365_best_practices._acquire_exo_access_token",
                new_callable=AsyncMock,
                return_value=("exo-token", "tenant-123"),
            ),
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
                return_value={"bp_disable_direct_send"},
            ),
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                new_callable=AsyncMock,
            ),
        ):
            results = await bp_service.run_best_practices(company_id=99)
    finally:
        bp_entry["source"] = real_source

    assert len(results) == 1
    assert results[0]["check_id"] == "bp_disable_direct_send"
    # EXO runner must be called with (exo_token, tenant_id)
    fake_source.assert_awaited_once_with("exo-token", "tenant-123")


# ---------------------------------------------------------------------------
# remediate_check
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_remediate_check_success():
    """Successful EXO remediation updates DB and returns success."""
    upserts: list[dict] = []

    with (
        patch(
            "app.services.m365_best_practices._acquire_exo_access_token",
            new_callable=AsyncMock,
            return_value=("exo-token", "tenant-123"),
        ),
        patch(
            "app.services.m365_best_practices._exo_invoke_command",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.update_remediation_status",
            side_effect=lambda **kw: upserts.append(kw) or None,
        ),
    ):
        result = await bp_service.remediate_check(company_id=7, check_id="bp_disable_direct_send")

    assert result["success"] is True
    assert len(upserts) == 1
    assert upserts[0]["company_id"] == 7
    assert upserts[0]["check_id"] == "bp_disable_direct_send"
    assert upserts[0]["remediation_status"] == "success"


@pytest.mark.anyio("asyncio")
async def test_remediate_check_failure_on_exo_error():
    """If the EXO command fails, remediation status is recorded as 'failed'."""
    upserts: list[dict] = []

    with (
        patch(
            "app.services.m365_best_practices._acquire_exo_access_token",
            new_callable=AsyncMock,
            return_value=("exo-token", "tenant-123"),
        ),
        patch(
            "app.services.m365_best_practices._exo_invoke_command",
            new_callable=AsyncMock,
            side_effect=M365Error("Set-OrgConfig failed"),
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.update_remediation_status",
            side_effect=lambda **kw: upserts.append(kw) or None,
        ),
    ):
        result = await bp_service.remediate_check(company_id=7, check_id="bp_disable_direct_send")

    assert result["success"] is False
    assert upserts[0]["remediation_status"] == "failed"


@pytest.mark.anyio("asyncio")
async def test_remediate_check_unknown_id_returns_failure():
    """Passing an unrecognised check_id should return a failure without DB writes."""
    result = await bp_service.remediate_check(company_id=1, check_id="bp_does_not_exist")
    assert result["success"] is False


@pytest.mark.anyio("asyncio")
async def test_remediate_check_non_remediable_check_returns_failure():
    """A check without has_remediation=True must not attempt any external call."""
    result = await bp_service.remediate_check(
        company_id=1, check_id="bp_security_defaults"
    )
    assert result["success"] is False


# ---------------------------------------------------------------------------
# get_last_results – new fields
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_last_results_includes_remediation_fields():
    """get_last_results must expose has_remediation, remediation_status, remediated_at."""
    catalog = bp_service.list_best_practices()
    check_id = "bp_disable_direct_send"
    entry = next(bp for bp in catalog if bp["id"] == check_id)

    rows = [
        {
            "check_id": check_id,
            "check_name": entry["name"],
            "status": "fail",
            "details": "Direct Send enabled",
            "run_at": datetime(2026, 1, 1, 10, 0, 0),
            "remediation_status": "success",
            "remediated_at": datetime(2026, 1, 2, 10, 0, 0),
        }
    ]

    with (
        patch(
            "app.services.m365_best_practices.bp_repo.list_results",
            new_callable=AsyncMock,
            return_value=rows,
        ),
        patch(
            "app.services.m365_best_practices.get_enabled_check_ids",
            new_callable=AsyncMock,
            return_value={check_id},
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.get_company_exclusions",
            new_callable=AsyncMock,
            return_value=set(),
        ),
    ):
        out = await bp_service.get_last_results(company_id=1)

    assert len(out) == 1
    item = out[0]
    assert item["has_remediation"] is True
    assert item["remediation_status"] == "success"
    assert item["remediated_at"] == datetime(2026, 1, 2, 10, 0, 0)


# ---------------------------------------------------------------------------
# Auto-remediation settings
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_auto_remediate_check_ids_returns_only_enabled_remediable():
    """Only checks with auto_remediate=True and has_remediation=True are returned."""
    catalog = bp_service.list_best_practices()
    remediable_bp = next((bp for bp in catalog if bp.get("has_remediation")), None)
    non_remediable_bp = next((bp for bp in catalog if not bp.get("has_remediation")), None)
    assert remediable_bp is not None, "catalog must contain at least one remediable check"
    assert non_remediable_bp is not None, "catalog must contain at least one non-remediable check"
    remediable_id = remediable_bp["id"]
    non_remediable_id = non_remediable_bp["id"]

    with patch(
        "app.services.m365_best_practices.bp_repo.get_settings_map",
        new_callable=AsyncMock,
    ) as mock_map:
        mock_map.return_value = {
            remediable_id: {"enabled": True, "auto_remediate": True},
            non_remediable_id: {"enabled": True, "auto_remediate": True},
        }
        ids = await bp_service.get_auto_remediate_check_ids()

    assert remediable_id in ids
    assert non_remediable_id not in ids


@pytest.mark.anyio("asyncio")
async def test_get_auto_remediate_check_ids_empty_when_none_set():
    with patch(
        "app.services.m365_best_practices.bp_repo.get_settings_map",
        new_callable=AsyncMock,
        return_value={},
    ):
        ids = await bp_service.get_auto_remediate_check_ids()
    assert ids == set()


@pytest.mark.anyio("asyncio")
async def test_set_enabled_checks_persists_auto_remediate_flag():
    """set_enabled_checks must pass auto_remediate to upsert_setting for each check."""
    catalog = bp_service.list_best_practices()
    remediable_bp = next((bp for bp in catalog if bp.get("has_remediation")), None)
    non_remediable_bp = next((bp for bp in catalog if not bp.get("has_remediation")), None)
    assert remediable_bp is not None, "catalog must contain at least one remediable check"
    assert non_remediable_bp is not None, "catalog must contain at least one non-remediable check"
    remediable_id = remediable_bp["id"]
    non_remediable_id = non_remediable_bp["id"]

    upserted: list[dict] = []

    with (
        patch(
            "app.services.m365_best_practices.bp_repo.upsert_setting",
            side_effect=lambda **kw: upserted.append(kw) or None,
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.delete_result_for_check",
            new_callable=AsyncMock,
        ),
    ):
        await bp_service.set_enabled_checks(
            {remediable_id},
            auto_remediate_check_ids={remediable_id},
        )

    remediable_call = next((c for c in upserted if c["check_id"] == remediable_id), None)
    assert remediable_call is not None
    assert remediable_call["enabled"] is True
    assert remediable_call["auto_remediate"] is True

    # Non-remediable checks must have auto_remediate=False even if passed in
    other_calls = [c for c in upserted if c["check_id"] == non_remediable_id]
    assert all(not c["auto_remediate"] for c in other_calls)


@pytest.mark.anyio("asyncio")
async def test_set_enabled_checks_none_auto_remediate_defaults_to_false():
    """When auto_remediate_check_ids is None, all checks get auto_remediate=False."""
    catalog = bp_service.list_best_practices()

    upserted: list[dict] = []

    with (
        patch(
            "app.services.m365_best_practices.bp_repo.upsert_setting",
            side_effect=lambda **kw: upserted.append(kw) or None,
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.delete_result_for_check",
            new_callable=AsyncMock,
        ),
    ):
        await bp_service.set_enabled_checks({bp["id"] for bp in catalog})

    assert all(c["auto_remediate"] is False for c in upserted)


@pytest.mark.anyio("asyncio")
async def test_list_settings_with_catalog_includes_auto_remediate():
    """list_settings_with_catalog must expose auto_remediate for each entry."""
    catalog = bp_service.list_best_practices()
    remediable_bp = next((bp for bp in catalog if bp.get("has_remediation")), None)
    assert remediable_bp is not None, "catalog must contain at least one remediable check"
    remediable_id = remediable_bp["id"]

    with patch(
        "app.services.m365_best_practices.bp_repo.get_settings_map",
        new_callable=AsyncMock,
    ) as mock_map:
        mock_map.return_value = {
            remediable_id: {"enabled": True, "auto_remediate": True},
        }
        rows = await bp_service.list_settings_with_catalog()

    entry = next((r for r in rows if r["id"] == remediable_id), None)
    assert entry is not None
    assert entry["auto_remediate"] is True

    # Other entries (not in settings map) should default to False
    other = next((r for r in rows if r["id"] != remediable_id), None)
    assert other is not None
    assert other["auto_remediate"] is False


# ---------------------------------------------------------------------------
# run_best_practices – auto-remediation integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_triggers_auto_remediation_on_fail():
    """When a check fails and auto_remediate is enabled, remediate_check is called."""
    bp_entry = next(bp for bp in bp_service._BEST_PRACTICES if bp["id"] == "bp_disable_direct_send")
    real_source = bp_entry["source"]
    fake_source = AsyncMock(return_value={"status": "fail", "details": "Direct Send enabled"})
    bp_entry["source"] = fake_source

    remediated: list[dict] = []

    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
                return_value="graph-token",
            ),
            patch(
                "app.services.m365_best_practices._acquire_exo_access_token",
                new_callable=AsyncMock,
                return_value=("exo-token", "tenant-123"),
            ),
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
                return_value={"bp_disable_direct_send"},
            ),
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value={"bp_disable_direct_send"},
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.m365_best_practices.remediate_check",
                new_callable=AsyncMock,
                side_effect=lambda **kw: remediated.append(kw) or {"success": True, "message": "ok"},
            ),
        ):
            results = await bp_service.run_best_practices(company_id=5)
    finally:
        bp_entry["source"] = real_source

    assert len(results) == 1
    assert results[0]["status"] == "fail"
    assert len(remediated) == 1
    assert remediated[0]["company_id"] == 5
    assert remediated[0]["check_id"] == "bp_disable_direct_send"


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_does_not_auto_remediate_on_pass():
    """Passing checks must not trigger auto-remediation even if it is enabled."""
    bp_entry = next(bp for bp in bp_service._BEST_PRACTICES if bp["id"] == "bp_disable_direct_send")
    real_source = bp_entry["source"]
    fake_source = AsyncMock(return_value={"status": "pass", "details": "ok"})
    bp_entry["source"] = fake_source

    remediated: list[dict] = []

    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
                return_value="graph-token",
            ),
            patch(
                "app.services.m365_best_practices._acquire_exo_access_token",
                new_callable=AsyncMock,
                return_value=("exo-token", "tenant-123"),
            ),
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
                return_value={"bp_disable_direct_send"},
            ),
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value={"bp_disable_direct_send"},
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.m365_best_practices.remediate_check",
                new_callable=AsyncMock,
                side_effect=lambda **kw: remediated.append(kw) or {"success": True, "message": "ok"},
            ),
        ):
            results = await bp_service.run_best_practices(company_id=5)
    finally:
        bp_entry["source"] = real_source

    assert len(results) == 1
    assert results[0]["status"] == "pass"
    assert len(remediated) == 0


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_does_not_auto_remediate_when_not_in_auto_remediate_set():
    """A failing check without auto_remediate enabled must not be automatically remediated."""
    bp_entry = next(bp for bp in bp_service._BEST_PRACTICES if bp["id"] == "bp_disable_direct_send")
    real_source = bp_entry["source"]
    fake_source = AsyncMock(return_value={"status": "fail", "details": "Direct Send enabled"})
    bp_entry["source"] = fake_source

    remediated: list[dict] = []

    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
                return_value="graph-token",
            ),
            patch(
                "app.services.m365_best_practices._acquire_exo_access_token",
                new_callable=AsyncMock,
                return_value=("exo-token", "tenant-123"),
            ),
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
                return_value={"bp_disable_direct_send"},
            ),
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value=set(),  # auto-remediation disabled
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.m365_best_practices.remediate_check",
                new_callable=AsyncMock,
                side_effect=lambda **kw: remediated.append(kw) or {"success": True, "message": "ok"},
            ),
        ):
            results = await bp_service.run_best_practices(company_id=5)
    finally:
        bp_entry["source"] = real_source

    assert len(results) == 1
    assert results[0]["status"] == "fail"
    assert len(remediated) == 0


# ---------------------------------------------------------------------------
# _check_concealed_names
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_concealed_names_pass_when_display_enabled():
    from app.services.m365_best_practices import _check_concealed_names

    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"displayConcealedNames": True},
    ):
        result = await _check_concealed_names("token")

    assert result["status"] == "pass"
    assert "real" in result["details"].lower()


@pytest.mark.anyio("asyncio")
async def test_check_concealed_names_fail_when_concealed():
    from app.services.m365_best_practices import _check_concealed_names

    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"displayConcealedNames": False},
    ):
        result = await _check_concealed_names("token")

    assert result["status"] == "fail"
    assert "conceal" in result["details"].lower()


@pytest.mark.anyio("asyncio")
async def test_check_concealed_names_unknown_on_graph_error():
    from app.services.m365_best_practices import _check_concealed_names

    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        side_effect=M365Error("Graph error"),
    ):
        result = await _check_concealed_names("token")

    assert result["status"] == "unknown"
    assert "Graph error" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_concealed_names_unknown_when_field_missing():
    from app.services.m365_best_practices import _check_concealed_names

    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={},
    ):
        result = await _check_concealed_names("token")

    assert result["status"] == "unknown"


def test_concealed_names_in_catalog():
    """bp_concealed_names must be present in the public catalog."""
    catalog = bp_service.list_best_practices()
    ids = {bp["id"] for bp in catalog}
    assert "bp_concealed_names" in ids


def test_concealed_names_catalog_entry_has_remediation():
    """bp_concealed_names must advertise automated remediation support."""
    catalog = bp_service.list_best_practices()
    entry = next(bp for bp in catalog if bp["id"] == "bp_concealed_names")
    assert entry.get("has_remediation") is True
    # Internal implementation keys must not be exposed
    assert "source" not in entry
    assert "remediation_url" not in entry
    assert "remediation_payload" not in entry


# ---------------------------------------------------------------------------
# Graph-type remediation (bp_concealed_names)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_remediate_concealed_names_success():
    """Successful Graph PATCH remediation updates DB and returns success."""
    upserts: list[dict] = []

    with (
        patch(
            "app.services.m365_best_practices.acquire_access_token",
            new_callable=AsyncMock,
            return_value="graph-token",
        ),
        patch(
            "app.services.m365_best_practices._graph_patch",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.update_remediation_status",
            side_effect=lambda **kw: upserts.append(kw) or None,
        ),
    ):
        result = await bp_service.remediate_check(company_id=3, check_id="bp_concealed_names")

    assert result["success"] is True
    assert len(upserts) == 1
    assert upserts[0]["company_id"] == 3
    assert upserts[0]["check_id"] == "bp_concealed_names"
    assert upserts[0]["remediation_status"] == "success"


@pytest.mark.anyio("asyncio")
async def test_remediate_concealed_names_failure_on_graph_error():
    """If the Graph PATCH fails, remediation status is recorded as 'failed'."""
    upserts: list[dict] = []

    with (
        patch(
            "app.services.m365_best_practices.acquire_access_token",
            new_callable=AsyncMock,
            return_value="graph-token",
        ),
        patch(
            "app.services.m365_best_practices._graph_patch",
            new_callable=AsyncMock,
            side_effect=M365Error("PATCH failed"),
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.update_remediation_status",
            side_effect=lambda **kw: upserts.append(kw) or None,
        ),
    ):
        result = await bp_service.remediate_check(company_id=3, check_id="bp_concealed_names")

    assert result["success"] is False
    assert upserts[0]["remediation_status"] == "failed"


@pytest.mark.anyio("asyncio")
async def test_remediate_concealed_names_failure_on_token_acquisition_error():
    """If Graph token acquisition fails, remediation status is recorded as 'failed' and a clear message is returned."""
    upserts: list[dict] = []

    with (
        patch(
            "app.services.m365_best_practices.acquire_access_token",
            new_callable=AsyncMock,
            side_effect=M365Error("Invalid client secret"),
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.update_remediation_status",
            side_effect=lambda **kw: upserts.append(kw) or None,
        ),
    ):
        result = await bp_service.remediate_check(company_id=3, check_id="bp_concealed_names")

    assert result["success"] is False
    assert result["message"] == "Unable to acquire Microsoft Graph token. Check that the app credentials are correct."
    assert len(upserts) == 1
    assert upserts[0]["company_id"] == 3
    assert upserts[0]["check_id"] == "bp_concealed_names"
    assert upserts[0]["remediation_status"] == "failed"


# ---------------------------------------------------------------------------
# Tenant capability detection / N/A marking
# ---------------------------------------------------------------------------


def test_detect_capabilities_from_skus_entra_p1_and_intune():
    payload = {
        "value": [
            {
                "skuPartNumber": "ENTERPRISEPACK",
                "prepaidUnits": {"enabled": 5},
                "servicePlans": [
                    {
                        "servicePlanId": "41781fb2-bc02-4b7c-bd55-b576c07bb09d",
                        "provisioningStatus": "Success",
                    },
                    {
                        "servicePlanId": "c1ec4a95-1f05-45b3-a911-aa3fa01094f5",
                        "provisioningStatus": "Success",
                    },
                ],
            }
        ]
    }
    caps = bp_service._detect_capabilities_from_skus(payload)
    assert bp_service.CAP_ENTRA_ID_P1 in caps
    assert bp_service.CAP_INTUNE in caps
    assert bp_service.CAP_ENTRA_ID_P2 not in caps


def test_detect_capabilities_from_skus_p2_implies_p1():
    payload = {
        "value": [
            {
                "prepaidUnits": {"enabled": 1},
                "servicePlans": [
                    {
                        "servicePlanId": "EEC0EB4F-6444-4F95-ABA0-50C24D67F998",
                        "provisioningStatus": "Success",
                    }
                ],
            }
        ]
    }
    caps = bp_service._detect_capabilities_from_skus(payload)
    assert bp_service.CAP_ENTRA_ID_P1 in caps
    assert bp_service.CAP_ENTRA_ID_P2 in caps


def test_detect_capabilities_from_skus_ignores_zero_units():
    payload = {
        "value": [
            {
                "prepaidUnits": {"enabled": 0},
                "servicePlans": [
                    {
                        "servicePlanId": "41781fb2-bc02-4b7c-bd55-b576c07bb09d",
                        "provisioningStatus": "Success",
                    }
                ],
            }
        ]
    }
    caps = bp_service._detect_capabilities_from_skus(payload)
    assert caps == set()


def test_detect_capabilities_from_skus_ignores_disabled_service_plans():
    payload = {
        "value": [
            {
                "prepaidUnits": {"enabled": 10},
                "servicePlans": [
                    {
                        "servicePlanId": "c1ec4a95-1f05-45b3-a911-aa3fa01094f5",
                        "provisioningStatus": "Disabled",
                    }
                ],
            }
        ]
    }
    caps = bp_service._detect_capabilities_from_skus(payload)
    assert bp_service.CAP_INTUNE not in caps


def test_missing_capabilities_returns_empty_when_capabilities_unknown():
    """When detection failed (None), nothing is reported as missing."""
    assert bp_service._missing_capabilities(["entra_id_p2"], None) == []


def test_missing_capabilities_lists_unmet_requirements():
    assert bp_service._missing_capabilities(
        [bp_service.CAP_ENTRA_ID_P2, bp_service.CAP_INTUNE],
        {bp_service.CAP_ENTRA_ID_P1},
    ) == [bp_service.CAP_ENTRA_ID_P2, bp_service.CAP_INTUNE]


def test_catalog_marks_legacy_auth_as_requiring_entra_p1():
    catalog = bp_service.list_best_practices()
    entry = next(bp for bp in catalog if bp["id"] == "bp_block_legacy_auth")
    assert bp_service.CAP_ENTRA_ID_P1 in entry.get("requires_licenses", [])


def test_catalog_marks_risky_users_as_requiring_entra_p2():
    catalog = bp_service.list_best_practices()
    entry = next(bp for bp in catalog if bp["id"] == "bp_monitor_risky_users")
    assert bp_service.CAP_ENTRA_ID_P2 in entry.get("requires_licenses", [])


def test_catalog_marks_intune_checks_as_requiring_intune():
    catalog = bp_service.list_best_practices()
    intune_entries = [bp for bp in catalog if bp.get("cis_group", "").startswith("intune_")]
    assert intune_entries, "expected at least one Intune catalog entry"
    for entry in intune_entries:
        assert bp_service.CAP_INTUNE in entry.get("requires_licenses", []), entry["id"]


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_marks_check_as_na_when_license_missing():
    """A check with requires_licenses must be marked N/A when capability is absent."""
    bp_entry = next(b for b in bp_service._BEST_PRACTICES if b["id"] == "bp_monitor_risky_users")
    real_source = bp_entry["source"]
    fake_source = AsyncMock(return_value={"status": "fail", "details": "should not run"})
    bp_entry["source"] = fake_source

    upserts: list[dict] = []

    async def fake_upsert(**kwargs):
        upserts.append(kwargs)

    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
                return_value="graph-token",
            ),
            patch(
                "app.services.m365_best_practices.detect_tenant_capabilities",
                new_callable=AsyncMock,
                # Tenant only has Entra ID P1, not P2
                return_value={bp_service.CAP_ENTRA_ID_P1},
            ),
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
                return_value={"bp_monitor_risky_users"},
            ),
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                side_effect=fake_upsert,
            ),
        ):
            results = await bp_service.run_best_practices(company_id=7)
    finally:
        bp_entry["source"] = real_source

    assert len(results) == 1
    assert results[0]["status"] == bp_service.STATUS_NOT_APPLICABLE
    assert "Microsoft Entra ID P2" in results[0]["details"]
    # The check runner must NOT have been called
    fake_source.assert_not_awaited()
    # The N/A status must be persisted
    assert upserts[0]["status"] == bp_service.STATUS_NOT_APPLICABLE


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_runs_check_when_license_present():
    """A check with requires_licenses runs normally when the tenant has the license."""
    bp_entry = next(b for b in bp_service._BEST_PRACTICES if b["id"] == "bp_monitor_risky_users")
    real_source = bp_entry["source"]
    fake_source = AsyncMock(return_value={"status": "pass", "details": "no risky users"})
    bp_entry["source"] = fake_source

    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
                return_value="graph-token",
            ),
            patch(
                "app.services.m365_best_practices.detect_tenant_capabilities",
                new_callable=AsyncMock,
                return_value={bp_service.CAP_ENTRA_ID_P1, bp_service.CAP_ENTRA_ID_P2},
            ),
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
                return_value={"bp_monitor_risky_users"},
            ),
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                new_callable=AsyncMock,
            ),
        ):
            results = await bp_service.run_best_practices(company_id=7)
    finally:
        bp_entry["source"] = real_source

    assert len(results) == 1
    assert results[0]["status"] == "pass"
    fake_source.assert_awaited_once_with("graph-token")


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_runs_check_when_capabilities_unknown():
    """When capability detection returns None, checks must run normally (no N/A)."""
    bp_entry = next(b for b in bp_service._BEST_PRACTICES if b["id"] == "bp_block_legacy_auth")
    real_source = bp_entry["source"]
    fake_source = AsyncMock(return_value={"status": "fail", "details": "blocked"})
    bp_entry["source"] = fake_source

    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
                return_value="graph-token",
            ),
            patch(
                "app.services.m365_best_practices.detect_tenant_capabilities",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
                return_value={"bp_block_legacy_auth"},
            ),
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                new_callable=AsyncMock,
            ),
        ):
            results = await bp_service.run_best_practices(company_id=8)
    finally:
        bp_entry["source"] = real_source

    assert len(results) == 1
    assert results[0]["status"] == "fail"
    fake_source.assert_awaited_once_with("graph-token")


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_na_does_not_trigger_auto_remediation():
    """Auto-remediation must not run for checks marked N/A due to missing licenses."""
    bp_entry = next(b for b in bp_service._BEST_PRACTICES if b["id"] == "bp_monitor_risky_users")
    real_source = bp_entry["source"]
    fake_source = AsyncMock(return_value={"status": "fail", "details": "x"})
    bp_entry["source"] = fake_source

    remediated: list[str] = []

    async def fake_remediate(*, company_id: int, check_id: str) -> dict:
        remediated.append(check_id)
        return {"success": True, "message": "ok"}


# ---------------------------------------------------------------------------
# Transient-failure retry helpers
# ---------------------------------------------------------------------------


def test_is_retryable_m365_error_network_error():
    # No HTTP status -> network/decode error -> retryable
    assert bp_service._is_retryable_m365_error(M365Error("boom"))


def test_is_retryable_m365_error_transient_status():
    for status in (408, 429, 500, 502, 503, 504):
        assert bp_service._is_retryable_m365_error(M365Error("x", http_status=status))


def test_is_retryable_m365_error_permanent_status():
    for status in (400, 401, 403, 404):
        assert not bp_service._is_retryable_m365_error(M365Error("x", http_status=status))


def test_result_indicates_transient_failure_status_in_details():
    assert bp_service._result_indicates_transient_failure(
        {"status": "unknown", "details": "Microsoft Graph request failed (429)"}
    )
    assert bp_service._result_indicates_transient_failure(
        {"status": "unknown", "details": "Exchange Online Get-OrganizationConfig failed (503)"}
    )


def test_result_indicates_transient_failure_decode_error():
    assert bp_service._result_indicates_transient_failure(
        {"status": "unknown", "details": "Unable to query: response decode error: bad json"}
    )


def test_result_indicates_transient_failure_non_transient_unknown():
    # An "Unable to determine" message is informational, not a transient request failure.
    assert not bp_service._result_indicates_transient_failure(
        {"status": "unknown", "details": "Unable to determine Direct Send status."}
    )
    # Permanent client errors are not retryable.
    assert not bp_service._result_indicates_transient_failure(
        {"status": "unknown", "details": "Microsoft Graph request failed (403)"}
    )


def test_result_indicates_transient_failure_pass_or_fail_not_retried():
    assert not bp_service._result_indicates_transient_failure(
        {"status": "pass", "details": "all good"}
    )
    assert not bp_service._result_indicates_transient_failure(
        {"status": "fail", "details": "Microsoft Graph request failed (500)"}
    )


def test_result_indicates_transient_failure_batch_list():
    transient_list = [
        {"status": "unknown", "details": "Unable to retrieve device compliance policies: failed (502)"},
        {"status": "unknown", "details": "Unable to retrieve device compliance policies: failed (502)"},
    ]
    assert bp_service._result_indicates_transient_failure(transient_list)
    # Empty list is not transient
    assert not bp_service._result_indicates_transient_failure([])
    # Mixed list: not all unknown -> not retried as a batch
    mixed = [
        {"status": "pass", "details": "ok"},
        {"status": "unknown", "details": "failed (503)"},
    ]
    assert not bp_service._result_indicates_transient_failure(mixed)


@pytest.mark.anyio("asyncio")
async def test_call_check_with_retry_succeeds_after_transient_error(monkeypatch):
    """A transient M365Error is retried and the second attempt's result is returned."""
    monkeypatch.setattr(bp_service, "_RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(bp_service, "_MAX_RETRY_DELAY", 0)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise M365Error("transient", http_status=503)
        return {"status": "pass", "details": "ok"}

    result = await bp_service._call_check_with_retry(
        factory, company_id=1, check_id="bp_test"
    )
    assert result == {"status": "pass", "details": "ok"}
    assert calls["n"] == 2


@pytest.mark.anyio("asyncio")
async def test_call_check_with_retry_succeeds_after_transient_unknown_result(monkeypatch):
    """An unknown result with a transient marker is retried; success on retry replaces it."""
    monkeypatch.setattr(bp_service, "_RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(bp_service, "_MAX_RETRY_DELAY", 0)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        if calls["n"] < 3:
            return {
                "status": bp_service.STATUS_UNKNOWN,
                "details": "Unable to retrieve policy: Microsoft Graph request failed (429)",
            }
        return {"status": "fail", "details": "policy not configured"}

    result = await bp_service._call_check_with_retry(
        factory, company_id=1, check_id="bp_test"
    )
    assert result["status"] == "fail"
    assert calls["n"] == 3


@pytest.mark.anyio("asyncio")
async def test_call_check_with_retry_does_not_retry_permanent_error(monkeypatch):
    """A permanent (e.g. 403) M365Error is raised immediately without retrying."""
    monkeypatch.setattr(bp_service, "_RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(bp_service, "_MAX_RETRY_DELAY", 0)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        raise M365Error("forbidden", http_status=403)

    with pytest.raises(M365Error):
        await bp_service._call_check_with_retry(
            factory, company_id=1, check_id="bp_test"
        )
    assert calls["n"] == 1


@pytest.mark.anyio("asyncio")
async def test_call_check_with_retry_returns_last_unknown_after_exhaustion(monkeypatch):
    """After exhausting retries we still return the last (unknown) result so the
    caller can persist the underlying error message."""
    monkeypatch.setattr(bp_service, "_RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(bp_service, "_MAX_RETRY_DELAY", 0)
    calls = {"n": 0}
    transient = {
        "status": bp_service.STATUS_UNKNOWN,
        "details": "Unable to retrieve policy: Microsoft Graph request failed (503)",
    }

    async def factory():
        calls["n"] += 1
        return transient

    result = await bp_service._call_check_with_retry(
        factory, company_id=1, check_id="bp_test"
    )
    assert result == transient
    assert calls["n"] == bp_service._MAX_CHECK_ATTEMPTS


@pytest.mark.anyio("asyncio")
async def test_call_check_with_retry_does_not_retry_non_transient_unknown(monkeypatch):
    """An unknown result without a transient marker is returned immediately."""
    monkeypatch.setattr(bp_service, "_RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(bp_service, "_MAX_RETRY_DELAY", 0)
    calls = {"n": 0}
    informational = {
        "status": bp_service.STATUS_UNKNOWN,
        "details": "Unable to determine Direct Send status from organization config.",
    }

    async def factory():
        calls["n"] += 1
        return informational

    result = await bp_service._call_check_with_retry(
        factory, company_id=1, check_id="bp_test"
    )
    assert result == informational
    assert calls["n"] == 1


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_retries_transient_check_failure(monkeypatch):
    """run_best_practices retries a Graph check whose first attempt returns a
    transient unknown result, and persists the successful retry's status."""
    monkeypatch.setattr(bp_service, "_RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(bp_service, "_MAX_RETRY_DELAY", 0)

    catalog = bp_service.list_best_practices()
    enabled_id = next(
        bp["id"] for bp in catalog if not bp.get("cis_group")
    )
    target = next(bp for bp in bp_service._BEST_PRACTICES if bp["id"] == enabled_id)
    real_source = target["source"]

    transient = {
        "check_id": enabled_id,
        "check_name": target["name"],
        "status": bp_service.STATUS_UNKNOWN,
        "details": "Unable to retrieve policy: Microsoft Graph request failed (429)",
    }
    success = {
        "check_id": enabled_id,
        "check_name": target["name"],
        "status": "pass",
        "details": "configured correctly",
    }
    fake_source = AsyncMock(side_effect=[transient, success])
    target["source"] = fake_source

    upserts: list[dict] = []

    async def fake_upsert(**kwargs):
        upserts.append(kwargs)

    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
                return_value="graph-token",
            ),
            patch(
                "app.services.m365_best_practices.detect_tenant_capabilities",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
                return_value={enabled_id},
            ),
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.get_company_exclusions",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                side_effect=fake_upsert,
            ),
        ):
            results = await bp_service.run_best_practices(company_id=7)
    finally:
        target["source"] = real_source

    assert fake_source.await_count == 2
    assert results[0]["status"] == "pass"
    assert upserts[0]["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_detect_tenant_capabilities_returns_none_on_graph_error():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        side_effect=M365Error("403 Forbidden", http_status=403),
    ):
        caps = await bp_service.detect_tenant_capabilities("token")
    assert caps is None


# ---------------------------------------------------------------------------
# Per-company exclusions
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_list_settings_with_catalog_includes_excluded_field_no_company():
    """Without a company_id, excluded is always False."""
    with patch(
        "app.services.m365_best_practices.bp_repo.get_settings_map",
        new_callable=AsyncMock,
        return_value={},
    ):
        rows = await bp_service.list_settings_with_catalog()

    assert all("excluded" in row for row in rows)
    assert all(row["excluded"] is False for row in rows)


@pytest.mark.anyio("asyncio")
async def test_list_settings_with_catalog_reflects_company_exclusions():
    """Checks in the company exclusion set are flagged excluded=True."""
    catalog = bp_service.list_best_practices()
    excl_id = catalog[0]["id"]

    with (
        patch(
            "app.services.m365_best_practices.bp_repo.get_settings_map",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.get_company_exclusions",
            new_callable=AsyncMock,
            return_value={excl_id},
        ),
    ):
        rows = await bp_service.list_settings_with_catalog(company_id=99)

    excluded_row = next(r for r in rows if r["id"] == excl_id)
    assert excluded_row["excluded"] is True
    other_rows = [r for r in rows if r["id"] != excl_id]
    assert all(r["excluded"] is False for r in other_rows)


@pytest.mark.anyio("asyncio")
async def test_save_company_exclusions_persists_valid_ids():
    """Valid check_ids are forwarded to the repo; unknown ids are dropped."""
    catalog = bp_service.list_best_practices()
    valid_id = catalog[0]["id"]

    saved: list = []

    async def fake_set_excl(company_id, excluded):
        saved.append((company_id, excluded))

    with (
        patch(
            "app.services.m365_best_practices.bp_repo.set_company_exclusions",
            side_effect=fake_set_excl,
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.delete_result_for_check_and_company",
            new_callable=AsyncMock,
        ),
    ):
        await bp_service.save_company_exclusions(
            company_id=42,
            excluded_check_ids={valid_id, "bp_does_not_exist"},
        )

    assert len(saved) == 1
    company_id_saved, excluded_saved = saved[0]
    assert company_id_saved == 42
    assert valid_id in excluded_saved
    assert "bp_does_not_exist" not in excluded_saved


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_skips_excluded_checks():
    """Checks in the company exclusion set are not evaluated even if globally enabled."""
    catalog = bp_service.list_best_practices()
    enabled_id = catalog[0]["id"]
    target = next(bp for bp in bp_service._BEST_PRACTICES if bp["id"] == enabled_id)

    fake_source = AsyncMock(return_value={"status": "pass", "details": "ok"})
    real_source = target["source"]
    target["source"] = fake_source

    try:
        with (
            patch(
                "app.services.m365_best_practices.acquire_access_token",
                new_callable=AsyncMock,
                return_value="token",
            ),
            patch(
                "app.services.m365_best_practices.get_enabled_check_ids",
                new_callable=AsyncMock,
                return_value={enabled_id},
            ),
            patch(
                "app.services.m365_best_practices.get_auto_remediate_check_ids",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.get_company_exclusions",
                new_callable=AsyncMock,
                return_value={enabled_id},  # company has excluded this check
            ),
            patch(
                "app.services.m365_best_practices.bp_repo.upsert_result",
                new_callable=AsyncMock,
            ),
        ):
            results = await bp_service.run_best_practices(company_id=10)
    finally:
        target["source"] = real_source

    assert results == []
    fake_source.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_get_last_results_filters_excluded_checks():
    """Stored results for company-excluded checks do not appear in the output."""
    catalog = bp_service.list_best_practices()
    shown_id = catalog[0]["id"]
    hidden_id = catalog[1]["id"]

    fake_rows = [
        {"check_id": shown_id, "check_name": "A", "status": "pass", "details": "", "run_at": None,
         "remediation_status": None, "remediated_at": None},
        {"check_id": hidden_id, "check_name": "B", "status": "fail", "details": "", "run_at": None,
         "remediation_status": None, "remediated_at": None},
    ]

    with (
        patch(
            "app.services.m365_best_practices.bp_repo.list_results",
            new_callable=AsyncMock,
            return_value=fake_rows,
        ),
        patch(
            "app.services.m365_best_practices.get_enabled_check_ids",
            new_callable=AsyncMock,
            return_value={shown_id, hidden_id},
        ),
        patch(
            "app.services.m365_best_practices.bp_repo.get_company_exclusions",
            new_callable=AsyncMock,
            return_value={hidden_id},
        ),
    ):
        results = await bp_service.get_last_results(company_id=7)

    result_ids = {r["check_id"] for r in results}
    assert shown_id in result_ids
    assert hidden_id not in result_ids


# ---------------------------------------------------------------------------
# Expanded best-practice catalog (65 additional checks)
# ---------------------------------------------------------------------------


# A representative sample of the new check ids that must appear in the catalog
_EXPECTED_NEW_CHECK_IDS = {
    # Graph identity / CA / PIM / auth-methods
    "bp_per_user_mfa_disabled",
    "bp_dynamic_group_for_guests",
    "bp_managed_device_required_auth",
    "bp_managed_device_required_secinfo_reg",
    "bp_access_reviews_guest_users",
    "bp_access_reviews_privileged_roles",
    "bp_admin_accounts_cloud_only",
    "bp_admin_accounts_reduced_license",
    "bp_all_members_mfa_capable",
    "bp_approval_required_ga_activation",
    "bp_approval_required_pra_activation",
    "bp_collab_invitations_allowed_domains",
    "bp_custom_banned_passwords",
    "bp_password_expiry_never_expire",
    "bp_email_otp_disabled",
    "bp_user_consent_apps_disallowed",
    "bp_users_cannot_create_security_groups",
    "bp_users_restricted_bitlocker_recovery",
    "bp_only_managed_public_groups",
    "bp_pim_used_to_manage_roles",
    "bp_phishing_resistant_mfa_admins",
    "bp_security_defaults_appropriate",
    "bp_signin_freq_intune_enrollment",
    "bp_signin_freq_admin_browser_no_persist",
    "bp_system_preferred_mfa",
    "bp_authenticator_mfa_fatigue",
    "bp_weak_auth_methods_disabled",
    "bp_internal_phishing_forms",
    "bp_laps_enabled",
    "bp_two_emergency_access_accounts",
    # Exchange Online
    "bp_audit_bypass_disabled_mailboxes",
    "bp_audit_disabled_org_false",
    "bp_audit_log_search_enabled",
    "bp_mailbox_audit_actions",
    "bp_mailbox_auditing_enabled",
    "bp_modern_auth_exo",
    "bp_smtp_auth_disabled",
    "bp_dkim_enabled_all_domains",
    "bp_third_party_storage_owa",
    "bp_outlook_addins_disabled",
    "bp_idle_session_timeout_3h",
    "bp_mailtips_enabled",
    "bp_shared_mailbox_signin_blocked",
    "bp_antiphish_impersonated_domain_protection",
    "bp_antiphish_impersonated_user_protection",
    "bp_antiphish_quarantine_impersonated_domain",
    "bp_antiphish_quarantine_impersonated_user",
    "bp_antiphish_domain_impersonation_safety_tip",
    "bp_antiphish_user_impersonation_safety_tip",
    "bp_antiphish_unusual_characters_safety_tip",
    # SharePoint Online / OneDrive
    "bp_external_content_sharing_restricted",
    "bp_sharepoint_external_sharing_restricted",
    "bp_sp_guests_cannot_share_unowned",
    "bp_onedrive_content_sharing_restricted",
    "bp_link_sharing_restricted_spo_od",
    "bp_modern_auth_sp_apps",
    "bp_sharepoint_infected_files_block",
    "bp_sharepoint_sign_out_inactive_users",
    # Teams
    "bp_anon_dialin_cannot_start_meeting",
    "bp_only_org_can_bypass_lobby",
    "bp_invited_users_auto_admitted",
    "bp_dialin_cannot_bypass_lobby",
    "bp_external_participants_no_control",
    "bp_external_users_cannot_initiate",
    "bp_teams_external_files_approved_storage",
    "bp_restrict_anon_users_join_meeting",
    # Defender / Purview
    "bp_safe_links_office_apps",
    "bp_dlp_policies_enabled",
    "bp_dlp_policies_teams",
    "bp_zap_teams_on",
    # DNS / on-prem
    "bp_spf_records_published",
    "bp_dmarc_records_published",
    "bp_onprem_password_protection",
}


def test_expanded_catalog_contains_all_new_check_ids():
    catalog_ids = {bp["id"] for bp in bp_service._BEST_PRACTICES}
    missing = _EXPECTED_NEW_CHECK_IDS - catalog_ids
    assert not missing, f"missing new check ids: {sorted(missing)}"


def test_expanded_catalog_entries_have_required_fields():
    new_entries = [
        bp for bp in bp_service._BEST_PRACTICES
        if bp["id"] in _EXPECTED_NEW_CHECK_IDS
    ]
    assert len(new_entries) == len(_EXPECTED_NEW_CHECK_IDS)
    for entry in new_entries:
        assert entry["id"].startswith("bp_"), entry["id"]
        assert entry["name"], entry["id"]
        assert entry["description"], entry["id"]
        assert entry["remediation"], entry["id"]
        assert callable(entry["source"]), entry["id"]
        assert "default_enabled" in entry, entry["id"]
        # New entries must have a known source_type
        assert entry.get("source_type") in {"graph", "exo"}, entry["id"]


def test_expanded_catalog_known_capabilities_only():
    """Every requires_licenses value must reference a known capability constant."""
    known = set(bp_service._CAPABILITY_FRIENDLY_NAMES.keys())
    for bp in bp_service._BEST_PRACTICES:
        for cap in bp.get("requires_licenses") or []:
            assert cap in known, f"{bp['id']} references unknown capability {cap!r}"


def test_capability_constants_defined():
    """All new capability constants are exported and have friendly names."""
    for name in (
        "CAP_EXCHANGE_ONLINE",
        "CAP_SHAREPOINT_ONLINE",
        "CAP_TEAMS",
        "CAP_TEAMS_AUDIO_CONF",
        "CAP_DEFENDER_O365_P1",
        "CAP_DEFENDER_O365_P2",
        "CAP_PURVIEW_DLP",
        "CAP_INTUNE_LAPS",
    ):
        cap = getattr(bp_service, name)
        assert cap in bp_service._CAPABILITY_FRIENDLY_NAMES


# ---------------------------------------------------------------------------
# Representative pass/fail/unknown unit tests for new check runners
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_password_expiry_pass_when_all_domains_never_expire():
    domains = [
        {"id": "contoso.com", "isVerified": True, "passwordValidityPeriodInDays": 2147483647},
        {"id": "contoso.onmicrosoft.com", "isVerified": True, "passwordValidityPeriodInDays": 2147483647},
    ]
    with patch(
        "app.services.m365_best_practices._graph_get_all",
        new_callable=AsyncMock,
        return_value=domains,
    ):
        result = await bp_service._check_password_expiry_never_expire("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_password_expiry_fail_when_any_domain_expires():
    domains = [
        {"id": "contoso.com", "isVerified": True, "passwordValidityPeriodInDays": 90},
    ]
    with patch(
        "app.services.m365_best_practices._graph_get_all",
        new_callable=AsyncMock,
        return_value=domains,
    ):
        result = await bp_service._check_password_expiry_never_expire("token")
    assert result["status"] == "fail"
    # Use a regex with word boundaries to verify the domain identifier is
    # present in the details message; this avoids CodeQL
    # py/incomplete-url-substring-sanitization warnings about plain `in`
    # substring checks against URL-like strings.
    assert re.search(r"\bcontoso\.com\b", result["details"])


@pytest.mark.anyio("asyncio")
async def test_check_password_expiry_unknown_on_graph_error():
    with patch(
        "app.services.m365_best_practices._graph_get_all",
        new_callable=AsyncMock,
        side_effect=M365Error("Graph error"),
    ):
        result = await bp_service._check_password_expiry_never_expire("token")
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_email_otp_disabled_pass():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"state": "disabled"},
    ):
        result = await bp_service._check_email_otp_disabled("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_email_otp_disabled_fail():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"state": "enabled"},
    ):
        result = await bp_service._check_email_otp_disabled("token")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_mailtips_enabled_pass():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [{"MailTipsAllTipsEnabled": True}]},
    ):
        result = await bp_service._check_mailtips_enabled("exo-token", "tenant-id")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_mailtips_enabled_fail():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [{"MailTipsAllTipsEnabled": False}]},
    ):
        result = await bp_service._check_mailtips_enabled("exo-token", "tenant-id")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_smtp_auth_disabled_pass():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [{"SmtpClientAuthenticationDisabled": True}]},
    ):
        result = await bp_service._check_smtp_auth_disabled("exo-token", "tenant-id")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_smtp_auth_disabled_fail():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [{"SmtpClientAuthenticationDisabled": False}]},
    ):
        result = await bp_service._check_smtp_auth_disabled("exo-token", "tenant-id")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_audit_bypass_disabled_pass_when_no_bypass():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [
            {"Identity": "alice@contoso.com", "AuditBypassEnabled": False},
            {"Identity": "bob@contoso.com", "AuditBypassEnabled": False},
        ]},
    ):
        result = await bp_service._check_audit_bypass_disabled_mailboxes("exo-token", "tenant-id")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_audit_bypass_disabled_fail_when_bypassed():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [
            {"Identity": "svc@contoso.com", "AuditBypassEnabled": True},
        ]},
    ):
        result = await bp_service._check_audit_bypass_disabled_mailboxes("exo-token", "tenant-id")
    assert result["status"] == "fail"
    assert "svc@contoso.com" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_manual_review_check_returns_unknown_with_instructions():
    """SPO/Teams/SCC/DNS-style manual checks return STATUS_UNKNOWN with the
    expected manual-review instructions in details."""
    runner = bp_service._manual_review_factory(
        "bp_test_manual", "Test manual check", "Run: Get-SPOTenant"
    )
    result = await runner("token", "tenant")
    assert result["status"] == "unknown"
    assert "Run: Get-SPOTenant" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_users_cannot_create_security_groups_pass():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={
            "defaultUserRolePermissions": {"allowedToCreateSecurityGroups": False}
        },
    ):
        result = await bp_service._check_users_cannot_create_security_groups("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_users_cannot_create_security_groups_fail():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={
            "defaultUserRolePermissions": {"allowedToCreateSecurityGroups": True}
        },
    ):
        result = await bp_service._check_users_cannot_create_security_groups("token")
    assert result["status"] == "fail"


# ---------------------------------------------------------------------------
# User consent to apps check
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_user_consent_disallowed_pass():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={
            "defaultUserRolePermissions": {"permissionGrantPoliciesAssigned": []}
        },
    ):
        result = await bp_service._check_user_consent_disallowed("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_user_consent_disallowed_fail_legacy_policy():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={
            "defaultUserRolePermissions": {
                "permissionGrantPoliciesAssigned": ["microsoft-user-default-legacy"]
            }
        },
    ):
        result = await bp_service._check_user_consent_disallowed("token")
    assert result["status"] == "fail"
    assert "microsoft-user-default-legacy" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_user_consent_disallowed_fail_lowrisk_policy():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={
            "defaultUserRolePermissions": {
                "permissionGrantPoliciesAssigned": ["microsoft-user-default-lowrisk"]
            }
        },
    ):
        result = await bp_service._check_user_consent_disallowed("token")
    assert result["status"] == "fail"
    assert "microsoft-user-default-lowrisk" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_user_consent_disallowed_unknown_on_error():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        side_effect=M365Error("network error"),
    ):
        result = await bp_service._check_user_consent_disallowed("token")
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# Quarantine notification check
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_quarantine_notification_enabled_pass():
    """All policies have notifications enabled and frequency=1: pass."""
    from app.services.m365_best_practices import _check_quarantine_notification_enabled

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
    ) as mock_cmd:
        mock_cmd.return_value = {
            "value": [
                {
                    "Name": "Default",
                    "EnableEndUserSpamNotifications": True,
                    "EndUserSpamNotificationFrequency": 1,
                }
            ]
        }
        result = await _check_quarantine_notification_enabled("token", "tenant-id")

    assert result["status"] == "pass"
    assert result["check_id"] == "bp_quarantine_notification_enabled"


@pytest.mark.anyio("asyncio")
async def test_check_quarantine_notification_disabled_fails():
    """Policy with notifications disabled: fail."""
    from app.services.m365_best_practices import _check_quarantine_notification_enabled

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
    ) as mock_cmd:
        mock_cmd.return_value = {
            "value": [
                {
                    "Name": "Default",
                    "EnableEndUserSpamNotifications": False,
                    "EndUserSpamNotificationFrequency": 1,
                }
            ]
        }
        result = await _check_quarantine_notification_enabled("token", "tenant-id")

    assert result["status"] == "fail"
    assert "disabled" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_quarantine_notification_high_frequency_fails():
    """Policy with notifications enabled but frequency > 1 day: fail."""
    from app.services.m365_best_practices import _check_quarantine_notification_enabled

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
    ) as mock_cmd:
        mock_cmd.return_value = {
            "value": [
                {
                    "Name": "Default",
                    "EnableEndUserSpamNotifications": True,
                    "EndUserSpamNotificationFrequency": 3,
                }
            ]
        }
        result = await _check_quarantine_notification_enabled("token", "tenant-id")

    assert result["status"] == "fail"
    assert "frequency=3" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_quarantine_notification_unknown_on_error():
    """EXO error returns unknown status with error message."""
    from app.services.m365_best_practices import _check_quarantine_notification_enabled

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("EXO unavailable"),
    ):
        result = await _check_quarantine_notification_enabled("token", "tenant-id")

    assert result["status"] == "unknown"
    assert "EXO unavailable" in result["details"]


def test_quarantine_notification_in_catalog():
    """bp_quarantine_notification_enabled must be present in the public catalog."""
    catalog = bp_service.list_best_practices()
    ids = {bp["id"] for bp in catalog}
    assert "bp_quarantine_notification_enabled" in ids


def test_quarantine_notification_catalog_entry():
    """bp_quarantine_notification_enabled catalog entry must have the expected fields."""
    catalog = bp_service.list_best_practices()
    entry = next(bp for bp in catalog if bp["id"] == "bp_quarantine_notification_enabled")
    assert entry.get("has_remediation") is True
    assert entry.get("default_enabled") is True
    # Internal implementation keys must not be exposed
    assert "source" not in entry
    assert "remediation_cmdlet" not in entry
    assert "remediation_params" not in entry


# ---------------------------------------------------------------------------
# License-based N/A scoping for the new capability constants
# ---------------------------------------------------------------------------


def test_service_plan_to_capabilities_includes_new_capabilities():
    """Every new capability constant has at least one service-plan GUID mapping."""
    mapped: set[str] = set()
    for caps in bp_service._SERVICE_PLAN_TO_CAPABILITIES.values():
        mapped.update(caps)
    for new_cap in (
        bp_service.CAP_EXCHANGE_ONLINE,
        bp_service.CAP_SHAREPOINT_ONLINE,
        bp_service.CAP_TEAMS,
        bp_service.CAP_TEAMS_AUDIO_CONF,
        bp_service.CAP_DEFENDER_O365_P1,
        bp_service.CAP_DEFENDER_O365_P2,
        bp_service.CAP_PURVIEW_DLP,
        bp_service.CAP_INTUNE_LAPS,
    ):
        assert new_cap in mapped, f"{new_cap} has no service-plan mapping"


# ---------------------------------------------------------------------------
# bp_outlook_addins_disabled
# Anti-phishing impersonation checks
# _check_mailbox_auditing_enabled_all_users
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_outlook_addins_disabled_pass_when_disabled():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [{"Identity": "OwaMailboxPolicy-Default", "WebPartsFrameworkEnabled": False}]},
    ):
        result = await bp_service._check_outlook_addins_disabled("exo-token", "tenant-id")
async def test_antiphish_impersonated_domain_protection_pass():
    """Pass when at least one policy has EnableTargetedDomainsProtection True."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {"Name": "Office365 AntiPhish Default", "EnableTargetedDomainsProtection": True}
            ]
        },
    ):
        result = await bp_service._check_antiphish_impersonated_domain_protection(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_outlook_addins_disabled_fail_when_enabled():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [{"Identity": "OwaMailboxPolicy-Default", "WebPartsFrameworkEnabled": True}]},
    ):
        result = await bp_service._check_outlook_addins_disabled("exo-token", "tenant-id")
    assert result["status"] == "fail"
    assert "OwaMailboxPolicy-Default" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_outlook_addins_disabled_unknown_on_error():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("EXO error"),
    ):
        result = await bp_service._check_outlook_addins_disabled("exo-token", "tenant-id")
    assert result["status"] == "unknown"
    assert "EXO error" in result["details"]


def test_outlook_addins_disabled_in_catalog():
    catalog = bp_service.list_best_practices()
    ids = {bp["id"] for bp in catalog}
    assert "bp_outlook_addins_disabled" in ids


def test_outlook_addins_disabled_has_remediation():
    catalog = bp_service.list_best_practices()
    entry = next(bp for bp in catalog if bp["id"] == "bp_outlook_addins_disabled")
    assert entry.get("has_remediation") is True
    assert "source" not in entry
async def test_antiphish_impersonated_domain_protection_fail():
    """Fail when no policy has EnableTargetedDomainsProtection True."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {"Name": "Office365 AntiPhish Default", "EnableTargetedDomainsProtection": False}
            ]
        },
    ):
        result = await bp_service._check_antiphish_impersonated_domain_protection(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_antiphish_impersonated_domain_protection_unknown_on_error():
    """Return unknown when the EXO command raises M365Error."""
    from app.services.m365 import M365Error

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("connection refused"),
    ):
        result = await bp_service._check_antiphish_impersonated_domain_protection(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_mailbox_auditing_enabled_pass_when_all_enabled():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [
            {"UserPrincipalName": "alice@contoso.com", "AuditEnabled": True},
            {"UserPrincipalName": "bob@contoso.com", "AuditEnabled": True},
        ]},
    ):
        result = await bp_service._check_mailbox_auditing_enabled_all_users(
            "exo-token", "tenant-id"
        )
    assert result["status"] == "pass"
    assert "2" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_mailbox_auditing_enabled_fail_when_some_disabled():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [
            {"UserPrincipalName": "alice@contoso.com", "AuditEnabled": True},
            {"UserPrincipalName": "bob@contoso.com", "AuditEnabled": False},
        ]},
    ):
        result = await bp_service._check_mailbox_auditing_enabled_all_users(
            "exo-token", "tenant-id"
        )
    assert result["status"] == "fail"
    assert "bob@contoso.com" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_mailbox_auditing_enabled_unknown_when_no_mailboxes():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": []},
    ):
        result = await bp_service._check_mailbox_auditing_enabled_all_users(
            "exo-token", "tenant-id"
        )
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_antiphish_impersonated_user_protection_pass():
    """Pass when at least one policy has EnableTargetedUserProtection True."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {"Name": "Office365 AntiPhish Default", "EnableTargetedUserProtection": True}
            ]
        },
    ):
        result = await bp_service._check_antiphish_impersonated_user_protection(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_antiphish_impersonated_user_protection_fail():
    """Fail when no policy has EnableTargetedUserProtection True."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {"Name": "Office365 AntiPhish Default", "EnableTargetedUserProtection": False}
            ]
        },
    ):
        result = await bp_service._check_antiphish_impersonated_user_protection(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_antiphish_impersonated_user_protection_unknown_on_error():
    """Return unknown when the EXO command raises M365Error."""
    from app.services.m365 import M365Error

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("timeout"),
    ):
        result = await bp_service._check_antiphish_impersonated_user_protection(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_mailbox_auditing_enabled_unknown_on_exo_error():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=bp_service.M365Error("connection refused"),
    ):
        result = await bp_service._check_mailbox_auditing_enabled_all_users(
            "exo-token", "tenant-id"
        )
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_antiphish_quarantine_impersonated_domain_pass():
    """Pass when at least one policy has TargetedDomainProtectionAction == Quarantine."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {
                    "Name": "Office365 AntiPhish Default",
                    "TargetedDomainProtectionAction": "Quarantine",
                }
            ]
        },
    ):
        result = await bp_service._check_antiphish_quarantine_impersonated_domain(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_antiphish_quarantine_impersonated_domain_fail():
    """Fail when no policy has TargetedDomainProtectionAction == Quarantine."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {
                    "Name": "Office365 AntiPhish Default",
                    "TargetedDomainProtectionAction": "MoveToJmf",
                }
            ]
        },
    ):
        result = await bp_service._check_antiphish_quarantine_impersonated_domain(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_antiphish_quarantine_impersonated_domain_unknown_on_error():
    """Return unknown when the EXO command raises M365Error."""
    from app.services.m365 import M365Error

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("service unavailable"),
    ):
        result = await bp_service._check_antiphish_quarantine_impersonated_domain(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_antiphish_quarantine_impersonated_user_pass():
    """Pass when at least one policy has TargetedUserProtectionAction == Quarantine."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {
                    "Name": "Office365 AntiPhish Default",
                    "TargetedUserProtectionAction": "Quarantine",
                }
            ]
        },
    ):
        result = await bp_service._check_antiphish_quarantine_impersonated_user(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_antiphish_quarantine_impersonated_user_fail():
    """Fail when no policy has TargetedUserProtectionAction == Quarantine."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {
                    "Name": "Office365 AntiPhish Default",
                    "TargetedUserProtectionAction": "MoveToJmf",
                }
            ]
        },
    ):
        result = await bp_service._check_antiphish_quarantine_impersonated_user(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_antiphish_quarantine_impersonated_user_unknown_on_error():
    """Return unknown when the EXO command raises M365Error."""
    from app.services.m365 import M365Error

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("internal server error"),
    ):
        result = await bp_service._check_antiphish_quarantine_impersonated_user(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# _check_antiphish_domain_impersonation_safety_tip
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_antiphish_domain_impersonation_safety_tip_pass():
    """Pass when at least one policy has EnableSimilarDomainsSafetyTips == True."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {
                    "Name": "Office365 AntiPhish Default",
                    "EnableSimilarDomainsSafetyTips": True,
                }
            ]
        },
    ):
        result = await bp_service._check_antiphish_domain_impersonation_safety_tip(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_antiphish_domain_impersonation_safety_tip_fail():
    """Fail when no policy has EnableSimilarDomainsSafetyTips == True."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {
                    "Name": "Office365 AntiPhish Default",
                    "EnableSimilarDomainsSafetyTips": False,
                }
            ]
        },
    ):
        result = await bp_service._check_antiphish_domain_impersonation_safety_tip(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_antiphish_domain_impersonation_safety_tip_unknown_on_error():
    """Return unknown when the EXO command raises M365Error."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("internal server error"),
    ):
        result = await bp_service._check_antiphish_domain_impersonation_safety_tip(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# _check_antiphish_user_impersonation_safety_tip
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_antiphish_user_impersonation_safety_tip_pass():
    """Pass when at least one policy has EnableSimilarUsersSafetyTips == True."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {
                    "Name": "Office365 AntiPhish Default",
                    "EnableSimilarUsersSafetyTips": True,
                }
            ]
        },
    ):
        result = await bp_service._check_antiphish_user_impersonation_safety_tip(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_antiphish_user_impersonation_safety_tip_fail():
    """Fail when no policy has EnableSimilarUsersSafetyTips == True."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {
                    "Name": "Office365 AntiPhish Default",
                    "EnableSimilarUsersSafetyTips": False,
                }
            ]
        },
    ):
        result = await bp_service._check_antiphish_user_impersonation_safety_tip(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_antiphish_user_impersonation_safety_tip_unknown_on_error():
    """Return unknown when the EXO command raises M365Error."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("internal server error"),
    ):
        result = await bp_service._check_antiphish_user_impersonation_safety_tip(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# _check_antiphish_unusual_characters_safety_tip
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_antiphish_unusual_characters_safety_tip_pass():
    """Pass when at least one policy has EnableUnusualCharactersSafetyTips == True."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {
                    "Name": "Office365 AntiPhish Default",
                    "EnableUnusualCharactersSafetyTips": True,
                }
            ]
        },
    ):
        result = await bp_service._check_antiphish_unusual_characters_safety_tip(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_antiphish_unusual_characters_safety_tip_fail():
    """Fail when no policy has EnableUnusualCharactersSafetyTips == True."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={
            "value": [
                {
                    "Name": "Office365 AntiPhish Default",
                    "EnableUnusualCharactersSafetyTips": False,
                }
            ]
        },
    ):
        result = await bp_service._check_antiphish_unusual_characters_safety_tip(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_antiphish_unusual_characters_safety_tip_unknown_on_error():
    """Return unknown when the EXO command raises M365Error."""
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("internal server error"),
    ):
        result = await bp_service._check_antiphish_unusual_characters_safety_tip(
            "exo-token", "tenant-123"
        )
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# _remediate_foreach_mailbox
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_remediate_foreach_mailbox_enables_audit_on_affected_only():
    """Set-Mailbox is called only for mailboxes where AuditEnabled is not True."""
    mailboxes = [
        {"UserPrincipalName": "alice@contoso.com", "AuditEnabled": True},
        {"UserPrincipalName": "bob@contoso.com", "AuditEnabled": False},
    ]
    get_call = AsyncMock(return_value={"value": mailboxes})
    set_call = AsyncMock(return_value={})

    async def fake_exo(token, tenant, cmdlet, params=None):
        if cmdlet == "Get-Mailbox":
            return await get_call(token, tenant, cmdlet, params)
        return await set_call(token, tenant, cmdlet, params)

    with patch("app.services.m365_best_practices._exo_invoke_command", side_effect=fake_exo):
        result = await bp_service._remediate_foreach_mailbox(
            "exo-token", "tenant-id", 1, "bp_mailbox_auditing_enabled",
            {"AuditEnabled": True},
        )
    assert result is True
    # Set-Mailbox called exactly once (only for bob)
    assert set_call.call_count == 1
    _, _, _, params = set_call.call_args[0]
    assert params["Identity"] == "bob@contoso.com"
    assert params["AuditEnabled"] is True


@pytest.mark.anyio("asyncio")
async def test_remediate_foreach_mailbox_returns_false_on_set_error():
    """Returns False when Set-Mailbox fails for at least one mailbox."""
    mailboxes = [{"UserPrincipalName": "alice@contoso.com", "AuditEnabled": False}]

    async def fake_exo(token, tenant, cmdlet, params=None):
        if cmdlet == "Get-Mailbox":
            return {"value": mailboxes}
        raise bp_service.M365Error("permission denied")

    with patch("app.services.m365_best_practices._exo_invoke_command", side_effect=fake_exo):
        result = await bp_service._remediate_foreach_mailbox(
            "exo-token", "tenant-id", 1, "bp_mailbox_auditing_enabled",
            {"AuditEnabled": True},
        )
    assert result is False


@pytest.mark.anyio("asyncio")
async def test_remediate_foreach_mailbox_skips_all_when_already_compliant():
    """No Set-Mailbox calls when all mailboxes are already compliant."""
    mailboxes = [
        {"UserPrincipalName": "alice@contoso.com", "AuditEnabled": True},
        {"UserPrincipalName": "bob@contoso.com", "AuditEnabled": True},
    ]
    set_call = AsyncMock(return_value={})

    async def fake_exo(token, tenant, cmdlet, params=None):
        if cmdlet == "Get-Mailbox":
            return {"value": mailboxes}
        return await set_call(token, tenant, cmdlet, params)

    with patch("app.services.m365_best_practices._exo_invoke_command", side_effect=fake_exo):
        result = await bp_service._remediate_foreach_mailbox(
            "exo-token", "tenant-id", 1, "bp_mailbox_auditing_enabled",
            {"AuditEnabled": True},
        )
    assert result is True
    assert set_call.call_count == 0


# ---------------------------------------------------------------------------
# bp_mailbox_auditing_enabled catalog entry
# ---------------------------------------------------------------------------


def test_bp_mailbox_auditing_enabled_catalog_entry():
    catalog = {bp["id"]: bp for bp in bp_service.list_best_practices()}
    entry = catalog.get("bp_mailbox_auditing_enabled")
    assert entry is not None, "bp_mailbox_auditing_enabled must be in the catalog"
    assert entry["has_remediation"] is True
    assert entry["is_cis_benchmark"] is True
    assert "AuditEnabled" in entry["remediation"]



# ---------------------------------------------------------------------------
# SharePoint Online checks (Graph /admin/sharepoint/settings)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_external_content_sharing_restricted_pass():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"sharingCapability": "existingExternalUserSharingOnly"},
    ):
        result = await bp_service._check_external_content_sharing_restricted("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_external_content_sharing_restricted_fail():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"sharingCapability": "externalUserAndGuestSharing"},
    ):
        result = await bp_service._check_external_content_sharing_restricted("token")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_external_content_sharing_restricted_unknown_on_error():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        side_effect=M365Error("forbidden"),
    ):
        result = await bp_service._check_external_content_sharing_restricted("token")
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_sp_guests_cannot_share_unowned_pass():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"isResharingByExternalUsersEnabled": False},
    ):
        result = await bp_service._check_sp_guests_cannot_share_unowned("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_sp_guests_cannot_share_unowned_fail():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"isResharingByExternalUsersEnabled": True},
    ):
        result = await bp_service._check_sp_guests_cannot_share_unowned("token")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_modern_auth_sp_apps_pass():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"isLegacyAuthProtocolsEnabled": False},
    ):
        result = await bp_service._check_modern_auth_sp_apps("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_modern_auth_sp_apps_fail():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"isLegacyAuthProtocolsEnabled": True},
    ):
        result = await bp_service._check_modern_auth_sp_apps("token")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_link_sharing_restricted_spo_od_pass():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"defaultSharingLinkType": "direct", "defaultLinkPermission": "view"},
    ):
        result = await bp_service._check_link_sharing_restricted_spo_od("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_link_sharing_restricted_spo_od_fail():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"defaultSharingLinkType": "anonymous", "defaultLinkPermission": "edit"},
    ):
        result = await bp_service._check_link_sharing_restricted_spo_od("token")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_sharepoint_infected_files_block_pass():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"isDisableInfectedFileDownload": True},
    ):
        result = await bp_service._check_sharepoint_infected_files_block("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_sharepoint_infected_files_block_fail():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"isDisableInfectedFileDownload": False},
    ):
        result = await bp_service._check_sharepoint_infected_files_block("token")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_sharepoint_infected_files_block_unknown_when_property_missing():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"sharingCapability": "disabled"},
    ):
        result = await bp_service._check_sharepoint_infected_files_block("token")
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_sharepoint_sign_out_inactive_users_pass():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={
            "idleSignOutEnabled": True,
            "idleSignOutWarnAfterSeconds": 2700,
            "idleSignOutSignOutAfterSeconds": 300,
        },
    ):
        result = await bp_service._check_sharepoint_sign_out_inactive_users("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_sharepoint_sign_out_inactive_users_fail_disabled():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"idleSignOutEnabled": False},
    ):
        result = await bp_service._check_sharepoint_sign_out_inactive_users("token")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_sharepoint_sign_out_inactive_users_fail_timeout_too_long():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={
            "idleSignOutEnabled": True,
            "idleSignOutWarnAfterSeconds": 3600,
            "idleSignOutSignOutAfterSeconds": 900,
        },
    ):
        result = await bp_service._check_sharepoint_sign_out_inactive_users("token")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_sharepoint_sign_out_inactive_users_unknown_on_error():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        side_effect=M365Error("forbidden"),
    ):
        result = await bp_service._check_sharepoint_sign_out_inactive_users("token")
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_sharepoint_sign_out_inactive_users_unknown_when_property_missing():
    with patch(
        "app.services.m365_best_practices._graph_get",
        new_callable=AsyncMock,
        return_value={"sharingCapability": "disabled"},
    ):
        result = await bp_service._check_sharepoint_sign_out_inactive_users("token")
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# Defender for Office 365 checks (EXO)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_safe_links_office_apps_pass():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [
            {
                "Name": "Strict Safe Links",
                "EnableSafeLinksForOffice": True,
                "TrackClicks": True,
                "AllowClickThrough": False,
            }
        ]},
    ):
        result = await bp_service._check_safe_links_office_apps("exo-token", "tenant-id")
    assert result["status"] == "pass"
    assert "Strict Safe Links" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_safe_links_office_apps_fail_when_disabled():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [
            {
                "Name": "Default",
                "EnableSafeLinksForOffice": False,
                "TrackClicks": True,
                "AllowClickThrough": True,
            }
        ]},
    ):
        result = await bp_service._check_safe_links_office_apps("exo-token", "tenant-id")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_safe_links_office_apps_unknown_on_exo_error():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("timeout"),
    ):
        result = await bp_service._check_safe_links_office_apps("exo-token", "tenant-id")
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_zap_teams_on_pass():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [{"Name": "Teams Protection Policy", "ZapEnabled": True}]},
    ):
        result = await bp_service._check_zap_teams_on("exo-token", "tenant-id")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_zap_teams_on_fail():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [{"Name": "Teams Protection Policy", "ZapEnabled": False}]},
    ):
        result = await bp_service._check_zap_teams_on("exo-token", "tenant-id")
    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_zap_teams_on_unknown_on_exo_error():
    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("not found"),
    ):
        result = await bp_service._check_zap_teams_on("exo-token", "tenant-id")
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# DNS checks (SPF / DMARC)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_spf_records_published_pass():
    domains = [
        {"id": "contoso.com", "isVerified": True},
    ]
    with (
        patch(
            "app.services.m365_best_practices._graph_get_all",
            new_callable=AsyncMock,
            return_value=domains,
        ),
        patch(
            "app.services.m365_best_practices._dns_txt_records",
            new_callable=AsyncMock,
            return_value=["v=spf1 include:spf.protection.outlook.com -all"],
        ),
    ):
        result = await bp_service._check_spf_records_published("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_spf_records_published_fail_when_missing():
    domains = [{"id": "contoso.com", "isVerified": True}]
    with (
        patch(
            "app.services.m365_best_practices._graph_get_all",
            new_callable=AsyncMock,
            return_value=domains,
        ),
        patch(
            "app.services.m365_best_practices._dns_txt_records",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await bp_service._check_spf_records_published("token")
    assert result["status"] == "fail"
    assert re.search(r"\bcontoso\.com\b", result["details"])


@pytest.mark.anyio("asyncio")
async def test_check_spf_records_published_skips_onmicrosoft_domains():
    domains = [
        {"id": "contoso.onmicrosoft.com", "isVerified": True},
    ]
    with patch(
        "app.services.m365_best_practices._graph_get_all",
        new_callable=AsyncMock,
        return_value=domains,
    ):
        result = await bp_service._check_spf_records_published("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_dmarc_records_published_pass():
    domains = [{"id": "contoso.com", "isVerified": True}]
    with (
        patch(
            "app.services.m365_best_practices._graph_get_all",
            new_callable=AsyncMock,
            return_value=domains,
        ),
        patch(
            "app.services.m365_best_practices._dns_txt_records",
            new_callable=AsyncMock,
            return_value=["v=DMARC1; p=quarantine; rua=mailto:dmarc@contoso.com"],
        ),
    ):
        result = await bp_service._check_dmarc_records_published("token")
    assert result["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_check_dmarc_records_published_fail_when_missing():
    domains = [{"id": "contoso.com", "isVerified": True}]
    with (
        patch(
            "app.services.m365_best_practices._graph_get_all",
            new_callable=AsyncMock,
            return_value=domains,
        ),
        patch(
            "app.services.m365_best_practices._dns_txt_records",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await bp_service._check_dmarc_records_published("token")
    assert result["status"] == "fail"
    assert re.search(r"\bcontoso\.com\b", result["details"])


@pytest.mark.anyio("asyncio")
async def test_check_dmarc_records_published_unknown_on_dns_failure():
    domains = [{"id": "contoso.com", "isVerified": True}]
    with (
        patch(
            "app.services.m365_best_practices._graph_get_all",
            new_callable=AsyncMock,
            return_value=domains,
        ),
        patch(
            "app.services.m365_best_practices._dns_txt_records",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await bp_service._check_dmarc_records_published("token")
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_spf_records_published_unknown_on_graph_error():
    with patch(
        "app.services.m365_best_practices._graph_get_all",
        new_callable=AsyncMock,
        side_effect=M365Error("Graph error"),
    ):
        result = await bp_service._check_spf_records_published("token")
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# Block users who reached the message limit check
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_block_users_message_limit_pass():
    """All outbound spam filter policies have ActionWhenThresholdReached=BlockUser: pass."""
    from app.services.m365_best_practices import _check_block_users_message_limit

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
    ) as mock_cmd:
        mock_cmd.return_value = {
            "value": [
                {
                    "Name": "Default",
                    "ActionWhenThresholdReached": "BlockUser",
                }
            ]
        }
        result = await _check_block_users_message_limit("token", "tenant-id")

    assert result["status"] == "pass"
    assert result["check_id"] == "bp_block_users_message_limit"


@pytest.mark.anyio("asyncio")
async def test_check_block_users_message_limit_fail_when_alert_only():
    """Policy with ActionWhenThresholdReached=Alert should fail."""
    from app.services.m365_best_practices import _check_block_users_message_limit

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
    ) as mock_cmd:
        mock_cmd.return_value = {
            "value": [
                {
                    "Name": "Default",
                    "ActionWhenThresholdReached": "Alert",
                }
            ]
        }
        result = await _check_block_users_message_limit("token", "tenant-id")

    assert result["status"] == "fail"
    assert "BlockUser" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_block_users_message_limit_fail_when_restrict():
    """Policy with ActionWhenThresholdReached=RestrictAccess should fail."""
    from app.services.m365_best_practices import _check_block_users_message_limit

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
    ) as mock_cmd:
        mock_cmd.return_value = {
            "value": [
                {
                    "Name": "Default",
                    "ActionWhenThresholdReached": "RestrictAccess",
                }
            ]
        }
        result = await _check_block_users_message_limit("token", "tenant-id")

    assert result["status"] == "fail"


@pytest.mark.anyio("asyncio")
async def test_check_block_users_message_limit_unknown_on_error():
    """EXO error returns unknown status with error message."""
    from app.services.m365_best_practices import _check_block_users_message_limit

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=M365Error("EXO unavailable"),
    ):
        result = await _check_block_users_message_limit("token", "tenant-id")

    assert result["status"] == "unknown"
    assert "EXO unavailable" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_block_users_message_limit_unknown_when_no_policies():
    """Empty policy list returns unknown status."""
    from app.services.m365_best_practices import _check_block_users_message_limit

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
    ) as mock_cmd:
        mock_cmd.return_value = {"value": []}
        result = await _check_block_users_message_limit("token", "tenant-id")

    assert result["status"] == "unknown"


def test_block_users_message_limit_in_catalog():
    """bp_block_users_message_limit must be present in the public catalog."""
    catalog = bp_service.list_best_practices()
    ids = {bp["id"] for bp in catalog}
    assert "bp_block_users_message_limit" in ids


def test_block_users_message_limit_catalog_entry():
    """bp_block_users_message_limit catalog entry must have the expected fields."""
    catalog = bp_service.list_best_practices()
    entry = next(bp for bp in catalog if bp["id"] == "bp_block_users_message_limit")
    assert entry.get("has_remediation") is True
    assert entry.get("default_enabled") is True
    assert "BlockUser" in entry["remediation"]
    # Internal implementation keys must not be exposed
    assert "source" not in entry
    assert "remediation_cmdlet" not in entry
    assert "remediation_params" not in entry


# ---------------------------------------------------------------------------
# _check_automatic_email_forwarding
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_automatic_email_forwarding_pass_when_disabled():
    """PASS when AutoForwardEnabled is False on the Default remote domain."""
    from app.services.m365_best_practices import _check_automatic_email_forwarding

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [{"Identity": "Default", "AutoForwardEnabled": False}]},
    ):
        result = await _check_automatic_email_forwarding("exo-token", "tenant-id")

    assert result["status"] == "pass"
    assert result["check_id"] == "bp_automatic_email_forwarding"


@pytest.mark.anyio("asyncio")
async def test_check_automatic_email_forwarding_fail_when_enabled():
    """FAIL when AutoForwardEnabled is True on the Default remote domain."""
    from app.services.m365_best_practices import _check_automatic_email_forwarding

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": [{"Identity": "Default", "AutoForwardEnabled": True}]},
    ):
        result = await _check_automatic_email_forwarding("exo-token", "tenant-id")

    assert result["status"] == "fail"
    assert "AutoForwardEnabled" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_automatic_email_forwarding_unknown_on_exo_error():
    """UNKNOWN when EXO command raises M365Error."""
    from app.services.m365_best_practices import _check_automatic_email_forwarding

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        side_effect=bp_service.M365Error("EXO unavailable"),
    ):
        result = await _check_automatic_email_forwarding("exo-token", "tenant-id")

    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_automatic_email_forwarding_unknown_when_empty():
    """UNKNOWN when no remote domain rows are returned."""
    from app.services.m365_best_practices import _check_automatic_email_forwarding

    with patch(
        "app.services.m365_best_practices._exo_invoke_command",
        new_callable=AsyncMock,
        return_value={"value": []},
    ):
        result = await _check_automatic_email_forwarding("exo-token", "tenant-id")

    assert result["status"] == "unknown"


def test_automatic_email_forwarding_in_catalog():
    """bp_automatic_email_forwarding must be present in the public catalog."""
    catalog = bp_service.list_best_practices()
    ids = {bp.get("id") for bp in catalog}
    assert "bp_automatic_email_forwarding" in ids


def test_automatic_email_forwarding_catalog_entry():
    """bp_automatic_email_forwarding catalog entry must have the expected fields."""
    catalog = bp_service.list_best_practices()
    entry = next(bp for bp in catalog if bp["id"] == "bp_automatic_email_forwarding")
    assert entry.get("has_remediation") is True
    assert entry.get("default_enabled") is True
    assert "AutoForwardEnabled" in entry["remediation"]
    # Internal implementation keys must not be exposed
    assert "source" not in entry
    assert "remediation_cmdlet" not in entry
    assert "remediation_params" not in entry
