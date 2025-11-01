(function () {
  const adminSection = document.querySelector('[data-notification-admin]');
  if (!adminSection) {
    return;
  }

  const menu = document.querySelector('[data-notification-event-menu]');
  const form = adminSection.querySelector('[data-event-settings-form]');
  const saveButton = adminSection.querySelector('[data-event-settings-save]');
  const resetButton = adminSection.querySelector('[data-event-settings-reset]');
  const successAlert = adminSection.querySelector('[data-event-settings-success]');
  const errorAlert = adminSection.querySelector('[data-event-settings-error]');
  const actionList = adminSection.querySelector('[data-event-action-list]');
  const addActionButton = adminSection.querySelector('[data-event-action-add]');
  const actionTemplate = document.getElementById('notification-event-action-row');
  const hiddenEventInput = form ? form.querySelector('[data-event-type]') : null;
  const displayNameInput = form ? form.querySelector('[data-event-display-name]') : null;
  const descriptionInput = form ? form.querySelector('[data-event-description]') : null;
  const templateInput = form ? form.querySelector('[data-event-message-template]') : null;
  const allowInAppInput = form ? form.querySelector('[data-event-allow-in-app]') : null;
  const allowEmailInput = form ? form.querySelector('[data-event-allow-email]') : null;
  const allowSmsInput = form ? form.querySelector('[data-event-allow-sms]') : null;
  const defaultInAppInput = form ? form.querySelector('[data-event-default-in-app]') : null;
  const defaultEmailInput = form ? form.querySelector('[data-event-default-email]') : null;
  const defaultSmsInput = form ? form.querySelector('[data-event-default-sms]') : null;
  const visibleInput = form ? form.querySelector('[data-event-visible]') : null;

  if (!menu || !form || !saveButton || !resetButton || !actionList || !addActionButton || !actionTemplate) {
    return;
  }

  const endpointBase = form.getAttribute('data-endpoint') || '';

  function parseJsonAttribute(attribute, fallback) {
    try {
      return JSON.parse(attribute);
    } catch (error) {
      return fallback;
    }
  }

  const modulesRaw = form.getAttribute('data-modules') || '[]';
  const moduleEntries = parseJsonAttribute(modulesRaw, []);
  const moduleOptions = moduleEntries
    .map((entry) => ({
      value: String(entry.slug || '').trim(),
      label: String(entry.name || entry.slug || '').trim() || 'Module',
    }))
    .filter((option) => option.value);

  const settingsRaw = form.getAttribute('data-settings') || '[]';
  const settingsList = parseJsonAttribute(settingsRaw, []);
  const settingsMap = new Map();
  settingsList.forEach((entry) => {
    if (!entry || !entry.event_type) {
      return;
    }
    const eventType = String(entry.event_type).trim();
    if (!eventType) {
      return;
    }
    settingsMap.set(eventType, {
      event_type: eventType,
      display_name: entry.display_name || eventType,
      description: entry.description || '',
      message_template: entry.message_template || '{{ message }}',
      is_user_visible: Boolean(entry.is_user_visible ?? true),
      allow_channel_in_app: Boolean(entry.allow_channel_in_app ?? true),
      allow_channel_email: Boolean(entry.allow_channel_email ?? false),
      allow_channel_sms: Boolean(entry.allow_channel_sms ?? false),
      default_channel_in_app: Boolean(entry.default_channel_in_app ?? true),
      default_channel_email: Boolean(entry.default_channel_email ?? false),
      default_channel_sms: Boolean(entry.default_channel_sms ?? false),
      module_actions: Array.isArray(entry.module_actions) ? entry.module_actions : [],
    });
  });

  function createEmptySetting(eventType) {
    return {
      event_type: eventType,
      display_name: eventType,
      description: '',
      message_template: '{{ message }}',
      is_user_visible: true,
      allow_channel_in_app: true,
      allow_channel_email: false,
      allow_channel_sms: false,
      default_channel_in_app: true,
      default_channel_email: false,
      default_channel_sms: false,
      module_actions: [],
    };
  }

  function getSetting(eventType) {
    if (!settingsMap.has(eventType)) {
      settingsMap.set(eventType, createEmptySetting(eventType));
    }
    return settingsMap.get(eventType);
  }

  function clearAlerts() {
    if (successAlert) {
      successAlert.hidden = true;
      successAlert.textContent = '';
    }
    if (errorAlert) {
      errorAlert.hidden = true;
      errorAlert.textContent = '';
    }
  }

  function showError(message) {
    if (!errorAlert) {
      return;
    }
    errorAlert.textContent = message;
    errorAlert.hidden = false;
    if (successAlert) {
      successAlert.hidden = true;
    }
  }

  function showSuccess(message) {
    if (!successAlert) {
      return;
    }
    successAlert.textContent = message;
    successAlert.hidden = false;
    if (errorAlert) {
      errorAlert.hidden = true;
    }
  }

  function buildModuleSelect(value) {
    const select = document.createElement('select');
    select.className = 'form-input';
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
    select.value = value || '';
    return select;
  }

  function renderActions(actions) {
    actionList.innerHTML = '';
    const list = Array.isArray(actions) ? actions : [];
    if (!list.length) {
      return;
    }
    list.forEach((action) => {
      addActionRow(action);
    });
  }

  function addActionRow(action) {
    const clone = actionTemplate.content.firstElementChild.cloneNode(true);
    const moduleSelectContainer = clone.querySelector('[data-action-module]');
    const payloadTextarea = clone.querySelector('[data-action-payload]');
    const removeButton = clone.querySelector('[data-action-remove]');

    if (moduleSelectContainer) {
      const select = buildModuleSelect(action && action.module ? action.module : '');
      moduleSelectContainer.replaceWith(select);
      select.setAttribute('data-action-module', '');
    }

    if (payloadTextarea) {
      if (action && action.payload) {
        try {
          payloadTextarea.value = JSON.stringify(action.payload, null, 2);
        } catch (error) {
          payloadTextarea.value = String(action.payload);
        }
      } else {
        payloadTextarea.value = '';
      }
    }

    if (removeButton) {
      removeButton.addEventListener('click', (event) => {
        event.preventDefault();
        clearAlerts();
        clone.remove();
      });
    }

    actionList.appendChild(clone);
  }

  addActionButton.addEventListener('click', (event) => {
    event.preventDefault();
    clearAlerts();
    addActionRow({ module: '', payload: {} });
  });

  function setMenuSelection(eventType) {
    const buttons = menu.querySelectorAll('[data-event-select]');
    buttons.forEach((button) => {
      if (button.getAttribute('data-event-id') === eventType) {
        button.classList.add('menu__link--active');
        button.setAttribute('aria-current', 'true');
      } else {
        button.classList.remove('menu__link--active');
        button.removeAttribute('aria-current');
      }
    });
  }

  function populateForm(setting) {
    if (!setting) {
      return;
    }
    if (hiddenEventInput) {
      hiddenEventInput.value = setting.event_type;
    }
    if (displayNameInput) {
      displayNameInput.value = setting.display_name || setting.event_type;
    }
    if (descriptionInput) {
      descriptionInput.value = setting.description || '';
    }
    if (templateInput) {
      templateInput.value = setting.message_template || '{{ message }}';
    }
    if (allowInAppInput) {
      allowInAppInput.checked = Boolean(setting.allow_channel_in_app);
    }
    if (allowEmailInput) {
      allowEmailInput.checked = Boolean(setting.allow_channel_email);
    }
    if (allowSmsInput) {
      allowSmsInput.checked = Boolean(setting.allow_channel_sms);
    }
    if (defaultInAppInput) {
      defaultInAppInput.checked = Boolean(setting.default_channel_in_app);
    }
    if (defaultEmailInput) {
      defaultEmailInput.checked = Boolean(setting.default_channel_email);
    }
    if (defaultSmsInput) {
      defaultSmsInput.checked = Boolean(setting.default_channel_sms);
    }
    if (visibleInput) {
      visibleInput.checked = Boolean(setting.is_user_visible);
    }
    renderActions(setting.module_actions);
  }

  function loadEvent(eventType) {
    if (!eventType) {
      return;
    }
    const setting = getSetting(eventType);
    setMenuSelection(eventType);
    clearAlerts();
    populateForm(setting);
  }

  function parseActionRows() {
    const rows = Array.from(actionList.querySelectorAll('[data-action-row]'));
    const actions = [];
    for (let index = 0; index < rows.length; index += 1) {
      const row = rows[index];
      const moduleSelect = row.querySelector('[data-action-module]');
      const payloadTextarea = row.querySelector('[data-action-payload]');
      const moduleValue = moduleSelect ? moduleSelect.value.trim() : '';
      if (!moduleValue) {
        showError(`Select a module for action ${index + 1}.`);
        return null;
      }
      let payload = {};
      const payloadText = payloadTextarea ? payloadTextarea.value.trim() : '';
      if (payloadText) {
        try {
          payload = JSON.parse(payloadText);
        } catch (error) {
          showError(`Action ${index + 1} payload must be valid JSON.`);
          return null;
        }
      }
      actions.push({ module: moduleValue, payload });
    }
    return actions;
  }

  function collectFormState() {
    const eventType = hiddenEventInput ? hiddenEventInput.value.trim() : '';
    if (!eventType) {
      showError('Select a notification event to configure.');
      return null;
    }
    const displayName = displayNameInput ? displayNameInput.value.trim() : '';
    if (!displayName) {
      showError('Enter a display name for the notification event.');
      return null;
    }
    const messageTemplate = templateInput ? templateInput.value.trim() : '';
    if (!messageTemplate) {
      showError('Provide a message template for the notification event.');
      return null;
    }
    const actions = parseActionRows();
    if (actions === null) {
      return null;
    }
    return {
      event_type: eventType,
      display_name: displayName,
      description: descriptionInput ? descriptionInput.value.trim() : '',
      message_template: messageTemplate,
      allow_channel_in_app: allowInAppInput ? Boolean(allowInAppInput.checked) : true,
      allow_channel_email: allowEmailInput ? Boolean(allowEmailInput.checked) : false,
      allow_channel_sms: allowSmsInput ? Boolean(allowSmsInput.checked) : false,
      default_channel_in_app: defaultInAppInput ? Boolean(defaultInAppInput.checked) : true,
      default_channel_email: defaultEmailInput ? Boolean(defaultEmailInput.checked) : false,
      default_channel_sms: defaultSmsInput ? Boolean(defaultSmsInput.checked) : false,
      is_user_visible: visibleInput ? Boolean(visibleInput.checked) : true,
      module_actions: actions,
    };
  }

  async function saveCurrentEvent() {
    const state = collectFormState();
    if (!state) {
      return;
    }
    clearAlerts();
    const eventType = state.event_type;
    const url = endpointBase ? `${endpointBase}/${encodeURIComponent(eventType)}` : '';
    if (!url) {
      showError('Unable to determine configuration endpoint.');
      return;
    }
    try {
      const response = await fetch(url, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': getCsrfToken(),
        },
        credentials: 'same-origin',
        body: JSON.stringify({
          display_name: state.display_name,
          description: state.description || null,
          message_template: state.message_template,
          is_user_visible: state.is_user_visible,
          allow_channel_in_app: state.allow_channel_in_app,
          allow_channel_email: state.allow_channel_email,
          allow_channel_sms: state.allow_channel_sms,
          default_channel_in_app: state.default_channel_in_app,
          default_channel_email: state.default_channel_email,
          default_channel_sms: state.default_channel_sms,
          module_actions: state.module_actions,
        }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        const message = detail && detail.detail ? detail.detail : 'Failed to save notification event settings.';
        showError(message);
        return;
      }
      const updated = await response.json();
      settingsMap.set(eventType, {
        event_type: updated.event_type,
        display_name: updated.display_name,
        description: updated.description || '',
        message_template: updated.message_template,
        is_user_visible: Boolean(updated.is_user_visible),
        allow_channel_in_app: Boolean(updated.allow_channel_in_app),
        allow_channel_email: Boolean(updated.allow_channel_email),
        allow_channel_sms: Boolean(updated.allow_channel_sms),
        default_channel_in_app: Boolean(updated.default_channel_in_app),
        default_channel_email: Boolean(updated.default_channel_email),
        default_channel_sms: Boolean(updated.default_channel_sms),
        module_actions: Array.isArray(updated.module_actions) ? updated.module_actions : [],
      });
      showSuccess('Event settings saved successfully.');
      populateForm(getSetting(eventType));
    } catch (error) {
      showError('An unexpected error occurred while saving event settings.');
    }
  }

  let initialEvent = null;
  const initialButton = menu.querySelector('[data-event-select]');
  if (initialButton) {
    initialEvent = initialButton.getAttribute('data-event-id');
  }
  if (!initialEvent && settingsMap.size) {
    initialEvent = settingsMap.keys().next().value;
  }

  menu.addEventListener('click', (event) => {
    const target = event.target.closest('[data-event-select]');
    if (!target) {
      return;
    }
    event.preventDefault();
    const eventType = target.getAttribute('data-event-id');
    if (eventType) {
      loadEvent(eventType);
    }
  });

  saveButton.addEventListener('click', (event) => {
    event.preventDefault();
    saveCurrentEvent();
  });

  resetButton.addEventListener('click', (event) => {
    event.preventDefault();
    const eventType = hiddenEventInput ? hiddenEventInput.value.trim() : '';
    if (eventType) {
      clearAlerts();
      populateForm(getSetting(eventType));
    }
  });

  if (initialEvent) {
    loadEvent(initialEvent);
  }
})();
