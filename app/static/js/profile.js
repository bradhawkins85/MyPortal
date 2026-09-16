(function () {
  const root = document.getElementById('profile-root');
  if (!root) {
    return;
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : null;
  }

  function formatErrorDetail(data, fallback) {
    let detail = fallback;
    if (data && data.detail) {
      detail = Array.isArray(data.detail)
        ? data.detail.map((entry) => entry.msg || entry).join(', ')
        : data.detail;
    }
    const reference = data && (data.error_reference || data.request_id);
    if (reference) {
      return `${detail} (Reference: ${reference})`;
    }
    return detail;
  }

  async function requestJson(url, options = {}) {
    const csrf = getCsrfToken();
    const headers = new Headers(options.headers || {});
    if (!headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    if (csrf && !headers.has('X-CSRF-Token')) {
      headers.set('X-CSRF-Token', csrf);
    }
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers,
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        detail = formatErrorDetail(data, detail);
      } catch (error) {
        /* ignore parse errors */
      }
      throw new Error(detail);
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  function supportsPasskeys() {
    return Boolean(window.PublicKeyCredential && navigator.credentials);
  }

  function decodeBase64Url(value) {
    const padding = '='.repeat((4 - (value.length % 4 || 4)) % 4);
    const base64 = `${value}${padding}`.replace(/-/g, '+').replace(/_/g, '/');
    const raw = window.atob(base64);
    const bytes = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index += 1) {
      bytes[index] = raw.charCodeAt(index);
    }
    return bytes.buffer;
  }

  function encodeBase64Url(buffer) {
    const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    let binary = '';
    bytes.forEach((byte) => {
      binary += String.fromCharCode(byte);
    });
    return window.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/u, '');
  }

  function decodeCredentialOptions(publicKey) {
    const options = { ...publicKey };
    if (typeof options.challenge === 'string') {
      options.challenge = decodeBase64Url(options.challenge);
    }
    if (options.user && typeof options.user.id === 'string') {
      options.user = { ...options.user, id: decodeBase64Url(options.user.id) };
    }
    if (Array.isArray(options.excludeCredentials)) {
      options.excludeCredentials = options.excludeCredentials.map((credential) => ({
        ...credential,
        id: decodeBase64Url(credential.id),
      }));
    }
    if (Array.isArray(options.allowCredentials)) {
      options.allowCredentials = options.allowCredentials.map((credential) => ({
        ...credential,
        id: decodeBase64Url(credential.id),
      }));
    }
    return options;
  }

  function serializeCredential(credential) {
    return {
      id: credential.id,
      type: credential.type,
      rawId: encodeBase64Url(credential.rawId),
      authenticatorAttachment: credential.authenticatorAttachment || null,
      response: {
        clientDataJSON: encodeBase64Url(credential.response.clientDataJSON),
        ...(credential.response.attestationObject
          ? {
              attestationObject: encodeBase64Url(credential.response.attestationObject),
              transports:
                typeof credential.response.getTransports === 'function'
                  ? credential.response.getTransports()
                  : [],
            }
          : {}),
        ...(credential.response.authenticatorData
          ? { authenticatorData: encodeBase64Url(credential.response.authenticatorData) }
          : {}),
        ...(credential.response.signature
          ? { signature: encodeBase64Url(credential.response.signature) }
          : {}),
        ...(credential.response.userHandle
          ? { userHandle: encodeBase64Url(credential.response.userHandle) }
          : {}),
      },
    };
  }

  function passkeyErrorMessage(error, fallback) {
    if (!error || !error.name) {
      return fallback;
    }
    if (error.name === 'NotAllowedError') {
      return 'The passkey request was cancelled, timed out, or no credential was available.';
    }
    if (error.name === 'InvalidStateError') {
      return 'This passkey is already registered to your account.';
    }
    if (error.name === 'NotSupportedError' || error.name === 'SecurityError') {
      return 'Passkeys are not supported in this browser or on this connection.';
    }
    return fallback;
  }

  function showMessage(config, message) {
    if (!message) {
      return;
    }
    const variant = config && config.variant ? config.variant : 'info';
    if (window.__portalToast && typeof window.__portalToast.show === 'function') {
      window.__portalToast.show(message, { variant });
      return;
    }
    window.alert(message);
  }

  function clearMessages(_elements) {
    // Intentionally no-op: status messages are surfaced as toast notifications.
  }

  const userId = root.dataset.userId;
  const clickToCallForm = document.getElementById('click-to-call-form');
  if (clickToCallForm) {
    const enabledInput = clickToCallForm.querySelector('#click-to-call-enabled');
    const phoneIpInput = clickToCallForm.querySelector('#click-to-call-phone-ip');
    const usernameInput = clickToCallForm.querySelector('#click-to-call-username');
    const passwordInput = clickToCallForm.querySelector('#click-to-call-password');
    requestJson('/api/click-to-call/settings').then((settings) => {
      enabledInput.checked = Boolean(settings.enabled);
      phoneIpInput.value = settings.phone_ip || '';
      usernameInput.value = settings.login_username || '';
    }).catch(() => showMessage({ variant: 'error' }, 'Unable to load click-to-call settings.'));

    clickToCallForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        await requestJson('/api/click-to-call/settings', {
          method: 'PUT',
          body: JSON.stringify({
            enabled: enabledInput.checked,
            phone_ip: phoneIpInput.value.trim() || null,
            login_username: usernameInput.value.trim() || null,
            password: passwordInput.value || null,
          }),
        });
        passwordInput.value = '';
        showMessage({ variant: 'success' }, 'Click-to-call settings saved.');
      } catch (error) {
        showMessage({ variant: 'error' }, error.message || 'Unable to save click-to-call settings.');
      }
    });
  }
  let totpDevices = [];
  try {
    const parsed = JSON.parse(root.dataset.totpDevices || '[]');
    if (Array.isArray(parsed)) {
      totpDevices = parsed.map((item) => ({
        id: item.id,
        name: item.name || 'Authenticator',
      }));
    }
  } catch (error) {
    totpDevices = [];
  }
  let passkeys = [];
  try {
    const parsed = JSON.parse(root.dataset.passkeys || '[]');
    if (Array.isArray(parsed)) {
      passkeys = parsed.map((item) => ({
        id: item.id,
        name: item.name || 'Passkey',
        created_at: item.created_at || null,
        last_used_at: item.last_used_at || null,
        transports: Array.isArray(item.transports) ? item.transports : [],
        credential_device_type: item.credential_device_type || null,
        credential_backed_up: Boolean(item.credential_backed_up),
      }));
    }
  } catch (error) {
    passkeys = [];
  }

  const passwordForm = document.getElementById('password-form');
  const passwordSuccess = { variant: 'success' };
  const passwordError = { variant: 'error' };

  if (passwordForm) {
    passwordForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([passwordSuccess, passwordError]);

      const current = passwordForm.querySelector('#current-password');
      const nextPassword = passwordForm.querySelector('#new-password');
      const confirmPassword = passwordForm.querySelector('#confirm-password');

      const currentValue = current ? current.value : '';
      const newValue = nextPassword ? nextPassword.value : '';
      const confirmValue = confirmPassword ? confirmPassword.value : '';

      if (newValue !== confirmValue) {
        showMessage(passwordError, 'New passwords do not match.');
        return;
      }

      try {
        await requestJson('/auth/password/change', {
          method: 'POST',
          body: JSON.stringify({
            current_password: currentValue,
            new_password: newValue,
          }),
        });
        showMessage(passwordSuccess, 'Password updated successfully.');
        passwordForm.reset();
      } catch (error) {
        showMessage(passwordError, error.message || 'Unable to update password.');
      }
    });
  }

  const mobileForm = document.getElementById('mobile-form');
  const mobileSuccess = { variant: 'success' };
  const mobileError = { variant: 'error' };

  if (mobileForm && userId) {
    mobileForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([mobileSuccess, mobileError]);

      const input = mobileForm.querySelector('#mobile-number');
      const value = input ? input.value.trim() : '';

      try {
        await requestJson(`/api/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ mobile_phone: value || null }),
        });
        showMessage(mobileSuccess, 'Mobile number saved.');
      } catch (error) {
        showMessage(mobileError, error.message || 'Unable to save mobile number.');
      }
    });
  }

  const bookingLinkForm = document.getElementById('booking-link-form');
  const bookingSuccess = { variant: 'success' };
  const bookingError = { variant: 'error' };

  if (bookingLinkForm && userId) {
    bookingLinkForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([bookingSuccess, bookingError]);

      const input = bookingLinkForm.querySelector('#booking-link-url');
      const value = input ? input.value.trim() : '';

      try {
        await requestJson(`/api/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ booking_link_url: value || null }),
        });
        showMessage(bookingSuccess, 'Booking link saved.');
      } catch (error) {
        showMessage(bookingError, error.message || 'Unable to save booking link.');
      }
    });
  }

  const emailSignatureForm = document.getElementById('email-signature-form');
  const signatureSuccess = { variant: 'success' };
  const signatureError = { variant: 'error' };

  if (emailSignatureForm && userId) {
    emailSignatureForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([signatureSuccess, signatureError]);

      const input = emailSignatureForm.querySelector('#email-signature-value');
      const value = input ? input.value.trim() : '';

      // Validate signature length (max 50KB)
      if (value.length > 51200) {
        showMessage(signatureError, 'Email signature is too large. Please keep it under 50KB.');
        return;
      }

      try {
        await requestJson(`/api/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ email_signature: value || null }),
        });
        showMessage(signatureSuccess, 'Email signature saved.');
      } catch (error) {
        showMessage(signatureError, error.message || 'Unable to save email signature.');
      }
    });
  }

  const matrixUsernameForm = document.getElementById('matrix-username-form');
  const matrixUsernameSuccess = { variant: 'success' };
  const matrixUsernameError = { variant: 'error' };

  if (matrixUsernameForm && userId) {
    matrixUsernameForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([matrixUsernameSuccess, matrixUsernameError]);

      const input = matrixUsernameForm.querySelector('#matrix-user-id');
      const value = input ? input.value.trim() : '';

      try {
        await requestJson(`/api/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ matrix_user_id: value || null }),
        });
        showMessage(matrixUsernameSuccess, 'Matrix username saved.');
      } catch (error) {
        showMessage(matrixUsernameError, error.message || 'Unable to save Matrix username.');
      }
    });
  }

  const sidebarSection = root.querySelector('[data-sidebar-customisation]');
  const sidebarItemsBody = root.querySelector('[data-sidebar-items]');
  const sidebarSaveButton = root.querySelector('[data-sidebar-save]');
  const sidebarResetButton = root.querySelector('[data-sidebar-reset]');
  const sidebarAddDividerButton = root.querySelector('[data-sidebar-add-divider]');
  const sidebarAddSpacerButton = root.querySelector('[data-sidebar-add-spacer]');
  const sidebarSuccess = { variant: 'success' };
  const sidebarError = { variant: 'error' };
  let sidebarState = [];
  const SIDEBAR_DIVIDER_KEY_PREFIX = '__divider__:';
  const SIDEBAR_SPACER_KEY_PREFIX = '__spacer__:';
  const SIDEBAR_PROTECTED_KEYS = new Set(['/admin/profile']);
  let dragSourceIndex = null;
  let touchDragSourceIndex = null;

  function renderSidebarItems() {
    if (!sidebarItemsBody) {
      return;
    }
    sidebarItemsBody.innerHTML = '';
    sidebarState.forEach((item, index) => {
      const row = document.createElement('tr');
      row.draggable = true;

      const visibleCell = document.createElement('td');
      const isProtected = SIDEBAR_PROTECTED_KEYS.has(item.key);
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = !item.hidden;
      checkbox.disabled = isProtected;
      checkbox.setAttribute('aria-label', `Toggle ${item.label}`);
      checkbox.addEventListener('change', () => {
        if (isProtected) {
          item.hidden = false;
          checkbox.checked = true;
          return;
        }
        item.hidden = !checkbox.checked;
      });
      visibleCell.appendChild(checkbox);
      if (isProtected) {
        const hint = document.createElement('span');
        hint.className = 'text-muted';
        hint.style.marginLeft = '0.4rem';
        hint.textContent = 'Required';
        visibleCell.appendChild(hint);
      }
      row.appendChild(visibleCell);

      const labelCell = document.createElement('td');
      labelCell.textContent = item.label;
      row.appendChild(labelCell);

      const handleCell = document.createElement('td');
      handleCell.className = 'table__actions';
      const handle = document.createElement('span');
      handle.className = 'sidebar-drag-handle';
      handle.setAttribute('aria-hidden', 'true');
      handle.innerHTML =
        '<svg viewBox="0 0 24 24" width="16" height="16" focusable="false" fill="currentColor">' +
        '<circle cx="9" cy="5" r="1.5"/><circle cx="15" cy="5" r="1.5"/>' +
        '<circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/>' +
        '<circle cx="9" cy="19" r="1.5"/><circle cx="15" cy="19" r="1.5"/>' +
        '</svg>';
      handleCell.appendChild(handle);
      row.appendChild(handleCell);

      const removeCell = document.createElement('td');
      removeCell.className = 'table__actions';
      const isDividerOrSpacer =
        item.key.startsWith(SIDEBAR_DIVIDER_KEY_PREFIX) ||
        item.key.startsWith(SIDEBAR_SPACER_KEY_PREFIX);
      if (isDividerOrSpacer) {
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'button button--danger button--small';
        removeButton.textContent = 'Remove';
        removeButton.setAttribute('aria-label', `Remove ${item.label}`);
        removeButton.addEventListener('click', () => {
          sidebarState.splice(index, 1);
          renderSidebarItems();
        });
        removeCell.appendChild(removeButton);
      }
      row.appendChild(removeCell);

      row.addEventListener('dragstart', (e) => {
        dragSourceIndex = index;
        e.dataTransfer.effectAllowed = 'move';
        // Defer opacity change so the drag ghost image is captured before the style is applied.
        setTimeout(() => row.classList.add('sidebar-row--dragging'), 0);
      });

      row.addEventListener('dragend', () => {
        dragSourceIndex = null;
        sidebarItemsBody.querySelectorAll('tr').forEach((r) => {
          r.classList.remove('sidebar-row--dragging');
          r.classList.remove('sidebar-row--drag-over');
        });
      });

      row.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (dragSourceIndex !== null && dragSourceIndex !== index) {
          sidebarItemsBody.querySelectorAll('tr').forEach((r) =>
            r.classList.remove('sidebar-row--drag-over'),
          );
          row.classList.add('sidebar-row--drag-over');
        }
      });

      row.addEventListener('drop', (e) => {
        e.preventDefault();
        if (dragSourceIndex === null || dragSourceIndex === index) {
          return;
        }
        const moved = sidebarState.splice(dragSourceIndex, 1)[0];
        // Removing the source shifts all subsequent indices down by one, so
        // when dragging downward the effective target index is one less.
        const insertAt = dragSourceIndex < index ? index - 1 : index;
        sidebarState.splice(insertAt, 0, moved);
        renderSidebarItems();
      });

      handle.addEventListener(
        'touchstart',
        (e) => {
          e.preventDefault();
          touchDragSourceIndex = index;
          row.classList.add('sidebar-row--dragging');
        },
        { passive: false },
      );

      handle.addEventListener(
        'touchmove',
        (e) => {
          e.preventDefault();
          if (touchDragSourceIndex === null) {
            return;
          }
          const touch = e.touches[0];
          const target = document.elementFromPoint(touch.clientX, touch.clientY);
          const targetRow = target ? target.closest('tr') : null;
          sidebarItemsBody.querySelectorAll('tr').forEach((r) =>
            r.classList.remove('sidebar-row--drag-over'),
          );
          if (targetRow && targetRow !== row && sidebarItemsBody.contains(targetRow)) {
            targetRow.classList.add('sidebar-row--drag-over');
          }
        },
        { passive: false },
      );

      handle.addEventListener('touchend', (e) => {
        if (touchDragSourceIndex === null) {
          return;
        }
        const touch = e.changedTouches[0];
        const target = document.elementFromPoint(touch.clientX, touch.clientY);
        const targetRow = target ? target.closest('tr') : null;
        sidebarItemsBody.querySelectorAll('tr').forEach((r) => {
          r.classList.remove('sidebar-row--dragging');
          r.classList.remove('sidebar-row--drag-over');
        });
        if (targetRow && sidebarItemsBody.contains(targetRow)) {
          const rows = Array.from(sidebarItemsBody.querySelectorAll('tr'));
          const targetIndex = rows.indexOf(targetRow);
          if (targetIndex !== -1 && targetIndex !== touchDragSourceIndex) {
            const src = touchDragSourceIndex;
            const moved = sidebarState.splice(src, 1)[0];
            // Removing the source shifts all subsequent indices down by one,
            // so when dragging downward the effective target index is one less.
            const insertAt = src < targetIndex ? targetIndex - 1 : targetIndex;
            sidebarState.splice(insertAt, 0, moved);
            renderSidebarItems();
          }
        }
        touchDragSourceIndex = null;
      }, { passive: false });

      sidebarItemsBody.appendChild(row);
    });
  }

  if (sidebarSection && window.MyPortalSidebarMenu) {
    sidebarState = window.MyPortalSidebarMenu.listItems().map((item) => ({
      ...item,
      hidden: SIDEBAR_PROTECTED_KEYS.has(item.key) ? false : Boolean(item.hidden),
    }));
    renderSidebarItems();

    if (sidebarSaveButton) {
      sidebarSaveButton.addEventListener('click', async () => {
        clearMessages([sidebarSuccess, sidebarError]);
        const payload = {
          order: sidebarState.map((item) => item.key),
          hidden: sidebarState
            .filter((item) => item.hidden && !SIDEBAR_PROTECTED_KEYS.has(item.key))
            .map((item) => item.key),
        };
        try {
          await window.MyPortalSidebarMenu.save(payload);
          showMessage(sidebarSuccess, 'Left menu preferences saved.');
        } catch (error) {
          showMessage(sidebarError, error.message || 'Unable to save left menu preferences.');
        }
      });
    }


    if (sidebarAddDividerButton) {
      sidebarAddDividerButton.addEventListener('click', () => {
        clearMessages([sidebarSuccess, sidebarError]);
        sidebarState.push({
          key: `${SIDEBAR_DIVIDER_KEY_PREFIX}${Date.now()}`,
          label: 'Divider',
          hidden: false,
        });
        renderSidebarItems();
      });
    }

    if (sidebarAddSpacerButton) {
      sidebarAddSpacerButton.addEventListener('click', () => {
        clearMessages([sidebarSuccess, sidebarError]);
        sidebarState.push({
          key: `${SIDEBAR_SPACER_KEY_PREFIX}${Date.now()}`,
          label: 'Spacer',
          hidden: false,
        });
        renderSidebarItems();
      });
    }

    if (sidebarResetButton) {
      sidebarResetButton.addEventListener('click', () => {
        clearMessages([sidebarSuccess, sidebarError]);
        sidebarState = window.MyPortalSidebarMenu
          .listItems()
          .map((item) => ({ ...item, hidden: false }));
        renderSidebarItems();
      });
    }
  }

  const totpTable = document.getElementById('totp-table');
  const totpBody = root.querySelector('[data-totp-body]');
  const totpEmptyRow = root.querySelector('[data-totp-empty]');
  const addButton = root.querySelector('[data-totp-add]');
  const setupSection = root.querySelector('[data-totp-setup]');
  const secretInput = document.getElementById('totp-secret');
  const linkInput = document.getElementById('totp-link');
  const verifyForm = document.getElementById('totp-verify-form');
  const verifyName = document.getElementById('totp-name');
  const verifyCode = document.getElementById('totp-code');
  const verifySuccess = { variant: 'success' };
  const verifyError = { variant: 'error' };
  const cancelButton = root.querySelector('[data-totp-cancel]');

  function renderTotpDevices() {
    if (!totpBody) {
      return;
    }
    totpDevices.sort((a, b) => {
      const nameA = (a.name || '').toLowerCase();
      const nameB = (b.name || '').toLowerCase();
      if (nameA < nameB) return -1;
      if (nameA > nameB) return 1;
      return 0;
    });
    totpBody.innerHTML = '';
    if (!totpDevices.length) {
      if (totpEmptyRow) {
        totpEmptyRow.hidden = false;
        totpBody.appendChild(totpEmptyRow);
      }
    } else {
      if (totpEmptyRow) {
        totpEmptyRow.hidden = true;
      }
      totpDevices.forEach((device) => {
        const row = document.createElement('tr');
        row.dataset.deviceId = String(device.id);

        const nameCell = document.createElement('td');
        nameCell.textContent = device.name || 'Authenticator';
        nameCell.setAttribute('data-label', 'Name');
        nameCell.setAttribute('data-value', (device.name || '').toLowerCase());
        row.appendChild(nameCell);

        const actionsCell = document.createElement('td');
        actionsCell.className = 'table__actions';
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'button button--danger button--small';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => handleRemoveTotp(device));
        actionsCell.appendChild(removeButton);
        row.appendChild(actionsCell);

        totpBody.appendChild(row);
      });
    }
    if (totpTable) {
      const event = new CustomEvent('table:rows-updated');
      totpTable.dispatchEvent(event);
    }
  }

  function resetTotpSetup() {
    if (setupSection) {
      setupSection.hidden = true;
    }
    if (secretInput) {
      secretInput.value = '';
    }
    if (linkInput) {
      linkInput.value = '';
    }
    if (verifyName) {
      verifyName.value = '';
    }
    if (verifyCode) {
      verifyCode.value = '';
    }
    clearMessages([verifySuccess, verifyError]);
  }

  async function startTotpSetup() {
    clearMessages([verifySuccess, verifyError]);
    resetTotpSetup();
    try {
      const response = await requestJson('/auth/totp/setup', { method: 'POST' });
      if (secretInput) {
        secretInput.value = response.secret || '';
      }
      if (linkInput) {
        linkInput.value = response.otpauth_url || '';
      }
      if (setupSection) {
        setupSection.hidden = false;
      }
      if (verifyCode) {
        verifyCode.focus();
      }
    } catch (error) {
      alert(`Unable to start authenticator setup: ${error.message}`);
    }
  }

  async function handleRemoveTotp(device) {
    if (!device || !device.id) {
      return;
    }
    const confirmRemoval = window.confirm(`Remove authenticator "${device.name}"?`);
    if (!confirmRemoval) {
      return;
    }
    try {
      await requestJson(`/auth/totp/${device.id}`, { method: 'DELETE' });
      totpDevices = totpDevices.filter((entry) => entry.id !== device.id);
      renderTotpDevices();
    } catch (error) {
      alert(`Unable to remove authenticator: ${error.message}`);
    }
  }

  if (addButton) {
    addButton.addEventListener('click', () => {
      startTotpSetup();
    });
  }

  if (cancelButton) {
    cancelButton.addEventListener('click', () => {
      resetTotpSetup();
    });
  }

  if (verifyForm) {
    verifyForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([verifySuccess, verifyError]);

      const codeRaw = verifyCode ? verifyCode.value.trim() : '';
      const normalisedCode = codeRaw.replace(/\s+/g, '');
      if (!normalisedCode) {
        showMessage(verifyError, 'Enter the authenticator code.');
        return;
      }
      if (!/^\d+$/.test(normalisedCode)) {
        showMessage(verifyError, 'Authenticator codes must contain digits only.');
        return;
      }

      const nameValue = verifyName ? verifyName.value.trim() : '';
      try {
        const response = await requestJson('/auth/totp/verify', {
          method: 'POST',
          body: JSON.stringify({
            code: normalisedCode,
            name: nameValue || null,
          }),
        });
        totpDevices.push({ id: response.id, name: response.name || 'Authenticator' });
        renderTotpDevices();
        showMessage(verifySuccess, 'Authenticator added successfully.');
        if (verifyCode) {
          verifyCode.value = '';
        }
        if (verifyName) {
          verifyName.value = '';
        }
        if (secretInput) {
          secretInput.value = '';
        }
        if (linkInput) {
          linkInput.value = '';
        }
        if (setupSection) {
          setupSection.hidden = true;
        }
      } catch (error) {
        showMessage(verifyError, error.message || 'Unable to verify authenticator.');
      }
    });
  }

  const passkeyTable = document.getElementById('passkey-table');
  const passkeyBody = root.querySelector('[data-passkey-body]');
  const passkeyEmptyRow = root.querySelector('[data-passkey-empty]');
  const passkeyAddForm = document.getElementById('passkey-add-form');
  const passkeyNameInput = document.getElementById('passkey-name');
  const passkeyPasswordInput = document.getElementById('passkey-current-password');
  const passkeySuccess = { variant: 'success' };
  const passkeyError = { variant: 'error' };

  function formatDateTime(value) {
    if (!value) {
      return 'Never';
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleString();
  }

  function renderPasskeys() {
    if (!passkeyBody) {
      return;
    }
    passkeys.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' }));
    passkeyBody.innerHTML = '';
    if (!passkeys.length) {
      if (passkeyEmptyRow) {
        passkeyEmptyRow.hidden = false;
        passkeyBody.appendChild(passkeyEmptyRow);
      }
    } else {
      if (passkeyEmptyRow) {
        passkeyEmptyRow.hidden = true;
      }
      passkeys.forEach((item) => {
        const row = document.createElement('tr');

        const nameCell = document.createElement('td');
        const transportSuffix = item.transports.length ? ` (${item.transports.join(', ')})` : '';
        nameCell.textContent = `${item.name || 'Passkey'}${transportSuffix}`;
        row.appendChild(nameCell);

        const createdCell = document.createElement('td');
        createdCell.textContent = formatDateTime(item.created_at);
        row.appendChild(createdCell);

        const lastUsedCell = document.createElement('td');
        lastUsedCell.textContent = formatDateTime(item.last_used_at);
        row.appendChild(lastUsedCell);

        const actionsCell = document.createElement('td');
        actionsCell.className = 'table__actions';

        const renameButton = document.createElement('button');
        renameButton.type = 'button';
        renameButton.className = 'button button--ghost button--small';
        renameButton.textContent = 'Rename';
        renameButton.addEventListener('click', () => renamePasskey(item));
        actionsCell.appendChild(renameButton);

        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'button button--danger button--small';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => removePasskey(item));
        actionsCell.appendChild(removeButton);

        row.appendChild(actionsCell);
        passkeyBody.appendChild(row);
      });
    }
    if (passkeyTable) {
      passkeyTable.dispatchEvent(new CustomEvent('table:rows-updated'));
    }
  }

  async function renamePasskey(item) {
    const nextName = window.prompt('Rename passkey', item.name || 'Passkey');
    if (!nextName || !nextName.trim()) {
      return;
    }
    try {
      const updated = await requestJson(`/auth/passkeys/${item.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name: nextName.trim() }),
      });
      passkeys = passkeys.map((entry) => (entry.id === item.id ? updated : entry));
      renderPasskeys();
      showMessage(passkeySuccess, 'Passkey renamed.');
    } catch (error) {
      showMessage(passkeyError, error.message || 'Unable to rename passkey.');
    }
  }

  async function removePasskey(item) {
    const confirmed = window.confirm(`Remove passkey "${item.name}"?`);
    if (!confirmed) {
      return;
    }
    const currentPassword = window.prompt(`Enter your current password to remove "${item.name}"`);
    if (!currentPassword) {
      return;
    }
    try {
      await requestJson(`/auth/passkeys/${item.id}`, {
        method: 'DELETE',
        body: JSON.stringify({ current_password: currentPassword }),
      });
      passkeys = passkeys.filter((entry) => entry.id !== item.id);
      renderPasskeys();
      showMessage(passkeySuccess, 'Passkey removed.');
    } catch (error) {
      showMessage(passkeyError, error.message || 'Unable to remove passkey.');
    }
  }

  if (passkeyAddForm) {
    if (!supportsPasskeys()) {
      const submitButton = passkeyAddForm.querySelector('[data-passkey-add]');
      if (submitButton) {
        submitButton.disabled = true;
      }
      showMessage(passkeyError, 'Passkeys are not supported in this browser or on this connection.');
    }
    passkeyAddForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!supportsPasskeys()) {
        showMessage(passkeyError, 'Passkeys are not supported in this browser or on this connection.');
        return;
      }
      const passkeyName = passkeyNameInput ? passkeyNameInput.value.trim() : '';
      const currentPassword = passkeyPasswordInput ? passkeyPasswordInput.value : '';
      if (!passkeyName) {
        showMessage(passkeyError, 'Enter a name for the passkey.');
        return;
      }
      if (!currentPassword) {
        showMessage(passkeyError, 'Enter your current password to continue.');
        return;
      }
      try {
        const options = await requestJson('/auth/passkeys/register/options', {
          method: 'POST',
          body: JSON.stringify({ current_password: currentPassword }),
        });
        const credential = await navigator.credentials.create({
          publicKey: decodeCredentialOptions(options.public_key),
        });
        if (!credential) {
          throw new Error('No passkey was created.');
        }
        const created = await requestJson('/auth/passkeys/register/verify', {
          method: 'POST',
          body: JSON.stringify({
            challenge_id: options.challenge_id,
            name: passkeyName,
            credential: serializeCredential(credential),
          }),
        });
        passkeys.push(created);
        renderPasskeys();
        if (passkeyAddForm) {
          passkeyAddForm.reset();
        }
        showMessage(passkeySuccess, 'Passkey registered successfully.');
      } catch (error) {
        showMessage(
          passkeyError,
          passkeyErrorMessage(error, error.message || 'Unable to register a passkey.'),
        );
      }
    });
  }

  root.querySelectorAll('[data-copy-target]').forEach((button) => {
    button.addEventListener('click', async () => {
      const targetId = button.getAttribute('data-copy-target');
      if (!targetId) {
        return;
      }
      const target = document.getElementById(targetId);
      if (!target) {
        return;
      }
      try {
        await navigator.clipboard.writeText(target.value || '');
        button.textContent = 'Copied';
        setTimeout(() => {
          button.textContent = 'Copy';
        }, 2000);
      } catch (error) {
        alert('Unable to copy to clipboard.');
      }
    });
  });

  renderTotpDevices();
  renderPasskeys();
})();
