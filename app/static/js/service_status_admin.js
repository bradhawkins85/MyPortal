(function () {
  'use strict';

  const FREQUENCY_KEYS = ['operational', 'maintenance', 'degraded', 'partial_outage', 'outage'];
  const DURING = {
    operational: 'while operational',
    maintenance: 'during maintenance',
    degraded: 'while degraded',
    partial_outage: 'during a partial outage',
    outage: 'during a major outage',
  };
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled])';

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
      } else if (value === true) {
        node.setAttribute(key, '');
      } else {
        node.setAttribute(key, value);
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
      console.error('Unable to parse service status data', error);
      return null;
    }
  }

  function formatWhen(iso) {
    if (!iso) {
      return '';
    }
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? String(iso) : date.toLocaleString();
  }

  function describeMinutes(value) {
    const minutes = parseInt(value, 10);
    if (!minutes || minutes < 1) {
      return null;
    }
    if (minutes % 1440 === 0) {
      const days = minutes / 1440;
      return days === 1 ? 'every day' : `every ${days} days`;
    }
    if (minutes % 60 === 0) {
      const hours = minutes / 60;
      return hours === 1 ? 'every hour' : `every ${hours} hours`;
    }
    return minutes === 1 ? 'every minute' : `every ${minutes} minutes`;
  }

  function trapFocus(modal, event) {
    if (event.key !== 'Tab') {
      return;
    }
    const focusable = Array.from(modal.querySelectorAll(FOCUSABLE)).filter((node) => node.offsetParent !== null);
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
  }

  function showModal(modal) {
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('scf-modal-open');
  }

  function hideModal(modal) {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.scf-modal:not([hidden])')) {
      document.body.classList.remove('scf-modal-open');
    }
  }

  // ----- published status pages ------------------------------------------------

  function initPublicPages() {
    const modal = document.getElementById('public-pages-modal');
    if (!modal) {
      return;
    }
    let trigger = null;
    function close() {
      hideModal(modal);
      if (trigger && typeof trigger.focus === 'function') {
        trigger.focus();
      }
      trigger = null;
    }
    document.querySelectorAll('[data-public-pages-modal-open]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        trigger = button;
        showModal(modal);
        const search = modal.querySelector('input[type="search"]');
        if (search) {
          search.focus();
        }
      });
    });
    modal.querySelectorAll('[data-public-pages-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        close();
      });
    });
    modal.addEventListener('keydown', (event) => trapFocus(modal, event));
    modal.querySelectorAll('[data-ssa-copy]').forEach((button) => {
      button.addEventListener('click', () => {
        const url = new URL(button.dataset.ssaCopy, window.location.origin).toString();
        const done = () => {
          const label = button.firstChild;
          label.textContent = 'Copied';
          window.setTimeout(() => { label.textContent = 'Copy'; }, 1600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(url).then(done, () => window.prompt('Copy this link', url));
        } else {
          window.prompt('Copy this link', url);
        }
      });
    });
  }

  // ----- service list -----------------------------------------------------------

  function initList(root) {
    const items = Array.from(root.querySelectorAll('[data-ssa-item]'));
    const search = document.querySelector('[data-ssa-search]');
    const filter = document.querySelector('[data-ssa-filter]');
    const count = document.querySelector('[data-ssa-count]');
    const noResults = root.querySelector('[data-ssa-no-results]');
    if (!items.length) {
      return;
    }
    function apply() {
      const terms = normalize(search && search.value).split(/\s+/).filter(Boolean);
      const wanted = filter ? filter.value : '';
      let visible = 0;
      items.forEach((item) => {
        const text = normalize(item.dataset.searchText);
        const active = item.dataset.active === 'true';
        let matchesFilter = true;
        if (wanted === 'attention') {
          matchesFilter = active && item.dataset.status !== 'operational';
        } else if (wanted === 'inactive') {
          matchesFilter = !active;
        } else if (wanted) {
          matchesFilter = item.dataset.status === wanted;
        }
        const match = matchesFilter && terms.every((term) => text.includes(term));
        item.hidden = !match;
        visible += match ? 1 : 0;
      });
      if (noResults) {
        noResults.hidden = visible > 0;
      }
      if (count) {
        const total = items.length;
        count.textContent = visible === total
          ? `${total} service${total === 1 ? '' : 's'}`
          : `${visible} of ${total} services`;
      }
    }
    if (search) {
      search.addEventListener('input', apply);
    }
    if (filter) {
      filter.addEventListener('change', apply);
    }
  }

  // ----- editor -------------------------------------------------------------------

  function initEditor(data) {
    const modal = document.getElementById('service-modal');
    const form = modal && modal.querySelector('[data-ssa-form]');
    if (!form) {
      return;
    }
    const services = new Map((data.services || []).map((service) => [String(service.id), service]));
    const statuses = new Map((data.statuses || []).map((entry) => [entry.value, entry]));
    const frequencyDefaults = data.frequencyDefaults || {};
    const defaultStatus = data.defaultStatus || 'operational';

    const input = (name) => form.querySelector(`[data-ssa-input="${name}"]`);
    const statusInputs = Array.from(form.querySelectorAll('[data-ssa-input="status"]'));
    const title = modal.querySelector('[data-ssa-title]');
    const subtitle = modal.querySelector('[data-ssa-subtitle]');
    const saveButton = modal.querySelector('[data-ssa-save]');
    const deleteButton = modal.querySelector('[data-ssa-delete]');
    const refreshTagsButton = modal.querySelector('[data-ssa-refresh-tags]');
    const checkNowButton = modal.querySelector('[data-ssa-check-now]');
    const tabs = Array.from(modal.querySelectorAll('[data-ssa-tab]'));
    const panels = Array.from(modal.querySelectorAll('[data-ssa-panel]'));
    const tagsBox = modal.querySelector('[data-ssa-tags]');
    const tagsInput = tagsBox.querySelector('.scf-tags__input');
    const tagsOut = form.querySelector('[data-ssa-out="tags"]');
    const modeInputs = Array.from(form.querySelectorAll('[data-ssa-visibility-mode]'));
    const companiesWrap = modal.querySelector('[data-ssa-companies]');
    const companyInputs = Array.from(form.querySelectorAll('[data-ssa-company]'));
    const companyRows = Array.from(modal.querySelectorAll('[data-ssa-company-row]'));
    const companySearch = modal.querySelector('[data-ssa-company-search]');
    const companyChips = modal.querySelector('[data-ssa-company-chips]');
    const companyCount = modal.querySelector('[data-ssa-company-count]');
    const companyNoResults = modal.querySelector('[data-ssa-company-no-results]');
    const aiEnabled = input('ai_lookup_enabled');
    const aiSettings = modal.querySelector('[data-ssa-ai-settings]');
    const aiNote = modal.querySelector('[data-ssa-ai-note]');
    const frequencySentence = modal.querySelector('[data-ssa-frequency-sentence]');
    const lastCheck = modal.querySelector('[data-ssa-last-check]');
    const lastCheckText = modal.querySelector('[data-ssa-last-check-text]');
    const preview = {
      card: modal.querySelector('[data-ssa-preview]'),
      name: modal.querySelector('[data-ssa-preview-name]'),
      status: modal.querySelector('[data-ssa-preview-status]'),
      message: modal.querySelector('[data-ssa-preview-message]'),
      description: modal.querySelector('[data-ssa-preview-description]'),
      tags: modal.querySelector('[data-ssa-preview-tags]'),
      updated: modal.querySelector('[data-ssa-preview-updated]'),
      ai: modal.querySelector('[data-ssa-preview-ai]'),
      rules: modal.querySelector('[data-ssa-preview-rules]'),
    };
    const errors = {};
    modal.querySelectorAll('[data-ssa-error]').forEach((node) => {
      errors[node.dataset.ssaError] = node;
    });
    const errorTab = { name: 'details', visibility: 'visibility', ai: 'ai' };

    let current = null;
    let tags = [];
    let snapshot = '';
    let lastTrigger = null;

    // --- tabs

    function selectTab(name, focus) {
      tabs.forEach((tab) => {
        const active = tab.dataset.ssaTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
        if (active && focus) {
          tab.focus();
        }
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.ssaPanel !== name;
      });
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => selectTab(tab.dataset.ssaTab, false));
      tab.addEventListener('keydown', (event) => {
        let next = null;
        if (event.key === 'ArrowRight') {
          next = tabs[(index + 1) % tabs.length];
        } else if (event.key === 'ArrowLeft') {
          next = tabs[(index - 1 + tabs.length) % tabs.length];
        } else if (event.key === 'Home') {
          next = tabs[0];
        } else if (event.key === 'End') {
          next = tabs[tabs.length - 1];
        }
        if (next) {
          event.preventDefault();
          selectTab(next.dataset.ssaTab, true);
        }
      });
    });
    function tab(name) {
      return tabs.find((node) => node.dataset.ssaTab === name);
    }

    // --- tags

    function renderTags() {
      tagsBox.querySelectorAll('.scf-tag').forEach((node) => node.remove());
      tags.forEach((value, index) => {
        const remove = el('button', {
          type: 'button',
          className: 'scf-tag__remove',
          'aria-label': `Remove tag ${value}`,
          'data-index': String(index),
          text: '×',
        });
        tagsBox.insertBefore(el('span', { className: 'scf-tag' }, [
          el('span', { className: 'scf-tag__text', text: value }),
          remove,
        ]), tagsInput);
      });
      tagsOut.value = tags.join(', ');
    }
    function addTags(raw) {
      String(raw || '').split(/[,\n]/).map((value) => value.trim()).filter(Boolean).forEach((value) => {
        if (!tags.some((existing) => normalize(existing) === normalize(value))) {
          tags.push(value);
        }
      });
      renderTags();
      refresh();
    }
    tagsBox.addEventListener('click', (event) => {
      const remove = event.target.closest('.scf-tag__remove');
      if (remove) {
        tags.splice(parseInt(remove.dataset.index, 10), 1);
        renderTags();
        refresh();
        tagsInput.focus();
        return;
      }
      if (event.target === tagsBox) {
        tagsInput.focus();
      }
    });
    tagsInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        addTags(tagsInput.value);
        tagsInput.value = '';
      } else if (event.key === 'Backspace' && !tagsInput.value && tags.length) {
        tags.pop();
        renderTags();
        refresh();
      }
    });
    tagsInput.addEventListener('paste', (event) => {
      const text = (event.clipboardData || window.clipboardData).getData('text');
      if (/[,\n]/.test(text)) {
        event.preventDefault();
        addTags(text);
      }
    });
    tagsInput.addEventListener('blur', () => {
      if (tagsInput.value.trim()) {
        addTags(tagsInput.value);
        tagsInput.value = '';
      }
    });

    // --- helpers

    function status() {
      const checked = statusInputs.find((node) => node.checked);
      return checked ? checked.value : defaultStatus;
    }
    function restricted() {
      const checked = modeInputs.find((node) => node.checked);
      return Boolean(checked && checked.value === 'restricted');
    }
    function selectedCompanies() {
      return companyInputs.filter((node) => node.checked);
    }
    function setError(name, message) {
      if (errors[name]) {
        errors[name].textContent = message || '';
        errors[name].hidden = !message;
      }
      const target = tab(errorTab[name]);
      if (target) {
        target.classList.toggle('has-error', Boolean(message));
      }
    }
    function serialize() {
      const values = [];
      new FormData(form).forEach((value, key) => {
        if (key !== '_csrf') {
          values.push([key, String(value)]);
        }
      });
      values.push(['mode', restricted() ? 'restricted' : 'everyone']);
      return JSON.stringify(values);
    }
    function isDirty() {
      return serialize() !== snapshot;
    }

    // --- rendering

    function renderCompanies() {
      const chosen = selectedCompanies();
      const isRestricted = restricted();
      companiesWrap.hidden = !isRestricted;
      companyChips.textContent = '';
      chosen.slice(0, 8).forEach((node) => {
        companyChips.append(el('li', { className: 'scf-chip scf-chip--visibility', text: node.dataset.name }));
      });
      if (chosen.length > 8) {
        companyChips.append(el('li', { className: 'scf-chip', text: `+ ${chosen.length - 8} more` }));
      }
      if (isRestricted && !chosen.length) {
        companyChips.append(el('li', { className: 'scf-chip scf-chip--muted', text: 'No companies selected' }));
      }
      companyCount.textContent = isRestricted && chosen.length ? String(chosen.length) : '';
      tab('visibility').classList.toggle('is-configured', isRestricted);
    }

    function renderAi() {
      const enabled = aiEnabled.checked;
      aiSettings.hidden = !enabled;
      tab('ai').classList.toggle('is-configured', enabled);
      const parts = [];
      FREQUENCY_KEYS.forEach((key) => {
        const when = describeMinutes(input(`ai_lookup_frequency_${key}`).value);
        if (when) {
          parts.push(`${when} ${DURING[key]}`);
        }
      });
      if (parts.length) {
        const sentence = parts.join(', ');
        frequencySentence.textContent = `Checks ${sentence}.`;
      } else {
        frequencySentence.textContent = '';
      }
      aiNote.hidden = !enabled;
      aiNote.textContent = enabled
        ? 'Automatic checks are on, so the next check may change the status you set here.'
        : '';
    }

    function renderLastCheck(service) {
      if (!service) {
        lastCheck.hidden = true;
        return;
      }
      checkNowButton.hidden = false;
      lastCheck.hidden = !service.ai_lookup_enabled && !service.ai_lookup_last_checked_at;
      if (service.ai_lookup_last_checked_at) {
        const found = statuses.get(service.ai_lookup_last_status);
        let text = `${formatWhen(service.ai_lookup_last_checked_at)}`;
        if (service.ai_lookup_last_status) {
          text += ` · AI said ${found ? found.label : service.ai_lookup_last_status}`;
        }
        if (service.ai_lookup_last_message) {
          text += ` · ${service.ai_lookup_last_message}`;
        }
        lastCheckText.textContent = text;
      } else {
        lastCheckText.textContent = 'Not checked yet. Save, then use Check now to run the first check.';
      }
    }

    function renderPreview() {
      const value = status();
      const entry = statuses.get(value) || { label: value, description: '', variant: 'status--neutral' };
      const name = input('name').value.trim();
      const message = input('status_message').value.trim();
      const description = input('description').value.trim();
      preview.card.className = `service-card ssa-preview__card service-card--${value}`;
      preview.name.textContent = name || (current ? current.name : 'New service');
      preview.status.className = `status ${entry.variant}`;
      preview.status.textContent = entry.label;
      preview.message.textContent = message || entry.description;
      preview.message.classList.toggle('service-card__message--muted', !message);
      preview.message.hidden = !(message || entry.description);
      preview.description.textContent = description;
      preview.description.hidden = !description;
      preview.tags.textContent = '';
      tags.forEach((tag) => preview.tags.append(el('span', { className: 'tag', text: tag })));
      preview.tags.hidden = !tags.length;
      preview.updated.textContent = current && current.updated_at
        ? `Last Change ${formatWhen(current.updated_at)}`
        : 'Last Change not recorded';
      preview.ai.hidden = !aiEnabled.checked;

      const rules = [];
      if (!input('is_active').checked) {
        rules.push(['', 'Hidden from every dashboard until you turn it back on.']);
      } else if (restricted()) {
        const chosen = selectedCompanies().map((node) => node.dataset.name);
        if (!chosen.length) {
          rules.push(['', 'Choose at least one company, or show it to every company.']);
        } else {
          rules.push(['visibility', chosen.length <= 3
            ? `Visible to ${chosen.join(', ')}.`
            : `Visible to ${chosen.slice(0, 2).join(', ')} and ${chosen.length - 2} other companies.`]);
        }
      } else {
        rules.push(['visibility', 'Visible to every company.']);
      }
      if (aiEnabled.checked) {
        const when = describeMinutes(input(`ai_lookup_frequency_${value}`) ? input(`ai_lookup_frequency_${value}`).value : '');
        rules.push(['logic', when
          ? `AI checks the status page ${when} ${DURING[value] || 'at this status'}.`
          : 'AI checks the status page automatically.']);
      }
      const order = parseInt(input('display_order').value, 10);
      rules.push(['m365', `Shown in position ${Number.isNaN(order) ? 0 : order} on the dashboard.`]);
      preview.rules.textContent = '';
      rules.forEach(([variant, text]) => {
        preview.rules.append(el('li', { className: `scf-preview__rule${variant ? ` scf-preview__rule--${variant}` : ''}`, text }));
      });
    }

    function refresh() {
      statusInputs.forEach((node) => node.closest('.scf-type-card').classList.toggle('is-selected', node.checked));
      modeInputs.forEach((node) => node.closest('.scf-choice').classList.toggle('is-selected', node.checked));
      tab('status').classList.toggle('is-configured', status() !== 'operational');
      renderCompanies();
      renderAi();
      renderPreview();
      if (form.dataset.submitted === 'true') {
        validate();
      }
    }

    form.addEventListener('input', (event) => {
      if (event.target !== tagsInput && event.target !== companySearch) {
        refresh();
      }
    });
    form.addEventListener('change', (event) => {
      if (event.target !== tagsInput && event.target !== companySearch) {
        refresh();
      }
    });

    companySearch.addEventListener('input', () => {
      const terms = normalize(companySearch.value).split(/\s+/).filter(Boolean);
      let visible = 0;
      companyRows.forEach((row) => {
        const match = terms.every((term) => normalize(row.dataset.searchText).includes(term));
        row.hidden = !match;
        visible += match ? 1 : 0;
      });
      companyNoResults.hidden = visible > 0;
    });

    // --- validation

    function validate() {
      const problems = [];
      ['name', 'visibility', 'ai'].forEach((name) => setError(name, ''));
      const name = input('name');
      name.classList.toggle('is-invalid', !name.value.trim());
      if (!name.value.trim()) {
        setError('name', 'Give the service a name.');
        problems.push(['details', name]);
      }
      if (restricted() && !selectedCompanies().length) {
        setError('visibility', 'Choose at least one company, or show the service to every company.');
        problems.push(['visibility', companySearch]);
      }
      const url = input('ai_lookup_url');
      let urlProblem = '';
      if (aiEnabled.checked) {
        if (!url.value.trim()) {
          urlProblem = 'Add the status page to read, or turn automatic checks off.';
        } else if (!/^https?:\/\/\S+$/i.test(url.value.trim())) {
          urlProblem = 'Use a full web address starting with https://';
        }
      }
      url.classList.toggle('is-invalid', Boolean(urlProblem));
      if (urlProblem) {
        setError('ai', urlProblem);
        problems.push(['ai', url]);
      }
      FREQUENCY_KEYS.forEach((key) => {
        const field = input(`ai_lookup_frequency_${key}`);
        const bad = aiEnabled.checked && !(parseInt(field.value, 10) >= 1);
        field.classList.toggle('is-invalid', bad);
        if (bad && !urlProblem) {
          setError('ai', 'Each check interval needs to be at least 1 minute.');
          problems.push(['ai', field]);
        }
      });
      return problems;
    }

    // --- load / open / close

    function load(service) {
      current = service || null;
      const isEdit = Boolean(service);
      form.reset();
      form.dataset.submitted = 'false';
      form.action = isEdit ? `/admin/service-status/${service.id}` : '/admin/service-status';
      title.textContent = isEdit ? `Edit ${service.name}` : 'Add service';
      subtitle.textContent = isEdit
        ? 'Changes show on the dashboard as soon as you save.'
        : 'Name the service, set its current status and choose who can see it.';
      saveButton.textContent = isEdit ? 'Save changes' : 'Create service';
      saveButton.disabled = false;
      deleteButton.hidden = !isEdit;
      refreshTagsButton.hidden = !isEdit;

      const s = service || {};
      input('name').value = s.name || '';
      input('description').value = s.description || '';
      input('status_message').value = s.status_message || '';
      input('display_order').value = s.display_order != null ? s.display_order : 0;
      input('is_active').checked = isEdit ? Boolean(s.is_active) : true;
      const wanted = statuses.has(s.status) ? s.status : defaultStatus;
      statusInputs.forEach((node) => { node.checked = node.value === wanted; });

      tags = Array.isArray(s.tags) ? s.tags.slice() : String(s.tags || '').split(',').map((t) => t.trim()).filter(Boolean);
      tagsInput.value = '';
      renderTags();

      const ids = new Set((s.company_ids || []).map((id) => String(id)));
      companyInputs.forEach((node) => { node.checked = ids.has(node.value); });
      modeInputs.forEach((node) => { node.checked = node.value === (ids.size ? 'restricted' : 'everyone'); });
      companySearch.value = '';
      companyRows.forEach((row) => { row.hidden = false; });
      companyNoResults.hidden = true;

      aiEnabled.checked = Boolean(s.ai_lookup_enabled);
      input('ai_lookup_url').value = s.ai_lookup_url || '';
      input('ai_lookup_prompt').value = s.ai_lookup_prompt || '';
      input('ai_lookup_model_override').value = s.ai_lookup_model_override || '';
      FREQUENCY_KEYS.forEach((key) => {
        const stored = s[`ai_lookup_frequency_${key}`];
        input(`ai_lookup_frequency_${key}`).value = stored != null && stored !== '' ? stored : (frequencyDefaults[key] || 60);
      });
      renderLastCheck(service);
      if (!isEdit) {
        checkNowButton.hidden = true;
      }

      ['name', 'visibility', 'ai'].forEach((name) => setError(name, ''));
      form.querySelectorAll('.is-invalid').forEach((node) => node.classList.remove('is-invalid'));
      selectTab('details', false);
      refresh();
    }

    function open(trigger) {
      lastTrigger = trigger || null;
      showModal(modal);
      snapshot = serialize();
      window.requestAnimationFrame(() => input('name').focus());
    }

    function close(force) {
      if (!force && isDirty() && !window.confirm('Discard your changes to this service?')) {
        return false;
      }
      hideModal(modal);
      if (lastTrigger && typeof lastTrigger.focus === 'function') {
        lastTrigger.focus();
      }
      lastTrigger = null;
      return true;
    }

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        close(false);
      });
    });
    modal.addEventListener('keydown', (event) => trapFocus(modal, event));

    document.querySelectorAll('[data-ssa-create]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        load(null);
        open(button);
      });
    });
    document.querySelectorAll('[data-ssa-edit]').forEach((link) => {
      link.addEventListener('click', (event) => {
        const service = services.get(String(link.dataset.ssaEdit));
        if (!service) {
          return;
        }
        event.preventDefault();
        load(service);
        open(link);
      });
    });

    // --- secondary actions on the saved service

    function submitSecondary(formId, action, question) {
      if (!current) {
        return;
      }
      if (isDirty() && !window.confirm(question)) {
        return;
      }
      const target = document.getElementById(formId);
      target.action = `/admin/service-status/${current.id}/${action}`;
      target.submit();
    }
    refreshTagsButton.addEventListener('click', () => {
      submitSecondary('ssa-refresh-tags-form', 'refresh-tags', 'Suggesting tags reloads the page and discards your unsaved changes. Continue?');
    });
    checkNowButton.addEventListener('click', () => {
      submitSecondary('ssa-check-now-form', 'check-now', 'Checking now reloads the page and discards your unsaved changes. Continue?');
    });
    deleteButton.addEventListener('click', () => {
      if (!current) {
        return;
      }
      if (!window.confirm(`Delete ${current.name}? Customers will no longer see it and this can't be undone.`)) {
        return;
      }
      const target = document.getElementById('ssa-delete-form');
      target.action = `/admin/service-status/${current.id}/delete`;
      target.submit();
    });

    form.addEventListener('submit', (event) => {
      if (tagsInput.value.trim()) {
        addTags(tagsInput.value);
        tagsInput.value = '';
      }
      form.dataset.submitted = 'true';
      const problems = validate();
      if (problems.length) {
        event.preventDefault();
        const [panel, field] = problems[0];
        selectTab(panel, false);
        window.requestAnimationFrame(() => field.focus());
        return;
      }
      // "Every company" is stored as no company assignments.
      if (!restricted()) {
        companyInputs.forEach((node) => { node.checked = false; });
      }
      renderTags();
      window.setTimeout(() => {
        saveButton.disabled = true;
        saveButton.textContent = 'Saving…';
      }, 0);
    });

    // Reopen after a redirect such as a tag refresh or a failed save.
    if (data.editing && data.editing.id) {
      load(services.get(String(data.editing.id)) || data.editing);
      open(null);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const data = parseJson('service-status-editor-data') || {};
    initPublicPages();
    const root = document.querySelector('[data-ssa-root]');
    if (root) {
      initList(root);
    }
    initEditor(data);
  });
})();
