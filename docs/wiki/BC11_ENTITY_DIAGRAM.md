# BC11 Entity Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Business Continuity Plan                  │
│                            (bc_plan)                             │
│  - id, title, status, owner_user_id                             │
└───────────┬──────────────────────────────────────────────────────┘
            │
            │ Has Many
            │
    ┌───────┴────────┬──────────────┬──────────────┐
    │                │              │              │
    ▼                ▼              ▼              ▼
┌─────────┐    ┌──────────┐   ┌──────────┐   ┌──────────┐
│Contacts │    │ Vendors  │   │Processes │   │ Others   │
│         │    │  (NEW)   │   │          │   │          │
└─────────┘    └──────────┘   └──────────┘   └──────────┘
                                     │
                                     │ References (JSON)
                                     │
                           ┌─────────┴──────────┐
                           │                    │
                           ▼                    ▼
                      ┌──────────┐        ┌─────────┐
                      │ Systems  │        │  Sites  │
                      └──────────┘        └─────────┘
```

## BC Contact Entity
```
bc_contact
├── id (PK)
├── plan_id (FK -> bc_plan)
├── name ✓
├── role ✓
├── phone ✓
├── email ✓
├── notes ✓
└── timestamps
```

## BC Vendor Entity (NEW)
```
bc_vendor
├── id (PK)
├── plan_id (FK -> bc_plan)
├── name ✓
├── vendor_type
├── contact_name
├── contact_email
├── contact_phone
├── sla_notes ✓ (TEXT - for detailed SLA tracking)
├── contract_reference
├── criticality
└── timestamps
```

## BC Process Entity
```
bc_process
├── id (PK)
├── plan_id (FK -> bc_plan)
├── name ✓
├── description
├── rto_minutes (Recovery Time Objective)
├── rpo_minutes (Recovery Point Objective)
├── mtpd_minutes (Max Tolerable Period of Disruption)
├── impact_rating
├── dependencies_json ✓ (JSON - flexible typed references)
└── timestamps
```

## Dependencies JSON Structure
```json
{
  "systems": [
    {
      "type": "system",
      "id": 123,
      "name": "CRM System",
      "criticality": "high",
      "technology": "PostgreSQL",
      "notes": "..."
    }
  ],
  "vendors": [
    {
      "type": "vendor",
      "id": 456,
      "name": "Cloud Provider",
      "sla": "99.9% uptime",
      "notes": "..."
    }
  ],
  "sites": [
    {
      "type": "site",
      "id": 789,
      "name": "Data Center",
      "location": "Sydney",
      "notes": "..."
    }
  ]
}
```

## API Endpoint Structure
```
/api/bc/plans/{plan_id}/
    ├── contacts/
    │   ├── GET     - List all
    │   ├── POST    - Create
    │   └── {contact_id}/
    │       ├── GET    - Get one
    │       ├── PUT    - Update
    │       └── DELETE - Delete
    │
    ├── vendors/
    │   ├── GET     - List all
    │   ├── POST    - Create
    │   └── {vendor_id}/
    │       ├── GET    - Get one
    │       ├── PUT    - Update
    │       └── DELETE - Delete
    │
    └── processes/
        ├── GET     - List all
        ├── POST    - Create
        └── {process_id}/
            ├── GET    - Get one
            ├── PUT    - Update
            └── DELETE - Delete
```

## RBAC Authorization Flow
```
Request
  │
  ├─> Authentication Check (JWT)
  │
  ├─> BC RBAC Check
  │   ├─> Viewer Role  → GET endpoints
  │   └─> Editor Role  → All CRUD endpoints
  │
  ├─> Plan ID Validation
  │   └─> Ensure plan exists and user has access
  │
  └─> Process Request
```

## Data Flow Example: Creating a Process with Dependencies

```
1. Client Request
   POST /api/bc/plans/1/processes
   {
     "name": "Order Processing",
     "dependencies_json": {
       "systems": [{"type": "system", "id": 1, "name": "CRM"}],
       "vendors": [{"type": "vendor", "id": 2, "name": "AWS"}]
     }
   }

2. API Layer (bc11.py)
   ├─> Authenticate user
   ├─> Check BC Editor role
   ├─> Validate plan exists
   └─> Call repository

3. Repository Layer (bc3.py)
   ├─> Validate input via Pydantic
   ├─> Execute parameterized SQL INSERT
   └─> Return created process

4. Response
   {
     "id": 10,
     "plan_id": 1,
     "name": "Order Processing",
     "dependencies_json": {...},
     "created_at": "2025-11-10T09:00:00Z",
     "updated_at": "2025-11-10T09:00:00Z"
   }
```

## Integration Pattern: Address Book Import

```
External Address Book
        │
        │ Query API
        ▼
    Transform Data
        │
        │ Map fields:
        │ - full_name → name
        │ - title → role
        │ - work_email → email
        │ - work_phone → phone
        ▼
POST /api/bc/plans/1/contacts
        │
        ▼
    bc_contact table
```

## Vendor SLA Tracking Pattern

```
Vendor Contract Documents
        │
        │ Extract SLA Terms
        ▼
    Format as Text
    ├─ Uptime guarantees
    ├─ Response times
    ├─ Support hours
    ├─ Credits/penalties
    └─ Review schedule
        │
        ▼
POST /api/bc/plans/1/vendors
{
  "sla_notes": "
    99.9% uptime guarantee
    Response times:
    - Critical: 1 hour
    - High: 4 hours
    Support: 24/7
    Monthly credits for downtime
    Quarterly review"
}
        │
        ▼
    bc_vendor table
```

## Testing Pyramid

```
        API Tests (15 endpoints)
       /                        \
      /    Repository Tests      \
     /    (CRUD operations)       \
    /                              \
   /     Schema Validation Tests   \
  /     (Pydantic validation)       \
 ─────────────────────────────────────
          17 Tests Total
```

## Security Layers

```
┌──────────────────────────────────────┐
│     Network/Transport Security       │
│         (HTTPS, TLS)                 │
└────────────────┬─────────────────────┘
                 │
┌────────────────▼─────────────────────┐
│     Authentication Layer             │
│         (JWT Tokens)                 │
└────────────────┬─────────────────────┘
                 │
┌────────────────▼─────────────────────┐
│     Authorization Layer              │
│     (BC RBAC - Viewer/Editor)        │
└────────────────┬─────────────────────┘
                 │
┌────────────────▼─────────────────────┐
│     Input Validation                 │
│     (Pydantic Schemas)               │
└────────────────┬─────────────────────┘
                 │
┌────────────────▼─────────────────────┐
│     SQL Layer                        │
│     (Parameterized Queries)          │
└────────────────┬─────────────────────┘
                 │
                 ▼
            Database
```
