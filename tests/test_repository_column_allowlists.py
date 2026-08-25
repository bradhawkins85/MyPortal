from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.repositories import (
    companies,
    company_memberships,
    invoices,
    knowledge_base,
    port_pricing,
    ports,
    roles,
    service_status,
)


MALICIOUS_UPDATE_KEY = "name = %s WHERE 1=1 --"
MALICIOUS_INSERT_KEY = "name); DROP TABLE ports; --"


@pytest.mark.parametrize(
    ("repository", "call"),
    [
        (companies, lambda: companies.create_company(**{MALICIOUS_INSERT_KEY: "x"})),
        (companies, lambda: companies.update_company(1, **{MALICIOUS_UPDATE_KEY: "x"})),
        (invoices, lambda: invoices.patch_invoice(1, **{MALICIOUS_UPDATE_KEY: "x"})),
        (knowledge_base, lambda: knowledge_base.update_article(1, **{MALICIOUS_UPDATE_KEY: "x"})),
        (roles, lambda: roles.update_role(1, **{MALICIOUS_UPDATE_KEY: "x"})),
        (company_memberships, lambda: company_memberships.update_membership(1, **{MALICIOUS_UPDATE_KEY: "x"})),
        (ports, lambda: ports.update_port(1, **{MALICIOUS_INSERT_KEY: "x"})),
        (port_pricing, lambda: port_pricing.update_pricing_version(1, **{MALICIOUS_UPDATE_KEY: "x"})),
        (service_status, lambda: service_status.create_service({MALICIOUS_INSERT_KEY: "x"})),
        (service_status, lambda: service_status.update_service(1, {MALICIOUS_UPDATE_KEY: "x"})),
    ],
)
def test_dynamic_sql_rejects_unknown_columns_before_database_call(
    monkeypatch, repository, call
):
    database_methods = [
        AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
    ]
    for name, mock in zip(
        ("execute", "execute_returning_lastrowid", "fetch_one", "fetch_all"),
        database_methods,
    ):
        monkeypatch.setattr(repository.db, name, mock)

    with pytest.raises(ValueError, match="Unsupported"):
        asyncio.run(call())

    for database_method in database_methods:
        database_method.assert_not_called()


@pytest.mark.parametrize(
    ("repository", "call", "value"),
    [
        (invoices, lambda value: invoices.patch_invoice(7, status=value), "paid-secret"),
        (knowledge_base, lambda value: knowledge_base.update_article(7, title=value), "article-secret"),
        (roles, lambda value: roles.update_role(7, name=value), "role-secret"),
        (company_memberships, lambda value: company_memberships.update_membership(7, role_id=value), 912345),
        (ports, lambda value: ports.update_port(7, name=value), "port-secret"),
        (port_pricing, lambda value: port_pricing.update_pricing_version(7, notes=value), "pricing-secret"),
    ],
)
def test_valid_updates_bind_values_instead_of_interpolating_them(
    monkeypatch, repository, call, value
):
    execute = AsyncMock()
    monkeypatch.setattr(repository.db, "execute", execute)
    monkeypatch.setattr(repository.db, "fetch_one", AsyncMock(return_value={"id": 7}))
    monkeypatch.setattr(repository.db, "fetch_all", AsyncMock(return_value=[]))
    if repository is company_memberships:
        monkeypatch.setattr(
            company_memberships, "get_membership_by_id", AsyncMock(return_value={"id": 7})
        )

    asyncio.run(call(value))

    sql, params = execute.await_args.args
    assert str(value) not in sql
    assert value in params
    assert "%s" in sql


def test_valid_company_and_service_inserts_bind_values(monkeypatch):
    company_value = "company-secret"
    company_insert = AsyncMock(return_value=7)
    monkeypatch.setattr(companies.db, "execute_returning_lastrowid", company_insert)
    monkeypatch.setattr(companies.db, "fetch_one", AsyncMock(return_value={"id": 7, "name": company_value}))
    asyncio.run(companies.create_company(name=company_value))
    company_sql, company_params = company_insert.await_args.args
    assert company_value not in company_sql
    assert company_value in company_params

    service_value = "service-secret"
    service_insert = AsyncMock(return_value=8)
    monkeypatch.setattr(service_status.db, "execute_returning_lastrowid", service_insert)
    monkeypatch.setattr(service_status.db, "execute", AsyncMock())
    monkeypatch.setattr(service_status.db, "fetch_one", AsyncMock(return_value={"id": 8, "name": service_value}))
    monkeypatch.setattr(service_status.db, "fetch_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(service_status, "replace_service_companies", AsyncMock())
    asyncio.run(service_status.create_service({"name": service_value}))
    service_sql, service_params = service_insert.await_args.args
    assert service_value not in service_sql
    assert service_value in service_params
