"""Rack lane/depth validation and migration contracts."""
from pathlib import Path
from unittest.mock import AsyncMock
import asyncio

import pytest

from app.repositories import infrastructure


ROOT = Path(__file__).resolve().parents[1]


def test_migration_preserves_old_placements_as_full_width_half_depth():
    sql = (ROOT / "migrations/420_graphical_rack_placement.sql").read_text()
    assert "width_lanes INT NOT NULL DEFAULT 3" in sql
    assert "depth_mode VARCHAR(16) NOT NULL DEFAULT 'half'" in sql
    assert "rack_equipment_slots" in sql
    assert "SELECT 1 lane UNION ALL SELECT 2 UNION ALL SELECT 3" in sql


def test_place_asset_reserves_each_lane_on_both_faces(monkeypatch):
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(side_effect=[
        {"unit_count": 42}, {"id": 9}, None, None,
    ]))
    insert = AsyncMock(return_value=55)
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute_returning_lastrowid", insert)
    monkeypatch.setattr(infrastructure.db, "execute", execute)

    result = asyncio.run(infrastructure.place_asset(1, 2, 9, 10, 2, "front", None, 2, 2, "full"))

    assert result == 55
    assert execute.await_count == 8
    inserted_faces = {call.args[1][3] for call in execute.await_args_list}
    inserted_lanes = {call.args[1][4] for call in execute.await_args_list}
    assert inserted_faces == {"front", "rear"}
    assert inserted_lanes == {2, 3}


@pytest.mark.parametrize("width,lane", [(2, 3), (3, 2), (4, 1)])
def test_place_asset_rejects_width_outside_three_lanes(width, lane):
    with pytest.raises(ValueError, match="Invalid rack position"):
        asyncio.run(infrastructure.place_asset(1, 2, 9, 10, 1, "front", None, width, lane, "half"))


def test_reservation_rejects_existing_equipment(monkeypatch):
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(side_effect=[
        {"unit_count": 42}, {"unit_number": 10},
    ]))
    insert = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute_returning_lastrowid", insert)

    with pytest.raises(ValueError, match="occupied or reserved"):
        asyncio.run(infrastructure.reserve_space(
            1, 2, 10, 3, "front", 1, 1, "half", "Network change", "Alex", None
        ))
    insert.assert_not_awaited()


def test_reservation_uses_lanes_and_both_faces(monkeypatch):
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(side_effect=[
        {"unit_count": 42}, None, None,
    ]))
    monkeypatch.setattr(infrastructure.db, "execute_returning_lastrowid", AsyncMock(return_value=71))
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute", execute)

    result = asyncio.run(infrastructure.reserve_space(
        1, 2, 10, 2, "front", 2, 2, "full", "Reserved", None, None
    ))

    assert result == 71
    assert execute.await_count == 8
    assert {call.args[1][3] for call in execute.await_args_list} == {"front", "rear"}


def test_rack_details_migration_has_deployment_metadata():
    import json

    sql = (ROOT / "migrations/433_rack_details_and_reservations.sql").read_text()
    metadata = json.loads((ROOT / "migrations/deployment_metadata.json").read_text())
    assert "power_capacity_watts INT NULL" in sql
    assert "rack_reservation_slots" in sql
    assert "433_rack_details_and_reservations.sql" in metadata["migrations"]


def test_rack_template_explains_capacity_and_unknown_power():
    template = (ROOT / "app/templates/infrastructure/racks.html").read_text()
    assert "Occupied lane-units across both faces" in template
    assert "Capacity and per-placement draw must both be recorded" in template
    assert "Accessible list and actions" in template
