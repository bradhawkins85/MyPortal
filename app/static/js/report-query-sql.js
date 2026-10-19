/* Converts report SQL to and from the visual query builder's state so the
 * Visual, AI and SQL modes can all edit the same query. */
(function (root) {
  'use strict';

  const IDENT = '`(?:[^`]|``)+`|"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*';
  const TABLE_REF = new RegExp(`^(${IDENT})(?:\\s*\\.\\s*(${IDENT}))?(?:\\s+(?:AS\\s+)?(${IDENT}))?$`, 'i');
  const COLUMN_REF = new RegExp(`^(?:(${IDENT})\\s*\\.\\s*)?(${IDENT})$`, 'i');
  const RESERVED_ALIASES = new Set(['on', 'using', 'where', 'join', 'left', 'right', 'inner', 'cross', 'natural', 'full', 'group', 'order', 'having', 'limit']);

  const quote = value => '`' + String(value).replaceAll('`', '``') + '`';
  const sameName = (left, right) => String(left || '').toLowerCase() === String(right || '').toLowerCase();

  function unquote(identifier) {
    const raw = String(identifier || '').trim();
    if (raw.startsWith('`') && raw.endsWith('`')) return raw.slice(1, -1).replaceAll('``', '`');
    if (raw.startsWith('"') && raw.endsWith('"')) return raw.slice(1, -1).replaceAll('""', '"');
    return raw;
  }

  // Remove comments that sit outside string literals and identifiers.
  function stripComments(sql) {
    let out = '', quoteChar = null;
    for (let i = 0; i < sql.length; i += 1) {
      const ch = sql[i], next = sql[i + 1];
      if (quoteChar) {
        out += ch;
        if (ch === '\\' && quoteChar !== '`') { out += next || ''; i += 1; } else if (ch === quoteChar) quoteChar = null;
      } else if (ch === "'" || ch === '"' || ch === '`') {
        quoteChar = ch; out += ch;
      } else if ((ch === '-' && next === '-' && /\s/.test(sql[i + 2] || ' ')) || ch === '#') {
        while (i < sql.length && sql[i] !== '\n') i += 1;
        out += '\n';
      } else if (ch === '/' && next === '*') {
        const end = sql.indexOf('*/', i + 2);
        i = end === -1 ? sql.length : end + 1;
        out += ' ';
      } else {
        out += ch;
      }
    }
    return out;
  }

  // Same length as the input, with quoted text and anything inside brackets
  // blanked out, so keyword and comma positions can be found at the top level.
  function mask(sql) {
    let out = '', quoteChar = null, depth = 0;
    for (let i = 0; i < sql.length; i += 1) {
      const ch = sql[i];
      if (quoteChar) {
        if (ch === '\\' && quoteChar !== '`') { out += '  '; i += 1; continue; }
        if (ch === quoteChar) quoteChar = null;
        out += ' ';
      } else if (ch === "'" || ch === '"' || ch === '`') {
        quoteChar = ch; out += ' ';
      } else if (ch === '(') {
        out += depth === 0 ? '(' : ' '; depth += 1;
      } else if (ch === ')') {
        depth = Math.max(0, depth - 1); out += depth === 0 ? ')' : ' ';
      } else {
        out += depth > 0 ? ' ' : ch;
      }
    }
    return out;
  }

  function splitTopLevel(text, masked, pattern) {
    const parts = [];
    let last = 0, match;
    const regex = new RegExp(pattern, 'gi');
    while ((match = regex.exec(masked))) {
      parts.push({ text: text.slice(last, match.index), keyword: null });
      parts[parts.length - 1].next = match[0];
      last = match.index + match[0].length;
    }
    parts.push({ text: text.slice(last), keyword: null });
    for (let i = 1; i < parts.length; i += 1) parts[i].keyword = parts[i - 1].next;
    return parts.map(({ text: part, keyword }) => ({ text: part.trim(), keyword }));
  }

  function findTable(schema, name) {
    return (schema.tables || []).find(table => sameName(table.name, name)) || null;
  }

  function findColumn(tableMeta, name) {
    return tableMeta ? (tableMeta.columns || []).find(column => sameName(column.name, name)) || null : null;
  }

  function unsupported(reason) {
    return { ok: false, reason };
  }

  /** Parse SQL into builder state, or explain why it can't be shown visually. */
  function parse(sqlText, schema) {
    const sql = stripComments(String(sqlText || '')).trim().replace(/;\s*$/, '').trim();
    if (!sql) return { ok: true, state: emptyState() };
    const masked = mask(sql);
    if (/^WITH\b/i.test(masked)) return unsupported('it starts with a WITH (common table expression) clause');
    if (!/^SELECT\b/i.test(masked)) return unsupported('it is not a plain SELECT statement');
    if (/\b(UNION|INTERSECT|EXCEPT)\b/i.test(masked)) return unsupported('it combines several SELECT statements');
    if (masked.includes(';')) return unsupported('it contains more than one statement');
    const fromMatch = /\bFROM\b/i.exec(masked);
    if (!fromMatch) return unsupported('it does not select from a table');

    const tailMatch = /\b(WHERE|GROUP\s+BY|HAVING|WINDOW|ORDER\s+BY|LIMIT|FOR\s+UPDATE|LOCK\s+IN)\b/i.exec(masked.slice(fromMatch.index));
    const tailIndex = tailMatch ? fromMatch.index + tailMatch.index : sql.length;

    // SELECT list
    let selectStart = 'SELECT'.length;
    let distinct = false;
    const modifiers = /^\s*(DISTINCT|ALL)\b/i.exec(masked.slice(selectStart));
    if (modifiers) {
      distinct = modifiers[1].toUpperCase() === 'DISTINCT';
      selectStart += modifiers[0].length;
    }
    if (/^\s*(DISTINCTROW|HIGH_PRIORITY|STRAIGHT_JOIN|SQL_\w+)\b/i.test(masked.slice(selectStart))) {
      return unsupported('it uses SELECT modifiers the visual builder does not support');
    }
    const selectItems = splitTopLevel(sql.slice(selectStart, fromMatch.index), masked.slice(selectStart, fromMatch.index), ',');
    if (selectItems.some(item => !item.text)) return unsupported('its column list could not be read');

    // FROM / JOIN clauses
    const fromText = sql.slice(fromMatch.index + 4, tailIndex);
    const fromMasked = masked.slice(fromMatch.index + 4, tailIndex);
    const joinPattern = ',|\\b(?:NATURAL\\s+)?(?:(?:LEFT|RIGHT|FULL)(?:\\s+OUTER)?\\s+|INNER\\s+|CROSS\\s+)?JOIN\\b|\\bSTRAIGHT_JOIN\\b';
    const segments = splitTopLevel(fromText, fromMasked, joinPattern);
    const tables = [];
    for (const segment of segments) {
      const segmentMasked = mask(segment.text);
      const conditionMatch = /\b(ON|USING)\b/i.exec(segmentMasked);
      const refText = (conditionMatch ? segment.text.slice(0, conditionMatch.index) : segment.text).trim();
      if (refText.includes('(')) return unsupported('it selects from a subquery or derived table');
      const ref = TABLE_REF.exec(refText);
      if (!ref) return unsupported(`the table reference "${refText}" could not be read`);
      const name = unquote(ref[2] || ref[1]);
      const alias = ref[3] ? unquote(ref[3]) : null;
      if (alias && RESERVED_ALIASES.has(alias.toLowerCase())) return unsupported(`the table reference "${refText}" could not be read`);
      if (tables.some(table => sameName(table.name, name))) return unsupported(`it uses the table "${name}" more than once`);
      const keyword = segment.keyword ? segment.keyword.replace(/\s+/g, ' ').toUpperCase() : null;
      tables.push({
        name,
        alias,
        join: keyword ? {
          type: keyword === ',' ? 'CROSS JOIN' : keyword,
          condition: conditionMatch ? segment.text.slice(conditionMatch.index).trim() : '',
        } : null,
      });
    }

    const state = emptyState();
    state.distinct = distinct;
    state.tables = tables;
    state.tail = tailMatch ? sql.slice(tailIndex).trim() : '';
    selectItems.forEach(({ text }) => {
      const column = resolveColumn(text, tables, schema);
      state.items.push(column || { expr: text });
    });
    return { ok: true, state };
  }

  function resolveColumn(text, tables, schema) {
    const ref = COLUMN_REF.exec(text.trim());
    if (!ref) return null;
    const qualifier = ref[1] ? unquote(ref[1]) : null;
    const columnName = unquote(ref[2]);
    const candidates = qualifier
      ? tables.filter(table => (table.alias ? sameName(table.alias, qualifier) : sameName(table.name, qualifier)))
      : tables.filter(table => findColumn(findTable(schema, table.name), columnName));
    if (candidates.length !== 1) return null;
    const column = findColumn(findTable(schema, candidates[0].name), columnName);
    return column ? { table: candidates[0].name, column: column.name } : null;
  }

  function emptyState() {
    return { distinct: false, tables: [], items: [], tail: '' };
  }

  function tableSqlRef(table) {
    return table.alias ? quote(table.alias) : quote(table.name);
  }

  function linkFor(table, joined, schema, manualJoins) {
    const candidates = [...(schema.relations || []), ...(manualJoins || [])];
    return candidates.find(rel => (sameName(rel.from_table, table.name) && joined.some(other => sameName(other.name, rel.to_table)))
      || (sameName(rel.to_table, table.name) && joined.some(other => sameName(other.name, rel.from_table)))) || null;
  }

  /** Build SQL from builder state. */
  function build(state, schema, manualJoins) {
    if (!state.tables.length) return '';
    const byName = name => state.tables.find(table => sameName(table.name, name));
    const fields = state.items.map(item => (item.expr ? item.expr : `${tableSqlRef(byName(item.table))}.${quote(item.column)}`));
    if (!fields.length) fields.push('*');
    const first = state.tables[0];
    const fromRef = table => quote(table.name) + (table.alias ? ` ${quote(table.alias)}` : '');
    let query = `SELECT${state.distinct ? ' DISTINCT' : ''}\n  ${fields.join(',\n  ')}\nFROM ${fromRef(first)}`;
    const joined = [first];
    const pending = state.tables.slice(1);
    while (pending.length) {
      let index = pending.findIndex(table => table.join || linkFor(table, joined, schema, manualJoins));
      if (index === -1) index = 0;
      const [table] = pending.splice(index, 1);
      if (table.join) {
        query += `\n${table.join.type} ${fromRef(table)}${table.join.condition ? ` ${table.join.condition}` : ''}`;
      } else {
        const rel = linkFor(table, joined, schema, manualJoins);
        if (rel) {
          query += `\nLEFT JOIN ${fromRef(table)} ON ${tableSqlRef(byName(rel.from_table))}.${quote(rel.from_column)} = ${tableSqlRef(byName(rel.to_table))}.${quote(rel.to_column)}`;
        } else {
          query += `\nCROSS JOIN ${fromRef(table)}`;
        }
      }
      joined.push(table);
    }
    if (state.tail) query += `\n${state.tail}`;
    return query;
  }

  /** True when raw SQL text (an expression, join condition or clause) mentions the table. */
  function references(text, table) {
    if (!text) return false;
    const names = [table.alias, table.alias ? null : table.name].filter(Boolean);
    return names.some(name => {
      const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return new RegExp(`(?:\`${escaped}\`|"${escaped}"|\\b${escaped})\\s*\\.`, 'i').test(text);
    });
  }

  const KEYWORDS = new Set(['SELECT', 'DISTINCT', 'FROM', 'WHERE', 'AND', 'OR', 'NOT', 'IN', 'IS', 'NULL', 'AS', 'ON', 'USING',
    'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'CROSS', 'NATURAL', 'STRAIGHT_JOIN', 'GROUP', 'BY', 'ORDER', 'HAVING',
    'LIMIT', 'OFFSET', 'UNION', 'INTERSECT', 'EXCEPT', 'ALL', 'WITH', 'RECURSIVE', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
    'ASC', 'DESC', 'LIKE', 'BETWEEN', 'EXISTS', 'WINDOW', 'OVER', 'PARTITION', 'ROLLUP']);
  const JOIN_STARTERS = new Set(['LEFT', 'RIGHT', 'INNER', 'CROSS', 'FULL', 'NATURAL']);
  const LIST_CLAUSES = new Set(['SELECT']);
  const CONDITION_CLAUSES = new Set(['WHERE', 'HAVING', 'ON']);
  const SPACED_OPERATORS = new Set(['=', '<', '>', '<=', '>=', '<>', '!=', '<=>']);

  function tokenize(sql) {
    const tokens = [];
    let i = 0, space = false;
    while (i < sql.length) {
      const ch = sql[i], next = sql[i + 1];
      if (/\s/.test(ch)) { space = true; i += 1; continue; }
      let end = i + 1, type = 'punct';
      if (ch === "'" || ch === '"' || ch === '`') {
        type = ch === '`' ? 'ident' : 'string';
        while (end < sql.length) {
          if (sql[end] === '\\' && ch !== '`') { end += 2; continue; }
          if (sql[end] === ch) { if (sql[end + 1] === ch) { end += 2; continue; } end += 1; break; }
          end += 1;
        }
      } else if ((ch === '-' && next === '-' && /\s/.test(sql[i + 2] || ' ')) || ch === '#') {
        type = 'line-comment';
        while (end < sql.length && sql[end] !== '\n') end += 1;
      } else if (ch === '/' && next === '*') {
        type = 'block-comment';
        end = sql.indexOf('*/', i + 2);
        end = end === -1 ? sql.length : end + 2;
      } else if (/[=<>!:]/.test(ch)) {
        type = 'operator';
        while (end < sql.length && /[=<>!:]/.test(sql[end])) end += 1;
      } else if (/[A-Za-z0-9_$]/.test(ch)) {
        type = 'word';
        while (end < sql.length && /[A-Za-z0-9_$]/.test(sql[end])) end += 1;
      }
      tokens.push({ type, text: sql.slice(i, end), space });
      space = false;
      i = end;
    }
    return tokens;
  }

  /** Lay SQL out one clause per line. Only whitespace and keyword case change. */
  function format(sqlText) {
    const tokens = tokenize(String(sqlText || ''));
    const significant = index => {
      for (let j = index; j < tokens.length; j += 1) if (!tokens[j].type.endsWith('comment')) return tokens[j];
      return null;
    };
    const upper = index => {
      const token = significant(index);
      return token && token.type === 'word' ? token.text.toUpperCase() : '';
    };
    let out = '';
    const stack = [{ kind: 'root', base: 0, clause: null, caseDepth: 0, between: false, breakNext: false }];
    let forceBreak = false, joinPhrase = false, prev = null;

    const lineIndent = () => {
      const line = out.slice(out.lastIndexOf('\n') + 1);
      return line.length - line.trimStart().length;
    };
    const newline = indent => {
      out = out.replace(/[ \t]+$/, '');
      if (out) out += '\n' + ' '.repeat(indent);
    };

    tokens.forEach((token, index) => {
      const ctx = stack[stack.length - 1];
      const clauseLevel = ctx.kind !== 'expr';
      let text = token.text;
      const word = token.type === 'word' ? text.toUpperCase() : '';
      const dotted = (prev && prev.text === '.' && !token.space) || (tokens[index + 1]?.text === '.' && !tokens[index + 1].space);
      const keyword = word && KEYWORDS.has(word) && !dotted ? word : '';
      if (keyword) text = keyword;
      let broke = false;
      const lineBreak = indent => { newline(indent); broke = true; };

      if (forceBreak) { lineBreak(lineIndent()); forceBreak = false; }

      if (clauseLevel && keyword) {
        const startsJoin = JOIN_STARTERS.has(keyword) && ['JOIN', 'OUTER'].includes(upper(index + 1));
        if (startsJoin || keyword === 'STRAIGHT_JOIN' || (keyword === 'JOIN' && !joinPhrase)) {
          lineBreak(ctx.base); ctx.clause = 'JOIN';
          joinPhrase = startsJoin;
        } else if (keyword === 'JOIN') {
          joinPhrase = false;
        } else if (['SELECT', 'FROM', 'WHERE', 'HAVING', 'LIMIT', 'WINDOW', 'UNION', 'INTERSECT', 'EXCEPT', 'WITH'].includes(keyword)
          || (['GROUP', 'ORDER'].includes(keyword) && upper(index + 1) === 'BY')) {
          lineBreak(ctx.base);
          ctx.clause = keyword;
          ctx.breakNext = LIST_CLAUSES.has(keyword);
          ctx.between = false;
        } else if (keyword === 'ON') {
          ctx.clause = 'ON';
        } else if (keyword === 'CASE') {
          ctx.caseDepth += 1;
        } else if (keyword === 'END' && ctx.caseDepth) {
          ctx.caseDepth -= 1;
        } else if (keyword === 'BETWEEN') {
          ctx.between = true;
        } else if ((keyword === 'AND' || keyword === 'OR') && CONDITION_CLAUSES.has(ctx.clause) && !ctx.caseDepth) {
          if (keyword === 'AND' && ctx.between) ctx.between = false;
          else lineBreak(ctx.base + 2);
        }
      }

      if (clauseLevel && ctx.breakNext && !broke && !(keyword === 'SELECT' || keyword === 'DISTINCT' || keyword === 'ALL') && !token.type.endsWith('comment')) {
        lineBreak(ctx.base + 2);
        ctx.breakNext = false;
      }

      if (!broke && out && !/\n *$/.test(out)) {
        const tight = text === ',' || text === ')' || text === ';' || prev?.text === '(';
        const spaced = SPACED_OPERATORS.has(text) || SPACED_OPERATORS.has(prev?.text);
        if (!tight && (token.space || spaced || prev?.text === ',' || token.type.endsWith('comment'))) out += ' ';
      }
      out += text;

      if (text === '(') {
        const opensQuery = ['SELECT', 'WITH'].includes(upper(index + 1));
        stack.push(opensQuery
          ? { kind: 'sub', base: lineIndent() + 2, openIndent: lineIndent(), clause: null, caseDepth: 0, between: false, breakNext: false }
          : { kind: 'expr' });
      } else if (text === ')' && stack.length > 1) {
        const closed = stack.pop();
        if (closed.kind === 'sub') {
          out = out.slice(0, -1);
          newline(closed.openIndent);
          out += ')';
        }
      } else if (text === ',' && clauseLevel) {
        if (ctx.clause === 'SELECT') lineBreak(ctx.base + 2);
        else if (ctx.clause === 'WITH') lineBreak(ctx.base);
      } else if (text === ';') {
        stack.length = 1;
        forceBreak = true;
      } else if (token.type === 'line-comment') {
        forceBreak = true;
      }
      prev = token;
    });
    return out.trim();
  }

  const api = { parse, build, format, references, unquote };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ReportQuerySql = api;
})(typeof window !== 'undefined' ? window : globalThis);
