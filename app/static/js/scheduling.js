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

  // Drag a booking to another time on the same row, or to another technician's
  // row. The length and details stay the same; the server refuses a move over
  // another booking or busy calendar time, and the booking goes back. Touch needs a short press before dragging so
  // the board still scrolls.
  const boardEl = document.querySelector('[data-sch-board]');
  const moveStatus = document.querySelector('[data-sch-move-status]');
  const SNAP = 15;
  const toTime = (minutes) => String(Math.floor(minutes / 60)).padStart(2, '0') + ':' + String(minutes % 60).padStart(2, '0');
  let suppressClick = false;

  function say(text) { if (moveStatus) moveStatus.textContent = text; }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) return meta.getAttribute('content');
    const input = document.querySelector('input[name="_csrf"]');
    return input ? input.value : '';
  }

  async function postMove(id, body) {
    const response = await fetch('/api/appointments/' + encodeURIComponent(id) + '/move', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
      body: JSON.stringify(body),
    });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    return { status: response.status, ok: response.ok, data };
  }

  function errorText(data) {
    const detail = data && data.detail;
    if (!detail) return 'The appointment could not be moved.';
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return 'The appointment could not be moved.';
    return Object.values(detail).join(' ');
  }

  async function commitMove(block, body) {
    block.classList.add('sch-block--saving');
    say('Moving appointment…');
    try {
      const result = await postMove(block.getAttribute('data-sch-move'), body);
      if (!result.ok) {
        say(errorText(result.data));
        window.alert(errorText(result.data));
        window.location.reload();
        return;
      }
      say('Moved to ' + (result.data && result.data.when ? result.data.when : body.start_time) + '.');
      window.location.reload();
    } catch (error) {
      window.alert('The appointment could not be moved. Check your connection and try again.');
      window.location.reload();
    }
  }

  if (boardEl) {
    const startHour = Number(boardEl.getAttribute('data-start-hour'));
    const endHour = Number(boardEl.getAttribute('data-end-hour'));
    const dayStart = startHour * 60;
    const dayEnd = endHour * 60;
    const span = dayEnd - dayStart;
    const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    document.addEventListener('click', (event) => {
      if (!suppressClick) return;
      suppressClick = false;
      event.preventDefault();
      event.stopPropagation();
    }, true);

    boardEl.querySelectorAll('[data-sch-move]').forEach((block) => {
      const duration = Number(block.getAttribute('data-duration')) || SNAP;
      const originalStart = toMinutes(block.getAttribute('data-start'));
      const timeLabel = block.querySelector('.sch-block__time');
      const originalLabel = timeLabel ? timeLabel.textContent : '';
      let drag = null;

      function rowAt(x, y) {
        const hit = document.elementFromPoint(x, y);
        return hit ? hit.closest('[data-sch-row]') : null;
      }

      function place(x, y) {
        const row = rowAt(x, y) || drag.row;
        const track = row.querySelector('[data-sch-track]');
        const rect = track.getBoundingClientRect();
        const pointer = dayStart + ((x - rect.left) / rect.width) * span;
        let start = Math.round((pointer - drag.grabOffset) / SNAP) * SNAP;
        start = Math.min(Math.max(start, dayStart), Math.max(dayStart, dayEnd - duration));
        if (row !== drag.row) {
          drag.row.classList.remove('sch-row--drop-target');
          drag.row = row;
          track.appendChild(block);
        }
        row.classList.add('sch-row--drop-target');
        drag.start = start;
        block.style.left = (100 * (start - dayStart) / span) + '%';
        block.style.setProperty('--sch-lane', '0');
        if (timeLabel) timeLabel.textContent = toTime(start) + '–' + toTime(Math.min(start + duration, 24 * 60 - 1));
      }

      function begin() {
        drag.active = true;
        block.classList.add('sch-block--dragging');
        boardEl.classList.add('sch-board--dragging');
        if (reduceMotion) block.style.transition = 'none';
        say('Moving ' + (block.querySelector('.sch-block__title') || block).textContent.trim() + '. Release to drop, Escape to cancel.');
      }

      function restore(state) {
        state.row.classList.remove('sch-row--drop-target');
        if (state.originalParent && block.parentElement !== state.originalParent) {
          state.originalParent.insertBefore(block, state.originalNext);
        }
        block.style.left = state.originalLeft;
        block.style.setProperty('--sch-lane', state.originalLane);
        if (timeLabel) timeLabel.textContent = originalLabel;
      }

      function finish(event, cancelled) {
        if (!drag) return;
        const state = drag;
        drag = null;
        clearTimeout(state.timer);
        window.removeEventListener('keydown', state.onKey, true);
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onCancel);
        if (!state.active) return;
        suppressClick = true;
        setTimeout(() => { suppressClick = false; }, 400);
        block.classList.remove('sch-block--dragging');
        boardEl.classList.remove('sch-board--dragging');
        const technician = state.row.getAttribute('data-technician');
        const unchanged = technician === state.originalTechnician && state.start === originalStart;
        if (cancelled || unchanged || state.start === null) {
          restore(state);
          say(cancelled ? 'Move cancelled.' : '');
          return;
        }
        state.row.classList.remove('sch-row--drop-target');
        commitMove(block, { technician_user_id: Number(technician), date: boardEl.getAttribute('data-date'),
          start_time: toTime(state.start) });
      }

      block.addEventListener('pointerdown', (event) => {
        if (event.button !== 0 || block.classList.contains('sch-block--saving')) return;
        const track = block.closest('[data-sch-track]');
        const rect = track.getBoundingClientRect();
        const pointer = dayStart + ((event.clientX - rect.left) / rect.width) * span;
        const row = block.closest('[data-sch-row]');
        drag = {
          pointerId: event.pointerId, x: event.clientX, y: event.clientY, active: false, start: null,
          touch: event.pointerType === 'touch', grabOffset: pointer - originalStart, row,
          originalTechnician: row.getAttribute('data-technician'), originalParent: block.parentElement,
          originalNext: block.nextSibling, originalLeft: block.style.left,
          originalLane: block.style.getPropertyValue('--sch-lane'),
        };
        drag.onKey = (keyEvent) => {
          if (keyEvent.key === 'Escape' && drag && drag.active) {
            keyEvent.preventDefault();
            finish(keyEvent, true);
          }
        };
        window.addEventListener('keydown', drag.onKey, true);
        // Listen on the window: moving the block to another row's track drops pointer capture.
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
        window.addEventListener('pointercancel', onCancel);
        if (drag.touch) {
          drag.timer = setTimeout(() => {
            if (!drag) return;
            begin();
            place(drag.x, drag.y);
          }, 350);
        }
      });

      function onUp(event) { if (drag && event.pointerId === drag.pointerId) finish(event, false); }
      function onCancel(event) { if (drag && event.pointerId === drag.pointerId) finish(event, true); }
      function onMove(event) {
        if (!drag || event.pointerId !== drag.pointerId) return;
        if (!drag.active) {
          const moved = Math.hypot(event.clientX - drag.x, event.clientY - drag.y);
          if (drag.touch) {
            if (moved > 8) finish(event, true); // a scroll, not a drag
            return;
          }
          if (moved < 5) return;
          begin();
        }
        event.preventDefault();
        place(event.clientX, event.clientY);
      }

      // While a touch drag is under way, stop the page scrolling under the finger.
      block.addEventListener('touchmove', (event) => { if (drag && drag.active) event.preventDefault(); }, { passive: false });
      block.addEventListener('dragstart', (event) => event.preventDefault());
      block.addEventListener('contextmenu', (event) => { if (drag && drag.touch) event.preventDefault(); });
    });
  }

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
