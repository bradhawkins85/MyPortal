# BC5 API Documentation

## Overview

The BC5 (Business Continuity 5) API provides comprehensive RESTful endpoints for managing business continuity plans, templates, versions, workflows, and related resources. The API implements role-based access control (RBAC) with four permission levels: viewer, editor, approver, and admin.

## Base URL

All BC5 endpoints are prefixed with `/api/bc`.

## Authentication

All endpoints require authentication. Include your session credentials in the request headers.

## Authorization (RBAC)

The BC5 system implements a 4-tier role-based access control system:

1. **Viewer** (`bc.viewer`): Read-only access to plans, templates, and related resources
2. **Editor** (`bc.editor`): Can create and edit plans, versions, and sections
3. **Approver** (`bc.approver`): Can approve reviews and request changes
4. **Admin** (`bc.admin`): Full administrative access including template management

Super admins automatically have all BC permissions.

## Endpoints

### Templates

Templates define the structure and schema for business continuity plans.

#### List Templates
```
GET /api/bc/templates
```

**Authorization:** Viewer or higher

**Response:** Array of template objects

**Example Response:**
```json
[
  {
    "id": 1,
    "name": "Government BCP Template",
    "version": "1.0",
    "is_default": true,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
]
```

#### Create Template
```
POST /api/bc/templates
```

**Authorization:** Admin only

**Request Body:**
```json
{
  "name": "Government BCP Template",
  "version": "1.0",
  "is_default": true,
  "schema_json": {
    "sections": [
      {
        "key": "overview",
        "title": "Plan Overview",
        "fields": []
      }
    ]
  }
}
```

#### Get Template
```
GET /api/bc/templates/{template_id}
```

**Authorization:** Viewer or higher

#### Update Template
```
PATCH /api/bc/templates/{template_id}
```

**Authorization:** Admin only

**Request Body:** Partial template object (only fields to update)

---

### Plans

Business continuity plans are the core entities in the BC system.

#### List Plans
```
GET /api/bc/plans?status={status}&q={search}&owner={user_id}&page={page}&per_page={per_page}
```

**Authorization:** Viewer or higher

**Query Parameters:**
- `status` (optional): Filter by plan status (`draft`, `in_review`, `approved`, `archived`)
- `q` (optional): Search query for plan title
- `owner` (optional): Filter by owner user ID
- `template_id` (optional): Filter by template
- `page` (default: 1): Page number
- `per_page` (default: 20, max: 100): Items per page

**Response:** Paginated response with plans

**Example Response:**
```json
{
  "items": [
    {
      "id": 1,
      "org_id": null,
      "title": "Disaster Recovery Plan 2024",
      "status": "approved",
      "template_id": 1,
      "current_version_id": 5,
      "owner_user_id": 1,
      "approved_at_utc": "2024-01-15T10:30:00Z",
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-15T10:30:00Z",
      "owner_name": "John Doe",
      "template_name": "Government BCP Template",
      "current_version_number": 2
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 20,
  "total_pages": 1
}
```

#### Create Plan
```
POST /api/bc/plans
```

**Authorization:** Editor or higher

**Request Body:**
```json
{
  "org_id": 1,
  "title": "Disaster Recovery Plan 2024",
  "status": "draft",
  "template_id": 1
}
```

#### Get Plan
```
GET /api/bc/plans/{plan_id}
```

**Authorization:** Viewer or higher

#### Update Plan
```
PATCH /api/bc/plans/{plan_id}
```

**Authorization:** Editor or higher

**Request Body:** Partial plan object

#### Delete Plan
```
DELETE /api/bc/plans/{plan_id}
```

**Authorization:** Admin only

**Note:** This permanently deletes the plan and all related data (versions, reviews, attachments, etc.).

---

### Versions

Plans support versioning to track changes over time.

#### List Versions
```
GET /api/bc/plans/{plan_id}/versions
```

**Authorization:** Viewer or higher

#### Create Version
```
POST /api/bc/plans/{plan_id}/versions
```

**Authorization:** Editor or higher

**Request Body:**
```json
{
  "summary_change_note": "Updated section 3 with new procedures",
  "content_json": {
    "sections": [
      {
        "key": "overview",
        "title": "Plan Overview",
        "content": {
          "purpose": "Ensure business continuity...",
          "scope": "All operations..."
        }
      }
    ]
  }
}
```

#### Get Version
```
GET /api/bc/plans/{plan_id}/versions/{version_id}
```

**Authorization:** Viewer or higher

#### Activate Version
```
POST /api/bc/plans/{plan_id}/versions/{version_id}/activate
```

**Authorization:** Editor or higher

Makes the specified version the active version, superseding all others.

---

### Workflow

The workflow system manages plan reviews and approvals.

#### Submit for Review
```
POST /api/bc/plans/{plan_id}/submit-for-review
```

**Authorization:** Editor or higher

**Request Body:**
```json
{
  "reviewer_user_ids": [2, 3, 4],
  "notes": "Please review by end of week"
}
```

**Effect:** Updates plan status to `in_review` and creates review requests.

#### Approve Review
```
POST /api/bc/plans/{plan_id}/reviews/{review_id}/approve
```

**Authorization:** Approver or higher (must be the assigned reviewer)

**Request Body:**
```json
{
  "notes": "Approved with minor suggestions"
}
```

**Effect:** Marks review as approved. If all reviews are approved, plan status updates to `approved`.

#### Request Changes
```
POST /api/bc/plans/{plan_id}/reviews/{review_id}/request-changes
```

**Authorization:** Approver or higher (must be the assigned reviewer)

**Request Body:**
```json
{
  "notes": "Please update section 3 with more detail"
}
```

**Effect:** Plan status reverts to `draft`.

#### Acknowledge Plan
```
POST /api/bc/plans/{plan_id}/acknowledge
```

**Authorization:** Viewer or higher

**Request Body:**
```json
{
  "ack_version_number": 2
}
```

Records that the current user has read and acknowledged the plan.

---

### Sections

Sections represent the content structure within plan versions.

#### List Sections
```
GET /api/bc/plans/{plan_id}/sections
```

**Authorization:** Viewer or higher

Returns sections from the current active version.

#### Update Section
```
PATCH /api/bc/plans/{plan_id}/sections/{section_key}
```

**Authorization:** Editor or higher

**Request Body:**
```json
{
  "content_json": {
    "field1": "Updated value",
    "field2": "New value"
  }
}
```

Performs a partial update, merging the provided content with existing section data.

---

### Attachments

Plans can have file attachments (documents, images, etc.).

#### List Attachments
```
GET /api/bc/plans/{plan_id}/attachments
```

**Authorization:** Viewer or higher

#### Upload Attachment
```
POST /api/bc/plans/{plan_id}/attachments
Content-Type: multipart/form-data
```

**Authorization:** Editor or higher

**Form Data:**
- `file`: The file to upload

**Response:**
```json
{
  "id": 1,
  "plan_id": 1,
  "file_name": "evacuation-map.pdf",
  "content_type": "application/pdf",
  "size_bytes": 524288,
  "uploaded_by_user_id": 1,
  "uploaded_at_utc": "2024-01-01T12:00:00Z",
  "storage_path": "bc_attachments/1/abc123_evacuation-map.pdf",
  "hash": "abc123..."
}
```

#### Delete Attachment
```
DELETE /api/bc/plans/{plan_id}/attachments/{attachment_id}
```

**Authorization:** Editor or higher

---

### Exports

Plans can be exported to DOCX or PDF format.

**Note:** Export endpoints are rate-limited to prevent abuse.

#### Export to DOCX
```
POST /api/bc/plans/{plan_id}/export/docx
```

**Authorization:** Viewer or higher

**Request Body:**
```json
{
  "version_id": 5,
  "include_attachments": false
}
```

**Response:**
```json
{
  "export_url": "/api/bc/exports/1/v2.docx",
  "format": "docx",
  "version_id": 5,
  "generated_at": "2024-01-01T12:00:00Z",
  "file_hash": "def456..."
}
```

#### Export to PDF
```
POST /api/bc/plans/{plan_id}/export/pdf
```

Similar to DOCX export.

---

### Audit and Change Log

#### Get Audit Trail
```
GET /api/bc/plans/{plan_id}/audit?limit={limit}
```

**Authorization:** Viewer or higher

**Query Parameters:**
- `limit` (default: 100, max: 500): Number of entries to return

Returns chronological audit trail of all actions on the plan.

**Example Response:**
```json
[
  {
    "id": 1,
    "plan_id": 1,
    "action": "created",
    "actor_user_id": 1,
    "actor_name": "John Doe",
    "details_json": {
      "title": "Disaster Recovery Plan 2024",
      "status": "draft"
    },
    "at_utc": "2024-01-01T00:00:00Z"
  },
  {
    "id": 2,
    "plan_id": 1,
    "action": "version_created",
    "actor_user_id": 1,
    "actor_name": "John Doe",
    "details_json": {
      "version_number": 1,
      "version_id": 1
    },
    "at_utc": "2024-01-01T00:15:00Z"
  }
]
```

#### Get Change Log
```
GET /api/bc/plans/{plan_id}/change-log
```

**Authorization:** Viewer or higher

Returns change log entries linked to this plan from the `changes/` folder.

---

## Error Responses

All endpoints follow standard HTTP status codes:

- `200 OK`: Successful GET/PATCH request
- `201 Created`: Successful POST request that creates a resource
- `204 No Content`: Successful DELETE request
- `400 Bad Request`: Invalid request data (validation error)
- `401 Unauthorized`: Authentication required
- `403 Forbidden`: Insufficient permissions
- `404 Not Found`: Resource not found
- `409 Conflict`: Resource conflict (e.g., duplicate)
- `500 Internal Server Error`: Server error

**Error Response Format:**
```json
{
  "detail": "Error message here"
}
```

---

## Rate Limiting

Export endpoints (`/export/docx` and `/export/pdf`) are rate-limited to prevent abuse. If you exceed the rate limit, you'll receive a `429 Too Many Requests` response.

---

## Security

### CSRF Protection

All state-changing endpoints (POST, PATCH, DELETE) are protected against CSRF attacks. Include the CSRF token in your requests.

### Input Validation

All request payloads are validated using Pydantic schemas. Invalid data will result in a `400 Bad Request` response with detailed validation errors.

### Audit Logging

All actions are logged in the audit trail with:
- User who performed the action
- Timestamp (UTC)
- Action type
- Relevant details

### File Security

Uploaded files are:
- Validated for size and type
- Stored securely with SHA256 hashing
- Access-controlled based on plan permissions

---

## Best Practices

1. **Use Pagination**: When listing plans, use pagination parameters to limit response size
2. **Cache Templates**: Templates change infrequently; cache them client-side
3. **Version Control**: Create new versions for significant changes rather than modifying existing content
4. **Audit Trail**: Review the audit trail regularly for compliance
5. **Acknowledgments**: Ensure users acknowledge plans after significant updates

---

## Examples

### Complete Workflow Example

1. **Create a plan:**
```bash
curl -X POST /api/bc/plans \
  -H "Content-Type: application/json" \
  -d '{"title": "DR Plan 2024", "template_id": 1}'
```

2. **Create a version:**
```bash
curl -X POST /api/bc/plans/1/versions \
  -H "Content-Type: application/json" \
  -d '{"summary_change_note": "Initial version", "content_json": {...}}'
```

3. **Submit for review:**
```bash
curl -X POST /api/bc/plans/1/submit-for-review \
  -H "Content-Type: application/json" \
  -d '{"reviewer_user_ids": [2, 3]}'
```

4. **Approve (as reviewer):**
```bash
curl -X POST /api/bc/plans/1/reviews/1/approve \
  -H "Content-Type: application/json" \
  -d '{"notes": "Approved"}'
```

5. **Export to PDF:**
```bash
curl -X POST /api/bc/plans/1/export/pdf \
  -H "Content-Type: application/json" \
  -d '{"version_id": 1}'
```

---

## Support

For issues or questions about the BC5 API, please contact the system administrator or refer to the Swagger documentation at `/docs`.
