(function () {
  function getLineHeightPx(element) {
    const computed = window.getComputedStyle(element);
    const lineHeight = parseFloat(computed.lineHeight);
    if (!Number.isNaN(lineHeight)) {
      return lineHeight;
    }

    const fontSize = parseFloat(computed.fontSize);
    if (!Number.isNaN(fontSize)) {
      return fontSize * 1.5;
    }

    return 24;
  }

  function initialiseTimelineMessage(wrapper, toggle) {
    const content = wrapper.querySelector('[data-timeline-message-content]');
    if (!content) {
      return;
    }

    const lineHeight = getLineHeightPx(content);
    const maxCollapsedHeight = lineHeight * 5;
    const currentScrollHeight = content.scrollHeight;

    if (currentScrollHeight <= maxCollapsedHeight + 1) {
      return;
    }

    const moreLabel = toggle.dataset.moreLabel || 'Show more';
    const lessLabel = toggle.dataset.lessLabel || 'Show less';

    function collapse() {
      const collapsedHeight = `${maxCollapsedHeight}px`;
      wrapper.style.maxHeight = collapsedHeight;
      content.style.maxHeight = collapsedHeight;
      wrapper.classList.add('timeline__message--collapsed');
      content.classList.add('timeline__message-content--collapsed');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.textContent = moreLabel;
    }

    function expand() {
      wrapper.style.maxHeight = '';
      content.style.maxHeight = '';
      wrapper.classList.remove('timeline__message--collapsed');
      content.classList.remove('timeline__message-content--collapsed');
      toggle.setAttribute('aria-expanded', 'true');
      toggle.textContent = lessLabel;
    }

    collapse();
    toggle.hidden = false;

    toggle.addEventListener('click', () => {
      const isExpanded = toggle.getAttribute('aria-expanded') === 'true';
      if (isExpanded) {
        collapse();
      } else {
        expand();
      }
    });
  }

  function formatTimeSummary(minutes, isBillable, labourName) {
    if (typeof minutes !== 'number' || Number.isNaN(minutes) || minutes < 0) {
      return '';
    }
    const label = minutes === 1 ? 'minute' : 'minutes';
    const billing = isBillable ? 'Billable' : 'Non-billable';
    let summary = `${minutes} ${label} · ${billing}`;
    if (typeof labourName === 'string' && labourName.trim() !== '') {
      summary = `${summary} · ${labourName.trim()}`;
    }
    return summary;
  }

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

  function parseMinutesValue(value) {
    if (typeof value !== 'string' || value.trim() === '') {
      return null;
    }
    const parsed = Number.parseInt(value, 10);
    if (Number.isNaN(parsed) || parsed < 0) {
      return null;
    }
    return parsed;
  }

  function parseDisplayNumber(element) {
    if (!element) {
      return 0;
    }
    const text = element.textContent || '';
    const parsed = Number.parseInt(text, 10);
    return Number.isNaN(parsed) ? 0 : parsed;
  }

  function adjustTimeTotals(oldMinutes, oldBillable, newMinutes, newBillable) {
    const billableElement = document.querySelector('[data-ticket-billable-total]');
    const nonBillableElement = document.querySelector('[data-ticket-non-billable-total]');
    if (!billableElement || !nonBillableElement) {
      return;
    }

    let billableTotal = parseDisplayNumber(billableElement);
    let nonBillableTotal = parseDisplayNumber(nonBillableElement);

    if (typeof oldMinutes === 'number') {
      if (oldBillable) {
        billableTotal -= oldMinutes;
      } else {
        nonBillableTotal -= oldMinutes;
      }
    }

    if (typeof newMinutes === 'number') {
      if (newBillable) {
        billableTotal += newMinutes;
      } else {
        nonBillableTotal += newMinutes;
      }
    }

    billableTotal = Math.max(billableTotal, 0);
    nonBillableTotal = Math.max(nonBillableTotal, 0);

    billableElement.textContent = billableTotal.toString();
    nonBillableElement.textContent = nonBillableTotal.toString();
  }

  function parseJsonArray(value, fallback = []) {
    if (typeof value !== 'string' || value.trim() === '') {
      return Array.isArray(fallback) ? fallback : [];
    }
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : Array.isArray(fallback) ? fallback : [];
    } catch (error) {
      console.warn('Failed to parse JSON dataset value', error);
      return Array.isArray(fallback) ? fallback : [];
    }
  }

  function normaliseIdList(values) {
    if (!Array.isArray(values)) {
      return [];
    }
    return values
      .map((value) => {
        if (typeof value === 'number') {
          return value.toString();
        }
        if (typeof value === 'string') {
          return value.trim();
        }
        if (value && typeof value === 'object' && 'id' in value) {
          return String(value.id);
        }
        return '';
      })
      .filter((value) => value !== '');
  }

  function formatAssetLabel(record) {
    if (!record || typeof record !== 'object') {
      return '';
    }
    const idValue = 'id' in record ? String(record.id) : '';
    let name = '';
    if (typeof record.label === 'string' && record.label.trim() !== '') {
      return record.label.trim();
    }
    if (typeof record.name === 'string' && record.name.trim() !== '') {
      name = record.name.trim();
    } else if (idValue) {
      name = `Asset ${idValue}`;
    }
    const serial = typeof record.serial_number === 'string' ? record.serial_number.trim() : '';
    const status = typeof record.status === 'string' ? record.status.trim() : '';
    const parts = [name];
    if (serial) {
      parts.push(`SN ${serial}`);
    }
    if (status) {
      const cleanedStatus = status
        .split('_')
        .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
        .join(' ');
      parts.push(cleanedStatus);
    }
    return parts.join(' · ');
  }

  function initialiseAssetSelector() {
    const select = document.querySelector('[data-ticket-asset-selector]');
    if (!(select instanceof HTMLSelectElement)) {
      return;
    }

    const companySelect = document.getElementById('ticket-company-detail');
    if (!(companySelect instanceof HTMLSelectElement)) {
      return;
    }

    const helpElement = document.querySelector('[data-ticket-asset-help]');
    const endpointTemplate = select.dataset.assetsEndpointTemplate || '';
    const initialOptions = parseJsonArray(select.dataset.initialOptions, []);
    const initialSelection = normaliseIdList(parseJsonArray(select.dataset.selectedAssets, []));

    function setHelpState(state, overrideMessage) {
      if (!helpElement) {
        return;
      }
      const enabledMessage = helpElement.dataset.helpEnabled || helpElement.textContent || '';
      const disabledMessage = helpElement.dataset.helpDisabled || enabledMessage;
      let message = enabledMessage;
      let isError = false;

      if (state === 'disabled') {
        message = overrideMessage || disabledMessage;
      } else if (state === 'loading') {
        message = overrideMessage || 'Loading assets…';
      } else if (state === 'error') {
        message = overrideMessage || 'Unable to load assets. Please try again.';
        isError = true;
      } else if (state === 'empty') {
        message = overrideMessage || 'No assets are available for the selected company.';
      } else if (overrideMessage) {
        message = overrideMessage;
      }

      helpElement.textContent = message;
      helpElement.classList.toggle('form-help--error', isError);
    }

    function renderOptions(options, selectedValues) {
      const selectedSet = new Set(normaliseIdList(selectedValues));
      select.innerHTML = '';
      options.forEach((option) => {
        if (!option || typeof option !== 'object') {
          return;
        }
        const optionId = 'id' in option ? option.id : undefined;
        if (optionId === null || optionId === undefined) {
          return;
        }
        const optionElement = document.createElement('option');
        optionElement.value = String(optionId);
        optionElement.textContent = formatAssetLabel(option);
        if (selectedSet.has(String(optionId))) {
          optionElement.selected = true;
        }
        select.appendChild(optionElement);
      });
    }

    async function fetchAssets(companyId) {
      if (!endpointTemplate || !companyId) {
        return [];
      }
      const endpoint = endpointTemplate.replace('{companyId}', encodeURIComponent(companyId));
      const response = await fetch(endpoint, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }
      const payload = await response.json();
      if (!Array.isArray(payload)) {
        return [];
      }
      return payload
        .map((record) => {
          if (!record || typeof record !== 'object') {
            return null;
          }
          const idValue = 'id' in record ? record.id : null;
          if (idValue === null || idValue === undefined) {
            return null;
          }
          return {
            id: idValue,
            label: formatAssetLabel(record),
            serial_number: record.serial_number,
            status: record.status,
            name: record.name,
          };
        })
        .filter((option) => option !== null);
    }

    async function reloadAssets(companyId) {
      if (!companyId) {
        renderOptions([], []);
        select.value = '';
        select.disabled = true;
        select.setAttribute('aria-disabled', 'true');
        setHelpState('disabled');
        return;
      }

      select.disabled = true;
      select.setAttribute('aria-disabled', 'true');
      setHelpState('loading');

      try {
        const assets = await fetchAssets(companyId);
        renderOptions(assets, []);
        if (!assets.length) {
          setHelpState('empty');
        } else {
          setHelpState('enabled');
        }
      } catch (error) {
        console.error('Failed to load company assets', error);
        renderOptions([], []);
        setHelpState('error');
      } finally {
        select.disabled = false;
        select.removeAttribute('aria-disabled');
      }
    }

    renderOptions(initialOptions, initialSelection);
    if (companySelect.value) {
      select.disabled = false;
      select.removeAttribute('aria-disabled');
      setHelpState(initialOptions.length ? 'enabled' : 'empty');
    } else {
      select.disabled = true;
      select.setAttribute('aria-disabled', 'true');
      setHelpState('disabled');
    }

    companySelect.addEventListener('change', () => {
      const companyId = companySelect.value;
      if (!companyId) {
        renderOptions([], []);
        select.value = '';
        select.disabled = true;
        select.setAttribute('aria-disabled', 'true');
        setHelpState('disabled');
        return;
      }
      reloadAssets(companyId);
    });
  }

  function initialiseReplyTimeEditing() {
    const modal = document.getElementById('reply-time-modal');
    const timeline = document.querySelector('[data-ticket-timeline]');
    if (!modal || !timeline) {
      return;
    }

    const ticketId = timeline.getAttribute('data-ticket-id');
    const form = modal.querySelector('[data-reply-time-form]');
    const minutesInput = modal.querySelector('[data-reply-time-minutes]');
    const billableCheckbox = modal.querySelector('[data-reply-time-billable]');
    const labourSelect = modal.querySelector('[data-reply-time-labour]');
    const errorMessage = modal.querySelector('[data-reply-time-error]');
    const submitButton = modal.querySelector('[data-reply-time-submit]');
    const triggers = document.querySelectorAll('[data-reply-edit]');
    if (
      !form ||
      !minutesInput ||
      !billableCheckbox ||
      !errorMessage ||
      !submitButton ||
      !labourSelect ||
      !triggers.length
    ) {
      return;
    }

    const focusableSelector =
      'a[href], button:not([disabled]), textarea, input:not([type="hidden"]):not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    let activeTrigger = null;
    let activeReply = null;

    function setError(message) {
      if (!errorMessage) {
        return;
      }
      if (message) {
        errorMessage.textContent = message;
        errorMessage.hidden = false;
      } else {
        errorMessage.textContent = '';
        errorMessage.hidden = true;
      }
    }

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
      const [first] = getFocusableElements();
      if (first && typeof first.focus === 'function') {
        first.focus();
      }
    }

    function closeModal() {
      modal.classList.remove('is-visible');
      modal.hidden = true;
      modal.setAttribute('aria-hidden', 'true');
      document.removeEventListener('keydown', handleKeydown);
      setError('');
      if (activeTrigger && typeof activeTrigger.focus === 'function') {
        activeTrigger.focus();
      }
      activeTrigger = null;
      activeReply = null;
      form.reset();
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
      const current = document.activeElement;
      if (event.shiftKey) {
        if (current === first) {
          event.preventDefault();
          last.focus();
        }
      } else if (current === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function openModal(trigger, replyArticle) {
      activeTrigger = trigger instanceof HTMLElement ? trigger : null;
      activeReply = replyArticle;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.addEventListener('keydown', handleKeydown);
      focusFirstElement();
    }

    function updateReplyDisplay(replyArticle, minutes, billable, summary, labourTypeId, labourTypeName) {
      if (!replyArticle) {
        return;
      }
      replyArticle.dataset.replyMinutes = typeof minutes === 'number' ? String(minutes) : '';
      replyArticle.dataset.replyBillable = billable ? 'true' : 'false';
      replyArticle.dataset.replyLabour =
        typeof labourTypeId === 'number' && !Number.isNaN(labourTypeId) && labourTypeId > 0
          ? String(labourTypeId)
          : '';
      const summaryElement = replyArticle.querySelector('[data-reply-time-summary]');
      if (summaryElement) {
        const text = summary || formatTimeSummary(minutes, billable, labourTypeName);
        if (text) {
          summaryElement.textContent = text;
          summaryElement.hidden = false;
        } else {
          summaryElement.textContent = '';
          summaryElement.hidden = true;
        }
      }
    }

    triggers.forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        if (!ticketId) {
          return;
        }
        const replyArticle = button.closest('[data-ticket-reply]');
        if (!(replyArticle instanceof HTMLElement)) {
          return;
        }
        const minutesValue = replyArticle.getAttribute('data-reply-minutes') || '';
        const billableValue = replyArticle.getAttribute('data-reply-billable') === 'true';
        const labourValue = replyArticle.getAttribute('data-reply-labour') || '';
        minutesInput.value = minutesValue;
        billableCheckbox.checked = billableValue;
        labourSelect.value = labourValue;
        setError('');
        openModal(button, replyArticle);
      });
    });

    modal.addEventListener('click', (event) => {
      if (event.target === modal) {
        closeModal();
      }
    });

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal();
      });
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!ticketId || !(activeReply instanceof HTMLElement)) {
        setError('Unable to determine which reply to update.');
        return;
      }
      const replyId = activeReply.getAttribute('data-reply-id');
      if (!replyId) {
        setError('Unable to determine which reply to update.');
        return;
      }

      const rawMinutes = minutesInput.value.trim();
      const labourValue = labourSelect.value.trim();
      let minutes = null;
      if (rawMinutes !== '') {
        const parsed = Number.parseInt(rawMinutes, 10);
        if (Number.isNaN(parsed)) {
          setError('Enter minutes as a whole number.');
          return;
        }
        if (parsed < 0) {
          setError('Minutes cannot be negative.');
          return;
        }
        if (parsed > 1440) {
          setError('Minutes cannot exceed 1440.');
          return;
        }
        minutes = parsed;
      }

      const isBillable = billableCheckbox.checked;
      let labourTypeId = null;
      if (labourValue !== '') {
        const parsedLabour = Number.parseInt(labourValue, 10);
        if (Number.isNaN(parsedLabour) || parsedLabour <= 0) {
          setError('Select a valid labour type.');
          return;
        }
        labourTypeId = parsedLabour;
      }
      const csrfToken = getCsrfToken();
      const headers = {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      };
      if (csrfToken) {
        headers['X-CSRF-Token'] = csrfToken;
      }

      const previousMinutes = parseMinutesValue(activeReply.getAttribute('data-reply-minutes'));
      const previousBillable = activeReply.getAttribute('data-reply-billable') === 'true';

      submitButton.disabled = true;
      submitButton.setAttribute('aria-busy', 'true');
      setError('');

      try {
        const response = await fetch(`/api/tickets/${ticketId}/replies/${replyId}`, {
          method: 'PATCH',
          credentials: 'same-origin',
          headers,
          body: JSON.stringify({
            minutes_spent: minutes,
            is_billable: isBillable,
            labour_type_id: labourTypeId,
          }),
        });

        if (!response.ok) {
          let detail = `${response.status} ${response.statusText}`;
          try {
            const payload = await response.json();
            if (payload && payload.detail) {
              detail = Array.isArray(payload.detail)
                ? payload.detail.map((entry) => entry.msg || entry).join(', ')
                : payload.detail;
            }
          } catch (error) {
            /* ignore parse errors */
          }
          throw new Error(detail);
        }

        const data = await response.json();
        const replyData = data && data.reply ? data.reply : null;
        if (!replyData) {
          throw new Error('No reply data returned.');
        }
        const updatedMinutes =
          typeof replyData.minutes_spent === 'number' ? replyData.minutes_spent : null;
        const updatedBillable = Boolean(replyData.is_billable);
        const summaryText = typeof replyData.time_summary === 'string' ? replyData.time_summary : '';
        const updatedLabourId =
          typeof replyData.labour_type_id === 'number' && replyData.labour_type_id > 0
            ? replyData.labour_type_id
            : null;
        const labourName =
          typeof replyData.labour_type_name === 'string' ? replyData.labour_type_name : '';

        updateReplyDisplay(
          activeReply,
          updatedMinutes,
          updatedBillable,
          summaryText,
          updatedLabourId,
          labourName,
        );
        adjustTimeTotals(previousMinutes, previousBillable, updatedMinutes, updatedBillable);
        closeModal();
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unable to update time entry.';
        setError(message);
      } finally {
        submitButton.disabled = false;
        submitButton.removeAttribute('aria-busy');
      }
    });
  }

  function initialiseTaskManagement() {
    const taskLists = document.querySelectorAll('[data-task-list]');
    if (!taskLists.length) {
      return;
    }

    taskLists.forEach((taskList) => {
      const ticketId = taskList.dataset.ticketId;
      if (!ticketId) {
        return;
      }

      const card = taskList.closest('[data-ticket-tasks-card]');
      const emptyMessage = card ? card.querySelector('[data-tasks-empty]') : null;
      const addButton = card ? card.querySelector('[data-add-task-button]') : null;
      const countBadge = card ? card.querySelector('[data-tasks-count-badge]') : null;

      async function loadTasks() {
        try {
          const response = await fetch(`/api/tickets/${ticketId}/tasks`, {
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
          });
          if (!response.ok) {
            throw new Error('Failed to load tasks');
          }
          const data = await response.json();
          renderTasks(data.items || []);
        } catch (error) {
          console.error('Failed to load tasks', error);
        }
      }

      function renderTasks(tasks) {
        taskList.innerHTML = '';
        if (!tasks || !tasks.length) {
          if (emptyMessage) {
            emptyMessage.hidden = false;
          }
          updateTaskCount(0, 0);
          return;
        }
        if (emptyMessage) {
          emptyMessage.hidden = true;
        }
        tasks.forEach((task) => {
          const li = createTaskElement(task);
          taskList.appendChild(li);
        });
        
        // Update task count badge
        const incompleteCount = tasks.filter(task => !task.is_completed).length;
        updateTaskCount(incompleteCount, tasks.length);
      }

      function updateTaskCount(incompleteCount, totalCount) {
        if (!countBadge) {
          return;
        }
        if (totalCount === 0) {
          countBadge.hidden = true;
          countBadge.textContent = '';
        } else if (incompleteCount > 0) {
          countBadge.hidden = false;
          countBadge.textContent = `(${incompleteCount})`;
        } else {
          countBadge.hidden = true;
          countBadge.textContent = '';
        }
      }

      function createTaskElement(task) {
        const li = document.createElement('li');
        li.className = 'task-list__item';
        li.dataset.taskId = task.id;

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'task-list__checkbox';
        checkbox.checked = task.is_completed || false;
        checkbox.addEventListener('change', () => handleTaskToggle(task.id, checkbox.checked));

        const label = document.createElement('span');
        label.className = 'task-list__label';
        label.textContent = task.task_name || '';
        if (task.is_completed) {
          label.classList.add('task-list__label--completed');
        }

        const actions = document.createElement('div');
        actions.className = 'task-list__actions';

        const editButton = document.createElement('button');
        editButton.type = 'button';
        editButton.className = 'button button--ghost button--icon button--small';
        editButton.setAttribute('aria-label', 'Edit task');
        editButton.innerHTML = '✎';
        editButton.addEventListener('click', () => handleEditTask(task));

        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'button button--ghost button--icon button--small';
        deleteButton.setAttribute('aria-label', 'Delete task');
        deleteButton.innerHTML = '×';
        deleteButton.addEventListener('click', () => handleDeleteTask(task.id));

        actions.appendChild(editButton);
        actions.appendChild(deleteButton);

        li.appendChild(checkbox);
        li.appendChild(label);
        li.appendChild(actions);

        return li;
      }

      async function handleTaskToggle(taskId, isCompleted) {
        const csrfToken = getCsrfToken();
        const headers = {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        };
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }

        try {
          const response = await fetch(`/api/tickets/${ticketId}/tasks/${taskId}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers,
            body: JSON.stringify({ isCompleted }),
          });
          if (!response.ok) {
            throw new Error('Failed to update task');
          }
          await loadTasks();
        } catch (error) {
          console.error('Failed to update task', error);
          await loadTasks();
        }
      }

      async function handleEditTask(task) {
        const newName = prompt('Edit task name:', task.task_name);
        if (!newName || newName.trim() === '' || newName === task.task_name) {
          return;
        }

        const csrfToken = getCsrfToken();
        const headers = {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        };
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }

        try {
          const response = await fetch(`/api/tickets/${ticketId}/tasks/${task.id}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers,
            body: JSON.stringify({ taskName: newName.trim() }),
          });
          if (!response.ok) {
            throw new Error('Failed to update task');
          }
          await loadTasks();
        } catch (error) {
          console.error('Failed to update task', error);
          alert('Failed to update task. Please try again.');
        }
      }

      async function handleDeleteTask(taskId) {
        if (!confirm('Delete this task? This cannot be undone.')) {
          return;
        }

        const csrfToken = getCsrfToken();
        const headers = { Accept: 'application/json' };
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }

        try {
          const response = await fetch(`/api/tickets/${ticketId}/tasks/${taskId}`, {
            method: 'DELETE',
            credentials: 'same-origin',
            headers,
          });
          if (!response.ok) {
            throw new Error('Failed to delete task');
          }
          await loadTasks();
        } catch (error) {
          console.error('Failed to delete task', error);
          alert('Failed to delete task. Please try again.');
        }
      }

      async function handleAddTask() {
        const taskName = prompt('Enter task name:');
        if (!taskName || taskName.trim() === '') {
          return;
        }

        const csrfToken = getCsrfToken();
        const headers = {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        };
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken;
        }

        try {
          const response = await fetch(`/api/tickets/${ticketId}/tasks`, {
            method: 'POST',
            credentials: 'same-origin',
            headers,
            body: JSON.stringify({ taskName: taskName.trim(), sortOrder: 0 }),
          });
          if (!response.ok) {
            throw new Error('Failed to create task');
          }
          await loadTasks();
        } catch (error) {
          console.error('Failed to create task', error);
          alert('Failed to create task. Please try again.');
        }
      }

      if (addButton) {
        addButton.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          handleAddTask();
        });
      }

      loadTasks();
    });
  }

  function ready() {
    const messageWrappers = document.querySelectorAll('[data-timeline-message]');

    messageWrappers.forEach((wrapper) => {
      const toggle = wrapper.parentElement?.querySelector('[data-timeline-message-toggle]');
      if (!(toggle instanceof HTMLButtonElement)) {
        return;
      }

      initialiseTimelineMessage(wrapper, toggle);
    });

    initialiseReplyTimeEditing();
    initialiseAssetSelector();
    initialiseTaskManagement();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
