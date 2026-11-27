(function () {
  'use strict';

  // Saved per user in the database (see view_preferences.js); the old
  // browser-only key is transferred the first time the page loads.
  const PREFERENCE_KEY = 'tables:tickets:visible-columns';
  const LEGACY_STORAGE_KEY = 'portal.tickets.columns';
  let columnController = null;
  let pendingVisibleColumns = null;

  function setColumnVisibility(table, column, visible) {
    if (!table) {
      return;
    }
    const selector = `[data-column="${column}"]`;
    table.querySelectorAll(selector).forEach((element) => {
      element.style.display = visible ? '' : 'none';
    });
  }

  function initialiseColumnControls(table) {
    const container = document.querySelector('[data-ticket-columns]');
    if (!container || !table) {
      return;
    }
    const toggleButton = container.querySelector('[data-columns-toggle]');
    const panel = container.querySelector('[data-columns-panel]');
    const toggles = Array.from(container.querySelectorAll('.ticket-column-toggle'));

    if (!toggleButton || !panel || toggles.length === 0 || !window.MyPortalViewPrefs) {
      return;
    }

    function openPanel() {
      container.classList.add('ticket-columns--open');
      panel.hidden = false;
    }

    function closePanel() {
      container.classList.remove('ticket-columns--open');
      panel.hidden = true;
    }

    toggleButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const isOpen = container.classList.contains('ticket-columns--open');
      if (isOpen) {
        closePanel();
      } else {
        openPanel();
      }
    });

    document.addEventListener('click', (event) => {
      if (!container.contains(event.target)) {
        closePanel();
      }
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closePanel();
        toggleButton.focus();
      }
    });

    const availableColumns = toggles.map((input) => input.dataset.column).filter(Boolean);
    const defaultColumns = toggles.filter((input) => input.checked).map((input) => input.dataset.column).filter(Boolean);

    function normaliseColumns(columns) {
      const selected = Array.isArray(columns)
        ? columns.filter((column) => typeof column === 'string' && availableColumns.includes(column))
        : [];
      if (!selected.includes('subject')) selected.push('subject');
      return [...new Set(selected)];
    }

    function renderColumns(columns) {
      const visibleColumns = normaliseColumns(columns);
      toggles.forEach((input) => {
        const column = input.dataset.column;
        const shouldShow = column === 'subject' || visibleColumns.includes(column);
        input.checked = shouldShow;
        setColumnVisibility(table, column, shouldShow);
      });
      return visibleColumns;
    }

    const viewLayoutApplied = Array.isArray(pendingVisibleColumns);
    const preference = window.MyPortalViewPrefs.bindVisibleColumns({
      key: PREFERENCE_KEY,
      defaults: defaultColumns,
      available: availableColumns,
      required: 'subject',
      legacyKey: LEGACY_STORAGE_KEY,
      // A saved view chosen before the stored layout loaded takes priority.
      apply: (columns) => { if (!viewLayoutApplied) renderColumns(columns); },
    });

    function applyVisibleColumns(columns, persist = true) {
      const visibleColumns = renderColumns(columns);
      if (persist) preference.save(visibleColumns);
      return visibleColumns;
    }

    columnController = {
      getVisibleColumns() {
        return normaliseColumns(toggles.filter((toggle) => toggle.checked).map((toggle) => toggle.dataset.column));
      },
      applyVisibleColumns
    };
    applyVisibleColumns(pendingVisibleColumns || preference.initial, false);
    pendingVisibleColumns = null;

    toggles.forEach((input) => {
      input.addEventListener('change', () => {
        const column = input.dataset.column;
        if (!column) {
          return;
        }
        if (column === 'subject') {
          input.checked = true;
          return;
        }
        const selected = toggles
          .filter((toggle) => (toggle.checked && !toggle.disabled) || toggle.dataset.column === 'subject')
          .map((toggle) => toggle.dataset.column)
          .filter(Boolean);
        if (!selected.includes('subject')) {
          selected.push('subject');
        }
        applyVisibleColumns(selected);
      });
    });
  }

  // Saved views load asynchronously, so expose a small API that can also queue a
  // layout if a view arrives before the column controls have initialised.
  window.ticketColumns = {
    getVisibleColumns() {
      return columnController ? columnController.getVisibleColumns() : null;
    },
    applyVisibleColumns(columns) {
      if (columnController) return columnController.applyVisibleColumns(columns);
      pendingVisibleColumns = Array.isArray(columns) ? columns.slice() : [];
      return pendingVisibleColumns;
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    const table = document.getElementById('tickets-table');
    initialiseColumnControls(table);
  });
})();
