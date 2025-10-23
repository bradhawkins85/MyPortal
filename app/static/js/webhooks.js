(function () {
  let attemptsModal = null;
  let selectedAttemptRow = null;
  let attemptPlaceholder = null;
  let attemptDetailsWrapper = null;
  let attemptRequestHeaders = null;
  let attemptRequestBody = null;
  let attemptResponseHeaders = null;
  let attemptResponseBody = null;
  let attemptResponseStatus = null;
  let attemptResponseError = null;

  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getMetaCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta && typeof meta.getAttribute === 'function'
      ? meta.getAttribute('content') || ''
      : '';
  }

  function getCsrfToken() {
    const metaToken = getMetaCsrfToken();
    if (metaToken) {
      return metaToken;
    }
    return getCookie('myportal_session_csrf');
  }

  async function requestJson(url, options = {}) {
    const init = { ...options };
    if (!init.credentials) {
      init.credentials = 'same-origin';
    }

    const headers = new Headers(init.headers || {});
    const csrfToken = getCsrfToken();
    if (csrfToken && !headers.has('X-CSRF-Token')) {
      headers.set('X-CSRF-Token', csrfToken);
    }
    if (init.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }

    init.headers = headers;

    const response = await fetch(url, init);
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
        /* ignore */
      }
      throw new Error(detail);
    }
    if (response.status === 204) {
      return null;
    }
    try {
      return await response.json();
    } catch (error) {
      return null;
    }
  }

  function parseEvent(row) {
    if (!row) {
      return null;
    }
    const { dataset } = row;
    if (dataset) {
      const identifier = dataset.eventId || dataset.eventid;
      if (identifier) {
        const idNumber = Number.parseInt(identifier, 10);
        const resolvedId = Number.isNaN(idNumber) ? identifier : idNumber;
        return {
          id: resolvedId,
          name: dataset.eventName || '',
          target_url: dataset.eventTarget || '',
          targetUrl: dataset.eventTarget || '',
          status: dataset.eventStatus || '',
        };
      }
    }
    try {
      const value = row.dataset?.event || row.getAttribute('data-event') || '{}';
      return JSON.parse(value);
    } catch (error) {
      return null;
    }
  }

  function query(id) {
    return document.getElementById(id);
  }

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.__previousActive = document.activeElement;
    const focusTarget =
      modal.querySelector('[data-initial-focus]') ||
      modal.querySelector(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
    if (focusTarget && typeof focusTarget.focus === 'function') {
      focusTarget.focus();
    }
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.remove('is-visible');
    modal.hidden = true;
    const previous = modal.__previousActive;
    if (previous && typeof previous.focus === 'function') {
      previous.focus();
    }
  }

  function bindModalDismissal(modal) {
    if (!modal) {
      return;
    }
    modal.addEventListener('click', (event) => {
      if (event.target === modal || event.target.hasAttribute('data-modal-close')) {
        closeModal(modal);
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !modal.hidden) {
        closeModal(modal);
      }
    });
  }

  function formatIso(iso) {
    if (!iso) {
      return { text: '—', value: '' };
    }
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return { text: '—', value: '' };
    }
    return { text: date.toLocaleString(), value: iso };
  }

  function setAttemptsPlaceholder(message) {
    const tbody = query('webhook-attempts-body');
    if (!tbody) {
      return;
    }
    tbody.innerHTML = '';
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 6;
    cell.className = 'table__empty';
    cell.textContent = message;
    row.appendChild(cell);
    tbody.appendChild(row);
    showAttemptPrompt(message);
  }

  function renderAttempts(attempts) {
    const tbody = query('webhook-attempts-body');
    if (!tbody) {
      return;
    }
    tbody.innerHTML = '';
    if (!attempts || attempts.length === 0) {
      setAttemptsPlaceholder('No delivery attempts recorded for this event.');
      return;
    }
    attempts.forEach((attempt) => {
      const row = document.createElement('tr');

      const attemptedCell = document.createElement('td');
      attemptedCell.setAttribute('data-label', 'Attempted');
      const attempted = formatIso(
        attempt.attempted_at || attempt.attemptedAt || attempt.attemptedIso
      );
      attemptedCell.setAttribute('data-value', attempted.value);
      attemptedCell.textContent = attempted.text;
      row.appendChild(attemptedCell);

      const statusCell = document.createElement('td');
      statusCell.setAttribute('data-label', 'Status');
      statusCell.textContent = attempt.status || 'unknown';
      statusCell.setAttribute('data-value', attempt.status || '');
      row.appendChild(statusCell);

      const durationCell = document.createElement('td');
      durationCell.setAttribute('data-label', 'Duration (ms)');
      const duration = typeof attempt.duration_ms === 'number' ? attempt.duration_ms : attempt.durationMs;
      durationCell.setAttribute('data-value', String(duration ?? 0));
      durationCell.textContent = Number.isFinite(duration) ? String(duration) : '—';
      row.appendChild(durationCell);

      const responseCell = document.createElement('td');
      responseCell.setAttribute('data-label', 'Response');
      const statusCode =
        attempt.response_status ?? attempt.responseStatus ?? attempt.statusCode ?? null;
      responseCell.setAttribute('data-value', statusCode !== null ? String(statusCode) : '');
      responseCell.textContent = statusCode !== null ? String(statusCode) : '—';
      row.appendChild(responseCell);

      const errorCell = document.createElement('td');
      errorCell.setAttribute('data-label', 'Error');
      const errorMessage = attempt.error_message || attempt.errorMessage || attempt.error;
      errorCell.textContent = errorMessage || '—';
      row.appendChild(errorCell);

      const detailsCell = document.createElement('td');
      detailsCell.className = 'table__actions';
      const detailsButton = document.createElement('button');
      detailsButton.type = 'button';
      detailsButton.className = 'button button--ghost';
      detailsButton.textContent = 'View details';
      detailsButton.addEventListener('click', () => {
        selectAttempt(row, attempt);
      });
      detailsCell.appendChild(detailsButton);
      row.appendChild(detailsCell);

      tbody.appendChild(row);
    });
    showAttemptPrompt('Select an attempt to inspect request and response payloads.');
  }

  function updateTableEmptyState() {
    const tbody = document.querySelector('#webhooks-table tbody');
    if (!tbody) {
      return;
    }
    const hasDataRow = Array.from(tbody.rows).some((row) => {
      if (row.classList.contains('table__empty')) {
        return false;
      }
      const { dataset } = row;
      return Boolean(dataset?.eventId || dataset?.event || row.getAttribute('data-event'));
    });
    if (!hasDataRow) {
      tbody.innerHTML = '';
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 8;
      cell.className = 'table__empty';
      cell.textContent = 'No webhook activity recorded.';
      row.appendChild(cell);
      tbody.appendChild(row);
    }
  }

  function formatData(value) {
    if (value === null || value === undefined) {
      return '—';
    }
    if (typeof value === 'string') {
      return value || '—';
    }
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      try {
        return String(value);
      } catch (stringError) {
        return '—';
      }
    }
  }

  function showAttemptPrompt(message) {
    if (attemptPlaceholder) {
      attemptPlaceholder.hidden = false;
      attemptPlaceholder.textContent = message;
    }
    if (attemptDetailsWrapper) {
      attemptDetailsWrapper.hidden = true;
    }
    if (selectedAttemptRow) {
      selectedAttemptRow.classList.remove('table__row--active');
      selectedAttemptRow = null;
    }
  }

  function updateAttemptDetails(attempt) {
    if (!attemptDetailsWrapper || !attemptPlaceholder) {
      return;
    }
    if (!attempt) {
      showAttemptPrompt('Select an attempt to inspect request and response payloads.');
      return;
    }
    attemptPlaceholder.hidden = true;
    attemptDetailsWrapper.hidden = false;

    if (attemptRequestHeaders) {
      const headers = attempt.request_headers || attempt.requestHeaders || null;
      attemptRequestHeaders.textContent = formatData(headers);
    }
    if (attemptRequestBody) {
      const body = attempt.request_body || attempt.requestBody || null;
      attemptRequestBody.textContent = formatData(body);
    }
    if (attemptResponseHeaders) {
      const headers = attempt.response_headers || attempt.responseHeaders || null;
      attemptResponseHeaders.textContent = formatData(headers);
    }
    if (attemptResponseBody) {
      const body = attempt.response_body || attempt.responseBody || null;
      attemptResponseBody.textContent = formatData(body);
    }
    if (attemptResponseStatus) {
      const statusCode =
        attempt.response_status ?? attempt.responseStatus ?? attempt.statusCode ?? null;
      attemptResponseStatus.textContent = statusCode !== null ? String(statusCode) : '—';
    }
    if (attemptResponseError) {
      const errorMessage = attempt.error_message || attempt.errorMessage || attempt.error;
      attemptResponseError.textContent = errorMessage || '—';
    }
  }

  function selectAttempt(row, attempt) {
    if (selectedAttemptRow) {
      selectedAttemptRow.classList.remove('table__row--active');
    }
    selectedAttemptRow = row;
    if (selectedAttemptRow) {
      selectedAttemptRow.classList.add('table__row--active');
    }
    updateAttemptDetails(attempt);
  }

  async function showAttemptsModal(eventData) {
    if (!attemptsModal || !eventData || !eventData.id) {
      return;
    }
    const title = query('webhook-attempts-title');
    if (title) {
      title.textContent = `Webhook attempts — ${eventData.name || `Event #${eventData.id}`}`;
    }
    const description = query('webhook-attempts-description');
    if (description) {
      const target = eventData.target_url || eventData.targetUrl || eventData.target || '';
      description.textContent = target
        ? `Review the latest delivery attempts for ${target}.`
        : 'Review the latest delivery attempts for the selected webhook event.';
    }
    setAttemptsPlaceholder('Loading attempts…');
    openModal(attemptsModal);
    try {
      const attempts = await requestJson(`/scheduler/webhooks/${eventData.id}/attempts?limit=50`);
      renderAttempts(Array.isArray(attempts) ? attempts : []);
    } catch (error) {
      setAttemptsPlaceholder(`Unable to load attempts: ${error.message}`);
    }
  }

  function bindRetryButtons() {
    document.querySelectorAll('[data-webhook-retry]').forEach((button) => {
      button.addEventListener('click', async () => {
        if (button.disabled) {
          return;
        }
        const row = button.closest('tr');
        const eventData = parseEvent(row);
        if (!eventData || !eventData.id) {
          return;
        }
        try {
          await requestJson(`/scheduler/webhooks/${eventData.id}/retry`, { method: 'POST' });
          alert('Webhook retry has been queued. Refresh the page to see updates.');
          window.location.reload();
        } catch (error) {
          alert(`Unable to retry webhook: ${error.message}`);
        }
      });
    });
  }

  function bindAttemptsButtons() {
    document.querySelectorAll('[data-webhook-attempts]').forEach((button) => {
      button.addEventListener('click', () => {
        const row = button.closest('tr');
        const eventData = parseEvent(row);
        if (eventData) {
          showAttemptsModal(eventData);
        }
      });
    });
  }

  function bindDeleteButtons() {
    document.querySelectorAll('[data-webhook-delete]').forEach((button) => {
      button.addEventListener('click', async () => {
        const row = button.closest('tr');
        const eventData = parseEvent(row);
        if (!eventData || !eventData.id) {
          return;
        }
        const target = eventData.target_url || eventData.targetUrl || 'the configured endpoint';
        const status = (eventData.status || '').toLowerCase();
        const confirmMessage =
          status === 'pending'
            ? `Delete this webhook event and cancel the pending request to ${target}?`
            : 'Delete this webhook event?';
        if (!window.confirm(confirmMessage)) {
          return;
        }
        button.disabled = true;
        try {
          await requestJson(`/scheduler/webhooks/${eventData.id}`, { method: 'DELETE' });
          if (row && row.parentElement) {
            row.remove();
            updateTableEmptyState();
          }
          alert('Webhook event deleted.');
        } catch (error) {
          alert(`Unable to delete webhook: ${error.message}`);
          button.disabled = false;
        }
      });
    });
  }

  function bindBulkDeleteButtons() {
    document.querySelectorAll('[data-webhook-delete-status]').forEach((button) => {
      button.addEventListener('click', async () => {
        const status = button.dataset.webhookDeleteStatus;
        if (!status || button.disabled) {
          return;
        }
        const confirmationMessage =
          status === 'failed'
            ? 'Delete all failed webhook events? This action cannot be undone.'
            : 'Delete all successful webhook events? This action cannot be undone.';
        if (!window.confirm(confirmationMessage)) {
          return;
        }
        button.disabled = true;
        try {
          const response = await requestJson(`/scheduler/webhooks?status=${encodeURIComponent(status)}`, {
            method: 'DELETE',
          });
          const deletedCount = response && typeof response.deleted === 'number' ? response.deleted : 0;
          alert(
            deletedCount
              ? `Removed ${deletedCount} ${status} webhook event${deletedCount === 1 ? '' : 's'}.`
              : `No ${status} webhook events were removed.`
          );
          window.location.reload();
        } catch (error) {
          alert(`Unable to delete ${status} webhooks: ${error.message}`);
          button.disabled = false;
        }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    attemptsModal = query('webhook-attempts-modal');
    attemptPlaceholder = document.querySelector('[data-attempt-placeholder]');
    attemptDetailsWrapper = document.querySelector('[data-attempt-details]');
    attemptRequestHeaders = document.querySelector('[data-attempt-request-headers]');
    attemptRequestBody = document.querySelector('[data-attempt-request-body]');
    attemptResponseHeaders = document.querySelector('[data-attempt-response-headers]');
    attemptResponseBody = document.querySelector('[data-attempt-response-body]');
    attemptResponseStatus = document.querySelector('[data-attempt-response-status]');
    attemptResponseError = document.querySelector('[data-attempt-response-error]');
    bindModalDismissal(attemptsModal);
    bindRetryButtons();
    bindAttemptsButtons();
    bindDeleteButtons();
    bindBulkDeleteButtons();
  });
})();
