import { test } from 'node:test';
import assert from 'node:assert/strict';
process.env.SESSION_SECRET = 'test';
process.env.TOTP_ENCRYPTION_KEY = 'test';
const { getCompanyAppPriceHandler } = require('../src/server');

test('falls back to default price when company price missing', async () => {
  const req: any = { params: { companyId: '1', appId: '2' } };
  const jsonCalls: any[] = [];
  const res: any = {
    json: (data: any) => { jsonCalls.push(data); },
    status: (code: number) => { res.statusCode = code; return res; },
  };
  const deps = {
    getAppPrice: async () => null,
    getAppById: async () => ({ default_price: 10 }),
  };
  await getCompanyAppPriceHandler(req, res, deps);
  assert.equal(res.statusCode, undefined);
  assert.deepEqual(jsonCalls, [{ price: 10 }]);
});

test('returns 404 when app not found', async () => {
  const req: any = { params: { companyId: '1', appId: '2' } };
  const jsonCalls: any[] = [];
  const res: any = {
    json: (data: any) => { jsonCalls.push(data); },
    status: (code: number) => { res.statusCode = code; return res; },
  };
  const deps = {
    getAppPrice: async () => null,
    getAppById: async () => null,
  };
  await getCompanyAppPriceHandler(req, res, deps);
  assert.equal(res.statusCode, 404);
  assert.deepEqual(jsonCalls, [{ error: 'App not found' }]);
});
