(function () {
  'use strict';

  const modal = document.getElementById('cof-link-modal');
  let lastFocus = null;

  function openModal() {
    if (!modal) {
      return;
    }
    lastFocus = document.activeElement;
    modal.hidden = false;
    const first = modal.querySelector('input:not([type="hidden"])');
    window.requestAnimationFrame(() => first && first.focus());
  }

  document.querySelectorAll('[data-cof-link-modal-open]').forEach((trigger) => {
    trigger.addEventListener('click', openModal);
  });

  if (modal) {
    // main.js closes the modal (Escape, backdrop, [data-modal-close]); return focus afterwards.
    new MutationObserver(() => {
      if (modal.hidden && lastFocus && document.contains(lastFocus)) {
        lastFocus.focus();
        lastFocus = null;
      }
    }).observe(modal, { attributes: true, attributeFilter: ['hidden'] });

    const form = modal.querySelector('[data-cof-link-form]');
    const email = modal.querySelector('[data-cof-link-email]');
    const emailError = modal.querySelector('[data-cof-link-email-error]');
    const send = modal.querySelector('[data-cof-link-send]');
    const sendHelp = modal.querySelector('[data-cof-link-send-help]');
    const submit = modal.querySelector('[data-cof-link-submit]');

    const syncSend = () => {
      const hasEmail = Boolean(email.value.trim());
      send.disabled = !hasEmail;
      sendHelp.textContent = hasEmail
        ? (send.checked ? "We'll send the link as soon as you create it. You'll also be able to copy it." : "You'll copy the link and send it yourself.")
        : 'Add the client email to send the link automatically, or copy it after you create it.';
      submit.textContent = hasEmail && send.checked ? 'Create and email link' : 'Create link';
    };
    const validEmail = (value) => {
      const text = value.trim();
      const at = text.indexOf('@');
      if (!text) {
        return true;
      }
      if (/\s/.test(text) || at < 1 || text.indexOf('@', at + 1) !== -1) {
        return false;
      }
      const labels = text.slice(at + 1).split('.');
      return labels.length >= 2 && labels.every(Boolean);
    };

    email.addEventListener('input', () => {
      emailError.hidden = true;
      email.classList.remove('is-invalid');
      syncSend();
    });
    send.addEventListener('change', syncSend);
    form.addEventListener('submit', (event) => {
      if (!validEmail(email.value)) {
        event.preventDefault();
        emailError.hidden = false;
        email.classList.add('is-invalid');
        email.setAttribute('aria-invalid', 'true');
        email.focus();
        return;
      }
      submit.disabled = true;
      submit.classList.add('button--processing');
    });
    syncSend();
  }

  const copyButton = document.querySelector('[data-cof-copy]');
  const copySource = document.querySelector('[data-cof-copy-source]');
  const copyStatus = document.querySelector('[data-cof-copy-status]');
  if (copyButton && copySource) {
    copySource.addEventListener('focus', () => copySource.select());
    copyButton.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(copySource.value);
        copyStatus.textContent = 'Link copied.';
      } catch (error) {
        copySource.select();
        copyStatus.textContent = 'Press Ctrl+C (or Cmd+C) to copy the selected link.';
      }
    });
  }
})();
