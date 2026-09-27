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
        {"unit_count": 42}, {"id": 9}, None,
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
