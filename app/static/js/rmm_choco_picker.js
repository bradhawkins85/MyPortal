(function () {
  'use strict';

  // Search the downloaded Chocolatey app list and choose apps from it.
  // Shared by the asset page's install modal and Script automation.
  function create(options) {
    const search = options.search;
    const button = options.button;
    const status = options.status;
    const results = options.results;
    const multiple = Boolean(options.multiple);
    const name = options.name;
    // Chosen apps survive new searches.
    const chosen = new Map();
    let sequence = 0;
    let loaded = false;

    function render(packages) {
      const seen = new Set();
      const items = packages.filter((app) => !seen.has(app.package_id) && seen.add(app.package_id));
      chosen.forEach((app, id) => { if (!seen.has(id)) { items.unshift(app); seen.add(id); } });
      const list = document.createElement('ul');
      list.className = 'rmm-app-push__list';
      items.forEach((app) => {
        const item = document.createElement('li');
        const label = document.createElement('label');
        const input = document.createElement('input');
        input.type = multiple ? 'checkbox' : 'radio';
        input.name = name;
        input.value = app.package_id;
        input.checked = chosen.has(app.package_id);
        input.addEventListener('change', () => {
          if (!multiple) chosen.clear();
          if (input.checked) chosen.set(app.package_id, app); else chosen.delete(app.package_id);
          if (options.onChange) options.onChange();
        });
        label.append(input, ' ' + (app.display_name || app.title) + ' — ' + app.version + ' (' + app.package_id + ')');
        item.append(label);
        list.append(item);
      });
      results.replaceChildren(list);
    }

    async function find() {
      const current = ++sequence;
      loaded = true;
      button.disabled = true;
      status.textContent = 'Loading Chocolatey applications…';
      try {
        const response = await fetch('/api/rmm/chocolatey/packages?q=' + encodeURIComponent(search.value.trim()), { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        const data = await response.json();
        if (current !== sequence) return;
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load the app list.');
        render(data.packages);
        status.textContent = (data.packages.length ? (multiple ? 'Tick the applications.' : 'Choose an application.') : 'No applications found in the downloaded list.')
          + (data.updated_at ? ' App list updated ' + new Date(data.updated_at).toLocaleString() + '.' : ' The app list has not been downloaded yet.');
      } catch (error) {
        if (current === sequence) status.textContent = error.message;
      } finally {
        if (current === sequence) button.disabled = false;
      }
    }

    button.addEventListener('click', find);
    search.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); find(); } });

    return {
      find: find,
      ensureLoaded: () => { if (!loaded) find(); },
      selected: () => Array.from(chosen.keys()),
      // Pre-select package IDs (for example a saved step's); unknown IDs show by ID.
      select: (ids) => {
        chosen.clear();
        ids.forEach((id) => chosen.set(id, { package_id: id, title: id, version: 'saved' }));
        render([]);
      },
      clear: () => { chosen.clear(); results.querySelectorAll('input').forEach((input) => { input.checked = false; }); },
    };
  }

  window.MyPortalChocoPicker = { create: create };
})();
