// RMM checks form: check settings and advanced device targeting.
(function () {
  'use strict';

  const form = document.querySelector('[data-rmm-check-form]');
  if (!form) return;
  const select = form.querySelector('[data-rmm-check-type]');
  const durationLabel = form.querySelector('[data-rmm-check-duration-label]');
  const scope = form.querySelector('[name="scope"]');
  const asset = form.querySelector('[name="asset_id"]');
  const targetMode = form.querySelector('[data-rmm-check-target-mode]');
  const rulesInput = form.querySelector('[data-rmm-check-target-rules]');
  const rulesField = form.querySelector('[data-rmm-rules-field]');
  let fields = [];
  try {
    fields = JSON.parse(document.getElementById('rmm-check-targeting-config').textContent || '{}').fields || [];
  } catch (error) {
    // The editor retries loading the field catalogue through the API.
  }

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
    getContext: () => ({ scope: scope.value, asset_id: scope.value === 'asset' && asset.value ? Number(asset.value) : null }),
  }) : null;
  try {
    if (targeting) targeting.setRules(JSON.parse(rulesInput.value || 'null'));
  } catch (error) {
    if (targeting) targeting.setRules(null);
  }

  function writeRules() {
    rulesInput.value = targetMode.value === 'rules' && targeting ? JSON.stringify(targeting.getRules()) : '';
  }

  function updateSelection() {
    const oneDevice = scope.value === 'asset';
    asset.closest('.form-field').hidden = !oneDevice;
    asset.disabled = !oneDevice;
    rulesField.hidden = targetMode.value !== 'rules';
    writeRules();
  }

  function update() {
    const type = select.value;
    form.querySelectorAll('[data-rmm-check-fields]').forEach((fieldset) => {
      const active = fieldset.getAttribute('data-rmm-check-fields') === type;
      fieldset.hidden = !active;
      fieldset.disabled = !active;
    });
    if (durationLabel) durationLabel.textContent = type === 'offline' ? 'Not checked in for' : 'For at least';
  }

  select.addEventListener('change', update);
  targetMode.addEventListener('change', updateSelection);
  scope.addEventListener('change', () => {
    updateSelection();
    if (targeting) targeting.invalidate();
  });
  asset.addEventListener('change', () => { if (targeting) targeting.invalidate(); });
  rulesField.addEventListener('rmm:rules-change', writeRules);
  form.addEventListener('submit', (event) => {
    if (targetMode.value === 'rules' && (!targeting || !targeting.validate(true))) {
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
  });
  update();
  updateSelection();
})();
