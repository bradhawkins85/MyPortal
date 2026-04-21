/* Generic per-table column-visibility persistence.
 *
 * Markup contract (see templates/macros/tables.html):
 *   <table data-table data-table-id="my-table">
 *     <thead><tr><th data-column-key="name">…</th>…</tr></thead>
 *     <tbody>
 *       <tr><td data-column-key="name">…</td>…</tr>
 *     </tbody>
 *   </table>
 *
 *   <div data-table-columns="my-table">
 *     <input type="checkbox" data-table-column-toggle data-column-key="name"
 *            data-default-visible="true" />
 *     <button data-table-columns-reset>Reset to defaults</button>
 *   </div>
 *
 * Storage:
 *   - localStorage cache under "myportal:tables:<table-id>:columns"
 *     value is JSON {"hidden": ["key1","key2"]}
 *   - When authenticated (CSRF token present), preferences are also synced
 *     to GET/PUT /api/users/me/preferences?key=tables:<table-id>:columns.
 */
(function () {
  'use strict';

  var STORAGE_PREFIX = 'myportal:tables:';
  var API_BASE = '/api/users/me/preferences';

  function storageKey(tableId) {
    return STORAGE_PREFIX + tableId + ':columns';
  }

  function preferenceKey(tableId) {
    return 'tables:' + tableId + ':columns';
  }

  function readLocal(tableId) {
    try {
      var raw = window.localStorage.getItem(storageKey(tableId));
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (parsed && Array.isArray(parsed.hidden)) {
        return { hidden: parsed.hidden.filter(function (k) { return typeof k === 'string'; }) };
      }
    } catch (err) {
      /* ignore */
    }
    return null;
  }

  function writeLocal(tableId, hidden) {
    try {
      window.localStorage.setItem(storageKey(tableId), JSON.stringify({ hidden: hidden }));
    } catch (err) {
      /* quota or disabled — non-fatal */
    }
  }

  function isAuthenticated() {
    return !!document.querySelector('meta[name="csrf-token"]');
  }

  function fetchRemote(tableId) {
    if (!isAuthenticated() || typeof window.fetch !== 'function') {
      return Promise.resolve(null);
    }
    return window.fetch(API_BASE + '?key=' + encodeURIComponent(preferenceKey(tableId)), {
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
    })
      .then(function (resp) {
        if (!resp.ok) return null;
        return resp.json();
      })
      .then(function (data) {
        if (data && data.value && Array.isArray(data.value.hidden)) {
          return { hidden: data.value.hidden.filter(function (k) { return typeof k === 'string'; }) };
        }
        return null;
      })
      .catch(function () { return null; });
  }

  function pushRemote(tableId, hidden) {
    if (!isAuthenticated() || typeof window.fetch !== 'function') {
      return;
    }
    window.fetch(API_BASE, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({ key: preferenceKey(tableId), value: { hidden: hidden } }),
    }).catch(function () { /* non-fatal */ });
  }

  function applyVisibility(table, hiddenSet) {
    var headerCells = table.querySelectorAll('thead th[data-column-key]');
    headerCells.forEach(function (th) {
      var key = th.getAttribute('data-column-key');
      var hidden = hiddenSet[key] === true;
      th.classList.toggle('is-hidden', hidden);
    });
    var bodyCells = table.querySelectorAll('tbody td[data-column-key], tfoot td[data-column-key]');
    bodyCells.forEach(function (td) {
      var key = td.getAttribute('data-column-key');
      td.classList.toggle('is-hidden', hiddenSet[key] === true);
    });
  }

  function syncCheckboxes(panel, hiddenSet) {
    if (!panel) return;
    panel.querySelectorAll('[data-table-column-toggle]').forEach(function (input) {
      var key = input.getAttribute('data-column-key');
      input.checked = hiddenSet[key] !== true;
    });
  }

  function defaultsFor(panel) {
    var defaults = {};
    if (!panel) return defaults;
    panel.querySelectorAll('[data-table-column-toggle]').forEach(function (input) {
      var key = input.getAttribute('data-column-key');
      var defaultVisible = input.getAttribute('data-default-visible') !== 'false';
      defaults[key] = !defaultVisible; // hidden by default?
    });
    return defaults;
  }

  function setToHiddenSet(table, panel, prefs) {
    var hiddenSet = {};
    if (prefs && Array.isArray(prefs.hidden)) {
      prefs.hidden.forEach(function (key) { hiddenSet[key] = true; });
    } else {
      hiddenSet = defaultsFor(panel);
    }
    applyVisibility(table, hiddenSet);
    syncCheckboxes(panel, hiddenSet);
    return hiddenSet;
  }

  function collectHidden(panel) {
    var hidden = [];
    if (!panel) return hidden;
    panel.querySelectorAll('[data-table-column-toggle]').forEach(function (input) {
      if (!input.checked) {
        hidden.push(input.getAttribute('data-column-key'));
      }
    });
    return hidden;
  }

  function bindPanel(table, panel, tableId) {
    if (!panel || panel.__columnPickerBound) return;
    panel.__columnPickerBound = true;

    panel.addEventListener('change', function (event) {
      var input = event.target.closest('[data-table-column-toggle]');
      if (!input) return;
      var hidden = collectHidden(panel);
      var hiddenSet = {};
      hidden.forEach(function (k) { hiddenSet[k] = true; });
      applyVisibility(table, hiddenSet);
      writeLocal(tableId, hidden);
      pushRemote(tableId, hidden);
    });

    var resetBtn = panel.querySelector('[data-table-columns-reset]');
    if (resetBtn) {
      resetBtn.addEventListener('click', function (event) {
        event.preventDefault();
        var defaults = defaultsFor(panel);
        var hidden = Object.keys(defaults).filter(function (k) { return defaults[k]; });
        applyVisibility(table, defaults);
        syncCheckboxes(panel, defaults);
        writeLocal(tableId, hidden);
        pushRemote(tableId, hidden);
      });
    }
  }

  function initTable(table) {
    if (table.__columnVisibilityBound) return;
    table.__columnVisibilityBound = true;
    var tableId = table.getAttribute('data-table-id');
    if (!tableId) return;
    var picker = document.querySelector('[data-table-columns="' + tableId + '"]');
    var panel = picker ? picker.querySelector('[data-table-columns-panel]') : null;

    var local = readLocal(tableId);
    var hiddenSet = setToHiddenSet(table, panel, local);
    bindPanel(table, panel, tableId);

    // Reconcile with server-side preferences asynchronously.
    fetchRemote(tableId).then(function (remote) {
      if (!remote) return;
      // If remote differs from local, prefer remote and update cache.
      var currentHidden = Object.keys(hiddenSet).filter(function (k) { return hiddenSet[k]; }).sort();
      var remoteHidden = (remote.hidden || []).slice().sort();
      if (currentHidden.join('|') === remoteHidden.join('|')) {
        return;
      }
      setToHiddenSet(table, panel, remote);
      writeLocal(tableId, remote.hidden || []);
    });
  }

  function init() {
    document.querySelectorAll('table[data-table][data-table-id]').forEach(initTable);
  }

  document.addEventListener('htmx:afterSwap', init);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.MyPortalTableColumns = { init: init };
})();
