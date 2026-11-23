const {test} = require('node:test');
const assert = require('node:assert/strict');

global.window = {};
global.document = {readyState: 'complete', body: null, querySelectorAll: () => []};
require('../app/static/js/local_time.js');
const {parse, toDate, format, isDateOnly} = global.window.MyPortalTime;

test('zone-less database timestamps are read as UTC, not browser local time', () => {
  assert.equal(parse('2026-11-23 10:15:00').toISOString(), '2026-11-23T10:15:00.000Z');
  assert.equal(parse('2026-11-23T10:15:00').toISOString(), '2026-11-23T10:15:00.000Z');
  assert.equal(parse('2026-11-23T10:15:00.123456').toISOString(), '2026-11-23T10:15:00.123Z');
  assert.equal(parse('2026-11-23T10:15').toISOString(), '2026-11-23T10:15:00.000Z');
});

test('explicit zones are respected', () => {
  assert.equal(parse('2026-11-23T10:15:00Z').toISOString(), '2026-11-23T10:15:00.000Z');
  assert.equal(parse('2026-11-23T20:15:00+10:00').toISOString(), '2026-11-23T10:15:00.000Z');
  assert.equal(parse('2026-11-23T20:15:00+1000').toISOString(), '2026-11-23T10:15:00.000Z');
  assert.equal(parse('2026-11-23 20:15:00+10:00').toISOString(), '2026-11-23T10:15:00.000Z');
});

test('calendar dates are not shifted across days', () => {
  assert.ok(isDateOnly('2026-11-23'));
  const expected = new Date(Date.UTC(2026, 10, 23)).toLocaleDateString(undefined, {timeZone: 'UTC'});
  assert.equal(format('2026-11-23'), expected);
});

test('invalid input is reported, not thrown', () => {
  assert.equal(parse(''), null);
  assert.equal(parse('not a date'), null);
  assert.equal(format('not a date'), '');
  assert.ok(Number.isNaN(toDate('nope').getTime()));
});

test('formats use the viewer locale and time zone', () => {
  const date = new Date(Date.UTC(2026, 10, 23, 10, 15));
  assert.equal(format('2026-11-23 10:15:00'), date.toLocaleString());
  assert.equal(format('2026-11-23 10:15:00', 'date'), date.toLocaleDateString());
});
