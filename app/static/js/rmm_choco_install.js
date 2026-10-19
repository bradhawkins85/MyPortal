(function () {
  'use strict';

  // The asset page's "Install Choco Package" modal: pick an app from the
  // downloaded Chocolatey list (or type a package ID) and push the install.
  const modal = document.querySelector('[data-choco-modal]');
  if (!modal) return;
  const form = modal.querySelector('[data-choco-modal-form]');
  const search = modal.querySelector('[data-choco-modal-search]');
  const searchButton = modal.querySelector('[data-choco-modal-search-button]');
  const status = modal.querySelector('[data-choco-modal-status]');
  const results = modal.querySelector('[data-choco-modal-results]');
  const other = modal.querySelector('[data-choco-modal-other]');
  const packageInput = modal.querySelector('[data-choco-modal-package]');
  const error = modal.querySelector('[data-choco-modal-error]');
  let opener = null;
  let loaded = false;
  let sequence = 0;

  function open(trigger) {
    opener = trigger;
    const menu = trigger.closest('details');
    if (menu) menu.open = false;
    error.hidden = true;
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    search.focus();
    if (!loaded) { loaded = true; find(); }
  }

  function close() {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (opener) opener.focus();
  }

  function render(packages) {
    const seen = new Set();
    const list = document.createElement('ul');
    list.className = 'rmm-app-push__list';
    packages.filter((app) => !seen.has(app.package_id) && seen.add(app.package_id)).forEach((app) => {
      const item = document.createElement('li');
      const label = document.createElement('label');
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'choco-modal-app';
      radio.value = app.package_id;
      radio.addEventListener('change', () => { other.value = ''; });
      label.append(radio, ' ' + (app.display_name || app.title) + ' — ' + app.version + ' (' + app.package_id + ')');
      item.append(label);
      list.append(item);
    });
    results.replaceChildren(list);
  }

  async function find() {
    const current = ++sequence;
    searchButton.disabled = true;
    status.textContent = 'Loading Chocolatey applications…';
    try {
      const response = await fetch('/api/rmm/chocolatey/packages?q=' + encodeURIComponent(search.value.trim()), { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const data = await response.json();
      if (current !== sequence) return;
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load the app list.');
      render(data.packages);
      status.textContent = (data.packages.length ? 'Choose an application.' : 'No applications found in the downloaded list.')
        + (data.updated_at ? ' App list updated ' + new Date(data.updated_at).toLocaleString() + '.' : ' The app list has not been downloaded yet.');
    } catch (err) {
      if (current === sequence) status.textContent = err.message;
    } finally {
      if (current === sequence) searchButton.disabled = false;
    }
  }

  document.querySelectorAll('[data-choco-modal-open]').forEach((button) => button.addEventListener('click', () => open(button)));
  modal.querySelectorAll('[data-choco-modal-close]').forEach((button) => button.addEventListener('click', close));
  modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
  searchButton.addEventListener('click', find);
  search.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); find(); } });
  other.addEventListener('input', () => {
    if (other.value.trim()) modal.querySelectorAll('[name="choco-modal-app"]').forEach((radio) => { radio.checked = false; });
  });

  form.addEventListener('submit', (event) => {
    const chosen = modal.querySelector('[name="choco-modal-app"]:checked');
    const value = (other.value.trim() || (chosen ? chosen.value : '')).toLowerCase();
    if (!/^[a-z0-9][a-z0-9._-]{0,127}$/.test(value)) {
      event.preventDefault();
      error.textContent = value ? 'Enter an exact Chocolatey package ID, such as googlechrome.' : 'Choose an application or enter a package ID.';
      error.hidden = false;
      return;
    }
    packageInput.value = value;
  });
})();
