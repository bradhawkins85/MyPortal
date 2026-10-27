(function () {
  'use strict';

  // "Uninstall agents" on the asset page and each Assets list row: choose an
  // optional offboarding script from the device's scripts, then queue it (or the
  // removal itself) and watch it in the shared result viewer.
  const modal = document.querySelector('[data-asset-uninstall-modal]');
  if (!modal) return;
  const form = modal.querySelector('[data-asset-uninstall-form]');
  const select = modal.querySelector('[data-asset-uninstall-script]');
  const confirmBox = modal.querySelector('[data-asset-uninstall-confirm]');
  const submit = modal.querySelector('[data-asset-uninstall-submit]');
  const device = modal.querySelector('[data-asset-uninstall-device]');
  let assetId = null;
  let opener = null;

  function clearErrors() {
    modal.querySelectorAll('[data-asset-uninstall-error]').forEach((node) => { node.hidden = true; });
  }

  function showError(key, message) {
    const target = modal.querySelector('[data-asset-uninstall-error="' + key + '"]') || modal.querySelector('[data-asset-uninstall-error="form"]');
    target.textContent = message;
    target.hidden = false;
  }

  async function loadScripts() {
    select.replaceChildren(new Option('None – uninstall straight away', ''));
    select.disabled = true;
    try {
      const data = await window.MyPortalRmm.api('/api/rmm/scripts?asset_id=' + encodeURIComponent(assetId));
      (data.scripts || []).forEach((script) => {
        select.add(new Option(script.folder ? script.folder + ' / ' + script.name : script.name, String(script.id)));
      });
    } catch (error) {
      showError('offboarding_script_id', 'Could not list the device\'s scripts: ' + error.message);
    } finally {
      select.disabled = false;
    }
  }

  function open(trigger) {
    opener = trigger;
    assetId = trigger.getAttribute('data-asset-uninstall');
    if (device) device.textContent = trigger.getAttribute('data-asset-uninstall-name') || 'this device';
    const menu = trigger.closest('details');
    if (menu) menu.open = false;
    clearErrors();
    confirmBox.checked = false;
    submit.disabled = true;
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    select.focus();
    loadScripts();
  }

  function close() {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (opener) opener.focus();
  }

  document.querySelectorAll('[data-asset-uninstall]').forEach((button) => button.addEventListener('click', () => open(button)));
  modal.querySelectorAll('[data-asset-uninstall-close]').forEach((button) => button.addEventListener('click', close));
  modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
  confirmBox.addEventListener('change', () => { submit.disabled = !confirmBox.checked; });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    clearErrors();
    if (!confirmBox.checked || !assetId) return;
    const rmm = window.MyPortalRmm;
    submit.disabled = true;
    try {
      const result = await rmm.api('/api/rmm/assets/' + encodeURIComponent(assetId) + '/uninstall', {
        method: 'POST',
        body: { offboarding_script_id: select.value ? Number(select.value) : null },
      });
      close();
      rmm.openResult(result.run_id);
    } catch (error) {
      const errors = (error.data && error.data.errors) || {};
      if (Object.keys(errors).length) {
        Object.entries(errors).forEach(([key, message]) => showError(key, message));
      } else {
        showError('form', error.message);
      }
      submit.disabled = !confirmBox.checked;
    }
  });
})();
