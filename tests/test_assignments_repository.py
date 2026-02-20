"""Tests for the assignments repository.

These tests verify that JSON fields (repeat_pattern, point_overrides,
category_point_overrides, rotation_state) are correctly serialised to JSON
strings before being inserted into the database.  This prevents the
'Column count doesn't match value count' SQL error that occurs when object
values are not serialised to strings.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.repositories import assignments


class _DummyDB:
    def __init__(self, *, fetched_rows: list[dict[str, Any]] | None = None):
        self.executed: list[tuple[str, tuple]] = []
        self._fetched_rows = fetched_rows or []

    async def execute(self, sql: str, params: tuple | None = None) -> None:
        self.executed.append((sql.strip(), params or ()))

    async def fetch_all(self, sql: str, params: tuple | None = None) -> list[dict]:
        return self._fetched_rows


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_replace_assignments_serialises_repeat_pattern(monkeypatch):
    """repeat_pattern dict must be JSON-serialised before insertion."""
    dummy = _DummyDB()
    monkeypatch.setattr(assignments, "db", dummy)

    repeat_pattern = {
        "interval": 2,
        "unit": "weeks",
        "specificDays": "wednesday",
        "anchorDate": 1777384800000,
    }

    await assignments.replace_assignments(
        "fc81a7d1-fd2d-4efc-91a6-3df5a9c38879",
        [
            {
                "id": "588f5280-3712-4744-a169-4086b91d98f6",
                "chore_id": "389b3ee4-bc94-4cf8-9d26-a5bc8d54fe56",
                "child_id": "46696129-3ce9-4f60-a382-b4c08bcd0355",
                "assigned_at": 1771558706286,
                "assigned_for": None,
                "start_date": 1777384800000,
                "end_date": None,
                "days_of_week": None,
                "repeat_pattern": repeat_pattern,
                "time_of_day": "am",
                "time_window": None,
                "point_overrides": [],
                "category_point_overrides": None,
                "rotation_state": None,
                "status": 0,
                "points": 15,
                "sort_order": 0,
            }
        ],
    )

    # Should be DELETE + INSERT
    assert len(dummy.executed) == 2

    _delete_sql, delete_params = dummy.executed[0]
    assert "DELETE" in _delete_sql
    assert delete_params == ("fc81a7d1-fd2d-4efc-91a6-3df5a9c38879",)

    insert_sql, insert_params = dummy.executed[1]
    assert "INSERT INTO tenant_assignments_v2" in insert_sql

    # Count the number of %s placeholders – must equal number of columns (18)
    placeholder_count = insert_sql.count("%s")
    assert placeholder_count == 18, f"Expected 18 placeholders, got {placeholder_count}"

    # Verify repeat_pattern is a JSON string, not a raw object
    repeat_pattern_param = insert_params[9]  # 10th param (0-indexed: 9)
    assert isinstance(repeat_pattern_param, str), (
        "repeat_pattern must be serialised to a JSON string before insertion"
    )
    parsed = json.loads(repeat_pattern_param)
    assert parsed["interval"] == 2
    assert parsed["unit"] == "weeks"
    assert parsed["specificDays"] == "wednesday"
    assert parsed["anchorDate"] == 1777384800000

    # Verify parameter count matches column count
    assert len(insert_params) == 18, (
        f"INSERT must have exactly 18 values, got {len(insert_params)}"
    )


@pytest.mark.anyio
async def test_replace_assignments_serialises_point_overrides(monkeypatch):
    """point_overrides list must be JSON-serialised before insertion."""
    dummy = _DummyDB()
    monkeypatch.setattr(assignments, "db", dummy)

    await assignments.replace_assignments(
        "tenant-1",
        [
            {
                "id": "assign-1",
                "chore_id": "chore-1",
                "child_id": "child-1",
                "assigned_at": 1000,
                "assigned_for": None,
                "start_date": None,
                "end_date": None,
                "days_of_week": None,
                "repeat_pattern": None,
                "time_of_day": None,
                "time_window": None,
                "point_overrides": [{"chore_id": "c1", "points": 10}],
                "category_point_overrides": {"cat1": 5},
                "rotation_state": {"current": 0},
                "status": 1,
                "points": 10,
                "sort_order": 1,
            }
        ],
    )

    _delete, insert = dummy.executed[0], dummy.executed[1]
    insert_params = insert[1]

    # point_overrides is at index 12, category_point_overrides at 13, rotation_state at 14
    point_overrides_param = insert_params[12]
    category_point_overrides_param = insert_params[13]
    rotation_state_param = insert_params[14]

    assert isinstance(point_overrides_param, str)
    assert json.loads(point_overrides_param) == [{"chore_id": "c1", "points": 10}]

    assert isinstance(category_point_overrides_param, str)
    assert json.loads(category_point_overrides_param) == {"cat1": 5}

    assert isinstance(rotation_state_param, str)
    assert json.loads(rotation_state_param) == {"current": 0}


@pytest.mark.anyio
async def test_replace_assignments_none_json_fields(monkeypatch):
    """None JSON fields must remain None (not serialised to 'null' string)."""
    dummy = _DummyDB()
    monkeypatch.setattr(assignments, "db", dummy)

    await assignments.replace_assignments(
        "tenant-2",
        [
            {
                "id": "assign-2",
                "chore_id": "chore-2",
                "child_id": "child-2",
                "assigned_at": None,
                "assigned_for": None,
                "start_date": None,
                "end_date": None,
                "days_of_week": None,
                "repeat_pattern": None,
                "time_of_day": None,
                "time_window": None,
                "point_overrides": None,
                "category_point_overrides": None,
                "rotation_state": None,
                "status": 0,
                "points": 0,
                "sort_order": 0,
            }
        ],
    )

    _delete, insert = dummy.executed[0], dummy.executed[1]
    insert_params = insert[1]

    assert insert_params[9] is None   # repeat_pattern
    assert insert_params[12] is None  # point_overrides
    assert insert_params[13] is None  # category_point_overrides
    assert insert_params[14] is None  # rotation_state


@pytest.mark.anyio
async def test_replace_assignments_empty_list(monkeypatch):
    """Empty assignment list should only execute the DELETE statement."""
    dummy = _DummyDB()
    monkeypatch.setattr(assignments, "db", dummy)

    await assignments.replace_assignments("tenant-3", [])

    assert len(dummy.executed) == 1
    delete_sql = dummy.executed[0][0]
    assert "DELETE" in delete_sql


@pytest.mark.anyio
async def test_get_assignments_deserialises_json_fields(monkeypatch):
    """get_assignments must deserialise JSON string fields back to Python objects."""
    raw_row = {
        "id": "assign-99",
        "tenant_id": "tenant-99",
        "chore_id": "chore-99",
        "child_id": "child-99",
        "assigned_at": 1000,
        "assigned_for": None,
        "start_date": None,
        "end_date": None,
        "days_of_week": None,
        "repeat_pattern": '{"interval": 2, "unit": "weeks"}',
        "time_of_day": "pm",
        "time_window": None,
        "point_overrides": "[]",
        "category_point_overrides": None,
        "rotation_state": None,
        "status": 0,
        "points": 5,
        "sort_order": 0,
    }
    dummy = _DummyDB(fetched_rows=[raw_row])
    monkeypatch.setattr(assignments, "db", dummy)

    result = await assignments.get_assignments("tenant-99")

    assert len(result) == 1
    record = result[0]
    assert record["repeat_pattern"] == {"interval": 2, "unit": "weeks"}
    assert record["point_overrides"] == []
    assert record["category_point_overrides"] is None
