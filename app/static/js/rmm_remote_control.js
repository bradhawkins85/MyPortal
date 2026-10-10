(function () {
  'use strict';

  // Asset page: "Connect with RustDesk / MeshCentral" runs the activation script
  // on the device, then offers the launch link once the script reports back.
  const buttons = Array.from(document.querySelectorAll('[data-rmm-remote]'));
  const box = document.querySelector('[data-rmm-remote-status]');
  if (!buttons.length || !box) {
    return;
  }
  const message = box.querySelector('[data-rmm-remote-message]');
  const link = box.querySelector('[data-rmm-remote-link]');
  const POLL_MS = 2000;
  const GIVE_UP_MS = 10 * 60 * 1000;
  const RUN_STATUS = {
    queued: 'waiting for the device to collect the script',
    dispatched: 'the device has collected the script',
    running: 'the script is running',
  };
  let timer = null;

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  async function api(url, options) {
    const init = Object.assign({ credentials: 'same-origin', headers: { Accept: 'application/json' } }, options || {});
    if (init.method && init.method !== 'GET') {
      init.headers['Content-Type'] = 'application/json';
      init.headers['X-CSRF-Token'] = csrfToken();
    }
    const response = await fetch(url, init);
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed (' + response.status + ').');
    }
    return data;
  }

  function show(text, isError) {
    box.hidden = false;
    box.classList.toggle('is-error', Boolean(isError));
    message.textContent = text;
  }

  function setBusy(busy) {
    buttons.forEach(function (button) {
      button.disabled = busy;
    });
  }

  function finish() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    setBusy(false);
  }

  function render(session, startedAt) {
    if (session.status === 'ready' && session.launch_url) {
      finish();
      show(session.label + ' is ready.', false);
      link.href = session.launch_url;
      link.textContent = 'Open ' + session.label;
      // A rustdesk:// link opens the desktop app; MeshCentral opens in a new tab.
      if (session.launch_url.indexOf('rustdesk:') === 0) {
        link.removeAttribute('target');
      } else {
        link.setAttribute('target', '_blank');
      }
      link.hidden = false;
      link.focus();
      return;
    }
    if (session.status !== 'activating') {
      finish();
      show(session.error || (session.label + ' could not be switched on.'), true);
      return;
    }
    if (Date.now() - startedAt > GIVE_UP_MS) {
      finish();
      show('The device has not reported back. Check the run in the table below.', true);
      return;
    }
    show('Switching on ' + session.label + ': ' + (RUN_STATUS[session.run_status] || 'working') + '…', false);
    timer = setTimeout(function () {
      poll(session.id, startedAt);
    }, POLL_MS);
  }

  async function poll(sessionId, startedAt) {
    try {
      const data = await api('/api/rmm/remote-sessions/' + encodeURIComponent(sessionId));
      render(data.session, startedAt);
    } catch (error) {
      finish();
      show(error.message, true);
    }
  }

  buttons.forEach(function (button) {
    button.addEventListener('click', async function () {
      finish();
      setBusy(true);
      link.hidden = true;
      link.removeAttribute('href');
      show('Starting…', false);
      try {
        const data = await api('/api/rmm/assets/' + encodeURIComponent(button.dataset.rmmRemoteAsset) + '/remote-control', {
          method: 'POST',
          body: JSON.stringify({ provider: button.dataset.rmmRemote }),
        });
        render(data.session, Date.now());
      } catch (error) {
        finish();
        show(error.message, true);
      }
    });
  });
})();
