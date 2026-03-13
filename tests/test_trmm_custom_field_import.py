"""Tests for TRMM custom field import into MyPortal asset custom fields."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from app.services import tacticalrmm


# ---------------------------------------------------------------------------
# Tests for extract_trmm_custom_fields
# ---------------------------------------------------------------------------


def test_extract_trmm_custom_fields_empty_agent():
    """Agent with no custom_fields returns empty dict."""
    assert tacticalrmm.extract_trmm_custom_fields({}) == {}


def test_extract_trmm_custom_fields_non_list_ignored():
    """Non-list custom_fields is silently ignored."""
    assert tacticalrmm.extract_trmm_custom_fields({"custom_fields": "bad"}) == {}


def test_extract_trmm_custom_fields_checkbox_type():
    """Checkbox field returns bool_value."""
    agent = {
        "custom_fields": [
            {
                "name": "Bitdefender",
                "type": "checkbox",
                "bool_value": True,
                "string_value": None,
            }
        ]
    }
    result = tacticalrmm.extract_trmm_custom_fields(agent)
    assert "Bitdefender" in result
    assert result["Bitdefender"]["type"] == "checkbox"
    assert result["Bitdefender"]["value"] is True


def test_extract_trmm_custom_fields_checkbox_false():
    """Checkbox field with bool_value False returns False."""
    agent = {
        "custom_fields": [
            {
                "name": "Sophos",
                "type": "checkbox",
                "bool_value": False,
            }
        ]
    }
    result = tacticalrmm.extract_trmm_custom_fields(agent)
    assert result["Sophos"]["value"] is False


def test_extract_trmm_custom_fields_checkbox_from_value_key():
    """Checkbox field using 'value' key instead of 'bool_value'."""
    agent = {
        "custom_fields": [
            {
                "name": "AV Installed",
                "type": "checkbox",
                "bool_value": None,
                "value": True,
            }
        ]
    }
    result = tacticalrmm.extract_trmm_custom_fields(agent)
    assert result["AV Installed"]["value"] is True


def test_extract_trmm_custom_fields_text_type():
    """Text field returns string_value."""
    agent = {
        "custom_fields": [
            {
                "name": "Notes",
                "type": "text",
                "string_value": "Some note text",
                "bool_value": None,
            }
        ]
    }
    result = tacticalrmm.extract_trmm_custom_fields(agent)
    assert result["Notes"]["type"] == "text"
    assert result["Notes"]["value"] == "Some note text"


def test_extract_trmm_custom_fields_multiple_fields():
    """Multiple custom fields are all parsed."""
    agent = {
        "custom_fields": [
            {"name": "Field A", "type": "text", "string_value": "hello"},
            {"name": "Field B", "type": "checkbox", "bool_value": True},
            {"name": "Field C", "type": "text", "string_value": "world"},
        ]
    }
    result = tacticalrmm.extract_trmm_custom_fields(agent)
    assert len(result) == 3
    assert result["Field A"]["value"] == "hello"
    assert result["Field B"]["value"] is True
    assert result["Field C"]["value"] == "world"


def test_extract_trmm_custom_fields_skips_entries_without_name():
    """Entries without a name are silently skipped."""
    agent = {
        "custom_fields": [
            {"type": "text", "string_value": "orphaned"},
            {"name": "Good Field", "type": "text", "string_value": "value"},
        ]
    }
    result = tacticalrmm.extract_trmm_custom_fields(agent)
    assert len(result) == 1
    assert "Good Field" in result


def test_extract_trmm_custom_fields_checkbox_string_value_true():
    """Checkbox where bool_value is None but value is string 'true'."""
    agent = {
        "custom_fields": [
            {
                "name": "Enabled",
                "type": "checkbox",
                "bool_value": None,
                "value": "true",
            }
        ]
    }
    result = tacticalrmm.extract_trmm_custom_fields(agent)
    assert result["Enabled"]["value"] is True


# ---------------------------------------------------------------------------
# Tests for fetch_agent_installed_software
# ---------------------------------------------------------------------------


def test_fetch_agent_installed_software_list_response(monkeypatch):
    """List response returns software names."""

    async def fake_call_endpoint(endpoint: str):
        assert "software/agent-001/" in endpoint
        return [
            {"id": 1, "name": "Microsoft Office", "version": "2021"},
            {"id": 2, "name": "Bitdefender Total Security", "version": "7.0"},
            {"id": 3, "name": "Google Chrome", "version": "120.0"},
        ]

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    names = asyncio.run(tacticalrmm.fetch_agent_installed_software("agent-001"))

    assert "Microsoft Office" in names
    assert "Bitdefender Total Security" in names
    assert "Google Chrome" in names
    assert len(names) == 3


def test_fetch_agent_installed_software_paginated_response(monkeypatch):
    """Paginated response with 'results' key returns software names."""

    async def fake_call_endpoint(endpoint: str):
        return {
            "count": 2,
            "results": [
                {"name": "App One"},
                {"name": "App Two"},
            ],
        }

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    names = asyncio.run(tacticalrmm.fetch_agent_installed_software("agent-002"))

    assert "App One" in names
    assert "App Two" in names


def test_fetch_agent_installed_software_api_error_returns_empty(monkeypatch):
    """API error returns empty list without raising."""

    async def fake_call_endpoint(endpoint: str):
        raise tacticalrmm.TacticalRMMAPIError("not found")

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    names = asyncio.run(tacticalrmm.fetch_agent_installed_software("agent-bad"))

    assert names == []


def test_fetch_agent_installed_software_empty_response(monkeypatch):
    """Empty list response returns empty list."""

    async def fake_call_endpoint(endpoint: str):
        return []

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    names = asyncio.run(tacticalrmm.fetch_agent_installed_software("agent-003"))

    assert names == []


# ---------------------------------------------------------------------------
# Tests for _sync_tactical_asset_custom_fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_non_checkbox_field_imports_text_value():
    """Non-checkbox field gets its text value imported from TRMM."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 1, "name": "Location", "field_type": "text"},
    ]
    agent = {
        "custom_fields": [
            {"name": "Location", "type": "text", "string_value": "Server Room A"},
        ]
    }

    set_calls = []

    async def fake_set_value(**kwargs):
        set_calls.append(kwargs)

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock(side_effect=fake_set_value)):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert len(set_calls) == 1
    assert set_calls[0]["asset_id"] == 10
    assert set_calls[0]["field_definition_id"] == 1
    assert set_calls[0]["value_text"] == "Server Room A"


@pytest.mark.asyncio
async def test_sync_non_checkbox_field_skipped_when_no_trmm_match():
    """Non-checkbox field with no matching TRMM field is skipped."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 1, "name": "Location", "field_type": "text"},
    ]
    agent = {"custom_fields": []}  # No matching field

    set_calls = []

    async def fake_set_value(**kwargs):
        set_calls.append(kwargs)

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock(side_effect=fake_set_value)):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert len(set_calls) == 0


@pytest.mark.asyncio
async def test_sync_checkbox_from_trmm_checkbox_true():
    """MyPortal checkbox field syncs from TRMM checkbox field (True)."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 2, "name": "Bitdefender", "field_type": "checkbox"},
    ]
    agent = {
        "custom_fields": [
            {"name": "Bitdefender", "type": "checkbox", "bool_value": True},
        ]
    }

    set_calls = []

    async def fake_set_value(**kwargs):
        set_calls.append(kwargs)

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock(side_effect=fake_set_value)):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert len(set_calls) == 1
    assert set_calls[0]["value_boolean"] is True


@pytest.mark.asyncio
async def test_sync_checkbox_from_trmm_checkbox_false():
    """MyPortal checkbox field syncs from TRMM checkbox field (False) - unchecks."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 2, "name": "Bitdefender", "field_type": "checkbox"},
    ]
    agent = {
        "custom_fields": [
            {"name": "Bitdefender", "type": "checkbox", "bool_value": False},
        ]
    }

    set_calls = []

    async def fake_set_value(**kwargs):
        set_calls.append(kwargs)

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock(side_effect=fake_set_value)):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert len(set_calls) == 1
    assert set_calls[0]["value_boolean"] is False


@pytest.mark.asyncio
async def test_sync_checkbox_from_trmm_text_exact_match():
    """MyPortal checkbox checked when TRMM text value exactly matches field name."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 3, "name": "Sophos", "field_type": "checkbox"},
    ]
    agent = {
        "custom_fields": [
            {"name": "Sophos", "type": "text", "string_value": "Sophos"},
        ]
    }

    set_calls = []

    async def fake_set_value(**kwargs):
        set_calls.append(kwargs)

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock(side_effect=fake_set_value)):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert len(set_calls) == 1
    assert set_calls[0]["value_boolean"] is True


@pytest.mark.asyncio
async def test_sync_checkbox_from_trmm_text_no_match():
    """MyPortal checkbox NOT checked when TRMM text value does not match field name."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 3, "name": "Sophos", "field_type": "checkbox"},
    ]
    agent = {
        "custom_fields": [
            {"name": "Sophos", "type": "text", "string_value": "Not installed"},
        ]
    }

    set_calls = []

    async def fake_set_value(**kwargs):
        set_calls.append(kwargs)

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock(side_effect=fake_set_value)):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert len(set_calls) == 1
    assert set_calls[0]["value_boolean"] is False


@pytest.mark.asyncio
async def test_sync_checkbox_from_installed_software_match():
    """MyPortal checkbox checked when field name matches installed software."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 4, "name": "Bitdefender Total Security", "field_type": "checkbox"},
    ]
    agent = {"custom_fields": []}  # No matching TRMM custom field

    set_calls = []

    async def fake_set_value(**kwargs):
        set_calls.append(kwargs)

    async def fake_fetch_software(agent_id: str):
        return ["Microsoft Office", "Bitdefender Total Security", "Google Chrome"]

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock(side_effect=fake_set_value)), \
         patch.object(tacticalrmm, "fetch_agent_installed_software", new=fake_fetch_software):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert len(set_calls) == 1
    assert set_calls[0]["value_boolean"] is True


@pytest.mark.asyncio
async def test_sync_checkbox_from_installed_software_no_match_unchecks():
    """MyPortal checkbox unchecked when field name NOT in installed software."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 4, "name": "Bitdefender Total Security", "field_type": "checkbox"},
    ]
    agent = {"custom_fields": []}

    set_calls = []

    async def fake_set_value(**kwargs):
        set_calls.append(kwargs)

    async def fake_fetch_software(agent_id: str):
        return ["Microsoft Office", "Google Chrome"]  # Bitdefender NOT installed

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock(side_effect=fake_set_value)), \
         patch.object(tacticalrmm, "fetch_agent_installed_software", new=fake_fetch_software):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert len(set_calls) == 1
    assert set_calls[0]["value_boolean"] is False


@pytest.mark.asyncio
async def test_sync_checkbox_software_fetched_once_for_multiple_fields():
    """Installed software is fetched only once even when multiple checkbox fields lack TRMM match."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 1, "name": "App One", "field_type": "checkbox"},
        {"id": 2, "name": "App Two", "field_type": "checkbox"},
        {"id": 3, "name": "App Three", "field_type": "checkbox"},
    ]
    agent = {"custom_fields": []}

    fetch_count = {"n": 0}

    async def fake_fetch_software(agent_id: str):
        fetch_count["n"] += 1
        return ["App One", "App Three"]

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock()), \
         patch.object(tacticalrmm, "fetch_agent_installed_software", new=fake_fetch_software):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert fetch_count["n"] == 1, "Software should be fetched exactly once"


@pytest.mark.asyncio
async def test_sync_checkbox_case_insensitive_software_match():
    """Software name matching is case-insensitive."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 1, "name": "bitdefender total security", "field_type": "checkbox"},
    ]
    agent = {"custom_fields": []}

    set_calls = []

    async def fake_set_value(**kwargs):
        set_calls.append(kwargs)

    async def fake_fetch_software(agent_id: str):
        return ["Bitdefender Total Security"]  # different casing

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock(side_effect=fake_set_value)), \
         patch.object(tacticalrmm, "fetch_agent_installed_software", new=fake_fetch_software):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert set_calls[0]["value_boolean"] is True


@pytest.mark.asyncio
async def test_sync_no_field_definitions_does_nothing():
    """When no custom field definitions exist, sync is a no-op."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=[])), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock()) as mock_set:
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent={},
        )

    mock_set.assert_not_called()


@pytest.mark.asyncio
async def test_sync_custom_field_error_does_not_abort_import():
    """A failure in custom field sync is caught and does not abort the import."""
    from app.services import asset_importer
    from app.repositories import assets as assets_repo
    from app.repositories import companies as company_repo

    agent = {
        "agent_id": "agent-001",
        "hostname": "PC-ONE",
        "operating_system": "Windows 10",
    }

    async def fake_fetch_agents(client_id):
        return [agent]

    async def fake_upsert_asset(**kwargs):
        return 42

    async def fake_get_company(company_id):
        return {"id": 1, "tacticalrmm_client_id": "client-1"}

    async def fake_sync_fields(asset_id, trmm_agent_id, agent_data):
        raise RuntimeError("Custom field sync exploded")

    with patch.object(tacticalrmm, "fetch_agents", new=fake_fetch_agents), \
         patch.object(assets_repo, "upsert_asset", new=fake_upsert_asset), \
         patch.object(company_repo, "get_company_by_id", new=fake_get_company), \
         patch.object(asset_importer, "_sync_tactical_asset_custom_fields", new=fake_sync_fields):
        result = await asset_importer.import_tactical_assets_for_company(1)

    # Import should still succeed even though custom field sync failed
    assert result == 1


@pytest.mark.asyncio
async def test_sync_mixed_field_types():
    """Mix of text, date, and checkbox fields are all handled correctly."""
    from app.services.asset_importer import _sync_tactical_asset_custom_fields
    from app.repositories import asset_custom_fields as acf_repo

    field_defs = [
        {"id": 1, "name": "Location", "field_type": "text"},
        {"id": 2, "name": "Purchase Date", "field_type": "date"},
        {"id": 3, "name": "Bitdefender", "field_type": "checkbox"},
    ]
    agent = {
        "custom_fields": [
            {"name": "Location", "type": "text", "string_value": "HQ"},
            {"name": "Purchase Date", "type": "text", "string_value": "2024-01-15"},
            {"name": "Bitdefender", "type": "checkbox", "bool_value": True},
        ]
    }

    set_calls = []

    async def fake_set_value(**kwargs):
        set_calls.append(kwargs)

    with patch.object(acf_repo, "list_field_definitions", new=AsyncMock(return_value=field_defs)), \
         patch.object(acf_repo, "set_asset_field_value", new=AsyncMock(side_effect=fake_set_value)):
        await _sync_tactical_asset_custom_fields(
            asset_id=10,
            trmm_agent_id="agent-001",
            agent=agent,
        )

    assert len(set_calls) == 3

    location_call = next(c for c in set_calls if c["field_definition_id"] == 1)
    assert location_call["value_text"] == "HQ"

    date_call = next(c for c in set_calls if c["field_definition_id"] == 2)
    assert date_call["value_date"] == "2024-01-15"

    checkbox_call = next(c for c in set_calls if c["field_definition_id"] == 3)
    assert checkbox_call["value_boolean"] is True

