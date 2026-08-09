from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repositories import company_recurring_invoice_items, scheduled_tasks


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_scheduled_task_counts_are_grouped_by_company(monkeypatch):
    fetch_all = AsyncMock(
        return_value=[
            {"company_id": 2, "task_count": 3},
            {"company_id": 7, "task_count": 1},
        ]
    )
    monkeypatch.setattr(scheduled_tasks.db, "fetch_all", fetch_all)

    result = await scheduled_tasks.count_tasks_by_company_ids([7, 2, 7])

    assert result == {2: 3, 7: 1}
    query, params = fetch_all.await_args.args
    assert "GROUP BY company_id" in query
    assert params == (2, 7)


@pytest.mark.anyio
async def test_recurring_item_counts_are_grouped_by_company(monkeypatch):
    fetch_all = AsyncMock(
        return_value=[{"company_id": 2, "item_count": 4}]
    )
    monkeypatch.setattr(company_recurring_invoice_items.db, "fetch_all", fetch_all)

    result = await company_recurring_invoice_items.count_items_by_company_ids([9, 2])

    assert result == {2: 4}
    query, params = fetch_all.await_args.args
    assert "company_recurring_invoice_items" in query
    assert "GROUP BY company_id" in query
    assert params == (2, 9)


def test_company_table_exposes_optional_counts_and_edit_links():
    template = open("app/templates/admin/companies.html", encoding="utf-8").read()

    assert '"key": "automations"' in template
    assert '"key": "recurring_invoice_items"' in template
    assert template.count('"default_visible": false') >= 2
    assert 'data-column-key="automations"' in template
    assert 'data-column-key="recurring_invoice_items"' in template
    assert (
        '<a href="/admin/companies/{{ company.id }}/edit">{{ company.name }}</a>'
        in template
    )
