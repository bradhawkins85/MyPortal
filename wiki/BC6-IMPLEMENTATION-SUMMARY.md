# BC6 Pydantic Schema Implementation Summary

## Overview

This document summarizes the Pydantic schemas implemented for BC6, which provide comprehensive validation for Business Continuity Planning (BCP) system components.

## Schemas Implemented

### 1. Template Structure Schemas

#### FieldSchema
Defines the structure and validation rules for individual fields within a BCP template section.

**Key Features:**
- Unique field identifier with min/max length validation
- Type-safe field type definition
- Required/optional flag
- Help text and default values
- Custom validation rules

**Example:**
```python
field = FieldSchema(
    field_id="rto_minutes",
    label="Recovery Time Objective (minutes)",
    field_type="integer",
    required=True,
    help_text="Target time to recover this process",
    validation_rules={"min": 0, "max": 525600}
)
```

#### SectionSchema
Organizes related fields within a BCP template.

**Key Features:**
- Hierarchical structure with parent_section_id support
- Ordered sections (order must be non-negative)
- Contains list of FieldSchema objects
- Optional description

**Example:**
```python
section = SectionSchema(
    section_id="recovery_objectives",
    title="Recovery Objectives",
    description="Define RTO, RPO, and MTPD for critical processes",
    order=1,
    fields=[field1, field2]
)
```

#### TemplateSchema
Complete template schema definition representing the full structure of a BCP template.

**Key Features:**
- Template name and version tracking
- Default template flag
- Contains list of SectionSchema objects
- Additional metadata support

**Example:**
```python
template = TemplateSchema(
    name="Government Business Continuity Plan",
    version="2.0",
    description="Standard BCP template for government organizations",
    is_default=True,
    sections=[section1, section2]
)
```

### 2. Plan Management Schemas (Aliases)

- **PlanCreate** → BCPlanCreate
- **PlanUpdate** → BCPlanUpdate
- **PlanDetail** → BCPlanDetail
- **PlanVersionCreate** → BCVersionCreate
- **PlanVersionDetail** → BCVersionDetail
- **ExportRequest** → BCExportRequest

These aliases provide consistent naming and ease of use across the API.

### 3. Review and Workflow Schemas

#### ReviewCreate
Creates a review request for a BCP plan.

**Key Features:**
- Plan ID validation (must be positive)
- At least one reviewer required
- Optional notes and due date
- **UTC datetime validation** - rejects naive datetimes

**Example:**
```python
review = ReviewCreate(
    plan_id=1,
    reviewer_user_ids=[2, 3, 4],
    notes="Please review the updated recovery procedures",
    due_date=datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
)
```

#### ReviewDecision
Records a review decision with proper timestamp handling.

**Key Features:**
- Review ID and decision type (approved/changes_requested)
- Required notes
- Automatic timestamp with UTC validation
- **ISO 8601 UTC enforcement**

**Example:**
```python
decision = ReviewDecision(
    review_id=1,
    decision=BCReviewDecision.APPROVED,
    notes="All recovery procedures are clear and well-documented",
    decided_at=datetime.now(timezone.utc)
)
```

### 4. Attachment Management

#### AttachmentMeta
Metadata schema for plan attachments without the file content itself.

**Key Features:**
- File name, MIME type, and size validation
- Size must be non-negative
- SHA256 hash for integrity verification
- Tag support for categorization
- **UTC timestamp validation**

**Example:**
```python
attachment = AttachmentMeta(
    plan_id=1,
    file_name="contact_list.pdf",
    content_type="application/pdf",
    size_bytes=245760,
    hash="a1b2c3d4e5f6...",
    tags=["contacts", "emergency"]
)
```

### 5. Process and Recovery Objectives

#### BCProcessBase / BCProcessCreate / BCProcessUpdate / BCProcessDetail
Business continuity processes with recovery objectives.

**Key Features:**
- **RTO (Recovery Time Objective)** - non-negative integer in minutes
- **RPO (Recovery Point Objective)** - non-negative integer in minutes
- **MTPD (Maximum Tolerable Period of Disruption)** - non-negative integer in minutes
- Impact rating (critical, high, medium, low)
- Process dependencies as JSON
- **Custom validators ensure RTO/RPO/MTPD ≥ 0**

**Example:**
```python
process = BCProcessCreate(
    plan_id=1,
    name="Email Service",
    description="Corporate email system",
    rto_minutes=240,   # 4 hours
    rpo_minutes=60,    # 1 hour
    mtpd_minutes=480,  # 8 hours
    impact_rating="critical"
)
```

### 6. Status Transition Validation

#### PlanStatusTransition
Validates plan status transitions to ensure only allowed state changes.

**Allowed Transitions:**
- `draft` → `in_review`
- `in_review` → `approved` or `draft`
- `approved` → `archived`
- `archived` → `draft` (reactivation)

**Example:**
```python
# Valid transition
transition = PlanStatusTransition(
    current_status=BCPlanListStatus.DRAFT,
    new_status=BCPlanListStatus.IN_REVIEW
)

# Invalid transition - raises ValidationError
transition = PlanStatusTransition(
    current_status=BCPlanListStatus.DRAFT,
    new_status=BCPlanListStatus.APPROVED  # Not allowed!
)
```

## Validation Features

### 1. RTO/RPO/MTPD Validation
All recovery objectives are validated as non-negative integers:
- Uses `Field(ge=0)` constraint
- Custom validator provides clear error messages
- Zero values are allowed (instant recovery/no data loss)

### 2. ISO 8601 UTC Datetime Validation
All datetime fields enforce timezone information:
- Rejects naive datetimes (without timezone)
- Accepts any timezone (UTC or offset-based)
- Clear validation error messages mentioning "timezone"

### 3. Status Transition Validation
Custom validator ensures only allowed status transitions:
- Checks current and new status
- Allows same-status transitions
- Provides detailed error message with allowed transitions

### 4. String Length Validation
All string fields have appropriate min/max length constraints:
- Field IDs: 1-100 characters
- Labels/Titles: 1-255 characters
- Descriptions: unlimited
- Notes: 1-5000 characters

### 5. Non-Negative Integer Validation
File sizes and counts must be non-negative:
- Attachment size_bytes
- Order indices in sections

## Testing

Comprehensive test suite in `tests/test_bc6_schemas.py`:
- **50 tests** covering all schema types
- Tests for positive and negative validation cases
- Integration tests for complex schema compositions
- All existing BC tests continue to pass

### Test Categories:
1. **Template Schemas** (8 tests) - Field, section, and template validation
2. **Plan Schemas** (2 tests) - Alias verification
3. **Version Schemas** (1 test) - Alias verification
4. **Review Schemas** (9 tests) - Review workflow validation
5. **Attachment Schemas** (5 tests) - File metadata validation
6. **Export Schemas** (1 test) - Export request validation
7. **Process Schemas** (10 tests) - RTO/RPO/MTPD validation
8. **Status Transition** (10 tests) - Valid and invalid transitions
9. **Datetime Validation** (2 tests) - UTC format validation
10. **Integration Tests** (2 tests) - Complex schema compositions

## Usage Examples

### Creating a Complete BCP Template

```python
from app.schemas.bc5_models import (
    TemplateSchema,
    SectionSchema,
    FieldSchema
)

# Define fields for recovery objectives
rto_field = FieldSchema(
    field_id="rto_minutes",
    label="Recovery Time Objective (minutes)",
    field_type="integer",
    required=True,
    validation_rules={"min": 0}
)

rpo_field = FieldSchema(
    field_id="rpo_minutes",
    label="Recovery Point Objective (minutes)",
    field_type="integer",
    required=True,
    validation_rules={"min": 0}
)

# Create section
recovery_section = SectionSchema(
    section_id="recovery_objectives",
    title="Recovery Objectives",
    description="Define RTO and RPO for critical processes",
    order=1,
    fields=[rto_field, rpo_field]
)

# Create template
template = TemplateSchema(
    name="IT Recovery Plan",
    version="1.0",
    description="Template for IT system recovery planning",
    sections=[recovery_section]
)
```

### Creating a Process with Recovery Objectives

```python
from app.schemas.bc5_models import BCProcessCreate

process = BCProcessCreate(
    plan_id=1,
    name="Critical Database Service",
    description="Primary customer database",
    rto_minutes=120,   # Must recover within 2 hours
    rpo_minutes=15,    # Max 15 minutes of data loss
    mtpd_minutes=240,  # Cannot be down more than 4 hours
    impact_rating="critical",
    dependencies_json={
        "services": ["storage", "network"],
        "vendors": ["cloud_provider"]
    }
)
```

### Validating Status Transitions

```python
from app.schemas.bc5_models import PlanStatusTransition, BCPlanListStatus

# Valid transition
try:
    transition = PlanStatusTransition(
        current_status=BCPlanListStatus.DRAFT,
        new_status=BCPlanListStatus.IN_REVIEW
    )
    # Proceed with status update
except ValidationError as e:
    # Handle invalid transition
    print(f"Invalid transition: {e}")
```

### Creating a Review with UTC Datetime

```python
from datetime import datetime, timezone
from app.schemas.bc5_models import ReviewCreate

review = ReviewCreate(
    plan_id=1,
    reviewer_user_ids=[2, 3],
    notes="Please review before end of year",
    due_date=datetime(2024, 12, 31, tzinfo=timezone.utc)
)
```

## Integration with Existing Code

The new schemas integrate seamlessly with existing BC5 API endpoints:
- Schemas are imported in `app/api/routes/bc5.py`
- Compatible with existing SQLAlchemy models in `app/models/bc_models.py`
- Follows existing validation patterns in `app/schemas/bc3_models.py`
- All existing tests continue to pass

## Benefits

1. **Type Safety** - Pydantic ensures all data meets the defined schema
2. **Clear Validation** - Descriptive error messages for validation failures
3. **Documentation** - Schemas serve as API documentation
4. **Consistency** - Uniform validation across all BC endpoints
5. **Extensibility** - Easy to add new fields and validations
6. **Testing** - Comprehensive test coverage ensures reliability

## Next Steps

To use these schemas in API endpoints:

1. Import the required schema:
   ```python
   from app.schemas.bc5_models import ReviewCreate, BCProcessCreate
   ```

2. Use as request/response models:
   ```python
   @router.post("/reviews")
   async def create_review(review: ReviewCreate):
       # review is already validated
       pass
   ```

3. Benefit from automatic OpenAPI documentation:
   - Schema examples in Swagger UI
   - Field descriptions
   - Validation rules visible to API consumers
