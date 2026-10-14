(function () {
  'use strict';
  const form = document.querySelector('[data-rmm-patch-form]');
  if (!form) return;
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
  kind.addEventListener('change', updateKind);
  updateKind();
  update();
})();
