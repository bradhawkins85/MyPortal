(function () {
  'use strict';

  document.querySelectorAll('[data-copy-target]').forEach((button) => {
    button.addEventListener('click', async () => {
      const input = document.getElementById(button.getAttribute('data-copy-target'));
      if (!input) return;
      const label = button.textContent;
      try {
        await navigator.clipboard.writeText(input.value);
        button.textContent = 'Copied';
      } catch (error) {
        input.select();
        button.textContent = 'Press Ctrl+C';
      }
      window.setTimeout(() => {
        button.textContent = label;
      }, 2000);
    });
  });

  const provisioningEnabled = document.querySelector('[data-provisioning-enabled]');
  const provisioningFields = document.querySelector('[data-provisioning-fields]');
  if (provisioningEnabled && provisioningFields) {
    const adminPassword = provisioningFields.querySelector('[data-provisioning-admin-password]');
    const joinMode = provisioningFields.querySelector('[data-provisioning-join-mode]');
    const joinFields = provisioningFields.querySelectorAll('[data-provisioning-join-fields]');
    const updateProvisioningFields = () => {
      const enabled = provisioningEnabled.checked;
      provisioningFields.disabled = !enabled;
      provisioningFields.hidden = !enabled;
      if (adminPassword) adminPassword.required = enabled;
      joinFields.forEach((fields) => {
        const active = enabled && joinMode && joinMode.value === fields.dataset.provisioningJoinFields;
        fields.disabled = !active;
        fields.hidden = !active;
        fields.querySelectorAll('[data-join-required]').forEach((input) => {
          input.required = Boolean(active);
        });
        if (!active) {
          fields.querySelectorAll('[data-provisioning-secret]').forEach((input) => {
            input.value = '';
          });
        }
      });
      if (!enabled) {
        provisioningFields.querySelectorAll('input[type="password"]').forEach((input) => {
          input.value = '';
        });
      }
    };
    provisioningEnabled.addEventListener('change', updateProvisioningFields);
    if (joinMode) joinMode.addEventListener('change', updateProvisioningFields);
    updateProvisioningFields();
  }

  const clearPackagePasswords = [];
  document.querySelectorAll('[data-provisioning-package]').forEach((details) => {
    const button = details.querySelector('[data-provisioning-password-url]');
    const panel = details.querySelector('[data-provisioning-password-panel]');
    const input = details.querySelector('[data-provisioning-password-value]');
    const status = details.querySelector('[data-provisioning-password-status]');
    if (!button || !panel || !input || !status) return;
    let controller = null;

    const clearPassword = () => {
      if (controller) controller.abort();
      controller = null;
      input.value = '';
      panel.hidden = true;
      button.disabled = false;
      button.textContent = 'Show package password';
      button.setAttribute('aria-expanded', 'false');
      status.textContent = '';
    };
    clearPackagePasswords.push(clearPassword);
    details.addEventListener('toggle', () => {
      if (!details.open) clearPassword();
    });
    button.addEventListener('click', async () => {
      if (!panel.hidden) {
        clearPassword();
        return;
      }
      controller = new AbortController();
      const requestController = controller;
      button.disabled = true;
      button.textContent = 'Loading…';
      status.textContent = '';
      try {
        const response = await fetch(button.dataset.provisioningPasswordUrl, {
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
          signal: requestController.signal,
        });
        if (!response.ok) throw new Error('Package password unavailable');
        const data = await response.json();
        if (requestController.signal.aborted || !details.open) return;
        if (typeof data.password !== 'string' || !data.password) {
          throw new Error('Package password unavailable');
        }
        input.value = data.password;
        panel.hidden = false;
        button.textContent = 'Hide package password';
        button.setAttribute('aria-expanded', 'true');
        status.textContent = 'Share this unlock password separately from the download link.';
      } catch (error) {
        if (error.name === 'AbortError') return;
        clearPassword();
        status.textContent = 'The package password could not be loaded. Check that the build is ready and you are still signed in, then try again.';
      } finally {
        if (controller === requestController) {
          controller = null;
          button.disabled = false;
          if (panel.hidden) button.textContent = 'Show package password';
        }
      }
    });
  });
  window.addEventListener('pagehide', () => clearPackagePasswords.forEach((clear) => clear()));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearPackagePasswords.forEach((clear) => clear());
  });

  const search = document.querySelector('[data-deploy-search]');
  const rows = Array.from(document.querySelectorAll('[data-deploy-row]'));
  const noResults = document.querySelector('[data-deploy-no-results]');
  if (search) {
    search.addEventListener('input', () => {
      const query = search.value.trim().toLowerCase();
      let visible = 0;
      rows.forEach((row) => {
        const match = !query || row.textContent.toLowerCase().includes(query);
        row.hidden = !match;
        if (match) visible += 1;
      });
      if (noResults) noResults.hidden = visible > 0 || rows.length === 0;
    });
  }
})();
