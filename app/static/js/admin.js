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

  function parseJsonScript(elementId, fallbackValue) {
    if (!elementId) {
      return fallbackValue;
    }
    const element = document.getElementById(elementId);
    if (!element) {
      return fallbackValue;
    }
    const textContent = element.textContent || element.innerText || '';
    if (!textContent || !textContent.trim()) {
      return fallbackValue;
    }
    try {
      return JSON.parse(textContent);
    } catch (error) {
      return fallbackValue;
    }
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

  const tableRefreshHandlers = Object.create(null);
  const tableRefreshControllers = new WeakMap();

  function registerTableRefreshHandler(name, handler) {
    if (!name || typeof handler !== 'function') {
      return;
    }
    const key = String(name).trim().toLowerCase();
    if (!key) {
      return;
    }
    tableRefreshHandlers[key] = handler;
  }

  function getTableRefreshHandler(name) {
    if (!name) {
      return null;
    }
    const key = String(name).trim().toLowerCase();
    if (!key) {
      return null;
    }
    return tableRefreshHandlers[key] || null;
  }

  function parseRefreshTopics(value) {
    if (!value) {
      return new Set();
    }
    const topics = String(value)
      .split(',')
      .map((topic) => topic.trim().toLowerCase())
      .filter((topic) => topic.length > 0);
    return new Set(topics);
  }

  function shouldHandleRefresh(detail, topicSet) {
    if (!(topicSet instanceof Set) || topicSet.size === 0) {
      return true;
    }
    const detailTopics = Array.isArray(detail?.topics)
      ? detail.topics
          .map((topic) => String(topic || '').trim().toLowerCase())
          .filter((topic) => topic.length > 0)
      : [];
    if (detailTopics.length) {
      return detailTopics.some((topic) => topicSet.has(topic));
    }
    const reason = typeof detail?.reason === 'string' ? detail.reason.toLowerCase() : '';
    if (!reason) {
      return false;
    }
    return Array.from(topicSet).some((topic) => reason.includes(topic));
  }

  function setupTableRealtimeRefreshControllers() {
    const tables = document.querySelectorAll('[data-table][data-table-refresh-url]');
    tables.forEach((table) => {
      if (!(table instanceof HTMLElement)) {
        return;
      }
      if (tableRefreshControllers.has(table)) {
        return;
      }

      const endpoint = table.getAttribute('data-table-refresh-url');
      if (!endpoint) {
        return;
      }

      const handlerName = table.getAttribute('data-table-refresh-handler');
      const handler =
        getTableRefreshHandler(handlerName) ||
        getTableRefreshHandler(table.id || '') ||
        null;
      if (!handler) {
        return;
      }

      const topicSet = parseRefreshTopics(table.getAttribute('data-table-refresh-topics'));
      const successMessageAttr = table.getAttribute('data-table-refresh-success') || '';
      const errorMessageAttr = table.getAttribute('data-table-refresh-error') || '';
      const defaultSuccessMessage = successMessageAttr.trim();
      const defaultErrorMessage = errorMessageAttr.trim() || 'Unable to refresh data automatically.';

      let refreshing = false;
      let queued = false;
      let queuedDetail = null;

      function setBusy(isBusy) {
        if (isBusy) {
          table.setAttribute('aria-busy', 'true');
          table.dataset.refreshing = 'true';
        } else {
          table.removeAttribute('aria-busy');
          delete table.dataset.refreshing;
        }
      }

      async function perform(detail) {
        setBusy(true);
        let result;
        try {
          const response = await requestJson(endpoint);
          result =
            (await handler({
              table,
              endpoint,
              response,
              detail: detail || null,
              requestJson,
              requestForm,
              defaultSuccessMessage,
              defaultErrorMessage,
            })) || {};
        } catch (error) {
          console.error('Realtime table refresh failed', { endpoint, error });
          if (detail && typeof detail.showToast === 'function') {
            const message =
              (error && typeof error === 'object' && typeof error.userMessage === 'string' && error.userMessage.trim()) ||
              defaultErrorMessage;
            detail.showToast(message, { variant: 'error', autoHideMs: 6000 });
          }
          return;
        } finally {
          setBusy(false);
        }

        if (detail && typeof detail.showToast === 'function') {
          if (result && result.skipDefaultToast) {
            return;
          }
          const message =
            (result && typeof result.successMessage === 'string' && result.successMessage.trim()) ||
            defaultSuccessMessage;
          if (message) {
            detail.showToast(message, { variant: 'success', autoHideMs: 4000 });
          }
        }
      }

      async function flush(detail) {
        queuedDetail = detail || queuedDetail;
        if (refreshing) {
          queued = true;
          return;
        }
        refreshing = true;
        try {
          do {
            queued = false;
            const currentDetail = queuedDetail;
            queuedDetail = null;
            await perform(currentDetail);
          } while (queued);
        } finally {
          refreshing = false;
        }
      }

      function handleRefreshEvent(event) {
        const detail = event.detail || {};
        if (!shouldHandleRefresh(detail, topicSet)) {
          return;
        }
        event.preventDefault();
        flush(detail);
      }

      document.addEventListener('realtime:refresh', handleRefreshEvent);
      table.addEventListener('table:refresh-request', (event) => {
        flush(event.detail || null);
      });

      table.dataset.tableRefreshBound = 'true';
      tableRefreshControllers.set(table, { flush });
    });
  }

  function requestTableRefresh(target, detail) {
    let table = null;
    if (target instanceof HTMLElement) {
      table = target;
    } else if (typeof target === 'string') {
      table = document.getElementById(target) || document.querySelector(target);
    }
    if (!table) {
      return Promise.resolve(false);
    }
    const controller = tableRefreshControllers.get(table);
    if (!controller || typeof controller.flush !== 'function') {
      return Promise.resolve(false);
    }
    return controller.flush(detail || null).then(
      () => true,
      (error) => {
        console.error('Table refresh invocation failed', error);
        return false;
      },
    );
  }

  const existingTableRefreshApi = window.MyPortalTableRefresh || {};
  window.MyPortalTableRefresh = {
    ...existingTableRefreshApi,
    registerHandler: registerTableRefreshHandler,
    bind: setupTableRealtimeRefreshControllers,
    requestRefresh: requestTableRefresh,
  };

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
      const processingLabel = button.dataset.processingLabel || 'Reprocessing AI summary and AI tags…';
      label.textContent = isProcessing ? processingLabel : button.dataset.defaultLabel;
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

  function updateTicketDescriptionContent(html, raw) {
    const panel = document.querySelector('[data-ticket-description-panel]');
    if (panel && typeof panel.open === 'boolean') {
      panel.open = true;
    }
    const viewer = panel ? panel.querySelector('[data-ticket-description-viewer]') : document.querySelector('[data-ticket-description-viewer]');
    const emptyState = panel
      ? panel.querySelector('[data-ticket-description-empty]')
      : document.querySelector('[data-ticket-description-empty]');
    const input = panel
      ? panel.querySelector('[data-ticket-description-input]')
      : document.querySelector('[data-ticket-description-input]');

    const safeHtml = typeof html === 'string' ? html : '';
    const rawValue = typeof raw === 'string' ? raw : '';

    if (viewer) {
      viewer.innerHTML = safeHtml;
      viewer.hidden = !safeHtml;
    }
    if (emptyState) {
      emptyState.hidden = Boolean(safeHtml);
    }
    if (input) {
      input.value = rawValue;
    }
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

  function bindTicketAiReplaceDescription() {
    const buttons = document.querySelectorAll('[data-ticket-ai-replace-description]');
    if (!buttons.length) {
      return;
    }

    buttons.forEach((button) => {
      button.addEventListener('click', async (event) => {
        event.preventDefault();
        if (button.disabled) {
          return;
        }

        const ticketId = button.getAttribute('data-ticket-id');
        if (!ticketId) {
          return;
        }

        const confirmed = window.confirm(
          'Replace the current ticket description with the AI summary? This will overwrite any manual edits.',
        );
        if (!confirmed) {
          return;
        }

        try {
          setButtonProcessing(button, true);
          updateTicketAiStatus(
            button,
            'Replacing the ticket description with the AI summary. This may take a moment…',
            false,
          );
          const response = await requestJson(`/admin/tickets/${ticketId}/description/replace`, {
            method: 'POST',
            body: JSON.stringify({}),
          });
          const message = response && response.message
            ? response.message
            : 'Ticket description replaced with the AI summary.';
          const html = response && typeof response.descriptionHtml === 'string' ? response.descriptionHtml : '';
          const rawDescription = response && typeof response.description === 'string' ? response.description : '';
          updateTicketDescriptionContent(html, rawDescription);
          updateTicketAiStatus(button, message, false);
        } catch (error) {
          const message = error instanceof Error
            ? error.message
            : 'Unable to replace the ticket description.';
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

    if (table.dataset.bulkDeleteBound === 'true') {
      table.dispatchEvent(new CustomEvent('table:rows-updated'));
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

    table.addEventListener('change', (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) {
        return;
      }
      if (target.matches('input[type="checkbox"][data-bulk-delete-checkbox]')) {
        window.requestAnimationFrame(updateState);
      }
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
    table.dataset.bulkDeleteBound = 'true';
  }

  function bindTicketStatusAutoSubmit() {
    const forms = document.querySelectorAll('[data-ticket-status-form]');
    forms.forEach((form) => {
      if (form.dataset.ticketStatusBound === 'true') {
        return;
      }
      const select = form.querySelector('[data-ticket-status-select]');
      if (!select) {
        form.dataset.ticketStatusBound = 'true';
        return;
      }

      let hasSubmitted = false;

      form.dataset.ticketStatusBound = 'true';

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

  function bindIssueStatusAutoSubmit() {
    const forms = document.querySelectorAll('[data-issue-status-form]');
    forms.forEach((form) => {
      if (form.dataset.issueStatusBound === 'true') {
        return;
      }
      const select = form.querySelector('[data-issue-status-select]');
      if (!select) {
        form.dataset.issueStatusBound = 'true';
        return;
      }

      let hasSubmitted = false;

      form.dataset.issueStatusBound = 'true';

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

  const ticketTableStateCache = new WeakMap();
  let ticketRefreshHandlerRegistered = false;

  function getTicketTableState(table) {
    let state = ticketTableStateCache.get(table);
    if (state) {
      return state;
    }

    let statusOptions = [];
    try {
      const parsed = JSON.parse(table.dataset.ticketStatusOptions || '[]');
      if (Array.isArray(parsed)) {
        statusOptions = parsed.map((value) => String(value));
      }
    } catch (error) {
      statusOptions = [];
    }
    if (!statusOptions.length) {
      statusOptions = ['open', 'in_progress', 'pending', 'resolved', 'closed'];
    }

    const statusLabels = statusOptions.reduce((acc, value) => {
      acc[value] = value.replace(/_/g, ' ');
      return acc;
    }, {});

    const csrfToken = table.getAttribute('data-csrf-token') || '';
    const canBulkDelete = table.getAttribute('data-can-bulk-delete') === 'true';
    const bulkDeleteFormId = table.getAttribute('data-bulk-delete-form-id') || '';
    const emptyMessage = table.getAttribute('data-table-empty-label') || 'No records found.';

    const statsContainer = document.querySelector('[data-ticket-stats]');
    const statElements = {};
    if (statsContainer) {
      statsContainer.querySelectorAll('[data-ticket-stat]').forEach((element) => {
        const key = element.getAttribute('data-ticket-stat');
        if (key) {
          statElements[key] = element;
        }
      });
    }

    let dateFormatter;
    try {
      dateFormatter = new Intl.DateTimeFormat(undefined, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      });
    } catch (error) {
      dateFormatter = null;
    }

    function normaliseCounts(counts) {
      const output = {};
      if (!counts || typeof counts !== 'object') {
        return output;
      }
      Object.entries(counts).forEach(([key, value]) => {
        const normalisedKey = String(key || '').toLowerCase();
        if (!normalisedKey) {
          return;
        }
        const numeric = Number(value);
        output[normalisedKey] = Number.isFinite(numeric) ? numeric : 0;
      });
      return output;
    }

    function updateStats(counts, total) {
      const normalised = normaliseCounts(counts);
      if (statElements.open) {
        const value = Number(normalised.open ?? 0);
        statElements.open.textContent = String(Number.isFinite(value) ? value : 0);
      }
      if (statElements.in_progress) {
        const value = Number(normalised.in_progress ?? 0) + Number(normalised.pending ?? 0);
        statElements.in_progress.textContent = String(Number.isFinite(value) ? value : 0);
      }
      if (statElements.resolved) {
        const value = Number(normalised.resolved ?? 0) + Number(normalised.closed ?? 0);
        statElements.resolved.textContent = String(Number.isFinite(value) ? value : 0);
      }
      if (statElements.total) {
        const safeTotal = Number(total);
        statElements.total.textContent = String(Number.isFinite(safeTotal) ? safeTotal : 0);
      }
    }

    function formatUpdatedAt(value) {
      if (!value) {
        return '—';
      }
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) {
        return '—';
      }
      if (dateFormatter) {
        return dateFormatter.format(date);
      }
      return date.toISOString().replace('T', ' ').slice(0, 16);
    }

    function createStatusCell(ticketId, currentStatus) {
      const cell = document.createElement('td');
      cell.dataset.label = 'Status';
      const normalisedStatus = String(currentStatus || 'open');
      cell.dataset.value = normalisedStatus;

      const wrapper = document.createElement('div');
      wrapper.className = 'ticket-status';

      const labelId = `ticket-status-label-${ticketId}`;
      const hiddenLabel = document.createElement('span');
      hiddenLabel.className = 'visually-hidden';
      hiddenLabel.id = labelId;
      hiddenLabel.textContent = 'Ticket status';
      wrapper.appendChild(hiddenLabel);

      const form = document.createElement('form');
      form.id = `ticket-status-form-${ticketId}`;
      form.action = `/admin/tickets/${ticketId}/status`;
      form.method = 'post';
      form.className = 'inline-form ticket-status__form';
      form.setAttribute('data-ticket-status-form', '');
      form.setAttribute('aria-labelledby', labelId);

      if (csrfToken) {
        const csrfInput = document.createElement('input');
        csrfInput.type = 'hidden';
        csrfInput.name = '_csrf';
        csrfInput.value = csrfToken;
        form.appendChild(csrfInput);
      }

      const label = document.createElement('label');
      label.className = 'visually-hidden';
      label.htmlFor = `ticket-status-${ticketId}`;
      label.textContent = 'Status';
      form.appendChild(label);

      const select = document.createElement('select');
      select.id = `ticket-status-${ticketId}`;
      select.name = 'status';
      select.className = 'form-input form-input--compact';
      select.setAttribute('data-ticket-status-select', '');

      const uniqueOptions = Array.from(new Set([...statusOptions, normalisedStatus]));
      uniqueOptions.forEach((option) => {
        const optionElement = document.createElement('option');
        optionElement.value = option;
        optionElement.textContent = statusLabels[option] || option.replace(/_/g, ' ');
        if (option === normalisedStatus) {
          optionElement.selected = true;
        }
        select.appendChild(optionElement);
      });

      form.appendChild(select);
      wrapper.appendChild(form);
      cell.appendChild(wrapper);
      return cell;
    }

    function buildRow(ticket) {
      const numericId = Number(ticket.id);
      if (!Number.isFinite(numericId) || numericId <= 0) {
        return null;
      }
      const ticketId = numericId;
      const row = document.createElement('tr');

      if (canBulkDelete) {
        const selectCell = document.createElement('td');
        selectCell.dataset.label = 'Select';
        selectCell.className = 'table__select';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.name = 'ticketIds';
        checkbox.value = String(ticketId);
        checkbox.setAttribute('aria-label', `Select ticket ${ticketId}`);
        checkbox.setAttribute('data-bulk-delete-checkbox', '');
        if (bulkDeleteFormId) {
          checkbox.setAttribute('form', bulkDeleteFormId);
        }
        selectCell.appendChild(checkbox);
        row.appendChild(selectCell);
      }

      const idCell = document.createElement('td');
      idCell.dataset.label = 'ID';
      idCell.dataset.value = String(ticketId);
      idCell.textContent = String(ticketId);
      row.appendChild(idCell);

      const subjectCell = document.createElement('td');
      subjectCell.dataset.label = 'Subject';
      const subjectLink = document.createElement('a');
      subjectLink.href = `/admin/tickets/${ticketId}`;
      subjectLink.textContent = String(ticket.subject || '');
      subjectCell.appendChild(subjectLink);
      row.appendChild(subjectCell);

      const statusCell = createStatusCell(ticketId, ticket.status);
      row.appendChild(statusCell);

      const priorityCell = document.createElement('td');
      priorityCell.dataset.label = 'Priority';
      priorityCell.textContent = String(ticket.priority || 'normal');
      row.appendChild(priorityCell);

      const companyCell = document.createElement('td');
      companyCell.dataset.label = 'Company';
      let companyDisplay = '';
      if (ticket.company_name) {
        companyDisplay = String(ticket.company_name);
      } else if (ticket.company_id !== null && ticket.company_id !== undefined) {
        companyDisplay = String(ticket.company_id);
      }
      companyCell.textContent = companyDisplay || '—';
      row.appendChild(companyCell);

      const assignedCell = document.createElement('td');
      assignedCell.dataset.label = 'Assigned';
      assignedCell.textContent = ticket.assigned_user_email ? String(ticket.assigned_user_email) : '—';
      row.appendChild(assignedCell);

      const updatedCell = document.createElement('td');
      updatedCell.dataset.label = 'Updated';
      updatedCell.dataset.value = ticket.updated_at || '';
      updatedCell.textContent = formatUpdatedAt(ticket.updated_at);
      row.appendChild(updatedCell);

      const actionsCell = document.createElement('td');
      actionsCell.className = 'table__actions';
      const actionLink = document.createElement('a');
      actionLink.className = 'button button--ghost';
      actionLink.href = `/admin/tickets/${ticketId}`;
      actionLink.textContent = 'Open';
      actionsCell.appendChild(actionLink);
      row.appendChild(actionsCell);

      return row;
    }

    function renderTable(items) {
      const tbody = table.tBodies[0] || table.createTBody();
      const fragment = document.createDocumentFragment();
      const rows = Array.isArray(items) ? items : [];

      rows.forEach((ticket) => {
        const row = buildRow(ticket);
        if (row) {
          fragment.appendChild(row);
        }
      });

      if (!fragment.childNodes.length) {
        const emptyRow = document.createElement('tr');
        const headerCells = table.querySelectorAll('thead th');
        const columnCount = headerCells.length || (canBulkDelete ? 9 : 8);
        const emptyCell = document.createElement('td');
        emptyCell.colSpan = columnCount || 8;
        emptyCell.className = 'table__empty';
        emptyCell.textContent = emptyMessage;
        emptyRow.appendChild(emptyCell);
        fragment.appendChild(emptyRow);
      }

      while (tbody.firstChild) {
        tbody.removeChild(tbody.firstChild);
      }
      tbody.appendChild(fragment);
    }

    state = {
      renderTable,
      updateStats,
    };
    ticketTableStateCache.set(table, state);
    return state;
  }

  function registerTicketTableRefreshHandler() {
    if (ticketRefreshHandlerRegistered) {
      return;
    }
    ticketRefreshHandlerRegistered = true;

    const handler = async ({ table, response }) => {
      if (!(table instanceof HTMLTableElement)) {
        return { skipDefaultToast: true };
      }
      const state = getTicketTableState(table);
      const items = Array.isArray(response?.items) ? response.items : [];
      state.renderTable(items);
      state.updateStats(response?.status_counts, response?.total);
      table.dispatchEvent(new CustomEvent('table:rows-updated'));
      bindTicketStatusAutoSubmit();
      bindTicketBulkDelete();
      return { successMessage: 'Tickets updated.' };
    };

    registerTableRefreshHandler('tickets', handler);
    registerTableRefreshHandler('tickets-table', handler);
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

  function bindCompanyAssignForm() {
    const form = document.querySelector('[data-company-assign-form]');
    if (!form) {
      return;
    }

    const companySelect = form.querySelector('[data-company-select]');
    const userSelect = form.querySelector('[data-user-select]');
    if (!companySelect || !userSelect) {
      return;
    }

    const optionsMap = parseJsonScript('company-assign-user-options', {});
    if (!optionsMap || typeof optionsMap !== 'object') {
      return;
    }

    const placeholderOption = userSelect.querySelector('[data-placeholder]') || null;
    const initialCompanyId = form.getAttribute('data-initial-company-id') || companySelect.value || '';
    const initialUserId = form.getAttribute('data-initial-user-id');

    function getOptionsForCompany(companyId) {
      const key = String(companyId || '').trim();
      if (!key) {
        return [];
      }
      const value = optionsMap[key];
      return Array.isArray(value) ? value : [];
    }

    function populateUsers(companyId, targetSelection) {
      const options = getOptionsForCompany(companyId);
      const desiredSelection =
        targetSelection !== undefined && targetSelection !== null
          ? String(targetSelection)
          : userSelect.value;

      Array.from(userSelect.options).forEach((option) => {
        if (option.hasAttribute('data-placeholder')) {
          return;
        }
        option.remove();
      });

      let hasSelection = false;
      options.forEach((entry) => {
        if (!entry || typeof entry !== 'object') {
          return;
        }
        const { id, email } = entry;
        if (id === undefined || email === undefined) {
          return;
        }
        const option = document.createElement('option');
        option.value = String(id);
        option.textContent = String(email);
        if (desiredSelection && String(id) === String(desiredSelection)) {
          option.selected = true;
          hasSelection = true;
        }
        userSelect.appendChild(option);
      });

      if (!hasSelection) {
        if (placeholderOption) {
          placeholderOption.selected = true;
        } else {
          userSelect.value = '';
        }
      }
    }

    populateUsers(initialCompanyId, initialUserId ?? undefined);

    companySelect.addEventListener('change', () => {
      const selectedCompanyId = companySelect.value;
      populateUsers(selectedCompanyId, undefined);
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
    bindIssueStatusAutoSubmit();
    registerTicketTableRefreshHandler();
    setupTableRealtimeRefreshControllers();
    bindTicketAiReplaceDescription();
    bindTicketAiRefresh();
    bindRoleForm();
    bindCompanyAssignForm();
    bindCompanyAssignmentControls();
    bindApiKeyCopyButtons();
    bindConfirmationButtons();
    bindModal({ modalId: 'add-company-modal', triggerSelector: '[data-add-company-modal-open]' });
    bindModal({ modalId: 'create-ticket-modal', triggerSelector: '[data-create-ticket-modal-open]' });
    bindModal({ modalId: 'create-api-key-modal', triggerSelector: '[data-create-api-key-modal-open]' });
  });
})();
