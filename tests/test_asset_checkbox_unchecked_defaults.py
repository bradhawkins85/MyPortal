"""Checkbox custom fields are always ticked or unticked, never "not set"."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.repositories import asset_checkbox_defaults
from app.repositories import rmm_targeting as targeting_repo
from app.services.rmm_targeting import field_catalogue, matches_rules, validate_rules

MIGRATION = Path(__file__).parent.parent / "migrations" / "576_backfill_unchecked_asset_checkbox_values.sql"


def _rule(operator, key="ARCHIVE_ASSET", value=None):
    node = {"field": "asset.custom", "operator": operator, "key": key}
    if value is not None:
        node["value"] = value
    return {"operator": "and", "children": [node]}


@pytest.mark.parametrize(("stored", "checked", "unchecked"), [
    (True, True, False), (False, False, True), ("1", True, False), ("false", False, True),
])
def test_checkbox_operators_match_ticked_and_unticked_values(stored, checked, unchecked):
    context = {"asset_custom": {"ARCHIVE_ASSET": stored}}
    assert matches_rules(_rule("is_checked"), context) is checked
    assert matches_rules(_rule("is_unchecked"), context) is unchecked


def test_checkbox_operators_fail_closed_on_unknown_or_non_boolean_values():
    for context in ({"asset_custom": None}, {"asset_custom": {}}, {"asset_custom": {"ARCHIVE_ASSET": "Sydney"}}):
        assert matches_rules(_rule("is_checked"), context) is False
        assert matches_rules(_rule("is_unchecked"), context) is False


def test_checkbox_operators_need_no_value_and_are_offered_for_custom_fields():
    assert validate_rules(_rule("is_unchecked", value="1"))["children"][0] == {
        "field": "asset.custom", "operator": "is_unchecked", "key": "ARCHIVE_ASSET"}
    operators = {field["key"]: field["operators"] for field in field_catalogue()}
    assert {"is_checked", "is_unchecked"} <= set(operators["asset.custom"])
    assert "is_checked" not in operators["asset.name"]


class _Database:
    def __init__(self, connection):
        self.connection = connection

    async def fetch_all(self, sql, params=()):
        return [dict(row) for row in self.connection.execute(sql.replace("%s", "?"), params).fetchall()]

    async def fetch_one(self, sql, params=()):
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql, params=()):
        self.connection.execute(sql.replace("%s", "?"), params)


@pytest.fixture
def checkbox_db(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, archived INT, is_vip INT);
        CREATE TABLE rmm_agents (id INTEGER PRIMARY KEY, company_id INT, status TEXT);
        CREATE TABLE assets (id INTEGER PRIMARY KEY, company_id INT, name TEXT, archived_at TEXT,
                             type TEXT, asset_type TEXT, motherboard_manufacturer TEXT, hdd_size TEXT);
        CREATE TABLE company_variable_definitions (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE company_variable_values (company_id INT, variable_id INT, value TEXT);
        CREATE TABLE company_tags (company_id INT, tag_id INT);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE asset_tags (asset_id INT, tag_id INT);
        CREATE TABLE asset_tag_blocks (asset_id INT, tag_id INT);
        CREATE TABLE ip_addresses (id INTEGER PRIMARY KEY, company_id INT, asset_id INT, address TEXT);
        CREATE TABLE asset_interfaces (asset_id INT, company_id INT, ip_address_id INT);
        CREATE TABLE asset_custom_field_definitions (id INTEGER PRIMARY KEY, name TEXT, field_type TEXT);
        CREATE TABLE asset_custom_field_values (id INTEGER PRIMARY KEY, asset_id INT, field_definition_id INT,
            value_text TEXT, value_date TEXT, value_boolean INT, UNIQUE (asset_id, field_definition_id));
        INSERT INTO companies VALUES (1, 'Acme', 0, 0);
        INSERT INTO assets (id, company_id, name) VALUES (10, 1, 'New'), (11, 1, 'Ticked'), (12, 1, 'Cleared');
        INSERT INTO asset_custom_field_definitions VALUES (1, 'ARCHIVE_ASSET', 'checkbox'), (2, 'Site', 'text');
        INSERT INTO asset_custom_field_values (asset_id, field_definition_id, value_boolean)
            VALUES (11, 1, 1), (12, 1, NULL);
    """)
    adapter = _Database(connection)
    monkeypatch.setattr(targeting_repo, "db", adapter)
    monkeypatch.setattr(asset_checkbox_defaults, "db", adapter)

    async def presence(pairs):
        return None

    monkeypatch.setattr(targeting_repo, "_apply_tray_presence", presence)
    yield connection
    connection.close()


def _stored(connection):
    return {(row[0], row[1]): row[2] for row in connection.execute(
        "SELECT asset_id, field_definition_id, value_boolean FROM asset_custom_field_values")}


@pytest.mark.asyncio
async def test_assets_without_a_stored_checkbox_are_targeted_as_unchecked(checkbox_db):
    results = {}
    for asset_id in (10, 11, 12):
        context = await targeting_repo.context_for_asset(1, asset_id)
        results[asset_id] = (
            matches_rules(_rule("is_unchecked"), context),
            matches_rules(_rule("not_equals", value="1"), context),
            matches_rules(_rule("is_checked"), context),
        )
    assert results == {10: (True, True, False), 11: (False, False, True), 12: (True, True, False)}


@pytest.mark.asyncio
async def test_unchecked_values_are_stored_for_every_checkbox(checkbox_db):
    await asset_checkbox_defaults.ensure_unchecked_checkbox_values(asset_id=10)
    assert _stored(checkbox_db) == {(10, 1): 0, (11, 1): 1, (12, 1): None}
    await asset_checkbox_defaults.ensure_unchecked_checkbox_values()
    assert _stored(checkbox_db) == {(10, 1): 0, (11, 1): 1, (12, 1): 0}


def test_migration_backfills_unchecked_values_idempotently(checkbox_db):
    sql = MIGRATION.read_text(encoding="utf-8")
    assert sql.startswith("-- phase: backfill")
    checkbox_db.executescript(sql)
    checkbox_db.executescript(sql)
    assert _stored(checkbox_db) == {(10, 1): 0, (11, 1): 1, (12, 1): 0}


@pytest.mark.asyncio
async def test_defined_text_field_without_value_matches_does_not_contain(checkbox_db):
    rule = {"operator": "and", "children": [{
        "field": "asset.custom", "key": "Site", "operator": "not_contains", "value": "Brisbane",
    }]}
    checkbox_db.execute(
        "INSERT INTO asset_custom_field_values (asset_id, field_definition_id, value_text) VALUES (11, 2, 'Brisbane')"
    )
    results = {asset_id: matches_rules(rule, await targeting_repo.context_for_asset(1, asset_id))
               for asset_id in (10, 11)}
    assert results == {10: True, 11: False}
