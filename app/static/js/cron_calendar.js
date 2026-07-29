(function () {
  const root = document.querySelector('[data-cron-calendar]');
  if (!root) return;

  const RUN_DURATION_MINUTES = 1;
  const MIN_AVAILABLE_MINUTES = 15;
  const state = { view: 'week', anchor: new Date(), events: [], search: '', includeInactive: false };
  const grid = root.querySelector('[data-calendar-grid]');
  const rangeLabel = root.querySelector('[data-calendar-range]');
  const countLabel = root.querySelector('[data-calendar-count]');
  const statusBox = root.querySelector('[data-calendar-status]');
  const searchInput = root.querySelector('[data-calendar-search]');
  const inactiveInput = root.querySelector('[data-calendar-inactive]');

  function startOfDay(date) { const result = new Date(date); result.setHours(0, 0, 0, 0); return result; }
  function addDays(date, days) { const result = new Date(date); result.setDate(result.getDate() + days); return result; }
  function startOfWeek(date) { const result = startOfDay(date); result.setDate(result.getDate() - result.getDay()); return result; }
  function escapeHtml(value) { return String(value || '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char])); }
  function formatDate(date, options) { return new Intl.DateTimeFormat(undefined, options).format(date); }
  function formatTime(date) { return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date); }
  function isSameDay(left, right) { return startOfDay(left).getTime() === startOfDay(right).getTime(); }
  function durationLabel(minutes) {
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    return remainder ? `${hours} hr ${remainder} min` : `${hours} hr`;
  }

  function rangeForView() {
    if (state.view === 'week') { const start = startOfWeek(state.anchor); return { start, end: addDays(start, 7) }; }
    if (state.view === 'list') { const start = startOfDay(state.anchor); return { start, end: addDays(start, 30) }; }
    const start = startOfDay(state.anchor); return { start, end: addDays(start, 1) };
  }

  function setStatus(message, isError) {
    statusBox.hidden = !message;
    statusBox.textContent = message || '';
    statusBox.classList.toggle('card--danger', Boolean(isError));
  }

  function updateRangeLabel() {
    const { start, end } = rangeForView();
    rangeLabel.textContent = state.view === 'day'
      ? formatDate(start, { dateStyle: 'full' })
      : `${formatDate(start, { month: 'short', day: 'numeric' })} – ${formatDate(addDays(end, -1), { month: 'short', day: 'numeric', year: 'numeric' })}`;
  }

  async function loadEvents() {
    const { start, end } = rangeForView();
    updateRangeLabel();
    setStatus('Loading scheduled tasks…', false);
    const params = new URLSearchParams({ start: start.toISOString(), end: end.toISOString(), include_inactive: state.includeInactive ? 'true' : 'false', limit: '1000' });
    try {
      const response = await fetch(`/scheduler/calendar?${params}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`Calendar request failed (${response.status})`);
      state.events = await response.json();
      setStatus('', false);
      render();
    } catch (error) {
      state.events = [];
      setStatus(error.message || 'Unable to load scheduled task calendar.', true);
      render();
    }
  }

  function filteredEvents() {
    const term = state.search.trim().toLowerCase();
    if (!term) return state.events;
    return state.events.filter(event => [event.title, event.command, event.cron, event.companyName].some(value => String(value || '').toLowerCase().includes(term)));
  }

  function eventHtml(event) {
    const startsAt = new Date(event.start);
    return `<a class="cron-calendar__event" href="${escapeHtml(event.url)}">
      <time datetime="${escapeHtml(startsAt.toISOString())}">${formatTime(startsAt)}</time>
      <span class="cron-calendar__event-body"><strong>${escapeHtml(event.title)}</strong><span>${escapeHtml(event.companyName || 'All companies')} · <code>${escapeHtml(event.cron)}</code></span><small>${escapeHtml(event.command)}</small></span>
      ${event.active ? '<span class="cron-calendar__badge">Scheduled</span>' : '<span class="cron-calendar__badge cron-calendar__badge--inactive">Inactive</span>'}
    </a>`;
  }

  function availabilityHtml(start, end) {
    const minutes = Math.floor((end - start) / 60000);
    if (minutes < MIN_AVAILABLE_MINUTES) return '';
    return `<div class="cron-calendar__availability"><span class="cron-calendar__availability-icon" aria-hidden="true">✓</span><span><strong>Available</strong><small>${formatTime(start)} – ${formatTime(end)} · ${durationLabel(minutes)}</small></span></div>`;
  }

  function dayHtml(day, events, showAvailability) {
    const sorted = [...events].sort((left, right) => new Date(left.start) - new Date(right.start));
    let schedule = '';
    let cursor = startOfDay(day);
    const dayEnd = addDays(cursor, 1);
    sorted.forEach(event => {
      const startsAt = new Date(event.start);
      if (showAvailability) schedule += availabilityHtml(cursor, startsAt);
      schedule += eventHtml(event);
      cursor = new Date(Math.max(cursor.getTime(), startsAt.getTime() + RUN_DURATION_MINUTES * 60000));
    });
    if (showAvailability) schedule += availabilityHtml(cursor, dayEnd);
    if (!schedule) schedule = '<p class="cron-calendar__empty">No scheduled runs.</p>';
    const today = isSameDay(day, new Date());
    return `<section class="cron-calendar__day${today ? ' is-today' : ''}">
      <header class="cron-calendar__day-header"><div><span>${formatDate(day, { weekday: 'long' })}</span><strong>${formatDate(day, { month: 'short', day: 'numeric' })}</strong></div><span class="cron-calendar__run-count">${sorted.length} ${sorted.length === 1 ? 'run' : 'runs'}</span></header>
      <div class="cron-calendar__schedule">${schedule}</div>
    </section>`;
  }

  function render() {
    const events = filteredEvents();
    const { start, end } = rangeForView();
    const numberOfDays = Math.round((end - start) / 86400000);
    const days = Array.from({ length: numberOfDays }, (_, index) => addDays(start, index));
    countLabel.textContent = String(events.length);
    grid.className = `cron-calendar__grid cron-calendar__grid--${state.view}`;
    grid.innerHTML = days.map(day => dayHtml(day, events.filter(event => isSameDay(new Date(event.start), day)), state.view !== 'list')).join('');
  }

  document.querySelectorAll('[data-calendar-view]').forEach(button => {
    const active = button.dataset.calendarView === state.view;
    button.classList.toggle('button--primary', active);
    button.classList.toggle('button--ghost', !active);
    button.setAttribute('aria-pressed', String(active));
    button.addEventListener('click', () => {
      state.view = button.dataset.calendarView;
      document.querySelectorAll('[data-calendar-view]').forEach(item => {
        const selected = item === button;
        item.classList.toggle('button--primary', selected);
        item.classList.toggle('button--ghost', !selected);
        item.setAttribute('aria-pressed', String(selected));
      });
      loadEvents();
    });
  });
  root.querySelector('[data-calendar-prev]').addEventListener('click', () => { state.anchor = addDays(state.anchor, state.view === 'week' ? -7 : state.view === 'list' ? -30 : -1); loadEvents(); });
  root.querySelector('[data-calendar-next]').addEventListener('click', () => { state.anchor = addDays(state.anchor, state.view === 'week' ? 7 : state.view === 'list' ? 30 : 1); loadEvents(); });
  root.querySelector('[data-calendar-today]').addEventListener('click', () => { state.anchor = new Date(); loadEvents(); });
  searchInput.addEventListener('input', () => { state.search = searchInput.value || ''; render(); });
  inactiveInput.addEventListener('change', () => { state.includeInactive = inactiveInput.checked; loadEvents(); });
  loadEvents();
}());
