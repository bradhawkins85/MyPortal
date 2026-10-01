(function () {
  const form = document.getElementById('kb-article-form');
  if (!form) {
    return;
  }

  function parseJson(id, fallback) {
    const script = document.getElementById(id);
    if (!script) {
      return fallback;
    }
    try {
      return JSON.parse(script.textContent || 'null') || fallback;
    } catch (error) {
      console.error('Failed to parse knowledge base admin payload', error);
      return fallback;
    }
  }

  const userOptions = parseJson('kb-admin-user-options', []);
  const companyOptions = parseJson('kb-admin-company-options', [])
    .filter((company) => company && company.id != null)
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  const initialArticle = parseJson('kb-admin-active-article', null);
  const formMode = form.dataset.kbMode || 'edit';

  const state = {
    activeSlug: null,
    activeId: null,
    aiTags: [],
    manualAiTags: [],
    dirty: false,
    saving: false,
    slugTouched: false,
  };

  const $ = (selector, root) => (root || document).querySelector(selector);
  const idField = document.getElementById('kb-article-id');
  const slugField = document.getElementById('kb-article-slug');
  const titleField = document.getElementById('kb-article-title');
  const summaryField = document.getElementById('kb-article-summary');
  const scopeField = document.getElementById('kb-article-scope');
  const userSelect = document.getElementById('kb-article-users');
  const companySelect = document.getElementById('kb-article-companies');
  const roleSelect = document.getElementById('kb-article-roles');
  const ownerField = document.getElementById('kb-article-owner');
  const lifecycleField = document.getElementById('kb-article-lifecycle');
  const reviewDueField = document.getElementById('kb-article-review-due');
  const assetSelect = document.getElementById('kb-article-assets');
  const attachmentFile = document.getElementById('kb-attachment-file');
  const previewCompany = document.getElementById('kb-preview-company');
  const aiTagsContainer = document.getElementById('kb-ai-tags-container');
  const statusElement = $('[data-kb-status]', form);
  const editorTitle = $('[data-kb-editor-title]', form);
  const saveButton = $('[data-kb-save]', form);
  const viewLink = $('[data-kb-view-link]', form);
  const userFieldWrapper = $('[data-kb-user-select]', form);
  const companyFieldWrapper = $('[data-kb-company-select]', form);
  const roleFieldWrapper = $('[data-kb-role-select]', form);
  const scopeHelp = $('[data-kb-scope-help]', form);
  const companyHelp = $('[data-kb-company-help]', form);
  const visibilitySummary = $('[data-kb-visibility-summary]', form);
  const runbookSummary = $('[data-kb-runbook-summary]', form);
  const deleteButton = $('[data-kb-delete]', form);
  const sectionsContainer = $('[data-kb-sections]', form);
  const sectionCount = $('[data-kb-section-count]', form);
  const aiTagsSection = $('[data-kb-ai-tags-section]', form);
  const aiTagsCount = $('[data-kb-ai-tags-count]', form);
  const refreshTagsButton = $('[data-kb-refresh-tags]', form);
  const existingTools = $('[data-kb-existing-tools]', form);
  const reviewTools = $('[data-kb-review-tools]', form);
  const attachmentsElement = $('[data-kb-attachments]', form);
  const attachmentCount = $('[data-kb-attachment-count]', form);
  const accessDialog = $('[data-kb-access-dialog]');
  const reviewDialog = $('[data-kb-review-dialog]');
  const versionList = reviewDialog ? $('[data-kb-version-list]', reviewDialog) : null;
  const reviewContent = reviewDialog ? $('[data-kb-review-content]', reviewDialog) : null;
  const reviewTitle = reviewDialog ? $('[data-kb-review-title]', reviewDialog) : null;

  const scopeLabels = {
    anonymous: 'Public',
    user: 'Specific users',
    company: 'Company members',
    company_admin: 'Company admins',
    super_admin: 'Super admins',
  };
  const scopeHelpMessages = {
    anonymous: 'Anyone with the link can read this article.',
    user: 'Only the selected users can read it. No selection means nobody.',
    company: 'Members of the selected companies. No selection means every company member.',
    company_admin: 'Administrators of the selected companies. No selection means any company admin.',
    super_admin: 'Only super administrators can read it.',
  };

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  }

  const RICH_HTML_ALLOWED_TAGS = new Set([
    'a', 'b', 'blockquote', 'br', 'code', 'div', 'em', 'h1', 'h2', 'h3', 'h4',
    'h5', 'h6', 'hr', 'i', 'img', 'kb-if', 'li', 'ol', 'p', 'pre', 's', 'section',
    'span', 'strong', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'u', 'ul',
  ]);
  const RICH_HTML_BLOCKED_TAGS = new Set(['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'base', 'form']);
  const RICH_HTML_GLOBAL_ATTRS = new Set(['class', 'title', 'role', 'aria-label', 'aria-hidden', 'colspan', 'rowspan']);
  const RICH_HTML_ATTRS_BY_TAG = {
    a: new Set(['href', 'target', 'rel']),
    img: new Set(['src', 'alt', 'width', 'height', 'style']),
    'kb-if': new Set(['company']),
  };

  function sanitizeRichHtml(html) {
    const source = String(html || '');
    if (window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
      const allowedAttrs = [
        ...Array.from(RICH_HTML_GLOBAL_ATTRS),
        ...Object.values(RICH_HTML_ATTRS_BY_TAG).flatMap((attrs) => Array.from(attrs)),
      ];
      return window.DOMPurify.sanitize(source, {
        ALLOWED_TAGS: Array.from(RICH_HTML_ALLOWED_TAGS),
        ALLOWED_ATTR: Array.from(new Set(allowedAttrs)),
        FORBID_TAGS: Array.from(RICH_HTML_BLOCKED_TAGS),
        ALLOW_DATA_ATTR: false,
        CUSTOM_ELEMENT_HANDLING: {
          tagNameCheck: /^kb-if$/,
        },
      });
    }
    return escapeHtml(source).replace(/\n/g, '<br>');
  }

  function setSanitizedHtml(element, html) {
    element.innerHTML = sanitizeRichHtml(html);
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  async function api(url, options) {
    const settings = Object.assign({ method: 'GET' }, options || {});
    const headers = Object.assign({ Accept: 'application/json' }, settings.headers || {});
    if (settings.json !== undefined) {
      headers['Content-Type'] = 'application/json';
      settings.body = JSON.stringify(settings.json);
      delete settings.json;
    }
    if (settings.method !== 'GET') {
      const token = getCsrfToken();
      if (token) {
        headers['X-CSRF-Token'] = token;
      }
    }
    settings.headers = headers;
    settings.credentials = 'same-origin';
    const response = await fetch(url, settings);
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      const message = detail && typeof detail.detail === 'string' ? detail.detail : `${response.status} ${response.statusText}`;
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) {
      return null;
    }
    return response.json().catch(() => null);
  }

  let statusTimer = null;
  function setStatus(message, tone, timeout) {
    if (!statusElement) {
      return;
    }
    window.clearTimeout(statusTimer);
    statusElement.textContent = message || '';
    statusElement.dataset.tone = tone || '';
    if (message && timeout) {
      statusTimer = window.setTimeout(() => setStatus(state.dirty ? 'Unsaved changes' : '', state.dirty ? 'dirty' : ''), timeout);
    }
  }

  function markDirty() {
    if (state.dirty) {
      return;
    }
    state.dirty = true;
    setStatus('Unsaved changes', 'dirty');
  }

  function markClean() {
    state.dirty = false;
  }

  function slugify(value) {
    return String(value || '')
      .normalize('NFKD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 191);
  }

  function getSelectedValues(selectElement) {
    if (!selectElement) {
      return [];
    }
    return Array.from(selectElement.selectedOptions || [])
      .map((option) => parseInt(option.value, 10))
      .filter((value) => Number.isFinite(value));
  }

  function setSelectedValues(selectElement, values) {
    if (!selectElement) {
      return;
    }
    const selected = new Set((values || []).map(String));
    Array.from(selectElement.options).forEach((option) => {
      option.selected = selected.has(option.value);
    });
    refreshPicklist(selectElement);
  }

  function fillSelect(selectElement, items, labelFor) {
    if (!selectElement) {
      return;
    }
    const selected = new Set(Array.from(selectElement.selectedOptions).map((option) => option.value));
    selectElement.innerHTML = '';
    items.forEach((item) => {
      if (item.id == null) {
        return;
      }
      const option = document.createElement('option');
      option.value = String(item.id);
      option.textContent = labelFor(item);
      option.selected = selected.has(option.value);
      selectElement.appendChild(option);
    });
  }

  function autosize(textarea) {
    if (!textarea) {
      return;
    }
    textarea.style.height = 'auto';
    textarea.style.height = `${textarea.scrollHeight + 2}px`;
  }

  // ---------------------------------------------------------------------------
  // Pick lists: searchable checkbox lists that drive hidden <select multiple>
  // ---------------------------------------------------------------------------

  const picklists = new WeakMap();

  function enhancePicklist(select) {
    if (!select || picklists.has(select)) {
      return;
    }
    const emptyLabel = select.dataset.kbPicklistEmpty || 'None selected';
    const wrapper = document.createElement('div');
    wrapper.className = 'kbe-pick';
    wrapper.innerHTML = `
      <div class="kbe-pick__head">
        <input type="search" class="kbe-pick__filter" placeholder="Filter…" aria-label="Filter options" />
        <span class="kbe-pick__count"></span>
        <button type="button" class="kbe-pick__clear">Clear</button>
      </div>
      <div class="kbe-pick__list" role="group"></div>`;
    select.hidden = true;
    select.insertAdjacentElement('afterend', wrapper);
    const filter = $('.kbe-pick__filter', wrapper);
    const count = $('.kbe-pick__count', wrapper);
    const clear = $('.kbe-pick__clear', wrapper);
    const list = $('.kbe-pick__list', wrapper);
    const label = select.id ? document.querySelector(`label[for="${select.id}"]`) : null;
    if (label) {
      list.setAttribute('aria-label', label.textContent.trim());
    }

    function applyFilter() {
      const term = filter.value.trim().toLowerCase();
      list.querySelectorAll('.kbe-pick__option').forEach((option) => {
        option.hidden = Boolean(term) && !option.textContent.toLowerCase().includes(term);
      });
    }

    function render() {
      const options = Array.from(select.options);
      const selected = options.filter((option) => option.selected);
      list.textContent = '';
      if (options.length) {
        options.forEach((option) => {
          const labelNode = document.createElement('label');
          labelNode.className = 'kbe-pick__option';
          const checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.value = option.value;
          checkbox.checked = option.selected;
          const text = document.createElement('span');
          text.textContent = option.textContent || '';
          labelNode.append(checkbox, text);
          list.appendChild(labelNode);
        });
      } else {
        const empty = document.createElement('p');
        empty.className = 'kbe-pick__empty';
        empty.textContent = 'No options available.';
        list.appendChild(empty);
      }
      count.textContent = selected.length ? `${selected.length} selected` : emptyLabel;
      clear.hidden = selected.length === 0;
      wrapper.classList.toggle('kbe-pick--compact', options.length <= 6);
      applyFilter();
    }

    list.addEventListener('change', (event) => {
      const checkbox = event.target.closest('input[type="checkbox"]');
      if (!checkbox) {
        return;
      }
      const option = Array.from(select.options).find((item) => item.value === checkbox.value);
      if (option) {
        option.selected = checkbox.checked;
      }
      const total = select.selectedOptions.length;
      count.textContent = total ? `${total} selected` : emptyLabel;
      clear.hidden = total === 0;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    clear.addEventListener('click', () => {
      Array.from(select.options).forEach((option) => { option.selected = false; });
      render();
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    filter.addEventListener('input', applyFilter);
    filter.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
      }
    });
    picklists.set(select, { render });
    render();
  }

  function refreshPicklist(select) {
    const picklist = select ? picklists.get(select) : null;
    if (picklist) {
      picklist.render();
    }
  }

  // ---------------------------------------------------------------------------
  // Sections
  // ---------------------------------------------------------------------------

  const ICONS = {
    grip: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>',
    chevron: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.4 8.6 12 13.2l4.6-4.6L18 10l-6 6-6-6z"/></svg>',
    up: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5.8 5.6 12.2 7 13.6l4-4V19h2V9.6l4 4 1.4-1.4z"/></svg>',
    down: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 18.2 6.4-6.4-1.4-1.4-4 4V5h-2v9.4l-4-4-1.4 1.4z"/></svg>',
    copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11z"/></svg>',
    insert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 11V5h2v6h6v2h-6v6h-2v-6H5v-2z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 1h4v2H4V4h4zm-3 5h12l-1 13H7z"/></svg>',
    lock: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 9V7a5 5 0 0 0-10 0v2H5v12h14V9zm-8-2a3 3 0 0 1 6 0v2H9z"/></svg>',
    ul: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="4.5" cy="6.5" r="1.5"/><circle cx="4.5" cy="12" r="1.5"/><circle cx="4.5" cy="17.5" r="1.5"/><path d="M8 5.5h13v2H8zm0 5.5h13v2H8zm0 5.5h13v2H8z"/></svg>',
    ol: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5h13v2H8zm0 5.5h13v2H8zm0 5.5h13v2H8zM3 4h2v4H4V5H3zm-.2 6.8h2.7v.9L3.9 13.4h1.6v.9H2.8v-.8l1.6-1.8H2.8zM2.8 16h2.7v3.8H2.8v-.8h1.7v-.7H3.5v-.8h1v-.6H2.8z"/></svg>',
    quote: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 17h3l2-4V7H5v6h3zm8 0h3l2-4V7h-6v6h3z"/></svg>',
    code: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9.4 16.6 4.8 12l4.6-4.6L8 6l-6 6 6 6zm5.2 0 4.6-4.6-4.6-4.6L16 6l6 6-6 6z"/></svg>',
    link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.9 12a3.1 3.1 0 0 1 3.1-3.1h4V7H7a5 5 0 0 0 0 10h4v-1.9H7A3.1 3.1 0 0 1 3.9 12zM8 13h8v-2H8zm9-6h-4v1.9h4a3.1 3.1 0 0 1 0 6.2h-4V17h4a5 5 0 0 0 0-10z"/></svg>',
    image: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 19V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2zM8.5 13.5l2.5 3 3.5-4.5 4.5 6H5z"/></svg>',
    clear: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.3 5.3 4.7 3.9 20 19.2l-1.4 1.4-6.3-6.3L10.9 18H8.8l2.3-5.6L3.3 5.3zM6 4h14v3h-5.8l-1 2.4-2.1-2.1.3-.3H6z"/></svg>',
    company: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 7V3H2v18h20V7zM6 19H4v-2h2zm0-4H4v-2h2zm0-4H4V9h2zm0-4H4V5h2zm4 12H8v-2h2zm0-4H8v-2h2zm0-4H8V9h2zm0-4H8V5h2zm10 12h-8v-2h2v-2h-2v-2h2v-2h-2V9h8zm-2-8h-2v2h2zm0 4h-2v2h2z"/></svg>',
  };

  const TOOLBAR = [
    { command: 'bold', label: 'Bold (Ctrl+B)', html: '<b>B</b>' },
    { command: 'italic', label: 'Italic (Ctrl+I)', html: '<i>I</i>' },
    { command: 'underline', label: 'Underline (Ctrl+U)', html: '<u>U</u>' },
    { command: 'strikeThrough', label: 'Strikethrough', html: '<s>S</s>' },
    { separator: true },
    { command: 'formatBlock', value: 'h3', label: 'Subheading', html: 'H3' },
    { command: 'formatBlock', value: 'h4', label: 'Minor heading', html: 'H4' },
    { command: 'formatBlock', value: 'p', label: 'Paragraph', html: '¶' },
    { separator: true },
    { command: 'insertUnorderedList', label: 'Bulleted list', html: ICONS.ul },
    { command: 'insertOrderedList', label: 'Numbered list', html: ICONS.ol },
    { command: 'formatBlock', value: 'blockquote', label: 'Quote', html: ICONS.quote },
    { command: 'formatBlock', value: 'pre', label: 'Code block', html: ICONS.code },
    { command: 'inlineCode', label: 'Inline code', html: '<span class="kbe-tool__mono">`c`</span>' },
    { separator: true },
    { command: 'createLink', label: 'Insert link', html: ICONS.link },
    { command: 'insertImage', label: 'Insert image', html: ICONS.image },
    { command: 'insertHorizontalRule', label: 'Divider', html: '—' },
    { command: 'insertConditional', label: 'Content only for one company', html: 'If…' },
    { separator: true },
    { command: 'removeFormat', label: 'Clear formatting', html: ICONS.clear },
  ];

  function buildToolbarHtml() {
    return TOOLBAR.map((item) => {
      if (item.separator) {
        return '<span class="kbe-tool__sep" aria-hidden="true"></span>';
      }
      const value = item.value ? ` data-kb-command-value="${item.value}"` : '';
      return `<button type="button" class="kbe-tool" data-kb-command="${item.command}"${value} title="${escapeHtml(item.label)}" aria-label="${escapeHtml(item.label)}">${item.html}</button>`;
    }).join('');
  }
  const toolbarHtml = buildToolbarHtml();

  function getSections() {
    return sectionsContainer ? Array.from(sectionsContainer.querySelectorAll(':scope > [data-kb-section]')) : [];
  }

  function hasMeaningfulContent(html) {
    if (!html) {
      return false;
    }
    const temp = document.createElement('div');
    setSanitizedHtml(temp, html);
    const text = (temp.textContent || '').replace(/ /g, ' ').trim();
    if (text) {
      return true;
    }
    return Boolean(temp.querySelector('img, video, audio, iframe, table, hr'));
  }

  function sectionSnippet(editor) {
    // Pad block boundaries so "Step one.Step two" reads as separate sentences.
    const temp = document.createElement('div');
    setSanitizedHtml(temp, editor.innerHTML.replace(/<\/(p|li|h\d|div|pre|blockquote|kb-if|td)>|<br\s*\/?>/gi, '$& '));
    const text = (temp.textContent || '').replace(/\s+/g, ' ').trim();
    if (text) {
      return text.length > 160 ? `${text.slice(0, 160)}…` : text;
    }
    return editor.querySelector('img') ? '[image]' : 'Empty section';
  }

  function readSectionCompanyIds(section) {
    try {
      const ids = JSON.parse(section.dataset.kbSectionCompanyIds || '[]');
      return Array.isArray(ids) ? ids.map(Number).filter(Number.isFinite) : [];
    } catch (error) {
      return [];
    }
  }

  function updateAccessChip(section) {
    const chip = $('[data-kb-section-access]', section);
    if (!chip) {
      return;
    }
    const ids = readSectionCompanyIds(section);
    const label = $('.kbe-chip__label', chip);
    if (!ids.length) {
      label.textContent = 'All companies';
      chip.classList.remove('kbe-chip--restricted');
      chip.title = 'Visible to every company that can read the article. Click to restrict.';
      return;
    }
    const names = ids.map((id) => {
      const company = companyOptions.find((item) => Number(item.id) === id);
      return company ? company.name : `Company #${id}`;
    });
    label.textContent = names.length === 1 ? names[0] : `${names.length} companies`;
    chip.classList.add('kbe-chip--restricted');
    chip.title = `Only visible to: ${names.join(', ')}`;
  }

  function updateSectionChrome() {
    const sections = getSections();
    sections.forEach((section, index) => {
      const number = $('.kbe-section__num', section);
      if (number) {
        number.textContent = String(index + 1);
      }
      const up = $('[data-kb-section-up]', section);
      const down = $('[data-kb-section-down]', section);
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === sections.length - 1;
      const remove = $('[data-kb-section-delete]', section);
      if (remove) remove.disabled = sections.length === 1;
    });
    if (sectionCount) {
      sectionCount.textContent = String(sections.length);
    }
  }

  function setCollapsed(section, collapsed) {
    section.classList.toggle('is-collapsed', collapsed);
    const toggle = $('[data-kb-section-toggle]', section);
    if (toggle) {
      toggle.setAttribute('aria-expanded', String(!collapsed));
      toggle.title = collapsed ? 'Expand section' : 'Collapse section';
    }
    const snippet = $('.kbe-section__snippet', section);
    const editor = $('[data-kb-section-editor]', section);
    if (collapsed && snippet && editor) {
      snippet.textContent = sectionSnippet(editor);
    }
    if (collapsed && imageResize.activeEditor === editor) {
      hideImageOverlay();
    }
  }

  function createSectionElement(section) {
    const wrapper = document.createElement('article');
    wrapper.className = 'kbe-section';
    wrapper.dataset.kbSection = 'true';
    wrapper.dataset.kbSectionCompanyIds = JSON.stringify(section && section.allowed_company_ids ? section.allowed_company_ids : []);
    wrapper.innerHTML = `
      <header class="kbe-section__bar">
        <span class="kbe-section__grip" data-kb-drag-handle title="Drag to reorder (or Alt+↑/↓)" aria-hidden="true">${ICONS.grip}</span>
        <button type="button" class="kbe-icon kbe-section__toggle" data-kb-section-toggle aria-expanded="true" title="Collapse section">${ICONS.chevron}</button>
        <span class="kbe-section__num" aria-hidden="true"></span>
        <input type="text" class="kbe-section__heading" data-kb-section-heading maxlength="255" placeholder="Section heading (optional)" aria-label="Section heading" />
        <button type="button" class="kbe-chip" data-kb-section-access>${ICONS.company}<span class="kbe-chip__label"></span></button>
        <div class="kbe-section__actions">
          <button type="button" class="kbe-icon" data-kb-section-up title="Move up" aria-label="Move section up">${ICONS.up}</button>
          <button type="button" class="kbe-icon" data-kb-section-down title="Move down" aria-label="Move section down">${ICONS.down}</button>
          <button type="button" class="kbe-icon" data-kb-section-insert title="Insert section below" aria-label="Insert section below">${ICONS.insert}</button>
          <button type="button" class="kbe-icon" data-kb-section-duplicate title="Duplicate section" aria-label="Duplicate section">${ICONS.copy}</button>
          <button type="button" class="kbe-icon kbe-icon--danger" data-kb-section-delete title="Remove section" aria-label="Remove section">${ICONS.trash}</button>
        </div>
      </header>
      <p class="kbe-section__snippet" data-kb-section-snippet></p>
      <div class="kbe-section__body">
        <div class="kbe-section__toolbar" role="toolbar" aria-label="Formatting">${toolbarHtml}</div>
        <div class="kbe-section__editor knowledge-base__body" contenteditable="true" data-kb-section-editor role="textbox" aria-multiline="true" aria-label="Section content"></div>
      </div>`;
    const heading = $('[data-kb-section-heading]', wrapper);
    heading.value = section && section.heading ? section.heading : '';
    const editor = $('[data-kb-section-editor]', wrapper);
    setSanitizedHtml(editor, section && section.content ? section.content : '<p><br></p>');
    initialiseSectionEditor(editor);
    updateAccessChip(wrapper);
    return wrapper;
  }

  function renderSections(sections) {
    if (!sectionsContainer) {
      return;
    }
    sectionsContainer.querySelectorAll('[data-kb-section-editor]').forEach(destroySectionEditor);
    sectionsContainer.innerHTML = '';
    const list = sections && sections.length ? sections : [{ heading: '', content: '<p><br></p>' }];
    list.forEach((section) => sectionsContainer.appendChild(createSectionElement(section)));
    // Long articles open collapsed so their structure is visible at a glance.
    if (list.length > 4) {
      getSections().forEach((section) => setCollapsed(section, true));
    }
    updateSectionChrome();
  }

  function addSection(section, after) {
    const element = createSectionElement(section || { heading: '', content: '<p><br></p>' });
    if (after && after.parentNode === sectionsContainer) {
      after.insertAdjacentElement('afterend', element);
    } else {
      sectionsContainer.appendChild(element);
    }
    updateSectionChrome();
    markDirty();
    $('[data-kb-section-heading]', element).focus();
    element.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    return element;
  }

  function moveSection(section, offset) {
    const sections = getSections();
    const index = sections.indexOf(section);
    const target = index + offset;
    if (index === -1 || target < 0 || target >= sections.length) {
      return;
    }
    if (offset < 0) {
      sectionsContainer.insertBefore(section, sections[target]);
    } else {
      sectionsContainer.insertBefore(section, sections[target].nextSibling);
    }
    updateSectionChrome();
    markDirty();
    flash(section);
  }

  function flash(section) {
    section.classList.remove('is-moved');
    // Force reflow so the animation restarts.
    void section.offsetWidth;
    section.classList.add('is-moved');
  }

  function removeSection(section) {
    const editor = $('[data-kb-section-editor]', section);
    const heading = $('[data-kb-section-heading]', section);
    const hasContent = (heading && heading.value.trim()) || (editor && hasMeaningfulContent(editor.innerHTML));
    if (hasContent && !window.confirm('Remove this section and its content?')) {
      return;
    }
    destroySectionEditor(editor);
    section.remove();
    if (!getSections().length) {
      addSection();
    }
    updateSectionChrome();
    markDirty();
  }

  function duplicateSection(section) {
    const editor = $('[data-kb-section-editor]', section);
    const heading = $('[data-kb-section-heading]', section);
    const copy = addSection({
      heading: heading && heading.value ? `${heading.value} (copy)` : '',
      content: editor ? cleanEditorHtml(editor) : '',
      allowed_company_ids: readSectionCompanyIds(section),
    }, section);
    flash(copy);
  }

  function cleanEditorHtml(editor) {
    const clone = editor.cloneNode(true);
    clone.querySelectorAll('img').forEach((image) => {
      image.removeAttribute('data-kb-image-decorated');
      image.removeAttribute('draggable');
    });
    return sanitizeRichHtml(clone.innerHTML.trim());
  }

  function collectSectionsFromDom() {
    const sections = [];
    getSections().forEach((section) => {
      const heading = ($('[data-kb-section-heading]', section).value || '').trim();
      const content = cleanEditorHtml($('[data-kb-section-editor]', section));
      if (!hasMeaningfulContent(content)) {
        return;
      }
      sections.push({
        heading: heading || null,
        content,
        allowed_company_ids: readSectionCompanyIds(section),
      });
    });
    return sections;
  }

  function composeSectionsHtml(sections) {
    return sections
      .map((section, index) => {
        const heading = section.heading ? `<h2>${escapeHtml(section.heading)}</h2>` : '';
        return `<section class="kb-article__section" data-section-index="${index + 1}">${heading}${section.content}</section>`;
      })
      .join('');
  }

  // Drag and drop: sections only become draggable while the grip is held so
  // text selection inside the editor keeps working normally.
  let dragged = null;
  let dropMarker = null;

  function ensureDropMarker() {
    if (!dropMarker) {
      dropMarker = document.createElement('div');
      dropMarker.className = 'kbe-drop-marker';
    }
    return dropMarker;
  }

  function dropReference(clientY) {
    return getSections().find((section) => {
      if (section === dragged) {
        return false;
      }
      const rect = section.getBoundingClientRect();
      return clientY < rect.top + rect.height / 2;
    }) || null;
  }

  if (sectionsContainer) {
    sectionsContainer.addEventListener('pointerdown', (event) => {
      const handle = event.target.closest('[data-kb-drag-handle]');
      if (handle) {
        handle.closest('[data-kb-section]').draggable = true;
      }
    });
    sectionsContainer.addEventListener('pointerup', () => {
      getSections().forEach((section) => { if (section !== dragged) section.draggable = false; });
    });
    sectionsContainer.addEventListener('dragstart', (event) => {
      const section = event.target.closest && event.target.closest('[data-kb-section]');
      if (!section || !section.draggable) {
        return;
      }
      dragged = section;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', 'kb-section');
      window.requestAnimationFrame(() => section.classList.add('is-dragging'));
      sectionsContainer.classList.add('is-sorting');
      hideImageOverlay();
    });
    sectionsContainer.addEventListener('dragover', (event) => {
      if (!dragged) {
        return;
      }
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      const reference = dropReference(event.clientY);
      const marker = ensureDropMarker();
      if (reference) {
        sectionsContainer.insertBefore(marker, reference);
      } else {
        sectionsContainer.appendChild(marker);
      }
    });
    sectionsContainer.addEventListener('drop', (event) => {
      if (!dragged) {
        return;
      }
      event.preventDefault();
      const marker = ensureDropMarker();
      if (marker.parentNode === sectionsContainer) {
        const before = marker.nextSibling;
        sectionsContainer.insertBefore(dragged, before);
      }
      const moved = dragged;
      endDrag();
      updateSectionChrome();
      markDirty();
      flash(moved);
    });
    sectionsContainer.addEventListener('dragend', endDrag);
  }

  function endDrag() {
    if (dragged) {
      dragged.classList.remove('is-dragging');
      dragged.draggable = false;
    }
    dragged = null;
    if (dropMarker && dropMarker.parentNode) {
      dropMarker.remove();
    }
    if (sectionsContainer) {
      sectionsContainer.classList.remove('is-sorting');
    }
  }

  // ---------------------------------------------------------------------------
  // Rich text editing
  // ---------------------------------------------------------------------------

  const editorObservers = new WeakMap();
  const imageResize = {
    overlay: null,
    handle: null,
    activeImage: null,
    activeEditor: null,
    pointerId: null,
    startWidth: 0,
    startX: 0,
    maxWidth: Infinity,
    minWidth: 32,
  };

  function ensureImageOverlay() {
    if (imageResize.overlay) {
      return imageResize.overlay;
    }
    const overlay = document.createElement('div');
    overlay.className = 'kb-admin__image-overlay';
    overlay.hidden = true;
    overlay.setAttribute('aria-hidden', 'true');
    const handle = document.createElement('button');
    handle.type = 'button';
    handle.className = 'kb-admin__image-overlay-handle';
    handle.setAttribute('aria-label', 'Resize image');
    handle.tabIndex = -1;
    overlay.appendChild(handle);
    document.body.appendChild(overlay);
    imageResize.overlay = overlay;
    imageResize.handle = handle;
    handle.addEventListener('pointerdown', startResizeSession);
    handle.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
    });
    return overlay;
  }

  function hideImageOverlay() {
    if (imageResize.overlay) {
      imageResize.overlay.hidden = true;
    }
    imageResize.activeImage = null;
    imageResize.activeEditor = null;
    imageResize.pointerId = null;
  }

  function positionImageOverlay() {
    if (!imageResize.overlay || imageResize.overlay.hidden || !imageResize.activeImage) {
      return;
    }
    const image = imageResize.activeImage;
    if (!document.body.contains(image)) {
      hideImageOverlay();
      return;
    }
    const rect = image.getBoundingClientRect();
    const overlay = imageResize.overlay;
    overlay.style.width = `${rect.width}px`;
    overlay.style.height = `${rect.height}px`;
    overlay.style.left = `${Math.round(window.scrollX + rect.left)}px`;
    overlay.style.top = `${Math.round(window.scrollY + rect.top)}px`;
  }

  function showImageOverlay(editor, image) {
    const overlay = ensureImageOverlay();
    imageResize.activeImage = image;
    imageResize.activeEditor = editor;
    overlay.hidden = false;
    positionImageOverlay();
  }

  function startResizeSession(event) {
    const image = imageResize.activeImage;
    const editor = imageResize.activeEditor;
    if (!image || !editor || !editor.contains(image)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const imageRect = image.getBoundingClientRect();
    const editorRect = editor.getBoundingClientRect();
    imageResize.pointerId = event.pointerId;
    imageResize.startWidth = imageRect.width;
    imageResize.startX = event.clientX;
    const availableWidth = Math.max(32, editorRect.right - imageRect.left - 16);
    imageResize.maxWidth = Math.max(32, Math.min(availableWidth, image.naturalWidth || Infinity));
    imageResize.minWidth = Math.min(Math.max(32, Math.min(48, imageRect.width)), imageResize.maxWidth);
    image.style.height = 'auto';
    document.addEventListener('pointermove', handleResizePointerMove);
    document.addEventListener('pointerup', handleResizePointerEnd);
    document.addEventListener('pointercancel', handleResizePointerEnd);
  }

  function handleResizePointerMove(event) {
    if (imageResize.pointerId != null && event.pointerId !== imageResize.pointerId) {
      return;
    }
    const image = imageResize.activeImage;
    if (!image) {
      return;
    }
    event.preventDefault();
    const width = Math.max(imageResize.minWidth, Math.min(imageResize.maxWidth, imageResize.startWidth + event.clientX - imageResize.startX));
    image.style.width = `${Math.round(width)}px`;
    image.style.maxWidth = '100%';
    positionImageOverlay();
  }

  function handleResizePointerEnd(event) {
    if (imageResize.pointerId != null && event.pointerId !== imageResize.pointerId) {
      return;
    }
    document.removeEventListener('pointermove', handleResizePointerMove);
    document.removeEventListener('pointerup', handleResizePointerEnd);
    document.removeEventListener('pointercancel', handleResizePointerEnd);
    imageResize.pointerId = null;
    if (event.type === 'pointerup') {
      markDirty();
    }
    positionImageOverlay();
  }

  function decorateEditorImages(editor) {
    editor.querySelectorAll('img').forEach((image) => {
      if (image.dataset.kbImageDecorated === 'true') {
        return;
      }
      image.dataset.kbImageDecorated = 'true';
      image.setAttribute('draggable', 'false');
      if (!image.style.maxWidth) {
        image.style.maxWidth = '100%';
      }
      image.style.height = image.style.height || 'auto';
      image.addEventListener('load', () => {
        if (imageResize.activeImage === image) {
          positionImageOverlay();
        }
      });
    });
  }

  const UPLOADING_PLACEHOLDER = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="160" height="90"%3E%3Crect fill="%231b2533" width="160" height="90"/%3E%3Ctext x="80" y="45" text-anchor="middle" dy=".3em" fill="%239fb0c4" font-family="sans-serif" font-size="12"%3EUploading…%3C/text%3E%3C/svg%3E';

  function insertNodeAtSelection(editor, node) {
    const selection = window.getSelection();
    if (selection && selection.rangeCount > 0 && editor.contains(selection.getRangeAt(0).commonAncestorContainer)) {
      const range = selection.getRangeAt(0);
      range.deleteContents();
      range.insertNode(node);
      range.setStartAfter(node);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
    } else {
      editor.appendChild(node);
    }
  }

  async function uploadImageInto(editor, file) {
    if (!file || !/^image\//.test(file.type)) {
      return;
    }
    const placeholder = document.createElement('img');
    placeholder.src = UPLOADING_PLACEHOLDER;
    placeholder.alt = 'Uploading…';
    placeholder.style.maxWidth = '100%';
    insertNodeAtSelection(editor, placeholder);
    try {
      const body = new FormData();
      body.append('file', file);
      const result = await api('/api/knowledge-base/upload-image', { method: 'POST', body });
      if (!result || !result.url) {
        throw new Error('Upload did not return an image URL');
      }
      placeholder.src = result.url;
      placeholder.alt = '';
      decorateEditorImages(editor);
      markDirty();
    } catch (error) {
      placeholder.remove();
      setStatus(`Image upload failed: ${error.message}`, 'error', 6000);
    }
  }

  function initialiseSectionEditor(editor) {
    if (!editor || editor.dataset.kbEditorReady === 'true') {
      return;
    }
    editor.dataset.kbEditorReady = 'true';
    decorateEditorImages(editor);
    editor.addEventListener('click', (event) => {
      const image = event.target.closest('img');
      if (image && editor.contains(image)) {
        showImageOverlay(editor, image);
      } else if (imageResize.activeEditor === editor) {
        hideImageOverlay();
      }
    });
    editor.addEventListener('input', markDirty);
    editor.addEventListener('paste', (event) => {
      const items = Array.from((event.clipboardData && event.clipboardData.items) || []);
      const images = items.filter((item) => item.type.indexOf('image') !== -1);
      if (!images.length) {
        return;
      }
      event.preventDefault();
      images.forEach((item) => uploadImageInto(editor, item.getAsFile()));
    });
    editor.addEventListener('dragover', (event) => {
      if (!dragged && event.dataTransfer && Array.from(event.dataTransfer.types || []).includes('Files')) {
        event.preventDefault();
      }
    });
    editor.addEventListener('drop', (event) => {
      if (dragged) {
        return;
      }
      const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []).filter((file) => /^image\//.test(file.type));
      if (!files.length) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      if (document.caretRangeFromPoint) {
        const range = document.caretRangeFromPoint(event.clientX, event.clientY);
        if (range) {
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
        }
      }
      files.forEach((file) => uploadImageInto(editor, file));
    });
    const observer = new MutationObserver(() => {
      decorateEditorImages(editor);
      if (imageResize.activeImage && !editor.contains(imageResize.activeImage)) {
        hideImageOverlay();
      } else {
        positionImageOverlay();
      }
    });
    observer.observe(editor, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'src'] });
    editorObservers.set(editor, observer);
  }

  function destroySectionEditor(editor) {
    if (!editor) {
      return;
    }
    const observer = editorObservers.get(editor);
    if (observer) {
      observer.disconnect();
      editorObservers.delete(editor);
    }
    if (imageResize.activeEditor === editor) {
      hideImageOverlay();
    }
  }

  function normaliseUrl(raw) {
    let url = (raw || '').trim();
    if (!url) {
      return null;
    }
    if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(url) && !url.startsWith('/')) {
      url = `https://${url}`;
    }
    if (!/^(https?:|mailto:|\/)/i.test(url)) {
      return null;
    }
    return url;
  }

  const imagePicker = document.createElement('input');
  imagePicker.type = 'file';
  imagePicker.accept = 'image/*';
  imagePicker.hidden = true;
  document.body.appendChild(imagePicker);
  let imagePickerTarget = null;
  let imagePickerRange = null;
  imagePicker.addEventListener('change', () => {
    const file = imagePicker.files && imagePicker.files[0];
    const editor = imagePickerTarget;
    if (file && editor) {
      editor.focus();
      if (imagePickerRange) {
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(imagePickerRange);
      }
      uploadImageInto(editor, file);
    }
    imagePicker.value = '';
    imagePickerTarget = null;
    imagePickerRange = null;
  });

  function runCommand(editor, command, value) {
    editor.focus();
    if (command === 'createLink') {
      const selection = window.getSelection();
      const existing = selection && selection.anchorNode && selection.anchorNode.parentElement
        ? selection.anchorNode.parentElement.closest('a')
        : null;
      const url = window.prompt('Link URL (leave empty to remove the link)', existing ? existing.getAttribute('href') : 'https://');
      if (url === null) {
        return;
      }
      if (!url.trim() || url.trim() === 'https://') {
        document.execCommand('unlink');
        markDirty();
        return;
      }
      const safeUrl = normaliseUrl(url);
      if (!safeUrl) {
        setStatus('Only http(s), mailto, or site-relative links are allowed.', 'error', 5000);
        return;
      }
      if (selection && selection.isCollapsed && !existing) {
        const anchor = document.createElement('a');
        anchor.href = safeUrl;
        anchor.textContent = safeUrl;
        insertNodeAtSelection(editor, anchor);
      } else {
        document.execCommand('createLink', false, safeUrl);
      }
      editor.querySelectorAll('a[href]').forEach((anchor) => {
        if (/^https?:/i.test(anchor.getAttribute('href') || '')) {
          anchor.target = '_blank';
          anchor.rel = 'noopener';
        }
      });
    } else if (command === 'insertImage') {
      const selection = window.getSelection();
      imagePickerRange = selection && selection.rangeCount ? selection.getRangeAt(0).cloneRange() : null;
      imagePickerTarget = editor;
      imagePicker.click();
      return;
    } else if (command === 'inlineCode') {
      const selection = window.getSelection();
      const text = selection ? selection.toString() : '';
      document.execCommand('insertHTML', false, `<code>${escapeHtml(text || 'code')}</code>`);
    } else if (command === 'insertConditional') {
      const names = companyOptions.map((company) => company.name).filter(Boolean);
      const hint = names.length ? ` (e.g. ${names.slice(0, 3).join(', ')})` : '';
      const companyName = window.prompt(`Show this content only to which company?${hint}`);
      if (!companyName || !companyName.trim()) {
        return;
      }
      const selectedText = window.getSelection().toString();
      const safeName = escapeHtml(companyName.trim());
      document.execCommand('insertHTML', false, `<kb-if company="${safeName}">${escapeHtml(selectedText) || `Content for ${safeName}`}</kb-if><p><br></p>`);
    } else if (command === 'formatBlock') {
      const current = (document.queryCommandValue('formatBlock') || '').toLowerCase();
      const target = (value || 'p').toLowerCase();
      document.execCommand('formatBlock', false, current === target ? 'p' : target);
    } else {
      document.execCommand(command, false, value || null);
    }
    markDirty();
  }

  // ---------------------------------------------------------------------------
  // Section access dialog
  // ---------------------------------------------------------------------------

  let accessTarget = null;

  function openAccessDialog(section) {
    if (!accessDialog) {
      return;
    }
    accessTarget = section;
    const selected = new Set(readSectionCompanyIds(section));
    const list = $('[data-kb-access-list]', accessDialog);
    const filter = $('[data-kb-access-filter]', accessDialog);
    filter.value = '';
    list.textContent = '';
    if (companyOptions.length) {
      companyOptions.forEach((company) => {
        const labelNode = document.createElement('label');
        labelNode.className = 'kbe-pick__option';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = String(company.id);
        checkbox.checked = selected.has(Number(company.id));
        const text = document.createElement('span');
        text.textContent = company.name || `Company ${company.id}`;
        labelNode.append(checkbox, text);
        list.appendChild(labelNode);
      });
    } else {
      const empty = document.createElement('p');
      empty.className = 'kbe-pick__empty';
      empty.textContent = 'No companies available.';
      list.appendChild(empty);
    }
    accessDialog.showModal();
    filter.focus();
  }

  if (accessDialog) {
    const list = $('[data-kb-access-list]', accessDialog);
    $('[data-kb-access-filter]', accessDialog).addEventListener('input', (event) => {
      const term = event.target.value.trim().toLowerCase();
      list.querySelectorAll('.kbe-pick__option').forEach((option) => {
        option.hidden = Boolean(term) && !option.textContent.toLowerCase().includes(term);
      });
    });
    $('[data-kb-access-clear]', accessDialog).addEventListener('click', () => {
      list.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => { checkbox.checked = false; });
    });
    $('[data-kb-access-apply]', accessDialog).addEventListener('click', () => {
      if (accessTarget) {
        const ids = Array.from(list.querySelectorAll('input:checked')).map((checkbox) => Number(checkbox.value));
        accessTarget.dataset.kbSectionCompanyIds = JSON.stringify(ids);
        updateAccessChip(accessTarget);
        markDirty();
      }
      accessDialog.close();
    });
    accessDialog.addEventListener('close', () => { accessTarget = null; });
  }

  document.querySelectorAll('[data-kb-dialog-close]').forEach((button) => {
    button.addEventListener('click', () => button.closest('dialog').close());
  });
  document.querySelectorAll('dialog.kbe-dialog').forEach((dialog) => {
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) {
        dialog.close();
      }
    });
  });

  // ---------------------------------------------------------------------------
  // AI tags
  // ---------------------------------------------------------------------------

  function renderAiTags(tags, manualTags) {
    if (!aiTagsContainer) {
      return;
    }
    const generatedTags = Array.isArray(tags) ? tags : [];
    const addedTags = Array.isArray(manualTags) ? manualTags : [];
    if (aiTagsCount) {
      const total = generatedTags.length + addedTags.length;
      aiTagsCount.textContent = total ? String(total) : '';
    }
    aiTagsContainer.textContent = '';
    if (generatedTags.length === 0 && addedTags.length === 0) {
      const empty = document.createElement('span');
      empty.className = 'kbe__muted';
      empty.textContent = 'No tags yet — they are generated after saving.';
      aiTagsContainer.appendChild(empty);
    }
    generatedTags.forEach((tag) => {
      const chip = document.createElement('span');
      chip.className = 'tag tag--removable';
      chip.dataset.tagValue = tag;
      chip.append(document.createTextNode(tag));
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'tag__remove';
      remove.dataset.tagAction = 'remove';
      remove.setAttribute('aria-label', `Remove tag ${tag}`);
      remove.title = 'Remove from this article';
      remove.textContent = '×';
      const exclude = document.createElement('button');
      exclude.type = 'button';
      exclude.className = 'tag__exclude';
      exclude.dataset.tagAction = 'exclude';
      exclude.setAttribute('aria-label', `Exclude tag ${tag} everywhere`);
      exclude.title = 'Remove and exclude from future use';
      exclude.textContent = '⊘';
      chip.append(remove, exclude);
      aiTagsContainer.appendChild(chip);
    });
    addedTags.forEach((tag) => {
      const chip = document.createElement('span');
      chip.className = 'tag tag--manual';
      chip.dataset.manualTagValue = tag;
      chip.title = 'Manually added tag';
      chip.append(document.createTextNode(tag));
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'tag__remove';
      remove.dataset.tagAction = 'remove-manual';
      remove.setAttribute('aria-label', `Remove manual tag ${tag}`);
      remove.title = 'Remove manual tag';
      remove.textContent = '×';
      chip.appendChild(remove);
      aiTagsContainer.appendChild(chip);
    });
    const addWrap = document.createElement('div');
    addWrap.className = 'kb-admin__manual-tag-form';
    addWrap.dataset.kbManualTagForm = '';
    const addInput = document.createElement('input');
    addInput.className = 'form-input form-input--sm';
    addInput.type = 'text';
    addInput.name = 'manual_tag';
    addInput.maxLength = 48;
    addInput.placeholder = 'Add keyword';
    addInput.setAttribute('aria-label', 'Add manual AI tag');
    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'kbe-btn';
    addButton.dataset.kbManualTagAdd = '';
    addButton.textContent = 'Add';
    addWrap.append(addInput, addButton);
    aiTagsContainer.appendChild(addWrap);

    const manualTagWrapper = aiTagsContainer.querySelector('[data-kb-manual-tag-form]');
    const manualTagInput = manualTagWrapper.querySelector('input[name="manual_tag"]');
    const manualTagButton = manualTagWrapper.querySelector('[data-kb-manual-tag-add]');
    const submitManualTag = async () => {
      const value = manualTagInput.value.trim();
      if (!value) {
        return;
      }
      await addManualTag(value);
      manualTagInput.value = '';
      manualTagInput.focus();
    };
    manualTagButton.addEventListener('click', submitManualTag);
    manualTagInput.addEventListener('keydown', async (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        await submitManualTag();
      }
    });
  }

  if (aiTagsContainer) {
    aiTagsContainer.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-tag-action]');
      if (!button) {
        return;
      }
      const action = button.dataset.tagAction;
      if (action === 'remove-manual') {
        await removeManualTag(button.closest('[data-manual-tag-value]').dataset.manualTagValue);
        return;
      }
      const tagValue = button.closest('[data-tag-value]').dataset.tagValue;
      if (action === 'exclude' && !window.confirm(`Remove "${tagValue}" and add it to the shared exclusion list?`)) {
        return;
      }
      await removeTag(tagValue, action === 'exclude');
    });
  }

  async function removeTag(tagSlug, excludeGlobally) {
    if (!state.activeId) {
      return;
    }
    try {
      const result = await api(`/api/tag-exclusions/knowledge-base/${state.activeId}/remove-tag`, {
        method: 'POST',
        json: { tag_slug: tagSlug, exclude_globally: Boolean(excludeGlobally) },
      });
      state.aiTags = (result && result.remaining_tags) || [];
      renderAiTags(state.aiTags, state.manualAiTags);
      setStatus(excludeGlobally ? 'Tag removed and excluded everywhere.' : 'Tag removed.', 'success', 3000);
    } catch (error) {
      setStatus(`Unable to remove tag: ${error.message}`, 'error', 6000);
    }
  }

  async function addManualTag(tagSlug) {
    if (!state.activeId || !tagSlug) {
      return;
    }
    try {
      const result = await api(`/api/knowledge-base/articles/${state.activeId}/manual-ai-tags`, {
        method: 'POST',
        json: { tag_slug: tagSlug },
      });
      state.aiTags = (result && result.ai_tags) || state.aiTags;
      state.manualAiTags = (result && result.manual_ai_tags) || [];
      renderAiTags(state.aiTags, state.manualAiTags);
      setStatus('Tag added.', 'success', 3000);
    } catch (error) {
      setStatus(`Unable to add tag: ${error.message}`, 'error', 6000);
    }
  }

  async function removeManualTag(tagSlug) {
    if (!state.activeId || !tagSlug) {
      return;
    }
    try {
      const result = await api(`/api/knowledge-base/articles/${state.activeId}/manual-ai-tags/${encodeURIComponent(tagSlug)}`, { method: 'DELETE' });
      state.manualAiTags = (result && result.manual_ai_tags) || [];
      renderAiTags(state.aiTags, state.manualAiTags);
      setStatus('Tag removed.', 'success', 3000);
    } catch (error) {
      setStatus(`Unable to remove tag: ${error.message}`, 'error', 6000);
    }
  }

  async function refreshAiTags() {
    if (!state.activeId) {
      return;
    }
    refreshTagsButton.disabled = true;
    try {
      await api(`/api/knowledge-base/articles/${state.activeId}/refresh-ai-tags`, { method: 'POST' });
      setStatus('Tag regeneration queued…', 'info');
      window.setTimeout(reloadTagsOnly, 4000);
    } catch (error) {
      setStatus(`Unable to regenerate tags: ${error.message}`, 'error', 6000);
    } finally {
      refreshTagsButton.disabled = false;
    }
  }

  async function reloadTagsOnly() {
    if (!state.activeSlug) {
      return;
    }
    try {
      const article = await fetchArticle(state.activeSlug);
      state.aiTags = article.ai_tags || [];
      state.manualAiTags = article.manual_ai_tags || [];
      renderAiTags(state.aiTags, state.manualAiTags);
      setStatus(state.dirty ? 'Unsaved changes' : 'Tags updated.', state.dirty ? 'dirty' : 'success', state.dirty ? 0 : 3000);
    } catch (error) {
      console.error(error);
    }
  }

  // ---------------------------------------------------------------------------
  // Attachments, preview, versions
  // ---------------------------------------------------------------------------

  function renderAttachments(attachments) {
    if (!attachmentsElement) {
      return;
    }
    const articleId = Number.parseInt(state.activeId, 10);
    if (!Number.isFinite(articleId)) {
      attachmentsElement.textContent = '';
      return;
    }
    if (attachmentCount) {
      attachmentCount.textContent = attachments.length ? String(attachments.length) : '';
    }
    if (!attachments.length) {
      attachmentsElement.textContent = '';
      const empty = document.createElement('p');
      empty.className = 'kbe__muted';
      empty.textContent = 'No attachments.';
      attachmentsElement.appendChild(empty);
      return;
    }
    attachmentsElement.textContent = '';
    attachments.forEach((item) => {
      const attachmentId = Number.parseInt(item.id, 10);
      if (!Number.isFinite(attachmentId)) {
        return;
      }
      const size = Number(item.file_size || 0);
      const sizeLabel = size >= 1048576 ? `${(size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.ceil(size / 1024))} KB`;
      const row = document.createElement('div');
      row.className = 'kbe__attachment';
      const link = document.createElement('a');
      const attachmentUrl = new URL(`/api/knowledge-base/articles/${articleId}/attachments/${attachmentId}`, window.location.origin);
      link.href = attachmentUrl.toString();
      link.title = item.file_name || '';
      link.textContent = item.file_name || '';
      const sizeNode = document.createElement('span');
      sizeNode.className = 'kbe__muted';
      sizeNode.textContent = sizeLabel;
      const remove = document.createElement('button');
      remove.className = 'kbe-icon kbe-icon--danger';
      remove.type = 'button';
      remove.dataset.kbDeleteAttachment = String(attachmentId);
      remove.setAttribute('aria-label', `Remove ${item.file_name || 'attachment'}`);
      remove.title = 'Remove';
      remove.textContent = '×';
      row.append(link, sizeNode, remove);
      attachmentsElement.appendChild(row);
    });
  }

  async function reloadAttachments() {
    const article = await fetchArticle(state.activeSlug);
    renderAttachments(article.attachments || []);
  }

  async function uploadAttachment() {
    if (!state.activeId || !attachmentFile.files.length) {
      return;
    }
    const body = new FormData();
    body.append('file', attachmentFile.files[0]);
    setStatus('Uploading attachment…', 'info');
    try {
      await api(`/api/knowledge-base/articles/${state.activeId}/attachments`, { method: 'POST', body });
      await reloadAttachments();
      setStatus('Attachment uploaded.', 'success', 3000);
    } finally {
      attachmentFile.value = '';
    }
  }

  function openReview(title) {
    reviewTitle.textContent = title;
    versionList.hidden = true;
    versionList.textContent = '';
    reviewContent.textContent = '';
    const loading = document.createElement('p');
    loading.className = 'kbe__muted';
    loading.textContent = 'Loading…';
    reviewContent.appendChild(loading);
    if (!reviewDialog.open) {
      reviewDialog.showModal();
    }
  }

  async function showCustomerPreview() {
    const companyQuery = previewCompany.value ? `?company_id=${encodeURIComponent(previewCompany.value)}` : '';
    const companyLabel = previewCompany.value ? previewCompany.selectedOptions[0].textContent : 'an anonymous customer';
    openReview(`Preview as ${companyLabel}`);
    reviewContent.textContent = '';
    if (state.dirty) {
      const warn = document.createElement('div');
      warn.className = 'alert alert--warning';
      warn.textContent = 'Showing the last saved version — save to preview your latest changes.';
      reviewContent.appendChild(warn);
    }
    try {
      const article = await api(`/api/knowledge-base/articles/${encodeURIComponent(state.activeSlug)}/customer-preview${companyQuery}`);
      const title = document.createElement('h1');
      title.textContent = article.title || '';
      reviewContent.appendChild(title);
      if (article.summary) {
        const summary = document.createElement('p');
        summary.className = 'kbe__muted';
        summary.textContent = article.summary;
        reviewContent.appendChild(summary);
      }
      if (Array.isArray(article.sections) && article.sections.length) {
        article.sections.forEach((section) => {
          const sectionNode = document.createElement('section');
          sectionNode.className = 'knowledge-base__section';
          if (section.heading) {
            const heading = document.createElement('h2');
            heading.className = 'knowledge-base__section-heading';
            heading.textContent = section.heading;
            sectionNode.appendChild(heading);
          }
          const contentNode = document.createElement('div');
          contentNode.className = 'knowledge-base__section-content';
          setSanitizedHtml(contentNode, section.content || '');
          sectionNode.appendChild(contentNode);
          reviewContent.appendChild(sectionNode);
        });
      } else {
        const contentNode = document.createElement('div');
        setSanitizedHtml(contentNode, article.content || '');
        reviewContent.appendChild(contentNode);
      }
    } catch (error) {
      const warning = document.createElement('div');
      warning.className = 'alert alert--warning';
      warning.textContent = 'This article is not visible to that customer. It may be unpublished or restricted.';
      reviewContent.appendChild(warning);
    }
  }

  async function showVersions() {
    openReview('Version history');
    const versions = await api(`/api/knowledge-base/articles/${state.activeId}/versions`);
    if (!versions || !versions.length) {
      reviewContent.textContent = '';
      const empty = document.createElement('p');
      empty.className = 'kbe__muted';
      empty.textContent = 'No earlier versions yet. A version is recorded every time the article is saved.';
      reviewContent.appendChild(empty);
      return;
    }
    versionList.hidden = false;
    versionList.textContent = '';
    versions.forEach((item) => {
      const when = item.created_at ? new Date(item.created_at) : null;
      const label = when && !Number.isNaN(when.getTime()) ? when.toLocaleString() : (item.created_at || '');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'kbe-review__version';
      button.dataset.kbVersion = String(item.version_number);
      const strong = document.createElement('strong');
      strong.textContent = `v${item.version_number}`;
      const span = document.createElement('span');
      span.textContent = label;
      button.append(strong, span);
      versionList.appendChild(button);
    });
    await showVersion(versions[0].version_number);
  }

  async function showVersion(versionNumber) {
    versionList.querySelectorAll('[data-kb-version]').forEach((button) => {
      button.classList.toggle('is-active', button.dataset.kbVersion === String(versionNumber));
    });
    reviewContent.textContent = '';
    const loading = document.createElement('p');
    loading.className = 'kbe__muted';
    loading.textContent = 'Loading…';
    reviewContent.appendChild(loading);
    const version = await api(`/api/knowledge-base/articles/${state.activeId}/versions/${versionNumber}`);
    reviewContent.textContent = '';
    const title = document.createElement('h1');
    title.textContent = version.title || '';
    reviewContent.appendChild(title);
    if (version.summary) {
      const summary = document.createElement('p');
      summary.className = 'kbe__muted';
      summary.textContent = version.summary;
      reviewContent.appendChild(summary);
    }
    const contentNode = document.createElement('div');
    setSanitizedHtml(contentNode, version.content || '');
    reviewContent.appendChild(contentNode);
  }

  // ---------------------------------------------------------------------------
  // Visibility & runbook fields
  // ---------------------------------------------------------------------------

  function updateScopeFields() {
    const scope = scopeField.value || 'anonymous';
    if (scopeHelp) {
      scopeHelp.textContent = scopeHelpMessages[scope] || '';
    }
    const companyScope = scope === 'company' || scope === 'company_admin';
    userFieldWrapper.hidden = scope !== 'user';
    companyFieldWrapper.hidden = !companyScope;
    if (roleFieldWrapper) {
      roleFieldWrapper.hidden = !companyScope;
    }
    if (companyHelp) {
      companyHelp.textContent = companyScope ? 'No selection means every company.' : '';
    }
    updatePanelSummaries();
  }

  function updatePanelSummaries() {
    if (visibilitySummary) {
      const scope = scopeField.value || 'anonymous';
      let detail = scopeLabels[scope] || scope;
      if (scope === 'user') {
        detail += ` · ${userSelect.selectedOptions.length}`;
      } else if ((scope === 'company' || scope === 'company_admin') && companySelect.selectedOptions.length) {
        detail += ` · ${companySelect.selectedOptions.length}`;
      }
      visibilitySummary.textContent = detail;
    }
    if (runbookSummary) {
      const parts = [];
      if (reviewDueField.value) {
        const due = new Date(`${reviewDueField.value}T00:00:00`);
        parts.push(due < new Date() ? 'Review overdue' : `Due ${reviewDueField.value}`);
      }
      if (assetSelect.selectedOptions.length) {
        parts.push(`${assetSelect.selectedOptions.length} asset${assetSelect.selectedOptions.length === 1 ? '' : 's'}`);
      }
      runbookSummary.textContent = parts.join(' · ');
      runbookSummary.classList.toggle('is-warning', parts[0] === 'Review overdue');
    }
    if (lifecycleField) {
      lifecycleField.dataset.status = lifecycleField.value;
    }
  }

  // ---------------------------------------------------------------------------
  // Load, populate, save
  // ---------------------------------------------------------------------------

  async function fetchArticle(slug) {
    return api(`/api/knowledge-base/articles/${encodeURIComponent(slug)}?include_permissions=true`);
  }

  function setExistingArticleUi(article) {
    const exists = Boolean(article && article.id);
    if (aiTagsSection) aiTagsSection.hidden = !exists;
    if (existingTools) existingTools.hidden = !exists;
    if (reviewTools) reviewTools.hidden = !exists;
    if (deleteButton) deleteButton.hidden = !exists;
    if (viewLink) {
      viewLink.hidden = !exists;
      if (exists) {
        viewLink.href = `/knowledge-base/articles/${encodeURIComponent(article.slug)}`;
      }
    }
    if (editorTitle) {
      editorTitle.textContent = exists ? `Editing · updated ${formatRelative(article.updated_at)}` : 'New article';
    }
  }

  function formatRelative(value) {
    if (!value) {
      return 'just now';
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return 'recently';
    }
    const seconds = Math.round((Date.now() - date.getTime()) / 1000);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    return date.toLocaleDateString();
  }

  function populateForm(article, options) {
    const keepSections = options && options.keepSections;
    state.activeSlug = article ? article.slug : null;
    state.activeId = article ? article.id : null;
    state.slugTouched = Boolean(article);
    idField.value = article && article.id != null ? String(article.id) : '';
    slugField.value = (article && article.slug) || '';
    titleField.value = (article && article.title) || '';
    summaryField.value = (article && article.summary) || '';
    autosize(summaryField);

    if (!keepSections) {
      if (article && Array.isArray(article.sections) && article.sections.length) {
        renderSections(article.sections.slice().sort((a, b) => (a.position || 0) - (b.position || 0)));
      } else if (article && article.content) {
        renderSections([{ heading: '', content: article.content }]);
      } else {
        renderSections([]);
      }
    }

    scopeField.value = (article && article.permission_scope) || 'anonymous';
    lifecycleField.value = article
      ? (article.is_published ? 'published' : (article.lifecycle_status === 'published' ? 'draft' : article.lifecycle_status || 'draft'))
      : 'draft';
    ownerField.value = article && article.owner_id != null ? String(article.owner_id) : '';
    reviewDueField.value = article && article.review_due_at ? String(article.review_due_at).slice(0, 10) : '';
    setSelectedValues(assetSelect, (article && article.asset_ids) || []);
    setSelectedValues(userSelect, (article && article.allowed_user_ids) || []);
    const companyIds = article
      ? (article.permission_scope === 'company_admin' ? article.company_admin_ids : article.allowed_company_ids) || []
      : [];
    setSelectedValues(companySelect, companyIds);
    setSelectedValues(roleSelect, (article && article.allowed_role_ids) || []);
    updateScopeFields();

    state.aiTags = (article && article.ai_tags) || [];
    state.manualAiTags = (article && article.manual_ai_tags) || [];
    renderAiTags(state.aiTags, state.manualAiTags);
    renderAttachments((article && article.attachments) || []);
    setExistingArticleUi(article);
    markClean();
  }

  function getPayloadFromForm() {
    const sections = collectSectionsFromDom();
    if (!sections.length) {
      throw new Error('Add some content to at least one section.');
    }
    const scope = scopeField.value;
    const lifecycle = lifecycleField.value;
    const payload = {
      slug: slugField.value.trim(),
      title: titleField.value.trim(),
      summary: summaryField.value.trim() || null,
      content: composeSectionsHtml(sections),
      permission_scope: scope,
      is_published: lifecycle === 'published',
      lifecycle_status: lifecycle,
      sections,
      owner_id: ownerField.value ? Number(ownerField.value) : null,
      review_due_at: reviewDueField.value ? `${reviewDueField.value}T00:00:00Z` : null,
      asset_ids: getSelectedValues(assetSelect),
      allowed_user_ids: scope === 'user' ? getSelectedValues(userSelect) : [],
      allowed_company_ids: scope === 'company' || scope === 'company_admin' ? getSelectedValues(companySelect) : [],
      allowed_role_ids: scope === 'company' || scope === 'company_admin' ? getSelectedValues(roleSelect) : [],
    };
    if (!payload.title) {
      throw new Error('A title is required.');
    }
    if (!payload.slug) {
      payload.slug = slugify(payload.title);
      slugField.value = payload.slug;
    }
    return payload;
  }

  async function submitForm() {
    if (state.saving) {
      return;
    }
    let payload;
    try {
      payload = getPayloadFromForm();
    } catch (error) {
      setStatus(error.message, 'error', 6000);
      if (!titleField.value.trim()) {
        titleField.focus();
      }
      return;
    }
    const isNew = !state.activeId;
    state.saving = true;
    saveButton.disabled = true;
    saveButton.textContent = 'Saving…';
    setStatus('Saving…', 'info');
    try {
      const article = await api(isNew ? '/api/knowledge-base/articles' : `/api/knowledge-base/articles/${state.activeId}`, {
        method: isNew ? 'POST' : 'PUT',
        json: payload,
      });
      // Keep the editor DOM (and the caret) in place; only refresh metadata.
      populateForm(Object.assign({}, article, { allowed_role_ids: payload.allowed_role_ids }), { keepSections: true });
      const editUrl = `/admin/knowledge-base/articles/${encodeURIComponent(article.slug)}`;
      if (window.location.pathname !== editUrl) {
        window.history.replaceState(null, '', editUrl);
      }
      setStatus(isNew ? 'Article created.' : 'Saved.', 'success', 4000);
      if (!isNew) {
        window.setTimeout(reloadTagsOnly, 5000);
      }
    } catch (error) {
      setStatus(`Save failed: ${error.message}`, 'error');
    } finally {
      state.saving = false;
      saveButton.disabled = false;
      saveButton.textContent = 'Save';
    }
  }

  async function deleteArticle() {
    if (!state.activeId || !window.confirm('Delete this article permanently? This cannot be undone.')) {
      return;
    }
    setStatus('Deleting…', 'info');
    try {
      await api(`/api/knowledge-base/articles/${state.activeId}`, { method: 'DELETE' });
      markClean();
      window.location.assign('/admin/knowledge-base');
    } catch (error) {
      setStatus(`Unable to delete: ${error.message}`, 'error');
    }
  }

  // ---------------------------------------------------------------------------
  // Event wiring
  // ---------------------------------------------------------------------------

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    submitForm();
  });

  form.addEventListener('input', (event) => {
    if (event.target.closest('.kbe-pick__filter, [data-kb-manual-tag-form], #kb-attachment-file, #kb-preview-company')) {
      return;
    }
    markDirty();
  });
  form.addEventListener('change', (event) => {
    if (event.target.closest('.kbe-pick__filter, [data-kb-manual-tag-form], #kb-attachment-file, #kb-preview-company')) {
      return;
    }
    if (event.target === scopeField) {
      updateScopeFields();
    } else {
      updatePanelSummaries();
    }
    markDirty();
  });

  titleField.addEventListener('input', () => {
    if (!state.slugTouched) {
      slugField.value = slugify(titleField.value);
    }
  });
  slugField.addEventListener('input', () => {
    state.slugTouched = slugField.value.trim() !== '';
  });
  slugField.addEventListener('blur', () => {
    if (slugField.value.trim()) {
      slugField.value = slugify(slugField.value);
    }
  });
  summaryField.addEventListener('input', () => autosize(summaryField));
  [titleField, slugField].forEach((field) => {
    field.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
      }
    });
  });

  if (sectionsContainer) {
    // Keep the editor selection when clicking toolbar buttons.
    sectionsContainer.addEventListener('mousedown', (event) => {
      if (event.target.closest('[data-kb-command]')) {
        event.preventDefault();
      }
    });

    sectionsContainer.addEventListener('click', (event) => {
      const section = event.target.closest('[data-kb-section]');
      if (!section) {
        return;
      }
      const commandButton = event.target.closest('[data-kb-command]');
      if (commandButton) {
        runCommand($('[data-kb-section-editor]', section), commandButton.dataset.kbCommand, commandButton.dataset.kbCommandValue);
        return;
      }
      if (event.target.closest('[data-kb-section-access]')) {
        openAccessDialog(section);
      } else if (event.target.closest('[data-kb-section-toggle]')) {
        setCollapsed(section, !section.classList.contains('is-collapsed'));
      } else if (event.target.closest('[data-kb-section-snippet]')) {
        setCollapsed(section, false);
        $('[data-kb-section-editor]', section).focus();
      } else if (event.target.closest('[data-kb-section-up]')) {
        moveSection(section, -1);
      } else if (event.target.closest('[data-kb-section-down]')) {
        moveSection(section, 1);
      } else if (event.target.closest('[data-kb-section-insert]')) {
        addSection(null, section);
      } else if (event.target.closest('[data-kb-section-duplicate]')) {
        duplicateSection(section);
      } else if (event.target.closest('[data-kb-section-delete]')) {
        removeSection(section);
      }
    });

    sectionsContainer.addEventListener('keydown', (event) => {
      const section = event.target.closest('[data-kb-section]');
      if (!section) {
        return;
      }
      if (event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
        event.preventDefault();
        const active = document.activeElement;
        moveSection(section, event.key === 'ArrowUp' ? -1 : 1);
        if (active && section.contains(active)) {
          active.focus();
        }
      } else if (event.key === 'Enter' && event.target.matches('[data-kb-section-heading]')) {
        event.preventDefault();
        setCollapsed(section, false);
        $('[data-kb-section-editor]', section).focus();
      }
    });
  }

  $('[data-kb-add-section]', form).addEventListener('click', () => addSection());
  $('[data-kb-collapse-all]', form).addEventListener('click', () => getSections().forEach((section) => setCollapsed(section, true)));
  $('[data-kb-expand-all]', form).addEventListener('click', () => getSections().forEach((section) => setCollapsed(section, false)));

  if (refreshTagsButton) {
    refreshTagsButton.addEventListener('click', refreshAiTags);
  }
  if (attachmentFile) {
    attachmentFile.addEventListener('change', () => uploadAttachment().catch((error) => setStatus(`Upload failed: ${error.message}`, 'error', 6000)));
  }
  const uploadTrigger = $('[data-kb-upload]', form);
  if (uploadTrigger) {
    uploadTrigger.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        attachmentFile.click();
      }
    });
  }
  if (attachmentsElement) {
    attachmentsElement.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-kb-delete-attachment]');
      if (!button || !window.confirm('Remove this attachment?')) {
        return;
      }
      try {
        await api(`/api/knowledge-base/articles/${state.activeId}/attachments/${button.dataset.kbDeleteAttachment}`, { method: 'DELETE' });
        await reloadAttachments();
      } catch (error) {
        setStatus(`Unable to remove attachment: ${error.message}`, 'error', 6000);
      }
    });
  }
  $('[data-kb-preview]', form).addEventListener('click', () => showCustomerPreview());
  $('[data-kb-versions]', form).addEventListener('click', () => showVersions().catch((error) => {
    reviewContent.textContent = '';
    const warning = document.createElement('div');
    warning.className = 'alert alert--warning';
    warning.textContent = error.message;
    reviewContent.appendChild(warning);
  }));
  if (versionList) {
    versionList.addEventListener('click', (event) => {
      const button = event.target.closest('[data-kb-version]');
      if (button) {
        showVersion(button.dataset.kbVersion).catch((error) => {
          reviewContent.textContent = '';
          const warning = document.createElement('div');
          warning.className = 'alert alert--warning';
          warning.textContent = error.message;
          reviewContent.appendChild(warning);
        });
      }
    });
  }
  if (deleteButton) {
    deleteButton.addEventListener('click', deleteArticle);
  }

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 's') {
      event.preventDefault();
      submitForm();
    }
  });

  window.addEventListener('beforeunload', (event) => {
    if (state.dirty) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  window.addEventListener('resize', positionImageOverlay);
  window.addEventListener('scroll', positionImageOverlay, true);
  document.addEventListener('pointerdown', (event) => {
    if (!imageResize.overlay || imageResize.overlay.hidden) {
      return;
    }
    const target = event.target;
    if (!(target instanceof Element)) {
      hideImageOverlay();
      return;
    }
    if (imageResize.handle && imageResize.handle.contains(target)) {
      return;
    }
    const image = target.closest('img');
    if (image && imageResize.activeEditor && imageResize.activeEditor.contains(image)) {
      return;
    }
    hideImageOverlay();
  });

  // ---------------------------------------------------------------------------
  // Initialise
  // ---------------------------------------------------------------------------

  fillSelect(
    userSelect,
    userOptions.slice().sort((a, b) => (a.label || '').localeCompare(b.label || '')),
    (user) => user.label || `User ${user.id}`,
  );
  fillSelect(companySelect, companyOptions, (company) => company.name || `Company ${company.id}`);
  form.querySelectorAll('select[data-kb-picklist]').forEach(enhancePicklist);

  if (initialArticle) {
    populateForm(initialArticle);
    setStatus('');
  } else {
    populateForm(null);
    if (formMode === 'create') {
      titleField.focus();
    }
  }
})();
