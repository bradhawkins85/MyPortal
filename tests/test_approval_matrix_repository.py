from unittest.mock import AsyncMock
from pathlib import Path

import pytest

from app.repositories import approval_matrix


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


def test_approval_configuration_uses_checkbox_lists_for_all_multi_value_selectors():
    template = Path("app/templates/admin/approvals.html").read_text(encoding="utf-8")

    assert 'type="checkbox"' in template
    assert 'name="{{ name }}"' in template
    assert "technicianUserIds" in template
    assert "technicalRoleIds" in template
    assert "contactStaffIds" in template
    assert "companyRoles" in template
    assert "jobTitles" in template
    assert " multiple" not in template
    assert "Department / job titles" in template


def test_department_groupings_are_driven_by_company_job_titles():
    routes = Path("app/features/tickets/admin_routes.py").read_text(encoding="utf-8")
    repository = Path("app/repositories/approval_matrix.py").read_text(encoding="utf-8")

    assert 'FROM staff WHERE company_id = %s AND enabled = 1' in routes
    assert "TRIM(position)" not in routes
    assert "), position)" not in repository
    assert '"department_manager"' not in routes
    assert '"department_manager"' not in repository


def test_approval_selector_migration_preserves_legacy_technician():
    migration = Path("migrations/379_approval_selector_options.sql").read_text(encoding="utf-8")

    assert "approval_configuration_technicians" in migration
    assert "approval_configuration_technical_roles" in migration
    assert "approval_configuration_company_roles" in migration
    assert "approval_configuration_job_titles" in migration
    assert "SELECT id, technician_user_id, 0 FROM approval_configurations" in migration
