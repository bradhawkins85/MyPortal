/* Per-user view preferences (table column layouts, …) stored in the database.
 *
 * The database (GET/PUT /api/users/me/preferences) is the source of truth, so
 * a layout follows the user between devices and survives a cleared browser.
 * localStorage only keeps a per-user copy so the last known layout paints
 * before the request returns.
 *
 * Every stored record carries a "modified" flag:
 *   - false: the user has never changed this page's layout. The page's
 *     current default is applied (so later changes to a default layout reach
 *     these users) and the stored value is kept in step with it.
 *   - true:  the user changed the layout; their stored value always wins.
 *
 * The first time a user opens a page with no record in the database, any
 * layout this browser saved under the page's old localStorage key is
 * transferred (flagged modified only if it differs from the default) and the
 * old key is removed; otherwise the default is recorded, unmodified.
 *
 * Usage:
 *   var pref = MyPortalViewPrefs.bind({
 *     key: 'tables:tickets:visible-columns',   // database preference key
 *     defaults: {visible: [...]},              // the page's default layout
 *     normalise: function (value) { … },       // valid value or null
 *     equals: function (a, b) { … },           // optional, defaults to JSON
 *     legacy: function () { … },               // optional old browser value
 *     legacyKeys: ['portal.tickets.columns'],  // removed once transferred
 *     apply: function (value) { … },           // render a layout
 *   });
 *   pref.initial            // layout to render straight away
 *   pref.save(value)        // the user changed the layout (modified = true)
 *   pref.reset()            // back to the default (modified = false)
 */
(function () {
  'use strict';

  var API_BASE = '/api/users/me/preferences';
  var CACHE_PREFIX = 'myportal:viewprefs:';
  var SAVE_DELAY_MS = 400;

  function userScope() {
    var body = document.body;
    var id = body ? body.getAttribute('data-user-id') : '';
    return id ? 'u' + id : 'anon';
  }

  function isAuthenticated() {
    return !!document.querySelector('meta[name="csrf-token"]') && userScope() !== 'anon';
  }

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function cacheKey(key) {
    return CACHE_PREFIX + userScope() + ':' + key;
  }

  function readCache(key) {
    try {
      var raw = window.localStorage.getItem(cacheKey(key));
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && 'value' in parsed) {
        return { value: parsed.value, modified: parsed.modified === true };
      }
    } catch (err) {
      /* storage unavailable or corrupt */
    }
    return null;
  }

  function writeCache(key, value, modified) {
    try {
      window.localStorage.setItem(cacheKey(key), JSON.stringify({ value: value, modified: !!modified }));
    } catch (err) {
      /* storage unavailable — non-fatal */
    }
  }

  function removeLegacy(keys) {
    (keys || []).forEach(function (legacyKey) {
      try {
        window.localStorage.removeItem(legacyKey);
      } catch (err) {
        /* storage unavailable — non-fatal */
      }
    });
  }

  function jsonEquals(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function fetchRecord(key) {
    if (!isAuthenticated() || typeof window.fetch !== 'function') {
      return Promise.resolve(null);
    }
    return window.fetch(API_BASE + '?key=' + encodeURIComponent(key), {
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
    })
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .catch(function () { return null; });
  }

  function putRecord(key, value, modified, seed, keepalive) {
    if (!isAuthenticated() || typeof window.fetch !== 'function') {
      return Promise.resolve(null);
    }
    return window.fetch(API_BASE, {
      method: 'PUT',
      credentials: 'same-origin',
      keepalive: !!keepalive,
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRF-Token': csrfToken(),
      },
      body: JSON.stringify({ key: key, value: value, modified: !!modified, seed: !!seed }),
    })
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .catch(function () { return null; });
  }

  // Debounced saves so quickly toggling several columns sends one request,
  // flushed when the page is hidden so the last change is not lost.
  var pending = {};

  function flush(key, keepalive) {
    var entry = pending[key];
    if (!entry) return;
    window.clearTimeout(entry.timer);
    delete pending[key];
    putRecord(key, entry.value, entry.modified, false, keepalive);
  }

  function queueSave(key, value, modified) {
    writeCache(key, value, modified);
    if (pending[key]) window.clearTimeout(pending[key].timer);
    pending[key] = {
      value: value,
      modified: modified,
      timer: window.setTimeout(function () { flush(key, false); }, SAVE_DELAY_MS),
    };
  }

  window.addEventListener('pagehide', function () {
    Object.keys(pending).forEach(function (key) { flush(key, true); });
  });

  function bind(options) {
    var key = options.key;
    var defaults = options.defaults;
    var normalise = options.normalise || function (value) { return value; };
    var equals = options.equals || jsonEquals;
    var apply = options.apply || function () {};
    var userChanged = false;

    function legacyValue() {
      if (typeof options.legacy !== 'function') return null;
      try {
        return normalise(options.legacy());
      } catch (err) {
        return null;
      }
    }

    function effective(record) {
      if (!record) return null;
      if (!record.modified) return defaults;
      return normalise(record.value);
    }

    var cached = readCache(key);
    var initial = effective(cached) || legacyValue() || defaults;
    var current = initial;

    function show(value) {
      if (userChanged || equals(value, current)) return;
      current = value;
      apply(value);
    }

    fetchRecord(key).then(function (data) {
      if (!data) return; // offline, signed out or API unavailable: keep the cache
      if (data.exists) {
        // The database already holds this user's layout; drop the shared
        // browser copy so another user of this browser cannot inherit it.
        removeLegacy(options.legacyKeys);
        var modified = data.is_modified === true;
        var stored = normalise(data.value);
        if (modified && stored) {
          writeCache(key, stored, true);
          show(stored);
          return;
        }
        // Unmodified (or unreadable): follow the page's current default and
        // keep the stored copy in step with it.
        writeCache(key, defaults, false);
        if (!equals(stored, defaults) && !userChanged) {
          putRecord(key, defaults, false, false, false);
        }
        show(defaults);
        return;
      }
      // Nothing in the database yet: transfer this browser's layout, if any.
      var legacy = legacyValue();
      var seedValue = legacy || defaults;
      var seedModified = !!legacy && !equals(legacy, defaults);
      putRecord(key, seedValue, seedModified, true, false).then(function (record) {
        if (!record) return;
        removeLegacy(options.legacyKeys);
        var result = effective({ value: record.value, modified: record.is_modified === true }) || defaults;
        writeCache(key, result, record.is_modified === true);
        show(result);
      });
    });

    return {
      initial: initial,
      defaults: defaults,
      save: function (value) {
        userChanged = true;
        current = value;
        queueSave(key, value, true);
      },
      reset: function () {
        userChanged = true;
        current = defaults;
        queueSave(key, defaults, false);
        return defaults;
      },
    };
  }

  // Helpers shared by the column pickers.
  function sameMembers(a, b) {
    var left = (a || []).slice().sort();
    var right = (b || []).slice().sort();
    return left.join('|') === right.join('|');
  }

  function stringList(value) {
    if (!Array.isArray(value)) return null;
    return value.filter(function (item) { return typeof item === 'string'; });
  }

  function readLegacyJson(storageKey) {
    try {
      var raw = window.localStorage.getItem(storageKey);
      return raw === null ? null : JSON.parse(raw);
    } catch (err) {
      return null;
    }
  }

  /* Column pickers that keep a list of visible column keys.
   *   options.key         database preference key (value {"visible": [...]})
   *   options.defaults    visible columns by default
   *   options.available   optional list of valid column keys
   *   options.required    optional column that is always visible
   *   options.legacyKey   old localStorage key holding a JSON array
   *   options.legacyValid optional check rejecting a bad legacy array
   *   options.apply       render(visibleColumns)
   * Returns {initial, save(list), reset()} working with plain arrays.
   */
  function bindVisibleColumns(options) {
    function normalise(value) {
      var list = value ? stringList(value.visible) : null;
      if (!list || list.length === 0) return null;
      if (Array.isArray(options.available)) {
        list = list.filter(function (column) { return options.available.indexOf(column) !== -1; });
      }
      if (options.required && list.indexOf(options.required) === -1) {
        list.push(options.required);
      }
      list = list.filter(function (column, index) { return list.indexOf(column) === index; });
      return list.length ? { visible: list } : null;
    }

    var defaults = normalise({ visible: options.defaults }) || { visible: (options.defaults || []).slice() };
    var legacyKeys = options.legacyKey ? [options.legacyKey] : [];

    var pref = bind({
      key: options.key,
      defaults: defaults,
      normalise: normalise,
      equals: function (a, b) { return sameMembers(a && a.visible, b && b.visible); },
      legacy: function () {
        if (!options.legacyKey) return null;
        var raw = readLegacyJson(options.legacyKey);
        if (!Array.isArray(raw) || raw.length === 0) return null;
        if (!raw.every(function (item) { return typeof item === 'string'; })) return null;
        if (typeof options.legacyValid === 'function' && !options.legacyValid(raw)) return null;
        return { visible: raw };
      },
      legacyKeys: legacyKeys,
      apply: function (value) { options.apply(value.visible.slice()); },
    });

    return {
      initial: pref.initial.visible.slice(),
      defaults: defaults.visible.slice(),
      save: function (list) {
        var value = normalise({ visible: list }) || defaults;
        pref.save(value);
        return value.visible.slice();
      },
      reset: function () { return pref.reset().visible.slice(); },
    };
  }

  window.MyPortalViewPrefs = {
    bind: bind,
    bindVisibleColumns: bindVisibleColumns,
    sameMembers: sameMembers,
    stringList: stringList,
    readLegacyJson: readLegacyJson,
  };
})();
