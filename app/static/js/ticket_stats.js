(function () {
  'use strict';

  const STORAGE_KEY = 'portal.tickets.stats';

  function loadSelectedStatuses(defaultStatuses) {
    try {
      const storedValue = localStorage.getItem(STORAGE_KEY);
      if (storedValue === null) {
        return defaultStatuses;
      }
      const stored = JSON.parse(storedValue);
      if (Array.isArray(stored) && stored.every((item) => typeof item === 'string')) {
        return stored;
      }
    } catch (err) {
      console.warn('Failed to read stored ticket stat preferences', err);
    }
    return defaultStatuses;
  }

  function saveSelectedStatuses(statuses) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(statuses));
    } catch (err) {
      console.warn('Failed to persist ticket stat preferences', err);
    }
  }

  function initialiseStatControls() {
    const statsContainer = document.querySelector('[data-ticket-stats]');
    const controls = document.querySelector('[data-ticket-stat-controls]');
    if (!statsContainer || !controls) {
      return;
    }

    const toggleButton = controls.querySelector('[data-ticket-stats-toggle]');
    const panel = controls.querySelector('[data-ticket-stats-panel]');
    const toggles = Array.from(controls.querySelectorAll('[data-ticket-stat-toggle]'));
    if (!toggleButton || !panel || !toggles.length) {
      return;
    }

    function closePanel() {
      controls.classList.remove('ticket-columns--open');
      panel.hidden = true;
      toggleButton.setAttribute('aria-expanded', 'false');
    }

    function updateTotal() {
      const totalValue = statsContainer.querySelector('[data-ticket-stat="total"] .stat-strip__stat-value');
      if (!totalValue) {
        return;
      }
      const total = Array.from(statsContainer.querySelectorAll('[data-ticket-stat]:not([data-ticket-stat="total"])'))
        .filter((tile) => tile.dataset.ticketStatSelected === 'true')
        .reduce((sum, tile) => {
          const value = Number(tile.querySelector('.stat-strip__stat-value')?.textContent || 0);
          return sum + (Number.isFinite(value) ? value : 0);
        }, 0);
      totalValue.textContent = String(total);
    }

    function applySelection(selectedStatuses) {
      const selected = new Set(selectedStatuses);
      toggles.forEach((input) => {
        const status = input.dataset.ticketStatToggle;
        const isSelected = Boolean(status && selected.has(status));
        input.checked = isSelected;
        const tile = statsContainer.querySelector(`[data-ticket-stat="${CSS.escape(status || '')}"]`);
        if (tile) {
          tile.dataset.ticketStatSelected = isSelected ? 'true' : 'false';
          tile.hidden = !isSelected;
        }
      });
      updateTotal();
    }

    const defaultStatuses = toggles
      .filter((input) => input.checked)
      .map((input) => input.dataset.ticketStatToggle)
      .filter(Boolean);
    applySelection(loadSelectedStatuses(defaultStatuses));

    toggleButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const isOpen = controls.classList.toggle('ticket-columns--open');
      panel.hidden = !isOpen;
      toggleButton.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });

    document.addEventListener('click', (event) => {
      if (!controls.contains(event.target)) {
        closePanel();
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !panel.hidden) {
        closePanel();
        toggleButton.focus();
      }
    });

    toggles.forEach((input) => {
      input.addEventListener('change', () => {
        const selectedStatuses = toggles
          .filter((toggle) => toggle.checked)
          .map((toggle) => toggle.dataset.ticketStatToggle)
          .filter(Boolean);
        saveSelectedStatuses(selectedStatuses);
        applySelection(selectedStatuses);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', initialiseStatControls);
})();
