import asyncio
from datetime import timezone
import importlib.util
from pathlib import Path
import sys
import types

import pytest


def _load_unbill_tickets_module():
    app_module = sys.modules.setdefault("app", types.ModuleType("app"))
    repos_module = types.ModuleType("app.repositories")
    repos_module.ticket_billed_time_entries = types.SimpleNamespace()
    repos_module.tickets = types.SimpleNamespace()
    sys.modules["app.repositories"] = repos_module
    services_module = types.ModuleType("app.services")
    sys.modules["app.services"] = services_module
    app_module.repositories = repos_module
    app_module.services = services_module

    spec = importlib.util.spec_from_file_location(
        "unbill_tickets_under_test",
        Path(__file__).resolve().parents[1] / "app" / "services" / "unbill_tickets.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


unbill_tickets = _load_unbill_tickets_module()


def test_parse_unbill_cutoff_date_accepts_yyyymmdd_as_utc_start():
    parsed = unbill_tickets.parse_unbill_cutoff_date("20240131")

    assert parsed.year == 2024
    assert parsed.month == 1
    assert parsed.day == 31
    assert parsed.hour == 0
    assert parsed.minute == 0
    assert parsed.tzinfo == timezone.utc


@pytest.mark.parametrize("value", ["", "2024-01-31", "202401", "20240231", "abcdefgh"])
def test_parse_unbill_cutoff_date_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        unbill_tickets.parse_unbill_cutoff_date(value)


def test_preview_unbill_tickets_scans_all_ticket_pages(monkeypatch):
    calls = []

    async def fake_tickets(cutoff_date, limit, offset=0):
        calls.append((limit, offset))
        pages = {
            0: [
                {
                    "id": 1,
                    "ticket_number": "T-1",
                    "subject": "Old billed ticket",
                    "created_at": cutoff_date,
                    "billed_at": cutoff_date,
                    "xero_invoice_number": "INV-1",
                },
            ],
            1: [
                {
                    "id": 2,
                    "ticket_number": "T-2",
                    "subject": "Older billed ticket",
                    "created_at": cutoff_date,
                    "billed_at": cutoff_date,
                    "xero_invoice_number": "INV-2",
                },
            ],
        }
        return pages.get(offset, [])

    monkeypatch.setattr(
        unbill_tickets.tickets_repo,
        "list_billed_tickets_older_than",
        fake_tickets,
        raising=False,
    )

    preview = asyncio.run(
        unbill_tickets.preview_unbill_tickets(
            unbill_tickets.parse_unbill_cutoff_date("20260530"),
            limit=1,
        )
    )

    assert preview["status"] == "ready"
    assert preview["totals"] == {"ticketCount": 2}
    assert [item["id"] for item in preview["items"]] == [1, 2]
    assert calls == [(1, 0), (1, 1), (1, 2)]
