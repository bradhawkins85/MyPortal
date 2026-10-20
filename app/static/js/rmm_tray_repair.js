(function () {
  'use strict';

  // The asset page's "Repair tray agent" menu item: the RMM agent stops the tray
  // service, clears its enrolment and starts it again; the run opens in the
  // shared result viewer.
  document.querySelectorAll('[data-rmm-tray-repair]').forEach((button) => button.addEventListener('click', async () => {
    const assetId = button.getAttribute('data-rmm-tray-repair');
    const name = button.getAttribute('data-rmm-tray-repair-name') || 'this device';
    const menu = button.closest('details');
    if (menu) menu.open = false;
    if (button.disabled || !window.confirm('Repair the tray agent on ' + name + '? The RMM agent stops the tray service, '
      + 'deletes its saved state and config and its device ID, then restarts it so it enrols again.')) {
      return;
    }
    const rmm = window.MyPortalRmm;
    button.disabled = true;
    try {
      const result = await rmm.api('/api/rmm/assets/' + encodeURIComponent(assetId) + '/tray-repair', { method: 'POST', body: {} });
      rmm.openResult(result.run_id);
    } catch (error) {
      window.alert(error.message || 'Could not queue the tray agent repair.');
    } finally {
      button.disabled = false;
    }
  }));
})();
