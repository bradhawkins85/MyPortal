(() => {
  const modal = document.getElementById('device-types-modal');
  const openButton = document.querySelector('[data-device-types-open]');
  if (!modal || !openButton) return;

  const closeButtons = modal.querySelectorAll('[data-device-types-close]');

  function openModal() {
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    modal.querySelector('input, button')?.focus();
  }

  function closeModal() {
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    openButton.focus();
  }

  openButton.addEventListener('click', openModal);
  closeButtons.forEach((button) => button.addEventListener('click', closeModal));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) closeModal();
  });
})();

(() => {
  const modal = document.getElementById('device-ipam-modal');
  if (!modal) return;
  const csrf = modal.querySelector('input[name="_csrf"]')?.value || '';
  const checkboxes = Array.from(document.querySelectorAll('[data-device-select]'));
  const bulkButton = document.querySelector('[data-ipam-bulk-open]');
  const wanToggle = modal.querySelector('[data-ipam-use-wan]');
  let selectedIds = [];
  let trigger = null;

  const refreshSelection = () => {
    const count = checkboxes.filter((item) => item.checked).length;
    if (bulkButton) bulkButton.disabled = count === 0;
    const label = document.querySelector('[data-ipam-selected-count]');
    if (label) label.textContent = `(${count})`;
  };
  checkboxes.forEach((item) => item.addEventListener('change', refreshSelection));
  document.querySelector('[data-device-select-all]')?.addEventListener('change', () =>
    window.setTimeout(refreshSelection));

  const renderPreview = async () => {
    const body = modal.querySelector('[data-ipam-preview]');
    const submit = modal.querySelector('[data-ipam-import-submit]');
    body.innerHTML = '<tr><td colspan="5">Loading preview…</td></tr>';
    submit.disabled = true;
    const data = new FormData();
    selectedIds.forEach((id) => data.append('device_ids', id));
    if (wanToggle.checked) data.append('use_wan', '1');
    data.append('_csrf', csrf);
    try {
      const response = await fetch('/devices/ipam-preview', { method: 'POST', body: data });
      if (!response.ok) throw new Error('Preview request failed');
      const payload = await response.json();
      body.replaceChildren(...payload.items.map((item) => {
        const row = document.createElement('tr');
        const values = [item.device, item.candidate || '—', item.network || '—'];
        values.forEach((value) => { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); });
        const links = document.createElement('td');
        if (item.asset_url) { const asset = document.createElement('a'); asset.href = item.asset_url; asset.textContent = item.asset; links.append(asset, document.createTextNode(' · ')); }
        const source = document.createElement('a'); source.href = item.source_url; source.textContent = 'Discovery source'; links.append(source); row.append(links);
        const review = document.createElement('td'); review.textContent = item.reason || 'Ready';
        if (item.reason) review.className = 'text-danger';
        row.append(review); return row;
      }));
      submit.disabled = !payload.items.some((item) => !item.reason);
    } catch (_error) {
      body.innerHTML = '<tr><td colspan="5">Preview could not be loaded. Nothing has been changed.</td></tr>';
    }
  };
  const open = (ids, button) => {
    selectedIds = ids; trigger = button; wanToggle.checked = false;
    const holder = modal.querySelector('[data-ipam-device-ids]');
    holder.replaceChildren(...ids.map((id) => { const input = document.createElement('input'); input.type = 'hidden'; input.name = 'device_ids'; input.value = id; return input; }));
    modal.hidden = false; modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open'); wanToggle.focus(); renderPreview();
  };
  const close = () => { modal.hidden = true; modal.setAttribute('aria-hidden', 'true'); document.body.classList.remove('modal-open'); trigger?.focus(); };
  bulkButton?.addEventListener('click', () => open(checkboxes.filter((item) => item.checked).map((item) => item.value), bulkButton));
  document.querySelectorAll('[data-ipam-device-open]').forEach((button) => button.addEventListener('click', () => open([button.dataset.ipamDeviceOpen], button)));
  modal.querySelectorAll('[data-ipam-device-close]').forEach((button) => button.addEventListener('click', close));
  wanToggle.addEventListener('change', renderPreview);
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modal.hidden) close(); });
  refreshSelection();
})();

(() => {
  const modal = document.getElementById('device-bulk-modal');
  const openButton = document.querySelector('[data-device-bulk-open]');
  const selectAll = document.querySelector('[data-device-select-all]');
  const checkboxes = Array.from(document.querySelectorAll('[data-device-select]'));
  if (!modal || !openButton || !selectAll || !checkboxes.length) return;

  const selected = () => checkboxes.filter((checkbox) => checkbox.checked);
  const refresh = () => {
    const count = selected().length;
    openButton.disabled = count === 0;
    document.querySelector('[data-device-selected-count]').textContent = `(${count})`;
    selectAll.checked = count === checkboxes.length;
    selectAll.indeterminate = count > 0 && count < checkboxes.length;
  };
  selectAll.addEventListener('change', () => {
    checkboxes.filter((checkbox) => !checkbox.closest('tr').hidden)
      .forEach((checkbox) => { checkbox.checked = selectAll.checked; });
    refresh();
  });
  checkboxes.forEach((checkbox) => checkbox.addEventListener('change', refresh));

  const close = () => {
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    openButton.focus();
  };
  openButton.addEventListener('click', () => {
    const chosen = selected();
    if (!chosen.length) return;
    modal.querySelector('[data-device-bulk-count]').textContent = chosen.length;
    const container = modal.querySelector('[data-device-bulk-ids]');
    container.replaceChildren(...chosen.map((checkbox) => {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = 'device_ids'; input.value = checkbox.value;
      return input;
    }));
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    modal.querySelector('[data-device-bulk-action]').focus();
  });
  modal.querySelectorAll('[data-device-bulk-close]').forEach((button) => button.addEventListener('click', close));
  modal.querySelector('[data-device-bulk-action]').addEventListener('change', (event) => {
    modal.querySelectorAll('[data-device-bulk-field]').forEach((field) => {
      field.hidden = field.dataset.deviceBulkField !== event.target.value;
    });
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) close();
  });
  refresh();
})();

(() => {
  const modal = document.getElementById('port-check-modal');
  if (!modal) return;
  const MAX_PORTS = 1024;
  const POLL_MS = 2000;
  const POLL_LIMIT_MS = 120000;
  const form = modal.querySelector('[data-port-check-form]');
  const input = modal.querySelector('[data-port-check-input]');
  const errorEl = modal.querySelector('[data-port-check-error]');
  const summaryEl = modal.querySelector('[data-port-check-summary]');
  const resultsEl = modal.querySelector('[data-port-check-results]');
  const submit = modal.querySelector('[data-port-check-submit]');
  let deviceId = null;
  let trigger = null;
  let pollTimer = null;

  const toDate = (value) => {
    if (!value) return null;
    let text = String(value).trim().replace(' ', 'T');
    if (!/[zZ]|[+-]\d\d:?\d\d$/.test(text)) text += 'Z';
    const date = new Date(text);
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const formatWhen = (value) => {
    const date = toDate(value);
    return date ? date.toLocaleString() : '';
  };
  document.querySelectorAll('[data-port-check-chip] [data-utc]').forEach((node) => {
    const text = formatWhen(node.getAttribute('data-utc'));
    if (text) node.textContent = text;
  });

  // Mirrors app/services/port_checks.parse_port_spec.
  const parsePorts = (raw) => {
    const tokens = String(raw || '').split(/[\s,]+/).filter(Boolean);
    if (!tokens.length) return { error: 'Enter at least one port, for example 443 or 8000-8100.' };
    const ports = new Set();
    for (const token of tokens) {
      const match = /^(\d{1,5})(?:-(\d{1,5}))?$/.exec(token);
      if (!match) return { error: `“${token}” isn't a port or a range. Use a number like 443 or a range like 8000-8100.` };
      const start = Number(match[1]);
      const end = match[2] ? Number(match[2]) : start;
      if (start < 1 || start > 65535 || end < 1 || end > 65535) return { error: `“${token}” is outside 1–65535.` };
      if (end < start) return { error: `“${token}” runs backwards. Put the lower port first.` };
      if (end - start + 1 > MAX_PORTS) return { error: `Check at most ${MAX_PORTS.toLocaleString()} ports at a time.` };
      for (let port = start; port <= end; port += 1) ports.add(port);
      if (ports.size > MAX_PORTS) return { error: `Check at most ${MAX_PORTS.toLocaleString()} ports at a time.` };
    }
    return { ports: Array.from(ports).sort((a, b) => a - b) };
  };
  const compress = (ports) => {
    const parts = [];
    for (let i = 0; i < ports.length; i += 1) {
      const start = ports[i];
      while (i + 1 < ports.length && ports[i + 1] === ports[i] + 1) i += 1;
      parts.push(start === ports[i] ? String(start) : `${start}–${ports[i]}`);
    }
    return parts.join(', ');
  };

  const showError = (message) => {
    errorEl.textContent = message || '';
    errorEl.hidden = !message;
    input.setAttribute('aria-invalid', message ? 'true' : 'false');
  };
  const updateSummary = () => {
    const parsed = parsePorts(input.value);
    if (!input.value.trim()) {
      summaryEl.textContent = 'Enter the ports you want to check.';
      showError('');
      return parsed;
    }
    if (parsed.error) {
      summaryEl.textContent = 'Fix the ports list to see what will be checked.';
    } else {
      const count = parsed.ports.length;
      summaryEl.textContent = `${count.toLocaleString()} port${count === 1 ? '' : 's'}: ${compress(parsed.ports)}`;
    }
    showError(parsed.error || '');
    return parsed;
  };

  const pill = (text, variant) => {
    const span = document.createElement('span');
    span.className = `status status--${variant}`;
    span.textContent = text;
    return span;
  };
  const updateChip = (check) => {
    const chip = document.querySelector(`[data-port-check-chip="${deviceId}"]`);
    if (!chip || !check) return;
    let label = 'Port check waiting';
    let variant = 'warning';
    if (check.status === 'completed') {
      label = `Port check: ${check.open_ports.length} of ${check.checked_count} open`;
      variant = check.open_ports.length ? 'success' : 'neutral';
    } else if (check.status === 'failed') {
      label = 'Port check failed';
      variant = 'danger';
    }
    const when = document.createElement('small');
    when.textContent = formatWhen(check.completed_at || check.requested_at);
    chip.replaceChildren(pill(label, variant), document.createTextNode(' '), when);
  };
  const renderCheck = (check, { delivered = true, waitingTooLong = false } = {}) => {
    resultsEl.replaceChildren();
    if (!check) return;
    const heading = document.createElement('p');
    heading.className = 'port-check__status';
    if (check.status === 'completed') {
      heading.textContent = `Checked ${formatWhen(check.completed_at)}: ${check.open_ports.length} of ${check.checked_count} open.`;
      const list = document.createElement('ul');
      list.className = 'port-check__list';
      check.open_ports.forEach((port) => {
        const item = document.createElement('li');
        item.append(pill('Open', 'success'), document.createTextNode(` ${port}`));
        list.append(item);
      });
      const closed = check.ports.filter((port) => !check.open_ports.includes(port));
      resultsEl.append(heading, list);
      if (closed.length) {
        const details = document.createElement('details');
        details.className = 'port-check__closed';
        const summary = document.createElement('summary');
        summary.append(pill('Closed / no answer', 'neutral'), document.createTextNode(` ${closed.length.toLocaleString()} port${closed.length === 1 ? '' : 's'}`));
        const text = document.createElement('p');
        text.textContent = compress(closed);
        details.append(summary, text);
        resultsEl.append(details);
      }
      return;
    }
    if (check.status === 'failed') {
      heading.append(pill('Not checked', 'danger'), document.createTextNode(` ${check.error || 'The scanner could not run the check.'}`));
    } else if (waitingTooLong) {
      heading.textContent = 'Still waiting for the scanner. The result will appear on this device\'s row when it reports back.';
    } else if (!delivered || check.status === 'queued') {
      heading.textContent = 'The scanner is offline. The check will run when it reconnects.';
    } else {
      heading.textContent = `Waiting for the scanner to check ${check.ports.length.toLocaleString()} port${check.ports.length === 1 ? '' : 's'}…`;
    }
    resultsEl.append(heading);
  };

  const stopPolling = () => { if (pollTimer) window.clearTimeout(pollTimer); pollTimer = null; };
  const poll = (started, delivered) => {
    stopPolling();
    pollTimer = window.setTimeout(async () => {
      try {
        const response = await fetch(`/devices/discovered/${deviceId}/port-check`, { headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error('poll failed');
        const { check } = await response.json();
        const done = check && (check.status === 'completed' || check.status === 'failed');
        const tooLong = !done && Date.now() - started > POLL_LIMIT_MS;
        renderCheck(check, { delivered, waitingTooLong: tooLong });
        if (done) updateChip(check);
        if (!done && !tooLong) poll(started, delivered);
      } catch (_error) {
        if (Date.now() - started <= POLL_LIMIT_MS) poll(started, delivered);
      }
    }, POLL_MS);
  };

  const open = async (button) => {
    trigger = button;
    deviceId = button.dataset.portCheckOpen;
    form.action = `/devices/discovered/${deviceId}/port-check`;
    modal.querySelector('[data-port-check-device]').textContent = button.dataset.portCheckDevice || '';
    modal.querySelector('[data-port-check-scanner]').textContent = button.dataset.portCheckScanner || 'its subnet scanner';
    input.value = '';
    submit.disabled = false;
    updateSummary();
    resultsEl.replaceChildren();
    button.closest('details')?.removeAttribute('open');
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    input.focus();
    try {
      const response = await fetch(`/devices/discovered/${deviceId}/port-check`, { headers: { Accept: 'application/json' } });
      if (response.ok) {
        const { check } = await response.json();
        if (check) {
          input.value = compress(check.ports).replace(/–/g, '-');
          updateSummary();
          renderCheck(check);
          if (check.status !== 'completed' && check.status !== 'failed') poll(Date.now(), check.status !== 'queued');
        }
      }
    } catch (_error) { /* the form still works without the last result */ }
  };
  const close = () => {
    stopPolling();
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    trigger?.focus();
  };

  input.addEventListener('input', updateSummary);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const parsed = updateSummary();
    if (parsed.error) { input.focus(); return; }
    submit.disabled = true;
    stopPolling();
    try {
      const response = await fetch(form.action, { method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' } });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        showError(payload.detail || 'The port check could not be started. Nothing was sent to the scanner.');
        return;
      }
      renderCheck(payload.check, { delivered: payload.delivered });
      updateChip(payload.check);
      poll(Date.now(), payload.delivered);
    } catch (_error) {
      showError('The port check could not be started. Check your connection and try again.');
    } finally {
      submit.disabled = false;
    }
  });
  document.querySelectorAll('[data-port-check-open]').forEach((button) => {
    button.hidden = false;
    button.addEventListener('click', () => open(button));
  });
  modal.querySelectorAll('[data-port-check-close]').forEach((button) => button.addEventListener('click', close));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) close();
  });
})();
