// Health check pages: client-side search on the lists, and a running
// report that reloads when its stage changes.
(function () {
  'use strict';

  function setupSearch() {
    var input = document.querySelector('[data-hc-search]');
    if (!input) return;
    var items = Array.prototype.slice.call(document.querySelectorAll('[data-hc-item]'));
    if (!items.length) return;
    var info = document.querySelector('[data-hc-info]');
    var empty = document.querySelector('[data-hc-empty]');
    input.hidden = false;
    input.addEventListener('input', function () {
      var query = input.value.trim().toLowerCase();
      var shown = 0;
      items.forEach(function (item) {
        var text = (item.getAttribute('data-hc-search') || '').toLowerCase();
        var match = !query || text.indexOf(query) !== -1;
        var row = item.closest('li') || item;
        row.hidden = !match;
        if (match) shown += 1;
      });
      if (info) info.textContent = shown + (shown === 1 ? ' match' : ' matches');
      if (empty) empty.hidden = shown !== 0;
    });
  }

  function setupPolling() {
    var report = document.querySelector('[data-hc-report]');
    if (!report) return;
    var status = report.getAttribute('data-hc-status');
    if (['queued', 'collecting', 'analysing'].indexOf(status) === -1) return;
    var id = report.getAttribute('data-hc-id');
    var stage = report.querySelector('[data-hc-stage]');
    var lastMessage = stage ? stage.textContent : '';
    var delay = 8000;

    function poll() {
      fetch('/api/health-checks/' + encodeURIComponent(id), { credentials: 'same-origin', headers: { Accept: 'application/json' } })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) {
          var check = data && data.health_check;
          if (!check) return;
          if (check.status !== status) {
            window.location.reload();
            return;
          }
          if (stage && check.stage_message && check.stage_message !== lastMessage) {
            lastMessage = check.stage_message;
            stage.textContent = check.stage_message;
          }
        })
        .catch(function () { /* try again later */ })
        .then(function () {
          delay = Math.min(delay + 2000, 30000);
          window.setTimeout(poll, delay);
        });
    }
    window.setTimeout(poll, delay);
  }

  document.addEventListener('DOMContentLoaded', function () {
    setupSearch();
    setupPolling();
  });
})();
