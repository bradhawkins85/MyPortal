(() => {
  const modal = document.getElementById('dns-history-modal');
  const opener = document.getElementById('dns-history-open');
  if (!modal || !opener) return;
  const close = () => { modal.hidden = true; document.body.classList.remove('modal-open'); opener.focus(); };
  opener.addEventListener('click', () => { modal.hidden = false; document.body.classList.add('modal-open'); modal.querySelector('[data-dns-history-close]').focus(); });
  modal.querySelectorAll('[data-dns-history-close]').forEach((button) => button.addEventListener('click', close));
  modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
  modal.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') close();
    if (event.key !== 'Tab') return;
    const focusable = [...modal.querySelectorAll('a[href], button:not([disabled])')];
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
})();
