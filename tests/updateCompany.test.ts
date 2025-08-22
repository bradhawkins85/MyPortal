import { test } from 'node:test';
import assert from 'node:assert/strict';
process.env.SESSION_SECRET = 'test';
process.env.TOTP_ENCRYPTION_KEY = 'test';
const { updateCompanyHandler } = require('../src/server');

test('omitting address keeps existing address', async () => {
  const getCompanyByIdCalls: number[] = [];
  const updateCompanyCalls: any[] = [];
  const deps = {
    getCompanyById: async (id: number) => {
      getCompanyByIdCalls.push(id);
      return { id, name: 'Existing Name', address: 'Existing Address' };
    },
    updateCompany: async (id: number, name: string, address: string | null) => {
      updateCompanyCalls.push([id, name, address]);
    },
    updateCompanyIds: async () => {},
  };
  const req: any = { params: { id: '1' }, body: { name: 'New Name' } };
  const res: any = { json: () => {} };
  await updateCompanyHandler(req, res, deps);
  assert.deepEqual(getCompanyByIdCalls, [1]);
  assert.deepEqual(updateCompanyCalls, [[1, 'New Name', 'Existing Address']]);
});

test('omitting name keeps existing name', async () => {
  const getCompanyByIdCalls: number[] = [];
  const updateCompanyCalls: any[] = [];
  const deps = {
    getCompanyById: async (id: number) => {
      getCompanyByIdCalls.push(id);
      return { id, name: 'Existing Name', address: 'Existing Address' };
    },
    updateCompany: async (id: number, name: string, address: string | null) => {
      updateCompanyCalls.push([id, name, address]);
    },
    updateCompanyIds: async () => {},
  };
  const req: any = { params: { id: '1' }, body: { address: 'New Address' } };
  const res: any = { json: () => {} };
  await updateCompanyHandler(req, res, deps);
  assert.deepEqual(getCompanyByIdCalls, [1]);
  assert.deepEqual(updateCompanyCalls, [[1, 'Existing Name', 'New Address']]);
});
