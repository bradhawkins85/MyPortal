"""Inventory loading must preserve company boundaries and unknown values."""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.repositories import rmm_targeting as targeting


class _Database:
    def __init__(self, connection):
        self.connection = connection
        self.calls = []

    async def fetch_all(self, sql, params=()):
        self.calls.append((sql, params))
        cursor = self.connection.execute(sql.replace("%s", "?"), params)
        return [dict(row) for row in cursor.fetchall()]


@pytest.fixture
def inventory_db(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, archived INT, is_vip INT,
                                trello_token TEXT);
        CREATE TABLE rmm_agents (id INTEGER PRIMARY KEY, company_id INT, asset_id INT, status TEXT,
            hostname TEXT, os TEXT, os_version TEXT, arch TEXT, agent_version TEXT,
            last_ip TEXT, last_seen_utc TEXT);
        CREATE TABLE assets (id INTEGER PRIMARY KEY, company_id INT, name TEXT,
            archived_at TEXT, type TEXT, asset_type TEXT, motherboard_manufacturer TEXT,
            ram_gb INT, hdd_size TEXT);
        CREATE TABLE company_variable_definitions (id INTEGER PRIMARY KEY, name TEXT, is_secret INT DEFAULT 0);
        CREATE TABLE company_variable_values (company_id INT, variable_id INT, value TEXT);
        CREATE TABLE asset_custom_field_definitions (id INTEGER PRIMARY KEY, name TEXT, field_type TEXT);
        CREATE TABLE asset_custom_field_values (asset_id INT, field_definition_id INT,
            value_text TEXT, value_date TEXT, value_boolean INT);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE asset_tags (asset_id INT, tag_id INT);
        CREATE TABLE company_tags (company_id INT, tag_id INT);
        CREATE TABLE asset_tag_blocks (asset_id INT, tag_id INT);
        CREATE TABLE ip_addresses (id INTEGER PRIMARY KEY, company_id INT, asset_id INT, address TEXT);
        CREATE TABLE asset_interfaces (asset_id INT, company_id INT, ip_address_id INT);
        CREATE TABLE rmm_device_inventory (agent_id INTEGER PRIMARY KEY, company_id INT, asset_id INT,
            received_at TEXT, manufacturer TEXT, model TEXT, cpu_model TEXT, cpu_cores INT,
            cpu_logical_processors INT, ram_bytes INT, disk_total_bytes INT,
            system_disk_free_bytes INT, os_name TEXT, os_version TEXT, os_build TEXT,
            bios_version TEXT, domain TEXT, arch TEXT, hostname TEXT, last_boot_at TEXT, details_json TEXT);
        CREATE TABLE rmm_device_software (agent_id INT, name TEXT, version TEXT, publisher TEXT);
        INSERT INTO companies VALUES (1, 'VIP', 0, 1, 'private-token'), (2, 'Other', 0, 0, NULL),
                                     (3, 'Archived', 1, 1, NULL);
        INSERT INTO rmm_agents (id, company_id, asset_id, status, hostname, os_version, arch,
                               agent_version, last_ip)
            VALUES (1, 1, 10, 'active', 'VIP-SERVER', '10.0.20348', 'amd64', '1.2.3', '198.51.100.1'),
                   (2, 1, 30, 'active', NULL, NULL, NULL, NULL, NULL),
                   (3, 1, 10, 'revoked', NULL, NULL, NULL, NULL, NULL),
                   (4, 2, 20, 'active', NULL, NULL, NULL, NULL, NULL),
                   (5, 3, NULL, 'active', NULL, NULL, NULL, NULL, NULL);
        INSERT INTO assets VALUES (10, 1, 'Server', NULL, 'Server', 'server', 'Dell', 32, '1 TB'),
                                  (20, 2, 'Other device', NULL, 'Workstation', 'workstation', NULL, 16, 'Disk model 512GB'),
                                  (30, 1, 'Archived device', '2026-10-01', 'Server', 'server', NULL, NULL, NULL);
        INSERT INTO company_variable_definitions VALUES (1, 'Tier', 0), (2, 'API key', 1), (3, 'Missing', 0);
        INSERT INTO company_variable_values VALUES (1, 1, 'Gold'), (1, 2, 'company-secret'), (2, 1, 'Other tier');
        INSERT INTO asset_custom_field_definitions VALUES (1, 'VPN required', 'checkbox'),
                                                          (2, 'Site', 'text'), (3, 'Renewal', 'date');
        INSERT INTO asset_custom_field_values VALUES (10, 1, NULL, NULL, 0),
                                                     (10, 2, 'Brisbane', NULL, NULL),
                                                     (10, 3, NULL, '2027-01-02', NULL),
                                                     (20, 2, 'Private site', NULL, NULL);
        INSERT INTO tags VALUES (1, 'Managed'), (2, 'Local'), (3, 'Blocked');
        INSERT INTO company_tags VALUES (1, 1), (1, 3), (2, 2);
        INSERT INTO asset_tags VALUES (10, 2);
        INSERT INTO asset_tag_blocks VALUES (10, 3);
        INSERT INTO ip_addresses VALUES (1, 1, 10, '10.1.2.3'), (2, 1, NULL, '2001:db8::1'),
                                        (3, 2, 10, '192.0.2.99'), (4, 1, 10, 'invalid');
        INSERT INTO asset_interfaces VALUES (10, 1, 2), (10, 1, 3);
    """)
    adapter = _Database(connection)
    monkeypatch.setattr(targeting, "db", adapter)
    yield adapter
    connection.close()


def _agent(agent_id=1, *, company_id=1, asset_id=10):
    return {"id": agent_id, "company_id": company_id, "asset_id": asset_id,
            "hostname": "VIP-SERVER", "arch": "amd64", "agent_version": "1.2.3",
            "os_version": "10.0.20348", "last_ip": "198.51.100.1", "status": "active",
            "auth_token_hash": "private-hash"}


def _snapshot(inventory_db, *, agent_id=1, company_id=1, asset_id=10, received_at="2026-10-12",
              details=None):
    inventory_db.connection.execute(
        "INSERT INTO rmm_device_inventory (agent_id, company_id, asset_id, received_at, details_json) "
        "VALUES (?, ?, ?, ?, ?)", (agent_id, company_id, asset_id, received_at, json.dumps(details or {})),
    )


@pytest.mark.asyncio
async def test_context_loads_inventory_and_preserves_typed_fields(inventory_db):
    _snapshot(inventory_db, details={"network_adapters": [{"addresses": ["10.2.3.4/24", "invalid"]}]})
    inventory_db.connection.execute("""
        UPDATE rmm_device_inventory SET manufacturer='HP', model='ProLiant', cpu_model='Xeon',
            cpu_cores=12, cpu_logical_processors=24, ram_bytes=?, disk_total_bytes=?,
            system_disk_free_bytes=?, os_name='Windows Server', os_version='10.0.20348',
            os_build='20348', bios_version='2.1', domain='example.test' WHERE agent_id=1
    """, (64 * 1024 ** 3, 512 * 1024 ** 3, 20 * 1024 ** 3))
    inventory_db.connection.execute("INSERT INTO rmm_device_software VALUES (1, 'WireGuard', '1.0', 'WireGuard Project')")
    contexts = await targeting.contexts_for_agents([_agent()])
    context = contexts[1]
    assert context["company"]["vip"] is True
    assert context["company"]["pending_approval"] is None
    assert context["company"]["is_demo"] is None
    assert context["company"]["agent_count"] == 2
    assert context["asset"]["catalogue_type"] == "server"
    assert context["asset"]["manufacturer"] == "HP"
    assert context["asset"]["model"] == "ProLiant"
    assert context["asset"]["ram_gb"] == 64
    assert context["asset"]["cpu_cores"] == 12
    assert context["asset"]["cpu_logical_processors"] == 24
    assert context["asset"]["hdd_size"] == "1 TB"
    assert context["asset"]["disk_gb"] == 512
    assert context["asset"]["disk_free_gb"] == 20
    assert context["asset"]["os_version"] == "10.0.20348"
    assert context["agent"]["architecture"] == "amd64"
    assert context["agent"]["version"] == "1.2.3"
    assert context["software"] == ["WireGuard"]
    assert context["software_versions"] == {"WireGuard": ["1.0"]}
    assert context["software_publishers"] == {"WireGuard": ["WireGuard Project"]}
    assert context["asset_custom"]["VPN required"] is False
    assert context["asset_custom"]["1"] is False
    assert context["asset_custom"]["Site"] == "Brisbane"
    assert context["asset_custom"]["Renewal"] == "2027-01-02"
    assert context["company_variables"] == {"Tier": "Gold", "1": "Gold"}
    assert context["asset_tags"] == ["Local", "Managed"]
    assert context["company_tags"] == ["Blocked", "Managed"]
    assert set(context["asset"]["ip_address"]) == {"10.1.2.3", "2001:db8::1", "10.2.3.4"}
    assert "trello_token" not in context["company"]
    assert "auth_token_hash" not in context["agent"]
    assert "198.51.100.1" not in context["asset"]["ip_address"]


@pytest.mark.asyncio
async def test_missing_cross_company_and_archived_records_are_not_used(inventory_db):
    inventory_db.connection.execute("UPDATE rmm_agents SET asset_id=20 WHERE id=1")
    inventory_db.connection.execute("INSERT INTO rmm_agents (id, company_id, status) VALUES (7, 1, 'active')")
    contexts = await targeting.contexts_for_agents([
        _agent(1, asset_id=20), _agent(2, asset_id=30),
        _agent(5, company_id=3, asset_id=None), _agent(6, company_id=99, asset_id=None),
        _agent(7, asset_id=None),
    ])
    assert set(contexts) == {1, 2, 7}
    for context in contexts.values():
        assert context["asset"] is None
        assert context["asset_custom"] is None
        assert context["asset_tags"] is None
        assert context["company_variables"] == {"Tier": "Gold", "1": "Gold"}
    assert contexts[1]["software"] is None
    assert contexts[2]["software"] is None


@pytest.mark.asyncio
async def test_batch_loading_has_bounded_queries_and_does_not_read_unrelated_data(inventory_db, monkeypatch):
    monkeypatch.setattr(targeting, "_BATCH_SIZE", 2)
    inventory_db.connection.execute("UPDATE rmm_agents SET asset_id=NULL WHERE id=2")
    await targeting.contexts_for_agents([
        _agent(1), _agent(2, asset_id=None), _agent(4, company_id=2, asset_id=20),
    ])
    assert len(inventory_db.calls) == 13
    assert max(len(params) for _, params in inventory_db.calls) <= 4
    for sql, params in inventory_db.calls:
        assert " IN (" in sql
        assert 3 not in params


@pytest.mark.asyncio
async def test_software_distinguishes_unknown_empty_failed_and_reassigned_inventory(inventory_db):
    _snapshot(inventory_db, agent_id=1, company_id=2)
    _snapshot(inventory_db, agent_id=2, asset_id=30)
    _snapshot(inventory_db, agent_id=4, company_id=2, asset_id=20,
              details={"errors": ["software: permission denied"]})
    contexts = await targeting.contexts_for_agents([
        _agent(1), _agent(2, asset_id=30), _agent(4, company_id=2, asset_id=20),
    ])
    assert contexts[1]["software"] is None
    assert contexts[2]["software"] == []
    assert contexts[2]["software_versions"] == {}
    assert contexts[4]["software"] is None
    assert contexts[4]["software_versions"] is None


@pytest.mark.asyncio
async def test_inventory_without_canonical_asset_exposes_only_device_facts(inventory_db):
    inventory_db.connection.execute("UPDATE rmm_agents SET asset_id=NULL WHERE id=1")
    _snapshot(inventory_db, asset_id=None)
    inventory_db.connection.execute("UPDATE rmm_device_inventory SET manufacturer='Dell', ram_bytes=?", (16 * 1024 ** 3,))
    context = (await targeting.contexts_for_agents([_agent(asset_id=None)]))[1]
    assert context["asset"]["manufacturer"] == "Dell"
    assert context["asset"]["ram_gb"] == 16
    assert context["asset"]["id"] is None
    assert context["asset"]["name"] is None
    assert context["asset_tags"] is None
    assert context["asset_custom"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error,known", [
    ("software: permission denied", False), ("powershell: process failed", False),
    ("system_profiler: invalid JSON", False), ("gpus: permission denied", True),
])
async def test_software_collector_failures_do_not_become_known_empty(inventory_db, error, known):
    _snapshot(inventory_db, details={"errors": [error]})
    context = (await targeting.contexts_for_agents([_agent()]))[1]
    assert context["software"] == ([] if known else None)
    assert context["software_versions"] == ({} if known else None)
    assert context["software_publishers"] == ({} if known else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["broken JSON", "[]", "null",
    '{"errors":"software: failed"}', '{"errors":{"powershell":"failed"}}',
    '{"errors":[null]}', '{"errors":null}'])
async def test_invalid_inventory_details_leave_software_and_unrecorded_ips_unknown(inventory_db, raw):
    _snapshot(inventory_db)
    inventory_db.connection.execute("UPDATE rmm_device_inventory SET details_json=?", (raw,))
    inventory_db.connection.execute("DELETE FROM ip_addresses WHERE asset_id=10")
    inventory_db.connection.execute("DELETE FROM asset_interfaces WHERE asset_id=10")
    context = (await targeting.contexts_for_agents([_agent()]))[1]
    assert context["software"] is None
    assert context["software_versions"] is None
    assert context["asset"]["ip_address"] is None


@pytest.mark.asyncio
async def test_unrecorded_canonical_ips_are_unknown_without_native_collection(inventory_db):
    inventory_db.connection.execute("DELETE FROM ip_addresses WHERE asset_id=10")
    inventory_db.connection.execute("DELETE FROM asset_interfaces WHERE asset_id=10")
    context = (await targeting.contexts_for_agents([_agent()]))[1]
    assert context["asset"]["ip_address"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [(None, []), ("network: failed", None),
    ("powershell: failed", None), ("system_profiler: failed", None), ("gpus: failed", [])])
async def test_native_empty_network_inventory_requires_successful_collection(inventory_db, error, expected):
    inventory_db.connection.execute("DELETE FROM ip_addresses WHERE asset_id=10")
    inventory_db.connection.execute("DELETE FROM asset_interfaces WHERE asset_id=10")
    _snapshot(inventory_db, details={"network_adapters": [], "errors": [error] if error else []})
    context = (await targeting.contexts_for_agents([_agent()]))[1]
    assert context["asset"]["ip_address"] == expected


@pytest.mark.asyncio
async def test_recorded_ips_remain_available_when_native_collection_failed(inventory_db):
    from app.services.rmm_targeting import matches_rules

    _snapshot(inventory_db, details={"network_adapters": [], "errors": ["powershell: failed"]})
    context = (await targeting.contexts_for_agents([_agent()]))[1]
    assert set(context["asset"]["ip_address"]) == {"10.1.2.3", "2001:db8::1"}
    assert context["incomplete_fields"] == ["asset.ip_address"]
    assert matches_rules({"operator": "and", "children": [
        {"field": "asset.ip_address", "operator": "in_subnet", "value": "10.0.0.0/8"},
    ]}, context)
    assert not matches_rules({"operator": "and", "children": [
        {"field": "asset.ip_address", "operator": "not_in_subnet", "value": "192.168.0.0/16"},
    ]}, context)


@pytest.mark.asyncio
async def test_agent_reassignment_after_input_snapshot_is_excluded(inventory_db):
    assert await targeting.contexts_for_agents([_agent(asset_id=20)]) == {}


@pytest.mark.asyncio
async def test_numeric_names_cannot_impersonate_other_definition_ids(inventory_db):
    inventory_db.connection.executescript("""
        INSERT INTO asset_custom_field_definitions VALUES (15, '16', 'text'), (16, 'Absent field', 'text');
        INSERT INTO asset_custom_field_values VALUES (10, 15, 'numeric field name', NULL, NULL);
        INSERT INTO company_variable_definitions VALUES (15, '16', 0), (16, 'Absent variable', 0);
        INSERT INTO company_variable_values VALUES (1, 15, 'numeric variable name');
    """)
    context = (await targeting.contexts_for_agents([_agent()]))[1]
    assert context["asset_custom"]["15"] == "numeric field name"
    assert "16" not in context["asset_custom"]
    assert context["company_variables"]["15"] == "numeric variable name"
    assert "16" not in context["company_variables"]
    inventory_db.connection.executescript("""
        INSERT INTO asset_custom_field_values VALUES (10, 16, 'actual field value', NULL, NULL);
        INSERT INTO company_variable_values VALUES (1, 16, 'actual variable value');
    """)
    context = (await targeting.contexts_for_agents([_agent()]))[1]
    assert context["asset_custom"]["16"] == "actual field value"
    assert context["company_variables"]["16"] == "actual variable value"


@pytest.mark.asyncio
async def test_nullable_company_flags_stay_unknown_and_explicit_false_is_preserved(inventory_db):
    inventory_db.connection.executescript("""
        ALTER TABLE companies ADD COLUMN pending_approval TEXT;
        ALTER TABLE companies ADD COLUMN is_demo TEXT;
        UPDATE companies SET pending_approval='false', is_demo='0' WHERE id=1;
        UPDATE companies SET is_vip=NULL, is_demo='1' WHERE id=2;
    """)
    contexts = await targeting.contexts_for_agents([_agent(), _agent(4, company_id=2, asset_id=20)])
    assert contexts[1]["company"]["vip"] is True
    assert contexts[1]["company"]["pending_approval"] is False
    assert contexts[1]["company"]["is_demo"] is False
    assert contexts[4]["company"]["vip"] is None
    assert contexts[4]["company"]["pending_approval"] is None
    assert contexts[4]["company"]["is_demo"] is True


@pytest.mark.parametrize("raw,expected", [(None, None), ("unknown", None), (2, None),
    (0, False), (1, True), (False, False), (True, True), ("false", False), ("true", True)])
def test_company_boolean_conversion_preserves_unknown_flags(raw, expected):
    assert targeting._boolean(raw) is expected


@pytest.mark.parametrize("raw,expected", [("512 GB", 512), ("1.5 TiB", 1536),
    ("256", 256), ("Disk model 512GB", None), ("512 GB | 1 TB", None), (None, None)])
def test_disk_capacity_only_uses_unambiguous_values(raw, expected):
    assert targeting._capacity_gb(raw) == expected


@pytest.mark.asyncio
async def test_empty_or_invalid_agents_do_not_query(inventory_db):
    assert await targeting.contexts_for_agents([]) == {}
    assert await targeting.contexts_for_agents([{"id": True, "company_id": 1}]) == {}
    assert inventory_db.calls == []
