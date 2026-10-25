// Health check pages: client-side search on the lists, a running report
// that reloads when its stage changes, the modal editors (Detections'
// script editor, Monthly's group editor), which the server renders open, and
// the Detections page's lists of the assets that report each detection.
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
    // The close button and Cancel hide the modal; main.js turns Escape into a
    // click on the close button.
    modal.addEventListener('click', function (event) {
      if (!event.target.closest('[data-modal-close]')) return;
      event.preventDefault();
      modal.hidden = true;
      var origin = document.getElementById(modal.getAttribute('data-hc-open-modal'));
      if (origin) {
        if (!origin.hasAttribute('tabindex')) origin.setAttribute('tabindex', '-1');
        origin.focus();
      }
    });
    if (!window.MutationObserver || !window.history || !window.history.replaceState) return;
    // Once closed, drop the query (?edit=, ?group=, ?new=) so a reload does not
    // open it again.
    var observer = new MutationObserver(function () {
      if (!modal.hidden) return;
      observer.disconnect();
      window.history.replaceState(null, '', window.location.pathname + '#' + modal.getAttribute('data-hc-open-modal'));
    });
    observer.observe(modal, { attributes: true, attributeFilter: ['hidden'] });
  }

  function setupAssetLists() {
    var open = null;
    var opener = null;

    function close() {
      if (!open) return;
      open.hidden = true;
      open.setAttribute('aria-hidden', 'true');
      open = null;
      if (opener) opener.focus();
      opener = null;
    }

    document.addEventListener('click', function (event) {
      var button = event.target.closest('[data-hc-assets-open]');
      if (button) {
        var modal = document.getElementById(button.getAttribute('data-hc-assets-open'));
        if (!modal) return;
        event.preventDefault();
        close();
        open = modal;
        opener = button;
        modal.hidden = false;
        modal.style.display = '';
        modal.setAttribute('aria-hidden', 'false');
        var closeButton = modal.querySelector('[data-modal-close]');
        if (closeButton) closeButton.focus();
        return;
      }
      if (open && (event.target === open || (open.contains(event.target) && event.target.closest('[data-modal-close]')))) {
        event.preventDefault();
        close();
      }
    });
    document.addEventListener('keydown', function (event) {
      if (open && (event.key === 'Escape' || event.key === 'Esc')) close();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    setupSearch();
    setupPolling();
    setupOpenModal();
    setupAssetLists();
  });
})();
