(function () {
  'use strict';

  const STORAGE_KEY = 'myportal:company-edit:sections';

  function sectionKey(section) {
    if (section.dataset.companyEditSection) {
      return section.dataset.companyEditSection;
    }

    const title = section.querySelector(':scope > summary .card__title');
    return title ? title.textContent.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-') : null;
  }

  function loadState() {
    try {
      const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
      return stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {};
    } catch (error) {
      return {};
    }
  }

  function saveState(state) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (error) {
      // Storage can be unavailable in private browsing or under a restrictive policy.
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    const sections = document.querySelectorAll('.company-edit-page details.card-collapsible');
    const state = loadState();

    sections.forEach(function (section) {
      const key = sectionKey(section);
      if (!key) return;

      if (Object.prototype.hasOwnProperty.call(state, key)) {
        section.open = Boolean(state[key]);
      }

      section.addEventListener('toggle', function () {
        state[key] = section.open;
        saveState(state);
      });
    });
  });
})();
