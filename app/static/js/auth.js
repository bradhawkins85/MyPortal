const initAuthForms = () => {
  const forms = document.querySelectorAll('[data-auth-form]');
  forms.forEach((form) => new AuthForm(form));
};

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
    this.totpField = form.querySelector('[data-totp-field]');
    this.totpToggle = form.querySelector('[data-auth-toggle-totp]');
    this.defaultButtonLabel = this.submitButton ? this.submitButton.textContent : '';

    form.addEventListener('submit', (event) => this.handleSubmit(event));

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
        if (detail && /totp/i.test(detail)) {
          const shouldSelect = /invalid/i.test(detail);
          this.revealTotpField({ focus: true, select: shouldSelect });
        }
        this.showError(detail);
        return;
      }

      if (result && result.verification_required) {
        this.showError(this.extractDetail(result) || 'Check your email to verify your account before signing in.');
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
