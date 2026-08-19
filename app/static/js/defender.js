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
  document.querySelector('#defender-exclusion-form')?.addEventListener('submit', async (event) => {
    event.preventDefault(); const data = Object.fromEntries(new FormData(event.currentTarget));
    data.tray_device_id = data.tray_device_id ? Number(data.tray_device_id) : null;
    try { await send('/api/defender/exclusions', { method: 'POST', body: JSON.stringify(data) }); location.reload(); } catch (error) { alert(error.message); }
  });
  document.querySelector('#defender-settings-form')?.addEventListener('submit', async (event) => {
    event.preventDefault(); const data = Object.fromEntries(new FormData(event.currentTarget));
    data.scheduled_scan_type = data.scheduled_scan_type || null;
    data.scheduled_scan_day = data.scheduled_scan_type ? Number(data.scheduled_scan_day) : null;
    data.scheduled_scan_time = data.scheduled_scan_type ? data.scheduled_scan_time : null;
    data.auto_ticket_min_severity = data.auto_ticket_min_severity || null;
    try { await send('/api/defender/settings', { method: 'PUT', body: JSON.stringify(data) }); location.reload(); } catch (error) { alert(error.message); }
  });
  document.querySelectorAll('[data-delete-exclusion]').forEach((button) => button.addEventListener('click', async () => {
    if (!confirm('Remove this Defender exclusion?')) return;
    try { await send(`/api/defender/exclusions/${button.dataset.deleteExclusion}`, { method: 'DELETE' }); location.reload(); } catch (error) { alert(error.message); }
  }));
  document.querySelectorAll('[data-ticket-detection]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    try { const result = await send(`/api/defender/detections/${button.dataset.ticketDetection}/ticket`, { method: 'POST', body: '{}' }); location.href = result.url; } catch (error) { alert(error.message); button.disabled = false; }
  }));
  document.querySelectorAll('[data-ticket-device]').forEach((button) => button.addEventListener('click', async () => {
    const issue = prompt('Describe the Defender issue for this endpoint:', 'Windows Defender requires investigation');
    if (issue === null) return;
    button.disabled = true;
    try { const result = await send(`/api/defender/devices/${button.dataset.ticketDevice}/ticket`, { method: 'POST', body: JSON.stringify({ issue }) }); location.href = result.url; } catch (error) { alert(error.message); button.disabled = false; }
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
