(function () {
  'use strict';

  // RMM actions beside each asset (rmm/_macros.html asset_menu): a RustDesk
  // menu (the RustDesk icon) with every RustDesk session, and an RMM menu (the
  // MeshCentral icon) with every other action. Choosing a session runs the activation
  // script on the device, then opens the session for the technician as soon as
  // the script reports back. Progress shows on this page for both. MeshCentral
  // opens straight away in a tab reserved on the click (so pop-up blockers allow
  // it), at the device or the device list filtered to it, while the script runs.
  // Ticket pages redraw their asset list, so clicks are handled on the document.

  const MODES = [['remote', 'Remote desktop'], ['file_transfer', 'File transfer'], ['terminal', 'Terminal'], ['registry', 'Registry']];
  const RUSTDESK_MODES = [['remote', 'Remote desktop'], ['remote_prompt', 'Remote desktop (Prompt)'],
    ['file_transfer', 'File transfer'], ['terminal', 'Terminal']];

  function icon(className, src) {
    const image = document.createElement('img');
    image.className = className;
    image.src = src;
    image.alt = '';
    image.width = 16;
    image.height = 16;
    return image;
  }

  function menuItem(provider, mode, label, assetId, assetName) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'dropdown__item';
    item.setAttribute('role', 'menuitem');
    item.setAttribute('data-rmm-remote', provider);
    item.setAttribute('data-rmm-remote-mode', mode);
    item.setAttribute('data-rmm-remote-asset', assetId);
    item.setAttribute('data-rmm-remote-name', assetName);
    item.textContent = label;
    return item;
  }

  function dropdown(toggleText, toggleLabel, menuLabel, extraClass) {
    const menu = document.createElement('details');
    menu.className = 'rmm-menu' + (extraClass ? ' ' + extraClass : '');
    menu.setAttribute('data-rmm-menu', '');
    const toggle = document.createElement('summary');
    toggle.className = 'button button--ghost button--small rmm-menu__toggle';
    toggle.setAttribute('aria-label', toggleLabel);
    if (toggleText) {
      toggle.append(toggleText + ' ');
    }
    const caret = document.createElement('span');
    caret.setAttribute('aria-hidden', 'true');
    caret.textContent = '▾';
    toggle.append(caret);
    menu.append(toggle);
    const panel = document.createElement('div');
    panel.className = 'dropdown__menu rmm-menu__panel';
    panel.setAttribute('role', 'menu');
    panel.setAttribute('aria-label', menuLabel);
    menu.append(panel);
    return { menu: menu, panel: panel };
  }

  // Same markup as the asset_menu macro, for lists drawn in the browser.
  function assetActions(assetId, assetName, providers) {
    const names = (providers || []).map(function (item) {
      return item && item.provider;
    });
    const wrapper = document.createElement('div');
    wrapper.className = 'rmm-asset-actions';
    if (names.indexOf('rustdesk') !== -1) {
      const rustdesk = dropdown('', 'RustDesk actions for ' + assetName, 'RustDesk actions for ' + assetName, '');
      const toggle = rustdesk.menu.querySelector('summary');
      toggle.classList.add('rmm-menu__toggle--icon');
      toggle.title = 'RustDesk actions';
      toggle.prepend(icon('rmm-rustdesk__icon', '/static/images/rustdesk.svg'), ' ');
      RUSTDESK_MODES.forEach(function (entry) {
        rustdesk.panel.append(menuItem('rustdesk', entry[0], entry[1], String(assetId), assetName));
      });
      wrapper.append(rustdesk.menu);
    }
    if (names.indexOf('meshcentral') !== -1) {
      const rmm = dropdown('', 'RMM actions for ' + assetName, 'RMM actions for ' + assetName, '');
      const toggle = rmm.menu.querySelector('summary');
      toggle.classList.add('rmm-menu__toggle--icon');
      toggle.title = 'MeshCentral and RMM actions';
      toggle.prepend(icon('rmm-meshcentral__icon', '/static/images/meshcentral.svg'), ' ');
      MODES.forEach(function (entry) {
        rmm.panel.append(menuItem('meshcentral', entry[0], 'MeshCentral: ' + entry[1].toLowerCase(), String(assetId), assetName));
      });
      wrapper.append(rmm.menu);
    }
    return wrapper;
  }

  // Same markup as the agent_indicator macro.
  const AGENT_STATES = {
    connected: ['\u2713', 'RMM agent connected'],
    offline: ['O', 'RMM agent installed but not connected'],
    missing: ['\u2717', 'RMM agent not installed'],
  };
  function agentIndicator(state) {
    const key = Object.prototype.hasOwnProperty.call(AGENT_STATES, state) ? state : 'missing';
    const indicator = document.createElement('span');
    indicator.className = 'rmm-agent-indicator rmm-agent-indicator--' + key;
    indicator.setAttribute('role', 'img');
    indicator.setAttribute('aria-label', AGENT_STATES[key][1]);
    indicator.title = AGENT_STATES[key][1];
    indicator.textContent = AGENT_STATES[key][0];
    return indicator;
  }

  window.MyPortalRmm = Object.assign(window.MyPortalRmm || {}, { assetActions: assetActions, agentIndicator: agentIndicator });

  // Menus close when an item is chosen, the click lands elsewhere, or on Escape.
  document.addEventListener('click', function (event) {
    const target = event.target instanceof Element ? event.target : null;
    document.querySelectorAll('details[data-rmm-menu][open]').forEach(function (menu) {
      const item = target ? target.closest('[role="menuitem"]') : null;
      if (!target || !menu.contains(target) || (item && menu.contains(item))) {
        menu.removeAttribute('open');
      }
    });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') {
      return;
    }
    document.querySelectorAll('details[data-rmm-menu][open]').forEach(function (menu) {
      menu.removeAttribute('open');
      const toggle = menu.querySelector('summary');
      if (toggle) {
        toggle.focus();
      }
    });
  });

  const box = document.querySelector('[data-rmm-remote-status]');
  if (!box) {
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
  let busy = false;
  let target = '';
  let meshTab = null;
  let note = '';

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

  function reserveTab() {
    // Opened blank on the click so a blocked pop-up can be detected (window.open
    // returns null for noopener tabs); the opener is dropped before MeshCentral loads.
    const tab = window.open('', '_blank');
    if (!tab) {
      return null;
    }
    try {
      tab.opener = null;
    } catch (error) {
      // Some browsers do not let the opener be cleared; the tab is still usable.
    }
    return tab;
  }

  function closeMeshTab() {
    if (meshTab && !meshTab.closed) {
      meshTab.close();
    }
    meshTab = null;
  }

  function isWebLink(url) {
    return /^https?:\/\//i.test(url);
  }

  // Sends the reserved tab to MeshCentral; true when it went.
  function sendMeshTab(url) {
    const tab = meshTab;
    meshTab = null;
    if (!tab || tab.closed || !isWebLink(url)) {
      if (tab && !tab.closed) {
        tab.close();
      }
      return false;
    }
    tab.location.replace(url);
    return true;
  }

  function setBusy(value) {
    busy = value;
    document.querySelectorAll('[data-rmm-remote]').forEach(function (button) {
      button.disabled = value;
    });
  }

  function finish() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    setBusy(false);
  }

  function sessionName(session) {
    return session.label + (session.mode && session.mode !== 'remote' ? ' ' + (session.mode_label || '').toLowerCase() : '');
  }

  function launch(session) {
    const url = String(session.launch_url || '');
    if (!/^(rustdesk:\/\/|https?:\/\/)/i.test(url)) {
      closeMeshTab();
      show('MyPortal returned a link it cannot open.', true);
      return;
    }
    link.href = url;
    link.textContent = 'Open ' + sessionName(session) + ' again';
    if (url.indexOf('rustdesk:') === 0) {
      // Hands the link to the RustDesk app; the page stays where it is.
      link.removeAttribute('target');
      window.location.href = url;
      show(sessionName(session) + ' is opening' + target + '.', false);
    } else {
      link.setAttribute('target', '_blank');
      link.setAttribute('rel', 'noopener noreferrer');
      if (sendMeshTab(url)) {
        show(sessionName(session) + ' is opening' + target + ' in a new tab.', false);
      } else {
        // MeshCentral is already open in its tab (or the browser blocked it):
        // the link goes straight to the session without moving that tab.
        link.textContent = 'Open ' + sessionName(session);
        show(sessionName(session) + ' is ready' + target + '. Use the link to go straight to it.', false);
      }
    }
    link.hidden = false;
  }

  function render(session, startedAt) {
    if (session.status === 'ready' && session.launch_url) {
      finish();
      launch(session);
      return;
    }
    if (session.status !== 'activating') {
      finish();
      closeMeshTab();
      show(session.error || (session.label + ' could not be switched on.'), true);
      return;
    }
    if (Date.now() - startedAt > GIVE_UP_MS) {
      finish();
      closeMeshTab();
      show('The device has not reported back. Check its script runs.', true);
      return;
    }
    show('Switching on ' + session.label + target + ': ' + (RUN_STATUS[session.run_status] || 'working') + '…' + note, false);
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
      closeMeshTab();
      show(error.message, true);
    }
  }

  document.addEventListener('click', async function (event) {
    const button = event.target instanceof Element ? event.target.closest('[data-rmm-remote]') : null;
    if (!button || busy) {
      return;
    }
    event.preventDefault();
    finish();
    closeMeshTab();
    const provider = button.dataset.rmmRemote;
    if (provider === 'meshcentral') {
      meshTab = reserveTab();
    }
    setBusy(true);
    note = '';
    target = button.dataset.rmmRemoteName ? ' on ' + button.dataset.rmmRemoteName : '';
    link.hidden = true;
    link.removeAttribute('href');
    show('Starting…', false);
    try {
      const data = await api('/api/rmm/assets/' + encodeURIComponent(button.dataset.rmmRemoteAsset) + '/remote-control', {
        method: 'POST',
        body: JSON.stringify({ provider: provider, mode: button.dataset.rmmRemoteMode || 'remote' }),
      });
      const session = data.session || {};
      const openUrl = String(session.open_url || '');
      if (provider === 'meshcentral' && !(session.status === 'ready' && session.launch_url)) {
        // Still switching on: MeshCentral opens now so the device is in view.
        if (sendMeshTab(openUrl)) {
          note = ' MeshCentral is open in a new tab.';
        } else if (isWebLink(openUrl)) {
          link.href = openUrl;
          link.textContent = 'Open MeshCentral';
          link.setAttribute('target', '_blank');
          link.setAttribute('rel', 'noopener noreferrer');
          link.hidden = false;
        }
      }
      render(session, Date.now());
    } catch (error) {
      finish();
      closeMeshTab();
      show(error.message, true);
    }
  });
})();
