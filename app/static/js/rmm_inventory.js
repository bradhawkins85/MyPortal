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

  // Check only visible rows, with bounded concurrency and a retry on feed errors.
  const queue = [];
  let running = 0;
  function drain() {
    while (running < 4 && queue.length) {
      const form = queue.shift();
      running += 1;
      checkUpgrade(form).finally(() => { running -= 1; drain(); });
    }
  }
  async function checkUpgrade(form) {
    const row = form.closest('[data-rmm-software-row]');
    const status = row.querySelector('[data-choco-version]');
    const button = form.querySelector('[data-choco-upgrade-button]');
    const retry = form.querySelector('[data-choco-retry]');
    retry.hidden = true;
    status.textContent = 'Checking…';
    try {
      const response = await fetch('/api/rmm/assets/' + form.dataset.assetId + '/software/upgrade?name=' + encodeURIComponent(form.dataset.name), { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('Version check unavailable');
      const data = await response.json();
      const app = data.application;
      status.textContent = app ? app.version : 'No unique package match';
      button.hidden = !(app && app.can_upgrade && data.can_run);
      if (app && !app.can_upgrade) status.textContent += ' · No upgrade available';
      button.setAttribute('aria-label', 'Upgrade ' + form.dataset.name + (app ? ' to ' + app.version : ''));
    } catch (error) {
      status.textContent = 'Version check unavailable';
      retry.hidden = false;
    }
  }
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        observer.unobserve(entry.target);
        queue.push(entry.target.querySelector('[data-choco-upgrade]'));
        drain();
      }
    });
  });
  document.querySelectorAll('[data-choco-upgrade]').forEach((form) => {
    observer.observe(form.closest('[data-rmm-software-row]'));
    form.querySelector('[data-choco-retry]').addEventListener('click', () => { queue.push(form); drain(); });
  });

  // Suggest packages from the downloaded Chocolatey app list while typing.
  document.querySelectorAll('[data-choco-install]').forEach((form) => {
    const input = form.querySelector('[data-choco-install-input]');
    const options = form.querySelector('[data-choco-install-options]');
    let timer = null;
    let sequence = 0;
    async function suggest() {
      const current = ++sequence;
      try {
        const response = await fetch('/api/rmm/chocolatey/packages?q=' + encodeURIComponent(input.value.trim()), { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        if (!response.ok || current !== sequence) return;
        const data = await response.json();
        const seen = new Set();
        options.replaceChildren(...data.packages.filter((app) => !seen.has(app.package_id) && seen.add(app.package_id)).map((app) => {
          const option = document.createElement('option');
          option.value = app.package_id;
          option.label = (app.display_name || app.title) + ' — ' + app.version;
          return option;
        }));
      } catch (error) { /* Suggestions are optional; an exact package ID still works. */ }
    }
    input.addEventListener('focus', () => { if (!options.children.length) suggest(); }, { once: true });
    input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(suggest, 250); });
  });
})();
