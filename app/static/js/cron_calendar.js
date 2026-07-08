(function () {
  const root = document.querySelector('[data-cron-calendar]');
  if (!root) return;

  const DISPLAY_MINUTES = 1;
  const DAY_START_HOUR = 0;
  const DAY_END_HOUR = 24;
  const HOUR_HEIGHT = 96;
  const state = { view: 'week', anchor: new Date(), events: [], search: '', includeInactive: false };
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
  function formatHour(hour) { const d = new Date(); d.setHours(hour, 0, 0, 0); return new Intl.DateTimeFormat(undefined, { hour: 'numeric' }).format(d).replace(' ', ''); }
  function minutesSinceStart(date) { return (date.getHours() - DAY_START_HOUR) * 60 + date.getMinutes(); }
  function isSameDay(left, right) { return startOfDay(left).getTime() === startOfDay(right).getTime(); }

  function layoutTimelineEvents(events) {
    const sorted = [...events].sort((left, right) => new Date(left.start) - new Date(right.start));
    const active = [];
    return sorted.map(event => {
      const startsAt = new Date(event.start);
      const endsAt = new Date(startsAt.getTime() + DISPLAY_MINUTES * 60000);
      for (let idx = active.length - 1; idx >= 0; idx -= 1) {
        if (active[idx].end <= startsAt) active.splice(idx, 1);
      }
      const usedColumns = new Set(active.map(item => item.column));
      let column = 0;
      while (usedColumns.has(column)) column += 1;
      const entry = { event, startsAt, endsAt, end: endsAt, column, clusterSize: column + 1 };
      active.push(entry);
      const clusterSize = Math.max(...active.map(item => item.column), column) + 1;
      active.forEach(item => { item.clusterSize = Math.max(item.clusterSize, clusterSize); });
      return entry;
    });
  }

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

  function updateRangeLabel() {
    const { start, end } = rangeForView();
    if (state.view === 'day') rangeLabel.textContent = formatDate(start, { dateStyle: 'medium' });
    else rangeLabel.textContent = `${formatDate(start, { month: 'short', day: 'numeric' })} - ${formatDate(addDays(end, -1), { month: 'short', day: 'numeric', year: 'numeric' })}`;
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
    const endsAt = new Date(startsAt.getTime() + DISPLAY_MINUTES * 60000);
    return `<a class="cron-calendar__event" href="${escapeHtml(event.url)}">
      <strong>${escapeHtml(event.title)}</strong>
      <span class="cron-calendar__event-meta"><span>${formatTime(startsAt)} - ${formatTime(endsAt)}</span><span>${escapeHtml(event.companyName)}</span><code>${escapeHtml(event.cron)}</code><span>${escapeHtml(event.command)}</span>${event.active ? '' : '<span>Inactive</span>'}</span>
    </a>`;
  }

  function renderPeriod(label, events) {
    return `<section class="cron-calendar__period"><div class="cron-calendar__period-header">${escapeHtml(label)}</div><div class="cron-calendar__events">${events.length ? events.map(eventHtml).join('') : '<p class="cron-calendar__empty">No scheduled runs.</p>'}</div></section>`;
  }

  function renderTimeline(days) {
    const events = filteredEvents();
    const totalHours = DAY_END_HOUR - DAY_START_HOUR;
    const timelineHeight = totalHours * HOUR_HEIGHT;
    const dayList = Array.from({ length: days }, (_, idx) => addDays(days === 7 ? startOfWeek(state.anchor) : startOfDay(state.anchor), idx));
    const now = new Date();
    countLabel.textContent = String(events.length);
    grid.className = `cron-calendar__grid cron-calendar__grid--timeline cron-calendar__grid--${state.view}`;
    grid.innerHTML = `<div class="cron-calendar__timeline" style="--calendar-hour-height:${HOUR_HEIGHT}px;--calendar-timeline-height:${timelineHeight}px">
      <div class="cron-calendar__corner" aria-hidden="true"></div>
      <div class="cron-calendar__day-headings">${dayList.map(day => `<div class="cron-calendar__day-heading${isSameDay(day, now) ? ' is-today' : ''}"><span>${formatDate(day, { weekday: 'short' })}</span><strong>${formatDate(day, { day: '2-digit' })}</strong></div>`).join('')}</div>
      <div class="cron-calendar__time-axis">${Array.from({ length: totalHours }, (_, idx) => `<span>${formatHour(DAY_START_HOUR + idx)}</span>`).join('')}</div>
      <div class="cron-calendar__day-columns">${dayList.map(day => {
        const items = layoutTimelineEvents(events.filter(event => isSameDay(new Date(event.start), day)));
        return `<div class="cron-calendar__day-column${isSameDay(day, now) ? ' is-today' : ''}">${items.map(item => {
          const { event, startsAt, endsAt, column, clusterSize } = item;
          const top = Math.max(0, Math.min(timelineHeight - 36, (minutesSinceStart(startsAt) / 60) * HOUR_HEIGHT));
          const height = Math.max(36, (DISPLAY_MINUTES / 60) * HOUR_HEIGHT);
          const width = `calc(${100 / clusterSize}% - .3rem)`;
          const left = `calc(${(100 / clusterSize) * column}% + .15rem)`;
          return `<a class="cron-calendar__timeline-event" href="${escapeHtml(event.url)}" style="top:${top}px;height:${height}px;left:${left};width:${width}" title="${escapeHtml(event.title)} ${formatTime(startsAt)} - ${formatTime(endsAt)}">
            <strong>${escapeHtml(event.title)}</strong><span>${formatTime(startsAt)} - ${formatTime(endsAt)}</span>
          </a>`;
        }).join('')}</div>`;
      }).join('')}</div>
    </div>`;
  }

  function render() {
    const events = filteredEvents();
    if (state.view === 'day' || state.view === 'week') { renderTimeline(state.view === 'week' ? 7 : 1); return; }
    countLabel.textContent = String(events.length);
    grid.className = `cron-calendar__grid cron-calendar__grid--${state.view}`;
    const { start } = rangeForView();
    if (state.view === 'list') {
      const byDay = new Map();
      events.forEach(event => { const key = formatDate(new Date(event.start), { dateStyle: 'full' }); byDay.set(key, [...(byDay.get(key) || []), event]); });
      grid.innerHTML = byDay.size ? Array.from(byDay.entries()).map(([label, items]) => renderPeriod(label, items)).join('') : renderPeriod('Upcoming 30 days', []);
      return;
    }
    const days = new Date(start.getFullYear(), start.getMonth() + 1, 0).getDate();
    grid.innerHTML = Array.from({ length: days }, (_, idx) => {
      const day = addDays(start, idx);
      const items = events.filter(event => isSameDay(new Date(event.start), day));
      return renderPeriod(formatDate(day, { weekday: 'short', month: 'short', day: 'numeric' }), items);
    }).join('');
  }

  document.querySelectorAll('[data-calendar-view]').forEach(button => {
    const active = button.dataset.calendarView === state.view;
    button.classList.toggle('button--primary', active); button.classList.toggle('button--ghost', !active);
    button.addEventListener('click', () => {
      state.view = button.dataset.calendarView;
      document.querySelectorAll('[data-calendar-view]').forEach(btn => { btn.classList.toggle('button--primary', btn === button); btn.classList.toggle('button--ghost', btn !== button); });
      loadEvents();
    });
  });
  root.querySelector('[data-calendar-prev]').addEventListener('click', () => { state.anchor = state.view === 'month' ? addMonths(state.anchor, -1) : addDays(state.anchor, state.view === 'week' ? -7 : state.view === 'list' ? -30 : -1); loadEvents(); });
  root.querySelector('[data-calendar-next]').addEventListener('click', () => { state.anchor = state.view === 'month' ? addMonths(state.anchor, 1) : addDays(state.anchor, state.view === 'week' ? 7 : state.view === 'list' ? 30 : 1); loadEvents(); });
  root.querySelector('[data-calendar-today]').addEventListener('click', () => { state.anchor = new Date(); loadEvents(); });
  searchInput.addEventListener('input', () => { state.search = searchInput.value || ''; render(); });
  inactiveInput.addEventListener('change', () => { state.includeInactive = inactiveInput.checked; loadEvents(); });
  loadEvents();
}());
