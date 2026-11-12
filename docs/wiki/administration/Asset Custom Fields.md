# Asset Custom Fields Feature

## Overview

This feature adds support for custom fields on assets, allowing administrators to define and manage additional metadata for all assets in the system.

## Key Features

### 1. Global Custom Field Definitions
- Administrators can create custom field definitions that apply to all assets
- Supported field types:
  - **Text**: Free-form text input
  - **Image**: URL to an image
  - **Checkbox**: Boolean value (yes/no)
  - **URL**: Web link with validation
  - **Date**: Calendar date picker

### 2. Settings Page
- Access via the "Settings" button in the assets page header (right-aligned)
- Only visible to super administrators
- Manage all custom field definitions:
  - Create new field definitions
  - Edit existing definitions (name, type, display order)
  - Delete definitions (cascades to all asset values)
  - Reorder fields by display order

### Columns end users can see
- On the same Settings page, **Columns end users can see** lists every Assets
  list column (for example *Serial number* or *Health check score*) and every
  custom field. Untick a column and save to hide it from end users.
- Hidden custom fields are also left off end users' asset pages and are not
  returned or changeable for them through the asset custom-field API; saving
  an asset as an end user leaves their stored values unchanged.
- *Name* is always shown. Super admins and helpdesk technicians always see
  every built-in column.
- Custom field columns, and the *Custom fields* section of the asset page,
  are shown to admins and technicians: super admins, company admins, the
  technician permission and helpdesk technicians. Other roles do not see
  custom fields.

### Asset access levels
Admins and technicians (as above) keep full asset management with Read Write
access. For every other role:
- **Read Only** sees the same asset list and asset page as Read Write, with
  every input read-only.
- **Read Write** can additionally edit the asset's *Profile*, and photos when
  the *Asset photos* permission allows it.
- The inventory *Asset type* override, *Custom fields*, *Data provenance*
  and *Archive & restore* are hidden, and warranty dates and possible
  integration matches are read-only.

*Customer visibility* is managed by super admins only.

### 3. Asset-Level Field Values
- Each asset can have values for all defined custom fields
- Access via the "Edit" button on each asset row
- Modal interface for editing field values
- Values are saved per asset

## API Endpoints

### Field Definitions
- `GET /asset-custom-fields/definitions` - List all field definitions
- `POST /asset-custom-fields/definitions` - Create new definition
- `GET /asset-custom-fields/definitions/{id}` - Get single definition
- `PUT /asset-custom-fields/definitions/{id}` - Update definition
- `DELETE /asset-custom-fields/definitions/{id}` - Delete definition

### Asset Field Values
- `GET /assets/{asset_id}/custom-fields` - Get all field values for an asset
- `POST /assets/{asset_id}/custom-fields` - Set field values for an asset
- `DELETE /assets/{asset_id}/custom-fields/{field_id}` - Delete a field value

## Database Schema

### asset_custom_field_definitions
- `id` - Primary key
- `name` - Field name (unique)
- `field_type` - Enum: text, image, checkbox, url, date
- `display_order` - Integer for sorting
- `created_at` - Timestamp
- `updated_at` - Timestamp

### asset_custom_field_values
- `id` - Primary key
- `asset_id` - Foreign key to assets (cascade delete)
- `field_definition_id` - Foreign key to definitions (cascade delete)
- `value_text` - Text/URL/Image values
- `value_date` - Date values
- `value_boolean` - Checkbox values
- `created_at` - Timestamp
- `updated_at` - Timestamp
- Unique constraint on (asset_id, field_definition_id)

## Usage

### For Administrators

1. **Configure Custom Fields**:
   - Navigate to Assets page
   - Click the "Settings" button (gear icon, right side of header)
   - Click "Add Custom Field"
   - Enter field name, select type, set display order
   - Click "Save Field"

2. **Edit Asset Custom Fields**:
   - Navigate to Assets page
   - Click "Edit" button on any asset row
   - Fill in values for the custom fields
   - Click "Save Changes"

### For API Users

See the Swagger documentation at `/docs` for complete API reference and examples.

## Testing

The feature includes comprehensive test coverage:
- Repository layer tests (CRUD operations)
- Schema validation tests
- Field type handling tests
- All tests pass successfully

Run tests with:
```bash
pytest tests/test_asset_custom_fields.py -v
```
