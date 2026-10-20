// Tray menu configuration form: advanced device rules (the RMM check rule
// builder) so matching devices in the scope get this menu.
(function () {
  'use strict';

  const section = document.querySelector('[data-tray-config-targeting]');
  const form = document.getElementById('tray-config-form');
  if (!section || !form) return;
  const rulesInput = section.querySelector('[data-tray-target-rules]');
  const rulesField = section.querySelector('[data-rmm-rules-field]');
  const priorityField = section.querySelector('[data-tray-target-priority]');
  const scopeSelect = document.getElementById('config-scope');
  const scopeRef = document.getElementById('config-scope-ref');
  let fields = [];
  try {
    fields = JSON.parse(document.getElementById('tray-config-targeting-config').textContent || '{}').fields || [];
  } catch (error) {
    // The editor retries loading the field catalogue through the API.
  }

  const mode = () => {
    const input = section.querySelector('[data-tray-target-mode]:checked');
    return input ? input.value : 'all';
  };

  async function api(url, options) {
    const token = document.querySelector('meta[name="csrf-token"]');
    const init = Object.assign({ credentials: 'same-origin' }, options || {});
    init.headers = { Accept: 'application/json' };
    if (init.body) {
      init.body = JSON.stringify(init.body);
      init.headers['Content-Type'] = 'application/json';
    }
    if (init.method && init.method !== 'GET') {
      init.headers['X-CSRF-Token'] = token ? token.getAttribute('content') : (form.querySelector('[name="csrf_token"]') || {}).value || '';
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
    previewUrl: '/api/tray/admin/configs/targeting/preview',
    fieldsUrl: '/api/tray/admin/configs/targeting/fields',
    getContext: () => ({
      scope: scopeSelect ? scopeSelect.value : 'global',
      scope_ref_id: scopeRef && scopeRef.value ? Number(scopeRef.value) : null,
    }),
  }) : null;
  try {
    if (targeting) targeting.setRules(JSON.parse(rulesInput.value || 'null'));
  } catch (error) {
    if (targeting) targeting.setRules(null);
  }
  const initialValue = rulesInput.value;

  function writeRules() {
    const before = rulesInput.value;
    rulesInput.value = mode() === 'rules' && targeting ? JSON.stringify(targeting.getRules()) : '';
    if (rulesInput.value !== before) rulesInput.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function updateMode() {
    const rules = mode() === 'rules';
    rulesField.hidden = !rules;
    if (priorityField) priorityField.hidden = !rules;
    writeRules();
  }

  section.addEventListener('change', (event) => {
    if (event.target.matches('[data-tray-target-mode]')) updateMode();
  });
  [scopeSelect, scopeRef].forEach((control) => {
    if (control && targeting) control.addEventListener('change', () => targeting.invalidate());
  });
  rulesField.addEventListener('rmm:rules-change', writeRules);

  form.addEventListener('submit', (event) => {
    if (mode() === 'rules' && (!targeting || !targeting.validate(true))) {
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

  rulesField.hidden = mode() !== 'rules';
  // Keep the saved value until the rules change, so opening the page is not an edit.
  rulesInput.value = initialValue;
})();
