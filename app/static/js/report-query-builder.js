(() => {
  const root = document.getElementById('report-query-designer');
  if (!root) return;
  const schema = JSON.parse(root.dataset.schema || '{"tables":[],"relations":[]}');
  const sql = document.getElementById('report-sql');
  const tablesEl = document.getElementById('schema-tables');
  const selectedEl = document.getElementById('selected-tables');
  const sqlTools = window.ReportQuerySql;
  let state = {distinct: false, tables: [], items: [], tail: ''};
  let unsupportedReason = null;
  let lastSyncedSql = null;
  const manualJoins = [];
  const expandedTables = new Set();
  const expandedGroups = new Set();
  const key = (table, column) => `${table}.${column}`;

  function formatGroupName(groupName) {
    const raw = String(groupName || 'Other').trim();
    if (!raw) return 'Other';
    return raw.replace(/[_-]+/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
  }

  function deriveTableGroup(tableName) {
    const raw = String(tableName || '').trim();
    if (!raw) return 'Other';
    const parts = raw.split(/[^a-zA-Z0-9]+/).filter(Boolean);
    return parts[0] || raw;
  }

  function groupedTables() {
    const groups = new Map();
    schema.tables.forEach(table => {
      const groupName = table.feature_group || table.group || deriveTableGroup(table.name);
      const entries = groups.get(groupName) || [];
      entries.push(table);
      groups.set(groupName, entries);
    });
    return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right));
  }

  function renderSchema(filter = '') {
    const needle = filter.toLowerCase();
    tablesEl.innerHTML = '';
    groupedTables().forEach(([groupName, tables]) => {
      const visibleTables = tables.filter(table => table.name.toLowerCase().includes(needle) || table.columns.some(col => col.name.toLowerCase().includes(needle)));
      if (!visibleTables.length) return;
      const group = document.createElement('details');
      group.className = 'query-schema__group';
      group.open = Boolean(needle) || expandedGroups.has(groupName) || visibleTables.some(table => tableInQuery(table.name) || expandedTables.has(table.name));
      const groupSummary = document.createElement('summary');
      groupSummary.className = 'query-schema__group-summary';
      const groupLabel = document.createElement('span');
      groupLabel.textContent = formatGroupName(groupName);
      const groupCount = document.createElement('small');
      groupCount.textContent = String(visibleTables.length);
      groupSummary.append(groupLabel, groupCount);
      group.append(groupSummary);
      visibleTables.forEach(table => {
        const tableDetails = document.createElement('details');
        tableDetails.className = 'query-schema__table';
        tableDetails.open = Boolean(needle) || Boolean(tableInQuery(table.name)) || expandedTables.has(table.name);
        const summary = document.createElement('summary');
        summary.className = 'query-schema__table-summary';
        const tableToggle = document.createElement('input');
        tableToggle.type = 'checkbox';
        tableToggle.className = 'query-schema__table-toggle';
        const tableColumns = columnsFor(table.name);
        const totalColumns = table.columns.length || 0;
        tableToggle.checked = totalColumns > 0 && tableColumns.size === totalColumns;
        tableToggle.indeterminate = totalColumns > 0 && tableColumns.size > 0 && tableColumns.size < totalColumns;
        tableToggle.addEventListener('change', () => toggleTable(table.name, tableToggle.checked));
        const label = document.createElement('span');
        label.className = 'query-schema__table-name';
        label.textContent = table.name;
        summary.append(tableToggle, label);
        tableDetails.append(summary);
        table.columns.forEach(column => {
          const labelRow = document.createElement('label');
          labelRow.className = 'query-schema__column';
          const input = document.createElement('input');
          input.type = 'checkbox';
          input.checked = tableColumns.has(column.name);
          input.addEventListener('change', () => toggleColumn(table.name, column.name, input.checked));
          const name = document.createElement('span');
          name.textContent = column.name;
          const type = document.createElement('small');
          type.textContent = column.type;
          labelRow.append(input, name, type);
          tableDetails.append(labelRow);
        });
        tableDetails.addEventListener('toggle', () => {
          if (tableDetails.open) {
            expandedTables.add(table.name);
          } else {
            expandedTables.delete(table.name);
          }
        });
        group.append(tableDetails);
      });
      group.addEventListener('toggle', () => {
        if (group.open) {
          expandedGroups.add(groupName);
        } else {
          expandedGroups.delete(groupName);
          tables.forEach(table => expandedTables.delete(table.name));
        }
      });
      tablesEl.append(group);
    });
  }

  function tableInQuery(name) {
    return state.tables.find(table => table.name === name) || null;
  }

  function columnsFor(name) {
    return new Set(state.items.filter(item => item.table === name).map(item => item.column));
  }

  // A table stays in the query while a kept expression, clause or join still refers to it.
  function tableStillUsed(table) {
    if (state.items.some(item => item.table === table.name)) return true;
    if (state.items.some(item => item.expr && sqlTools.references(item.expr, table))) return true;
    if (sqlTools.references(state.tail, table)) return true;
    return state.tables.some(other => other !== table && other.join && sqlTools.references(other.join.condition, table));
  }

  function pruneTables() {
    let removed = true;
    while (removed) {
      removed = false;
      state.tables = state.tables.filter(table => {
        if (tableStillUsed(table)) return true;
        removed = true;
        return false;
      });
    }
    // The first table is written without a join clause.
    if (state.tables[0]?.join) state.tables[0] = {...state.tables[0], join: null};
  }

  // Changing fields when the SQL can't be shown visually replaces it with a new query.
  function confirmReplaceUnsupported() {
    if (!unsupportedReason) return true;
    if (!window.confirm('The current SQL query can\'t be edited in the visual builder. Start a new visual query and replace it?')) return false;
    unsupportedReason = null;
    state = {distinct: false, tables: [], items: [], tail: ''};
    return true;
  }

  function addTable(name) {
    if (!tableInQuery(name)) state.tables.push({name, alias: null, join: null});
  }

  function toggleTable(table, enabled) {
    if (!confirmReplaceUnsupported()) { renderSelected(); return; }
    if (enabled) {
      const tableMeta = schema.tables.find(item => item.name === table);
      if (tableMeta) {
        addTable(table);
        const existing = columnsFor(table);
        tableMeta.columns.forEach(column => { if (!existing.has(column.name)) state.items.push({table, column: column.name}); });
      }
      expandedTables.add(table);
    } else {
      state.items = state.items.filter(item => item.table !== table);
      pruneTables();
      if (!tableInQuery(table)) expandedTables.delete(table);
    }
    renderSelected();
    buildSql();
  }

  function toggleColumn(table, column, enabled) {
    if (!confirmReplaceUnsupported()) { renderSelected(); return; }
    if (enabled) {
      addTable(table);
      if (!columnsFor(table).has(column)) state.items.push({table, column});
    } else {
      state.items = state.items.filter(item => !(item.table === table && item.column === column));
      pruneTables();
    }
    expandedTables.add(table);
    renderSelected(); buildSql();
  }

  function removeExpression(index) {
    state.items.splice(index, 1);
    pruneTables();
    renderSelected(); buildSql();
  }

  function fieldOptions(select) {
    select.innerHTML = '<option value="">Select a field…</option>';
    schema.tables.forEach(table => table.columns.forEach(column => {
      const option = document.createElement('option'); option.value = key(table.name, column.name); option.textContent = option.value; select.append(option);
    }));
  }

  function buildSql() {
    sql.value = sqlTools.build(state, schema, manualJoins);
    lastSyncedSql = sql.value;
    sql.dispatchEvent(new Event('input'));
  }

  // Load whatever is in the SQL box (typed by hand, written by the AI assistant
  // or saved with the report) into the visual builder.
  function syncFromSql() {
    if (sql.value === lastSyncedSql) return;
    lastSyncedSql = sql.value;
    const result = sqlTools.parse(sql.value, schema);
    if (result.ok) {
      state = result.state;
      unsupportedReason = null;
      state.tables.forEach(table => expandedTables.add(table.name));
    } else {
      state = {distinct: false, tables: [], items: [], tail: ''};
      unsupportedReason = result.reason;
    }
    renderSelected();
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderSelected() {
    selectedEl.innerHTML = '';
    if (unsupportedReason) {
      const notice = element('div', 'query-notice');
      notice.append(
        element('strong', null, 'This query can’t be shown in the visual builder'),
        element('p', null, `The SQL below is kept as it is because ${unsupportedReason}. Edit it in the SQL or AI assistant modes, or choose fields here to start a new visual query.`),
      );
      selectedEl.append(notice);
    } else if (!state.tables.length) {
      selectedEl.append(element('p', 'query-empty', 'Select a field from the schema to begin.'));
    }
    state.tables.forEach(table => {
      const card = element('article', 'query-table-card');
      const heading = element('strong', null, table.name);
      if (table.alias) heading.append(element('small', 'query-table-card__alias', ` as ${table.alias}`));
      card.append(heading);
      const columns = [...columnsFor(table.name)];
      if (columns.length) {
        const list = element('ul');
        columns.forEach(column => list.append(element('li', null, column)));
        card.append(list);
      } else {
        card.append(element('p', 'query-table-card__note', 'Used by calculated fields, joins or filters.'));
      }
      if (table.join) card.append(element('code', 'query-table-card__join', `${table.join.type}${table.join.condition ? ` ${table.join.condition}` : ''}`));
      selectedEl.append(card);
    });
    const expressions = state.items.map((item, index) => ({item, index})).filter(({item}) => item.expr);
    if (expressions.length || state.tail || state.distinct) {
      const card = element('article', 'query-table-card query-table-card--extras');
      card.append(element('strong', null, 'Calculated fields & clauses'));
      if (expressions.length) {
        const list = element('ul', 'query-expressions');
        expressions.forEach(({item, index}) => {
          const row = element('li');
          row.append(element('code', null, item.expr));
          const remove = element('button', 'button button--ghost button--small', '×');
          remove.type = 'button';
          remove.title = 'Remove this calculated field';
          remove.setAttribute('aria-label', `Remove calculated field ${item.expr}`);
          remove.addEventListener('click', () => removeExpression(index));
          row.append(remove);
          list.append(row);
        });
        card.append(list);
      }
      if (state.distinct) card.append(element('p', 'query-table-card__note', 'Returns distinct rows only.'));
      if (state.tail) card.append(element('pre', 'query-table-card__tail', state.tail));
      card.append(element('p', 'form-hint', 'Kept from the SQL query. Edit these in the SQL or AI assistant modes.'));
      selectedEl.append(card);
    }
    renderSchema(document.getElementById('schema-search').value); updateJoinList();
  }
  function updateJoinList() { document.getElementById('join-list').textContent = manualJoins.length ? manualJoins.map(j => `${j.from_table}.${j.from_column} = ${j.to_table}.${j.to_column}`).join(' · ') : 'Automatic links use declared foreign keys.'; }

  function updateAiButton() {
    const button = document.getElementById('ai-query-send');
    if (!button.disabled) button.textContent = sql.value.trim() ? 'Refine query' : 'Generate query';
  }

  root.querySelectorAll('[data-designer-tab]').forEach(button => button.addEventListener('click', () => {
    const mode = button.dataset.designerTab;
    if (mode === 'visual') syncFromSql();
    if (mode === 'ai') updateAiButton();
    root.querySelectorAll('[data-designer-panel]').forEach(panel => panel.hidden = panel.dataset.designerPanel !== mode);
    root.querySelectorAll('[data-designer-tab]').forEach(tab => {
      tab.classList.toggle('button--primary', tab === button);
      tab.classList.toggle('button--ghost', tab !== button);
      tab.setAttribute('aria-selected', String(tab === button));
    });
    document.querySelector('.query-sql-field').hidden = mode !== 'sql' && mode !== 'visual';
  }));
  let syncTimer = null;
  sql.addEventListener('input', () => {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(syncFromSql, 400);
  });
  document.getElementById('schema-search').addEventListener('input', event => renderSchema(event.target.value));
  document.getElementById('clear-query').addEventListener('click', () => {
    state = {distinct: false, tables: [], items: [], tail: ''};
    unsupportedReason = null;
    manualJoins.length = 0; expandedTables.clear(); renderSelected(); buildSql();
  });
  const left = document.getElementById('join-left'), right = document.getElementById('join-right'); fieldOptions(left); fieldOptions(right);
  document.getElementById('add-join').addEventListener('click', () => { if (!left.value || !right.value || left.value === right.value) return; const [from_table, from_column] = left.value.split('.'), [to_table, to_column] = right.value.split('.'); manualJoins.push({from_table, from_column, to_table, to_column}); updateJoinList(); if (!unsupportedReason && state.tables.length) buildSql(); });
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
    const token = csrfToken();
    const body = new URLSearchParams();
    body.set('instruction', instruction);
    body.set('current_sql', sql.value);
    if (token) body.set('_csrf', token);
    const headers = {'Content-Type': 'application/x-www-form-urlencoded'};
    if (token) headers['X-CSRF-Token'] = token;
    try { const response = await fetch('/admin/reporting/query-assistant', {method: 'POST', headers, body: body.toString()}); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(errorMessage(data)); if (typeof data?.sql !== 'string' || !data.sql.trim()) throw new Error('Server returned no SQL query.'); sql.value = data.sql; sql.dispatchEvent(new Event('input')); messages.insertAdjacentHTML('beforeend', '<div class="ai-query-message ai-query-message--assistant"></div>'); messages.lastElementChild.textContent = data.summary || 'I updated the SQL query. Ask for another change if needed.'; button.textContent = 'Refine query'; }
    catch (error) { messages.insertAdjacentHTML('beforeend', '<div class="ai-query-message ai-query-message--error"></div>'); messages.lastElementChild.textContent = error.message; }
    finally { button.disabled = false; if (button.textContent === 'Thinking…') button.textContent = 'Try again'; messages.scrollTop = messages.scrollHeight; }
  });
  syncFromSql(); updateAiButton();
})();
