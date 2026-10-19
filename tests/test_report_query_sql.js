const {test} = require('node:test');
const assert = require('node:assert/strict');
const {parse, build} = require('../app/static/js/report-query-sql.js');

const schema = {
  tables: [
    {name: 'assets', columns: [{name: 'id'}, {name: 'type'}, {name: 'status'}, {name: 'company_id'}]},
    {name: 'companies', columns: [{name: 'id'}, {name: 'name'}]},
    {name: 'tickets', columns: [{name: 'id'}, {name: 'subject'}, {name: 'company_id'}]},
  ],
  relations: [
    {from_table: 'assets', from_column: 'company_id', to_table: 'companies', to_column: 'id'},
    {from_table: 'tickets', from_column: 'company_id', to_table: 'companies', to_column: 'id'},
  ],
};

test('builder SQL round-trips back into the same visual selection', () => {
  const state = {distinct: false, tables: [{name: 'assets', alias: null, join: null}, {name: 'companies', alias: null, join: null}],
    items: [{table: 'assets', column: 'type'}, {table: 'companies', column: 'name'}], tail: ''};
  const sql = build(state, schema, []);
  assert.match(sql, /LEFT JOIN `companies` ON `assets`.`company_id` = `companies`.`id`/);
  const parsed = parse(sql, schema);
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.state.items, state.items);
  assert.deepEqual(parsed.state.tables.map(table => table.name), ['assets', 'companies']);
  assert.equal(build(parsed.state, schema, []), sql);
});

test('hand-written aggregate query loads with its table, expressions and clauses preserved', () => {
  const sql = "SELECT COALESCE(NULLIF(TRIM(a.type), ''), '(unspecified)') AS asset_type, COUNT(*) AS asset_count FROM assets a GROUP BY COALESCE(NULLIF(TRIM(a.type), ''), '(unspecified)') ORDER BY asset_count DESC";
  const parsed = parse(sql, schema);
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.state.tables, [{name: 'assets', alias: 'a', join: null}]);
  assert.equal(parsed.state.items.length, 2);
  assert.ok(parsed.state.items.every(item => item.expr));
  assert.match(parsed.state.tail, /^GROUP BY .* ORDER BY asset_count DESC$/);

  // Adding a field in the visual builder keeps the alias, expressions and clauses.
  parsed.state.items.push({table: 'assets', column: 'status'});
  const rebuilt = build(parsed.state, schema, []);
  assert.match(rebuilt, /FROM `assets` `a`/);
  assert.match(rebuilt, /`a`.`status`/);
  assert.match(rebuilt, /COUNT\(\*\) AS asset_count/);
  assert.match(rebuilt, /GROUP BY COALESCE/);
  assert.ok(parse(rebuilt, schema).ok);
});

test('plain columns are matched by alias, table name or unambiguous bare name', () => {
  const sql = 'SELECT t.subject, c.`name`, status FROM tickets AS t INNER JOIN companies c ON c.id = t.company_id, assets WHERE t.id > 5;';
  const parsed = parse(sql, schema);
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.state.items, [
    {table: 'tickets', column: 'subject'},
    {table: 'companies', column: 'name'},
    {table: 'assets', column: 'status'},
  ]);
  assert.deepEqual(parsed.state.tables[1].join, {type: 'INNER JOIN', condition: 'ON c.id = t.company_id'});
  assert.deepEqual(parsed.state.tables[2].join, {type: 'CROSS JOIN', condition: ''});
  assert.equal(parsed.state.tail, 'WHERE t.id > 5');
});

test('aliased or ambiguous columns stay as expressions', () => {
  const parsed = parse('SELECT id, a.type AS kind FROM assets a JOIN companies c ON c.id = a.company_id', schema);
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.state.items, [{expr: 'id'}, {expr: 'a.type AS kind'}]);
});

test('comments and keywords inside strings do not confuse the parser', () => {
  const parsed = parse("-- report\nSELECT a.type /* kind */ FROM assets a WHERE a.status <> 'from where'", schema);
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.state.items, [{table: 'assets', column: 'type'}]);
  assert.equal(parsed.state.tail, "WHERE a.status <> 'from where'");
});

test('queries the visual builder cannot represent are reported, not mangled', () => {
  for (const sql of [
    'WITH x AS (SELECT 1) SELECT * FROM x',
    'SELECT id FROM assets UNION SELECT id FROM tickets',
    'SELECT x.id FROM (SELECT id FROM assets) x',
    'SELECT 1',
    'SELECT a.id FROM assets a JOIN assets b ON b.id = a.id',
  ]) {
    const parsed = parse(sql, schema);
    assert.equal(parsed.ok, false, sql);
    assert.ok(parsed.reason);
  }
});

test('empty SQL gives an empty builder', () => {
  const parsed = parse('  ', schema);
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.state.tables, []);
  assert.equal(build(parsed.state, schema, []), '');
});
