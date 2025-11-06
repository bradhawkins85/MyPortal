"""Tests for asset custom fields functionality."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_list_field_definitions_returns_ordered_list():
    """Test that field definitions are returned in display order."""
    from app.repositories import asset_custom_fields
    
    mock_rows = [
        {
            "id": 1,
            "name": "Location",
            "field_type": "text",
            "display_order": 0,
            "created_at": "2024-01-01 00:00:00",
            "updated_at": "2024-01-01 00:00:00",
        },
        {
            "id": 2,
            "name": "Purchase Date",
            "field_type": "date",
            "display_order": 1,
            "created_at": "2024-01-01 00:00:00",
            "updated_at": "2024-01-01 00:00:00",
        },
    ]
    
    with patch.object(asset_custom_fields.db, 'fetch_all', new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_rows
        
        result = await asset_custom_fields.list_field_definitions()
        
        assert len(result) == 2
        assert result[0]["name"] == "Location"
        assert result[1]["name"] == "Purchase Date"
        mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_get_field_definition_returns_single_definition():
    """Test that a single field definition is returned by ID."""
    from app.repositories import asset_custom_fields
    
    mock_row = {
        "id": 1,
        "name": "Location",
        "field_type": "text",
        "display_order": 0,
        "created_at": "2024-01-01 00:00:00",
        "updated_at": "2024-01-01 00:00:00",
    }
    
    with patch.object(asset_custom_fields.db, 'fetch_one', new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_row
        
        result = await asset_custom_fields.get_field_definition(1)
        
        assert result is not None
        assert result["name"] == "Location"
        assert result["field_type"] == "text"


@pytest.mark.asyncio
async def test_create_field_definition_inserts_record():
    """Test that a new field definition is created."""
    from app.repositories import asset_custom_fields
    
    with patch.object(asset_custom_fields.db, 'execute', new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = 123
        
        result = await asset_custom_fields.create_field_definition(
            name="Test Field",
            field_type="text",
            display_order=5,
        )
        
        assert result == 123
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args[0]
        assert "INSERT INTO asset_custom_field_definitions" in call_args[0]


@pytest.mark.asyncio
async def test_update_field_definition_updates_record():
    """Test that a field definition is updated."""
    from app.repositories import asset_custom_fields
    
    with patch.object(asset_custom_fields.db, 'execute', new_callable=AsyncMock) as mock_execute:
        await asset_custom_fields.update_field_definition(
            definition_id=1,
            name="Updated Name",
            display_order=10,
        )
        
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args[0]
        assert "UPDATE asset_custom_field_definitions" in call_args[0]


@pytest.mark.asyncio
async def test_delete_field_definition_removes_record():
    """Test that a field definition is deleted."""
    from app.repositories import asset_custom_fields
    
    with patch.object(asset_custom_fields.db, 'execute', new_callable=AsyncMock) as mock_execute:
        await asset_custom_fields.delete_field_definition(1)
        
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args[0]
        assert "DELETE FROM asset_custom_field_definitions" in call_args[0]
        assert call_args[1] == (1,)


@pytest.mark.asyncio
async def test_get_asset_field_values_returns_values_with_definitions():
    """Test that asset field values are returned with definition info."""
    from app.repositories import asset_custom_fields
    
    mock_rows = [
        {
            "id": 1,
            "asset_id": 100,
            "field_definition_id": 1,
            "value_text": "Server Room A",
            "value_date": None,
            "value_boolean": None,
            "field_name": "Location",
            "field_type": "text",
            "display_order": 0,
        },
    ]
    
    with patch.object(asset_custom_fields.db, 'fetch_all', new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_rows
        
        result = await asset_custom_fields.get_asset_field_values(100)
        
        assert len(result) == 1
        assert result[0]["value_text"] == "Server Room A"
        assert result[0]["field_name"] == "Location"


@pytest.mark.asyncio
async def test_set_asset_field_value_creates_new_value():
    """Test that a new asset field value is created."""
    from app.repositories import asset_custom_fields
    
    with patch.object(asset_custom_fields.db, 'fetch_one', new_callable=AsyncMock) as mock_fetch, \
         patch.object(asset_custom_fields.db, 'execute', new_callable=AsyncMock) as mock_execute:
        mock_fetch.return_value = None  # No existing value
        
        await asset_custom_fields.set_asset_field_value(
            asset_id=100,
            field_definition_id=1,
            value_text="Test Value",
        )
        
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args[0]
        assert "INSERT INTO asset_custom_field_values" in call_args[0]


@pytest.mark.asyncio
async def test_set_asset_field_value_updates_existing_value():
    """Test that an existing asset field value is updated."""
    from app.repositories import asset_custom_fields
    
    mock_existing = {"id": 1}
    
    with patch.object(asset_custom_fields.db, 'fetch_one', new_callable=AsyncMock) as mock_fetch, \
         patch.object(asset_custom_fields.db, 'execute', new_callable=AsyncMock) as mock_execute:
        mock_fetch.return_value = mock_existing
        
        await asset_custom_fields.set_asset_field_value(
            asset_id=100,
            field_definition_id=1,
            value_text="Updated Value",
        )
        
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args[0]
        assert "UPDATE asset_custom_field_values" in call_args[0]


@pytest.mark.asyncio
async def test_delete_asset_field_value_removes_value():
    """Test that an asset field value is deleted."""
    from app.repositories import asset_custom_fields
    
    with patch.object(asset_custom_fields.db, 'execute', new_callable=AsyncMock) as mock_execute:
        await asset_custom_fields.delete_asset_field_value(100, 1)
        
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args[0]
        assert "DELETE FROM asset_custom_field_values" in call_args[0]


def test_field_type_enum_values():
    """Test that field type enum has expected values."""
    from app.schemas.asset_custom_fields import FieldType
    
    assert FieldType.TEXT.value == "text"
    assert FieldType.IMAGE.value == "image"
    assert FieldType.CHECKBOX.value == "checkbox"
    assert FieldType.URL.value == "url"
    assert FieldType.DATE.value == "date"


def test_field_definition_create_schema_validation():
    """Test that FieldDefinitionCreate validates correctly."""
    from app.schemas.asset_custom_fields import FieldDefinitionCreate, FieldType
    
    # Valid data
    definition = FieldDefinitionCreate(
        name="Test Field",
        field_type=FieldType.TEXT,
        display_order=5,
    )
    assert definition.name == "Test Field"
    assert definition.field_type == FieldType.TEXT
    assert definition.display_order == 5
    
    # Test default display_order
    definition2 = FieldDefinitionCreate(
        name="Another Field",
        field_type=FieldType.DATE,
    )
    assert definition2.display_order == 0


def test_asset_field_value_from_db_row_text():
    """Test AssetFieldValue.from_db_row for text field."""
    from app.schemas.asset_custom_fields import AssetFieldValue
    
    row = {
        "id": 1,
        "asset_id": 100,
        "field_definition_id": 1,
        "value_text": "Test Value",
        "value_date": None,
        "value_boolean": None,
        "field_name": "Location",
        "field_type": "text",
        "display_order": 0,
    }
    
    value = AssetFieldValue.from_db_row(row)
    assert value.value == "Test Value"
    assert value.field_name == "Location"
    assert value.field_type.value == "text"


def test_asset_field_value_from_db_row_checkbox():
    """Test AssetFieldValue.from_db_row for checkbox field."""
    from app.schemas.asset_custom_fields import AssetFieldValue
    
    row = {
        "id": 2,
        "asset_id": 100,
        "field_definition_id": 2,
        "value_text": None,
        "value_date": None,
        "value_boolean": True,
        "field_name": "Active",
        "field_type": "checkbox",
        "display_order": 1,
    }
    
    value = AssetFieldValue.from_db_row(row)
    assert value.value is True
    assert value.field_name == "Active"
    assert value.field_type.value == "checkbox"


def test_asset_field_value_from_db_row_date():
    """Test AssetFieldValue.from_db_row for date field."""
    from datetime import date
    from app.schemas.asset_custom_fields import AssetFieldValue
    
    test_date = date(2024, 1, 15)
    row = {
        "id": 3,
        "asset_id": 100,
        "field_definition_id": 3,
        "value_text": None,
        "value_date": test_date,
        "value_boolean": None,
        "field_name": "Purchase Date",
        "field_type": "date",
        "display_order": 2,
    }
    
    value = AssetFieldValue.from_db_row(row)
    assert value.value == test_date
    assert value.field_name == "Purchase Date"
    assert value.field_type.value == "date"
