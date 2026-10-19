(function () {
  'use strict';

  // Push Chocolatey installs and updates from Script automation.
  const form = document.querySelector('[data-rmm-app-push-form]');
  if (!form) return;
  const search = form.querySelector('[data-rmm-app-search]');
  const searchButton = form.querySelector('[data-rmm-app-search-button]');
  const status = form.querySelector('[data-rmm-app-status]');
  const results = form.querySelector('[data-rmm-app-results]');
  const extra = form.querySelector('[data-rmm-app-extra]');
  const agentsField = form.querySelector('[data-rmm-app-agents-field]');
  const agents = form.querySelector('[data-rmm-app-agents]');
  const submit = form.querySelector('[data-rmm-app-submit]');
  const message = form.querySelector('[data-rmm-app-message]');
  // Chosen packages survive new searches.
  const chosen = new Map();
  let sequence = 0;

  function show(text, kind) {
    message.textContent = text;
    message.className = 'alert alert--' + kind;
    message.hidden = false;
  }

  function render(packages) {
    const seen = new Set();
    const items = packages.filter((app) => !seen.has(app.package_id) && seen.add(app.package_id));
    chosen.forEach((app, id) => { if (!seen.has(id)) { items.unshift(app); seen.add(id); } });
    const list = document.createElement('ul');
    list.className = 'rmm-app-push__list';
    items.forEach((app) => {
      const item = document.createElement('li');
      const label = document.createElement('label');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.value = app.package_id;
      box.checked = chosen.has(app.package_id);
      box.addEventListener('change', () => { if (box.checked) chosen.set(app.package_id, app); else chosen.delete(app.package_id); });
      label.append(box, ' ' + (app.display_name || app.title) + ' — ' + app.version + ' (' + app.package_id + ')');
      item.append(label);
      list.append(item);
    });
    results.replaceChildren(list);
  }

  async function find() {
    const current = ++sequence;
    searchButton.disabled = true;
    status.textContent = 'Loading Chocolatey applications…';
    try {
      const response = await fetch('/api/rmm/chocolatey/packages?q=' + encodeURIComponent(search.value.trim()), { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const data = await response.json();
      if (current !== sequence) return;
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load the app list.');
      render(data.packages);
      status.textContent = (data.packages.length ? 'Tick the applications to push.' : 'No applications found in the downloaded list.')
        + (data.updated_at ? ' App list updated ' + new Date(data.updated_at).toLocaleString() + '.' : ' The app list has not been downloaded yet.');
    } catch (error) {
      if (current === sequence) status.textContent = error.message;
    } finally {
      if (current === sequence) searchButton.disabled = false;
    }
  }

  searchButton.addEventListener('click', find);
  search.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); find(); } });
  form.querySelectorAll('[data-rmm-app-target]').forEach((radio) => radio.addEventListener('change', () => {
    agentsField.hidden = form.querySelector('[data-rmm-app-target]:checked').value !== 'chosen';
  }));

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const ids = Array.from(new Set(Array.from(chosen.keys()).concat(
      extra.value.split(',').map((value) => value.trim().toLowerCase()).filter(Boolean))));
    const allDevices = form.querySelector('[data-rmm-app-target]:checked').value === 'all';
    const agentIds = Array.from(agents.selectedOptions).map((option) => Number(option.value));
    if (!ids.length) { show('Choose at least one application.', 'warning'); return; }
    if (!allDevices && !agentIds.length) { show('Choose at least one device.', 'warning'); return; }
    const mode = form.querySelector('[data-rmm-app-mode]').value;
    const devices = allDevices ? 'every Windows device' : agentIds.length + ' device' + (agentIds.length === 1 ? '' : 's');
    if (!window.confirm((mode === 'install' ? 'Install or update ' : 'Update ') + ids.join(', ') + ' on ' + devices + '?')) return;
    submit.disabled = true;
    try {
      const csrf = document.querySelector('meta[name="csrf-token"]');
      const response = await fetch('/api/rmm/chocolatey/push', {
        method: 'POST', credentials: 'same-origin',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': csrf ? csrf.content : '' },
        body: JSON.stringify({ package_ids: ids, mode: mode, all_devices: allDevices, agent_ids: agentIds }),
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
  find();
})();
