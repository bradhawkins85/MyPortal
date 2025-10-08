(function () {
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
        ...(options.headers || {}),
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
    return response.status !== 204 ? response.json() : null;
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

  function populateTaskForm(task) {
    const idField = document.getElementById('task-id');
    const nameField = document.getElementById('task-name');
    const commandField = document.getElementById('task-command');
    const companyField = document.getElementById('task-company');
    const cronField = document.getElementById('task-cron');
    const descriptionField = document.getElementById('task-description');
    const maxRetriesField = document.getElementById('task-max-retries');
    const backoffField = document.getElementById('task-backoff');
    const activeField = document.getElementById('task-active');
    if (!idField || !nameField || !commandField || !cronField || !descriptionField || !maxRetriesField || !backoffField || !activeField) {
      return;
    }
    idField.value = task.id || '';
    nameField.value = task.name || '';
    commandField.value = task.command || '';
    companyField.value = task.company_id || '';
    cronField.value = task.cron || '';
    descriptionField.value = task.description || '';
    maxRetriesField.value = typeof task.max_retries === 'number' ? task.max_retries : task.maxRetries || 0;
    backoffField.value = typeof task.retry_backoff_seconds === 'number'
      ? task.retry_backoff_seconds
      : task.retryBackoffSeconds || 300;
    activeField.checked = Boolean(task.active !== false);
    nameField.focus();
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

  function bindTaskForm() {
    const form = document.getElementById('scheduled-task-form');
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
        retryBackoffSeconds: Number(formData.get('retryBackoffSeconds') || formData.get('retry_backoff_seconds') || 300),
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
          populateTaskForm(task);
        }
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

  function bindWebhookActions() {
    document.querySelectorAll('[data-webhook-retry]').forEach((button) => {
      button.addEventListener('click', async () => {
        if (button.disabled) {
          return;
        }
        const row = button.closest('tr');
        const eventId = row ? row.getAttribute('data-event-id') : null;
        if (!eventId) {
          return;
        }
        try {
          await requestJson(`/scheduler/webhooks/${eventId}/retry`, { method: 'POST' });
          window.location.reload();
        } catch (error) {
          alert(`Unable to retry webhook: ${error.message}`);
        }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindTaskForm();
    bindTaskActions();
    bindWebhookActions();
  });
})();
