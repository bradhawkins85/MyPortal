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
