(function () {
  'use strict';

  const OPERATOR_LABELS = {
    equals: 'is', not_equals: 'is not', in: 'is one of', not_in: 'is not one of',
    contains: 'contains', not_contains: 'does not contain', starts_with: 'starts with',
    is_set: 'is set', is_not_set: 'is not set',
  };
  const NO_VALUE = new Set(['is_set', 'is_not_set']);
  const MULTI_VALUE = new Set(['in', 'not_in']);
  const PREVIEW_LIMIT = 8;

  function element(tag, attrs, text) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? '' : String(value));
    });
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function readData() {
    const node = document.getElementById('mpol-data');
    try {
      return node ? JSON.parse(node.textContent) : {};
    } catch (error) {
      return {};
    }
  }

  function setupSearch(root) {
    const input = root.querySelector('[data-mpol-search]');
    const empty = root.querySelector('[data-mpol-no-results]');
    if (!input) return;
    input.addEventListener('input', () => {
      const needle = input.value.trim().toLowerCase();
      let shown = 0;
      root.querySelectorAll('[data-mpol-item]').forEach((item) => {
        const match = !needle || (item.dataset.mpolSearchText || '').toLowerCase().includes(needle);
        item.hidden = !match;
        if (match) shown += 1;
      });
      if (empty) empty.hidden = shown > 0;
    });
  }

  function setupResultsToolbar() {
    const select = document.getElementById('mpol-results-table-filter-status');
    if (select && select.form) select.addEventListener('change', () => select.form.submit());
  }

  function setupEditor(data) {
    const modal = document.getElementById('mpol-modal');
    if (!modal) return;
    const form = modal.querySelector('[data-mpol-form]');
    const deleteForm = modal.querySelector('[data-mpol-delete-form]');
    const deleteButton = modal.querySelector('[data-mpol-delete]');
    const title = modal.querySelector('[data-mpol-title]');
    const nameInput = form.querySelector('[data-mpol-input="name"]');
    const descriptionInput = form.querySelector('[data-mpol-input="description"]');
    const enabledInput = form.querySelector('[data-mpol-input="enabled"]');
    const matchSelect = form.querySelector('[data-mpol-match]');
    const conditionList = form.querySelector('[data-mpol-conditions]');
    const conditionsEmpty = form.querySelector('[data-mpol-conditions-empty]');
    const nestedNotice = form.querySelector('[data-mpol-nested]');
    const previewCount = modal.querySelector('[data-mpol-preview-count]');
    const previewTenants = modal.querySelector('[data-mpol-preview-tenants]');
    const summaryTargets = modal.querySelector('[data-mpol-summary-targets]');
    const summaryStandards = modal.querySelector('[data-mpol-summary-standards]');
    const fields = data.fields || [];
    const tenants = new Map((data.tenants || []).map((tenant) => [String(tenant.company_id), tenant.company_name]));
    const policies = new Map((data.policies || []).map((policy) => [String(policy.id), policy]));
    const standardSearch = modal.querySelector('[data-mpol-standard-search]');
    const standardSelectedOnly = modal.querySelector('[data-mpol-standard-selected]');
    const standardEmpty = modal.querySelector('[data-mpol-standard-empty]');
    const standardNames = new Map((data.standards || []).map((standard) => [standard.id, standard.name]));
    const state = { policyId: null, conditions: [], overrides: [], nestedRules: null, snapshot: '', lastTrigger: null };
    let previewTimer = null;
    let previewRequest = 0;

    function field(key) { return fields.find((item) => item.key === key); }

    function conditionNode(condition) {
      const node = { field: condition.field, operator: condition.operator };
      if (field(condition.field) && field(condition.field).keyed) node.key = condition.key.trim();
      if (!NO_VALUE.has(condition.operator)) {
        node.value = MULTI_VALUE.has(condition.operator)
          ? condition.value.split(/[\n,]/).map((value) => value.trim()).filter(Boolean)
          : condition.value.trim();
      }
      return node;
    }

    function rules() {
      if (state.nestedRules) return state.nestedRules;
      if (!state.conditions.length) return null;
      return { operator: matchSelect.value, children: state.conditions.map(conditionNode) };
    }

    function describe(node) {
      if (!node) return 'Every connected tenant';
      function text(item, nested) {
        if (Array.isArray(item.children)) {
          const parts = item.children.map((child) => text(child, true)).filter(Boolean);
          const joined = parts.join(item.operator === 'or' ? ' or ' : ' and ');
          return nested && parts.length > 1 ? '(' + joined + ')' : joined;
        }
        if (!field(item.field)) return '';
        const meta = field(item.field);
        let label = (meta ? meta.label : item.field) + (item.key ? ' ' + item.key : '');
        label += ' ' + (OPERATOR_LABELS[item.operator] || item.operator);
        if (Array.isArray(item.value)) label += ' ' + item.value.join(', ');
        else if (item.value !== undefined) label += ' “' + item.value + '”';
        return label;
      }
      const sentence = text(node, false);
      return sentence ? 'Tenants where ' + sentence : 'Every connected tenant';
    }

    function selectedStandards() {
      return Array.from(form.querySelectorAll('[data-mpol-standard-input]:checked')).map((input) => {
        const mode = form.querySelector('input[name="mode_' + input.value + '"]:checked');
        return { standard_id: input.value, mode: mode ? mode.value : 'report' };
      });
    }

    function serialize() {
      return JSON.stringify({
        name: nameInput.value, description: descriptionInput.value, enabled: enabledInput.checked,
        rules: rules(), standards: selectedStandards(), overrides: state.overrides,
      });
    }

    function showTab(name) {
      form.querySelectorAll('[data-mpol-tab]').forEach((tab) => {
        const active = tab.dataset.mpolTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
      });
      form.querySelectorAll('[data-mpol-panel]').forEach((panel) => { panel.hidden = panel.dataset.mpolPanel !== name; });
    }

    function setCount(name, count) {
      const badge = form.querySelector('[data-mpol-count="' + name + '"]');
      const tab = form.querySelector('[data-mpol-tab="' + name + '"]');
      if (badge) badge.textContent = count ? String(count) : '';
      if (tab) tab.classList.toggle('has-settings', count > 0);
    }

    function refreshSummary() {
      const chosen = selectedStandards();
      setCount('standards', chosen.length);
      setCount('targets', state.nestedRules ? 1 : state.conditions.length);
      setCount('exceptions', state.overrides.length);
      form.querySelectorAll('[data-mpol-standard]').forEach((item) => {
        const checked = item.querySelector('[data-mpol-standard-input]').checked;
        item.classList.toggle('is-selected', checked);
        item.querySelectorAll('[data-mpol-mode] input').forEach((radio) => {
          radio.disabled = !checked || radio.closest('.is-disabled') !== null;
        });
      });
      summaryTargets.textContent = describe(rules());
      const listed = chosen.slice(0, 5).map((item) => standardNames.get(item.standard_id) + ' (' + item.mode + ')');
      if (chosen.length > listed.length) listed.push('and ' + (chosen.length - listed.length) + ' more');
      summaryStandards.textContent = chosen.length ? listed.join(', ') : 'No standards chosen yet.';
      conditionsEmpty.hidden = state.conditions.length > 0 || Boolean(state.nestedRules);
    }

    function filterStandards() {
      if (!standardSearch) return;
      const terms = standardSearch.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      const selectedOnly = standardSelectedOnly.checked;
      let shown = 0;
      form.querySelectorAll('[data-mpol-standard-group]').forEach((group) => {
        let groupShown = 0;
        group.querySelectorAll('[data-mpol-standard]').forEach((item) => {
          const text = item.dataset.mpolStandardText || '';
          const checked = item.querySelector('[data-mpol-standard-input]').checked;
          const visible = terms.every((term) => text.includes(term)) && (!selectedOnly || checked);
          item.hidden = !visible;
          if (visible) groupShown += 1;
        });
        group.hidden = groupShown === 0;
        shown += groupShown;
      });
      standardEmpty.hidden = shown > 0;
    }

    function renderConditions() {
      conditionList.replaceChildren();
      state.conditions.forEach((condition, index) => {
        const id = 'mpol-condition-' + index;
        const row = element('li', { class: 'mpol-condition' });
        const fieldSelect = element('select', { class: 'form-input', id: id + '-field', 'aria-label': 'Condition ' + (index + 1) + ' field' });
        fieldSelect.append(element('option', { value: '' }, 'Choose…'));
        fields.forEach((item) => fieldSelect.append(element('option', { value: item.key }, item.label)));
        fieldSelect.value = condition.field;
        row.append(fieldSelect);
        const meta = field(condition.field);
        if (meta && meta.keyed) {
          const key = element('input', { class: 'form-input', type: 'text', placeholder: 'Variable name', 'aria-label': 'Variable name', list: 'mpol-suggest-company-variable', autocomplete: 'off' });
          key.value = condition.key;
          key.addEventListener('input', () => { condition.key = key.value; changed(); });
          row.append(key);
        }
        const operator = element('select', { class: 'form-input', 'aria-label': 'Condition ' + (index + 1) + ' operator' });
        ((meta && meta.operators) || Object.keys(OPERATOR_LABELS)).forEach((key) => operator.append(element('option', { value: key }, OPERATOR_LABELS[key] || key)));
        operator.value = condition.operator;
        row.append(operator);
        if (!NO_VALUE.has(condition.operator)) {
          const suggestions = meta && !meta.keyed ? 'mpol-suggest-' + meta.key.replace('.', '-') : null;
          const value = element('input', {
            class: 'form-input', type: 'text', autocomplete: 'off', 'aria-label': 'Condition ' + (index + 1) + ' value',
            placeholder: MULTI_VALUE.has(condition.operator) ? 'Values, separated by commas' : 'Value',
            list: suggestions && document.getElementById(suggestions) ? suggestions : null,
          });
          value.value = condition.value;
          value.addEventListener('input', () => { condition.value = value.value; changed(); });
          row.append(value);
        }
        const remove = element('button', { type: 'button', class: 'scf-icon-button scf-icon-button--danger', 'aria-label': 'Remove condition ' + (index + 1) }, '×');
        remove.addEventListener('click', () => {
          state.conditions.splice(index, 1);
          renderConditions();
          changed();
        });
        row.append(remove, element('p', { class: 'scf-error', 'data-mpol-condition-error': '', hidden: true }));
        fieldSelect.addEventListener('change', () => {
          condition.field = fieldSelect.value;
          const operators = (field(condition.field) || {}).operators || [];
          if (!operators.includes(condition.operator)) condition.operator = operators[0] || 'equals';
          renderConditions();
          changed();
        });
        operator.addEventListener('change', () => {
          condition.operator = operator.value;
          renderConditions();
          changed();
        });
        conditionList.append(row);
      });
    }

    function renderOverrides() {
      form.querySelectorAll('[data-mpol-exceptions]').forEach((group) => {
        const action = group.dataset.mpolExceptions;
        const list = group.querySelector('[data-mpol-exception-list]');
        list.replaceChildren();
        state.overrides.filter((item) => item.action === action).forEach((item) => {
          const name = tenants.get(String(item.company_id)) || 'Company ' + item.company_id;
          const tag = element('li', { class: 'scf-tag' });
          const remove = element('button', { type: 'button', class: 'scf-tag__remove', 'aria-label': 'Remove ' + name }, '×');
          remove.addEventListener('click', () => {
            state.overrides = state.overrides.filter((other) => other !== item);
            renderOverrides();
            changed();
          });
          tag.append(element('span', { class: 'scf-tag__text' }, name), remove);
          list.append(tag);
        });
      });
    }

    function schedulePreview() {
      window.clearTimeout(previewTimer);
      previewTimer = window.setTimeout(runPreview, 350);
    }

    async function runPreview() {
      if (!validateConditions(false)) {
        previewCount.textContent = 'Finish the conditions to see matching tenants.';
        previewTenants.replaceChildren();
        return;
      }
      const request = ++previewRequest;
      previewCount.textContent = 'Checking tenants…';
      try {
        const headers = { Accept: 'application/json', 'Content-Type': 'application/json' };
        const token = csrfToken();
        if (token) headers['X-CSRF-Token'] = token;
        const response = await fetch('/api/m365/policies/preview', {
          method: 'POST', credentials: 'same-origin', headers: headers,
          body: JSON.stringify({ target_rules: rules(), overrides: state.overrides }),
        });
        const payload = await response.json().catch(() => ({}));
        if (request !== previewRequest) return;
        if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Preview failed');
        previewCount.textContent = 'Matches ' + payload.matched_count + ' of ' + payload.eligible_count + ' connected tenant' + (payload.eligible_count === 1 ? '' : 's') + '.';
        previewTenants.replaceChildren();
        payload.tenants.slice(0, PREVIEW_LIMIT).forEach((tenant) => previewTenants.append(element('li', {}, tenant.company_name)));
        if (payload.tenants.length > PREVIEW_LIMIT) previewTenants.append(element('li', { class: 'text-muted' }, '…and ' + (payload.tenants.length - PREVIEW_LIMIT) + ' more'));
      } catch (error) {
        if (request !== previewRequest) return;
        previewCount.textContent = 'Could not preview tenants: ' + error.message;
        previewTenants.replaceChildren();
      }
    }

    function changed() {
      refreshSummary();
      schedulePreview();
    }

    function showError(name, message) {
      const box = form.querySelector('[data-mpol-error="' + name + '"]');
      if (!box) return;
      box.textContent = message || '';
      box.hidden = !message;
    }

    function validateConditions(report) {
      let valid = true;
      conditionList.querySelectorAll('.mpol-condition').forEach((row, index) => {
        const condition = state.conditions[index];
        const meta = field(condition.field);
        let message = '';
        if (!meta) message = 'Choose what to check.';
        else if (meta.keyed && !condition.key.trim()) message = 'Enter the variable name.';
        else if (!NO_VALUE.has(condition.operator) && !condition.value.trim()) message = 'Enter a value.';
        const box = row.querySelector('[data-mpol-condition-error]');
        if (report) {
          box.textContent = message;
          box.hidden = !message;
        }
        if (message) valid = false;
      });
      return valid;
    }

    function load(policy, duplicate) {
      state.policyId = policy && !duplicate ? policy.id : null;
      title.textContent = !policy ? 'Add policy' : duplicate ? 'Duplicate policy' : 'Edit policy';
      nameInput.value = policy ? (duplicate ? policy.name + ' (copy)' : policy.name) : '';
      descriptionInput.value = policy ? policy.description || '' : '';
      enabledInput.checked = policy ? Boolean(policy.enabled) : true;
      const chosen = new Map(((policy && policy.standards) || []).map((item) => [item.standard_id, item.mode]));
      form.querySelectorAll('[data-mpol-standard-input]').forEach((input) => {
        input.checked = chosen.has(input.value);
        const mode = chosen.get(input.value) || 'report';
        form.querySelectorAll('input[name="mode_' + input.value + '"]').forEach((radio) => { radio.checked = radio.value === mode; });
      });
      if (standardSearch) {
        standardSearch.value = '';
        standardSelectedOnly.checked = Boolean(policy && policy.standards && policy.standards.length);
        filterStandards();
      }
      const target = policy && policy.target_rules;
      const flat = target && Array.isArray(target.children) && target.children.every((child) => !Array.isArray(child.children));
      state.nestedRules = target && !flat ? target : null;
      matchSelect.value = target && target.operator === 'or' ? 'or' : 'and';
      state.conditions = flat ? target.children.map((child) => ({
        field: child.field, operator: child.operator, key: child.key || '',
        value: Array.isArray(child.value) ? child.value.join(', ') : child.value === undefined ? '' : String(child.value),
      })) : [];
      nestedNotice.hidden = !state.nestedRules;
      state.overrides = ((policy && policy.overrides) || []).map((item) => ({ company_id: item.company_id, action: item.action }));
      deleteButton.hidden = !state.policyId;
      ['name', 'standards', 'targets'].forEach((name) => showError(name, ''));
      renderConditions();
      renderOverrides();
      refreshSummary();
      runPreview();
      state.snapshot = duplicate ? '' : serialize();
    }

    function open(trigger, policy, duplicate) {
      state.lastTrigger = trigger || null;
      load(policy, duplicate);
      showTab('details');
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('scf-modal-open');
      window.requestAnimationFrame(() => nameInput.focus());
    }

    function close(force) {
      if (!force && serialize() !== state.snapshot && !window.confirm('Discard your unsaved changes to this policy?')) return;
      modal.hidden = true;
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('scf-modal-open');
      if (state.lastTrigger && typeof state.lastTrigger.focus === 'function') state.lastTrigger.focus();
    }

    document.querySelectorAll('[data-mpol-create]').forEach((button) => button.addEventListener('click', () => open(button, null, false)));
    document.querySelectorAll('[data-mpol-edit]').forEach((button) => button.addEventListener('click', () => open(button, policies.get(button.dataset.mpolEdit), false)));
    document.querySelectorAll('[data-mpol-duplicate]').forEach((button) => button.addEventListener('click', () => open(button, policies.get(button.dataset.mpolDuplicate), true)));
    modal.querySelectorAll('[data-modal-close]').forEach((button) => button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      close(false);
    }));
    modal.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close(false);
      }
    });

    const tabs = Array.from(form.querySelectorAll('[data-mpol-tab]'));
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => showTab(tab.dataset.mpolTab));
      tab.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
        event.preventDefault();
        const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
        showTab(next.dataset.mpolTab);
        next.focus();
      });
    });

    form.querySelectorAll('[data-mpol-standard-input], [data-mpol-mode] input').forEach((input) => input.addEventListener('change', changed));
    [nameInput, descriptionInput, enabledInput].forEach((input) => input.addEventListener('input', refreshSummary));
    if (standardSearch) {
      standardSearch.addEventListener('input', filterStandards);
      standardSearch.addEventListener('keydown', (event) => { if (event.key === 'Enter') event.preventDefault(); });
      standardSelectedOnly.addEventListener('change', filterStandards);
    }
    matchSelect.addEventListener('change', changed);
    form.querySelector('[data-mpol-condition-add]').addEventListener('click', () => {
      if (state.nestedRules) {
        state.nestedRules = null;
        nestedNotice.hidden = true;
      }
      state.conditions.push({ field: '', operator: 'equals', key: '', value: '' });
      renderConditions();
      changed();
      const last = conditionList.lastElementChild;
      if (last) last.querySelector('select').focus();
    });
    form.querySelectorAll('[data-mpol-exceptions]').forEach((group) => {
      const select = group.querySelector('[data-mpol-exception-select]');
      group.querySelector('[data-mpol-exception-add]').addEventListener('click', () => {
        const companyId = Number(select.value);
        if (!companyId) return;
        state.overrides = state.overrides.filter((item) => item.company_id !== companyId);
        state.overrides.push({ company_id: companyId, action: group.dataset.mpolExceptions });
        select.value = '';
        renderOverrides();
        changed();
      });
    });

    deleteButton.addEventListener('click', () => {
      if (!state.policyId || !window.confirm('Delete this policy? Its standards stop being checked on these tenants.')) return;
      deleteForm.action = '/m365/policies/' + encodeURIComponent(state.policyId) + '/delete';
      state.snapshot = serialize();
      deleteForm.submit();
    });

    form.addEventListener('submit', (event) => {
      showError('name', nameInput.value.trim() ? '' : 'Give the policy a name.');
      const conditionsValid = validateConditions(true);
      showError('targets', conditionsValid ? '' : 'Finish or remove the highlighted conditions.');
      if (!nameInput.value.trim() || !conditionsValid) {
        event.preventDefault();
        showTab(!nameInput.value.trim() ? 'details' : 'targets');
        return;
      }
      form.querySelector('[data-mpol-out="policy_id"]').value = state.policyId || '';
      const target = rules();
      form.querySelector('[data-mpol-out="target_rules_json"]').value = target ? JSON.stringify(target) : '';
      form.querySelector('[data-mpol-out="overrides_json"]').value = JSON.stringify(state.overrides);
      state.snapshot = serialize();
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const root = document.querySelector('[data-mpol]');
    if (!root) return;
    setupSearch(root);
    setupResultsToolbar();
    setupEditor(readData());
  });
})();
