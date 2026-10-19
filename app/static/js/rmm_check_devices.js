// RMM checks page: a check's "failing" or "with a problem" count opens a
// modal listing those devices of the company and their latest values.
(function () {
  'use strict';

  const modal = document.querySelector('[data-rmm-check-devices-modal]');
  if (!modal) return;
  const title = modal.querySelector('[data-rmm-check-devices-title]');
  const eyebrow = modal.querySelector('[data-rmm-check-devices-eyebrow]');
  const subtitle = modal.querySelector('[data-rmm-check-devices-subtitle]');
  const statusLine = modal.querySelector('[data-rmm-check-devices-status]');
  const error = modal.querySelector('[data-rmm-check-devices-error]');
  const table = modal.querySelector('[data-rmm-check-devices-table]');
  const rows = modal.querySelector('[data-rmm-check-devices-rows]');
  const VARIANTS = { failing: 'error', warning: 'warning', passing: 'success' };
  const HEADINGS = { failing: 'Failing devices', warning: 'Devices with a problem' };
  let opener = null;
  let request = 0;

  // Times are stored in UTC; show them in the viewer's time zone.
  function formatDate(value) {
    if (!value) return '–';
    const date = new Date(/Z|[+-]\d\d:?\d\d$/.test(value) ? value : value + 'Z');
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  function cell(label, content) {
    const td = document.createElement('td');
    td.setAttribute('data-label', label);
    if (content instanceof Node) td.appendChild(content);
    else td.textContent = content == null || content === '' ? '–' : String(content);
    return td;
  }

  function deviceCell(device) {
    const wrapper = document.createElement('span');
    let name;
    if (device.asset_id) {
      name = document.createElement('a');
      name.href = '/assets/' + encodeURIComponent(device.asset_id) + '#checks';
    } else {
      name = document.createElement('strong');
    }
    name.textContent = device.name;
    wrapper.appendChild(name);
    if (device.hostname && device.hostname !== device.name) {
      wrapper.appendChild(document.createElement('br'));
      const host = document.createElement('span');
      host.className = 'text-muted';
      host.textContent = device.hostname;
      wrapper.appendChild(host);
    }
    if (device.ticket_id) {
      wrapper.appendChild(document.createElement('br'));
      const ticket = document.createElement('a');
      ticket.href = '/admin/tickets/' + encodeURIComponent(device.ticket_id);
      ticket.textContent = 'Ticket #' + device.ticket_id;
      wrapper.appendChild(ticket);
    }
    return wrapper;
  }

  function statusCell(device) {
    const pill = document.createElement('span');
    pill.className = 'status status--' + (VARIANTS[device.status] || 'neutral');
    pill.textContent = device.status_label || device.status || 'Unknown';
    return pill;
  }

  function render(devices) {
    rows.replaceChildren();
    devices.forEach((device) => {
      const tr = document.createElement('tr');
      tr.append(
        cell('Device', deviceCell(device)),
        cell('Result', statusCell(device)),
        cell('Value', device.value_text),
        cell('Since', formatDate(device.problem_since)),
        cell('Last checked', formatDate(device.checked_at)),
      );
      rows.appendChild(tr);
    });
    table.hidden = devices.length === 0;
    statusLine.textContent = devices.length
      ? devices.length + ' device' + (devices.length === 1 ? '' : 's')
      : 'No devices in this company have this result right now.';
  }

  async function load(checkId, status) {
    const current = ++request;
    try {
      const response = await fetch(
        '/api/rmm/checks/' + encodeURIComponent(checkId) + '/devices?status=' + encodeURIComponent(status),
        { credentials: 'same-origin', headers: { Accept: 'application/json' } },
      );
      let data = {};
      try { data = await response.json(); } catch (parseError) { data = {}; }
      if (current !== request) return;
      if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed (' + response.status + ').');
      }
      if (data.check) {
        eyebrow.textContent = data.check.type_label || 'Check';
        subtitle.textContent = data.check.description ? 'Fails when: ' + data.check.description : '';
      }
      render(Array.isArray(data.devices) ? data.devices : []);
    } catch (failure) {
      if (current !== request) return;
      statusLine.textContent = '';
      error.textContent = 'The devices could not be loaded. ' + (failure.message || '');
      error.hidden = false;
    }
  }

  function open(trigger) {
    opener = trigger;
    const status = trigger.getAttribute('data-rmm-check-devices-status');
    const name = trigger.getAttribute('data-rmm-check-devices-name') || 'Check';
    title.textContent = (HEADINGS[status] || 'Devices') + ': ' + name;
    eyebrow.textContent = 'Check';
    subtitle.textContent = '';
    error.hidden = true;
    table.hidden = true;
    rows.replaceChildren();
    statusLine.textContent = 'Loading devices…';
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    modal.querySelector('.modal__close').focus();
    load(trigger.getAttribute('data-rmm-check-devices-open'), status);
  }

  function close() {
    request += 1;
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (opener) opener.focus();
  }

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-rmm-check-devices-open]');
    if (trigger) {
      event.preventDefault();
      open(trigger);
    }
  });
  modal.querySelectorAll('[data-rmm-check-devices-close]').forEach((button) => button.addEventListener('click', close));
  modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
  modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
})();
