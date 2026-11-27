// Scheduling pages (/admin/schedule and /admin/schedule/me).
// Clicking an empty spot on a technician's row opens the booking editor for
// that technician and time (to the nearest 15 minutes); "+ Book appointment"
// opens it blank. The editor writes a plain-English summary as it is filled
// in. Without JavaScript the links load the page with the editor open.
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
    const first = field('ticket_id').value ? field('technician_user_id') : field('ticket_id');
    if (first) first.focus();
  }

  form.addEventListener('input', describe);
  form.addEventListener('change', describe);

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
      if (event.target.closest('a')) return;
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
