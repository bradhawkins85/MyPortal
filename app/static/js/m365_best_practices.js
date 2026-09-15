(function () {
  'use strict';

  const STORAGE_PREFIX = 'myportal.m365BestPractices.statusFilters.';

  function storageKey(tableId) {
    return `${STORAGE_PREFIX}${tableId}`;
  }

  function loadStatuses(tableId, availableStatuses) {
    try {
      const stored = JSON.parse(window.localStorage.getItem(storageKey(tableId)) || '[]');
      if (!Array.isArray(stored)) {
        return [];
      }
      return stored.filter((status) => typeof status === 'string' && availableStatuses.has(status));
    } catch (error) {
      return [];
    }
  }

  function saveStatuses(tableId, statuses) {
    try {
      if (statuses.length) {
        window.localStorage.setItem(storageKey(tableId), JSON.stringify(statuses));
      } else {
        window.localStorage.removeItem(storageKey(tableId));
      }
    } catch (error) {
      /* Storage may be unavailable; the filter remains functional for this page view. */
    }
  }

  function applyStatuses(strip, table, statuses) {
    const selected = new Set(statuses);
    strip.querySelectorAll('.bp-filter-btn').forEach((button) => {
      button.setAttribute('aria-pressed', selected.has(button.dataset.bpStatus) ? 'true' : 'false');
    });
    table.querySelectorAll('tbody tr[data-bp-status]').forEach((row) => {
      row.hidden = selected.size > 0 && !selected.has(row.dataset.bpStatus);
    });
  }

  function initialiseStatusFilters() {
    document.querySelectorAll('.bp-filter-strip[data-bp-table]').forEach((strip) => {
      const tableId = strip.dataset.bpTable;
      const table = tableId ? document.getElementById(tableId) : null;
      const buttons = Array.from(strip.querySelectorAll('.bp-filter-btn[data-bp-status]'));
      if (!table || !buttons.length) {
        return;
      }

      const availableStatuses = new Set(buttons.map((button) => button.dataset.bpStatus));
      applyStatuses(strip, table, loadStatuses(tableId, availableStatuses));

      buttons.forEach((button) => {
        button.addEventListener('click', () => {
          const statuses = buttons
            .filter((candidate) => candidate === button
              ? candidate.getAttribute('aria-pressed') !== 'true'
              : candidate.getAttribute('aria-pressed') === 'true')
            .map((candidate) => candidate.dataset.bpStatus);
          applyStatuses(strip, table, statuses);
          saveStatuses(tableId, statuses);
        });
      });
    });
  }

  document.addEventListener('DOMContentLoaded', initialiseStatusFilters);
}());
