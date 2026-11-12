from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app.core.database import Database
from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.api.routes import invoices as invoice_api
from app.features.invoices import routes
from app.schemas.invoices import InvoiceCreate, InvoiceUpdate
from app.services import invoice_assignment


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_owner_migration_runs_on_sqlite_and_startup_can_repeat(tmp_path, monkeypatch):
    migration = Path("migrations/556_invoice_visibility_owners.sql")
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / migration.name).write_text(migration.read_text())
    database = Database()
    database._use_sqlite = True
    monkeypatch.setattr(database, "_get_sqlite_path", lambda: tmp_path / "invoice.sqlite")
    monkeypatch.setattr(database, "_get_migrations_dir", lambda: migration_dir)
    await database.connect()
    try:
        await database.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY)")
        await database.run_migrations()
        await database.run_migrations()
        columns = await database.fetch_all("PRAGMA table_info(invoices)")
        assert {"created_by", "assigned_user_id"} <= {column["name"] for column in columns}
    finally:
        await database.disconnect()


@pytest.mark.anyio
async def test_invoice_assignment_checks_company_membership_and_allows_unassign(monkeypatch):
    monkeypatch.setattr(invoice_assignment.users_repo, "list_company_members", AsyncMock(return_value=[{"id": 5}]))
    await invoice_assignment.validate_invoice_assignee(7, 5)
    await invoice_assignment.validate_invoice_assignee(7, None)
    with pytest.raises(HTTPException) as exc:
        await invoice_assignment.validate_invoice_assignee(7, 9)
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_company_admin_can_assign_existing_invoice(monkeypatch):
    user = {"id": 1}
    monkeypatch.setattr(routes, "_load_invoice_context", AsyncMock(return_value=(user, {"is_admin": True}, {}, 7, None)))
    invoice = {"id": 10, "company_id": 7, "assigned_user_id": None}
    monkeypatch.setattr(routes.invoice_repo, "get_invoice_by_id", AsyncMock(return_value=invoice))
    patch = AsyncMock(return_value=invoice | {"assigned_user_id": 5})
    monkeypatch.setattr(routes.invoice_repo, "patch_invoice", patch)
    monkeypatch.setattr(routes, "validate_invoice_assignee", AsyncMock())
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)
    response = await routes.assign_invoice(Request({"type": "http"}), 10, "5")
    assert response.status_code == 303
    patch.assert_awaited_once_with(10, assigned_user_id=5)
    audit.assert_awaited_once()


@pytest.mark.parametrize("membership, company_id", [({"menu_permissions": {"menu.invoices": "all"}}, 7), ({"is_admin": True}, 8)])
@pytest.mark.anyio
async def test_assignment_blocks_non_admins_and_other_companies(monkeypatch, membership, company_id):
    monkeypatch.setattr(routes, "_load_invoice_context", AsyncMock(return_value=({"id": 1}, membership, {}, 7, None)))
    monkeypatch.setattr(routes.invoice_repo, "get_invoice_by_id", AsyncMock(return_value={"id": 10, "company_id": company_id}))
    patch = AsyncMock()
    monkeypatch.setattr(routes.invoice_repo, "patch_invoice", patch)
    with pytest.raises(HTTPException) as exc:
        await routes.assign_invoice(Request({"type": "http"}), 10, "5")
    assert exc.value.status_code in {403, 404}
    patch.assert_not_awaited()


def test_creator_cannot_be_forged_through_invoice_payload():
    payload = InvoiceCreate(companyId=7, invoiceNumber="INV1", amount="12.00", createdBy=999, assignedUserId=5)
    assert "created_by" not in payload.model_dump()
    assert payload.assigned_user_id == 5
    assert InvoiceUpdate(assignedUserId=None).model_dump(exclude_unset=True) == {"assigned_user_id": None}


@pytest.mark.anyio
async def test_invoice_create_records_authenticated_creator_and_validated_assignee(monkeypatch):
    app = FastAPI()
    app.include_router(invoice_api.router)
    app.dependency_overrides[require_super_admin] = lambda: {"id": 5, "is_super_admin": True}
    app.dependency_overrides[require_database] = lambda: None
    monkeypatch.setattr(invoice_api.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 7}))
    monkeypatch.setattr(invoice_assignment.users_repo, "list_company_members", AsyncMock(return_value=[{"id": 8}]))
    create = AsyncMock(return_value={"id": 1, "company_id": 7, "invoice_number": "INV1", "amount": "12.00", "status": "draft", "created_by": 5, "assigned_user_id": 8})
    monkeypatch.setattr(invoice_api.invoice_repo, "create_invoice", create)
    monkeypatch.setattr(invoice_api.audit_service, "record", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/invoices", json={"companyId": 7, "invoiceNumber": "INV1", "amount": "12.00", "createdBy": 999, "assignedUserId": 8})
        assert response.status_code == 201, response.text
        assert response.json()["createdBy"] == 5
        assert create.await_args.kwargs["created_by"] == 5
        assert create.await_args.kwargs["assigned_user_id"] == 8
        response = await client.post("/api/invoices", json={"companyId": 7, "invoiceNumber": "INV2", "amount": "12.00", "assignedUserId": 99})
        assert response.status_code == 400
        assert create.await_count == 1
