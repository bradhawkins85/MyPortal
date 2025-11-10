# BCP Template API Documentation

## Overview

The BCP Template Discovery and Mapping feature provides a comprehensive JSON schema for Business Continuity Plans that follow government standards. This schema can be used to:

- Understand the structure of a Business Continuity Plan
- Generate forms and data collection interfaces
- Validate plan completeness
- Build automated reporting tools

## API Endpoint

### GET /api/business-continuity-plans/template/default

Returns the default government BCP template schema.

**Authentication Required:** Yes

**Response:** `BCPTemplateSchema` (JSON)

## Example Usage

```bash
curl -X GET "https://your-domain/api/business-continuity-plans/template/default" \
  -H "Cookie: myportal_session=YOUR_SESSION_TOKEN"
```

## Template Structure

The template includes 12 main sections:

1. **Plan Overview** - Purpose, scope, objectives, assumptions
2. **Governance & Roles** - Roles, responsibilities, escalation matrix, approvals
3. **Business Impact Analysis (BIA)** - Critical processes, impact categories, RTO, RPO, MTPD, dependencies
4. **Risk Assessment** - Threats, likelihood, impact, risk rating, mitigations
5. **Recovery Strategies** - Process-level strategies, workarounds, resource needs
6. **Incident Response** - Activation criteria, notification tree, procedures
7. **Communications Plan** - Internal, external, regulators, media, templates
8. **IT/Systems Recovery** - Applications, infrastructure, DR runbooks, backup/restore, test cadence
9. **Testing & Exercises** - Schedule, scenarios, evidence, outcomes
10. **Maintenance & Review** - Review cadence, owners, change process
11. **Appendices** - Contact lists, vendor SLAs, site info, inventories, floor plans
12. **Revision History** - Version, author, date, summary

## Supported Field Types

The template supports 14 different field types:

- `text` - Single-line text input
- `rich_text` - Multi-line HTML text editor
- `date` - Date picker
- `datetime` - Date and time picker
- `select` - Single selection dropdown
- `multiselect` - Multiple selection dropdown
- `integer` - Whole number input
- `decimal` - Decimal number input
- `boolean` - Checkbox/toggle
- `table` - Table with defined columns
- `file` - File upload
- `contact_ref` - Reference to a contact
- `user_ref` - Reference to a user
- `url` - URL/hyperlink input

## Example Response Structure

```json
{
  "metadata": {
    "template_name": "Government Business Continuity Plan",
    "template_version": "1.0",
    "description": "Comprehensive business continuity plan template following government standards",
    "requires_approval": true,
    "approval_workflow": "Plans require approval from business unit head, risk management, and executive sponsor",
    "revision_tracking": true,
    "attachments_required": [
      "Emergency Contact List",
      "Vendor SLA Documentation",
      "Site Information Sheets",
      "System Inventory",
      "Floor Plans and Facility Diagrams"
    ]
  },
  "sections": [
    {
      "section_id": "plan_overview",
      "title": "Plan Overview",
      "description": "High-level overview of the business continuity plan",
      "order": 1,
      "parent_section_id": null,
      "fields": [
        {
          "field_id": "purpose",
          "label": "Purpose",
          "field_type": "rich_text",
          "required": true,
          "help_text": "Describe the purpose and objectives of this business continuity plan",
          "choices": null,
          "min_value": null,
          "max_value": null,
          "default_value": null,
          "columns": null,
          "is_computed": false,
          "computation_note": null
        },
        {
          "field_id": "scope",
          "label": "Scope",
          "field_type": "rich_text",
          "required": true,
          "help_text": "Define what business units, processes, and systems are covered by this plan",
          "choices": null,
          "min_value": null,
          "max_value": null,
          "default_value": null,
          "columns": null,
          "is_computed": false,
          "computation_note": null
        }
      ]
    }
  ]
}
```

## Table Field Example

Table fields include column definitions with their own field types and validation:

```json
{
  "field_id": "critical_processes",
  "label": "Critical Business Processes",
  "field_type": "table",
  "required": true,
  "help_text": "Identify and analyze critical business processes",
  "columns": [
    {
      "column_id": "process_name",
      "label": "Process Name",
      "field_type": "text",
      "required": true,
      "help_text": null,
      "choices": null,
      "min_value": null,
      "max_value": null,
      "is_computed": false
    },
    {
      "column_id": "rto",
      "label": "RTO (Recovery Time Objective)",
      "field_type": "text",
      "required": true,
      "help_text": "Maximum acceptable downtime, e.g., '4 hours', '24 hours'",
      "choices": null,
      "min_value": null,
      "max_value": null,
      "is_computed": false
    },
    {
      "column_id": "financial_impact",
      "label": "Financial Impact",
      "field_type": "select",
      "required": true,
      "help_text": null,
      "choices": [
        {"value": "critical", "label": "Critical (>$1M/day)"},
        {"value": "high", "label": "High ($100K-$1M/day)"},
        {"value": "medium", "label": "Medium ($10K-$100K/day)"},
        {"value": "low", "label": "Low (<$10K/day)"}
      ],
      "min_value": null,
      "max_value": null,
      "is_computed": false
    }
  ]
}
```

## Computed Fields

Some fields are marked as computed, indicating they should be automatically calculated or populated:

```json
{
  "field_id": "revision_history_table",
  "label": "Revision History",
  "field_type": "table",
  "required": true,
  "is_computed": true,
  "computation_note": "Automatically populated from plan update history"
}
```

## Integration Notes

### Using the Template for Form Generation

The template schema can be used to dynamically generate forms:

1. Iterate through sections in order
2. For each section, render its fields according to field_type
3. Apply validation rules based on required, min_value, max_value
4. For select/multiselect fields, use the choices array
5. For table fields, render a grid with columns as headers

### Validation

- Check `required` field to ensure mandatory data is provided
- For select fields, validate against the `choices` array
- For numeric fields, validate against `min_value` and `max_value`
- For computed fields, ensure automation is in place to calculate values

## Testing

The template has comprehensive test coverage:

```bash
# Run template structure tests
pytest tests/test_bcp_template.py -v

# Run API endpoint tests
pytest tests/test_bcp_template_api.py -v
```

## Extending the Template

To customize the template for your organization:

1. Modify `app/services/bcp_template.py`
2. Add/remove sections or fields as needed
3. Update field types, choices, and validation rules
4. Run tests to ensure schema validity
5. Consider versioning your template changes

## Related Documentation

- Business Continuity Plans API: `/docs#/Business Continuity Plans`
- Schema Definitions: `app/schemas/bcp_template.py`
- Service Implementation: `app/services/bcp_template.py`
