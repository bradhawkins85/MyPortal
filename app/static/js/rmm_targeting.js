(function () {
  'use strict';

  const LABELS = {
    equals: 'Equals', not_equals: 'Does not equal', contains: 'Contains', not_contains: 'Does not contain',
    starts_with: 'Starts with', ends_with: 'Ends with', in: 'Is one of', not_in: 'Is not one of',
    is_set: 'Is set', is_not_set: 'Is not set', gt: 'Greater than', gte: 'At least',
    lt: 'Less than', lte: 'At most', between: 'Between (inclusive)', version_gte: 'Version at least',
    version_lte: 'Version at most', in_subnet: 'In IP subnet', not_in_subnet: 'Outside IP subnet',
  };
  const NO_VALUE = new Set(['is_set', 'is_not_set']);
  const MULTI_VALUE = new Set(['in', 'not_in', 'between']);
  const NUMERIC = new Set(['gt', 'gte', 'lt', 'lte']);
  let nextId = 0;

  function element(tag, attrs, text) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== false) {
        node.setAttribute(key, value === true ? '' : String(value));
      }
    });
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function create(options) {
    const host = options.host;
    const builder = host.querySelector('[data-rmm-rule-builder]');
    const errorBox = host.querySelector('[data-rmm-error="target_rules"]');
    const previewButton = host.querySelector('[data-rmm-rules-preview]');
    const previewStatus = host.querySelector('[data-rmm-rules-preview-status]');
    const results = host.querySelector('[data-rmm-rules-preview-results]');
    const agents = host.querySelector('[data-rmm-rules-preview-agents]');
    const ids = new WeakMap();
    let fields = options.fields || [];
    let root = { operator: 'and', children: [] };
    let ready = fields.length > 0;
    let revision = 0;
    let requestId = 0;
    let checked = false;

    function id(node) {
      if (!ids.has(node)) ids.set(node, 'rmm-rule-' + (++nextId));
      return ids.get(node);
    }

    function metadata(node) { return fields.find((field) => field.key === node.field); }
    function group(node) { return Array.isArray(node.children); }
    function newCondition() { return { field: '', operator: 'equals', value: '' }; }

    function typedValue(node, field) {
      if (NO_VALUE.has(node.operator)) return undefined;
      if (MULTI_VALUE.has(node.operator)) {
        const values = Array.isArray(node.value) ? node.value : String(node.value || '').split(/[\n,]/);
        return values.map((value) => String(value).trim()).filter(Boolean);
      }
      if (field && field.type === 'boolean' && node.value !== '') return node.value === true || node.value === 'true';
      if (field && (field.type === 'number' || NUMERIC.has(node.operator)) && String(node.value).trim() !== '') return Number(node.value);
      return node.value;
    }

    function serialize(node) {
      if (group(node)) return { operator: node.operator, children: node.children.map(serialize) };
      const item = { field: node.field, operator: node.operator };
      const field = metadata(node);
      if (field && field.keyed) item.key = String(node.key || '').trim();
      const value = typedValue(node, field);
      if (value !== undefined) item.value = value;
      return item;
    }

    function issues(node, path, collected) {
      if (group(node)) {
        if (!node.children.length) collected.push({ node: node, message: path + ': add a condition or a group.' });
        node.children.forEach((child, index) => issues(child, path + ' / ' + (index + 1), collected));
        return collected;
      }
      const field = metadata(node);
      let problem = '';
      if (!field) problem = 'Choose an available field.';
      else if (!(field.operators || []).includes(node.operator)) problem = 'Choose an available operator.';
      else if (field.keyed && !String(node.key || '').trim()) problem = field.key.startsWith('software.') ? 'Enter the installed software name.' : 'Enter the custom field or variable name, or its ID.';
      else if (!NO_VALUE.has(node.operator)) {
        const value = typedValue(node, field);
        if (value === undefined || value === null || value === '' || (Array.isArray(value) && !value.length)) problem = 'Enter a value.';
        else if (node.operator === 'between' && (value.length !== 2 || value.some((item) => !Number.isFinite(Number(item))) || Number(value[0]) > Number(value[1]))) problem = 'Enter two numbers, lower bound first.';
        else if ((field.type === 'number' || NUMERIC.has(node.operator)) && !MULTI_VALUE.has(node.operator) && !Number.isFinite(value)) problem = 'Enter a valid number.';
      }
      if (problem) collected.push({ node: node, message: path + ': ' + problem });
      return collected;
    }

    function validate(show) {
      const problems = ready ? issues(root, 'Rules', []) : [{ node: root, message: 'Rule fields are unavailable. Reload the page and try again.' }];
      if (show) {
        checked = true;
        builder.querySelectorAll('[data-rule-error]').forEach((box) => { box.hidden = true; });
        builder.querySelectorAll('[aria-invalid]').forEach((control) => control.removeAttribute('aria-invalid'));
        problems.forEach((problem) => {
          const row = document.getElementById(id(problem.node));
          const box = row && row.querySelector('[data-rule-error]');
          if (box) {
            box.textContent = problem.message;
            box.hidden = false;
            row.querySelectorAll('input, select, textarea').forEach((control) => control.setAttribute('aria-invalid', 'true'));
          }
        });
        errorBox.hidden = !problems.length;
        errorBox.textContent = problems.length ? problems[0].message : '';
      }
      return !problems.length;
    }

    function invalidate() {
      revision += 1;
      results.hidden = true;
      previewStatus.textContent = 'Preview again to see devices matching the current rules.';
      if (checked) validate(true);
    }

    function changed() {
      invalidate();
      host.dispatchEvent(new CustomEvent('rmm:rules-change', { bubbles: true }));
    }

    function button(text, callback, attrs) {
      const node = element('button', Object.assign({ type: 'button', class: 'button button--ghost button--small' }, attrs || {}), text);
      node.addEventListener('click', callback);
      return node;
    }

    function labeledControl(label, control, help) {
      const wrapper = element('div', { class: 'rmm-rule__control' });
      wrapper.append(element('label', { class: 'form-label', for: control.id }, label), control);
      if (help) wrapper.append(element('p', { class: 'form-help' }, help));
      return wrapper;
    }

    function focus(controlId) {
      const control = document.getElementById(controlId);
      if (control) control.focus();
    }

    function renderCondition(node, parent, number) {
      const nodeId = id(node);
      const row = element('div', { class: 'rmm-rule', id: nodeId });
      const controls = element('div', { class: 'rmm-rule__row' });
      const fieldSelect = element('select', { class: 'form-input', id: nodeId + '-field', 'aria-describedby': nodeId + '-error' });
      fieldSelect.append(element('option', { value: '' }, 'Choose a field…'));
      const categories = new Map();
      fields.forEach((field) => {
        const category = field.category || 'Device';
        if (!categories.has(category)) {
          const optionGroup = element('optgroup', { label: category });
          categories.set(category, optionGroup);
          fieldSelect.append(optionGroup);
        }
        categories.get(category).append(element('option', { value: field.key }, field.label));
      });
      if (node.field && !metadata(node)) fieldSelect.append(element('option', { value: node.field }, node.field + ' (unavailable)'));
      fieldSelect.value = node.field;
      fieldSelect.addEventListener('change', () => {
        node.field = fieldSelect.value;
        const chosen = metadata(node);
        node.operator = (chosen && chosen.operators || [])[0] || 'equals';
        node.value = '';
        delete node.key;
        render();
        changed();
        focus(nodeId + '-field');
      });
      controls.append(labeledControl('Field', fieldSelect));
      const field = metadata(node);
      const operatorSelect = element('select', { class: 'form-input', id: nodeId + '-operator', disabled: !field, 'aria-describedby': nodeId + '-error' });
      ((field && field.operators) || ['equals']).forEach((operator) => operatorSelect.append(element('option', { value: operator }, LABELS[operator] || operator)));
      operatorSelect.value = node.operator;
      operatorSelect.addEventListener('change', () => {
        node.operator = operatorSelect.value;
        render();
        changed();
        focus(nodeId + '-operator');
      });
      controls.append(labeledControl('Operator', operatorSelect));
      if (NO_VALUE.has(node.operator)) {
        controls.append(element('span', { class: 'rmm-rule__no-value' }, 'No value needed'));
      } else {
        const multiple = MULTI_VALUE.has(node.operator);
        const boolean = field && field.type === 'boolean';
        const choices = field && field.options && field.options.length ? field.options : boolean ? [{ value: true, label: 'Yes' }, { value: false, label: 'No' }] : null;
        let control;
        if (choices && !multiple) {
          control = element('select', { class: 'form-input', id: nodeId + '-value', 'aria-describedby': nodeId + '-error' });
          control.append(element('option', { value: '' }, 'Choose a value…'));
          choices.forEach((choice) => control.append(element('option', { value: choice.value === undefined ? choice : String(choice.value) }, choice.label === undefined ? choice : choice.label)));
          control.value = String(node.value === undefined ? '' : node.value);
        } else {
          const numeric = field && (field.type === 'number' || NUMERIC.has(node.operator));
          control = element(multiple ? 'textarea' : 'input', {
            class: 'form-input', id: nodeId + '-value', type: multiple ? null : numeric ? 'number' : 'text',
            step: numeric ? 'any' : null, rows: multiple ? 2 : null, autocomplete: 'off',
            spellcheck: 'false', 'aria-describedby': nodeId + '-error',
            placeholder: node.operator.includes('subnet') ? '192.168.1.0/24' : node.operator === 'between' ? '10, 50' : '',
          });
          control.value = Array.isArray(node.value) ? node.value.join('\n') : node.value === undefined ? '' : String(node.value);
        }
        control.addEventListener('input', () => { node.value = control.value; changed(); });
        control.addEventListener('change', () => { node.value = control.value; changed(); });
        controls.append(labeledControl('Value', control, multiple ? node.operator === 'between' ? 'Lower and upper number, separated by a comma.' : 'One value per line or separated by commas.' : null));
      }
      controls.append(button('Remove', () => {
        parent.children.splice(parent.children.indexOf(node), 1);
        render();
        changed();
        focus(id(parent) + '-add');
      }, { class: 'button button--ghost button--small rmm-rule__remove', 'aria-label': 'Remove condition ' + number }));
      row.append(controls);
      if (field && field.keyed) {
        const software = field.key.startsWith('software.');
        const keyLabel = software ? 'Installed software name' : field.key.startsWith('company.') ? 'Company variable name or ID' : 'Asset custom field name or ID';
        const key = element('input', { class: 'form-input', id: nodeId + '-key', type: 'text', autocomplete: 'off', 'aria-describedby': nodeId + '-error', placeholder: software ? 'WireGuard' : 'Field or variable name, or ID' });
        key.value = node.key || '';
        key.addEventListener('input', () => { node.key = key.value; changed(); });
        row.append(labeledControl(keyLabel, key));
      }
      row.append(element('p', { class: 'form-help form-help--error', id: nodeId + '-error', 'data-rule-error': '', hidden: true }));
      return row;
    }

    function renderGroup(node, parent, path) {
      const nodeId = id(node);
      const wrapper = element('fieldset', { class: 'rmm-rule-group', id: nodeId });
      wrapper.append(element('legend', { class: 'rmm-rule-group__legend' }, parent ? 'Group ' + path : 'All device rules'));
      const header = element('div', { class: 'rmm-rule-group__header' });
      const operator = element('select', { class: 'form-input rmm-rule-group__operator', id: nodeId + '-operator' });
      operator.append(element('option', { value: 'and' }, 'AND — match all'), element('option', { value: 'or' }, 'OR — match any'));
      operator.value = node.operator;
      operator.addEventListener('change', () => { node.operator = operator.value; changed(); });
      header.append(element('label', { class: 'form-label', for: operator.id }, 'Combine conditions using'), operator);
      if (parent) header.append(button('Remove group', () => {
        parent.children.splice(parent.children.indexOf(node), 1);
        render();
        changed();
        focus(id(parent) + '-add');
      }, { 'aria-label': 'Remove group ' + path }));
      wrapper.append(header);
      const children = element('div', { class: 'rmm-rule-group__children' });
      node.children.forEach((child, index) => children.append(group(child) ? renderGroup(child, node, path ? path + '.' + (index + 1) : String(index + 1)) : renderCondition(child, node, path ? path + '.' + (index + 1) : String(index + 1))));
      if (!node.children.length) children.append(element('p', { class: 'form-help' }, 'Add a condition, or a group to combine alternatives.'));
      wrapper.append(children);
      const actions = element('div', { class: 'rmm-rule-group__actions' });
      actions.append(button('Add condition', () => {
        const child = newCondition();
        node.children.push(child);
        render();
        changed();
        focus(id(child) + '-field');
      }, { id: nodeId + '-add' }), button('Add group', () => {
        const child = { operator: 'and', children: [newCondition()] };
        node.children.push(child);
        render();
        changed();
        focus(id(child) + '-operator');
      }));
      wrapper.append(actions, element('p', { class: 'form-help form-help--error', 'data-rule-error': '', hidden: true }));
      return wrapper;
    }

    function render() {
      builder.replaceChildren(renderGroup(root, null, ''));
      if (checked) validate(true);
    }

    function setRules(rules) {
      root = rules && group(rules) ? JSON.parse(JSON.stringify(rules)) : { operator: 'and', children: [newCondition()] };
      checked = false;
      errorBox.hidden = true;
      render();
      invalidate();
      previewStatus.textContent = '';
    }

    function applyExample(name) {
      const example = name === 'vip' ? {
        operator: 'and', children: [
          { field: 'asset.catalogue_type', operator: 'equals', value: 'server' },
          { field: 'company.vip', operator: 'equals', value: true },
        ],
      } : {
        operator: 'and', children: [
          { field: 'software.name', operator: 'contains', value: 'WireGuard' },
          { field: 'asset.custom', operator: 'equals', key: '', value: '' },
        ],
      };
      setRules(example);
      changed();
      const condition = name === 'vip' ? root.children[0] : root.children[1];
      focus(id(condition) + (name === 'vip' ? '-value' : '-key'));
    }

    host.querySelectorAll('[data-rmm-rule-example]').forEach((control) => {
      control.disabled = !ready;
      control.addEventListener('click', () => {
        if (root.children.some((child) => group(child) || child.field) && !window.confirm('Replace the current rules with this example?')) return;
        applyExample(control.dataset.rmmRuleExample);
      });
    });

    previewButton.addEventListener('click', async () => {
      if (!validate(true)) {
        const invalid = builder.querySelector('[aria-invalid="true"]');
        if (invalid) invalid.focus();
        return;
      }
      const context = options.getContext();
      const body = Object.assign({}, context, { rules: serialize(root) });
      if (!body.script_id) delete body.script_id;
      const currentRevision = revision;
      const currentRequest = ++requestId;
      previewButton.disabled = true;
      results.hidden = true;
      previewStatus.textContent = 'Checking matching devices…';
      host.setAttribute('aria-busy', 'true');
      try {
        const data = await options.api(options.previewUrl || '/api/rmm/targeting/preview', { method: 'POST', body: body });
        if (currentRevision !== revision || currentRequest !== requestId) return;
        agents.replaceChildren();
        (data.agents || []).forEach((agent) => {
          const row = element('tr');
          row.append(element('td', {}, agent.hostname || 'Device ' + (agent.asset_id || agent.id)), element('td', {}, agent.company_name || '—'));
          agents.append(row);
        });
        results.hidden = !(data.agents || []).length;
        previewStatus.textContent = data.matched_count + ' of ' + data.eligible_count + ' eligible devices match.' +
          (data.truncated ? ' Showing the first ' + (data.agents || []).length + ' matches.' : '') +
          (data.matched_count === 0 ? ' Review the conditions or wait for inventory to update.' : '');
      } catch (error) {
        if (currentRevision !== revision || currentRequest !== requestId) return;
        const errors = error.data && error.data.errors;
        errorBox.textContent = errors && errors.target_rules ? errors.target_rules : error.message;
        errorBox.hidden = false;
        previewStatus.textContent = 'Could not preview matching devices. Review the rules and try again.';
      } finally {
        if (currentRequest === requestId) {
          previewButton.disabled = !ready;
          host.removeAttribute('aria-busy');
        }
      }
    });

    setRules(null);
    if (!ready) {
      previewButton.disabled = true;
      previewStatus.textContent = 'Loading available rule fields…';
      options.api(options.fieldsUrl || '/api/rmm/targeting/fields').then((data) => {
        fields = data.fields || [];
        ready = fields.length > 0;
        previewButton.disabled = !ready;
        host.querySelectorAll('[data-rmm-rule-example]').forEach((control) => { control.disabled = !ready; });
        render();
        previewStatus.textContent = ready ? '' : 'No rule fields are available.';
      }).catch(() => {
        previewStatus.textContent = 'Could not load rule fields. Reload the page to try again.';
      });
    }
    return { setRules: setRules, getRules: () => serialize(root), validate: validate, invalidate: invalidate };
  }

  window.MyPortalRmmTargeting = { create: create };
})();
