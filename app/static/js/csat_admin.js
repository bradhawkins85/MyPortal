// Customer satisfaction report (/admin/csat): the period and company filters
// in the page header apply as soon as they change. Without JavaScript the
// <noscript> Apply button submits them instead.
(function () {
  'use strict';

  document.querySelectorAll('[data-submit-on-change]').forEach(function (input) {
    input.addEventListener('change', function () {
      if (input.form) {
        input.form.requestSubmit();
      }
    });
  });
})();
