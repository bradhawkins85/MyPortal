(function () {
  'use strict';

  const AUDIENCE_LABELS = {
    none: 'Colleagues only',
    contactsOnly: 'Colleagues + external contacts',
    all: 'Colleagues + everyone external',
  };
  const MAX_MESSAGE = 10000;

  function normalize(value) {
    return String(value == null ? '' : value).trim().toLowerCase();
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) {
        return;
      }
      if (key === 'className') {
        node.className = value;
      } else if (key === 'text') {
        node.textContent = value;
      } else if (value === true) {
        node.setAttribute(key, '');
      } else {
        node.setAttribute(key, value);
      }
    });
    (children || []).forEach((child) => {
      if (child !== null && child !== undefined && child !== false) {
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
      }
    });
    return node;
  }

  function parseJson(id) {
    const node = document.getElementById(id);
    if (!node) {
      return null;
    }
    try {
      return JSON.parse(node.textContent || 'null');
    } catch (error) {
      console.error('Unable to parse out of office data', error);
      return null;
    }
  }

  // Graph returns {dateTime, timeZone}; replies are written in UTC.
  function graphDate(item) {
    if (!item || !item.dateTime) {
      return null;
    }
    let text = item.dateTime;
    if ((item.timeZone || 'UTC') === 'UTC' && !/[zZ]|[+-]\d\d:?\d\d$/.test(text)) {
      text += 'Z';
    }
    const date = new Date(text);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function toLocalInput(date) {
    if (!date) {
      return '';
    }
    const offset = date.getTimezoneOffset() * 60000;
    return new Date(date.getTime() - offset).toISOString().slice(0, 16);
  }

  function fromLocalInput(value) {
    if (!value) {
      return null;
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatWhen(date) {
    return date.toLocaleString(undefined, {
      weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
    });
  }

  // Graph messages are HTML; edit them as plain text.
  function htmlToText(html) {
    const text = String(html || '');
    if (!/<[a-z][\s\S]*>/i.test(text)) {
      return text;
    }
    const doc = new DOMParser().parseFromString(text, 'text/html');
    doc.querySelectorAll('br').forEach((br) => br.replaceWith('\n'));
    doc.querySelectorAll('p, div, li').forEach((block) => block.append('\n'));
    return (doc.body.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
  }

  function describeState(setting, now) {
    if (!setting) {
      return { key: 'error', label: 'Couldn\'t read' };
    }
    const status = setting.status || 'disabled';
    if (status === 'alwaysEnabled') {
      return { key: 'on', label: 'Replying (no end date)' };
    }
    if (status === 'scheduled') {
      const start = graphDate(setting.scheduledStartDateTime);
      const end = graphDate(setting.scheduledEndDateTime);
      if (end && end <= now) {
        return { key: 'off', label: `Ended ${formatWhen(end)}` };
      }
      if (start && start > now) {
        return { key: 'scheduled', label: `Scheduled ${formatWhen(start)} → ${end ? formatWhen(end) : 'no end'}` };
      }
      return { key: 'on', label: end ? `Replying until ${formatWhen(end)}` : 'Replying' };
    }
    return { key: 'off', label: 'Off' };
  }

  document.addEventListener('DOMContentLoaded', () => {
    const data = parseJson('oof-data');
    const root = document.querySelector('[data-oof-root]');
    if (!data || !root) {
      return;
    }
    const now = new Date();
    const byMailbox = new Map((data.mailboxes || []).map((item) => [normalize(item.mailbox), item]));
    const items = Array.from(root.querySelectorAll('[data-oof-item]'));

    // ----- list: live status text, stats, search and filter ------------------

    const counts = { on: 0, scheduled: 0, off: 0, error: 0 };
    items.forEach((item) => {
      const record = byMailbox.get(normalize(item.dataset.mailbox));
      const state = describeState(record && record.success ? record.setting || {} : null, now);
      item.dataset.state = state.key;
      counts[state.key] = (counts[state.key] || 0) + 1;
      const excerpt = item.querySelector('[data-oof-excerpt]');
      if (excerpt && record && record.setting) {
        const text = htmlToText(record.setting.internalReplyMessage).replace(/\s+/g, ' ').trim();
        excerpt.textContent = `“${text.length > 140 ? `${text.slice(0, 137)}…` : text}”`;
      }
      const chip = item.querySelector('[data-oof-status]');
      if (chip && record && record.success) {
        chip.textContent = state.label;
        chip.className = `scf-chip oof-status oof-status--${state.key}`;
      }
    });
    ['on', 'scheduled', 'off'].forEach((key) => {
      const node = root.querySelector(`[data-oof-stat="${key}"]`);
      if (node) {
        node.textContent = String(counts[key] || 0);
      }
    });

    const search = root.querySelector('[data-oof-search]');
    const filter = root.querySelector('[data-oof-filter]');
    const noResults = root.querySelector('[data-oof-no-results]');
    function applyFilter() {
      const terms = normalize(search && search.value).split(/\s+/).filter(Boolean);
      const wanted = filter ? filter.value : '';
      let visible = 0;
      items.forEach((item) => {
        const text = normalize(item.dataset.searchText);
        const match = terms.every((term) => text.includes(term)) && (!wanted || item.dataset.state === wanted);
        item.hidden = !match;
        visible += match ? 1 : 0;
      });
      if (noResults) {
        noResults.hidden = visible > 0;
      }
    }
    if (search) {
      search.addEventListener('input', applyFilter);
    }
    if (filter) {
      filter.addEventListener('change', applyFilter);
    }

    const modal = document.getElementById('oof-modal');
    if (!data.canWrite || !modal) {
      return;
    }

    // ----- bulk selection -------------------------------------------------------

    const bulk = root.querySelector('[data-oof-bulk]');
    const bulkCount = root.querySelector('[data-oof-bulk-count]');
    const selects = Array.from(root.querySelectorAll('[data-oof-select]'));
    function selectedFromList() {
      return selects.filter((input) => input.checked).map((input) => input.value);
    }
    function refreshBulk() {
      const count = selectedFromList().length;
      bulk.hidden = count === 0;
      bulkCount.textContent = `${count} selected`;
      items.forEach((item) => {
        const input = item.querySelector('[data-oof-select]');
        item.classList.toggle('is-selected', Boolean(input && input.checked));
      });
    }
    selects.forEach((input) => input.addEventListener('change', refreshBulk));
    root.querySelector('[data-oof-bulk-clear]').addEventListener('click', () => {
      selects.forEach((input) => { input.checked = false; });
      refreshBulk();
    });

    // ----- editor ---------------------------------------------------------------

    const form = modal.querySelector('[data-oof-form]');
    const title = modal.querySelector('[data-oof-title]');
    const subtitle = modal.querySelector('[data-oof-subtitle]');
    const whoChips = modal.querySelector('[data-oof-who-chips]');
    const whoToggle = modal.querySelector('[data-oof-who-toggle]');
    const whoWrap = modal.querySelector('[data-oof-who]');
    const whoSearch = modal.querySelector('[data-oof-who-search]');
    const whoRows = Array.from(modal.querySelectorAll('[data-oof-who-row]'));
    const mailboxInputs = Array.from(modal.querySelectorAll('[data-oof-mailbox]'));
    const startInput = modal.querySelector('[data-oof-start]');
    const endInput = modal.querySelector('[data-oof-end]');
    const rangeNote = modal.querySelector('[data-oof-range]');
    const startOut = modal.querySelector('[data-oof-out="start"]');
    const endOut = modal.querySelector('[data-oof-out="end"]');
    const internal = modal.querySelector('[data-oof-internal]');
    const counter = modal.querySelector('[data-oof-counter]');
    const audienceInputs = Array.from(modal.querySelectorAll('[data-oof-audience]'));
    const externalWrap = modal.querySelector('[data-oof-external-wrap]');
    const same = modal.querySelector('[data-oof-same]');
    const externalField = modal.querySelector('[data-oof-external-field]');
    const external = modal.querySelector('[data-oof-external]');
    const previewSubject = modal.querySelector('[data-oof-preview-subject]');
    const previewInternal = modal.querySelector('[data-oof-preview-internal-body]');
    const previewExternalCard = modal.querySelector('[data-oof-preview-external]');
    const previewExternal = modal.querySelector('[data-oof-preview-external-body]');
    const previewExternalTo = modal.querySelector('[data-oof-preview-external-to]');
    const previewRules = modal.querySelector('[data-oof-preview-rules]');
    const disableButton = modal.querySelector('[data-oof-disable]');
    const saveButton = modal.querySelector('[data-oof-save]');
    const errors = {};
    modal.querySelectorAll('[data-oof-error]').forEach((node) => {
      errors[node.dataset.oofError] = node;
    });
    const timezone = (Intl.DateTimeFormat().resolvedOptions().timeZone || 'your timezone');
    let snapshot = '';
    let lastTrigger = null;

    function selectedMailboxes() {
      return mailboxInputs.filter((input) => input.checked).map((input) => input.value);
    }
    function audience() {
      const checked = audienceInputs.find((input) => input.checked);
      return checked ? checked.value : 'none';
    }
    function setError(name, message) {
      if (errors[name]) {
        errors[name].textContent = message || '';
        errors[name].hidden = !message;
      }
    }
    function serialize() {
      return JSON.stringify([selectedMailboxes(), startInput.value, endInput.value, internal.value, external.value, same.checked, audience()]);
    }

    function nameFor(mailbox) {
      const record = byMailbox.get(normalize(mailbox));
      return record ? record.name : mailbox;
    }

    function renderWho() {
      const chosen = selectedMailboxes();
      whoChips.textContent = '';
      chosen.slice(0, 6).forEach((mailbox) => {
        whoChips.append(el('li', { className: 'scf-chip', title: mailbox, text: nameFor(mailbox) }));
      });
      if (chosen.length > 6) {
        whoChips.append(el('li', { className: 'scf-chip scf-chip--muted', text: `+ ${chosen.length - 6} more` }));
      }
      if (!chosen.length) {
        whoChips.append(el('li', { className: 'scf-chip sif-chip--warning', text: 'No one selected' }));
      }
      subtitle.textContent = chosen.length === 1
        ? `For ${nameFor(chosen[0])} (${chosen[0]}).`
        : `For ${chosen.length} mailboxes.`;
      disableButton.disabled = chosen.length === 0;
    }

    function renderRange() {
      const start = fromLocalInput(startInput.value);
      const end = fromLocalInput(endInput.value);
      if (start && end) {
        const hours = Math.round((end - start) / 36e5);
        const length = hours >= 48 ? `${Math.round(hours / 24)} days` : `${hours} hour${hours === 1 ? '' : 's'}`;
        rangeNote.textContent = end > start
          ? `${formatWhen(start)} → ${formatWhen(end)} (${length}, ${timezone}). Saved to Microsoft 365 as ${start.toISOString().slice(0, 16).replace('T', ' ')} → ${end.toISOString().slice(0, 16).replace('T', ' ')} UTC.`
          : '';
      } else {
        rangeNote.textContent = `Times are in ${timezone}.`;
      }
    }

    function renderPreview() {
      const names = selectedMailboxes().map(nameFor);
      previewSubject.textContent = 'Your message';
      previewInternal.textContent = internal.value.trim() || 'Your reply to colleagues appears here.';
      previewInternal.classList.toggle('text-muted', !internal.value.trim());
      const aud = audience();
      previewExternalCard.hidden = aud === 'none';
      previewExternalTo.textContent = aud === 'all' ? 'To everyone outside the organisation' : 'To external senders in their contacts';
      const externalText = same.checked ? internal.value.trim() : external.value.trim();
      previewExternal.textContent = externalText || 'Your reply to external senders appears here.';
      previewExternal.classList.toggle('text-muted', !externalText);

      previewRules.textContent = '';
      const rules = [];
      const start = fromLocalInput(startInput.value);
      const end = fromLocalInput(endInput.value);
      if (start && end && end > start) {
        rules.push(['logic', `Replies from ${formatWhen(start)} until ${formatWhen(end)}.`]);
      } else {
        rules.push(['', 'Pick a start and end time.']);
      }
      rules.push(['visibility', `${AUDIENCE_LABELS[aud]} get a reply.`]);
      if (names.length) {
        rules.push(['m365', names.length === 1 ? `Applies to ${names[0]}.` : `Applies to ${names.length} mailboxes.`]);
      }
      rules.forEach(([variant, text]) => {
        previewRules.append(el('li', { className: `scf-preview__rule${variant ? ` scf-preview__rule--${variant}` : ''}`, text }));
      });
    }

    function refresh() {
      audienceInputs.forEach((input) => input.closest('.scf-choice').classList.toggle('is-selected', input.checked));
      externalWrap.hidden = audience() === 'none';
      externalField.hidden = same.checked;
      counter.textContent = `${internal.value.length} / ${MAX_MESSAGE}`;
      renderWho();
      renderRange();
      renderPreview();
      if (form.dataset.submitted === 'true') {
        validate('schedule');
      }
    }

    [startInput, endInput, internal, external].forEach((input) => input.addEventListener('input', refresh));
    [startInput, endInput].forEach((input) => input.addEventListener('change', refresh));
    audienceInputs.concat([same]).forEach((input) => input.addEventListener('change', refresh));
    mailboxInputs.forEach((input) => input.addEventListener('change', refresh));

    whoToggle.addEventListener('click', () => {
      const open = whoWrap.hidden;
      whoWrap.hidden = !open;
      whoToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      whoToggle.textContent = open ? 'Done' : 'Change';
      if (open) {
        whoSearch.focus();
      }
    });
    whoSearch.addEventListener('input', () => {
      const terms = normalize(whoSearch.value).split(/\s+/).filter(Boolean);
      whoRows.forEach((row) => {
        row.hidden = !terms.every((term) => normalize(row.dataset.searchText).includes(term));
      });
    });

    function setRange(start, end) {
      startInput.value = toLocalInput(start);
      endInput.value = toLocalInput(end);
      refresh();
    }
    modal.querySelectorAll('[data-oof-preset]').forEach((button) => {
      button.addEventListener('click', () => {
        const base = new Date();
        base.setSeconds(0, 0);
        const at = (date, hours) => {
          const copy = new Date(date);
          copy.setHours(hours, 0, 0, 0);
          return copy;
        };
        const addDays = (date, days) => {
          const copy = new Date(date);
          copy.setDate(copy.getDate() + days);
          return copy;
        };
        const preset = button.dataset.oofPreset;
        if (preset === 'today') {
          setRange(base, at(addDays(base, 1), 0));
        } else if (preset === 'tomorrow') {
          setRange(at(addDays(base, 1), 0), at(addDays(base, 2), 0));
        } else if (preset === 'week') {
          setRange(base, at(addDays(base, 7), 0));
        } else {
          const daysToSaturday = (6 - base.getDay() + 7) % 7 || 7;
          setRange(base, at(addDays(base, daysToSaturday), 0));
        }
      });
    });

    function validate(intent) {
      const problems = [];
      ['who', 'when', 'internal', 'external'].forEach((name) => setError(name, ''));
      if (!selectedMailboxes().length) {
        setError('who', 'Choose at least one mailbox.');
        problems.push(whoToggle);
      }
      if (intent === 'disable') {
        return problems;
      }
      const start = fromLocalInput(startInput.value);
      const end = fromLocalInput(endInput.value);
      if (!start || !end) {
        setError('when', 'Choose when the replies start and end.');
        problems.push(start ? endInput : startInput);
      } else if (end <= start) {
        setError('when', 'The end must be after the start.');
        problems.push(endInput);
      }
      if (!internal.value.trim()) {
        setError('internal', 'Write the reply colleagues will get.');
        problems.push(internal);
      }
      if (audience() !== 'none' && !same.checked && !external.value.trim()) {
        setError('external', 'Write the reply external senders will get, or send the same message.');
        problems.push(external);
      }
      return problems;
    }

    function load(mailboxes, setting) {
      mailboxInputs.forEach((input) => {
        input.checked = mailboxes.some((mailbox) => normalize(mailbox) === normalize(input.value));
      });
      whoRows.forEach((row) => { row.hidden = false; });
      whoSearch.value = '';
      whoWrap.hidden = mailboxes.length > 0;
      whoToggle.setAttribute('aria-expanded', whoWrap.hidden ? 'false' : 'true');
      whoToggle.textContent = whoWrap.hidden ? 'Change' : 'Done';
      const start = setting && graphDate(setting.scheduledStartDateTime);
      const end = setting && graphDate(setting.scheduledEndDateTime);
      startInput.value = toLocalInput(start);
      endInput.value = toLocalInput(end);
      internal.value = setting ? htmlToText(setting.internalReplyMessage) : '';
      external.value = setting ? htmlToText(setting.externalReplyMessage) : '';
      const aud = (setting && setting.externalAudience) || 'none';
      audienceInputs.forEach((input) => { input.checked = input.value === aud; });
      same.checked = !setting || !external.value.trim() || external.value.trim() === internal.value.trim();
      const status = setting && setting.status;
      title.textContent = mailboxes.length === 1 && status && status !== 'disabled' ? 'Edit automatic reply' : 'Set automatic reply';
      form.dataset.submitted = 'false';
      saveButton.disabled = false;
      saveButton.textContent = 'Save schedule';
      refresh();
    }

    function open(trigger) {
      lastTrigger = trigger || null;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('scf-modal-open');
      snapshot = serialize();
      window.requestAnimationFrame(() => {
        (whoWrap.hidden ? (startInput.value ? internal : startInput) : whoSearch).focus();
      });
    }

    function close(force) {
      if (!force && serialize() !== snapshot && !window.confirm('Discard this automatic reply?')) {
        return;
      }
      modal.hidden = true;
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('scf-modal-open');
      if (lastTrigger && typeof lastTrigger.focus === 'function') {
        lastTrigger.focus();
      }
    }

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        close(false);
      });
    });
    modal.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = Array.from(modal.querySelectorAll(
        'button:not([disabled]), input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled])',
      )).filter((node) => node.offsetParent !== null);
      if (!focusable.length) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    document.querySelectorAll('[data-oof-new]').forEach((button) => {
      button.addEventListener('click', () => {
        load(selectedFromList(), null);
        open(button);
      });
    });
    root.querySelectorAll('[data-oof-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        const record = byMailbox.get(normalize(button.dataset.oofEdit));
        const setting = record && record.setting && record.setting.status !== 'disabled' ? record.setting : null;
        load([button.dataset.oofEdit], setting || (record && record.setting ? {
          externalAudience: record.setting.externalAudience,
          internalReplyMessage: record.setting.internalReplyMessage,
          externalReplyMessage: record.setting.externalReplyMessage,
        } : null));
        open(button);
      });
    });
    root.querySelector('[data-oof-bulk-set]').addEventListener('click', (event) => {
      load(selectedFromList(), null);
      open(event.currentTarget);
    });
    root.querySelector('[data-oof-bulk-off]').addEventListener('click', () => {
      const chosen = selectedFromList();
      if (!chosen.length || !window.confirm(`Turn off automatic replies for ${chosen.length} mailbox${chosen.length === 1 ? '' : 'es'}? Their saved messages are kept.`)) {
        return;
      }
      load(chosen, null);
      form.dataset.confirmed = 'true';
      disableButton.click();
    });

    form.addEventListener('submit', (event) => {
      const intent = event.submitter && event.submitter.value === 'disable' ? 'disable' : 'schedule';
      form.dataset.submitted = 'true';
      const problems = validate(intent);
      if (problems.length) {
        event.preventDefault();
        if (problems[0] === whoToggle && whoWrap.hidden) {
          whoToggle.click();
        } else {
          problems[0].focus();
        }
        return;
      }
      if (intent === 'disable') {
        const count = selectedMailboxes().length;
        if (form.dataset.confirmed !== 'true' && !window.confirm(`Turn off automatic replies for ${count} mailbox${count === 1 ? '' : 'es'}? Their saved messages are kept.`)) {
          event.preventDefault();
          return;
        }
        delete form.dataset.confirmed;
        startOut.value = '';
        endOut.value = '';
      } else {
        startOut.value = fromLocalInput(startInput.value).toISOString();
        endOut.value = fromLocalInput(endInput.value).toISOString();
        // The API needs an external message unless "same message" is on,
        // even when external senders get no reply.
        if (audience() === 'none' && !external.value.trim()) {
          same.checked = true;
        }
        if (same.checked) {
          external.value = '';
        }
      }
      // Disable after this tick so the clicked button's intent is still posted.
      window.setTimeout(() => {
        saveButton.disabled = true;
        disableButton.disabled = true;
        saveButton.textContent = intent === 'disable' ? 'Turning off…' : 'Saving…';
      }, 0);
    });

    // After a partial failure, reopen the editor with what was submitted.
    const submitted = data.submitted || {};
    if ((data.failed || []).length && submitted.start_time) {
      load(data.failed, {
        scheduledStartDateTime: { dateTime: submitted.start_time, timeZone: 'Z' },
        scheduledEndDateTime: { dateTime: submitted.end_time, timeZone: 'Z' },
        internalReplyMessage: submitted.internal_message,
        externalReplyMessage: submitted.same_message ? '' : submitted.external_message,
        externalAudience: submitted.external_audience,
        status: 'scheduled',
      });
      same.checked = Boolean(submitted.same_message);
      refresh();
      open(null);
    }
  });
})();
