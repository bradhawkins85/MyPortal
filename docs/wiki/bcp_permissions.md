# BCP (Business Continuity Planning) Permissions

This document outlines the permission model for the BCP system.

## Permission Types

The BCP system uses four primary permissions:

### 1. `bcp:view`
**Description**: Allows users to view BCP plans and related data.

**Grants Access To:**
- View BCP plans
- View critical activities and business impact analysis
- View risk assessments
- View incident history
- View contacts and roles
- View recovery actions
- View training and review history
- Export plans (read-only format)

**Typical Roles**: All staff members who should be aware of business continuity procedures.

### 2. `bcp:edit`
**Description**: Allows users to create and edit BCP plans and components.

**Grants Access To:**
- Create new BCP plans
- Edit existing plans
- Add/modify critical activities
- Add/modify risk assessments
- Add/modify contacts and roles
- Add/modify recovery actions
- Record training sessions
- Conduct plan reviews
- Update plan versions

**Typical Roles**: BCP coordinators, managers, and administrators responsible for maintaining continuity plans.

### 3. `bcp:export`
**Description**: Allows users to export BCP plans in various formats.

**Grants Access To:**
- Export plans to PDF
- Export plans to DOCX
- Export plans with attachments
- Export plans with sensitive information (if applicable)
- Batch export multiple plans

**Typical Roles**: Senior management, auditors, and compliance officers who need to review or distribute plans externally.

### 4. `bcp:incident:run`
**Description**: Allows users to activate and manage BCP incidents.

**Grants Access To:**
- Activate a BCP incident
- Update incident status
- Complete checklist items
- Log incident events
- Close incidents
- Assign roles during incidents
- Coordinate incident response

**Typical Roles**: Incident commanders, emergency response team members, and senior management who coordinate business continuity responses.

## Permission Hierarchy

The permissions are hierarchical in nature:

```
bcp:incident:run (highest privilege)
  ├── bcp:export
  │   ├── bcp:edit
  │   │   └── bcp:view (base permission)
```

- Users with `bcp:incident:run` automatically have export, edit, and view permissions
- Users with `bcp:export` automatically have edit and view permissions  
- Users with `bcp:edit` automatically have view permissions
- Users with only `bcp:view` have read-only access

## Multi-Tenancy

All BCP entities are scoped by `company_id` to support multi-tenant environments:

- Users can only access plans for companies they have access to
- Company membership is required in addition to BCP permissions
- Permissions are checked at both the company level and the plan level

## Implementation Notes

### Database Level
- All BCP tables include `company_id` foreign key (except core plan table which owns it)
- Indexes on `company_id` for performance
- Foreign key constraints with CASCADE delete to maintain referential integrity

### Application Level
- Permissions are checked using the role-based access control (RBAC) system
- Permission checks occur at API endpoints before data access
- Company membership verified before BCP permission checks

### API Endpoints
- GET endpoints require `bcp:view` or higher
- POST/PUT/PATCH endpoints require `bcp:edit` or higher
- Export endpoints require `bcp:export` or higher
- Incident management endpoints require `bcp:incident:run`

## Security Considerations

1. **Sensitive Information**: BCP plans may contain sensitive business information. Ensure proper authentication and authorization before granting permissions.

2. **Incident Activation**: The `bcp:incident:run` permission should be restricted to trained personnel who understand the implications of activating a business continuity plan.

3. **Audit Trail**: All BCP operations are logged for compliance and accountability:
   - Plan creations and modifications
   - Incident activations and closures
   - Export operations
   - Permission changes

4. **Data Retention**: Maintain historical records of:
   - Plan versions
   - Incident responses
   - Training sessions
   - Review cycles

## Example Use Cases

### Scenario 1: New Employee Onboarding
- Grant `bcp:view` to allow employee to familiarize with continuity procedures
- No edit or incident activation rights until trained

### Scenario 2: BCP Coordinator
- Grant `bcp:edit` to allow maintaining and updating plans
- Grant `bcp:export` for distributing plans to stakeholders
- May or may not have `bcp:incident:run` depending on role

### Scenario 3: Incident Commander
- Grant `bcp:incident:run` to allow full incident management
- Includes all lower permissions for complete plan access

### Scenario 4: External Auditor (Temporary)
- Grant temporary `bcp:view` for assessment purposes
- Optionally grant `bcp:export` for audit documentation
- Revoke after audit completion

## Configuration

Permissions are configured in the application's role management system:

1. Navigate to Admin → Roles & Permissions
2. Create or edit a role
3. Select appropriate BCP permissions from the list
4. Assign role to users or groups
5. Permissions take effect immediately

## See Also

- [BCP Data Models](../app/models/bcp_models.py)
- [BCP Schemas](../app/schemas/bcp_models.py)
- [BCP Migrations](../migrations/126_bc02_bcp_data_model.sql)
- Role-Based Access Control (RBAC) Documentation
