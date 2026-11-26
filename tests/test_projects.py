"""Tests for native project management."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import ImmutableMultiDict

from app.core.database import db
from app.features.projects import PACK
from app.repositories import projects as repo
from app.services import projects

MIGRATION = Path("migrations/583_projects.sql")
TODAY = date(2026, 11, 26)


def _form(**values):
    return ImmutableMultiDict([(key, str(value)) for key, value in values.items()])


def _project(**overrides):
    base = {"id": 1, "company_id": 5, "company_name": "Acme", "name": "Office move", "description": None,
            "status": "active", "billing_type": "fixed_fee", "budget_hours": 40.0, "budget_amount": 12000.0,
            "start_date": date(2026, 11, 1), "due_date": date(2026, 12, 20), "manager_user_id": None}
    base.update(overrides)
    return base


def test_pack_registers_routes():
    paths = {route.path for router in PACK.routers for route in router.routes}
    assert {"/admin/projects", "/admin/projects/{project_id:int}", "/admin/projects/{project_id:int}/delete",
            "/admin/projects/{project_id:int}/phases", "/admin/projects/{project_id:int}/phases/{phase_id:int}",
            "/admin/projects/{project_id:int}/tasks", "/admin/projects/{project_id:int}/tasks/{ticket_id:int}",
            "/admin/projects/{project_id:int}/milestones",
            "/admin/projects/{project_id:int}/milestones/{milestone_id:int}/invoice",
            "/api/projects", "/api/projects/{project_id}", "/api/projects/{project_id}/tasks",
            "/api/projects/{project_id}/milestones/{milestone_id}/invoice"} <= paths


def test_migration_runs_on_sqlite():
    adapted = db._adapt_sql_for_sqlite(MIGRATION.read_text(encoding="utf-8"))
    connection = sqlite3.connect(":memory:")
    for table in ("companies", "users", "tickets"):
        connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    for statement in db._split_sql_statements(adapted):
        connection.execute(statement)
    connection.execute("INSERT INTO projects (company_id, name) VALUES (1, 'x')")
    assert connection.execute("SELECT status, billing_type FROM projects").fetchone() == ("planning", "time_materials")
    connection.execute("INSERT INTO project_tickets (ticket_id, project_id) VALUES (9, 1)")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO project_tickets (ticket_id, project_id) VALUES (9, 1)")


def test_parse_project_form():
    values = projects.parse_project_form(_form(name=" Office  move ", company_id="5", status="active",
                                               billing_type="fixed_fee", budget_hours="40", budget_amount="12,000",
                                               start_date="2026-11-01", due_date="2026-12-20", manager_user_id="3"))
    assert values["name"] == "Office move" and values["budget_amount"] == Decimal("12000")
    assert values["due_date"] == date(2026, 12, 20) and values["manager_user_id"] == 3


def test_parse_project_form_reports_every_problem():
    with pytest.raises(projects.ProjectError) as excinfo:
        projects.parse_project_form(_form(name="", company_id="", status="done", billing_type="fixed_fee",
                                          budget_hours="-1", start_date="2026-12-01", due_date="2026-11-01"))
    assert set(excinfo.value.errors) == {"name", "company_id", "status", "budget_hours", "budget_amount", "due_date"}


def test_parse_milestone_form():
    values = projects.parse_milestone_form(_form(name="Deposit", amount="3600", due_date="2026-11-10", phase_id="2",
                                                 ready="1"), phase_ids=[2])
    assert values == {"name": "Deposit", "amount": Decimal("3600"), "due_date": date(2026, 11, 10), "phase_id": 2,
                      "status": "ready"}
    with pytest.raises(projects.ProjectError) as excinfo:
        projects.parse_milestone_form(_form(name="", amount="0", phase_id="9"), phase_ids=[2])
    assert set(excinfo.value.errors) == {"name", "amount", "phase_id"}


def test_summary_progress_hours_and_flags():
    summary = projects.summarise(_project(), {"tasks": 4, "done": 1, "estimate_hours": 30.0, "minutes_spent": 2580},
                                 today=TODAY)
    assert summary["progress"] == 25 and summary["hours_percent"] == 108
    assert "over_budget" in summary["flags"] and "due_soon" not in summary["flags"]
    assert summary["used_text"] == "43 h" and summary["budget_text"] == "40 h" and summary["budget_source"] == "budget"
    from_estimates = projects.summarise(_project(budget_hours=None, due_date=date(2026, 11, 20)),
                                        {"tasks": 2, "done": 0, "estimate_hours": 10.0, "minutes_spent": 540}, today=TODAY)
    assert from_estimates["budget_source"] == "estimates" and from_estimates["hours_percent"] == 90
    assert "near_budget" in from_estimates["flags"] and "overdue" in from_estimates["flags"]
    empty = projects.summarise(_project(budget_hours=None, status="completed", due_date=date(2020, 1, 1)), None, today=TODAY)
    assert empty["progress"] == 0 and empty["flags"] == [] and empty["budget_text"] == "No budget"


def test_financials_fixed_fee_margin_and_milestones():
    entries = [{"minutes_spent": 600, "labour_rate": 150, "is_billable": True},
               {"minutes_spent": 120, "labour_rate": 0, "is_billable": False}]
    milestones = [{"amount": 3600.0, "status": "invoiced"}, {"amount": 6000.0, "status": "pending"}]
    result = projects.financials(_project(), entries, milestones)
    assert result["time_value"] == 1500.0 and result["margin"] == 10500.0 and result["margin_percent"] == 88
    assert result["milestones_invoiced"] == 3600.0 and result["milestones_unplanned"] == 2400.0
    t_and_m = projects.financials(_project(billing_type="time_materials", budget_amount=1000.0), entries, [])
    assert t_and_m["budget_left"] == -500.0 and "margin" not in t_and_m


@pytest.mark.asyncio
async def test_link_ticket_rules(monkeypatch):
    from app.repositories import tickets as tickets_repo

    get_ticket = AsyncMock(return_value={"id": 9, "company_id": 6})
    monkeypatch.setattr(tickets_repo, "get_ticket", get_ticket)
    with pytest.raises(projects.ProjectError, match="project's company"):
        await projects.link_ticket(_project(), 9, phase_id=None, estimate_hours=None)
    get_ticket.return_value = {"id": 9, "company_id": 5}
    monkeypatch.setattr(repo, "get_task", AsyncMock(return_value={"project_id": 2}))
    with pytest.raises(projects.ProjectError, match="another project"):
        await projects.link_ticket(_project(), 9, phase_id=None, estimate_hours=None)
    monkeypatch.setattr(repo, "get_task", AsyncMock(return_value=None))
    add = AsyncMock()
    monkeypatch.setattr(repo, "add_task", add)
    await projects.link_ticket(_project(), 9, phase_id=3, estimate_hours=2.5)
    assert add.await_args.args == (1, 9) and add.await_args.kwargs == {"phase_id": 3, "estimate_hours": 2.5}


@pytest.mark.asyncio
async def test_create_task_ticket_for_the_projects_company(monkeypatch):
    from app.services import tickets as tickets_service

    create = AsyncMock(return_value={"id": 77, "ticket_number": "T77"})
    monkeypatch.setattr(tickets_service, "create_ticket", create)
    add = AsyncMock()
    monkeypatch.setattr(repo, "add_task", add)
    await projects.create_task_ticket(_project(), subject=" Install  switches ", description=None, assigned_user_id=3,
                                      phase_id=None, estimate_hours=4.0, user={"id": 1})
    kwargs = create.await_args.kwargs
    assert kwargs["company_id"] == 5 and kwargs["subject"] == "Install switches" and kwargs["module_slug"] == "projects"
    assert kwargs["assigned_user_id"] == 3
    assert add.await_args.args == (1, 77)
    with pytest.raises(projects.ProjectError):
        await projects.create_task_ticket(_project(), subject="  ", description=None, assigned_user_id=None,
                                          phase_id=None, estimate_hours=None, user=None)


@pytest.mark.asyncio
async def test_invoice_milestone_creates_draft_invoice_once(monkeypatch):
    from app.services import invoice_generator

    monkeypatch.setattr(repo, "claim_milestone_for_invoice", AsyncMock(return_value=True))
    mark = AsyncMock()
    monkeypatch.setattr(repo, "mark_milestone_invoiced", mark)
    generate = AsyncMock(return_value={"status": "succeeded", "invoice_id": 31, "invoice_number": "INV-0031"})
    monkeypatch.setattr(invoice_generator, "generate_invoice", generate)
    milestone = {"id": 4, "name": "Deposit", "amount": 3600.0, "status": "ready"}
    result = await projects.invoice_milestone(_project(), milestone, user={"id": 1})
    assert result["invoice_number"] == "INV-0031"
    kwargs = generate.await_args.kwargs
    assert generate.await_args.args == (5,)
    assert kwargs["include_ticket_items"] is False and kwargs["include_recurring_items"] is False
    assert kwargs["recurring_line_items"] == [{"Description": "Office move: Deposit", "Quantity": 1.0,
                                               "UnitAmount": 3600.0, "ItemCode": ""}]
    assert mark.await_args.kwargs["invoice_id"] == 31


@pytest.mark.asyncio
async def test_invoice_milestone_is_released_when_invoicing_fails(monkeypatch):
    from app.services import invoice_generator

    monkeypatch.setattr(repo, "claim_milestone_for_invoice", AsyncMock(return_value=True))
    mark = AsyncMock()
    monkeypatch.setattr(repo, "mark_milestone_invoiced", mark)
    monkeypatch.setattr(invoice_generator, "generate_invoice", AsyncMock(return_value={"status": "error", "reason": "db"}))
    with pytest.raises(projects.ProjectError):
        await projects.invoice_milestone(_project(), {"id": 4, "name": "x", "amount": 1.0, "status": "ready"}, user=None)
    assert mark.await_args.kwargs["status"] == "ready"


@pytest.mark.asyncio
async def test_invoice_milestone_refuses_double_billing(monkeypatch):
    monkeypatch.setattr(repo, "claim_milestone_for_invoice", AsyncMock(return_value=False))
    with pytest.raises(projects.ProjectError, match="already being invoiced"):
        await projects.invoice_milestone(_project(), {"id": 4, "name": "x", "amount": 1.0, "status": "ready"}, user=None)
    with pytest.raises(projects.ProjectError, match="already been invoiced"):
        await projects.invoice_milestone(_project(), {"id": 4, "name": "x", "amount": 1.0, "status": "invoiced"}, user=None)


@pytest.mark.asyncio
async def test_fixed_fee_lookup_never_blocks_invoicing(monkeypatch):
    monkeypatch.setattr(repo, "fixed_fee_ticket_ids", AsyncMock(side_effect=RuntimeError("no table")))
    assert await projects.fixed_fee_ticket_ids([1, 2]) == set()
    monkeypatch.setattr(repo, "fixed_fee_ticket_ids", AsyncMock(return_value={2}))
    assert await projects.fixed_fee_ticket_ids([1, "2", None, "x"]) == {2}


@pytest.mark.asyncio
async def test_local_invoice_generator_skips_fixed_fee_time_but_keeps_expenses(monkeypatch):
    from app.repositories import companies as company_repo
    from app.repositories import tickets as tickets_repo
    from app.services import invoice_generator, xero as xero_service

    monkeypatch.setattr(company_repo, "get_company_by_id", AsyncMock(return_value={"id": 5, "name": "Acme"}))
    monkeypatch.setattr(xero_service, "build_invoice_context", AsyncMock(return_value={}))
    monkeypatch.setattr(invoice_generator, "_get_xero_rate_lookup_credentials", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(invoice_generator, "_get_xero_line_item_template", AsyncMock(return_value=""))
    monkeypatch.setattr(invoice_generator, "_get_env_xero_billable_statuses", lambda: {"resolved"})
    monkeypatch.setattr(tickets_repo, "list_tickets", AsyncMock(return_value=[
        {"id": 1, "status": "resolved", "subject": "Normal"}, {"id": 2, "status": "resolved", "subject": "Fixed fee task"}]))
    monkeypatch.setattr(repo, "fixed_fee_ticket_ids", AsyncMock(return_value={2}))
    unbilled = AsyncMock(side_effect=lambda ticket_id: {10 * ticket_id})
    monkeypatch.setattr(invoice_generator.billed_time_repo, "get_unbilled_reply_ids", unbilled)
    monkeypatch.setattr(invoice_generator.expenses_repo, "list_expenses", AsyncMock(return_value=[]))
    seen_replies = []

    async def list_replies(ticket_id, include_internal=True):
        seen_replies.append(ticket_id)
        return [{"id": 10 * ticket_id, "minutes_spent": 60, "is_billable": True}]

    monkeypatch.setattr(tickets_repo, "list_replies", list_replies)
    # Stop at invoice creation: we only need to know which tickets contributed time.
    monkeypatch.setattr(invoice_generator, "_generate_invoice_number", AsyncMock(return_value="INV-1"))
    monkeypatch.setattr(xero_service, "resolve_invoice_due_days", lambda company: 7)
    monkeypatch.setattr(invoice_generator.invoice_repo, "create_invoice", AsyncMock(side_effect=RuntimeError("stop")))
    result = await invoice_generator.generate_invoice(5, include_recurring_items=False, recurring_line_items=[])
    assert seen_replies == [1]
    assert result["status"] == "error"


def test_xero_ticket_sync_excludes_fixed_fee_tickets():
    source = Path("app/services/xero.py").read_text(encoding="utf-8")
    assert "projects_service.fixed_fee_ticket_ids" in source
    generator = Path("app/services/invoice_generator.py").read_text(encoding="utf-8")
    assert "projects_service.fixed_fee_ticket_ids" in generator


@pytest.mark.asyncio
async def test_delete_and_invoice_require_super_admin(monkeypatch):
    from fastapi.responses import RedirectResponse

    from app.features.projects import routes

    redirect = RedirectResponse("/login")
    monkeypatch.setattr(routes, "_main", lambda: SimpleNamespace(_require_super_admin_page=AsyncMock(return_value=(None, redirect))))
    delete = AsyncMock()
    monkeypatch.setattr(repo, "delete_project", delete)
    assert await routes.delete_project(1, SimpleNamespace()) is redirect
    assert await routes.invoice_milestone(1, 2, SimpleNamespace()) is redirect
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_from_another_project_is_not_found(monkeypatch):
    from fastapi import HTTPException

    from app.features.projects import routes

    monkeypatch.setattr(routes, "_helpdesk", AsyncMock(return_value=({"id": 1}, None)))
    monkeypatch.setattr(repo, "get_task", AsyncMock(return_value={"project_id": 2}))
    with pytest.raises(HTTPException) as excinfo:
        await routes.change_task(1, 9, SimpleNamespace())
    assert excinfo.value.status_code == 404


def test_templates_compile_and_post_with_csrf():
    import app.main as main_module

    for name in ("projects/list.html", "projects/_project_editor.html", "projects/detail.html"):
        main_module.templates.env.get_template(name)
    detail = Path("app/templates/projects/detail.html").read_text(encoding="utf-8")
    assert detail.count('method="post"') == detail.count('{% include "partials/csrf.html" %}')
    assert "<dialog" not in detail and "onclick=" not in detail


@pytest.mark.asyncio
async def test_repository_queries_bind_every_value(monkeypatch):
    calls = []

    def record(result):
        async def capture(sql, params=None):
            calls.append((sql, tuple(params or ())))
            return result
        return capture

    monkeypatch.setattr(db, "fetch_all", record([]))
    monkeypatch.setattr(db, "fetch_one", record(None))
    monkeypatch.setattr(db, "execute", record(None))
    monkeypatch.setattr(db, "execute_rowcount", record(1))
    monkeypatch.setattr(db, "execute_returning_lastrowid", record(1))
    values = {"company_id": 5, "name": "x", "description": None, "status": "active", "billing_type": "fixed_fee",
              "budget_hours": None, "budget_amount": 1, "start_date": None, "due_date": None, "manager_user_id": None}
    await repo.list_projects(company_id=5)
    await repo.create_project(values, user_id=1)
    await repo.update_project(1, values)
    await repo.create_phase(1, "x", None)
    await repo.list_tasks(1)
    await repo.add_task(1, 9, phase_id=None, estimate_hours=None)
    await repo.fixed_fee_ticket_ids([1, 2, 3])
    await repo.project_totals([1, 2])
    await repo.list_time_entries(1)
    await repo.create_milestone(1, {"name": "x", "amount": 1})
    await repo.update_milestone(1, {"name": "x", "amount": 1})
    await repo.claim_milestone_for_invoice(1)
    await repo.mark_milestone_invoiced(1, invoice_id=1, invoice_number="I", when=None)
    for sql, params in calls:
        assert sql.count("%s") == len(params), sql
