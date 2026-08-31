document.addEventListener('DOMContentLoaded', () => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const send = async (url, options = {}) => {
    const response = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...(options.headers || {}) } });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'Request failed');
    return response.json();
  };
  document.querySelector('[data-defender-enable]')?.addEventListener('click', async () => {
    try { await send('/api/defender/enabled', { method: 'POST', body: JSON.stringify({ enabled: true }) }); location.reload(); } catch (error) { alert(error.message); }
  });
  const closeModal = (modal) => {
    if (!modal) return;
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
  };
  const openModal = (modal) => {
    if (!modal) return;
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    modal.querySelector('input:not([disabled]), select:not([disabled]), button:not([disabled])')?.focus();
  };
  const isRegistryPath = (value) => /^(HKLM|HKCU|HKCR|HKU|HKCC|HKEY_)/i.test((value || '').trim());
  document.querySelectorAll('[data-defender-modal-open]').forEach((button) => button.addEventListener('click', () => {
    openModal(document.getElementById(button.dataset.defenderModalOpen));
  }));
  document.querySelectorAll('[data-defender-modal-close]').forEach((button) => button.addEventListener('click', () => closeModal(button.closest('.modal'))));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeModal(document.querySelector('.modal:not([hidden])'));
  });
  document.querySelector('#defender-exclusion-form')?.addEventListener('submit', async (event) => {
    event.preventDefault(); const data = Object.fromEntries(new FormData(event.currentTarget));
    data.tray_device_id = data.tray_device_id ? Number(data.tray_device_id) : null;
    try { await send('/api/defender/exclusions', { method: 'POST', body: JSON.stringify(data) }); location.reload(); } catch (error) { alert(error.message); }
  });
  document.querySelectorAll('[data-defender-settings-form]').forEach((form) => form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = {};
    document.querySelectorAll('[data-defender-settings-form]').forEach((settingsForm) => Object.assign(data, Object.fromEntries(new FormData(settingsForm))));
    data.scheduled_scan_type = data.scheduled_scan_type || null;
    data.scheduled_scan_day = data.scheduled_scan_type ? Number(data.scheduled_scan_day) : null;
    data.scheduled_scan_time = data.scheduled_scan_type ? data.scheduled_scan_time : null;
    data.auto_ticket_min_severity = data.auto_ticket_min_severity || null;
    ['auto_ticket_antivirus_off', 'auto_ticket_realtime_off', 'auto_ticket_tamper_off', 'auto_ticket_threat_detected']
      .forEach((name) => { data[name] = Boolean(document.querySelector(`[name="${name}"]`)?.checked); });
    try { await send('/api/defender/settings', { method: 'PUT', body: JSON.stringify(data) }); location.reload(); } catch (error) { alert(error.message); }
  }));
  document.querySelectorAll('[data-delete-exclusion]').forEach((button) => button.addEventListener('click', async () => {
    if (!confirm('Remove this Defender exclusion?')) return;
    try { await send(`/api/defender/exclusions/${button.dataset.deleteExclusion}`, { method: 'DELETE' }); location.reload(); } catch (error) { alert(error.message); }
  }));
  const itemMarkup = '<div class="form-grid" data-exclusion-list-item><label>Type<select name="exclusion_type"><option value="path">Path</option><option value="process">Process</option><option value="extension">Extension</option><option value="registry">Registry path</option></select></label><label>Value<input name="value" required maxlength="1000"></label><button class="button button--ghost" type="button" data-remove-list-item>Remove</button></div>';
  document.querySelectorAll('[data-add-list-item]').forEach((button) => button.addEventListener('click', () => {
    button.closest('form').querySelector('[data-exclusion-list-items]').insertAdjacentHTML('beforeend', itemMarkup);
  }));
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-remove-list-item]');
    if (button) button.closest('[data-exclusion-list-item]').remove();
  });
  document.querySelectorAll('[data-exclusion-list-form]').forEach((form) => form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const exclusions = [...form.querySelectorAll('[data-exclusion-list-item]')].map((row) => ({
      exclusion_type: row.querySelector('[name="exclusion_type"]').value,
      value: row.querySelector('[name="value"]').value.trim(),
    })).filter((item) => item.value);
    const company_ids = [...form.querySelectorAll('[name="company_ids"]:checked')].map((input) => Number(input.value));
    const listId = form.dataset.listId;
    try {
      await send(listId ? `/api/defender/exclusion-lists/${listId}` : '/api/defender/exclusion-lists', {
        method: listId ? 'PUT' : 'POST', body: JSON.stringify({ name: form.elements.name.value.trim(), exclusions, company_ids }),
      });
      location.reload();
    } catch (error) { alert(error.message); }
  }));
  document.querySelectorAll('[data-delete-exclusion-list]').forEach((button) => button.addEventListener('click', async () => {
    if (!confirm('Delete this exclusion list from every assigned company?')) return;
    try { await send(`/api/defender/exclusion-lists/${button.dataset.deleteExclusionList}`, { method: 'DELETE' }); location.reload(); } catch (error) { alert(error.message); }
  }));
  document.querySelectorAll('[data-defender-command]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    try { await send(`/api/defender/devices/${button.dataset.deviceId}/commands/${button.dataset.defenderCommand}`, { method: 'POST', body: '{}' }); button.textContent = 'Queued'; } catch (error) { alert(error.message); button.disabled = false; }
  }));
  document.querySelectorAll('[data-detection-action]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    try { await send(`/api/defender/detections/${button.dataset.detectionId}/actions`, { method: 'POST', body: JSON.stringify({ action: button.dataset.detectionAction }) }); location.reload(); } catch (error) { alert(error.message); button.disabled = false; }
  }));
  document.querySelectorAll('[data-detection-exclude-value]').forEach((button) => button.addEventListener('click', () => {
    const exclusionForm = document.querySelector('#defender-exclusion-form');
    if (!exclusionForm) return;
    const value = (button.dataset.detectionExcludeValue || '').trim();
    exclusionForm.elements.scope.value = 'device';
    exclusionForm.elements.tray_device_id.value = button.dataset.detectionDeviceId || '';
    exclusionForm.elements.exclusion_type.value = isRegistryPath(value) ? 'registry' : 'path';
    exclusionForm.elements.value.value = value;
    openModal(document.getElementById('exclusions-modal'));
    exclusionForm.elements.value.focus();
  }));
});
