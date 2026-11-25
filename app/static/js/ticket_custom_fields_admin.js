(function () {
  'use strict';

  const modal = document.getElementById('ticket-custom-field-modal');
  const dataNode = document.getElementById('ticket-custom-field-editor-data');
  if (!modal || !dataNode) {
    return;
  }

  let data = { fields: [], optionTypes: [], typeLabels: {}, visibilityLabels: {}, maxRating: 10 };
  try {
    data = Object.assign(data, JSON.parse(dataNode.textContent || '{}'));
  } catch (error) {
    // Keep the defaults; the list still renders without the editor data.
  }

  const form = modal.querySelector('[data-tcf-form]');
  const deleteForm = document.querySelector('[data-tcf-delete-form]');
  const optionTypes = new Set(data.optionTypes || []);
  const fieldsById = new Map((data.fields || []).map((field) => [String(field.id), field]));
  const existingKeys = (data.fields || []).map((field) => ({ id: String(field.id), key: field.field_key }));

  const $ = (selector, root) => (root || modal).querySelector(selector);
  const $$ = (selector, root) => Array.from((root || modal).querySelectorAll(selector));
  const input = (name) => $(`[data-tcf-input="${name}"]`);
  const radios = (name) => $$(`[data-tcf-input="${name}"]`);
  const radioValue = (name) => {
    const checked = radios(name).find((node) => node.checked);
    return checked ? checked.value : '';
  };
  const setRadio = (name, value) => {
    radios(name).forEach((node) => {
      node.checked = node.value === value;
    });
  };

  const labelInput = input('label');
  const keyInput = input('field_key');
  const optionsList = $('[data-tcf-options]');
  const optionsTab = $('[data-tcf-tab="options"]');
  const optionsCount = $('[data-tcf-options-count]');
  const ratingField = $('[data-tcf-rating-field]');
  const activeField = $('[data-tcf-active-field]');
  const deleteButton = $('[data-tcf-delete]');
  const preview = $('[data-tcf-preview]');
  const previewRules = $('[data-tcf-preview-rules]');
  const bulkPanel = $('[data-tcf-bulk]');
  const keyExample = $('[data-tcf-key-example]');

  const state = { id: '', options: [], keyTouched: false, snapshot: '' };

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
      if (key === 'className') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (typeof value === 'boolean') node[key] = value;
      else if (value !== undefined && value !== null) node.setAttribute(key, value);
    });
    (children || []).forEach((child) => child && node.append(child));
    return node;
  }

  function keyFromLabel(label) {
    let key = String(label || '').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean).join('_');
    if (key && !/^[a-z]/.test(key)) key = `field_${key}`;
    return key.slice(0, 100);
  }

  // ------------------------------------------------------------------ tabs

  function showTab(name) {
    $$('[data-tcf-tab]').forEach((tab) => {
      const active = tab.dataset.tcfTab === name;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      tab.tabIndex = active ? 0 : -1;
    });
    $$('[data-tcf-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.tcfPanel !== name;
    });
  }

  $$('[data-tcf-tab]').forEach((tab) => {
    tab.addEventListener('click', () => showTab(tab.dataset.tcfTab));
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const tabs = $$('[data-tcf-tab]').filter((node) => !node.hidden);
      const index = tabs.indexOf(tab);
      let next = index;
      if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      event.preventDefault();
      showTab(tabs[next].dataset.tcfTab);
      tabs[next].focus();
    });
  });

  // --------------------------------------------------------------- options

  function renderOptions() {
    optionsList.textContent = '';
    state.options.forEach((option, index) => {
      const field = el('input', {
        className: 'form-input',
        value: option,
        placeholder: `Choice ${index + 1}`,
        'aria-label': `Choice ${index + 1}`,
        dataset: { tcfOption: String(index) },
      });
      optionsList.append(el('li', { className: 'scf-option tcf-option' }, [
        el('span', { className: 'scf-option__move' }, [
          el('button', { type: 'button', className: 'scf-icon-button', 'aria-label': 'Move choice up', title: 'Move up', disabled: index === 0, dataset: { tcfMove: `${index}:-1` }, text: '↑' }),
          el('button', { type: 'button', className: 'scf-icon-button', 'aria-label': 'Move choice down', title: 'Move down', disabled: index === state.options.length - 1, dataset: { tcfMove: `${index}:1` }, text: '↓' }),
        ]),
        field,
        el('button', { type: 'button', className: 'scf-icon-button scf-icon-button--danger', 'aria-label': `Remove choice ${option || index + 1}`, title: 'Remove choice', dataset: { tcfRemove: String(index) }, text: '×' }),
      ]));
    });
    if (!state.options.length) {
      optionsList.append(el('li', { className: 'scf-options__empty text-muted', text: 'No choices yet. Add at least one.' }));
    }
  }

  optionsList.addEventListener('input', (event) => {
    const index = event.target.dataset.tcfOption;
    if (index === undefined) return;
    state.options[Number(index)] = event.target.value;
    refresh(false);
  });
  optionsList.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.target.dataset.tcfOption === undefined) return;
    event.preventDefault();
    const index = Number(event.target.dataset.tcfOption);
    state.options.splice(index + 1, 0, '');
    renderOptions();
    refresh(false);
    const next = optionsList.querySelector(`[data-tcf-option="${index + 1}"]`);
    if (next) next.focus();
  });
  optionsList.addEventListener('click', (event) => {
    const move = event.target.closest('[data-tcf-move]');
    const remove = event.target.closest('[data-tcf-remove]');
    if (move) {
      const [index, step] = move.dataset.tcfMove.split(':').map(Number);
      const target = index + step;
      if (target < 0 || target >= state.options.length) return;
      [state.options[index], state.options[target]] = [state.options[target], state.options[index]];
    } else if (remove) {
      state.options.splice(Number(remove.dataset.tcfRemove), 1);
    } else {
      return;
    }
    renderOptions();
    refresh(false);
  });
  $('[data-tcf-option-add]').addEventListener('click', () => {
    state.options.push('');
    renderOptions();
    refresh(false);
    const last = optionsList.querySelector(`[data-tcf-option="${state.options.length - 1}"]`);
    if (last) last.focus();
  });
  $$('[data-tcf-bulk-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      bulkPanel.hidden = !bulkPanel.hidden;
      $$('[data-tcf-bulk-toggle][aria-expanded]').forEach((node) => node.setAttribute('aria-expanded', bulkPanel.hidden ? 'false' : 'true'));
      if (!bulkPanel.hidden) $('[data-tcf-bulk-input]').focus();
    });
  });
  $('[data-tcf-bulk-apply]').addEventListener('click', () => {
    const area = $('[data-tcf-bulk-input]');
    const lines = area.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    state.options = state.options.filter((option) => option.trim());
    lines.forEach((line) => {
      if (!state.options.includes(line)) state.options.push(line);
    });
    area.value = '';
    bulkPanel.hidden = true;
    renderOptions();
    refresh(false);
  });

  // --------------------------------------------------------------- preview

  function cleanOptions() {
    const seen = [];
    state.options.forEach((option) => {
      const text = String(option || '').trim();
      if (text && !seen.includes(text)) seen.push(text);
    });
    return seen;
  }

  function renderPreview() {
    const type = radioValue('field_type') || 'text';
    const label = labelInput.value.trim() || 'Untitled detail';
    const help = input('help_text').value.trim();
    const editable = radioValue('editable') === '1';
    const options = cleanOptions();
    preview.textContent = '';
    const id = 'tcf-preview-input';
    preview.append(el('label', { className: 'form-label', for: id, text: label }));
    let control;
    if (type === 'textarea') {
      control = el('textarea', { className: 'form-input', id, rows: '2' });
    } else if (type === 'select') {
      control = el('select', { className: 'form-input', id }, [el('option', { text: 'Not set' }), ...options.map((option) => el('option', { text: option }))]);
    } else if (type === 'multiselect') {
      control = el('select', { className: 'form-input', id, multiple: true, size: String(Math.min(Math.max(options.length, 2), 5)) }, options.map((option) => el('option', { text: option })));
    } else if (type === 'radio') {
      control = el('div', { className: 'scf-preview__multi', id, role: 'radiogroup' }, options.map((option) => el('label', {}, [el('input', { type: 'radio', name: 'tcf-preview-radio' }), document.createTextNode(` ${option}`)])));
    } else if (type === 'checkbox') {
      control = el('label', { className: 'scf-preview__checkbox' }, [el('input', { type: 'checkbox', id }), document.createTextNode(' Yes')]);
    } else if (type === 'rating') {
      const max = Number(input('rating_max').value) || 5;
      control = el('div', { id, 'aria-label': `Rating out of ${max}` , text: '☆'.repeat(Math.min(max, data.maxRating || 10)) });
    } else {
      const htmlType = { email: 'email', number: 'number', time: 'time', url: 'url', date: 'date' }[type] || 'text';
      control = el('input', { className: 'form-input', id, type: htmlType });
    }
    if (!editable && control.tagName !== 'DIV' && control.tagName !== 'LABEL') {
      control.disabled = true;
    }
    preview.append(control);
    if (help) preview.append(el('p', { className: 'form-help', text: help }));

    previewRules.textContent = '';
    const visibility = radioValue('visibility');
    const rules = [
      { text: `${data.visibilityLabels[visibility] || visibility}: ${
        { public: 'requesters see it on their ticket.', private: 'technicians and admins only.', admin_only: 'super admins only.' }[visibility] || ''}`, className: 'scf-preview__rule--visibility' },
      { text: editable ? 'Technicians who can see it can change it.' : 'Read-only: only automations can set it.', className: editable ? '' : 'scf-preview__rule--logic' },
    ];
    if (input('show_in_list').checked) rules.push({ text: 'Offered as a ticket list column.' });
    if (!input('is_active').checked) rules.push({ text: 'Inactive: hidden from tickets.' });
    rules.forEach((rule) => previewRules.append(el('li', { className: `scf-preview__rule ${rule.className || ''}`.trim(), text: rule.text })));
  }

  function refresh(updateKey) {
    const type = radioValue('field_type') || 'text';
    const hasOptions = optionTypes.has(type);
    optionsTab.hidden = !hasOptions;
    if (!hasOptions && !$('[data-tcf-panel="options"]').hidden) showTab('details');
    optionsCount.textContent = hasOptions ? ` ${cleanOptions().length}` : '';
    ratingField.hidden = type !== 'rating';
    if (updateKey && !state.keyTouched) {
      keyInput.value = keyFromLabel(labelInput.value);
    }
    if (keyExample) {
      keyExample.textContent = `{{ ticket.custom_fields.${keyInput.value || 'your_key'} }}`;
    }
    renderPreview();
  }

  labelInput.addEventListener('input', () => refresh(true));
  keyInput.addEventListener('input', () => {
    state.keyTouched = keyInput.value.trim() !== '';
    refresh(false);
  });
  $$('[data-tcf-input]').forEach((node) => {
    if (node === labelInput || node === keyInput) return;
    node.addEventListener(node.type === 'radio' || node.type === 'checkbox' ? 'change' : 'input', () => refresh(false));
  });

  // ------------------------------------------------------------ validation

  function showError(name, message) {
    const node = $(`[data-tcf-error="${name}"]`);
    if (!node) return;
    node.textContent = message || '';
    node.hidden = !message;
  }

  function validate() {
    const errors = {};
    const label = labelInput.value.trim();
    const key = keyInput.value.trim() || keyFromLabel(label);
    if (!label) errors.label = 'Enter a label for the detail.';
    if (!/^[a-z][a-z0-9_]*$/.test(key)) {
      errors.field_key = 'Start with a letter and use only lowercase letters, numbers and underscores.';
    } else if (existingKeys.some((entry) => entry.key === key && entry.id !== state.id)) {
      errors.field_key = `Another detail already uses the key “${key}”.`;
    }
    if (optionTypes.has(radioValue('field_type')) && !cleanOptions().length) {
      errors.options = 'Add at least one choice.';
    }
    ['label', 'field_key', 'options'].forEach((name) => showError(name, errors[name]));
    if (errors.label || errors.field_key) showTab('details');
    else if (errors.options) showTab('options');
    return !Object.keys(errors).length;
  }

  // ----------------------------------------------------------------- modal

  function serialize() {
    return JSON.stringify([
      labelInput.value, keyInput.value, input('help_text').value, input('sort_order').value,
      radioValue('field_type'), radioValue('visibility'), radioValue('editable'),
      input('show_in_list').checked, input('is_active').checked, input('rating_max').value, cleanOptions(),
    ]);
  }

  function load(field, mode) {
    const source = field || {};
    state.id = mode === 'edit' ? String(source.id) : '';
    state.options = Array.isArray(source.options) ? source.options.slice() : [];
    labelInput.value = mode === 'duplicate' ? `${source.label} (copy)` : (source.label || '');
    keyInput.value = mode === 'edit' ? (source.field_key || '') : '';
    state.keyTouched = mode === 'edit';
    input('help_text').value = source.help_text || '';
    input('sort_order').value = String(source.sort_order || 0);
    input('rating_max').value = String(source.rating_max || 5);
    setRadio('field_type', source.field_type || 'text');
    setRadio('visibility', source.visibility || 'private');
    setRadio('editable', source.editable === false ? '' : '1');
    input('show_in_list').checked = Boolean(source.show_in_list);
    input('is_active').checked = source.is_active !== false;
    activeField.hidden = mode !== 'edit';
    deleteButton.hidden = mode !== 'edit';
    bulkPanel.hidden = true;
    ['label', 'field_key', 'options'].forEach((name) => showError(name, ''));
    $('[data-tcf-title]').textContent = mode === 'edit' ? `Edit “${source.label}”` : 'Add custom ticket detail';
    $('[data-tcf-subtitle]').textContent = mode === 'edit'
      ? `Saved on ${source.usage_count || 0} ticket${source.usage_count === 1 ? '' : 's'}. Changing the key does not lose saved values.`
      : 'Name the detail, choose how it is answered, then decide who sees and changes it.';
    renderOptions();
    refresh(mode !== 'edit');
  }

  let lastTrigger = null;

  function openModal(trigger, field, mode) {
    lastTrigger = trigger || null;
    load(field, mode);
    showTab('details');
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('scf-modal-open');
    state.snapshot = serialize();
    window.requestAnimationFrame(() => labelInput.focus());
  }

  function closeModal(force) {
    if (!force && serialize() !== state.snapshot && !window.confirm('Discard your unsaved changes to this detail?')) {
      return;
    }
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('scf-modal-open');
    if (lastTrigger && typeof lastTrigger.focus === 'function') lastTrigger.focus();
  }

  modal.querySelectorAll('[data-modal-close]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      closeModal(false);
    });
  });
  modal.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeModal(false);
    }
  });
  window.addEventListener('beforeunload', (event) => {
    if (!modal.hidden && serialize() !== state.snapshot) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  document.querySelectorAll('[data-tcf-create]').forEach((button) => {
    button.addEventListener('click', () => openModal(button, null, 'create'));
  });
  document.querySelectorAll('[data-tcf-edit]').forEach((button) => {
    button.addEventListener('click', () => openModal(button, fieldsById.get(button.dataset.tcfEdit), 'edit'));
  });
  document.querySelectorAll('[data-tcf-duplicate]').forEach((button) => {
    button.addEventListener('click', () => openModal(button, fieldsById.get(button.dataset.tcfDuplicate), 'duplicate'));
  });

  form.addEventListener('submit', (event) => {
    if (!validate()) {
      event.preventDefault();
      return;
    }
    if (!keyInput.value.trim()) keyInput.value = keyFromLabel(labelInput.value);
    $('[data-tcf-out="field_id"]').value = state.id;
    $('[data-tcf-out="options_json"]').value = JSON.stringify(cleanOptions());
    state.snapshot = serialize();
  });

  deleteButton.addEventListener('click', () => {
    const field = fieldsById.get(state.id);
    if (!field || !deleteForm) return;
    const count = field.usage_count || 0;
    const message = count
      ? `Delete “${field.label}”? Its value on ${count} ticket${count === 1 ? '' : 's'} will also be deleted.`
      : `Delete “${field.label}”?`;
    if (!window.confirm(message)) return;
    deleteForm.action = `/admin/tickets/custom-fields/${encodeURIComponent(field.id)}/delete`;
    state.snapshot = serialize();
    deleteForm.submit();
  });

  // ---------------------------------------------------------------- search

  const search = document.querySelector('[data-tcf-search]');
  if (search) {
    search.addEventListener('input', () => {
      const term = search.value.trim().toLowerCase();
      let visible = 0;
      document.querySelectorAll('[data-tcf-item]').forEach((item) => {
        const match = !term || (item.dataset.tcfSearchText || '').toLowerCase().includes(term);
        item.hidden = !match;
        if (match) visible += 1;
      });
      document.querySelectorAll('[data-tcf-group]').forEach((group) => {
        group.hidden = !group.querySelector('[data-tcf-item]:not([hidden])');
      });
      const empty = document.querySelector('[data-tcf-no-results]');
      if (empty) empty.hidden = visible > 0;
    });
  }
})();
