(function () {
  'use strict';

  const STORAGE_KEY = 'portal.staff.columns';

  function loadVisibleColumns(defaultColumns) {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      if (Array.isArray(stored) && stored.every((item) => typeof item === 'string')) {
        return stored;
      }
    } catch (err) {
      console.warn('Failed to read stored staff column preferences', err);
    }
    return defaultColumns;
  }

  function saveVisibleColumns(columns) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(columns));
    } catch (err) {
      console.warn('Failed to persist staff column preferences', err);
    }
  }

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
    const container = document.querySelector('[data-staff-columns]');
    if (!container || !table) {
      return;
    }
    const toggleButton = container.querySelector('[data-columns-toggle]');
    const panel = container.querySelector('[data-columns-panel]');
    const toggles = Array.from(container.querySelectorAll('.staff-column-toggle'));

    if (!toggleButton || !panel || toggles.length === 0) {
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

    const defaultColumns = toggles.filter((input) => input.checked).map((input) => input.dataset.column).filter(Boolean);
    let visibleColumns = loadVisibleColumns(defaultColumns);
    if (!visibleColumns.includes('first-name')) {
      visibleColumns.push('first-name');
    }

    toggles.forEach((input) => {
      const column = input.dataset.column;
      if (!column) {
        return;
      }
      const shouldShow = column === 'first-name' || visibleColumns.includes(column);
      input.checked = shouldShow;
      setColumnVisibility(table, column, shouldShow);
    });

    toggles.forEach((input) => {
      input.addEventListener('change', () => {
        const column = input.dataset.column;
        if (!column) {
          return;
        }
        if (column === 'first-name') {
          input.checked = true;
          return;
        }
        const selected = toggles
          .filter((toggle) => (toggle.checked && !toggle.disabled) || toggle.dataset.column === 'first-name')
          .map((toggle) => toggle.dataset.column)
          .filter(Boolean);
        if (!selected.includes('first-name')) {
          selected.push('first-name');
        }
        saveVisibleColumns(selected);
        setColumnVisibility(table, column, input.checked);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const table = document.getElementById('staff-table');
    initialiseColumnControls(table);
  });
})();
