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
