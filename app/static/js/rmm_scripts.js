(function () {
  'use strict';

  const page = document.querySelector('[data-rmm-page]');
  const runModal = document.querySelector('[data-rmm-run-modal]');
  const resultModal = document.querySelector('[data-rmm-result-modal]');
  if (!page || !runModal || !resultModal) {
    return;
  }

  let config = { can_run: false, asset_id: null, is_super_admin: false, tags: [], default_timezone: 'UTC' };
  try {
    config = Object.assign(config, JSON.parse(document.getElementById('rmm-config').textContent || '{}'));
  } catch (error) {
    // Defaults keep the page read-only.
  }

  const STATUS = {
    queued: ['info', 'Waiting for device'],
    dispatched: ['info', 'Sent to device'],
    running: ['warning', 'Running'],
    completed: ['success', 'Completed'],
    failed: ['error', 'Failed'],
    timed_out: ['error', 'Timed out'],
    cancelled: ['neutral', 'Cancelled'],
    expired: ['neutral', 'Expired'],
  };
  const ACTIVE = new Set(['queued', 'dispatched', 'running']);
  const RETRYABLE = new Set(['expired', 'timed_out', 'failed']);
  const TYPE_LABELS = {
    string: 'Text', integer: 'Whole number', number: 'Number', boolean: 'Yes / no', switch: 'Switch',
    choice: 'Choice', list: 'List', secret: 'Secret',
  };

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  async function api(url, options) {
    const init = Object.assign({ credentials: 'same-origin', headers: {} }, options || {});
    init.headers = Object.assign({ Accept: 'application/json' }, init.headers);
    if (init.body && typeof init.body !== 'string') {
      init.body = JSON.stringify(init.body);
      init.headers['Content-Type'] = 'application/json';
    }
    if (init.method && init.method !== 'GET') {
      init.headers['X-CSRF-Token'] = csrfToken();
    }
    const response = await fetch(url, init);
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      const failure = new Error(typeof data.detail === 'string' ? data.detail : 'Request failed (' + response.status + ').');
      failure.data = data;
      throw failure;
    }
    return data;
  }

  function el(tag, attrs, text) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === false || value === null || value === undefined) {
        return;
      }
      if (key === 'className') {
        node.className = value;
      } else {
        node.setAttribute(key, value === true ? '' : String(value));
      }
    });
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function statusPill(status) {
    const item = STATUS[status] || ['neutral', status];
    return el('span', { className: 'status status--' + item[0] }, item[1]);
  }

  function formatDate(value) {
    if (!value) {
      return '–';
    }
    const date = new Date(/Z|[+-]\d\d:?\d\d$/.test(value) ? value : value + 'Z');
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  function openModal(modal) {
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
  }

  function closeModal(modal) {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
  }

  // Times are stored in UTC; show them in the viewer's time zone.
  page.querySelectorAll('[data-utc]').forEach((node) => {
    node.textContent = formatDate(node.getAttribute('data-utc'));
  });

  // ------------------------------------------------------------------ //
  // Run editor
  // ------------------------------------------------------------------ //

  const form = runModal.querySelector('[data-rmm-run-form]');
  const scriptSelect = runModal.querySelector('[data-rmm-script-select]');
  const picker = runModal.querySelector('[data-rmm-script-picker]');
  const devicesSection = runModal.querySelector('[data-rmm-devices-section]');
  const devicesList = runModal.querySelector('[data-rmm-devices]');
  const devicesEmpty = runModal.querySelector('[data-rmm-devices-empty]');
  const deviceSearch = runModal.querySelector('[data-rmm-device-search]');
  const fieldsSection = runModal.querySelector('[data-rmm-fields-section]');
  const fieldsHost = runModal.querySelector('[data-rmm-fields]');
  const noFields = runModal.querySelector('[data-rmm-no-fields]');
  const timeoutSection = runModal.querySelector('[data-rmm-timeout-section]');
  const timeoutInput = runModal.querySelector('[data-rmm-timeout]');
  const runAsSection = runModal.querySelector('[data-rmm-run-as-section]');
  const runAsSelect = runModal.querySelector('[data-rmm-run-as]');
  const previewSection = runModal.querySelector('[data-rmm-preview-section]');
  const submitButton = runModal.querySelector('[data-rmm-submit]');
  const formError = runModal.querySelector('[data-rmm-error="form"]');
  const assetsError = runModal.querySelector('[data-rmm-error="assets"]');
  const automationSection = runModal.querySelector('[data-rmm-automation-section]');
  const scheduleName = runModal.querySelector('[data-rmm-schedule-name]');
  const scopeField = runModal.querySelector('[data-rmm-scope-field]');
  const scopeSelect = runModal.querySelector('[data-rmm-scope]');
  const targetMode = runModal.querySelector('[data-rmm-target-mode]');
  const tagsField = runModal.querySelector('[data-rmm-tags-field]');
  const tagsList = runModal.querySelector('[data-rmm-tags]');
  const cronPreset = runModal.querySelector('[data-rmm-cron-preset]');
  const cronInput = runModal.querySelector('[data-rmm-cron]');
  const timezoneInput = runModal.querySelector('[data-rmm-timezone]');
  const cronPreview = runModal.querySelector('[data-rmm-cron-preview]');
  const enabledBox = runModal.querySelector('[data-rmm-enabled]');
  const continueBox = runModal.querySelector('[data-rmm-continue]');

  // mode: 'run' pushes the script now; 'schedule' and 'onboarding' save it
  // with its values for later (on /rmm/automation). item is the record being edited.
  const state = { detail: null, dirty: false, done: false, scriptsLoaded: false, mode: 'run', item: null, onSaved: null };

  const rulesField = runModal.querySelector('[data-rmm-rules-field]');
  const targeting = window.MyPortalRmmTargeting ? window.MyPortalRmmTargeting.create({
    host: rulesField, api: api,
    getContext: () => ({ scope: state.mode === 'schedule' && scopeSelect.value === 'all' ? 'global' : 'company',
      script_id: state.detail && state.detail.script.id }),
  }) : null;

  function fieldInputId(field) {
    return 'rmm-field-' + field.key.replace(/[^A-Za-z0-9_-]/g, '-');
  }

  function variableOptions(select, groups) {
    Object.keys(groups).forEach((group) => {
      const optgroup = el('optgroup', { label: group });
      groups[group].forEach((variable) => {
        optgroup.append(el('option', { value: variable.token }, variable.label + ' — ' + variable.token));
      });
      select.append(optgroup);
    });
  }

  function groupedVariables(variables) {
    const groups = {};
    (variables || []).forEach((variable) => {
      (groups[variable.group] = groups[variable.group] || []).push(variable);
    });
    return groups;
  }

  function renderField(field, groups) {
    const wrapper = el('div', { className: 'rmm-field', 'data-rmm-field': field.key });
    const id = fieldInputId(field);
    const header = el('div', { className: 'rmm-field__header' });
    const label = el('label', { className: 'form-label', for: id }, field.name);
    if (!field.mandatory || field.type === 'switch') {
      label.append(' ', el('span', { className: 'rmm-optional' }, 'optional'));
    }
    header.append(label);
    const chips = el('ul', { className: 'rmm-chips', 'aria-label': field.name + ' details' });
    chips.append(el('li', { className: 'rmm-chip' + (field.kind === 'env' ? ' rmm-chip--env' : '') }, field.kind === 'env' ? 'Environment variable' : 'Parameter'));
    chips.append(el('li', { className: 'rmm-chip' }, TYPE_LABELS[field.type] || field.type));
    if (field.sensitive) {
      chips.append(el('li', { className: 'rmm-chip rmm-chip--muted' }, 'Sensitive'));
    }
    header.append(chips);
    wrapper.append(header);

    const row = el('div', { className: 'rmm-field__row' });
    let control;
    const selectTypes = ['choice', 'boolean', 'switch'];
    if (selectTypes.includes(field.type)) {
      control = el('select', { className: 'form-input', id: id, 'data-rmm-value': field.key });
      const blank = field.default ? 'Script default (' + field.default + ')' : (field.type === 'switch' ? 'Off' : 'Not set');
      control.append(el('option', { value: '' }, blank));
      const values = field.type === 'choice' ? field.choices.map((choice) => [choice, choice]) : [['true', 'Yes'], ['false', 'No']];
      const choiceGroup = el('optgroup', { label: field.type === 'choice' ? 'Choices' : 'Value' });
      values.forEach(([value, text]) => choiceGroup.append(el('option', { value: value }, text)));
      control.append(choiceGroup);
      variableOptions(control, groups);
      row.append(control);
    } else {
      const multiline = field.type === 'list';
      control = el(multiline ? 'textarea' : 'input', {
        className: 'form-input', id: id, 'data-rmm-value': field.key, autocomplete: 'off', spellcheck: 'false',
        rows: multiline ? 3 : null,
        type: multiline ? null : (field.sensitive ? 'password' : 'text'),
        inputmode: field.type === 'integer' ? 'numeric' : (field.type === 'number' ? 'decimal' : null),
        placeholder: field.default ? 'Script default: ' + field.default : (multiline ? 'One value per line' : ''),
      });
      row.append(control);
      const insert = el('select', { className: 'form-input rmm-field__variable', 'aria-label': 'Use a MyPortal variable for ' + field.name });
      insert.append(el('option', { value: '' }, 'Use a variable…'));
      variableOptions(insert, groups);
      insert.addEventListener('change', () => {
        if (!insert.value) {
          return;
        }
        if (field.type === 'string' || field.type === 'list') {
          const start = control.selectionStart != null ? control.selectionStart : control.value.length;
          const end = control.selectionEnd != null ? control.selectionEnd : control.value.length;
          control.value = control.value.slice(0, start) + insert.value + control.value.slice(end);
        } else {
          control.value = insert.value;
        }
        if (control.type === 'password') {
          control.type = 'text';
        }
        insert.value = '';
        control.dispatchEvent(new Event('input', { bubbles: true }));
        control.focus();
      });
      row.append(insert);
    }
    wrapper.append(row);
    if (field.help) {
      wrapper.append(el('p', { className: 'form-help' }, field.help));
    }
    wrapper.append(el('p', { className: 'form-help form-help--error', 'data-rmm-error': field.key, hidden: true }));
    return wrapper;
  }

  function collectValues() {
    const values = {};
    fieldsHost.querySelectorAll('[data-rmm-value]').forEach((control) => {
      values[control.dataset.rmmValue] = control.value;
    });
    return values;
  }

  function selectedAssets() {
    return Array.from(devicesList.querySelectorAll('input[type="checkbox"]:checked')).map((box) => Number(box.value));
  }

  function quote(value, powershell) {
    const text = String(value);
    if (/^[A-Za-z0-9_./:@-]+$/.test(text)) {
      return text;
    }
    return powershell ? "'" + text.replace(/'/g, "''") + "'" : "'" + text.replace(/'/g, "'\\''") + "'";
  }

  function updatePreview() {
    const detail = state.detail;
    if (!detail) {
      return;
    }
    const powershell = detail.script.language === 'powershell';
    const values = collectValues();
    const parts = [powershell ? '& ' + quote(detail.script.name + '.ps1', true) : './' + detail.script.path.split('/').pop()];
    const env = [];
    detail.fields.forEach((field) => {
      const value = values[field.key] || '';
      if (!value) {
        return;
      }
      const shown = field.sensitive && !value.includes('{{') ? '••••••' : value;
      if (field.kind === 'env') {
        env.push([field.name, shown]);
        return;
      }
      const flag = (powershell ? '-' : '--') + field.name;
      if (field.type === 'switch') {
        if (value === 'true') {
          parts.push(flag);
        } else if (value !== 'false') {
          parts.push(flag + ':' + shown);
        }
        return;
      }
      parts.push(flag, quote(shown, powershell));
    });
    runModal.querySelector('[data-rmm-preview-command]').textContent = parts.join(' ');
    const envList = runModal.querySelector('[data-rmm-preview-env]');
    envList.replaceChildren();
    env.forEach(([name, value]) => {
      const row = el('div');
      row.append(el('dt', null, name), el('dd', null, value));
      envList.append(row);
    });
    const count = selectedAssets().length;
    const envText = env.length ? ' with ' + env.length + ' environment variable' + (env.length === 1 ? '' : 's') : '';
    const summary = runModal.querySelector('[data-rmm-preview-summary]');
    if (state.mode !== 'run' && targetMode.value === 'rules') {
      summary.textContent = 'Runs only on devices matching the device rules' + envText + '.';
      submitButton.disabled = false;
      return;
    }
    if (state.mode === 'onboarding' && targetMode.value === 'all') {
      summary.textContent = 'Runs on each new device' + envText + ', after the steps before it.';
      submitButton.disabled = false;
      return;
    }
    if (state.mode === 'onboarding') {
      const tagCount = selectedTags().length;
      summary.textContent = tagCount
        ? 'Runs on each new device with any of ' + tagCount + ' tag' + (tagCount === 1 ? '' : 's') + envText + ', after the steps before it.'
        : 'Choose at least one tag, such as Server or Workstation.';
      submitButton.disabled = !tagCount;
      return;
    }
    if (state.mode === 'schedule') {
      const mode = targetMode.value;
      const tagCount = selectedTags().length;
      if (mode === 'assets') {
        summary.textContent = count ? 'Runs on ' + count + ' chosen device' + (count === 1 ? '' : 's') + envText + '.' : 'Choose at least one device.';
      } else if (mode === 'tags') {
        summary.textContent = tagCount ? 'Runs on devices with any of ' + tagCount + ' tag' + (tagCount === 1 ? '' : 's') + envText + '.' : 'Choose at least one tag.';
      } else {
        summary.textContent = 'Runs on every device with the RMM agent' + envText + '.';
      }
      submitButton.disabled = (mode === 'assets' && !count) || (mode === 'tags' && !tagCount);
      return;
    }
    summary.textContent = count ? 'Runs on ' + count + ' device' + (count === 1 ? '' : 's') + envText + '.' : 'Choose at least one device.';
    submitButton.disabled = !count;
    const retry = state.item && state.item.retry && Number(state.item.script_id) === Number(detail.script.id);
    submitButton.textContent = count > 1 ? 'Run on ' + count + ' devices' : (retry ? 'Retry script' : 'Run script');
  }

  // ------------------------------------------------------------------ //
  // Schedule and onboarding settings
  // ------------------------------------------------------------------ //

  function selectedTags() {
    return Array.from(tagsList.querySelectorAll('input[type="checkbox"]:checked')).map((box) => Number(box.value));
  }

  function renderTags(chosen) {
    tagsList.replaceChildren();
    (config.tags || []).forEach((tag) => {
      const id = 'rmm-tag-' + tag.id;
      const item = el('li', { className: 'rmm-tag-option' });
      const box = el('input', { type: 'checkbox', id: id, value: tag.id });
      box.checked = chosen.includes(Number(tag.id));
      item.append(box, el('label', { for: id }, tag.name));
      tagsList.append(item);
    });
    runModal.querySelector('[data-rmm-tags-empty]').hidden = (config.tags || []).length > 0;
  }

  function editorTexts() {
    if (state.mode === 'schedule') {
      return ['Schedule a script', state.item ? 'Edit schedule' : 'New schedule', 'Pick a script, when it runs and the values it needs.', 'Save schedule'];
    }
    if (state.mode === 'onboarding') {
      return ['Onboarding step', state.item ? 'Edit onboarding step' : 'Add an onboarding step', 'Runs once on each matching new device, after the steps before it.', state.item ? 'Save step' : 'Add step'];
    }
    return ['Run a script', 'Choose a script', 'Pick a script, the devices to run it on and the values it needs.', 'Run script'];
  }

  function applyMode() {
    const automated = state.mode !== 'run';
    automationSection.hidden = !automated;
    runModal.querySelectorAll('[data-rmm-mode-only]').forEach((node) => {
      node.hidden = node.getAttribute('data-rmm-mode-only') !== state.mode;
    });
    scopeField.hidden = state.mode !== 'schedule' || !config.is_super_admin || Boolean(state.item);
    const everyCompany = scopeSelect.value === 'all';
    const assetsOption = targetMode.querySelector('[data-rmm-company-only]');
    assetsOption.disabled = everyCompany || state.mode === 'onboarding';
    if ((everyCompany || state.mode === 'onboarding') && targetMode.value === 'assets') {
      targetMode.value = 'all';
    }
    tagsField.hidden = !automated || targetMode.value !== 'tags';
    rulesField.hidden = !automated || targetMode.value !== 'rules';
    if (targeting) targeting.invalidate();
    if (state.detail) {
      devicesSection.hidden = automated && !(state.mode === 'schedule' && targetMode.value === 'assets');
    }
  }

  let previewTimer = null;
  function refreshCronPreview() {
    window.clearTimeout(previewTimer);
    if (state.mode !== 'schedule') {
      return;
    }
    const cron = cronInput.value.trim();
    if (!cron) {
      cronPreview.textContent = 'Enter a cron expression.';
      return;
    }
    previewTimer = window.setTimeout(async () => {
      const query = '?cron=' + encodeURIComponent(cron) + '&timezone=' + encodeURIComponent(timezoneInput.value.trim());
      try {
        const data = await api('/api/rmm/schedules/preview' + query);
        cronPreview.textContent = data.next_runs.length
          ? 'Next runs: ' + data.next_runs.map((value) => formatDate(value)).join(', ') + ' (your time).'
          : 'This schedule never runs.';
      } catch (error) {
        const errors = (error.data && error.data.errors) || {};
        cronPreview.textContent = errors.cron || errors.timezone || error.message;
      }
    }, 300);
  }

  function resetAutomation() {
    const item = state.item || {};
    scheduleName.value = item.name || '';
    scopeSelect.value = item.scope || 'company';
    targetMode.value = item.target_mode || (state.mode === 'onboarding' ? 'tags' : 'all');
    if (targeting) targeting.setRules(item.target_rules || { operator: 'and', children: [] });
    renderTags((item.tag_ids || []).map(Number));
    const cron = item.cron || '0 2 * * *';
    cronInput.value = cron;
    cronPreset.value = Array.from(cronPreset.options).some((option) => option.value === cron) ? cron : '';
    timezoneInput.value = item.timezone || config.default_timezone || 'UTC';
    enabledBox.checked = item.is_enabled === undefined ? true : Boolean(item.is_enabled);
    continueBox.checked = Boolean(item.continue_on_failure);
    applyMode();
    refreshCronPreview();
  }

  scopeSelect.addEventListener('change', applyMode);
  scriptSelect.addEventListener('change', () => { if (targeting) targeting.invalidate(); });
  rulesField.addEventListener('rmm:rules-change', () => { state.dirty = true; });
  targetMode.addEventListener('change', () => { applyMode(); updatePreview(); });
  tagsList.addEventListener('change', updatePreview);
  cronPreset.addEventListener('change', () => {
    if (cronPreset.value) {
      cronInput.value = cronPreset.value;
    }
    refreshCronPreview();
  });
  cronInput.addEventListener('input', () => {
    cronPreset.value = Array.from(cronPreset.options).some((option) => option.value === cronInput.value.trim()) ? cronInput.value.trim() : '';
    refreshCronPreview();
  });
  timezoneInput.addEventListener('input', refreshCronPreview);

  function renderDevices(agents) {
    devicesList.replaceChildren();
    devicesEmpty.hidden = agents.length > 0;
    agents.forEach((agent) => {
      if (!agent.asset_id) {
        return;
      }
      const id = 'rmm-device-' + agent.asset_id;
      const item = el('li', { className: 'rmm-device', 'data-search': ((agent.asset_name || '') + ' ' + (agent.hostname || '') + ' ' + (agent.os || '')).toLowerCase() });
      const box = el('input', { type: 'checkbox', id: id, value: agent.asset_id });
      const chosen = state.item && (state.mode === 'schedule' || state.mode === 'run')
        ? (state.item.asset_ids || []).map(Number) : [Number(config.asset_id)];
      if (chosen.includes(Number(agent.asset_id))) {
        box.checked = true;
      }
      const label = el('label', { for: id });
      label.append(el('span', { className: 'rmm-device__name' }, agent.asset_name || agent.hostname || 'Device'));
      label.append(el('span', { className: 'rmm-device__meta' }, [agent.os, agent.os_version].filter(Boolean).join(' ') + (agent.last_seen_utc ? ' · last seen ' + formatDate(agent.last_seen_utc) : '')));
      item.append(box, label);
      devicesList.append(item);
    });
  }

  function resetEditor() {
    state.detail = null;
    state.dirty = false;
    state.done = false;
    fieldsHost.replaceChildren();
    [devicesSection, fieldsSection, timeoutSection, runAsSection, previewSection].forEach((section) => { section.hidden = true; });
    formError.hidden = true;
    assetsError.hidden = true;
    runModal.querySelector('[data-rmm-retry-help]').hidden = true;
    submitButton.disabled = true;
    const texts = editorTexts();
    submitButton.textContent = texts[3];
    runModal.querySelector('[data-rmm-run-title]').textContent = texts[1];
    runModal.querySelector('[data-rmm-run-eyebrow]').textContent = texts[0];
    runModal.querySelector('[data-rmm-run-subtitle]').textContent = texts[2];
  }

  async function loadScript(scriptId) {
    resetEditor();
    if (!scriptId) {
      return;
    }
    let detail;
    try {
      detail = await api('/api/rmm/scripts/' + encodeURIComponent(scriptId));
    } catch (error) {
      formError.textContent = error.message;
      formError.hidden = false;
      return;
    }
    state.detail = detail;
    runModal.querySelector('[data-rmm-run-title]').textContent = detail.script.name;
    runModal.querySelector('[data-rmm-run-eyebrow]').textContent = detail.script.language_label + ' script · ' + detail.script.path;
    runModal.querySelector('[data-rmm-run-subtitle]').textContent = detail.script.description || 'Fill in the values this script needs, then run it.';
    renderDevices(detail.agents || []);
    const groups = groupedVariables(detail.variables);
    const saved = state.item && Number(state.item.script_id) === Number(detail.script.id) ? state.item : null;
    detail.fields.forEach((field) => {
      const needsSecret = saved && saved.retry && saved.secret_keys.includes(field.key);
      fieldsHost.append(renderField(needsSecret ? Object.assign({}, field, { mandatory: true }) : field, groups));
    });
    noFields.hidden = detail.fields.length > 0;
    const timeout = saved ? saved.timeout_seconds : detail.script.default_timeout_seconds;
    const retry = saved && saved.retry;
    timeoutInput.min = retry ? String(10 / 60) : '1';
    timeoutInput.step = retry ? 'any' : '1';
    timeoutInput.value = retry ? (timeout || 600) / 60 : Math.max(1, Math.round((timeout || 600) / 60));
    runAsSelect.value = saved && saved.run_as === 'user' ? 'user' : 'system';
    if (saved) {
      Object.entries(saved.values || {}).forEach(([key, value]) => {
        const control = fieldsHost.querySelector('[data-rmm-value="' + CSS.escape(key) + '"]');
        if (control) {
          control.value = value;
        }
      });
    }
    if (saved && saved.retry) {
      const help = runModal.querySelector('[data-rmm-retry-help]');
      help.textContent = 'Review the saved values, then run a new attempt using the current script. The original run stays in history.';
      if (saved.secret_keys.length) {
        help.textContent += ' Re-enter the secret values; they are not retained after a run finishes.';
      }
      help.hidden = false;
    }
    runModal.querySelector('[data-rmm-source]').textContent = detail.script.content || '';
    const link = runModal.querySelector('[data-rmm-source-link]');
    if (detail.source_url) {
      link.href = detail.source_url;
      link.hidden = false;
    } else {
      link.hidden = true;
    }
    [devicesSection, fieldsSection, timeoutSection, runAsSection, previewSection].forEach((section) => { section.hidden = false; });
    applyMode();
    updatePreview();
  }

  async function ensureScriptOptions() {
    if (state.scriptsLoaded) {
      return;
    }
    const data = await api('/api/rmm/scripts');
    (data.scripts || []).forEach((script) => {
      const label = (script.folder ? script.folder + ' / ' : '') + script.name + ' (' + script.language_label + ')';
      scriptSelect.append(el('option', { value: script.id }, label));
    });
    state.scriptsLoaded = true;
  }

  async function openRunEditor(scriptId, options) {
    const settings = options || {};
    state.mode = settings.mode || 'run';
    state.item = settings.item || null;
    state.onSaved = settings.onSaved || null;
    if (settings.scope) {
      scopeSelect.value = settings.scope;
    }
    resetEditor();
    if (state.mode !== 'run') {
      resetAutomation();
      if (settings.scope && !state.item) {
        scopeSelect.value = settings.scope;
        applyMode();
      }
    } else {
      applyMode();
    }
    openModal(runModal);
    try {
      await ensureScriptOptions();
    } catch (error) {
      formError.textContent = error.message;
      formError.hidden = false;
    }
    scriptSelect.value = scriptId || '';
    picker.hidden = false;
    if (scriptId) {
      await loadScript(scriptId);
    }
    if (state.mode === 'schedule' && !state.item) {
      scheduleName.focus();
    } else {
      (scriptId ? (runModal.querySelector('[data-rmm-value]') || submitButton) : scriptSelect).focus();
    }
  }

  window.MyPortalRmm = Object.assign(window.MyPortalRmm || {}, { openEditor: openRunEditor, api: api, formatDate: formatDate, openResult: (runId) => openResult(runId) });

  function closeRunEditor() {
    if (state.dirty && !state.done && !window.confirm('Discard the values you entered?')) {
      return;
    }
    closeModal(runModal);
    if (state.done) {
      window.location.reload();
    }
  }

  document.addEventListener('click', (event) => {
    const action = event.target.closest('[data-rmm-cancel-run-id], [data-rmm-retry-run-id]');
    if (action && config.can_run) {
      event.preventDefault();
      if (action.hasAttribute('data-rmm-cancel-run-id')) {
        cancelRun(action.getAttribute('data-rmm-cancel-run-id'), action);
      } else {
        retryRun(action.getAttribute('data-rmm-retry-run-id'), action);
      }
      return;
    }
    const opener = event.target.closest('[data-rmm-run-open]');
    if (opener && config.can_run) {
      event.preventDefault();
      // The Assets list opens the editor from each row, with that device chosen.
      if (opener.hasAttribute('data-rmm-run-asset')) {
        config.asset_id = Number(opener.getAttribute('data-rmm-run-asset')) || null;
      }
      openRunEditor(opener.getAttribute('data-rmm-run-open') || '');
      return;
    }
    const viewer = event.target.closest('[data-rmm-view-run]');
    if (viewer) {
      event.preventDefault();
      openResult(viewer.getAttribute('data-rmm-view-run'));
    }
  });

  runModal.querySelectorAll('[data-rmm-close]').forEach((button) => button.addEventListener('click', closeRunEditor));
  scriptSelect.addEventListener('change', () => loadScript(scriptSelect.value));
  form.addEventListener('input', (event) => {
    if (event.target !== scriptSelect && event.target !== deviceSearch && event.target !== scopeSelect) {
      state.dirty = true;
    }
    updatePreview();
  });
  form.addEventListener('change', updatePreview);
  deviceSearch.addEventListener('input', () => {
    const term = deviceSearch.value.trim().toLowerCase();
    devicesList.querySelectorAll('.rmm-device').forEach((item) => {
      item.hidden = Boolean(term) && !(item.dataset.search || '').includes(term);
    });
  });
  runModal.querySelector('[data-rmm-select-all]').addEventListener('click', () => {
    devicesList.querySelectorAll('.rmm-device:not([hidden]) input[type="checkbox"]').forEach((box) => { box.checked = true; });
    updatePreview();
  });

  function showErrors(errors) {
    runModal.querySelectorAll('[data-rmm-error]').forEach((node) => { node.hidden = true; });
    let first = null;
    Object.entries(errors || {}).forEach(([key, message]) => {
      const target = key === 'assets' ? assetsError : runModal.querySelector('[data-rmm-error="' + CSS.escape(key) + '"]');
      if (target) {
        target.textContent = message;
        target.hidden = false;
        first = first || target;
      } else {
        formError.textContent = message;
        formError.hidden = false;
      }
    });
    if (first) {
      first.scrollIntoView({ block: 'nearest' });
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.done) {
      closeRunEditor();
      return;
    }
    if (!state.detail) {
      return;
    }
    formError.hidden = true;
    assetsError.hidden = true;
    runModal.querySelectorAll('[data-rmm-error]').forEach((node) => { node.hidden = true; });
    if (state.item && state.item.retry && Number(state.item.script_id) === Number(state.detail.script.id)) {
      const values = collectValues();
      const errors = {};
      state.item.secret_keys.forEach((key) => {
        if (state.detail.fields.some((field) => field.key === key) && !values[key]) {
          errors[key] = 'Re-enter this secret value to retry the script.';
        }
      });
      if (Object.keys(errors).length) {
        showErrors(errors);
        return;
      }
    }
    submitButton.disabled = true;
    const minutes = Number(timeoutInput.value) || 10;
    if (state.mode !== 'run') {
      await saveAutomation(Math.round(minutes * 60));
      return;
    }
    try {
      const result = await api('/api/rmm/scripts/' + state.detail.script.id + '/runs', {
        method: 'POST',
        body: {
          asset_ids: selectedAssets(), values: collectValues(), timeout_seconds: Math.round(minutes * 60), run_as: runAsSelect.value,
        },
      });
      const queued = result.queued || [];
      const problems = result.problems || [];
      if (queued.length === 1 && !problems.length) {
        state.done = true;
        closeModal(runModal);
        openResult(queued[0].run_id, true);
        return;
      }
      const lines = [];
      if (queued.length) {
        lines.push('Sent to ' + queued.length + ' device' + (queued.length === 1 ? '' : 's') + '.');
      }
      problems.forEach((problem) => {
        lines.push((problem.asset_name || 'Device ' + problem.asset_id) + ': ' + problem.message);
      });
      formError.textContent = lines.join(' ');
      formError.hidden = false;
      if (queued.length) {
        state.done = true;
        submitButton.textContent = 'Done';
      }
      submitButton.disabled = false;
    } catch (error) {
      submitButton.disabled = false;
      if (error.data && error.data.errors) {
        showErrors(error.data.errors);
      }
      formError.textContent = error.message;
      formError.hidden = false;
    }
  });

  async function saveAutomation(timeoutSeconds) {
    if (targetMode.value === 'rules' && (!targeting || !targeting.validate(true))) {
      submitButton.disabled = false;
      const invalid = rulesField.querySelector('[aria-invalid="true"]');
      if (invalid) invalid.focus();
      return;
    }
    const base = state.mode === 'schedule' ? '/api/rmm/schedules' : '/api/rmm/onboarding/steps';
    const body = {
      script_id: state.detail.script.id,
      values: collectValues(),
      timeout_seconds: timeoutSeconds,
      run_as: runAsSelect.value,
      target_mode: targetMode.value,
      target_rules: targetMode.value === 'rules' ? targeting.getRules() : null,
    };
    if (state.mode === 'schedule') {
      Object.assign(body, {
        scope: scopeSelect.value,
        name: scheduleName.value.trim(),
        cron: cronInput.value.trim(),
        timezone: timezoneInput.value.trim(),
        target_mode: targetMode.value,
        asset_ids: targetMode.value === 'assets' ? selectedAssets() : [],
        tag_ids: targetMode.value === 'tags' ? selectedTags() : [],
        enabled: enabledBox.checked,
      });
    } else {
      body.tag_ids = targetMode.value === 'tags' ? selectedTags() : [];
      body.continue_on_failure = continueBox.checked;
    }
    try {
      await api(state.item ? base + '/' + state.item.id : base, { method: state.item ? 'PUT' : 'POST', body: body });
      state.done = true;
      closeModal(runModal);
      if (state.onSaved) {
        state.onSaved();
      } else {
        window.location.reload();
      }
    } catch (error) {
      submitButton.disabled = false;
      if (error.data && error.data.errors) {
        showErrors(error.data.errors);
      }
      formError.textContent = error.message;
      formError.hidden = false;
    }
  }

  // ------------------------------------------------------------------ //
  // Run result
  // ------------------------------------------------------------------ //

  let pollTimer = null;
  let currentRun = null;
  let reloadOnClose = false;

  function stat(label, value) {
    const row = el('div');
    row.append(el('dt', null, label));
    const dd = el('dd');
    if (value instanceof Node) {
      dd.append(value);
    } else {
      dd.textContent = value;
    }
    row.append(dd);
    return row;
  }

  function updateOutput(node, text) {
    if (node.textContent === text) return;
    const follow = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
    node.textContent = text;
    if (follow) node.scrollTop = node.scrollHeight;
  }

  function renderRun(run) {
    currentRun = run;
    resultModal.querySelector('[data-rmm-result-title]').textContent = run.script_name;
    resultModal.querySelector('[data-rmm-result-eyebrow]').textContent = run.script_path;
    let source = run.requested_by_email ? ' · requested by ' + run.requested_by_email : '';
    if (run.run_source === 'schedule') {
      source = ' · schedule ' + (run.schedule_name || '(deleted)') + (run.requested_by_email ? ', run now by ' + run.requested_by_email : '');
    } else if (run.run_source === 'onboarding') {
      source = ' · onboarding';
    } else if (run.run_source === 'remote_control') {
      source = ' · remote control' + (run.requested_by_email ? ' by ' + run.requested_by_email : '');
    } else if (run.run_source === 'patch') {
      source = (run.script_name === 'Install Windows updates' ? ' · Windows updates' : ' · patch policy')
        + (run.requested_by_email ? ' by ' + run.requested_by_email : '');
    } else if (run.run_source === 'update_scan') {
      source = ' · Windows Update scan' + (run.requested_by_email ? ' by ' + run.requested_by_email : '');
    } else if (run.run_source === 'tray') {
      source = ' · tray menu';
    } else if (run.run_source === 'command') {
      source = ' · command' + (run.requested_by_email ? ' by ' + run.requested_by_email : '');
    }
    resultModal.querySelector('[data-rmm-result-subtitle]').textContent =
      (run.asset_name ? 'On ' + run.asset_name : 'Device removed') + source;
    const stats = resultModal.querySelector('[data-rmm-result-stats]');
    stats.replaceChildren(
      stat('Status', statusPill(run.status)),
      stat('Exit code', run.exit_code === null || run.exit_code === undefined ? '–' : String(run.exit_code)),
      stat('Run as', run.run_as === 'user' ? 'Logged in user' : 'System'),
      stat('Queued', formatDate(run.queued_at)),
      stat('Finished', formatDate(run.completed_at)),
    );
    const error = resultModal.querySelector('[data-rmm-result-error]');
    error.textContent = run.error_message || '';
    error.hidden = !run.error_message;

    const values = run.custom_values || [];
    const valuesBody = resultModal.querySelector('[data-rmm-result-values]');
    valuesBody.replaceChildren();
    values.forEach((item) => {
      const row = el('tr');
      row.append(
        el('td', null, { asset: 'Asset field', company: 'Company variable', session: 'Remote control' }[item.scope] || item.scope),
        el('td', null, item.name),
        el('td', null, item.value),
      );
      const outcome = el('td');
      const appliedLabel = item.scope === 'session' ? 'Used' : 'Updated';
      outcome.append(el('span', { className: 'status status--' + (item.applied ? 'success' : 'warning') }, item.applied ? appliedLabel : 'Skipped'));
      if (!item.applied && item.message) {
        outcome.append(' ', el('span', { className: 'text-muted' }, item.message));
      }
      row.append(outcome);
      valuesBody.append(row);
    });
    resultModal.querySelector('[data-rmm-result-values-section]').hidden = values.length === 0;

    const active = ACTIVE.has(run.status);
    updateOutput(resultModal.querySelector('[data-rmm-result-stdout]'),
      run.stdout || (active ? 'Waiting for script output…' : 'The script printed nothing.'));
    updateOutput(resultModal.querySelector('[data-rmm-result-stderr]'), run.stderr || '');
    resultModal.querySelector('[data-rmm-output-state]').textContent = active ? 'Live · updates every second' : '';
    resultModal.querySelector('[data-rmm-result-stderr-section]').hidden = !run.stderr;

    const inputs = run.inputs || [];
    const inputList = resultModal.querySelector('[data-rmm-result-inputs]');
    inputList.replaceChildren();
    inputs.forEach((item) => {
      const row = el('div');
      const label = item.kind === 'command' ? item.name : (item.kind === 'env' ? '$env:' : '-') + item.name;
      const value = el('dd', null, item.kind === 'command' ? null : item.value);
      if (item.kind === 'command') value.append(el('pre', { className: 'rmm-output' }, item.value));
      row.append(el('dt', null, label), value);
      inputList.append(row);
    });
    resultModal.querySelector('[data-rmm-result-inputs-section]').hidden = inputs.length === 0;
    resultModal.querySelector('[data-rmm-cancel-run]').hidden = !(config.can_run && run.status === 'queued');
    resultModal.querySelector('[data-rmm-retry-run]').hidden = !(config.can_run && RETRYABLE.has(run.status) && run.script_id && run.asset_id);
    updateRunRow(run);
  }

  function updateRunRow(run) {
    const row = document.querySelector('[data-rmm-run-row="' + Number(run.id) + '"]');
    if (!row) return;
    const status = row.querySelector('[data-column-key="status"]');
    status.replaceChildren(statusPill(run.status));
    status.setAttribute('data-sort-value', run.status);
    const actions = row.querySelector('[data-column-key="actions"]');
    if (config.can_run && RETRYABLE.has(run.status) && run.script_id && run.asset_id && !actions.querySelector('[data-rmm-retry-run-id]')) {
      actions.append(el('button', {
        type: 'button', className: 'button button--ghost button--small', 'data-rmm-retry-run-id': run.id,
        'aria-label': 'Retry run of ' + run.script_name, 'aria-haspopup': 'dialog', 'aria-controls': 'rmm-run-modal',
      }, 'Retry'));
    }
    row.querySelectorAll('[data-rmm-cancel-run-id], [data-rmm-retry-run-id]').forEach((button) => {
      button.hidden = button.hasAttribute('data-rmm-cancel-run-id') ? run.status !== 'queued' : !(RETRYABLE.has(run.status) && run.script_id && run.asset_id);
    });
  }

  function actionError(button, message) {
    const row = button.closest('[data-rmm-run-row]');
    let target = row ? row.querySelector('[data-rmm-action-error]') : resultModal.querySelector('[data-rmm-result-error]');
    if (!target && row) {
      target = el('p', { className: 'form-help form-help--error', 'data-rmm-action-error': true, role: 'alert' });
      row.querySelector('[data-column-key="actions"]').append(target);
    }
    target.textContent = message;
    target.hidden = !message;
  }

  async function retryRun(runId, button) {
    if (button.disabled) return;
    button.disabled = true;
    actionError(button, '');
    try {
      const data = await api('/api/rmm/runs/' + encodeURIComponent(runId));
      const run = data.run;
      updateRunRow(run);
      if (!RETRYABLE.has(run.status) || !run.script_id || !run.asset_id) {
        throw new Error('Only expired, timed out or failed runs with an available script and device can be retried.');
      }
      const values = {};
      const secretKeys = [];
      (run.inputs || []).forEach((input) => {
        const key = input.kind + ':' + input.name;
        if (input.sensitive && !input.is_variable) {
          secretKeys.push(key);
        } else {
          values[key] = input.value;
        }
      });
      stopPolling();
      closeModal(resultModal);
      await openRunEditor(run.script_id, { item: {
        retry: true, script_id: run.script_id, asset_ids: [run.asset_id],
        values: values, timeout_seconds: run.timeout_seconds, run_as: run.run_as, secret_keys: secretKeys,
      } });
    } catch (error) {
      actionError(button, error.message);
    } finally {
      button.disabled = false;
    }
  }

  async function cancelRun(runId, button) {
    if (button.disabled) return;
    button.disabled = true;
    actionError(button, '');
    try {
      const data = await api('/api/rmm/runs/' + encodeURIComponent(runId) + '/cancel', { method: 'POST' });
      updateRunRow(data.run);
      if (currentRun && Number(currentRun.id) === Number(runId) && !resultModal.hidden) {
        stopPolling();
        renderRun(data.run);
      }
    } catch (error) {
      // An agent may have picked it up since the list was rendered.
      try {
        const data = await api('/api/rmm/runs/' + encodeURIComponent(runId));
        updateRunRow(data.run);
        if (currentRun && Number(currentRun.id) === Number(runId) && !resultModal.hidden) renderRun(data.run);
      } catch (refreshError) {
        // Keep the original action failure visible when refresh also fails.
      }
      actionError(button, error.message);
    } finally {
      button.disabled = false;
    }
  }

  let pollGeneration = 0;

  function stopPolling() {
    pollGeneration += 1;
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  async function refreshRun(runId) {
    const generation = pollGeneration;
    try {
      const data = await api('/api/rmm/runs/' + encodeURIComponent(runId));
      if (generation !== pollGeneration || resultModal.hidden) return;
      renderRun(data.run);
      if (ACTIVE.has(data.run.status) && !resultModal.hidden) {
        pollTimer = window.setTimeout(() => refreshRun(runId), 1000);
      }
    } catch (error) {
      if (generation !== pollGeneration || resultModal.hidden) return;
      const message = resultModal.querySelector('[data-rmm-result-error]');
      message.textContent = error.message;
      message.hidden = false;
      pollTimer = window.setTimeout(() => refreshRun(runId), 3000);
    }
  }

  function openResult(runId, reload) {
    stopPolling();
    currentRun = null;
    resultModal.querySelector('[data-rmm-cancel-run]').hidden = true;
    resultModal.querySelector('[data-rmm-retry-run]').hidden = true;
    reloadOnClose = Boolean(reload) || reloadOnClose;
    openModal(resultModal);
    resultModal.querySelector('.modal__close').focus();
    refreshRun(runId);
  }

  resultModal.querySelectorAll('[data-rmm-close]').forEach((button) => button.addEventListener('click', () => {
    stopPolling();
    closeModal(resultModal);
    if (reloadOnClose) {
      window.location.reload();
    }
  }));

  resultModal.querySelector('[data-rmm-cancel-run]').addEventListener('click', (event) => {
    if (currentRun) cancelRun(currentRun.id, event.currentTarget);
  });
  resultModal.querySelector('[data-rmm-retry-run]').addEventListener('click', (event) => {
    if (currentRun) retryRun(currentRun.id, event.currentTarget);
  });
})();
