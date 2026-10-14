(function () {
  'use strict';

  // Filter apps by their current status and the technician's search.

  function setUp(input) {
    const table = document.getElementById(input.getAttribute('data-rmm-software-filter'));
    if (!table) {
      return;
    }
    const rows = Array.from(table.querySelectorAll('[data-rmm-software-row]'));
    const empty = table.closest('.ce-section__body').querySelector('[data-rmm-software-empty]');
    const showUninstalled = table.closest('.ce-section__body').querySelector('[data-rmm-show-uninstalled]');
    function applyFilter() {
      const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      rows.forEach((row) => {
        const text = row.textContent.toLowerCase();
        const match = (row.dataset.installed !== 'false' || (showUninstalled && showUninstalled.checked))
          && words.every((word) => text.includes(word));
        row.hidden = !match;
        shown += match ? 1 : 0;
      });
      if (empty) {
        empty.hidden = shown > 0;
      }
    }
    input.addEventListener('input', applyFilter);
    if (showUninstalled) {
      showUninstalled.addEventListener('change', applyFilter);
    }
    applyFilter();
  }

  document.querySelectorAll('[data-rmm-software-filter]').forEach(setUp);
})();
