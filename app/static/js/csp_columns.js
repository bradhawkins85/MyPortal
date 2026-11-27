(function () {
  'use strict';

  // Saved per user in the database (see view_preferences.js); the old
  // browser-only key is transferred the first time the page loads.
  const PREFERENCE_KEY = 'tables:csp-customers:visible-columns';
  const LEGACY_STORAGE_KEY = 'portal.csp.columns';

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
    const container = document.querySelector('[data-csp-columns]');
    if (!container || !table) {
      return;
    }
    const toggleButton = container.querySelector('[data-columns-toggle]');
    const panel = container.querySelector('[data-columns-panel]');
    const toggles = Array.from(container.querySelectorAll('.csp-column-toggle'));

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

    function renderColumns(columns) {
      toggles.forEach((input) => {
        const column = input.dataset.column;
        if (!column) {
          return;
        }
        const shouldShow = column === 'name' || columns.includes(column);
        input.checked = shouldShow;
        setColumnVisibility(table, column, shouldShow);
      });
    }

    const defaultColumns = toggles.filter((input) => input.checked).map((input) => input.dataset.column).filter(Boolean);
    const preference = window.MyPortalViewPrefs.bindVisibleColumns({
      key: PREFERENCE_KEY,
      defaults: defaultColumns,
      available: toggles.map((input) => input.dataset.column).filter(Boolean),
      required: 'name',
      legacyKey: LEGACY_STORAGE_KEY,
      apply: renderColumns,
    });
    renderColumns(preference.initial);

    toggles.forEach((input) => {
      input.addEventListener('change', () => {
        const column = input.dataset.column;
        if (!column) {
          return;
        }
        if (column === 'name') {
          input.checked = true;
          return;
        }
        const selected = toggles
          .filter((toggle) => (toggle.checked && !toggle.disabled) || toggle.dataset.column === 'name')
          .map((toggle) => toggle.dataset.column)
          .filter(Boolean);
        if (!selected.includes('name')) {
          selected.push('name');
        }
        preference.save(selected);
        setColumnVisibility(table, column, input.checked);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const table = document.getElementById('csp-customers-table');
    initialiseColumnControls(table);
  });
})();
