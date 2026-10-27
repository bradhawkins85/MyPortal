(function () {
  'use strict';

  // "Delete" in the asset page's Actions menu. The Assets list has its own
  // handler in assets.js, which removes the row instead of leaving the page.
  document.querySelectorAll('[data-asset-delete-redirect]').forEach((button) => button.addEventListener('click', async () => {
    const assetId = button.getAttribute('data-asset-id');
    const name = button.getAttribute('data-asset-name') || 'this asset';
    if (!assetId || !window.confirm('Delete ' + name + ' from MyPortal?\n\nIf the tray or RMM agent is still installed, '
      + 'the device is registered again as a new asset the next time it checks in. Use "Uninstall agents" to remove them.')) {
      return;
    }
    const csrf = document.querySelector('meta[name="csrf-token"]');
    button.disabled = true;
    try {
      const response = await fetch('/assets/' + encodeURIComponent(assetId), {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { Accept: 'application/json', 'X-CSRF-Token': csrf ? csrf.getAttribute('content') : '' },
      });
      if (!response.ok) {
        throw new Error('Request failed with status ' + response.status);
      }
      window.location.assign(button.getAttribute('data-asset-delete-redirect'));
    } catch (error) {
      console.error('Failed to delete asset', error);
      window.alert('Unable to delete asset. Please try again.');
      button.disabled = false;
    }
  }));
})();
