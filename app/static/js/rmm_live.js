/* RMM live tools: terminal, task manager, service manager, file browser,
   registry editor, disk usage scanner and real-time CPU/memory. The page
   starts a session over an authenticated POST, then opens a WebSocket to
   MyPortal which relays JSON to the device's RMM agent. */
(function () {
  'use strict';

  const root = document.querySelector('[data-rmm-live]');
  if (!root) return;

  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  const assetId = root.dataset.assetId;
  const agentOS = (root.dataset.agentOs || '').toLowerCase();
  const el = (sel, base) => (base || root).querySelector(sel);
  const els = (sel, base) => Array.from((base || root).querySelectorAll(sel));

  const connectionPill = el('[data-live-connection]');
  const errorBox = el('[data-live-error]');
  const workspace = el('[data-live-workspace]');

  function fmtBytes(bytes) {
    if (bytes === null || bytes === undefined || isNaN(bytes)) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let value = Number(bytes), unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
    return (unit === 0 ? value : value.toFixed(value < 10 ? 2 : 1)) + ' ' + units[unit];
  }
  function fmtDuration(seconds) {
    if (!seconds) return '—';
    const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
    if (d) return d + 'd ' + h + 'h';
    if (h) return h + 'h ' + m + 'm';
    return m + 'm';
  }
  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = !message;
  }

  /* ----- WebSocket RPC client ----- */
  function createClient() {
    let socket = null;
    let seq = 0;
    const pending = new Map();
    const listeners = new Map();
    const state = { connected: false };

    function on(event, handler) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(handler);
      return () => listeners.get(event).delete(handler);
    }
    function emit(event, data) {
      (listeners.get(event) || []).forEach((handler) => {
        try { handler(data); } catch (err) { /* keep other listeners alive */ }
      });
    }
    function request(op, args) {
      return new Promise((resolve, reject) => {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
          reject(new Error('Not connected to the device.'));
          return;
        }
        seq += 1;
        const id = 'c' + seq;
        pending.set(id, { resolve, reject });
        socket.send(JSON.stringify({ id: id, op: op, args: args || {} }));
      });
    }
    function send(op, args) {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ op: op, args: args || {} }));
      }
    }
    async function connect() {
      const resp = await fetch('/api/rmm/assets/' + assetId + '/live-session', {
        method: 'POST', headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json' },
      });
      if (!resp.ok) {
        let detail = 'Could not start a live session.';
        try { detail = (await resp.json()).detail || detail; } catch (e) { /* ignore */ }
        throw new Error(detail);
      }
      const info = await resp.json();
      state.sessionId = info.session_id;
      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const url = scheme + '://' + window.location.host + info.ws_url + '?token=' + encodeURIComponent(info.token);
      await new Promise((resolve, reject) => {
        socket = new WebSocket(url);
        socket.onopen = () => { state.connected = true; resolve(); };
        socket.onerror = () => { if (!state.connected) reject(new Error('The live session connection failed.')); };
        socket.onclose = () => {
          state.connected = false;
          pending.forEach((p) => p.reject(new Error('The session closed.')));
          pending.clear();
          emit('close');
        };
        socket.onmessage = (event) => {
          let msg;
          try { msg = JSON.parse(event.data); } catch (e) { return; }
          if (msg.id && pending.has(msg.id)) {
            const p = pending.get(msg.id);
            pending.delete(msg.id);
            if (msg.ok) p.resolve(msg.result); else p.reject(new Error(msg.error || 'The device reported an error.'));
          } else if (msg.event) {
            emit(msg.event, msg.data);
          }
        };
      });
    }
    function close() {
      if (state.sessionId) {
        navigator.sendBeacon && navigator.sendBeacon(
          '/api/rmm/assets/' + assetId + '/live-session/' + state.sessionId + '/close',
          new Blob([JSON.stringify({ _csrf: csrf })], { type: 'application/json' })
        );
        fetch('/api/rmm/assets/' + assetId + '/live-session/' + state.sessionId + '/close',
          { method: 'POST', headers: { 'X-CSRF-Token': csrf }, keepalive: true }).catch(() => {});
      }
      if (socket) { try { socket.close(); } catch (e) { /* ignore */ } }
    }
    return { on, request, send, connect, close, state };
  }

  const client = createClient();
  let hello = null;
  const views = [];

  /* ----- Connect / disconnect ----- */
  const connectBtn = el('[data-live-connect]');
  const disconnectBtn = el('[data-live-disconnect]');
  const canStart = root.dataset.agentState === 'connected' && root.dataset.liveSupported === 'true';

  function setConnection(stateLabel, variant) {
    if (connectionPill) {
      connectionPill.textContent = stateLabel;
      connectionPill.className = 'status status--' + variant;
    }
  }

  async function connect() {
    showError('');
    setConnection('Connecting…', 'warning');
    if (connectBtn) connectBtn.disabled = true;
    try {
      await client.connect();
      hello = await client.request('hello');
      setConnection('Connected', 'success');
      if (connectBtn) connectBtn.hidden = true;
      if (disconnectBtn) disconnectBtn.hidden = false;
      el('[data-live-os]').textContent = hello.os + ' · ' + hello.arch + ' · agent ' + hello.version;
      els('[data-live-windows-only]').forEach((node) => { node.hidden = (hello.os !== 'windows'); });
      views.forEach((view) => view.onConnect && view.onConnect(hello));
      activateTab(currentTab);
    } catch (err) {
      setConnection('Not connected', 'neutral');
      showError(err.message);
      if (connectBtn) { connectBtn.hidden = false; connectBtn.disabled = false; }
    }
  }
  function disconnect() {
    client.close();
  }
  client.on('close', () => {
    setConnection('Not connected', 'neutral');
    if (connectBtn) { connectBtn.hidden = false; connectBtn.disabled = false; }
    if (disconnectBtn) disconnectBtn.hidden = true;
    views.forEach((view) => view.onDisconnect && view.onDisconnect());
  });

  if (connectBtn && canStart) {
    connectBtn.hidden = false;
    connectBtn.addEventListener('click', connect);
  }
  if (disconnectBtn) disconnectBtn.addEventListener('click', disconnect);
  window.addEventListener('beforeunload', () => client.close());

  /* ----- Tabs ----- */
  let currentTab = 'overview';
  function activateTab(name) {
    currentTab = name;
    els('[data-live-tab]').forEach((tab) => {
      const active = tab.dataset.liveTab === name;
      tab.setAttribute('aria-current', active ? 'true' : 'false');
    });
    els('[data-live-panel]').forEach((panel) => { panel.hidden = panel.dataset.livePanel !== name; });
    views.forEach((view) => { if (view.tab === name && view.onShow) view.onShow(); });
  }
  els('[data-live-tab]').forEach((tab) => tab.addEventListener('click', () => activateTab(tab.dataset.liveTab)));

  /* ----- Prompt modal (shared by file/registry editors) ----- */
  const promptModal = el('#live-prompt');
  function openPrompt(options) {
    return new Promise((resolve) => {
      el('[data-live-prompt-title]', promptModal).textContent = options.title || 'Edit';
      el('[data-live-prompt-message]', promptModal).textContent = options.message || '';
      const fields = el('[data-live-prompt-fields]', promptModal);
      fields.innerHTML = '';
      const inputs = {};
      (options.fields || []).forEach((field) => {
        const wrap = document.createElement('label');
        wrap.className = 'rmm-live__field';
        wrap.textContent = field.label;
        let input;
        if (field.type === 'select') {
          input = document.createElement('select');
          (field.options || []).forEach((opt) => {
            const option = document.createElement('option');
            option.value = opt.value; option.textContent = opt.label;
            if (opt.value === field.value) option.selected = true;
            input.appendChild(option);
          });
        } else if (field.type === 'textarea') {
          input = document.createElement('textarea');
          input.value = field.value || '';
        } else {
          input = document.createElement('input');
          input.type = field.type || 'text';
          input.value = field.value || '';
        }
        input.className = 'form-input';
        wrap.appendChild(input);
        fields.appendChild(wrap);
        inputs[field.name] = input;
      });
      const errorNode = el('[data-live-prompt-error]', promptModal);
      errorNode.hidden = true;
      promptModal.hidden = false;
      const okBtn = el('[data-live-prompt-ok]', promptModal);
      const cancels = els('[data-live-prompt-cancel]', promptModal);
      const first = Object.values(inputs)[0];
      if (first) first.focus();
      function cleanup(result) {
        promptModal.hidden = true;
        okBtn.onclick = null;
        cancels.forEach((btn) => (btn.onclick = null));
        resolve(result);
      }
      okBtn.onclick = () => {
        const values = {};
        Object.keys(inputs).forEach((key) => (values[key] = inputs[key].value));
        cleanup(values);
      };
      cancels.forEach((btn) => (btn.onclick = () => cleanup(null)));
    });
  }

  function confirmAction(message) { return window.confirm(message); }

  /* ----- Register each tool view ----- */
  initOverview(client, views, { el, els, fmtBytes, fmtDuration });
  initTerminal(client, views, { el, els, showError });
  initProcesses(client, views, { el, els, fmtBytes, confirmAction });
  initServices(client, views, { el, els, openPrompt, confirmAction });
  initFiles(client, views, { el, els, fmtBytes, openPrompt, confirmAction, csrf, agentOS });
  initDiskUsage(client, views, { el, els, fmtBytes });
  initRegistry(client, views, { el, els, openPrompt, confirmAction });

  if (!canStart && workspace) workspace.hidden = true;

  /* =====================================================================
     Overview — real-time CPU, memory and network
     ===================================================================== */
  function initOverview(client, views, h) {
    const cpuValue = h.el('[data-live-cpu-value]');
    const cpuGauge = h.el('[data-live-cpu-gauge]');
    const cores = h.el('[data-live-cores]');
    const memValue = h.el('[data-live-mem-value]');
    const memGauge = h.el('[data-live-mem-gauge]');
    const memNote = h.el('[data-live-mem-note]');
    const swapValue = h.el('[data-live-swap-value]');
    const swapGauge = h.el('[data-live-swap-gauge]');
    const swapNote = h.el('[data-live-swap-note]');
    const chart = h.el('[data-live-chart]');
    const history = [];
    let running = false;

    function gauge(node, percent) {
      const pct = Math.max(0, Math.min(100, percent || 0));
      node.style.setProperty('--pct', pct.toFixed(1));
      node.classList.toggle('rmm-live__gauge--high', pct >= 90);
      node.classList.toggle('rmm-live__gauge--warn', pct >= 70 && pct < 90);
    }
    function drawChart() {
      if (!chart || !chart.getContext) return;
      const ctx = chart.getContext('2d');
      const width = chart.width = chart.clientWidth || 600;
      const height = chart.height;
      ctx.clearRect(0, 0, width, height);
      if (history.length < 2) return;
      const styles = getComputedStyle(document.documentElement);
      const draw = (key, color) => {
        ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
        history.forEach((point, i) => {
          const x = (i / (history.length - 1)) * width;
          const y = height - (Math.max(0, Math.min(100, point[key])) / 100) * height;
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
      };
      draw('cpu', (styles.getPropertyValue('--color-primary') || '#4f46e5').trim());
      draw('mem', (styles.getPropertyValue('--color-success') || '#16a34a').trim());
    }
    client.on('metrics', (m) => {
      cpuValue.textContent = Math.round(m.cpu_percent) + '%';
      gauge(cpuGauge, m.cpu_percent);
      cores.innerHTML = '';
      (m.cores || []).forEach((pct) => {
        const bar = document.createElement('span');
        bar.className = 'rmm-live__core';
        bar.style.setProperty('--pct', Math.round(pct));
        bar.title = Math.round(pct) + '%';
        cores.appendChild(bar);
      });
      const memPct = m.memory_total ? (m.memory_used / m.memory_total) * 100 : 0;
      memValue.textContent = Math.round(memPct) + '%';
      gauge(memGauge, memPct);
      memNote.textContent = h.fmtBytes(m.memory_used) + ' / ' + h.fmtBytes(m.memory_total);
      const swapPct = m.swap_total ? (m.swap_used / m.swap_total) * 100 : 0;
      swapValue.textContent = m.swap_total ? Math.round(swapPct) + '%' : 'n/a';
      gauge(swapGauge, swapPct);
      swapNote.textContent = m.swap_total ? h.fmtBytes(m.swap_used) + ' / ' + h.fmtBytes(m.swap_total) : 'No swap in use';
      h.el('[data-live-net-rx]').textContent = h.fmtBytes(m.net_rx_bps) + '/s';
      h.el('[data-live-net-tx]').textContent = h.fmtBytes(m.net_tx_bps) + '/s';
      h.el('[data-live-proc-count]').textContent = m.processes || '—';
      h.el('[data-live-uptime]').textContent = h.fmtDuration(m.uptime_seconds);
      history.push({ cpu: m.cpu_percent, mem: memPct });
      if (history.length > 120) history.shift();
      drawChart();
    });
    views.push({
      tab: 'overview',
      onConnect() { if (!running) { running = true; client.request('metrics.start', { interval_ms: 1000 }).catch(() => {}); } },
      onDisconnect() { running = false; history.length = 0; },
    });
  }

  /* =====================================================================
     Terminal — xterm.js over PTY/ConPTY
     ===================================================================== */
  function initTerminal(client, views, h) {
    const TerminalCtor = window.Terminal;
    const FitAddon = (window.FitAddon && window.FitAddon.FitAddon) || window.FitAddon;
    const host = h.el('[data-live-term-host]');
    const shellSelect = h.el('[data-live-shell]');
    const tabsBox = h.el('[data-live-term-tabs]');
    const terminals = new Map();
    let counter = 0;
    let active = null;

    const encode = (text) => btoa(unescape(encodeURIComponent(text)));
    const decode = (b64) => decodeURIComponent(escape(atob(b64)));

    function show(id) {
      active = id;
      terminals.forEach((t, key) => {
        t.wrap.hidden = key !== id;
        if (t.tab) t.tab.setAttribute('aria-current', key === id ? 'true' : 'false');
      });
      const current = terminals.get(id);
      if (current) { current.fit && current.fit.fit(); current.term.focus(); }
    }
    function closeTerm(id) {
      const t = terminals.get(id);
      if (!t) return;
      client.send('terminal.close', { term: id });
      t.term.dispose();
      t.wrap.remove();
      if (t.tab) t.tab.remove();
      terminals.delete(id);
      if (active === id) {
        const next = terminals.keys().next();
        if (!next.done) show(next.value); else active = null;
      }
    }
    async function openTerm() {
      if (!TerminalCtor) { h.showError('The terminal component failed to load.'); return; }
      counter += 1;
      const id = 't' + counter;
      const term = new TerminalCtor({ cursorBlink: true, fontFamily: 'var(--font-mono, monospace)', fontSize: 13, scrollback: 5000 });
      let fit = null;
      if (FitAddon) { fit = new FitAddon(); term.loadAddon(fit); }
      const wrap = document.createElement('div');
      wrap.className = 'rmm-live__term-instance';
      host.appendChild(wrap);
      term.open(wrap);
      if (fit) fit.fit();
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'rmm-live__term-tab';
      const label = document.createElement('span');
      label.textContent = (shellSelect.value || 'shell') + ' ' + counter;
      tab.appendChild(label);
      const close = document.createElement('span');
      close.className = 'rmm-live__term-close';
      close.textContent = '×';
      close.title = 'Close terminal';
      close.addEventListener('click', (e) => { e.stopPropagation(); closeTerm(id); });
      tab.appendChild(close);
      tab.addEventListener('click', () => show(id));
      tabsBox.appendChild(tab);
      terminals.set(id, { term, fit, wrap, tab });
      term.onData((data) => client.send('terminal.input', { term: id, data: encode(data) }));
      term.onResize(({ cols, rows }) => client.send('terminal.resize', { term: id, cols, rows }));
      show(id);
      try {
        await client.request('terminal.open', { term: id, shell: shellSelect.value, cols: term.cols, rows: term.rows });
      } catch (err) {
        term.write('\r\n\x1b[31m' + err.message + '\x1b[0m\r\n');
      }
    }
    client.on('terminal.output', (data) => {
      const t = terminals.get(data.term);
      if (t) t.term.write(decode(data.data));
    });
    client.on('terminal.exit', (data) => {
      const t = terminals.get(data.term);
      if (t) t.term.write('\r\n\x1b[33m[process exited' + (data.code !== undefined ? ' with code ' + data.code : '') + ']\x1b[0m\r\n');
    });
    h.el('[data-live-term-new]').addEventListener('click', openTerm);
    window.addEventListener('resize', () => { const t = terminals.get(active); if (t && t.fit) t.fit.fit(); });
    views.push({
      tab: 'terminal',
      onConnect(info) {
        shellSelect.innerHTML = '';
        (info.shells || []).forEach((shell) => {
          const opt = document.createElement('option');
          opt.value = shell; opt.textContent = shell;
          shellSelect.appendChild(opt);
        });
        shellSelect.disabled = !(info.shells || []).length;
      },
      onShow() { if (!terminals.size && shellSelect.options.length) openTerm(); else show(active); },
      onDisconnect() { terminals.forEach((t) => t.term.dispose()); terminals.clear(); tabsBox.innerHTML = ''; host.innerHTML = ''; active = null; },
    });
  }

  /* =====================================================================
     Task manager
     ===================================================================== */
  function initProcesses(client, views, h) {
    const body = h.el('[data-live-proc-body]');
    const filter = h.el('[data-live-proc-filter]');
    const auto = h.el('[data-live-proc-auto]');
    let processes = [];
    let sortKey = 'cpu_percent';
    let sortDir = -1;
    let timer = null;

    function render() {
      const query = (filter.value || '').toLowerCase();
      const rows = processes
        .filter((p) => !query || (p.name || '').toLowerCase().includes(query) || String(p.pid).includes(query) || (p.user || '').toLowerCase().includes(query))
        .sort((a, b) => {
          const x = a[sortKey], y = b[sortKey];
          if (typeof x === 'number') return (x - y) * sortDir;
          return String(x || '').localeCompare(String(y || '')) * sortDir;
        })
        .slice(0, 500);
      body.innerHTML = '';
      if (!rows.length) { body.innerHTML = '<tr><td colspan="7" class="text-muted">No processes match.</td></tr>'; return; }
      const frag = document.createDocumentFragment();
      rows.forEach((p) => {
        const tr = document.createElement('tr');
        tr.innerHTML =
          '<td title="' + escapeHtml(p.path || p.command || '') + '">' + escapeHtml(p.name || '') + '</td>' +
          '<td class="rmm-live__num">' + p.pid + '</td>' +
          '<td>' + escapeHtml(p.user || '') + '</td>' +
          '<td class="rmm-live__num">' + (p.cpu_percent || 0).toFixed(1) + '</td>' +
          '<td class="rmm-live__num">' + h.fmtBytes(p.memory_bytes) + '</td>' +
          '<td class="rmm-live__num">' + (p.threads || '') + '</td>' +
          '<td class="rmm-live__num"></td>';
        const actions = tr.lastChild;
        const end = document.createElement('button');
        end.type = 'button'; end.className = 'button button--danger button--small'; end.textContent = 'End';
        end.addEventListener('click', () => endProcess(p));
        actions.appendChild(end);
        frag.appendChild(tr);
      });
      body.appendChild(frag);
    }
    async function refresh() {
      try {
        const result = await client.request('process.list');
        processes = result.processes || [];
        render();
      } catch (err) { if (client.state.connected) body.innerHTML = '<tr><td colspan="7" class="text-muted">' + escapeHtml(err.message) + '</td></tr>'; }
    }
    async function endProcess(p) {
      const tree = p.name && confirmEndTree(p);
      if (tree === null) return;
      try {
        await client.request('process.kill', { pid: p.pid, force: tree.force, tree: tree.tree });
        refresh();
      } catch (err) { h.showError ? h.showError(err.message) : window.alert(err.message); }
    }
    function confirmEndTree(p) {
      if (!window.confirm('End ' + (p.name || 'process ' + p.pid) + ' (PID ' + p.pid + ') and its child processes?')) return null;
      return { tree: true, force: false };
    }
    h.els('[data-live-sort]', h.el('[data-live-proc-table]')).forEach((th) => {
      th.classList.add('rmm-live__sortable');
      th.addEventListener('click', () => {
        const key = th.dataset.liveSort;
        if (sortKey === key) sortDir = -sortDir; else { sortKey = key; sortDir = (key === 'name' || key === 'user') ? 1 : -1; }
        render();
      });
    });
    filter.addEventListener('input', render);
    h.el('[data-live-proc-refresh]').addEventListener('click', refresh);
    function schedule() {
      clearInterval(timer);
      if (auto.checked && client.state.connected) timer = setInterval(refresh, 3000);
    }
    auto.addEventListener('change', schedule);
    views.push({
      tab: 'processes',
      onShow() { refresh(); schedule(); },
      onDisconnect() { clearInterval(timer); },
    });
  }

  /* =====================================================================
     Service manager
     ===================================================================== */
  function initServices(client, views, h) {
    const body = h.el('[data-live-svc-body]');
    const filter = h.el('[data-live-svc-filter]');
    let services = [];
    let startTypes = [];
    const STATUS = { running: 'success', stopped: 'neutral', failed: 'danger', starting: 'warning', stopping: 'warning', paused: 'warning', missing: 'neutral', exited: 'neutral' };

    function render() {
      const query = (filter.value || '').toLowerCase();
      const rows = services.filter((s) => !query || (s.display_name || '').toLowerCase().includes(query) || (s.name || '').toLowerCase().includes(query));
      body.innerHTML = '';
      if (!rows.length) { body.innerHTML = '<tr><td colspan="5" class="text-muted">No services match.</td></tr>'; return; }
      const frag = document.createDocumentFragment();
      rows.forEach((s) => {
        const tr = document.createElement('tr');
        const variant = STATUS[s.status] || 'neutral';
        tr.innerHTML =
          '<td><strong>' + escapeHtml(s.display_name || s.name) + '</strong><br><span class="text-muted">' + escapeHtml(s.name) + '</span>' +
          (s.description ? '<br><span class="rmm-live__svc-desc">' + escapeHtml(s.description) + '</span>' : '') + '</td>' +
          '<td><span class="status status--' + variant + '">' + escapeHtml(s.status) + '</span></td>' +
          '<td>' + escapeHtml(s.start_type || '—') + '</td>' +
          '<td>' + escapeHtml(s.account || '—') + '</td>' +
          '<td class="rmm-live__svc-actions"></td>';
        const actions = tr.lastChild;
        const addBtn = (label, action, variantClass) => {
          const btn = document.createElement('button');
          btn.type = 'button'; btn.className = 'button button--' + variantClass + ' button--small'; btn.textContent = label;
          btn.addEventListener('click', () => control(s, action, btn));
          actions.appendChild(btn);
        };
        if (s.status === 'running') { addBtn('Stop', 'stop', 'secondary'); addBtn('Restart', 'restart', 'secondary'); }
        else { addBtn('Start', 'start', 'secondary'); }
        if (startTypes.length) {
          const btn = document.createElement('button');
          btn.type = 'button'; btn.className = 'button button--secondary button--small'; btn.textContent = 'Start type';
          btn.addEventListener('click', () => changeStart(s));
          actions.appendChild(btn);
        }
        frag.appendChild(tr);
      });
      body.appendChild(frag);
    }
    async function refresh() {
      try {
        const result = await client.request('service.list');
        services = result.services || [];
        render();
      } catch (err) { if (client.state.connected) body.innerHTML = '<tr><td colspan="5" class="text-muted">' + escapeHtml(err.message) + '</td></tr>'; }
    }
    async function control(s, action, btn) {
      if (action === 'stop' && !window.confirm('Stop ' + (s.display_name || s.name) + '?')) return;
      if (btn) btn.disabled = true;
      try { const updated = await client.request('service.control', { name: s.name, action }); mergeService(updated); render(); setTimeout(refresh, 1500); }
      catch (err) { window.alert(err.message); }
      finally { if (btn) btn.disabled = false; }
    }
    async function changeStart(s) {
      const result = await h.openPrompt({
        title: 'Start type', message: s.display_name || s.name,
        fields: [{ name: 'start_type', label: 'Start type', type: 'select', value: s.start_type, options: startTypes.map((t) => ({ value: t.value, label: t.label })) }],
      });
      if (!result) return;
      try { const updated = await client.request('service.set_start', { name: s.name, start_type: result.start_type }); mergeService(updated); render(); }
      catch (err) { window.alert(err.message); }
    }
    function mergeService(updated) {
      if (!updated || !updated.name) return;
      const index = services.findIndex((s) => s.name === updated.name);
      if (index >= 0) services[index] = Object.assign({}, services[index], updated);
    }
    filter.addEventListener('input', render);
    h.el('[data-live-svc-refresh]').addEventListener('click', refresh);
    views.push({
      tab: 'services',
      onConnect(info) { startTypes = info.start_types || []; },
      onShow() { refresh(); },
    });
  }

  /* =====================================================================
     File browser with upload/download
     ===================================================================== */
  function initFiles(client, views, h) {
    const rootsSelect = h.el('[data-live-fs-roots]');
    const pathInput = h.el('[data-live-fs-path]');
    const body = h.el('[data-live-fs-body]');
    const progress = h.el('[data-live-fs-progress]');
    const sep = () => (agentOS === 'windows' ? '\\' : '/');
    let current = '';

    function setProgress(text) { progress.hidden = !text; progress.textContent = text || ''; }
    async function list(path) {
      try {
        const result = await client.request('fs.list', { path });
        current = result.path;
        pathInput.value = current;
        render(result);
      } catch (err) { body.innerHTML = '<tr><td colspan="4" class="text-muted">' + escapeHtml(err.message) + '</td></tr>'; }
    }
    function render(result) {
      body.innerHTML = '';
      const frag = document.createDocumentFragment();
      (result.entries || []).forEach((entry) => {
        const tr = document.createElement('tr');
        const isDir = entry.type === 'dir' || entry.type === 'link-dir';
        const nameCell = document.createElement('td');
        if (isDir) {
          const link = document.createElement('a');
          link.href = '#'; link.textContent = '📁 ' + entry.name;
          link.addEventListener('click', (e) => { e.preventDefault(); list(entry.path); });
          nameCell.appendChild(link);
        } else {
          nameCell.textContent = (entry.type === 'link' ? '🔗 ' : '📄 ') + entry.name;
        }
        tr.appendChild(nameCell);
        const size = document.createElement('td');
        size.className = 'rmm-live__num';
        size.textContent = isDir ? '' : h.fmtBytes(entry.size);
        tr.appendChild(size);
        const mod = document.createElement('td');
        if (entry.modified) { const span = document.createElement('span'); span.setAttribute('data-utc', entry.modified); span.textContent = entry.modified; mod.appendChild(span); }
        tr.appendChild(mod);
        const actions = document.createElement('td');
        actions.className = 'rmm-live__svc-actions';
        if (!isDir && entry.type !== 'link') {
          const dl = document.createElement('button');
          dl.type = 'button'; dl.className = 'button button--secondary button--small'; dl.textContent = 'Download';
          dl.addEventListener('click', () => download(entry));
          actions.appendChild(dl);
        }
        const ren = document.createElement('button');
        ren.type = 'button'; ren.className = 'button button--secondary button--small'; ren.textContent = 'Rename';
        ren.addEventListener('click', () => rename(entry));
        actions.appendChild(ren);
        const del = document.createElement('button');
        del.type = 'button'; del.className = 'button button--danger button--small'; del.textContent = 'Delete';
        del.addEventListener('click', () => remove(entry, isDir));
        actions.appendChild(del);
        tr.appendChild(actions);
        frag.appendChild(tr);
      });
      if (!(result.entries || []).length) body.innerHTML = '<tr><td colspan="4" class="text-muted">This folder is empty.</td></tr>';
      else body.appendChild(frag);
      if (result.truncated) { const tr = document.createElement('tr'); tr.innerHTML = '<td colspan="4" class="text-muted">Only the first entries are shown.</td>'; body.appendChild(tr); }
      if (window.MyPortal && window.MyPortal.localiseTimestamps) window.MyPortal.localiseTimestamps(body);
    }
    async function rename(entry) {
      const result = await h.openPrompt({ title: 'Rename', message: entry.path, fields: [{ name: 'name', label: 'New name', value: entry.name }] });
      if (!result || !result.name) return;
      try { await client.request('fs.rename', { path: entry.path, name: result.name }); list(current); }
      catch (err) { window.alert(err.message); }
    }
    async function remove(entry, isDir) {
      if (!window.confirm('Delete ' + entry.path + (isDir ? ' and everything in it?' : '?'))) return;
      try { await client.request('fs.delete', { path: entry.path, recursive: isDir }); list(current); }
      catch (err) { window.alert(err.message); }
    }
    async function mkdir() {
      const result = await h.openPrompt({ title: 'New folder', message: 'In ' + current, fields: [{ name: 'name', label: 'Folder name', value: '' }] });
      if (!result || !result.name) return;
      try { await client.request('fs.mkdir', { path: current + sep() + result.name }); list(current); }
      catch (err) { window.alert(err.message); }
    }

    /* Download: the agent streams base64 chunks; we acknowledge each one. */
    const downloads = new Map();
    client.on('fs.chunk', (chunk) => {
      const dl = downloads.get(chunk.transfer);
      if (!dl) return;
      if (chunk.error) { setProgress(''); window.alert(chunk.error); downloads.delete(chunk.transfer); return; }
      if (chunk.data) dl.parts.push(Uint8Array.from(atob(chunk.data), (c) => c.charCodeAt(0)));
      client.send('fs.download.ack', { transfer: chunk.transfer });
      dl.received += 1;
      setProgress('Downloading ' + dl.name + '… ' + dl.received + ' chunks');
      if (chunk.eof) {
        const blob = new Blob(dl.parts);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = dl.name; document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
        downloads.delete(chunk.transfer);
        setProgress('');
      }
    });
    async function download(entry) {
      try {
        const info = await client.request('fs.download', { path: entry.path });
        downloads.set(info.transfer, { name: info.name, parts: [], received: 0 });
        setProgress('Downloading ' + info.name + '…');
      } catch (err) { window.alert(err.message); }
    }

    /* Upload: chunk the file and send base64 fragments. */
    async function upload(file) {
      const target = current + sep() + file.name;
      let overwrite = false;
      try {
        const begin = await client.request('fs.upload.begin', { path: target, size: file.size, overwrite });
        await sendChunks(file, begin);
      } catch (err) {
        if (/already exists/.test(err.message) && window.confirm(file.name + ' exists. Overwrite?')) {
          try { const begin = await client.request('fs.upload.begin', { path: target, size: file.size, overwrite: true }); await sendChunks(file, begin); }
          catch (e2) { window.alert(e2.message); }
        } else { window.alert(err.message); }
      }
    }
    async function sendChunks(file, begin) {
      const chunkBytes = begin.chunk_bytes || 262144;
      const buffer = new Uint8Array(await file.arrayBuffer());
      const digest = await sha256Hex(buffer);
      let seq = 0;
      for (let offset = 0; offset < buffer.length || offset === 0; offset += chunkBytes) {
        const slice = buffer.subarray(offset, offset + chunkBytes);
        setProgress('Uploading ' + file.name + '… ' + Math.round((offset / Math.max(1, buffer.length)) * 100) + '%');
        await client.request('fs.upload.chunk', { upload: begin.upload, seq, data: base64Bytes(slice) });
        seq += 1;
        if (offset + chunkBytes >= buffer.length) break;
      }
      await client.request('fs.upload.end', { upload: begin.upload, sha256: digest });
      setProgress('');
      list(current);
    }

    h.el('[data-live-fs-go]').addEventListener('click', () => list(pathInput.value.trim()));
    pathInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); list(pathInput.value.trim()); } });
    h.el('[data-live-fs-up]').addEventListener('click', () => {
      const trimmed = current.replace(/[\\/]+$/, '');
      const idx = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
      if (idx > 0) list(trimmed.slice(0, idx)); else if (idx === 0) list(sep());
    });
    h.el('[data-live-fs-mkdir]').addEventListener('click', mkdir);
    h.el('[data-live-fs-upload]').addEventListener('change', (e) => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ''; });
    rootsSelect.addEventListener('change', () => { if (rootsSelect.value) list(rootsSelect.value); });
    views.push({
      tab: 'files',
      onConnect(info) {
        rootsSelect.innerHTML = '<option value="">Drive…</option>';
        (info.roots || []).forEach((r) => { const o = document.createElement('option'); o.value = r; o.textContent = r; rootsSelect.appendChild(o); });
        if ((info.roots || []).length === 1) { rootsSelect.value = info.roots[0]; }
      },
      onShow() { if (!current && rootsSelect.value) list(rootsSelect.value); },
    });
  }

  /* =====================================================================
     Disk usage scanner (gncdu-style)
     ===================================================================== */
  function initDiskUsage(client, views, h) {
    const rootsSelect = h.el('[data-live-du-roots]');
    const pathInput = h.el('[data-live-du-path]');
    const sameFs = h.el('[data-live-du-samefs]');
    const scanBtn = h.el('[data-live-du-scan]');
    const cancelBtn = h.el('[data-live-du-cancel]');
    const progress = h.el('[data-live-du-progress]');
    const crumbs = h.el('[data-live-du-crumbs]');
    const body = h.el('[data-live-du-body]');
    let scanId = null;
    let rootPath = '';

    client.on('diskusage.progress', (p) => {
      if (!scanId || p.scan !== scanId) return;
      progress.hidden = false;
      progress.textContent = 'Scanning… ' + (p.files || 0) + ' files, ' + (p.dirs || 0) + ' folders, ' + h.fmtBytes(p.bytes) + ' — ' + (p.current || '');
    });
    client.on('diskusage.done', (p) => {
      if (!scanId || p.scan !== scanId) return;
      progress.textContent = (p.cancelled ? 'Scan cancelled. ' : 'Scan complete. ') + (p.files || 0) + ' files, ' + h.fmtBytes(p.size) + ' total.';
      cancelBtn.hidden = true;
      scanBtn.disabled = false;
      open(rootPath);
    });
    async function startScan() {
      const path = pathInput.value.trim() || rootsSelect.value;
      if (!path) { window.alert('Choose a drive or enter a folder to scan.'); return; }
      body.innerHTML = '<tr><td colspan="5" class="text-muted">Scanning…</td></tr>';
      scanBtn.disabled = true; cancelBtn.hidden = false;
      try {
        const result = await client.request('diskusage.scan', { path, same_filesystem: sameFs.checked });
        scanId = result.scan; rootPath = result.path;
      } catch (err) { scanBtn.disabled = false; cancelBtn.hidden = true; window.alert(err.message); }
    }
    async function open(path) {
      if (!scanId) return;
      try {
        const result = await client.request('diskusage.children', { scan: scanId, path });
        render(result);
      } catch (err) { body.innerHTML = '<tr><td colspan="5" class="text-muted">' + escapeHtml(err.message) + '</td></tr>'; }
    }
    function render(result) {
      crumbs.innerHTML = '';
      const parts = result.path.replace(/[\\/]+$/, '').split(/[\\/]/);
      let acc = '';
      parts.forEach((part, i) => {
        acc = i === 0 ? (part || '/') : acc.replace(/[\\/]+$/, '') + (agentOSsep()) + part;
        const link = document.createElement('a');
        link.href = '#'; link.textContent = part || '/';
        const target = acc;
        link.addEventListener('click', (e) => { e.preventDefault(); open(target); });
        crumbs.appendChild(link);
        if (i < parts.length - 1) crumbs.appendChild(document.createTextNode(' › '));
      });
      body.innerHTML = '';
      if (result.parent !== undefined && result.parent !== '') {
        const tr = document.createElement('tr');
        const td = document.createElement('td'); td.colSpan = 5;
        const up = document.createElement('a'); up.href = '#'; up.textContent = '⬆ Up a level';
        up.addEventListener('click', (e) => { e.preventDefault(); open(result.parent); });
        td.appendChild(up); tr.appendChild(td); body.appendChild(tr);
      }
      (result.items || []).forEach((item) => {
        const tr = document.createElement('tr');
        const name = document.createElement('td');
        if (item.type === 'dir' && !item.skipped) {
          const link = document.createElement('a'); link.href = '#'; link.textContent = '📁 ' + item.name;
          link.addEventListener('click', (e) => { e.preventDefault(); open(item.path); });
          name.appendChild(link);
        } else { name.textContent = (item.type === 'dir' ? '📁 ' : '📄 ') + item.name; }
        if (item.skipped) { const note = document.createElement('span'); note.className = 'text-muted'; note.textContent = ' — ' + item.skipped; name.appendChild(note); }
        tr.appendChild(name);
        const size = document.createElement('td'); size.className = 'rmm-live__num'; size.textContent = h.fmtBytes(item.size); tr.appendChild(size);
        const bar = document.createElement('td'); bar.className = 'rmm-live__bar-col';
        bar.innerHTML = '<span class="rmm-live__bar" style="--pct:' + (item.percent || 0).toFixed(1) + '"></span><span class="rmm-live__bar-label">' + (item.percent || 0).toFixed(1) + '%</span>';
        tr.appendChild(bar);
        const items = document.createElement('td'); items.className = 'rmm-live__num';
        items.textContent = item.type === 'dir' ? (item.files + ' files') : ''; tr.appendChild(items);
        const actions = document.createElement('td'); actions.className = 'rmm-live__svc-actions';
        const del = document.createElement('button'); del.type = 'button'; del.className = 'button button--danger button--small'; del.textContent = 'Delete';
        del.addEventListener('click', () => removeItem(item));
        actions.appendChild(del); tr.appendChild(actions);
        body.appendChild(tr);
      });
      if (!(result.items || []).length) { const tr = document.createElement('tr'); tr.innerHTML = '<td colspan="5" class="text-muted">Empty.</td>'; body.appendChild(tr); }
    }
    function agentOSsep() { return agentOS === 'windows' ? '\\' : '/'; }
    async function removeItem(item) {
      if (!window.confirm('Delete ' + item.path + '? This removes it from the device.')) return;
      try {
        await client.request('fs.delete', { path: item.path, recursive: item.type === 'dir' });
        await client.request('diskusage.remove', { scan: scanId, path: item.path });
        open(currentDuPath());
      } catch (err) { window.alert(err.message); }
    }
    function currentDuPath() {
      const link = crumbs.querySelector('a:last-of-type');
      return link ? rootPath : rootPath;
    }
    scanBtn.addEventListener('click', startScan);
    cancelBtn.addEventListener('click', () => { if (scanId) client.request('diskusage.cancel', { scan: scanId }).catch(() => {}); });
    rootsSelect.addEventListener('change', () => { if (rootsSelect.value) pathInput.value = rootsSelect.value; });
    views.push({
      tab: 'diskusage',
      onConnect(info) {
        rootsSelect.innerHTML = '<option value="">Drive…</option>';
        (info.roots || []).forEach((r) => { const o = document.createElement('option'); o.value = r; o.textContent = r; rootsSelect.appendChild(o); });
      },
    });
  }

  /* =====================================================================
     Registry editor (Windows)
     ===================================================================== */
  function initRegistry(client, views, h) {
    const pathInput = h.el('[data-live-reg-path]');
    const keysBody = h.el('[data-live-reg-keys-body]');
    const valuesBody = h.el('[data-live-reg-values-body]');
    let current = '';
    let hives = [];

    async function open(path) {
      try {
        const result = await client.request('registry.list', { path: path || '' });
        current = result.path || '';
        pathInput.value = current;
        renderKeys(result);
        renderValues(result);
      } catch (err) { valuesBody.innerHTML = '<tr><td colspan="4" class="text-muted">' + escapeHtml(err.message) + '</td></tr>'; }
    }
    function renderKeys(result) {
      keysBody.innerHTML = '';
      (result.keys || []).forEach((key) => {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        const link = document.createElement('a'); link.href = '#'; link.textContent = '📁 ' + key;
        const child = current ? current + '\\' + key : key;
        link.addEventListener('click', (e) => { e.preventDefault(); open(child); });
        td.appendChild(link);
        const del = document.createElement('button'); del.type = 'button'; del.className = 'button button--danger button--small rmm-live__reg-del'; del.textContent = '×'; del.title = 'Delete key';
        del.addEventListener('click', () => deleteKey(child));
        td.appendChild(del);
        tr.appendChild(td); keysBody.appendChild(tr);
      });
      if (!(result.keys || []).length) keysBody.innerHTML = '<tr><td class="text-muted">No sub-keys.</td></tr>';
    }
    function renderValues(result) {
      valuesBody.innerHTML = '';
      (result.values || []).forEach((value) => {
        const tr = document.createElement('tr');
        tr.innerHTML =
          '<td>' + escapeHtml(value.name || '(Default)') + '</td>' +
          '<td>' + escapeHtml(value.type) + '</td>' +
          '<td class="rmm-live__reg-data">' + escapeHtml(formatData(value)) + '</td>' +
          '<td class="rmm-live__svc-actions"></td>';
        const actions = tr.lastChild;
        const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'button button--secondary button--small'; edit.textContent = 'Edit';
        edit.addEventListener('click', () => editValue(value));
        actions.appendChild(edit);
        const del = document.createElement('button'); del.type = 'button'; del.className = 'button button--danger button--small'; del.textContent = 'Delete';
        del.addEventListener('click', () => deleteValue(value));
        actions.appendChild(del);
        valuesBody.appendChild(tr);
      });
      if (!(result.values || []).length) valuesBody.innerHTML = '<tr><td colspan="4" class="text-muted">No values.</td></tr>';
    }
    function formatData(value) {
      if (Array.isArray(value.data)) return value.data.join('; ');
      return value.data === null || value.data === undefined ? '' : String(value.data);
    }
    async function editValue(value) {
      const isMulti = value.type === 'REG_MULTI_SZ';
      const result = await h.openPrompt({
        title: 'Edit value', message: value.name || '(Default)',
        fields: [{ name: 'data', label: value.type + (isMulti ? ' (one per line)' : ''), type: isMulti ? 'textarea' : 'text', value: isMulti ? (value.data || []).join('\n') : formatData(value) }],
      });
      if (!result) return;
      const data = isMulti ? result.data.split('\n').map((s) => s.trim()).filter(Boolean) : result.data;
      try { await client.request('registry.set', { path: current, name: value.name, type: value.type, data }); open(current); }
      catch (err) { window.alert(err.message); }
    }
    async function deleteValue(value) {
      if (!window.confirm('Delete value ' + (value.name || '(Default)') + '?')) return;
      try { await client.request('registry.delete_value', { path: current, name: value.name }); open(current); }
      catch (err) { window.alert(err.message); }
    }
    async function newValue() {
      if (!current) { window.alert('Open a key first.'); return; }
      const result = await h.openPrompt({
        title: 'New value', message: 'In ' + current,
        fields: [
          { name: 'name', label: 'Name', value: '' },
          { name: 'type', label: 'Type', type: 'select', value: 'REG_SZ', options: ['REG_SZ', 'REG_EXPAND_SZ', 'REG_DWORD', 'REG_QWORD', 'REG_MULTI_SZ', 'REG_BINARY'].map((t) => ({ value: t, label: t })) },
          { name: 'data', label: 'Data', type: 'textarea', value: '' },
        ],
      });
      if (!result) return;
      const data = result.type === 'REG_MULTI_SZ' ? result.data.split('\n').map((s) => s.trim()).filter(Boolean) : result.data;
      try { await client.request('registry.set', { path: current, name: result.name, type: result.type, data }); open(current); }
      catch (err) { window.alert(err.message); }
    }
    async function newKey() {
      const result = await h.openPrompt({ title: 'New key', message: 'Under ' + (current || 'a hive'), fields: [{ name: 'name', label: 'Key name', value: '' }] });
      if (!result || !result.name) return;
      try { await client.request('registry.create_key', { path: (current ? current + '\\' : '') + result.name }); open(current); }
      catch (err) { window.alert(err.message); }
    }
    async function deleteKey(path) {
      if (!window.confirm('Delete key ' + path + ' and all its sub-keys and values?')) return;
      try { await client.request('registry.delete_key', { path, recursive: true }); open(current); }
      catch (err) { window.alert(err.message); }
    }
    h.el('[data-live-reg-go]').addEventListener('click', () => open(pathInput.value.trim()));
    pathInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); open(pathInput.value.trim()); } });
    h.el('[data-live-reg-up]').addEventListener('click', () => { const idx = current.lastIndexOf('\\'); open(idx > 0 ? current.slice(0, idx) : ''); });
    h.el('[data-live-reg-newkey]').addEventListener('click', newKey);
    h.el('[data-live-reg-newvalue]').addEventListener('click', newValue);
    views.push({ tab: 'registry', onConnect(info) { hives = info.hives || []; }, onShow() { if (!current) open(''); } });
  }

  /* ----- helpers ----- */
  function escapeHtml(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function base64Bytes(bytes) {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }
  async function sha256Hex(bytes) {
    if (!window.crypto || !window.crypto.subtle) return '';
    const digest = await window.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
  }
})();
