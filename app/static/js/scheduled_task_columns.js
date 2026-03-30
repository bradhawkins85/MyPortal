(function () {
  'use strict';

  const STORAGE_KEY = 'portal.scheduled-tasks.columns';

  function loadVisibleColumns(defaultColumns) {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      if (Array.isArray(stored) && stored.every((item) => typeof item === 'string')) {
        return stored;
      }
    } catch (err) {
      console.warn('Failed to read stored scheduled task column preferences', err);
    }
    return defaultColumns;
  }

  function saveVisibleColumns(columns) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(columns));
    } catch (err) {
      console.warn('Failed to persist scheduled task column preferences', err);
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
    const container = document.querySelector('[data-scheduled-task-columns]');
    if (!container || !table) {
      return;
    }
    const toggleButton = container.querySelector('[data-columns-toggle]');
    const panel = container.querySelector('[data-columns-panel]');
    const toggles = Array.from(container.querySelectorAll('.scheduled-task-column-toggle'));

    if (!toggleButton || !panel || toggles.length === 0) {
      return;
    }

    function openPanel() {
      container.classList.add('scheduled-task-columns--open');
      panel.hidden = false;
    }

    function closePanel() {
      container.classList.remove('scheduled-task-columns--open');
      panel.hidden = true;
    }

    toggleButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const isOpen = container.classList.contains('scheduled-task-columns--open');
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

    const defaultColumns = toggles
      .filter((input) => input.checked)
      .map((input) => input.dataset.column)
      .filter(Boolean);
    let visibleColumns = loadVisibleColumns(defaultColumns);
    if (!visibleColumns.includes('name')) {
      visibleColumns.push('name');
    }

    toggles.forEach((input) => {
      const column = input.dataset.column;
      if (!column) {
        return;
      }
      const shouldShow = column === 'name' || visibleColumns.includes(column);
      input.checked = shouldShow;
      setColumnVisibility(table, column, shouldShow);
    });

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
        saveVisibleColumns(selected);
        setColumnVisibility(table, column, input.checked);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const table = document.getElementById('scheduled-tasks-table');
    initialiseColumnControls(table);
  });
})();
