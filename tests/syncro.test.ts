import { test } from 'node:test';
import assert from 'node:assert/strict';
import { getSyncroContacts, getSyncroAssets } from '../src/syncro';

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
      `https://example.com/contacts?customer_id=${customerId}`
    );
  } finally {
    // restore env and fetch
    if (originalFetch) {
      global.fetch = originalFetch;
    }
    delete process.env.SYNCRO_WEBHOOK_URL;
  }
});

// Ensure getSyncroAssets requests the correct assets endpoint
test('getSyncroAssets uses /assets endpoint', async () => {
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
      `https://example.com/assets?customer_id=${customerId}`
    );
  } finally {
    if (originalFetch) {
      global.fetch = originalFetch;
    }
    delete process.env.SYNCRO_WEBHOOK_URL;
  }
});
