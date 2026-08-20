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
  document.querySelectorAll('[data-defender-modal-open]').forEach((button) => button.addEventListener('click', () => {
    const modal = document.getElementById(button.dataset.defenderModalOpen);
    if (!modal) return;
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    modal.querySelector('input:not([disabled]), select:not([disabled]), button:not([disabled])')?.focus();
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
  document.querySelectorAll('[data-defender-command]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    try { await send(`/api/defender/devices/${button.dataset.deviceId}/commands/${button.dataset.defenderCommand}`, { method: 'POST', body: '{}' }); button.textContent = 'Queued'; } catch (error) { alert(error.message); button.disabled = false; }
  }));
  document.querySelectorAll('[data-detection-action]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    try { await send(`/api/defender/detections/${button.dataset.detectionId}/actions`, { method: 'POST', body: JSON.stringify({ action: button.dataset.detectionAction }) }); location.reload(); } catch (error) { alert(error.message); button.disabled = false; }
  }));
});
