(function () {
  const STORAGE_KEY = 'portal.assets.columns';

  function loadVisibleColumns(defaultColumns) {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      if (Array.isArray(stored) && stored.every((item) => typeof item === 'string')) {
        return stored;
      }
    } catch (err) {
      console.warn('Failed to read stored asset column preferences', err);
    }
    return defaultColumns;
  }

  function saveVisibleColumns(columns) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(columns));
    } catch (err) {
      console.warn('Failed to persist asset column preferences', err);
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

  function updateVisibleCount(table) {
    const totalElement = document.querySelector('[data-asset-total]');
    if (!totalElement || !table) {
      return;
    }
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    const visibleRows = rows.filter((row) => row.style.display !== 'none');
    totalElement.textContent = String(visibleRows.length);
  }

  function initialiseColumnControls(table) {
    const container = document.querySelector('[data-asset-columns]');
    if (!container || !table) {
      return;
    }
    const toggleButton = container.querySelector('[data-columns-toggle]');
    const panel = container.querySelector('[data-columns-panel]');
    const toggles = Array.from(container.querySelectorAll('.asset-column-toggle'));

    if (!toggleButton || !panel || toggles.length === 0) {
      return;
    }

    function openPanel() {
      container.classList.add('asset-columns--open');
      panel.hidden = false;
    }

    function closePanel() {
      container.classList.remove('asset-columns--open');
      panel.hidden = true;
    }

    toggleButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const isOpen = container.classList.contains('asset-columns--open');
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

    const defaultColumns = toggles.map((input) => input.dataset.column).filter(Boolean);
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
      input.checked = shouldShow || input.disabled;
      setColumnVisibility(table, column, shouldShow);
    });

    saveVisibleColumns(visibleColumns);

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

  function initialiseDeletion(table) {
    const buttons = document.querySelectorAll('.asset-delete-button');
    buttons.forEach((button) => {
      button.addEventListener('click', async () => {
        const assetId = button.getAttribute('data-asset-id');
        if (!assetId) {
          return;
        }
        if (!window.confirm('Delete asset?')) {
          return;
        }
        button.disabled = true;
        try {
          const response = await fetch(`/assets/${assetId}`, { method: 'DELETE' });
          if (!response.ok) {
            throw new Error(`Request failed with status ${response.status}`);
          }
          const row = button.closest('tr');
          if (row) {
            row.remove();
          }
          updateVisibleCount(table);
        } catch (error) {
          console.error('Failed to delete asset', error);
          window.alert('Unable to delete asset. Please try again.');
          button.disabled = false;
        }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const table = document.getElementById('assets-table');
    const searchInput = document.getElementById('asset-search');

    initialiseColumnControls(table);
    initialiseDeletion(table);
    updateVisibleCount(table);

    if (searchInput) {
      searchInput.addEventListener('input', () => {
        window.requestAnimationFrame(() => updateVisibleCount(table));
      });
    }
  });
})();
