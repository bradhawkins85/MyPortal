(() => {
  const table = document.getElementById('kb-admin-table');
  if (!table) return;
  const statusInput = document.querySelector('[data-kb-status-input]');
  const chips = Array.from(document.querySelectorAll('[data-kb-status-filter]'));

  function syncChips() {
    const value = statusInput ? statusInput.value : '';
    chips.forEach((chip) => {
      const active = chip.dataset.kbStatusFilter === value;
      chip.classList.toggle('is-active', active);
      chip.setAttribute('aria-pressed', String(active));
    });
  }

  chips.forEach((chip) => {
    chip.addEventListener('click', () => {
      if (!statusInput) return;
      statusInput.value = chip.dataset.kbStatusFilter;
      // tables.js listens for change on column filter inputs.
      statusInput.dispatchEvent(new Event('change'));
      syncChips();
    });
  });

  // tables.js may restore a persisted status filter after load.
  window.addEventListener('load', syncChips);

  table.addEventListener('click', (event) => {
    if (event.target.closest('a, button, input, label')) return;
    const row = event.target.closest('tr[data-kb-href]');
    if (!row) return;
    // A fixed path from the slug: navigating to an attribute's value would run a javascript: URL.
    const slug = row.dataset.kbArticleSlug;
    if (!slug) return;
    const href = '/admin/knowledge-base/articles/' + encodeURIComponent(slug);
    if (event.ctrlKey || event.metaKey) {
      window.open(href, '_blank', 'noopener');
    } else {
      window.location.assign(href);
    }
  });
})();
