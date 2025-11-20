(function () {
  const root = document.getElementById('profile-root');
  if (!root) {
    return;
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : null;
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
        if (data && data.detail) {
          detail = Array.isArray(data.detail)
            ? data.detail.map((entry) => entry.msg || entry).join(', ')
            : data.detail;
        }
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

  function showMessage(element, message) {
    if (!element) {
      return;
    }
    element.textContent = message;
    element.hidden = !message;
  }

  function clearMessages(elements) {
    elements.forEach((element) => {
      if (element) {
        element.hidden = true;
        element.textContent = '';
      }
    });
  }

  const userId = root.dataset.userId;
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

  const passwordForm = document.getElementById('password-form');
  const passwordSuccess = document.querySelector('[data-password-success]');
  const passwordError = document.querySelector('[data-password-error]');

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
  const mobileSuccess = document.querySelector('[data-mobile-success]');
  const mobileError = document.querySelector('[data-mobile-error]');

  if (mobileForm && userId) {
    mobileForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([mobileSuccess, mobileError]);

      const input = mobileForm.querySelector('#mobile-number');
      const value = input ? input.value.trim() : '';

      try {
        await requestJson(`/users/${userId}`, {
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
  const bookingSuccess = document.querySelector('[data-booking-success]');
  const bookingError = document.querySelector('[data-booking-error]');

  if (bookingLinkForm && userId) {
    bookingLinkForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([bookingSuccess, bookingError]);

      const input = bookingLinkForm.querySelector('#booking-link-url');
      const value = input ? input.value.trim() : '';

      try {
        await requestJson(`/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ booking_link_url: value || null }),
        });
        showMessage(bookingSuccess, 'Booking link saved.');
      } catch (error) {
        showMessage(bookingError, error.message || 'Unable to save booking link.');
      }
    });
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
  const verifySuccess = document.querySelector('[data-totp-success]');
  const verifyError = document.querySelector('[data-totp-error]');
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
})();
