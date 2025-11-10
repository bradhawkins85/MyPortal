# BC3 Implementation Summary

## Issue Addressed
**BC3 Data model design**: Design SQLAlchemy 2.0 async models to support a template-driven, versioned BCP with review/approval workflow and attachments.

## Implementation Complete ✅

### Files Created

1. **`app/models/__init__.py`** (52 lines)
   - SQLAlchemy 2.0 base class with naming conventions
   - TimestampMixin for common audit fields
   - Async-ready declarative base

2. **`app/models/bc_models.py`** (510 lines)
   - 12 comprehensive SQLAlchemy 2.0 async models
   - Full type hints using `Mapped[]`
   - Proper indexes and constraints
   - Documented relationships (commented for future use)

3. **`migrations/124_bc3_bcp_data_model.sql`** (196 lines)
   - Creates all 12 database tables
   - Foreign key constraints with CASCADE deletes
   - Indexes for query optimization
   - Check constraints for data validation
   - JSON columns for flexible storage

4. **`app/schemas/bc3_models.py`** (467 lines)
   - 44 Pydantic schemas for validation and serialization
   - 3 enum classes for status fields
   - Create, Update, and Response schemas for each entity
   - Field validation with constraints

5. **`tests/test_bc3_models.py`** (623 lines)
   - 36 comprehensive test cases
   - Schema validation tests
   - SQLAlchemy model import verification
   - Field constraint testing
   - All tests passing ✅

6. **`docs/bc3_data_model.md`** (387 lines)
   - Complete documentation
   - Table descriptions
   - Usage examples
   - Architecture notes
   - Future enhancement suggestions

## Database Schema

### Tables Implemented (12 total)

#### Core Plan Tables
1. **bc_plan** - Main business continuity plans
   - Multi-tenant ready with optional org_id
   - Status lifecycle: draft → in_review → approved → archived
   - Links to current version and template

2. **bc_plan_version** - Version history
   - Sequential version numbers
   - JSON content storage
   - Change summaries
   - Export hash tracking (DOCX, PDF)

3. **bc_template** - Template definitions
   - Schema JSON for section definitions
   - Default template flag
   - Version tracking

4. **bc_section_definition** - Optional granular sections
   - Template sections with ordering
   - Field definitions per section

#### Supporting Entity Tables
5. **bc_contact** - Emergency contacts
   - Name, role, phone, email
   - Free-form notes

6. **bc_process** - Critical business processes
   - RTO (Recovery Time Objective)
   - RPO (Recovery Point Objective)
   - MTPD (Maximum Tolerable Period of Disruption)
   - Impact ratings
   - Dependencies as JSON

7. **bc_risk** - Risk assessments
   - Threat descriptions
   - Likelihood and impact ratings
   - Overall risk rating
   - Mitigation strategies
   - Risk owner assignment

8. **bc_attachment** - File attachments
   - Metadata storage (file system stores actual files)
   - Content type and size
   - SHA256 hash for integrity
   - Upload tracking

#### Workflow Tables
9. **bc_review** - Review/approval workflow
   - Requester and reviewer tracking
   - Status: pending/approved/changes_requested
   - Decision timestamps
   - Review notes

10. **bc_ack** - User acknowledgments
    - Tracks who read/acknowledged plans
    - Version number acknowledged
    - Timestamp tracking

11. **bc_audit** - Complete audit trail
    - All actions on plans
    - Actor tracking
    - JSON details field
    - Chronological indexing

12. **bc_change_log_map** - Change log integration
    - Links change log GUIDs to plans
    - Import timestamp tracking

## Features Delivered

### ✅ All Requirements Met

From the issue specification:

- [x] bc_plan with org_id (nullable), title, status, template_id, current_version_id, owner_user_id, timestamps, approved_at_utc
- [x] bc_plan_version with plan_id, version_number, status, authored_by_user_id, timestamps, summary_change_note, content_json, docx_export_hash, pdf_export_hash
- [x] bc_template with name, version, is_default, schema_json, timestamps
- [x] bc_section_definition (optional) with template_id, key, title, order_index, schema_json
- [x] bc_contact with plan_id, name, role, phone, email, notes
- [x] bc_process with plan_id, name, description, rto_minutes, rpo_minutes, mtpd_minutes, impact_rating, dependencies_json
- [x] bc_risk with plan_id, threat, likelihood, impact, rating, mitigation, owner_user_id
- [x] bc_attachment with plan_id, file_name, storage_path, content_type, size_bytes, uploaded_by_user_id, uploaded_at_utc, hash
- [x] bc_review with plan_id, requested_by_user_id, reviewer_user_id, status, requested_at_utc, decided_at_utc, notes
- [x] bc_ack with plan_id, user_id, ack_at_utc, ack_version_number
- [x] bc_audit with plan_id, action, actor_user_id, details_json, at_utc
- [x] bc_change_log_map with plan_id, change_guid, imported_at_utc

### ✅ Constraints Implemented

- [x] Foreign keys with ON DELETE CASCADE where appropriate
- [x] Indexes on plan_id, status, updated_at_utc
- [x] UTC timestamps only (naive UTC in DB, timezone-aware in Python)
- [x] JSON columns for flexible section data and dependencies

### ✅ Additional Features

- Type-safe SQLAlchemy 2.0 models with `Mapped[]` hints
- Comprehensive Pydantic schemas for validation
- Check constraints for data integrity (positive numbers, etc.)
- Strategic indexes for common query patterns
- Complete test coverage
- Detailed documentation

## Test Results

```
tests/test_bc3_models.py::36 tests PASSED
tests/test_business_continuity_plans.py::14 tests PASSED
tests/test_bcp_template.py::12 tests PASSED
tests/test_bcp_template_api.py::11 tests PASSED

Total: 73 tests PASSED ✅
```

## Architecture Decisions

### SQLAlchemy 2.0 + Raw SQL Hybrid
- SQLAlchemy models provide type-safe definitions and documentation
- Repository layer continues using raw SQL via aiomysql (existing pattern)
- Models serve as future migration path to full ORM usage
- No breaking changes to existing code

### JSON Storage for Flexibility
- Template schemas stored as JSON (no migrations needed for schema changes)
- Plan content stored as JSON (flexible section structure)
- Dependencies and details as JSON (complex nested data)

### UTC Timestamp Convention
- All timestamps suffixed with `_utc` for clarity
- Stored as naive UTC in MySQL
- Application handles timezone conversion

### Cascade Deletes
- Most relationships use ON DELETE CASCADE
- Maintains referential integrity automatically
- Simplifies plan deletion

## Integration Notes

### Existing System Compatibility
- No changes to existing business_continuity_plans table
- New tables use `bc_` prefix to avoid naming conflicts
- Can coexist with or replace existing system
- Follows existing MyPortal patterns

### Future Repository Implementation
When implementing repositories, use patterns like:

```python
# app/repositories/bc3_plans.py
async def create_plan(plan_data: BCPlanCreate) -> dict:
    query = """
        INSERT INTO bc_plan 
        (org_id, title, status, template_id, owner_user_id)
        VALUES (%s, %s, %s, %s, %s)
    """
    plan_id = await db.execute(
        query, 
        (plan_data.org_id, plan_data.title, plan_data.status.value, 
         plan_data.template_id, plan_data.owner_user_id)
    )
    return await get_plan_by_id(plan_id)
```

## Security Considerations

### Implemented Safeguards
- Check constraints prevent negative values (RTO, RPO, MTPD, file sizes)
- Foreign key constraints maintain referential integrity
- Audit trail tracks all actions
- File attachment hashing for integrity verification
- User ownership and review tracking

### Recommendations for API Implementation
- Validate user permissions before allowing access to plans
- Verify org_id matches user's organization (multi-tenant)
- Sanitize JSON content before storage
- Validate file uploads (size, type, scan for malware)
- Rate limit review requests
- Audit all sensitive operations

## Migration Strategy

### Database Migration
The migration file `124_bc3_bcp_data_model.sql` will:
1. Run automatically on application startup (existing pattern)
2. Create all tables if they don't exist
3. Handle circular FK between bc_plan and bc_plan_version
4. Be idempotent (safe to run multiple times)

### No Manual Steps Required
The migration is designed to run automatically with no intervention needed.

## Next Steps (Not in Scope)

While the data model is complete, these components would be needed for a full implementation:

1. **Repository Layer** - CRUD operations for each table
2. **API Endpoints** - RESTful routes for BC3 plans
3. **Service Layer** - Business logic (versioning, workflows, etc.)
4. **UI Components** - Forms, tables, dashboards
5. **File Storage** - Integration for attachment uploads
6. **Template Engine** - Default templates and customization
7. **Export Functions** - DOCX and PDF generation
8. **Notification System** - Review requests, approvals, etc.

## Validation

✅ All SQL table definitions validated
✅ SQLAlchemy models import successfully
✅ Pydantic schemas validate correctly
✅ All 36 new tests passing
✅ All 37 existing BCP tests still passing
✅ No breaking changes to existing code
✅ Documentation complete

## Summary

The BC3 data model is **production-ready** and provides a solid foundation for a comprehensive Business Continuity Planning system. The implementation follows best practices, includes proper constraints and indexes, and is fully tested and documented.

The SQLAlchemy 2.0 models provide excellent documentation and type safety while maintaining compatibility with the existing raw SQL patterns used throughout MyPortal.
