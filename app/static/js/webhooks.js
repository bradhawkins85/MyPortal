(function () {
  let attemptsModal = null;
  let attemptsEvent = null;
  let selectedAttemptRow = null;
  let attemptPlaceholder = null;
  let attemptDetailsWrapper = null;
  let attemptRequestHeaders = null;
  let attemptRequestBody = null;
  let attemptResponseHeaders = null;
  let attemptResponseBody = null;
  let attemptResponseStatus = null;
  let attemptResponseError = null;
  let deletionRulesModal = null;

  const OPERATORS = [
    ['equals', 'is'],
    ['not_equals', 'is not'],
    ['contains', 'contains'],
    ['not_contains', "doesn't contain"],
    ['starts_with', 'starts with'],
    ['ends_with', 'ends with'],
    ['is_empty', 'is empty'],
    ['is_not_empty', 'is not empty'],
    ['greater_than', 'is greater than'],
    ['less_than', 'is less than'],
  ];
  const OPERATOR_LABELS = Object.fromEntries(OPERATORS);
  const VALUELESS_OPERATORS = new Set(['is_empty', 'is_not_empty']);
  const MAX_CONDITIONS = 20;
  const CRON_LABELS = {
    '0 * * * *': 'every hour',
    '0 2 * * *': 'every day at 02:00 UTC',
    '0 3 * * 0': 'every Sunday at 03:00 UTC',
  };
  const ATTEMPT_STATUS_VARIANTS = {
    succeeded: 'success',
    success: 'success',
    failed: 'error',
    error: 'error',
    timeout: 'warning',
  };

  function query(id) {
    return document.getElementById(id);
  }

  function notify(message, variant) {
    const toast = window.__portalToast;
    if (toast && typeof toast.show === 'function') {
      toast.show(message, { variant: variant || 'info', persist: false });
      return;
    }
    window.alert(message);
  }

  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const metaToken = meta ? meta.getAttribute('content') || '' : '';
    return metaToken || getCookie('myportal_session_csrf');
  }

  function formatErrorDetail(data, fallback) {
    let detail = fallback;
    if (data && data.detail) {
      detail = Array.isArray(data.detail)
        ? data.detail.map((entry) => entry.msg || entry).join(', ')
        : data.detail;
    }
    const reference = data && (data.error_reference || data.request_id);
    return reference ? `${detail} (Reference: ${reference})` : detail;
  }

  async function requestJson(url, options = {}) {
    const init = { credentials: 'same-origin', ...options };
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
        detail = formatErrorDetail(await response.json(), detail);
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

  // ── Modals ────────────────────────────────────────────────────────────────

  function openModal(modal, trigger) {
    if (!modal) {
      return;
    }
    modal.__previousActive = trigger || document.activeElement;
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('scf-modal-open');
    const body = modal.querySelector('.modal__body');
    if (body) {
      body.scrollTop = 0;
    }
    const focusTarget =
      modal.querySelector('[data-initial-focus]') ||
      modal.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (focusTarget && typeof focusTarget.focus === 'function') {
      focusTarget.focus();
    }
  }

  function closeModal(modal) {
    if (!modal || modal.hidden) {
      return;
    }
    modal.classList.remove('is-visible');
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.modal.scf-modal:not([hidden])')) {
      document.body.classList.remove('scf-modal-open');
    }
    const previous = modal.__previousActive;
    if (previous && typeof previous.focus === 'function' && document.contains(previous)) {
      previous.focus();
    }
  }

  function bindModalDismissal(modal) {
    if (!modal) {
      return;
    }
    // main.js turns Escape into a click on the modal's close control.
    modal.addEventListener('click', (event) => {
      if (event.target instanceof Element && event.target.closest('[data-modal-close]')) {
        closeModal(modal);
      }
    });
  }

  // ── Delivery log ──────────────────────────────────────────────────────────

  function parseEvent(row) {
    if (!row || !row.dataset || !row.dataset.eventId) {
      return null;
    }
    const idNumber = Number.parseInt(row.dataset.eventId, 10);
    return {
      id: Number.isNaN(idNumber) ? row.dataset.eventId : idNumber,
      name: row.dataset.eventName || '',
      target_url: row.dataset.eventTarget || '',
      status: row.dataset.eventStatus || '',
    };
  }

  function updateTableEmptyState() {
    const table = query('webhooks-table');
    const empty = document.querySelector('[data-webhook-empty]');
    if (!table || !empty) {
      return;
    }
    const hasRows = Boolean(table.querySelector('tbody tr[data-event-id]'));
    if (!hasRows) {
      table.closest('.table-wrapper')?.setAttribute('hidden', '');
      document.querySelector('[data-pagination="webhooks-table"]')?.setAttribute('hidden', '');
      empty.hidden = false;
    }
  }

  async function retryEvent(eventData, button) {
    if (!eventData || !eventData.id) {
      return;
    }
    if (button) {
      button.disabled = true;
    }
    try {
      await requestJson(`/scheduler/webhooks/${eventData.id}/retry`, { method: 'POST' });
      notify(`Retry queued for ${eventData.name || `event #${eventData.id}`}. Reloading…`, 'success');
      window.setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      notify(`Unable to retry webhook: ${error.message}`, 'error');
      if (button) {
        button.disabled = false;
      }
    }
  }

  function bindRowActions() {
    const table = query('webhooks-table');
    if (!table) {
      return;
    }
    table.addEventListener('click', async (event) => {
      const button = event.target instanceof Element ? event.target.closest('button') : null;
      if (!button || button.disabled) {
        return;
      }
      const row = button.closest('tr');
      const eventData = parseEvent(row);
      if (!eventData) {
        return;
      }
      if (button.hasAttribute('data-webhook-attempts')) {
        showAttemptsModal(eventData, button);
      } else if (button.hasAttribute('data-webhook-retry')) {
        retryEvent(eventData, button);
      } else if (button.hasAttribute('data-webhook-delete')) {
        const target = eventData.target_url || 'the configured endpoint';
        const message =
          eventData.status === 'pending'
            ? `Delete this webhook event and cancel the pending request to ${target}?`
            : `Delete “${eventData.name || `event #${eventData.id}`}” and its delivery attempts?`;
        if (!window.confirm(message)) {
          return;
        }
        button.disabled = true;
        try {
          await requestJson(`/scheduler/webhooks/${eventData.id}`, { method: 'DELETE' });
          row.remove();
          table.dispatchEvent(new CustomEvent('table:rows-updated', { bubbles: true }));
          updateTableEmptyState();
          notify('Webhook event deleted.', 'success');
        } catch (error) {
          notify(`Unable to delete webhook: ${error.message}`, 'error');
          button.disabled = false;
        }
      }
    });
  }

  function bindBulkDeleteButtons() {
    document.querySelectorAll('[data-webhook-delete-status]').forEach((button) => {
      button.addEventListener('click', async () => {
        const status = button.dataset.webhookDeleteStatus;
        if (!status || button.disabled) {
          return;
        }
        const label = status === 'failed' ? 'failed' : 'successful';
        if (!window.confirm(`Delete all ${label} webhook events and their attempts? This can't be undone.`)) {
          return;
        }
        button.disabled = true;
        try {
          const response = await requestJson(`/scheduler/webhooks?status=${encodeURIComponent(status)}`, {
            method: 'DELETE',
          });
          const deleted = response && typeof response.deleted === 'number' ? response.deleted : 0;
          notify(
            deleted
              ? `Removed ${deleted} ${label} webhook event${deleted === 1 ? '' : 's'}. Reloading…`
              : `No ${label} webhook events to remove.`,
            'success'
          );
          if (deleted) {
            window.setTimeout(() => window.location.reload(), 900);
          } else {
            button.disabled = false;
          }
        } catch (error) {
          notify(`Unable to delete ${label} webhooks: ${error.message}`, 'error');
          button.disabled = false;
        }
      });
    });
  }

  function bindToolbarAutosubmit() {
    document.querySelectorAll('[data-whm-autosubmit]').forEach((select) => {
      select.addEventListener('change', () => {
        const form = select.form;
        if (form && typeof form.requestSubmit === 'function') {
          form.requestSubmit();
        } else if (form) {
          form.submit();
        }
      });
    });
  }

  // ── Attempts modal ────────────────────────────────────────────────────────

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

  function formatData(value) {
    if (value === null || value === undefined || value === '') {
      return '—';
    }
    if (typeof value === 'string') {
      try {
        return JSON.stringify(JSON.parse(value), null, 2);
      } catch (error) {
        return value;
      }
    }
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
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

  function cell(label, content, value) {
    const td = document.createElement('td');
    td.setAttribute('data-label', label);
    if (value !== undefined) {
      td.setAttribute('data-value', value);
    }
    if (content instanceof Node) {
      td.appendChild(content);
    } else {
      td.textContent = content;
    }
    return td;
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
    attempts.forEach((attempt, index) => {
      const row = document.createElement('tr');
      const attempted = formatIso(attempt.attempted_at || attempt.attemptedAt || attempt.attemptedIso);
      row.appendChild(cell('Attempted', attempted.text, attempted.value));

      const status = String(attempt.status || 'unknown');
      const pill = document.createElement('span');
      pill.className = `status status--${ATTEMPT_STATUS_VARIANTS[status.toLowerCase()] || 'neutral'}`;
      pill.textContent = status.replace(/_/g, ' ');
      row.appendChild(cell('Result', pill, status));

      const statusCode = attempt.response_status ?? attempt.responseStatus ?? attempt.statusCode ?? null;
      row.appendChild(cell('HTTP', statusCode !== null ? String(statusCode) : '—'));

      const duration = typeof attempt.duration_ms === 'number' ? attempt.duration_ms : attempt.durationMs;
      row.appendChild(cell('Duration', Number.isFinite(duration) ? `${duration} ms` : '—'));

      const errorMessage = attempt.error_message || attempt.errorMessage || attempt.error;
      row.appendChild(cell('Error', errorMessage || '—'));

      const detailsCell = document.createElement('td');
      detailsCell.className = 'table__actions';
      const detailsButton = document.createElement('button');
      detailsButton.type = 'button';
      detailsButton.className = 'button button--ghost button--small';
      detailsButton.textContent = 'Details';
      detailsButton.setAttribute('aria-controls', 'webhook-attempt-details');
      detailsButton.addEventListener('click', () => selectAttempt(row, attempt));
      detailsCell.appendChild(detailsButton);
      row.appendChild(detailsCell);

      tbody.appendChild(row);
      if (index === 0) {
        selectAttempt(row, attempt);
      }
    });
  }

  function updateAttemptDetails(attempt) {
    if (!attemptDetailsWrapper || !attemptPlaceholder) {
      return;
    }
    attemptPlaceholder.hidden = true;
    attemptDetailsWrapper.hidden = false;
    const statusCode = attempt.response_status ?? attempt.responseStatus ?? attempt.statusCode ?? null;
    const errorMessage = attempt.error_message || attempt.errorMessage || attempt.error;
    if (attemptRequestHeaders) {
      attemptRequestHeaders.textContent = formatData(attempt.request_headers || attempt.requestHeaders);
    }
    if (attemptRequestBody) {
      attemptRequestBody.textContent = formatData(attempt.request_body || attempt.requestBody);
    }
    if (attemptResponseHeaders) {
      attemptResponseHeaders.textContent = formatData(attempt.response_headers || attempt.responseHeaders);
    }
    if (attemptResponseBody) {
      attemptResponseBody.textContent = formatData(attempt.response_body || attempt.responseBody);
    }
    if (attemptResponseStatus) {
      attemptResponseStatus.textContent = statusCode !== null ? String(statusCode) : '—';
    }
    if (attemptResponseError) {
      attemptResponseError.textContent = errorMessage || '—';
    }
  }

  function selectAttempt(row, attempt) {
    if (selectedAttemptRow) {
      selectedAttemptRow.classList.remove('table__row--active');
    }
    selectedAttemptRow = row;
    row.classList.add('table__row--active');
    updateAttemptDetails(attempt);
  }

  async function showAttemptsModal(eventData, trigger) {
    if (!attemptsModal || !eventData || !eventData.id) {
      return;
    }
    attemptsEvent = eventData;
    const title = query('webhook-attempts-title');
    if (title) {
      title.textContent = eventData.name || `Event #${eventData.id}`;
    }
    const description = query('webhook-attempts-description');
    if (description) {
      description.textContent = eventData.target_url
        ? `Latest delivery attempts to ${eventData.target_url}.`
        : 'Latest delivery attempts for the selected webhook event.';
    }
    const retryButton = attemptsModal.querySelector('[data-webhook-modal-retry]');
    if (retryButton) {
      retryButton.disabled = false;
      retryButton.textContent = eventData.status === 'succeeded' ? 'Resend' : 'Retry delivery';
    }
    setAttemptsPlaceholder('Loading attempts…');
    openModal(attemptsModal, trigger);
    try {
      const attempts = await requestJson(`/scheduler/webhooks/${eventData.id}/attempts?limit=50`);
      renderAttempts(Array.isArray(attempts) ? attempts : []);
    } catch (error) {
      setAttemptsPlaceholder(`Unable to load attempts: ${error.message}`);
    }
  }

  // ── Deletion rules ────────────────────────────────────────────────────────

  function ruleForm() {
    return document.querySelector('[data-deletion-rule-form]');
  }

  function conditionRows(form) {
    return Array.from(form.querySelectorAll('[data-rule-condition]'));
  }

  function readConditions(form) {
    return conditionRows(form).map((row) => ({
      field: row.querySelector('[name="field"]').value.trim(),
      operator: row.querySelector('[name="operator"]').value,
      value: row.querySelector('[name="value"]').value,
    }));
  }

  function refreshConditionRows(form) {
    const rows = conditionRows(form);
    rows.forEach((row, index) => {
      row.querySelector('.whm-condition__word').textContent = index === 0 ? 'Where' : 'And';
      row.querySelector('[data-condition-remove]').disabled = rows.length === 1;
      const operator = row.querySelector('[name="operator"]').value;
      row.querySelector('[name="value"]').hidden = VALUELESS_OPERATORS.has(operator);
    });
    const addButton = form.querySelector('[data-add-condition]');
    if (addButton) {
      addButton.disabled = rows.length >= MAX_CONDITIONS;
    }
  }

  function addRuleCondition(form, condition = {}, focus = false) {
    const host = form.querySelector('[data-rule-conditions]');
    if (!host || conditionRows(form).length >= MAX_CONDITIONS) {
      return;
    }
    const position = conditionRows(form).length + 1;
    const row = document.createElement('li');
    row.className = 'whm-condition';
    row.setAttribute('data-rule-condition', '');

    const word = document.createElement('span');
    word.className = 'whm-condition__word';
    word.setAttribute('aria-hidden', 'true');

    const field = document.createElement('input');
    field.className = 'form-input scf-mono';
    field.name = 'field';
    field.required = true;
    field.maxLength = 100;
    field.placeholder = 'Field, e.g. status';
    field.setAttribute('list', 'webhook-rule-fields');
    field.setAttribute('aria-label', `Condition ${position} field`);
    field.autocomplete = 'off';
    field.spellcheck = false;
    field.value = condition.field || '';

    const operator = document.createElement('select');
    operator.className = 'form-input';
    operator.name = 'operator';
    operator.setAttribute('aria-label', `Condition ${position} comparison`);
    OPERATORS.forEach(([value, label]) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      option.selected = value === (condition.operator || 'equals');
      operator.appendChild(option);
    });

    const value = document.createElement('input');
    value.className = 'form-input whm-condition__value';
    value.name = 'value';
    value.placeholder = 'Value';
    value.setAttribute('aria-label', `Condition ${position} value`);
    value.value = condition.value ?? '';

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'scf-icon-button scf-icon-button--danger';
    remove.setAttribute('data-condition-remove', '');
    remove.setAttribute('aria-label', `Remove condition ${position}`);
    remove.textContent = '×';

    row.append(word, field, operator, value, remove);
    host.appendChild(row);
    refreshConditionRows(form);
    if (focus) {
      field.focus();
    }
  }

  function describeValue(value) {
    return `“${value}”`;
  }

  function updateRuleSummary(form) {
    const summary = form.querySelector('[data-rule-summary]');
    if (!summary) {
      return;
    }
    const conditions = readConditions(form).filter((condition) => condition.field);
    const execution = form.querySelector('[name="executionType"]:checked')?.value || 'event';
    const cron = form.elements.cronExpression.value.trim();
    summary.textContent = '';

    const when =
      execution === 'scheduled'
        ? cron
          ? `Runs ${CRON_LABELS[cron] || `on the schedule ${cron} (UTC)`}`
          : 'Runs on a schedule you still need to set'
        : 'Runs after each webhook event';
    summary.append(`${when} and deletes entries where `);
    if (!conditions.length) {
      summary.append('… add a condition to choose what it removes.');
      return;
    }
    conditions.forEach((condition, index) => {
      if (index > 0) {
        summary.append(' and ');
      }
      const code = document.createElement('code');
      code.textContent = condition.field;
      summary.append(code, ` ${OPERATOR_LABELS[condition.operator] || condition.operator}`);
      if (!VALUELESS_OPERATORS.has(condition.operator)) {
        summary.append(` ${describeValue(condition.value)}`);
      }
    });
    summary.append(form.elements.enabled.checked ? '.' : '. It is disabled, so nothing is deleted yet.');
  }

  function setRuleError(form, key, message) {
    const target = form.querySelector(`[data-rule-error="${key}"]`);
    if (target) {
      target.textContent = message || '';
      target.hidden = !message;
    }
  }

  function clearRuleErrors(form) {
    form.querySelectorAll('[data-rule-error]').forEach((node) => {
      node.textContent = '';
      node.hidden = true;
    });
    form.querySelectorAll('.is-invalid').forEach((node) => node.classList.remove('is-invalid'));
  }

  function syncExecution(form) {
    const scheduled = form.querySelector('[name="executionType"]:checked')?.value === 'scheduled';
    const cronField = form.querySelector('[data-rule-cron-field]');
    if (cronField) {
      cronField.hidden = !scheduled;
    }
    form.querySelectorAll('.scf-choice').forEach((choice) => {
      choice.classList.toggle('is-selected', Boolean(choice.querySelector('input:checked')));
    });
  }

  function openRuleEditor(rule, trigger) {
    const modal = query('webhook-deletion-rule-modal');
    const form = ruleForm();
    if (!modal || !form) {
      return;
    }
    const data = rule || {};
    clearRuleErrors(form);
    form.elements.id.value = data.id || '';
    form.elements.name.value = data.name || '';
    form.elements.cronExpression.value = data.cron_expression || '';
    form.elements.enabled.checked = data.enabled !== false;
    const execution = data.execution_type === 'scheduled' ? 'scheduled' : 'event';
    form.querySelectorAll('[name="executionType"]').forEach((radio) => {
      radio.checked = radio.value === execution;
    });
    form.querySelector('[data-rule-title]').textContent = data.id ? 'Edit deletion rule' : 'New deletion rule';
    form.querySelector('[data-rule-conditions]').innerHTML = '';
    const conditions = Array.isArray(data.conditions) && data.conditions.length ? data.conditions : [{}];
    conditions.forEach((condition) => addRuleCondition(form, condition));
    syncExecution(form);
    updateRuleSummary(form);
    openModal(modal, trigger);
  }

  function openDeletionRulesModal(trigger) {
    if (!deletionRulesModal) return;
    openModal(deletionRulesModal, trigger);
  }

  function validateRule(form) {
    clearRuleErrors(form);
    let valid = true;
    const name = form.elements.name;
    if (!name.value.trim()) {
      name.classList.add('is-invalid');
      setRuleError(form, 'name', 'Give the rule a name.');
      valid = false;
    }
    const scheduled = form.querySelector('[name="executionType"]:checked')?.value === 'scheduled';
    const cron = form.elements.cronExpression;
    if (scheduled && !cron.value.trim()) {
      cron.classList.add('is-invalid');
      setRuleError(form, 'cron', 'Scheduled rules need a cron schedule.');
      valid = false;
    }
    let conditionsValid = true;
    conditionRows(form).forEach((row) => {
      const field = row.querySelector('[name="field"]');
      const operator = row.querySelector('[name="operator"]').value;
      const value = row.querySelector('[name="value"]');
      if (!field.value.trim()) {
        field.classList.add('is-invalid');
        conditionsValid = false;
      }
      if (!VALUELESS_OPERATORS.has(operator) && !value.value.trim()) {
        value.classList.add('is-invalid');
        conditionsValid = false;
      }
    });
    if (!conditionsValid) {
      setRuleError(form, 'conditions', 'Every condition needs a field and a value to compare with.');
      valid = false;
    }
    if (!valid) {
      form.querySelector('.is-invalid')?.focus();
    }
    return valid;
  }

  async function saveRule(form) {
    if (!validateRule(form)) {
      return;
    }
    const id = form.elements.id.value;
    const scheduled = form.querySelector('[name="executionType"]:checked')?.value === 'scheduled';
    const payload = {
      name: form.elements.name.value.trim(),
      executionType: scheduled ? 'scheduled' : 'event',
      cronExpression: scheduled ? form.elements.cronExpression.value.trim() : null,
      enabled: form.elements.enabled.checked,
      conditions: readConditions(form).map((condition) => ({
        ...condition,
        value: VALUELESS_OPERATORS.has(condition.operator) ? null : condition.value,
      })),
    };
    const saveButton = form.querySelector('[data-rule-save]');
    if (saveButton) {
      saveButton.disabled = true;
    }
    try {
      await requestJson(`/scheduler/webhook-deletion-rules${id ? `/${id}` : ''}`, {
        method: id ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      });
      notify(`Deletion rule “${payload.name}” saved. Reloading…`, 'success');
      window.setTimeout(() => window.location.reload(), 600);
    } catch (error) {
      setRuleError(form, 'form', `Couldn't save the rule: ${error.message}`);
      if (saveButton) {
        saveButton.disabled = false;
      }
    }
  }

  function bindDeletionRules() {
    const modal = query('webhook-deletion-rule-modal');
    const form = ruleForm();
    deletionRulesModal = query('webhook-deletion-rules-modal');

    // Bind the Actions menu item to open the rules list modal
    document.querySelectorAll('[data-deletion-rules-modal-open]').forEach((button) => {
      button.addEventListener('click', () => openDeletionRulesModal(button));
    });

    // Bind dismissal for the rules list modal
    if (deletionRulesModal) {
      bindModalDismissal(deletionRulesModal);
    }

    if (!modal || !form) {
      return;
    }
    bindModalDismissal(modal);

    document.querySelectorAll('[data-deletion-rule-new]').forEach((button) => {
      button.addEventListener('click', () => openRuleEditor(null, button));
    });

    document.querySelectorAll('[data-deletion-rule]').forEach((item) => {
      let rule = {};
      try {
        rule = JSON.parse(item.dataset.rule || '{}');
      } catch (error) {
        return;
      }
      item.querySelector('[data-deletion-rule-edit]')?.addEventListener('click', (event) => {
        openRuleEditor(rule, event.currentTarget);
      });
      item.querySelector('[data-deletion-rule-delete]')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        if (!window.confirm(`Delete the rule “${rule.name}”? Entries it already removed stay deleted.`)) {
          return;
        }
        button.disabled = true;
        try {
          await requestJson(`/scheduler/webhook-deletion-rules/${rule.id}`, { method: 'DELETE' });
          notify(`Deletion rule “${rule.name}” deleted. Reloading…`, 'success');
          window.setTimeout(() => window.location.reload(), 600);
        } catch (error) {
          notify(`Unable to delete the rule: ${error.message}`, 'error');
          button.disabled = false;
        }
      });
    });

    form.querySelector('[data-add-condition]')?.addEventListener('click', () => {
      addRuleCondition(form, {}, true);
      updateRuleSummary(form);
    });

    form.querySelector('[data-rule-conditions]')?.addEventListener('click', (event) => {
      const remove = event.target instanceof Element ? event.target.closest('[data-condition-remove]') : null;
      if (!remove || remove.disabled) {
        return;
      }
      const row = remove.closest('[data-rule-condition]');
      const next = row.nextElementSibling || row.previousElementSibling;
      row.remove();
      refreshConditionRows(form);
      updateRuleSummary(form);
      next?.querySelector('[name="field"]')?.focus();
    });

    form.querySelectorAll('[data-cron-preset]').forEach((preset) => {
      preset.addEventListener('click', () => {
        form.elements.cronExpression.value = preset.dataset.cronPreset;
        form.elements.cronExpression.classList.remove('is-invalid');
        setRuleError(form, 'cron', '');
        updateRuleSummary(form);
      });
    });

    form.addEventListener('input', (event) => {
      if (event.target instanceof Element) {
        event.target.classList.remove('is-invalid');
      }
      updateRuleSummary(form);
    });

    form.addEventListener('change', (event) => {
      if (event.target instanceof Element && event.target.matches('[data-rule-execution]')) {
        syncExecution(form);
      }
      if (event.target instanceof Element && event.target.matches('[name="operator"]')) {
        refreshConditionRows(form);
      }
      updateRuleSummary(form);
    });

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      saveRule(form);
    });
  }

  // ── Retention ─────────────────────────────────────────────────────────────

  function retentionSummary(enabled, value, unit) {
    if (!enabled) {
      return 'Old entries are kept until a deletion rule or a manual delete removes them.';
    }
    if (unit === 'immediately') {
      return 'Every remaining entry is deleted on the next cleanup run.';
    }
    const amount = Number(value);
    const singular = unit.replace(/s$/, '');
    return `Entries created more than ${amount} ${amount === 1 ? singular : unit} ago are deleted on each cleanup run.`;
  }

  function bindRetention() {
    const form = document.querySelector('[data-retention-form]');
    if (!form) {
      return;
    }
    const enabled = form.querySelector('[data-retention-enabled]');
    const value = form.querySelector('[data-retention-value]');
    const unit = form.querySelector('[data-retention-unit]');
    const summary = form.querySelector('[data-retention-summary]');
    const error = form.querySelector('[data-retention-error]');

    function sync() {
      const immediate = unit.value === 'immediately';
      value.disabled = immediate || !enabled.checked;
      unit.disabled = !enabled.checked;
      form.classList.toggle('is-disabled', !enabled.checked);
      if (summary) {
        summary.textContent = retentionSummary(enabled.checked, value.value || 0, unit.value);
      }
    }

    form.addEventListener('input', sync);
    form.addEventListener('change', sync);
    sync();

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      error.hidden = true;
      const amount = Number(value.value);
      if (unit.value !== 'immediately' && (!Number.isInteger(amount) || amount < 0 || amount > 5256000)) {
        error.textContent = 'Enter a whole number between 0 and 5,256,000.';
        error.hidden = false;
        value.focus();
        return;
      }
      if (enabled.checked && unit.value === 'immediately' &&
          !window.confirm('Delete every webhook entry on each cleanup run? This keeps no history at all.')) {
        return;
      }
      const saveButton = form.querySelector('[data-retention-save]');
      saveButton.disabled = true;
      try {
        await requestJson('/scheduler/webhook-retention', {
          method: 'PUT',
          body: JSON.stringify({
            enabled: enabled.checked,
            retentionValue: unit.value === 'immediately' ? 0 : amount,
            retentionUnit: unit.value,
          }),
        });
        notify('Retention rule saved.', 'success');
      } catch (requestError) {
        error.textContent = `Couldn't save retention: ${requestError.message}`;
        error.hidden = false;
      } finally {
        saveButton.disabled = false;
      }
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
    attemptsModal?.querySelector('[data-webhook-modal-retry]')?.addEventListener('click', (event) => {
      retryEvent(attemptsEvent, event.currentTarget);
    });
    bindRowActions();
    bindBulkDeleteButtons();
    bindToolbarAutosubmit();
    bindDeletionRules();
    bindRetention();
  });
})();
