const {test} = require('node:test');
const assert = require('node:assert/strict');
const {analyseEnvironment} = require('../app/static/js/env-formatter.js');
const catalog = [
  {key: 'APP_NAME', aliases: ['APP_NAME'], group: 'Core', active: true},
  {key: 'OLLAMA_MODEL', aliases: ['OLLAMA_MODEL'], group: 'Module: Ollama', active: false},
  {key: 'SESSION_SECRET', aliases: ['SESSION_SECRET', 'SECRET_KEY'], group: 'Security', active: true},
  {key: 'SECRET_KEY', aliases: ['SESSION_SECRET', 'SECRET_KEY'], group: 'Security', active: true},
];

test('active before disabled before stale, preserving every assignment and comment', () => {
  const input = '# obsolete setting\nOLD="abc=#def"\n# AI model\nOLLAMA_MODEL=llama3 # keep\n# portal\nexport APP_NAME = "Portal"\n# trailing note\n';
  const result = analyseEnvironment(input, catalog);
  assert.ok(result.formatted.indexOf('export APP_NAME') < result.formatted.indexOf('OLLAMA_MODEL='));
  assert.ok(result.formatted.indexOf('OLLAMA_MODEL=') < result.formatted.indexOf('OLD='));
  for (const line of input.split('\n')) assert.ok(result.formatted.includes(line));
  assert.deepEqual(result.extra, ['OLD']);
  assert.deepEqual(result.missing.map(item => item.key), ['SESSION_SECRET']);
});

test('preserves multiline secrets, escaped quotes, CRLF and duplicate order', () => {
  const input = 'APP_NAME="first\r\nsecond \\" third"\r\nAPP_NAME=last\r\n';
  const result = analyseEnvironment(input, catalog);
  assert.ok(result.formatted.includes(input.trimEnd()));
  assert.equal(result.issues.length, 1);
  assert.ok(result.issues[0].includes('duplicate'));
});

test('accepts aliases without reporting canonical key missing', () => {
  const result = analyseEnvironment('SECRET_KEY=private', catalog);
  assert.ok(!result.missing.some(item => item.key === 'SESSION_SECRET'));
  assert.deepEqual(result.extra, []);
});

test('malformed content and unterminated quotes are retained with line-only warnings', () => {
  const input = 'invalid sensitive text\nAPP_NAME="unterminated\nmore private text';
  const result = analyseEnvironment(input, catalog);
  assert.ok(result.formatted.includes(input));
  assert.equal(result.issues.length, 2);
  assert.ok(!result.issues.join('').includes('sensitive'));
  assert.ok(!result.issues.join('').includes('private'));
});

test('warns about order-sensitive interpolation without expanding secrets', () => {
  const result = analyseEnvironment('APP_NAME=${SECRET_KEY}\nSECRET_KEY=private', catalog);
  assert.ok(result.formatted.includes('APP_NAME=${SECRET_KEY}'));
  assert.ok(result.issues.some(issue => issue.includes('interpolation')));
});

test('trailing junk after a quoted value is retained as stale content', () => {
  const result = analyseEnvironment('APP_NAME="name" secret junk', catalog);
  assert.ok(result.formatted.includes('APP_NAME="name" secret junk'));
  assert.equal(result.count, 0);
  assert.equal(result.issues.length, 1);
});
