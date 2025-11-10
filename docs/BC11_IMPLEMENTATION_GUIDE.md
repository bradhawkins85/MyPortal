# BC11: Supportive Entities Implementation Guide

## Overview

BC11 implements supportive entities for business continuity plans, including:
- **Contacts**: Emergency contacts and key personnel
- **Vendors**: Service providers, suppliers, and external parties with SLA tracking
- **Processes**: Critical business processes with dependencies

## API Endpoints

All endpoints are under `/api/bc/plans/{plan_id}/` and require BC RBAC permissions:
- **Viewer role**: Read access (GET)
- **Editor role**: Full CRUD access (GET, POST, PUT, DELETE)

### Contacts

```
GET    /api/bc/plans/{plan_id}/contacts
POST   /api/bc/plans/{plan_id}/contacts
GET    /api/bc/plans/{plan_id}/contacts/{contact_id}
PUT    /api/bc/plans/{plan_id}/contacts/{contact_id}
DELETE /api/bc/plans/{plan_id}/contacts/{contact_id}
```

**Example Contact:**
```json
{
  "plan_id": 1,
  "name": "John Smith",
  "role": "Emergency Coordinator",
  "phone": "+1-555-0123",
  "email": "john.smith@example.com",
  "notes": "Primary contact for incident response, available 24/7"
}
```

### Vendors

```
GET    /api/bc/plans/{plan_id}/vendors
POST   /api/bc/plans/{plan_id}/vendors
GET    /api/bc/plans/{plan_id}/vendors/{vendor_id}
PUT    /api/bc/plans/{plan_id}/vendors/{vendor_id}
DELETE /api/bc/plans/{plan_id}/vendors/{vendor_id}
```

**Example Vendor:**
```json
{
  "plan_id": 1,
  "name": "Cloud Services Inc",
  "vendor_type": "Cloud Provider",
  "contact_name": "Support Team",
  "contact_email": "support@cloudservices.com",
  "contact_phone": "+1-800-CLOUD",
  "sla_notes": "99.9% uptime guarantee\n4-hour response time for critical issues\n24/7 support available\nMonthly service credits for downtime",
  "contract_reference": "CONTRACT-2024-001",
  "criticality": "critical"
}
```

**Vendor Fields:**
- `name`: Vendor name (required)
- `vendor_type`: Type of vendor (e.g., "IT Service Provider", "Supplier", "Cloud Provider")
- `contact_name`: Primary contact at vendor
- `contact_email`: Vendor contact email
- `contact_phone`: Vendor contact phone
- `sla_notes`: Service Level Agreement details (use for response times, uptime guarantees, support hours, etc.)
- `contract_reference`: Contract number or reference for tracking
- `criticality`: Vendor criticality rating (e.g., "critical", "high", "medium", "low")

### Processes

```
GET    /api/bc/plans/{plan_id}/processes
POST   /api/bc/plans/{plan_id}/processes
GET    /api/bc/plans/{plan_id}/processes/{process_id}
PUT    /api/bc/plans/{plan_id}/processes/{process_id}
DELETE /api/bc/plans/{plan_id}/processes/{process_id}
```

**Example Process:**
```json
{
  "plan_id": 1,
  "name": "Customer Order Processing",
  "description": "Critical process for handling online customer orders",
  "rto_minutes": 60,
  "rpo_minutes": 15,
  "mtpd_minutes": 240,
  "impact_rating": "critical",
  "dependencies_json": {
    "systems": [
      {
        "type": "system",
        "id": 1,
        "name": "CRM System",
        "criticality": "high",
        "notes": "Primary customer database"
      },
      {
        "type": "system",
        "id": 2,
        "name": "Payment Gateway",
        "criticality": "critical",
        "notes": "Required for payment processing"
      }
    ],
    "vendors": [
      {
        "type": "vendor",
        "id": 3,
        "name": "Cloud Provider",
        "sla": "99.9% uptime",
        "notes": "Hosts all application infrastructure"
      }
    ],
    "sites": [
      {
        "type": "site",
        "id": 4,
        "name": "Primary Data Center",
        "location": "Sydney, Australia",
        "notes": "Main processing location"
      }
    ]
  }
}
```

**Process Fields:**
- `name`: Process name (required)
- `description`: Detailed process description
- `rto_minutes`: Recovery Time Objective in minutes (time to restore after disruption)
- `rpo_minutes`: Recovery Point Objective in minutes (maximum acceptable data loss)
- `mtpd_minutes`: Maximum Tolerable Period of Disruption in minutes
- `impact_rating`: Business impact rating (e.g., "critical", "high", "medium", "low")
- `dependencies_json`: Structured dependencies (see below)

## Dependencies JSON Structure

The `dependencies_json` field in processes allows you to represent relationships between processes and other entities (systems, sites, vendors) using a flexible JSON structure with typed references.

### Structure Format

```json
{
  "systems": [
    {
      "type": "system",
      "id": <number>,
      "name": <string>,
      // Additional custom fields as needed
    }
  ],
  "sites": [
    {
      "type": "site",
      "id": <number>,
      "name": <string>,
      // Additional custom fields as needed
    }
  ],
  "vendors": [
    {
      "type": "vendor",
      "id": <number>,
      "name": <string>,
      // Additional custom fields as needed
    }
  ],
  "custom": [
    {
      "type": <string>,
      "id": <number>,
      "name": <string>,
      // Additional custom fields as needed
    }
  ]
}
```

### Recommended Fields

While you can add any fields you need, these are recommended:

**For all types:**
- `type`: Entity type (required for identification)
- `id`: Unique identifier (numeric)
- `name`: Human-readable name
- `criticality`: "critical", "high", "medium", "low"
- `notes`: Additional context or details

**For systems:**
- `technology`: Technology stack (e.g., "PostgreSQL", "AWS S3")
- `location`: Where the system is hosted
- `backup_available`: boolean

**For sites:**
- `location`: Physical address or region
- `capacity`: Site capacity information
- `contact`: Site contact information

**For vendors:**
- `sla`: SLA summary
- `contract_ref`: Contract reference
- `support_hours`: Available support hours

### Examples

**Simple Dependencies:**
```json
{
  "systems": [
    {"type": "system", "id": 1, "name": "Database Server"},
    {"type": "system", "id": 2, "name": "Web Server"}
  ]
}
```

**Detailed Dependencies:**
```json
{
  "systems": [
    {
      "type": "system",
      "id": 1,
      "name": "PostgreSQL Primary",
      "criticality": "critical",
      "technology": "PostgreSQL 15",
      "location": "AWS Sydney",
      "backup_available": true,
      "notes": "Primary database with hourly backups"
    }
  ],
  "vendors": [
    {
      "type": "vendor",
      "id": 5,
      "name": "AWS",
      "sla": "99.95% uptime",
      "contract_ref": "AWS-ENT-2024",
      "support_hours": "24/7",
      "notes": "Enterprise support plan"
    }
  ],
  "sites": [
    {
      "type": "site",
      "id": 10,
      "name": "Sydney Office",
      "location": "100 Market St, Sydney NSW 2000",
      "capacity": "100 staff",
      "contact": "facilities@example.com"
    }
  ]
}
```

**Cross-referencing:**
You can reference vendors created through the vendors API by using their IDs:

```json
{
  "vendors": [
    {
      "type": "vendor",
      "id": 3,
      "name": "Cloud Services Inc",
      "notes": "See vendor record for full SLA details"
    }
  ]
}
```

## Integration Patterns

### Creating a Complete Business Continuity Plan

1. **Create the plan** (via BC5 API)
2. **Add contacts** for emergency response team
3. **Add vendors** with SLA information
4. **Add processes** with dependencies referencing systems, sites, and vendors

### Importing from Address Book

If you have an existing address book or contact system:

1. Query your address book API
2. Transform contacts to BC contact format
3. POST to `/api/bc/plans/{plan_id}/contacts`

Example transformation:
```python
# From address book
address_book_contact = {
    "full_name": "John Smith",
    "title": "IT Director",
    "work_phone": "+1-555-0123",
    "work_email": "john.smith@example.com"
}

# Transform to BC contact
bc_contact = {
    "plan_id": 1,
    "name": address_book_contact["full_name"],
    "role": address_book_contact["title"],
    "phone": address_book_contact["work_phone"],
    "email": address_book_contact["work_email"],
    "notes": "Imported from company address book"
}
```

### Vendor SLA Tracking

Use the `sla_notes` field to track important SLA details:

```json
{
  "name": "Hosting Provider",
  "sla_notes": "SLA Terms:\n- 99.9% uptime guarantee\n- Response Times:\n  * Critical: 1 hour\n  * High: 4 hours\n  * Medium: 8 hours\n  * Low: 24 hours\n- Support: 24/7 via phone/email\n- Downtime credits: 5% per hour\n- Review: Quarterly"
}
```

### Process Dependencies Best Practices

1. **Start simple**: Begin with just type, id, and name
2. **Add detail gradually**: Add criticality and notes as you refine
3. **Keep it maintainable**: Don't duplicate information that changes frequently
4. **Reference other entities**: Link to vendor records rather than duplicating data
5. **Document custom fields**: If you add custom fields, document them in your team wiki

## Testing

Run the BC11 tests:
```bash
pytest tests/test_bc11_supportive_entities.py -v
```

All 17 tests should pass:
- 4 contact schema tests
- 4 vendor schema tests
- 5 process schema tests
- 4 repository tests

## Database Schema

### bc_contact
Already existed, no changes needed.

### bc_vendor (NEW in migration 125)
```sql
CREATE TABLE bc_vendor (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  vendor_type VARCHAR(100),
  contact_name VARCHAR(255),
  contact_email VARCHAR(255),
  contact_phone VARCHAR(50),
  sla_notes TEXT,
  contract_reference VARCHAR(255),
  criticality VARCHAR(50),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bc_plan(id) ON DELETE CASCADE,
  INDEX idx_bc_vendor_plan (plan_id),
  INDEX idx_bc_vendor_criticality (criticality)
);
```

### bc_process
Already existed, uses `dependencies_json` JSON field for flexible dependency tracking.

## Security

- All endpoints require authentication
- BC RBAC roles enforced (viewer/editor/approver/admin)
- Plan ID validation on all operations
- Cascade delete protection (contacts/vendors/processes deleted when plan deleted)

## Migration

To apply the vendor table:
1. Database migration runs automatically on startup
2. Migration file: `migrations/125_bc11_vendors_table.sql`
3. Safe to run multiple times (uses `IF NOT EXISTS`)
