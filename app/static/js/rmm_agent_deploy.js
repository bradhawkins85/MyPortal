// Install, repair and upgrade actions push the RMM agent through the device's tray app.
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
    const upgrade = button.hasAttribute('data-rmm-agent-upgrade');
    const kind = upgrade ? 'upgrade' : (repair ? 'repair' : 'install');
    const assetId = button.getAttribute('data-rmm-agent-' + kind);
    const name = button.getAttribute('data-rmm-agent-name') || 'this device';
    const prompts = {
      install: 'Install the RMM agent on ' + name + '?',
      repair: 'Reinstall and reconnect the RMM agent on ' + name + '? This also installs it if missing.',
      upgrade: 'Upgrade the agents on ' + name + ' to the latest release? Nothing changes if they are already up to date.',
    };
    if (button.disabled || !assetId || !window.confirm(prompts[kind])) {
      return;
    }
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = 'Sending…';
    button.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch('/api/rmm/assets/' + encodeURIComponent(assetId) + '/agent-' + kind, {
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
      if (data.status === 'current') {
        button.textContent = 'Up to date';
        notify(data.message || 'The agents on this device are already up to date.', 'info');
        return;
      }
      if (data.status === 'installed') {
        button.textContent = 'Already installed';
        notify(data.message || 'The RMM agent is already installed on this device.', 'info');
        return;
      }
      button.textContent = { install: 'Install queued', repair: 'Repair queued', upgrade: 'Upgrade queued' }[kind];
      notify(data.message || 'The RMM agent ' + kind + ' was queued.', 'success');
    } catch (error) {
      notify(error.message || 'Could not push the RMM agent.', 'error');
      button.disabled = false;
      button.textContent = originalLabel;
    } finally {
      button.removeAttribute('aria-busy');
    }
  }

  document.addEventListener('click', function (event) {
    const button = event.target.closest('[data-rmm-agent-install], [data-rmm-agent-repair], [data-rmm-agent-upgrade]');
    if (button) {
      event.preventDefault();
      push(button);
    }
  });
})();
