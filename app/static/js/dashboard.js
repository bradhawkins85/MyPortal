(() => {
  const app = document.querySelector('[data-dashboard-app]');
  if (!app) return;

  const grid = app.querySelector('[data-dashboard-grid]');
  const source = app.querySelector('[data-dashboard-source]');
  const toolbar = app.querySelector('[data-dashboard-toolbar]');
  const dialog = document.querySelector('[data-dashboard-builder]');
  const builderForm = dialog?.querySelector('form');
  const file = document.querySelector('[data-dashboard-file]');
  let state;
  let catalog;
  let editable = false;
  let canAssign = false;
  let dragged;
  let editingId = null;
  let dirty = false;
  const saveButton = app.querySelector('[data-dashboard-save]');

  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
  const api = async (url, options = {}) => {
    const response = await fetch(url, {headers: {'Content-Type': 'application/json'}, ...options});
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'Request failed');
    return response.json();
  };

  function chart(panel) {
    const data = panel.chart_data || {};
    const labels = data.labels || [];
    const series = data.series || [];
    if (!labels.length || !series.length) return '<p>No numeric graph data.</p>';
    const values = series.flatMap(item => item.values.map(Number).filter(Number.isFinite));
    const max = Math.max(1, ...values);
    const colours = series.map((_, index) => `hsl(${215 + index * 55} 75% 55%)`);
    const plot = {left: 12, right: 96, top: 8, bottom: 72};
    const xAt = index => plot.left + (labels.length === 1 ? (plot.right - plot.left) / 2 : index * (plot.right - plot.left) / (labels.length - 1));
    const yAt = value => plot.bottom - Number(value || 0) / max * (plot.bottom - plot.top);
    let marks = '';
    for (let tick = 0; tick <= 4; tick += 1) {
      const y = plot.bottom - tick * (plot.bottom - plot.top) / 4;
      marks += `<line class="dashboard-chart__grid" x1="${plot.left}" x2="${plot.right}" y1="${y}" y2="${y}"/><text x="10" y="${y + 2}" text-anchor="end">${esc((max * tick / 4).toLocaleString(undefined, {maximumFractionDigits: 1}))}</text>`;
    }
    labels.forEach((label, index) => { marks += `<text x="${xAt(index)}" y="80" text-anchor="middle">${esc(label)}</text>`; });
    if (panel.chart === 'bar') {
      const group = (plot.right - plot.left) / labels.length;
      labels.forEach((label, index) => series.forEach((item, seriesIndex) => {
        const value = Number(item.values[index]) || 0;
        const width = Math.max(1.5, group / series.length - 1.5);
        const x = plot.left + index * group + seriesIndex * group / series.length + .75;
        marks += `<rect x="${x}" y="${yAt(value)}" width="${width}" height="${plot.bottom - yAt(value)}" rx="1" fill="${colours[seriesIndex]}"><title>${esc(label)} · ${esc(item.name)}: ${value}</title></rect>`;
      }));
    } else if (panel.chart === 'doughnut') {
      const totals = series.map(item => item.values.reduce((sum, value) => sum + (Number(value) || 0), 0));
      const total = totals.reduce((sum, value) => sum + value, 0) || 1;
      let offset = 0;
      totals.forEach((value, index) => {
        const length = value / total * 100;
        marks += `<circle class="dashboard-chart__doughnut" cx="54" cy="40" r="24" pathLength="100" stroke="${colours[index]}" stroke-dasharray="${length} ${100 - length}" stroke-dashoffset="${-offset}"><title>${esc(series[index].name)}: ${value}</title></circle>`;
        offset += length;
      });
    } else {
      series.forEach((item, seriesIndex) => {
        const points = item.values.map((value, index) => `${xAt(index)},${yAt(value)}`).join(' ');
        if (panel.chart === 'area') marks += `<polygon points="${xAt(0)},${plot.bottom} ${points} ${xAt(labels.length - 1)},${plot.bottom}" fill="${colours[seriesIndex]}" opacity=".22"/>`;
        marks += `<polyline points="${points}" fill="none" stroke="${colours[seriesIndex]}" stroke-width="1.8" vector-effect="non-scaling-stroke"/>`;
        item.values.forEach((value, index) => { marks += `<circle cx="${xAt(index)}" cy="${yAt(value)}" r="1.5" fill="${colours[seriesIndex]}"><title>${esc(labels[index])} · ${esc(item.name)}: ${Number(value) || 0}</title></circle>`; });
      });
    }
    const legend = series.map((item, index) => `<span><i style="--legend-colour:${colours[index]}"></i>${esc(item.name)}</span>`).join('');
    return `<div class="dashboard-chart-wrap"><svg class="dashboard-chart" viewBox="0 0 100 85" role="img" aria-label="${esc(panel.title)} ${esc(panel.chart)} graph">${marks}</svg><div class="dashboard-chart__legend" aria-label="Graph legend">${legend}</div></div>`;
  }

  function setDirty(value = true) {
    dirty = value;
    if (saveButton) saveButton.disabled = !editable || !dirty;
  }

  const automaticHeights = new Map();
  const panelHeight = panel => panel.h === 0 ? (automaticHeights.get(panel.id) || 1) : panel.h;
  const overlaps = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + panelHeight(b) && a.y + panelHeight(a) > b.y;
  function makeRoom(moved) {
    const queue = [moved];
    while (queue.length) {
      const active = queue.shift();
      state.panels.filter(panel => panel !== active && overlaps(active, panel)).forEach(panel => {
        panel.y = Math.min(500, active.y + panelHeight(active));
        queue.push(panel);
      });
    }
  }

  function gridPosition(event, panel) {
    const bounds = grid.getBoundingClientRect();
    const style = getComputedStyle(grid);
    const gap = parseFloat(style.columnGap) || 0;
    const column = (bounds.width - gap * 11) / 12;
    const row = parseFloat(style.gridAutoRows) || 72;
    return {
      x: Math.max(0, Math.min(12 - panel.w, Math.floor((event.clientX - bounds.left) / (column + gap)))),
      y: Math.max(0, Math.min(500, Math.floor((event.clientY - bounds.top) / (row + gap))))
    };
  }

  function countColour(panel) {
    if (panel.type !== 'stat' || panel.function !== 'count' || !Number.isFinite(Number(panel.value))) return '';
    const value = Number(panel.value);
    const target = Number(panel.compare_value) || 0;
    return value < target ? panel.less_colour : value > target ? panel.greater_colour : panel.equal_colour;
  }

  function table(panel) {
    const data = panel.table_data || {};
    const columns = data.columns || [];
    const rows = data.rows || [];
    if (!columns.length) return '<p>No tabular data.</p>';
    const head = columns.map(column => `<th scope="col">${esc(column)}</th>`).join('');
    const body = rows.map(row => `<tr>${columns.map((_, index) => `<td>${esc(row[index])}</td>`).join('')}</tr>`).join('');
    return `<div class="dashboard-panel__table-wrap"><table class="dashboard-panel__table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function render() {
    grid.innerHTML = '';
    state.panels.forEach(panel => {
      const element = document.createElement('article');
      element.className = 'dashboard-panel';
      element.dataset.id = panel.id;
      element.draggable = editable;
      element.style.setProperty('--panel-w', panel.w);
      element.classList.toggle('dashboard-panel--auto-height', panel.h === 0);
      element.style.setProperty('--panel-h', panelHeight(panel));
      element.style.gridColumn = `${panel.x + 1} / span ${panel.w}`;
      element.style.gridRow = `${panel.y + 1} / span ${panelHeight(panel)}`;
      const background = countColour(panel);
      if (background) element.style.setProperty('--panel-background', background);

      let body = '';
      if (panel.error) body = `<p class="error">${esc(panel.error)}</p>`;
      else if (panel.type === 'link') body = `<a class="dashboard-panel__link" href="${esc(panel.url)}"><span class="button">${esc(panel.label)}</span></a>`;
      else if (panel.type === 'graph') body = chart(panel);
      else if (panel.table_data) body = table(panel);
      else if (Array.isArray(panel.value)) body = `<ul class="dashboard-panel__list">${panel.value.map(value => `<li>${esc(value)}</li>`).join('')}</ul>`;
      else body = `<div class="dashboard-panel__value">${esc(panel.value)}</div>`;

      const controls = editable ? '<div class="dashboard-panel__controls"><button class="button button--secondary dashboard-panel__edit" type="button" aria-label="Edit panel">Edit</button><button class="dashboard-panel__remove" type="button" aria-label="Remove panel">×</button></div>' : '';
      element.innerHTML = `<div class="dashboard-panel__top"><h2>${esc(panel.title)}</h2>${controls}</div>${body}`;
      element.querySelector('.dashboard-panel__edit')?.addEventListener('click', () => openBuilder(panel));
      element.querySelector('.dashboard-panel__remove')?.addEventListener('click', () => {
        state.panels = state.panels.filter(item => item.id !== panel.id);
        setDirty();
        render();
      });
      element.addEventListener('dragstart', event => {
        if (event.target.closest('button')) return event.preventDefault();
        dragged = panel;
        element.classList.add('is-dragging');
      });
      element.addEventListener('dragend', () => { element.classList.remove('is-dragging'); dragged = null; });
      grid.append(element);
    });
    requestAnimationFrame(resizeAutomaticPanels);
  }

  function resizeAutomaticPanels() {
    const style = getComputedStyle(grid);
    const row = parseFloat(style.gridAutoRows) || 72;
    const gap = parseFloat(style.rowGap) || 0;
    let changed = false;
    state.panels.filter(panel => panel.h === 0).forEach(panel => {
      const element = grid.querySelector(`[data-id="${CSS.escape(panel.id)}"]`);
      if (!element) return;
      const height = Math.max(1, Math.min(6, Math.ceil((element.scrollHeight + gap) / (row + gap))));
      if (automaticHeights.get(panel.id) !== height) { automaticHeights.set(panel.id, height); changed = true; }
    });
    if (!changed) return;
    state.panels.filter(panel => panel.h === 0).forEach(makeRoom);
    state.panels.forEach(panel => {
      const element = grid.querySelector(`[data-id="${CSS.escape(panel.id)}"]`);
      if (element) element.style.gridRow = `${panel.y + 1} / span ${panelHeight(panel)}`;
    });
  }

  function updateBuilderVisibility() {
    const type = builderForm.elements.type.value;
    const func = builderForm.elements.function.value;
    dialog.querySelector('[data-panel-report]').hidden = !['stat', 'graph'].includes(type);
    dialog.querySelector('[data-panel-function]').hidden = type !== 'stat';
    dialog.querySelector('[data-panel-count-colours]').hidden = type !== 'stat' || func !== 'count';
    dialog.querySelector('[data-panel-chart]').hidden = type !== 'graph';
    dialog.querySelector('[data-panel-variable]').hidden = type !== 'variable';
    dialog.querySelector('[data-panel-link]').hidden = type !== 'link';
  }

  function openBuilder(panel = null) {
    const reports = builderForm.elements.report;
    const variables = builderForm.elements.variable;
    reports.innerHTML = (catalog?.reports || []).map(report => `<option value="${esc(report.slug)}">${esc(report.name)}</option>`).join('');
    variables.innerHTML = (catalog?.variables || []).map(variable => `<option value="${esc(variable.name)}">${esc(variable.name)}</option>`).join('');
    builderForm.reset();
    editingId = panel?.id || null;
    dialog.querySelector('[data-panel-dialog-title]').textContent = panel ? 'Edit panel' : 'Add a panel';
    dialog.querySelector('[data-panel-confirm]').textContent = panel ? 'Update panel' : 'Add panel';
    const defaults = panel || {type: 'stat', title: 'New panel', w: 4, h: 2};
    for (const [name, value] of Object.entries(defaults)) {
      if (builderForm.elements[name] && value !== undefined) builderForm.elements[name].value = value;
    }
    updateBuilderVisibility();
    dialog.showModal();
  }

  async function load() {
    try {
      const data = await api('/api/dashboard');
      state = data.layout;
      editable = data.editable;
      canAssign = data.can_assign_company;
      toolbar.hidden = !editable;
      if (canAssign && !toolbar.querySelector('[data-dashboard-assign]')) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'button button--secondary';
        button.dataset.dashboardAssign = '';
        button.textContent = 'Assign to company';
        button.addEventListener('click', async () => {
          const id = prompt('Company ID to receive this layout');
          if (id) await api(`/api/dashboard/companies/${encodeURIComponent(id)}`, {method: 'PUT', body: JSON.stringify(state)});
        });
        toolbar.prepend(button);
      }
      source.textContent = `${state.title} · ${data.source} layout`;
      // Populate the builder before exposing edit controls for existing panels.
      if (editable) catalog = await api('/api/dashboard/catalog');
      render();
      setDirty(false);
    } catch (error) {
      grid.innerHTML = `<div class="dashboard-loading">${esc(error.message)}</div>`;
    }
  }

  async function resolveState() {
    const data = await api('/api/dashboard/resolve', {method: 'POST', body: JSON.stringify(state)});
    state = data.layout;
  }

  app.querySelector('[data-dashboard-save]')?.addEventListener('click', async () => {
    await api('/api/dashboard', {method: 'PUT', body: JSON.stringify(state)});
    source.textContent = `${state.title} · personal layout saved`;
    setDirty(false);
  });
  app.querySelector('[data-dashboard-export]')?.addEventListener('click', () => {
    const anchor = document.createElement('a');
    anchor.href = URL.createObjectURL(new Blob([JSON.stringify(state, null, 2)], {type: 'application/json'}));
    anchor.download = 'myportal-dashboard.json';
    anchor.click();
    URL.revokeObjectURL(anchor.href);
  });
  app.querySelector('[data-dashboard-import]')?.addEventListener('click', () => file.click());
  file?.addEventListener('change', async () => {
    try {
      state = JSON.parse(await file.files[0].text());
      await resolveState();
      setDirty();
      render();
    } catch (error) {
      alert(`Invalid dashboard JSON: ${error.message}`);
    }
  });
  app.querySelector('[data-dashboard-add]')?.addEventListener('click', () => openBuilder());
  builderForm?.elements.type.addEventListener('change', updateBuilderVisibility);
  builderForm?.elements.function.addEventListener('change', updateBuilderVisibility);
  dialog?.querySelector('[data-panel-confirm]').addEventListener('click', async event => {
    event.preventDefault();
    if (!builderForm.reportValidity()) return;
    const form = new FormData(builderForm);
    const type = form.get('type');
    const previous = state.panels.find(panel => panel.id === editingId);
    const panel = {
      id: previous?.id || `panel-${Date.now()}`,
      type,
      title: form.get('title'),
      x: previous?.x || 0,
      y: previous?.y ?? state.panels.length,
      w: Number(form.get('w')),
      h: Number(form.get('h'))
    };
    if (type === 'link') Object.assign(panel, {label: form.get('label'), url: form.get('url')});
    if (type === 'variable') panel.variable = form.get('variable');
    if (type === 'stat') Object.assign(panel, {
      report: form.get('report'), function: form.get('function'),
      compare_value: Number(form.get('compare_value')), less_colour: form.get('less_colour'),
      equal_colour: form.get('equal_colour'), greater_colour: form.get('greater_colour')
    });
    if (type === 'graph') Object.assign(panel, {report: form.get('report'), chart: form.get('chart')});
    if (previous) state.panels[state.panels.indexOf(previous)] = panel;
    else state.panels.push(panel);
    makeRoom(panel);
    try {
      await resolveState();
      editingId = null;
      dialog.close();
      setDirty();
      render();
    } catch (error) {
      alert(`Unable to load panel data: ${error.message}`);
    }
  });
  grid.addEventListener('dragover', event => { if (editable && dragged) event.preventDefault(); });
  grid.addEventListener('drop', event => {
    if (!editable || !dragged) return;
    event.preventDefault();
    Object.assign(dragged, gridPosition(event, dragged));
    makeRoom(dragged);
    dragged = null;
    setDirty();
    render();
  });
  load();
})();
