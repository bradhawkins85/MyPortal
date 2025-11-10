# BC17: Default Template Bootstrap Implementation

## Overview

This document describes the BC17 implementation for creating and loading a default Business Continuity Plan (BCP) template instance into the database. The bootstrap process ensures that the default government BCP template is available immediately upon application startup.

## Features

### Automatic Bootstrap on Startup

The default template is automatically loaded into the database when the application starts. The bootstrap process:

1. Checks if a default template already exists
2. If not, creates the template from the schema definition
3. Stores the complete template structure in JSON format
4. Logs success or failure (without breaking startup)

### Template Structure

The bootstrapped template includes:

- **Complete section structure** following government BCP standards
- **12 major sections** in proper order:
  1. Plan Overview
  2. Governance & Roles
  3. Business Impact Analysis (BIA)
  4. Risk Assessment
  5. Recovery Strategies
  6. Incident Response
  7. Communications Plan
  8. IT/Systems Recovery
  9. Testing & Exercises
  10. Maintenance & Review
  11. Appendices
  12. Revision History

- **Field definitions** with proper types and validation
- **Table schemas** for structured data:
  - BIA: Critical processes with RTO, RPO, MTPD
  - Risk Assessment: Threats, likelihood, impact, mitigations
  - Contacts: Notification tree with contact information
  - Vendors: Dependencies on third-party vendors

- **Default placeholders**: Help text for all fields
- **Required/optional flags**: Proper validation rules

## Usage

### Automatic Bootstrap

The template is automatically bootstrapped on application startup. No manual intervention is required.

### Manual Bootstrap via API

You can manually trigger the bootstrap using the API endpoint:

```bash
POST /api/bc/templates/bootstrap-default
```

**Authorization**: Requires BC admin role

**Response**: Returns the created or existing template (idempotent)

```json
{
  "id": 1,
  "name": "Government Business Continuity Plan",
  "version": "1.0",
  "is_default": true,
  "schema_json": {
    "metadata": {
      "template_name": "Government Business Continuity Plan",
      "template_version": "1.0",
      "requires_approval": true,
      "revision_tracking": true,
      "attachments_required": [...]
    },
    "sections": [...]
  },
  "created_at": "2024-01-01T00:00:00",
  "updated_at": "2024-01-01T00:00:00"
}
```

### Retrieving the Default Template

Get the default template:

```bash
GET /api/bc/templates?is_default=true
```

Or get template by ID:

```bash
GET /api/bc/templates/{template_id}
```

### Using the Template for Plans

Once bootstrapped, the template can be used as the basis for creating new BCP plans:

```bash
POST /api/bc/plans
{
  "title": "Company XYZ Business Continuity Plan",
  "template_id": 1,
  ...
}
```

## Implementation Details

### Bootstrap Function

Location: `app/services/bcp_template.py`

```python
async def bootstrap_default_template() -> dict[str, Any]:
    """
    Bootstrap the default government BCP template into the database.
    
    This function loads the default template schema and stores it in the database
    if it doesn't already exist. It ensures that the template instance matches
    the discovered/mapped schema with proper section order, field labels,
    default placeholders, and table schemas.
    
    Returns:
        dict: The created or existing template record from the database
    """
```

### Startup Hook

Location: `app/main.py`

```python
@app.on_event("startup")
async def on_startup() -> None:
    await db.connect()
    await db.run_migrations()
    await change_log_service.sync_change_log_sources()
    await modules_service.ensure_default_modules()
    await automations_service.refresh_all_schedules()
    
    # Bootstrap default BCP template if it doesn't exist
    try:
        from app.services.bcp_template import bootstrap_default_template
        await bootstrap_default_template()
        log_info("BCP default template bootstrapped")
    except Exception as exc:
        log_error("Failed to bootstrap default BCP template", error=str(exc))
    
    await scheduler_service.start()
    log_info("Application started", environment=settings.environment)
```

## Database Schema

The template is stored in the `bc_template` table:

```sql
CREATE TABLE bc_template (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  version VARCHAR(50) NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  schema_json JSON COMMENT 'Section and field definitions as JSON',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_bc_template_default (is_default)
);
```

## Testing

Comprehensive test suite ensures bootstrap functionality:

- `tests/test_bcp_template_bootstrap.py`: 9 tests
- `tests/test_bcp_template.py`: 14 tests  
- `tests/test_bcp_template_api.py`: 9 tests

**Total**: 32 tests, all passing ✅

Test coverage includes:
- Template creation when none exists
- Returns existing template (idempotency)
- Schema structure validation
- BIA table with RTO/RPO/MTPD columns
- Risk assessment table structure
- Contact list (notification tree)
- Vendor dependencies
- Section ordering
- Default placeholders

## Requirements Met

✅ **Section order matching the template**: All 12 sections in correct order  
✅ **Field labels consistent with the document**: Labels match BCP standards  
✅ **Default text placeholders**: Help text provided for guidance  
✅ **Table schemas for BIA, Risk, Contacts, Vendors**: All tables included

## Troubleshooting

### Template Not Created

If the template is not being created:

1. Check database connectivity
2. Check database migrations have run
3. Review application logs for bootstrap errors
4. Manually trigger bootstrap via API endpoint

### Multiple Default Templates

Only one default template should exist. If multiple exist:

1. Query templates: `SELECT * FROM bc_template WHERE is_default = TRUE`
2. Determine which to keep
3. Update others: `UPDATE bc_template SET is_default = FALSE WHERE id != <keep_id>`

### Schema Changes

If the template schema is updated:

1. The bootstrap function creates templates only once (on first run)
2. To update existing templates, use the update endpoint or create a new version
3. Consider creating a migration script for existing templates

## Future Enhancements

Possible improvements:
- Support for multiple template versions
- Template inheritance and customization
- DOCX template upload and parsing
- Automatic section mapping from uploaded documents
- Template comparison and diff tools
