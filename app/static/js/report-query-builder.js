(() => {
  const root = document.getElementById('report-query-designer');
  if (!root) return;
  const schema = JSON.parse(root.dataset.schema || '{"tables":[],"relations":[]}');
  const sql = document.getElementById('report-sql');
  const tablesEl = document.getElementById('schema-tables');
  const selectedEl = document.getElementById('selected-tables');
  const selected = new Map();
  const manualJoins = [];
  const quote = value => '`' + String(value).replaceAll('`', '``') + '`';
  const key = (table, column) => `${table}.${column}`;

  function renderSchema(filter = '') {
    const needle = filter.toLowerCase();
    tablesEl.innerHTML = '';
    schema.tables.filter(table => table.name.toLowerCase().includes(needle) || table.columns.some(col => col.name.toLowerCase().includes(needle))).forEach(table => {
      const details = document.createElement('details');
      details.className = 'query-schema__table'; details.open = Boolean(needle);
      const summary = document.createElement('summary'); summary.textContent = table.name; details.append(summary);
      table.columns.forEach(column => {
        const label = document.createElement('label'); label.className = 'query-schema__column';
        const input = document.createElement('input'); input.type = 'checkbox'; input.checked = selected.get(table.name)?.has(column.name) || false;
        input.addEventListener('change', () => toggleColumn(table.name, column.name, input.checked));
        label.append(input, document.createTextNode(` ${column.name}`));
        const type = document.createElement('small'); type.textContent = column.type; label.append(type); details.append(label);
      }); tablesEl.append(details);
    });
  }
  function toggleColumn(table, column, enabled) {
    if (!selected.has(table)) selected.set(table, new Set());
    enabled ? selected.get(table).add(column) : selected.get(table).delete(column);
    if (!selected.get(table).size) selected.delete(table);
    renderSelected(); buildSql();
  }
  function fieldOptions(select) {
    select.innerHTML = '<option value="">Select a field…</option>';
    schema.tables.forEach(table => table.columns.forEach(column => {
      const option = document.createElement('option'); option.value = key(table.name, column.name); option.textContent = option.value; select.append(option);
    }));
  }
  function joinsFor(tables) {
    const joined = new Set([tables[0]]), joins = [];
    while (joined.size < tables.length) {
      const relation = schema.relations.find(rel => tables.includes(rel.from_table) && tables.includes(rel.to_table) && (joined.has(rel.from_table) !== joined.has(rel.to_table)));
      if (!relation) break;
      const next = joined.has(relation.from_table) ? relation.to_table : relation.from_table;
      joins.push({...relation, next_table: next}); joined.add(next);
    }
    manualJoins.forEach(join => { if (tables.includes(join.from_table) && tables.includes(join.to_table)) joins.push({...join, next_table: joined.has(join.from_table) ? join.to_table : join.from_table}); });
    return {joined, joins};
  }
  function buildSql() {
    const tables = [...selected.keys()]; if (!tables.length) { sql.value = ''; return; }
    const fields = [...selected].flatMap(([table, columns]) => [...columns].map(column => `${quote(table)}.${quote(column)}`));
    const result = joinsFor(tables); let query = `SELECT\n  ${fields.join(',\n  ')}\nFROM ${quote(tables[0])}`;
    result.joins.forEach(rel => { query += `\nLEFT JOIN ${quote(rel.next_table)} ON ${quote(rel.from_table)}.${quote(rel.from_column)} = ${quote(rel.to_table)}.${quote(rel.to_column)}`; });
    tables.filter(table => table !== tables[0] && !result.joins.some(rel => rel.from_table === table || rel.to_table === table)).forEach(table => { query += `\nCROSS JOIN ${quote(table)}`; });
    sql.value = query; sql.dispatchEvent(new Event('input'));
  }
  function renderSelected() {
    selectedEl.innerHTML = '';
    if (!selected.size) selectedEl.innerHTML = '<p class="query-empty">Select a field from the schema to begin.</p>';
    selected.forEach((columns, table) => { const card = document.createElement('article'); card.className = 'query-table-card'; card.innerHTML = `<strong>${table}</strong><ul>${[...columns].map(col => `<li>${col}</li>`).join('')}</ul>`; selectedEl.append(card); });
    renderSchema(document.getElementById('schema-search').value); updateJoinList();
  }
  function updateJoinList() { document.getElementById('join-list').textContent = manualJoins.length ? manualJoins.map(j => `${j.from_table}.${j.from_column} = ${j.to_table}.${j.to_column}`).join(' · ') : 'Automatic links use declared foreign keys.'; }

  root.querySelectorAll('[data-designer-tab]').forEach(button => button.addEventListener('click', () => {
    root.querySelectorAll('[data-designer-panel]').forEach(panel => panel.hidden = panel.dataset.designerPanel !== button.dataset.designerTab);
    root.querySelectorAll('[data-designer-tab]').forEach(tab => { tab.classList.toggle('button--primary', tab === button); tab.classList.toggle('button--ghost', tab !== button); });
    document.querySelector('.query-sql-field').hidden = button.dataset.designerTab !== 'sql' && button.dataset.designerTab !== 'visual';
  }));
  document.getElementById('schema-search').addEventListener('input', event => renderSchema(event.target.value));
  document.getElementById('clear-query').addEventListener('click', () => { selected.clear(); manualJoins.length = 0; renderSelected(); buildSql(); });
  const left = document.getElementById('join-left'), right = document.getElementById('join-right'); fieldOptions(left); fieldOptions(right);
  document.getElementById('add-join').addEventListener('click', () => { if (!left.value || !right.value || left.value === right.value) return; const [from_table, from_column] = left.value.split('.'), [to_table, to_column] = right.value.split('.'); manualJoins.push({from_table, from_column, to_table, to_column}); updateJoinList(); buildSql(); });
  const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || document.querySelector('input[name="_csrf"]')?.value || '';
  const errorMessage = (data) => {
    if (data?.error) return data.error;
    const detail = data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail) && detail.length) return detail.map(item => typeof item === 'string' ? item : item?.msg || '').filter(Boolean).join('; ');
    return 'Unable to generate query.';
  };
  document.getElementById('ai-query-send').addEventListener('click', async () => {
    const input = document.getElementById('ai-query-instruction'), button = document.getElementById('ai-query-send'), messages = document.getElementById('ai-query-messages');
    const instruction = input.value.trim(); if (!instruction) return;
    messages.insertAdjacentHTML('beforeend', `<div class="ai-query-message ai-query-message--user"></div>`); messages.lastElementChild.textContent = instruction; input.value = ''; button.disabled = true; button.textContent = 'Thinking…';
    const body = new FormData(), token = csrfToken(); body.set('instruction', instruction); body.set('current_sql', sql.value); if (token) body.set('_csrf', token);
    try { const response = await fetch('/admin/reporting/query-assistant', {method: 'POST', headers: token ? {'X-CSRF-Token': token} : {}, body}); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(errorMessage(data)); if (typeof data?.sql !== 'string' || !data.sql.trim()) throw new Error('Server returned no SQL query.'); sql.value = data.sql; messages.insertAdjacentHTML('beforeend', '<div class="ai-query-message ai-query-message--assistant"></div>'); messages.lastElementChild.textContent = data.summary || 'I updated the SQL query. Ask for another change if needed.'; button.textContent = 'Refine query'; }
    catch (error) { messages.insertAdjacentHTML('beforeend', '<div class="ai-query-message ai-query-message--error"></div>'); messages.lastElementChild.textContent = error.message; }
    finally { button.disabled = false; if (button.textContent === 'Thinking…') button.textContent = 'Try again'; messages.scrollTop = messages.scrollHeight; }
  });
  renderSchema(); updateJoinList();
})();
