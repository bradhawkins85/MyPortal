(function () {
  'use strict';

  const root = document.querySelector('[data-documentation-search]');
  if (!root) return;

  const form = root.querySelector('[data-documentation-search-form]');
  const query = form.elements.q;
  const company = root.querySelector('[data-documentation-company]');
  const status = root.querySelector('[data-documentation-status]');
  const results = root.querySelector('[data-documentation-results]');
  const heading = root.querySelector('[data-documentation-heading]');
  const list = root.querySelector('[data-documentation-list]');
  const pagination = root.querySelector('[data-documentation-pagination]');
  const pageLabel = root.querySelector('[data-documentation-page]');
  const previous = root.querySelector('[data-documentation-previous]');
  const next = root.querySelector('[data-documentation-next]');
  let currentPage = 1;
  let pages = 0;
  let controller = null;

  function setStatus(message, kind) {
    status.textContent = message;
    status.className = 'documentation-search__status' + (kind ? ' alert alert--' + kind : '');
  }

  function safeRecordUrl(item) {
    const value = String(item.url || '');
    if (item.source === 'assets' && /^\/assets\/[^/]+$/.test(value)) return value;
    if (item.source === 'knowledge_base' && /^\/knowledge-base\/articles\/[^/]+$/.test(value)) return value;
    return null;
  }

  function renderItem(item) {
    const url = safeRecordUrl(item);
    if (!url) return null;
    const row = document.createElement('li');
    row.className = 'documentation-search__result';
    const title = document.createElement('a');
    title.className = 'documentation-search__result-title';
    title.href = url;
    title.textContent = String(item.title || 'Untitled record');
    const meta = document.createElement('div');
    meta.className = 'documentation-search__result-meta';
    meta.textContent = item.source === 'assets' ? 'Asset' : (item.metadata && item.metadata.linked_runbook ? 'Linked runbook' : 'Knowledge base');
    row.append(title, meta);
    if (item.snippet) {
      const snippet = document.createElement('p');
      snippet.textContent = String(item.snippet);
      row.appendChild(snippet);
    }
    return row;
  }

  function parameters(page) {
    const values = new FormData(form);
    const params = new URLSearchParams();
    params.set('q', String(values.get('q') || '').trim());
    params.set('page', String(page));
    params.set('page_size', '20');
    const source = values.get('record_type');
    if (source) params.append('source', source);
    ['status', 'owner', 'asset_type'].forEach(function (name) {
      const value = String(values.get(name) || '').trim();
      if (value) params.set(name, value);
    });
    if (company && company.value) params.set('company_id', company.value);
    return params;
  }

  async function search(page) {
    const params = parameters(page);
    if (!params.get('q')) { query.focus(); return; }
    if (controller) controller.abort();
    controller = new AbortController();
    setStatus('Searching accessible documentation…', 'info');
    results.hidden = true;
    const browserUrl = new URL(window.location.href);
    browserUrl.search = params.toString();
    window.history.replaceState({}, '', browserUrl);
    try {
      const response = await fetch('/api/documentation/search?' + params.toString(), {
        headers: { Accept: 'application/json' }, signal: controller.signal
      });
      if (!response.ok) throw new Error('Search request failed');
      const payload = await response.json();
      list.replaceChildren();
      (Array.isArray(payload.results) ? payload.results : []).forEach(function (item) {
        const row = renderItem(item);
        if (row) list.appendChild(row);
      });
      currentPage = Number(payload.page) || page;
      pages = Number(payload.pages) || 0;
      if (!list.children.length) {
        setStatus('No accessible documentation matched your search. Try different words or filters.', 'info');
        results.hidden = true;
        return;
      }
      setStatus('');
      heading.textContent = Number(payload.total) === 1 ? '1 accessible result' : String(payload.total) + ' accessible results';
      pageLabel.textContent = 'Page ' + currentPage + ' of ' + pages;
      previous.disabled = currentPage <= 1;
      next.disabled = currentPage >= pages;
      pagination.hidden = pages <= 1;
      results.hidden = false;
    } catch (error) {
      if (error.name === 'AbortError') return;
      list.replaceChildren();
      results.hidden = true;
      setStatus('Documentation search is unavailable right now. Please try again.', 'danger');
    }
  }

  form.addEventListener('submit', function (event) { event.preventDefault(); search(1); });
  previous.addEventListener('click', function () { if (currentPage > 1) search(currentPage - 1); });
  next.addEventListener('click', function () { if (currentPage < pages) search(currentPage + 1); });
  root.querySelector('[data-documentation-clear]').addEventListener('click', function () {
    form.elements.record_type.value = '';
    form.elements.status.value = '';
    form.elements.owner.value = '';
    form.elements.asset_type.value = '';
    if (query.value.trim()) search(1);
  });
  if (company) company.addEventListener('change', function () {
    const switcher = document.querySelector('[data-company-switcher]');
    const globalCompany = switcher && switcher.querySelector('select[name="companyId"]');
    if (switcher && globalCompany) {
      globalCompany.value = company.value;
      switcher.querySelector('input[name="returnUrl"]').value = window.location.pathname + window.location.search;
      switcher.requestSubmit ? switcher.requestSubmit() : switcher.submit();
    }
  });

  const initial = new URLSearchParams(window.location.search);
  ['q', 'record_type', 'status', 'owner', 'asset_type'].forEach(function (name) {
    if (initial.has(name) && form.elements[name]) form.elements[name].value = initial.get(name);
  });
  if (initial.get('q')) search(Math.max(1, Number(initial.get('page')) || 1));
}());
