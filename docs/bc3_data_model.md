# BC3 Business Continuity Planning Data Model

## Overview

This document describes the comprehensive Business Continuity Planning (BCP) data model implemented for MyPortal. The model supports a template-driven, versioned BCP system with review/approval workflows and attachments.

## Architecture

The BC3 data model is implemented using:

1. **SQLAlchemy 2.0 async declarative models** (`app/models/bc_models.py`)
2. **SQL migration** (`migrations/124_bc3_bcp_data_model.sql`)
3. **Pydantic schemas** (`app/schemas/bc3_models.py`)

## Database Tables

### Core Tables

#### bc_plan
Main business continuity plan table. Tracks the overall plan metadata and lifecycle.

**Key Fields:**
- `id` (INT, PK): Unique plan identifier
- `org_id` (INT, nullable): Organization ID for multi-tenant support
- `title` (VARCHAR(255)): Plan title
- `status` (ENUM): Plan lifecycle status (draft, in_review, approved, archived)
- `template_id` (INT, FK): Reference to template used
- `current_version_id` (INT, FK): Reference to active version
- `owner_user_id` (INT): Plan owner
- `approved_at_utc` (DATETIME): Approval timestamp
- `created_at`, `updated_at` (DATETIME): Audit timestamps

**Indexes:**
- `idx_bc_plan_org_status`: Fast filtering by org and status
- `idx_bc_plan_status_updated`: Recent plans by status
- `idx_bc_plan_template`: Plans using specific template
- `idx_bc_plan_owner`: Plans by owner

#### bc_plan_version
Version history for business continuity plans. Each plan can have multiple versions.

**Key Fields:**
- `id` (INT, PK): Unique version identifier
- `plan_id` (INT, FK): Reference to parent plan
- `version_number` (INT): Sequential version number (must be > 0)
- `status` (ENUM): Version status (active, superseded)
- `authored_by_user_id` (INT): Version author
- `authored_at_utc` (DATETIME): Creation timestamp
- `summary_change_note` (TEXT): Description of changes
- `content_json` (JSON): Section data as structured JSON
- `docx_export_hash` (VARCHAR(64)): Hash of Word export
- `pdf_export_hash` (VARCHAR(64)): Hash of PDF export

**Constraints:**
- `ck_version_number_positive`: Ensures version_number > 0
- ON DELETE CASCADE: Deletes with parent plan

#### bc_template
Template definitions that define the structure of business continuity plans.

**Key Fields:**
- `id` (INT, PK): Unique template identifier
- `name` (VARCHAR(255)): Template name
- `version` (VARCHAR(50)): Template version
- `is_default` (BOOLEAN): Default template flag
- `schema_json` (JSON): Complete section and field definitions
- `created_at`, `updated_at` (DATETIME): Audit timestamps

**Indexes:**
- `idx_bc_template_default`: Fast access to default template

#### bc_section_definition (Optional)
Granular section definitions for templates. Can be used instead of or in addition to storing all sections in `bc_template.schema_json`.

**Key Fields:**
- `id` (INT, PK): Unique section identifier
- `template_id` (INT, FK): Parent template
- `key` (VARCHAR(100)): Unique section key (e.g., "overview", "risks")
- `title` (VARCHAR(255)): Display title
- `order_index` (INT): Display order
- `schema_json` (JSON): Field definitions for this section

**Constraints:**
- ON DELETE CASCADE: Deletes with parent template

### Supporting Tables

#### bc_contact
Emergency contacts associated with business continuity plans.

**Key Fields:**
- `id` (INT, PK): Unique contact identifier
- `plan_id` (INT, FK): Reference to parent plan
- `name` (VARCHAR(255)): Contact name
- `role` (VARCHAR(255)): Contact role/title
- `phone` (VARCHAR(50)): Phone number
- `email` (VARCHAR(255)): Email address
- `notes` (TEXT): Additional notes

**Constraints:**
- ON DELETE CASCADE: Deletes with parent plan

#### bc_process
Critical business processes tracked in continuity plans with recovery objectives.

**Key Fields:**
- `id` (INT, PK): Unique process identifier
- `plan_id` (INT, FK): Reference to parent plan
- `name` (VARCHAR(255)): Process name
- `description` (TEXT): Process description
- `rto_minutes` (INT): Recovery Time Objective (minutes)
- `rpo_minutes` (INT): Recovery Point Objective (minutes)
- `mtpd_minutes` (INT): Maximum Tolerable Period of Disruption (minutes)
- `impact_rating` (VARCHAR(50)): Impact level (e.g., critical, high, medium, low)
- `dependencies_json` (JSON): Process dependencies as structured data

**Constraints:**
- `ck_rto_non_negative`, `ck_rpo_non_negative`, `ck_mtpd_non_negative`: Ensure non-negative values
- ON DELETE CASCADE: Deletes with parent plan

#### bc_risk
Risk assessments for business continuity plans.

**Key Fields:**
- `id` (INT, PK): Unique risk identifier
- `plan_id` (INT, FK): Reference to parent plan
- `threat` (VARCHAR(500)): Threat description
- `likelihood` (VARCHAR(50)): Likelihood rating
- `impact` (VARCHAR(50)): Impact rating
- `rating` (VARCHAR(50)): Overall risk rating
- `mitigation` (TEXT): Mitigation strategy
- `owner_user_id` (INT): Risk owner

**Indexes:**
- `idx_bc_risk_rating`: Fast filtering by risk rating
- `idx_bc_risk_owner`: Risks by owner

**Constraints:**
- ON DELETE CASCADE: Deletes with parent plan

#### bc_attachment
File attachments for business continuity plans. Stores metadata; actual files stored in filesystem/object storage.

**Key Fields:**
- `id` (INT, PK): Unique attachment identifier
- `plan_id` (INT, FK): Reference to parent plan
- `file_name` (VARCHAR(255)): Original filename
- `storage_path` (VARCHAR(500)): Path in storage system
- `content_type` (VARCHAR(100)): MIME type
- `size_bytes` (INT): File size
- `uploaded_by_user_id` (INT): Uploader
- `uploaded_at_utc` (DATETIME): Upload timestamp
- `hash` (VARCHAR(64)): SHA256 hash for integrity

**Constraints:**
- `ck_size_non_negative`: Ensures non-negative size
- ON DELETE CASCADE: Deletes with parent plan

### Workflow Tables

#### bc_review
Review and approval workflow tracking.

**Key Fields:**
- `id` (INT, PK): Unique review identifier
- `plan_id` (INT, FK): Reference to parent plan
- `requested_by_user_id` (INT): Review requester
- `reviewer_user_id` (INT): Assigned reviewer
- `status` (ENUM): Review status (pending, approved, changes_requested)
- `requested_at_utc` (DATETIME): Request timestamp
- `decided_at_utc` (DATETIME): Decision timestamp
- `notes` (TEXT): Review notes/comments

**Indexes:**
- `idx_bc_review_reviewer`: Reviews by reviewer
- `idx_bc_review_status`: Active reviews

**Constraints:**
- ON DELETE CASCADE: Deletes with parent plan

#### bc_ack
User acknowledgments tracking who has read/acknowledged specific plan versions.

**Key Fields:**
- `id` (INT, PK): Unique acknowledgment identifier
- `plan_id` (INT, FK): Reference to parent plan
- `user_id` (INT): User who acknowledged
- `ack_at_utc` (DATETIME): Acknowledgment timestamp
- `ack_version_number` (INT): Version number acknowledged

**Indexes:**
- `idx_bc_ack_plan_user`: Fast lookup of user acknowledgments for a plan

**Constraints:**
- ON DELETE CASCADE: Deletes with parent plan

#### bc_audit
Audit trail for all plan changes and actions.

**Key Fields:**
- `id` (INT, PK): Unique audit entry identifier
- `plan_id` (INT, FK): Reference to parent plan
- `action` (VARCHAR(100)): Action performed (e.g., created, updated, approved)
- `actor_user_id` (INT): User who performed action
- `details_json` (JSON): Additional audit details
- `at_utc` (DATETIME): Action timestamp

**Indexes:**
- `idx_bc_audit_action`: Audit entries by action type
- `idx_bc_audit_at`: Chronological audit trail

**Constraints:**
- ON DELETE CASCADE: Deletes with parent plan

#### bc_change_log_map
Links change log files from the `changes/` directory to specific plans.

**Key Fields:**
- `id` (INT, PK): Unique mapping identifier
- `plan_id` (INT, FK): Reference to parent plan
- `change_guid` (VARCHAR(36)): GUID of change log file
- `imported_at_utc` (DATETIME): Import timestamp

**Indexes:**
- `idx_bc_change_log_guid`: Fast lookup by change GUID

**Constraints:**
- ON DELETE CASCADE: Deletes with parent plan

## Data Model Features

### 1. Template-Driven Design
Plans are based on templates (`bc_template`) that define their structure. Templates contain:
- Section definitions (either in `schema_json` or via `bc_section_definition` table)
- Field types and validation rules
- Default values and help text

### 2. Version Control
Each plan maintains a complete version history via `bc_plan_version`. Features:
- Sequential version numbering
- Change summaries
- JSON content storage for flexibility
- Export hash tracking (DOCX, PDF)
- Only one "active" version per plan

### 3. Review/Approval Workflow
Plans go through lifecycle stages:
1. **Draft**: Initial creation and editing
2. **In Review**: Submitted for review (`bc_review` entries created)
3. **Approved**: Approved by designated reviewers
4. **Archived**: No longer active

### 4. Audit Trail
Complete accountability via:
- `bc_audit`: Action-level tracking
- Timestamp columns: `created_at`, `updated_at`, `approved_at_utc`
- User references: `owner_user_id`, `authored_by_user_id`, `actor_user_id`

### 5. Multi-Tenancy Ready
The optional `org_id` field in `bc_plan` supports future multi-tenant deployments.

### 6. Flexible Content Storage
JSON columns (`content_json`, `schema_json`, `dependencies_json`, `details_json`) provide:
- Schema flexibility without migrations
- Complex nested structures
- Easy evolution of data models

### 7. Foreign Key Cascade Deletes
Most child tables use `ON DELETE CASCADE` to maintain referential integrity.

## Usage Examples

### Creating a Plan from Template
```python
from app.schemas.bc3_models import BCPlanCreate, BCPlanStatus

plan = BCPlanCreate(
    title="2024 Disaster Recovery Plan",
    status=BCPlanStatus.DRAFT,
    template_id=1,  # Government BCP template
    owner_user_id=42
)
```

### Creating a Plan Version
```python
from app.schemas.bc3_models import BCPlanVersionCreate

version = BCPlanVersionCreate(
    plan_id=1,
    version_number=1,
    authored_by_user_id=42,
    content_json={
        "overview": {
            "purpose": "Ensure business continuity...",
            "scope": "All critical systems"
        },
        "processes": [...],
        "risks": [...]
    }
)
```

### Adding a Process with Recovery Objectives
```python
from app.schemas.bc3_models import BCProcessCreate

process = BCProcessCreate(
    plan_id=1,
    name="Email Service",
    description="Critical email infrastructure",
    rto_minutes=240,  # 4 hours
    rpo_minutes=60,   # 1 hour
    mtpd_minutes=480, # 8 hours
    impact_rating="critical"
)
```

### Requesting Plan Review
```python
from app.schemas.bc3_models import BCReviewCreate, BCReviewStatus

review = BCReviewCreate(
    plan_id=1,
    requested_by_user_id=42,
    reviewer_user_id=7,
    status=BCReviewStatus.PENDING,
    notes="Please review before Monday's meeting"
)
```

## UTC Timestamp Convention

All timestamps use UTC (`*_at_utc` suffix) and should be stored as naive UTC datetimes in MySQL. The application layer handles timezone conversion for display.

## SQLAlchemy 2.0 Features

The models use modern SQLAlchemy 2.0 features:
- `Mapped[]` type hints for type safety
- `mapped_column()` for column definitions
- Declarative base with naming conventions
- Async-ready (though currently used with raw SQL)

## Integration Notes

While SQLAlchemy models are defined, the repository layer currently uses raw SQL queries via `aiomysql`. The SQLAlchemy models serve as:
1. Documentation of the schema
2. Type hints for development
3. Future migration path to ORM usage

## Testing

Comprehensive tests are provided in `tests/test_bc3_models.py` covering:
- Pydantic schema validation
- Field constraints (min/max lengths, positive numbers, etc.)
- Required vs optional fields
- SQLAlchemy model imports and table names

Run tests:
```bash
pytest tests/test_bc3_models.py -v
```

## Migration

The SQL migration creates all tables with proper:
- Foreign key constraints with CASCADE deletes
- Indexes for common query patterns
- Check constraints for data validation
- ENUM types for status fields
- JSON columns for flexible content
- UTF-8 character set (utf8mb4)

Apply migration:
```bash
# Migrations run automatically on app startup
# Or manually: mysql < migrations/124_bc3_bcp_data_model.sql
```

## Future Enhancements

Potential additions to the model:
1. **bc_plan_collaborator**: Track multiple plan contributors
2. **bc_exercise**: Testing and drill records
3. **bc_incident**: Actual incident activation records
4. **bc_resource**: Required resources and assets
5. **bc_vendor**: Third-party vendor information
6. **bc_notification**: Alert and notification tracking
7. **bc_kpi**: Key performance indicators and metrics
