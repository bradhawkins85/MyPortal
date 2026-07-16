(function () {
  const actionPayloadEditor = window.MyPortalActionPayloadEditor || null;
  let taskModal = null;
  let logsModal = null;
  let previewModal = null;
  let bulkTaskModal = null;
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
      label: 'Match ticket status equals "open"',
      value: toJsonTemplate({
        match: {
          'ticket.status': 'open',
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
      label: 'Ticket is older than 30 days',
      value: toJsonTemplate({
        greater_than: {
          'ticket.age_days': 30,
        },
      }),
    },
    {
      label: 'Ticket has not been updated for 7 days',
      value: toJsonTemplate({
        greater_than: {
          'ticket.updated_age_days': 7,
        },
      }),
    },
    {
      label: 'Ticket has been in current status for 24 hours',
      value: toJsonTemplate({
        greater_than: {
          'ticket.in_status_age_hours': 24,
        },
      }),
    },
    {
      label: 'Ticket has not had a reply for 24 hours',
      value: toJsonTemplate({
        greater_than: {
          'ticket.last_reply_age_hours': 24,
        },
      }),
    },
    {
      label: 'Match any ticket status open or pending',
      value: toJsonTemplate({
        any: [
          { match: { 'ticket.status': 'open' } },
          { match: { 'ticket.status': 'pending' } },
        ],
      }),
    },
    {
      label: 'Require ticket status open and priority high',
      value: toJsonTemplate({
        all: [
          { match: { 'ticket.status': 'open' } },
          { match: { 'ticket.priority': 'high' } },
        ],
      }),
    },
    {
      label: 'Exclude cancelled ticket status',
      value: toJsonTemplate({
        not: {
          match: {
            'ticket.status': 'cancelled',
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
    {
      label: 'Reply is an internal note',
      value: toJsonTemplate({
        match: {
          'reply.is_internal': true,
        },
      }),
    },
    {
      label: 'Reply is a public message',
      value: toJsonTemplate({
        match: {
          'reply.is_internal': false,
        },
      }),
    },
    {
      label: 'Module equals Trello',
      value: toJsonTemplate({
        match: {
          'ticket.module_slug': 'trello',
        },
      }),
    },
    {
      label: 'External ID is not empty',
      value: toJsonTemplate({
        not: {
          match: {
            'ticket.external_reference': null,
          },
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
    smtp2go: [
      {
        label: 'Full SMTP2Go API payload template',
        value: toJsonTemplate({
          sender: '{{company.email}}',
          to: [
            '{{ticket.requester.email}}',
            '{{ticket.assigned_user.email}}',
          ],
          cc: [
            'manager@example.com',
          ],
          bcc: [
            'archive@example.com',
          ],
          subject: 'Ticket #{{ticket.number}} - {{ticket.subject}}',
          html_body: '<h1>Ticket Update</h1><p>Ticket #{{ticket.number}} has been updated.</p><p>Status: {{ticket.status}}</p>',
          text_body: 'Ticket Update\n\nTicket #{{ticket.number}} has been updated.\n\nStatus: {{ticket.status}}',
          attachments: [
            {
              filename: 'report.pdf',
              content: 'JVBERi0xLjQKJ...',
            },
          ],
          custom_headers: [
            {
              header: 'X-Ticket-ID',
              value: '{{ticket.id}}',
            },
            {
              header: 'X-Company-ID',
              value: '{{company.id}}',
            },
          ],
          template_id: 'your_template_id',
          template_data: {
            ticket_number: '{{ticket.number}}',
            ticket_subject: '{{ticket.subject}}',
            requester_name: '{{ticket.requester.name}}',
            company_name: '{{company.name}}',
          },
        }),
      },
      {
        label: 'Simple notification with tracking',
        value: toJsonTemplate({
          sender: '{{company.email}}',
          to: [
            '{{ticket.requester.email}}',
          ],
          subject: 'Re: Ticket #{{ticket.number}}',
          html_body: '<p>Your ticket has been updated.</p>',
          text_body: 'Your ticket has been updated.',
        }),
      },
    ],
    'sms-gateway': [
      {
        label: 'Send technician reply to SMS requester',
        value: toJsonTemplate({
          message: '{{ ticket.latest_reply.body }}',
          phoneNumbers: ['{{ ticket.sms.recipient }}'],
        }),
      },
      {
        label: 'Send custom SMS update',
        value: toJsonTemplate({
          message: 'Ticket {{ ticket.number }} has been updated: {{ ticket.latest_reply.body }}',
          phoneNumbers: ['{{ ticket.sms.recipient }}'],
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
    'create-ticket': [
      {
        label: 'Create escalation ticket for current requester',
        value: toJsonTemplate({
          subject: 'Escalation for ticket {{ ticket.ticket_number }}',
          description:
            'Follow up on {{ ticket.ticket_number }}. Include investigation details and notify the duty engineer.',
          priority: 'high',
          status: 'open',
          requester_id: '{{ ticket.requester_id }}',
          company_id: '{{ ticket.company_id }}',
        }),
      },
      {
        label: 'Create proactive maintenance ticket',
        value: toJsonTemplate({
          subject: 'Proactive maintenance follow-up',
          description: 'Log a preventative maintenance visit from this automation trigger.',
          priority: 'normal',
          status: 'open',
          assigned_user_id: '{{ staff.id }}',
          module_slug: 'automations',
        }),
      },
    ],
    'create-task': [
      {
        label: 'Add follow-up task to current ticket',
        value: toJsonTemplate({
          context: {
            ticket_id: '{{ ticket.id }}',
          },
          task_name: 'Call customer to confirm resolution',
          sort_order: 10,
        }),
      },
      {
        label: 'Add multiple checklist tasks',
        value: toJsonTemplate({
          context: {
            ticket_id: '{{ ticket.id }}',
          },
          tasks: [
            {
              task_name: 'Review diagnostic logs',
              sort_order: 10,
            },
            {
              task_name: 'Document remediation steps',
              sort_order: 20,
            },
          ],
        }),
      },
    ],
    'update-ticket': [
      {
        label: 'Change ticket status to resolved',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          status: 'resolved',
        }),
      },
      {
        label: 'Escalate ticket to high priority',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          priority: 'high',
        }),
      },
      {
        label: 'Assign ticket to specific technician',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          assigned_user_id: 1,
        }),
      },
      {
        label: 'Update multiple ticket fields',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          status: 'in_progress',
          priority: 'high',
          assigned_user_id: '{{ staff.id }}',
          category: 'escalation',
        }),
      },
      {
        label: 'Unassign ticket',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          assigned_user_id: null,
        }),
      },
    ],
    'update-ticket-description': [
      {
        label: 'Append escalation note to description',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          description: '{{ ticket.description }}\n\n---\nEscalated by automation on {{ now }}.',
        }),
      },
      {
        label: 'Set new description',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          description: 'Updated description content goes here.',
        }),
      },
    ],
    'reprocess-ai': [
      {
        label: 'Refresh AI summary and tags',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          refresh_summary: true,
          refresh_tags: true,
        }),
      },
      {
        label: 'Refresh AI summary only',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          refresh_summary: true,
          refresh_tags: false,
        }),
      },
      {
        label: 'Refresh AI tags only',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          refresh_summary: false,
          refresh_tags: true,
        }),
      },
    ],
    'add-ticket-reply': [
      {
        label: 'Add public reply',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          body: '<p>Thank you for contacting support. We are looking into this issue.</p>',
          is_internal: false,
        }),
      },
      {
        label: 'Add internal note',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          body: 'Internal investigation note: Checking system logs for related errors.',
          is_internal: true,
        }),
      },
      {
        label: 'Add billable time entry',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          body: 'Completed remote troubleshooting session.',
          is_internal: false,
          minutes_spent: 30,
          is_billable: true,
        }),
      },
      {
        label: 'Add non-billable internal note with time',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          body: 'Initial triage and assessment completed.',
          is_internal: true,
          minutes_spent: 15,
          is_billable: false,
        }),
      },
      {
        label: 'Add automated resolution reply',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          body: '<p>This issue has been automatically resolved. Please contact us if you experience any further problems.</p>',
          is_internal: false,
          author_id: null,
        }),
      },
    ],
    whisperx: [
      {
        label: 'Transcribe voicemail attachments and add internal note',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          add_note: true,
        }),
      },
      {
        label: 'Transcribe with language override (no note)',
        value: toJsonTemplate({
          ticket_id: '{{ ticket.id }}',
          add_note: false,
          language: 'en',
        }),
      },
    ],
    trello: [
      {
        label: 'Add comment to linked Trello card',
        value: toJsonTemplate({
          card_id: '{{ticket.external_reference}}',
          text: 'Ticket #{{ticket.number}} updated: {{ticket.subject}}',
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

  const COMMAND_DEFAULT_CRONS = {
    unbill_time_entries: '0 0 L * *',
    generate_invoice: '1 0 L * *',
    sync_to_xero: '1 15 L * *',
    sync_to_xero_auto_send: '1 16 L * *',
  };

  function defaultCronForCommand(command) {
    return COMMAND_DEFAULT_CRONS[command] || randomDailyCron();
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

  function toggleJsonPayloadField() {
    const commandField = query('task-command');
    const jsonPayloadField = query('task-json-payload-field');
    const descriptionField = query('task-description-field');
    
    if (commandField && jsonPayloadField && descriptionField) {
      const command = commandField.value;
      if (command === 'create_scheduled_ticket') {
        jsonPayloadField.style.display = 'block';
        descriptionField.style.display = 'none';
      } else {
        jsonPayloadField.style.display = 'none';
        descriptionField.style.display = 'block';
      }
    }
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
    const excludeCalendarField = query('task-exclude-calendar');
    const nameFields = getTaskNameFields();
    if (
      !idField ||
      !commandField ||
      !cronField ||
      !descriptionField ||
      !maxRetriesField ||
      !backoffField ||
      !activeField ||
      !excludeCalendarField
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

    const cronValue = taskData.cron || (isEditing ? '' : defaultCronForCommand(commandField.value || commandValue));
    cronField.value = cronValue;

    const command = commandValue || '';
    const description = taskData.description || '';
    
    // If command is create_scheduled_ticket, populate JSON payload field
    const jsonPayloadTextarea = query('task-json-payload');
    if (command === 'create_scheduled_ticket' && description) {
      if (jsonPayloadTextarea) {
        jsonPayloadTextarea.value = description;
      }
      descriptionField.value = '';
    } else {
      descriptionField.value = description;
      if (jsonPayloadTextarea) {
        jsonPayloadTextarea.value = '';
      }
    }

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
    excludeCalendarField.checked = Boolean(
      taskData.exclude_from_calendar ?? taskData.excludeFromCalendar ?? false
    );

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
    
    // Toggle field visibility after populating the form
    toggleJsonPayloadField();
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


  function humanizePreviewKey(key) {
    return String(key || '')
      .replace(/([A-Z])/g, ' $1')
      .replace(/^./, (char) => char.toUpperCase());
  }

  function formatPreviewValue(value) {
    if (value === null || value === undefined || value === '') {
      return '';
    }
    if (Array.isArray(value)) {
      return value.map(formatPreviewValue).filter(Boolean).join(', ');
    }
    if (typeof value === 'object') {
      return Object.entries(value)
        .filter(([, nestedValue]) => nestedValue !== null && nestedValue !== undefined && nestedValue !== '')
        .map(([nestedKey, nestedValue]) => `${humanizePreviewKey(nestedKey)}: ${formatPreviewValue(nestedValue)}`)
        .join('; ');
    }
    return String(value);
  }

  function formatPreviewDetails(item) {
    if (!item || typeof item !== 'object') {
      return '—';
    }
    const hidden = new Set(['type', 'id', 'label', 'action']);
    return Object.entries(item)
      .filter(([key, value]) => !hidden.has(key) && value !== null && value !== undefined && value !== '')
      .map(([key, value]) => `${humanizePreviewKey(key)}: ${formatPreviewValue(value)}`)
      .filter(Boolean)
      .join(' · ') || '—';
  }

  function renderTaskPreview(preview) {
    const summary = query('task-preview-summary');
    if (summary) {
      summary.textContent = (preview && preview.summary) || 'No preview details were returned.';
    }
    const totals = query('task-preview-totals');
    if (totals) {
      const entries = Object.entries((preview && preview.totals) || {});
      totals.innerHTML = '';
      totals.hidden = entries.length < 1;
      entries.forEach(([key, value]) => {
        const item = document.createElement('div');
        item.className = 'detail-grid__item';
        const label = document.createElement('span');
        label.className = 'detail-grid__label';
        label.textContent = humanizePreviewKey(key);
        const data = document.createElement('strong');
        data.className = 'detail-grid__value';
        data.textContent = String(value);
        item.append(label, data);
        totals.appendChild(item);
      });
    }
    const tbody = query('task-preview-body');
    if (!tbody) {
      return;
    }
    tbody.innerHTML = '';
    const items = Array.isArray(preview && preview.items) ? preview.items : [];
    if (items.length < 1) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 4;
      cell.className = 'table__empty';
      cell.textContent = 'No matching records would be processed.';
      row.appendChild(cell);
      tbody.appendChild(row);
      return;
    }
    items.forEach((item) => {
      const row = document.createElement('tr');
      ['type', 'label', 'action'].forEach((key) => {
        const cell = document.createElement('td');
        cell.textContent = item && item[key] ? String(item[key]) : '—';
        row.appendChild(cell);
      });
      const details = document.createElement('td');
      details.textContent = formatPreviewDetails(item);
      row.appendChild(details);
      tbody.appendChild(row);
    });
  }

  async function showTaskPreview(task) {
    if (!task || !task.id || !previewModal) {
      return;
    }
    const title = query('task-preview-title');
    if (title) {
      title.textContent = `Preview — ${task.name || `Task #${task.id}`}`;
    }
    const summary = query('task-preview-summary');
    if (summary) {
      summary.textContent = 'Loading preview…';
    }
    renderTaskPreview({ summary: 'Loading preview…', items: [] });
    openModal(previewModal);
    try {
      const preview = await requestJson(`/scheduler/tasks/${task.id}/preview`, { cache: 'no-store' });
      renderTaskPreview(preview || {});
    } catch (error) {
      renderTaskPreview({ summary: `Unable to load preview: ${error.message}`, items: [] });
    }
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
      const runs = await requestJson(`/scheduler/tasks/${task.id}/runs?limit=50`, { cache: 'no-store' });
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
      const command = (formData.get('command') || '').toString().trim();
      let description = (formData.get('description') || '').toString().trim() || null;
      
      // For create_scheduled_ticket command, use JSON payload as description
      if (command === 'create_scheduled_ticket') {
        const jsonPayload = (formData.get('jsonPayload') || '').toString().trim();
        if (jsonPayload) {
          // Validate JSON
          try {
            JSON.parse(jsonPayload);
            description = jsonPayload;
          } catch (error) {
            alert('Invalid JSON payload. Please check your JSON syntax.');
            return;
          }
        } else {
          alert('JSON payload is required for scheduled ticket creation.');
          return;
        }
      }
      
      const payload = {
        name: (formData.get('name') || '').toString().trim(),
        command: command,
        cron: (formData.get('cron') || '').toString().trim(),
        description: description,
        active: formData.get('active') !== null,
        excludeFromCalendar: formData.get('excludeFromCalendar') !== null,
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
        saveTaskFilter();
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
          saveTaskFilter();
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
      commandField.addEventListener('change', () => {
        toggleJsonPayloadField();
        refreshTaskName();
      });
      commandField.addEventListener('input', () => {
        toggleJsonPayloadField();
        refreshTaskName();
      });
      // Initialize on page load
      toggleJsonPayloadField();
    }
    if (companyField) {
      companyField.addEventListener('change', refreshTaskName);
      companyField.addEventListener('input', refreshTaskName);
    }
  }

  const TASK_FILTER_SESSION_KEY = 'portal.scheduled-tasks.filter';

  function saveTaskFilter() {
    try {
      const input = document.querySelector('[data-table-filter="scheduled-tasks-table"]');
      const value = input ? input.value : '';
      if (value) {
        sessionStorage.setItem(TASK_FILTER_SESSION_KEY, value);
      } else {
        sessionStorage.removeItem(TASK_FILTER_SESSION_KEY);
      }
    } catch (err) {
      // sessionStorage unavailable — ignore
    }
  }

  function restoreTaskFilter() {
    try {
      const value = sessionStorage.getItem(TASK_FILTER_SESSION_KEY);
      if (!value) {
        return;
      }
      sessionStorage.removeItem(TASK_FILTER_SESSION_KEY);
      const input = document.querySelector('[data-table-filter="scheduled-tasks-table"]');
      if (input) {
        input.value = value;
        // The table filter listens for 'input' events, so dispatch one to trigger filtering.
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    } catch (err) {
      // sessionStorage unavailable — ignore
    }
  }

  // Closes the row's action <details> dropdown (data-header-menu) when an action button inside
  // it is activated. The [data-header-menu] element is always a <details> element in this page.
  function closeRowDropdown(button) {
    const menu = button.closest('[data-header-menu]');
    if (menu && typeof menu.open !== 'undefined') {
      menu.open = false;
    }
  }


  function bindBulkTaskCreateForm() {
    const openButtons = document.querySelectorAll('[data-bulk-task-create]');
    openButtons.forEach((button) => {
      button.addEventListener('click', () => {
        if (bulkTaskModal) {
          openModal(bulkTaskModal);
        }
      });
    });

    const form = query('bulk-scheduled-task-form');
    if (!form) {
      return;
    }

    const commandField = query('bulk-task-command');
    const descriptionField = query('bulk-task-description-field');
    const jsonPayloadField = query('bulk-task-json-payload-field');
    const jsonPayloadInput = query('bulk-task-json-payload');

    const togglePayload = () => {
      const requiresPayload = commandField && commandField.value === 'create_scheduled_ticket';
      if (descriptionField) {
        descriptionField.hidden = Boolean(requiresPayload);
      }
      if (jsonPayloadField) {
        jsonPayloadField.hidden = !requiresPayload;
      }
      if (jsonPayloadInput) {
        jsonPayloadInput.required = Boolean(requiresPayload);
      }
    };

    if (commandField) {
      commandField.addEventListener('change', togglePayload);
      togglePayload();
    }

    form.addEventListener('submit', (event) => {
      const selectedCompanies = form.querySelectorAll('input[name="companyIds"]:checked');
      if (selectedCompanies.length < 1) {
        event.preventDefault();
        window.alert('Select at least one active company.');
        return;
      }
      if (commandField && commandField.value === 'create_scheduled_ticket' && jsonPayloadInput) {
        try {
          JSON.parse(jsonPayloadInput.value || '');
        } catch (error) {
          event.preventDefault();
          window.alert('Invalid JSON payload. Please check your JSON syntax.');
        }
      }
    });
  }

  function bindTaskActions() {
    document.querySelectorAll('[data-task-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        closeRowDropdown(button);
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

        closeRowDropdown(button);

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
            saveTaskFilter();
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

    document.querySelectorAll('[data-task-preview]').forEach((button) => {
      button.addEventListener('click', () => {
        closeRowDropdown(button);
        const row = button.closest('tr');
        const task = parseTask(row);
        if (task) {
          showTaskPreview(task);
        }
      });
    });

    document.querySelectorAll('[data-task-logs]').forEach((button) => {
      button.addEventListener('click', () => {
        closeRowDropdown(button);
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

  function setupTriggerFilterBuilder() {
    const container = document.querySelector('[data-filter-builder]');
    if (!container) {
      return;
    }
    const list = container.querySelector('[data-filter-builder-list]');
    const addButton = container.querySelector('[data-filter-add-condition]');
    const errorEl = container.querySelector('[data-filter-builder-error]');
    const advancedToggle = container.querySelector('[data-filter-advanced-toggle]');
    const advancedPanel = container.querySelector('[data-filter-advanced-panel]');
    const advancedEditor = query('automation-filters-advanced');
    const hiddenInput = query('automation-filters-data');
    const modeInput = query('automation-filters-mode');
    if (!list || !addButton || !advancedToggle || !advancedPanel || !advancedEditor || !hiddenInput || !modeInput) {
      return;
    }

    let builderState = { all: [] };
    let advancedMode = false;

    const setError = (message) => {
      if (!errorEl) {
        return;
      }
      if (!message) {
        errorEl.hidden = true;
        errorEl.textContent = '';
        return;
      }
      errorEl.hidden = false;
      errorEl.textContent = message;
    };

    const parseTypedValue = (valueType, rawValue) => {
      if (valueType === 'null') {
        return null;
      }
      if (valueType === 'boolean') {
        return String(rawValue).trim().toLowerCase() === 'true';
      }
      if (valueType === 'number') {
        const parsed = Number(String(rawValue).trim());
        return Number.isFinite(parsed) ? parsed : 0;
      }
      return String(rawValue);
    };

    const inferType = (value) => {
      if (value === null) {
        return 'null';
      }
      if (typeof value === 'boolean') {
        return 'boolean';
      }
      if (typeof value === 'number' && Number.isFinite(value)) {
        return 'number';
      }
      return 'string';
    };

    const makeRowState = (partial) => ({
      conditionType: partial && partial.conditionType ? partial.conditionType : 'match',
      fieldPath: partial && partial.fieldPath ? partial.fieldPath : '',
      operator: partial && partial.operator ? partial.operator : 'equals',
      valueType: partial && partial.valueType ? partial.valueType : 'string',
      value: partial && Object.prototype.hasOwnProperty.call(partial, 'value') ? partial.value : '',
    });

    const filterNodeToRow = (node) => {
      if (!node || typeof node !== 'object') {
        return null;
      }
      const operatorNodes = {
        match: 'equals',
        not_equals: 'not_equals',
        in: 'in',
        not_in: 'not_in',
        greater_than: 'greater_than',
        gt: 'greater_than',
        greater_than_or_equal: 'greater_than_or_equal',
        gte: 'greater_than_or_equal',
        less_than: 'less_than',
        lt: 'less_than',
        less_than_or_equal: 'less_than_or_equal',
        lte: 'less_than_or_equal',
        starts_with: 'starts_with',
        ends_with: 'ends_with',
        contains: 'contains',
        not_contains: 'not_contains',
        regex: 'regex',
      };
      const operatorKey = Object.keys(operatorNodes).find((key) => (
        node[key] && typeof node[key] === 'object' && !Array.isArray(node[key])
      ));
      if (operatorKey) {
        const entries = Object.entries(node[operatorKey]);
        if (entries.length < 1) {
          return null;
        }
        const [fieldPath, value] = entries[0];
        return makeRowState({
          conditionType: 'match',
          fieldPath: String(fieldPath),
          operator: operatorNodes[operatorKey],
          valueType: inferType(value),
          value: value === null ? '' : String(value),
        });
      }
      if (Array.isArray(node.all) || Array.isArray(node.any)) {
        const key = Array.isArray(node.all) ? 'all' : 'any';
        const clauses = node[key];
        if (!Array.isArray(clauses) || clauses.length < 1) {
          return null;
        }
        const child = filterNodeToRow(clauses[0]);
        if (!child) {
          return null;
        }
        return makeRowState({
          ...child,
          conditionType: key,
        });
      }
      if (node.not && typeof node.not === 'object') {
        const child = filterNodeToRow(node.not);
        if (!child) {
          return null;
        }
        return makeRowState({
          ...child,
          conditionType: 'not',
        });
      }
      return null;
    };

    const filtersToRows = (filters) => {
      if (!filters || typeof filters !== 'object') {
        return [];
      }
      if (Array.isArray(filters.all)) {
        return filters.all.map((node) => filterNodeToRow(node)).filter(Boolean);
      }
      const asNode = filterNodeToRow(filters);
      if (asNode) {
        return [asNode];
      }
      return [];
    };

    const rowToFilterNode = (row) => {
      const fieldPath = String(row.fieldPath || '').trim();
      if (!fieldPath) {
        return null;
      }
      const typedValue = parseTypedValue(row.valueType, row.value);
      const operatorKey = {
        not_equals: 'not_equals',
        in: 'in',
        not_in: 'not_in',
        greater_than: 'greater_than',
        greater_than_or_equal: 'greater_than_or_equal',
        less_than: 'less_than',
        less_than_or_equal: 'less_than_or_equal',
        starts_with: 'starts_with',
        ends_with: 'ends_with',
        contains: 'contains',
        not_contains: 'not_contains',
        regex: 'regex',
      }[row.operator] || 'match';
      const matchNode = { [operatorKey]: { [fieldPath]: typedValue } };
      if (row.conditionType === 'all') {
        return { all: [matchNode] };
      }
      if (row.conditionType === 'any') {
        return { any: [matchNode] };
      }
      if (row.conditionType === 'not') {
        return { not: matchNode };
      }
      return matchNode;
    };

    const serializeBuilderState = () => {
      const rows = Array.isArray(builderState.all) ? builderState.all : [];
      const nodes = rows.map((row) => rowToFilterNode(row)).filter(Boolean);
      if (nodes.length < 1) {
        return null;
      }
      return nodes.length === 1 ? nodes[0] : { all: nodes };
    };

    const updateHiddenValue = () => {
      const payload = serializeBuilderState();
      const serialized = payload ? JSON.stringify(payload, null, 2) : '';
      hiddenInput.value = serialized;
      return serialized;
    };

    const handleRowChange = (index, key, value) => {
      if (!builderState.all[index]) {
        return;
      }
      builderState.all[index][key] = value;
      const serialized = updateHiddenValue();
      if (advancedMode) {
        advancedEditor.value = serialized;
      }
    };

    const removeRow = (index) => {
      builderState.all.splice(index, 1);
      renderRows();
    };

    function renderRows() {
      list.textContent = '';
      if (!Array.isArray(builderState.all) || builderState.all.length < 1) {
        const empty = document.createElement('p');
        empty.className = 'form-help';
        empty.textContent = 'No conditions yet. Add one to build trigger filters.';
        list.appendChild(empty);
      }
      builderState.all.forEach((row, index) => {
        const rowWrap = document.createElement('div');
        rowWrap.className = 'form-grid';

        const typeSelect = document.createElement('select');
        typeSelect.className = 'form-input';
        ['match', 'all', 'any', 'not'].forEach((type) => {
          const option = document.createElement('option');
          option.value = type;
          option.textContent = type;
          if (row.conditionType === type) {
            option.selected = true;
          }
          typeSelect.appendChild(option);
        });
        typeSelect.addEventListener('change', () => handleRowChange(index, 'conditionType', typeSelect.value));

        const fieldInput = document.createElement('input');
        fieldInput.className = 'form-input';
        fieldInput.type = 'text';
        fieldInput.placeholder = 'ticket.status or reply.is_internal';
        fieldInput.setAttribute('list', 'automation-filter-field-suggestions');
        if (!document.getElementById('automation-filter-field-suggestions')) {
          const datalist = document.createElement('datalist');
          datalist.id = 'automation-filter-field-suggestions';
          [
            'ticket.subject',
            'ticket.body',
            'ticket.status',
            'ticket.priority',
            'ticket.requester_email',
            'ticket.requester_display_name',
            'ticket.assigned_user_email',
            'ticket.assigned_user_display_name',
            'ticket.company_name',
            'ticket.company.id',
            'ticket.category',
            'ticket.external_reference',
            'ticket.billable_minutes',
            'ticket.non_billable_minutes',
            'ticket.has_attachments',
            'ticket.attachment_count',
            'ticket.expense_total',
            'ticket.expense_count',
            'ticket.has_expenses',
            'ticket.has_tasks',
            'ticket.task_count',
            'ticket.has_open_tasks',
            'ticket.open_task_count',
            'ticket.ai_tags',
            'ticket.labels',
            'ticket.age_days',
            'ticket.updated_age_hours',
            'ticket.in_status_age_hours',
            'ticket.last_reply_age_hours',
            'ticket_update.actor_type',
            'reply.is_internal',
            'reply.kind',
          ].forEach((field) => {
            const option = document.createElement('option');
            option.value = field;
            datalist.appendChild(option);
          });
          document.body.appendChild(datalist);
        }
        fieldInput.value = row.fieldPath || '';
        fieldInput.addEventListener('input', () => handleRowChange(index, 'fieldPath', fieldInput.value));

        const operatorSelect = document.createElement('select');
        operatorSelect.className = 'form-input';
        [
          ['equals', 'equals'],
          ['not_equals', 'not equals'],
          ['in', 'in'],
          ['not_in', 'not in'],
          ['greater_than', 'greater than'],
          ['greater_than_or_equal', 'greater than or equal'],
          ['less_than', 'less than'],
          ['less_than_or_equal', 'less than or equal'],
          ['starts_with', 'starts with'],
          ['ends_with', 'ends with'],
          ['contains', 'contains'],
          ['not_contains', 'not contains'],
          ['regex', 'custom regex'],
        ].forEach(([value, label]) => {
          const option = document.createElement('option');
          option.value = value;
          option.textContent = label;
          operatorSelect.appendChild(option);
        });
        operatorSelect.value = row.operator || 'equals';
        operatorSelect.addEventListener('change', () => {
          handleRowChange(index, 'operator', operatorSelect.value);
          valueInput.placeholder = operatorSelect.value === 'in' || operatorSelect.value === 'not_in' ? 'Resolved, Closed' : 'value';
        });

        const valueTypeSelect = document.createElement('select');
        valueTypeSelect.className = 'form-input';
        ['string', 'number', 'boolean', 'null'].forEach((type) => {
          const option = document.createElement('option');
          option.value = type;
          option.textContent = type;
          if (row.valueType === type) {
            option.selected = true;
          }
          valueTypeSelect.appendChild(option);
        });

        const valueInput = document.createElement('input');
        valueInput.className = 'form-input';
        valueInput.type = 'text';
        valueInput.placeholder = row.operator === 'in' || row.operator === 'not_in' ? 'Resolved, Closed' : 'value';
        valueInput.value = row.value || '';
        valueInput.disabled = row.valueType === 'null';

        valueTypeSelect.addEventListener('change', () => {
          handleRowChange(index, 'valueType', valueTypeSelect.value);
          valueInput.disabled = valueTypeSelect.value === 'null';
        });
        valueInput.addEventListener('input', () => handleRowChange(index, 'value', valueInput.value));

        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'button button--ghost';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => removeRow(index));

        rowWrap.appendChild(typeSelect);
        rowWrap.appendChild(fieldInput);
        rowWrap.appendChild(operatorSelect);
        rowWrap.appendChild(valueTypeSelect);
        rowWrap.appendChild(valueInput);
        rowWrap.appendChild(removeButton);
        list.appendChild(rowWrap);
      });
      const serialized = updateHiddenValue();
      if (advancedMode) {
        advancedEditor.value = serialized;
      }
    }

    const addRow = (row) => {
      builderState.all.push(makeRowState(row));
      renderRows();
    };

    const setAdvancedMode = (enabled) => {
      advancedMode = enabled;
      modeInput.value = enabled ? 'advanced' : 'builder';
      advancedPanel.hidden = !enabled;
      advancedToggle.setAttribute('aria-expanded', enabled ? 'true' : 'false');
      if (enabled) {
        advancedEditor.value = hiddenInput.value || '';
      } else {
        setError('');
      }
    };

    addButton.addEventListener('click', () => addRow());
    advancedToggle.addEventListener('click', () => {
      if (!advancedMode) {
        setAdvancedMode(true);
        return;
      }
      const raw = (advancedEditor.value || '').trim();
      if (!raw) {
        builderState = { all: [] };
        setAdvancedMode(false);
        renderRows();
        return;
      }
      try {
        const parsed = JSON.parse(raw);
        const rows = filtersToRows(parsed);
        if (!rows.length) {
          throw new Error('Advanced JSON could not be represented in the builder.');
        }
        builderState = { all: rows };
        setError('');
        setAdvancedMode(false);
        renderRows();
      } catch (error) {
        setError(error && error.message ? error.message : 'Advanced JSON is invalid.');
      }
    });

    const initialRaw = (container.dataset.filterRaw || hiddenInput.value || '').trim();
    if (initialRaw) {
      try {
        const parsed = JSON.parse(initialRaw);
        const rows = filtersToRows(parsed);
        if (rows.length) {
          builderState = { all: rows };
          modeInput.value = 'builder';
        } else {
          advancedEditor.value = initialRaw;
          modeInput.value = 'advanced';
          setAdvancedMode(true);
        }
      } catch (error) {
        advancedEditor.value = initialRaw;
        modeInput.value = 'advanced';
        setAdvancedMode(true);
      }
    }

    renderRows();

    const form = container.closest('form');
    if (form) {
      form.addEventListener('submit', (event) => {
        if (advancedMode) {
          const raw = (advancedEditor.value || '').trim();
          if (!raw) {
            hiddenInput.value = '';
            return;
          }
          try {
            const parsed = JSON.parse(raw);
            hiddenInput.value = JSON.stringify(parsed, null, 2);
            setError('');
          } catch (error) {
            event.preventDefault();
            setError('Advanced JSON is invalid. Fix trigger filters before submitting.');
          }
          return;
        }
        updateHiddenValue();
      });
    }
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
    const cadenceSelect = document.querySelector('[data-cadence-select]');
    const cronField = document.querySelector('[data-cron-field]');
    const onetimeField = document.querySelector('[data-onetime-field]');
    const runOnceHidden = document.getElementById('automation-run-once');
    
    if (!kindField || !scheduleFields) {
      return;
    }

    const toggleSchedule = () => {
      scheduleFields.hidden = kindField.value === 'event';
    };

    const toggleCadenceFields = () => {
      if (!cadenceSelect || !cronField || !onetimeField) {
        return;
      }
      
      const isOnetime = cadenceSelect.value === 'once';
      
      // Show/hide fields based on cadence selection
      if (isOnetime) {
        cronField.style.display = 'none';
        onetimeField.style.display = 'block';
        if (runOnceHidden) {
          runOnceHidden.value = 'true';
        }
      } else {
        cronField.style.display = 'block';
        onetimeField.style.display = 'none';
        if (runOnceHidden) {
          runOnceHidden.value = 'false';
        }
      }
    };

    kindField.addEventListener('change', toggleSchedule);
    if (cadenceSelect) {
      cadenceSelect.addEventListener('change', toggleCadenceFields);
      // Initialize on page load
      toggleCadenceFields();
    }
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
        payloadSchema: entry && entry.payload_schema ? entry.payload_schema : null,
      }))
      .filter((option) => option.value);
    const moduleBySlug = new Map(moduleOptions.map((option) => [option.value, option]));

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

    const serializeActions = (validateNotes = false) => {
      const rows = Array.from(list.querySelectorAll('[data-action-row]'));
      const actions = [];
      let errorMessage = '';
      let firstErrorRow = null;

      rows.forEach((row, index) => {
        if (errorMessage) {
          return;
        }
        const moduleSelect = row.querySelector('[data-action-module]');
        const payloadFieldInput = row.querySelector('[data-action-payload]');
        const orderInput = row.querySelector('[data-action-order]');
        const noteInput = row.querySelector('[data-action-note]');
        const payloadModeInput = row.querySelector('[data-action-payload-mode]');
        const moduleValue = moduleSelect ? moduleSelect.value.trim() : '';
        const payloadMode = payloadModeInput ? payloadModeInput.value : 'raw';
        const payloadText = payloadFieldInput ? payloadFieldInput.value.trim() : '';
        const orderValue = orderInput ? parseInt(orderInput.value, 10) : index;
        const noteValue = noteInput ? noteInput.value.trim() : '';
        if (!moduleValue) {
          errorMessage = 'Select a module for every trigger action.';
          firstErrorRow = row;
          return;
        }
        let payload = {};
        if (payloadMode === 'schema') {
          const parsedPayload = actionPayloadEditor
            ? actionPayloadEditor.parseSchemaFields({
              container: row,
              rowIndex: index,
              errorPrefix: 'Trigger action',
            })
            : { ok: true, payload: {} };
          if (!parsedPayload.ok) {
            errorMessage = parsedPayload.error;
            firstErrorRow = row;
            return;
          }
          payload = parsedPayload.payload;
        } else {
          const parsedPayload = actionPayloadEditor
            ? actionPayloadEditor.parseRawPayload({
              text: payloadText,
              rowIndex: index,
              errorPrefix: 'Trigger action',
            })
            : { ok: true, payload: {} };
          if (!parsedPayload.ok) {
            errorMessage = parsedPayload.error;
            firstErrorRow = row;
            return;
          }
          payload = parsedPayload.payload;
        }
        if (validateNotes) {
          const isNew = row.dataset.actionLoaded !== 'true';
          let isModified = false;
          if (!isNew) {
            const origModule = row.dataset.actionOriginalModule || '';
            const origPayload = row.dataset.actionOriginalPayload || '';
            let currentPayload = '';
            try {
              currentPayload = JSON.stringify(payload);
            } catch (error) {
              currentPayload = '';
            }
            isModified = moduleValue !== origModule || currentPayload !== origPayload;
          }
          if ((isNew || isModified) && !noteValue) {
            errorMessage = `Add a note for action ${index + 1}.`;
            firstErrorRow = row;
            return;
          }
        }
        const actionEntry = { order: isNaN(orderValue) ? index : orderValue, module: moduleValue, payload };
        if (noteValue) {
          actionEntry.note = noteValue;
        }
        actions.push(actionEntry);
      });

      if (errorMessage) {
        payloadField.value = '';
        moduleField.value = actions.length ? actions[0].module : '';
        if (errorField) {
          errorField.textContent = errorMessage;
          errorField.hidden = false;
        }
        if (firstErrorRow) {
          const body = firstErrorRow.querySelector('[data-action-body]');
          const toggle = firstErrorRow.querySelector('[data-action-toggle]');
          if (body && body.hidden) {
            body.hidden = false;
            if (toggle) toggle.setAttribute('aria-expanded', 'true');
          }
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
      serializeActions(false);
    };

      let actionRowCounter = 0;

      const createActionRow = (action = null) => {
        actionRowCounter += 1;
        const rowId = actionRowCounter;
        const isLoaded = action !== null;

        const row = document.createElement('div');
        row.className = 'automation-action';
        row.setAttribute('data-action-row', 'true');
        row.dataset.actionLoaded = isLoaded ? 'true' : 'false';
        if (isLoaded) {
          row.dataset.actionOriginalModule = action.module || '';
          try {
          row.dataset.actionOriginalPayload = action.payload ? JSON.stringify(action.payload) : '';
          } catch (e) {
            row.dataset.actionOriginalPayload = '';
          }
        }
        row.dataset.actionPayloadMode = 'raw';

        // ── Header (always visible) ──────────────────────────────
        const header = document.createElement('div');
        header.className = 'automation-action__header';

        const noteWrapper = document.createElement('div');
        noteWrapper.className = 'automation-action__header-note';
        const noteLabel = document.createElement('label');
        noteLabel.className = 'sr-only';
        noteLabel.setAttribute('for', `automation-action-note-${rowId}`);
        noteLabel.textContent = 'Action note';
        const noteInput = document.createElement('input');
        noteInput.type = 'text';
        noteInput.className = 'form-input';
        noteInput.id = `automation-action-note-${rowId}`;
        noteInput.placeholder = 'Action note';
        noteInput.setAttribute('data-action-note', 'true');
        noteInput.value = action && action.note ? String(action.note) : '';
        noteInput.addEventListener('input', updateState);
        noteInput.addEventListener('click', (e) => e.stopPropagation());
        noteWrapper.appendChild(noteLabel);
        noteWrapper.appendChild(noteInput);
        header.appendChild(noteWrapper);

        const toggleButton = document.createElement('button');
        toggleButton.type = 'button';
        toggleButton.className = 'automation-action__toggle';
        toggleButton.setAttribute('aria-expanded', 'false');
        toggleButton.setAttribute('data-action-toggle', 'true');
        toggleButton.setAttribute('aria-label', 'Expand action');
        toggleButton.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
        toggleButton.addEventListener('click', () => {
          const expanded = toggleButton.getAttribute('aria-expanded') === 'true';
          toggleButton.setAttribute('aria-expanded', String(!expanded));
          body.hidden = expanded;
        });
        header.appendChild(toggleButton);
        row.appendChild(header);

        // ── Body (collapsed by default) ──────────────────────────
        const body = document.createElement('div');
        body.className = 'automation-action__body';
        body.hidden = true;
        body.setAttribute('data-action-body', 'true');

        const columns = document.createElement('div');
        columns.className = 'automation-action__columns';

        // Left column: order, module, remove
        const leftCol = document.createElement('div');
        leftCol.className = 'automation-action__left';

        const orderLabel = document.createElement('label');
        orderLabel.className = 'form-label';
        orderLabel.setAttribute('for', `automation-action-order-${rowId}`);
        orderLabel.textContent = 'Order';
        const orderInput = document.createElement('input');
        orderInput.type = 'number';
        orderInput.min = '0';
        orderInput.className = 'form-input';
        orderInput.id = `automation-action-order-${rowId}`;
        orderInput.placeholder = '0';
        orderInput.setAttribute('data-action-order', 'true');
        orderInput.value = action && action.order !== undefined ? String(action.order) : String(list.querySelectorAll('[data-action-row]').length);
        orderInput.addEventListener('input', updateState);
        leftCol.appendChild(orderLabel);
        leftCol.appendChild(orderInput);

        const moduleLabel = document.createElement('label');
        moduleLabel.className = 'form-label';
        moduleLabel.setAttribute('for', `automation-action-module-${rowId}`);
        moduleLabel.textContent = 'Module';
        let refreshQuickAddOptions = () => {};
        const moduleSelect = createModuleSelect(action && action.module ? action.module : '');
        moduleSelect.id = `automation-action-module-${rowId}`;
        moduleSelect.addEventListener('change', () => {
          refreshQuickAddOptions();
          updateState();
        });
        leftCol.appendChild(moduleLabel);
        leftCol.appendChild(moduleSelect);

        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'button button--ghost automation-action__remove';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => {
          row.remove();
          updateState();
        });
        leftCol.appendChild(removeButton);
        columns.appendChild(leftCol);

        // Right column: mode + schema/raw payload
        const rightCol = document.createElement('div');
        rightCol.className = 'automation-action__right';

        const payloadModeLabel = document.createElement('label');
        payloadModeLabel.className = 'form-label';
        payloadModeLabel.setAttribute('for', `automation-action-payload-mode-${rowId}`);
        payloadModeLabel.textContent = 'Payload mode';
        const payloadModeSelect = document.createElement('select');
        payloadModeSelect.className = 'form-input';
        payloadModeSelect.id = `automation-action-payload-mode-${rowId}`;
        payloadModeSelect.setAttribute('data-action-payload-mode', 'true');
        const modeSchema = document.createElement('option');
        modeSchema.value = 'schema';
        modeSchema.textContent = 'Schema fields';
        const modeRaw = document.createElement('option');
        modeRaw.value = 'raw';
        modeRaw.textContent = 'Raw payload JSON';
        payloadModeSelect.appendChild(modeSchema);
        payloadModeSelect.appendChild(modeRaw);
        rightCol.appendChild(payloadModeLabel);
        rightCol.appendChild(payloadModeSelect);

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
        payloadInput.className = 'form-input automation-action__payload';
        payloadInput.rows = 4;
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
        const schemaContainer = document.createElement('div');
        schemaContainer.setAttribute('data-action-schema-container', 'true');
        schemaContainer.hidden = true;
        const rawContainer = document.createElement('div');
        rawContainer.setAttribute('data-action-raw-container', 'true');
        rawContainer.appendChild(quickAddWrapper);
        rawContainer.appendChild(payloadInput);

        const buildSchemaFields = (moduleSlug, existingPayload) => {
          const moduleMeta = moduleBySlug.get(moduleSlug);
          const schema = moduleMeta && moduleMeta.payloadSchema ? moduleMeta.payloadSchema : null;
          if (!actionPayloadEditor) {
            schemaContainer.textContent = '';
            return false;
          }
          const result = actionPayloadEditor.buildSchemaFields({
            container: schemaContainer,
            schema,
            existingPayload,
            idPrefix: `automation-action-${rowId}`,
            onValueChange: updateState,
          });
          return result.hasSchema;
        };

        const applyPayloadMode = () => {
          const moduleSlug = moduleSelect.value;
          const moduleMeta = moduleBySlug.get(moduleSlug);
          const schema = moduleMeta && moduleMeta.payloadSchema ? moduleMeta.payloadSchema : null;
          const schemaFields = actionPayloadEditor ? actionPayloadEditor.getSchemaFields(schema) : [];
          const schemaFieldNames = new Set(schemaFields.map((field) => field.name));
          let existingPayload = {};
          try {
            existingPayload = payloadInput.value.trim() ? JSON.parse(payloadInput.value) : {};
          } catch (error) {
            existingPayload = {};
          }
          const hasSchema = buildSchemaFields(moduleSlug, existingPayload);
          if (!hasSchema) {
            payloadModeSelect.value = 'raw';
            payloadModeSelect.disabled = true;
            modeSchema.disabled = true;
            schemaContainer.hidden = true;
            rawContainer.hidden = false;
            row.dataset.actionPayloadMode = 'raw';
            return;
          }
          payloadModeSelect.disabled = false;
          modeSchema.disabled = false;
          const hasUnknownKeys = actionPayloadEditor
            ? actionPayloadEditor.hasUnknownKeys(existingPayload, schemaFieldNames)
            : false;
          const preferredMode =
            row.dataset.actionPayloadMode === 'raw' || hasUnknownKeys ? 'raw' : 'schema';
          payloadModeSelect.value = preferredMode;
          schemaContainer.hidden = preferredMode !== 'schema';
          rawContainer.hidden = preferredMode !== 'raw';
          row.dataset.actionPayloadMode = preferredMode;
        };

        payloadModeSelect.addEventListener('change', () => {
          row.dataset.actionPayloadMode = payloadModeSelect.value;
          applyPayloadMode();
          updateState();
        });

        refreshQuickAddOptions();
        moduleSelect.addEventListener('change', () => {
          applyPayloadMode();
        });
        rightCol.appendChild(schemaContainer);
        rightCol.appendChild(rawContainer);
        columns.appendChild(rightCol);

        body.appendChild(columns);
        row.appendChild(body);

        applyPayloadMode();
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
          const sorted = parsed.actions.slice().sort((a, b) => {
            const oa = typeof a.order === 'number' ? a.order : 0;
            const ob = typeof b.order === 'number' ? b.order : 0;
            return oa - ob;
          });
          sorted.forEach((action) => addRow(action));
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
        const result = serializeActions(true);
        if (result === null) {
          event.preventDefault();
        }
      });
    }
  }

  function setupAutomationForm() {
    setupTriggerField();
    setupTriggerFilterBuilder();
    setupActionBuilder();
    setupScheduleVisibility();
  }

  function bindScheduledTaskRowDropdowns() {
    // Per-row dropdown panels are inside .table-wrapper which has overflow-x: auto.
    // Because overflow-x: auto implicitly sets overflow-y to auto, the browser clips
    // absolutely-positioned children that extend outside the wrapper's padding box.
    // Fix by repositioning opened panels with position:fixed so they escape the
    // overflow container entirely.
    document.querySelectorAll('#scheduled-tasks-table [data-header-menu]').forEach(function (menu) {
      var summary = menu.querySelector('[data-header-menu-toggle]');
      var list = menu.querySelector('.header-title-menu__list');
      if (!summary || !list) {
        return;
      }
      menu.addEventListener('toggle', function () {
        if (menu.open) {
          var rect = summary.getBoundingClientRect();
          list.style.position = 'fixed';
          list.style.top = (rect.bottom + 4) + 'px';
          list.style.right = (window.innerWidth - rect.right) + 'px';
          list.style.left = 'auto';
          list.style.zIndex = '9999';
        } else {
          list.style.position = '';
          list.style.top = '';
          list.style.right = '';
          list.style.left = '';
          list.style.zIndex = '';
        }
      });
    });
  }

  function initialiseAutomationUI() {
    taskModal = query('task-editor-modal');
    logsModal = query('task-logs-modal');
    previewModal = query('task-preview-modal');
    bulkTaskModal = query('bulk-task-create-modal');
    bindModalDismissal(taskModal);
    bindModalDismissal(logsModal);
    bindModalDismissal(previewModal);
    bindModalDismissal(bulkTaskModal);
    clearTaskForm();
    bindTaskForm();
    bindBulkTaskCreateForm();
    bindTaskActions();
    bindScheduledTaskRowDropdowns();
    bindAutomationDeleteActions();
    setupAutomationForm();
    bindAutomationSectionPagination();
    restoreTaskFilter();
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

(function () {
  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return escapeHtml(value);
    return date.toLocaleString();
  }

  function formatJson(value) {
    if (value === null || value === undefined || value === '') return '—';
    try {
      return escapeHtml(JSON.stringify(value));
    } catch (error) {
      return escapeHtml(value);
    }
  }

  function renderRows(tbody, rows) {
    if (!tbody) return;
    if (!Array.isArray(rows) || rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="table__empty">No history recorded for this automation yet.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map((item) => {
      const ticket = item.ticket_number || item.ticket_id || '—';
      const action = item.action_name || item.action_module || '—';
      const details = item.error_message || item.result_payload || null;
      return `
        <tr>
          <td data-label="When" data-column-key="occurred_at">${formatDate(item.occurred_at)}</td>
          <td data-label="Ticket" data-column-key="ticket">${escapeHtml(ticket)}</td>
          <td data-label="Action" data-column-key="action">${escapeHtml(action)}</td>
          <td data-label="Status" data-column-key="status">${escapeHtml(item.status || 'unknown')}</td>
          <td data-label="Previous values" data-column-key="previous"><code>${formatJson(item.previous_values)}</code></td>
          <td data-label="Details" data-column-key="details"><code>${formatJson(details)}</code></td>
        </tr>`;
    }).join('');
  }

  document.addEventListener('DOMContentLoaded', function () {
    const modal = document.getElementById('automation-history-modal');
    const title = document.getElementById('automation-history-title');
    const tbody = document.querySelector('[data-automation-history-rows]');
    if (!modal || !tbody) return;

    function openModal() { modal.hidden = false; }
    function closeModal() { modal.hidden = true; }

    document.querySelectorAll('[data-automation-history-close]').forEach((button) => {
      button.addEventListener('click', closeModal);
    });

    document.querySelectorAll('[data-automation-history-open]').forEach((button) => {
      button.addEventListener('click', async () => {
        const automationId = button.getAttribute('data-automation-id');
        const automationName = button.getAttribute('data-automation-name') || `#${automationId}`;
        title.textContent = `Automation history: ${automationName}`;
        tbody.innerHTML = '<tr><td colspan="6" class="table__empty">Loading history…</td></tr>';
        openModal();
        try {
          const response = await fetch(`/admin/automations/${encodeURIComponent(automationId)}/history`, {
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
          });
          if (!response.ok) throw new Error(`Request failed with ${response.status}`);
          const payload = await response.json();
          renderRows(tbody, payload.history || []);
        } catch (error) {
          tbody.innerHTML = `<tr><td colspan="6" class="table__empty">Unable to load history: ${escapeHtml(error.message)}</td></tr>`;
        }
      });
    });
  });

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-automation-test]').forEach((button) => {
      button.addEventListener('click', async () => {
        const automationId = button.getAttribute('data-automation-id');
        const automationName = button.getAttribute('data-automation-name') || `#${automationId}`;
        const ticketNumber = window.prompt(`Enter a ticket number to test "${automationName}" against:`);
        if (!ticketNumber || !ticketNumber.trim()) return;

        async function runTest(apply) {
          const response = await fetch(`/admin/automations/${encodeURIComponent(automationId)}/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ ticket_number: ticketNumber.trim(), apply }),
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(payload.detail || `Request failed with ${response.status}`);
          }
          return payload;
        }

        try {
          const evaluation = await runTest(false);
          const ticketLabel = evaluation.ticket?.ticket_number || evaluation.ticket?.id || ticketNumber.trim();
          if (!evaluation.applies) {
            window.alert(`Automation does not apply to ticket #${ticketLabel}.\n\n${evaluation.reason || 'The filters did not match.'}`);
            return;
          }
          const shouldApply = window.confirm(`Automation applies to ticket #${ticketLabel}. Run/apply this automation immediately?`);
          if (!shouldApply) return;
          const applied = await runTest(true);
          window.alert(`Automation test ${applied.status || 'completed'} for ticket #${ticketLabel}.`);
        } catch (error) {
          window.alert(`Unable to test automation: ${error.message}`);
        }
      });
    });
  });

}());
