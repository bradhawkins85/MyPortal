(function () {
  'use strict';

  const STORAGE_KEY = 'myportal:asset-edit:active-section';

  function normalise(value) {
    return (value || '').toString().toLowerCase().replace(/\s+/g, ' ').trim();
  }

  function readStoredSection() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (_) {
      return null;
    }
  }

  function storeSection(key) {
    try {
      window.localStorage.setItem(STORAGE_KEY, key);
    } catch (_) {
      // The workspace also works when browser storage is unavailable.
    }
  }

  function elementForHash(hash) {
    try {
      return document.getElementById(decodeURIComponent((hash || '').slice(1)));
    } catch (_) {
      return null;
    }
  }

  function initWorkspace(workspace) {
    const nav = workspace.querySelector('[data-ae-nav]');
    const sections = Array.from(workspace.querySelectorAll('.ce-section[data-asset-edit-section]'));
    const links = Array.from(workspace.querySelectorAll('[data-ae-link]'));
    if (!nav || !sections.length || !links.length) {
      return null;
    }

    const sectionsByKey = new Map(sections.map((section) => [section.dataset.assetEditSection, section]));
    const linksByKey = new Map(links.map((link) => [link.dataset.aeLink, link]));
    const search = nav.querySelector('[data-ae-search]');
    const emptyMessage = nav.querySelector('[data-ae-search-empty]');
    const groups = Array.from(nav.querySelectorAll('[data-ae-nav-group]'));
    const searchIndex = new Map(sections.map((section) => [
      section.dataset.assetEditSection,
      normalise(Array.from(section.querySelectorAll('.card__title, .card__subtitle, h3, legend, label'))
        .map((element) => element.textContent).join(' ')),
    ]));
    let activeKey = null;

    function isAvailable(key) {
      const section = sectionsByKey.get(key);
      return Boolean(section && linksByKey.has(key) && !section.hidden);
    }

    function defaultKey() {
      return ['profile', 'inventory', ...sectionsByKey.keys()].find(isAvailable);
    }

    function keyForElement(element) {
      const section = element && element.closest('.ce-section[data-asset-edit-section]');
      return section && workspace.contains(section) ? section.dataset.assetEditSection : null;
    }

    function activate(key, options) {
      const settings = options || {};
      if (!isAvailable(key)) {
        key = defaultKey();
      }
      const section = sectionsByKey.get(key);
      if (!section) {
        return null;
      }
      const changed = activeKey !== key;
      activeKey = key;
      sections.forEach((item) => item.classList.toggle('is-active', item === section));
      links.forEach((link) => {
        if (link.dataset.aeLink === key) {
          link.setAttribute('aria-current', 'true');
        } else {
          link.removeAttribute('aria-current');
        }
      });
      storeSection(key);
      if (settings.updateHash !== false && window.history && window.history.replaceState) {
        window.history.replaceState(null, '', `#${section.id}`);
      }
      if (changed) {
        section.dispatchEvent(new CustomEvent('ae:section-shown', { bubbles: true }));
      }
      if (settings.focus) {
        const heading = section.querySelector('.ce-section__header .card__title');
        if (heading) {
          heading.setAttribute('tabindex', '-1');
          heading.focus({ preventScroll: true });
        }
        if (section.getBoundingClientRect().top < 0) {
          section.scrollIntoView({ block: 'start', behavior: 'auto' });
        }
      }
      return section;
    }

    function applySearch() {
      const query = normalise(search && search.value);
      let matches = 0;
      links.forEach((link) => {
        const key = link.dataset.aeLink;
        const visible = isAvailable(key) && (!query || `${normalise(link.textContent)} ${searchIndex.get(key) || ''}`.includes(query));
        link.hidden = !visible;
        if (visible) {
          matches += 1;
        }
      });
      groups.forEach((group) => {
        group.hidden = !group.querySelector('[data-ae-link]:not([hidden])');
      });
      if (emptyMessage) {
        emptyMessage.hidden = matches > 0;
      }
    }

    workspace.addEventListener('click', (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      const link = event.target.closest('a[href^="#"]');
      if (!link || link.target === '_blank' || link.hasAttribute('download')) {
        return;
      }
      const target = elementForHash(link.getAttribute('href'));
      const key = keyForElement(target);
      if (!key) {
        return;
      }
      event.preventDefault();
      activate(key, { focus: true, updateHash: false });
      window.history.replaceState(null, '', link.getAttribute('href'));
      if (target !== sectionsByKey.get(key)) {
        target.scrollIntoView({ block: 'nearest', behavior: 'auto' });
      }
    });

    window.addEventListener('hashchange', () => {
      const target = elementForHash(window.location.hash);
      const key = keyForElement(target);
      if (key) {
        activate(key, { focus: true, updateHash: false });
        if (target !== sectionsByKey.get(key)) {
          target.scrollIntoView({ block: 'nearest', behavior: 'auto' });
        }
      }
    });

    if (search) {
      search.addEventListener('input', applySearch);
      search.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          const first = links.find((link) => !link.hidden);
          if (first) {
            activate(first.dataset.aeLink, { focus: true });
          }
        } else if (event.key === 'Escape' && search.value) {
          search.value = '';
          applySearch();
        }
      });
    }

    const observer = new MutationObserver(() => {
      if (!isAvailable(activeKey)) {
        activate(defaultKey());
      }
      applySearch();
    });
    sections.forEach((section) => observer.observe(section, { attributes: true, attributeFilter: ['hidden'] }));

    const hashTarget = elementForHash(window.location.hash);
    workspace.classList.add('is-enhanced');
    activate(keyForElement(hashTarget) || readStoredSection() || defaultKey(), { updateHash: false });
    applySearch();
    if (hashTarget && keyForElement(hashTarget)) {
      window.requestAnimationFrame(() => hashTarget.scrollIntoView({ block: 'nearest', behavior: 'auto' }));
    }
    return { activate, keyForElement };
  }

  function initSettingsForm(workspace, api) {
    const form = document.getElementById('asset-settings-form');
    if (!form) {
      return;
    }
    // form.elements includes custom controls associated from other sections.
    const fields = Array.from(form.elements).filter((element) =>
      element.matches('input, select, textarea') && element.name && element.name !== '_csrf' &&
      !['submit', 'button', 'reset', 'file'].includes(element.type)
    );
    const savebar = workspace.querySelector('[data-ae-savebar]');
    const count = workspace.querySelector('[data-ae-savebar-count]');
    const sectionNames = workspace.querySelector('[data-ae-savebar-sections]');
    const discard = workspace.querySelector('[data-ae-discard]');
    let submitting = false;

    function valueOf(element) {
      if (element.type === 'checkbox' || element.type === 'radio') {
        return element.checked;
      }
      if (element.tagName === 'SELECT' && element.multiple) {
        return Array.from(element.selectedOptions, (option) => option.value);
      }
      return element.value;
    }

    const initial = new Map(fields.map((element) => [element, valueOf(element)]));
    function dirtyFields() {
      return fields.filter((element) => JSON.stringify(valueOf(element)) !== JSON.stringify(initial.get(element)));
    }

    function refresh() {
      const dirty = dirtyFields();
      const dirtyKeys = new Set(dirty.map((element) => api && api.keyForElement(element)).filter(Boolean));
      workspace.querySelectorAll('[data-ae-link]').forEach((link) => {
        const dot = link.querySelector('[data-ae-dirty-dot]');
        if (dot) {
          dot.hidden = !dirtyKeys.has(link.dataset.aeLink);
        }
      });
      if (savebar) {
        savebar.hidden = dirty.length === 0;
      }
      if (count) {
        count.textContent = dirty.length === 1 ? '1 unsaved change' : `${dirty.length} unsaved changes`;
      }
      if (sectionNames) {
        const titles = dirty.map((element) => {
          const section = element.closest('.ce-section');
          const heading = section && section.querySelector('.ce-section__header .card__title');
          return heading ? heading.textContent.trim() : '';
        }).filter(Boolean);
        sectionNames.textContent = Array.from(new Set(titles)).join(', ');
      }
    }

    function firstInvalid() {
      return fields.find((element) => element.willValidate && !element.validity.valid);
    }

    function reveal(element) {
      const key = api && api.keyForElement(element);
      if (key) {
        api.activate(key);
      }
    }

    // Capture at document level because externally associated controls do not
    // bubble through the form. Native Enter and requestSubmit validation also
    // take this path, before the browser tries to focus a hidden field.
    document.addEventListener('invalid', (event) => {
      if (event.target.form !== form) {
        return;
      }
      const invalid = firstInvalid();
      if (invalid) {
        reveal(invalid);
        if (event.target !== invalid) {
          event.preventDefault();
        }
      }
    }, true);

    document.addEventListener('click', (event) => {
      const button = event.target.closest('button, input[type="submit"]');
      if (!button || button.form !== form || button.type !== 'submit' || button.formNoValidate) {
        return;
      }
      const invalid = firstInvalid();
      if (invalid) {
        event.preventDefault();
        reveal(invalid);
        invalid.reportValidity();
      }
    }, true);

    form.addEventListener('submit', (event) => {
      const invalid = firstInvalid();
      if (invalid) {
        event.preventDefault();
        reveal(invalid);
        invalid.reportValidity();
        return;
      }
      // Other forms can navigate away too; they must retain the warning.
      // Wait until all submit listeners have had the chance to cancel saving.
      queueMicrotask(() => { submitting = !event.defaultPrevented; });
    });

    fields.forEach((element) => {
      element.addEventListener('input', refresh);
      element.addEventListener('change', refresh);
    });
    form.addEventListener('reset', () => window.setTimeout(refresh, 0));

    if (discard) {
      discard.addEventListener('click', () => {
        const changed = dirtyFields();
        changed.forEach((element) => {
          const original = initial.get(element);
          if (element.type === 'checkbox' || element.type === 'radio') {
            element.checked = original;
          } else if (element.tagName === 'SELECT' && element.multiple) {
            Array.from(element.options).forEach((option) => { option.selected = original.includes(option.value); });
          } else {
            element.value = original;
          }
        });
        changed.forEach((element) => {
          element.dispatchEvent(new Event('input', { bubbles: true }));
          element.dispatchEvent(new Event('change', { bubbles: true }));
        });
        refresh();
      });
    }

    window.addEventListener('beforeunload', (event) => {
      if (!submitting && dirtyFields().length) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
    refresh();
  }

  document.addEventListener('DOMContentLoaded', () => {
    const workspace = document.querySelector('[data-asset-edit]');
    if (workspace) {
      initSettingsForm(workspace, initWorkspace(workspace));
    }
  });
})();
