import { test } from 'node:test';
import assert from 'node:assert/strict';
import { findExistingStaff, BasicStaff } from '../src/staff-import';

test('findExistingStaff matches by email when provided', () => {
  const existing: BasicStaff[] = [
    { first_name: 'John', last_name: 'Doe', email: 'john@example.com' },
  ];
  const result = findExistingStaff(existing, 'John', 'Doe', 'john@example.com');
  assert.equal(result, existing[0]);
});

test('findExistingStaff treats same name different email as new staff', () => {
  const existing: BasicStaff[] = [
    { first_name: 'John', last_name: 'Doe', email: 'john@example.com' },
  ];
  const result = findExistingStaff(existing, 'John', 'Doe', 'john2@example.com');
  assert.equal(result, undefined);
});

test('findExistingStaff falls back to name when email missing', () => {
  const existing: BasicStaff[] = [
    { first_name: 'Jane', last_name: 'Smith', email: null },
  ];
  const result = findExistingStaff(existing, 'Jane', 'Smith', null);
  assert.equal(result, existing[0]);
});
