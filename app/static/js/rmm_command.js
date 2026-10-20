(function () {
  'use strict';

  // The asset page's "Execute command" modal: type a one-off command, choose
  // the shell and who it runs as, then watch it in the shared result viewer.
  const modal = document.querySelector('[data-rmm-command-modal]');
  if (!modal) return;
  const form = modal.querySelector('[data-rmm-command-form]');
  const language = modal.querySelector('[data-rmm-command-language]');
  const text = modal.querySelector('[data-rmm-command-text]');
  const runAs = modal.querySelector('[data-rmm-command-run-as]');
  const timeout = modal.querySelector('[data-rmm-command-timeout]');
  const submit = modal.querySelector('[data-rmm-command-submit]');
  const assetId = modal.getAttribute('data-rmm-command-asset');
  let opener = null;

  function clearErrors() {
    modal.querySelectorAll('[data-rmm-command-error]').forEach((node) => { node.hidden = true; });
  }

  function showError(key, message) {
    const target = modal.querySelector('[data-rmm-command-error="' + key + '"]') || modal.querySelector('[data-rmm-command-error="form"]');
    target.textContent = message;
    target.hidden = false;
  }

  function open(trigger) {
    opener = trigger;
    const menu = trigger.closest('details');
    if (menu) menu.open = false;
    clearErrors();
    submit.disabled = false;
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    text.focus();
  }

  function close() {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (opener) opener.focus();
  }

  document.querySelectorAll('[data-rmm-command-open]').forEach((button) => button.addEventListener('click', () => open(button)));
  modal.querySelectorAll('[data-rmm-command-close]').forEach((button) => button.addEventListener('click', close));
  modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    clearErrors();
    if (!text.value.trim()) {
      showError('command', 'Enter a command.');
      text.focus();
      return;
    }
    const rmm = window.MyPortalRmm;
    submit.disabled = true;
    try {
      const result = await rmm.api('/api/rmm/assets/' + encodeURIComponent(assetId) + '/commands', {
        method: 'POST',
        body: {
          language: language.value,
          command: text.value,
          run_as: runAs.value,
          timeout_seconds: Math.round((Number(timeout.value) || 5) * 60),
        },
      });
      text.value = '';
      close();
      rmm.openResult(result.run_id);
    } catch (error) {
      const errors = (error.data && error.data.errors) || {};
      if (Object.keys(errors).length) {
        Object.entries(errors).forEach(([key, message]) => showError(key, message));
      } else {
        showError('form', error.message);
      }
    } finally {
      submit.disabled = false;
    }
  });
})();
