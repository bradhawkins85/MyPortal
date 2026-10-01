(function () {
  'use strict';

  const OPERATOR_LABELS = {
    equals: 'is',
    not_equals: 'is not',
    contains: 'contains',
  };

  const BOOLEAN_VALUES = ['Yes', 'No'];

  function normalize(value) {
    return String(value == null ? '' : value).trim().toLowerCase();
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
      console.error('Unable to parse ticket question data', error);
      return null;
    }
  }

  function sanitizeBaseUrl(value) {
    try {
      const raw = String(value == null ? '' : value).trim();
      const resolved = new URL(raw || '/', window.location.origin);
      const protocolOk = resolved.protocol === 'http:' || resolved.protocol === 'https:';
      if (!protocolOk || resolved.origin !== window.location.origin) {
        return '/';
      }
      return `${resolved.pathname}${resolved.search}${resolved.hash}`;
    } catch (error) {
      return '/';
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const data = parseJson('tq-editor-data');
    const modal = document.getElementById('tq-modal');
    if (!data || !modal) {
      return;
    }

    const form = modal.querySelector('[data-tq-form]');
    const baseUrl = sanitizeBaseUrl(data.baseUrl);
    const fixedCompanyId = data.companyId == null ? null : Number(data.companyId);
    const questionsById = new Map((data.questions || []).map((q) => [String(q.id), q]));
    const parents = data.parents || [];
    const parentsById = new Map(parents.map((q) => [String(q.id), q]));

    const title = modal.querySelector('[data-tq-title]');
    const subtitle = modal.querySelector('[data-tq-subtitle]');
    const tabs = Array.from(modal.querySelectorAll('[data-tq-tab]'));
    const panels = Array.from(modal.querySelectorAll('[data-tq-panel]'));
    const labelInput = form.querySelector('[data-tq-input="label"]');
    const placeholderInput = form.querySelector('[data-tq-input="placeholder"]');
    const typeInputs = Array.from(form.querySelectorAll('[data-tq-input="field_type"]'));
    const scopeInputs = Array.from(form.querySelectorAll('[data-tq-input="scope"]'));
    const companySelect = form.querySelector('[data-tq-input="company_id"]');
    const companyField = form.querySelector('[data-tq-company-field]');
    const scopeLocked = form.querySelector('[data-tq-scope-locked]');
    const requiredInput = form.querySelector('[data-tq-input="is_required"]');
    const activeInput = form.querySelector('[data-tq-input="is_active"]');
    const orderInput = form.querySelector('[data-tq-input="sort_order"]');
    const optionsList = form.querySelector('[data-tq-options]');
    const optionsCount = form.querySelector('[data-tq-options-count]');
    const conditionsCount = form.querySelector('[data-tq-conditions-count]');
    const bulk = form.querySelector('[data-tq-bulk]');
    const bulkInput = form.querySelector('[data-tq-bulk-input]');
    const bulkToggles = Array.from(form.querySelectorAll('[data-tq-bulk-toggle]'));
    const conditionsList = form.querySelector('[data-tq-conditions]');
    const conditionAdd = form.querySelector('[data-tq-condition-add]');
    const noParents = form.querySelector('[data-tq-no-parents]');
    const optionsOutput = form.querySelector('[data-tq-out="options_text"]');
    const conditionOutputs = form.querySelector('[data-tq-condition-outputs]');
    const preview = modal.querySelector('[data-tq-preview]');
    const previewRules = modal.querySelector('[data-tq-preview-rules]');
    const submitButton = modal.querySelector('[data-tq-submit]');
    const deleteButton = modal.querySelector('[data-tq-delete]');
    const deleteForm = document.querySelector('[data-tq-delete-form]');
    const errorNodes = {};
    form.querySelectorAll('[data-tq-error]').forEach((node) => {
      errorNodes[node.dataset.tqError] = node;
    });

    const state = {
      mode: 'create',
      id: null,
      options: [],
      conditions: [],
      activeTab: 'details',
      snapshot: '',
    };
    let uid = 0;
    let lastTrigger = null;

    // ----- helpers -------------------------------------------------------

    function currentType() {
      const checked = typeInputs.find((input) => input.checked);
      return checked ? checked.value : 'text';
    }

    function currentScope() {
      if (fixedCompanyId !== null) {
        return 'company';
      }
      const checked = scopeInputs.find((input) => input.checked);
      return checked ? checked.value : 'global';
    }

    function currentCompanyId() {
      if (fixedCompanyId !== null) {
        return fixedCompanyId;
      }
      return companySelect && companySelect.value ? Number(companySelect.value) : null;
    }

    function companyName(id) {
      if (!companySelect || id == null) {
        return '';
      }
      const option = Array.from(companySelect.options).find((item) => Number(item.value) === Number(id));
      return option ? option.textContent.trim() : '';
    }

    function parentCandidates() {
      const scope = currentScope();
      const companyId = currentCompanyId();
      return parents.filter((parent) => {
        if (state.mode === 'edit' && String(parent.id) === String(state.id)) {
          return false;
        }
        if (parent.scope === 'global') {
          return true;
        }
        return scope === 'company' && Number(parent.company_id) === Number(companyId);
      });
    }

    function operatorsFor(parent) {
      if (parent && (parent.field_type === 'select' || parent.field_type === 'boolean')) {
        return ['equals', 'not_equals'];
      }
      return ['equals', 'not_equals', 'contains'];
    }

    function valuesFor(parent) {
      if (!parent) {
        return null;
      }
      if (parent.field_type === 'boolean') {
        return BOOLEAN_VALUES;
      }
      if (parent.field_type === 'select' && (parent.options || []).length) {
        return parent.options.map(String);
      }
      return null;
    }

    function cleanOptions() {
      return state.options.map((option) => option.text.trim()).filter(Boolean);
    }

    function serializeState() {
      return JSON.stringify({
        label: labelInput.value,
        placeholder: placeholderInput.value,
        type: currentType(),
        scope: currentScope(),
        company: currentCompanyId(),
        required: requiredInput.checked,
        active: activeInput.checked,
        order: orderInput.value,
        options: cleanOptions(),
        conditions: state.conditions.map((c) => [c.parent, c.operator, c.value]),
      });
    }

    // ----- tabs ----------------------------------------------------------

    function tabAvailable(name) {
      return name !== 'options' || currentType() === 'select';
    }

    function showTab(name, focus) {
      if (!tabAvailable(name)) {
        name = 'details';
      }
      state.activeTab = name;
      tabs.forEach((tab) => {
        const active = tab.dataset.tqTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
        if (active && focus) {
          tab.focus();
        }
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.tqPanel !== name;
      });
    }

    tabs.forEach((tab) => {
      tab.addEventListener('click', () => showTab(tab.dataset.tqTab));
      tab.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
          return;
        }
        event.preventDefault();
        const visible = tabs.filter((item) => !item.hidden);
        const index = visible.indexOf(tab);
        const next = visible[(index + (event.key === 'ArrowRight' ? 1 : -1) + visible.length) % visible.length];
        showTab(next.dataset.tqTab, true);
      });
    });

    // ----- options -------------------------------------------------------

    function addOption(text) {
      const option = { uid: ++uid, text: text || '' };
      state.options.push(option);
      return option;
    }

    function renderOptions() {
      optionsList.textContent = '';
      state.options.forEach((option, index) => {
        optionsList.append(el('li', { className: 'scf-option tq-option', dataset: { uid: String(option.uid) } }, [
          el('span', { className: 'scf-option__move' }, [
            el('button', {
              type: 'button',
              className: 'scf-icon-button',
              'aria-label': 'Move option up',
              title: 'Move up',
              disabled: index === 0,
              dataset: { tqOptionMove: '-1' },
              text: '↑',
            }),
            el('button', {
              type: 'button',
              className: 'scf-icon-button',
              'aria-label': 'Move option down',
              title: 'Move down',
              disabled: index === state.options.length - 1,
              dataset: { tqOptionMove: '1' },
              text: '↓',
            }),
          ]),
          el('input', {
            className: 'form-input',
            value: option.text,
            maxlength: '255',
            placeholder: `Option ${index + 1}`,
            'aria-label': `Option ${index + 1}`,
            dataset: { tqOptionField: '' },
          }),
          el('button', {
            type: 'button',
            className: 'scf-icon-button scf-icon-button--danger',
            'aria-label': `Remove option ${option.text || index + 1}`,
            title: 'Remove option',
            dataset: { tqOptionRemove: '' },
            text: '×',
          }),
        ]));
      });
      if (!state.options.length) {
        optionsList.append(el('li', { className: 'scf-options__empty text-muted', text: 'No options yet. Add at least one.' }));
      }
    }

    function focusOption(optionUid) {
      const input = optionsList.querySelector(`[data-uid="${optionUid}"] [data-tq-option-field]`);
      if (input) {
        input.focus();
      }
    }

    function optionIndex(node) {
      const row = node.closest('.scf-option');
      return row ? state.options.findIndex((option) => String(option.uid) === row.dataset.uid) : -1;
    }

    optionsList.addEventListener('input', (event) => {
      if (!event.target.matches('[data-tq-option-field]')) {
        return;
      }
      const option = state.options[optionIndex(event.target)];
      if (option) {
        option.text = event.target.value;
        refresh({ keepOptions: true });
      }
    });

    optionsList.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || !event.target.matches('[data-tq-option-field]')) {
        return;
      }
      event.preventDefault();
      const index = optionIndex(event.target);
      const option = { uid: ++uid, text: '' };
      state.options.splice(index + 1, 0, option);
      refresh();
      focusOption(option.uid);
    });

    optionsList.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button) {
        return;
      }
      const index = optionIndex(button);
      if (index < 0) {
        return;
      }
      if (button.hasAttribute('data-tq-option-remove')) {
        state.options.splice(index, 1);
        refresh();
        const next = state.options[Math.min(index, state.options.length - 1)];
        if (next) {
          focusOption(next.uid);
        }
        return;
      }
      const offset = Number(button.dataset.tqOptionMove || 0);
      const target = index + offset;
      if (offset && target >= 0 && target < state.options.length) {
        const [moved] = state.options.splice(index, 1);
        state.options.splice(target, 0, moved);
        refresh();
        const again = optionsList.querySelector(`[data-uid="${moved.uid}"] [data-tq-option-move="${offset}"]`);
        if (again && !again.disabled) {
          again.focus();
        }
      }
    });

    form.querySelector('[data-tq-option-add]').addEventListener('click', () => {
      const option = addOption('');
      refresh();
      focusOption(option.uid);
    });

    function setBulkOpen(open) {
      bulk.hidden = !open;
      bulkToggles.forEach((button) => {
        if (button.hasAttribute('aria-expanded')) {
          button.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
      });
      if (open) {
        bulkInput.focus();
      }
    }

    bulkToggles.forEach((button) => button.addEventListener('click', () => setBulkOpen(bulk.hidden)));

    form.querySelector('[data-tq-bulk-apply]').addEventListener('click', () => {
      state.options = state.options.filter((option) => option.text.trim());
      const existing = new Set(state.options.map((option) => normalize(option.text)));
      bulkInput.value.split(/\r?\n/).forEach((line) => {
        const text = line.trim();
        if (text && !existing.has(normalize(text))) {
          existing.add(normalize(text));
          addOption(text);
        }
      });
      bulkInput.value = '';
      setBulkOpen(false);
      refresh();
    });

    // ----- conditions ----------------------------------------------------

    function renderConditions() {
      const candidates = parentCandidates();
      conditionsList.textContent = '';
      state.conditions.forEach((condition, index) => {
        const parent = parentsById.get(String(condition.parent));
        const parentSelect = el('select', {
          className: 'form-input',
          'aria-label': `Rule ${index + 1} question`,
          dataset: { tqConditionField: 'parent' },
        }, [el('option', { value: '', text: 'Choose a question…' })]);
        candidates.forEach((candidate) => {
          parentSelect.append(el('option', {
            value: String(candidate.id),
            text: candidate.label,
            selected: String(candidate.id) === String(condition.parent),
          }));
        });
        if (condition.parent && !candidates.some((c) => String(c.id) === String(condition.parent))) {
          parentSelect.append(el('option', {
            value: String(condition.parent),
            text: parent ? `${parent.label} (not asked here)` : 'Deleted question',
            selected: true,
          }));
        }

        const operators = operatorsFor(parent);
        if (!operators.includes(condition.operator)) {
          operators.push(condition.operator);
        }
        const operatorSelect = el('select', {
          className: 'form-input',
          'aria-label': `Rule ${index + 1} comparison`,
          dataset: { tqConditionField: 'operator' },
        }, operators.map((operator) => el('option', {
          value: operator,
          text: OPERATOR_LABELS[operator] || operator,
          selected: operator === condition.operator,
        })));

        const choices = valuesFor(parent);
        let valueField;
        if (choices) {
          const list = choices.slice();
          if (condition.value && !list.some((choice) => normalize(choice) === normalize(condition.value))) {
            list.push(condition.value);
          }
          valueField = el('select', {
            className: 'form-input',
            'aria-label': `Rule ${index + 1} answer`,
            dataset: { tqConditionField: 'value' },
          }, [el('option', { value: '', text: 'Choose an answer…' })].concat(list.map((choice) => el('option', {
            value: choice,
            text: choice,
            selected: normalize(choice) === normalize(condition.value),
          }))));
        } else {
          valueField = el('input', {
            className: 'form-input',
            value: condition.value,
            maxlength: '255',
            placeholder: 'Answer',
            'aria-label': `Rule ${index + 1} answer`,
            dataset: { tqConditionField: 'value' },
          });
        }

        conditionsList.append(el('li', { className: 'tq-condition', dataset: { index: String(index) } }, [
          el('span', { className: 'scf-condition__word', text: index === 0 ? 'Ask when' : 'and' }),
          parentSelect,
          operatorSelect,
          valueField,
          el('button', {
            type: 'button',
            className: 'scf-icon-button scf-icon-button--danger',
            'aria-label': `Remove rule ${index + 1}`,
            title: 'Remove rule',
            dataset: { tqConditionRemove: '' },
            text: '×',
          }),
        ]));
      });
      if (!state.conditions.length) {
        conditionsList.append(el('li', { className: 'scf-options__empty text-muted', text: 'Always asked. Add a rule to ask it only after a matching answer.' }));
      }
      noParents.hidden = candidates.length > 0;
      conditionAdd.disabled = candidates.length === 0;
    }

    function conditionAt(node) {
      const row = node.closest('.tq-condition');
      return row ? { index: Number(row.dataset.index), condition: state.conditions[Number(row.dataset.index)] } : null;
    }

    conditionsList.addEventListener('change', (event) => {
      const field = event.target.dataset.tqConditionField;
      const found = field && conditionAt(event.target);
      if (!found) {
        return;
      }
      found.condition[field] = event.target.value;
      if (field === 'parent') {
        const parent = parentsById.get(String(found.condition.parent));
        if (!operatorsFor(parent).includes(found.condition.operator)) {
          found.condition.operator = 'equals';
        }
        const choices = valuesFor(parent);
        if (choices && !choices.some((choice) => normalize(choice) === normalize(found.condition.value))) {
          found.condition.value = '';
        }
        refresh();
        const next = conditionsList.querySelector(`[data-index="${found.index}"] [data-tq-condition-field="value"]`);
        if (next) {
          next.focus();
        }
        return;
      }
      refresh({ keepConditions: true });
    });

    conditionsList.addEventListener('input', (event) => {
      if (event.target.dataset.tqConditionField !== 'value' || event.target.tagName !== 'INPUT') {
        return;
      }
      const found = conditionAt(event.target);
      if (found) {
        found.condition.value = event.target.value;
        refresh({ keepConditions: true });
      }
    });

    conditionsList.addEventListener('click', (event) => {
      const button = event.target.closest('[data-tq-condition-remove]');
      const found = button && conditionAt(button);
      if (!found) {
        return;
      }
      state.conditions.splice(found.index, 1);
      refresh();
      const next = conditionsList.querySelector('[data-tq-condition-field="parent"]') || conditionAdd;
      next.focus();
    });

    conditionAdd.addEventListener('click', () => {
      state.conditions.push({ parent: '', operator: 'equals', value: '' });
      refresh();
      const selects = conditionsList.querySelectorAll('[data-tq-condition-field="parent"]');
      if (selects.length) {
        selects[selects.length - 1].focus();
      }
    });

    function describeCondition(condition) {
      const parent = parentsById.get(String(condition.parent));
      const name = parent ? `“${parent.label}”` : 'another question';
      return `${name} ${OPERATOR_LABELS[condition.operator] || condition.operator} “${condition.value}”`;
    }

    // ----- validation ----------------------------------------------------

    function validate() {
      const errors = {};
      if (!labelInput.value.trim()) {
        errors.details = { message: 'Enter the question people will be asked.', focus: labelInput };
      } else if (fixedCompanyId === null && currentScope() === 'company' && !currentCompanyId() && state.mode !== 'edit') {
        errors.details = { message: 'Choose which company is asked this question.', focus: companySelect };
      }
      if (currentType() === 'select') {
        const values = cleanOptions();
        const seen = new Set();
        const duplicate = values.find((value) => {
          const key = normalize(value);
          const repeat = seen.has(key);
          seen.add(key);
          return repeat;
        });
        if (!values.length) {
          errors.options = { message: 'Add at least one choice so people have something to pick.', focus: form.querySelector('[data-tq-option-add]') };
        } else if (duplicate) {
          errors.options = { message: `“${duplicate}” is listed more than once.`, focus: optionsList.querySelector('[data-tq-option-field]') };
        }
      }
      const incomplete = state.conditions.findIndex((c) => !c.parent || !String(c.value).trim());
      if (incomplete >= 0) {
        errors.logic = {
          message: 'Choose a question and an answer for every rule, or remove the rule.',
          focus: conditionsList.querySelector(`[data-index="${incomplete}"] [data-tq-condition-field="${state.conditions[incomplete].parent ? 'value' : 'parent'}"]`),
        };
      }
      return errors;
    }

    function showErrors(errors) {
      Object.entries(errorNodes).forEach(([name, node]) => {
        const error = errors[name];
        node.textContent = error ? error.message : '';
        node.hidden = !error;
      });
      tabs.forEach((tab) => tab.classList.toggle('has-error', Boolean(errors[tab.dataset.tqTab])));
      labelInput.classList.toggle('is-invalid', Boolean(errors.details && errors.details.focus === labelInput));
    }

    // ----- preview -------------------------------------------------------

    function renderPreview() {
      const type = currentType();
      const label = labelInput.value.trim() || 'Your question';
      const placeholder = placeholderInput.value.trim();
      preview.textContent = '';
      preview.append(el('span', { className: 'form-label' }, [
        label,
        requiredInput.checked ? el('span', { className: 'scf-required', 'aria-hidden': 'true', text: ' *' }) : null,
      ]));
      if (type === 'select') {
        preview.append(el('select', { className: 'form-input', tabindex: '-1' }, [
          el('option', { text: placeholder || 'Select…' }),
          ...cleanOptions().map((text) => el('option', { text })),
        ]));
      } else if (type === 'boolean') {
        preview.append(el('div', { className: 'scf-preview__multi tq-preview__yesno' }, BOOLEAN_VALUES.map((text) => (
          el('label', { className: 'scf-preview__checkbox' }, [el('input', { type: 'radio', tabindex: '-1', name: 'tq-preview-yesno' }), text])
        ))));
      } else {
        preview.append(el('input', { className: 'form-input', tabindex: '-1', placeholder }));
      }
      if (!activeInput.checked) {
        preview.append(el('p', { className: 'scf-preview__inactive', text: 'Inactive — not asked until reactivated.' }));
      }

      previewRules.textContent = '';
      const rules = [];
      if (fixedCompanyId === null) {
        const scope = currentScope();
        const name = companyName(currentCompanyId());
        rules.push(['visibility', scope === 'global' ? 'Asked on every company\'s devices.' : `Asked only on ${name || 'one company'}'s devices.`]);
      }
      rules.push(['', requiredInput.checked ? 'Must be answered before the ticket can be sent.' : 'Can be skipped.']);
      const complete = state.conditions.filter((c) => c.parent && String(c.value).trim());
      if (complete.length) {
        rules.push(['logic', `Asked only when ${complete.map(describeCondition).join(' and ')}.`]);
      }
      if (type === 'select') {
        const count = cleanOptions().length;
        rules.push(['m365', `${count} choice${count === 1 ? '' : 's'}.`]);
      }
      rules.forEach(([variant, text]) => {
        previewRules.append(el('li', { className: `scf-preview__rule${variant ? ` scf-preview__rule--${variant}` : ''}`, text }));
      });
    }

    // ----- refresh -------------------------------------------------------

    function refresh(opts) {
      const settings = opts || {};
      const type = currentType();
      typeInputs.forEach((input) => input.closest('.scf-type-card').classList.toggle('is-selected', input.checked));
      scopeInputs.forEach((input) => input.closest('.scf-choice').classList.toggle('is-selected', input.checked));
      if (companyField) {
        companyField.hidden = currentScope() !== 'company';
      }
      const optionsTab = tabs.find((tab) => tab.dataset.tqTab === 'options');
      optionsTab.hidden = type !== 'select';
      if (!tabAvailable(state.activeTab)) {
        showTab('details');
      }
      if (!settings.keepOptions) {
        renderOptions();
      }
      if (!settings.keepConditions) {
        renderConditions();
      }
      const optionTotal = cleanOptions().length;
      optionsCount.textContent = optionTotal ? String(optionTotal) : '';
      conditionsCount.textContent = state.conditions.length ? String(state.conditions.length) : '';
      tabs.forEach((tab) => {
        const name = tab.dataset.tqTab;
        const configured = (name === 'options' && optionTotal > 0) || (name === 'logic' && state.conditions.length > 0);
        tab.classList.toggle('is-configured', configured);
      });
      if (form.dataset.submitted === 'true') {
        showErrors(validate());
      }
      renderPreview();
    }

    [labelInput, placeholderInput, orderInput].forEach((input) => input.addEventListener('input', () => refresh({ keepOptions: true, keepConditions: true })));
    [requiredInput, activeInput].forEach((input) => input.addEventListener('change', () => refresh({ keepOptions: true, keepConditions: true })));
    typeInputs.forEach((input) => input.addEventListener('change', () => {
      if (currentType() === 'select' && !state.options.length) {
        addOption('');
      }
      refresh();
    }));
    scopeInputs.forEach((input) => input.addEventListener('change', () => refresh()));
    if (companySelect) {
      companySelect.addEventListener('change', () => refresh());
    }

    // ----- load / open / close ------------------------------------------

    function loadQuestion(question, mode) {
      state.mode = mode;
      state.id = mode === 'edit' ? question.id : null;
      state.options = [];
      (question.options || []).forEach((option) => addOption(String(option)));
      state.conditions = (question.conditions || []).map((condition) => ({
        parent: String(condition.parent_question_id || ''),
        operator: condition.operator || 'equals',
        value: condition.expected_value == null ? '' : String(condition.expected_value),
      }));
      labelInput.value = mode === 'duplicate' ? `${question.label} (copy)` : (question.label || '');
      placeholderInput.value = question.placeholder || '';
      typeInputs.forEach((input) => {
        input.checked = input.value === (question.field_type || 'text');
      });
      requiredInput.checked = Boolean(question.is_required);
      activeInput.checked = question.is_active !== false;
      orderInput.value = String(question.sort_order || 0);
      if (fixedCompanyId === null) {
        scopeInputs.forEach((input) => {
          input.checked = input.value === (question.scope || 'global');
          input.disabled = mode === 'edit';
        });
        companySelect.value = question.company_id ? String(question.company_id) : '';
        companySelect.disabled = mode === 'edit';
        scopeLocked.hidden = mode !== 'edit';
      }
      title.textContent = mode === 'edit' ? 'Edit question' : (mode === 'duplicate' ? 'Duplicate question' : 'Add question');
      subtitle.textContent = mode === 'edit'
        ? 'Changes apply to the next ticket submitted from the tray.'
        : 'Asked when someone submits a ticket from the tray icon.';
      deleteButton.hidden = mode !== 'edit';
      form.dataset.submitted = 'false';
      submitButton.disabled = false;
      submitButton.textContent = mode === 'edit' ? 'Save question' : 'Add question';
      setBulkOpen(false);
      showErrors({});
      showTab('details');
      refresh();
    }

    function openModal(trigger) {
      lastTrigger = trigger || null;
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
      if (!force && serializeState() !== state.snapshot && !window.confirm('Discard your unsaved changes to this question?')) {
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
        'button:not([disabled]), textarea:not([disabled]), input:not([type="hidden"]):not([disabled]):not([tabindex="-1"]), select:not([disabled]):not([tabindex="-1"]), [tabindex="0"]',
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

    const nextOrder = (data.questions || []).reduce((max, q) => Math.max(max, Number(q.sort_order) || 0), -10) + 10;
    const emptyQuestion = {
      scope: fixedCompanyId === null ? 'global' : 'company',
      company_id: fixedCompanyId,
      label: '',
      placeholder: '',
      field_type: 'text',
      is_required: false,
      is_active: true,
      sort_order: Math.max(nextOrder, 0),
      options: [],
      conditions: [],
    };

    document.querySelectorAll('[data-tq-create]').forEach((trigger) => {
      trigger.addEventListener('click', (event) => {
        event.preventDefault();
        loadQuestion(emptyQuestion, 'create');
        openModal(trigger);
      });
    });
    document.querySelectorAll('[data-tq-edit]').forEach((trigger) => {
      trigger.addEventListener('click', (event) => {
        const question = questionsById.get(trigger.dataset.tqEdit);
        if (!question) {
          return;
        }
        event.preventDefault();
        loadQuestion(question, 'edit');
        openModal(trigger);
      });
    });
    document.querySelectorAll('[data-tq-duplicate]').forEach((trigger) => {
      trigger.hidden = false;
      trigger.addEventListener('click', () => {
        const question = questionsById.get(trigger.dataset.tqDuplicate);
        if (question) {
          loadQuestion(question, 'duplicate');
          openModal(trigger);
        }
      });
    });

    deleteButton.addEventListener('click', () => {
      if (state.mode !== 'edit' || !deleteForm) {
        return;
      }
      const name = labelInput.value.trim() || 'this question';
      if (!window.confirm(`Delete “${name}”? It will no longer be asked, and rules that depend on it will stop matching.`)) {
        return;
      }
      deleteForm.action = `${baseUrl}/${state.id}/delete`;
      deleteForm.submit();
    });

    // ----- submit --------------------------------------------------------

    form.addEventListener('submit', (event) => {
      form.dataset.submitted = 'true';
      const errors = validate();
      showErrors(errors);
      const failed = tabs.map((tab) => tab.dataset.tqTab).filter((name) => errors[name]);
      if (failed.length) {
        event.preventDefault();
        const target = failed.includes(state.activeTab) ? state.activeTab : failed[0];
        showTab(target);
        if (errors[target].focus) {
          errors[target].focus.focus();
        }
        return;
      }
      form.action = state.mode === 'edit' ? `${baseUrl}/${state.id}/edit` : `${baseUrl}/new`;
      optionsOutput.value = currentType() === 'select' ? cleanOptions().join('\n') : '';
      conditionOutputs.textContent = '';
      state.conditions.forEach((condition) => {
        conditionOutputs.append(
          el('input', { type: 'hidden', name: 'cond_parent_id[]', value: condition.parent }),
          el('input', { type: 'hidden', name: 'cond_operator[]', value: condition.operator }),
          el('input', { type: 'hidden', name: 'cond_expected[]', value: String(condition.value).trim() }),
        );
      });
      submitButton.disabled = true;
      submitButton.textContent = 'Saving…';
    });

    // ----- list search ---------------------------------------------------

    const search = document.querySelector('[data-tq-search]');
    if (search) {
      const items = Array.from(document.querySelectorAll('[data-tq-item]'));
      const groups = Array.from(document.querySelectorAll('[data-tq-group]'));
      const noResults = document.querySelector('[data-tq-no-results]');
      search.addEventListener('input', () => {
        const terms = normalize(search.value).split(/\s+/).filter(Boolean);
        let visible = 0;
        items.forEach((item) => {
          const haystack = normalize(item.dataset.tqSearchText);
          const match = terms.every((term) => haystack.includes(term));
          item.hidden = !match;
          visible += match ? 1 : 0;
        });
        groups.forEach((group) => {
          group.hidden = !group.querySelector('[data-tq-item]:not([hidden])');
        });
        if (noResults) {
          noResults.hidden = visible > 0;
        }
      });
    }
  });
})();
