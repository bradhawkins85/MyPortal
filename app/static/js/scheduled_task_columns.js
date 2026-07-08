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

  // ---------------------------------------------------------------------------
  // Bulk selection and bulk actions
  // ---------------------------------------------------------------------------

  function isRowVisible(row) {
    if (!row) {
      return false;
    }
    if (row.dataset.filterHidden === 'true' || row.dataset.pageHidden === 'true') {
      return false;
    }
    if (row.classList.contains('table-search-hidden')) {
      return false;
    }
    if (row.hidden || row.style.display === 'none') {
      return false;
    }
    return true;
  }

  function initialiseBulkActions(table) {
    if (!table) {
      return;
    }

    const selectAll = document.querySelector('[data-scheduled-tasks-select-all]');
    const deleteBtn = document.querySelector('[data-scheduled-tasks-bulk-action="delete"]');
    const renameBtn = document.querySelector('[data-scheduled-tasks-bulk-action="rename"]');
    const redistributeBtn = document.querySelector('[data-scheduled-tasks-bulk-action="redistribute"]');
    const deleteForm = document.querySelector('[data-scheduled-tasks-bulk-form="delete"]');
    const renameForm = document.querySelector('[data-scheduled-tasks-bulk-form="rename"]');
    const redistributeForm = document.querySelector('[data-scheduled-tasks-bulk-form="redistribute"]');
    const redistributeHourInput = document.querySelector('[data-scheduled-tasks-redistribute-hour]');
    const countLabel = document.getElementById('scheduled-tasks-bulk-count');

    const getRowCheckboxes = () =>
      Array.from(table.querySelectorAll('[data-scheduled-tasks-row-checkbox]'));

    const getVisibleCheckboxes = () =>
      getRowCheckboxes().filter((cb) => {
        const row = cb.closest('tr');
        return row && !cb.disabled && isRowVisible(row);
      });

    const uncheckHiddenCheckboxes = () => {
      getRowCheckboxes().forEach((cb) => {
        const row = cb.closest('tr');
        if (row && !isRowVisible(row)) {
          cb.checked = false;
        }
      });
    };

    const updateState = () => {
      const visible = getVisibleCheckboxes();
      const selected = visible.filter((cb) => cb.checked);
      const count = selected.length;

      if (deleteBtn) {
        deleteBtn.disabled = count === 0;
        deleteBtn.hidden = false;
      }
      if (renameBtn) {
        renameBtn.disabled = count === 0;
        renameBtn.hidden = false;
      }
      if (redistributeBtn) {
        redistributeBtn.disabled = count === 0;
        redistributeBtn.hidden = false;
      }
      if (countLabel) {
        countLabel.textContent = `(${count})`;
        countLabel.hidden = count === 0;
      }

      if (selectAll) {
        if (!visible.length) {
          selectAll.checked = false;
          selectAll.indeterminate = false;
        } else {
          const selectedVisible = visible.filter((cb) => cb.checked);
          selectAll.checked = selectedVisible.length === visible.length;
          selectAll.indeterminate =
            selectedVisible.length > 0 && selectedVisible.length < visible.length;
        }
      }

      // Mirror checked checkboxes into forms that do not own the row checkboxes.
      [renameForm, redistributeForm].forEach((form) => {
        if (!form) {
          return;
        }
        form.querySelectorAll('input[name="taskIds"]').forEach((el) => el.remove());
        selected.forEach((cb) => {
          const input = document.createElement('input');
          input.type = 'hidden';
          input.name = 'taskIds';
          input.value = cb.value;
          form.appendChild(input);
        });
      });
    };

    if (selectAll) {
      selectAll.addEventListener('change', () => {
        const visible = getVisibleCheckboxes();
        visible.forEach((cb) => {
          cb.checked = selectAll.checked;
        });
        updateState();
      });
    }

    table.addEventListener('change', (event) => {
      if (
        event.target instanceof HTMLInputElement &&
        event.target.hasAttribute('data-scheduled-tasks-row-checkbox')
      ) {
        window.requestAnimationFrame(updateState);
      }
    });

    // Keep state in sync when table filter hides/shows rows
    const filterInput = document.querySelector('[data-table-filter="scheduled-tasks-table"]');
    if (filterInput) {
      filterInput.addEventListener('input', () => {
        uncheckHiddenCheckboxes();
        window.requestAnimationFrame(updateState);
      });
    }

    // Handle delete button click — confirm then submit the form
    if (deleteBtn && deleteForm) {
      deleteBtn.addEventListener('click', () => {
        uncheckHiddenCheckboxes();
        const selected = getVisibleCheckboxes().filter((cb) => cb.checked);
        const count = selected.length;
        if (!count) {
          return;
        }
        const noun = count === 1 ? 'task' : 'tasks';
        const message = `Delete ${count} selected ${noun}? This cannot be undone.`;
        if (!window.confirm(message)) {
          return;
        }
        deleteForm.submit();
      });
    }

    // Handle rename button click — confirm then submit the form
    if (renameBtn && renameForm) {
      renameBtn.addEventListener('click', () => {
        uncheckHiddenCheckboxes();
        const selected = getVisibleCheckboxes().filter((cb) => cb.checked);
        const count = selected.length;
        if (!count) {
          return;
        }
        const noun = count === 1 ? 'task' : 'tasks';
        const message = `Rename ${count} selected ${noun} to "Company Name — Command" format?`;
        if (!window.confirm(message)) {
          return;
        }
        renameForm.submit();
      });
    }

    // Handle redistribute button click — prompt for hour then submit the form
    if (redistributeBtn && redistributeForm && redistributeHourInput) {
      redistributeBtn.addEventListener('click', () => {
        uncheckHiddenCheckboxes();
        const selected = getVisibleCheckboxes().filter((cb) => cb.checked);
        const count = selected.length;
        if (!count) {
          return;
        }
        const rawHour = window.prompt('Enter the UTC hour for the selected tasks to start running (0-23):', '0');
        if (rawHour === null) {
          return;
        }
        const hour = Number.parseInt(rawHour.trim(), 10);
        if (!Number.isInteger(hour) || hour < 0 || hour > 23) {
          window.alert('Enter an hour between 0 and 23.');
          return;
        }
        const noun = count === 1 ? 'task' : 'tasks';
        const message = `Redistribute ${count} selected ${noun} from ${String(hour).padStart(2, '0')}:00 UTC, one minute apart?`;
        if (!window.confirm(message)) {
          return;
        }
        redistributeHourInput.value = String(hour);
        redistributeForm.submit();
      });
    }

    updateState();
  }

  document.addEventListener('DOMContentLoaded', () => {
    const table = document.getElementById('scheduled-tasks-table');
    initialiseColumnControls(table);
    initialiseBulkActions(table);
  });
})();
