/* Scheduled tasks admin page: task list, editor, bulk create, spread-out and history modals. */
(function () {
  'use strict';

  const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const MONTH_NAMES = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ];
  const DAY_ALIASES = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
  const MONTH_ALIASES = {
    jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
  };
  const FILTER_SESSION_KEY = 'portal.scheduled-tasks.filter';
  const SORT_STORAGE_KEY = 'portal.scheduled-tasks.sort';

  // ---------------------------------------------------------------------------
  // Cron parsing, description and next-run calculation (croniter semantics)
  // ---------------------------------------------------------------------------

  const FIELD_SPECS = [
    { min: 0, max: 59 },
    { min: 0, max: 23 },
    { min: 1, max: 31, allowLast: true },
    { min: 1, max: 12, aliases: MONTH_ALIASES },
    { min: 0, max: 7, aliases: DAY_ALIASES },
  ];

  function parseValue(text, spec) {
    const lower = text.toLowerCase();
    if (spec.aliases && Object.prototype.hasOwnProperty.call(spec.aliases, lower)) {
      return spec.aliases[lower];
    }
    if (!/^\d+$/.test(text)) {
      throw new Error(`"${text}" is not a number`);
    }
    const value = Number(text);
    if (value < spec.min || value > spec.max) {
      throw new Error(`${value} must be between ${spec.min} and ${spec.max}`);
    }
    return value;
  }

  function parseField(text, spec) {
    const field = { any: text === '*' || text === '?', values: new Set(), last: false };
    if (field.any) {
      for (let value = spec.min; value <= spec.max; value += 1) {
        field.values.add(value);
      }
      return field;
    }
    text.split(',').forEach((part) => {
      if (!part) {
        throw new Error('empty list item');
      }
      const [range, stepText] = part.split('/');
      let step = 1;
      if (stepText !== undefined) {
        if (!/^\d+$/.test(stepText) || Number(stepText) < 1) {
          throw new Error(`"${stepText}" is not a valid step`);
        }
        step = Number(stepText);
      }
      if (range === 'L' && spec.allowLast && stepText === undefined) {
        field.last = true;
        return;
      }
      let start;
      let end;
      if (range === '*') {
        start = spec.min;
        end = spec.max;
      } else if (range.includes('-')) {
        const bounds = range.split('-');
        if (bounds.length !== 2) {
          throw new Error(`"${range}" is not a valid range`);
        }
        start = parseValue(bounds[0], spec);
        end = parseValue(bounds[1], spec);
      } else {
        start = parseValue(range, spec);
        end = stepText === undefined ? start : spec.max;
      }
      if (start > end) {
        throw new Error(`"${range}" runs backwards`);
      }
      for (let value = start; value <= end; value += step) {
        field.values.add(value);
      }
    });
    return field;
  }

  function parseYears(text) {
    if (text === undefined || text === '*') {
      return null;
    }
    const years = new Set();
    text.split(',').forEach((part) => {
      const bounds = part.split('-');
      if (bounds.length > 2 || bounds.some((value) => !/^\d{4}$/.test(value))) {
        throw new Error('years must be four digits');
      }
      const start = Number(bounds[0]);
      const end = Number(bounds[bounds.length - 1]);
      if (start < 1970 || end > 2099 || start > end) {
        throw new Error('years must be between 1970 and 2099');
      }
      for (let year = start; year <= end; year += 1) {
        years.add(year);
      }
    });
    return years;
  }

  function parseCron(expression) {
    const parts = String(expression || '').trim().split(/\s+/).filter(Boolean);
    if (parts.length !== 5 && parts.length !== 6) {
      throw new Error(`a schedule needs 5 or 6 parts, this has ${parts.length}`);
    }
    const names = ['minute', 'hour', 'day of month', 'month', 'day of week'];
    const fields = parts.slice(0, 5).map((part, index) => {
      try {
        return parseField(part, FIELD_SPECS[index]);
      } catch (error) {
        throw new Error(`${names[index]}: ${error.message}`);
      }
    });
    if (fields[4].values.has(7)) {
      fields[4].values.delete(7);
      fields[4].values.add(0);
    }
    return {
      minute: fields[0],
      hour: fields[1],
      dom: fields[2],
      month: fields[3],
      dow: fields[4],
      years: parseYears(parts[5]),
    };
  }

  const wallFormatters = new Map();

  function wallParts(ms, timeZone) {
    let formatter = wallFormatters.get(timeZone);
    if (!formatter) {
      formatter = new Intl.DateTimeFormat('en-US', {
        timeZone,
        hourCycle: 'h23',
        year: 'numeric',
        month: 'numeric',
        day: 'numeric',
        hour: 'numeric',
        minute: 'numeric',
        second: 'numeric',
      });
      wallFormatters.set(timeZone, formatter);
    }
    const parts = {};
    formatter.formatToParts(new Date(ms)).forEach((part) => {
      parts[part.type] = Number(part.value);
    });
    return {
      y: parts.year,
      m: parts.month,
      d: parts.day,
      h: parts.hour % 24,
      mi: parts.minute,
      s: parts.second,
    };
  }

  function timezoneOffset(ms, timeZone) {
    const p = wallParts(ms, timeZone);
    return Date.UTC(p.y, p.m - 1, p.d, p.h, p.mi, p.s) - Math.floor(ms / 1000) * 1000;
  }

  function wallToUtc(y, m, d, h, mi, timeZone) {
    const guess = Date.UTC(y, m - 1, d, h, mi);
    const first = timezoneOffset(guess, timeZone);
    const result = guess - first;
    const second = timezoneOffset(result, timeZone);
    return second === first ? result : guess - second;
  }

  function safeTimeZone(timeZone) {
    try {
      new Intl.DateTimeFormat('en-US', { timeZone });
      return timeZone;
    } catch (error) {
      return 'UTC';
    }
  }

  function dayMatches(cron, day, dow, daysInMonth) {
    const domMatch = cron.dom.values.has(day) || (cron.dom.last && day === daysInMonth);
    const dowMatch = cron.dow.values.has(dow);
    if (!cron.dom.any && !cron.dow.any) {
      return domMatch || dowMatch;
    }
    return domMatch && dowMatch;
  }

  function nextRuns(expression, count, timeZone, fromMs) {
    const cron = parseCron(expression);
    const zone = safeTimeZone(timeZone);
    const from = typeof fromMs === 'number' ? fromMs : Date.now();
    const start = wallParts(from, zone);
    const hours = Array.from(cron.hour.values).sort((a, b) => a - b);
    const minutes = Array.from(cron.minute.values).sort((a, b) => a - b);
    const runs = [];
    for (let offset = 0; offset < 366 * 8 && runs.length < count; offset += 1) {
      const date = new Date(Date.UTC(start.y, start.m - 1, start.d + offset));
      const year = date.getUTCFullYear();
      const month = date.getUTCMonth() + 1;
      const day = date.getUTCDate();
      if (cron.years && year > Math.max(...cron.years)) {
        break;
      }
      if ((cron.years && !cron.years.has(year)) || !cron.month.values.has(month)) {
        continue;
      }
      const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
      if (!dayMatches(cron, day, date.getUTCDay(), daysInMonth)) {
        continue;
      }
      for (const hour of hours) {
        for (const minute of minutes) {
          const utc = wallToUtc(year, month, day, hour, minute, zone);
          if (utc > from) {
            runs.push(new Date(utc));
            if (runs.length >= count) {
              return runs;
            }
          }
        }
      }
    }
    return runs;
  }

  function pad(value) {
    return String(value).padStart(2, '0');
  }

  function ordinal(value) {
    const n = Number(value);
    const suffix = n % 10 === 1 && n % 100 !== 11 ? 'st' : n % 10 === 2 && n % 100 !== 12 ? 'nd' : n % 10 === 3 && n % 100 !== 13 ? 'rd' : 'th';
    return `${n}${suffix}`;
  }

  function joinWords(items) {
    if (items.length <= 1) {
      return items.join('');
    }
    return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
  }

  function sortedValues(field) {
    return Array.from(field.values).sort((a, b) => a - b);
  }

  function describeDays(dowText) {
    const field = parseField(dowText, FIELD_SPECS[4]);
    const days = new Set(Array.from(field.values).map((value) => value % 7));
    const key = Array.from(days).sort().join(',');
    if (key === '1,2,3,4,5') {
      return 'Every weekday';
    }
    if (key === '0,6') {
      return 'Every Saturday and Sunday';
    }
    const ordered = [1, 2, 3, 4, 5, 6, 0].filter((day) => days.has(day)).map((day) => DAY_NAMES[day]);
    return `Every ${joinWords(ordered)}`;
  }

  /** Return a plain-English description of a cron expression, or '' when it is too unusual to summarise. */
  function describeCron(expression) {
    const parts = String(expression || '').trim().split(/\s+/).filter(Boolean);
    if (parts.length !== 5 && parts.length !== 6) {
      return '';
    }
    try {
      parseCron(expression);
    } catch (error) {
      return '';
    }
    const [minute, hour, dom, month, dow, year] = parts;
    const yearText = year && year !== '*' ? ` in ${year.replace(/,/g, ', ')}` : '';
    const isNumber = (text) => /^\d+$/.test(text);
    const isNumberList = (text) => /^\d+(,\d+)*$/.test(text);
    const step = (text) => {
      const match = /^\*\/(\d+)$/.exec(text);
      return match ? Number(match[1]) : null;
    };
    let text = '';
    if (month === '*' && dom === '*' && dow === '*') {
      if (minute === '*' && hour === '*') {
        text = 'Every minute';
      } else if (step(minute) && hour === '*') {
        text = `Every ${step(minute)} minutes`;
      } else if (isNumber(minute) && hour === '*') {
        text = Number(minute) === 0 ? 'Every hour, on the hour' : `Every hour at ${minute} minutes past`;
      } else if (isNumber(minute) && step(hour)) {
        const every = step(hour);
        text = `Every ${every} hour${every === 1 ? '' : 's'}, ${Number(minute) === 0 ? 'on the hour' : `at ${minute} minutes past`}`;
      }
    }
    if (!text && isNumber(minute) && isNumberList(hour)) {
      const times = `at ${joinWords(hour.split(',').map((value) => `${pad(value)}:${pad(minute)}`))}`;
      if (month === '*' && dom === '*' && dow === '*') {
        text = `Every day ${times}`;
      } else if (month === '*' && dom === '*') {
        text = `${describeDays(dow)} ${times}`;
      } else if (month === '*' && dow === '*' && dom === 'L') {
        text = `On the last day of every month ${times}`;
      } else if (month === '*' && dow === '*' && isNumberList(dom)) {
        text = `On the ${joinWords(dom.split(',').map(ordinal))} of every month ${times}`;
      } else if (isNumber(month) && isNumber(dom) && dow === '*') {
        text = `Every year on ${Number(dom)} ${MONTH_NAMES[Number(month) - 1]} ${times}`;
      }
    }
    return text ? `${text}${yearText}` : '';
  }

  // ---------------------------------------------------------------------------
  // Formatting helpers
  // ---------------------------------------------------------------------------

  const relativeFormatter = typeof Intl.RelativeTimeFormat === 'function'
    ? new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
    : null;

  function formatRelative(date) {
    const diff = date.getTime() - Date.now();
    const abs = Math.abs(diff);
    const units = [
      ['day', 86400000],
      ['hour', 3600000],
      ['minute', 60000],
    ];
    for (const [unit, size] of units) {
      if (abs >= size || unit === 'minute') {
        const value = Math.round(diff / size);
        if (unit === 'minute' && value === 0) {
          return diff >= 0 ? 'in under a minute' : 'just now';
        }
        return relativeFormatter ? relativeFormatter.format(value, unit) : date.toLocaleString();
      }
    }
    return date.toLocaleString();
  }

  function formatShort(date) {
    return date.toLocaleString(undefined, {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  function formatDuration(ms) {
    if (!Number.isFinite(ms) || ms < 0) {
      return '—';
    }
    if (ms < 1000) {
      return `${Math.round(ms)} ms`;
    }
    const seconds = ms / 1000;
    if (seconds < 60) {
      return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
    }
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
      return `${minutes} min ${Math.round(seconds % 60)} s`;
    }
    return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
  }

  function formatWait(seconds) {
    if (!Number.isFinite(seconds)) {
      return '';
    }
    if (seconds < 60) {
      return `${seconds} seconds`;
    }
    const minutes = seconds / 60;
    if (minutes < 60) {
      return `${Number.isInteger(minutes) ? minutes : minutes.toFixed(1)} minute${minutes === 1 ? '' : 's'}`;
    }
    const hours = minutes / 60;
    return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} hour${hours === 1 ? '' : 's'}`;
  }

  function localiseTimestamps(root) {
    root.querySelectorAll('[data-utc]').forEach((element) => {
      const date = new Date(element.getAttribute('data-utc'));
      if (Number.isNaN(date.getTime())) {
        return;
      }
      element.title = date.toLocaleString();
      element.textContent = element.hasAttribute('data-sch-relative') ? formatRelative(date) : formatShort(date);
    });
  }

  // ---------------------------------------------------------------------------
  // Requests
  // ---------------------------------------------------------------------------

  function getCookie(name) {
    const match = document.cookie.split('; ').find((item) => item.startsWith(`${name}=`));
    return match ? decodeURIComponent(match.split('=').slice(1).join('=')) : '';
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return getCookie('myportal_session_csrf') || (meta ? meta.getAttribute('content') || '' : '');
  }

  async function requestJson(url, options) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...(options || {}),
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken(),
        ...((options && options.headers) || {}),
      },
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        if (data && data.detail) {
          detail = Array.isArray(data.detail)
            ? data.detail.map((entry) => entry.msg || entry).join(', ')
            : String(data.detail);
        }
      } catch (error) {
        /* keep the status text */
      }
      throw new Error(detail);
    }
    if (response.status === 204) {
      return null;
    }
    try {
      return await response.json();
    } catch (error) {
      return null;
    }
  }

  // ---------------------------------------------------------------------------
  // Modals
  // ---------------------------------------------------------------------------

  const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function isVisible(element) {
    return Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
  }

  function openModal(modal, trigger) {
    if (!modal) {
      return;
    }
    modal.__trigger = trigger || document.activeElement;
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('scf-modal-open');
    const body = modal.querySelector('.modal__body');
    if (body) {
      body.scrollTop = 0;
    }
    window.requestAnimationFrame(() => {
      const target = modal.querySelector('[data-initial-focus]') ||
        Array.from(modal.querySelectorAll(`.modal__body ${FOCUSABLE}`)).find(isVisible) ||
        modal.querySelector('.modal__close');
      if (target && isVisible(target)) {
        target.focus();
      }
    });
  }

  function closeModal(modal, force) {
    if (!modal || modal.hidden) {
      return;
    }
    if (!force && typeof modal.__confirmClose === 'function' && !modal.__confirmClose()) {
      return;
    }
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.modal.is-visible:not([hidden])')) {
      document.body.classList.remove('scf-modal-open');
    }
    const trigger = modal.__trigger;
    if (trigger && typeof trigger.focus === 'function' && document.contains(trigger)) {
      trigger.focus();
    }
  }

  function bindModal(modal) {
    if (!modal) {
      return;
    }
    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal(modal);
      });
    });
    modal.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = Array.from(modal.querySelectorAll(FOCUSABLE)).filter(isVisible);
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
  }

  function showError(container, key, message, attribute) {
    const node = container.querySelector(`[${attribute || 'data-sch-error'}="${key}"]`);
    if (!node) {
      return;
    }
    node.textContent = message || '';
    node.hidden = !message;
  }

  function saveListState() {
    try {
      const search = document.querySelector('[data-sch-search]');
      const filter = document.querySelector('[data-sch-filter]');
      sessionStorage.setItem(FILTER_SESSION_KEY, JSON.stringify({
        q: search ? search.value : '',
        filter: filter ? filter.value : '',
      }));
    } catch (error) {
      /* storage unavailable */
    }
  }

  function reloadPage() {
    saveListState();
    window.location.reload();
  }

  // ---------------------------------------------------------------------------
  // Page
  // ---------------------------------------------------------------------------

  function readData() {
    const node = document.getElementById('scheduled-tasks-data');
    if (!node) {
      return null;
    }
    try {
      return JSON.parse(node.textContent || '{}');
    } catch (error) {
      return null;
    }
  }

  function init() {
    const root = document.querySelector('[data-sch-root]');
    const data = readData();
    if (!root || !data) {
      return;
    }
    const timeZone = safeTimeZone(data.timezone || 'UTC');
    const tasksById = new Map((data.tasks || []).map((task) => [String(task.id), task]));
    const items = Array.from(root.querySelectorAll('[data-sch-item]'));

    const editorModal = document.getElementById('task-editor-modal');
    const bulkModal = document.getElementById('bulk-task-create-modal');
    const redistributeModal = document.getElementById('scheduled-tasks-redistribute-modal');
    const logsModal = document.getElementById('task-logs-modal');
    const previewModal = document.getElementById('task-preview-modal');
    [editorModal, bulkModal, redistributeModal, logsModal, previewModal].forEach(bindModal);

    localiseTimestamps(root);
    root.querySelectorAll('[data-sch-cron-summary]').forEach((node) => {
      const summary = describeCron(node.getAttribute('data-sch-cron-summary'));
      node.textContent = summary || 'Custom schedule';
    });

    const list = initList(root, items);
    const selection = initSelection(root, items, redistributeModal, timeZone);
    const editor = initEditor(editorModal, data, timeZone);
    initBulkCreate(bulkModal, timeZone);
    const history = initHistory(logsModal);
    const preview = initPreview(previewModal);

    const taskFor = (element) => {
      const item = element.closest('[data-sch-item]');
      return item ? tasksById.get(item.getAttribute('data-task-id')) : null;
    };

    document.querySelectorAll('[data-task-create]').forEach((button) => {
      button.addEventListener('click', () => editor.open(null, button));
    });
    document.querySelectorAll('[data-bulk-task-create]').forEach((button) => {
      button.addEventListener('click', () => openModal(bulkModal, button));
    });

    root.addEventListener('click', (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) {
        return;
      }
      const action = target.closest('[data-task-edit], [data-task-duplicate], [data-task-logs], [data-task-preview], [data-task-run]');
      if (!action) {
        return;
      }
      const task = taskFor(action);
      const more = action.closest('[data-sch-more]');
      if (more) {
        more.open = false;
      }
      if (!task) {
        return;
      }
      if (action.hasAttribute('data-task-edit')) {
        editor.open(task, action);
      } else if (action.hasAttribute('data-task-duplicate')) {
        editor.open({ ...task, id: '', name: '' }, action, true);
      } else if (action.hasAttribute('data-task-logs')) {
        history.open(task, action);
      } else if (action.hasAttribute('data-task-preview')) {
        preview.open(task, action);
      } else if (action.hasAttribute('data-task-run')) {
        runTask(task, action);
      }
    });

    // Only one "more" menu open at a time; close on outside click.
    const menus = Array.from(root.querySelectorAll('[data-sch-more]'));
    menus.forEach((menu) => {
      menu.addEventListener('toggle', () => {
        if (menu.open) {
          menus.forEach((other) => {
            if (other !== menu) {
              other.open = false;
            }
          });
        }
      });
    });
    document.addEventListener('click', (event) => {
      menus.forEach((menu) => {
        if (menu.open && !menu.contains(event.target)) {
          menu.open = false;
        }
      });
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        menus.filter((menu) => menu.open).forEach((menu) => {
          menu.open = false;
          const toggle = menu.querySelector('summary');
          if (toggle) {
            toggle.focus();
          }
        });
      }
    });

    const inactiveToggle = root.querySelector('[data-sch-show-inactive]');
    if (inactiveToggle && inactiveToggle.form) {
      inactiveToggle.addEventListener('change', () => {
        saveListState();
        inactiveToggle.form.submit();
      });
    }

    async function runTask(task, button) {
      if (button.disabled) {
        return;
      }
      const item = button.closest('[data-sch-item]');
      const chip = item ? item.querySelector('[data-sch-status-chip]') : null;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      if (chip) {
        chip.className = 'scf-chip sch-chip sch-chip--running';
        chip.textContent = 'Running…';
      }
      try {
        await requestJson(`/scheduler/tasks/${task.id}/run`, { method: 'POST' });
        window.setTimeout(reloadPage, 250);
      } catch (error) {
        button.disabled = false;
        button.removeAttribute('aria-busy');
        if (chip) {
          chip.className = 'scf-chip sch-chip sch-chip--failed';
          chip.textContent = `Couldn't start: ${error.message}`;
          chip.title = error.message;
        }
      }
    }

    // Deep link from the schedule calendar: /admin/scheduled-tasks?taskId=12
    const params = new URLSearchParams(window.location.search);
    const linked = params.get('taskId') && tasksById.get(params.get('taskId'));
    if (linked) {
      const item = items.find((node) => node.getAttribute('data-task-id') === String(linked.id));
      if (item) {
        item.scrollIntoView({ block: 'center' });
        item.classList.add('is-highlighted');
      }
      editor.open(linked, item ? item.querySelector('[data-task-edit]') : null);
    }

    list.refresh();
    selection.refresh();
  }

  function initList(root, items) {
    const search = root.querySelector('[data-sch-search]');
    const filter = root.querySelector('[data-sch-filter]');
    const sort = root.querySelector('[data-sch-sort]');
    const groups = Array.from(root.querySelectorAll('[data-sch-group]'));
    const noResults = root.querySelector('[data-sch-no-results]');
    const dueStat = root.querySelector('[data-sch-stat="due"]');
    const dayMs = 86400000;

    const isDue = (item) => {
      const next = Date.parse(item.getAttribute('data-next-run') || '');
      return item.getAttribute('data-active') === 'true' && Number.isFinite(next) && next - Date.now() <= dayMs;
    };

    if (dueStat) {
      dueStat.textContent = String(items.filter(isDue).length);
    }

    try {
      const saved = JSON.parse(sessionStorage.getItem(FILTER_SESSION_KEY) || 'null');
      sessionStorage.removeItem(FILTER_SESSION_KEY);
      if (saved && search) {
        search.value = saved.q || '';
      }
      if (saved && filter && Array.from(filter.options).some((option) => option.value === saved.filter)) {
        filter.value = saved.filter;
      }
    } catch (error) {
      /* storage unavailable */
    }
    try {
      const savedSort = localStorage.getItem(SORT_STORAGE_KEY);
      if (sort && savedSort && Array.from(sort.options).some((option) => option.value === savedSort)) {
        sort.value = savedSort;
      }
    } catch (error) {
      /* storage unavailable */
    }

    function matchesFilter(item, value) {
      switch (value) {
        case 'failed':
          return item.getAttribute('data-status') === 'failed';
        case 'never':
          return item.getAttribute('data-status') === 'never';
        case 'due':
          return isDue(item);
        case 'paused':
          return item.getAttribute('data-active') === 'false';
        case 'hidden':
          return item.getAttribute('data-hidden') === 'true';
        default:
          return true;
      }
    }

    function sortKey(item, mode) {
      if (mode === 'next') {
        const value = Date.parse(item.getAttribute('data-next-run') || '');
        return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER;
      }
      if (mode === 'last') {
        const value = Date.parse(item.getAttribute('data-last-run') || '');
        return Number.isFinite(value) ? -value : Number.MAX_SAFE_INTEGER;
      }
      return (item.getAttribute('data-name') || '').toLocaleLowerCase();
    }

    function applySort() {
      const mode = sort ? sort.value : 'name';
      root.querySelectorAll('[data-sch-list]').forEach((listNode) => {
        const children = Array.from(listNode.children);
        children.sort((a, b) => {
          const left = sortKey(a, mode);
          const right = sortKey(b, mode);
          if (typeof left === 'string') {
            return left.localeCompare(right);
          }
          return left - right;
        });
        children.forEach((child) => listNode.appendChild(child));
      });
    }

    function refresh() {
      const query = search ? search.value.trim().toLocaleLowerCase() : '';
      const filterValue = filter ? filter.value : '';
      let visibleTotal = 0;
      items.forEach((item) => {
        const text = (item.getAttribute('data-search-text') || '').toLocaleLowerCase();
        const visible = (!query || text.includes(query)) && matchesFilter(item, filterValue);
        item.hidden = !visible;
        if (!visible) {
          const checkbox = item.querySelector('[data-scheduled-tasks-row-checkbox]');
          if (checkbox && checkbox.checked) {
            checkbox.checked = false;
            checkbox.dispatchEvent(new Event('change', { bubbles: true }));
          }
        } else {
          visibleTotal += 1;
        }
      });
      groups.forEach((group) => {
        const groupItems = Array.from(group.querySelectorAll('[data-sch-item]'));
        const visible = groupItems.filter((item) => !item.hidden).length;
        group.hidden = visible === 0;
        const counter = group.querySelector('[data-sch-group-count]');
        if (counter) {
          counter.textContent = visible === groupItems.length ? String(visible) : `${visible} of ${groupItems.length}`;
        }
      });
      if (noResults) {
        noResults.hidden = visibleTotal > 0 || items.length === 0;
      }
      root.dispatchEvent(new CustomEvent('sch:filtered'));
    }

    if (search) {
      search.addEventListener('input', refresh);
    }
    if (filter) {
      filter.addEventListener('change', refresh);
    }
    if (sort) {
      sort.addEventListener('change', () => {
        try {
          localStorage.setItem(SORT_STORAGE_KEY, sort.value);
        } catch (error) {
          /* storage unavailable */
        }
        applySort();
      });
    }
    applySort();
    return { refresh };
  }

  function initSelection(root, items, redistributeModal, timeZone) {
    const bar = root.querySelector('[data-sch-bulk]');
    const countLabel = root.querySelector('[data-sch-bulk-count]');
    const selectAll = root.querySelector('[data-scheduled-tasks-select-all]');
    const deleteForm = document.querySelector('[data-scheduled-tasks-bulk-form="delete"]');
    const renameForm = document.querySelector('[data-scheduled-tasks-bulk-form="rename"]');
    const redistributeForm = document.querySelector('[data-scheduled-tasks-bulk-form="redistribute"]');
    const checkboxes = items.map((item) => item.querySelector('[data-scheduled-tasks-row-checkbox]')).filter(Boolean);

    const visibleBoxes = () => checkboxes.filter((box) => !box.closest('[data-sch-item]').hidden);
    const selectedBoxes = () => checkboxes.filter((box) => box.checked);
    const selectedItems = () => selectedBoxes().map((box) => box.closest('[data-sch-item]'));

    function mirror(form) {
      if (!form) {
        return;
      }
      form.querySelectorAll('input[name="taskIds"]').forEach((input) => input.remove());
      selectedBoxes().forEach((box) => {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'taskIds';
        input.value = box.value;
        form.appendChild(input);
      });
    }

    function refresh() {
      const count = selectedBoxes().length;
      items.forEach((item) => {
        const box = item.querySelector('[data-scheduled-tasks-row-checkbox]');
        item.classList.toggle('is-selected', Boolean(box && box.checked));
      });
      if (bar) {
        bar.hidden = count === 0;
      }
      if (countLabel) {
        countLabel.textContent = `${count} task${count === 1 ? '' : 's'} selected`;
      }
      if (selectAll) {
        const visible = visibleBoxes();
        const checked = visible.filter((box) => box.checked).length;
        selectAll.checked = visible.length > 0 && checked === visible.length;
        selectAll.indeterminate = checked > 0 && checked < visible.length;
      }
      mirror(renameForm);
      mirror(redistributeForm);
    }

    root.addEventListener('change', (event) => {
      if (event.target instanceof HTMLInputElement && event.target.hasAttribute('data-scheduled-tasks-row-checkbox')) {
        refresh();
      }
    });
    root.addEventListener('sch:filtered', refresh);
    if (selectAll) {
      selectAll.addEventListener('change', () => {
        visibleBoxes().forEach((box) => {
          box.checked = selectAll.checked;
        });
        refresh();
      });
    }
    const clear = root.querySelector('[data-sch-bulk-clear]');
    if (clear) {
      clear.addEventListener('click', () => {
        checkboxes.forEach((box) => {
          box.checked = false;
        });
        refresh();
      });
    }

    const plural = (count) => `${count} task${count === 1 ? '' : 's'}`;

    const deleteButton = root.querySelector('[data-scheduled-tasks-bulk-action="delete"]');
    if (deleteButton && deleteForm) {
      deleteButton.addEventListener('click', () => {
        const count = selectedBoxes().length;
        if (count && window.confirm(`Delete ${plural(count)}? This can't be undone.`)) {
          saveListState();
          deleteForm.submit();
        }
      });
    }
    const renameButton = root.querySelector('[data-scheduled-tasks-bulk-action="rename"]');
    if (renameButton && renameForm) {
      renameButton.addEventListener('click', () => {
        const count = selectedBoxes().length;
        if (count && window.confirm(`Rename ${plural(count)} to "Company — Task" names?`)) {
          mirror(renameForm);
          saveListState();
          renameForm.submit();
        }
      });
    }

    const redistributeButton = root.querySelector('[data-scheduled-tasks-bulk-action="redistribute"]');
    if (redistributeButton && redistributeForm && redistributeModal) {
      const timeInput = redistributeForm.querySelector('[data-sch-redistribute-time]');
      const hourInput = redistributeForm.querySelector('[data-scheduled-tasks-redistribute-hour]');
      const offsetInput = redistributeForm.querySelector('[data-scheduled-tasks-redistribute-offset]');
      const previewList = redistributeForm.querySelector('[data-sch-redistribute-preview]');
      const submit = redistributeForm.querySelector('[data-sch-redistribute-submit]');

      const renderPreview = () => {
        const [hour, minute] = (timeInput.value || '00:00').split(':').map(Number);
        const start = (hour || 0) * 60 + (minute || 0);
        const selected = selectedItems();
        previewList.innerHTML = '';
        selected.slice(0, 8).forEach((item, index) => {
          const total = (start + index) % 1440;
          const li = document.createElement('li');
          const time = document.createElement('strong');
          time.textContent = `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
          li.append(time, ` ${item.getAttribute('data-name')}`);
          previewList.appendChild(li);
        });
        if (selected.length > 8) {
          const li = document.createElement('li');
          li.className = 'text-muted';
          li.textContent = `…and ${selected.length - 8} more, ending at ${(() => {
            const total = (start + selected.length - 1) % 1440;
            return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
          })()} ${timeZone}`;
          previewList.appendChild(li);
        }
        if (submit) {
          submit.textContent = `Update ${plural(selected.length)}`;
        }
      };

      redistributeButton.addEventListener('click', () => {
        if (!selectedBoxes().length) {
          return;
        }
        renderPreview();
        showError(redistributeForm, '', '', 'data-sch-redistribute-error');
        openModal(redistributeModal, redistributeButton);
      });
      timeInput.addEventListener('input', renderPreview);
      redistributeForm.addEventListener('submit', (event) => {
        const match = /^(\d{1,2}):(\d{2})$/.exec(timeInput.value || '');
        const errorNode = redistributeForm.querySelector('[data-sch-redistribute-error]');
        if (!match || !selectedBoxes().length) {
          event.preventDefault();
          if (errorNode) {
            errorNode.textContent = match ? 'Select at least one task first.' : 'Choose a start time.';
            errorNode.hidden = false;
          }
          return;
        }
        hourInput.value = String(Number(match[1]));
        offsetInput.value = String(Number(match[2]));
        mirror(redistributeForm);
        saveListState();
      });
    }

    return { refresh };
  }

  // ---------------------------------------------------------------------------
  // Schedule builder (shared by the task editor)
  // ---------------------------------------------------------------------------

  function cronToBuilder(expression) {
    const parts = String(expression || '').trim().split(/\s+/).filter(Boolean);
    const fallback = { mode: 'custom' };
    if (parts.length !== 5) {
      return fallback;
    }
    const [minute, hour, dom, month, dow] = parts;
    const isNumber = (text) => /^\d+$/.test(text);
    const time = () => `${pad(hour)}:${pad(minute)}`;
    if (month !== '*') {
      return fallback;
    }
    const stepMinutes = /^\*\/(\d+)$/.exec(minute);
    if (stepMinutes && hour === '*' && dom === '*' && dow === '*' && ['2', '5', '10', '15', '20', '30'].includes(stepMinutes[1])) {
      return { mode: 'minutes', everyMinutes: stepMinutes[1] };
    }
    if (minute === '*' && hour === '*' && dom === '*' && dow === '*') {
      return { mode: 'minutes', everyMinutes: '1' };
    }
    if (!isNumber(minute) || Number(minute) > 59) {
      return fallback;
    }
    const stepHours = /^\*\/(\d+)$/.exec(hour);
    if ((hour === '*' || (stepHours && ['2', '3', '4', '6', '8', '12'].includes(stepHours[1]))) && dom === '*' && dow === '*') {
      return { mode: 'hourly', everyHours: stepHours ? stepHours[1] : '1', minute: String(Number(minute)) };
    }
    if (!isNumber(hour) || Number(hour) > 23) {
      return fallback;
    }
    if (dom === '*' && dow === '*') {
      return { mode: 'daily', time: time() };
    }
    if (dom === '*' && /^[0-7](,[0-7])*$/.test(dow)) {
      return { mode: 'weekly', time: time(), days: Array.from(new Set(dow.split(',').map((value) => String(Number(value) % 7)))) };
    }
    if (dom === '*' && dow === '1-5') {
      return { mode: 'weekly', time: time(), days: ['1', '2', '3', '4', '5'] };
    }
    if (dow === '*' && (dom === 'L' || (isNumber(dom) && Number(dom) >= 1 && Number(dom) <= 28))) {
      return { mode: 'monthly', time: time(), monthDay: dom === 'L' ? 'L' : String(Number(dom)) };
    }
    return fallback;
  }

  function builderToCron(state) {
    const [hour, minute] = (state.time || '00:00').split(':').map((value) => Number(value) || 0);
    switch (state.mode) {
      case 'minutes':
        return state.everyMinutes === '1' ? '* * * * *' : `*/${state.everyMinutes} * * * *`;
      case 'hourly': {
        const at = Math.min(59, Math.max(0, Number(state.minute) || 0));
        return `${at} ${state.everyHours === '1' ? '*' : `*/${state.everyHours}`} * * *`;
      }
      case 'daily':
        return `${minute} ${hour} * * *`;
      case 'weekly': {
        const days = (state.days || []).map(Number).sort((a, b) => a - b);
        const key = days.join(',');
        return `${minute} ${hour} * * ${key === '1,2,3,4,5' ? '1-5' : key || '*'}`;
      }
      case 'monthly':
        return `${minute} ${hour} ${state.monthDay || '1'} * *`;
      default:
        return null;
    }
  }

  function initBuilder(container, cronInput, onChange) {
    const modes = Array.from(container.querySelectorAll('[data-sch-mode]'));
    const panels = Array.from(container.querySelectorAll('[data-sch-mode-panel]'));
    const part = (name) => container.querySelector(`[data-sch-part="${name}"]`);
    const days = Array.from(container.querySelectorAll('[data-sch-day]'));
    const cronHelp = container.querySelector('[data-sch-cron-help]');

    const currentMode = () => {
      const checked = modes.find((input) => input.checked);
      return checked ? checked.value : 'custom';
    };

    function showPanels() {
      const mode = currentMode();
      panels.forEach((panel) => {
        panel.hidden = !panel.getAttribute('data-sch-mode-panel').split(' ').includes(mode);
      });
      const custom = mode === 'custom';
      cronInput.readOnly = !custom;
      cronInput.classList.toggle('is-readonly', !custom);
      if (cronHelp) {
        cronHelp.textContent = custom
          ? 'Five parts: minute, hour, day of month, month, day of week, plus an optional year (1970 to 2099). For example 30 6 * * 1-5 runs at 06:30 on weekdays.'
          : 'Built from the choices above. Pick Custom to write your own.';
      }
    }

    function writeCron() {
      const mode = currentMode();
      if (mode !== 'custom') {
        const cron = builderToCron({
          mode,
          everyMinutes: part('everyMinutes').value,
          everyHours: part('everyHours').value,
          minute: part('minute').value,
          time: part('time').value,
          monthDay: part('monthDay').value,
          days: days.filter((input) => input.checked).map((input) => input.value),
        });
        if (cron) {
          cronInput.value = cron;
        }
      }
      showPanels();
      onChange();
    }

    function load(expression) {
      const state = cronToBuilder(expression);
      modes.forEach((input) => {
        input.checked = input.value === state.mode;
      });
      if (state.everyMinutes) {
        part('everyMinutes').value = state.everyMinutes;
      }
      if (state.everyHours) {
        part('everyHours').value = state.everyHours;
      }
      if (state.minute !== undefined) {
        part('minute').value = state.minute;
      }
      if (state.time) {
        part('time').value = state.time;
      }
      if (state.monthDay) {
        part('monthDay').value = state.monthDay;
      }
      days.forEach((input) => {
        input.checked = Boolean(state.days && state.days.includes(input.value));
      });
      if (state.mode === 'weekly' && !days.some((input) => input.checked)) {
        days[0].checked = true;
      }
      cronInput.value = expression || '';
      showPanels();
    }

    modes.forEach((input) => input.addEventListener('change', () => {
      if (currentMode() === 'weekly' && !days.some((day) => day.checked)) {
        days[0].checked = true;
      }
      writeCron();
    }));
    container.querySelectorAll('[data-sch-part], [data-sch-day]').forEach((input) => {
      input.addEventListener('input', writeCron);
      input.addEventListener('change', writeCron);
    });
    cronInput.addEventListener('input', onChange);

    return { load, mode: currentMode };
  }

  // ---------------------------------------------------------------------------
  // Task editor
  // ---------------------------------------------------------------------------

  function initEditor(modal, data, timeZone) {
    const form = modal && modal.querySelector('[data-sch-form]');
    const noop = { open() {} };
    if (!form) {
      return noop;
    }
    const field = (id) => document.getElementById(id);
    const idField = field('task-id');
    const nameField = field('task-name');
    const nameDisplay = field('task-name-display');
    const commandField = field('task-command');
    const companyField = field('task-company');
    const descriptionField = field('task-description');
    const descriptionWrap = field('task-description-field');
    const payloadWrap = field('task-json-payload-field');
    const payloadField = field('task-json-payload');
    const cronField = field('task-cron');
    const activeField = field('task-active');
    const hideField = field('task-exclude-calendar');
    const retriesField = field('task-max-retries');
    const backoffField = field('task-backoff');
    const backoffHelp = form.querySelector('[data-sch-backoff-help]');
    const title = form.querySelector('[data-sch-editor-title]');
    const subtitle = form.querySelector('[data-sch-editor-subtitle]');
    const saveButton = form.querySelector('[data-sch-save]');
    const deleteButton = form.querySelector('[data-task-delete-modal]');
    const tabs = Array.from(form.querySelectorAll('[data-sch-tab]'));
    const panels = Array.from(form.querySelectorAll('[data-sch-panel]'));
    const preview = {
      name: form.querySelector('[data-sch-preview-name]'),
      schedule: form.querySelector('[data-sch-preview-schedule]'),
      paused: form.querySelector('[data-sch-preview-paused]'),
      runs: form.querySelector('[data-sch-preview-runs]'),
      rules: form.querySelector('[data-sch-preview-rules]'),
    };
    const defaults = data.commandDefaults || {};
    let snapshot = '';
    let editing = null;
    let nameLocked = '';

    const builder = initBuilder(form.querySelector('[data-sch-builder]'), cronField, update);

    function showTab(name) {
      tabs.forEach((tab) => {
        const active = tab.getAttribute('data-sch-tab') === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
      });
      panels.forEach((panel) => {
        panel.hidden = panel.getAttribute('data-sch-panel') !== name;
      });
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => showTab(tab.getAttribute('data-sch-tab')));
      tab.addEventListener('keydown', (event) => {
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
          showTab(next.getAttribute('data-sch-tab'));
          next.focus();
        }
      });
    });

    const selectedLabel = (select) => {
      const option = select.options[select.selectedIndex];
      return option ? option.textContent.trim() : '';
    };

    function generatedName() {
      return `${selectedLabel(companyField) || 'All companies'} — ${selectedLabel(commandField) || 'Task'}`;
    }

    function isTicketTask() {
      return commandField.value === 'create_scheduled_ticket';
    }

    function serialize() {
      return JSON.stringify(Array.from(new FormData(form).entries()));
    }

    function renderRuns(runs) {
      preview.runs.innerHTML = '';
      runs.forEach((run) => {
        const li = document.createElement('li');
        const when = document.createElement('strong');
        when.textContent = formatShort(run);
        const relative = document.createElement('span');
        relative.className = 'text-muted';
        relative.textContent = ` ${formatRelative(run)}`;
        li.append(when, relative);
        preview.runs.appendChild(li);
      });
    }

    function update() {
      const name = nameLocked || generatedName();
      nameField.value = name;
      nameDisplay.value = name;
      preview.name.textContent = name;

      const ticket = isTicketTask();
      descriptionWrap.hidden = ticket;
      if (payloadWrap) {
        payloadWrap.hidden = !ticket;
      }

      const cron = cronField.value.trim();
      let cronError = '';
      let runs = [];
      if (cron) {
        try {
          runs = nextRuns(cron, 5, timeZone);
        } catch (error) {
          cronError = `This schedule can't be read: ${error.message}.`;
        }
      }
      showError(form, 'cron', cronError);
      cronField.classList.toggle('is-invalid', Boolean(cronError));
      tabs.find((tab) => tab.getAttribute('data-sch-tab') === 'when').classList.toggle('has-error', Boolean(cronError));

      const summary = describeCron(cron);
      if (!cron) {
        preview.schedule.textContent = 'Choose when it runs.';
      } else if (cronError) {
        preview.schedule.textContent = 'Schedule needs fixing.';
      } else {
        preview.schedule.textContent = `${summary || `Custom schedule (${cron})`}, ${timeZone}`;
      }
      preview.paused.hidden = activeField.checked;
      renderRuns(activeField.checked ? runs : []);
      if (!runs.length && cron && !cronError) {
        const li = document.createElement('li');
        li.className = 'text-muted';
        li.textContent = 'No upcoming runs for this schedule.';
        preview.runs.appendChild(li);
      } else if (!activeField.checked) {
        const li = document.createElement('li');
        li.className = 'text-muted';
        li.textContent = 'None while paused.';
        preview.runs.appendChild(li);
      }

      const retries = Number(retriesField.value);
      const backoff = Number(backoffField.value);
      if (backoffHelp) {
        backoffHelp.textContent = Number.isFinite(backoff) && backoff >= 30 ? `${formatWait(backoff)} between attempts.` : 'At least 30 seconds.';
      }
      const rules = [];
      if (Number.isFinite(retries)) {
        rules.push(retries === 0
          ? 'Not retried if it fails.'
          : `Retried up to ${retries} time${retries === 1 ? '' : 's'}, ${formatWait(backoff) || '—'} apart, if it fails.`);
      }
      rules.push(companyField.value ? `Runs for ${selectedLabel(companyField)} only.` : 'Runs once for all companies.');
      if (hideField.checked) {
        rules.push('Hidden from the schedule calendar.');
      }
      preview.rules.innerHTML = '';
      rules.forEach((text) => {
        const li = document.createElement('li');
        li.className = 'scf-preview__rule';
        li.textContent = text;
        preview.rules.appendChild(li);
      });

      tabs.find((tab) => tab.getAttribute('data-sch-tab') === 'options').classList.toggle(
        'is-configured',
        !activeField.checked || hideField.checked || retries !== 12 || backoff !== 300,
      );
    }

    function open(task, trigger, duplicate) {
      const record = task || {};
      editing = record.id ? record : null;
      nameLocked = editing && record.name ? String(record.name) : '';
      form.reset();
      ['command', 'cron', 'retries', 'payload', 'save'].forEach((key) => showError(form, key, ''));
      idField.value = editing ? String(record.id) : '';

      commandField.value = record.command || '';
      if (!commandField.value && commandField.options.length) {
        commandField.selectedIndex = 0;
      }
      companyField.value = record.company_id === null || record.company_id === undefined ? '' : String(record.company_id);

      const description = record.description || '';
      if (record.command === 'create_scheduled_ticket') {
        descriptionField.value = '';
        if (payloadField) {
          payloadField.value = description;
        }
      } else {
        descriptionField.value = description;
        if (payloadField) {
          payloadField.value = '';
        }
      }
      if (payloadField) {
        payloadField.dispatchEvent(new Event('input', { bubbles: true }));
      }

      builder.load(record.cron || defaults[commandField.value] || '0 2 * * *');
      activeField.checked = record.active !== false;
      hideField.checked = Boolean(record.exclude_from_calendar);
      const retries = Number(record.max_retries);
      retriesField.value = record.max_retries !== null && record.max_retries !== undefined && retries >= 0 ? retries : 12;
      backoffField.value = Number(record.retry_backoff_seconds) >= 30 ? record.retry_backoff_seconds : 300;

      title.textContent = editing ? record.name || `Task ${record.id}` : duplicate ? 'Duplicate task' : 'New task';
      subtitle.textContent = editing
        ? `${record.command_label || record.command} · ${record.company_name || 'All companies'}`
        : duplicate
          ? 'A copy of the task. Change the company or schedule, then create it.'
          : 'Choose what to run, for whom, and when.';
      saveButton.textContent = editing ? 'Save changes' : 'Create task';
      if (deleteButton) {
        deleteButton.hidden = !editing;
        deleteButton.disabled = !editing;
      }
      showTab('what');
      update();
      snapshot = serialize();
      openModal(modal, trigger);
    }

    modal.__confirmClose = () => serialize() === snapshot || window.confirm('Discard your unsaved changes to this task?');

    commandField.addEventListener('change', () => {
      nameLocked = '';
      if (!editing && defaults[commandField.value]) {
        builder.load(defaults[commandField.value]);
      }
      showError(form, 'command', '');
      update();
    });
    companyField.addEventListener('change', () => {
      nameLocked = '';
      update();
    });
    [activeField, hideField, retriesField, backoffField].forEach((input) => {
      input.addEventListener('input', update);
      input.addEventListener('change', update);
    });

    function validate() {
      const problems = [];
      if (!commandField.value) {
        problems.push(['command', 'what', 'Choose a task to run.']);
      }
      const cron = cronField.value.trim();
      if (!cron) {
        problems.push(['cron', 'when', 'Choose when the task runs.']);
      } else {
        try {
          parseCron(cron);
        } catch (error) {
          problems.push(['cron', 'when', `This schedule can't be read: ${error.message}.`]);
        }
      }
      const retries = Number(retriesField.value);
      const backoff = Number(backoffField.value);
      if (!Number.isInteger(retries) || retries < 0) {
        problems.push(['retries', 'options', 'Retry attempts must be 0 or more.']);
      } else if (!Number.isFinite(backoff) || backoff < 30) {
        problems.push(['retries', 'options', 'Wait at least 30 seconds between retries.']);
      }
      let payload = null;
      if (isTicketTask()) {
        const raw = payloadField ? payloadField.value.trim() : '';
        let parsed = null;
        try {
          parsed = raw ? JSON.parse(raw) : null;
        } catch (error) {
          parsed = null;
        }
        if (!parsed || typeof parsed !== 'object' || !String(parsed.subject || '').trim()) {
          problems.push(['command', 'what', 'Give the ticket a subject.']);
        } else {
          payload = raw;
        }
      }
      return { problems, payload };
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      ['command', 'cron', 'retries', 'save'].forEach((key) => showError(form, key, ''));
      tabs.forEach((tab) => tab.classList.remove('has-error'));
      const { problems, payload } = validate();
      if (problems.length) {
        problems.forEach(([key, tab, message]) => {
          showError(form, key, message);
          const tabNode = tabs.find((node) => node.getAttribute('data-sch-tab') === tab);
          if (tabNode) {
            tabNode.classList.add('has-error');
          }
        });
        showTab(problems[0][1]);
        return;
      }
      const body = {
        name: nameField.value.trim() || generatedName(),
        command: commandField.value,
        cron: cronField.value.trim(),
        description: isTicketTask() ? payload : descriptionField.value.trim() || null,
        active: activeField.checked,
        excludeFromCalendar: hideField.checked,
        maxRetries: Number(retriesField.value),
        retryBackoffSeconds: Number(backoffField.value),
        companyId: companyField.value ? Number(companyField.value) : null,
      };
      saveButton.disabled = true;
      saveButton.setAttribute('aria-busy', 'true');
      try {
        await requestJson(editing ? `/scheduler/tasks/${editing.id}` : '/scheduler/tasks', {
          method: editing ? 'PUT' : 'POST',
          body: JSON.stringify(body),
        });
        snapshot = serialize();
        reloadPage();
      } catch (error) {
        showError(form, 'save', `Couldn't save: ${error.message}`);
        saveButton.disabled = false;
        saveButton.removeAttribute('aria-busy');
      }
    });

    if (deleteButton) {
      deleteButton.addEventListener('click', async () => {
        if (!editing || !window.confirm(`Delete "${editing.name || `task ${editing.id}`}"? This can't be undone.`)) {
          return;
        }
        deleteButton.disabled = true;
        try {
          await requestJson(`/scheduler/tasks/${editing.id}`, { method: 'DELETE' });
          snapshot = serialize();
          reloadPage();
        } catch (error) {
          deleteButton.disabled = false;
          showError(form, 'save', `Couldn't delete: ${error.message}`);
        }
      });
    }

    return { open };
  }

  // ---------------------------------------------------------------------------
  // Bulk create
  // ---------------------------------------------------------------------------

  function initBulkCreate(modal, timeZone) {
    const form = modal && modal.querySelector('[data-sch-bulk-form]');
    if (!form) {
      return;
    }
    const commandField = document.getElementById('bulk-task-command');
    const descriptionWrap = document.getElementById('bulk-task-description-field');
    const payloadWrap = document.getElementById('bulk-task-json-payload-field');
    const payloadField = document.getElementById('bulk-task-json-payload');
    const cronField = document.getElementById('bulk-task-cron');
    const staggerPanel = form.querySelector('[data-sch-bulk-stagger]');
    const timings = Array.from(form.querySelectorAll('[data-sch-bulk-timing]'));
    const part = (name) => form.querySelector(`[data-sch-bulk-part="${name}"]`);
    const rows = Array.from(form.querySelectorAll('[data-sch-bulk-company-row]'));
    const boxes = Array.from(form.querySelectorAll('[data-sch-bulk-company]'));
    const counter = form.querySelector('[data-sch-bulk-company-count]');
    const search = form.querySelector('[data-sch-bulk-company-search]');
    const previewTitle = form.querySelector('[data-sch-bulk-preview-title]');
    const previewSchedule = form.querySelector('[data-sch-bulk-preview-schedule]');
    const previewList = form.querySelector('[data-sch-bulk-preview-list]');
    const submit = form.querySelector('[data-sch-bulk-submit]');
    let snapshot = '';
    let cronEdited = false;

    const timing = () => (timings.find((input) => input.checked) || {}).value || 'random';
    const commandLabel = () => {
      const option = commandField.options[commandField.selectedIndex];
      return option ? option.textContent.trim() : '';
    };
    const serialize = () => JSON.stringify(Array.from(new FormData(form).entries()));

    function staggerCron() {
      const [hour, minute] = (part('time').value || '02:00').split(':').map((value) => Number(value) || 0);
      switch (part('repeat').value) {
        case 'weekdays':
          return `${minute} ${hour} * * 1-5`;
        case 'weekly':
          return `${minute} ${hour} * * ${part('weekday').value}`;
        case 'monthly':
          return `${minute} ${hour} ${part('monthDay').value} * *`;
        default:
          return `${minute} ${hour} * * *`;
      }
    }

    function update() {
      const ticket = commandField.value === 'create_scheduled_ticket';
      descriptionWrap.hidden = ticket;
      if (payloadWrap) {
        payloadWrap.hidden = !ticket;
      }
      const stagger = timing() === 'stagger';
      staggerPanel.hidden = !stagger;
      part('weekday').hidden = part('repeat').value !== 'weekly';
      part('monthDay').hidden = part('repeat').value !== 'monthly';
      if (!cronEdited) {
        cronField.value = stagger ? staggerCron() : '';
      }

      const selected = boxes.filter((box) => box.checked);
      if (counter) {
        counter.textContent = String(selected.length);
      }
      submit.textContent = selected.length ? `Create ${selected.length} task${selected.length === 1 ? '' : 's'}` : 'Create tasks';
      previewTitle.textContent = selected.length
        ? `${selected.length} × ${commandLabel()}`
        : 'Pick at least one company.';

      const cron = cronField.value.trim();
      const fields = cron.split(/\s+/);
      const numericStart = /^\d+$/.test(fields[0] || '') && /^\d+$/.test(fields[1] || '');
      previewSchedule.textContent = cron
        ? `${describeCron(cron) || `Custom schedule (${cron})`} for the first company, then one minute later for each next one. Times are ${timeZone}.`
        : 'Each company gets its own random time every day.';

      previewList.innerHTML = '';
      selected.slice(0, 6).forEach((box, index) => {
        const li = document.createElement('li');
        const label = box.closest('label').textContent.trim();
        if (cron && numericStart) {
          const total = (Number(fields[1]) * 60 + Number(fields[0]) + index) % 1440;
          const time = document.createElement('strong');
          time.textContent = `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
          li.append(time, ` ${label} — ${commandLabel()}`);
        } else {
          li.textContent = `${label} — ${commandLabel()}`;
        }
        previewList.appendChild(li);
      });
      if (selected.length > 6) {
        const li = document.createElement('li');
        li.className = 'text-muted';
        li.textContent = `…and ${selected.length - 6} more`;
        previewList.appendChild(li);
      }
    }

    form.addEventListener('input', (event) => {
      if (event.target === cronField) {
        cronEdited = cronField.value.trim() !== '';
        if (cronEdited) {
          timings.forEach((input) => {
            input.checked = input.value === 'stagger';
          });
        }
      }
      if (event.target === search) {
        const query = search.value.trim().toLocaleLowerCase();
        rows.forEach((row) => {
          row.hidden = Boolean(query) && !(row.getAttribute('data-search-text') || '').toLocaleLowerCase().includes(query);
        });
        return;
      }
      update();
    });
    form.addEventListener('change', (event) => {
      if (event.target && event.target.hasAttribute && event.target.hasAttribute('data-sch-bulk-timing')) {
        cronEdited = false;
      }
      update();
    });
    form.querySelectorAll('[data-sch-bulk-companies]').forEach((button) => {
      button.addEventListener('click', () => {
        const select = button.getAttribute('data-sch-bulk-companies') === 'all';
        rows.forEach((row) => {
          const box = row.querySelector('[data-sch-bulk-company]');
          if (select ? !row.hidden : true) {
            box.checked = select;
          }
        });
        update();
      });
    });

    form.addEventListener('submit', (event) => {
      ['companies', 'cron', 'payload'].forEach((key) => showError(form, key, '', 'data-sch-bulk-error'));
      let valid = true;
      if (!boxes.some((box) => box.checked)) {
        showError(form, 'companies', 'Pick at least one company.', 'data-sch-bulk-error');
        valid = false;
      }
      const cron = cronField.value.trim();
      if (cron) {
        const fields = cron.split(/\s+/);
        try {
          parseCron(cron);
          if (!/^\d+$/.test(fields[0]) || !/^\d+$/.test(fields[1])) {
            throw new Error('the minute and hour must be plain numbers');
          }
        } catch (error) {
          showError(form, 'cron', `This start time can't be used: ${error.message}.`, 'data-sch-bulk-error');
          valid = false;
        }
      }
      if (commandField.value === 'create_scheduled_ticket') {
        let parsed = null;
        try {
          parsed = JSON.parse(payloadField ? payloadField.value : '');
        } catch (error) {
          parsed = null;
        }
        if (!parsed || !String(parsed.subject || '').trim()) {
          showError(form, 'payload', 'Give the ticket a subject.', 'data-sch-bulk-error');
          valid = false;
        }
      }
      if (!valid) {
        event.preventDefault();
        const firstError = form.querySelector('[data-sch-bulk-error]:not([hidden])');
        if (firstError) {
          firstError.scrollIntoView({ block: 'center' });
        }
        return;
      }
      snapshot = serialize();
      saveListState();
      submit.disabled = true;
    });

    modal.__confirmClose = () => serialize() === snapshot || window.confirm('Discard this bulk task?');
    update();
    snapshot = serialize();
  }

  // ---------------------------------------------------------------------------
  // Run history
  // ---------------------------------------------------------------------------

  function initHistory(modal) {
    if (!modal) {
      return { open() {} };
    }
    const title = modal.querySelector('#task-logs-title');
    const description = modal.querySelector('#task-logs-description');
    const listNode = modal.querySelector('#task-logs-body');
    const empty = modal.querySelector('[data-sch-logs-empty]');
    const stats = modal.querySelector('[data-sch-logs-stats]');
    const search = modal.querySelector('[data-sch-logs-search]');
    const filter = modal.querySelector('[data-sch-logs-filter]');
    let runs = [];
    let request = 0;

    const stat = (name, value) => {
      const node = modal.querySelector(`[data-sch-logs-stat="${name}"]`);
      if (node) {
        node.textContent = value;
      }
    };

    function render() {
      const query = search.value.trim().toLocaleLowerCase();
      const status = filter.value;
      listNode.innerHTML = '';
      const shown = runs.filter((run) => {
        const text = `${run.status || ''} ${run.details || ''}`.toLocaleLowerCase();
        return (!status || run.status === status) && (!query || text.includes(query));
      });
      shown.forEach((run) => {
        const li = document.createElement('li');
        li.className = 'scf-item sch-run';
        const icon = document.createElement('span');
        icon.className = `sch-status-icon sch-status-icon--${run.status || 'never'}`;
        icon.setAttribute('aria-hidden', 'true');
        const main = document.createElement('div');
        main.className = 'scf-item__main';
        const heading = document.createElement('div');
        heading.className = 'scf-item__heading';
        const chip = document.createElement('span');
        chip.className = `scf-chip sch-chip sch-chip--${run.status || 'never'}`;
        chip.textContent = run.status ? run.status.charAt(0).toUpperCase() + run.status.slice(1).replace(/_/g, ' ') : 'Unknown';
        heading.appendChild(chip);
        const started = new Date(run.started_at || run.startedAt || '');
        if (!Number.isNaN(started.getTime())) {
          const when = document.createElement('span');
          when.className = 'sch-run__when';
          when.textContent = `${formatShort(started)} · ${formatRelative(started)}`;
          when.title = started.toLocaleString();
          heading.appendChild(when);
        }
        const duration = typeof run.duration_ms === 'number' ? run.duration_ms : run.durationMs;
        if (Number.isFinite(duration)) {
          const took = document.createElement('span');
          took.className = 'text-muted';
          took.textContent = `took ${formatDuration(duration)}`;
          heading.appendChild(took);
        }
        main.appendChild(heading);
        if (run.details) {
          const details = document.createElement('p');
          details.className = `sch-run__details${run.status === 'failed' ? ' sch-run__details--failed' : ''}`;
          details.textContent = run.details;
          main.appendChild(details);
        }
        li.append(icon, main);
        listNode.appendChild(li);
      });
      if (!runs.length) {
        empty.textContent = 'This task hasn\'t run yet.';
      } else if (!shown.length) {
        empty.textContent = 'No runs match.';
      }
      empty.hidden = shown.length > 0;
      listNode.hidden = shown.length === 0;
    }

    async function open(task, trigger) {
      const token = ++request;
      title.textContent = task.name || `Task ${task.id}`;
      description.textContent = `${task.command_label || task.command} · the last 50 runs, in your local time.`;
      runs = [];
      search.value = '';
      filter.value = '';
      stats.hidden = true;
      listNode.innerHTML = '';
      listNode.hidden = true;
      empty.hidden = false;
      empty.textContent = 'Loading runs…';
      openModal(modal, trigger);
      try {
        const result = await requestJson(`/scheduler/tasks/${task.id}/runs?limit=50`, { cache: 'no-store' });
        if (token !== request) {
          return;
        }
        runs = Array.isArray(result) ? result : [];
        const durations = runs
          .map((run) => (typeof run.duration_ms === 'number' ? run.duration_ms : run.durationMs))
          .filter(Number.isFinite)
          .sort((a, b) => a - b);
        stat('total', String(runs.length));
        stat('succeeded', String(runs.filter((run) => run.status === 'succeeded').length));
        stat('failed', String(runs.filter((run) => run.status === 'failed').length));
        stat('duration', durations.length ? formatDuration(durations[Math.floor(durations.length / 2)]) : '—');
        stats.hidden = runs.length === 0;
        render();
      } catch (error) {
        if (token === request) {
          empty.textContent = `Couldn't load runs: ${error.message}`;
        }
      }
    }

    search.addEventListener('input', render);
    filter.addEventListener('change', render);
    return { open };
  }

  // ---------------------------------------------------------------------------
  // Preview
  // ---------------------------------------------------------------------------

  function humanize(key) {
    return String(key || '')
      .replace(/_/g, ' ')
      .replace(/([a-z])([A-Z])/g, '$1 $2')
      .replace(/^./, (char) => char.toUpperCase());
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === '') {
      return '';
    }
    if (Array.isArray(value)) {
      return value.map(formatValue).filter(Boolean).join(', ');
    }
    if (typeof value === 'object') {
      return Object.entries(value)
        .filter(([, nested]) => nested !== null && nested !== undefined && nested !== '')
        .map(([key, nested]) => `${humanize(key)}: ${formatValue(nested)}`)
        .join('; ');
    }
    return String(value);
  }

  function initPreview(modal) {
    if (!modal) {
      return { open() {} };
    }
    const title = modal.querySelector('#task-preview-title');
    const summary = modal.querySelector('#task-preview-summary');
    const totals = modal.querySelector('#task-preview-totals');
    const body = modal.querySelector('#task-preview-body');
    let request = 0;

    function placeholder(text) {
      body.innerHTML = '';
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 4;
      cell.className = 'table__empty';
      cell.textContent = text;
      row.appendChild(cell);
      body.appendChild(row);
    }

    function render(result) {
      summary.textContent = (result && result.summary) || 'No preview details were returned.';
      const entries = Object.entries((result && result.totals) || {});
      totals.innerHTML = '';
      totals.hidden = entries.length === 0;
      entries.forEach(([key, value]) => {
        const item = document.createElement('div');
        item.className = 'sif-stats__item';
        const dt = document.createElement('dt');
        dt.textContent = humanize(key);
        const dd = document.createElement('dd');
        dd.textContent = String(value);
        item.append(dt, dd);
        totals.appendChild(item);
      });
      const rows = Array.isArray(result && result.items) ? result.items : [];
      if (!rows.length) {
        placeholder('Nothing would be processed right now.');
        return;
      }
      body.innerHTML = '';
      const hidden = new Set(['type', 'id', 'label', 'action']);
      rows.forEach((entry) => {
        const row = document.createElement('tr');
        ['type', 'label', 'action'].forEach((key) => {
          const cell = document.createElement('td');
          cell.textContent = entry && entry[key] ? String(entry[key]) : '—';
          row.appendChild(cell);
        });
        const details = document.createElement('td');
        details.textContent = Object.entries(entry || {})
          .filter(([key, value]) => !hidden.has(key) && value !== null && value !== undefined && value !== '')
          .map(([key, value]) => `${humanize(key)}: ${formatValue(value)}`)
          .join(' · ') || '—';
        row.appendChild(details);
        body.appendChild(row);
      });
    }

    async function open(task, trigger) {
      const token = ++request;
      title.textContent = `What "${task.name || `task ${task.id}`}" will do`;
      summary.textContent = 'Loading preview…';
      totals.hidden = true;
      placeholder('Loading…');
      openModal(modal, trigger);
      try {
        const result = await requestJson(`/scheduler/tasks/${task.id}/preview`, { cache: 'no-store' });
        if (token === request) {
          render(result || {});
        }
      } catch (error) {
        if (token === request) {
          summary.textContent = `Couldn't load the preview: ${error.message}`;
          placeholder('No preview available.');
        }
      }
    }

    return { open };
  }

  // Exposed for tests and the schedule calendar.
  window.MyPortalCron = { parseCron, describeCron, nextRuns, cronToBuilder, builderToCron };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
