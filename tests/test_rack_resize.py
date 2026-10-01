"""Rack resizing: grow freely, shrink only when the removed units are empty."""
from pathlib import Path
from unittest.mock import AsyncMock
import asyncio

import pytest

from app.repositories import infrastructure


ROOT = Path(__file__).resolve().parents[1]


def _mock_db(monkeypatch, rack, equipment=(), reservations=()):
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(return_value=rack))
    fetch_all = AsyncMock(side_effect=[list(equipment), list(reservations)])
    monkeypatch.setattr(infrastructure.db, "fetch_all", fetch_all)
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute", execute)
    return fetch_all, execute


def test_growing_a_rack_skips_the_occupancy_check(monkeypatch):
    fetch_all, execute = _mock_db(monkeypatch, {"unit_count": 42, "depth_mm": 800})

    before = asyncio.run(infrastructure.resize_rack(1, 5, 47, 1200))

    assert before == {"unit_count": 42, "depth_mm": 800}
    fetch_all.assert_not_awaited()
    execute.assert_awaited_once()
    assert execute.await_args.args[1] == (47, 1200, 5, 1)


def test_shrinking_an_empty_top_section_is_allowed(monkeypatch):
    fetch_all, execute = _mock_db(monkeypatch, {"unit_count": 47, "depth_mm": 1000})

    asyncio.run(infrastructure.resize_rack(1, 5, 42, 1000))

    assert fetch_all.await_count == 2
    assert all(call.args[1] == (5, 1, 42) for call in fetch_all.await_args_list)
    execute.assert_awaited_once()


def test_shrinking_over_items_or_reservations_is_refused(monkeypatch):
    _fetch_all, execute = _mock_db(
        monkeypatch, {"unit_count": 47, "depth_mm": 1000},
        equipment=[{"label": "Core switch", "start_unit": 44, "end_unit": 44}],
        reservations=[{"label": None, "start_unit": 40, "end_unit": 45}])

    with pytest.raises(infrastructure.RackResizeBlocked) as exc:
        asyncio.run(infrastructure.resize_rack(1, 5, 42, 1000))

    assert "above U42" in str(exc.value)
    assert "Core switch (U44–44)" in str(exc.value)
    assert "Reserved space (U40–45)" in str(exc.value)
    execute.assert_not_awaited()


@pytest.mark.parametrize("units,depth", [(0, 1000), (101, 1000), (42, 99), (42, 5001)])
def test_resize_rejects_out_of_range_dimensions(units, depth):
    with pytest.raises(ValueError):
        asyncio.run(infrastructure.resize_rack(1, 5, units, depth))


def test_resize_is_company_scoped(monkeypatch):
    _mock_db(monkeypatch, None)

    with pytest.raises(ValueError, match="Rack not found"):
        asyncio.run(infrastructure.resize_rack(1, 5, 42, 1000))


def test_rack_template_offers_resize_with_minimum_height():
    template = (ROOT / "app/templates/infrastructure/racks.html").read_text()
    assert 'action="/api/infrastructure/racks/{{ rack.id }}/resize"' in template
    assert 'min="{{ min_units }}"' in template
    assert "data-rack-resize-open" in (ROOT / "app/static/js/racks.js").read_text()
