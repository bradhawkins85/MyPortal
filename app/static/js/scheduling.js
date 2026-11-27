// Scheduling pages (/admin/schedule and /admin/schedule/me).
// Clicking an empty spot on a technician's row opens the booking editor for
// that technician and time (to the nearest 15 minutes); "+ Book appointment"
// opens it blank. The editor writes a plain-English summary as it is filled
// in. Without JavaScript the links load the page with the editor open.
// The editor lists the time the chosen technician is already taken that day
// (other appointments, and busy time from their Outlook calendar, which shows
// no event details) and won't let a booking over it be saved; the server
// refuses it too.
(function () {
  'use strict';

  document.querySelectorAll('[data-submit-on-change]').forEach((input) => {
    input.addEventListener('change', () => { if (input.form) input.form.requestSubmit(); });
  });

  const modal = document.querySelector('[data-sch-modal]');
  if (!modal) return;
  const form = modal.querySelector('[data-sch-form]');
  const preview = form.querySelector('[data-sch-preview]');
  const field = (name) => form.querySelector(`[name="${name}"]`);

  function selectedText(select) {
    return select && select.value ? select.options[select.selectedIndex].text : '';
  }

  function ticketLabel() {
    const fixed = form.querySelector('[data-sch-ticket-label]');
    if (fixed) return fixed.textContent.trim();
    const value = field('ticket_id').value.trim();
    if (!value) return '';
    const option = form.querySelector(`#sch-ticket-options option[value="${CSS.escape(value)}"]`);
    return option ? option.textContent.trim() : '#' + value;
  }

  function describe() {
    const kind = (form.querySelector('[name="kind"]:checked') || {}).parentElement;
    const kindLabel = kind ? kind.textContent.trim() : 'Appointment';
    const tech = selectedText(field('technician_user_id'));
    const day = field('date').value;
    const start = field('start_time').value;
    const duration = selectedText(field('duration_minutes'));
    const ticket = ticketLabel();
    if (!tech || !day || !start) {
      preview.textContent = 'Choose a ticket, technician and time.';
      return;
    }
    let text = kindLabel + ' with ' + tech + ' on ' + new Date(day + 'T00:00').toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
      + ' at ' + start + ' for ' + duration;
    if (ticket) text += ', for ' + ticket;
    if (field('location').value.trim()) text += ', at ' + field('location').value.trim();
    preview.textContent = text + '.';
  }

  let lastTrigger = null;
  function open(trigger) {
    lastTrigger = trigger || null;
    modal.hidden = false;
    describe();
    loadTaken();
    const first = field('ticket_id').value ? field('technician_user_id') : field('ticket_id');
    if (first) first.focus();
  }

  // ---- Availability -------------------------------------------------------
  const avail = form.querySelector('[data-sch-avail]');
  const availList = form.querySelector('[data-sch-avail-list]');
  const availError = form.querySelector('[data-sch-avail-error]');
  const submit = form.querySelector('.modal__footer button[type="submit"]:not([form])');
  const appointmentId = (form.querySelector('[data-sch-appointment]') || { getAttribute: () => '' }).getAttribute('data-sch-appointment');
  const cache = new Map();
  let taken = [];
  let requestKey = '';

  const toMinutes = (text) => {
    const match = /^(\d{2}):(\d{2})/.exec(text || '');
    return match ? Number(match[1]) * 60 + Number(match[2]) : null;
  };

  function checkSlot() {
    const startField = field('start_time');
    const start = toMinutes(startField.value);
    const duration = Number(field('duration_minutes').value || 0);
    let clash = null;
    if (start !== null && duration > 0) {
      const end = start + duration;
      clash = taken.find((range) => start < toMinutes(range.end) && end > toMinutes(range.start)) || null;
    }
    const message = clash
      ? 'That time is taken (' + clash.start + '–' + clash.end + ', '
        + (clash.source === 'calendar' ? 'busy in their calendar' : 'another appointment') + '). Choose another time.'
      : '';
    startField.setCustomValidity(message);
    if (availError) {
      availError.textContent = message;
      availError.hidden = !message;
    }
    if (submit) submit.disabled = Boolean(message);
  }

  function renderTaken() {
    if (!avail) return;
    availList.replaceChildren();
    taken.forEach((range) => {
      const item = document.createElement('li');
      const pill = document.createElement('span');
      pill.className = 'status ' + (range.source === 'calendar' ? 'status--neutral' : 'status--info');
      pill.textContent = range.start + '–' + range.end + ' · ' + (range.source === 'calendar' ? 'Unavailable' : 'Booked');
      item.appendChild(pill);
      availList.appendChild(item);
    });
    avail.hidden = taken.length === 0;
    checkSlot();
  }

  async function loadTaken() {
    const technician = field('technician_user_id').value;
    const day = field('date').value;
    const key = technician && day ? technician + '|' + day : '';
    requestKey = key;
    if (!key) {
      taken = [];
      renderTaken();
      return;
    }
    if (!cache.has(key)) {
      const params = new URLSearchParams({ technician_id: technician, date: day });
      if (appointmentId) params.set('exclude', appointmentId);
      try {
        const response = await fetch('/api/scheduling/availability?' + params.toString(),
          { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error(String(response.status));
        cache.set(key, (await response.json()).unavailable || []);
      } catch (error) {
        cache.set(key, []); // the server still refuses a clash when the form is saved
      }
    }
    if (requestKey !== key) return; // a later choice superseded this one
    taken = cache.get(key) || [];
    renderTaken();
  }

  form.addEventListener('input', describe);
  form.addEventListener('change', describe);
  form.addEventListener('change', (event) => {
    const name = event.target && event.target.name;
    if (name === 'technician_user_id' || name === 'date') loadTaken();
    else if (name === 'start_time' || name === 'duration_minutes') checkSlot();
  });
  form.addEventListener('input', (event) => {
    if (event.target && event.target.name === 'start_time') checkSlot();
  });

  const isNew = form.getAttribute('action') === '/admin/schedule/appointments';
  document.querySelectorAll('[data-sch-open]').forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      if (!isNew) return; // a saved appointment's editor is open; the link loads a blank one
      event.preventDefault();
      open(trigger);
    });
  });

  // Click an empty spot on a technician's row to book them then.
  document.querySelectorAll('[data-sch-track]').forEach((track) => {
    track.addEventListener('click', (event) => {
      if (event.target.closest('a') || event.target.closest('[data-sch-busy]')) return;
      const board = track.closest('[data-sch-board]');
      const row = track.closest('[data-sch-row]');
      const rect = track.getBoundingClientRect();
      const share = Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 0.999);
      const startHour = Number(board.getAttribute('data-start-hour'));
      const endHour = Number(board.getAttribute('data-end-hour'));
      const minutes = Math.floor((startHour * 60 + share * (endHour - startHour) * 60) / 15) * 15;
      const time = String(Math.floor(minutes / 60)).padStart(2, '0') + ':' + String(minutes % 60).padStart(2, '0');
      const params = new URLSearchParams({ new: '1', date: board.getAttribute('data-date'),
        technician_id: row.getAttribute('data-technician'), start: time });
      if (!isNew) {
        window.location.href = '/admin/schedule?' + params.toString();
        return;
      }
      form.reset();
      field('technician_user_id').value = row.getAttribute('data-technician');
      field('date').value = board.getAttribute('data-date');
      field('start_time').value = time;
      open(track);
    });
  });

  modal.querySelectorAll('[data-modal-close]').forEach((control) => {
    control.addEventListener('click', (event) => {
      event.preventDefault();
      modal.hidden = true;
      if (/[?&](new|appointment)=/.test(window.location.search)) {
        const params = new URLSearchParams(window.location.search);
        ['new', 'appointment', 'ticket_id', 'technician_id', 'start'].forEach((key) => params.delete(key));
        window.history.replaceState(null, '', '/admin/schedule' + (params.toString() ? '?' + params.toString() : ''));
      }
      if (lastTrigger && lastTrigger.focus) lastTrigger.focus();
    });
  });

  if (modal.hasAttribute('data-sch-open-on-load')) open(null); else describe();
})();
