const {test} = require('node:test');
const assert = require('node:assert/strict');
const {parse, build, format} = require('../app/static/js/report-query-sql.js');

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

const squash = sql => sql.replace(/\s+/g, '').toLowerCase();
const samples = [
  "SELECT COALESCE(NULLIF(TRIM(a.type), ''), '(unspecified)') AS asset_type, COUNT(*) AS asset_count FROM assets a GROUP BY COALESCE(NULLIF(TRIM(a.type), ''), '(unspecified)') ORDER BY asset_count DESC, asset_type ASC",
  "select distinct t.id,c.name from tickets t left outer join companies c on c.id=t.company_id and c.active = 1 where t.status in ('open','pending') and t.created_at between '2024-01-01' and '2024-12-31' order by t.id limit 10",
  "with x as (select id from assets where status = 'a') select * from x where id in (select id from tickets) union all select 1",
  "SELECT CASE WHEN a = 1 AND b = 2 THEN 'x' ELSE 'y' END AS z, t.`order`, t.left FROM t WHERE a.`select from` = 'it''s and where'",
];

test('format lays out one clause per line', () => {
  assert.equal(format(samples[1]), [
    'SELECT DISTINCT',
    '  t.id,',
    '  c.name',
    'FROM tickets t',
    'LEFT OUTER JOIN companies c ON c.id = t.company_id',
    '  AND c.active = 1',
    "WHERE t.status IN ('open', 'pending')",
    "  AND t.created_at BETWEEN '2024-01-01' AND '2024-12-31'",
    'ORDER BY t.id',
    'LIMIT 10',
  ].join('\n'));
});

test('format indents subqueries and keeps CASE conditions inline', () => {
  assert.match(format(samples[2]), /^WITH x AS \(\n  SELECT\n    id\n  FROM assets\n  WHERE status = 'a'\n\)\nSELECT/);
  assert.match(format(samples[2]), /WHERE id IN \(\n  SELECT\n    id\n  FROM tickets\n\)\nUNION ALL\nSELECT\n  1$/);
  assert.match(format(samples[3]), /^SELECT\n  CASE WHEN a = 1 AND b = 2 THEN 'x' ELSE 'y' END AS z,/);
});

test('format only changes whitespace and keyword case, and is stable', () => {
  for (const sql of samples) {
    const pretty = format(sql);
    assert.equal(squash(pretty), squash(sql), sql);
    assert.equal(format(pretty), pretty, sql);
  }
  // Text inside strings and quoted identifiers is never touched.
  assert.match(format(samples[3]), /t\.`order`, *\n  t\.left/);
  assert.match(format(samples[3]), /a\.`select from` = 'it''s and where'$/);
});

test('formatted SQL still loads into the visual builder, and builder SQL is already pretty', () => {
  const parsed = parse(format(samples[0]), schema);
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.state.tables.map(table => table.alias), ['a']);
  assert.match(parsed.state.tail, /^GROUP BY .*\nORDER BY asset_count DESC, asset_type ASC$/);
  const state = {distinct: false, tables: [{name: 'assets', alias: null, join: null}, {name: 'companies', alias: null, join: null}],
    items: [{table: 'assets', column: 'type'}, {table: 'companies', column: 'name'}], tail: ''};
  const built = build(state, schema, []);
  assert.equal(format(built), built);
});
