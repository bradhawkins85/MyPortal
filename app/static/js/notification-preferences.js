(function () {
  function getCookie(name) {
    const pattern = `(?:^|; )${name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1')}=([^;]*)`;
    const matches = document.cookie.match(new RegExp(pattern));
    return matches ? decodeURIComponent(matches[1]) : '';
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) {
      return meta.getAttribute('content');
    }
    return getCookie('myportal_session_csrf');
  }

  const form = document.querySelector('[data-notification-preferences-form]');
  if (!form) {
    return;
  }

  const tbody = form.querySelector('[data-preferences-body]');
  const template = document.getElementById('notification-preference-row');
  const addInput = form.querySelector('[data-preferences-new-event]');
  const addButton = form.querySelector('[data-preferences-add]');
  const resetButton = form.querySelector('[data-preferences-reset]');
  const successAlert = form.querySelector('[data-preferences-success]');
  const errorAlert = form.querySelector('[data-preferences-error]');
  const endpoint = form.getAttribute('data-endpoint');

  const defaultAttribute = tbody ? tbody.getAttribute('data-defaults') : '[]';
  let defaultEventTypes;
  try {
    defaultEventTypes = JSON.parse(defaultAttribute || '[]');
  } catch (error) {
    defaultEventTypes = [];
  }
  const defaultSet = new Set(Array.isArray(defaultEventTypes) ? defaultEventTypes : []);

  function normalisePreferences(list) {
    const seen = new Set();
    const normalised = [];
    list.forEach((item) => {
      const eventType = (item && item.event_type ? String(item.event_type) : '').trim();
      if (!eventType || seen.has(eventType)) {
        return;
      }
      seen.add(eventType);
      normalised.push({
        event_type: eventType,
        channel_in_app: Boolean(item.channel_in_app),
        channel_email: Boolean(item.channel_email),
        channel_sms: Boolean(item.channel_sms),
      });
    });
    normalised.sort((a, b) => a.event_type.localeCompare(b.event_type));
    return normalised;
  }

  function serialisePreferences() {
    if (!tbody) {
      return [];
    }
    const rows = Array.from(tbody.querySelectorAll('tr[data-event-type]'));
    return rows
      .map((row) => {
        const input = row.querySelector('[data-preference-event]');
        const eventType = input ? input.value.trim() : '';
        if (!eventType) {
          return null;
        }
        const inApp = row.querySelector('[data-channel="channel_in_app"]');
        const email = row.querySelector('[data-channel="channel_email"]');
        const sms = row.querySelector('[data-channel="channel_sms"]');
        return {
          event_type: eventType,
          channel_in_app: inApp ? Boolean(inApp.checked) : true,
          channel_email: email ? Boolean(email.checked) : false,
          channel_sms: sms ? Boolean(sms.checked) : false,
        };
      })
      .filter(Boolean);
  }

  function renderPreferences(preferences) {
    if (!tbody || !template) {
      return;
    }
    const data = normalisePreferences(preferences);
    tbody.innerHTML = '';

    if (!data.length) {
      const emptyRow = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 5;
      cell.className = 'table__empty';
      cell.textContent = 'No notification events are available yet.';
      emptyRow.appendChild(cell);
      tbody.appendChild(emptyRow);
      return;
    }

    const fragment = document.createDocumentFragment();
    data.forEach((item) => {
      const clone = template.content.firstElementChild.cloneNode(true);
      clone.setAttribute('data-event-type', item.event_type);
      clone.setAttribute('data-default', defaultSet.has(item.event_type) ? '1' : '0');
      const name = clone.querySelector('.notification-preference__name');
      const hidden = clone.querySelector('[data-preference-event]');
      if (name) {
        name.textContent = item.event_type;
      }
      if (hidden) {
        hidden.value = item.event_type;
      }
      const inApp = clone.querySelector('[data-channel="channel_in_app"]');
      const email = clone.querySelector('[data-channel="channel_email"]');
      const sms = clone.querySelector('[data-channel="channel_sms"]');
      if (inApp) {
        inApp.checked = Boolean(item.channel_in_app);
      }
      if (email) {
        email.checked = Boolean(item.channel_email);
      }
      if (sms) {
        sms.checked = Boolean(item.channel_sms);
      }
      const removeButton = clone.querySelector('[data-preferences-remove]');
      if (removeButton && defaultSet.has(item.event_type)) {
        removeButton.disabled = true;
      }
      fragment.appendChild(clone);
    });

    tbody.appendChild(fragment);
  }

  function hideAlerts() {
    if (successAlert) {
      successAlert.hidden = true;
    }
    if (errorAlert) {
      errorAlert.hidden = true;
      errorAlert.textContent = '';
    }
  }

  function showSuccess(message) {
    hideAlerts();
    if (!successAlert) {
      return;
    }
    successAlert.textContent = message;
    successAlert.hidden = false;
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

  let initialState = normalisePreferences(serialisePreferences());
  renderPreferences(initialState);
  initialState = normalisePreferences(serialisePreferences());

  if (addButton) {
    addButton.addEventListener('click', () => {
      hideAlerts();
      if (!addInput) {
        return;
      }
      const value = addInput.value.trim();
      if (!value) {
        showError('Enter an event type to add.');
        addInput.focus();
        return;
      }
      if (value.length > 100) {
        showError('Event types may not exceed 100 characters.');
        addInput.focus();
        return;
      }
      const existing = normalisePreferences(serialisePreferences());
      if (existing.some((item) => item.event_type === value)) {
        showError('That event type is already listed.');
        addInput.focus();
        return;
      }
      existing.push({
        event_type: value,
        channel_in_app: true,
        channel_email: false,
        channel_sms: false,
      });
      renderPreferences(existing);
      addInput.value = '';
      addInput.focus();
    });
  }

  form.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (!target.matches('[data-preferences-remove]')) {
      return;
    }
    event.preventDefault();
    hideAlerts();
    const row = target.closest('tr[data-event-type]');
    if (!row) {
      return;
    }
    if (row.getAttribute('data-default') === '1') {
      return;
    }
    const eventType = row.getAttribute('data-event-type');
    const next = normalisePreferences(serialisePreferences()).filter(
      (item) => item.event_type !== eventType
    );
    renderPreferences(next);
  });

  if (resetButton) {
    resetButton.addEventListener('click', (event) => {
      event.preventDefault();
      hideAlerts();
      renderPreferences(initialState);
    });
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    hideAlerts();
    if (!endpoint) {
      showError('Unable to determine preferences endpoint.');
      return;
    }
    const payload = { preferences: normalisePreferences(serialisePreferences()) };
    try {
      const response = await fetch(endpoint, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': getCsrfToken(),
        },
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        const message = detail && detail.detail ? detail.detail : 'Failed to save notification preferences.';
        showError(message);
        return;
      }
      const data = await response.json();
      renderPreferences(data);
      initialState = normalisePreferences(data);
      showSuccess('Notification preferences saved successfully.');
    } catch (error) {
      showError('An unexpected error occurred while saving preferences.');
    }
  });
})();
