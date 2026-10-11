// "Install RMM agent" buttons on the Assets list and asset page: push the
// RMM agent to a device through its tray app.
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
    const assetId = button.getAttribute('data-rmm-agent-install');
    const name = button.getAttribute('data-rmm-agent-name') || 'this device';
    if (!assetId || !window.confirm('Install the RMM agent on ' + name + '?')) {
      return;
    }
    button.disabled = true;
    try {
      const response = await fetch('/api/rmm/assets/' + encodeURIComponent(assetId) + '/agent-install', {
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
      notify(data.message || 'The RMM agent install was queued.', 'success');
    } catch (error) {
      notify(error.message || 'Could not push the RMM agent.', 'error');
      button.disabled = false;
    }
  }

  document.addEventListener('click', function (event) {
    const button = event.target.closest('[data-rmm-agent-install]');
    if (button) {
      event.preventDefault();
      push(button);
    }
  });
})();
