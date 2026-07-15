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

  function initialiseCustomFieldsEditing() {
    const modal = document.getElementById('asset-fields-modal');
    const form = document.querySelector('[data-asset-fields-form]');
    const assetNameElement = document.querySelector('[data-asset-name]');
    const fieldsContainer = document.querySelector('[data-custom-fields-container]');
    const trayNotificationCheckbox = document.getElementById('asset-send-tray-notification');
    const noFieldsMessage = document.getElementById('no-fields-message');
    let currentAssetId = null;
    let currentAssetName = '';
    let fieldDefinitions = [];

    async function loadFieldDefinitions() {
      try {
        const response = await fetch('/asset-custom-fields/definitions');
        if (!response.ok) throw new Error('Failed to load field definitions');
        fieldDefinitions = await response.json();
        return fieldDefinitions;
      } catch (error) {
        console.error('Error loading field definitions:', error);
        return [];
      }
    }

    async function loadAssetFieldValues(assetId) {
      try {
        const response = await fetch(`/assets/${assetId}/custom-fields`);
        if (!response.ok) throw new Error('Failed to load asset field values');
        return await response.json();
      } catch (error) {
        console.error('Error loading asset field values:', error);
        return [];
      }
    }

    function renderFieldInputs(definitions, values) {
      if (!definitions || definitions.length === 0) {
        fieldsContainer.innerHTML = '';
        fieldsContainer.style.display = 'none';
        noFieldsMessage.style.display = 'block';
        return;
      }

      fieldsContainer.style.display = 'block';
      noFieldsMessage.style.display = 'none';

      const valueMap = {};
      values.forEach(v => {
        valueMap[v.field_definition_id] = v.value;
      });

      fieldsContainer.innerHTML = definitions.map(def => {
        const value = valueMap[def.id] || '';
        const inputId = `field-${def.id}`;

        let inputHtml = '';
        switch (def.field_type) {
          case 'text':
          case 'url':
            inputHtml = `<input type="${def.field_type === 'url' ? 'url' : 'text'}" id="${inputId}" name="field_${def.id}" class="input" value="${escapeHtml(value)}">`;
            break;
          case 'image':
            inputHtml = `<input type="url" id="${inputId}" name="field_${def.id}" class="input" placeholder="Image URL" value="${escapeHtml(value)}">`;
            break;
          case 'checkbox':
            const checked = value === true || value === 'true' || value === '1' ? 'checked' : '';
            inputHtml = `<input type="checkbox" id="${inputId}" name="field_${def.id}" ${checked}>`;
            break;
          case 'date':
            inputHtml = `<input type="date" id="${inputId}" name="field_${def.id}" class="input" value="${value || ''}">`;
            break;
        }

        return `
          <div class="form-group">
            <label for="${inputId}" class="form-label">${escapeHtml(def.name)}</label>
            ${inputHtml}
          </div>
        `;
      }).join('');
    }

    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    async function openModal(assetId, assetName) {
      currentAssetId = assetId;
      currentAssetName = assetName;
      
      assetNameElement.textContent = `Asset: ${assetName}`;
      
      const [definitions, values] = await Promise.all([
        loadFieldDefinitions(),
        loadAssetFieldValues(assetId)
      ]);

      renderFieldInputs(definitions, values);
      modal.style.display = 'flex';
    }

    function closeModal() {
      modal.style.display = 'none';
      currentAssetId = null;
      currentAssetName = '';
      if (trayNotificationCheckbox instanceof HTMLInputElement) {
        trayNotificationCheckbox.checked = false;
      }
    }

    // Event listeners for edit buttons
    document.addEventListener('click', (e) => {
      const editBtn = e.target.closest('[data-edit-asset]');
      if (editBtn) {
        const assetId = editBtn.dataset.editAsset;
        const row = editBtn.closest('tr');
        const assetName = row ? row.querySelector('[data-column="name"]')?.textContent?.trim() : 'Unknown';
        openModal(assetId, assetName);
      }
    });

    // Close modal buttons
    document.querySelectorAll('[data-asset-modal-close]').forEach(btn => {
      btn.addEventListener('click', closeModal);
    });

    // Close on escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modal.style.display === 'flex') {
        closeModal();
      }
    });

    // Form submission
    form?.addEventListener('submit', async (e) => {
      e.preventDefault();
      
      if (!currentAssetId) return;

      const formData = new FormData(form);
      const fields = fieldDefinitions.map(def => {
        let value = null;
        const fieldName = `field_${def.id}`;
        
        if (def.field_type === 'checkbox') {
          value = formData.has(fieldName);
        } else {
          value = formData.get(fieldName) || null;
        }

        return {
          field_definition_id: def.id,
          value: value
        };
      });

      try {
        const shouldNotifyTray = trayNotificationCheckbox instanceof HTMLInputElement && trayNotificationCheckbox.checked;
        const endpoint = shouldNotifyTray
          ? `/assets/${currentAssetId}/custom-fields?send_tray_notification=1`
          : `/assets/${currentAssetId}/custom-fields`;
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(fields)
        });

        if (!response.ok) throw new Error('Failed to save custom fields');

        if (window.__portalToast && typeof window.__portalToast.show === 'function') {
          window.__portalToast.show('Custom fields saved successfully', { variant: 'success' });
        } else {
          window.alert('Custom fields saved successfully');
        }

        closeModal();
      } catch (error) {
        console.error('Error saving custom fields:', error);
        if (window.__portalToast && typeof window.__portalToast.show === 'function') {
          window.__portalToast.show('Failed to save custom fields. Please try again.', { variant: 'error' });
        } else {
          window.alert('Failed to save custom fields. Please try again.');
        }
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const table = document.getElementById('assets-table');
    const searchInput = document.getElementById('asset-search');

    initialiseColumnControls(table);
    initialiseCsvExport(table);
    initialiseDeletion(table);
    initialiseTrayChat();
    initialiseCustomFieldsEditing();
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
