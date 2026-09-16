const initAuthForms = () => {
  const forms = document.querySelectorAll('[data-auth-form]');
  forms.forEach((form) => new AuthForm(form));
  initPasskeyLogin();
};

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
    return 'The passkey request was cancelled or timed out. You can still sign in with your password.';
  }
  if (error.name === 'InvalidStateError') {
    return 'This passkey is already registered to your account.';
  }
  if (error.name === 'NotSupportedError' || error.name === 'SecurityError') {
    return 'Passkeys are not available in this browser or on this connection. Use your password instead.';
  }
  return fallback;
}

function initPasskeyLogin() {
  const button = document.querySelector('[data-passkey-login]');
  const errorContainer = document.querySelector('[data-passkey-error]');
  if (!button) {
    return;
  }
  if (!supportsPasskeys()) {
    button.disabled = true;
    if (errorContainer) {
      errorContainer.hidden = false;
      errorContainer.textContent = 'Passkeys are not supported in this browser. Sign in with your password instead.';
    }
    return;
  }
  const defaultLabel = button.textContent;
  button.addEventListener('click', async () => {
    if (errorContainer) {
      errorContainer.hidden = true;
      errorContainer.textContent = '';
    }
    button.disabled = true;
    button.textContent = 'Waiting for passkey…';
    try {
      const optionsResponse = await fetch('/auth/passkeys/authenticate/options', {
        method: 'POST',
        credentials: 'include',
        headers: {
          Accept: 'application/json',
        },
      });
      const optionsResult = await optionsResponse.json();
      if (!optionsResponse.ok) {
        throw new Error(optionsResult.detail || 'Unable to start passkey sign-in.');
      }
      const credential = await navigator.credentials.get({
        publicKey: decodeCredentialOptions(optionsResult.public_key),
      });
      if (!credential) {
        throw new Error('No passkey was selected.');
      }
      const verifyResponse = await fetch('/auth/passkeys/authenticate/verify', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          challenge_id: optionsResult.challenge_id,
          credential: serializeCredential(credential),
        }),
      });
      const verifyResult = await verifyResponse.json();
      if (!verifyResponse.ok) {
        throw new Error(verifyResult.detail || 'Passkey sign-in failed.');
      }
      window.location.assign(verifyResult.redirect || '/');
    } catch (error) {
      if (errorContainer) {
        errorContainer.hidden = false;
        errorContainer.textContent = passkeyErrorMessage(error, error.message || 'Passkey sign-in failed.');
      }
    } finally {
      button.disabled = false;
      button.textContent = defaultLabel;
    }
  });
}

class AuthForm {
  constructor(form) {
    this.form = form;
    this.endpoint = form.dataset.endpoint;
    this.successRedirect = Object.prototype.hasOwnProperty.call(form.dataset, 'successRedirect')
      ? form.dataset.successRedirect
      : '/';
    this.loadingText = form.dataset.loadingText || 'Submitting…';
    this.successMessage = form.dataset.successMessage || '';
    this.successDelay = Number(form.dataset.successDelay || 0);
    this.shouldResetOnSuccess = form.dataset.successReset === 'true';
    this.submitButton = form.querySelector('[data-auth-submit]');
    this.errorContainer = form.querySelector('[data-auth-error]');
    this.successContainer = form.querySelector('[data-auth-success]');
    this.accountSetupResetPrompt = form.querySelector('[data-account-setup-reset]');
    this.accountSetupResetMessage = form.querySelector('[data-account-setup-reset-message]');
    this.accountSetupResetButton = form.querySelector('[data-account-setup-reset-submit]');
    this.accountSetupResetEmail = '';
    this.totpField = form.querySelector('[data-totp-field]');
    this.totpToggle = form.querySelector('[data-auth-toggle-totp]');
    this.defaultButtonLabel = this.submitButton ? this.submitButton.textContent : '';

    form.addEventListener('submit', (event) => this.handleSubmit(event));

    if (this.accountSetupResetButton) {
      this.accountSetupResetButton.addEventListener('click', () => this.sendAccountSetupReset());
    }

    if (this.totpToggle && this.totpField) {
      this.syncTotpVisibility();
      this.totpToggle.addEventListener('click', (event) => this.toggleTotp(event));
    }
  }

  toggleTotp(event) {
    event.preventDefault();
    if (!this.totpField || !this.totpToggle) {
      return;
    }

    const isHidden = this.totpField.hasAttribute('hidden');
    this.applyTotpVisibility(isHidden, { focus: isHidden, clearOnHide: !isHidden });
  }

  async handleSubmit(event) {
    event.preventDefault();
    if (!this.endpoint) {
      this.showError('Authentication endpoint is not configured.');
      return;
    }

    this.showError('');
    this.showSuccess('');
    this.hideAccountSetupReset();

    const payload = this.buildPayload();
    if (!payload) {
      return;
    }

    this.setLoading(true);

    try {
      const response = await fetch(this.endpoint, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
      });

      const result = await this.parseJson(response);

      if (!response.ok) {
        const detail = this.extractDetail(result) || 'Unable to complete the request. Check your credentials and try again.';
        if (result && result.account_setup_reset_available) {
          this.showAccountSetupReset(detail, payload.email);
          return;
        }
        if (detail && /totp/i.test(detail)) {
          const shouldSelect = /invalid/i.test(detail);
          this.revealTotpField({ focus: true, select: shouldSelect });
        }
        this.showError(detail);
        return;
      }

      if (result && result.verification_required) {
        this.showSuccess(this.extractDetail(result) || 'Check your email to verify your account before signing in.');
        this.form.reset();
        return;
      }

      const detail = this.extractDetail(result);
      const message = this.successMessage || detail;
      if (message) {
        this.showSuccess(message);
      }

      if (this.shouldResetOnSuccess) {
        this.form.reset();
      }

      const redirectTarget = (result && result.redirect) || this.successRedirect;
      if (redirectTarget) {
        if (this.successDelay > 0 && message) {
          window.setTimeout(() => window.location.assign(redirectTarget), this.successDelay);
        } else {
          window.location.assign(redirectTarget);
        }
      }
    } catch (error) {
      console.error('Authentication request failed', error);
      this.showError('A network error occurred while contacting the server. Please try again.');
    } finally {
      this.setLoading(false);
    }
  }

  buildPayload() {
    const formData = new FormData(this.form);
    const payload = {};

    for (const [key, value] of formData.entries()) {
      if (typeof value !== 'string') {
        continue;
      }

      if (!value && key !== 'password') {
        continue;
      }

      if (key === 'confirm_password') {
        continue;
      }

      if (key === 'password') {
        payload[key] = value;
        continue;
      }

      const trimmed = value.trim();
      if (!trimmed) {
        continue;
      }

      if (key === 'totp_code') {
        payload[key] = trimmed.replace(/\s+/g, '');
        continue;
      }

      if (key === 'company_id') {
        const numeric = Number(trimmed);
        if (!Number.isNaN(numeric)) {
          payload[key] = numeric;
        }
        continue;
      }

      payload[key] = trimmed;
    }

    const passwordInput = this.form.querySelector('input[name="password"]');
    const confirmPasswordInput = this.form.querySelector('input[name="confirm_password"]');
    if (passwordInput && confirmPasswordInput && passwordInput.value !== confirmPasswordInput.value) {
      this.showError('Passwords do not match.');
      return null;
    }

    const totpInput = this.form.querySelector('input[name="totp_code"]');
    if (totpInput) {
      const raw = totpInput.value;
      if (typeof raw === 'string' && raw.trim()) {
        payload.totp_code = raw.trim().replace(/\s+/g, '');
      }
    }

    return payload;
  }

  async parseJson(response) {
    try {
      return await response.json();
    } catch (error) {
      console.warn('Failed to parse JSON response', error);
      return null;
    }
  }

  extractDetail(result) {
    if (!result) {
      return '';
    }

    if (typeof result.detail === 'string') {
      return result.detail;
    }

    if (Array.isArray(result.detail) && result.detail.length > 0) {
      const first = result.detail[0];
      if (typeof first === 'string') {
        return first;
      }
      if (first && typeof first.msg === 'string') {
        return first.msg;
      }
    }

    if (result.message && typeof result.message === 'string') {
      return result.message;
    }

    return '';
  }

  showError(message) {
    this.showMessage(this.errorContainer, message);
  }

  showSuccess(message) {
    this.showMessage(this.successContainer, message);
  }

  showAccountSetupReset(message, email) {
    this.showError('');
    this.showSuccess('');
    this.accountSetupResetEmail = email || '';

    if (!this.accountSetupResetPrompt || !this.accountSetupResetButton) {
      this.showError(message);
      return;
    }

    if (this.accountSetupResetMessage) {
      this.accountSetupResetMessage.textContent = message;
    }

    this.accountSetupResetPrompt.removeAttribute('hidden');
  }

  hideAccountSetupReset() {
    if (this.accountSetupResetPrompt) {
      this.accountSetupResetPrompt.setAttribute('hidden', '');
    }
    if (this.accountSetupResetMessage) {
      this.accountSetupResetMessage.textContent = '';
    }
    this.accountSetupResetEmail = '';
  }

  async sendAccountSetupReset() {
    if (!this.accountSetupResetEmail) {
      this.showError('Enter your email address and try again.');
      return;
    }

    const defaultLabel = this.accountSetupResetButton ? this.accountSetupResetButton.textContent : '';
    if (this.accountSetupResetButton) {
      this.accountSetupResetButton.disabled = true;
      this.accountSetupResetButton.textContent = 'Sending reset link…';
    }

    try {
      const response = await fetch('/auth/password/forgot', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({ email: this.accountSetupResetEmail }),
      });
      const result = await this.parseJson(response);
      const detail = this.extractDetail(result) || 'If the email is registered, reset instructions have been sent.';

      if (!response.ok) {
        this.showError(detail);
        return;
      }

      this.hideAccountSetupReset();
      this.showSuccess(detail);
    } catch (error) {
      console.error('Password reset request failed', error);
      this.showError('A network error occurred while sending the reset link. Please try again.');
    } finally {
      if (this.accountSetupResetButton) {
        this.accountSetupResetButton.disabled = false;
        this.accountSetupResetButton.textContent = defaultLabel;
      }
    }
  }

  showMessage(container, message) {
    if (!container) {
      return;
    }

    if (!message) {
      container.setAttribute('hidden', '');
      container.textContent = '';
      return;
    }

    container.removeAttribute('hidden');
    container.textContent = message;
  }

  setLoading(isLoading) {
    if (this.submitButton) {
      this.submitButton.disabled = isLoading;
      this.submitButton.textContent = isLoading ? this.loadingText : this.defaultButtonLabel;
    }

    if (isLoading) {
      this.form.classList.add('is-loading');
    } else {
      this.form.classList.remove('is-loading');
    }
  }

  syncTotpVisibility() {
    if (!this.totpField) {
      return;
    }
    const isHidden = this.totpField.hasAttribute('hidden');
    this.totpField.setAttribute('aria-hidden', isHidden ? 'true' : 'false');
    if (this.totpToggle) {
      this.totpToggle.textContent = isHidden ? 'Use authenticator code' : 'Hide authenticator code';
      this.totpToggle.setAttribute('aria-expanded', isHidden ? 'false' : 'true');
    }
  }

  applyTotpVisibility(shouldShow, { focus = false, clearOnHide = false } = {}) {
    if (!this.totpField) {
      return;
    }

    if (shouldShow) {
      this.totpField.removeAttribute('hidden');
      this.syncTotpVisibility();
      if (focus) {
        this.focusTotpInput({ select: false });
      }
      return;
    }

    this.totpField.setAttribute('hidden', '');
    this.syncTotpVisibility();
    if (clearOnHide) {
      this.clearTotpInput();
    }
  }

  revealTotpField({ focus = false, select = false } = {}) {
    if (!this.totpField) {
      return;
    }
    const wasHidden = this.totpField.hasAttribute('hidden');
    if (wasHidden) {
      this.totpField.removeAttribute('hidden');
      this.syncTotpVisibility();
    }
    if (focus || select) {
      this.focusTotpInput({ select });
    }
  }

  focusTotpInput({ select = false } = {}) {
    if (!this.totpField) {
      return;
    }
    const input = this.totpField.querySelector('input');
    if (!input) {
      return;
    }
    window.requestAnimationFrame(() => {
      if (select) {
        input.select();
      } else {
        input.focus();
      }
    });
  }

  clearTotpInput() {
    if (!this.totpField) {
      return;
    }
    const input = this.totpField.querySelector('input');
    if (input) {
      input.value = '';
    }
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAuthForms);
} else {
  initAuthForms();
}
