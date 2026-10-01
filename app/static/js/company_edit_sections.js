(function () {
  'use strict';

  // Company settings workspace (Admin → Companies → Edit).
  //
  // The page renders every section in one column so it works without
  // JavaScript. This script turns it into a workspace: a grouped section
  // navigation with search, one focused section at a time (deep-linkable
  // with #hash), a save bar for the shared company settings form, and the
  // small helpers used by the redesigned sections (filters, copy buttons,
  // live previews and modals).

  const STORAGE_KEY = 'myportal:company-edit:active-section';
  const DEFAULT_SECTION = 'general';

  function readStoredSection() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (error) {
      return null;
    }
  }

  function storeSection(key) {
    try {
      window.localStorage.setItem(STORAGE_KEY, key);
    } catch (error) {
      // Storage can be unavailable in private browsing or under a restrictive policy.
    }
  }

  function normalise(value) {
    return (value || '').toString().toLowerCase().replace(/\s+/g, ' ').trim();
  }

  function initWorkspace(workspace) {
    const nav = workspace.querySelector('[data-ce-nav]');
    const sections = Array.from(workspace.querySelectorAll('.ce-section[data-company-edit-section]'));
    const links = Array.from(workspace.querySelectorAll('[data-ce-link]'));
    if (!nav || !sections.length || !links.length) {
      return null;
    }

    const sectionsByKey = new Map(sections.map((section) => [section.dataset.companyEditSection, section]));
    const linksByKey = new Map(links.map((link) => [link.dataset.ceLink, link]));
    let activeKey = null;

    workspace.classList.add('is-enhanced');

    function isAvailable(key) {
      const section = sectionsByKey.get(key);
      const link = linksByKey.get(key);
      return Boolean(section && link && !section.hidden);
    }

    function activate(key, options) {
      const settings = options || {};
      if (!isAvailable(key)) {
        key = isAvailable(DEFAULT_SECTION) ? DEFAULT_SECTION : sections[0].dataset.companyEditSection;
      }
      const section = sectionsByKey.get(key);
      const changed = key !== activeKey;
      activeKey = key;

      sections.forEach((item) => item.classList.toggle('is-active', item === section));
      links.forEach((link) => {
        if (link.dataset.ceLink === key) {
          link.setAttribute('aria-current', 'true');
        } else {
          link.removeAttribute('aria-current');
        }
      });

      storeSection(key);
      if (settings.updateHash !== false && window.history && window.history.replaceState) {
        const hash = `#${section.id}`;
        if (window.location.hash !== hash) {
          window.history.replaceState(null, '', hash);
        }
      }

      if (changed || settings.announce) {
        section.dispatchEvent(new CustomEvent('ce:section-shown', { bubbles: true }));
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

    function keyForElement(element) {
      const section = element ? element.closest('.ce-section[data-company-edit-section]') : null;
      return section ? section.dataset.companyEditSection : null;
    }

    function keyFromHash() {
      const id = decodeURIComponent((window.location.hash || '').slice(1));
      if (!id) {
        return null;
      }
      return keyForElement(document.getElementById(id));
    }

    // Links anywhere in the workspace (navigation, stat tiles) that point at a
    // section, or at something inside one, switch to that section.
    workspace.addEventListener('click', (event) => {
      const link = event.target.closest('a[href^="#"]');
      if (!link || !workspace.contains(link)) {
        return;
      }
      const target = document.getElementById(decodeURIComponent(link.getAttribute('href').slice(1)));
      const key = keyForElement(target);
      if (!key) {
        return;
      }
      event.preventDefault();
      activate(key, { focus: true });
    });

    window.addEventListener('hashchange', () => {
      const key = keyFromHash();
      if (key) {
        activate(key, { focus: true, updateHash: false });
      }
    });

    // Sections can be hidden by their own scripts (the shared credentials
    // section only appears once the vault is enabled); keep the navigation in step.
    const observer = new MutationObserver(() => {
      sections.forEach((section) => {
        const link = linksByKey.get(section.dataset.companyEditSection);
        if (link) {
          link.hidden = section.hidden;
        }
      });
      if (activeKey && !isAvailable(activeKey)) {
        activate(DEFAULT_SECTION);
      }
      applySearch();
    });
    sections.forEach((section) => observer.observe(section, { attributes: true, attributeFilter: ['hidden'] }));
    sections.forEach((section) => {
      const link = linksByKey.get(section.dataset.companyEditSection);
      if (link) {
        link.hidden = section.hidden;
      }
    });

    // --- Search -------------------------------------------------------------
    const search = nav.querySelector('[data-ce-search]');
    const emptyMessage = nav.querySelector('[data-ce-search-empty]');
    const groups = Array.from(nav.querySelectorAll('[data-ce-nav-group]'));
    const searchIndex = new Map();
    sections.forEach((section) => {
      const parts = Array.from(
        section.querySelectorAll('.card__title, .card__subtitle, h3, legend, .form-label, .scf-choice strong, label')
      ).map((node) => node.textContent);
      searchIndex.set(section.dataset.companyEditSection, normalise(parts.join(' ')));
    });

    function applySearch() {
      if (!search) {
        return;
      }
      const query = normalise(search.value);
      let matches = 0;
      links.forEach((link) => {
        const key = link.dataset.ceLink;
        const section = sectionsByKey.get(key);
        const available = section && !section.hidden;
        const haystack = `${normalise(link.textContent)} ${searchIndex.get(key) || ''}`;
        const visible = available && (!query || haystack.includes(query));
        link.hidden = !visible;
        if (visible) {
          matches += 1;
        }
      });
      groups.forEach((group) => {
        group.hidden = !group.querySelector('[data-ce-link]:not([hidden])');
      });
      if (emptyMessage) {
        emptyMessage.hidden = matches > 0;
      }
    }

    if (search) {
      search.addEventListener('input', applySearch);
      search.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          const first = links.find((link) => !link.hidden);
          if (first) {
            activate(first.dataset.ceLink, { focus: true });
          }
        } else if (event.key === 'Escape' && search.value) {
          search.value = '';
          applySearch();
        }
      });
    }

    const initialKey = keyFromHash() || readStoredSection() || DEFAULT_SECTION;
    activate(initialKey, { updateHash: Boolean(keyFromHash()), announce: true });

    return { activate, keyForElement };
  }

  // --- Company settings form: unsaved changes and saving --------------------
  function initSettingsForm(workspace, api) {
    const form = document.getElementById('company-settings-form');
    if (!form) {
      return;
    }
    const savebar = workspace.querySelector('[data-ce-savebar]');
    const savebarCount = workspace.querySelector('[data-ce-savebar-count]');
    const savebarSections = workspace.querySelector('[data-ce-savebar-sections]');
    const discardButton = workspace.querySelector('[data-ce-discard]');
    const fields = Array.from(form.elements).filter(
      (element) => element.name && element.name !== '_csrf' && element.type !== 'submit' && element.type !== 'button'
    );

    function valueOf(element) {
      if (element.type === 'checkbox' || element.type === 'radio') {
        return element.checked;
      }
      return element.value;
    }

    const initial = new Map(fields.map((element) => [element, valueOf(element)]));
    let submitting = false;

    function sectionTitle(element) {
      const section = element.closest('.ce-section');
      const title = section ? section.querySelector('.ce-section__header .card__title') : null;
      return title ? title.textContent.trim() : '';
    }

    function dirtyFields() {
      return fields.filter((element) => valueOf(element) !== initial.get(element));
    }

    function refresh() {
      const dirty = dirtyFields();
      const dirtyKeys = new Set(dirty.map((element) => api && api.keyForElement(element)).filter(Boolean));
      workspace.querySelectorAll('[data-ce-link]').forEach((link) => {
        const dot = link.querySelector('[data-ce-dirty-dot]');
        if (dot) {
          dot.hidden = !dirtyKeys.has(link.dataset.ceLink);
        }
      });
      if (savebar) {
        savebar.hidden = dirty.length === 0;
        if (savebarCount) {
          savebarCount.textContent = dirty.length === 1 ? '1 unsaved change' : `${dirty.length} unsaved changes`;
        }
        if (savebarSections) {
          const titles = Array.from(new Set(dirty.map(sectionTitle).filter(Boolean)));
          savebarSections.textContent = titles.join(', ');
        }
      }
    }

    fields.forEach((element) => {
      element.addEventListener('input', refresh);
      element.addEventListener('change', refresh);
    });

    // A required or invalid field can sit in a section that isn't showing.
    // Bring it into view before the browser reports the problem.
    function revealFirstInvalid() {
      const invalid = fields.find((element) => typeof element.checkValidity === 'function' && !element.validity.valid);
      if (!invalid) {
        return true;
      }
      if (api) {
        const key = api.keyForElement(invalid);
        if (key) {
          api.activate(key);
        }
      }
      invalid.reportValidity();
      return false;
    }

    function save() {
      if (!revealFirstInvalid()) {
        return;
      }
      submitting = true;
      if (typeof form.requestSubmit === 'function') {
        form.requestSubmit();
      } else {
        form.submit();
      }
    }

    document.querySelectorAll('[data-company-save]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        save();
      });
    });

    workspace.querySelectorAll('[data-ce-savebar-submit]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        save();
      });
    });

    form.addEventListener('submit', () => {
      submitting = true;
    });

    if (discardButton) {
      discardButton.addEventListener('click', () => {
        fields.forEach((element) => {
          const original = initial.get(element);
          if (valueOf(element) === original) {
            return;
          }
          if (element.type === 'checkbox' || element.type === 'radio') {
            element.checked = original;
          } else {
            element.value = original;
          }
          element.dispatchEvent(new Event('input', { bubbles: true }));
          element.dispatchEvent(new Event('change', { bubbles: true }));
        });
        refresh();
      });
    }

    window.addEventListener('beforeunload', (event) => {
      if (submitting || dirtyFields().length === 0) {
        return;
      }
      event.preventDefault();
      event.returnValue = '';
    });

    refresh();
  }

  // --- Section helpers -------------------------------------------------------
  function initFilters(workspace) {
    workspace.querySelectorAll('[data-ce-filter]').forEach((input) => {
      const name = input.dataset.ceFilter;
      const list = workspace.querySelector(`[data-ce-filter-list="${name}"]`);
      const empty = workspace.querySelector(`[data-ce-filter-empty="${name}"]`);
      if (!list) {
        return;
      }
      const items = Array.from(list.querySelectorAll('[data-ce-filter-item]'));
      input.addEventListener('input', () => {
        const query = normalise(input.value);
        let visible = 0;
        items.forEach((item) => {
          const match = !query || normalise(item.dataset.ceFilterText).includes(query);
          item.hidden = !match;
          if (match) {
            visible += 1;
          }
        });
        if (empty) {
          empty.hidden = visible > 0;
        }
      });
    });
  }

  function initCopyButtons(workspace) {
    workspace.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-ce-copy]');
      if (!button) {
        return;
      }
      const status = button.querySelector('[data-ce-copy-status]');
      try {
        await navigator.clipboard.writeText(button.dataset.ceCopy);
        if (status) {
          status.textContent = 'Copied';
        }
      } catch (error) {
        if (status) {
          status.textContent = 'Press Ctrl+C';
        }
      }
      window.setTimeout(() => {
        if (status) {
          status.textContent = 'Copy';
        }
      }, 1800);
    });
  }

  function initDomainPreview(workspace) {
    const input = workspace.querySelector('[data-ce-domains]');
    const preview = workspace.querySelector('[data-ce-domain-preview]');
    if (!input || !preview) {
      return;
    }
    input.addEventListener('input', () => {
      const domains = input.value
        .split(',')
        .map((value) => value.trim().replace(/^@/, ''))
        .filter(Boolean);
      preview.replaceChildren(
        ...Array.from(new Set(domains)).map((domain) => {
          const chip = document.createElement('li');
          chip.className = 'scf-chip';
          chip.textContent = `@${domain}`;
          return chip;
        })
      );
    });
  }

  function initBusinessHours(workspace) {
    const radios = Array.from(workspace.querySelectorAll('[data-ce-bh-mode]'));
    const custom = workspace.querySelector('[data-ce-bh-custom]');
    if (!radios.length || !custom) {
      return;
    }
    function update() {
      const selected = radios.find((radio) => radio.checked);
      custom.classList.toggle('is-inactive', !selected || selected.value !== 'custom');
    }
    radios.forEach((radio) => radio.addEventListener('change', update));
    update();
  }

  function initAddressPreview() {
    const form = document.querySelector('[data-ce-address-form]');
    if (!form) {
      return;
    }
    const label = form.querySelector('[data-ce-address-preview="label"]');
    const lines = form.querySelector('[data-ce-address-preview="lines"]');
    const inputs = {};
    form.querySelectorAll('[data-ce-address-input]').forEach((input) => {
      inputs[input.dataset.ceAddressInput] = input;
    });
    function value(key) {
      return inputs[key] ? inputs[key].value.trim() : '';
    }
    function update() {
      label.textContent = value('label') || 'Head office';
      const rows = [
        value('street'),
        [value('city'), value('state'), value('postcode')].filter(Boolean).join(' '),
        value('country'),
      ].filter(Boolean);
      if (!rows.length) {
        lines.innerHTML = '<span class="text-muted">Start typing the address</span>';
        return;
      }
      lines.replaceChildren();
      rows.forEach((row, index) => {
        if (index) {
          lines.appendChild(document.createElement('br'));
        }
        lines.appendChild(document.createTextNode(row));
      });
    }
    Object.values(inputs).forEach((input) => input.addEventListener('input', update));
    form.addEventListener('reset', () => window.setTimeout(update));
  }

  function initVariableName() {
    const input = document.querySelector('[data-ce-variable-name]');
    const token = document.querySelector('[data-ce-variable-token]');
    if (!input || !token) {
      return;
    }
    input.addEventListener('input', () => {
      const cleaned = input.value.replace(/\s+/g, '_').replace(/[^A-Za-z0-9_]/g, '');
      if (cleaned !== input.value) {
        input.value = cleaned;
      }
      token.textContent = `{{ company.variables.${cleaned || 'VARIABLE_NAME'} }}`;
    });
  }

  function initModals() {
    const focusable =
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

    document.querySelectorAll('[data-ce-modal]').forEach((modal) => {
      const form = modal.querySelector('form');
      let trigger = null;

      function hasTyped() {
        return form
          ? Array.from(form.querySelectorAll('input:not([type="hidden"]), textarea')).some((input) => input.value.trim())
          : false;
      }

      function visibleFocusable() {
        return Array.from(modal.querySelectorAll(focusable)).filter((element) => element.offsetParent !== null);
      }

      function onKeydown(event) {
        if (event.key === 'Escape') {
          event.preventDefault();
          close();
          return;
        }
        if (event.key !== 'Tab') {
          return;
        }
        const items = visibleFocusable();
        if (!items.length) {
          return;
        }
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }

      function open(from) {
        trigger = from || null;
        modal.hidden = false;
        modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('scf-modal-open');
        document.addEventListener('keydown', onKeydown);
        const [first] = visibleFocusable().filter((element) => element.matches('input, select, textarea'));
        if (first) {
          first.focus();
        }
      }

      function close(force) {
        if (!force && hasTyped() && !window.confirm('Discard what you have entered?')) {
          return;
        }
        modal.hidden = true;
        modal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('scf-modal-open');
        document.removeEventListener('keydown', onKeydown);
        if (form) {
          form.reset();
        }
        if (trigger) {
          trigger.focus();
        }
      }

      document.querySelectorAll(`[data-ce-modal-open="${modal.id}"]`).forEach((button) => {
        button.addEventListener('click', (event) => {
          event.preventDefault();
          open(button);
        });
      });
      modal.addEventListener('click', (event) => {
        if (event.target === modal) {
          close();
        } else if (event.target.closest('[data-modal-close]')) {
          event.preventDefault();
          close();
        }
      });
      if (form) {
        form.addEventListener('submit', () => {
          document.body.classList.remove('scf-modal-open');
        });
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const workspace = document.querySelector('[data-company-edit]');
    if (!workspace) {
      return;
    }
    const api = initWorkspace(workspace);
    initSettingsForm(workspace, api);
    initFilters(workspace);
    initCopyButtons(workspace);
    initDomainPreview(workspace);
    initBusinessHours(workspace);
    initAddressPreview();
    initVariableName();
    initModals();
  });
})();
