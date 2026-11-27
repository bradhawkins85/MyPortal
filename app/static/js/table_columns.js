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
 *   - Saved per user in the database through MyPortalViewPrefs
 *     (view_preferences.js) under "tables:<table-id>:columns", value
 *     {"hidden": ["key1","key2"]}, so the layout follows the user between
 *     devices. A layout this browser saved under the old localStorage key
 *     "myportal:tables:<table-id>:columns" is transferred on first use.
 */
(function () {
  'use strict';

  var LEGACY_PREFIX = 'myportal:tables:';

  function legacyStorageKey(tableId) {
    return LEGACY_PREFIX + tableId + ':columns';
  }

  function preferenceKey(tableId) {
    return 'tables:' + tableId + ':columns';
  }

  function normaliseHidden(value) {
    if (value && Array.isArray(value.hidden)) {
      return { hidden: value.hidden.filter(function (k) { return typeof k === 'string'; }) };
    }
    return null;
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

  function hiddenSetFrom(prefs) {
    var hiddenSet = {};
    (prefs && Array.isArray(prefs.hidden) ? prefs.hidden : []).forEach(function (key) {
      hiddenSet[key] = true;
    });
    return hiddenSet;
  }

  function render(table, panel, prefs) {
    var hiddenSet = hiddenSetFrom(prefs);
    applyVisibility(table, hiddenSet);
    syncCheckboxes(panel, hiddenSet);
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

  function bindPanel(table, panel, pref) {
    if (!panel || panel.__columnPickerBound) return;
    panel.__columnPickerBound = true;

    panel.addEventListener('change', function (event) {
      var input = event.target.closest('[data-table-column-toggle]');
      if (!input) return;
      var prefs = { hidden: collectHidden(panel) };
      applyVisibility(table, hiddenSetFrom(prefs));
      pref.save(prefs);
    });

    var resetBtn = panel.querySelector('[data-table-columns-reset]');
    if (resetBtn) {
      resetBtn.addEventListener('click', function (event) {
        event.preventDefault();
        render(table, panel, pref.reset());
      });
    }
  }

  function initTable(table) {
    if (table.__columnVisibilityBound) return;
    var tableId = table.getAttribute('data-table-id');
    if (!tableId) return;
    var picker = document.querySelector('[data-table-columns="' + tableId + '"]');
    var panel = picker ? picker.querySelector('[data-table-columns-panel]') : null;
    // Tables without a column picker have no layout to remember.
    if (!panel || !window.MyPortalViewPrefs) return;
    table.__columnVisibilityBound = true;

    var defaultHidden = defaultsFor(panel);
    var defaults = {
      hidden: Object.keys(defaultHidden).filter(function (k) { return defaultHidden[k]; }),
    };

    var pref = window.MyPortalViewPrefs.bind({
      key: preferenceKey(tableId),
      defaults: defaults,
      normalise: normaliseHidden,
      equals: function (a, b) {
        return window.MyPortalViewPrefs.sameMembers(a && a.hidden, b && b.hidden);
      },
      legacy: function () {
        return window.MyPortalViewPrefs.readLegacyJson(legacyStorageKey(tableId));
      },
      legacyKeys: [legacyStorageKey(tableId)],
      apply: function (prefs) { render(table, panel, prefs); },
    });

    render(table, panel, pref.initial);
    bindPanel(table, panel, pref);
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
