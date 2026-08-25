"""Regression tests for SQL built at repository boundaries.

These tests deliberately inspect calls to the application's database adapter.  They
therefore protect the code which assembles dynamic identifiers and placeholder
lists, rather than merely demonstrating that a third-party SQL API can bind data.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.database import db
from app.repositories import (
    companies,
    company_memberships,
    email_blocklist,
    invoices,
    knowledge_base,
    port_pricing,
    ports,
    roles,
    service_status,
    users,
)


INJECTION = "x'); DROP TABLE users; --"
BAD_IDENTIFIER = "name = %s WHERE 1=1 --"
DB_METHODS = ("execute", "execute_returning_lastrowid", "fetch_one", "fetch_all")


def _mock_database(monkeypatch, **returns):
    mocks = {}
    for method in DB_METHODS:
        mock = AsyncMock(return_value=returns.get(method))
        monkeypatch.setattr(db, method, mock)
        mocks[method] = mock
    return mocks


@pytest.mark.parametrize(
    "call",
    [
        lambda: users.update_user(7, **{BAD_IDENTIFIER: INJECTION}),
        lambda: companies.create_company(**{BAD_IDENTIFIER: INJECTION}),
        lambda: companies.update_company(7, **{BAD_IDENTIFIER: INJECTION}),
        lambda: invoices.patch_invoice(7, **{BAD_IDENTIFIER: INJECTION}),
        lambda: knowledge_base.update_article(7, **{BAD_IDENTIFIER: INJECTION}),
        lambda: roles.update_role(7, **{BAD_IDENTIFIER: INJECTION}),
        lambda: company_memberships.update_membership(7, **{BAD_IDENTIFIER: INJECTION}),
        lambda: ports.update_port(7, **{BAD_IDENTIFIER: INJECTION}),
        lambda: port_pricing.update_pricing_version(7, **{BAD_IDENTIFIER: INJECTION}),
        lambda: service_status.create_service({BAD_IDENTIFIER: INJECTION}),
        lambda: service_status.update_service(7, {BAD_IDENTIFIER: INJECTION}),
    ],
)
def test_dynamic_columns_are_rejected_before_database_execution(monkeypatch, call):
    mocks = _mock_database(monkeypatch)

    with pytest.raises(ValueError, match="Unsupported"):
        asyncio.run(call())

    for mock in mocks.values():
        mock.assert_not_awaited()


def test_users_allowlisted_update_helper_separates_identifiers_and_values():
    clause, params = users._build_safe_update_clause(
        {"email": INJECTION, "first_name": "Robert'); --"}
    )

    assert clause == "email = %s, first_name = %s"
    assert params == [INJECTION, "Robert'); --"]
    assert all(value not in clause for value in params)

    with pytest.raises(ValueError, match="Unsupported update fields"):
        users._build_safe_update_clause({BAD_IDENTIFIER: INJECTION})


@pytest.mark.parametrize(
    ("call", "expected_method"),
    [
        (lambda: users.update_user(7, email=INJECTION), "execute"),
        (lambda: companies.update_company(7, name=INJECTION), "execute"),
        (lambda: invoices.patch_invoice(7, status=INJECTION), "execute"),
        (lambda: knowledge_base.update_article(7, title=INJECTION), "execute"),
        (lambda: roles.update_role(7, name=INJECTION), "execute"),
        (lambda: company_memberships.update_membership(7, role_id=INJECTION), "execute"),
        (lambda: ports.update_port(7, name=INJECTION), "execute"),
        (lambda: port_pricing.update_pricing_version(7, notes=INJECTION), "execute"),
        (lambda: service_status.update_service(7, {"name": INJECTION}), "execute"),
        (lambda: companies.create_company(name=INJECTION), "execute_returning_lastrowid"),
        (lambda: service_status.create_service({"name": INJECTION}), "execute_returning_lastrowid"),
    ],
)
def test_repository_values_are_bound_outside_sql(monkeypatch, call, expected_method):
    mocks = _mock_database(
        monkeypatch,
        execute_returning_lastrowid=7,
        fetch_one={"id": 7, "name": INJECTION},
        fetch_all=[],
    )
    monkeypatch.setattr(service_status, "replace_service_companies", AsyncMock())
    monkeypatch.setattr(
        company_memberships,
        "get_membership_by_id",
        AsyncMock(return_value={"id": 7}),
    )

    asyncio.run(call())

    sql, params = mocks[expected_method].await_args_list[0].args
    assert INJECTION not in sql
    assert INJECTION in params
    assert "%s" in sql


def test_bulk_in_clause_uses_one_placeholder_and_binding_per_item(monkeypatch):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(db, "fetch_all", fetch_all)
    identifiers = [101, 202, 303]

    asyncio.run(ports.bulk_get_ports(identifiers))

    sql, params = fetch_all.await_args.args
    in_clause = sql.split(" IN (", 1)[1].split(")", 1)[0]
    assert in_clause == "%s,%s,%s"
    assert set(params) == set(identifiers)
    assert all(str(value) not in sql for value in params)


def test_named_in_clause_binds_every_value_separately(monkeypatch):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(db, "fetch_all", fetch_all)
    addresses = ["safe@example.com", "x' OR '1'='1@example.com"]

    asyncio.run(email_blocklist.filter_allowed(addresses))

    sql, params = fetch_all.await_args.args
    assert "IN (:email0, :email1)" in sql
    assert params == {"email0": addresses[0], "email1": addresses[1].lower()}
    assert all(value not in sql for value in params.values())


@pytest.mark.parametrize(
    ("call", "fragment"),
    [
        (lambda: ports.list_ports(order_by="country", direction="desc"), "ORDER BY country DESC"),
        (lambda: email_blocklist.list_entries(sort="updated_at", direction="asc"), "ORDER BY updated_at ASC, id ASC"),
    ],
)
def test_allowlisted_ordering_resolves_to_fixed_sql(monkeypatch, call, fragment):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(db, "fetch_all", fetch_all)

    asyncio.run(call())

    sql = fetch_all.await_args.args[0]
    assert fragment in sql


@pytest.mark.parametrize(
    "call",
    [
        lambda: ports.list_ports(order_by=BAD_IDENTIFIER),
        lambda: ports.list_ports(direction="DESC; DELETE FROM ports"),
        lambda: email_blocklist.list_entries(sort=BAD_IDENTIFIER),
        lambda: email_blocklist.list_entries(direction="ASC; DELETE FROM email_blocklist"),
    ],
)
def test_unsupported_ordering_is_rejected_before_database_execution(monkeypatch, call):
    mocks = _mock_database(monkeypatch)

    with pytest.raises(ValueError, match="Unsupported .*order|Unsupported sort"):
        asyncio.run(call())

    for mock in mocks.values():
        mock.assert_not_awaited()
