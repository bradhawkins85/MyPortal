from datetime import date
import asyncio
from unittest.mock import AsyncMock

from app.repositories import expirations
from app.services import automations


def test_asset_expirations_are_live_queries_and_company_scoped(monkeypatch):
    fetch = AsyncMock(side_effect=[[
        {"source_id": 4, "source_type": "warranty", "source_field": "warranty_end_date",
         "title": "Laptop", "due_at": date(2027, 1, 2), "company_id": 9,
         "company_name": "Example"}
    ], []])
    monkeypatch.setattr(expirations.db, "fetch_all", fetch)

    rows = asyncio.run(expirations.list_asset_dates(9))

    assert rows[0]["due_at"] == date(2027, 1, 2)
    assert fetch.await_count == 2
    assert all(call.args[1] == (9,) for call in fetch.await_args_list)
    assert "expiration" not in fetch.await_args_list[0].args[0].lower().split("from")[1]


def test_expiration_reminders_are_available_to_existing_automation_builder():
    assert {option["value"] for option in automations.list_trigger_events()} >= {
        "expirations.reminder"
    }


def test_expiration_view_has_sort_filter_owner_and_retry_controls():
    template = open("app/templates/assets/expirations.html", encoding="utf-8").read()
    assert 'table_toolbar("expiration-table"' in template
    assert 'action="/expirations/settings"' in template
    assert 'name="owner_user_id"' in template
    assert 'action="/expirations/remind"' in template
