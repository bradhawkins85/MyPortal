(function () {
  let taskModal = null;
  let logsModal = null;
  let activeTaskContext = { id: '', name: '' };

  function toJsonTemplate(data) {
    try {
      return JSON.stringify(data, null, 2);
    } catch (error) {
      return '';
    }
  }

  const FILTER_SNIPPETS = [
    {
      label: 'Match status equals "open"',
      value: toJsonTemplate({
        match: {
          status: 'open',
        },
      }),
    },
    {
      label: 'Match ticket priority equals "high"',
      value: toJsonTemplate({
        match: {
          'ticket.priority': 'high',
        },
      }),
    },
    {
      label: 'Match any status open or pending',
      value: toJsonTemplate({
        any: [
          { match: { status: 'open' } },
          { match: { status: 'pending' } },
        ],
      }),
    },
    {
      label: 'Require status open and priority high',
      value: toJsonTemplate({
        all: [
          { match: { status: 'open' } },
          { match: { 'ticket.priority': 'high' } },
        ],
      }),
    },
    {
      label: 'Exclude cancelled status',
      value: toJsonTemplate({
        not: {
          match: {
            status: 'cancelled',
          },
        },
      }),
    },
    {
      label: 'Match nested payload customer ID',
      value: toJsonTemplate({
        match: {
          'payload.customer.id': 12345,
        },
      }),
    },
    {
      label: 'Match updates performed by technicians',
      value: toJsonTemplate({
        match: {
          'ticket_update.actor_type': 'technician',
        },
      }),
    },
    {
      label: 'Match updates performed by requesters',
      value: toJsonTemplate({
        match: {
          'ticket_update.actor_type': 'requester',
        },
      }),
    },
    {
      label: 'Match updates performed by watchers',
      value: toJsonTemplate({
        match: {
          'ticket_update.actor_type': 'watcher',
        },
      }),
    },
    {
      label: 'Match updates performed by automations',
      value: toJsonTemplate({
        match: {
          'ticket_update.actor_type': 'automation',
        },
      }),
    },
    {
      label: 'Match updates performed by the system',
      value: toJsonTemplate({
        match: {
          'ticket_update.actor_type': 'system',
        },
      }),
    },
  ];

  const ACTION_SNIPPETS = {
    smtp: [
      {
        label: 'Notify support team via email',
        value: toJsonTemplate({
          recipients: ['support@example.com'],
          subject: 'Ticket {{ticket.number}} requires attention',
          html: '<p>Ticket {{ticket.number}} triggered this automation.</p>',
          text: 'Ticket {{ticket.number}} triggered this automation.',
        }),
      },
      {
        label: 'Escalate with custom sender',
        value: toJsonTemplate({
          recipients: ['escalations@example.com'],
          subject: 'Escalation for ticket {{ticket.number}}',
          html: '<p>Please review the ticket escalation details.</p>',
          sender: 'alerts@example.com',
        }),
      },
    ],
    ntfy: [
      {
        label: 'Publish high priority ntfy alert',
        value: toJsonTemplate({
          topic: 'alerts',
          title: 'Automation alert',
          message: 'Ticket {{ticket.number}} breached SLA thresholds.',
          priority: 'urgent',
        }),
      },
      {
        label: 'Send acknowledgement request',
        value: toJsonTemplate({
          title: 'Ticket update required',
          message: 'Acknowledge ticket {{ticket.number}} in the portal.',
          priority: 'high',
        }),
      },
    ],
    tacticalrmm: [
      {
        label: 'Trigger Tactical RMM script',
        value: toJsonTemplate({
          endpoint: '/api/v3/scripts/run',
          method: 'POST',
          body: {
            agent_id: 1234,
            script: 'Reboot Agent',
          },
        }),
      },
      {
        label: 'Queue reboot task',
        value: toJsonTemplate({
          endpoint: '/api/v3/tasks/run',
          method: 'POST',
          body: {
            agent_id: 1234,
            task_name: 'Reboot',
          },
        }),
      },
    ],
    ollama: [
      {
        label: 'Generate ticket summary prompt',
        value: toJsonTemplate({
          prompt: 'Summarise ticket {{ticket.number}} and highlight next actions.',
        }),
      },
      {
        label: 'Draft customer reply prompt',
        value: toJsonTemplate({
          prompt: 'Draft a courteous reply for ticket {{ticket.number}} referencing recent updates.',
        }),
      },
    ],
    syncro: [
      {
        label: 'Override Syncro rate limit',
        value: toJsonTemplate({
          rate_limit_per_minute: 120,
        }),
      },
      {
        label: 'Provide temporary API key override',
        value: toJsonTemplate({
          api_key: '{{ secrets.syncro_api_key }}',
        }),
      },
    ],
    uptimekuma: [
      {
        label: 'Override shared secret for alert',
        value: toJsonTemplate({
          shared_secret: '{{ secrets.uptimekuma_shared_secret }}',
        }),
      },
      {
        label: 'Provide shared secret hash',
        value: toJsonTemplate({
          shared_secret_hash: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
        }),
      },
    ],
    'chatgpt-mcp': [
      {
        label: 'Limit MCP actions to ticket reads',
        value: toJsonTemplate({
          allowed_actions: ['listTickets', 'getTicket'],
          allow_ticket_updates: false,
        }),
      },
      {
        label: 'Enable ticket updates with cap',
        value: toJsonTemplate({
          allowed_actions: ['listTickets', 'getTicket', 'updateTicket'],
          allow_ticket_updates: true,
          max_results: 25,
        }),
      },
    ],
  };

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
    const cookieToken = getCookie('myportal_session_csrf');
    if (cookieToken) {
      return cookieToken;
    }
    return getMetaContent('csrf-token');
  }

  async function requestJson(url, options) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
        ...(options && options.headers ? options.headers : {}),
      },
      ...options,
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

  function parseTask(row) {
    if (!row) {
      return null;
    }
    try {
      const value = row.dataset.task || '{}';
      return JSON.parse(value);
    } catch (error) {
      return null;
    }
  }

  function updateTaskRowData(row, updates) {
    if (!row || !updates || typeof updates !== 'object') {
      return;
    }
    let payload = {};
    if (row.dataset.task) {
      try {
        payload = JSON.parse(row.dataset.task) || {};
      } catch (error) {
        payload = {};
      }
    }
    const merged = { ...payload, ...updates };
    try {
      row.dataset.task = JSON.stringify(merged);
    } catch (error) {
      // Ignore serialization issues for dataset metadata.
    }
  }

  function setButtonProcessing(button) {
    if (!button || button.dataset.processing === 'true') {
      return () => {};
    }

    const original = {
      html: button.innerHTML,
      disabled: button.disabled,
    };

    button.dataset.processing = 'true';
    button.disabled = true;
    button.classList.add('button--processing');
    button.setAttribute('aria-busy', 'true');
    button.innerHTML =
      '<span class="button__icon button__spinner" aria-hidden="true"></span><span class="button__label">Processing…</span>';

    return () => {
      button.dataset.processing = 'false';
      button.classList.remove('button--processing');
      button.removeAttribute('aria-busy');
      button.innerHTML = original.html;
      button.disabled = original.disabled;
    };
  }

  function setStatusProcessing(row) {
    if (!row) {
      return () => {};
    }
    const statusCell = row.querySelector('[data-label="Status"]');
    if (!statusCell) {
      return () => {};
    }

    const original = statusCell.innerHTML;

    statusCell.textContent = '';
    const badge = document.createElement('span');
    badge.className = 'status status--processing';
    const spinner = document.createElement('span');
    spinner.className = 'status__spinner';
    spinner.setAttribute('aria-hidden', 'true');
    const label = document.createElement('span');
    label.className = 'status__label';
    label.textContent = 'Running…';
    badge.appendChild(spinner);
    badge.appendChild(label);
    statusCell.appendChild(badge);

    return () => {
      statusCell.innerHTML = original;
    };
  }

  function showTaskError(row, message) {
    if (!row) {
      return;
    }
    const statusCell = row.querySelector('[data-label="Status"]');
    if (!statusCell) {
      return;
    }

    const details = typeof message === 'string' && message.trim().length > 0 ? message.trim() : 'Unable to run task.';
    const truncated = details.length > 140 ? `${details.slice(0, 137)}…` : details;

    statusCell.textContent = '';
    const badge = document.createElement('span');
    badge.className = 'status status--error';
    const label = document.createElement('span');
    label.className = 'status__label';
    label.textContent = `Failed: ${truncated}`;
    badge.appendChild(label);
    statusCell.appendChild(badge);

    updateTaskRowData(row, { last_status: 'failed', last_error: details });
  }

  function query(id) {
    return document.getElementById(id);
  }

  function getCompanyContext() {
    const field = query('task-company');
    if (!field) {
      return { value: '', label: 'All companies' };
    }
    const tag = field.tagName ? field.tagName.toUpperCase() : '';
    if (tag === 'SELECT' && field.options && field.options.length > 0) {
      const option = field.options[field.selectedIndex];
      const label = option ? option.textContent.trim() : '';
      return {
        value: field.value ? String(field.value) : '',
        label: label || 'All companies',
      };
    }
    const dataset = field.dataset || {};
    const defaultValue = dataset.defaultValue || '';
    const defaultName = dataset.defaultName || 'All companies';
    return {
      value: field.value ? String(field.value) : defaultValue,
      label: dataset.companyName || defaultName || 'All companies',
    };
  }

  function randomDailyCron() {
    const minute = Math.floor(Math.random() * 60);
    const hour = Math.floor(Math.random() * 24);
    return `${minute} ${hour} * * *`;
  }

  function getTaskNameFields() {
    return {
      hidden: query('task-name'),
      display: query('task-name-display'),
    };
  }

  function generateTaskName() {
    const commandField = query('task-command');
    const companyContext = getCompanyContext();
    const companyName = companyContext.label || 'All companies';
    let commandName = 'Task';
    if (commandField && commandField.options.length > 0) {
      const option = commandField.options[commandField.selectedIndex];
      const label = option ? option.textContent.trim() : '';
      const value = commandField.value ? commandField.value.trim() : '';
      commandName = label || value || commandName;
    }
    return `${companyName} — ${commandName}`;
  }

  function setTaskName(value) {
    const { hidden, display } = getTaskNameFields();
    const name = value || generateTaskName();
    if (hidden) {
      hidden.value = name;
    }
    if (display) {
      display.value = name;
    }
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

  function populateTaskForm(task) {
    const idField = query('task-id');
    const commandField = query('task-command');
    const companyField = query('task-company');
    const companyDisplayField = query('task-company-display');
    if (companyDisplayField) {
      companyDisplayField.readOnly = true;
      companyDisplayField.setAttribute('aria-readonly', 'true');
      companyDisplayField.setAttribute('tabindex', '-1');
    }
    const defaults = getCompanyContext();
    const cronField = query('task-cron');
    const descriptionField = query('task-description');
    const maxRetriesField = query('task-max-retries');
    const backoffField = query('task-backoff');
    const activeField = query('task-active');
    const nameFields = getTaskNameFields();
    if (
      !idField ||
      !commandField ||
      !cronField ||
      !descriptionField ||
      !maxRetriesField ||
      !backoffField ||
      !activeField
    ) {
      return;
    }
    const taskData = task || {};
    const isEditing = Boolean(taskData.id);
    activeTaskContext = {
      id: isEditing && taskData.id ? String(taskData.id) : '',
      name: taskData.name ? String(taskData.name) : '',
    };
    const form = query('scheduled-task-form');
    if (form) {
      form.dataset.taskMode = isEditing ? 'edit' : 'create';
      form.dataset.taskId = activeTaskContext.id;
    }
    idField.value = taskData.id || '';

    const rawCompanyValue =
      taskData.company_id ?? taskData.companyId ?? taskData.company ?? '';
    if (companyField) {
      const dataset = companyField.dataset || {};
      const defaultValue = dataset.defaultValue || defaults.value;
      const defaultName = dataset.defaultName || defaults.label;
      const resolvedCompanyValue =
        rawCompanyValue === null || rawCompanyValue === undefined || rawCompanyValue === ''
          ? defaultValue
          : String(rawCompanyValue);
      companyField.value = resolvedCompanyValue;
      const companyName = taskData.company_name || taskData.companyName || dataset.companyName || defaultName;
      companyField.dataset.companyName = companyName || defaultName;
      if (companyDisplayField) {
        companyDisplayField.value = companyName || defaultName;
      }
    }

    const commandValue = taskData.command || '';
    commandField.value = commandValue;
    if (!commandField.value && commandField.options.length > 0) {
      commandField.value = commandField.options[0].value;
    }

    if (
      companyField &&
      !companyField.value &&
      companyField.tagName &&
      companyField.tagName.toUpperCase() === 'SELECT' &&
      companyField.options.length > 0
    ) {
      companyField.value = '';
    }

    const cronValue = taskData.cron || (isEditing ? '' : randomDailyCron());
    cronField.value = cronValue;

    descriptionField.value = taskData.description || '';

    let maxRetriesValue = Number(taskData.max_retries ?? taskData.maxRetries ?? 12);
    if (!Number.isFinite(maxRetriesValue) || maxRetriesValue < 0) {
      maxRetriesValue = 12;
    }
    maxRetriesField.value = maxRetriesValue;

    let backoffValue = Number(
      taskData.retry_backoff_seconds ?? taskData.retryBackoffSeconds ?? 300
    );
    if (!Number.isFinite(backoffValue) || backoffValue < 30) {
      backoffValue = 300;
    }
    backoffField.value = backoffValue;

    activeField.checked = Boolean(taskData.active !== false);

    const existingName = taskData.name ? String(taskData.name) : '';
    if (nameFields.hidden) {
      nameFields.hidden.dataset.originalName = existingName;
    }
    setTaskName(existingName || generateTaskName());

    const deleteButton = document.querySelector('[data-task-delete-modal]');
    if (deleteButton) {
      deleteButton.dataset.taskId = activeTaskContext.id;
      deleteButton.dataset.taskName = activeTaskContext.name;
      deleteButton.dataset.processing = 'false';
      if (isEditing) {
        deleteButton.hidden = false;
        deleteButton.removeAttribute('aria-hidden');
        deleteButton.disabled = false;
      } else {
        deleteButton.hidden = true;
        deleteButton.setAttribute('aria-hidden', 'true');
        deleteButton.disabled = true;
      }
    }
  }

  function clearTaskForm() {
    populateTaskForm({
      id: '',
      name: '',
      command: '',
      company_id: '',
      cron: '',
      description: '',
      max_retries: 12,
      retry_backoff_seconds: 300,
      active: true,
    });
  }

  function showTaskModal(task) {
    if (!taskModal) {
      return;
    }
    populateTaskForm(task || {});
    openModal(taskModal);
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

  function setLogsPlaceholder(message) {
    const tbody = query('task-logs-body');
    if (!tbody) {
      return;
    }
    tbody.innerHTML = '';
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 5;
    cell.className = 'table__empty';
    cell.textContent = message;
    row.appendChild(cell);
    tbody.appendChild(row);
  }

  function renderTaskLogs(runs) {
    const tbody = query('task-logs-body');
    if (!tbody) {
      return;
    }
    tbody.innerHTML = '';
    if (!runs || runs.length === 0) {
      setLogsPlaceholder('No recent runs recorded for this task.');
      return;
    }
    runs.forEach((run) => {
      const row = document.createElement('tr');

      const statusCell = document.createElement('td');
      statusCell.setAttribute('data-label', 'Status');
      statusCell.textContent = run.status || 'unknown';
      row.appendChild(statusCell);

      const startedCell = document.createElement('td');
      startedCell.setAttribute('data-label', 'Started');
      const started = formatIso(
        run.started_at || run.startedAt || run.startedIso || run.started_iso
      );
      startedCell.setAttribute('data-value', started.value);
      startedCell.textContent = started.text;
      row.appendChild(startedCell);

      const finishedCell = document.createElement('td');
      finishedCell.setAttribute('data-label', 'Finished');
      const finished = formatIso(
        run.finished_at || run.finishedAt || run.finishedIso || run.finished_iso
      );
      finishedCell.setAttribute('data-value', finished.value);
      finishedCell.textContent = finished.text;
      row.appendChild(finishedCell);

      const durationCell = document.createElement('td');
      durationCell.setAttribute('data-label', 'Duration (ms)');
      const duration = typeof run.duration_ms === 'number' ? run.duration_ms : run.durationMs;
      durationCell.setAttribute('data-value', String(duration ?? 0));
      durationCell.textContent = Number.isFinite(duration) ? String(duration) : '—';
      row.appendChild(durationCell);

      const detailsCell = document.createElement('td');
      detailsCell.setAttribute('data-label', 'Details');
      detailsCell.textContent = run.details || '—';
      row.appendChild(detailsCell);

      tbody.appendChild(row);
    });
  }

  async function showTaskLogs(task) {
    if (!task || !task.id) {
      return;
    }
    if (!logsModal) {
      return;
    }
    const title = query('task-logs-title');
    if (title) {
      title.textContent = `Task run history — ${task.name || `Task #${task.id}`}`;
    }
    const description = query('task-logs-description');
    if (description) {
      const commandLabel = task.command ? `Command: ${task.command}.` : '';
      description.textContent = `Displaying up to 50 recent executions for this scheduled task. ${commandLabel}`.trim();
    }
    setLogsPlaceholder('Loading recent runs…');
    openModal(logsModal);
    try {
      const runs = await requestJson(`/scheduler/tasks/${task.id}/runs?limit=50`);
      renderTaskLogs(Array.isArray(runs) ? runs : []);
    } catch (error) {
      setLogsPlaceholder(`Unable to load task runs: ${error.message}`);
    }
  }

  function bindTaskForm() {
    const form = query('scheduled-task-form');
    if (!form) {
      return;
    }
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      const taskId = formData.get('task_id') || formData.get('taskId') || '';
      const payload = {
        name: (formData.get('name') || '').toString().trim(),
        command: (formData.get('command') || '').toString().trim(),
        cron: (formData.get('cron') || '').toString().trim(),
        description: (formData.get('description') || '').toString().trim() || null,
        active: formData.get('active') !== null,
        maxRetries: Number(formData.get('maxRetries') || formData.get('max_retries') || 12),
        retryBackoffSeconds: Number(
          formData.get('retryBackoffSeconds') || formData.get('retry_backoff_seconds') || 300
        ),
      };
      const companyIdValue = formData.get('companyId') || formData.get('company_id');
      if (companyIdValue === null || companyIdValue === undefined || companyIdValue === '') {
        payload.companyId = null;
      } else {
        const parsedCompanyId = Number(companyIdValue);
        if (Number.isNaN(parsedCompanyId)) {
          alert('Company selection is invalid.');
          return;
        }
        payload.companyId = parsedCompanyId;
      }
      if (!payload.name || !payload.command || !payload.cron) {
        alert('Name, command, and cron schedule are required.');
        return;
      }
      if (Number.isNaN(payload.maxRetries) || payload.maxRetries < 0) {
        alert('Max retries must be zero or a positive number.');
        return;
      }
      if (Number.isNaN(payload.retryBackoffSeconds) || payload.retryBackoffSeconds < 30) {
        alert('Retry backoff must be at least 30 seconds.');
        return;
      }
      if (payload.companyId !== null && Number.isNaN(payload.companyId)) {
        alert('Company ID must be a number.');
        return;
      }
      const method = taskId ? 'PUT' : 'POST';
      const url = taskId ? `/scheduler/tasks/${taskId}` : '/scheduler/tasks';
      try {
        await requestJson(url, { method, body: JSON.stringify(payload) });
        window.location.reload();
      } catch (error) {
        alert(`Unable to save task: ${error.message}`);
      }
    });

    const resetButton = form.querySelector('[data-task-reset]');
    if (resetButton) {
      resetButton.addEventListener('click', () => {
        clearTaskForm();
      });
    }

    const deleteButton = form.querySelector('[data-task-delete-modal]');
    if (deleteButton) {
      deleteButton.addEventListener('click', async () => {
        if (deleteButton.dataset.processing === 'true') {
          return;
        }

        const taskId = deleteButton.dataset.taskId || '';
        if (!taskId) {
          return;
        }
        const taskName = (deleteButton.dataset.taskName || '').trim();
        const confirmLabel = taskName ? `"${taskName}"` : `#${taskId}`;
        const message =
          deleteButton.getAttribute('data-confirm') ||
          `Delete scheduled task ${confirmLabel}? This cannot be undone.`;
        if (!window.confirm(message)) {
          return;
        }

        deleteButton.dataset.processing = 'true';
        deleteButton.disabled = true;

        try {
          await requestJson(`/scheduler/tasks/${taskId}`, { method: 'DELETE' });
          window.location.reload();
        } catch (error) {
          deleteButton.dataset.processing = 'false';
          deleteButton.disabled = false;
          const details =
            error instanceof Error && error.message ? error.message : 'Unable to delete task.';
          alert(`Unable to delete task: ${details}`);
        }
      });
    }

    const commandField = query('task-command');
    const companyField = query('task-company');
    const refreshTaskName = () => {
      const nameFields = getTaskNameFields();
      if (nameFields.hidden) {
        nameFields.hidden.dataset.originalName = '';
      }
      setTaskName(generateTaskName());
    };
    if (commandField) {
      commandField.addEventListener('change', refreshTaskName);
      commandField.addEventListener('input', refreshTaskName);
    }
    if (companyField) {
      companyField.addEventListener('change', refreshTaskName);
      companyField.addEventListener('input', refreshTaskName);
    }
  }

  function bindTaskActions() {
    document.querySelectorAll('[data-task-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        const row = button.closest('tr');
        const task = parseTask(row);
        if (task) {
          showTaskModal(task);
        }
      });
    });

    document.querySelectorAll('[data-task-create]').forEach((button) => {
      button.addEventListener('click', () => {
        clearTaskForm();
        showTaskModal({ active: true, retry_backoff_seconds: 300, max_retries: 12 });
      });
    });

    document.querySelectorAll('[data-task-run]').forEach((button) => {
      button.addEventListener('click', async () => {
        if (button.dataset.processing === 'true') {
          return;
        }

        const row = button.closest('tr');
        const task = parseTask(row);
        if (!task || !task.id) {
          return;
        }

        const restoreButton = setButtonProcessing(button);
        const restoreStatus = setStatusProcessing(row);
        updateTaskRowData(row, { last_status: 'running', last_error: null });

        try {
          await requestJson(`/scheduler/tasks/${task.id}/run`, { method: 'POST' });
          window.setTimeout(() => {
            window.location.reload();
          }, 250);
        } catch (error) {
          restoreButton();
          restoreStatus();
          const message = error instanceof Error ? error.message : 'Unable to run task.';
          showTaskError(row, message);
        }
      });
    });

    document.querySelectorAll('[data-task-logs]').forEach((button) => {
      button.addEventListener('click', () => {
        const row = button.closest('tr');
        const task = parseTask(row);
        if (task) {
          showTaskLogs(task);
        }
      });
    });

  }

  function bindAutomationDeleteActions() {
    document.querySelectorAll('[data-automation-delete]').forEach((form) => {
      if (form.dataset.automationDeleteBound === 'true') {
        return;
      }
      form.dataset.automationDeleteBound = 'true';
      form.addEventListener('submit', (event) => {
        const nameRaw = (form.dataset.automationName || '').trim();
        const idRaw = (form.dataset.automationId || '').trim();
        const confirmLabel = nameRaw
          ? `"${nameRaw}"`
          : idRaw
          ? `#${idRaw}`
          : 'this automation';
        const message =
          form.getAttribute('data-confirm') ||
          `Delete automation ${confirmLabel}? This cannot be undone.`;
        if (!window.confirm(message)) {
          event.preventDefault();
        }
      });
    });
  }

  function insertSnippet(field, snippet) {
    if (!field) {
      return;
    }
    const trimmedSnippet = typeof snippet === 'string' ? snippet.trim() : '';
    if (!trimmedSnippet) {
      return;
    }
    const existing = field.value || '';
    const hasContent = existing.trim().length > 0;
    const cleanedExisting = hasContent ? existing.replace(/\s+$/u, '') : '';
    const combined = hasContent ? `${cleanedExisting}\n${trimmedSnippet}` : trimmedSnippet;
    field.value = combined;
    field.dispatchEvent(new Event('input', { bubbles: true }));
    if (typeof field.focus === 'function') {
      field.focus({ preventScroll: false });
      const end = field.value.length;
      if (typeof field.setSelectionRange === 'function') {
        field.setSelectionRange(end, end);
      }
    }
  }

  function setupFilterQuickAdd() {
    const select = document.querySelector('[data-filter-quick-add]');
    const button = document.querySelector('[data-filter-quick-add-apply]');
    const textarea = document.getElementById('automation-filters');
    if (!select || !button || !textarea) {
      return;
    }

    FILTER_SNIPPETS.filter((snippet) => snippet && snippet.value).forEach((snippet) => {
      const option = document.createElement('option');
      option.value = snippet.value;
      option.textContent = snippet.label;
      select.appendChild(option);
    });

    const hasTemplates = select.options.length > 1;
    select.disabled = !hasTemplates;
    button.disabled = !hasTemplates;
    select.value = '';

    button.addEventListener('click', () => {
      const value = select.value;
      if (!value) {
        select.focus();
        return;
      }
      insertSnippet(textarea, value);
      select.value = '';
    });

    select.addEventListener('change', () => {
      if (select.value) {
        button.focus();
      }
    });
  }

  function getActionTemplates(moduleValue) {
    if (!moduleValue) {
      return [];
    }
    const key = String(moduleValue).trim().toLowerCase();
    const templates = ACTION_SNIPPETS[key];
    if (!Array.isArray(templates)) {
      return [];
    }
    return templates.filter((entry) => entry && entry.value);
  }

  function populateActionQuickAdd(select, button, wrapper, moduleValue) {
    if (!select || !button || !wrapper) {
      return;
    }
    while (select.options.length > 1) {
      select.remove(1);
    }
    const templates = getActionTemplates(moduleValue);
    templates.forEach((template) => {
      const option = document.createElement('option');
      option.value = template.value;
      option.textContent = template.label;
      select.appendChild(option);
    });
    const hasTemplates = templates.length > 0;
    select.disabled = !hasTemplates;
    button.disabled = !hasTemplates;
    wrapper.hidden = !hasTemplates;
    if (!hasTemplates) {
      select.value = '';
    }
  }

  function setupTriggerField() {
    const select = document.querySelector('[data-trigger-select]');
    const hidden = document.getElementById('automation-trigger');
    const custom = document.querySelector('[data-trigger-custom]');
    if (!select || !hidden) {
      return;
    }

    const applyValue = () => {
      const value = select.value;
      if (value === '__custom__') {
        if (custom) {
          custom.hidden = false;
          custom.setAttribute('required', 'required');
          hidden.value = (custom.value || '').trim();
        } else {
          hidden.value = '';
        }
      } else {
        if (custom) {
          custom.hidden = true;
          custom.removeAttribute('required');
        }
        hidden.value = value;
      }
    };

    select.addEventListener('change', () => {
      applyValue();
      if (select.value === '__custom__' && custom) {
        custom.focus();
      }
    });

    if (custom) {
      custom.addEventListener('input', () => {
        if (select.value === '__custom__') {
          hidden.value = (custom.value || '').trim();
        }
      });
    }

    applyValue();
  }

  function setupScheduleVisibility() {
    const kindField = document.getElementById('automation-kind');
    const scheduleFields = document.querySelector('[data-schedule-fields]');
    if (!kindField || !scheduleFields) {
      return;
    }

    const toggleSchedule = () => {
      scheduleFields.hidden = kindField.value === 'event';
    };

    kindField.addEventListener('change', toggleSchedule);
    toggleSchedule();
  }

  function setupActionBuilder() {
    const list = document.querySelector('[data-action-list]');
    const addButton = document.querySelector('[data-action-add]');
    const payloadField = document.getElementById('automation-actions-data');
    const moduleField = document.getElementById('automation-action-module-primary');
    const errorField = document.querySelector('[data-action-error]');
    if (!list || !addButton || !payloadField || !moduleField) {
      return;
    }

    let modules = [];
    try {
      modules = JSON.parse(list.dataset.modules || '[]');
    } catch (error) {
      modules = [];
    }

    const moduleOptions = modules
      .map((entry) => ({
        value: String(entry.slug || '').trim(),
        label: String(entry.name || entry.slug || '').trim(),
      }))
      .filter((option) => option.value);

    const createModuleSelect = (value) => {
      const select = document.createElement('select');
      select.className = 'form-input';
      select.setAttribute('data-action-module', 'true');

      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = 'Select module';
      select.appendChild(placeholder);

      moduleOptions.forEach((option) => {
        const opt = document.createElement('option');
        opt.value = option.value;
        opt.textContent = option.label;
        select.appendChild(opt);
      });

      if (value) {
        select.value = value;
      }

      return select;
    };

    const serializeActions = () => {
      const rows = Array.from(list.querySelectorAll('[data-action-row]'));
      const actions = [];
      let errorMessage = '';

      rows.forEach((row, index) => {
        if (errorMessage) {
          return;
        }
        const moduleSelect = row.querySelector('[data-action-module]');
        const payloadFieldInput = row.querySelector('[data-action-payload]');
        const moduleValue = moduleSelect ? moduleSelect.value.trim() : '';
        const payloadText = payloadFieldInput ? payloadFieldInput.value.trim() : '';
        if (!moduleValue) {
          errorMessage = 'Select a module for every trigger action.';
          return;
        }
        let payload = {};
        if (payloadText) {
          try {
            const parsed = JSON.parse(payloadText);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
              throw new Error('Invalid payload');
            }
            payload = parsed;
          } catch (error) {
            errorMessage = `Trigger action ${index + 1} payload must be valid JSON.`;
            return;
          }
        }
        actions.push({ module: moduleValue, payload });
      });

      if (errorMessage) {
        payloadField.value = '';
        moduleField.value = actions.length ? actions[0].module : '';
        if (errorField) {
          errorField.textContent = errorMessage;
          errorField.hidden = false;
        }
        return null;
      }

      if (!actions.length) {
        payloadField.value = '';
        moduleField.value = '';
        if (errorField) {
          errorField.hidden = true;
        }
        return [];
      }

      payloadField.value = JSON.stringify({ actions });
      moduleField.value = actions[0].module;
      if (errorField) {
        errorField.hidden = true;
      }
      return actions;
    };

    const updateState = () => {
      serializeActions();
    };

      let actionRowCounter = 0;

      const createActionRow = (action = null) => {
        actionRowCounter += 1;
        const rowId = actionRowCounter;
        const row = document.createElement('div');
        row.className = 'automation-action';
        row.setAttribute('data-action-row', 'true');

        const moduleWrapper = document.createElement('div');
        moduleWrapper.className = 'automation-action__field';
        let refreshQuickAddOptions = () => {};
        const moduleSelect = createModuleSelect(action && action.module ? action.module : '');
        moduleSelect.addEventListener('change', () => {
          refreshQuickAddOptions();
          updateState();
        });
        moduleWrapper.appendChild(moduleSelect);
        row.appendChild(moduleWrapper);

        const payloadWrapper = document.createElement('div');
        payloadWrapper.className = 'automation-action__field';
        const quickAddWrapper = document.createElement('div');
        quickAddWrapper.className = 'form-quick-add';
        quickAddWrapper.hidden = true;
        const quickAddLabel = document.createElement('label');
        quickAddLabel.className = 'sr-only';
        quickAddLabel.setAttribute('for', `automation-action-quick-add-${rowId}`);
        quickAddLabel.textContent = 'Quick add action payload template';
        const quickAddSelect = document.createElement('select');
        quickAddSelect.className = 'form-input form-quick-add__select';
        quickAddSelect.id = `automation-action-quick-add-${rowId}`;
        quickAddSelect.setAttribute('data-action-quick-add', 'true');
        const quickAddPlaceholder = document.createElement('option');
        quickAddPlaceholder.value = '';
        quickAddPlaceholder.textContent = 'Select a payload template';
        quickAddSelect.appendChild(quickAddPlaceholder);
        const quickAddButton = document.createElement('button');
        quickAddButton.type = 'button';
        quickAddButton.className = 'button button--ghost';
        quickAddButton.textContent = 'Insert template';
        quickAddButton.setAttribute('data-action-quick-add-apply', 'true');
        quickAddWrapper.appendChild(quickAddLabel);
        quickAddWrapper.appendChild(quickAddSelect);
        quickAddWrapper.appendChild(quickAddButton);

        const payloadInput = document.createElement('textarea');
        payloadInput.className = 'form-input';
        payloadInput.rows = 2;
        payloadInput.placeholder = '{}';
        payloadInput.setAttribute('data-action-payload', 'true');
        if (action && action.payload) {
          try {
            payloadInput.value = JSON.stringify(action.payload, null, 2);
          } catch (error) {
            payloadInput.value = '';
          }
        }
        payloadInput.addEventListener('input', updateState);
        quickAddButton.addEventListener('click', () => {
          const templateValue = quickAddSelect.value;
          if (!templateValue) {
            quickAddSelect.focus();
            return;
          }
          insertSnippet(payloadInput, templateValue);
          quickAddSelect.value = '';
          updateState();
        });
        quickAddSelect.addEventListener('change', () => {
          if (quickAddSelect.value) {
            quickAddButton.focus();
          }
        });
        refreshQuickAddOptions = () => {
          populateActionQuickAdd(quickAddSelect, quickAddButton, quickAddWrapper, moduleSelect.value);
        };
        refreshQuickAddOptions();
        payloadWrapper.appendChild(quickAddWrapper);
        payloadWrapper.appendChild(payloadInput);
        row.appendChild(payloadWrapper);

        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'button button--ghost automation-action__remove';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => {
          row.remove();
          updateState();
        });
        row.appendChild(removeButton);

        return row;
      };

    const addRow = (action = null) => {
      const row = createActionRow(action);
      list.appendChild(row);
      updateState();
    };

    addButton.addEventListener('click', (event) => {
      event.preventDefault();
      addRow();
    });

    const initialValue = payloadField.value;
    if (initialValue) {
      try {
        const parsed = JSON.parse(initialValue);
        if (parsed && Array.isArray(parsed.actions)) {
          parsed.actions.forEach((action) => addRow(action));
        }
      } catch (error) {
        addRow();
      }
    }

    if (!list.querySelector('[data-action-row]')) {
      addRow();
    }

    const form = list.closest('form');
    if (form) {
      form.addEventListener('submit', (event) => {
        const result = serializeActions();
        if (result === null) {
          event.preventDefault();
        }
      });
    }
  }

  function setupAutomationForm() {
    setupTriggerField();
    setupFilterQuickAdd();
    setupActionBuilder();
    setupScheduleVisibility();
  }

  function initialiseAutomationUI() {
    taskModal = query('task-editor-modal');
    logsModal = query('task-logs-modal');
    bindModalDismissal(taskModal);
    bindModalDismissal(logsModal);
    clearTaskForm();
    bindTaskForm();
    bindTaskActions();
    bindAutomationDeleteActions();
    setupAutomationForm();
    bindAutomationSectionPagination();
  }

  function dispatchAutomationTableLayoutRefresh() {
    const openSections = document.querySelectorAll('details[data-automation-section][open]');
    openSections.forEach((section) => {
      section.querySelectorAll('table[data-table]').forEach((table) => {
        table.dispatchEvent(new CustomEvent('table:layout-change'));
      });
    });
  }

  function bindAutomationSectionPagination() {
    const sections = document.querySelectorAll('details[data-automation-section]');
    if (!sections.length) {
      return;
    }
    const scheduleRefresh = () => {
      window.requestAnimationFrame(() => {
        dispatchAutomationTableLayoutRefresh();
      });
    };
    sections.forEach((section) => {
      section.addEventListener('toggle', scheduleRefresh);
    });
    scheduleRefresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialiseAutomationUI);
  } else {
    initialiseAutomationUI();
  }
})();
