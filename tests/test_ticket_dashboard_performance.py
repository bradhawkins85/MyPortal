"""Regressions for database work that previously grew with every ticket."""

import sqlite3
from unittest.mock import AsyncMock

import pytest

from app.repositories import companies, staff, tickets, users
from app.services import tickets as service


class DashboardDB:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.queries = []

    async def fetch_all(self, sql, params=()):
        self.queries.append((sql, params))
        # Other automation aggregates are covered by the repository tests.
        if "FROM ticket_" in sql and "FROM ticket_expenses" not in sql:
            return []
        return [dict(row) for row in self.connection.execute(sql.replace("%s", "?"), params)]


@pytest.fixture
def dashboard_db(monkeypatch):
    database = DashboardDB()
    for repository in (companies, staff, tickets, users):
        monkeypatch.setattr(repository, "db", database)
    yield database
    database.connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ticket_count", [1, 200, 500])
async def test_expenses_are_aggregated_once_for_entire_list(dashboard_db, ticket_count):
    dashboard_db.connection.executescript("""
        CREATE TABLE ticket_expenses (ticket_id INTEGER, amount NUMERIC, billed_at TEXT);
        INSERT INTO ticket_expenses VALUES (1, 12.50, NULL), (1, 7.25, NULL),
            (1, 100, '2026-01-01'), (999, 50, NULL);
    """)
    context = await tickets.get_automation_filter_context_by_ticket_ids(
        list(range(1, ticket_count + 1)) + [1, 0, -1]
    )
    assert context[1]["expense_total"] == 19.75
    assert context[1]["expense_count"] == 2
    assert context[1]["has_expenses"] is True
    if ticket_count > 1:
        assert context[2]["expense_total"] == 0
        assert context[2]["expense_count"] == 0
        assert context[2]["has_expenses"] is False
    assert 999 not in context
    assert len(dashboard_db.queries) == 8
    assert sum("FROM ticket_expenses" in sql for sql, _ in dashboard_db.queries) == 1


@pytest.mark.asyncio
async def test_dashboard_labels_use_three_bulk_queries(dashboard_db, monkeypatch):
    dashboard_db.connection.executescript("""
        CREATE TABLE users (id INTEGER, first_name TEXT, last_name TEXT, email TEXT);
        INSERT INTO users VALUES (1, 'Alex', 'Agent', 'alex@example.com');
        CREATE TABLE staff (id INTEGER, first_name TEXT, last_name TEXT, email TEXT);
        INSERT INTO staff VALUES (1, 'Riley', 'Requester', 'riley@example.com');
        CREATE TABLE companies (id INTEGER, name TEXT, archived INTEGER);
        INSERT INTO companies VALUES (1, 'Archived company', 1);
    """)
    records = [
        {"id": number, "company_id": number, "requester_id": number,
         "requester_staff_id": number, "assigned_user_id": 1, "status": "open"}
        for number in range(1, 201)
    ]
    monkeypatch.setattr(tickets, "list_tickets", AsyncMock(return_value=records))
    monkeypatch.setattr(tickets, "count_tickets", AsyncMock(return_value=200))
    monkeypatch.setattr(service, "list_status_definitions", AsyncMock(return_value=[]))
    state = await service.load_dashboard_state(include_reference_data=False)
    assert len(dashboard_db.queries) == 3
    assert state.company_lookup[1]["name"] == "Archived company"
    assert state.user_lookup[1]["email"] == "alex@example.com"
    assert state.tickets[0]["requester_label"] == "Riley Requester"
    assert state.tickets[0]["requester_email"] == "riley@example.com"
    assert 2 not in state.user_lookup
    assert "requester_label" not in state.tickets[1]


@pytest.mark.asyncio
async def test_empty_dashboard_does_not_query_labels(dashboard_db, monkeypatch):
    monkeypatch.setattr(tickets, "list_tickets", AsyncMock(return_value=[]))
    monkeypatch.setattr(tickets, "count_tickets", AsyncMock(return_value=0))
    monkeypatch.setattr(service, "list_status_definitions", AsyncMock(return_value=[]))
    state = await service.load_dashboard_state(include_reference_data=False)
    assert state.user_lookup == state.company_lookup == {}
    assert dashboard_db.queries == []
    assert await tickets.get_automation_filter_context_by_ticket_ids([]) == {}
    assert dashboard_db.queries == []
