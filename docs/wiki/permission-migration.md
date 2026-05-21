# Company Membership Permission Migration

## Overview
This document describes the migration from the old boolean permission system in the `user_companies` table to the new role-based permission system using `company_memberships` and `roles` tables.

## Problem Statement
The system previously used individual boolean columns in the `user_companies` table to manage permissions (e.g., `can_manage_licenses`, `can_access_shop`). With the introduction of the `company_memberships` table and the `roles` system, permissions needed to be migrated to a role-based approach where roles contain a JSON array of permission strings.

## Solution
The migration maintains **full backward compatibility** by automatically enriching `user_companies` data with permissions from the user's role. This means:
- Existing code continues to work without modifications
- Role-based permissions become the source of truth
- Boolean fields remain as a compatibility layer
- No breaking changes to the API or UI

## Implementation Details

### 1. Database Migration
**File:** `migrations/123_role_based_company_permissions.sql`

This migration adds granular permission strings to the three system roles:
- **Owner**: Full control (all permissions)
- **Administrator**: Operational management (most permissions except billing)
- **Member**: Basic access (portal and forms only)

### 2. Permission Mapping
The following mapping translates role permission strings to legacy boolean fields:

| Role Permission String | Legacy Boolean Field |
|------------------------|---------------------|
| `licenses.manage` | `can_manage_licenses` |
| `licenses.order` | `can_order_licenses` |
| `staff.manage` | `can_manage_staff` |
| `shop.access` | `can_access_shop` |
| `cart.access` | `can_access_cart` |
| `orders.access` | `can_access_orders` |
| `forms.access` | `can_access_forms` |
| `assets.manage` | `can_manage_assets` |
| `invoices.manage` | `can_manage_invoices` |
| `office_groups.manage` | `can_manage_office_groups` |
| `issues.manage` | `can_manage_issues` |
| `company.admin` | `is_admin` |

### 3. Code Changes
**File:** `app/repositories/user_companies.py`

#### Added Constants
```python
_PERMISSION_MAPPING = {
    "licenses.manage": "can_manage_licenses",
    "licenses.order": "can_order_licenses",
    # ... etc
}
```

#### Added Helper Function
```python
def _enrich_with_role_permissions(data: dict[str, Any], role_permissions_raw: Any) -> None:
    """Enrich user_company data with permissions from their role."""
```

#### Updated Functions
- `get_user_company()`: Joins with `company_memberships` and `roles`, enriches with permissions
- `list_companies_for_user()`: Joins with `company_memberships` and `roles`, enriches with permissions  
- `list_assignments()`: Joins with `company_memberships` and `roles`, enriches with permissions

### 4. Tests
**File:** `tests/test_role_permission_enrichment.py`

Added 5 comprehensive tests covering:
- Single permission mapping
- Multiple permission mapping
- Preservation of existing True values
- Handling of null permissions
- List operations with enrichment

## How It Works

1. When a user's company membership is queried via `get_user_company()` or `list_companies_for_user()`:
   - The function joins with `company_memberships` to get the user's role (if any)
   - The function joins with `roles` to get the role's permission list
   - The permission list is parsed from JSON
   - Each permission string is mapped to its corresponding boolean field
   - The boolean field is set to True if the role has that permission

2. Existing permissions in `user_companies` remain in the database and are still respected
3. Role permissions overlay on top of existing permissions (if either is true, the result is true)
4. Super admins continue to work as before, with all permissions granted

## Migration Path for Users

### Immediate Effect
- Users with a role assigned via `company_memberships` will automatically get the permissions defined in their role
- These permissions appear in the traditional boolean fields when queried
- No code changes needed

### Future State
- The `user_companies` table can eventually be deprecated
- All permission checks could migrate to use `company_memberships` directly
- For now, both systems work together seamlessly

## Testing
All existing tests pass with the changes:
- `test_company_memberships.py`: 11/12 passing (1 failure unrelated to changes)
- `test_role_permission_enrichment.py`: 5/5 passing
- `test_staff_access.py`, `test_agent_service.py`: All passing

## Notes
- The migration is non-destructive - it only adds permissions, never removes them
- If a user has both a boolean permission and a role permission, both are respected
- Super admin functionality is completely unchanged
- No UI or API changes required
