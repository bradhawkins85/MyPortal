import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app.core.database import db
from app.features.companies import handlers
from app.repositories import company_job_title_access as rules
from app.repositories import company_memberships as memberships
from app.repositories import user_companies

MIGRATION = Path(__file__).resolve().parents[1] / 'migrations/557_company_job_title_access.sql'


@pytest.fixture
def access_db(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE companies (id INT PRIMARY KEY, name TEXT, syncro_company_id TEXT);
        CREATE TABLE users (id INT PRIMARY KEY, company_id INT, email TEXT, first_name TEXT, last_name TEXT);
        CREATE TABLE roles (id INT PRIMARY KEY, name TEXT, permissions TEXT);
        CREATE TABLE staff (id INT PRIMARY KEY, company_id INT, email TEXT, job_title TEXT, enabled INT, is_ex_staff INT);
        CREATE TABLE company_memberships (id INT PRIMARY KEY, company_id INT, user_id INT, role_id INT, status TEXT,
            invited_by INT, invited_at TEXT, joined_at TEXT, last_seen_at TEXT);
        CREATE TABLE user_companies (company_id INT, user_id INT, staff_permission INT);
        INSERT INTO companies VALUES (1, 'Acme', NULL), (2, 'Other', NULL);
        INSERT INTO users VALUES (10, 1, 'alice@example.test', 'Alice', 'A');
        INSERT INTO roles VALUES (1, 'Member', '["forms.access"]'), (2, 'Supervisor', '["staff.manage"]'), (3, 'Manager', '["assets.manage"]');
        INSERT INTO staff VALUES (20, 1, 'ALICE@example.test', ' Production Supervisor ', 1, 0);
        INSERT INTO company_memberships VALUES (30, 1, 10, 1, 'active', NULL, NULL, NULL, NULL);
        INSERT INTO user_companies VALUES (1, 10, 0);
    ''')
    conn.executescript(MIGRATION.read_text())
    conn.executescript(MIGRATION.read_text())  # Idempotent migration.

    async def fetch_all(sql, params=()):
        return [dict(row) for row in conn.execute(sql.replace('%s', '?'), params)]

    async def fetch_one(sql, params=()):
        rows = await fetch_all(sql, params)
        return rows[0] if rows else None

    async def execute(sql, params=()):
        conn.execute(sql.replace('%s', '?'), params)

    monkeypatch.setattr(db, 'fetch_all', fetch_all)
    monkeypatch.setattr(db, 'fetch_one', fetch_one)
    monkeypatch.setattr(db, 'execute', execute)
    monkeypatch.setattr(db, 'is_sqlite', lambda: True)
    yield conn
    conn.close()


@pytest.mark.asyncio
async def test_rule_follows_current_title_and_new_hires(access_db):
    await rules.save_rule(company_id=1, job_title='Production Supervisor', role_id=2, staff_permission=3)
    await rules.save_rule(company_id=1, job_title='Manager', role_id=3, staff_permission=1)
    assert (await memberships.get_membership_by_company_user(1, 10))['role_id'] == 2
    assert (await user_companies.get_user_company(10, 1))['staff_permission'] == 3
    assert (await user_companies.list_companies_for_user(10))[0]['staff_permission'] == 3

    access_db.execute("UPDATE staff SET job_title = 'Manager' WHERE id = 20")
    membership = await memberships.list_memberships_for_user(10)
    assert membership[0]['role_id'] == 3
    assert 'staff.manage' not in membership[0]['permissions']
    assert (await user_companies.get_user_company(10, 1))['staff_permission'] == 1

    access_db.execute("INSERT INTO users VALUES (11, 1, 'new@example.test', 'New', 'Hire')")
    access_db.execute("INSERT INTO staff VALUES (21, 1, 'new@example.test', 'Manager', 1, 0)")
    assert (await memberships.list_memberships_for_user(11))[0]['role_id'] == 3
    assert (await user_companies.get_user_company(11, 1))['staff_permission'] == 1
    assert (await user_companies.list_companies_for_user(11))[0]['membership_role_id'] == 3

    await rules.delete_rule(1, 'Manager')
    assert (await memberships.get_membership_by_company_user(1, 10))['role_id'] == 1
    assert await memberships.list_memberships_for_user(11) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ["enabled = 0", "is_ex_staff = 1", "job_title = 'Unmapped'", "company_id = 2"])
async def test_ineligible_staff_and_other_companies_do_not_inherit(access_db, change):
    await rules.save_rule(company_id=1, job_title='Production Supervisor', role_id=2, staff_permission=3)
    access_db.execute('UPDATE staff SET ' + change)  # Static test values only.
    assert (await memberships.get_membership_by_company_user(1, 10))['role_id'] == 1
    assert await memberships.get_membership_by_company_user(2, 10) is None


@pytest.mark.asyncio
async def test_suspension_blocks_inheritance_and_rules_can_be_updated(access_db):
    await rules.save_rule(company_id=1, job_title='Production Supervisor', role_id=2, staff_permission=3)
    await rules.save_rule(company_id=1, job_title='PRODUCTION SUPERVISOR', role_id=3, staff_permission=1)
    assert len(await rules.list_rules(1)) == 1
    assert (await memberships.get_membership_by_company_user(1, 10))['role_id'] == 3
    access_db.execute("UPDATE company_memberships SET status = 'suspended'")
    assert await memberships.list_memberships_for_user(10) == []
    assert (await memberships.get_membership_by_company_user(1, 10))['job_title'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('form, expected', [
    ({'jobTitle': 'Production Supervisor', 'role_id': '2'}, 303),
    ({'jobTitle': 'Production Supervisor', 'role_id': '999'}, 400),
    ({'jobTitle': 'Other company title', 'role_id': '2'}, 400),
    ({'jobTitle': 'Production Supervisor', 'role_id': '2', 'userId': '10'}, 400),
    ({'jobTitle': 'Production Supervisor', 'removeJobTitle': '1'}, 303),
])
async def test_assignment_validation_and_company_scope(access_db, monkeypatch, form, expected):
    from app import main
    from app.services import audit
    request = Request({'type': 'http', 'method': 'POST', 'path': '/admin/companies/assign', 'headers': []})
    monkeypatch.setattr(request, 'form', AsyncMock(return_value={'sourceCompanyId': '1', 'companyId': '2', **form}))
    monkeypatch.setattr(main, '_require_super_admin_page', AsyncMock(return_value=({'id': 1, 'is_super_admin': True}, None)))
    render = AsyncMock(side_effect=lambda *args, **kwargs: HTMLResponse('', status_code=kwargs['status_code']))
    monkeypatch.setattr(handlers, '_render_company_edit_page', render)
    monkeypatch.setattr(audit, 'log_action', AsyncMock())
    from app.repositories import staff, roles, companies
    monkeypatch.setattr(staff, 'list_staff', AsyncMock(return_value=[{'job_title': 'Production Supervisor'}]))
    monkeypatch.setattr(roles, 'get_role_by_id', AsyncMock(side_effect=lambda role_id: {'id': 2} if role_id == 2 else None))
    monkeypatch.setattr(companies, 'get_company_by_id', AsyncMock(return_value={'id': 1}))
    response = await handlers.admin_assign_user_to_company(request)
    assert response.status_code == expected
    assert await rules.list_rules(2) == []
    if expected == 400:
        assert await rules.list_rules(1) == []


@pytest.mark.asyncio
async def test_empty_role_revokes_legacy_access_and_duplicate_staff_is_deterministic(access_db):
    access_db.execute('ALTER TABLE user_companies ADD COLUMN can_manage_staff INT DEFAULT 1')
    access_db.execute("UPDATE roles SET permissions = '{}' WHERE id = 3")
    await rules.save_rule(company_id=1, job_title='Production Supervisor', role_id=2, staff_permission=3)
    await rules.save_rule(company_id=1, job_title='Manager', role_id=3, staff_permission=0)
    access_db.execute("INSERT INTO staff VALUES (22, 1, 'alice@example.test', 'Manager', 1, 0)")
    assert len(await memberships.list_memberships_for_user(10)) == 1
    company = await user_companies.get_user_company(10, 1)
    assert company['can_manage_staff'] is False
    assert company['staff_permission'] == 0


@pytest.mark.asyncio
async def test_role_permission_checks_follow_title_changes(access_db, monkeypatch):
    from app.repositories import users
    from app.services import role_switching
    monkeypatch.setattr(role_switching, 'effective_role_has_permission', lambda permission: None)
    monkeypatch.setattr(users, 'get_user_by_id', AsyncMock(return_value={'id': 10, 'is_super_admin': False}))
    from app.repositories import user_permissions
    monkeypatch.setattr(user_permissions, 'list_user_permissions', AsyncMock(return_value=[]))
    await rules.save_rule(company_id=1, job_title='Production Supervisor', role_id=2, staff_permission=3)
    await rules.save_rule(company_id=1, job_title='Manager', role_id=3, staff_permission=1)
    assert await memberships.user_has_permission(10, 'staff.manage')
    access_db.execute("UPDATE staff SET job_title = 'Manager'")
    assert not await memberships.user_has_permission(10, 'staff.manage')
    assert await memberships.user_has_role_permission(10, 'assets.manage')
