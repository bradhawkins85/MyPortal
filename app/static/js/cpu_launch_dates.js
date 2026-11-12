(function () {
  'use strict';

  const root = document.querySelector('[data-cpu-launch]');
  const modal = document.getElementById('cpu-launch-modal');
  if (!root || !modal) {
    return;
  }

  const form = modal.querySelector('[data-cpu-form]');
  const title = modal.querySelector('[data-cpu-title]');
  const subtitle = modal.querySelector('[data-cpu-subtitle]');
  const monthInput = modal.querySelector('[data-cpu-month-input]');
  const errorEl = modal.querySelector('[data-cpu-error]');
  const clearButton = modal.querySelector('[data-cpu-clear]');
  const previewAge = modal.querySelector('[data-cpu-preview-age]');
  const previewSummary = modal.querySelector('[data-cpu-preview-summary]');
  const maxMonth = monthInput.getAttribute('max') || '';
  const state = { trigger: null, initial: '', devices: 0, label: '' };

  // The JavaScript editor replaces the per-item fallback forms.
  root.querySelectorAll('[data-cpu-fallback]').forEach((el) => {
    el.hidden = true;
  });

  function parseMonth(value) {
    const match = /^(\d{4})-(\d{2})$/.exec(String(value || '').trim());
    if (!match) {
      return null;
    }
    const year = Number(match[1]);
    const month = Number(match[2]);
    if (month < 1 || month > 12) {
      return null;
    }
    return new Date(Date.UTC(year, month - 1, 1));
  }

  function ageInYears(launched) {
    const days = (Date.now() - launched.getTime()) / 86400000;
    return days <= 0 ? 0 : Math.floor(days / 365.25 + 0.5);
  }

  function plural(count, word) {
    return count + ' ' + word + (count === 1 ? '' : 's');
  }

  function validate() {
    const value = monthInput.value;
    let message = '';
    if (!value) {
      message = 'Choose the month the processor was launched.';
    } else if (!parseMonth(value)) {
      message = 'Enter the month as YYYY-MM, for example 2019-07.';
    } else if (maxMonth && value > maxMonth) {
      message = 'The launch date cannot be in the future.';
    }
    errorEl.textContent = message;
    errorEl.hidden = !message;
    monthInput.setAttribute('aria-invalid', message ? 'true' : 'false');
    return !message;
  }

  function updatePreview() {
    const launched = parseMonth(monthInput.value);
    const devices = plural(state.devices, 'device');
    if (!launched || (maxMonth && monthInput.value > maxMonth)) {
      previewAge.textContent = 'Choose a month to see the estimated age.';
      previewSummary.textContent = devices + ' with this processor will be updated when you save.';
      return;
    }
    const age = ageInYears(launched);
    const when = launched.toLocaleDateString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' });
    previewAge.textContent = 'About ' + plural(age, 'year') + ' old';
    previewSummary.textContent = devices + ' with ' + state.label + ' will show an Approx age of '
      + plural(age, 'year') + ', counted from ' + when + '.';
  }

  function openEditor(trigger) {
    const data = trigger.dataset;
    state.trigger = trigger;
    state.initial = data.cpuMonth || '';
    state.devices = Number(data.cpuDevices || 0);
    state.label = data.cpuLabel || 'this processor';
    form.setAttribute('action', '/assets/cpu-launch-dates/' + encodeURIComponent(data.cpuId));
    title.textContent = (state.initial ? 'Edit launch date: ' : 'Add launch date: ') + state.label;
    subtitle.textContent = data.cpuName && data.cpuName !== state.label
      ? 'Reported as “' + data.cpuName + '”'
      : 'Used by ' + plural(state.devices, 'device') + '.';
    monthInput.value = state.initial;
    clearButton.hidden = data.cpuSource !== 'manual';
    errorEl.hidden = true;
    errorEl.textContent = '';
    monthInput.removeAttribute('aria-invalid');
    updatePreview();
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    window.requestAnimationFrame(() => monthInput.focus());
  }

  function closeEditor(force) {
    if (!force && monthInput.value !== state.initial
      && !window.confirm('Discard the launch date you entered?')) {
      return;
    }
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (state.trigger && typeof state.trigger.focus === 'function') {
      state.trigger.focus();
    }
  }

  root.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-cpu-edit]');
    if (trigger) {
      event.preventDefault();
      openEditor(trigger);
    }
  });

  modal.querySelectorAll('[data-cpu-close]').forEach((button) => {
    button.addEventListener('click', () => closeEditor(false));
  });

  modal.addEventListener('click', (event) => {
    if (event.target === modal) {
      closeEditor(false);
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) {
      event.preventDefault();
      closeEditor(false);
    }
  });

  monthInput.addEventListener('input', () => {
    updatePreview();
    if (!errorEl.hidden) {
      validate();
    }
  });

  form.addEventListener('submit', (event) => {
    const submitter = event.submitter;
    if (submitter && submitter.value === 'clear') {
      if (!window.confirm('Clear the launch date for ' + state.label + '? Its devices keep their current age until a new date is found.')) {
        event.preventDefault();
      }
      return;
    }
    if (!validate()) {
      event.preventDefault();
      monthInput.focus();
      return;
    }
    state.initial = monthInput.value;
  });

  const search = root.querySelector('[data-cpu-search-input]');
  const noResults = root.querySelector('[data-cpu-no-results]');
  if (search) {
    search.addEventListener('input', () => {
      const terms = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      let visible = 0;
      root.querySelectorAll('[data-cpu-item]').forEach((item) => {
        const text = (item.dataset.cpuSearch || '').toLowerCase();
        const match = terms.every((term) => text.includes(term));
        item.hidden = !match;
        if (match) {
          visible += 1;
        }
      });
      root.querySelectorAll('.scf-list').forEach((list) => {
        const heading = list.previousElementSibling;
        const any = Array.from(list.children).some((child) => !child.hidden);
        list.hidden = !any;
        if (heading && heading.hasAttribute('data-cpu-group')) {
          heading.hidden = !any;
        }
      });
      if (noResults) {
        noResults.hidden = visible > 0;
      }
    });
  }
})();
