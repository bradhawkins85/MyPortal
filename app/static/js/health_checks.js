// Health check pages: client-side search on the lists, a running report
// that reloads when its stage changes, and the Detections page's script
// editor, which the server renders open.
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
      // Areas on the Detections page hide when none of their detections match.
      Array.prototype.forEach.call(document.querySelectorAll('[data-hc-group]'), function (group) {
        group.hidden = !group.querySelector('li:not([hidden]) [data-hc-item]');
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

  function setupOpenModal() {
    var modal = document.querySelector('[data-hc-open-modal]');
    if (!modal || modal.hidden) return;
    var field = modal.querySelector('select, input:not([type="hidden"]), textarea');
    if (field) field.focus();
    if (!window.MutationObserver || !window.history || !window.history.replaceState) return;
    // Once closed, drop ?edit= so a reload does not open it again.
    var observer = new MutationObserver(function () {
      if (!modal.hidden) return;
      observer.disconnect();
      window.history.replaceState(null, '', window.location.pathname + '#' + modal.getAttribute('data-hc-open-modal'));
    });
    observer.observe(modal, { attributes: true, attributeFilter: ['hidden'] });
  }

  document.addEventListener('DOMContentLoaded', function () {
    setupSearch();
    setupPolling();
    setupOpenModal();
  });
})();
