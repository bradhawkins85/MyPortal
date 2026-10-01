(() => {
  const dialog = document.querySelector('[data-resolution-steps-dialog]');
  const openButton = document.querySelector('[data-resolution-steps-open]');
  if (!dialog || !openButton) return;
  const rows = dialog.querySelector('[data-resolution-rows]');
  const table = dialog.querySelector('[data-resolution-table]');
  const state = dialog.querySelector('[data-resolution-state]');
  const notice = dialog.querySelector('[data-resolution-notice]');
  const showIgnored = dialog.querySelector('[data-resolution-show-ignored]');
  const filter = dialog.querySelector('[data-resolution-filter]');
  let entries = [];
  let recurring = [];
  const escapeHtml = (value) => { const node = document.createElement('span'); node.textContent = value == null ? '' : String(value); return node.innerHTML; };
  function render() {
    const query = filter.value.trim().toLowerCase();
    const matches = (values) => values.join(' ').toLowerCase().includes(query);
    const visibleRecurring = recurring.filter((group) => matches([group.subject, 'recurring issue', ...(group.companies || []), ...(group.ai_tags || [])]));
    const visible = entries.filter((entry) => matches([entry.subject, entry.company, ...(entry.ai_tags || [])]));
    const tagList = (tags) => (tags || []).map((tag) => `<span class="tag tag--muted">${escapeHtml(tag)}</span>`).join(' ') || '<span class="text-muted">—</span>';
    const recurringRows = visibleRecurring.map((group) => {
      const action = group.ignored ? `<button class="button button--ghost button--small" data-review-action="restore" data-entry-type="recurring" data-ticket-id="${group.group_id}">Restore</button>` : `<button class="button button--ghost button--small" data-review-action="ignore" data-entry-type="recurring" data-ticket-id="${group.group_id}">Ignore</button> <button class="button button--primary button--small" data-review-action="generate" data-entry-type="recurring" data-ticket-id="${group.group_id}">Generate consolidated article</button>`;
      const companies = (group.companies || []).length ? escapeHtml(group.companies.join(', ')) : '—';
      return `<tr class="resolution-review__row--recurring ${group.ignored ? 'resolution-review__row--ignored' : ''}"><td data-label="Ticket subject"><span class="tag tag--info">Recurring issue</span> ${escapeHtml(group.subject || 'Recurring issue')}<div class="text-muted">${group.ticket_count} linked tickets: ${group.ticket_ids.map((id) => `#${Number(id)}`).join(', ')}</div></td><td data-label="AI tags">${tagList(group.ai_tags)}</td><td data-label="Company">${companies}</td><td data-label="Available actions"><div class="resolution-review__actions">${group.article_id ? '<span class="status status--success">Article generated</span>' : action}</div></td></tr>`;
    }).join('');
    rows.innerHTML = recurringRows + visible.map((entry) => {
      const tags = (entry.ai_tags || []).map((tag) => `<span class="tag tag--muted">${escapeHtml(tag)}</span>`).join(' ') || '<span class="text-muted">—</span>';
      const action = entry.ignored ? `<button class="button button--ghost button--small" data-review-action="restore" data-ticket-id="${entry.ticket_id}">Restore</button>` : `<button class="button button--ghost button--small" data-review-action="ignore" data-ticket-id="${entry.ticket_id}">Ignore</button> <button class="button button--primary button--small" data-review-action="generate" data-ticket-id="${entry.ticket_id}">Generate KB Article</button>`;
      return `<tr class="${entry.ignored ? 'resolution-review__row--ignored' : ''}"><td data-label="Ticket subject">${escapeHtml(entry.subject || 'Untitled ticket')}</td><td data-label="AI tags">${tags}</td><td data-label="Company">${escapeHtml(entry.company || '—')}</td><td data-label="Available actions"><div class="resolution-review__actions">${entry.article_id ? '<span class="status status--success">Article generated</span>' : action}</div></td></tr>`;
    }).join('');
    const total = visible.length + visibleRecurring.length;
    state.hidden = total > 0; state.textContent = query ? 'No entries match this filter.' : 'No Resolution Step entries are available.'; table.hidden = total === 0;
  }
  async function load() {
    state.hidden = false; table.hidden = true; state.textContent = 'Loading Resolution Step entries…';
    try { const response = await fetch(`/api/knowledge-base/resolution-steps?include_ignored=${showIgnored.checked}`); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || 'Unable to load Resolution Step entries.'); entries = payload; recurring = await loadRecurring(); render(); }
    catch (error) { state.textContent = error.message || 'Unable to load Resolution Step entries.'; notice.textContent = ''; }
  }
  async function loadRecurring() {
    // Recurring groups are an extra; a failure here should not hide single-ticket entries.
    try { const response = await fetch(`/api/knowledge-base/resolution-steps/recurring?include_ignored=${showIgnored.checked}`); if (!response.ok) return []; const payload = await response.json(); return Array.isArray(payload) ? payload : []; }
    catch (error) { return []; }
  }
  async function act(button) {
    const action = button.dataset.reviewAction; const ticketId = button.dataset.ticketId; const base = button.dataset.entryType === 'recurring' ? '/api/knowledge-base/resolution-steps/recurring' : '/api/knowledge-base/resolution-steps'; button.disabled = true; notice.className = 'resolution-review__notice'; notice.textContent = 'Saving…';
    try { const response = await fetch(action === 'generate' ? `${base}/${ticketId}/generate` : `${base}/${ticketId}`, {method: action === 'generate' ? 'POST' : 'PATCH', headers: action === 'generate' ? {} : {'Content-Type': 'application/json'}, body: action === 'generate' ? null : JSON.stringify({ignored: action === 'ignore'})}); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || 'The action could not be completed.'); notice.classList.add('is-success'); notice.textContent = payload.message; await load(); }
    catch (error) { notice.classList.add('is-error'); notice.textContent = error.message || 'The action could not be completed.'; button.disabled = false; }
  }
  openButton.addEventListener('click', () => { showIgnored.checked = false; filter.value = ''; notice.textContent = ''; dialog.showModal(); load(); });
  dialog.querySelector('[data-resolution-steps-close]').addEventListener('click', () => dialog.close()); showIgnored.addEventListener('change', load); filter.addEventListener('input', render);
  rows.addEventListener('click', (event) => { const button = event.target.closest('[data-review-action]'); if (button) act(button); }); dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
})();
