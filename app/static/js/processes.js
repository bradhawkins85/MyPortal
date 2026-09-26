(function () {
  'use strict';
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  async function api(url, method, body) {
    const response = await fetch(url, {method, credentials: 'same-origin', headers: {'Accept': 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': csrf()}, body: JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'The request could not be completed.');
    return data;
  }
  const message = (root, value) => { const node = root.querySelector('[data-form-message]'); if (node) node.textContent = value; };

  document.querySelectorAll('[data-process-table]').forEach((root) => {
    const rows = [...root.querySelectorAll('tbody tr:not([data-empty])')];
    const apply = () => { const term = (root.querySelector('[data-table-search]')?.value || '').toLowerCase(); const status = root.querySelector('[data-status-filter]')?.value || ''; rows.forEach(row => { row.hidden = !((row.dataset.search || '').toLowerCase().includes(term) && (!status || row.dataset.status === status)); }); };
    root.querySelector('[data-table-search]')?.addEventListener('input', apply);
    root.querySelector('[data-status-filter]')?.addEventListener('change', apply);
    root.querySelectorAll('[data-sort]').forEach(button => button.addEventListener('click', () => { const key = button.dataset.sort; const body = root.querySelector('tbody'); rows.sort((a, b) => (a.dataset[key] || '').localeCompare(b.dataset[key] || '', undefined, {numeric: true})).forEach(row => body.append(row)); }));
  });

  const templateForm = document.querySelector('[data-template-form]');
  if (templateForm) {
    const list = templateForm.querySelector('[data-step-list]');
    templateForm.querySelector('[data-add-step]').addEventListener('click', () => { const step = list.querySelector('[data-step]').cloneNode(true); step.querySelectorAll('input, textarea').forEach(input => input.value = ''); list.append(step); });
    templateForm.addEventListener('click', event => { const remove = event.target.closest('[data-remove-step]'); if (remove && list.querySelectorAll('[data-step]').length > 1) remove.closest('[data-step]').remove(); });
    templateForm.addEventListener('submit', async event => { event.preventDefault(); const id = templateForm.dataset.templateId; const steps = [...list.querySelectorAll('[data-step]')].map(step => ({title: step.querySelector('[name="step_title"]').value, instructions: step.querySelector('[name="step_instructions"]').value || null})); try { const result = await api(id ? `/api/processes/templates/${id}` : '/api/processes/templates', id ? 'PUT' : 'POST', {name: templateForm.elements.name.value, description: templateForm.elements.description.value || null, steps}); window.location.assign(`/processes/templates/${result.id}`); } catch (error) { message(templateForm, error.message); } });
  }
  const startForm = document.querySelector('[data-run-start-form]');
  if (startForm) startForm.addEventListener('submit', async event => { event.preventDefault(); const value = name => startForm.elements[name].value; const optionalId = name => value(name) ? Number(value(name)) : null; const due = value('due_at'); try { const run = await api('/api/processes/runs', 'POST', {template_id: Number(value('template_id')), asset_id: optionalId('asset_id'), ticket_id: optionalId('ticket_id'), assignee_id: optionalId('assignee_id'), priority: value('priority'), due_at: due ? new Date(due).toISOString() : null}); window.location.assign(`/processes/${run.id}`); } catch (error) { message(startForm, error.message); } });
  const detail = document.querySelector('[data-run-detail]');
  if (detail) {
    const updateRun = async status => { const assignee = detail.querySelector('[data-run-assignee]')?.value; await api(`/api/processes/runs/${detail.dataset.runId}`, 'PATCH', {status, assignee_id: assignee ? Number(assignee) : null}); window.location.reload(); };
    detail.querySelectorAll('[data-run-action]').forEach(button => button.addEventListener('click', () => updateRun(button.dataset.runAction).catch(error => message(detail, error.message))));
    detail.querySelector('[data-run-assignee]')?.addEventListener('change', () => updateRun(detail.querySelector('[data-run-status]').textContent.trim().toLowerCase().replace(' ', '_')).catch(error => message(detail, error.message)));
    detail.querySelectorAll('[data-step-action]').forEach(select => select.addEventListener('change', async () => { const step = select.closest('[data-run-step]'); try { await api(`/api/processes/runs/${detail.dataset.runId}/steps/${step.dataset.stepId}`, 'PATCH', {status: select.value, notes: step.querySelector('[data-step-notes]').value || null}); window.location.reload(); } catch (error) { message(detail, error.message); } }));
  }
})();
