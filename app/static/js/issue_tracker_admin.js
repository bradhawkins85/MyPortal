(function () {
  'use strict';

  const ACTIVE_STATUSES = ['new', 'investigating', 'in_progress'];

  function normalize(value) {
    return String(value == null ? '' : value).trim().toLowerCase();
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) {
        return;
      }
      if (key === 'className') {
        node.className = value;
      } else if (key === 'text') {
        node.textContent = value;
      } else {
        node.setAttribute(key, value === true ? '' : value);
      }
    });
    (children || []).forEach((child) => {
      if (child !== null && child !== undefined && child !== false) {
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
      }
    });
    return node;
  }

  function parseJson(id) {
    const node = document.getElementById(id);
    if (!node) {
      return null;
    }
    try {
      return JSON.parse(node.textContent || 'null');
    } catch (error) {
      console.error('Unable to parse issue tracker data', error);
      return null;
    }
  }

  function plural(count, one, many) {
    return `${count} ${count === 1 ? one : many}`;
  }

  // ----- list: search, filters, confirmations ------------------------------

  function initList() {
    const filters = document.querySelector('[data-iss-filters]');
    if (filters) {
      filters.querySelectorAll('[data-iss-filter]').forEach((select) => {
        select.addEventListener('change', () => {
          if (typeof filters.requestSubmit === 'function') {
            filters.requestSubmit();
          } else {
            filters.submit();
          }
        });
      });
      const apply = filters.querySelector('[data-iss-apply]');
      if (apply) {
        apply.hidden = true;
      }
    }

    const search = document.querySelector('[data-iss-search]');
    const items = Array.from(document.querySelectorAll('[data-iss-item]'));
    const noResults = document.querySelector('[data-iss-no-results]');
    if (search && items.length) {
      search.addEventListener('input', () => {
        const terms = normalize(search.value).split(/\s+/).filter(Boolean);
        let visible = 0;
        items.forEach((item) => {
          const haystack = normalize(item.dataset.issSearchText);
          const match = terms.every((term) => haystack.includes(term));
          item.hidden = !match;
          visible += match ? 1 : 0;
        });
        if (noResults) {
          noResults.hidden = visible > 0;
        }
      });
    }

    document.querySelectorAll('form[data-iss-confirm]').forEach((form) => {
      form.addEventListener('submit', (event) => {
        if (!window.confirm(form.dataset.issConfirm)) {
          event.preventDefault();
        }
      });
    });

    // Colour the status selects to match their value as soon as it changes.
    document.querySelectorAll('.iss-status-select').forEach((select) => {
      select.addEventListener('change', () => {
        Array.from(select.classList)
          .filter((name) => name.startsWith('iss-status--'))
          .forEach((name) => select.classList.remove(name));
        select.classList.add(`iss-status--${select.value}`);
      });
    });
  }

  // ----- editor modal -------------------------------------------------------

  function initEditor() {
    const data = parseJson('iss-editor-data');
    const modal = document.getElementById('iss-modal');
    if (!data || !modal) {
      return;
    }
    const form = modal.querySelector('[data-iss-form]');
    const title = modal.querySelector('[data-iss-title]');
    const subtitle = modal.querySelector('[data-iss-subtitle]');
    const submitButton = modal.querySelector('[data-iss-submit]');
    const nameInput = modal.querySelector('[data-iss-input="name"]');
    const descriptionInput = modal.querySelector('[data-iss-input="description"]');
    const descriptionCount = modal.querySelector('[data-iss-description-count]');
    const companySelect = modal.querySelector('[data-iss-input="companies"]');
    const companiesLabel = modal.querySelector('[data-iss-companies-label]');
    const companiesTitle = modal.querySelector('[data-iss-companies-title]');
    const companiesIntro = modal.querySelector('[data-iss-companies-intro]');
    const companiesEmpty = modal.querySelector('[data-iss-companies-empty]');
    const addSections = Array.from(modal.querySelectorAll('[data-iss-add]'));
    const companiesCount = modal.querySelector('[data-iss-companies-count]');
    const linked = modal.querySelector('[data-iss-linked]');
    const linkedList = modal.querySelector('[data-iss-linked-list]');
    const statusInputs = Array.from(modal.querySelectorAll('[data-iss-input="status"]'));
    const detailsError = modal.querySelector('[data-iss-error="details"]');
    const tabs = Array.from(modal.querySelectorAll('[data-iss-tab]'));
    const panels = Array.from(modal.querySelectorAll('[data-iss-panel]'));
    const previewName = modal.querySelector('[data-iss-preview-name]');
    const previewDescription = modal.querySelector('[data-iss-preview-description]');
    const previewRules = modal.querySelector('[data-iss-preview-rules]');
    const deleteButton = modal.querySelector('[data-iss-delete]');

    const companies = (data.companies || []).map((company) => ({ id: String(company.id), name: company.name }));
    const statusLabels = new Map((data.statuses || []).map((status) => [status.value, status.label]));
    const issuesById = new Map((data.issues || []).map((issue) => [String(issue.id), issue]));
    const takenNames = new Map((data.issues || []).map((issue) => [normalize(issue.name), String(issue.id)]));

    const state = { mode: 'create', issue: null, snapshot: '' };
    let lastTrigger = null;

    function selectedCompanies() {
      return Array.from(companySelect.selectedOptions).map((option) => option.textContent);
    }

    function selectedStatus() {
      const checked = statusInputs.find((input) => input.checked);
      return checked ? checked.value : 'new';
    }

    function serializeState() {
      return JSON.stringify([
        nameInput.value,
        descriptionInput.value,
        Array.from(companySelect.selectedOptions).map((option) => option.value),
        selectedStatus(),
      ]);
    }

    function showTab(name) {
      tabs.forEach((tab) => {
        const active = tab.dataset.issTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.issPanel !== name;
      });
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => showTab(tab.dataset.issTab));
      tab.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
          return;
        }
        event.preventDefault();
        const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
        showTab(next.dataset.issTab);
        next.focus();
      });
    });

    function nameError() {
      const name = nameInput.value.trim();
      if (!name) {
        return 'Give the issue a name.';
      }
      const owner = takenNames.get(normalize(name));
      if (owner && (!state.issue || owner !== String(state.issue.id))) {
        return 'Another issue already uses this name.';
      }
      return '';
    }

    function renderPreview() {
      const name = nameInput.value.trim();
      const description = descriptionInput.value.trim();
      previewName.textContent = name || 'Untitled issue';
      previewName.classList.toggle('is-placeholder', !name);
      previewDescription.textContent = description;
      previewDescription.hidden = !description;
      descriptionCount.textContent = String(descriptionInput.value.length);

      const existing = state.issue ? state.issue.assignments : [];
      const adding = selectedCompanies();
      const status = statusLabels.get(selectedStatus()) || selectedStatus();
      const total = existing.length + adding.length;
      companiesCount.textContent = total ? String(total) : '';

      const rules = [];
      if (existing.length) {
        const active = existing.filter((link) => ACTIVE_STATUSES.includes(link.status)).length;
        rules.push({
          text: `Linked to ${plural(existing.length, 'company', 'companies')}${active ? `, still active for ${active}` : ', none still active'}.`,
          tone: active ? 'active' : 'done',
        });
      }
      if (adding.length) {
        const names = adding.length <= 3 ? adding.join(', ') : `${adding.slice(0, 3).join(', ')} and ${adding.length - 3} more`;
        rules.push({ text: `Adds ${names} as “${status}”.`, tone: 'logic' });
      } else if (!existing.length) {
        rules.push({ text: 'Not linked to any companies yet. Add them now or later from Edit.', tone: 'muted' });
      }
      const error = nameError();
      if (error) {
        rules.push({ text: error, tone: 'error' });
      }
      previewRules.replaceChildren(...rules.map((rule) => el('li', {
        className: `scf-preview__rule iss-rule iss-rule--${rule.tone}`,
        text: rule.text,
      })));

      const detailsTab = tabs.find((tab) => tab.dataset.issTab === 'details');
      const companiesTab = tabs.find((tab) => tab.dataset.issTab === 'companies');
      if (detailsTab) {
        detailsTab.classList.toggle('is-configured', Boolean(name) && !error);
      }
      if (companiesTab) {
        companiesTab.classList.toggle('is-configured', total > 0);
      }
    }

    function showDetailsError(message) {
      detailsError.textContent = message;
      detailsError.hidden = !message;
      nameInput.classList.toggle('is-invalid', Boolean(message));
      const detailsTab = tabs.find((tab) => tab.dataset.issTab === 'details');
      if (detailsTab) {
        detailsTab.classList.toggle('has-error', Boolean(message));
      }
    }

    function load(issue) {
      state.issue = issue || null;
      state.mode = issue ? 'edit' : 'create';
      const editing = state.mode === 'edit';

      form.setAttribute('action', editing ? `/admin/issues/${encodeURIComponent(issue.id)}/update` : '/admin/issues');
      title.textContent = editing ? 'Edit issue' : 'Create issue';
      subtitle.textContent = editing
        ? 'Rename the issue, update its description or link more companies.'
        : 'Name the problem once, then link every company it affects.';
      submitButton.textContent = editing ? 'Save changes' : 'Create issue';
      submitButton.disabled = false;
      deleteButton.hidden = !editing;
      deleteButton.disabled = false;
      deleteButton.formAction = editing ? `/admin/issues/${encodeURIComponent(issue.id)}/delete` : '/admin/issues';

      nameInput.value = editing ? issue.name : '';
      descriptionInput.value = editing ? issue.description : '';

      // Existing links are managed from the list; the picker only adds new ones.
      companySelect.name = editing ? 'newCompanyIds' : 'companyIds';
      statusInputs.forEach((input) => {
        input.name = editing ? 'newCompanyStatus' : 'initialStatus';
        input.checked = input.value === 'new';
      });
      const linkedIds = new Set((editing ? issue.assignments : []).map((link) => String(link.company_id)));
      const available = companies.filter((company) => !linkedIds.has(company.id));
      companySelect.replaceChildren(...available.map((company) => el('option', { value: company.id, text: company.name })));
      companiesEmpty.hidden = available.length > 0;
      addSections.forEach((section) => {
        section.hidden = available.length === 0;
      });
      companiesLabel.textContent = editing ? 'Link more companies' : 'Add companies';
      companiesTitle.textContent = editing ? 'Companies affected' : 'Which companies are affected?';
      companiesIntro.textContent = editing
        ? 'New companies start with the status below. Change or remove existing ones from the issue list.'
        : 'Each company you add starts with the status below. You can change each one from the issue list later.';

      linked.hidden = linkedIds.size === 0;
      linkedList.replaceChildren(...(editing ? issue.assignments : []).map((link) => el('li', {
        className: `scf-chip iss-status iss-status--${link.status}`,
      }, [`${link.company_name || 'Unknown company'} · ${statusLabels.get(link.status) || link.status}`])));

      showDetailsError('');
      showTab('details');
      renderPreview();
    }

    function openModal(trigger) {
      lastTrigger = trigger || null;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('scf-modal-open');
      const body = modal.querySelector('.modal__body');
      if (body) {
        body.scrollTop = 0;
      }
      state.snapshot = serializeState();
      window.requestAnimationFrame(() => nameInput.focus());
    }

    function closeModal(force) {
      if (!force && serializeState() !== state.snapshot && !window.confirm('Discard your unsaved changes to this issue?')) {
        return;
      }
      modal.hidden = true;
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('scf-modal-open');
      if (window.location.search.includes('issueId=')) {
        const url = new URL(window.location.href);
        url.searchParams.delete('issueId');
        window.history.replaceState(null, '', url.pathname + url.search + url.hash);
      }
      if (lastTrigger && typeof lastTrigger.focus === 'function') {
        lastTrigger.focus();
      }
    }

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal(false);
      });
    });

    modal.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        // Let an open searchable picker close itself first.
        if (event.defaultPrevented) {
          return;
        }
        event.preventDefault();
        closeModal(false);
        return;
      }
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = Array.from(modal.querySelectorAll(
        'button:not([disabled]), textarea:not([disabled]), input:not([type="hidden"]):not([disabled]):not([tabindex="-1"]), select:not([disabled]):not([tabindex="-1"]), [tabindex="0"]',
      )).filter((node) => node.offsetParent !== null);
      if (!focusable.length) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    [nameInput, descriptionInput].forEach((input) => {
      input.addEventListener('input', () => {
        if (!detailsError.hidden) {
          showDetailsError(nameError());
        }
        renderPreview();
      });
    });
    companySelect.addEventListener('change', renderPreview);
    statusInputs.forEach((input) => input.addEventListener('change', renderPreview));

    form.addEventListener('submit', (event) => {
      if (event.submitter === deleteButton) {
        if (!window.confirm(`Delete “${state.issue.name}” and all of its company status records? This cannot be undone.`)) {
          event.preventDefault();
          return;
        }
        state.snapshot = serializeState();
        deleteButton.disabled = true;
        deleteButton.textContent = 'Deleting…';
        return;
      }
      const error = nameError();
      if (error) {
        event.preventDefault();
        showDetailsError(error);
        showTab('details');
        nameInput.focus();
        renderPreview();
        return;
      }
      nameInput.value = nameInput.value.trim();
      state.snapshot = serializeState();
      submitButton.disabled = true;
      submitButton.textContent = 'Saving…';
    });

    document.querySelectorAll('[data-iss-create]').forEach((trigger) => {
      trigger.addEventListener('click', (event) => {
        event.preventDefault();
        load(null);
        openModal(trigger);
      });
    });
    document.querySelectorAll('[data-iss-edit]').forEach((trigger) => {
      trigger.addEventListener('click', (event) => {
        const issue = issuesById.get(trigger.dataset.issEdit);
        if (!issue) {
          return;
        }
        event.preventDefault();
        load(issue);
        openModal(trigger);
      });
    });

    // /admin/issues?issueId=… (links from elsewhere, and redirects after a
    // failed save) opens the editor straight away.
    if (data.openIssueId !== null && data.openIssueId !== undefined) {
      const issue = issuesById.get(String(data.openIssueId));
      if (issue) {
        load(issue);
        openModal(null);
      }
    }
  }

  function init() {
    initList();
    initEditor();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
