(function () {
  'use strict';

  const form = document.querySelector('[data-cof-form]');
  if (!form) {
    return;
  }

  const STEPS = ['business', 'sites', 'billing', 'review'];
  const DAYS = [
    ['mon', 'Mon'], ['tue', 'Tue'], ['wed', 'Wed'], ['thu', 'Thu'],
    ['fri', 'Fri'], ['sat', 'Sat'], ['sun', 'Sun'],
  ];
  const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri'];
  const maxSites = parseInt(form.dataset.cofMaxSites || '20', 10);
  const siteList = form.querySelector('[data-cof-site-list]');
  const editors = form.querySelector('[data-cof-site-editors]');
  const template = document.querySelector('[data-cof-site-template]');
  const addButton = form.querySelector('[data-cof-site-add]');
  const submitButton = form.querySelector('[data-cof-submit]');
  const errorSummary = document.querySelector('[data-cof-error-summary]');

  let currentStep = 0;
  let furthestStep = errorSummary ? STEPS.length - 1 : 0;
  let dirty = false;
  let submitting = false;
  let openModal = null;
  let lastFocus = null;

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function value(root, selector) {
    const input = root.querySelector(selector);
    return input ? input.value.trim() : '';
  }

  function isValidEmail(text) {
    const email = String(text || '').trim();
    if (!email || email.length > 255 || /\s/.test(email)) {
      return false;
    }
    const at = email.indexOf('@');
    if (at < 1 || email.indexOf('@', at + 1) !== -1) {
      return false;
    }
    const labels = email.slice(at + 1).split('.');
    return labels.length >= 2 && labels.every((label) => label.length > 0);
  }

  function minutes(text) {
    const match = /^(\d{1,2}):(\d{2})$/.exec(String(text || '').trim());
    if (!match) {
      return null;
    }
    const hours = parseInt(match[1], 10);
    const mins = parseInt(match[2], 10);
    if (hours > 23 || mins > 59) {
      return null;
    }
    return hours * 60 + mins;
  }

  function closing(text) {
    const value = minutes(text);
    return value === 0 ? 24 * 60 : value;
  }

  function formatTime(text) {
    const total = minutes(text);
    if (total === null) {
      return text;
    }
    const hours = Math.floor(total / 60);
    const mins = total % 60;
    const suffix = hours < 12 ? 'am' : 'pm';
    const display = hours % 12 === 0 ? 12 : hours % 12;
    return mins ? `${display}:${String(mins).padStart(2, '0')}${suffix}` : `${display}${suffix}`;
  }

  function setError(root, key, message) {
    const target = root.querySelector(`[data-cof-error="${key}"]`);
    const input = root.querySelector(`[data-cof-field="${key}"]`);
    if (target) {
      target.textContent = message || '';
      target.hidden = !message;
      if (input && target.id === '') {
        target.id = `cof-error-${Math.random().toString(36).slice(2, 9)}`;
      }
    }
    if (input) {
      input.classList.toggle('is-invalid', Boolean(message));
      input.setAttribute('aria-invalid', message ? 'true' : 'false');
      if (target && message) {
        input.setAttribute('aria-describedby', target.id);
      } else if (target) {
        input.removeAttribute('aria-describedby');
      }
    }
    return !message;
  }

  // Each question owns its error and controls, including checkbox/radio groups.
  // Step validation skips site questions: validateSite checks those in their tabs.
  function customQuestions(root) {
    const owner = root.closest('[data-cof-site-modal]');
    return Array.from(root.querySelectorAll('[data-cof-custom-question]'))
      .filter((question) => question.closest('[data-cof-site-modal]') === owner);
  }

  function isValidCustomUrl(text) {
    try {
      const address = new URL(text);
      return ['http:', 'https:'].includes(address.protocol)
        && Boolean(address.hostname) && !address.username && !address.password
        && !/\s|\\/.test(text);
    } catch (error) {
      return false;
    }
  }

  function setCustomError(question, message) {
    const error = question.querySelector('[data-cof-custom-error]');
    error.textContent = message || '';
    error.hidden = !message;
    question.querySelectorAll('[data-cof-custom-control]').forEach((input) => {
      input.classList.toggle('is-invalid', Boolean(message));
      input.setAttribute('aria-invalid', message ? 'true' : 'false');
    });
  }

  function validateCustomQuestions(root, show) {
    let firstBad = null;
    customQuestions(root).forEach((question) => {
      const controls = Array.from(question.querySelectorAll('[data-cof-custom-control]'));
      const input = controls[0];
      const type = question.dataset.cofCustomType;
      const required = question.dataset.cofCustomRequired === 'true';
      let message = '';
      if (type === 'multi_select' || type === 'radio') {
        if (required && !controls.some((control) => control.checked)) {
          message = type === 'multi_select' ? 'Select at least one option.' : 'Select an option.';
        }
      } else if (type === 'checkbox') {
        if (required && !input.checked) {
          message = 'Tick the box to answer this question.';
        }
      } else if (required && !input.value.trim()) {
        message = type === 'dropdown' ? 'Select an option.' : 'Answer this question.';
      } else if (input.value && type === 'email' && !isValidEmail(input.value)) {
        message = 'Enter a valid email address, like name@example.com.';
      } else if (input.value && type === 'phone'
        && (!/^\+?[0-9().\- ]+(?: *(?:x|ext\.?) *[0-9]{1,8})?$/i.test(input.value.trim())
          || (input.value.match(/[0-9]/g) || []).length < 3
          || (input.value.match(/[0-9]/g) || []).length > 20)) {
        message = 'Enter a valid phone number.';
      } else if (input.value && type === 'url' && !isValidCustomUrl(input.value.trim())) {
        message = 'Enter a valid website URL beginning with http:// or https://.';
      } else if (input.maxLength > -1 && input.value.length > input.maxLength) {
        message = `Use no more than ${input.maxLength} characters.`;
      } else if (!input.validity.valid) {
        message = input.validationMessage || 'Enter a valid answer.';
      }
      if (show) {
        setCustomError(question, message);
      }
      if (message && !firstBad) {
        firstBad = question;
      }
    });
    return firstBad;
  }

  function customReviewRows(root) {
    return customQuestions(root).map((question) => {
      const controls = Array.from(question.querySelectorAll('[data-cof-custom-control]'));
      const type = question.dataset.cofCustomType;
      let answer;
      if (type === 'checkbox') {
        answer = controls[0].checked ? 'Yes' : 'No';
      } else if (type === 'multi_select' || type === 'radio') {
        answer = controls.filter((control) => control.checked).map((control) => control.value).join(', ');
      } else {
        answer = controls[0].value.trim();
      }
      return [question.dataset.cofCustomLabel, answer];
    });
  }

  function focusStepError(name) {
    const step = form.querySelector(`[data-cof-step="${name}"]`);
    const firstError = Array.from(step.querySelectorAll('.is-invalid'))
      .find((input) => !input.closest('[data-cof-site-modal]'));
    if (firstError) {
      firstError.focus();
    } else if (name === 'sites') {
      const incomplete = siteModals().find((modal) => validateSite(modal, false) !== null);
      if (incomplete) {
        open(incomplete, { tab: validateSite(incomplete, true) });
      } else if (addButton) {
        addButton.focus();
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Sites
  // ---------------------------------------------------------------------------

  function siteModals() {
    return Array.from(editors.querySelectorAll('[data-cof-site-modal]'));
  }

  function siteItem(index) {
    return siteList.querySelector(`[data-cof-site-item="${index}"]`);
  }

  function siteModal(index) {
    return editors.querySelector(`[data-cof-site-modal="${index}"]`);
  }

  function siteField(modal, key) {
    return value(modal, `[data-cof-field="${key}"]`);
  }

  function dayRows(modal) {
    return Array.from(modal.querySelectorAll('[data-cof-day]'));
  }

  function readDay(row) {
    const open = row.querySelector('[data-cof-day-open]').checked;
    const time = (key) => row.querySelector(`[data-cof-time="${key}"]`).value;
    const windows = [];
    if (open) {
      windows.push([time('start'), time('end')]);
      const breakSpan = row.querySelector('[data-cof-break]');
      if (!breakSpan.hasAttribute('data-cof-break-empty') && (time('start2') || time('end2'))) {
        windows.push([time('start2'), time('end2')]);
      }
    }
    return { key: row.dataset.cofDay, open, windows };
  }

  function hoursSummary(modal) {
    const days = dayRows(modal).map(readDay);
    const label = (day) => (day.open && day.windows.length
      ? day.windows.map((w) => `${formatTime(w[0])}–${formatTime(w[1])}`).join(', ')
      : 'closed');
    const groups = [];
    days.forEach((day, position) => {
      const text = label(day);
      const last = groups[groups.length - 1];
      if (last && last.text === text) {
        last.end = position;
      } else {
        groups.push({ start: position, end: position, text });
      }
    });
    if (groups.length === 1) {
      return groups[0].text === 'closed' ? 'Closed every day' : `Every day ${groups[0].text}`;
    }
    return groups
      .map((group) => {
        const first = DAYS[group.start][1];
        const range = group.start === group.end ? first : `${first}–${DAYS[group.end][1]}`;
        return `${range} ${group.text}`;
      })
      .join(' · ');
  }

  function validateSite(modal, show) {
    let firstBadTab = null;
    const fail = (tab, key, message) => {
      if (show) {
        setError(modal, key, message);
      }
      firstBadTab = firstBadTab || tab;
    };
    const pass = (key) => {
      if (show) {
        setError(modal, key, '');
      }
    };

    const name = siteField(modal, 'name');
    const others = siteModals()
      .filter((other) => other !== modal)
      .map((other) => siteField(other, 'name').toLowerCase())
      .filter(Boolean);
    if (!name) {
      fail('address', 'name', 'Enter a name for this site.');
    } else if (others.includes(name.toLowerCase())) {
      fail('address', 'name', 'Each site needs a different name.');
    } else {
      pass('name');
    }
    if (siteField(modal, 'street')) {
      pass('street');
    } else {
      fail('address', 'street', 'Enter the street address.');
    }
    ['contact_first_name', 'contact_last_name'].forEach((key) => {
      if (siteField(modal, key)) {
        pass(key);
      } else {
        fail('contact', key, key === 'contact_first_name' ? 'Enter a first name.' : 'Enter a last name.');
      }
    });
    const email = siteField(modal, 'contact_email');
    if (!email) {
      fail('contact', 'contact_email', 'Enter an email address.');
    } else if (!isValidEmail(email)) {
      fail('contact', 'contact_email', 'Enter a valid email address, like name@example.com.');
    } else {
      pass('contact_email');
    }

    dayRows(modal).forEach((row) => {
      const day = readDay(row);
      const name = DAYS.find((entry) => entry[0] === day.key)[1];
      let message = '';
      if (day.open) {
        const [start, end] = day.windows[0];
        if (minutes(start) === null || closing(end) === null) {
          message = `${name}: enter opening and closing times.`;
        } else if (closing(end) <= minutes(start)) {
          message = `${name}: the closing time must be after the opening time.`;
        } else if (day.windows[1]) {
          const [start2, end2] = day.windows[1];
          if (minutes(start2) === null || closing(end2) === null) {
            message = `${name}: enter both times after the break, or remove the break.`;
          } else if (minutes(start2) < closing(end) || closing(end2) <= minutes(start2)) {
            message = `${name}: the times after the break must come after the first period.`;
          }
        }
      }
      if (message) {
        fail('hours', `day-${day.key}`, message);
      } else {
        pass(`day-${day.key}`);
      }
    });

    const customBad = validateCustomQuestions(modal, show);
    if (customBad) {
      firstBadTab = firstBadTab || customBad.closest('[data-cof-panel]').dataset.cofPanel;
    }

    if (show) {
      modal.querySelectorAll('[data-cof-tab]').forEach((tab) => {
        const panel = tab.dataset.cofTab;
        const hasError = Boolean(modal.querySelector(`[data-cof-panel="${panel}"] .scf-error:not([hidden])`));
        tab.classList.toggle('has-error', hasError);
      });
    }
    return firstBadTab;
  }

  function refreshPreview(modal) {
    const name = siteField(modal, 'name');
    const address = [
      siteField(modal, 'street'),
      [siteField(modal, 'city'), siteField(modal, 'state'), siteField(modal, 'postcode')].filter(Boolean).join(' '),
      siteField(modal, 'country'),
    ].filter(Boolean).join(', ');
    const contactName = [siteField(modal, 'contact_first_name'), siteField(modal, 'contact_last_name')].filter(Boolean).join(' ');
    const contact = [contactName, siteField(modal, 'contact_email')].filter(Boolean).join(' · ');
    const set = (key, text) => {
      const target = modal.querySelector(`[data-cof-preview="${key}"]`);
      if (target) {
        target.textContent = text;
      }
    };
    set('name', name || 'New site');
    set('address', address);
    set('contact', contact ? `Contact: ${contact}` : '');
    set('hours', hoursSummary(modal));
    set('timezone', `Time zone: ${(siteField(modal, 'timezone') || '').replace(/_/g, ' ')}`);
    const title = modal.querySelector('[data-cof-editor-title]');
    if (title) {
      title.textContent = name || 'Add a site';
    }
    modal.querySelectorAll('[data-cof-tab]').forEach((tab) => {
      const panel = modal.querySelector(`[data-cof-panel="${tab.dataset.cofTab}"]`);
      const filled = Array.from(panel.querySelectorAll('[data-cof-field][required]')).every((input) => input.value.trim())
        && !validateCustomQuestions(panel, false);
      tab.classList.toggle('is-configured', filled);
    });
    return { name, address, contactName, email: siteField(modal, 'contact_email') };
  }

  function refreshItem(modal) {
    const index = modal.dataset.cofSiteModal;
    const item = siteItem(index);
    if (!item) {
      return;
    }
    const summary = refreshPreview(modal);
    const complete = validateSite(modal, false) === null;
    item.classList.toggle('is-incomplete', !complete);
    item.querySelector('[data-cof-site-name]').textContent = summary.name || 'New site';
    item.querySelector('[data-cof-site-address]').textContent = summary.address || 'Add the address, primary contact and opening hours.';
    const chips = item.querySelector('[data-cof-site-chips]');
    chips.replaceChildren();
    if (!complete) {
      chips.append(el('li', 'scf-chip cof-chip--warning', 'Needs details'));
    }
    if (summary.contactName) {
      chips.append(el('li', 'scf-chip', summary.contactName));
    }
    chips.append(el('li', 'scf-chip scf-chip--logic', hoursSummary(modal)));
    const zone = siteField(modal, 'timezone');
    if (zone) {
      chips.append(el('li', 'scf-chip', zone.replace(/_/g, ' ')));
    }
  }

  function renumberSites() {
    const modals = siteModals();
    modals.forEach((modal, position) => {
      const number = modal.querySelector('[data-cof-site-number]');
      if (number) {
        number.textContent = String(position + 1);
      }
      const item = siteItem(modal.dataset.cofSiteModal);
      if (item) {
        item.querySelector('[data-cof-site-role]').textContent = position === 0 ? 'Main site' : `Site ${position + 1}`;
        siteList.append(item);
      }
    });
    const count = modals.length;
    const empty = form.querySelector('[data-cof-sites-empty]');
    if (empty) {
      empty.hidden = count > 0;
    }
    if (addButton) {
      addButton.disabled = count >= maxSites;
      addButton.textContent = count ? '+ Add another site' : '+ Add your first site';
    }
    const counter = form.querySelector('[data-cof-site-count]');
    if (counter) {
      counter.textContent = count >= maxSites
        ? `You've added the maximum of ${maxSites} sites.`
        : `${count} ${count === 1 ? 'site' : 'sites'} added`;
    }
    refreshBillingLabel();
  }

  function snapshot(modal) {
    return Array.from(modal.querySelectorAll('input, select, textarea')).map((input) => ({
      input,
      value: input.value,
      checked: input.checked,
      selected: input.options ? Array.from(input.options).map((option) => option.selected) : null,
    }));
  }

  function restore(modal, saved) {
    saved.forEach((entry) => {
      entry.input.value = entry.value;
      entry.input.checked = entry.checked;
      if (entry.selected) {
        Array.from(entry.input.options).forEach((option, index) => { option.selected = entry.selected[index]; });
      }
    });
    dayRows(modal).forEach((row) => {
      const breakSpan = row.querySelector('[data-cof-break]');
      const has = row.querySelector('[data-cof-time="start2"]').value || row.querySelector('[data-cof-time="end2"]').value;
      breakSpan.toggleAttribute('data-cof-break-empty', !has);
      syncDay(row);
    });
    modal.querySelectorAll('.scf-error').forEach((error) => { error.hidden = true; });
    modal.querySelectorAll('.is-invalid').forEach((input) => input.classList.remove('is-invalid'));
    modal.querySelectorAll('[aria-invalid]').forEach((input) => input.removeAttribute('aria-invalid'));
    modal.querySelectorAll('.has-error').forEach((tab) => tab.classList.remove('has-error'));
  }

  function selectTab(modal, name, focus) {
    modal.querySelectorAll('[data-cof-tab]').forEach((tab) => {
      const active = tab.dataset.cofTab === name;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      tab.tabIndex = active ? 0 : -1;
      if (active && focus) {
        tab.focus();
      }
    });
    modal.querySelectorAll('[data-cof-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.cofPanel !== name;
    });
  }

  function focusable(modal) {
    return Array.from(modal.querySelectorAll(
      'button:not([disabled]), [href], input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )).filter((node) => node.offsetParent !== null);
  }

  function open(modal, options = {}) {
    lastFocus = document.activeElement;
    modal.cofSnapshot = snapshot(modal);
    modal.cofIsNew = Boolean(options.isNew);
    modal.hidden = false;
    document.body.classList.add('scf-modal-open');
    openModal = modal;
    selectTab(modal, options.tab || 'address', false);
    refreshPreview(modal);
    const first = modal.querySelector(`[data-cof-panel="${options.tab || 'address'}"] input:not([type="hidden"]), [data-cof-panel="${options.tab || 'address'}"] select, [data-cof-panel="${options.tab || 'address'}"] textarea`);
    window.requestAnimationFrame(() => (first || modal.querySelector('.modal__close')).focus());
  }

  function close(modal) {
    modal.hidden = true;
    document.body.classList.remove('scf-modal-open');
    openModal = null;
    if (lastFocus && document.contains(lastFocus)) {
      lastFocus.focus();
    } else if (addButton) {
      addButton.focus();
    }
  }

  function cancel(modal) {
    if (modal.cofIsNew) {
      removeSite(modal, false);
      return;
    }
    restore(modal, modal.cofSnapshot || []);
    refreshItem(modal);
    close(modal);
  }

  function save(modal) {
    const badTab = validateSite(modal, true);
    if (badTab) {
      selectTab(modal, badTab, false);
      const firstError = modal.querySelector(`[data-cof-panel="${badTab}"] .is-invalid, [data-cof-panel="${badTab}"] .scf-error:not([hidden])`);
      if (firstError && firstError.focus) {
        firstError.focus();
      }
      return;
    }
    modal.cofIsNew = false;
    refreshItem(modal);
    close(modal);
    setError(form, 'sites', '');
    dirty = true;
  }

  function removeSite(modal, ask) {
    const name = siteField(modal, 'name') || 'this site';
    if (ask && !window.confirm(`Remove ${name}? Its details will be cleared.`)) {
      return;
    }
    const item = siteItem(modal.dataset.cofSiteModal);
    if (item) {
      item.remove();
    }
    close(modal);
    modal.remove();
    renumberSites();
    dirty = true;
  }

  function syncDay(row) {
    const openToggle = row.querySelector('[data-cof-day-open]');
    row.classList.toggle('is-closed', !openToggle.checked);
    row.querySelector('[data-cof-day-state]').textContent = openToggle.checked ? 'Open' : 'Closed';
  }

  function setDay(row, open, start, end) {
    row.querySelector('[data-cof-day-open]').checked = open;
    if (open) {
      row.querySelector('[data-cof-time="start"]').value = start;
      row.querySelector('[data-cof-time="end"]').value = end;
    }
    clearBreak(row);
    syncDay(row);
  }

  function clearBreak(row) {
    row.querySelector('[data-cof-time="start2"]').value = '';
    row.querySelector('[data-cof-time="end2"]').value = '';
    row.querySelector('[data-cof-break]').setAttribute('data-cof-break-empty', '');
  }

  function applyPreset(modal, preset) {
    const rows = dayRows(modal);
    const byKey = (key) => rows.find((row) => row.dataset.cofDay === key);
    if (preset === 'weekdays-9-5' || preset === 'weekdays-830-5') {
      const start = preset === 'weekdays-9-5' ? '09:00' : '08:30';
      rows.forEach((row) => setDay(row, WEEKDAYS.includes(row.dataset.cofDay), start, '17:00'));
    } else if (preset === 'always') {
      rows.forEach((row) => setDay(row, true, '00:00', '00:00'));
    } else if (preset === 'copy-monday') {
      const monday = byKey('mon');
      const source = readDay(monday);
      const breakEmpty = monday.querySelector('[data-cof-break]').hasAttribute('data-cof-break-empty');
      WEEKDAYS.slice(1).forEach((key) => {
        const row = byKey(key);
        ['start', 'end', 'start2', 'end2'].forEach((field) => {
          row.querySelector(`[data-cof-time="${field}"]`).value = monday.querySelector(`[data-cof-time="${field}"]`).value;
        });
        row.querySelector('[data-cof-day-open]').checked = source.open;
        row.querySelector('[data-cof-break]').toggleAttribute('data-cof-break-empty', breakEmpty);
        syncDay(row);
      });
    }
    validateSite(modal, true);
    refreshPreview(modal);
  }

  function initEditor(modal) {
    const editor = modal.querySelector('.cof-editor');
    if (editor) {
      editor.classList.add('is-enhanced');
    }
    dayRows(modal).forEach(syncDay);

    modal.addEventListener('click', (event) => {
      if (event.target === modal) {
        cancel(modal);
        return;
      }
      const target = event.target.closest('button');
      if (!target) {
        return;
      }
      if (target.matches('[data-cof-modal-cancel]')) {
        cancel(modal);
      } else if (target.matches('[data-cof-site-save]')) {
        save(modal);
      } else if (target.matches('[data-cof-site-remove]')) {
        removeSite(modal, true);
      } else if (target.matches('[data-cof-tab]')) {
        selectTab(modal, target.dataset.cofTab, true);
      } else if (target.matches('[data-cof-preset]')) {
        applyPreset(modal, target.dataset.cofPreset);
      } else if (target.matches('[data-cof-break-add]')) {
        const row = target.closest('[data-cof-day]');
        row.querySelector('[data-cof-break]').removeAttribute('data-cof-break-empty');
        row.querySelector('[data-cof-time="start2"]').focus();
      } else if (target.matches('[data-cof-break-remove]')) {
        const row = target.closest('[data-cof-day]');
        clearBreak(row);
        row.querySelector('[data-cof-break-add]').focus();
        refreshPreview(modal);
      }
    });

    modal.querySelector('[role="tablist"]').addEventListener('keydown', (event) => {
      const tabs = Array.from(modal.querySelectorAll('[data-cof-tab]'));
      const index = tabs.indexOf(document.activeElement);
      if (index === -1) {
        return;
      }
      let next = null;
      if (event.key === 'ArrowRight') {
        next = tabs[(index + 1) % tabs.length];
      } else if (event.key === 'ArrowLeft') {
        next = tabs[(index - 1 + tabs.length) % tabs.length];
      } else if (event.key === 'Home') {
        next = tabs[0];
      } else if (event.key === 'End') {
        next = tabs[tabs.length - 1];
      }
      if (next) {
        event.preventDefault();
        selectTab(modal, next.dataset.cofTab, true);
      }
    });

    modal.addEventListener('input', () => refreshPreview(modal));
    modal.addEventListener('change', (event) => {
      if (event.target.matches('[data-cof-day-open]')) {
        syncDay(event.target.closest('[data-cof-day]'));
      }
      refreshPreview(modal);
    });
    // Clear a field's error as soon as it's edited. Re-checking on blur instead
    // moves the layout under the pointer and swallows clicks on Save.
    modal.addEventListener('input', (event) => {
      const field = event.target.dataset ? event.target.dataset.cofField : null;
      if (field) {
        setError(modal, field, '');
      }
      const day = event.target.closest('[data-cof-day]');
      if (day) {
        setError(modal, `day-${day.dataset.cofDay}`, '');
      }
      modal.querySelectorAll('[data-cof-tab]').forEach((tab) => {
        const panel = modal.querySelector(`[data-cof-panel="${tab.dataset.cofTab}"]`);
        tab.classList.toggle('has-error', Boolean(panel.querySelector('.scf-error:not([hidden])')));
      });
    });
  }

  function addSite() {
    if (!template || siteModals().length >= maxSites) {
      return;
    }
    const used = siteModals().map((modal) => parseInt(modal.dataset.cofSiteModal, 10) || 0);
    const index = String(used.length ? Math.max(...used) + 1 : 0);
    const markup = template.innerHTML
      .split('__INDEX__').join(index)
      .split('__NUMBER__').join(String(siteModals().length + 1));
    const holder = document.createElement('template');
    holder.innerHTML = markup;
    const item = holder.content.querySelector('[data-cof-site-item]');
    const modal = holder.content.querySelector('[data-cof-site-modal]');
    siteList.append(item);
    editors.append(modal);
    initEditor(modal);
    renumberSites();
    refreshItem(modal);
    open(modal, { isNew: true });
  }

  // ---------------------------------------------------------------------------
  // Billing
  // ---------------------------------------------------------------------------

  function billingSame() {
    const choice = form.querySelector('[data-cof-billing-mode]:checked');
    return Boolean(choice && choice.value);
  }

  function refreshBillingLabel() {
    const label = form.querySelector('[data-cof-billing-same-label]');
    const first = siteModals()[0];
    if (!label) {
      return;
    }
    if (!first) {
      label.textContent = 'Use the primary contact for your first site.';
      return;
    }
    const name = [siteField(first, 'contact_first_name'), siteField(first, 'contact_last_name')].filter(Boolean).join(' ');
    const site = siteField(first, 'name');
    label.textContent = name
      ? `Use ${name}${site ? `, the contact for ${site}` : ''}.`
      : 'Use the primary contact for your first site.';
  }

  function syncBilling() {
    const fields = form.querySelector('[data-cof-billing-fields]');
    fields.hidden = billingSame();
    fields.querySelectorAll('input').forEach((input) => { input.disabled = billingSame(); });
  }

  // ---------------------------------------------------------------------------
  // Steps
  // ---------------------------------------------------------------------------

  function validateStep(name, show) {
    const section = form.querySelector(`[data-cof-step="${name}"]`);
    const customOk = !validateCustomQuestions(section, show);
    if (name === 'business') {
      const nameOk = Boolean(value(form, '#client_name'));
      const phoneOk = Boolean(value(form, '#company_phone'));
      const email = value(form, '#company_email');
      const emailMessage = !email
        ? 'Enter your general email address.'
        : (isValidEmail(email) ? '' : 'Enter a valid email address, like hello@example.com.');
      if (show) {
        setError(form, 'client_name', nameOk ? '' : 'Enter your business name.');
        setError(form, 'company_phone', phoneOk ? '' : 'Enter your main phone number.');
        setError(form, 'company_email', emailMessage);
      }
      return nameOk && phoneOk && !emailMessage && customOk;
    }
    if (name === 'sites') {
      const modals = siteModals();
      if (!modals.length) {
        if (show) {
          setError(form, 'sites', 'Add at least one site.');
        }
        return false;
      }
      const incomplete = modals.filter((modal) => validateSite(modal, false) !== null);
      modals.forEach(refreshItem);
      if (show) {
        setError(form, 'sites', incomplete.length
          ? `Finish the details for ${incomplete.map((modal) => siteField(modal, 'name') || 'your new site').join(', ')}.`
          : '');
      }
      return incomplete.length === 0 && customOk;
    }
    if (name === 'billing') {
      if (billingSame()) {
        return siteModals().length > 0 && customOk;
      }
      let ok = true;
      [['billing_first_name', 'Enter a first name.'], ['billing_last_name', 'Enter a last name.']].forEach(([key, message]) => {
        const missing = !value(form, `[data-cof-field="${key}"]`);
        if (show) {
          setError(form, key, missing ? message : '');
        }
        ok = ok && !missing;
      });
      const email = value(form, '[data-cof-field="billing_email"]');
      const emailMessage = !email ? 'Enter an email address for invoices.' : (isValidEmail(email) ? '' : 'Enter a valid email address, like accounts@example.com.');
      if (show) {
        setError(form, 'billing_email', emailMessage);
      }
      return ok && !emailMessage && customOk;
    }
    if (name === 'review') {
      const confirmed = form.querySelector('[data-cof-field="confirm_details"]').checked;
      if (show) {
        setError(form, 'confirm_details', confirmed ? '' : 'Tick the box to confirm your details are correct.');
      }
      return confirmed && customOk;
    }
    return customOk;
  }

  function renderSteps() {
    STEPS.forEach((name, index) => {
      const section = form.querySelector(`[data-cof-step="${name}"]`);
      section.classList.toggle('is-current', index === currentStep);
      const indicator = document.querySelector(`[data-cof-step-indicator="${name}"]`);
      if (!indicator) {
        return;
      }
      const valid = validateStep(name, false);
      const complete = index !== currentStep && index <= furthestStep && valid;
      if (valid) {
        indicator.classList.remove('has-error');
      }
      indicator.classList.toggle('is-current', index === currentStep);
      indicator.classList.toggle('is-complete', complete && index !== currentStep);
      const button = indicator.querySelector('button');
      button.disabled = index > furthestStep;
      if (index === currentStep) {
        button.setAttribute('aria-current', 'step');
      } else {
        button.removeAttribute('aria-current');
      }
      indicator.querySelector('[data-cof-step-status]').textContent = index === currentStep
        ? ' (current step)'
        : (complete ? ' (completed)' : '');
    });
  }

  function goTo(index, focusHeading = true) {
    currentStep = Math.max(0, Math.min(index, STEPS.length - 1));
    furthestStep = Math.max(furthestStep, currentStep);
    if (STEPS[currentStep] === 'review') {
      renderReview();
    }
    renderSteps();
    if (focusHeading) {
      const heading = form.querySelector(`#cof-step-${STEPS[currentStep]}-title`);
      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      (document.querySelector('.cof-steps') || form).scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
      if (heading) {
        heading.focus({ preventScroll: true });
      }
    }
  }

  function next() {
    const name = STEPS[currentStep];
    if (!validateStep(name, true)) {
      focusStepError(name);
      return;
    }
    goTo(currentStep + 1);
  }

  // ---------------------------------------------------------------------------
  // Review
  // ---------------------------------------------------------------------------

  function reviewSection(title, step) {
    const section = el('section', 'cof-review__section');
    const head = el('div', 'cof-review__head');
    head.append(el('h3', 'cof-review__title', title));
    const edit = el('button', 'button button--ghost button--small', 'Edit');
    edit.type = 'button';
    edit.append(el('span', 'visually-hidden', ` ${title.toLowerCase()}`));
    edit.addEventListener('click', () => goTo(STEPS.indexOf(step)));
    head.append(edit);
    section.append(head);
    return section;
  }

  function reviewList(rows) {
    const list = el('dl', 'cof-review__list');
    rows.forEach(([label, text]) => {
      list.append(el('dt', null, label));
      const dd = el('dd', text ? null : 'cof-review__missing', text || 'Not provided');
      list.append(dd);
    });
    return list;
  }

  function renderReview() {
    const target = form.querySelector('[data-cof-review]');
    if (!target) {
      return;
    }
    target.replaceChildren();

    const business = reviewSection('Your business', 'business');
    business.append(reviewList([
      ['Business name', value(form, '#client_name')],
      ['Main phone', value(form, '#company_phone')],
      ['General email', value(form, '#company_email')],
      ['Website', value(form, '#website')],
      ...customReviewRows(form.querySelector('[data-cof-step="business"]')),
    ]));
    target.append(business);

    const sites = reviewSection(siteModals().length === 1 ? 'Site' : 'Sites', 'sites');
    const siteQuestions = customReviewRows(form.querySelector('[data-cof-step="sites"]'));
    if (siteQuestions.length) {
      sites.append(reviewList(siteQuestions));
    }
    siteModals().forEach((modal, position) => {
      const block = el('div', 'cof-review__site');
      const summary = refreshPreview(modal);
      block.append(el('p', 'cof-review__site-name', `${summary.name || 'New site'}${position === 0 ? ' (main site)' : ''}`));
      block.append(reviewList([
        ['Address', summary.address],
        ['Phone', siteField(modal, 'phone')],
        ['Primary contact', [summary.contactName, summary.email, siteField(modal, 'contact_phone')].filter(Boolean).join(' · ')],
        ['Opening hours', hoursSummary(modal)],
        ['Time zone', (siteField(modal, 'timezone') || '').replace(/_/g, ' ')],
        ...customReviewRows(modal),
      ]));
      sites.append(block);
    });
    target.append(sites);

    const billing = reviewSection('Billing contact', 'billing');
    if (billingSame() && siteModals()[0]) {
      const first = siteModals()[0];
      billing.append(reviewList([
        ['Name', [siteField(first, 'contact_first_name'), siteField(first, 'contact_last_name')].filter(Boolean).join(' ')],
        ['Email', siteField(first, 'contact_email')],
        ['Payment terms', 'Invoices issued in advance, due within 7 days'],
      ]));
    } else {
      billing.append(reviewList([
        ['Name', [value(form, '#billing_first_name'), value(form, '#billing_last_name')].filter(Boolean).join(' ')],
        ['Email', value(form, '#billing_email')],
        ['Phone', value(form, '#billing_phone')],
        ['Payment terms', 'Invoices issued in advance, due within 7 days'],
      ]));
    }
    const billingQuestions = customReviewRows(form.querySelector('[data-cof-step="billing"]'));
    if (billingQuestions.length) {
      billing.append(reviewList(billingQuestions));
    }
    target.append(billing);
  }

  // ---------------------------------------------------------------------------
  // Wire up
  // ---------------------------------------------------------------------------

  form.classList.add('is-enhanced');
  siteModals().forEach((modal) => {
    initEditor(modal);
    refreshItem(modal);
  });
  renumberSites();
  syncBilling();

  siteList.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-cof-site-edit]');
    if (trigger) {
      const modal = siteModal(trigger.dataset.cofSiteEdit);
      if (modal) {
        const badTab = validateSite(modal, false);
        open(modal, { tab: badTab || 'address' });
        if (badTab) {
          validateSite(modal, true);
        }
      }
    }
  });
  if (addButton) {
    addButton.addEventListener('click', addSite);
  }

  form.querySelectorAll('[data-cof-next]').forEach((button) => button.addEventListener('click', next));
  form.querySelectorAll('[data-cof-back]').forEach((button) => button.addEventListener('click', () => goTo(currentStep - 1)));
  document.querySelectorAll('[data-cof-goto]').forEach((button) => {
    button.addEventListener('click', () => {
      const targetStep = STEPS.indexOf(button.dataset.cofGoto);
      for (let index = currentStep; index < targetStep; index += 1) {
        if (!validateStep(STEPS[index], true)) {
          if (currentStep !== index) {
            goTo(index);
          }
          focusStepError(STEPS[index]);
          return;
        }
      }
      goTo(targetStep);
    });
  });
  form.querySelectorAll('[data-cof-billing-mode]').forEach((radio) => radio.addEventListener('change', syncBilling));

  const notes = form.querySelector('[data-cof-notes]');
  const notesCounter = form.querySelector('[data-cof-notes-counter]');
  const countNotes = () => {
    if (notes && notesCounter) {
      notesCounter.textContent = `${notes.value.length} / ${notes.maxLength}`;
    }
  };
  if (notes) {
    notes.addEventListener('input', countNotes);
    countNotes();
  }

  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
  const clearCustomError = (event) => {
    const question = event.target.closest('[data-cof-custom-question]');
    if (question) {
      setCustomError(question, '');
      const modal = question.closest('[data-cof-site-modal]');
      if (modal) {
        const panel = question.closest('[data-cof-panel]');
        modal.querySelector(`[data-cof-tab="${panel.dataset.cofPanel}"]`)
          .classList.toggle('has-error', Boolean(panel.querySelector('.scf-error:not([hidden])')));
      }
    }
  };
  form.addEventListener('input', clearCustomError);
  form.addEventListener('change', clearCustomError);
  // Clear a field's error as soon as it's edited (see the site editor note).
  form.addEventListener('input', (event) => {
    const field = event.target.dataset ? event.target.dataset.cofField : null;
    if (field && !event.target.closest('[data-cof-site-modal]')) {
      setError(form, field, '');
    }
  });
  form.querySelector('[data-cof-field="confirm_details"]').addEventListener('change', () => {
    if (!form.querySelector('[data-cof-error="confirm_details"]').hidden) {
      validateStep('review', true);
    }
  });

  // Enter in a text input moves to the next step instead of submitting early.
  form.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.target.tagName !== 'INPUT' || openModal) {
      return;
    }
    if (STEPS[currentStep] !== 'review') {
      event.preventDefault();
      next();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (!openModal) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      cancel(openModal);
    } else if (event.key === 'Tab') {
      const nodes = focusable(openModal);
      if (!nodes.length) {
        return;
      }
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    } else if (event.key === 'Enter' && event.target.tagName === 'INPUT' && event.target.type !== 'checkbox') {
      event.preventDefault();
      save(openModal);
    }
  });

  form.addEventListener('submit', (event) => {
    if (openModal) {
      event.preventDefault();
      return;
    }
    for (let index = 0; index < STEPS.length; index += 1) {
      if (!validateStep(STEPS[index], true)) {
        event.preventDefault();
        if (index !== currentStep) {
          goTo(index);
        }
        focusStepError(STEPS[index]);
        return;
      }
    }
    submitting = true;
    submitButton.disabled = true;
    submitButton.classList.add('button--processing');
    submitButton.textContent = 'Sending your details…';
  });

  window.addEventListener('beforeunload', (event) => {
    if (dirty && !submitting) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  // Start on the first step with a problem the server reported.
  let startStep = 0;
  if (errorSummary) {
    const first = errorSummary.querySelector('[data-cof-error-step]');
    startStep = first ? Math.max(0, STEPS.indexOf(first.dataset.cofErrorStep)) : 0;
    errorSummary.querySelectorAll('[data-cof-error-step]').forEach((item) => {
      const indicator = document.querySelector(`[data-cof-step-indicator="${item.dataset.cofErrorStep}"]`);
      if (indicator) {
        indicator.classList.add('has-error');
      }
    });
  }
  goTo(startStep, false);
  if (errorSummary) {
    validateStep(STEPS[startStep], true);
    errorSummary.focus();
  }
})();
