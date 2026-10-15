(function () {
  'use strict';

  // The asset page's Windows updates section (rmm/_asset_windows_updates.html).

  function setUpFilter(input) {
    const table = document.getElementById(input.getAttribute('data-rmm-updates-filter'));
    if (!table) {
      return;
    }
    const rows = Array.from(table.querySelectorAll('[data-rmm-update-row]'));
    const wrapper = table.closest('.table-wrapper');
    const empty = wrapper && wrapper.nextElementSibling && wrapper.nextElementSibling.matches('[data-rmm-updates-empty]')
      ? wrapper.nextElementSibling : null;
    function applyFilter() {
      const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      rows.forEach((row) => {
        const match = words.every((word) => row.textContent.toLowerCase().includes(word));
        row.hidden = !match;
        shown += match ? 1 : 0;
      });
      if (empty) {
        empty.hidden = shown > 0;
      }
      input.dispatchEvent(new CustomEvent('rmm-updates-filtered', { bubbles: true }));
    }
    input.addEventListener('input', applyFilter);
  }

  function setUpInstall(form) {
    const choices = Array.from(form.querySelectorAll('[data-rmm-update-choice]'));
    const selectAll = form.querySelector('[data-rmm-updates-select-all]');
    const submit = form.querySelector('[data-rmm-updates-submit]');
    const count = form.querySelector('[data-rmm-updates-count]');
    const reboot = form.querySelector('[data-rmm-updates-reboot]');
    const delay = form.querySelector('[data-rmm-updates-delay]');
    const max = parseInt(form.dataset.max, 10) || 100;
    if (!submit) {
      return;
    }
    const pending = submit.disabled;

    function visible(choice) {
      return !choice.closest('[data-rmm-update-row]').hidden;
    }

    function refresh() {
      const selected = choices.filter((choice) => choice.checked).length;
      const shown = choices.filter(visible);
      if (selectAll) {
        const shownSelected = shown.filter((choice) => choice.checked).length;
        selectAll.checked = shown.length > 0 && shownSelected === shown.length;
        selectAll.indeterminate = shownSelected > 0 && shownSelected < shown.length;
        selectAll.disabled = shown.length === 0;
      }
      if (count) {
        count.textContent = selected === 0 ? 'No updates selected'
          : selected + ' update' + (selected === 1 ? '' : 's') + ' selected'
            + (selected > max ? ' (choose at most ' + max + ')' : '');
      }
      submit.disabled = pending || selected === 0 || selected > max;
      if (delay && reboot) {
        delay.disabled = reboot.value === 'never';
      }
    }

    choices.forEach((choice) => choice.addEventListener('change', refresh));
    if (selectAll) {
      selectAll.addEventListener('change', () => {
        choices.filter(visible).forEach((choice) => { choice.checked = selectAll.checked; });
        refresh();
      });
    }
    if (reboot) {
      reboot.addEventListener('change', refresh);
    }
    form.addEventListener('rmm-updates-filtered', refresh);
    form.addEventListener('submit', (event) => {
      const selected = choices.filter((choice) => choice.checked).length;
      const restart = reboot && reboot.value !== 'never'
        ? ' The device restarts afterwards if an update needs it.' : ' The device will not be restarted.';
      // eslint-disable-next-line no-alert
      if (!window.confirm('Install ' + selected + ' update' + (selected === 1 ? '' : 's') + ' on this device?' + restart)) {
        event.preventDefault();
      }
    });
    refresh();
  }

  document.querySelectorAll('[data-rmm-updates-filter]').forEach(setUpFilter);
  document.querySelectorAll('[data-rmm-updates-install]').forEach(setUpInstall);
})();
