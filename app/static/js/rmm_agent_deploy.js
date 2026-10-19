// Install and repair actions push the RMM agent through the device's tray app.
(function () {
  'use strict';

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function notify(text, variant) {
    if (window.__portalToast && typeof window.__portalToast.show === 'function') {
      window.__portalToast.show(text, { variant: variant });
    } else {
      window.alert(text);
    }
  }

  async function push(button) {
    const repair = button.hasAttribute('data-rmm-agent-repair');
    const assetId = button.getAttribute(repair ? 'data-rmm-agent-repair' : 'data-rmm-agent-install');
    const name = button.getAttribute('data-rmm-agent-name') || 'this device';
    if (button.disabled || !assetId || !window.confirm(repair
      ? 'Reinstall and reconnect the RMM agent on ' + name + '? This also installs it if missing.'
      : 'Install the RMM agent on ' + name + '?')) {
      return;
    }
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = 'Sending…';
    button.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch('/api/rmm/assets/' + encodeURIComponent(assetId) + (repair ? '/agent-repair' : '/agent-install'), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
        body: '{}',
      });
      let data = {};
      try {
        data = await response.json();
      } catch (error) {
        data = {};
      }
      if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed (' + response.status + ').');
      }
      if (data.status === 'installed') {
        button.textContent = 'Already installed';
        notify(data.message || 'The RMM agent is already installed on this device.', 'info');
        return;
      }
      button.textContent = repair ? 'Repair queued' : 'Install queued';
      notify(data.message || (repair ? 'The RMM agent repair was queued.' : 'The RMM agent install was queued.'), 'success');
    } catch (error) {
      notify(error.message || 'Could not push the RMM agent.', 'error');
      button.disabled = false;
      button.textContent = originalLabel;
    } finally {
      button.removeAttribute('aria-busy');
    }
  }

  document.addEventListener('click', function (event) {
    const button = event.target.closest('[data-rmm-agent-install], [data-rmm-agent-repair]');
    if (button) {
      event.preventDefault();
      push(button);
    }
  });
})();
