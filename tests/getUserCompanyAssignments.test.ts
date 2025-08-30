import { test } from 'node:test';
import assert from 'node:assert/strict';

process.env.DB_HOST = 'localhost';
process.env.DB_USER = 'root';
process.env.DB_PASSWORD = '';
process.env.DB_NAME = 'test';
process.env.TOTP_ENCRYPTION_KEY = 'test';

const { getUserCompanyAssignments } = require('../src/queries');
const { pool } = require('../src/db');

test('getUserCompanyAssignments normalizes boolean fields', async () => {
  const originalQuery = pool.query;
  (pool.query as any) = async () => [[{
    user_id: 1,
    company_id: 2,
    can_manage_licenses: '0',
    staff_permission: '3',
    can_manage_office_groups: '1',
    can_manage_assets: '0',
    can_manage_invoices: '1',
    can_order_licenses: '0',
    can_access_shop: '1',
    is_admin: '0',
    company_name: 'Acme',
    is_vip: '1',
    email: 'user@example.com',
  }]] as any;

  try {
    const result = await getUserCompanyAssignments();
    assert.deepEqual(result, [{
      user_id: 1,
      company_id: 2,
      can_manage_licenses: 0,
      staff_permission: 3,
      can_manage_office_groups: 1,
      can_manage_assets: 0,
      can_manage_invoices: 1,
      can_order_licenses: 0,
      can_access_shop: 1,
      is_admin: 0,
      company_name: 'Acme',
      is_vip: 1,
      email: 'user@example.com',
    }]);
  } finally {
    (pool.query as any) = originalQuery;
  }
});

