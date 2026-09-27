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

    result = asyncio.run(infrastructure.place_asset(1, 2, 9, 10, 2, "front", None, 2, 2, "full", name="Server"))

    assert result == 55
    assert execute.await_count == 8
    inserted_faces = {call.args[1][3] for call in execute.await_args_list}
    inserted_lanes = {call.args[1][4] for call in execute.await_args_list}
    assert inserted_faces == {"front", "rear"}
    assert inserted_lanes == {2, 3}


@pytest.mark.parametrize("width,lane", [(2, 3), (3, 2), (4, 1)])
def test_place_asset_rejects_width_outside_three_lanes(width, lane):
    with pytest.raises(ValueError, match="Invalid rack item or position"):
        asyncio.run(infrastructure.place_asset(1, 2, 9, 10, 1, "front", None, width, lane, "half", name="Device"))


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


def test_rack_template_uses_stat_strips_and_hides_unconfigured_values():
    template = (ROOT / "app/templates/infrastructure/racks.html").read_text()
    assert 'class="rack-facts"' in template
    assert 'class="rack-gauges"' in template
    assert "{% if rack.width_mm %}" in template
    assert "Not configured" not in template
    assert "Item list and port links" in template


def test_standalone_patch_panel_creates_numbered_ports(monkeypatch):
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(side_effect=[{"unit_count": 42}, None, None]))
    monkeypatch.setattr(infrastructure.db, "execute_returning_lastrowid", AsyncMock(return_value=81))
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute", execute)

    result = asyncio.run(infrastructure.place_asset(
        1, 2, None, 10, 1, "front", None, item_type="patch_panel",
        name="PP-A", port_count=4
    ))

    assert result == 81
    port_calls = [call for call in execute.await_args_list if "rack_equipment_ports" in call.args[0]]
    assert [call.args[1][2] for call in port_calls] == [1, 2, 3, 4]


@pytest.mark.parametrize("item_type", ["patch_panel", "switch"])
def test_port_capable_rack_item_can_be_placed_without_port_documentation(monkeypatch, item_type):
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(side_effect=[
        {"unit_count": 42}, None, None,
    ]))
    monkeypatch.setattr(
        infrastructure.db, "execute_returning_lastrowid", AsyncMock(return_value=82)
    )
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute", execute)

    result = asyncio.run(infrastructure.place_asset(
        1, 2, None, 10, 1, "front", None, item_type=item_type,
        name="Unmapped ports", port_count=0
    ))

    assert result == 82
    assert not any(
        "rack_equipment_ports" in call.args[0] for call in execute.await_args_list
    )


def test_link_port_rejects_cross_company_asset(monkeypatch):
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(side_effect=[{"id": 7}, None]))
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute", execute)
    with pytest.raises(ValueError, match="does not belong"):
        asyncio.run(infrastructure.link_equipment_port(1, 8, 2, 99))
    execute.assert_not_awaited()


def test_rack_item_migration_and_template_contracts():
    sql = (ROOT / "migrations/434_rack_item_types_and_ports.sql").read_text()
    template = (ROOT / "app/templates/infrastructure/racks.html").read_text()
    assert "asset_id INT NULL" in sql
    assert "rack_equipment_ports" in sql
    assert 'value="patch_panel"' in template and 'value="switch"' in template
    assert "Number of ports (optional)" in template
    assert "Not Configured" not in template and "Not configured" not in template


def test_edit_rack_item_checks_company_asset_before_updating(monkeypatch):
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(side_effect=[
        {"id": 8, "port_count": 0}, None,
    ]))
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute", execute)

    with pytest.raises(ValueError, match="does not belong"):
        asyncio.run(infrastructure.update_rack_equipment(
            1, 8, "Router", "device", 99, 120, None))
    execute.assert_not_awaited()


def test_edit_rack_item_preserves_placement_and_ports(monkeypatch):
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(side_effect=[
        {"id": 8, "port_count": 24}, {"id": 9},
    ]))
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute", execute)

    asyncio.run(infrastructure.update_rack_equipment(
        1, 8, "Core switch", "switch", 9, 150, "Uplink"))

    sql, params = execute.await_args.args
    assert "WHERE id=%s AND company_id=%s" in sql
    assert "start_unit" not in sql and "rack_equipment_ports" not in sql
    assert params == ("Core switch", "switch", 9, 150, "Uplink", 8, 1)


def test_edit_reservation_is_company_scoped(monkeypatch):
    monkeypatch.setattr(infrastructure.db, "fetch_one", AsyncMock(return_value={"id": 4}))
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute", execute)

    asyncio.run(infrastructure.update_rack_reservation(1, 4, "Maintenance", "Alex", None))

    sql, params = execute.await_args.args
    assert "WHERE id=%s AND company_id=%s" in sql
    assert params == ("Maintenance", "Alex", None, 4, 1)
