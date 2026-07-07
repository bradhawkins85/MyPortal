(function () {
  const root = document.querySelector('[data-cron-calendar]');
  if (!root) return;

  const state = { view: 'day', anchor: new Date(), events: [], search: '', includeInactive: false };
  const grid = root.querySelector('[data-calendar-grid]');
  const rangeLabel = root.querySelector('[data-calendar-range]');
  const countLabel = root.querySelector('[data-calendar-count]');
  const statusBox = root.querySelector('[data-calendar-status]');
  const searchInput = root.querySelector('[data-calendar-search]');
  const inactiveInput = root.querySelector('[data-calendar-inactive]');

  function startOfDay(date) { const d = new Date(date); d.setHours(0, 0, 0, 0); return d; }
  function addDays(date, days) { const d = new Date(date); d.setDate(d.getDate() + days); return d; }
  function startOfWeek(date) { const d = startOfDay(date); d.setDate(d.getDate() - d.getDay()); return d; }
  function startOfMonth(date) { return new Date(date.getFullYear(), date.getMonth(), 1); }
  function addMonths(date, months) { return new Date(date.getFullYear(), date.getMonth() + months, 1); }
  function escapeHtml(value) { return String(value || '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
  function formatDate(date, opts) { return new Intl.DateTimeFormat(undefined, opts).format(date); }
  function formatTime(date) { return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(date); }

  function rangeForView() {
    if (state.view === 'month') { const start = startOfMonth(state.anchor); return { start, end: addMonths(start, 1) }; }
    if (state.view === 'week') { const start = startOfWeek(state.anchor); return { start, end: addDays(start, 7) }; }
    if (state.view === 'list') { const start = startOfDay(state.anchor); return { start, end: addDays(start, 30) }; }
    const start = startOfDay(state.anchor); return { start, end: addDays(start, 1) };
  }

  function setStatus(message, isError) {
    statusBox.hidden = !message;
    statusBox.textContent = message || '';
    statusBox.classList.toggle('card--danger', Boolean(isError));
  }

  async function loadEvents() {
    const { start, end } = rangeForView();
    rangeLabel.textContent = `${formatDate(start, { dateStyle: 'medium' })} – ${formatDate(addDays(end, -1), { dateStyle: 'medium' })}`;
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
      <strong>${escapeHtml(event.title)}</strong>
      <span class="cron-calendar__event-meta"><span>${formatTime(startsAt)}</span><span>${escapeHtml(event.companyName)}</span><code>${escapeHtml(event.cron)}</code><span>${escapeHtml(event.command)}</span>${event.active ? '' : '<span>Inactive</span>'}</span>
    </a>`;
  }

  function renderPeriod(label, events) {
    return `<section class="cron-calendar__period"><div class="cron-calendar__period-header">${escapeHtml(label)}</div><div class="cron-calendar__events">${events.length ? events.map(eventHtml).join('') : '<p class="cron-calendar__empty">No scheduled runs.</p>'}</div></section>`;
  }

  function render() {
    const events = filteredEvents();
    countLabel.textContent = String(events.length);
    grid.className = `cron-calendar__grid cron-calendar__grid--${state.view}`;
    const { start } = rangeForView();
    if (state.view === 'day') {
      grid.innerHTML = renderPeriod(formatDate(start, { weekday: 'long', dateStyle: 'full' }), events);
      return;
    }
    if (state.view === 'list') {
      const byDay = new Map();
      events.forEach(event => { const key = formatDate(new Date(event.start), { dateStyle: 'full' }); byDay.set(key, [...(byDay.get(key) || []), event]); });
      grid.innerHTML = byDay.size ? Array.from(byDay.entries()).map(([label, items]) => renderPeriod(label, items)).join('') : renderPeriod('Upcoming 30 days', []);
      return;
    }
    const days = state.view === 'week' ? 7 : new Date(start.getFullYear(), start.getMonth() + 1, 0).getDate();
    grid.innerHTML = Array.from({ length: days }, (_, idx) => {
      const day = addDays(start, idx);
      const items = events.filter(event => startOfDay(new Date(event.start)).getTime() === day.getTime());
      return renderPeriod(formatDate(day, { weekday: 'short', month: 'short', day: 'numeric' }), items);
    }).join('');
  }

  document.querySelectorAll('[data-calendar-view]').forEach(button => button.addEventListener('click', () => {
    state.view = button.dataset.calendarView;
    document.querySelectorAll('[data-calendar-view]').forEach(btn => { btn.classList.toggle('button--primary', btn === button); btn.classList.toggle('button--ghost', btn !== button); });
    loadEvents();
  }));
  root.querySelector('[data-calendar-prev]').addEventListener('click', () => { state.anchor = state.view === 'month' ? addMonths(state.anchor, -1) : addDays(state.anchor, state.view === 'week' ? -7 : state.view === 'list' ? -30 : -1); loadEvents(); });
  root.querySelector('[data-calendar-next]').addEventListener('click', () => { state.anchor = state.view === 'month' ? addMonths(state.anchor, 1) : addDays(state.anchor, state.view === 'week' ? 7 : state.view === 'list' ? 30 : 1); loadEvents(); });
  root.querySelector('[data-calendar-today]').addEventListener('click', () => { state.anchor = new Date(); loadEvents(); });
  searchInput.addEventListener('input', () => { state.search = searchInput.value || ''; render(); });
  inactiveInput.addEventListener('change', () => { state.includeInactive = inactiveInput.checked; loadEvents(); });
  loadEvents();
}());
