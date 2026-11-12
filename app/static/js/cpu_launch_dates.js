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
  const dateInput = modal.querySelector('[data-cpu-date-input]');
  const urlInput = modal.querySelector('[data-cpu-url-input]');
  const sourceInputs = Array.from(modal.querySelectorAll('[data-cpu-source-input]'));
  const errorEl = modal.querySelector('[data-cpu-error]');
  const clearButton = modal.querySelector('[data-cpu-clear]');
  const arkRow = modal.querySelector('[data-cpu-ark]');
  const arkLink = modal.querySelector('[data-cpu-ark-link]');
  const previewAge = modal.querySelector('[data-cpu-preview-age]');
  const previewSummary = modal.querySelector('[data-cpu-preview-summary]');
  const state = { trigger: null, initial: '', devices: 0, label: '' };
  const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

  // The JavaScript editor replaces the per-item fallback forms.
  root.querySelectorAll('[data-cpu-fallback]').forEach((el) => {
    el.hidden = true;
  });

  function fullYear(text) {
    let year = Number(text);
    if (text.length <= 2) {
      year += year >= 70 ? 1900 : 2000;
    }
    return year;
  }

  function monthDate(year, month) {
    if (month < 1 || month > 12 || year < 1970 || year > 2100) {
      return null;
    }
    return new Date(Date.UTC(year, month - 1, 1));
  }

  // Mirrors app/services/cpu_release_dates.py::parse_release_text.
  function parseLaunch(value) {
    const text = String(value || '').trim().replace(/\s+/g, ' ');
    if (!text) {
      return null;
    }
    let m = /^Q([1-4])\s*['’`]?\s*(\d{4}|\d{2})$/i.exec(text);
    if (m) {
      const year = fullYear(m[2]);
      const when = monthDate(year, (Number(m[1]) - 1) * 3 + 1);
      return when ? { date: when, label: 'Q' + m[1] + ' ' + year } : null;
    }
    m = /^(\d{4})-(\d{1,2})(?:-\d{1,2})?$/.exec(text);
    if (m) {
      const when = monthDate(Number(m[1]), Number(m[2]));
      return when ? { date: when } : null;
    }
    m = /^(\d{1,2})\s*\/\s*(\d{4})$/.exec(text);
    if (m) {
      const when = monthDate(Number(m[2]), Number(m[1]));
      return when ? { date: when } : null;
    }
    m = /^([a-z]{3,9})\.?\s+(\d{4})$/i.exec(text);
    if (m && MONTHS.includes(m[1].slice(0, 3).toLowerCase())) {
      const when = monthDate(Number(m[2]), MONTHS.indexOf(m[1].slice(0, 3).toLowerCase()) + 1);
      return when ? { date: when } : null;
    }
    m = /^(19[89]\d|20\d{2})$/.exec(text);
    if (m) {
      return { date: new Date(Date.UTC(Number(m[1]), 0, 1)), label: m[1] };
    }
    return null;
  }

  function ageInYears(launched) {
    const days = (Date.now() - launched.getTime()) / 86400000;
    return days <= 0 ? 0 : Math.floor(days / 365.25 + 0.5);
  }

  function plural(count, word) {
    return count + ' ' + word + (count === 1 ? '' : 's');
  }

  function monthName(when) {
    return when.toLocaleDateString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' });
  }

  function validate() {
    const parsed = parseLaunch(dateInput.value);
    let message = '';
    if (!dateInput.value.trim()) {
      message = "Enter the launch date, for example Q3'19 as Intel ARK shows it.";
    } else if (!parsed) {
      message = "Use Q3'19, 2019-07 or Jul 2019.";
    } else if (parsed.date.getTime() > Date.now()) {
      message = 'The launch date cannot be in the future.';
    } else if (urlInput.value.trim() && !/^https:\/\/([a-z0-9-]+\.)*intel\.com(\/|$)/i.test(urlInput.value.trim())) {
      message = "Paste the https:// link of the processor's page on intel.com, or leave it blank.";
    }
    errorEl.textContent = message;
    errorEl.hidden = !message;
    dateInput.setAttribute('aria-invalid', message ? 'true' : 'false');
    return !message;
  }

  function updatePreview() {
    const parsed = parseLaunch(dateInput.value);
    const devices = plural(state.devices, 'device');
    if (!parsed || parsed.date.getTime() > Date.now()) {
      previewAge.textContent = 'Enter a launch date to see the estimated age.';
      previewSummary.textContent = devices + ' with this processor will be updated when you save.';
      return;
    }
    const age = ageInYears(parsed.date);
    previewAge.textContent = 'About ' + plural(age, 'year') + ' old';
    previewSummary.textContent = devices + ' with ' + state.label + ' will show an Approx age of '
      + plural(age, 'year') + ', counted from ' + monthName(parsed.date) + '.';
  }

  function snapshot() {
    const source = sourceInputs.find((input) => input.checked);
    return [dateInput.value.trim(), urlInput.value.trim(), source ? source.value : ''].join('|');
  }

  function openEditor(trigger) {
    const data = trigger.dataset;
    const isIntel = data.cpuIntel === '1';
    state.trigger = trigger;
    state.devices = Number(data.cpuDevices || 0);
    state.label = data.cpuLabel || 'this processor';
    form.setAttribute('action', '/assets/cpu-launch-dates/' + encodeURIComponent(data.cpuId));
    title.textContent = (data.cpuValue ? 'Edit launch date: ' : 'Add launch date: ') + state.label;
    subtitle.textContent = data.cpuName && data.cpuName !== state.label
      ? 'Reported as “' + data.cpuName + '”'
      : 'Used by ' + plural(state.devices, 'device') + '.';
    dateInput.value = data.cpuValue || '';
    urlInput.value = data.cpuSourceUrl || '';
    const source = data.cpuSource || (isIntel ? 'intel_ark' : 'manual');
    sourceInputs.forEach((input) => {
      input.checked = input.value === source;
    });
    arkRow.hidden = !data.cpuArkUrl;
    arkLink.href = data.cpuArkUrl || '#';
    clearButton.hidden = !data.cpuValue;
    errorEl.hidden = true;
    errorEl.textContent = '';
    dateInput.removeAttribute('aria-invalid');
    updatePreview();
    state.initial = snapshot();
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    window.requestAnimationFrame(() => dateInput.focus());
  }

  function closeEditor(force) {
    if (!force && snapshot() !== state.initial
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

  [dateInput, urlInput].forEach((input) => {
    input.addEventListener('input', () => {
      updatePreview();
      if (!errorEl.hidden) {
        validate();
      }
    });
  });

  // Pasting an intel.com page link implies the date came from Intel ARK.
  urlInput.addEventListener('change', () => {
    if (urlInput.value.trim()) {
      sourceInputs.forEach((input) => {
        input.checked = input.value === 'intel_ark';
      });
    }
  });

  form.addEventListener('submit', (event) => {
    const submitter = event.submitter;
    if (submitter && submitter.value === 'clear') {
      if (!window.confirm('Clear the launch date for ' + state.label + '? Its devices keep their current age.')) {
        event.preventDefault();
      }
      return;
    }
    if (!validate()) {
      event.preventDefault();
      dateInput.focus();
      return;
    }
    state.initial = snapshot();
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
