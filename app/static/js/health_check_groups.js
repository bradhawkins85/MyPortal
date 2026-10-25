// Monthly health check group editor: the advanced device rule builder
// (rmm_targeting.js), shown for "Computers matching device rules", with a
// preview of the matching devices, and the rules serialised on submit.
(function () {
  'use strict';

  const form = document.querySelector('[data-hc-group-form]');
  if (!form || !window.MyPortalRmmTargeting) return;
  const mode = form.querySelector('[name="target_mode"]');
  const scope = form.querySelector('[name="scope"]');
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

  const targeting = window.MyPortalRmmTargeting.create({
    host: field,
    api: api,
    getContext: () => ({ scope: scope.value, asset_id: null }),
  });
  try { targeting.setRules(JSON.parse(input.value) || { operator: 'and', children: [] }); }
  catch (error) { targeting.setRules({ operator: 'and', children: [] }); }

  function update() { field.hidden = mode.value !== 'rules'; targeting.invalidate(); }
  mode.addEventListener('change', update);
  scope.addEventListener('change', update);
  update();

  form.addEventListener('submit', (event) => {
    if (mode.value === 'rules' && !targeting.validate(true)) {
      event.preventDefault();
      const invalid = field.querySelector('[aria-invalid="true"]');
      if (invalid) invalid.focus();
      return;
    }
    input.value = JSON.stringify(mode.value === 'rules' ? targeting.getRules() : null);
  });
})();
