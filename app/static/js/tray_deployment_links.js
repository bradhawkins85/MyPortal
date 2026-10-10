(function () {
  'use strict';

  document.querySelectorAll('[data-copy-target]').forEach((button) => {
    button.addEventListener('click', async () => {
      const input = document.getElementById(button.getAttribute('data-copy-target'));
      if (!input) return;
      const label = button.textContent;
      try {
        await navigator.clipboard.writeText(input.value);
        button.textContent = 'Copied';
      } catch (error) {
        input.select();
        button.textContent = 'Press Ctrl+C';
      }
      window.setTimeout(() => {
        button.textContent = label;
      }, 2000);
    });
  });

  const search = document.querySelector('[data-deploy-search]');
  const rows = Array.from(document.querySelectorAll('[data-deploy-row]'));
  const noResults = document.querySelector('[data-deploy-no-results]');
  if (search) {
    search.addEventListener('input', () => {
      const query = search.value.trim().toLowerCase();
      let visible = 0;
      rows.forEach((row) => {
        const match = !query || row.textContent.toLowerCase().includes(query);
        row.hidden = !match;
        if (match) visible += 1;
      });
      if (noResults) noResults.hidden = visible > 0 || rows.length === 0;
    });
  }
})();
