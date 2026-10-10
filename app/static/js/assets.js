(function () {
  const STORAGE_KEY = 'portal.assets.columns';

  function loadVisibleColumns(defaultColumns) {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw === null) {
        return defaultColumns;
      }
      const stored = JSON.parse(raw);
      // A list holding only the locked "name" column was persisted on first load by an
      // earlier bug, so treat it like no preference rather than hiding every column.
      const onlyLockedColumn = stored.length === 1 && stored[0] === 'name';
      if (Array.isArray(stored) && stored.length > 0 && !onlyLockedColumn && stored.every((item) => typeof item === 'string')) {
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

  function updateVisibleCount(table, detail) {
    const totalElement = document.querySelector('[data-asset-total]');
    if (!totalElement || !table) {
      return;
    }
    let count = null;
    if (detail && typeof detail.filteredCount === 'number' && !Number.isNaN(detail.filteredCount)) {
      count = detail.filteredCount;
    }
    if (count === null) {
      const rows = Array.from(table.querySelectorAll('tbody tr'));
      const visibleRows = rows.filter((row) => row.style.display !== 'none');
      count = visibleRows.length;
    }
    totalElement.textContent = String(count);
  }


  function csvEscape(value) {
    const text = String(value ?? '').replace(/\r?\n|\r/g, ' ').trim();
    const safeText = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
    return `"${safeText.replace(/"/g, '""')}"`;
  }

  function getVisibleTableColumns(table) {
    if (!table || !table.tHead || !table.tHead.rows.length) {
      return [];
    }
    return Array.from(table.tHead.rows[0].cells)
      .filter((header) => header.dataset.column && header.style.display !== 'none')
      .map((header) => ({
        key: header.dataset.column,
        label: (header.textContent || '').trim(),
      }));
  }

  function getDisplayedRows(table) {
    if (!table || !table.tBodies.length) {
      return [];
    }
    return Array.from(table.tBodies[0].rows).filter((row) => row.style.display !== 'none');
  }

  function escapeColumnSelector(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') {
      return window.CSS.escape(value);
    }
    return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  }

  function getCellExportText(cell) {
    if (!cell) {
      return '';
    }
    const mutedPlaceholder = cell.querySelector('.text-muted');
    if (mutedPlaceholder && (cell.textContent || '').trim() === (mutedPlaceholder.textContent || '').trim()) {
      return '';
    }
    return (cell.innerText || cell.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function buildAssetsCsv(table) {
    const columns = getVisibleTableColumns(table);
    const rows = getDisplayedRows(table);
    if (!columns.length) {
      return '';
    }
    const lines = [columns.map((column) => csvEscape(column.label)).join(',')];
    rows.forEach((row) => {
      const values = columns.map((column) => {
        const cell = row.querySelector(`td[data-column="${escapeColumnSelector(column.key)}"]`);
        return csvEscape(getCellExportText(cell));
      });
      lines.push(values.join(','));
    });
    return lines.join('\r\n');
  }

  function downloadCsv(csv, filename) {
    const blob = new Blob([`\ufeff${csv}`], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function initialiseCsvExport(table) {
    const button = document.querySelector('[data-export-csv="assets-table"]');
    if (!button || !table) {
      return;
    }
    button.addEventListener('click', () => {
      const csv = buildAssetsCsv(table);
      if (!csv) {
        if (window.__portalToast && typeof window.__portalToast.show === 'function') {
          window.__portalToast.show('No asset columns are available to export.', { variant: 'error' });
        } else {
          window.alert('No asset columns are available to export.');
        }
        return;
      }
      const timestamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
      downloadCsv(csv, `assets-${timestamp}.csv`);
    });
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

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function initialiseTrayChat() {
    document.querySelectorAll('[data-tray-chat]').forEach((button) => {
      button.addEventListener('click', async () => {
        const deviceUid = button.getAttribute('data-device-uid');
        if (!deviceUid) {
          return;
        }
        const row = button.closest('[data-asset-id]');
        const assetName = (button.getAttribute('data-asset-name') || (row ? row.getAttribute('data-asset-name') : '') || 'Asset').trim();
        const chatSubject = `${assetName} - Helpdesk chat`;
        button.disabled = true;
        try {
          const response = await fetch(`/api/tray/${encodeURIComponent(deviceUid)}/chat/start`, {
            method: 'POST',
            headers: {
              'Accept': 'application/json',
              'Content-Type': 'application/json',
              'X-CSRF-Token': getCsrfToken(),
            },
            body: JSON.stringify({ subject: chatSubject }),
          });
          if (!response.ok) {
            throw new Error(await response.text());
          }
          const data = await response.json();
          if (data.room_id) {
            window.location.href = `/chat/${encodeURIComponent(data.room_id)}`;
            return;
          }
          throw new Error('Chat room was not returned.');
        } catch (error) {
          console.error('Failed to open tray chat', error);
          if (window.__portalToast && typeof window.__portalToast.show === 'function') {
            window.__portalToast.show('Failed to open chat. Please try again.', { variant: 'error' });
          } else {
            window.alert('Failed to open chat. Please try again.');
          }
          button.disabled = false;
        }
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
          if (table) {
            table.dispatchEvent(new CustomEvent('table:rows-updated'));
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
    initialiseCsvExport(table);
    initialiseDeletion(table);
    initialiseTrayChat();
    updateVisibleCount(table);

    if (table) {
      table.addEventListener('table:render', (event) => {
        updateVisibleCount(table, event.detail || {});
      });
    }

    if (searchInput) {
      searchInput.addEventListener('input', () => {
        window.requestAnimationFrame(() => updateVisibleCount(table));
      });
    }
  });
})();
