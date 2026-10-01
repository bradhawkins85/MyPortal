(function () {
  'use strict';

  // Message fields offered in the rule builder (see _build_filter_context in
  // app/services/imap.py). Anything else is edited as "Other field".
  const FIELDS = [
    { value: 'from.address', label: 'Sender address' },
    { value: 'from.domain', label: 'Sender domain' },
    { value: 'subject', label: 'Subject' },
    { value: 'body', label: 'Body' },
    { value: 'to', label: 'To address' },
    { value: 'to_domains', label: 'To domain' },
    { value: 'cc', label: 'Cc address' },
    { value: 'reply_to.addresses', label: 'Reply-to address' },
    { value: 'mailbox.folder', label: 'Folder' },
  ];
  const OTHER_FIELD = '__other';

  const OPERATORS = [
    { value: 'equals', label: 'is' },
    { value: 'not_equals', label: 'is not' },
    { value: 'contains', label: 'contains' },
    { value: 'not_contains', label: 'doesn\'t contain' },
    { value: 'starts_with', label: 'starts with' },
    { value: 'ends_with', label: 'ends with' },
    { value: 'in', label: 'is any of' },
    { value: 'not_in', label: 'is none of' },
    { value: 'matches', label: 'matches pattern' },
    { value: 'not_matches', label: 'doesn\'t match pattern' },
    { value: 'present', label: 'is present' },
    { value: 'absent', label: 'is missing' },
  ];
  const OPERATOR_LABELS = Object.fromEntries(OPERATORS.map((op) => [op.value, op.label]));
  const LIST_OPERATORS = new Set(['in', 'not_in']);
  const FLAG_OPERATORS = new Set(['present', 'absent']);
  const FIELD_LABELS = Object.fromEntries(FIELDS.map((field) => [field.value, field.label]));
  const KNOWN_FIELDS = new Set(FIELDS.map((field) => field.value));

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) {
        return;
      }
      if (key === 'className') {
        node.className = value;
      } else if (key === 'text') {
        node.textContent = value;
      } else if (key === 'dataset') {
        Object.assign(node.dataset, value);
      } else if (value === true) {
        node.setAttribute(key, '');
      } else {
        node.setAttribute(key, value);
      }
    });
    (children || []).forEach((child) => {
      if (child !== null && child !== undefined && child !== false) {
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
      }
    });
    return node;
  }

  function parseFilter(text) {
    const raw = String(text || '').trim();
    if (!raw) {
      return { ok: true, value: null };
    }
    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { ok: false, message: 'The filter must be a JSON object, for example {"all": [...]}.' };
      }
      return { ok: true, value: parsed };
    } catch (error) {
      return { ok: false, message: 'The JSON can\'t be read, so the rules above weren\'t updated.' };
    }
  }

  function conditionOperator(node) {
    return OPERATORS.map((op) => op.value).filter((op) => Object.prototype.hasOwnProperty.call(node, op));
  }

  function isSimpleCondition(node) {
    return node && typeof node === 'object' && !Array.isArray(node)
      && typeof node.field === 'string'
      && conditionOperator(node).length === 1
      && !['all', 'any', 'none'].some((key) => key in node);
  }

  // Return { match, include, exclude } when the builder can represent the
  // filter, or null for anything more complex (nested groups, all + any).
  function toModel(filter) {
    if (!filter) {
      return { match: 'all', include: [], exclude: [] };
    }
    const keys = Object.keys(filter);
    if (keys.some((key) => !['all', 'any', 'none'].includes(key))) {
      return null;
    }
    if ('all' in filter && 'any' in filter) {
      return null;
    }
    const match = 'any' in filter ? 'any' : 'all';
    const include = filter[match] || [];
    const exclude = filter.none || [];
    if (!Array.isArray(include) || !Array.isArray(exclude)) {
      return null;
    }
    if (![...include, ...exclude].every(isSimpleCondition)) {
      return null;
    }
    const toRow = (node) => {
      const operator = conditionOperator(node)[0];
      return { field: node.field, operator, value: node[operator], extra: node, edited: false };
    };
    return { match, include: include.map(toRow), exclude: exclude.map(toRow) };
  }

  function rowToCondition(row) {
    const condition = {};
    // Keep options the builder doesn't edit, such as case_sensitive.
    if (row.extra && row.extra.case_sensitive !== undefined) {
      condition.case_sensitive = row.extra.case_sensitive;
    }
    condition.field = row.field.trim();
    let value = row.value;
    if (FLAG_OPERATORS.has(row.operator)) {
      value = true;
    } else if (LIST_OPERATORS.has(row.operator)) {
      value = Array.isArray(value) ? value : (value === '' || value == null ? [] : [String(value)]);
    } else if (row.edited || Array.isArray(value)) {
      value = Array.isArray(value) ? value.join(', ') : String(value == null ? '' : value);
    }
    condition[row.operator] = value;
    return condition;
  }

  function describeValue(row) {
    if (FLAG_OPERATORS.has(row.operator)) {
      return '';
    }
    if (Array.isArray(row.value)) {
      return ` ${row.value.map((item) => `“${item}”`).join(' or ') || '…'}`;
    }
    return ` “${row.value == null || row.value === '' ? '…' : row.value}”`;
  }

  function describeRow(row) {
    const field = FIELD_LABELS[row.field] || row.field || 'a field';
    return `${field.charAt(0).toLowerCase()}${field.slice(1)} ${OPERATOR_LABELS[row.operator] || row.operator}${describeValue(row)}`;
  }

  // ----- tag input for "is any of" lists ---------------------------------

  function tagInput(values, label, onChange) {
    const wrap = el('div', { className: 'scf-tags mfb__tags' });
    const input = el('input', { className: 'scf-tags__input', placeholder: 'Type and press Enter', 'aria-label': label });
    const items = values.slice();

    function render() {
      wrap.querySelectorAll('.scf-tag').forEach((tag) => tag.remove());
      items.forEach((item, index) => {
        wrap.insertBefore(el('span', { className: 'scf-tag' }, [
          el('span', { className: 'scf-tag__text', text: item }),
          el('button', {
            type: 'button',
            className: 'scf-tag__remove',
            'aria-label': `Remove ${item}`,
            dataset: { index: String(index) },
            text: '×',
          }),
        ]), input);
      });
    }

    function commit(raw) {
      const added = String(raw || '').split(/[,\n]/).map((part) => part.trim()).filter(Boolean);
      added.forEach((item) => {
        if (!items.some((existing) => existing.toLowerCase() === item.toLowerCase())) {
          items.push(item);
        }
      });
      input.value = '';
      render();
      onChange(items.slice());
    }

    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        commit(input.value);
      } else if (event.key === 'Backspace' && !input.value && items.length) {
        items.pop();
        render();
        onChange(items.slice());
      }
    });
    input.addEventListener('blur', () => {
      if (input.value.trim()) {
        commit(input.value);
      }
    });
    input.addEventListener('paste', (event) => {
      const text = (event.clipboardData || window.clipboardData).getData('text');
      if (/[,\n]/.test(text)) {
        event.preventDefault();
        commit(text);
      }
    });
    wrap.addEventListener('click', (event) => {
      const remove = event.target.closest('.scf-tag__remove');
      if (remove) {
        items.splice(Number(remove.dataset.index), 1);
        render();
        onChange(items.slice());
        input.focus();
      } else if (event.target === wrap) {
        input.focus();
      }
    });
    wrap.append(input);
    render();
    return wrap;
  }

  // ----- builder ----------------------------------------------------------

  function enhance(container) {
    const ui = container.querySelector('[data-mfb-ui]');
    const textarea = container.querySelector('textarea[name="filterQuery"]');
    const advanced = container.querySelector('[data-mfb-advanced]');
    const modeInputs = Array.from(container.querySelectorAll('[data-mfb-mode]'));
    const rulesWrap = container.querySelector('[data-mfb-rules]');
    const matchSelect = container.querySelector('[data-mfb-match]');
    const lists = {
      include: container.querySelector('[data-mfb-list="include"]'),
      exclude: container.querySelector('[data-mfb-list="exclude"]'),
    };
    const summary = container.querySelector('[data-mfb-summary]');
    const complexNote = container.querySelector('[data-mfb-complex]');
    const errorNode = container.querySelector('[data-mfb-error]');
    if (!ui || !textarea) {
      return;
    }

    // Unique radio group name when a page has several filters.
    const groupName = `mfb_mode_${Math.random().toString(36).slice(2, 8)}`;
    modeInputs.forEach((input) => { input.name = groupName; });

    let model = { match: 'all', include: [], exclude: [] };
    let mode = 'all';
    let complex = false;

    ui.hidden = false;

    function setError(message) {
      errorNode.textContent = message || '';
      errorNode.hidden = !message;
    }

    function serialize() {
      if (mode !== 'rules') {
        return null;
      }
      const filter = {};
      if (model.include.length) {
        filter[model.match] = model.include.map(rowToCondition);
      }
      if (model.exclude.length) {
        filter.none = model.exclude.map(rowToCondition);
      }
      return Object.keys(filter).length ? filter : null;
    }

    function writeJson() {
      const filter = serialize();
      textarea.value = filter ? JSON.stringify(filter, null, 2) : '';
      setError('');
    }

    function renderSummary() {
      if (complex) {
        summary.textContent = '';
        return;
      }
      if (mode !== 'rules' || (!model.include.length && !model.exclude.length)) {
        summary.textContent = 'Every email in the folder is imported.';
        return;
      }
      const joiner = model.match === 'any' ? ' or ' : ' and ';
      let text = model.include.length
        ? `Imports emails where ${model.include.map(describeRow).join(joiner)}`
        : 'Imports every email';
      if (model.exclude.length) {
        text += `, except when ${model.exclude.map(describeRow).join(' or ')}`;
      }
      summary.textContent = `${text}.`;
    }

    function renderValue(row, index, listName) {
      const label = `${listName === 'include' ? 'Rule' : 'Exception'} ${index + 1} value`;
      if (FLAG_OPERATORS.has(row.operator)) {
        return el('span', { className: 'mfb__novalue text-muted', text: '—' });
      }
      if (LIST_OPERATORS.has(row.operator)) {
        const values = Array.isArray(row.value) ? row.value.map(String) : (row.value ? [String(row.value)] : []);
        return tagInput(values, label, (items) => {
          row.value = items;
          row.edited = true;
          writeJson();
          renderSummary();
        });
      }
      const input = el('input', {
        className: `form-input${row.operator.includes('matches') ? ' scf-mono' : ''}`,
        value: Array.isArray(row.value) ? row.value.join(', ') : (row.value == null ? '' : String(row.value)),
        placeholder: row.operator.includes('matches') ? 'Regular expression' : 'Value',
        'aria-label': label,
        dataset: { mfbRowField: 'value' },
      });
      return input;
    }

    function renderList(listName) {
      const list = lists[listName];
      list.textContent = '';
      model[listName].forEach((row, index) => {
        const isOther = !KNOWN_FIELDS.has(row.field);
        const fieldSelect = el('select', {
          className: 'form-input',
          'aria-label': `${listName === 'include' ? 'Rule' : 'Exception'} ${index + 1} field`,
          dataset: { mfbRowField: 'field' },
        }, FIELDS.map((field) => el('option', { value: field.value, text: field.label, selected: field.value === row.field }))
          .concat([el('option', { value: OTHER_FIELD, text: 'Other field…', selected: isOther })]));
        const operatorSelect = el('select', {
          className: 'form-input',
          'aria-label': `${listName === 'include' ? 'Rule' : 'Exception'} ${index + 1} comparison`,
          dataset: { mfbRowField: 'operator' },
        }, OPERATORS.map((op) => el('option', { value: op.value, text: op.label, selected: op.value === row.operator })));
        const fieldCell = el('div', { className: 'mfb__field' }, [fieldSelect]);
        if (isOther) {
          fieldCell.append(el('input', {
            className: 'form-input scf-mono',
            value: row.field,
            placeholder: 'e.g. headers.x-priority',
            spellcheck: 'false',
            'aria-label': `${listName === 'include' ? 'Rule' : 'Exception'} ${index + 1} field path`,
            dataset: { mfbRowField: 'path' },
          }));
        }
        list.append(el('li', { className: 'mfb__row', dataset: { index: String(index), list: listName } }, [
          fieldCell,
          operatorSelect,
          renderValue(row, index, listName),
          el('button', {
            type: 'button',
            className: 'scf-icon-button scf-icon-button--danger',
            'aria-label': `Remove ${listName === 'include' ? 'rule' : 'exception'} ${index + 1}`,
            title: 'Remove',
            dataset: { mfbRemove: '' },
            text: '×',
          }),
        ]));
      });
    }

    function render() {
      modeInputs.forEach((input) => {
        input.checked = input.value === mode;
        input.disabled = complex;
        input.closest('.scf-choice').classList.toggle('is-selected', input.checked);
      });
      rulesWrap.hidden = complex || mode !== 'rules';
      complexNote.hidden = !complex;
      matchSelect.value = model.match;
      renderList('include');
      renderList('exclude');
      renderSummary();
    }

    function readJson() {
      const parsed = parseFilter(textarea.value);
      if (!parsed.ok) {
        setError(parsed.message);
        if (advanced) {
          advanced.open = true;
        }
        return;
      }
      setError('');
      const next = toModel(parsed.value);
      complex = next === null;
      if (next) {
        model = next;
        mode = parsed.value ? 'rules' : 'all';
      }
      if (complex && advanced) {
        advanced.open = true;
      }
      render();
    }

    function rowAt(node) {
      const item = node.closest('.mfb__row');
      return item ? { row: model[item.dataset.list][Number(item.dataset.index)], item } : null;
    }

    Object.entries(lists).forEach(([listName, list]) => {
      list.addEventListener('change', (event) => {
        const found = rowAt(event.target);
        const key = event.target.dataset.mfbRowField;
        if (!found || !key || key === 'value' || key === 'path') {
          return;
        }
        const { row } = found;
        if (key === 'field') {
          row.field = event.target.value === OTHER_FIELD ? '' : event.target.value;
        } else if (key === 'operator') {
          const wasList = LIST_OPERATORS.has(row.operator);
          row.operator = event.target.value;
          if (LIST_OPERATORS.has(row.operator) && !wasList) {
            row.value = row.value && !Array.isArray(row.value) ? [String(row.value)] : (row.value || []);
          } else if (!LIST_OPERATORS.has(row.operator) && Array.isArray(row.value)) {
            row.value = row.value[0] || '';
          }
        }
        row.edited = true;
        const index = Number(found.item.dataset.index);
        writeJson();
        renderList(listName);
        renderSummary();
        const again = list.querySelector(`[data-index="${index}"] [data-mfb-row-field="${key === 'field' && !row.field ? 'path' : key}"]`);
        if (again) {
          again.focus();
        }
      });
      list.addEventListener('input', (event) => {
        const found = rowAt(event.target);
        const key = event.target.dataset.mfbRowField;
        if (!found || (key !== 'value' && key !== 'path')) {
          return;
        }
        if (key === 'path') {
          found.row.field = event.target.value;
        } else {
          found.row.value = event.target.value;
        }
        found.row.edited = true;
        writeJson();
        renderSummary();
      });
      list.addEventListener('click', (event) => {
        const found = event.target.closest('[data-mfb-remove]') && rowAt(event.target);
        if (!found) {
          return;
        }
        model[listName].splice(Number(found.item.dataset.index), 1);
        writeJson();
        render();
        const next = list.querySelector('[data-mfb-row-field="field"]') || container.querySelector(`[data-mfb-add="${listName}"]`);
        next.focus();
      });
    });

    container.querySelectorAll('[data-mfb-add]').forEach((button) => {
      button.addEventListener('click', () => {
        const listName = button.dataset.mfbAdd;
        const defaults = listName === 'include'
          ? { field: 'from.domain', operator: 'equals' }
          : { field: 'subject', operator: 'contains' };
        model[listName].push({ ...defaults, value: '', extra: null, edited: true });
        writeJson();
        render();
        const inputs = lists[listName].querySelectorAll('[data-mfb-row-field="value"]');
        if (inputs.length) {
          inputs[inputs.length - 1].focus();
        }
      });
    });

    modeInputs.forEach((input) => {
      input.addEventListener('change', () => {
        mode = input.value;
        if (mode === 'rules' && !model.include.length && !model.exclude.length) {
          model.include.push({ field: 'from.domain', operator: 'equals', value: '', extra: null, edited: true });
        }
        writeJson();
        render();
        if (mode === 'rules') {
          const first = lists.include.querySelector('[data-mfb-row-field="value"]');
          if (first) {
            first.focus();
          }
        }
      });
    });

    matchSelect.addEventListener('change', () => {
      model.match = matchSelect.value;
      writeJson();
      renderSummary();
    });

    textarea.addEventListener('input', readJson);
    readJson();
    if (advanced && !complex && errorNode.hidden) {
      advanced.open = false;
    }

    const form = container.closest('form');
    if (form) {
      // Capture on document so this runs before any other submit handlers.
      document.addEventListener('submit', (event) => {
        if (event.target !== form || complex || mode !== 'rules' || !parseFilter(textarea.value).ok) {
          return;
        }
        const rows = [...model.include, ...model.exclude];
        let problem = '';
        if (!rows.length) {
          problem = 'Add at least one rule, or choose Every email.';
        } else if (rows.some((row) => !String(row.field || '').trim())) {
          problem = 'Enter a field path for every “Other field” rule.';
        } else if (rows.some((row) => !FLAG_OPERATORS.has(row.operator) && (Array.isArray(row.value) ? !row.value.length : !String(row.value == null ? '' : row.value).trim()))) {
          problem = 'Give every rule a value, or remove the rules you don\'t need.';
        }
        if (problem) {
          event.preventDefault();
          event.stopImmediatePropagation();
          setError(problem);
          errorNode.scrollIntoView({ block: 'center' });
        }
      }, true);
    }
  }

  function init() {
    document.querySelectorAll('[data-mail-filter]').forEach(enhance);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
