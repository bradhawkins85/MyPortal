(function () {
  'use strict';

  const modal = document.getElementById('field-modal');
  const form = document.querySelector('[data-field-form]');
  const modalTitle = document.querySelector('[data-modal-title]');
  const fieldsContainer = document.querySelector('[data-fields-container]');
  const emptyState = document.querySelector('[data-empty-state]');
  const fieldsTable = document.querySelector('[data-fields-table]');
  const fieldsTbody = document.querySelector('[data-fields-tbody]');
  let editingFieldId = null;

  // Fetch and display fields
  async function loadFields() {
    try {
      const response = await fetch('/asset-custom-fields/definitions');
      if (!response.ok) throw new Error('Failed to load fields');
      
      const fields = await response.json();
      
      if (fields.length === 0) {
        emptyState.style.display = 'block';
        fieldsTable.style.display = 'none';
      } else {
        emptyState.style.display = 'none';
        fieldsTable.style.display = 'block';
        renderFields(fields);
      }
    } catch (error) {
      console.error('Error loading fields:', error);
      showToast('Failed to load custom fields', 'error');
    }
  }

  function renderFields(fields) {
    fieldsTbody.innerHTML = '';
    
    fields.forEach(field => {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td data-label="Name">${escapeHtml(field.name)}</td>
        <td data-label="Type">${formatFieldType(field.field_type)}</td>
        <td data-label="Display Order">${field.display_order}</td>
        <td class="table__actions">
          <button type="button" class="button button--ghost button--small" data-edit-field="${field.id}">
            Edit
          </button>
          <button type="button" class="button button--danger button--small" data-delete-field="${field.id}">
            Delete
          </button>
        </td>
      `;
      fieldsTbody.appendChild(row);
    });
  }

  function formatFieldType(type) {
    const types = {
      text: 'Text',
      image: 'Image (URL)',
      checkbox: 'Checkbox',
      url: 'URL',
      date: 'Date'
    };
    return types[type] || type;
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function showToast(message, type = 'info') {
    // Simple toast notification (can be enhanced)
    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;
    toast.textContent = message;
    toast.style.cssText = 'position: fixed; bottom: 20px; right: 20px; padding: 16px 24px; background: #333; color: white; border-radius: 4px; z-index: 10000;';
    document.body.appendChild(toast);
    setTimeout(() => {
      toast.remove();
    }, 3000);
  }

  // Modal controls
  function openModal(title = 'Add Custom Field', fieldId = null) {
    modalTitle.textContent = title;
    editingFieldId = fieldId;
    modal.style.display = 'flex';
    
    if (fieldId) {
      loadFieldData(fieldId);
    } else {
      form.reset();
    }
  }

  function closeModal() {
    modal.style.display = 'none';
    form.reset();
    editingFieldId = null;
  }

  async function loadFieldData(fieldId) {
    try {
      const response = await fetch(`/asset-custom-fields/definitions/${fieldId}`);
      if (!response.ok) throw new Error('Failed to load field data');
      
      const field = await response.json();
      document.getElementById('field-name').value = field.name;
      document.getElementById('field-type').value = field.field_type;
      document.getElementById('field-order').value = field.display_order;
    } catch (error) {
      console.error('Error loading field data:', error);
      showToast('Failed to load field data', 'error');
      closeModal();
    }
  }

  // Event listeners
  document.querySelector('[data-add-field]')?.addEventListener('click', () => {
    openModal('Add Custom Field');
  });

  document.querySelectorAll('[data-modal-close]').forEach(btn => {
    btn.addEventListener('click', closeModal);
  });

  // Handle form submission
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const formData = new FormData(form);
    const data = {
      name: formData.get('name'),
      field_type: formData.get('field_type'),
      display_order: parseInt(formData.get('display_order'), 10) || 0
    };

    try {
      let response;
      if (editingFieldId) {
        // Update existing field
        response = await fetch(`/asset-custom-fields/definitions/${editingFieldId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data)
        });
      } else {
        // Create new field
        response = await fetch('/asset-custom-fields/definitions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data)
        });
      }

      if (!response.ok) throw new Error('Failed to save field');

      showToast(editingFieldId ? 'Field updated successfully' : 'Field created successfully', 'success');
      closeModal();
      loadFields();
    } catch (error) {
      console.error('Error saving field:', error);
      showToast('Failed to save field', 'error');
    }
  });

  // Handle edit and delete buttons (using event delegation)
  fieldsTbody?.addEventListener('click', async (e) => {
    const editBtn = e.target.closest('[data-edit-field]');
    const deleteBtn = e.target.closest('[data-delete-field]');

    if (editBtn) {
      const fieldId = editBtn.dataset.editField;
      openModal('Edit Custom Field', fieldId);
    }

    if (deleteBtn) {
      const fieldId = deleteBtn.dataset.deleteField;
      if (confirm('Are you sure you want to delete this field? All associated data will be lost.')) {
        try {
          const response = await fetch(`/asset-custom-fields/definitions/${fieldId}`, {
            method: 'DELETE'
          });

          if (!response.ok) throw new Error('Failed to delete field');

          showToast('Field deleted successfully', 'success');
          loadFields();
        } catch (error) {
          console.error('Error deleting field:', error);
          showToast('Failed to delete field', 'error');
        }
      }
    }
  });

  // Close modal on escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal.style.display === 'flex') {
      closeModal();
    }
  });

  // Load fields on page load
  loadFields();
})();
