(function () {
  'use strict';
  const form = document.querySelector('[data-rmm-patch-form]');
  if (!form) return;
  const search = form.querySelector('[data-choco-search]');
  const results = form.querySelector('[data-choco-results]');
  const status = form.querySelector('[data-choco-status]');
  const searchButton = form.querySelector('[data-choco-search-button]');
  let packages = [];
  let searchSequence = 0;
  async function findPackages() {
    const sequence = ++searchSequence;
    searchButton.disabled = true;
    status.textContent = 'Loading Chocolatey applications…';
    try {
      const data = await api('/api/rmm/chocolatey/packages?q=' + encodeURIComponent(search.value.trim()));
      if (sequence !== searchSequence) return;
      packages = data.packages;
      results.replaceChildren(new Option('Choose an application', ''));
      packages.forEach((app, index) => results.add(new Option(app.title + ' — ' + app.version + ' (' + app.package_id + ')', String(index))));
      status.textContent = (packages.length ? 'Select an application below.' : 'No applications found.')
        + (data.updated_at ? ' App list updated ' + new Date(data.updated_at).toLocaleString() + '.' : '');
    } catch (error) { if (sequence === searchSequence) status.textContent = error.message; }
    finally { if (sequence === searchSequence) searchButton.disabled = false; }
  }
  searchButton.addEventListener('click', findPackages);
  search.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); findPackages(); } });
  results.addEventListener('change', () => {
    const app = results.value === '' ? null : packages[Number(results.value)];
    if (!app) return;
    form.querySelector('[name="package_id"]').value = app.package_id;
    form.querySelector('[name="display_name"]').value = app.title;
  });
  const mode = form.querySelector('[name="target_mode"]');
  const input = form.querySelector('[name="target_rules"]');
  const field = form.querySelector('[data-rmm-rules-field]');
  async function api(url, options) {
    const init = Object.assign({ credentials: 'same-origin', headers: { Accept: 'application/json' } }, options || {});
    if (init.body) {
      init.body = JSON.stringify(init.body);
      init.headers['Content-Type'] = 'application/json';
      const csrf = document.querySelector('meta[name="csrf-token"]');
      init.headers['X-CSRF-Token'] = csrf ? csrf.content : '';
    }
    const response = await fetch(url, init);
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : 'Could not preview device rules.');
      error.data = data;
      throw error;
    }
    return data;
  }
  const targeting = window.MyPortalRmmTargeting.create({ host: field, api: api,
    previewUrl: '/api/rmm/patching/targeting/preview',
    getContext: () => ({ scope: form.querySelector('[name="scope"]').value,
      asset_id: null }),
  });
  try { targeting.setRules(JSON.parse(input.value) || { operator: 'and', children: [] }); }
  catch (error) { targeting.setRules({ operator: 'and', children: [] }); }
  function update() { field.hidden = mode.value !== 'rules'; targeting.invalidate(); }
  mode.addEventListener('change', update);
  form.querySelector('[name="scope"]').addEventListener('change', update);

  form.addEventListener('submit', (event) => {
    if (mode.value === 'rules' && !targeting.validate(true)) {
      event.preventDefault();
      const invalid = field.querySelector('[aria-invalid="true"]');
      if (invalid) invalid.focus();
      return;
    }
    input.value = JSON.stringify(mode.value === 'rules' ? targeting.getRules() : null);
  });
  const kind = form.querySelector('[name="kind"]');
  function updateKind() {
    form.querySelector('[data-patch-app]').hidden = kind.value !== 'chocolatey';
    form.querySelector('[data-patch-windows]').hidden = kind.value !== 'windows';
  }
  let catalogLoaded = false;
  kind.addEventListener('change', () => { updateKind(); if (kind.value === 'chocolatey' && !catalogLoaded) { catalogLoaded = true; findPackages(); } });
  if (kind.value === 'chocolatey') { catalogLoaded = true; findPackages(); }
  updateKind();
  update();
})();
