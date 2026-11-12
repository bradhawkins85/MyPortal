// Asset page, Minidumps section: while an analysis is running, watch for it
// to finish and then reload the section. Without this script the page still
// works; the technician reloads it to see the result.
(function () {
  'use strict';

  var POLL_MS = 5000;
  var GIVE_UP_MS = 20 * 60 * 1000;

  function start() {
    var root = document.querySelector('[data-md-root]');
    if (!root || !root.querySelector('[data-md-status="analysing"]')) {
      return;
    }
    var url = root.getAttribute('data-md-url');
    var live = root.querySelector('[data-md-live]');
    var startedAt = Date.now();
    var timer = null;
    var busy = false;

    function running() {
      var ids = {};
      root.querySelectorAll('[data-md-status="analysing"]').forEach(function (item) {
        ids[item.getAttribute('data-md-dump')] = true;
      });
      return ids;
    }

    function unsavedChanges() {
      var savebar = document.querySelector('[data-ae-savebar]');
      return !!savebar && !savebar.hidden;
    }

    function finish(failed) {
      stop();
      var message = failed ? 'Analysis failed.' : 'Analysis finished.';
      if (unsavedChanges()) {
        // Reloading would discard edits made elsewhere on the page while waiting.
        if (live) {
          live.textContent = message + ' Reload the page to see the result.';
        }
        return;
      }
      if (live) {
        live.textContent = message;
      }
      if (window.location.hash !== '#minidumps') {
        window.location.hash = '#minidumps';
      }
      window.location.reload();
    }

    function check() {
      if (busy || document.hidden) {
        return;
      }
      if (Date.now() - startedAt > GIVE_UP_MS) {
        stop();
        return;
      }
      busy = true;
      fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) {
          if (!data || !Array.isArray(data.dumps)) {
            return;
          }
          var waiting = running();
          var done = data.dumps.filter(function (dump) {
            return waiting[String(dump.id)] && dump.analysis_status !== 'analysing';
          });
          if (done.length) {
            finish(done.some(function (dump) { return dump.analysis_status === 'failed'; }));
          }
        })
        .catch(function () { /* try again on the next tick */ })
        .then(function () { busy = false; });
    }

    function stop() {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    }

    timer = window.setInterval(check, POLL_MS);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && timer !== null) {
        check();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
