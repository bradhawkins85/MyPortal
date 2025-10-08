const initAuthForms = () => {
  const forms = document.querySelectorAll('[data-auth-form]');
  forms.forEach((form) => new AuthForm(form));
};

class AuthForm {
  constructor(form) {
    this.form = form;
    this.endpoint = form.dataset.endpoint;
    this.successRedirect = form.dataset.successRedirect || '/';
    this.loadingText = form.dataset.loadingText || 'Submitting…';
    this.submitButton = form.querySelector('[data-auth-submit]');
    this.errorContainer = form.querySelector('[data-auth-error]');
    this.totpField = form.querySelector('[data-totp-field]');
    this.totpToggle = form.querySelector('[data-auth-toggle-totp]');
    this.defaultButtonLabel = this.submitButton ? this.submitButton.textContent : '';

    form.addEventListener('submit', (event) => this.handleSubmit(event));

    if (this.totpToggle && this.totpField) {
      this.totpToggle.setAttribute('aria-expanded', this.totpField.hasAttribute('hidden') ? 'false' : 'true');
      this.totpToggle.addEventListener('click', (event) => this.toggleTotp(event));
    }
  }

  toggleTotp(event) {
    event.preventDefault();
    if (!this.totpField || !this.totpToggle) {
      return;
    }

    const isHidden = this.totpField.hasAttribute('hidden');
    if (isHidden) {
      this.totpField.removeAttribute('hidden');
      this.totpField.setAttribute('aria-hidden', 'false');
      this.totpToggle.textContent = 'Hide authenticator code';
      this.totpToggle.setAttribute('aria-expanded', 'true');
      const input = this.totpField.querySelector('input');
      if (input) {
        window.requestAnimationFrame(() => input.focus());
      }
    } else {
      this.totpField.setAttribute('hidden', '');
      this.totpField.setAttribute('aria-hidden', 'true');
      this.totpToggle.textContent = 'Use authenticator code';
      this.totpToggle.setAttribute('aria-expanded', 'false');
      const input = this.totpField.querySelector('input');
      if (input) {
        input.value = '';
      }
    }
  }

  async handleSubmit(event) {
    event.preventDefault();
    if (!this.endpoint) {
      this.showError('Authentication endpoint is not configured.');
      return;
    }

    this.showError('');

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
        this.showError(detail);
        return;
      }

      window.location.assign(this.successRedirect);
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

      if (key === 'password') {
        payload[key] = value;
        continue;
      }

      const trimmed = value.trim();
      if (!trimmed) {
        continue;
      }

      if (key === 'totp_code') {
        if (this.totpField && this.totpField.hasAttribute('hidden')) {
          continue;
        }
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
    if (!this.errorContainer) {
      return;
    }

    if (!message) {
      this.errorContainer.setAttribute('hidden', '');
      this.errorContainer.textContent = '';
      return;
    }

    this.errorContainer.removeAttribute('hidden');
    this.errorContainer.textContent = message;
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
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAuthForms);
} else {
  initAuthForms();
}
