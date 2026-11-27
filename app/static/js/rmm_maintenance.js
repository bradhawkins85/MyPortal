// Maintenance windows page (/rmm/maintenance).
// Opens the editor without a page load, shows the fields for the chosen
// schedule and scope, writes a plain-English summary as the form changes and
// filters the list. Without JavaScript the "+ Add" and "Edit" links load the
// page with the editor open instead.
(function () {
  'use strict';

  const modal = document.querySelector('[data-mw-modal]');
  const form = modal ? modal.querySelector('[data-mw-form]') : null;

  function value(name) {
    const field = form.querySelector(`[name="${name}"]:checked`) || form.querySelector(`[name="${name}"]:not([type="radio"]):not([type="checkbox"]):not([type="hidden"])`);
    return field ? field.value : '';
  }

  function selectedText(id) {
    const select = form.querySelector(id);
    return select && select.value ? select.options[select.selectedIndex].text : '';
  }

  function duration(minutes) {
    if (!minutes) return '';
    if (minutes % 1440 === 0) return `${minutes / 1440} day${minutes === 1440 ? '' : 's'}`;
    if (minutes % 60 === 0) return `${minutes / 60} hour${minutes === 60 ? '' : 's'}`;
    return `${minutes} minutes`;
  }

  function summary() {
    const schedule = value('schedule_type');
    const zone = value('timezone');
    let when = '';
    if (schedule === 'weekly') {
      const days = Array.from(form.querySelectorAll('[name="weekdays"]:checked')).map((box) => box.nextElementSibling.textContent);
      const start = value('start_time');
      const minutes = parseInt(value('duration_minutes'), 10);
      if (days.length && start && minutes) {
        when = `Every ${days.join(', ')} from ${start} for ${duration(minutes)} (${zone})`;
      }
    } else {
      const starts = value('starts_at');
      const ends = value('ends_at');
      if (starts && ends) when = `From ${starts.replace('T', ' ')} to ${ends.replace('T', ' ')} (${zone})`;
    }
    if (!when) return 'Fill in when the window happens to see a summary.';
    const scope = value('scope');
    let where = 'all of this company’s devices';
    if (scope === 'all') where = 'every company’s devices';
    if (scope === 'device') where = selectedText('#mw-device') || 'the chosen device';
    const check = selectedText('#mw-check');
    const what = check && form.querySelector('#mw-check').value ? `alerts from “${check}”` : 'all check alerts';
    return `${when}, ${what} on ${where} are held.`;
  }

  function sync() {
    const schedule = value('schedule_type') || 'once';
    form.querySelectorAll('[data-mw-when]').forEach((section) => {
      section.hidden = section.getAttribute('data-mw-when') !== schedule;
    });
    const device = form.querySelector('[data-mw-device]');
    if (device) device.hidden = value('scope') !== 'device';
    const preview = form.querySelector('[data-mw-preview]');
    if (preview) preview.textContent = summary();
  }

  let lastTrigger = null;

  function open(trigger) {
    lastTrigger = trigger || null;
    modal.hidden = false;
    sync();
    const first = form.querySelector('#mw-name');
    if (first) first.focus();
  }

  if (modal && form) {
    form.addEventListener('input', sync);
    form.addEventListener('change', sync);
    document.querySelectorAll('[data-mw-open]').forEach((trigger) => {
      trigger.addEventListener('click', (event) => {
        // A saved window is edited from its own link; only a blank editor opens in place.
        if (form.getAttribute('action') !== '/rmm/maintenance') return;
        event.preventDefault();
        open(trigger);
      });
    });
    modal.querySelectorAll('[data-modal-close]').forEach((control) => {
      control.addEventListener('click', (event) => {
        event.preventDefault();
        modal.hidden = true;
        if (window.location.search) {
          window.history.replaceState(null, '', '/rmm/maintenance');
        }
        if (lastTrigger) lastTrigger.focus();
      });
    });
    if (modal.hasAttribute('data-mw-open-on-load')) {
      open(null);
    } else {
      sync();
    }
  }

  const search = document.querySelector('[data-mw-search]');
  if (search) {
    search.addEventListener('input', () => {
      const term = search.value.trim().toLowerCase();
      let shown = 0;
      document.querySelectorAll('[data-mw-group]').forEach((group) => {
        let groupShown = 0;
        group.querySelectorAll('[data-mw-item]').forEach((item) => {
          const match = !term || (item.getAttribute('data-mw-text') || '').includes(term);
          item.hidden = !match;
          if (match) groupShown += 1;
        });
        group.hidden = groupShown === 0;
        shown += groupShown;
      });
      const empty = document.querySelector('[data-mw-no-results]');
      if (empty) empty.hidden = shown !== 0;
    });
  }
})();
