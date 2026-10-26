// RMM checks form: shows only the settings each choice uses, names the check,
// says in words when it fails, and drives the advanced device targeting.
(function () {
  'use strict';

  const form = document.querySelector('[data-rmm-check-form]');
  if (!form) return;
  const nameInput = form.querySelector('[data-rmm-check-name]');
  const durationLabel = form.querySelector('[data-rmm-check-duration-label]');
  const durationHelp = form.querySelector('[data-rmm-check-duration-help]');
  const duration = form.querySelector('[name="duration_minutes"]');
  const summary = form.querySelector('[data-rmm-check-summary]');
  const asset = form.querySelector('[name="asset_id"]');
  const deviceField = form.querySelector('[data-rmm-check-device]');
  const scheduleMode = form.querySelector('[data-rmm-check-schedule-mode]');
  const rulesInput = form.querySelector('[data-rmm-check-target-rules]');
  const rulesField = form.querySelector('[data-rmm-rules-field]');
  const submit = form.querySelector('[data-rmm-check-submit]');
  let fields = [];
  try {
    fields = JSON.parse(document.getElementById('rmm-check-targeting-config').textContent || '{}').fields || [];
  } catch (error) {
    // The editor retries loading the field catalogue through the API.
  }

  const checked = (name) => {
    const input = form.querySelector('[name="' + name + '"]:checked');
    return input ? input.value : '';
  };
  const value = (name) => {
    const input = form.querySelector('[name="' + name + '"]');
    return input ? String(input.value || '').trim() : '';
  };
  const selectedText = (name) => {
    const input = form.querySelector('select[name="' + name + '"]');
    const option = input && input.selectedOptions[0];
    return option && option.value ? option.textContent.replace(/\s*\(.*\)\s*$/, '').trim() : '';
  };
  const minutes = (count) => count + ' minute' + (Number(count) === 1 ? '' : 's');

  async function api(url, options) {
    const token = document.querySelector('meta[name="csrf-token"]');
    const init = Object.assign({ credentials: 'same-origin' }, options || {});
    init.headers = { Accept: 'application/json' };
    if (init.body) {
      init.body = JSON.stringify(init.body);
      init.headers['Content-Type'] = 'application/json';
    }
    if (init.method && init.method !== 'GET') {
      init.headers['X-CSRF-Token'] = token ? token.getAttribute('content') : (form.querySelector('[name="_csrf"]') || {}).value || '';
    }
    const response = await fetch(url, init);
    let data = {};
    try { data = await response.json(); } catch (error) { data = {}; }
    if (!response.ok) {
      const failure = new Error(typeof data.detail === 'string' ? data.detail : 'Request failed (' + response.status + ').');
      failure.data = data;
      throw failure;
    }
    return data;
  }

  const targeting = window.MyPortalRmmTargeting ? window.MyPortalRmmTargeting.create({
    host: rulesField,
    fields: fields,
    api: api,
    previewUrl: '/api/rmm/checks/targeting/preview',
    fieldsUrl: '/api/rmm/checks/targeting/fields',
    getContext: () => ({ scope: checked('scope'), asset_id: checked('scope') === 'asset' && asset.value ? Number(asset.value) : null }),
  }) : null;
  try {
    if (targeting) targeting.setRules(JSON.parse(rulesInput.value || 'null'));
  } catch (error) {
    if (targeting) targeting.setRules(null);
  }

  // What each type watches, for the name filled in until the technician types one.
  function suggestedName(type) {
    switch (type) {
      case 'disk': {
        const drive = value('disk_drive');
        return 'Low disk space' + (drive && drive !== '*' ? ' on ' + drive : '');
      }
      case 'ping': return value('ping_target') ? 'Ping ' + value('ping_target') : 'Ping';
      case 'cpu': return 'High CPU';
      case 'memory': return 'Low memory';
      case 'service': return value('service_name') ? value('service_name') + ' service' : 'Service';
      case 'file': return value('file_path') ? (value('file_expected') === 'missing' ? 'No ' : '') + value('file_path') : 'File check';
      case 'program': return value('program_name') ? value('program_name') + ' installation' : 'Program check';
      case 'process': return value('process_name') ? value('process_name') + (value('process_expected') === 'not_running' ? ' not running' : ' process') : 'Process';
      case 'proxmox': {
        const target = value('proxmox_target') && value('proxmox_target') !== '*' ? ' ' + value('proxmox_target') : '';
        const names = { guest: 'Proxmox guest', storage: 'Proxmox storage', zfs: 'ZFS pool', backup: 'Proxmox backups', quorum: 'Proxmox quorum', node_offline: 'Proxmox node' };
        return (names[value('proxmox_condition')] || 'Proxmox VE') + (value('proxmox_condition') === 'guest' || value('proxmox_condition') === 'storage' || value('proxmox_condition') === 'zfs' || value('proxmox_condition') === 'node_offline' ? target : '');
      }
      case 'network': return 'Network throughput' + (value('network_interface') ? ' on ' + value('network_interface') : '');
      case 'script': return selectedText('check_script_id') || 'Script check';
      case 'offline': return 'Agent offline';
      default: return '';
    }
  }

  // Mirrors app.services.rmm_checks.describe so the summary matches the list.
  function describe(type) {
    const lasting = Number(value('duration_minutes')) > 0 ? ' for ' + minutes(value('duration_minutes')) : '';
    const unit = (metric) => (value(metric) === 'free_gb' ? ' GB' : '%');
    switch (type) {
      case 'disk': {
        const drive = !value('disk_drive') || value('disk_drive') === '*' ? 'any drive' : value('disk_drive');
        return 'Free space on ' + drive + ' is ' + (value('disk_operator') || 'below') + ' ' + (value('disk_threshold') || '0') + unit('disk_metric') + lasting + '.';
      }
      case 'ping': {
        let text = 'Pinging ' + (value('ping_target') || 'the host') + ' loses ' + (value('ping_loss') || '100') + '% or more of its replies';
        if (Number(value('ping_latency')) > 0) text += ', or replies take over ' + value('ping_latency') + ' ms';
        return text + lasting + '.';
      }
      case 'cpu': return 'CPU is over ' + (value('cpu_threshold') || '0') + '%' + lasting + '.';
      case 'memory': return 'Free memory is below ' + (value('memory_threshold') || '0') + unit('memory_metric') + lasting + '.';
      case 'service': {
        const fix = value('service_fix');
        const problems = { running: ' is not running', stopped: ' is not stopped', exists: ' does not exist', missing: ' exists' };
        let text = (value('service_name') || 'The service') + (problems[value('service_expected')] || problems.running) + lasting + '.';
        if (fix && fix !== 'none') text += ' The agent will ' + fix + ' it straight away.';
        return text;
      }
      case 'file': {
        const state = value('file_expected') === 'missing' ? ' exists' : ' does not exist';
        return (value('file_path') || 'The path') + state + lasting + '.';
      }
      case 'process': {
        const state = value('process_expected') === 'not_running' ? ' is running' : ' is not running';
        return (value('process_name') || 'The process') + state + lasting + '.';
      }
      case 'program': {
        const name = value('program_name') || 'The program';
        if (value('program_expected') === 'missing') return name + ' is installed' + lasting + '.';
        const operator = value('program_version_operator');
        const requirement = operator && operator !== 'none'
          ? ' or its version fails the requirement: ' + selectedText('program_version_operator').toLowerCase() + ' ' + (value('program_version') || 'the required version') : '';
        return name + ' is not installed' + requirement + lasting + '.';
      }
      case 'proxmox': {
        const target = value('proxmox_target');
        const every = !target || target === '*';
        switch (value('proxmox_condition')) {
          case 'guest': return 'Guest ' + (target || '?') + ' is not running' + lasting + '.';
          case 'storage': return (every ? 'Any storage' : 'Storage ' + target) + ' is over ' + (value('proxmox_threshold') || '85') + '% full' + lasting + '.';
          case 'zfs': return (every ? 'A ZFS pool' : 'ZFS pool ' + target) + ' is not ONLINE' + lasting + '.';
          case 'backup': return 'The latest backup job on the node failed' + lasting + '.';
          case 'quorum': return 'The Proxmox VE cluster has lost quorum' + lasting + '.';
          case 'node_offline': return (every ? 'A cluster node' : 'Node ' + target) + ' is offline' + lasting + '.';
          default: return '';
        }
      }
      case 'network': {
        const where = value('network_interface') || 'All interfaces together';
        const directions = { in: 'received', out: 'sent', total: 'received and sent' };
        return where + ': data ' + (directions[value('network_direction')] || 'received and sent') + ' is ' + (value('network_operator') || 'above') + ' ' + (value('network_threshold') || '0') + ' Mbps' + lasting + '.';
      }
      case 'script': {
        const expect = form.querySelector('[name="script_expect"]');
        const how = expect && expect.selectedOptions[0] ? expect.selectedOptions[0].textContent.toLowerCase() : 'exit code is';
        return (selectedText('check_script_id') || 'The script') + ', run every ' + (value('script_interval') || '15') + ' min, does not pass: its ' + how + ' “' + (value('script_value') || '0') + '”' + lasting + '.';
      }
      case 'offline': return 'The agent has not checked in for ' + minutes(value('duration_minutes') || '0') + '.';
      default: return '';
    }
  }

  let autoName = nameInput && nameInput.hasAttribute('data-rmm-auto-name');
  if (nameInput) {
    nameInput.addEventListener('input', () => { autoName = nameInput.value.trim() === ''; });
  }

  function refreshText() {
    const type = checked('check_type');
    if (summary) summary.textContent = describe(type);
    if (autoName && nameInput) {
      nameInput.value = suggestedName(type);
      if (nameInput.value) nameInput.removeAttribute('aria-invalid');
    }
  }

  // [data-rmm-show="action:a,b|type:c"] shows when any clause matches.
  function matches(rule, state) {
    return rule.split('|').some((clause) => {
      const parts = clause.split(':');
      return (parts[1] || '').split(',').indexOf(state[parts[0]]) !== -1;
    });
  }

  function update() {
    const type = checked('check_type');
    const state = { type: type, action: checked('action') };
    form.querySelectorAll('[data-rmm-check-fields]').forEach((fieldset) => {
      const active = fieldset.getAttribute('data-rmm-check-fields') === type;
      fieldset.hidden = !active;
      fieldset.disabled = !active;
    });
    // Hidden, not disabled: the saved values of unused action settings stay as they were.
    form.querySelectorAll('[data-rmm-show]').forEach((element) => {
      element.hidden = !matches(element.getAttribute('data-rmm-show'), state);
    });
    // The agent can only start, restart or stop a service, not install or remove one.
    const serviceFix = form.querySelector('[data-rmm-service-fix-field]');
    if (serviceFix) {
      const presence = ['exists', 'missing'].indexOf(value('service_expected')) !== -1;
      serviceFix.hidden = presence;
      const fixSelect = serviceFix.querySelector('select');
      if (presence && fixSelect) fixSelect.value = 'none';
    }
    const programVersion = form.querySelector('[data-rmm-program-version]');
    if (programVersion) programVersion.hidden = value('program_expected') === 'missing';
    const programValue = form.querySelector('[data-rmm-program-version-value]');
    if (programValue) programValue.hidden = value('program_version_operator') === 'none';
    form.querySelectorAll('[data-rmm-proxmox-show]').forEach((element) => {
      element.hidden = element.getAttribute('data-rmm-proxmox-show').split(',').indexOf(value('proxmox_condition')) === -1;
    });
    const mode = scheduleMode ? scheduleMode.value : 'delay';
    form.querySelectorAll('[data-rmm-show-schedule]').forEach((element) => {
      element.hidden = element.getAttribute('data-rmm-show-schedule') !== mode;
    });
    const offline = type === 'offline';
    if (durationLabel) durationLabel.textContent = offline ? 'Fails after no check-in for' : 'Problem must last at least';
    if (durationHelp) durationHelp.textContent = offline ? 'At least 5 minutes.' : '0 fails as soon as the problem is seen.';
    if (duration) duration.min = offline ? '5' : '0';
    refreshText();
  }

  function writeRules() {
    rulesInput.value = checked('target_mode') === 'rules' && targeting ? JSON.stringify(targeting.getRules()) : '';
  }

  function updateSelection() {
    const oneDevice = checked('scope') === 'asset';
    deviceField.hidden = !oneDevice;
    asset.disabled = !oneDevice;
    asset.required = oneDevice;
    rulesField.hidden = checked('target_mode') !== 'rules';
    writeRules();
  }

  // A script chosen since the page loaded has its values filled in after saving.
  function updateScriptValues(select) {
    const kind = select.getAttribute('data-rmm-script-select');
    const values = form.querySelector('[data-rmm-script-values="' + kind + '"]');
    const pending = form.querySelector('[data-rmm-script-pending="' + kind + '"]');
    const saved = select.getAttribute('data-rmm-saved-script') || '';
    const same = select.value !== '' && select.value === saved && values && values.children.length > 0;
    if (values) values.hidden = !same;
    if (pending) {
      pending.hidden = same;
      pending.textContent = select.value
        ? 'Once you save, ' + selectedText(select.name) + "'s parameters appear here to fill in."
        : 'Choose a script. Once you save, its parameters appear here to fill in.';
    }
  }

  form.addEventListener('change', (event) => {
    const target = event.target;
    if (target.matches('[data-rmm-check-type], [data-rmm-check-action], [data-rmm-check-schedule-mode], [data-rmm-service-expected], [data-rmm-program-expected], [data-rmm-program-operator], [data-rmm-proxmox-condition]')) update();
    if (target.matches('[data-rmm-check-target-mode]')) updateSelection();
    if (target.matches('[data-rmm-check-scope]')) {
      updateSelection();
      if (targeting) targeting.invalidate();
    }
    if (target === asset && targeting) targeting.invalidate();
    if (target.matches('[data-rmm-script-select]')) updateScriptValues(target);
    if (target.getAttribute('aria-invalid') === 'true') target.removeAttribute('aria-invalid');
    refreshText();
  });
  form.addEventListener('input', refreshText);

  // Quick picks add a result the action is limited to, once.
  const actionResults = form.querySelector('[data-rmm-action-results]');
  form.addEventListener('click', (event) => {
    const pick = event.target.closest('[data-rmm-result-example]');
    if (!pick || !actionResults) return;
    const text = pick.getAttribute('data-rmm-result-example');
    const lines = actionResults.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (!lines.some((line) => line.toLowerCase() === text.toLowerCase())) lines.push(text);
    actionResults.value = lines.join('\n');
    actionResults.focus();
  });
  rulesField.addEventListener('rmm:rules-change', writeRules);

  // Jump to the first control the browser's own validation stops on, wherever it is.
  form.addEventListener('invalid', (event) => {
    const control = event.target;
    if (control.closest('[hidden]')) return;
    control.setAttribute('aria-invalid', 'true');
  }, true);

  form.addEventListener('submit', (event) => {
    if (checked('target_mode') === 'rules' && (!targeting || !targeting.validate(true))) {
      event.preventDefault();
      if (!targeting) {
        const error = rulesField.querySelector('[data-rmm-error="target_rules"]');
        error.textContent = 'The rule editor is unavailable. Reload the page and try again.';
        error.hidden = false;
      }
      const invalid = rulesField.querySelector('[aria-invalid="true"]');
      if (invalid) invalid.focus();
      return;
    }
    writeRules();
    if (submit) {
      submit.disabled = true;
      submit.classList.add('button--processing');
      submit.textContent = 'Saving…';
    }
  });

  // Links in the error summary move focus to the control, not just scroll to it.
  const errorSummary = form.querySelector('[data-rmm-error-summary]');
  if (errorSummary) {
    errorSummary.addEventListener('click', (event) => {
      const link = event.target.closest('a[href^="#"]');
      const control = link && document.getElementById(link.getAttribute('href').slice(1));
      if (!control) return;
      event.preventDefault();
      control.scrollIntoView({ block: 'center' });
      control.focus({ preventScroll: true });
    });
  }

  update();
  updateSelection();
  form.querySelectorAll('[data-rmm-script-select]').forEach(updateScriptValues);
  if (errorSummary) errorSummary.focus();
})();
