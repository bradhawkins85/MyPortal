(function () {
  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getMetaContent(name) {
    const meta = document.querySelector(`meta[name="${name}"]`);
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function getCsrfToken() {
    const metaToken = getMetaContent('csrf-token');
    if (metaToken) {
      return metaToken;
    }
    return getCookie('myportal_session_csrf');
  }

  async function requestJson(url, options) {
    const config = options || {};
    const csrfToken = getCsrfToken();
    const headers = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
      ...(config.headers || {}),
    };
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers,
      ...config,
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        if (data && data.detail) {
          detail = Array.isArray(data.detail)
            ? data.detail.map((entry) => entry.msg || entry).join(', ')
            : data.detail;
        }
      } catch (error) {
        /* ignore json parse errors */
      }
      throw new Error(detail);
    }
    return response.status !== 204 ? response.json() : null;
  }

  async function requestForm(url, formData) {
    const csrfToken = getCsrfToken();
    const headers = {};
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
    headers['Accept'] = 'application/json';
    headers['X-Requested-With'] = 'XMLHttpRequest';
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
      credentials: 'same-origin',
      headers,
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        if (data && data.detail) {
          detail = Array.isArray(data.detail)
            ? data.detail.map((entry) => entry.msg || entry).join(', ')
            : data.detail;
        }
      } catch (error) {
        /* ignore json parse errors */
      }
      throw new Error(detail);
    }
    return response.status !== 204 ? response.json() : null;
  }

  function setButtonProcessing(button, isProcessing) {
    if (!button) {
      return;
    }
    const label = button.querySelector('[data-button-label]');
    if (label) {
      const defaultLabel = button.dataset.defaultLabel || label.textContent || '';
      if (!button.dataset.defaultLabel) {
        button.dataset.defaultLabel = defaultLabel;
      }
      label.textContent = isProcessing ? 'Reprocessing AI summary and AI tags…' : button.dataset.defaultLabel;
    }
    button.classList.toggle('button--processing', Boolean(isProcessing));
    if (isProcessing) {
      button.setAttribute('aria-busy', 'true');
      button.disabled = true;
    } else {
      button.removeAttribute('aria-busy');
      button.disabled = false;
    }
  }

  function updateTicketAiStatus(button, message, isError) {
    const card = button ? button.closest('[data-ticket-ai-card]') : null;
    const status = card ? card.querySelector('[data-ticket-ai-status]') : null;
    if (!status) {
      if (message && isError) {
        alert(message);
      }
      return;
    }
    status.textContent = message || '';
    status.hidden = !message;
    status.classList.toggle('form-help--error', Boolean(isError));
  }

  function bindTicketAiRefresh() {
    const buttons = document.querySelectorAll('[data-ticket-ai-refresh]');
    if (!buttons.length) {
      return;
    }

    buttons.forEach((button) => {
      button.addEventListener('click', async (event) => {
        event.preventDefault();
        const ticketId = button.getAttribute('data-ticket-id');
        if (!ticketId || button.disabled) {
          return;
        }

        try {
          setButtonProcessing(button, true);
          updateTicketAiStatus(button, 'Requesting AI regeneration. You can continue working while we update the summary.', false);
          await requestJson(`/admin/tickets/${ticketId}/ai/reprocess`, {
            method: 'POST',
            body: JSON.stringify({}),
          });
          updateTicketAiStatus(
            button,
            'AI summary and tags will be regenerated shortly. Refresh the ticket in a moment to review the updates.',
            false,
          );
        } catch (error) {
          const message = error instanceof Error ? error.message : 'Unable to refresh AI summary and tags.';
          updateTicketAiStatus(button, message, true);
        } finally {
          setButtonProcessing(button, false);
        }
      });
    });
  }

  function bindSyncroTicketImportForms() {
    const forms = document.querySelectorAll('[data-syncro-ticket-import]');
    if (!forms.length) {
      return;
    }

    const statusRegion = document.querySelector('[data-syncro-ticket-import-status]');

    const renderStatus = (message, isError) => {
      if (!statusRegion) {
        if (message) {
          alert(message);
        }
        return;
      }
      statusRegion.innerHTML = '';
      if (!message) {
        statusRegion.hidden = true;
        return;
      }
      const alertBox = document.createElement('div');
      alertBox.className = isError ? 'alert alert--error' : 'alert';
      alertBox.setAttribute('role', isError ? 'alert' : 'status');
      alertBox.textContent = message;
      statusRegion.appendChild(alertBox);
      statusRegion.hidden = false;
    };

    forms.forEach((form) => {
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const mode = form.getAttribute('data-mode') || 'single';
        const submitButton = form.querySelector('button[type="submit"]');
        if (submitButton) {
          submitButton.disabled = true;
        }
        const payload = { mode };
        const formData = new FormData(form);

        const parseInteger = (value, errorMessage) => {
          const parsed = Number(value);
          if (!Number.isInteger(parsed) || parsed <= 0) {
            throw new Error(errorMessage);
          }
          return parsed;
        };

        try {
          if (mode === 'single') {
            payload.ticketId = parseInteger(formData.get('ticketId'), 'Enter a valid Syncro ticket ID.');
          } else if (mode === 'range') {
            payload.startId = parseInteger(formData.get('startId'), 'Enter a valid starting ticket ID.');
            payload.endId = parseInteger(formData.get('endId'), 'Enter a valid ending ticket ID.');
            if (payload.endId < payload.startId) {
              throw new Error('End ticket ID must be greater than or equal to the start ID.');
            }
          }

          renderStatus('Import in progress…', false);
          const response = await requestJson('/admin/syncro/import-tickets', {
            method: 'POST',
            body: JSON.stringify(payload),
          });
          const fetched = Number(response?.fetched ?? 0);
          const created = Number(response?.created ?? 0);
          const updated = Number(response?.updated ?? 0);
          const skipped = Number(response?.skipped ?? 0);
          renderStatus(
            `Imported ${fetched} ticket${fetched === 1 ? '' : 's'} (created ${created}, updated ${updated}, skipped ${skipped}).`,
            false,
          );
        } catch (error) {
          const message = error instanceof Error ? error.message : 'Unable to import tickets.';
          renderStatus(message, true);
        } finally {
          if (submitButton) {
            submitButton.disabled = false;
          }
        }
      });
    });
  }

  function bindSyncroCompanyImportForm() {
    const form = document.querySelector('[data-syncro-company-import]');
    if (!form) {
      return;
    }

    const statusRegion = document.querySelector('[data-syncro-company-import-status]');

    const renderStatus = (message, isError) => {
      if (!statusRegion) {
        if (message) {
          alert(message);
        }
        return;
      }
      statusRegion.innerHTML = '';
      if (!message) {
        statusRegion.hidden = true;
        return;
      }
      const alertBox = document.createElement('div');
      alertBox.className = isError ? 'alert alert--error' : 'alert';
      alertBox.setAttribute('role', isError ? 'alert' : 'status');
      alertBox.textContent = message;
      statusRegion.appendChild(alertBox);
      statusRegion.hidden = false;
    };

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) {
        submitButton.disabled = true;
      }
      try {
        renderStatus('Import in progress…', false);
        const response = await requestJson('/admin/syncro/import-companies', {
          method: 'POST',
          body: JSON.stringify({}),
        });
        const status = String(response?.status ?? '').toLowerCase();
        if (status === 'queued') {
          const message = response?.message || 'Syncro company import queued. Monitor the webhook monitor for updates.';
          renderStatus(message, false);
          return;
        }
        const fetched = Number(response?.fetched ?? 0);
        const created = Number(response?.created ?? 0);
        const updated = Number(response?.updated ?? 0);
        const skipped = Number(response?.skipped ?? 0);
        renderStatus(
          `Imported ${fetched} compan${fetched === 1 ? 'y' : 'ies'} (created ${created}, updated ${updated}, skipped ${skipped}).`,
          false,
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unable to import companies.';
        renderStatus(message, true);
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
        }
      }
    });
  }

  function bindTicketBulkDelete() {
    const form = document.querySelector('[data-bulk-delete-form]');
    const table = document.querySelector('[data-bulk-delete-table]');
    if (!form || !table) {
      return;
    }

    const submitButton = form.querySelector('[data-bulk-delete-submit]');
    const countLabel = form.querySelector('[data-bulk-delete-count]');
    const selectAll = table.querySelector('[data-bulk-select-all]');
    const filterInputs = document.querySelectorAll('[data-table-filter="tickets-table"]');

    const getRowCheckboxes = () =>
      Array.from(table.querySelectorAll('input[type="checkbox"][data-bulk-delete-checkbox]'));

    const getVisibleCheckboxes = () =>
      getRowCheckboxes().filter((checkbox) => {
        const row = checkbox.closest('tr');
        if (!row || checkbox.disabled) {
          return false;
        }
        return row.dataset.filterHidden !== 'true';
      });

    const updateState = () => {
      const selected = getRowCheckboxes().filter((checkbox) => checkbox.checked);
      const visible = getVisibleCheckboxes();
      if (submitButton) {
        submitButton.disabled = selected.length === 0;
      }
      if (countLabel) {
        const count = selected.length;
        countLabel.textContent = `${count} selected`;
        countLabel.hidden = count === 0;
      }
      if (selectAll) {
        if (!visible.length) {
          selectAll.checked = false;
          selectAll.indeterminate = false;
        } else {
          const selectedVisible = visible.filter((checkbox) => checkbox.checked);
          selectAll.checked = selectedVisible.length === visible.length;
          selectAll.indeterminate =
            selectedVisible.length > 0 && selectedVisible.length < visible.length;
        }
      }
    };

    getRowCheckboxes().forEach((checkbox) => {
      checkbox.addEventListener('change', updateState);
    });

    if (selectAll) {
      selectAll.addEventListener('change', () => {
        const visibleCheckboxes = getVisibleCheckboxes();
        visibleCheckboxes.forEach((checkbox) => {
          checkbox.checked = selectAll.checked;
        });
        updateState();
      });
    }

    if (filterInputs.length) {
      filterInputs.forEach((input) => {
        input.addEventListener('input', () => {
          window.requestAnimationFrame(updateState);
        });
      });
    }

    table.addEventListener('table:rows-updated', () => {
      window.requestAnimationFrame(updateState);
    });

    form.addEventListener('submit', (event) => {
      const selected = getRowCheckboxes().filter((checkbox) => checkbox.checked);
      const count = selected.length;
      if (!count) {
        event.preventDefault();
        return;
      }
      const confirmationMessage =
        count === 1
          ? 'Delete the selected ticket? This cannot be undone.'
          : `Delete ${count} selected tickets? This cannot be undone.`;
      if (!window.confirm(confirmationMessage)) {
        event.preventDefault();
      }
    });

    updateState();
  }

  function bindTicketStatusAutoSubmit() {
    const forms = document.querySelectorAll('[data-ticket-status-form]');
    if (!forms.length) {
      return;
    }

    forms.forEach((form) => {
      const select = form.querySelector('[data-ticket-status-select]');
      if (!select) {
        return;
      }

      let hasSubmitted = false;

      form.addEventListener('submit', () => {
        hasSubmitted = true;
        select.disabled = true;
        form.classList.add('inline-form--submitting');
      });

      select.addEventListener('change', () => {
        if (hasSubmitted) {
          return;
        }
        hasSubmitted = true;
        if (typeof form.requestSubmit === 'function') {
          form.requestSubmit();
        } else {
          form.submit();
        }
      });
    });
  }

  function parsePermissions(value) {
    return value
      .split(',')
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }

  function bindRoleForm() {
    const form = document.getElementById('role-form');
    if (!form) {
      return;
    }
    const idField = form.querySelector('#role-id');
    const nameField = form.querySelector('#role-name');
    const descriptionField = form.querySelector('#role-description');
    const permissionsField = form.querySelector('#role-permissions');

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const roleId = idField.value;
      const payload = {
        name: nameField.value.trim(),
        description: descriptionField.value.trim() || null,
        permissions: parsePermissions(permissionsField.value),
      };
      const method = roleId ? 'PATCH' : 'POST';
      const url = roleId ? `/roles/${roleId}` : '/roles';
      try {
        await requestJson(url, { method, body: JSON.stringify(payload) });
        window.location.reload();
      } catch (error) {
        alert(`Unable to save role: ${error.message}`);
      }
    });

    const resetButton = form.querySelector('[data-role-reset]');
    if (resetButton) {
      resetButton.addEventListener('click', () => {
        idField.value = '';
        nameField.value = '';
        descriptionField.value = '';
        permissionsField.value = '';
        nameField.focus();
      });
    }

    document.querySelectorAll('[data-role-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        const row = button.closest('tr');
        if (!row) {
          return;
        }
        idField.value = row.dataset.roleId || '';
        nameField.value = row.dataset.roleName || '';
        descriptionField.value = row.dataset.roleDescription || '';
        try {
          const permissions = JSON.parse(row.dataset.rolePermissions || '[]');
          permissionsField.value = Array.isArray(permissions) ? permissions.join(', ') : '';
        } catch (error) {
          permissionsField.value = '';
        }
        nameField.focus();
      });
    });

    document.querySelectorAll('[data-role-delete]').forEach((button) => {
      button.addEventListener('click', async () => {
        const row = button.closest('tr');
        if (!row) {
          return;
        }
        const roleId = row.dataset.roleId;
        if (!roleId) {
          return;
        }
        if (!confirm('Delete this role? This action cannot be undone.')) {
          return;
        }
        try {
          await requestJson(`/roles/${roleId}`, { method: 'DELETE' });
          window.location.reload();
        } catch (error) {
          alert(`Unable to delete role: ${error.message}`);
        }
      });
    });
  }

  function bindCompanyAssignmentControls() {
    document.querySelectorAll('[data-company-permission]').forEach((input) => {
      input.addEventListener('change', async () => {
        const { companyId, userId, field } = input.dataset;
        if (!companyId || !userId || !field) {
          return;
        }
        const formData = new FormData();
        formData.append('field', field);
        formData.append('value', input.checked ? '1' : '0');
        input.disabled = true;
        try {
          await requestForm(`/admin/companies/assignment/${companyId}/${userId}/permission`, formData);
        } catch (error) {
          input.checked = !input.checked;
          alert(`Unable to update permission: ${error.message}`);
        } finally {
          input.disabled = false;
        }
      });
    });

    document.querySelectorAll('[data-staff-permission]').forEach((select) => {
      select.addEventListener('change', async () => {
        const { companyId, userId } = select.dataset;
        if (!companyId || !userId) {
          return;
        }
        const formData = new FormData();
        formData.append('permission', select.value);
        select.disabled = true;
        try {
          await requestForm(`/admin/companies/assignment/${companyId}/${userId}/staff-permission`, formData);
        } catch (error) {
          alert(`Unable to update staff permission: ${error.message}`);
        } finally {
          select.disabled = false;
        }
      });
    });

    document.querySelectorAll('[data-membership-role]').forEach((select) => {
      select.addEventListener('change', async () => {
        const { companyId, userId } = select.dataset;
        if (!companyId || !userId) {
          return;
        }
        const previousValue = select.dataset.currentRole || '';
        const roleId = select.value;
        if (!roleId) {
          select.value = previousValue;
          return;
        }
        const formData = new FormData();
        formData.append('roleId', roleId);
        select.disabled = true;
        try {
          await requestForm(`/admin/companies/assignment/${companyId}/${userId}/role`, formData);
          select.dataset.currentRole = roleId;
        } catch (error) {
          select.value = previousValue;
          alert(`Unable to update role: ${error.message}`);
        } finally {
          select.disabled = false;
        }
      });
    });

    document.querySelectorAll('[data-remove-assignment]').forEach((button) => {
      button.addEventListener('click', async () => {
        const { companyId, userId } = button.dataset;
        if (!companyId || !userId) {
          return;
        }
        if (!confirm('Remove this membership? The user will immediately lose access.')) {
          return;
        }
        const row = button.closest('tr');
        const formData = new FormData();
        button.disabled = true;
        try {
          await requestForm(`/admin/companies/assignment/${companyId}/${userId}/remove`, formData);
          if (row) {
            row.remove();
          }
        } catch (error) {
          alert(`Unable to remove membership: ${error.message}`);
        } finally {
          button.disabled = false;
        }
      });
    });

  }

  function bindApiKeyCopyButtons() {
    document.querySelectorAll('[data-copy-api-key]').forEach((button) => {
      const value = button.getAttribute('data-copy-api-key');
      if (!value) {
        return;
      }
      button.addEventListener('click', async () => {
        const originalText = button.textContent;
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(value);
          } else {
            const input = document.createElement('input');
            input.type = 'text';
            input.value = value;
            input.setAttribute('aria-hidden', 'true');
            input.style.position = 'absolute';
            input.style.left = '-1000px';
            document.body.appendChild(input);
            input.select();
            document.execCommand('copy');
            document.body.removeChild(input);
          }
          button.textContent = 'Copied';
          setTimeout(() => {
            button.textContent = originalText;
          }, 2000);
        } catch (error) {
          alert('Unable to copy API key. Please copy it manually.');
        }
      });
    });
  }

  function bindConfirmationButtons() {
    document.querySelectorAll('[data-confirm]').forEach((element) => {
      element.addEventListener('click', (event) => {
        const message = element.getAttribute('data-confirm') || 'Are you sure?';
        if (!window.confirm(message)) {
          event.preventDefault();
        }
      });
    });
  }

  function bindModal({ modalId, triggerSelector }) {
    const modal = document.getElementById(modalId);
    const triggerButtons = triggerSelector
      ? Array.from(document.querySelectorAll(triggerSelector))
      : [];

    if (!modal || triggerButtons.length === 0) {
      return;
    }

    const focusableSelector =
      'a[href], button:not([disabled]), textarea, input:not([type="hidden"]):not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    let activeTrigger = null;

    function getFocusableElements() {
      return Array.from(modal.querySelectorAll(focusableSelector)).filter((element) => {
        if (element.hasAttribute('disabled')) {
          return false;
        }
        if (element.getAttribute('aria-hidden') === 'true') {
          return false;
        }
        return element.offsetParent !== null;
      });
    }

    function focusFirstElement() {
      const [firstFocusable] = getFocusableElements();
      if (firstFocusable && typeof firstFocusable.focus === 'function') {
        firstFocusable.focus();
      }
    }

    function handleKeydown(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== 'Tab') {
        return;
      }

      const focusable = getFocusableElements();
      if (!focusable.length) {
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const currentActive = document.activeElement;

      if (event.shiftKey) {
        if (currentActive === first) {
          event.preventDefault();
          last.focus();
        }
      } else if (currentActive === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function openModal(trigger) {
      activeTrigger = trigger instanceof HTMLElement ? trigger : null;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      if (activeTrigger) {
        activeTrigger.setAttribute('aria-expanded', 'true');
      }
      document.addEventListener('keydown', handleKeydown);
      focusFirstElement();
    }

    function closeModal() {
      modal.classList.remove('is-visible');
      modal.hidden = true;
      modal.setAttribute('aria-hidden', 'true');
      document.removeEventListener('keydown', handleKeydown);

      if (activeTrigger) {
        activeTrigger.setAttribute('aria-expanded', 'false');
        if (typeof activeTrigger.focus === 'function') {
          activeTrigger.focus();
        }
      }
      activeTrigger = null;
    }

    triggerButtons.forEach((button) => {
      button.setAttribute('aria-expanded', 'false');
      button.addEventListener('click', (event) => {
        event.preventDefault();
        openModal(button);
      });
    });

    modal.addEventListener('click', (event) => {
      if (event.target === modal) {
        closeModal();
      }
    });

    modal.querySelectorAll('[data-modal-close]').forEach((closeButton) => {
      closeButton.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal();
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindSyncroTicketImportForms();
    bindSyncroCompanyImportForm();
    bindTicketBulkDelete();
    bindTicketStatusAutoSubmit();
    bindTicketAiRefresh();
    bindRoleForm();
    bindCompanyAssignmentControls();
    bindApiKeyCopyButtons();
    bindConfirmationButtons();
    bindModal({ modalId: 'add-company-modal', triggerSelector: '[data-add-company-modal-open]' });
    bindModal({ modalId: 'create-ticket-modal', triggerSelector: '[data-create-ticket-modal-open]' });
  });
})();
