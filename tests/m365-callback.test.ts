import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import request from 'supertest';

process.env.SESSION_SECRET = 'test';
process.env.TOTP_ENCRYPTION_KEY = 'test';

test('failed Microsoft 365 authorization reports error to user', async () => {
  const queries = require('../src/queries');
  const crypto = require('../src/crypto');
  const msal = require('@azure/msal-node');
  const logger = require('../src/logger');

  mock.method(queries, 'getM365Credentials', async () => ({
    tenant_id: 'tenant',
    client_id: 'client',
    client_secret: 'secret',
  }));
  mock.method(crypto, 'decryptSecret', () => 'decrypted');
  mock.method(
    msal.ConfidentialClientApplication.prototype,
    'acquireTokenByCode',
    async () => {
      throw new Error('oauth failed');
    }
  );
  const logSpy = mock.method(logger, 'logError', () => {});

  const { app } = require('../src/server');

  const res = await request(app).get('/m365/callback?code=abc&state=1');
  assert.equal(res.status, 302);
  const redirect = res.headers.location;
  assert.ok(redirect.startsWith('/m365?error='));
  assert.equal(
    decodeURIComponent(redirect.split('=')[1]),
    'Authorization with Microsoft 365 failed. Please try again.'
  );
  assert.equal(logSpy.mock.callCount(), 1);

  mock.restoreAll();
});
