(function () {
  'use strict';

  // The asset page's Printers section: each printer's Actions menu opens the
  // Rename or Remove modal (rmm/_asset_printers.html), which posts a normal
  // form to /rmm/assets/{id}/printers/{printer_id}/rename or /remove.
  const renameModal = document.querySelector('[data-printer-rename-modal]');
  const removeModal = document.querySelector('[data-printer-remove-modal]');
  if (!renameModal || !removeModal) return;

  const renameForm = renameModal.querySelector('[data-printer-rename-form]');
  const nameInput = renameModal.querySelector('[data-printer-new-name]');
  const renameError = renameModal.querySelector('[data-printer-rename-error]');
  const preview = renameModal.querySelector('[data-printer-rename-preview]');
  const removeForm = removeModal.querySelector('[data-printer-remove-form]');
  const removeSubmit = removeModal.querySelector('[data-printer-remove-submit]');
  const usersNote = removeModal.querySelector('[data-printer-remove-users]');
  let opener = null;
  let currentName = '';

  function show(modal, trigger) {
    opener = trigger;
    const menu = trigger.closest('[data-header-menu]');
    const panel = menu && menu.querySelector('[data-header-menu-panel]');
    const toggle = menu && menu.querySelector('[data-header-menu-toggle]');
    if (panel) panel.hidden = true;
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
  }

  function hide(modal) {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (opener && document.body.contains(opener)) {
      const toggle = opener.closest('[data-header-menu]');
      const button = toggle && toggle.querySelector('[data-header-menu-toggle]');
      (button || opener).focus();
    }
  }

  // Only numeric IDs from the row are used to build the form's URL.
  function actionUrl(button, action) {
    const assetId = button.getAttribute('data-asset-id') || '';
    const printerId = button.getAttribute('data-printer-id') || '';
    if (!/^\d+$/.test(assetId) || !/^\d+$/.test(printerId)) return null;
    return '/rmm/assets/' + Number(assetId) + '/printers/' + Number(printerId) + '/' + action;
  }

  function setCurrent(modal, name) {
    modal.querySelectorAll('[data-printer-current]').forEach((node) => { node.textContent = name; });
  }

  // Mirrors rmm_printers.clean_new_name so mistakes show before posting.
  function problem(value) {
    const name = value.trim().replace(/\s+/g, ' ');
    if (!name) return 'Enter the printer’s new name.';
    if (name.length > 220) return 'Printer names can be at most 220 characters.';
    if (/[\\,]/.test(name) || /[\u0000-\u001f]/.test(name)) return 'Printer names cannot contain backslashes or commas.';
    if (name === currentName) return 'That is already the printer’s name.';
    return '';
  }

  function updatePreview() {
    const name = nameInput.value.trim().replace(/\s+/g, ' ');
    preview.textContent = name && name !== currentName ? '“' + currentName + '” will become “' + name + '”.' : '';
    if (!renameError.hidden) {
      const message = problem(nameInput.value);
      renameError.textContent = message;
      renameError.hidden = !message;
    }
  }

  document.querySelectorAll('[data-printer-rename]').forEach((button) => {
    button.addEventListener('click', () => {
      const url = actionUrl(button, 'rename');
      if (!url) return;
      currentName = button.getAttribute('data-printer-name') || '';
      renameForm.action = url;
      setCurrent(renameModal, currentName);
      nameInput.value = currentName;
      renameError.hidden = true;
      nameInput.removeAttribute('aria-invalid');
      preview.textContent = '';
      show(renameModal, button);
      nameInput.focus();
      nameInput.select();
    });
  });

  document.querySelectorAll('[data-printer-remove]').forEach((button) => {
    button.addEventListener('click', () => {
      const url = actionUrl(button, 'remove');
      if (!url) return;
      const kind = button.getAttribute('data-printer-kind') || 'device';
      const users = button.getAttribute('data-printer-users');
      removeForm.action = url;
      setCurrent(removeModal, button.getAttribute('data-printer-name') || '');
      removeModal.querySelectorAll('[data-printer-remove-kind]').forEach((node) => {
        node.hidden = node.getAttribute('data-printer-remove-kind') !== kind;
      });
      if (usersNote) usersNote.textContent = users ? ' (' + users + ')' : '';
      removeSubmit.textContent = kind === 'forget' ? 'Remove from list' : 'Remove printer';
      show(removeModal, button);
      removeSubmit.focus();
    });
  });

  nameInput.addEventListener('input', updatePreview);
  renameForm.addEventListener('submit', (event) => {
    const message = problem(nameInput.value);
    if (message) {
      event.preventDefault();
      renameError.textContent = message;
      renameError.hidden = false;
      nameInput.setAttribute('aria-invalid', 'true');
      nameInput.focus();
    }
  });

  [renameModal, removeModal].forEach((modal) => {
    modal.querySelectorAll('[data-printer-modal-close]').forEach((button) => {
      button.addEventListener('click', () => hide(modal));
    });
    modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') hide(modal); });
    modal.addEventListener('click', (event) => { if (event.target === modal) hide(modal); });
  });
})();
