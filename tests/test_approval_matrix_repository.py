from unittest.mock import AsyncMock
from pathlib import Path
from uuid import UUID

import pytest

from app.repositories import approval_matrix
from app.services import modules


@pytest.mark.anyio
async def test_assign_to_ticket_rejects_configuration_from_another_company(monkeypatch):
    monkeypatch.setattr(
        approval_matrix,
        "get_configuration",
        AsyncMock(return_value={"id": 3, "company_id": 8, "workflow_type": "change"}),
    )
    monkeypatch.setattr(
        approval_matrix.db,
        "fetch_one",
        AsyncMock(return_value={"company_id": 9}),
    )

    with pytest.raises(ValueError, match="ticket company"):
        await approval_matrix.assign_to_ticket(ticket_id=4, configuration_id=3)


@pytest.mark.anyio
async def test_set_decision_only_accepts_final_statuses():
    with pytest.raises(ValueError, match="approved or denied"):
        await approval_matrix.set_decision(
            ticket_id=4,
            decision_id=2,
            decision_status="pending",
            decided_by_user_id=1,
        )


@pytest.mark.anyio
async def test_ticket_configuration_list_only_returns_active_company_rows(monkeypatch):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(approval_matrix.db, "fetch_all", fetch_all)

    await approval_matrix.list_configurations(42)

    query, params = fetch_all.await_args.args
    assert "ac.company_id = %s" in query
    assert "ac.is_active = 1" in query
    assert params == (42, "change")


@pytest.mark.anyio
async def test_admin_configuration_list_can_include_inactive_rows(monkeypatch):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(approval_matrix.db, "fetch_all", fetch_all)

    await approval_matrix.list_configurations(7, include_inactive=True)

    query, params = fetch_all.await_args.args
    assert "ac.is_active = 1" not in query
    assert params == (7, "change")


@pytest.mark.anyio
async def test_update_configuration_requires_company_ownership(monkeypatch):
    monkeypatch.setattr(approval_matrix.db, "fetch_one", AsyncMock(return_value=None))
    execute = AsyncMock()
    monkeypatch.setattr(approval_matrix.db, "execute", execute)

    updated = await approval_matrix.update_configuration(
        configuration_id=3,
        company_id=99,
        name="Restricted change",
        description=None,
        technician_user_id=2,
        contact_staff_ids=[4],
    )

    assert updated is False
    execute.assert_not_awaited()


def test_approval_configuration_does_not_require_selected_approvers():
    template = Path("app/templates/admin/approvals.html").read_text(encoding="utf-8")
    routes = Path("app/features/tickets/admin_routes.py").read_text(encoding="utf-8")

    assert "technicianUserIds" not in template
    assert "technicalRoleIds" not in template
    assert "contactStaffIds" not in template
    assert "companyRoles" not in template
    assert "jobTitles" not in template
    assert "Select at least one technical employee" not in routes
    assert "job title approver" not in routes
    assert "classifier technicians can use" in template


def test_approval_selector_migration_preserves_legacy_technician():
    migration = Path("migrations/379_approval_selector_options.sql").read_text(encoding="utf-8")

    assert "approval_configuration_technicians" in migration
    assert "approval_configuration_technical_roles" in migration
    assert "approval_configuration_company_roles" in migration
    assert "approval_configuration_job_titles" in migration
    assert "SELECT id, technician_user_id, 0 FROM approval_configurations" in migration


@pytest.mark.anyio
async def test_create_configuration_assigns_a_guid(monkeypatch):
    insert = AsyncMock(return_value=12)
    monkeypatch.setattr(approval_matrix.db, "execute_returning_lastrowid", insert)
    monkeypatch.setattr(approval_matrix.db, "execute", AsyncMock())
    monkeypatch.setattr(approval_matrix, "_replace_selector_options", AsyncMock())
    monkeypatch.setattr(
        approval_matrix,
        "get_configuration",
        AsyncMock(return_value={"id": 12}),
    )

    await approval_matrix.create_configuration(
        company_id=4,
        name="Firewall change",
        technician_user_id=7,
        contact_staff_ids=[],
    )

    query, params = insert.await_args.args
    assert "(guid, company_id" in query
    assert str(UUID(params[0])) == params[0]


@pytest.mark.anyio
async def test_assign_to_ticket_uses_type_as_classifier_without_decisions(monkeypatch):
    monkeypatch.setattr(
        approval_matrix,
        "get_configuration",
        AsyncMock(return_value={
            "id": 3, "company_id": 8, "name": "Firewall change",
            "workflow_type": "change",
        }),
    )
    monkeypatch.setattr(
        approval_matrix.db,
        "fetch_one",
        AsyncMock(side_effect=[{"company_id": 8}, None]),
    )
    insert = AsyncMock(return_value=21)
    execute = AsyncMock()
    monkeypatch.setattr(approval_matrix.db, "execute_returning_lastrowid", insert)
    monkeypatch.setattr(approval_matrix.db, "execute", execute)
    monkeypatch.setattr(
        approval_matrix,
        "get_ticket_workflow",
        AsyncMock(return_value={"id": 21, "pending_count": 0, "decisions": []}),
    )

    result = await approval_matrix.assign_to_ticket(ticket_id=4, configuration_id=3)

    assert result["pending_count"] == 0
    assert all("ticket_approval_decisions" not in call.args[0] for call in execute.await_args_list)


@pytest.mark.anyio
async def test_assign_to_ticket_by_guid_resolves_public_identifier(monkeypatch):
    approval_guid = "e30ad5d9-f3a2-4a16-b53d-b77f5ec1505e"
    monkeypatch.setattr(
        approval_matrix,
        "get_configuration_by_guid",
        AsyncMock(return_value={"id": 31}),
    )
    assign = AsyncMock(return_value={"id": 8})
    monkeypatch.setattr(approval_matrix, "assign_to_ticket", assign)

    result = await approval_matrix.assign_to_ticket_by_guid(
        ticket_id=5,
        approval_guid=approval_guid,
    )

    assert result == {"id": 8}
    assign.assert_awaited_once_with(
        ticket_id=5,
        configuration_id=31,
        assigned_by_user_id=None,
    )


def test_approvals_table_displays_guid_column():
    template = Path("app/templates/admin/approvals.html").read_text(encoding="utf-8")

    assert '"key": "guid", "label": "GUID"' in template
    assert 'data-column-key="guid"><code>{{ configuration.guid }}</code>' in template


@pytest.mark.anyio
async def test_automation_assigns_approval_by_guid(monkeypatch):
    approval_guid = "e30ad5d9-f3a2-4a16-b53d-b77f5ec1505e"
    assign = AsyncMock(return_value={"id": 8, "pending_count": 2})
    monkeypatch.setattr(approval_matrix, "assign_to_ticket_by_guid", assign)

    result = await modules._invoke_assign_approval_configuration(
        {},
        {"approval_guid": approval_guid, "context": {"ticket_id": 5}},
    )

    assign.assert_awaited_once_with(
        ticket_id=5,
        approval_guid=approval_guid,
        assigned_by_user_id=None,
    )
    assert result["approval_guid"] == approval_guid
    assert result["pending_count"] == 2
