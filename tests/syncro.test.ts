import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  getSyncroContacts,
  getSyncroAssets,
  extractAssetDetails,
} from '../src/syncro';

// Ensure getSyncroContacts requests the correct contacts endpoint
// Uses a mock fetch implementation to capture the requested URL

test('getSyncroContacts uses /contacts endpoint', async () => {
  const customerId = 123;
  let requestedUrl: string | undefined;

  const mockFetch = async (url: string, _init?: RequestInit) => {
    requestedUrl = url;
    return {
      ok: true,
      json: async () => [] as any,
    } as Response;
  };

  const originalFetch = global.fetch;
  // @ts-expect-error assign mock
  global.fetch = mockFetch;
  process.env.SYNCRO_WEBHOOK_URL = 'https://example.com';

  try {
    await getSyncroContacts(customerId);
    assert.equal(
      requestedUrl,
      `https://example.com/api/v1/contacts?customer_id=${customerId}`
    );
  } finally {
    // restore env and fetch
    if (originalFetch) {
      global.fetch = originalFetch;
    }
    delete process.env.SYNCRO_WEBHOOK_URL;
  }
});

// Ensure getSyncroAssets requests the correct customer assets endpoint
test('getSyncroAssets uses /customer_assets endpoint', async () => {
  const customerId = 321;
  let requestedUrl: string | undefined;

  const mockFetch = async (url: string, _init?: RequestInit) => {
    requestedUrl = url;
    return {
      ok: true,
      json: async () => [] as any,
    } as Response;
  };

  const originalFetch = global.fetch;
  // @ts-expect-error assign mock
  global.fetch = mockFetch;
  process.env.SYNCRO_WEBHOOK_URL = 'https://example.com';

  try {
    await getSyncroAssets(customerId);
    assert.equal(
      requestedUrl,
      `https://example.com/api/v1/customer_assets?customer_id=${customerId}&page=1`
    );
  } finally {
    if (originalFetch) {
      global.fetch = originalFetch;
    }
    delete process.env.SYNCRO_WEBHOOK_URL;
  }
});

// Ensure getSyncroAssets handles 404 responses gracefully
test('getSyncroAssets returns empty array on 404', async () => {
  const customerId = 999;

  const mockFetch = async (_url: string, _init?: RequestInit) => {
    return {
      ok: false,
      status: 404,
    } as Response;
  };

  const originalFetch = global.fetch;
  // @ts-expect-error assign mock
  global.fetch = mockFetch;
  process.env.SYNCRO_WEBHOOK_URL = 'https://example.com';

  try {
    const assets = await getSyncroAssets(customerId);
    assert.deepEqual(assets, []);
  } finally {
    if (originalFetch) {
      global.fetch = originalFetch;
    }
    delete process.env.SYNCRO_WEBHOOK_URL;
  }
});

test('getSyncroAssets paginates through pages', async () => {
  const customerId = 456;
  const requestedUrls: string[] = [];
  const responses = [
    {
      ok: true,
      json: async () => ({ assets: [{ id: 1 }], meta: { total_pages: 2 } }),
    },
    {
      ok: true,
      json: async () => ({ assets: [{ id: 2 }], meta: { total_pages: 2 } }),
    },
  ] as Response[];

  const mockFetch = async (url: string, _init?: RequestInit) => {
    requestedUrls.push(url);
    return responses.shift()!;
  };

  const originalFetch = global.fetch;
  // @ts-expect-error assign mock
  global.fetch = mockFetch;
  process.env.SYNCRO_WEBHOOK_URL = 'https://example.com';

  try {
    const assets = await getSyncroAssets(customerId);
    assert.deepEqual(assets, [{ id: 1 }, { id: 2 }]);
    assert.equal(
      requestedUrls[0],
      `https://example.com/api/v1/customer_assets?customer_id=${customerId}&page=1`
    );
    assert.equal(
      requestedUrls[1],
      `https://example.com/api/v1/customer_assets?customer_id=${customerId}&page=2`
    );
  } finally {
    if (originalFetch) {
      global.fetch = originalFetch;
    }
    delete process.env.SYNCRO_WEBHOOK_URL;
  }
});

test('extractAssetDetails maps nested properties', () => {
  const asset = {
    id: 1,
    status: 'active',
    type: 'desktop',
    properties: {
      kabuto_information: {
        general: { serial_number: 'ABC', form_factor: 'desktop', name: 'Device' },
        os: { name: 'Windows 11 Pro' },
        cpu: [{ name: 'Intel CPU' }],
        ram_gb: 8,
        hdd: [{ size: '238.47 GB' }],
        last_synced_at: '2025-07-27T06:11:41.000Z',
        motherboard: { manufacturer: 'HP' },
        last_user: 'DOMAIN\\user',
        cpu_age: '2',
      },
      form_factor: 'Physical Desktop',
      'Performance Score': '7',
      warranty_status: 'in-warranty',
      warranty_end_date: '2026-01-01',
    },
  } as any;

  const details = extractAssetDetails(asset);
  assert.equal(details.type, 'desktop');
  assert.equal(details.serial_number, 'ABC');
  assert.equal(details.status, 'active');
  assert.equal(details.os_name, 'Windows 11 Pro');
  assert.equal(details.cpu_name, 'Intel CPU');
  assert.equal(details.ram_gb, 8);
  assert.equal(details.hdd_size, '238.47 GB');
  assert.equal(details.last_sync, '2025-07-27T06:11:41.000Z');
  assert.equal(details.motherboard_manufacturer, 'HP');
  assert.equal(details.form_factor, 'Physical Desktop');
  assert.equal(details.last_user, 'DOMAIN\\user');
  assert.equal(details.cpu_age, 2);
  assert.equal(details.performance_score, 7);
  assert.equal(details.warranty_status, 'in-warranty');
  assert.equal(details.warranty_end_date, '2026-01-01');
});

test('extractAssetDetails normalises CPUAge variants to cpu_age', () => {
  const asset = {
    id: 2,
    properties: {
      CPUAge: '3 years',
    },
  } as any;

  const details = extractAssetDetails(asset);
  assert.equal(details.cpu_age, 3);
});

test('upsertAsset uses syncro id when serial missing', async () => {
  const origEnv = {
    TOTP_ENCRYPTION_KEY: process.env.TOTP_ENCRYPTION_KEY,
    DB_HOST: process.env.DB_HOST,
    DB_USER: process.env.DB_USER,
    DB_PASSWORD: process.env.DB_PASSWORD,
    DB_NAME: process.env.DB_NAME,
  };
  process.env.TOTP_ENCRYPTION_KEY = 'test';
  process.env.DB_HOST = 'localhost';
  process.env.DB_USER = 'user';
  process.env.DB_PASSWORD = 'pass';
  process.env.DB_NAME = 'db';

  const { upsertAsset } = await import('../src/queries');
  const { pool } = await import('../src/db');

  const originalQuery = pool.query;
  const originalExecute = pool.execute;
  const queries: any[] = [];
  const executions: any[] = [];
  let selectCount = 0;

  // @ts-expect-error override for test
  pool.query = async (sql: string, params: any[]) => {
    queries.push({ sql, params });
    if (sql.startsWith('SELECT id FROM assets')) {
      selectCount++;
      if (selectCount === 1) {
        return [[], []];
      }
      return [[{ id: 1 }], []];
    }
    return [[], []];
  };

  // @ts-expect-error override for test
  pool.execute = async (sql: string, params: any[]) => {
    executions.push({ sql, params });
    return [undefined, undefined];
  };

  try {
    await upsertAsset(
      1,
      'Asset One',
      null,
      null,
      null,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      'sync-1'
    );
    await upsertAsset(
      1,
      'Asset One Updated',
      null,
      null,
      null,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      'sync-1'
    );

    assert(queries[0].sql.includes('syncro_asset_id'));
    assert.equal(executions.length, 2);
    assert(executions[0].sql.startsWith('INSERT INTO assets'));
    assert(executions[1].sql.startsWith('UPDATE assets'));
  } finally {
    // @ts-expect-error restore originals
    pool.query = originalQuery;
    // @ts-expect-error restore originals
    pool.execute = originalExecute;
    process.env.TOTP_ENCRYPTION_KEY = origEnv.TOTP_ENCRYPTION_KEY;
    process.env.DB_HOST = origEnv.DB_HOST;
    process.env.DB_USER = origEnv.DB_USER;
    process.env.DB_PASSWORD = origEnv.DB_PASSWORD;
    process.env.DB_NAME = origEnv.DB_NAME;
  }
});

test('upsertAsset updates when serial changes but sync id matches', async () => {
  const origEnv = {
    TOTP_ENCRYPTION_KEY: process.env.TOTP_ENCRYPTION_KEY,
    DB_HOST: process.env.DB_HOST,
    DB_USER: process.env.DB_USER,
    DB_PASSWORD: process.env.DB_PASSWORD,
    DB_NAME: process.env.DB_NAME,
  };
  process.env.TOTP_ENCRYPTION_KEY = 'test';
  process.env.DB_HOST = 'localhost';
  process.env.DB_USER = 'user';
  process.env.DB_PASSWORD = 'pass';
  process.env.DB_NAME = 'db';

  const { upsertAsset } = await import('../src/queries');
  const { pool } = await import('../src/db');

  const originalQuery = pool.query;
  const originalExecute = pool.execute;
  const queries: any[] = [];
  const executions: any[] = [];
  let selectCount = 0;

  // @ts-expect-error override for test
  pool.query = async (sql: string, params: any[]) => {
    queries.push({ sql, params });
    if (sql.startsWith('SELECT id FROM assets')) {
      selectCount++;
      if (selectCount === 3) {
        return [[{ id: 1 }], []];
      }
      return [[], []];
    }
    return [[], []];
  };

  // @ts-expect-error override for test
  pool.execute = async (sql: string, params: any[]) => {
    executions.push({ sql, params });
    return [undefined, undefined];
  };

  try {
    await upsertAsset(
      1,
      'Asset One',
      null,
      'SERIAL-1',
      null,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      'sync-1'
    );
    await upsertAsset(
      1,
      'Asset One Updated',
      null,
      'SERIAL-2',
      null,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      'sync-1'
    );

    assert.equal(queries.length, 3);
    assert(queries[0].sql.includes('syncro_asset_id'));
    assert(queries[1].sql.includes('serial_number'));
    assert(queries[2].sql.includes('syncro_asset_id'));
    assert.equal(executions.length, 2);
    assert(executions[0].sql.startsWith('INSERT INTO assets'));
    assert(executions[1].sql.startsWith('UPDATE assets'));
  } finally {
    // @ts-expect-error restore originals
    pool.query = originalQuery;
    // @ts-expect-error restore originals
    pool.execute = originalExecute;
    process.env.TOTP_ENCRYPTION_KEY = origEnv.TOTP_ENCRYPTION_KEY;
    process.env.DB_HOST = origEnv.DB_HOST;
    process.env.DB_USER = origEnv.DB_USER;
    process.env.DB_PASSWORD = origEnv.DB_PASSWORD;
    process.env.DB_NAME = origEnv.DB_NAME;
  }
});

test('upsertAsset updates asset with matching sync id even if serial matches another asset', async () => {
  const origEnv = {
    TOTP_ENCRYPTION_KEY: process.env.TOTP_ENCRYPTION_KEY,
    DB_HOST: process.env.DB_HOST,
    DB_USER: process.env.DB_USER,
    DB_PASSWORD: process.env.DB_PASSWORD,
    DB_NAME: process.env.DB_NAME,
  };
  process.env.TOTP_ENCRYPTION_KEY = 'test';
  process.env.DB_HOST = 'localhost';
  process.env.DB_USER = 'user';
  process.env.DB_PASSWORD = 'pass';
  process.env.DB_NAME = 'db';

  const { upsertAsset } = await import('../src/queries');
  const { pool } = await import('../src/db');

  const originalQuery = pool.query;
  const originalExecute = pool.execute;
  const queries: any[] = [];
  const executions: any[] = [];

  // @ts-expect-error override for test
  pool.query = async (sql: string, params: any[]) => {
    queries.push({ sql, params });
    if (sql.includes('syncro_asset_id')) {
      return [[{ id: 1 }], []];
    }
    if (sql.includes('serial_number')) {
      // would represent a conflicting asset
      return [[{ id: 2 }], []];
    }
    return [[], []];
  };

  // @ts-expect-error override for test
  pool.execute = async (sql: string, params: any[]) => {
    executions.push({ sql, params });
    return [undefined, undefined];
  };

  try {
    await upsertAsset(
      1,
      'Asset One Updated',
      null,
      'SERIAL-2',
      null,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      'sync-1'
    );

    assert.equal(queries.length, 1);
    assert(queries[0].sql.includes('syncro_asset_id'));
    assert.equal(executions.length, 1);
    assert(executions[0].sql.startsWith('UPDATE assets'));
    assert.equal(
      executions[0].params[executions[0].params.length - 1],
      1
    );
  } finally {
    // @ts-expect-error restore originals
    pool.query = originalQuery;
    // @ts-expect-error restore originals
    pool.execute = originalExecute;
    process.env.TOTP_ENCRYPTION_KEY = origEnv.TOTP_ENCRYPTION_KEY;
    process.env.DB_HOST = origEnv.DB_HOST;
    process.env.DB_USER = origEnv.DB_USER;
    process.env.DB_PASSWORD = origEnv.DB_PASSWORD;
    process.env.DB_NAME = origEnv.DB_NAME;
  }
});
