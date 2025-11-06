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
        const response = await fetch(`/assets/${currentAssetId}/custom-fields`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(fields)
        });

        if (!response.ok) throw new Error('Failed to save custom fields');

        // Show success message
        const toast = document.createElement('div');
        toast.style.cssText = 'position: fixed; bottom: 20px; right: 20px; padding: 16px 24px; background: #10b981; color: white; border-radius: 4px; z-index: 10000;';
        toast.textContent = 'Custom fields saved successfully';
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);

        closeModal();
      } catch (error) {
        console.error('Error saving custom fields:', error);
        alert('Failed to save custom fields. Please try again.');
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const table = document.getElementById('assets-table');
    const searchInput = document.getElementById('asset-search');

    initialiseColumnControls(table);
    initialiseDeletion(table);
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
