/* Manage reports: keep the header's report count in step with the table's
 * search and column filters (tables.js dispatches table:render). */
(function () {
  'use strict';

  var table = document.getElementById('reporting-reports');
  var info = document.querySelector('[data-table-info-for="reporting-reports"]');
  if (!table || !info) {
    return;
  }

  table.addEventListener('table:render', function (event) {
    var total = table.tBodies[0] ? table.tBodies[0].rows.length : 0;
    var shown = event.detail && typeof event.detail.filteredCount === 'number'
      ? event.detail.filteredCount
      : total;
    info.textContent = shown === total
      ? total + ' reports'
      : 'Showing ' + shown + ' of ' + total + ' reports';
  });
})();
