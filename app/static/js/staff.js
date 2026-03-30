(function () {
  function parseJson(elementId, fallback) {
    const element = document.getElementById(elementId);
    if (!element) {
      return fallback;
    }
    try {
      return JSON.parse(element.textContent || 'null') ?? fallback;
    } catch (error) {
      console.error('Unable to parse JSON data for', elementId, error);
      return fallback;
    }
  }

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
        // ignore json parsing errors
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

  function submitOnChange(container) {
    container.querySelectorAll('[data-submit-on-change]').forEach((input) => {
      input.addEventListener('change', () => {
        const form = input.closest('form');
        if (form) {
          form.submit();
        }
      });
    });
  }

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.hidden = false;
    modal.classList.add('is-visible');
    const focusTarget = modal.querySelector('[autofocus], input, select, textarea, button');
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

  function getField(id) {
    return document.getElementById(id);
  }

  function setValue(element, value) {
    if (!element) {
      return;
    }
    element.value = value ?? '';
  }

  function normalizeValue(value) {
    return String(value ?? '').trim().toLowerCase();
  }

  function getBrowserTimezone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch (error) {
      return '';
    }
  }

  function makeIdempotencyKey(prefix, staffId) {
    const randomPart = Math.random().toString(36).slice(2, 12);
    return `${prefix}-${staffId}-${Date.now()}-${randomPart}`;
  }

  function getInputCurrentValue(input) {
    if (!input) {
      return '';
    }
    if (input.type === 'checkbox') {
      return input.checked ? '1' : '0';
    }
    return input.value ?? '';
  }

  function evaluateCondition(input, operator, expectedValue) {
    const normalizedOperator = normalizeValue(operator);
    if (!input) {
      return false;
    }
    if (!normalizedOperator) {
      if (input.type === 'checkbox') {
        return Boolean(input.checked);
      }
      const actualFallback = normalizeValue(getInputCurrentValue(input));
      const expectedFallback = normalizeValue(expectedValue);
      if (expectedFallback) {
        return actualFallback === expectedFallback;
      }
      return Boolean(actualFallback);
    }
    if (normalizedOperator === 'is_checked') {
      return Boolean(input.checked);
    }
    if (normalizedOperator === 'is_not_checked') {
      return !input.checked;
    }
    const actual = normalizeValue(getInputCurrentValue(input));
    const expected = normalizeValue(expectedValue);
    if (normalizedOperator === 'not_equals') {
      return actual !== expected;
    }
    return actual === expected;
  }

  function parseConditionalSelectOptions(rawValue) {
    const rawText = String(rawValue || '').trim();
    if (!rawText) {
      return null;
    }

    if (rawText.startsWith('{')) {
      try {
        const parsedJson = JSON.parse(rawText);
        if (parsedJson && typeof parsedJson === 'object' && !Array.isArray(parsedJson)) {
          const matchToOptions = new Map();
          let fallbackOptions = null;
          Object.entries(parsedJson).forEach(([rawMatch, rawOptions]) => {
            const normalizedMatch = normalizeValue(rawMatch);
            const parsedOptions = Array.isArray(rawOptions)
              ? rawOptions.map((option) => String(option || '').trim()).filter(Boolean)
              : String(rawOptions || '')
                .split(/[,|]/)
                .map((option) => option.trim())
                .filter(Boolean);
            if (!parsedOptions.length) {
              return;
            }
            if (normalizedMatch === '*' || normalizedMatch === 'fallback' || normalizedMatch === 'default') {
              if (!fallbackOptions) {
                fallbackOptions = parsedOptions;
              }
              return;
            }
            if (!normalizedMatch || matchToOptions.has(normalizedMatch)) {
              return;
            }
            matchToOptions.set(normalizedMatch, parsedOptions);
          });
          if (matchToOptions.size || fallbackOptions) {
            return { matchToOptions, fallbackOptions };
          }
        }
      } catch (error) {
        // Fall back to legacy parser format.
      }
    }

    const separatorNormalized = rawText.replace(/\r?\n/g, ';');
    const chunks = separatorNormalized.split(';').map((entry) => entry.trim()).filter(Boolean);
    if (!chunks.length) {
      return null;
    }

    const matchToOptions = new Map();
    let fallbackOptions = null;

    chunks.forEach((chunk) => {
      const arrowIndex = chunk.indexOf('=>');
      if (arrowIndex < 0) {
        return;
      }
      const rawMatch = normalizeValue(chunk.slice(0, arrowIndex));
      const rawOptions = chunk.slice(arrowIndex + 2).trim();
      if (!rawOptions) {
        return;
      }
      const parsedOptions = rawOptions
        .split('|')
        .map((option) => option.trim())
        .filter(Boolean);
      if (!parsedOptions.length) {
        return;
      }

      if (rawMatch === '*' || rawMatch === 'fallback') {
        if (!fallbackOptions) {
          fallbackOptions = parsedOptions;
        }
        return;
      }

      if (!rawMatch || matchToOptions.has(rawMatch)) {
        return;
      }
      matchToOptions.set(rawMatch, parsedOptions);
    });

    if (!matchToOptions.size && !fallbackOptions) {
      return null;
    }

    return {
      matchToOptions,
      fallbackOptions,
    };
  }

  function updateMappedSelectOptions({
    selectInput,
    expectedMatch,
    optionMap,
    fallbackOptions,
    allOptions,
    fieldLabel,
  }) {
    if (!selectInput) {
      return false;
    }
    const normalizedParentValue = normalizeValue(expectedMatch);
    const desiredValues = optionMap.get(normalizedParentValue) || fallbackOptions || [];
    const availableValues = new Set(desiredValues.map((value) => normalizeValue(value)));
    const matchingOptions = allOptions.filter((option) => availableValues.has(normalizeValue(option.value)));
    const currentValue = normalizeValue(selectInput.value);

    while (selectInput.firstChild) {
      selectInput.removeChild(selectInput.firstChild);
    }

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = `Select ${String(fieldLabel || '').toLowerCase() || 'an option'}`;
    selectInput.appendChild(placeholder);

    matchingOptions.forEach((option) => {
      const optionElement = document.createElement('option');
      optionElement.value = option.value;
      optionElement.textContent = option.label || option.value;
      selectInput.appendChild(optionElement);
    });

    if (matchingOptions.some((option) => normalizeValue(option.value) === currentValue)) {
      selectInput.value = currentValue;
    } else {
      selectInput.value = '';
    }

    return matchingOptions.length > 0;
  }

  function initCustomFieldConditionals({
    wrappers,
    getInputByName,
    getFieldDefinitionByName,
  }) {
    if (!Array.isArray(wrappers) || wrappers.length === 0) {
      return;
    }

    const applyVisibility = () => {
      wrappers.forEach((wrapper) => {
        const parentName = wrapper.dataset.conditionParentName || '';
        const operator = wrapper.dataset.conditionOperator || '';
        const expected = wrapper.dataset.conditionValue || '';
        const parentInput = parentName ? getInputByName(parentName) : null;
        const selectInput = wrapper.querySelector('select');
        const fieldName = wrapper.dataset.customFieldName || '';
        const fieldDefinition = fieldName ? getFieldDefinitionByName(fieldName) : null;
        const mappedOptions = operator === 'select_map'
          ? parseConditionalSelectOptions(expected)
          : null;
        let shouldShow = !parentName
          ? true
          : (parentInput ? evaluateCondition(parentInput, operator, expected) : false);
        if (mappedOptions && selectInput && parentInput && fieldDefinition) {
          const fieldLabel = fieldDefinition.display_name || fieldDefinition.name || fieldName;
          const hasVisibleOptions = updateMappedSelectOptions({
            selectInput,
            expectedMatch: getInputCurrentValue(parentInput),
            optionMap: mappedOptions.matchToOptions,
            fallbackOptions: mappedOptions.fallbackOptions,
            allOptions: Array.isArray(fieldDefinition.options) ? fieldDefinition.options : [],
            fieldLabel,
          });
          shouldShow = hasVisibleOptions;
        }
        wrapper.hidden = !shouldShow;
        wrapper.querySelectorAll('input, select, textarea').forEach((input) => {
          input.disabled = !shouldShow;
        });
      });
    };

    const parents = new Map();
    wrappers.forEach((wrapper) => {
      const parentName = wrapper.dataset.conditionParentName || '';
      if (!parentName || parents.has(parentName)) {
        return;
      }
      const parentInput = getInputByName(parentName);
      if (!parentInput) {
        return;
      }
      parents.set(parentName, parentInput);
      parentInput.addEventListener('change', applyVisibility);
      parentInput.addEventListener('input', applyVisibility);
    });

    applyVisibility();
  }

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.body;
    const staffList = parseJson('staff-data', []);
    const customFieldDefinitions = parseJson('staff-custom-field-definitions', []);
    const flags = parseJson('staff-flags', {});
    const staffById = new Map(staffList.map((member) => [member.id, member]));

    submitOnChange(container);

    const editModal = document.getElementById('staff-edit-modal');
    const addModal = document.getElementById('staff-add-modal');
    const editForm = document.getElementById('staff-edit-form');
    const editIdField = getField('edit-staff-id');
    const editCustomFieldsGrid = getField('edit-custom-fields-grid');
    const offboardingModal = document.getElementById('staff-offboarding-modal');
    const offboardingForm = document.getElementById('staff-offboarding-form');
    const offboardingStaffIdField = getField('offboarding-staff-id');
    const offboardingDateField = getField('offboarding-date');
    const offboardingTimeField = getField('offboarding-time');
    const offboardingTimezoneField = getField('offboarding-timezone');
    const offboardingReasonNotesField = getField('offboarding-reason-notes');
    const offboardingImmediateButton = getField('offboarding-immediate');
    const offboardingFormError = getField('offboarding-form-error');
    const addForm = container.querySelector('form.staff-form');
    const editModalStaffName = getField('edit-modal-staff-name');
    const editActionNoteField = getField('edit-action-note');
    const editActionStepField = getField('edit-action-step');
    const editActionError = getField('edit-action-error');
    const editFormError = getField('edit-form-error');
    const editDeleteConfirm = getField('edit-delete-confirm');

    const editFields = {
      first_name: getField('edit-first-name'),
      last_name: getField('edit-last-name'),
      email: getField('edit-email'),
      mobile_phone: getField('edit-mobile'),
      date_onboarded: getField('edit-date-onboarded'),
      date_offboarded: getField('edit-date-offboarded'),
      enabled: getField('edit-enabled'),
      street: getField('edit-street'),
      city: getField('edit-city'),
      state: getField('edit-state'),
      postcode: getField('edit-postcode'),
      country: getField('edit-country'),
      department: getField('edit-department'),
      job_title: getField('edit-job-title'),
      org_company: getField('edit-company'),
      manager_name: getField('edit-manager-name'),
      account_action: getField('edit-account-action'),
      m365_last_sign_in: getField('edit-m365-last-sign-in'),
    };

    bindModalDismissal(editModal);
    bindModalDismissal(addModal);
    bindModalDismissal(offboardingModal);

    container.querySelectorAll('[data-open-add-staff-modal]').forEach((button) => {
      button.addEventListener('click', () => {
        openModal(addModal);
      });
    });

    const editCustomFieldInputs = new Map();
    const editCustomFieldGroups = new Map();
    let currentEditStaffId = null;
    const editActionButtons = {
      invite: container.querySelector('[data-edit-action="invite"]'),
      offboardingRequest: container.querySelector('[data-edit-action="offboarding-request"]'),
      approve: container.querySelector('[data-edit-action="approve"]'),
      deny: container.querySelector('[data-edit-action="deny"]'),
      workflowRerun: container.querySelector('[data-edit-action="workflow-rerun"]'),
      workflowRetry: container.querySelector('[data-edit-action="workflow-retry"]'),
      workflowResume: container.querySelector('[data-edit-action="workflow-resume"]'),
      workflowForceComplete: container.querySelector('[data-edit-action="workflow-force-complete"]'),
      delete: container.querySelector('[data-edit-action="delete"]'),
    };
    if (editCustomFieldsGrid && Array.isArray(customFieldDefinitions)) {
      const normalizeGroupLabel = (group) => {
        const raw = typeof group === 'string' ? group.trim() : '';
        return raw || 'Additional details';
      };

      const ensureGroupContainer = (groupLabel) => {
        const normalized = normalizeGroupLabel(groupLabel);
        if (editCustomFieldGroups.has(normalized)) {
          return editCustomFieldGroups.get(normalized);
        }
        const section = document.createElement('fieldset');
        section.className = 'fieldset staff-modal__subsection';
        section.dataset.customFieldGroup = normalized;
        const legend = document.createElement('legend');
        legend.textContent = normalized;
        const grid = document.createElement('div');
        grid.className = 'form-grid staff-modal-grid';
        section.appendChild(legend);
        section.appendChild(grid);
        editCustomFieldsGrid.appendChild(section);
        editCustomFieldGroups.set(normalized, grid);
        return grid;
      };

      customFieldDefinitions.forEach((field) => {
        if (!field || !field.name) {
          return;
        }
        const groupLabel = normalizeGroupLabel(field.field_group);
        const wrapper = document.createElement('div');
        wrapper.className = field.field_type === 'checkbox' ? 'form-field form-field--checkbox' : 'form-field';
        wrapper.dataset.customFieldWrapper = '1';
        wrapper.dataset.customFieldName = field.name;
        wrapper.dataset.customFieldGroup = groupLabel;
        wrapper.dataset.conditionParentName = field.condition_parent_name || '';
        wrapper.dataset.conditionOperator = field.condition_operator || '';
        wrapper.dataset.conditionValue = field.condition_value || '';
        const inputId = `edit-custom-${field.name}`;
        if (field.field_type === 'checkbox') {
          wrapper.innerHTML = `
            <label class="checkbox" for="${inputId}">
              <input type="checkbox" id="${inputId}" />
              <span>${field.display_name || field.name}</span>
            </label>
          `;
        } else if (field.field_type === 'select') {
          const options = (field.options || [])
            .map((option) => `<option value="${option.value}">${option.label || option.value}</option>`)
            .join('');
          wrapper.innerHTML = `
            <label class="form-label" for="${inputId}">${field.display_name || field.name}</label>
            <select class="form-input" id="${inputId}">
              <option value="">Select ${(field.display_name || field.name).toLowerCase()}</option>
              ${options}
            </select>
          `;
        } else {
          wrapper.innerHTML = `
            <label class="form-label" for="${inputId}">${field.display_name || field.name}</label>
            <input class="form-input" id="${inputId}" type="${field.field_type === 'date' ? 'date' : 'text'}" />
          `;
        }
        ensureGroupContainer(groupLabel).appendChild(wrapper);
        editCustomFieldInputs.set(field.name, {
          field,
          wrapper,
          input: wrapper.querySelector(`#${inputId}`),
        });
      });
    }

    if (addForm) {
      const timezoneField = addForm.querySelector('input[name="browser_timezone"]');
      if (timezoneField) {
        timezoneField.value = getBrowserTimezone();
      }
      const addWrappers = Array.from(addForm.querySelectorAll('[data-custom-field-wrapper]'));
      initCustomFieldConditionals({
        wrappers: addWrappers,
        getInputByName: (name) => addForm.querySelector(`[name="${name}"]`),
        getFieldDefinitionByName: (name) => (
          customFieldDefinitions.find((field) => field && field.name === name) || null
        ),
      });
    }

    if (editCustomFieldsGrid) {
      const editWrappers = Array.from(editCustomFieldsGrid.querySelectorAll('[data-custom-field-wrapper]'));
      initCustomFieldConditionals({
        wrappers: editWrappers,
        getInputByName: (name) => {
          const entry = editCustomFieldInputs.get(name);
          return entry && entry.input ? entry.input : null;
        },
        getFieldDefinitionByName: (name) => (
          customFieldDefinitions.find((field) => field && field.name === name) || null
        ),
      });
    }

    function setInlineError(element, message) {
      if (!element) {
        return;
      }
      element.textContent = message || '';
      element.hidden = !message;
    }

    function getActionNote({ required = false } = {}) {
      const note = editActionNoteField ? editActionNoteField.value.trim() : '';
      if (required && !note) {
        throw new Error('Please enter an action note before continuing.');
      }
      return note || null;
    }

    function getActionStep(defaultStep) {
      const explicitStep = editActionStepField ? editActionStepField.value.trim() : '';
      return explicitStep || (defaultStep || '').trim();
    }

    function setDefaultOffboardingDateTime() {
      if (!offboardingDateField || !offboardingTimeField) {
        return;
      }
      const now = new Date();
      const localDate = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
      const localTime = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
      offboardingDateField.value = localDate;
      offboardingTimeField.value = localTime;
    }

    function openOffboardingRequestModal(staffId) {
      const member = staffById.get(Number(staffId));
      if (
        !member
        || !offboardingStaffIdField
        || !offboardingDateField
        || !offboardingTimeField
        || !offboardingTimezoneField
        || !offboardingReasonNotesField
      ) {
        return;
      }
      offboardingStaffIdField.value = String(staffId);
      offboardingReasonNotesField.value = '';
      offboardingTimezoneField.value = getBrowserTimezone() || '';
      setDefaultOffboardingDateTime();
      openModal(offboardingModal);
    }

    if (offboardingImmediateButton) {
      offboardingImmediateButton.addEventListener('click', () => {
        setDefaultOffboardingDateTime();
      });
    }

    function getWorkflowContext(member) {
      const workflow = member && member.workflow_status ? member.workflow_status : {};
      const workflowState = String((workflow && workflow.state) || member.onboarding_status || 'requested').toLowerCase();
      const currentStep = String((workflow && workflow.current_step) || '').trim();
      return {
        workflow,
        workflowState,
        currentStep,
        canRerun: flags && flags.isAdmin && workflowState !== 'running',
        canRetry: flags && flags.isAdmin && workflowState === 'failed',
        canResume: flags && flags.isAdmin && ['paused', 'waiting_external'].includes(workflowState),
        canForceComplete: flags && flags.isSuperAdmin && Boolean(currentStep) && ['running', 'waiting_external'].includes(workflowState),
      };
    }

    function setActionVisibility(button, { visible, disabled }) {
      if (!button) {
        return;
      }
      button.hidden = !visible;
      button.disabled = !visible || Boolean(disabled);
    }

    function updateEditActionButtons(member) {
      const workflowContext = getWorkflowContext(member);
      const approvalStatus = String(member.approval_status || '').toLowerCase();
      const accountAction = String(member.account_action || '').toLowerCase();
      const isExStaff = Boolean(member.date_offboarded) || accountAction === 'offboarded';
      const canInvite = Boolean(flags && flags.isAdmin && member.enabled && !isExStaff && member.email);
      const canRequestOffboarding = Boolean(flags && flags.isAdmin && member.enabled && !isExStaff && accountAction !== 'offboard requested');
      const canApprove = Boolean(flags && flags.canApproveOnboarding && ['pending', 'requested'].includes(approvalStatus));
      const canDeny = Boolean(flags && flags.canApproveOnboarding && ['pending', 'requested'].includes(approvalStatus));

      setActionVisibility(editActionButtons.invite, { visible: canInvite, disabled: false });
      setActionVisibility(editActionButtons.offboardingRequest, { visible: canRequestOffboarding, disabled: false });
      setActionVisibility(editActionButtons.approve, { visible: canApprove, disabled: false });
      setActionVisibility(editActionButtons.deny, { visible: canDeny, disabled: false });
      setActionVisibility(editActionButtons.workflowRerun, { visible: workflowContext.canRerun, disabled: false });
      setActionVisibility(editActionButtons.workflowRetry, { visible: workflowContext.canRetry, disabled: false });
      setActionVisibility(editActionButtons.workflowResume, { visible: workflowContext.canResume, disabled: false });
      setActionVisibility(editActionButtons.workflowForceComplete, { visible: workflowContext.canForceComplete, disabled: false });
      setActionVisibility(editActionButtons.delete, { visible: Boolean(flags && flags.isSuperAdmin), disabled: false });

      if (editActionButtons.workflowForceComplete) {
        editActionButtons.workflowForceComplete.dataset.currentStep = workflowContext.currentStep || '';
      }
    }

    function openEditModalForStaff(staffId) {
      const id = Number(staffId);
      const member = staffById.get(id);
      if (!member || !editForm || !editIdField) {
        return;
      }
      currentEditStaffId = id;
      editIdField.value = String(id);
      setValue(editFields.first_name, member.first_name);
      setValue(editFields.last_name, member.last_name);
      if (editModalStaffName) {
        editModalStaffName.textContent = `${member.first_name || ''} ${member.last_name || ''}`.trim() || member.email || '';
      }
      setValue(editFields.email, member.email);
      setValue(editFields.mobile_phone, member.mobile_phone);
      setValue(editFields.date_onboarded, member.date_onboarded ? member.date_onboarded.slice(0, 10) : '');
      setValue(editFields.date_offboarded, member.date_offboarded ? member.date_offboarded.slice(0, 16) : '');
      if (editFields.enabled) {
        editFields.enabled.checked = Boolean(member.enabled);
      }
      setValue(editFields.street, member.street);
      setValue(editFields.city, member.city);
      setValue(editFields.state, member.state);
      setValue(editFields.postcode, member.postcode);
      setValue(editFields.country, member.country);
      setValue(editFields.department, member.department);
      setValue(editFields.job_title, member.job_title);
      setValue(editFields.org_company, member.org_company);
      setValue(editFields.manager_name, member.manager_name);
      if (editFields.account_action) {
        editFields.account_action.value = member.account_action || 'Onboard Requested';
      }
      setValue(editFields.m365_last_sign_in, member.m365_last_sign_in ? member.m365_last_sign_in.replace('T', ' ').slice(0, 16) : '');
      const existingCustom = member.custom_fields || {};
      editCustomFieldInputs.forEach((entry, name) => {
        if (!entry || !entry.input) {
          return;
        }
        const value = existingCustom[name];
        if (entry.field.field_type === 'checkbox') {
          entry.input.checked = Boolean(value);
        } else {
          entry.input.value = value ?? '';
        }
      });
      if (editActionNoteField) {
        editActionNoteField.value = '';
      }
      if (editActionStepField) {
        editActionStepField.value = '';
      }
      if (editDeleteConfirm) {
        editDeleteConfirm.checked = false;
      }
      setInlineError(editActionError, '');
      setInlineError(editFormError, '');
      updateEditActionButtons(member);
      openModal(editModal);
    }

    async function sendInvite(staffId) {
      await requestJson(`/staff/${staffId}/invite`, { method: 'POST' });
      window.location.reload();
    }

    async function deleteStaff(staffId, isConfirmed) {
      if (!isConfirmed) {
        throw new Error('Confirm deletion in the danger zone before deleting.');
      }
      await requestJson(`/staff/${staffId}`, { method: 'DELETE' });
      window.location.reload();
    }

    async function approveOnboarding(staffId, comment) {
      await requestJson(`/api/staff/${staffId}/onboarding/approve`, {
        method: 'POST',
        body: JSON.stringify({ comment: comment || '' }),
      });
      window.location.reload();
    }

    async function denyOnboarding(staffId, reason) {
      if (!reason || !reason.trim()) {
        throw new Error('A deny reason is required.');
      }
      await requestJson(`/api/staff/${staffId}/onboarding/deny`, {
        method: 'POST',
        body: JSON.stringify({ reason: reason.trim() }),
      });
      window.location.reload();
    }

    async function rerunWorkflow(staffId, reason) {
      await requestJson(`/api/staff/${staffId}/workflow/rerun`, {
        method: 'POST',
        headers: {
          'Idempotency-Key': makeIdempotencyKey('rerun', staffId),
        },
        body: JSON.stringify({ reason: reason.trim() || null }),
      });
      window.location.reload();
    }

    async function retryWorkflow(staffId, reason) {
      await requestJson(`/api/staff/${staffId}/workflow/retry-failed-step`, {
        method: 'POST',
        headers: {
          'Idempotency-Key': makeIdempotencyKey('retry', staffId),
        },
        body: JSON.stringify({ reason: reason.trim() || null }),
      });
      window.location.reload();
    }

    async function resumeWorkflow(staffId, reason) {
      await requestJson(`/api/staff/${staffId}/workflow/resume`, {
        method: 'POST',
        headers: {
          'Idempotency-Key': makeIdempotencyKey('resume', staffId),
        },
        body: JSON.stringify({ reason: reason.trim() || null }),
      });
      window.location.reload();
    }

    async function forceCompleteWorkflow(staffId, context, stepName, reason) {
      const currentStep = (context && context.currentStep) || '';
      const requestedStepName = (stepName || currentStep || '').trim();
      if (!requestedStepName) {
        throw new Error('Provide the workflow step to force-complete.');
      }
      await requestJson(`/api/staff/${staffId}/workflow/force-complete-step`, {
        method: 'POST',
        headers: {
          'Idempotency-Key': makeIdempotencyKey('force-complete', staffId),
        },
        body: JSON.stringify({
          stepName: requestedStepName,
          reason: reason.trim() || null,
        }),
      });
      window.location.reload();
    }

    container.querySelectorAll('[data-staff-offboarding-request]').forEach((button) => {
      button.addEventListener('click', () => {
        const id = button.getAttribute('data-staff-offboarding-request');
        if (!id) {
          return;
        }
        openOffboardingRequestModal(id);
      });
    });

    container.querySelectorAll('[data-staff-edit]').forEach((button) => {
      button.addEventListener('click', () => {
        const id = button.getAttribute('data-staff-edit');
        if (!id) {
          return;
        }
        openEditModalForStaff(id);
      });
    });

    if (offboardingForm) {
      offboardingForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const staffId = offboardingStaffIdField ? offboardingStaffIdField.value : '';
        const date = offboardingDateField ? offboardingDateField.value : '';
        const time = offboardingTimeField ? offboardingTimeField.value : '';
        const reasonNotes = offboardingReasonNotesField ? offboardingReasonNotesField.value.trim() : '';
        const timezone = offboardingTimezoneField ? offboardingTimezoneField.value.trim() : '';
        const requestedAt = date && time ? `${date}T${time}` : '';
        if (!staffId || !date || !time || !timezone || !reasonNotes) {
          setInlineError(offboardingFormError, 'Offboarding date, time, timezone, and reason/notes are required.');
          return;
        }
        try {
          setInlineError(offboardingFormError, '');
          await requestJson(`/api/staff/${staffId}/offboarding/request`, {
            method: 'POST',
            body: JSON.stringify({
              reason: reasonNotes,
              requestedAt,
              requestedTimezone: timezone,
              notes: null,
            }),
          });
          window.location.reload();
        } catch (error) {
          setInlineError(offboardingFormError, `Failed to submit offboarding request: ${error.message}`);
        }
      });
    }

    if (editForm) {
      editForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const staffId = editIdField ? editIdField.value : '';
        if (!staffId) {
          return;
        }
        const payload = {
          firstName: editFields.first_name ? editFields.first_name.value : '',
          lastName: editFields.last_name ? editFields.last_name.value : '',
          email: editFields.email ? editFields.email.value : '',
          mobilePhone: editFields.mobile_phone ? editFields.mobile_phone.value : '',
          dateOnboarded: editFields.date_onboarded ? editFields.date_onboarded.value : '',
          dateOffboarded: editFields.date_offboarded ? editFields.date_offboarded.value : '',
          enabled: editFields.enabled ? editFields.enabled.checked : false,
          street: editFields.street ? editFields.street.value : '',
          city: editFields.city ? editFields.city.value : '',
          state: editFields.state ? editFields.state.value : '',
          postcode: editFields.postcode ? editFields.postcode.value : '',
          country: editFields.country ? editFields.country.value : '',
          department: editFields.department ? editFields.department.value : '',
          jobTitle: editFields.job_title ? editFields.job_title.value : '',
          company: editFields.org_company ? editFields.org_company.value : '',
          managerName: editFields.manager_name ? editFields.manager_name.value : '',
          accountAction: editFields.account_action ? editFields.account_action.value : '',
          customFields: {},
        };
        editCustomFieldInputs.forEach((entry, name) => {
          if (!entry || !entry.input) {
            return;
          }
          if (entry.input.disabled) {
            return;
          }
          if (entry.field.field_type === 'checkbox') {
            payload.customFields[name] = Boolean(entry.input.checked);
          } else {
            payload.customFields[name] = entry.input.value || null;
          }
        });
        try {
          setInlineError(editFormError, '');
          await requestJson(`/staff/${staffId}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
          });
          window.location.reload();
        } catch (error) {
          setInlineError(editFormError, `Unable to update staff member: ${error.message}`);
        }
      });
    }

    container.querySelectorAll('[data-staff-verify]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-staff-verify');
        if (!id) {
          return;
        }
        try {
          const data = await requestJson(`/staff/${id}/verify`, { method: 'POST' });
          const row = button.closest('tr');
          const codeCell = row ? row.querySelector('.verification-code') : null;
          if (codeCell) {
            codeCell.textContent = data && data.code ? data.code : '';
            codeCell.classList.toggle('text-success', data && data.status === 202);
          }
          if (!data || data.status !== 202) {
            alert('Verification code dispatched, but upstream delivery may have failed.');
          }
        } catch (error) {
          alert(`Failed to send verification code: ${error.message}`);
        }
      });
    });

    container.querySelectorAll('[data-staff-invite]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-staff-invite');
        if (!id) {
          return;
        }
        try {
          await sendInvite(id);
        } catch (error) {
          alert(`Failed to send invitation: ${error.message}`);
        }
      });
    });

    container.querySelectorAll('[data-staff-delete]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-staff-delete');
        if (!id) {
          return;
        }
        try {
          await deleteStaff(id);
        } catch (error) {
          alert(`Failed to delete staff record: ${error.message}`);
        }
      });
    });

    container.querySelectorAll('[data-staff-approve]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-staff-approve');
        if (!id) {
          return;
        }
        try {
          await approveOnboarding(id);
        } catch (error) {
          alert(`Failed to approve onboarding request: ${error.message}`);
        }
      });
    });

    container.querySelectorAll('[data-staff-deny]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-staff-deny');
        if (!id) {
          return;
        }
        try {
          await denyOnboarding(id);
        } catch (error) {
          alert(`Failed to deny onboarding request: ${error.message}`);
        }
      });
    });

    container.querySelectorAll('[data-staff-workflow-rerun]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-staff-workflow-rerun');
        if (!id) {
          return;
        }
        try {
          await rerunWorkflow(id);
        } catch (error) {
          alert(`Failed to rerun workflow: ${error.message}`);
        }
      });
    });

    container.querySelectorAll('[data-staff-workflow-retry]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-staff-workflow-retry');
        if (!id) {
          return;
        }
        try {
          await retryWorkflow(id);
        } catch (error) {
          alert(`Failed to retry failed step: ${error.message}`);
        }
      });
    });

    container.querySelectorAll('[data-staff-workflow-resume]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-staff-workflow-resume');
        if (!id) {
          return;
        }
        try {
          await resumeWorkflow(id);
        } catch (error) {
          alert(`Failed to resume workflow: ${error.message}`);
        }
      });
    });

    container.querySelectorAll('[data-staff-workflow-force-complete]').forEach((button) => {
      button.addEventListener('click', async () => {
        const id = button.getAttribute('data-staff-workflow-force-complete');
        const currentStep = (button.getAttribute('data-current-step') || '').trim();
        if (!id) {
          return;
        }
        try {
          await forceCompleteWorkflow(id, { currentStep });
        } catch (error) {
          alert(`Failed to force-complete workflow step: ${error.message}`);
        }
      });
    });

    if (editActionButtons.invite) {
      editActionButtons.invite.addEventListener('click', async () => {
        if (!currentEditStaffId) {
          return;
        }
        try {
          setInlineError(editActionError, '');
          await sendInvite(currentEditStaffId, getActionNote());
        } catch (error) {
          setInlineError(editActionError, `Failed to send invitation: ${error.message}`);
        }
      });
    }
    if (editActionButtons.offboardingRequest) {
      editActionButtons.offboardingRequest.addEventListener('click', () => {
        if (!currentEditStaffId) {
          return;
        }
        openOffboardingRequestModal(currentEditStaffId);
      });
    }
    if (editActionButtons.approve) {
      editActionButtons.approve.addEventListener('click', async () => {
        if (!currentEditStaffId) {
          return;
        }
        try {
          setInlineError(editActionError, '');
          await approveOnboarding(currentEditStaffId, getActionNote());
        } catch (error) {
          setInlineError(editActionError, `Failed to approve onboarding request: ${error.message}`);
        }
      });
    }
    if (editActionButtons.deny) {
      editActionButtons.deny.addEventListener('click', async () => {
        if (!currentEditStaffId) {
          return;
        }
        try {
          setInlineError(editActionError, '');
          await denyOnboarding(currentEditStaffId, getActionNote({ required: true }) || '');
        } catch (error) {
          setInlineError(editActionError, `Failed to deny onboarding request: ${error.message}`);
        }
      });
    }
    if (editActionButtons.workflowRerun) {
      editActionButtons.workflowRerun.addEventListener('click', async () => {
        if (!currentEditStaffId) {
          return;
        }
        try {
          setInlineError(editActionError, '');
          await rerunWorkflow(currentEditStaffId, getActionNote() || '');
        } catch (error) {
          setInlineError(editActionError, `Failed to rerun workflow: ${error.message}`);
        }
      });
    }
    if (editActionButtons.workflowRetry) {
      editActionButtons.workflowRetry.addEventListener('click', async () => {
        if (!currentEditStaffId) {
          return;
        }
        try {
          setInlineError(editActionError, '');
          await retryWorkflow(currentEditStaffId, getActionNote() || '');
        } catch (error) {
          setInlineError(editActionError, `Failed to retry failed step: ${error.message}`);
        }
      });
    }
    if (editActionButtons.workflowResume) {
      editActionButtons.workflowResume.addEventListener('click', async () => {
        if (!currentEditStaffId) {
          return;
        }
        try {
          setInlineError(editActionError, '');
          await resumeWorkflow(currentEditStaffId, getActionNote() || '');
        } catch (error) {
          setInlineError(editActionError, `Failed to resume workflow: ${error.message}`);
        }
      });
    }
    if (editActionButtons.workflowForceComplete) {
      editActionButtons.workflowForceComplete.addEventListener('click', async () => {
        if (!currentEditStaffId) {
          return;
        }
        try {
          setInlineError(editActionError, '');
          await forceCompleteWorkflow(currentEditStaffId, {
            currentStep: (editActionButtons.workflowForceComplete.dataset.currentStep || '').trim(),
          }, getActionStep((editActionButtons.workflowForceComplete.dataset.currentStep || '').trim()), getActionNote() || '');
        } catch (error) {
          setInlineError(editActionError, `Failed to force-complete workflow step: ${error.message}`);
        }
      });
    }
    if (editActionButtons.delete) {
      editActionButtons.delete.addEventListener('click', async () => {
        if (!currentEditStaffId) {
          return;
        }
        try {
          setInlineError(editActionError, '');
          await deleteStaff(currentEditStaffId, Boolean(editDeleteConfirm && editDeleteConfirm.checked));
        } catch (error) {
          setInlineError(editActionError, `Failed to delete staff record: ${error.message}`);
        }
      });
    }


  });
})();
