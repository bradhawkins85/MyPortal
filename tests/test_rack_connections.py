"""Rack item connections: per-connector ports, links, power sources and full edits."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.repositories import infrastructure


def _mock_db(monkeypatch, fetch_one=(), fetch_all=(), lastrowid=70):
    fetch_one_mock = AsyncMock(side_effect=list(fetch_one))
    fetch_all_mock = AsyncMock(side_effect=list(fetch_all))
    execute = AsyncMock()
    insert = AsyncMock(return_value=lastrowid)
    monkeypatch.setattr(infrastructure.db, "fetch_one", fetch_one_mock)
    monkeypatch.setattr(infrastructure.db, "fetch_all", fetch_all_mock)
    monkeypatch.setattr(infrastructure.db, "execute", execute)
    monkeypatch.setattr(infrastructure.db, "execute_returning_lastrowid", insert)
    return execute, insert


def _sql(execute, fragment):
    return [call.args for call in execute.await_args_list if fragment in call.args[0]]


def test_ups_is_placed_with_iec_and_three_pin_outlets(monkeypatch):
    execute, _insert = _mock_db(monkeypatch, fetch_one=[{"unit_count": 42}, None, None])

    asyncio.run(infrastructure.place_asset(
        1, 2, None, 10, 2, "front", None, item_type="ups",
        port_counts={"iec": 2, "3pin": 1}))

    ports = _sql(execute, "INSERT INTO rack_equipment_ports")
    assert [(params[2], params[3]) for _sql_text, params in ports] == [(1, "iec"), (2, "iec"), (3, "3pin")]


def test_switch_port_links_record_assets_and_labels(monkeypatch):
    execute, _insert = _mock_db(
        monkeypatch, fetch_one=[{"unit_count": 42}, None, None],
        fetch_all=[[{"id": 5}], [{"id": 101, "port_number": 1}, {"id": 103, "port_number": 3}]])

    asyncio.run(infrastructure.place_asset(
        1, 2, None, 10, 1, "front", None, item_type="switch", port_counts={"data": 3},
        port_links=[infrastructure.PortLink("data", 1, 5, None),
                    infrastructure.PortLink("data", 3, None, "  Reception printer ")]))

    links = [params for _sql_text, params in _sql(execute, "UPDATE rack_equipment_ports SET asset_id")]
    assert links == [(5, None, None, 70, 1, 1), (None, "Reception printer", None, 70, 3, 1)]


def test_port_link_to_another_companys_asset_is_rejected_and_item_removed(monkeypatch):
    execute, _insert = _mock_db(
        monkeypatch, fetch_one=[{"unit_count": 42}, None, None], fetch_all=[[]])

    with pytest.raises(ValueError, match="does not belong"):
        asyncio.run(infrastructure.place_asset(
            1, 2, None, 10, 1, "front", None, item_type="switch", port_counts={"data": 1},
            port_links=[infrastructure.PortLink("data", 1, 99, None)]))
    assert _sql(execute, "DELETE FROM rack_equipment WHERE id")


def test_server_psus_are_fed_from_outlets_on_other_units(monkeypatch):
    execute, _insert = _mock_db(
        monkeypatch, fetch_one=[{"unit_count": 42}, None, None],
        fetch_all=[[{"id": 41, "equipment_id": 12}, {"id": 42, "equipment_id": 13}]])

    asyncio.run(infrastructure.place_asset(
        1, 2, None, 10, 1, "front", None, item_type="server", port_counts={"data": 1, "psu": 2},
        port_links=[infrastructure.PortLink("psu", 1, source_port_id=41, label="A feed"),
                    infrastructure.PortLink("psu", 2, asset_id=9, source_port_id=42)]))

    links = [params for _sql_text, params in _sql(execute, "UPDATE rack_equipment_ports SET asset_id")]
    # PSUs record their feeding outlet; any asset id sent for a PSU is ignored.
    assert links == [(None, "A feed", 41, 70, 2, 1), (None, None, 42, 70, 3, 1)]


def test_psu_cannot_be_fed_from_its_own_outlet(monkeypatch):
    _mock_db(monkeypatch, fetch_one=[{"id": 8, "rack_id": 2}],
             fetch_all=[[{"id": 5, "port_number": 1, "connector": "iec"},
                         {"id": 6, "port_number": 2, "connector": "psu"}],
                        [{"id": 5, "equipment_id": 8}]])
    with pytest.raises(ValueError, match="another UPS or PDU"):
        asyncio.run(infrastructure.update_rack_equipment(
            1, 8, "UPS", "ups", None, None, None, port_counts={"iec": 1, "3pin": 0, "psu": 1},
            port_links=[infrastructure.PortLink("psu", 1, source_port_id=5)]))


def test_sync_ports_keeps_existing_links_and_trims_highest_ports(monkeypatch):
    rows = [{"id": 1, "port_number": 1, "connector": "iec"},
            {"id": 2, "port_number": 2, "connector": "iec"},
            {"id": 3, "port_number": 3, "connector": "iec"}]
    execute, _insert = _mock_db(monkeypatch, fetch_all=[rows])

    kept = asyncio.run(infrastructure._sync_ports(1, 8, {"iec": 2, "3pin": 2}))

    assert _sql(execute, "DELETE FROM rack_equipment_ports")[0][1] == (3,)
    # Anything fed from a removed outlet loses that link.
    assert _sql(execute, "SET source_port_id=NULL")[0][1] == (3,)
    inserted = [params[2:] for _sql_text, params in _sql(execute, "INSERT INTO rack_equipment_ports")]
    assert inserted == [(4, "3pin"), (5, "3pin")]
    assert [row["port_number"] for row in kept] == [1, 2, 4, 5]


def test_edit_moves_item_ignoring_its_own_slots(monkeypatch):
    item = {"id": 8, "rack_id": 2, "port_count": 0}
    fetch_one = AsyncMock(side_effect=[item, {"unit_count": 42}, None, None])
    monkeypatch.setattr(infrastructure.db, "fetch_one", fetch_one)
    monkeypatch.setattr(infrastructure.db, "fetch_all", AsyncMock(side_effect=[[{"unit_number": 3, "face": "front", "lane": 1}], []]))
    execute = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute", execute)

    asyncio.run(infrastructure.update_rack_equipment(
        1, 8, "Web", "server", None, None, None,
        position=infrastructure.RackPosition(10, 1, "rear", 1, 2, "half"), port_counts={}))

    conflict_sql = fetch_one.await_args_list[2].args
    assert "equipment_id<>%s" in conflict_sql[0] and conflict_sql[1][-1] == 8
    slots = [params for _sql_text, params in _sql(execute, "INSERT INTO rack_equipment_slots")]
    assert slots == [(8, 2, 10, "rear", 2)]
    update = _sql(execute, "UPDATE rack_equipment SET")[0]
    assert "start_unit=%s" in update[0] and update[1][-2:] == (8, 1)


def test_edit_rejects_a_move_onto_occupied_space_before_changing_anything(monkeypatch):
    execute, _insert = _mock_db(monkeypatch, fetch_one=[
        {"id": 8, "rack_id": 2}, {"unit_count": 42}, {"unit_number": 11},
    ])
    with pytest.raises(ValueError, match="already occupied"):
        asyncio.run(infrastructure.update_rack_equipment(
            1, 8, "Web", "server", None, None, None,
            position=infrastructure.RackPosition(10, 2, "front", 3, 1, "half"), port_counts={}))
    execute.assert_not_awaited()


def test_number_ports_orders_each_connector_separately():
    numbered = infrastructure._number_ports([
        {"port_number": 3, "connector": "3pin"}, {"port_number": 1, "connector": "iec"},
        {"port_number": 2, "connector": "iec"},
    ])
    assert [port["display_label"] for port in numbered] == ["IEC 1", "IEC 2", "3-pin 1"]


def test_form_parsing_reads_counts_links_and_psu_sources():
    from app.features.assets.routes import _rack_connections

    form = {
        "port_count_iec": "2", "port_count_3pin": "1", "port_count_psu": "1", "port_count_data": "9",
        "port-iec-1-asset": "5", "port-iec-1-label": "", "port-iec-2-asset": "",
        "port-iec-2-label": "Monitor", "port-3pin-1-asset": "", "port-3pin-1-label": "",
        "port-psu-1-source": "41", "port-psu-1-label": "Wall B2",
    }
    parsed = _rack_connections(form, "ups")

    assert parsed["port_counts"] == {"iec": 2, "3pin": 1, "psu": 1}
    assert parsed["port_links"] == [
        infrastructure.PortLink("iec", 1, 5, None),
        infrastructure.PortLink("iec", 2, None, "Monitor"),
        infrastructure.PortLink("3pin", 1, None, None),
        infrastructure.PortLink("psu", 1, None, "Wall B2", 41),
    ]


def test_kvm_form_parses_connected_devices_network_and_psu():
    from app.features.assets.routes import _rack_connections

    parsed = _rack_connections({"port_count_kvm": "2", "port_count_data": "1", "port_count_psu": "1",
                                "port-kvm-2-asset": "7", "port-kvm-2-label": "Web01"}, "kvm")
    assert parsed["port_counts"] == {"kvm": 2, "data": 1, "psu": 1}
    assert parsed["port_links"] == [infrastructure.PortLink("kvm", 2, 7, "Web01")]


def test_migration_436_moves_power_sources_onto_psu_ports():
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "migrations/436_rack_power_supply_inputs.sql").read_text()
    assert sql.startswith("-- phase: expand")
    assert "ADD COLUMN source_port_id INT NULL" in sql
    assert "'psu', e.power_source_label, e.power_source_port_id" in sql
    assert "DROP" not in sql.upper()


def test_migration_adds_connectors_labels_and_power_sources():
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "migrations/435_rack_connections_and_power_sources.sql").read_text()
    assert sql.startswith("-- phase: expand")
    for column in ("connector VARCHAR(8) NOT NULL DEFAULT 'data'", "label VARCHAR(191) NULL",
                   "power_source_port_id INT NULL", "power_source_label VARCHAR(191) NULL"):
        assert column in sql
    assert "DROP" not in sql.upper()


def _peer_db(monkeypatch, targets, own_ports):
    execute, _insert = _mock_db(monkeypatch, fetch_one=[{"id": 8, "rack_id": 2}],
                                fetch_all=[own_ports, targets, own_ports])
    return execute


def test_network_port_links_to_a_remote_port_on_both_ends(monkeypatch):
    own = [{"id": 11, "port_number": 1, "connector": "data"}, {"id": 12, "port_number": 2, "connector": "data"}]
    execute = _peer_db(monkeypatch, [{"id": 90, "equipment_id": 20, "connector": "data"}], own)

    asyncio.run(infrastructure.update_rack_equipment(
        1, 8, "Web01", "server", None, None, None, port_counts={"data": 2, "psu": 0},
        port_links=[infrastructure.PortLink("data", 1, asset_id=4, peer_port_id=90),
                    infrastructure.PortLink("data", 2)]))

    links = [params for _sql_text, params in _sql(execute, "UPDATE rack_equipment_ports SET asset_id")]
    assert links[0][0] is None  # the port link replaces the bare asset link
    sets = [params for _sql_text, params in _sql(execute, "SET peer_port_id=%s WHERE")]
    assert sets == [(90, 1, 11), (11, 1, 90)]
    clears = [params for _sql_text, params in _sql(execute, "SET peer_port_id=NULL WHERE company_id")]
    assert (1, 11, 11) in clears and (1, 90, 90) in clears and (1, 12, 12) in clears


def test_outlet_link_records_the_feed_on_the_remote_psu(monkeypatch):
    own = [{"id": 31, "port_number": 1, "connector": "iec"}, {"id": 32, "port_number": 2, "connector": "psu"}]
    execute = _peer_db(monkeypatch, [{"id": 77, "equipment_id": 20, "connector": "psu"}], own)

    asyncio.run(infrastructure.update_rack_equipment(
        1, 8, "PDU A", "pdu", None, None, None, port_counts={"iec": 1, "3pin": 0, "psu": 1},
        port_links=[infrastructure.PortLink("iec", 1, asset_id=4, peer_port_id=77)]))

    assert _sql(execute, "SET source_port_id=NULL WHERE company_id=%s AND source_port_id=%s")[0][1] == (1, 31)
    assert _sql(execute, "SET source_port_id=%s WHERE company_id=%s AND id=%s")[0][1] == (31, 1, 77)


@pytest.mark.parametrize(("connector", "target"), [("data", "psu"), ("iec", "data")])
def test_remote_port_must_be_the_right_kind_on_another_item(monkeypatch, connector, target):
    own = [{"id": 31, "port_number": 1, "connector": connector}]
    _peer_db(monkeypatch, [{"id": 77, "equipment_id": 20, "connector": target}], own)
    counts = {"data": 1, "psu": 0} if connector == "data" else {"iec": 1, "3pin": 0, "psu": 0}
    with pytest.raises(ValueError, match="on another rack item"):
        asyncio.run(infrastructure.update_rack_equipment(
            1, 8, "Item", "server" if connector == "data" else "pdu", None, None, None,
            port_counts=counts, port_links=[infrastructure.PortLink(connector, 1, peer_port_id=77)]))


def test_removing_ports_clears_links_pointing_at_them(monkeypatch):
    rows = [{"id": 1, "port_number": 1, "connector": "data"}, {"id": 2, "port_number": 2, "connector": "data"}]
    execute, _insert = _mock_db(monkeypatch, fetch_all=[rows])
    asyncio.run(infrastructure._sync_ports(1, 8, {"data": 1}))
    assert _sql(execute, "SET peer_port_id=NULL WHERE peer_port_id IN")[0][1] == (2,)


def test_form_parsing_reads_remote_ports_and_rack_items_without_assets():
    from app.features.assets.routes import _rack_connections

    parsed = _rack_connections({
        "port_count_data": "2", "port_count_psu": "0",
        "port-data-1-asset": "item:20", "port-data-1-peer": "90", "port-data-1-label": "",
        "port-data-2-asset": "4", "port-data-2-peer": "", "port-data-2-label": "",
    }, "server")
    assert parsed["port_links"] == [
        infrastructure.PortLink("data", 1, None, None, None, 90),
        infrastructure.PortLink("data", 2, 4, None, None, None),
    ]
    kvm = _rack_connections({"port_count_kvm": "1", "port_count_data": "0", "port_count_psu": "0",
                             "port-kvm-1-asset": "7", "port-kvm-1-peer": "90"}, "kvm")
    assert kvm["port_links"] == [infrastructure.PortLink("kvm", 1, 7)]  # KVM devices link to assets only


def test_port_catalog_lists_linkable_ports_with_the_items_asset():
    from app.services import rack_dashboard

    catalog = rack_dashboard.port_catalog([
        {"id": 20, "name": "Core switch", "rack_name": "Main", "asset_id": 4, "item_type": "switch",
         "ports": [{"id": 90, "connector": "data", "display_label": "Port 1", "peer_port_id": 11},
                   {"id": 91, "connector": "psu", "display_label": "PSU 1", "source_port_id": None}]},
        {"id": 21, "name": "Blank", "item_type": "shelf", "ports": []},
        {"id": 22, "name": "PDU", "item_type": "pdu",
         "ports": [{"id": 95, "connector": "iec", "display_label": "IEC 1"}]},
    ])
    assert [entry["id"] for entry in catalog] == [20]
    assert catalog[0]["asset_id"] == 4
    assert [port["label"] for port in catalog[0]["ports"]] == ["Port 1", "PSU 1"]


def test_migration_437_adds_port_peers():
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "migrations/437_rack_port_peers.sql").read_text()
    assert sql.startswith("-- phase: expand") and "ADD COLUMN peer_port_id INT NULL" in sql
