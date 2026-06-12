(function () {
  const root = document.getElementById('totp-enrol-root');
  if (!root) {
    return;
  }

  const secretInput = document.getElementById('totp-secret');
  const linkInput = document.getElementById('totp-link');
  const form = document.getElementById('totp-enrol-form');
  const nameInput = document.getElementById('totp-name');
  const codeInput = document.getElementById('totp-code');
  const submitButton = root.querySelector('[data-totp-submit]');
  const refreshButton = root.querySelector('[data-totp-refresh]');
  const logoutButton = root.querySelector('[data-logout]');

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : null;
  }

  function toast(message, variant = 'info') {
    if (window.__portalToast && typeof window.__portalToast.show === 'function') {
      window.__portalToast.show(message, { variant });
      return;
    }
    window.alert(message);
  }

  function formatError(data, fallback) {
    if (!data || !data.detail) {
      return fallback;
    }
    if (Array.isArray(data.detail)) {
      return data.detail.map((entry) => entry.msg || entry).join(', ');
    }
    return data.detail;
  }

  async function requestJson(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (!headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    const csrf = getCsrfToken();
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
        detail = formatError(await response.json(), detail);
      } catch (error) {
        // Keep the HTTP status fallback when the response is not JSON.
      }
      throw new Error(detail);
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  async function startSetup() {
    if (refreshButton) {
      refreshButton.disabled = true;
    }
    try {
      const result = await requestJson('/auth/totp/setup', { method: 'POST' });
      if (secretInput) {
        secretInput.value = result.secret || '';
      }
      if (linkInput) {
        linkInput.value = result.otpauth_url || '';
      }
      if (codeInput) {
        codeInput.value = '';
        codeInput.focus();
      }
    } catch (error) {
      toast(error.message || 'Unable to start authenticator setup.', 'error');
    } finally {
      if (refreshButton) {
        refreshButton.disabled = false;
      }
    }
  }

  if (form) {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const code = codeInput ? codeInput.value.trim().replace(/\s+/g, '') : '';
      if (!/^\d{6}$/.test(code)) {
        toast('Enter the six-digit authenticator code.', 'error');
        return;
      }
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = 'Verifying…';
      }
      try {
        await requestJson('/auth/totp/verify', {
          method: 'POST',
          body: JSON.stringify({
            code,
            name: nameInput && nameInput.value.trim() ? nameInput.value.trim() : null,
          }),
        });
        toast('Two-factor authentication is enabled.', 'success');
        window.location.assign('/');
      } catch (error) {
        toast(error.message || 'Unable to verify authenticator.', 'error');
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
          submitButton.textContent = 'Verify and continue';
        }
      }
    });
  }

  if (refreshButton) {
    refreshButton.addEventListener('click', () => startSetup());
  }

  if (logoutButton) {
    logoutButton.addEventListener('click', async () => {
      try {
        await requestJson('/auth/logout', { method: 'POST' });
      } catch (error) {
        // Continue to the login page even if the session was already gone.
      }
      window.location.assign('/login');
    });
  }

  root.querySelectorAll('[data-copy-target]').forEach((button) => {
    button.addEventListener('click', async () => {
      const targetId = button.getAttribute('data-copy-target');
      const target = targetId ? document.getElementById(targetId) : null;
      if (!target || !target.value) {
        return;
      }
      try {
        await navigator.clipboard.writeText(target.value);
        toast('Copied to clipboard.', 'success');
      } catch (error) {
        target.focus();
        target.select();
        toast('Copy failed. Select the field text manually.', 'error');
      }
    });
  });

  startSetup();
})();
