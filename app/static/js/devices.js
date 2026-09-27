(() => {
  const modal = document.getElementById('device-types-modal');
  const openButton = document.querySelector('[data-device-types-open]');
  if (!modal || !openButton) return;

  const closeButtons = modal.querySelectorAll('[data-device-types-close]');

  function openModal() {
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    modal.querySelector('input, button')?.focus();
  }

  function closeModal() {
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    openButton.focus();
  }

  openButton.addEventListener('click', openModal);
  closeButtons.forEach((button) => button.addEventListener('click', closeModal));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) closeModal();
  });
})();

(() => {
  const modal = document.getElementById('device-ipam-modal');
  if (!modal) return;
  const csrf = modal.querySelector('input[name="_csrf"]')?.value || '';
  const checkboxes = Array.from(document.querySelectorAll('[data-device-select]'));
  const bulkButton = document.querySelector('[data-ipam-bulk-open]');
  const wanToggle = modal.querySelector('[data-ipam-use-wan]');
  let selectedIds = [];
  let trigger = null;

  const refreshSelection = () => {
    const count = checkboxes.filter((item) => item.checked).length;
    if (bulkButton) bulkButton.disabled = count === 0;
    const label = document.querySelector('[data-ipam-selected-count]');
    if (label) label.textContent = `(${count})`;
  };
  checkboxes.forEach((item) => item.addEventListener('change', refreshSelection));
  document.querySelector('[data-device-select-all]')?.addEventListener('change', () =>
    window.setTimeout(refreshSelection));

  const renderPreview = async () => {
    const body = modal.querySelector('[data-ipam-preview]');
    const submit = modal.querySelector('[data-ipam-import-submit]');
    body.innerHTML = '<tr><td colspan="5">Loading preview…</td></tr>';
    submit.disabled = true;
    const data = new FormData();
    selectedIds.forEach((id) => data.append('device_ids', id));
    if (wanToggle.checked) data.append('use_wan', '1');
    data.append('_csrf', csrf);
    try {
      const response = await fetch('/devices/ipam-preview', { method: 'POST', body: data });
      if (!response.ok) throw new Error('Preview request failed');
      const payload = await response.json();
      body.replaceChildren(...payload.items.map((item) => {
        const row = document.createElement('tr');
        const values = [item.device, item.candidate || '—', item.network || '—'];
        values.forEach((value) => { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); });
        const links = document.createElement('td');
        if (item.asset_url) { const asset = document.createElement('a'); asset.href = item.asset_url; asset.textContent = item.asset; links.append(asset, document.createTextNode(' · ')); }
        const source = document.createElement('a'); source.href = item.source_url; source.textContent = 'Discovery source'; links.append(source); row.append(links);
        const review = document.createElement('td'); review.textContent = item.reason || 'Ready';
        if (item.reason) review.className = 'text-danger';
        row.append(review); return row;
      }));
      submit.disabled = !payload.items.some((item) => !item.reason);
    } catch (_error) {
      body.innerHTML = '<tr><td colspan="5">Preview could not be loaded. Nothing has been changed.</td></tr>';
    }
  };
  const open = (ids, button) => {
    selectedIds = ids; trigger = button; wanToggle.checked = false;
    const holder = modal.querySelector('[data-ipam-device-ids]');
    holder.replaceChildren(...ids.map((id) => { const input = document.createElement('input'); input.type = 'hidden'; input.name = 'device_ids'; input.value = id; return input; }));
    modal.hidden = false; modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open'); wanToggle.focus(); renderPreview();
  };
  const close = () => { modal.hidden = true; modal.setAttribute('aria-hidden', 'true'); document.body.classList.remove('modal-open'); trigger?.focus(); };
  bulkButton?.addEventListener('click', () => open(checkboxes.filter((item) => item.checked).map((item) => item.value), bulkButton));
  document.querySelectorAll('[data-ipam-device-open]').forEach((button) => button.addEventListener('click', () => open([button.dataset.ipamDeviceOpen], button)));
  modal.querySelectorAll('[data-ipam-device-close]').forEach((button) => button.addEventListener('click', close));
  wanToggle.addEventListener('change', renderPreview);
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modal.hidden) close(); });
  refreshSelection();
})();

(() => {
  const modal = document.getElementById('device-bulk-modal');
  const openButton = document.querySelector('[data-device-bulk-open]');
  const selectAll = document.querySelector('[data-device-select-all]');
  const checkboxes = Array.from(document.querySelectorAll('[data-device-select]'));
  if (!modal || !openButton || !selectAll || !checkboxes.length) return;

  const selected = () => checkboxes.filter((checkbox) => checkbox.checked);
  const refresh = () => {
    const count = selected().length;
    openButton.disabled = count === 0;
    document.querySelector('[data-device-selected-count]').textContent = `(${count})`;
    selectAll.checked = count === checkboxes.length;
    selectAll.indeterminate = count > 0 && count < checkboxes.length;
  };
  selectAll.addEventListener('change', () => {
    checkboxes.filter((checkbox) => !checkbox.closest('tr').hidden)
      .forEach((checkbox) => { checkbox.checked = selectAll.checked; });
    refresh();
  });
  checkboxes.forEach((checkbox) => checkbox.addEventListener('change', refresh));

  const close = () => {
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    openButton.focus();
  };
  openButton.addEventListener('click', () => {
    const chosen = selected();
    if (!chosen.length) return;
    modal.querySelector('[data-device-bulk-count]').textContent = chosen.length;
    const container = modal.querySelector('[data-device-bulk-ids]');
    container.replaceChildren(...chosen.map((checkbox) => {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = 'device_ids'; input.value = checkbox.value;
      return input;
    }));
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    modal.querySelector('[data-device-bulk-action]').focus();
  });
  modal.querySelectorAll('[data-device-bulk-close]').forEach((button) => button.addEventListener('click', close));
  modal.querySelector('[data-device-bulk-action]').addEventListener('change', (event) => {
    modal.querySelectorAll('[data-device-bulk-field]').forEach((field) => {
      field.hidden = field.dataset.deviceBulkField !== event.target.value;
    });
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) close();
  });
  refresh();
})();
