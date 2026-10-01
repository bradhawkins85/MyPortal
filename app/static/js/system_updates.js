(function () {
  'use strict';

  const POLL_INTERVAL_MS = 3000;
  const ACTIVE_STATUSES = new Set(['pending', 'running']);
  const TAG_CLASSES = ['tag--success', 'tag--danger', 'tag--warning'];

  function bindHistoryFilters() {
    const table = document.getElementById('system-updates-table');
    const strip = document.querySelector('[data-system-updates-filter-strip]');
    if (!table || !strip) return;
    const tiles = Array.from(strip.querySelectorAll('[data-system-updates-filter]'));
    const clearTile = strip.querySelector('[data-system-updates-filter-clear]');
    const info = document.querySelector('[data-system-updates-info]');
    const rows = Array.from(table.querySelectorAll('tbody tr[data-system-update-group]'));
    const selected = new Set();

    function apply() {
      tiles.forEach((tile) => {
        tile.setAttribute('aria-pressed', selected.has(tile.dataset.systemUpdatesFilter) ? 'true' : 'false');
      });
      if (clearTile) clearTile.setAttribute('aria-pressed', selected.size ? 'false' : 'true');
      rows.forEach((row) => {
        if (selected.size && !selected.has(row.dataset.systemUpdateGroup)) {
          row.dataset.statFilterHidden = 'true';
        } else {
          delete row.dataset.statFilterHidden;
        }
      });
      table.dispatchEvent(new CustomEvent('table:rows-updated'));
    }

    // Tiles toggle independently, so several statuses can be shown at once.
    tiles.forEach((tile) => {
      tile.addEventListener('click', () => {
        const key = tile.dataset.systemUpdatesFilter;
        if (selected.has(key)) selected.delete(key); else selected.add(key);
        apply();
      });
    });
    if (clearTile) {
      clearTile.addEventListener('click', () => {
        selected.clear();
        apply();
      });
    }

    if (info) {
      table.addEventListener('table:render', (event) => {
        const shown = event.detail ? event.detail.filteredCount : rows.length;
        info.textContent = shown === rows.length
          ? `${rows.length} update${rows.length === 1 ? '' : 's'}`
          : `Showing ${shown} of ${rows.length}`;
      });
    }
  }

  function tagClass(status) {
    if (status === 'succeeded') return 'tag--success';
    if (status === 'failed') return 'tag--danger';
    return 'tag--warning';
  }

  function render(root, update) {
    // The status tag renders in the page header bar, outside root.
    const tag = document.querySelector('[data-system-update-tag]');
    if (tag) {
      tag.textContent = update.status;
      tag.classList.remove(...TAG_CLASSES);
      tag.classList.add(tagClass(update.status));
    }

    const output = root.querySelector('[data-system-update-output]');
    const scroller = root.querySelector('[data-system-update-output-scroll]');
    if (output && update.output && output.textContent !== update.output) {
      // Keep following the log unless the administrator scrolled up to read.
      const following = !scroller
        || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 40;
      output.textContent = update.output;
      if (scroller && following) scroller.scrollTop = scroller.scrollHeight;
    }

    const errorBox = root.querySelector('[data-system-update-error]');
    const errorText = root.querySelector('[data-system-update-error-text]');
    if (errorBox && errorText) {
      errorText.textContent = update.error || '';
      errorBox.hidden = !update.error;
    }

    const completed = root.querySelector('[data-system-update-completed]');
    if (completed && update.completed_at) {
      completed.textContent = new Date(update.completed_at).toLocaleString();
    }

    const active = ACTIVE_STATUSES.has(update.status);
    root.querySelectorAll('[data-system-update-live]').forEach((element) => {
      element.hidden = !active;
    });
    if (update.status !== 'pending') {
      // The cancel action renders in the page header, outside root.
      document.querySelectorAll('[data-system-update-waiting], [data-system-update-cancel]').forEach((element) => {
        element.hidden = true;
      });
    }
    return active;
  }

  function pollProgress() {
    const root = document.querySelector('[data-system-update]');
    if (!root || !ACTIVE_STATUSES.has(root.getAttribute('data-system-update-status'))) return;
    const url = `/scheduler/system-updates/${encodeURIComponent(root.getAttribute('data-system-update'))}`;

    async function tick() {
      let active = true;
      try {
        const response = await fetch(url, {
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
          cache: 'no-store',
        });
        if (response.ok) {
          active = render(root, await response.json());
        } else if (response.status === 401 || response.status === 403 || response.status === 404) {
          active = false;
        }
      } catch (error) {
        // The portal restarts during an upgrade; keep polling until it returns.
      }
      if (active) window.setTimeout(tick, POLL_INTERVAL_MS);
    }

    window.setTimeout(tick, POLL_INTERVAL_MS);
  }

  function init() {
    bindHistoryFilters();
    pollProgress();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
