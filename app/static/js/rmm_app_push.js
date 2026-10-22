(function () {
  'use strict';

  // Push Chocolatey package actions from Script automation.
  const form = document.querySelector('[data-rmm-app-push-form]');
  if (!form) return;
  const extra = form.querySelector('[data-rmm-app-extra]');
  const agentsField = form.querySelector('[data-rmm-app-agents-field]');
  const agents = form.querySelector('[data-rmm-app-agents]');
  const submit = form.querySelector('[data-rmm-app-submit]');
  const message = form.querySelector('[data-rmm-app-message]');
  const picker = window.MyPortalChocoPicker.create({
    search: form.querySelector('[data-rmm-app-search]'),
    button: form.querySelector('[data-rmm-app-search-button]'),
    status: form.querySelector('[data-rmm-app-status]'),
    results: form.querySelector('[data-rmm-app-results]'),
    multiple: true,
    name: 'rmm-app-push-app',
  });

  function show(text, kind) {
    message.textContent = text;
    message.className = 'alert alert--' + kind;
    message.hidden = false;
  }

  form.querySelectorAll('[data-rmm-app-target]').forEach((radio) => radio.addEventListener('change', () => {
    agentsField.hidden = form.querySelector('[data-rmm-app-target]:checked').value !== 'chosen';
  }));

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const ids = Array.from(new Set(picker.selected().concat(
      extra.value.split(',').map((value) => value.trim().toLowerCase()).filter(Boolean))));
    const allDevices = form.querySelector('[data-rmm-app-target]:checked').value === 'all';
    const agentIds = Array.from(agents.selectedOptions).map((option) => Number(option.value));
    if (!ids.length) { show('Choose at least one application.', 'warning'); return; }
    if (!allDevices && !agentIds.length) { show('Choose at least one device.', 'warning'); return; }
    const mode = form.querySelector('[name="mode"]').value;
    const force = form.querySelector('input[type="checkbox"][name="force"]').checked;
    const ignorePendingReboots = form.querySelector('input[type="checkbox"][name="ignore_pending_reboots"]').checked;
    const devices = allDevices ? 'every Windows device' : agentIds.length + ' device' + (agentIds.length === 1 ? '' : 's');
    const action = { install: 'Install', upgrade: 'Upgrade', uninstall: 'Uninstall' }[mode];
    if (!window.confirm(action + ' ' + ids.join(', ') + ' on ' + devices + '?')) return;
    submit.disabled = true;
    try {
      const csrf = document.querySelector('meta[name="csrf-token"]');
      const response = await fetch('/api/rmm/chocolatey/push', {
        method: 'POST', credentials: 'same-origin',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': csrf ? csrf.content : '' },
        body: JSON.stringify({ package_ids: ids, mode: mode, force: force,
          ignore_pending_reboots: ignorePendingReboots, all_devices: allDevices, agent_ids: agentIds }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not push the applications.');
      let text = 'Queued on ' + data.queued + ' device' + (data.queued === 1 ? '' : 's') + '. View results in Scripts run history.';
      if (data.not_needed) text += ' ' + data.not_needed + ' device' + (data.not_needed === 1 ? ' does' : 's do') + ' not have these apps installed.';
      if (data.problems.length) text += ' Not queued: ' + data.problems.map((p) => p.hostname + ' (' + p.detail + ')').join('; ');
      show(text, data.problems.length ? 'warning' : 'success');
    } catch (error) {
      show(error.message, 'error');
    } finally {
      submit.disabled = false;
    }
  });
  picker.find();
})();
