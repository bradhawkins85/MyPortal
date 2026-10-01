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
  const escapeHtml = (value) => { const node = document.createElement('span'); node.textContent = value == null ? '' : String(value); return node.innerHTML; };
  function render() {
    const query = filter.value.trim().toLowerCase();
    const visible = entries.filter((entry) => [entry.subject, entry.company, ...(entry.ai_tags || [])].join(' ').toLowerCase().includes(query));
    rows.innerHTML = visible.map((entry) => {
      const tags = (entry.ai_tags || []).map((tag) => `<span class="tag tag--muted">${escapeHtml(tag)}</span>`).join(' ') || '<span class="text-muted">—</span>';
      const action = entry.ignored ? `<button class="button button--ghost button--small" data-review-action="restore" data-ticket-id="${entry.ticket_id}">Restore</button>` : `<button class="button button--ghost button--small" data-review-action="ignore" data-ticket-id="${entry.ticket_id}">Ignore</button> <button class="button button--primary button--small" data-review-action="generate" data-ticket-id="${entry.ticket_id}">Generate KB Article</button>`;
      return `<tr class="${entry.ignored ? 'resolution-review__row--ignored' : ''}"><td data-label="Ticket subject">${escapeHtml(entry.subject || 'Untitled ticket')}</td><td data-label="AI tags">${tags}</td><td data-label="Company">${escapeHtml(entry.company || '—')}</td><td data-label="Available actions"><div class="resolution-review__actions">${entry.article_id ? '<span class="status status--success">Article generated</span>' : action}</div></td></tr>`;
    }).join('');
    state.hidden = visible.length > 0; state.textContent = query ? 'No entries match this filter.' : 'No Resolution Step entries are available.'; table.hidden = visible.length === 0;
  }
  async function load() {
    state.hidden = false; table.hidden = true; state.textContent = 'Loading Resolution Step entries…';
    try { const response = await fetch(`/api/knowledge-base/resolution-steps?include_ignored=${showIgnored.checked}`); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || 'Unable to load Resolution Step entries.'); entries = payload; render(); }
    catch (error) { state.textContent = error.message || 'Unable to load Resolution Step entries.'; notice.textContent = ''; }
  }
  async function act(button) {
    const action = button.dataset.reviewAction; const ticketId = button.dataset.ticketId; button.disabled = true; notice.className = 'resolution-review__notice'; notice.textContent = 'Saving…';
    try { const response = await fetch(action === 'generate' ? `/api/knowledge-base/resolution-steps/${ticketId}/generate` : `/api/knowledge-base/resolution-steps/${ticketId}`, {method: action === 'generate' ? 'POST' : 'PATCH', headers: action === 'generate' ? {} : {'Content-Type': 'application/json'}, body: action === 'generate' ? null : JSON.stringify({ignored: action === 'ignore'})}); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || 'The action could not be completed.'); notice.classList.add('is-success'); notice.textContent = payload.message; await load(); }
    catch (error) { notice.classList.add('is-error'); notice.textContent = error.message || 'The action could not be completed.'; button.disabled = false; }
  }
  openButton.addEventListener('click', () => { showIgnored.checked = false; filter.value = ''; notice.textContent = ''; dialog.showModal(); load(); });
  dialog.querySelector('[data-resolution-steps-close]').addEventListener('click', () => dialog.close()); showIgnored.addEventListener('change', load); filter.addEventListener('input', render);
  rows.addEventListener('click', (event) => { const button = event.target.closest('[data-review-action]'); if (button) act(button); }); dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
})();
