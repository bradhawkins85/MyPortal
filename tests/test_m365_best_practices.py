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
