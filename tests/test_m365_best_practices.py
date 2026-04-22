"""Tests for the Microsoft 365 Best Practices service."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365_best_practices as bp_service
from app.services.m365 import M365Error


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
        mock_map.return_value = {first_id: False, second_id: True}
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
    ):
        out = await bp_service.get_last_results(company_id=1)

    assert len(out) == 1
    item = out[0]
    assert item["has_remediation"] is True
    assert item["remediation_status"] == "success"
    assert item["remediated_at"] == datetime(2026, 1, 2, 10, 0, 0)

