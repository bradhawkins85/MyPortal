(function () {
  let taskModal = null;
  let logsModal = null;

  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getCsrfToken() {
    return getCookie('myportal_session_csrf');
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

  function populateTaskForm(task) {
    const idField = query('task-id');
    const nameField = query('task-name');
    const commandField = query('task-command');
    const companyField = query('task-company');
    const cronField = query('task-cron');
    const descriptionField = query('task-description');
    const maxRetriesField = query('task-max-retries');
    const backoffField = query('task-backoff');
    const activeField = query('task-active');
    if (
      !idField ||
      !nameField ||
      !commandField ||
      !cronField ||
      !descriptionField ||
      !maxRetriesField ||
      !backoffField ||
      !activeField
    ) {
      return;
    }
    idField.value = task.id || '';
    nameField.value = task.name || '';
    commandField.value = task.command || '';
    companyField.value = task.company_id || '';
    cronField.value = task.cron || '';
    descriptionField.value = task.description || '';
    maxRetriesField.value =
      typeof task.max_retries === 'number' ? task.max_retries : task.maxRetries || 0;
    backoffField.value =
      typeof task.retry_backoff_seconds === 'number'
        ? task.retry_backoff_seconds
        : task.retryBackoffSeconds || 300;
    activeField.checked = Boolean(task.active !== false);
  }

  function clearTaskForm() {
    populateTaskForm({
      id: '',
      name: '',
      command: '',
      company_id: '',
      cron: '',
      description: '',
      max_retries: 0,
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
        maxRetries: Number(formData.get('maxRetries') || formData.get('max_retries') || 0),
        retryBackoffSeconds: Number(
          formData.get('retryBackoffSeconds') || formData.get('retry_backoff_seconds') || 300
        ),
      };
      const companyIdValue = formData.get('companyId') || formData.get('company_id');
      payload.companyId = companyIdValue ? Number(companyIdValue) : null;
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
        showTaskModal({ active: true, retry_backoff_seconds: 300, max_retries: 0 });
      });
    });

    document.querySelectorAll('[data-task-toggle]').forEach((button) => {
      button.addEventListener('click', async () => {
        const row = button.closest('tr');
        const task = parseTask(row);
        if (!task || !task.id) {
          return;
        }
        try {
          await requestJson(`/scheduler/tasks/${task.id}/activate`, {
            method: 'POST',
            body: JSON.stringify({ active: !task.active }),
          });
          window.location.reload();
        } catch (error) {
          alert(`Unable to update task: ${error.message}`);
        }
      });
    });

    document.querySelectorAll('[data-task-run]').forEach((button) => {
      button.addEventListener('click', async () => {
        const row = button.closest('tr');
        const task = parseTask(row);
        if (!task || !task.id) {
          return;
        }
        try {
          await requestJson(`/scheduler/tasks/${task.id}/run`, { method: 'POST' });
          alert('Task queued for execution. Refresh the page to see updates.');
        } catch (error) {
          alert(`Unable to run task: ${error.message}`);
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

    document.querySelectorAll('[data-task-delete]').forEach((button) => {
      button.addEventListener('click', async () => {
        const row = button.closest('tr');
        const task = parseTask(row);
        if (!task || !task.id) {
          return;
        }
        if (!confirm(`Delete task "${task.name}"? This cannot be undone.`)) {
          return;
        }
        try {
          await requestJson(`/scheduler/tasks/${task.id}`, { method: 'DELETE' });
          window.location.reload();
        } catch (error) {
          alert(`Unable to delete task: ${error.message}`);
        }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    taskModal = query('task-editor-modal');
    logsModal = query('task-logs-modal');
    bindModalDismissal(taskModal);
    bindModalDismissal(logsModal);
    clearTaskForm();
    bindTaskForm();
    bindTaskActions();
  });
})();
