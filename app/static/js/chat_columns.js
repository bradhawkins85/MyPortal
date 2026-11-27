(function () {
  'use strict';

  // Saved per user in the database (see view_preferences.js); the old
  // browser-only key is transferred the first time the page loads.
  const PREFERENCE_KEY = 'tables:chat-rooms:visible-columns';
  const LEGACY_STORAGE_KEY = 'portal.chat.columns';
  const REQUIRED_COLUMN = 'subject';

  function setColumnVisibility(table, column, visible) {
    if (!table || !column) {
      return;
    }
    table.querySelectorAll(`[data-column="${column}"]`).forEach((element) => {
      element.style.display = visible ? '' : 'none';
    });
  }

  function initialiseColumnControls() {
    const table = document.getElementById('chat-rooms-table');
    const container = document.querySelector('[data-chat-columns]');
    if (!table || !container) {
      return;
    }

    const toggleButton = container.querySelector('[data-columns-toggle]');
    const panel = container.querySelector('[data-columns-panel]');
    const toggles = Array.from(container.querySelectorAll('.chat-column-toggle'));
    if (!toggleButton || !panel || toggles.length === 0 || !window.MyPortalViewPrefs) {
      return;
    }

    function openPanel() {
      container.classList.add('ticket-columns--open');
      panel.hidden = false;
      toggleButton.setAttribute('aria-expanded', 'true');
    }

    function closePanel() {
      container.classList.remove('ticket-columns--open');
      panel.hidden = true;
      toggleButton.setAttribute('aria-expanded', 'false');
    }

    toggleButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (container.classList.contains('ticket-columns--open')) {
        closePanel();
      } else {
        openPanel();
      }
    });

    document.addEventListener('click', (event) => {
      if (!container.contains(event.target)) {
        closePanel();
      }
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closePanel();
        toggleButton.focus();
      }
    });

    function renderColumns(columns) {
      toggles.forEach((input) => {
        const column = input.dataset.column;
        if (!column) {
          return;
        }
        const shouldShow = column === REQUIRED_COLUMN || columns.includes(column);
        input.checked = shouldShow;
        setColumnVisibility(table, column, shouldShow);
      });
    }

    const defaultColumns = toggles.filter((input) => input.checked).map((input) => input.dataset.column).filter(Boolean);
    const preference = window.MyPortalViewPrefs.bindVisibleColumns({
      key: PREFERENCE_KEY,
      defaults: defaultColumns,
      available: toggles.map((input) => input.dataset.column).filter(Boolean),
      required: REQUIRED_COLUMN,
      legacyKey: LEGACY_STORAGE_KEY,
      apply: renderColumns,
    });
    renderColumns(preference.initial);

    toggles.forEach((input) => {
      input.addEventListener('change', () => {
        const column = input.dataset.column;
        if (!column) {
          return;
        }
        if (column === REQUIRED_COLUMN) {
          input.checked = true;
          return;
        }
        const selected = toggles
          .filter((toggle) => (toggle.checked && !toggle.disabled) || toggle.dataset.column === REQUIRED_COLUMN)
          .map((toggle) => toggle.dataset.column)
          .filter(Boolean);
        if (!selected.includes(REQUIRED_COLUMN)) {
          selected.push(REQUIRED_COLUMN);
        }
        preference.save(selected);
        setColumnVisibility(table, column, input.checked);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', initialiseColumnControls);
})();
