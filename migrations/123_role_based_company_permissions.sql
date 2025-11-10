-- Add granular permission strings to roles to replace user_companies boolean fields
-- This migration maps old permission fields to new role-based permissions

-- Define new permission strings that correspond to old user_companies fields:
-- can_manage_licenses -> licenses.manage
-- can_order_licenses -> licenses.order
-- can_manage_staff -> staff.manage
-- can_access_shop -> shop.access
-- can_access_cart -> cart.access
-- can_access_orders -> orders.access
-- can_access_forms -> forms.access
-- can_manage_assets -> assets.manage
-- can_manage_invoices -> invoices.manage
-- can_manage_office_groups -> office_groups.manage
-- can_manage_issues -> issues.manage
-- is_admin -> company.admin

-- Update Owner role to have all permissions
UPDATE roles 
SET permissions = JSON_ARRAY(
  'company.manage',
  'membership.manage',
  'billing.manage',
  'audit.view',
  'company.admin',
  'licenses.manage',
  'licenses.order',
  'staff.manage',
  'shop.access',
  'cart.access',
  'orders.access',
  'forms.access',
  'assets.manage',
  'invoices.manage',
  'office_groups.manage',
  'issues.manage'
)
WHERE name = 'Owner' AND is_system = 1;

-- Update Administrator role with appropriate permissions
UPDATE roles 
SET permissions = JSON_ARRAY(
  'membership.manage',
  'audit.view',
  'company.admin',
  'licenses.manage',
  'licenses.order',
  'staff.manage',
  'shop.access',
  'cart.access',
  'orders.access',
  'forms.access',
  'assets.manage',
  'invoices.manage',
  'office_groups.manage',
  'issues.manage'
)
WHERE name = 'Administrator' AND is_system = 1;

-- Update Member role with basic permissions
UPDATE roles 
SET permissions = JSON_ARRAY(
  'portal.access',
  'forms.access'
)
WHERE name = 'Member' AND is_system = 1;
