(function () {
  'use strict';

  // The asset page's "Install Choco Package" modal: pick an app from the
  // downloaded Chocolatey list (or type a package ID) and push the chosen action.
  const modal = document.querySelector('[data-choco-modal]');
  if (!modal) return;
  const form = modal.querySelector('[data-choco-modal-form]');
  const other = modal.querySelector('[data-choco-modal-other]');
  const packageInput = modal.querySelector('[data-choco-modal-package]');
  const error = modal.querySelector('[data-choco-modal-error]');
  const picker = window.MyPortalChocoPicker.create({
    search: modal.querySelector('[data-choco-modal-search]'),
    button: modal.querySelector('[data-choco-modal-search-button]'),
    status: modal.querySelector('[data-choco-modal-status]'),
    results: modal.querySelector('[data-choco-modal-results]'),
    name: 'choco-modal-app',
    onChange: () => { other.value = ''; },
  });
  const device = modal.querySelector('[data-choco-modal-device]');
  let opener = null;

  // The Assets list shares one modal between its rows: point it at the row's device.
  function useDevice(trigger) {
    if (!trigger.hasAttribute('data-choco-modal-asset')) return;
    form.action = '/rmm/assets/' + encodeURIComponent(trigger.getAttribute('data-choco-modal-asset')) + '/software/install';
    if (device) device.textContent = trigger.getAttribute('data-choco-modal-name') || 'this device';
  }

  function open(trigger) {
    opener = trigger;
    useDevice(trigger);
    const menu = trigger.closest('details');
    if (menu) menu.open = false;
    error.hidden = true;
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    modal.querySelector('[data-choco-modal-search]').focus();
    picker.ensureLoaded();
  }

  function close() {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (opener) opener.focus();
  }

  document.querySelectorAll('[data-choco-modal-open]').forEach((button) => button.addEventListener('click', () => open(button)));
  modal.querySelectorAll('[data-choco-modal-close]').forEach((button) => button.addEventListener('click', close));
  modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
  other.addEventListener('input', () => { if (other.value.trim()) picker.clear(); });

  form.addEventListener('submit', (event) => {
    const value = (other.value.trim() || picker.selected()[0] || '').toLowerCase();
    if (!/^[a-z0-9][a-z0-9._-]{0,127}$/.test(value)) {
      event.preventDefault();
      error.textContent = value ? 'Enter an exact Chocolatey package ID, such as googlechrome.' : 'Choose an application or enter a package ID.';
      error.hidden = false;
      return;
    }
    packageInput.value = value;
  });
})();
