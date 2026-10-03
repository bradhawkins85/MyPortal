// Opens the account anonymisation modals (approve, reject, anonymise user).
// Closing is handled by the shared modal handler in main.js.
(function () {
  'use strict';

  function openModal(modal, trigger) {
    const form = modal.querySelector('form');
    if (form) {
      form.reset();
      const action = trigger.getAttribute('data-anonymise-action');
      if (action) form.setAttribute('action', action);
    }
    const label = trigger.getAttribute('data-anonymise-label');
    modal.querySelectorAll('[data-anonymise-target]').forEach((node) => {
      node.textContent = label || '';
    });
    modal.hidden = false;
    window.requestAnimationFrame(() => {
      const focusTarget = modal.querySelector('.modal__body textarea, .modal__body input:not([type="hidden"])')
        || modal.querySelector('.modal__close');
      if (focusTarget) focusTarget.focus();
    });
  }

  function bind() {
    document.querySelectorAll('[data-anonymise-open]').forEach((trigger) => {
      const modal = document.getElementById(trigger.getAttribute('data-anonymise-open'));
      if (!modal) return;
      trigger.addEventListener('click', () => openModal(modal, trigger));
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
