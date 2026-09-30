(function () {
  'use strict';

  const TYPE_LABELS = {
    text: 'Text',
    select: 'Dropdown',
    multiselect: 'Multi-select',
    checkbox: 'Checkbox',
    date: 'Date',
  };

  const OPERATOR_LABELS = {
    is_checked: 'is ticked',
    is_not_checked: 'is not ticked',
    equals: 'is',
    not_equals: 'is not',
    one_of: 'is any of',
    select_map: 'limits which options appear',
  };

  const OPTION_TYPES = new Set(['select', 'multiselect']);
  const M365_FIELD_TYPES = new Set(['checkbox', 'select']);
  const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const KEY_PATTERN = /^[a-z0-9_]+$/;

  function normalize(value) {
    return String(value == null ? '' : value).trim().toLowerCase();
  }

  function slugify(value) {
    return String(value || '')
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 100);
  }

  function splitList(value, separator) {
    return String(value || '')
      .split(separator || /[,;]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function uniqueList(items) {
    const seen = new Set();
    return items.filter((item) => {
      const key = normalize(item);
      if (!key || seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
  }

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
      if (child === null || child === undefined || child === false) {
        return;
      }
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  function parseJson(id) {
    const node = document.getElementById(id);
    if (!node) {
      return null;
    }
    try {
      return JSON.parse(node.textContent || 'null');
    } catch (error) {
      console.error('Unable to parse staff custom field data', error);
      return null;
    }
  }

  /**
   * Parse a stored select_map condition (JSON or the legacy
   * "match=>a|b;*=>c" format) into { entries: Map<match, string[]>, fallback }.
   * Mirrors parseConditionalSelectOptions in staff.js.
   */
  function parseSelectMap(rawValue) {
    const text = String(rawValue || '').trim();
    const result = { entries: new Map(), fallback: null };
    if (!text) {
      return result;
    }
    const addEntry = (rawMatch, rawOptions) => {
      const options = Array.isArray(rawOptions)
        ? rawOptions.map((item) => String(item || '').trim()).filter(Boolean)
        : splitList(rawOptions, /[,|]/);
      if (!options.length) {
        return;
      }
      const match = normalize(rawMatch);
      if (match === '*' || match === 'fallback' || match === 'default') {
        if (!result.fallback) {
          result.fallback = options;
        }
        return;
      }
      if (match && !result.entries.has(match)) {
        result.entries.set(match, options);
      }
    };
    if (text.startsWith('{')) {
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          Object.entries(parsed).forEach(([match, options]) => addEntry(match, options));
          return result;
        }
      } catch (error) {
        // Fall through to the legacy format.
      }
    }
    const chunks = text.replace(/\r?\n/g, ';').split(';').map((chunk) => chunk.trim()).filter(Boolean);
    chunks.forEach((chunk) => {
      const arrow = chunk.indexOf('=>');
      if (arrow < 0) {
        return;
      }
      addEntry(chunk.slice(0, arrow), chunk.slice(arrow + 2).split('|'));
    });
    return result;
  }

  function initTagInput(container, { validate, separator, onChange }) {
    const input = container.querySelector('.scf-tags__input');
    let values = [];
    const splitter = separator || /[,;\n]/;

    function render() {
      container.querySelectorAll('.scf-tag').forEach((tag) => tag.remove());
      values.forEach((value, index) => {
        const invalid = validate ? !validate(value) : false;
        const tag = el('span', { className: `scf-tag${invalid ? ' scf-tag--invalid' : ''}` }, [
          el('span', { className: 'scf-tag__text', text: value }),
          el('button', {
            type: 'button',
            className: 'scf-tag__remove',
            'aria-label': `Remove ${value}`,
            dataset: { index: String(index) },
            text: '×',
          }),
        ]);
        if (invalid) {
          tag.title = 'This entry does not look valid';
        }
        container.insertBefore(tag, input);
      });
    }

    function commit(raw) {
      const additions = splitList(raw, splitter);
      if (!additions.length) {
        return false;
      }
      values = uniqueList(values.concat(additions));
      render();
      onChange();
      return true;
    }

    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ',' || event.key === ';') {
        if (input.value.trim()) {
          event.preventDefault();
          commit(input.value);
          input.value = '';
        } else if (event.key === 'Enter') {
          event.preventDefault();
        }
      } else if (event.key === 'Backspace' && !input.value && values.length) {
        values.pop();
        render();
        onChange();
      }
    });
    input.addEventListener('paste', (event) => {
      const text = (event.clipboardData || window.clipboardData).getData('text');
      if (text && splitter.test(text)) {
        event.preventDefault();
        commit(text);
      }
    });
    input.addEventListener('blur', () => {
      if (commit(input.value)) {
        input.value = '';
      }
    });
    container.addEventListener('click', (event) => {
      const remove = event.target.closest('.scf-tag__remove');
      if (remove) {
        values.splice(Number(remove.dataset.index), 1);
        render();
        onChange();
        input.focus();
        return;
      }
      if (event.target === container) {
        input.focus();
      }
    });

    return {
      get() {
        const pending = input.value.trim();
        return pending ? uniqueList(values.concat(splitList(pending, splitter))) : values.slice();
      },
      set(next) {
        values = uniqueList(next || []);
        input.value = '';
        render();
      },
      invalid() {
        return validate ? this.get().filter((value) => !validate(value)) : [];
      },
      focus() {
        input.focus();
      },
    };
  }

  document.addEventListener('DOMContentLoaded', () => {
    const data = parseJson('staff-custom-field-editor-data');
    const modal = document.getElementById('staff-custom-field-modal');
    if (!data || !modal) {
      return;
    }

    const companyId = data.companyId;
    const fields = Array.isArray(data.fields) ? data.fields : [];
    const standardFields = Array.isArray(data.standardFields) ? data.standardFields : [];
    const fieldsById = new Map(fields.map((field) => [String(field.id), field]));
    const baseUrl = `/admin/companies/${companyId}/staff-custom-fields`;

    const form = modal.querySelector('[data-scf-form]');
    const deleteForm = document.querySelector('[data-scf-delete-form]');
    const titleEl = modal.querySelector('[data-scf-title]');
    const subtitleEl = modal.querySelector('[data-scf-subtitle]');
    const labelInput = form.querySelector('#scf-label');
    const keyInput = form.querySelector('#scf-key');
    const keyHelp = form.querySelector('[data-scf-key-help]');
    const groupInput = form.querySelector('#scf-group');
    const helpInput = form.querySelector('#scf-help');
    const helpCounter = form.querySelector('[data-scf-help-counter]');
    const orderInput = form.querySelector('#scf-order');
    const activeField = form.querySelector('[data-scf-active-field]');
    const activeInput = form.querySelector('[name="is_active"]');
    const typeInputs = Array.from(form.querySelectorAll('[name="field_type"]'));
    const tabs = Array.from(form.querySelectorAll('[data-scf-tab]'));
    const panels = Array.from(form.querySelectorAll('[data-scf-panel]'));
    const optionsList = form.querySelector('[data-scf-options]');
    const optionsCount = form.querySelector('[data-scf-options-count]');
    const bulkPanel = form.querySelector('[data-scf-bulk]');
    const bulkInput = form.querySelector('[data-scf-bulk-input]');
    const conditionModes = Array.from(form.querySelectorAll('[data-scf-condition-mode]'));
    const conditionPanel = form.querySelector('[data-scf-condition]');
    const parentSelect = form.querySelector('[data-scf-condition-parent]');
    const operatorSelect = form.querySelector('[data-scf-condition-operator]');
    const conditionValueSlot = form.querySelector('[data-scf-condition-value]');
    const conditionSentence = form.querySelector('[data-scf-condition-sentence]');
    const visibilityModes = Array.from(form.querySelectorAll('[data-scf-visibility-mode]'));
    const visibilityPanel = form.querySelector('[data-scf-visibility]');
    const m365FieldBlock = form.querySelector('[data-scf-m365-field]');
    const m365OptionsBlock = form.querySelector('[data-scf-m365-options]');
    const m365OptionList = form.querySelector('[data-scf-m365-option-list]');
    const m365Unsupported = form.querySelector('[data-scf-m365-unsupported]');
    const previewCard = form.querySelector('[data-scf-preview]');
    const previewRules = form.querySelector('[data-scf-preview-rules]');
    const deleteButton = form.querySelector('[data-scf-delete]');
    const outputs = {};
    form.querySelectorAll('[data-scf-out]').forEach((input) => {
      outputs[input.dataset.scfOut] = input;
    });

    const groupOptions = form.querySelector('[data-scf-group-options]');
    uniqueList(fields.map((field) => field.field_group || '')).forEach((group) => {
      groupOptions.append(el('option', { value: group }));
    });

    const state = {
      mode: 'create',
      id: null,
      keyTouched: false,
      options: [],
      condition: { enabled: false, parent: '', operator: '', value: '', values: [], map: new Map(), fallback: [] },
      snapshot: '',
      activeTab: 'details',
    };
    let optionUid = 0;

    const tagInputs = {
      jobTitles: initTagInput(form.querySelector('[data-scf-tags="job_titles"]'), { onChange: refresh }),
      emails: initTagInput(form.querySelector('[data-scf-tags="emails"]'), {
        validate: (value) => EMAIL_PATTERN.test(value),
        onChange: refresh,
      }),
      m365: initTagInput(form.querySelector('[data-scf-tags="m365"]'), { separator: /[|,;\n]/, onChange: refresh }),
    };
    let oneOfTags = null;

    // ----- helpers -------------------------------------------------------

    function currentType() {
      const checked = typeInputs.find((input) => input.checked);
      return checked ? checked.value : 'text';
    }

    function setType(value) {
      typeInputs.forEach((input) => {
        input.checked = input.value === value;
      });
    }

    function currentKey() {
      return state.mode === 'edit' ? state.fieldName : slugify(keyInput.value);
    }

    function parentCandidates() {
      const selfKey = currentKey();
      const custom = fields
        .filter((field) => field.name && field.name !== selfKey)
        .map((field) => ({
          key: field.name,
          label: field.display_name || field.name,
          type: field.field_type || 'text',
          options: Array.isArray(field.options) ? field.options : [],
          source: 'custom',
          inactive: !field.is_active,
        }));
      const standard = standardFields
        .filter((field) => field.key && field.key !== selfKey)
        .map((field) => ({
          key: field.key,
          label: field.label || field.key,
          type: field.type || 'text',
          options: Array.isArray(field.options) ? field.options : [],
          source: 'standard',
        }));
      return { custom, standard, all: custom.concat(standard) };
    }

    function findParent(key) {
      const normalized = normalize(key);
      return parentCandidates().all.find((candidate) => normalize(candidate.key) === normalized) || null;
    }

    function optionLabel(option) {
      return option.label || option.value || '';
    }

    function optionValue(option) {
      return (option.value || '').trim() || (option.label || '').trim();
    }

    function allowedOperators(parent) {
      const type = parent ? parent.type : null;
      let operators;
      if (type === 'checkbox') {
        operators = ['is_checked', 'is_not_checked'];
      } else if (type) {
        operators = ['equals', 'not_equals', 'one_of'];
        if (currentType() === 'select' && type === 'select') {
          operators.push('select_map');
        }
      } else {
        operators = ['is_checked', 'is_not_checked', 'equals', 'not_equals', 'one_of', 'select_map'];
      }
      const current = state.condition.operator;
      if (current && OPERATOR_LABELS[current] && !operators.includes(current)) {
        // Keep an existing rule's operator selectable so saved logic is never silently changed.
        operators.push(current);
      }
      return operators;
    }

    function findOwnOption(ref) {
      const normalized = normalize(ref);
      return state.options.find((option) => (
        normalize(optionValue(option)) === normalized || normalize(option.label) === normalized
      )) || null;
    }

    // ----- tabs ----------------------------------------------------------

    function tabAvailable(name) {
      const type = currentType();
      if (name === 'options') {
        return OPTION_TYPES.has(type);
      }
      return true;
    }

    function showTab(name, focus) {
      if (!tabAvailable(name)) {
        name = 'details';
      }
      state.activeTab = name;
      tabs.forEach((tab) => {
        const active = tab.dataset.scfTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
        if (active && focus) {
          tab.focus();
        }
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.scfPanel !== name;
      });
    }

    tabs.forEach((tab) => {
      tab.addEventListener('click', () => showTab(tab.dataset.scfTab));
      tab.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
          return;
        }
        event.preventDefault();
        const visible = tabs.filter((item) => !item.hidden);
        const index = visible.indexOf(tab);
        const next = visible[(index + (event.key === 'ArrowRight' ? 1 : -1) + visible.length) % visible.length];
        showTab(next.dataset.scfTab, true);
      });
    });

    // ----- options -------------------------------------------------------

    function renderOptions() {
      optionsList.textContent = '';
      state.options.forEach((option, index) => {
        const labelField = el('input', {
          className: 'form-input',
          value: option.label,
          placeholder: `Option ${index + 1}`,
          'aria-label': `Option ${index + 1} label`,
          dataset: { scfOptionField: 'label' },
        });
        const valueField = el('input', {
          className: 'form-input scf-mono',
          value: option.valueTouched ? option.value : '',
          placeholder: option.label ? option.label : 'Same as label',
          'aria-label': `Option ${index + 1} stored value`,
          spellcheck: 'false',
          dataset: { scfOptionField: 'value' },
        });
        const row = el('li', { className: 'scf-option', dataset: { uid: String(option.uid) } }, [
          el('span', { className: 'scf-option__move' }, [
            el('button', {
              type: 'button',
              className: 'scf-icon-button',
              'aria-label': 'Move option up',
              title: 'Move up',
              disabled: index === 0,
              dataset: { scfOptionMove: '-1' },
              text: '↑',
            }),
            el('button', {
              type: 'button',
              className: 'scf-icon-button',
              'aria-label': 'Move option down',
              title: 'Move down',
              disabled: index === state.options.length - 1,
              dataset: { scfOptionMove: '1' },
              text: '↓',
            }),
          ]),
          labelField,
          valueField,
          el('button', {
            type: 'button',
            className: 'scf-icon-button scf-icon-button--danger',
            'aria-label': `Remove option ${option.label || index + 1}`,
            title: 'Remove option',
            dataset: { scfOptionRemove: '' },
            text: '×',
          }),
        ]);
        optionsList.append(row);
      });
      if (!state.options.length) {
        optionsList.append(el('li', { className: 'scf-options__empty text-muted', text: 'No options yet. Add at least one.' }));
      }
    }

    function addOption(label, value) {
      const option = {
        uid: ++optionUid,
        label: label || '',
        value: value || '',
        valueTouched: Boolean(value) && value !== label,
        m365_upn: '',
      };
      state.options.push(option);
      return option;
    }

    optionsList.addEventListener('input', (event) => {
      const input = event.target.closest('[data-scf-option-field]');
      if (!input) {
        return;
      }
      const row = input.closest('.scf-option');
      const option = state.options.find((item) => String(item.uid) === row.dataset.uid);
      if (!option) {
        return;
      }
      if (input.dataset.scfOptionField === 'label') {
        option.label = input.value;
        const valueField = row.querySelector('[data-scf-option-field="value"]');
        valueField.placeholder = option.label || 'Same as label';
        if (!option.valueTouched) {
          option.value = '';
        }
      } else {
        option.value = input.value;
        option.valueTouched = input.value.trim() !== '';
      }
      refresh({ keepOptions: true });
    });

    optionsList.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || !event.target.matches('[data-scf-option-field]')) {
        return;
      }
      event.preventDefault();
      const row = event.target.closest('.scf-option');
      const index = state.options.findIndex((item) => String(item.uid) === row.dataset.uid);
      const option = addOption('');
      state.options.splice(state.options.length - 1, 1);
      state.options.splice(index + 1, 0, option);
      refresh();
      focusOption(option.uid);
    });

    optionsList.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button) {
        return;
      }
      const row = button.closest('.scf-option');
      const index = state.options.findIndex((item) => String(item.uid) === row.dataset.uid);
      if (index < 0) {
        return;
      }
      if (button.hasAttribute('data-scf-option-remove')) {
        state.options.splice(index, 1);
        refresh();
        const next = state.options[Math.min(index, state.options.length - 1)];
        if (next) {
          focusOption(next.uid);
        }
        return;
      }
      const offset = Number(button.dataset.scfOptionMove || 0);
      const target = index + offset;
      if (offset && target >= 0 && target < state.options.length) {
        const [moved] = state.options.splice(index, 1);
        state.options.splice(target, 0, moved);
        refresh();
        const movedRow = optionsList.querySelector(`[data-uid="${moved.uid}"] [data-scf-option-move="${offset}"]`);
        if (movedRow && !movedRow.disabled) {
          movedRow.focus();
        }
      }
    });

    function focusOption(uid) {
      const input = optionsList.querySelector(`[data-uid="${uid}"] [data-scf-option-field="label"]`);
      if (input) {
        input.focus();
      }
    }

    form.querySelector('[data-scf-option-add]').addEventListener('click', () => {
      const option = addOption('');
      refresh();
      focusOption(option.uid);
    });

    form.querySelectorAll('[data-scf-bulk-toggle]').forEach((button) => {
      button.addEventListener('click', () => {
        bulkPanel.hidden = !bulkPanel.hidden;
        form.querySelector('[data-scf-bulk-toggle][aria-expanded]').setAttribute('aria-expanded', bulkPanel.hidden ? 'false' : 'true');
        if (!bulkPanel.hidden) {
          bulkInput.focus();
        }
      });
    });

    form.querySelector('[data-scf-bulk-apply]').addEventListener('click', () => {
      const existing = new Set(state.options.map((option) => normalize(optionValue(option))));
      bulkInput.value.split(/\r?\n/).forEach((line) => {
        const text = line.trim();
        if (!text) {
          return;
        }
        let value = '';
        let label = text;
        const separator = text.indexOf('=');
        if (separator > 0) {
          value = text.slice(0, separator).trim();
          label = text.slice(separator + 1).trim() || value;
        }
        const key = normalize(value || label);
        if (existing.has(key)) {
          return;
        }
        existing.add(key);
        addOption(label, value);
      });
      state.options = state.options.filter((option) => optionValue(option));
      bulkInput.value = '';
      bulkPanel.hidden = true;
      refresh();
    });

    // ----- conditions ----------------------------------------------------

    function renderParentSelect() {
      const { custom, standard } = parentCandidates();
      parentSelect.textContent = '';
      parentSelect.append(el('option', { value: '', text: 'Choose a field…' }));
      const addGroup = (label, items) => {
        if (!items.length) {
          return;
        }
        const group = el('optgroup', { label });
        items.forEach((item) => {
          group.append(el('option', {
            value: item.key,
            text: `${item.label} (${TYPE_LABELS[item.type] || item.type})${item.inactive ? ' – inactive' : ''}`,
          }));
        });
        parentSelect.append(group);
      };
      addGroup('Custom fields', custom);
      addGroup('Standard staff fields', standard);
      if (state.condition.parent && !findParent(state.condition.parent)) {
        parentSelect.append(el('option', { value: state.condition.parent, text: `${state.condition.parent} (field not found)` }));
      }
      parentSelect.value = state.condition.parent || '';
    }

    function renderOperatorSelect() {
      const parent = findParent(state.condition.parent);
      const operators = allowedOperators(parent);
      if (!operators.includes(state.condition.operator)) {
        state.condition.operator = operators[0];
      }
      operatorSelect.textContent = '';
      operators.forEach((operator) => {
        operatorSelect.append(el('option', { value: operator, text: OPERATOR_LABELS[operator] || operator }));
      });
      operatorSelect.value = state.condition.operator;
      operatorSelect.disabled = !state.condition.parent;
    }

    function renderConditionValue() {
      conditionValueSlot.textContent = '';
      oneOfTags = null;
      const parent = findParent(state.condition.parent);
      const operator = state.condition.operator;
      if (!state.condition.parent || operator === 'is_checked' || operator === 'is_not_checked') {
        return;
      }
      const parentOptions = parent && Array.isArray(parent.options) ? parent.options : [];

      if (operator === 'equals' || operator === 'not_equals') {
        let control;
        if (parentOptions.length) {
          control = el('select', { className: 'form-input', id: 'scf-condition-single', dataset: { scfConditionSingle: '' } }, [
            el('option', { value: '', text: 'Choose a value…' }),
          ]);
          let matched = false;
          parentOptions.forEach((option) => {
            const selected = normalize(option.value) === normalize(state.condition.value);
            matched = matched || selected;
            control.append(el('option', { value: option.value, selected, text: optionLabel(option) }));
          });
          if (state.condition.value && !matched) {
            control.append(el('option', { value: state.condition.value, selected: true, text: `${state.condition.value} (not an option)` }));
          }
        } else {
          control = el('input', {
            className: 'form-input',
            id: 'scf-condition-single',
            value: state.condition.value,
            placeholder: 'Value to match',
            dataset: { scfConditionSingle: '' },
          });
        }
        conditionValueSlot.append(el('div', { className: 'form-field' }, [
          el('label', { className: 'form-label', for: 'scf-condition-single', text: 'Value' }),
          control,
        ]));
        return;
      }

      if (operator === 'one_of') {
        if (parentOptions.length) {
          const selected = new Set(state.condition.values.map(normalize));
          const list = el('div', { className: 'scf-check-grid' });
          parentOptions.forEach((option) => {
            list.append(el('label', { className: 'scf-check' }, [
              el('input', {
                type: 'checkbox',
                value: option.value,
                checked: selected.has(normalize(option.value)) || selected.has(normalize(option.label)),
                dataset: { scfConditionMulti: '' },
              }),
              el('span', { text: optionLabel(option) }),
            ]));
          });
          const known = new Set(parentOptions.flatMap((option) => [normalize(option.value), normalize(option.label)]));
          state.condition.values.filter((value) => !known.has(normalize(value))).forEach((value) => {
            list.append(el('label', { className: 'scf-check' }, [
              el('input', { type: 'checkbox', value, checked: true, dataset: { scfConditionMulti: '' } }),
              el('span', { text: `${value} (not an option)` }),
            ]));
          });
          conditionValueSlot.append(el('fieldset', { className: 'scf-fieldset' }, [
            el('legend', { className: 'form-label', text: 'Any of these values' }),
            list,
          ]));
        } else {
          const container = el('div', { className: 'scf-tags' }, [
            el('input', { className: 'scf-tags__input', id: 'scf-condition-tags', placeholder: 'Type a value and press Enter', autocomplete: 'off' }),
          ]);
          conditionValueSlot.append(el('div', { className: 'form-field' }, [
            el('label', { className: 'form-label', for: 'scf-condition-tags', text: 'Any of these values' }),
            container,
          ]));
          oneOfTags = initTagInput(container, {
            onChange() {
              state.condition.values = oneOfTags.get();
              refresh({ keepCondition: true });
            },
          });
          oneOfTags.set(state.condition.values);
        }
        return;
      }

      if (operator === 'select_map') {
        renderSelectMap(parent, parentOptions);
      }
    }

    function renderSelectMap(parent, parentOptions) {
      const ownOptions = state.options.filter((option) => optionValue(option));
      if (!ownOptions.length) {
        conditionValueSlot.append(el('p', {
          className: 'scf-empty scf-empty--inline',
          text: 'Add options to this field first, then choose which ones appear for each answer.',
        }));
        return;
      }
      const rows = [];
      const known = new Set();
      parentOptions.forEach((option) => {
        const key = normalize(option.value);
        known.add(key);
        known.add(normalize(option.label));
        const refs = state.condition.map.get(key) || state.condition.map.get(normalize(option.label)) || [];
        rows.push({ match: option.value, label: optionLabel(option), refs });
      });
      state.condition.map.forEach((refs, key) => {
        if (!known.has(key)) {
          rows.push({ match: key, label: `${key} (not an option)`, refs });
        }
      });
      rows.push({ match: '*', label: 'Any other answer', refs: state.condition.fallback, fallback: true });

      const table = el('div', { className: 'scf-map' }, [
        el('div', { className: 'scf-map__head' }, [
          el('span', { text: `When ${parent ? parent.label : state.condition.parent} is…` }),
          el('span', { text: 'Offer these options' }),
        ]),
      ]);
      rows.forEach((row) => {
        const selected = new Set(row.refs.map((ref) => {
          const own = findOwnOption(ref);
          return own ? normalize(optionValue(own)) : normalize(ref);
        }));
        const chips = el('div', { className: 'scf-map__options' });
        ownOptions.forEach((option) => {
          chips.append(el('label', { className: 'scf-pill-check' }, [
            el('input', {
              type: 'checkbox',
              value: optionValue(option),
              checked: selected.has(normalize(optionValue(option))),
              dataset: { scfMapOption: '' },
            }),
            el('span', { text: option.label || optionValue(option) }),
          ]));
        });
        table.append(el('div', {
          className: `scf-map__row${row.fallback ? ' scf-map__row--fallback' : ''}`,
          dataset: { scfMapMatch: row.match },
        }, [
          el('span', { className: 'scf-map__match', text: row.label }),
          chips,
        ]));
      });
      conditionValueSlot.append(table);
      conditionValueSlot.append(el('p', {
        className: 'form-help',
        text: 'Answers with no options ticked hide this field. Leave "Any other answer" empty to hide it for everything else.',
      }));
      if (!parentOptions.length) {
        const addRow = el('div', { className: 'scf-map__add' }, [
          el('input', { className: 'form-input', placeholder: 'Add an answer to map, e.g. sales', dataset: { scfMapNew: '' } }),
          el('button', { type: 'button', className: 'button button--ghost button--small', dataset: { scfMapAdd: '' }, text: 'Add answer' }),
        ]);
        conditionValueSlot.append(addRow);
      }
    }

    function readSelectMapFromDom() {
      const map = new Map();
      let fallback = [];
      conditionValueSlot.querySelectorAll('[data-scf-map-match]').forEach((row) => {
        const refs = Array.from(row.querySelectorAll('[data-scf-map-option]:checked')).map((input) => input.value);
        const match = row.dataset.scfMapMatch;
        if (match === '*') {
          fallback = refs;
        } else {
          map.set(normalize(match), refs);
        }
      });
      state.condition.map = map;
      state.condition.fallback = fallback;
    }

    conditionValueSlot.addEventListener('change', (event) => {
      const target = event.target;
      if (target.matches('[data-scf-condition-single]')) {
        state.condition.value = target.value;
      } else if (target.matches('[data-scf-condition-multi]')) {
        state.condition.values = Array.from(conditionValueSlot.querySelectorAll('[data-scf-condition-multi]:checked')).map((input) => input.value);
      } else if (target.matches('[data-scf-map-option]')) {
        readSelectMapFromDom();
      } else {
        return;
      }
      refresh({ keepCondition: true });
    });
    conditionValueSlot.addEventListener('input', (event) => {
      if (event.target.matches('input[data-scf-condition-single]')) {
        state.condition.value = event.target.value;
        refresh({ keepCondition: true });
      }
    });
    conditionValueSlot.addEventListener('click', (event) => {
      if (!event.target.closest('[data-scf-map-add]')) {
        return;
      }
      const input = conditionValueSlot.querySelector('[data-scf-map-new]');
      const match = normalize(input && input.value);
      if (match && match !== '*' && !state.condition.map.has(match)) {
        readSelectMapFromDom();
        state.condition.map.set(match, []);
        refresh();
        const next = conditionValueSlot.querySelector('[data-scf-map-new]');
        if (next) {
          next.focus();
        }
      }
    });
    conditionValueSlot.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && event.target.matches('[data-scf-map-new]')) {
        event.preventDefault();
        conditionValueSlot.querySelector('[data-scf-map-add]').click();
      }
    });

    conditionModes.forEach((input) => {
      input.addEventListener('change', () => {
        state.condition.enabled = input.value === 'conditional' && input.checked;
        refresh();
        if (state.condition.enabled && !state.condition.parent) {
          parentSelect.focus();
        }
      });
    });

    parentSelect.addEventListener('change', () => {
      state.condition.parent = parentSelect.value;
      state.condition.value = '';
      state.condition.values = [];
      state.condition.map = new Map();
      state.condition.fallback = [];
      state.condition.operator = '';
      refresh();
    });

    operatorSelect.addEventListener('change', () => {
      state.condition.operator = operatorSelect.value;
      refresh();
    });

    function conditionSentenceText() {
      if (!state.condition.enabled) {
        return 'Always shown.';
      }
      const parent = findParent(state.condition.parent);
      if (!state.condition.parent) {
        return 'Choose the field that controls when this one appears.';
      }
      const parentLabel = parent ? parent.label : state.condition.parent;
      const describe = (value) => {
        const match = parent && parent.options
          ? parent.options.find((option) => normalize(option.value) === normalize(value))
          : null;
        return match ? optionLabel(match) : value;
      };
      switch (state.condition.operator) {
        case 'is_checked':
          return `Shown when “${parentLabel}” is ticked.`;
        case 'is_not_checked':
          return `Shown when “${parentLabel}” is not ticked.`;
        case 'equals':
          return state.condition.value
            ? `Shown when “${parentLabel}” is “${describe(state.condition.value)}”.`
            : `Choose the value of “${parentLabel}” that shows this field.`;
        case 'not_equals':
          return state.condition.value
            ? `Shown when “${parentLabel}” is anything except “${describe(state.condition.value)}”.`
            : `Choose the value of “${parentLabel}” that hides this field.`;
        case 'one_of':
          return state.condition.values.length
            ? `Shown when “${parentLabel}” is ${state.condition.values.map((value) => `“${describe(value)}”`).join(', ')}.`
            : `Choose one or more values of “${parentLabel}”.`;
        case 'select_map':
          return `Options offered depend on the answer to “${parentLabel}”.`;
        default:
          return '';
      }
    }

    function serializeCondition() {
      if (!state.condition.enabled || !state.condition.parent) {
        return { parent: '', operator: '', value: '' };
      }
      const operator = state.condition.operator;
      let value = '';
      if (operator === 'equals' || operator === 'not_equals') {
        value = state.condition.value.trim();
      } else if (operator === 'one_of') {
        value = uniqueList(state.condition.values).join(', ');
      } else if (operator === 'select_map') {
        const map = {};
        state.condition.map.forEach((refs, match) => {
          if (refs.length) {
            map[match] = refs.slice();
          }
        });
        if (state.condition.fallback.length) {
          map.fallback = state.condition.fallback.slice();
        }
        value = Object.keys(map).length ? JSON.stringify(map) : '';
      }
      return { parent: state.condition.parent, operator, value };
    }

    // ----- visibility ----------------------------------------------------

    visibilityModes.forEach((input) => {
      input.addEventListener('change', () => {
        refresh();
        if (input.value === 'restricted' && input.checked) {
          tagInputs.jobTitles.focus();
        }
      });
    });

    function visibilityRestricted() {
      const checked = visibilityModes.find((input) => input.checked);
      return Boolean(checked && checked.value === 'restricted');
    }

    // ----- M365 ----------------------------------------------------------

    function renderM365Options() {
      m365OptionList.textContent = '';
      const options = state.options.filter((option) => optionValue(option));
      if (!options.length) {
        m365OptionList.append(el('p', { className: 'text-muted', text: 'Add options first to map them to mailboxes or groups.' }));
        return;
      }
      options.forEach((option) => {
        const id = `scf-m365-option-${option.uid}`;
        m365OptionList.append(el('div', { className: 'scf-m365-option' }, [
          el('label', { className: 'scf-m365-option__label', for: id, text: option.label || optionValue(option) }),
          el('input', {
            className: 'form-input',
            id,
            value: option.m365_upn || '',
            placeholder: 'mailbox@example.com | Group Name',
            autocomplete: 'off',
            spellcheck: 'false',
            dataset: { scfM365Option: String(option.uid) },
          }),
        ]));
      });
    }

    m365OptionList.addEventListener('input', (event) => {
      const input = event.target.closest('[data-scf-m365-option]');
      if (!input) {
        return;
      }
      const option = state.options.find((item) => String(item.uid) === input.dataset.scfM365Option);
      if (option) {
        option.m365_upn = input.value;
        refresh({ keepOptions: true, keepM365: true });
      }
    });

    // ----- preview -------------------------------------------------------

    function renderPreview() {
      const type = currentType();
      const label = labelInput.value.trim() || 'Untitled field';
      const help = helpInput.value.trim();
      const options = state.options.filter((option) => optionValue(option));
      previewCard.textContent = '';
      if (groupInput.value.trim()) {
        previewCard.append(el('p', { className: 'scf-preview__group', text: groupInput.value.trim() }));
      }
      let control;
      if (type === 'checkbox') {
        control = el('label', { className: 'scf-preview__checkbox' }, [
          el('input', { type: 'checkbox', tabindex: '-1', 'aria-hidden': 'true' }),
          el('span', { text: label }),
        ]);
        previewCard.append(control);
        if (help) {
          previewCard.append(el('p', { className: 'form-help', text: help }));
        }
      } else {
        previewCard.append(el('span', { className: 'form-label', text: label }));
        if (help) {
          previewCard.append(el('p', { className: 'form-help scf-preview__help', text: help }));
        }
        if (type === 'select') {
          control = el('select', { className: 'form-input', tabindex: '-1', 'aria-hidden': 'true' }, [
            el('option', { text: `Select ${label.toLowerCase()}` }),
          ].concat(options.map((option) => el('option', { text: option.label || optionValue(option) }))));
        } else if (type === 'multiselect') {
          control = el('div', { className: 'scf-preview__multi' }, options.length
            ? options.map((option) => el('label', { className: 'scf-check' }, [
              el('input', { type: 'checkbox', tabindex: '-1', 'aria-hidden': 'true' }),
              el('span', { text: option.label || optionValue(option) }),
            ]))
            : [el('span', { className: 'text-muted', text: 'No options yet' })]);
        } else if (type === 'date') {
          control = el('input', { className: 'form-input', type: 'date', tabindex: '-1', 'aria-hidden': 'true' });
        } else {
          control = el('input', { className: 'form-input', type: 'text', tabindex: '-1', 'aria-hidden': 'true', placeholder: 'Answer' });
        }
        previewCard.append(control);
      }
      if (state.mode === 'edit' && !activeInput.checked) {
        previewCard.append(el('p', { className: 'scf-preview__inactive', text: 'Inactive – hidden from staff forms' }));
      }

      previewRules.textContent = '';
      const rules = [];
      rules.push(['logic', conditionSentenceText()]);
      if (visibilityRestricted()) {
        const titles = tagInputs.jobTitles.get();
        const emails = tagInputs.emails.get();
        const parts = [];
        if (titles.length) {
          parts.push(`${titles.length} job title${titles.length === 1 ? '' : 's'}`);
        }
        if (emails.length) {
          parts.push(`${emails.length} email address${emails.length === 1 ? '' : 'es'}`);
        }
        rules.push(['visibility', parts.length ? `Only visible to requesters matching ${parts.join(' or ')}.` : 'Restricted, but no requesters added yet.']);
      } else {
        rules.push(['visibility', 'Visible to every requester.']);
      }
      if (type === 'multiselect') {
        const mapped = options.filter((option) => option.m365_upn && option.m365_upn.trim()).length;
        if (mapped) {
          rules.push(['m365', `${mapped} option${mapped === 1 ? '' : 's'} synced from Microsoft 365.`]);
        }
      } else if (M365_FIELD_TYPES.has(type) && tagInputs.m365.get().length) {
        rules.push(['m365', 'Synced from Microsoft 365 mailbox access.']);
      }
      rules.forEach(([kind, text]) => {
        previewRules.append(el('li', { className: `scf-preview__rule scf-preview__rule--${kind}`, text }));
      });
    }

    // ----- validation ----------------------------------------------------

    function validate() {
      const errors = {};
      const type = currentType();
      const addError = (tab, message, focusTarget) => {
        if (!errors[tab]) {
          errors[tab] = { messages: [], focus: focusTarget };
        }
        errors[tab].messages.push(message);
      };

      if (state.mode === 'create') {
        const key = slugify(keyInput.value);
        if (!labelInput.value.trim() && !key) {
          addError('details', 'Enter a label for the field.', labelInput);
        } else if (!key || !KEY_PATTERN.test(key)) {
          addError('details', 'The key can only contain lowercase letters, numbers and underscores.', keyInput);
        } else if (fields.some((field) => field.name === key)) {
          addError('details', `A field with the key “${key}” already exists.`, keyInput);
        }
      }

      if (OPTION_TYPES.has(type)) {
        const values = state.options.map((option) => normalize(optionValue(option))).filter(Boolean);
        if (!values.length) {
          addError('options', 'Add at least one option.', form.querySelector('[data-scf-option-add]'));
        }
        const duplicates = values.filter((value, index) => values.indexOf(value) !== index);
        if (duplicates.length) {
          addError('options', `Each option needs a unique value (duplicate: “${duplicates[0]}”).`);
        }
      }

      if (state.condition.enabled) {
        const operator = state.condition.operator;
        if (!state.condition.parent) {
          addError('logic', 'Choose which field controls this one, or switch back to “Always show”.', parentSelect);
        } else if ((operator === 'equals' || operator === 'not_equals') && !state.condition.value.trim()) {
          addError('logic', 'Choose the value to compare against.', conditionValueSlot.querySelector('[data-scf-condition-single]'));
        } else if (operator === 'one_of' && !state.condition.values.length) {
          addError('logic', 'Choose at least one value.');
        } else if (operator === 'select_map') {
          const hasAny = state.condition.fallback.length || Array.from(state.condition.map.values()).some((refs) => refs.length);
          if (!hasAny) {
            addError('logic', 'Tick at least one option for one of the answers.');
          }
        }
      }

      if (visibilityRestricted()) {
        const invalidEmails = tagInputs.emails.invalid();
        if (invalidEmails.length) {
          addError('visibility', `Check these email addresses: ${invalidEmails.join(', ')}.`);
        }
      }
      return errors;
    }

    function showErrors(errors) {
      tabs.forEach((tab) => {
        tab.classList.toggle('has-error', Boolean(errors[tab.dataset.scfTab]));
      });
      form.querySelectorAll('[data-scf-error]').forEach((node) => {
        const entry = errors[node.dataset.scfError];
        node.hidden = !entry;
        node.textContent = entry ? entry.messages.join(' ') : '';
      });
      const detailsError = errors.details;
      labelInput.classList.toggle('is-invalid', Boolean(detailsError && detailsError.focus === labelInput));
      keyInput.classList.toggle('is-invalid', Boolean(detailsError && detailsError.focus === keyInput));
      keyHelp.classList.toggle('scf-error', Boolean(detailsError));
      keyHelp.textContent = detailsError ? detailsError.messages.join(' ') : keyHelpText();
    }

    function keyHelpText() {
      return state.mode === 'edit'
        ? 'Keys can’t be changed once a field exists, because saved answers reference it.'
        : 'Generated from the label. Used by conditions, imports and automations.';
    }

    // ----- master refresh ------------------------------------------------

    function refresh(opts) {
      const settings = opts || {};
      const type = currentType();
      const hasOptions = OPTION_TYPES.has(type);

      tabs.forEach((tab) => {
        if (tab.dataset.scfTab === 'options') {
          tab.hidden = !hasOptions;
        }
      });
      if (!tabAvailable(state.activeTab)) {
        showTab('details');
      }
      const validOptions = state.options.filter((option) => optionValue(option)).length;
      optionsCount.textContent = hasOptions ? String(validOptions) : '';
      optionsCount.hidden = !hasOptions;

      if (!settings.keepOptions) {
        renderOptions();
      }

      conditionModes.forEach((input) => {
        input.checked = (input.value === 'conditional') === state.condition.enabled;
      });
      conditionPanel.hidden = !state.condition.enabled;
      if (!settings.keepCondition) {
        renderParentSelect();
        renderOperatorSelect();
        renderConditionValue();
      }
      conditionSentence.textContent = conditionSentenceText();

      visibilityPanel.hidden = !visibilityRestricted();

      m365FieldBlock.hidden = !M365_FIELD_TYPES.has(type);
      m365OptionsBlock.hidden = type !== 'multiselect';
      m365Unsupported.hidden = type === 'multiselect' || M365_FIELD_TYPES.has(type);
      if (type === 'multiselect' && !settings.keepM365) {
        renderM365Options();
      }

      helpCounter.textContent = `${helpInput.value.length} / 500`;
      form.querySelectorAll('.scf-type-card, .scf-choice').forEach((card) => {
        const input = card.querySelector('input');
        card.classList.toggle('is-selected', Boolean(input && input.checked));
      });

      const configured = {
        details: false,
        options: hasOptions && validOptions > 0,
        logic: state.condition.enabled && Boolean(state.condition.parent),
        visibility: visibilityRestricted() && (tagInputs.jobTitles.get().length + tagInputs.emails.get().length) > 0,
        m365: (M365_FIELD_TYPES.has(type) && tagInputs.m365.get().length > 0)
          || (type === 'multiselect' && state.options.some((option) => option.m365_upn && option.m365_upn.trim())),
      };
      tabs.forEach((tab) => {
        tab.classList.toggle('is-configured', Boolean(configured[tab.dataset.scfTab]));
      });

      renderPreview();
      if (form.dataset.submitted === 'true') {
        showErrors(validate());
      }
    }

    labelInput.addEventListener('input', () => {
      if (state.mode === 'create' && !state.keyTouched) {
        keyInput.value = slugify(labelInput.value);
      }
      refresh({ keepOptions: true, keepCondition: true, keepM365: true });
    });
    keyInput.addEventListener('input', () => {
      state.keyTouched = keyInput.value.trim() !== '';
      refresh({ keepOptions: true, keepCondition: true, keepM365: true });
    });
    keyInput.addEventListener('blur', () => {
      if (state.mode === 'create' && keyInput.value) {
        keyInput.value = slugify(keyInput.value);
      }
    });
    [groupInput, helpInput, orderInput].forEach((input) => {
      input.addEventListener('input', () => refresh({ keepOptions: true, keepCondition: true, keepM365: true }));
    });
    activeInput.addEventListener('change', () => refresh({ keepOptions: true, keepCondition: true, keepM365: true }));
    typeInputs.forEach((input) => {
      input.addEventListener('change', () => {
        if (OPTION_TYPES.has(currentType()) && !state.options.length) {
          addOption('');
        }
        refresh();
      });
    });

    // ----- load / open / close -------------------------------------------

    function loadField(field, mode) {
      state.mode = mode;
      state.id = mode === 'edit' ? field.id : null;
      state.fieldName = mode === 'edit' ? field.name : '';
      state.keyTouched = mode === 'duplicate';
      optionUid = 0;
      state.options = (field.options || []).map((option) => ({
        uid: ++optionUid,
        label: option.label || option.value || '',
        value: option.value || '',
        valueTouched: Boolean(option.value) && option.value !== (option.label || option.value),
        m365_upn: option.m365_upn || '',
      }));

      labelInput.value = mode === 'duplicate' ? `${field.display_name || field.name} (copy)` : (field.display_name || '');
      keyInput.value = mode === 'duplicate' ? slugify(`${field.name}_copy`) : (field.name || '');
      keyInput.readOnly = mode === 'edit';
      keyInput.classList.toggle('is-readonly', mode === 'edit');
      groupInput.value = field.field_group || '';
      helpInput.value = field.help_text || '';
      orderInput.value = String(field.display_order || 0);
      activeInput.checked = mode !== 'edit' || Boolean(field.is_active);
      activeField.hidden = mode !== 'edit';
      setType(field.field_type || 'text');

      const operator = field.condition_operator || (field.condition_parent_name ? 'is_checked' : '');
      state.condition = {
        enabled: Boolean(field.condition_parent_name),
        parent: field.condition_parent_name || '',
        operator,
        value: operator === 'equals' || operator === 'not_equals' ? (field.condition_value || '') : '',
        values: operator === 'one_of' ? uniqueList(splitList(field.condition_value, ',')) : [],
        map: new Map(),
        fallback: [],
      };
      if (operator === 'select_map') {
        const parsed = parseSelectMap(field.condition_value);
        state.condition.map = parsed.entries;
        state.condition.fallback = parsed.fallback || [];
      }

      const jobTitles = splitList(field.visible_to_job_titles);
      const emails = splitList(field.visible_to_requester_emails);
      tagInputs.jobTitles.set(jobTitles);
      tagInputs.emails.set(emails);
      visibilityModes.forEach((input) => {
        input.checked = (input.value === 'restricted') === Boolean(jobTitles.length || emails.length);
      });
      tagInputs.m365.set(splitList(field.m365_upn, '|'));

      bulkPanel.hidden = true;
      bulkInput.value = '';
      delete form.dataset.submitted;

      if (mode === 'edit') {
        titleEl.textContent = `Edit “${field.display_name || field.name}”`;
        subtitleEl.textContent = `${TYPE_LABELS[field.field_type] || field.field_type} field · key ${field.name}`;
        form.action = `${baseUrl}/${field.id}`;
        deleteButton.hidden = false;
        form.querySelector('[data-scf-submit]').textContent = 'Save changes';
      } else {
        titleEl.textContent = mode === 'duplicate' ? 'Duplicate custom field' : 'Add custom field';
        subtitleEl.textContent = 'Define the question, then optionally add options, conditions and visibility rules.';
        form.action = baseUrl;
        deleteButton.hidden = true;
        form.querySelector('[data-scf-submit]').textContent = 'Create field';
      }
      if (OPTION_TYPES.has(currentType()) && !state.options.length) {
        addOption('');
      }
      showErrors({});
      refresh();
    }

    function serializeState() {
      writeOutputs();
      const payload = new FormData(form);
      payload.delete('_csrf');
      payload.delete('csrf_token');
      return JSON.stringify(Array.from(payload.entries()));
    }

    let lastTrigger = null;

    function openModal(trigger) {
      lastTrigger = trigger || null;
      showTab('details');
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('scf-modal-open');
      const body = modal.querySelector('.modal__body');
      if (body) {
        body.scrollTop = 0;
      }
      state.snapshot = serializeState();
      window.requestAnimationFrame(() => labelInput.focus());
    }

    function closeModal(force) {
      if (!force && serializeState() !== state.snapshot && !window.confirm('Discard your unsaved changes to this field?')) {
        return;
      }
      modal.hidden = true;
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('scf-modal-open');
      if (lastTrigger && typeof lastTrigger.focus === 'function') {
        lastTrigger.focus();
      }
    }

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal(false);
      });
    });

    modal.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = Array.from(modal.querySelectorAll(
        'a[href], button:not([disabled]), textarea:not([disabled]), input:not([type="hidden"]):not([disabled]):not([tabindex="-1"]), select:not([disabled]):not([tabindex="-1"]), [tabindex="0"]',
      )).filter((node) => node.offsetParent !== null);
      if (!focusable.length) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    const emptyField = {
      name: '',
      display_name: '',
      help_text: '',
      field_type: 'text',
      field_group: '',
      display_order: fields.reduce((max, field) => Math.max(max, Number(field.display_order) || 0), -1) + 1,
      is_active: true,
      options: [],
    };

    document.querySelectorAll('[data-scf-create]').forEach((button) => {
      button.addEventListener('click', () => {
        loadField(emptyField, 'create');
        openModal(button);
      });
    });
    document.querySelectorAll('[data-scf-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        const field = fieldsById.get(button.dataset.scfEdit);
        if (field) {
          loadField(field, 'edit');
          openModal(button);
        }
      });
    });
    document.querySelectorAll('[data-scf-duplicate]').forEach((button) => {
      button.addEventListener('click', () => {
        const field = fieldsById.get(button.dataset.scfDuplicate);
        if (field) {
          loadField(field, 'duplicate');
          openModal(button);
        }
      });
    });

    deleteButton.addEventListener('click', () => {
      if (!state.id || !deleteForm) {
        return;
      }
      const name = labelInput.value.trim() || state.fieldName;
      if (!window.confirm(`Delete “${name}”? Answers already saved on staff records for this field will no longer be shown.`)) {
        return;
      }
      deleteForm.action = `${baseUrl}/${state.id}/delete`;
      deleteForm.submit();
    });

    // ----- submit --------------------------------------------------------

    function writeOutputs() {
      const type = currentType();
      const options = OPTION_TYPES.has(type)
        ? state.options
          .filter((option) => optionValue(option))
          .map((option) => ({
            value: optionValue(option),
            label: (option.label || '').trim() || optionValue(option),
            m365_upn: type === 'multiselect'
              ? splitList(option.m365_upn, '|').join('|')
              : '',
          }))
        : [];
      outputs.options_json.value = JSON.stringify(options);
      const condition = serializeCondition();
      outputs.condition_parent_name.value = condition.parent;
      outputs.condition_operator.value = condition.operator;
      outputs.condition_value.value = condition.value;
      const restricted = visibilityRestricted();
      outputs.visible_to_job_titles.value = restricted ? tagInputs.jobTitles.get().join(', ') : '';
      outputs.visible_to_requester_emails.value = restricted ? tagInputs.emails.get().join(', ') : '';
      outputs.m365_upn.value = M365_FIELD_TYPES.has(type) ? tagInputs.m365.get().join('|') : '';
      if (state.mode !== 'edit') {
        keyInput.value = slugify(keyInput.value || labelInput.value);
      }
    }

    form.addEventListener('submit', (event) => {
      form.dataset.submitted = 'true';
      const errors = validate();
      showErrors(errors);
      const failedTabs = tabs.map((tab) => tab.dataset.scfTab).filter((name) => errors[name]);
      if (failedTabs.length) {
        event.preventDefault();
        const target = failedTabs.includes(state.activeTab) ? state.activeTab : failedTabs[0];
        showTab(target);
        const focusTarget = errors[target].focus;
        if (focusTarget && typeof focusTarget.focus === 'function') {
          focusTarget.focus();
        }
        return;
      }
      writeOutputs();
      const submit = form.querySelector('[data-scf-submit]');
      submit.disabled = true;
      submit.textContent = 'Saving…';
    });

    // ----- list search ---------------------------------------------------

    const search = document.querySelector('[data-scf-search]');
    if (search) {
      const items = Array.from(document.querySelectorAll('[data-scf-item]'));
      const groups = Array.from(document.querySelectorAll('[data-scf-group]'));
      const noResults = document.querySelector('[data-scf-no-results]');
      search.addEventListener('input', () => {
        const terms = normalize(search.value).split(/\s+/).filter(Boolean);
        let visible = 0;
        items.forEach((item) => {
          const haystack = normalize(item.dataset.scfSearchText);
          const match = terms.every((term) => haystack.includes(term));
          item.hidden = !match;
          if (match) {
            visible += 1;
          }
        });
        groups.forEach((group) => {
          group.hidden = !group.querySelector('[data-scf-item]:not([hidden])');
        });
        if (noResults) {
          noResults.hidden = visible > 0;
        }
      });
    }
  });
})();
