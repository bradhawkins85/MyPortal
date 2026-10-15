"""Tenant-wide archive checks and Exchange Online remediation."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365_best_practices as service
from app.services.m365 import M365Error


CHECK_ID = "bp_auto_expanding_archive_enabled"


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize(
    ("value", "status"),
    [(True, "pass"), (False, "fail"), ("True", "pass"), ("False", "fail"),
     ({"value": True}, "pass"), ({"value": "False"}, "fail"),
     (None, "unknown"), ("unexpected", "unknown"), (1, "unknown")],
)
async def test_check_uses_organization_archive_setting(value, status):
    invoke = AsyncMock(return_value={"value": [{"AutoExpandingArchiveEnabled": value}]})
    with patch.object(service, "_exo_invoke_command", invoke):
        result = await service._check_auto_expanding_archive_enabled("token", "tenant")
    assert result["check_id"] == CHECK_ID
    assert result["status"] == status
    invoke.assert_awaited_once_with("token", "tenant", "Get-OrganizationConfig")


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("payload", [{}, {"value": []}, {"value": [{}]}])
async def test_check_returns_unknown_for_missing_configuration(payload):
    with patch.object(service, "_exo_invoke_command", AsyncMock(return_value=payload)):
        result = await service._check_auto_expanding_archive_enabled("token", "tenant")
    assert result["status"] == "unknown"


@pytest.mark.anyio("asyncio")
async def test_check_returns_unknown_on_exchange_error():
    with patch.object(service, "_exo_invoke_command", AsyncMock(side_effect=M365Error("Unavailable"))):
        result = await service._check_auto_expanding_archive_enabled("token", "tenant")
    assert result["status"] == "unknown"
    assert "Unavailable" in result["details"]


def test_catalog_registers_tenant_wide_check_and_remediation():
    entry = next(bp for bp in service._BEST_PRACTICES if bp["id"] == CHECK_ID)
    assert entry["source"] is service._check_auto_expanding_archive_enabled
    assert entry["source_type"] == "exo"
    assert entry["default_enabled"] is True
    assert entry["has_remediation"] is True
    assert entry["requires_licenses"] == [service.CAP_EXCHANGE_ONLINE]
    assert "cannot be disabled" in entry["remediation"]


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("error", [None, M365Error("Exchange update failed")])
async def test_remediation_sets_organization_switch_and_records_outcome(error):
    invoke = AsyncMock(return_value={}, side_effect=error)
    record = AsyncMock()
    with (
        patch.object(service, "_acquire_exo_access_token", AsyncMock(return_value=("token", "tenant"))),
        patch.object(service, "_exo_invoke_command", invoke),
        patch.object(service.bp_repo, "update_remediation_status", record),
    ):
        result = await service.remediate_check(company_id=5, check_id=CHECK_ID)
    invoke.assert_awaited_once_with(
        "token", "tenant", "Set-OrganizationConfig", {"AutoExpandingArchive": True},
    )
    assert result["success"] is (error is None)
    assert record.await_args.kwargs["check_id"] == CHECK_ID
    assert record.await_args.kwargs["remediation_status"] == ("failed" if error else "success")
    if error:
        assert "Exchange update failed" in result["message"]
