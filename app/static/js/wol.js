(() => {
  const buttons = document.querySelectorAll('[data-wol-asset-id], [data-wol-device-id]');
  if (!buttons.length) return;

  const statusFor = (button) => {
    const scope = button.closest('td, .card__body');
    return scope ? scope.querySelector('[data-wol-status]') : null;
  };

  const setButtonState = (button, busy, label) => {
    button.disabled = busy;
    if (!busy && label) button.textContent = label;
  };

  const formatTimestamp = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const stamp = date.toLocaleString();
    const seconds = Math.round((Date.now() - date.getTime()) / 1000);
    if (seconds < 60) return `${stamp} (${Math.max(seconds, 0)}s ago)`;
    if (seconds < 3600) return `${stamp} (${Math.round(seconds / 60)}m ago)`;
    if (seconds < 86400) return `${stamp} (${Math.round(seconds / 3600)}h ago)`;
    return `${stamp} (${Math.round(seconds / 86400)}d ago)`;
  };

  document.querySelectorAll('[data-wol-utc]').forEach((element) => {
    element.textContent = formatTimestamp(element.getAttribute('data-wol-utc'));
  });

  const refreshStats = async (assetId) => {
    try {
      const response = await fetch(`/api/wol/assets/${assetId}/attempts`, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) return;
      const data = await response.json();
      const stats = (data && data.stats) || {};
      const summary = document.querySelector('[data-wol-stat="summary"]');
      if (summary) {
        summary.textContent = stats.total
          ? `${stats.success}/${stats.total} attempts woke the device`
          : 'No WoL attempts yet';
      }
      const lastSuccess = document.querySelector('[data-wol-stat="last-success"]');
      if (lastSuccess) {
        lastSuccess.textContent = stats.last_success_at ? formatTimestamp(stats.last_success_at) : '—';
      }
    } catch (_) { /* stats refresh is best-effort */ }
  };

  buttons.forEach((button) => {
    const originalLabel = button.textContent;
    button.addEventListener('click', () => {
      const message = button.getAttribute('data-confirm');
      if (message && !window.confirm(message)) return;
      const assetId = button.getAttribute('data-wol-asset-id');
      const deviceId = button.getAttribute('data-wol-device-id');
      const endpoint = assetId
        ? `/api/wol/assets/${assetId}/wake`
        : `/api/wol/devices/${deviceId}/wake`;
      const status = statusFor(button);
      setButtonState(button, true, 'Waking…');
      if (status) status.textContent = 'Dispatching WoL magic packet…';
      fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      })
        .then(async (response) => {
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error((data && data.detail) || `Wake request failed (${response.status})`);
          }
          if (data.status === 'dispatched') {
            const detail = data.error ? ` (${data.error})` : '';
            if (status) status.textContent = `Wake dispatched — ${data.delivered_count}/${data.candidate_count} online agent(s) broadcast the packet${detail}. The result is tracked for about 2 minutes.`;
            if (assetId) refreshStats(assetId);
          } else if (data.status === 'no_mac') {
            if (status) status.textContent = data.error || 'No MAC address is recorded for this asset.';
          } else if (data.status === 'no_candidates') {
            if (status) status.textContent = data.error || 'No online agents were found on the target network.';
          } else if (status) {
            status.textContent = 'Wake request recorded.';
          }
        })
        .catch((error) => {
          if (status) status.textContent = error.message || 'Wake request failed. Check your connection.';
        })
        .finally(() => setButtonState(button, false, originalLabel));
    });
  });
})();