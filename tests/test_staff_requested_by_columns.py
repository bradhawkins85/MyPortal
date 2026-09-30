from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest

from app.repositories import staff as staff_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def captured(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    async def capture(sql, params=None):
        calls.append((sql, tuple(params or ())))
        return 1

    monkeypatch.setattr(staff_repo.db, "execute", capture)
    monkeypatch.setattr(staff_repo.db, "execute_returning_lastrowid", capture)
    monkeypatch.setattr(
        staff_repo.db,
        "fetch_one",
        AsyncMock(return_value={"id": 1, "company_id": 1, "enabled": 1, "is_ex_staff": 0}),
    )
    monkeypatch.setattr(staff_repo, "get_staff_by_id", AsyncMock(return_value={"id": 1}))
    return calls


def _update_kwargs(**overrides):
    kwargs = dict(
        company_id=1,
        first_name="Jane",
        last_name="Starter",
        email=None,
        mobile_phone=None,
        date_onboarded=None,
        date_offboarded=None,
        enabled=True,
        street=None,
        city=None,
        state=None,
        postcode=None,
        country=None,
        department=None,
        job_title=None,
        org_company=None,
        manager_name=None,
        account_action=None,
        syncro_contact_id=None,
        onboarding_status="provisioning",
    )
    kwargs.update(overrides)
    return kwargs


def _insert_columns(sql: str) -> int:
    match = re.search(r"INSERT INTO staff \((.*?)\) VALUES", sql, re.S)
    assert match
    return len([col for col in match.group(1).split(",") if col.strip()])


@pytest.mark.anyio
async def test_workflow_status_update_does_not_touch_requester_columns(captured):
    await staff_repo.update_staff(1, **_update_kwargs())

    sql, params = captured[0]
    assert "requested_by_name" not in sql
    assert "requested_by_email" not in sql
    assert sql.count("%s") == len(params)


@pytest.mark.anyio
async def test_update_writes_requester_snapshot_when_supplied(captured):
    await staff_repo.update_staff(
        1,
        **_update_kwargs(
            requested_by_name="Sam Manager", requested_by_email="sam@example.com"
        ),
    )

    sql, params = captured[0]
    assert "requested_by_name = %s" in sql
    assert "requested_by_email = %s" in sql
    assert sql.count("%s") == len(params)
    assert "Sam Manager" in params and "sam@example.com" in params


@pytest.mark.anyio
async def test_create_only_includes_requester_columns_when_supplied(captured):
    await staff_repo.create_staff(company_id=1, first_name="a", last_name="b", email="e")
    await staff_repo.create_staff(
        company_id=1,
        first_name="a",
        last_name="b",
        email="e",
        requested_by_name="Sam Manager",
        requested_by_email="sam@example.com",
    )

    plain_sql, plain_params = captured[0]
    assert "requested_by_name" not in plain_sql
    assert plain_sql.count("%s") == len(plain_params) == _insert_columns(plain_sql)

    snapshot_sql, snapshot_params = captured[1]
    assert "requested_by_name" in snapshot_sql
    assert snapshot_sql.count("%s") == len(snapshot_params) == _insert_columns(snapshot_sql)
    assert snapshot_params[-2:] == ("Sam Manager", "sam@example.com")
