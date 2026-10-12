(function () {
  'use strict';

  // The asset page's Hardware & software section (rmm/_asset_inventory.html):
  // narrow the installed software table as the technician types.

  function setUp(input) {
    const table = document.getElementById(input.getAttribute('data-rmm-software-filter'));
    if (!table) {
      return;
    }
    const rows = Array.from(table.querySelectorAll('[data-rmm-software-row]'));
    const empty = table.closest('.ce-section__body').querySelector('[data-rmm-software-empty]');
    input.addEventListener('input', () => {
      const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      rows.forEach((row) => {
        const text = row.textContent.toLowerCase();
        const match = words.every((word) => text.includes(word));
        row.hidden = !match;
        shown += match ? 1 : 0;
      });
      if (empty) {
        empty.hidden = shown > 0;
      }
    });
  }

  document.querySelectorAll('[data-rmm-software-filter]').forEach(setUp);
})();
