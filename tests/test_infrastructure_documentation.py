import asyncio
from unittest.mock import AsyncMock

import pytest

from app.repositories import infrastructure


def test_address_must_be_inside_company_network(monkeypatch):
    monkeypatch.setattr(
        infrastructure.db, "fetch_one", AsyncMock(return_value={"cidr": "192.0.2.0/24"})
    )
    insert = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute_returning_lastrowid", insert)

    with pytest.raises(ValueError, match="outside"):
        asyncio.run(infrastructure.create_address(
            8, 2, "198.51.100.4", "assigned", None, [], None
        ))
    insert.assert_not_awaited()


def test_address_links_only_company_asset(monkeypatch):
    fetch = AsyncMock(side_effect=[{"cidr": "192.0.2.0/24"}, None])
    monkeypatch.setattr(infrastructure.db, "fetch_one", fetch)

    with pytest.raises(ValueError, match="does not belong"):
        asyncio.run(infrastructure.create_address(
            8, 2, "192.0.2.10", "assigned", 99, [], None
        ))


def test_rack_overlap_is_rejected_before_insert(monkeypatch):
    fetch = AsyncMock(side_effect=[{"unit_count": 42}, {"id": 3}, {"unit_number": 12}])
    monkeypatch.setattr(infrastructure.db, "fetch_one", fetch)
    insert = AsyncMock()
    monkeypatch.setattr(infrastructure.db, "execute_returning_lastrowid", insert)

    with pytest.raises(ValueError, match="already occupied"):
        asyncio.run(infrastructure.place_asset(8, 4, 3, 12, 2, "front", None))
    insert.assert_not_awaited()


def test_rack_position_is_bounded_by_rack_size(monkeypatch):
    fetch = AsyncMock(side_effect=[{"unit_count": 12}, {"id": 3}])
    monkeypatch.setattr(infrastructure.db, "fetch_one", fetch)

    with pytest.raises(ValueError, match="invalid"):
        asyncio.run(infrastructure.place_asset(8, 4, 3, 12, 2, "front", None))


def test_migration_has_database_conflict_guards():
    sql = open("migrations/407_network_and_rack_documentation.sql", encoding="utf-8").read()
    assert "UNIQUE (company_id, address)" in sql
    assert "PRIMARY KEY (rack_id, unit_number, face)" in sql
    assert "REFERENCES assets(id)" in sql
