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
    const max = Math.max(1, ...series.flatMap(item => item.values.filter(Number.isFinite)));
    const group = 90 / labels.length;
    let marks = '';
    labels.forEach((label, index) => series.forEach((item, seriesIndex) => {
      const value = Number(item.values[index]) || 0;
      const x = 6 + index * group + seriesIndex * (group / series.length);
      const width = Math.max(2, group / series.length - 2);
      const height = value / max * 65;
      marks += `<rect x="${x}" y="${78 - height}" width="${width}" height="${height}" rx="1" fill="hsl(${215 + seriesIndex * 55} 75% 55%)"><title>${esc(label)} · ${esc(item.name)}: ${value}</title></rect>`;
    }));
    return `<svg class="dashboard-chart" viewBox="0 0 100 85" preserveAspectRatio="none" role="img" aria-label="${esc(panel.title)} graph">${marks}</svg>`;
  }

  function countColour(panel) {
    if (panel.type !== 'stat' || panel.function !== 'count' || !Number.isFinite(Number(panel.value))) return '';
    const value = Number(panel.value);
    const target = Number(panel.compare_value) || 0;
    return value < target ? panel.less_colour : value > target ? panel.greater_colour : panel.equal_colour;
  }

  function render() {
    grid.innerHTML = '';
    state.panels.forEach(panel => {
      const element = document.createElement('article');
      element.className = 'dashboard-panel';
      element.dataset.id = panel.id;
      element.draggable = editable;
      element.style.setProperty('--panel-w', panel.w);
      element.style.setProperty('--panel-h', panel.h);
      const background = countColour(panel);
      if (background) element.style.setProperty('--panel-background', background);

      let body = '';
      if (panel.error) body = `<p class="error">${esc(panel.error)}</p>`;
      else if (panel.type === 'link') body = `<a class="dashboard-panel__link" href="${esc(panel.url)}"><span class="button">${esc(panel.label)}</span></a>`;
      else if (panel.type === 'graph') body = chart(panel);
      else if (Array.isArray(panel.value)) body = `<ul class="dashboard-panel__list">${panel.value.map(value => `<li>${esc(value)}</li>`).join('')}</ul>`;
      else body = `<div class="dashboard-panel__value">${esc(panel.value)}</div>`;

      const controls = editable ? '<div class="dashboard-panel__controls"><button class="dashboard-panel__edit" type="button" aria-label="Edit panel">Edit</button><button class="dashboard-panel__remove" type="button" aria-label="Remove panel">×</button></div>' : '';
      element.innerHTML = `<div class="dashboard-panel__top"><h2>${esc(panel.title)}</h2>${controls}</div>${body}`;
      element.querySelector('.dashboard-panel__edit')?.addEventListener('click', () => openBuilder(panel));
      element.querySelector('.dashboard-panel__remove')?.addEventListener('click', () => {
        state.panels = state.panels.filter(item => item.id !== panel.id);
        render();
      });
      element.addEventListener('dragstart', event => {
        if (event.target.closest('button')) return event.preventDefault();
        dragged = panel;
        element.classList.add('is-dragging');
      });
      element.addEventListener('dragend', () => element.classList.remove('is-dragging'));
      element.addEventListener('dragover', event => event.preventDefault());
      element.addEventListener('drop', event => {
        event.preventDefault();
        if (!dragged || dragged === panel) return;
        const from = state.panels.indexOf(dragged);
        const to = state.panels.indexOf(panel);
        state.panels.splice(to, 0, state.panels.splice(from, 1)[0]);
        render();
      });
      grid.append(element);
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
      render();
      if (editable) catalog = await api('/api/dashboard/catalog');
    } catch (error) {
      grid.innerHTML = `<div class="dashboard-loading">${esc(error.message)}</div>`;
    }
  }

  app.querySelector('[data-dashboard-save]')?.addEventListener('click', async () => {
    await api('/api/dashboard', {method: 'PUT', body: JSON.stringify(state)});
    source.textContent = `${state.title} · personal layout saved`;
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
      render();
    } catch (error) {
      alert(`Invalid dashboard JSON: ${error.message}`);
    }
  });
  app.querySelector('[data-dashboard-add]')?.addEventListener('click', () => openBuilder());
  builderForm?.elements.type.addEventListener('change', updateBuilderVisibility);
  builderForm?.elements.function.addEventListener('change', updateBuilderVisibility);
  dialog?.querySelector('[data-panel-confirm]').addEventListener('click', event => {
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
    editingId = null;
    dialog.close();
    render();
  });
  load();
})();
