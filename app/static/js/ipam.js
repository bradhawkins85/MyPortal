(() => {
  let trigger = null;
  const close = (modal) => {
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    trigger?.focus();
  };
  document.querySelectorAll('[data-ipam-modal-open]').forEach((button) => {
    button.addEventListener('click', () => {
      const modal = document.getElementById(button.dataset.ipamModalOpen);
      if (!modal) return;
      trigger = button;
      modal.hidden = false;
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
      modal.querySelector('input, select, textarea, button')?.focus();
    });
  });
  document.querySelectorAll('#network-modal, #address-modal').forEach((modal) => {
    modal.querySelectorAll('[data-ipam-modal-close]').forEach((button) =>
      button.addEventListener('click', () => close(modal)));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const modal = Array.from(document.querySelectorAll('#network-modal, #address-modal'))
      .find((item) => !item.hidden);
    if (modal) close(modal);
  });
})();
