(function () {
  'use strict';

  // Show / hide password buttons.
  function initPasswordToggles() {
    document.querySelectorAll('[data-password-field]').forEach((field) => {
      const input = field.querySelector('input');
      const toggle = field.querySelector('[data-password-toggle]');
      const label = field.querySelector('[data-password-toggle-label]');
      if (!input || !toggle) {
        return;
      }
      toggle.hidden = false;
      field.classList.add('auth-password--enhanced');
      toggle.addEventListener('click', () => {
        const show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        toggle.setAttribute('aria-pressed', show ? 'true' : 'false');
        if (label) {
          label.textContent = show ? 'Hide' : 'Show';
        }
        input.focus();
      });
      // Never submit or leave the page with the password visible.
      const form = input.closest('form');
      if (form) {
        form.addEventListener('submit', () => {
          input.type = 'password';
          toggle.setAttribute('aria-pressed', 'false');
          if (label) {
            label.textContent = 'Show';
          }
        });
      }
    });
  }

  // Live password checklist and confirm-password match on sign-up.
  function initPasswordRules() {
    const rules = document.querySelector('[data-password-rules]');
    if (!rules) {
      return;
    }
    const form = rules.closest('form');
    const password = form.querySelector('input[name="password"]');
    const confirm = form.querySelector('input[name="confirm_password"]');
    const match = form.querySelector('[data-password-match]');
    const checks = {
      length: (value) => value.length >= 12,
      mixed: (value) => /[a-z]/.test(value) && /[A-Z]/.test(value),
      symbol: (value) => /[\d\W_]/.test(value),
    };

    function update() {
      const value = password.value;
      rules.querySelectorAll('[data-rule]').forEach((item) => {
        const passed = Boolean(value) && checks[item.dataset.rule](value);
        item.classList.toggle('is-met', passed);
        item.dataset.state = passed ? 'met' : 'unmet';
      });
      if (confirm && match) {
        if (!confirm.value) {
          match.textContent = '';
          match.dataset.state = '';
        } else if (confirm.value === value) {
          match.textContent = 'Passwords match.';
          match.dataset.state = 'met';
        } else {
          match.textContent = 'Passwords don’t match yet.';
          match.dataset.state = 'unmet';
        }
      }
    }

    password.addEventListener('input', update);
    if (confirm) {
      confirm.addEventListener('input', update);
    }
    form.addEventListener('reset', () => window.setTimeout(update, 0));
    update();
  }

  // Without passkey support the button can't work, so offer only email sign-in.
  function initPasskeySection() {
    const section = document.querySelector('[data-auth-passkey-section]');
    const utils = window.MyPortalPasskeyUtils;
    if (section && utils && typeof utils.supportsPasskeys === 'function' && !utils.supportsPasskeys()) {
      section.hidden = true;
    }
  }

  // Forgot password: swap to a "check your email" view once auth.js reports success.
  function initSentState() {
    const card = document.querySelector('[data-auth-sent-card]');
    if (!card) {
      return;
    }
    const form = card.querySelector('[data-auth-sent-form]');
    const formView = card.querySelector('[data-auth-sent-hide]');
    const sentView = card.querySelector('[data-auth-sent]');
    const success = form && form.querySelector('[data-auth-success]');
    const emailInput = form && form.querySelector('input[name="email"]');
    const emailOut = card.querySelector('[data-auth-sent-email]');
    const retry = card.querySelector('[data-auth-sent-retry]');
    if (!form || !formView || !sentView || !success) {
      return;
    }
    let submittedEmail = '';
    form.addEventListener('submit', () => {
      submittedEmail = emailInput ? emailInput.value.trim() : '';
    });
    new MutationObserver(() => {
      if (success.hidden || !success.textContent.trim()) {
        return;
      }
      if (emailOut) {
        emailOut.textContent = submittedEmail || 'that address';
      }
      formView.hidden = true;
      sentView.hidden = false;
      sentView.focus();
    }).observe(success, { attributes: true, attributeFilter: ['hidden'], childList: true, characterData: true, subtree: true });
    if (retry) {
      retry.addEventListener('click', () => {
        success.hidden = true;
        success.textContent = '';
        sentView.hidden = true;
        formView.hidden = false;
        if (emailInput) {
          emailInput.focus();
        }
      });
    }
  }

  function init() {
    initPasskeySection();
    initSentState();
    initPasswordToggles();
    initPasswordRules();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
