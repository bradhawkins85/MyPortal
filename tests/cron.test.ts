import { test } from 'node:test';
import assert from 'node:assert/strict';
import { getRandomDailyCron } from '../src/cron';

test('getRandomDailyCron produces daily cron with valid hour and minute', () => {
  const expr = getRandomDailyCron();
  const parts = expr.split(' ');
  assert.equal(parts.length, 5);
  const minute = parseInt(parts[0], 10);
  const hour = parseInt(parts[1], 10);
  assert.ok(minute >= 0 && minute < 60);
  assert.ok(hour >= 0 && hour < 24);
  assert.equal(parts[2], '*');
  assert.equal(parts[3], '*');
  assert.equal(parts[4], '*');
});
