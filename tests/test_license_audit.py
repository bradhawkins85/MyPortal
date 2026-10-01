"""Regression coverage for financially relevant licence audit events."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.api.routes import licenses as routes
from app.schemas.licenses import LicenseCreate, LicenseUpdate


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []})


def _license(**changes):
    record = {
        "id": 8,
        "company_id": 3,
        "name": "Microsoft 365",
        "platform": "M365",
        "count": 10,
        "allocated": 2,
        "expiry_date": None,
        "contract_term": "monthly",
        "auto_renew": True,
    }
    record.update(changes)
    return record


@pytest.mark.anyio("asyncio")
async def test_create_license_records_actor_company_and_configuration(monkeypatch) -> None:
    created = _license()
    monkeypatch.setattr(routes.license_repo, "create_license", AsyncMock(return_value=created))
    monkeypatch.setattr(routes.license_repo, "record_usage_if_changed", AsyncMock())
    monkeypatch.setattr(
        routes.staff_workflow_service, "process_paused_license_executions", AsyncMock()
    )
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record_create", audit)

    await routes.create_license(
        LicenseCreate(**{key: created[key] for key in (
            "company_id", "name", "platform", "count", "expiry_date",
            "contract_term", "auto_renew",
        )}),
        _request(),
        user={"id": 41},
    )

    call = audit.await_args.kwargs
    assert call["action"] == "license.create"
    assert call["user_id"] == 41
    assert call["metadata"] == {"company_id": 3}
    assert call["after"] == routes._audit_license(created)


@pytest.mark.anyio("asyncio")
async def test_update_license_supplies_before_and_after_for_diffing(monkeypatch) -> None:
    existing = _license()
    updated = _license(count=12)
    monkeypatch.setattr(routes.license_repo, "get_license_by_id", AsyncMock(return_value=existing))
    monkeypatch.setattr(routes.license_repo, "update_license", AsyncMock(return_value=updated))
    monkeypatch.setattr(routes.license_repo, "record_usage_if_changed", AsyncMock())
    monkeypatch.setattr(
        routes.staff_workflow_service, "process_paused_license_executions", AsyncMock()
    )
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)

    await routes.update_license(
        8, LicenseUpdate(count=12), _request(), user={"id": 41}
    )

    call = audit.await_args.kwargs
    assert call["action"] == "license.update"
    assert call["before"]["count"] == 10
    assert call["after"]["count"] == 12
    assert call["metadata"] == {"company_id": 3}


@pytest.mark.anyio("asyncio")
async def test_delete_license_retains_identifying_context(monkeypatch) -> None:
    existing = _license()
    monkeypatch.setattr(routes.license_repo, "get_license_by_id", AsyncMock(return_value=existing))
    monkeypatch.setattr(routes.license_repo, "delete_license", AsyncMock())
    monkeypatch.setattr(
        routes.staff_workflow_service, "process_paused_license_executions", AsyncMock()
    )
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record_delete", audit)

    await routes.delete_license(8, _request(), user={"id": 41})

    call = audit.await_args.kwargs
    assert call["action"] == "license.delete"
    assert call["before"]["name"] == "Microsoft 365"
    assert call["before"]["platform"] == "M365"


@pytest.mark.anyio("asyncio")
async def test_staff_assignment_is_audited_but_repeat_is_not(monkeypatch) -> None:
    existing = _license()
    monkeypatch.setattr(routes.license_repo, "get_license_by_id", AsyncMock(return_value=existing))
    monkeypatch.setattr(routes.license_repo, "is_staff_linked_to_license", AsyncMock(side_effect=[False, True]))
    monkeypatch.setattr(routes.license_repo, "link_staff_to_license", AsyncMock())
    monkeypatch.setattr(routes.license_repo, "record_usage_if_changed", AsyncMock())
    monkeypatch.setattr(
        routes.staff_workflow_service, "process_paused_license_executions", AsyncMock()
    )
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record_create", audit)

    await routes.link_staff(8, 22, _request(), user={"id": 41})
    await routes.link_staff(8, 22, _request(), user={"id": 41})

    audit.assert_awaited_once()
    assert audit.await_args.kwargs["after"] == {"license_id": 8, "staff_id": 22}


def test_license_audit_snapshot_excludes_derived_and_unknown_sensitive_fields() -> None:
    snapshot = routes._audit_license(
        _license(display_name="Friendly", allocated=9, token="must-not-appear")
    )

    assert "display_name" not in snapshot
    assert "allocated" not in snapshot
    assert "token" not in snapshot
    assert "must-not-appear" not in repr(snapshot)
