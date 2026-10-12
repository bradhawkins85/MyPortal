(function () {
  'use strict';

  // RMM actions beside each asset (rmm/_macros.html asset_menu): a RustDesk button
  // with a menu for remote desktop, file transfer and terminal sessions, and an
  // "RMM" menu with every other action. Choosing a session runs the activation
  // script on the device, then opens the session for the technician as soon as
  // the script reports back. Ticket pages redraw their asset list, so clicks are
  // handled on the document.

  const MODES = [['remote', 'Remote desktop'], ['file_transfer', 'File transfer'], ['terminal', 'Terminal']];

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
      const split = document.createElement('div');
      split.className = 'rmm-split';
      const main = menuItem('rustdesk', 'remote', 'RustDesk', String(assetId), assetName);
      main.className = 'button button--ghost button--small rmm-split__main';
      main.removeAttribute('role');
      main.title = 'Start a RustDesk remote session';
      split.append(main);
      const more = dropdown('', 'More RustDesk sessions for ' + assetName, 'RustDesk sessions for ' + assetName, 'rmm-split__more');
      MODES.forEach(function (entry) {
        more.panel.append(menuItem('rustdesk', entry[0], entry[1], String(assetId), assetName));
      });
      split.append(more.menu);
      wrapper.append(split);
    }
    if (names.indexOf('meshcentral') !== -1) {
      const rmm = dropdown('RMM', 'RMM actions for ' + assetName, 'RMM actions for ' + assetName, '');
      rmm.menu.querySelector('summary').title = 'RMM actions';
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
  // MeshCentral opens in a tab reserved on the click, so pop-up blockers allow it.
  let pendingTab = null;

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

  function setBusy(value) {
    busy = value;
    document.querySelectorAll('[data-rmm-remote]').forEach(function (button) {
      button.disabled = value;
    });
  }

  function closePendingTab() {
    if (pendingTab && !pendingTab.closed) {
      pendingTab.close();
    }
    pendingTab = null;
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
      closePendingTab();
      show('MyPortal returned a link it cannot open.', true);
      return;
    }
    link.href = url;
    link.textContent = 'Open ' + sessionName(session) + ' again';
    if (url.indexOf('rustdesk:') === 0) {
      // Hands the link to the RustDesk app; the page stays where it is.
      link.removeAttribute('target');
      closePendingTab();
      window.location.href = url;
    } else {
      link.setAttribute('target', '_blank');
      if (pendingTab && !pendingTab.closed) {
        pendingTab.location.href = url;
        pendingTab = null;
      } else {
        link.textContent = 'Open ' + sessionName(session);
      }
    }
    link.hidden = false;
  }

  function render(session, startedAt) {
    if (session.status === 'ready' && session.launch_url) {
      finish();
      show(sessionName(session) + ' is opening' + target + '.', false);
      launch(session);
      return;
    }
    if (session.status !== 'activating') {
      finish();
      closePendingTab();
      show(session.error || (session.label + ' could not be switched on.'), true);
      return;
    }
    if (Date.now() - startedAt > GIVE_UP_MS) {
      finish();
      closePendingTab();
      show('The device has not reported back. Check its script runs.', true);
      return;
    }
    show('Switching on ' + session.label + target + ': ' + (RUN_STATUS[session.run_status] || 'working') + '…', false);
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
      closePendingTab();
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
    closePendingTab();
    const provider = button.dataset.rmmRemote;
    if (provider !== 'rustdesk') {
      pendingTab = window.open('about:blank', '_blank');
      if (pendingTab) {
        pendingTab.opener = null;
      }
    }
    setBusy(true);
    target = button.dataset.rmmRemoteName ? ' on ' + button.dataset.rmmRemoteName : '';
    link.hidden = true;
    link.removeAttribute('href');
    show('Starting…', false);
    try {
      const data = await api('/api/rmm/assets/' + encodeURIComponent(button.dataset.rmmRemoteAsset) + '/remote-control', {
        method: 'POST',
        body: JSON.stringify({ provider: provider, mode: button.dataset.rmmRemoteMode || 'remote' }),
      });
      render(data.session, Date.now());
    } catch (error) {
      finish();
      closePendingTab();
      show(error.message, true);
    }
  });
})();
