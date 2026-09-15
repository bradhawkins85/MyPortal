(function () {
  'use strict';

  const STORAGE_KEY = 'myportal.m365BestPractices.statusFilters.global';

  function loadStatuses(availableStatuses) {
    try {
      const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '[]');
      if (!Array.isArray(stored)) {
        return [];
      }
      return stored.filter((status) => typeof status === 'string' && availableStatuses.has(status));
    } catch (error) {
      return [];
    }
  }

  function saveStatuses(statuses) {
    try {
      if (statuses.length) {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(statuses));
      } else {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    } catch (error) {
      /* Storage may be unavailable; the filter remains functional for this page view. */
    }
  }

  function applyStatuses(strip, tables, statuses) {
    const selected = new Set(statuses);
    strip.querySelectorAll('.bp-filter-btn').forEach((button) => {
      button.setAttribute('aria-pressed', selected.has(button.dataset.bpStatus) ? 'true' : 'false');
    });
    tables.forEach((table) => {
      table.querySelectorAll('tbody tr[data-bp-status]').forEach((row) => {
        row.hidden = selected.size > 0 && !selected.has(row.dataset.bpStatus);
      });
    });
  }

  function initialiseStatusFilters() {
    const strip = document.querySelector('.bp-filter-strip');
    const tables = Array.from(document.querySelectorAll('.bp-results-table'));
    const buttons = strip ? Array.from(strip.querySelectorAll('.bp-filter-btn[data-bp-status]')) : [];
    if (!strip || !tables.length || !buttons.length) {
      return;
    }

    const availableStatuses = new Set(buttons.map((button) => button.dataset.bpStatus));
    applyStatuses(strip, tables, loadStatuses(availableStatuses));

    buttons.forEach((button) => {
      button.addEventListener('click', () => {
        const statuses = buttons
          .filter((candidate) => candidate === button
            ? candidate.getAttribute('aria-pressed') !== 'true'
            : candidate.getAttribute('aria-pressed') === 'true')
          .map((candidate) => candidate.dataset.bpStatus);
        applyStatuses(strip, tables, statuses);
        saveStatuses(statuses);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', initialiseStatusFilters);
}());
