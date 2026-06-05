(function () {
  'use strict';

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) {
      return meta.getAttribute('content');
    }
    const pattern = 'myportal_session_csrf=([^;]*)';
    const match = document.cookie.match(new RegExp(pattern));
    return match ? decodeURIComponent(match[1]) : '';
  }

  async function deleteExclusion(exclusionId, endpoint) {
    const response = await fetch(`${endpoint}/${exclusionId}`, {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': getCsrfToken() },
    });
    if (!response.ok && response.status !== 204) {
      const detail = await response.json().catch(() => ({}));
      const message = (detail && detail.detail) ? detail.detail : 'Failed to remove exclusion';
      throw new Error(message);
    }
  }

  function bindRemoveExclusionButtons(endpoint) {
    document.querySelectorAll('[data-remove-exclusion]').forEach((button) => {
      button.addEventListener('click', async () => {
        const exclusionId = button.getAttribute('data-remove-exclusion');
        if (!exclusionId) {
          return;
        }
        button.disabled = true;
        const originalText = button.textContent;
        button.textContent = 'Removing…';
        try {
          await deleteExclusion(exclusionId, endpoint);
          const row = button.closest('[data-exclusion-row]');
          if (row) {
            row.remove();
          }
          // If no rows remain, show the empty state message
          const tbody = document.querySelector('#exclusions-table tbody');
          const emptyEl = document.querySelector('[data-exclusions-empty]');
          if (tbody && tbody.children.length === 0) {
            const table = document.getElementById('exclusions-table');
            if (table) {
              const wrapper = table.closest('.table-wrapper');
              if (wrapper) {
                wrapper.setAttribute('hidden', '');
              }
            }
            if (emptyEl) {
              emptyEl.removeAttribute('hidden');
            }
          }
        } catch (error) {
          button.textContent = originalText;
          button.disabled = false;
          window.alert(error.message || 'Unable to remove exclusion');
        }
      });
    });
  }

  function init() {
    const scriptEl = document.querySelector('script[data-exclusions-endpoint]');
    const endpoint = scriptEl ? scriptEl.getAttribute('data-exclusions-endpoint') : '/api/notifications/exclusions';
    if (!endpoint) {
      return;
    }
    bindRemoveExclusionButtons(endpoint);

    // Ensure the empty state hidden attribute matches the current DOM state
    const emptyEl = document.querySelector('[data-exclusions-empty]');
    if (emptyEl) {
      const tbody = document.querySelector('#exclusions-table tbody');
      if (tbody && tbody.children.length > 0) {
        emptyEl.setAttribute('hidden', '');
      } else {
        emptyEl.removeAttribute('hidden');
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
