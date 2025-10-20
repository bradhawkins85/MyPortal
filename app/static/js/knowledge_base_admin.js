(function () {
  const articlesScript = document.getElementById('kb-admin-articles');
  const form = document.getElementById('kb-article-form');
  if (!articlesScript || !form) {
    return;
  }

  function parseJson(script, fallback) {
    if (!script || typeof script.textContent !== 'string') {
      return fallback;
    }
    try {
      return JSON.parse(script.textContent || 'null') || fallback;
    } catch (error) {
      console.error('Failed to parse knowledge base admin payload', error);
      return fallback;
    }
  }

  const articles = parseJson(articlesScript, []);
  const userOptions = parseJson(document.getElementById('kb-admin-user-options') || { textContent: '[]' }, []);
  const companyOptions = parseJson(document.getElementById('kb-admin-company-options') || { textContent: '[]' }, []);
  const initialArticle = parseJson(document.getElementById('kb-admin-active-article'), null);
  const formMode = (form.dataset && form.dataset.kbMode) || 'edit';

  const state = {
    activeSlug: null,
    activeId: null,
  };

  const table = document.getElementById('kb-admin-table');
  const statusElement = form.querySelector('[data-kb-status]');
  const editorTitle = form.querySelector('[data-kb-editor-title]');
  const slugField = document.getElementById('kb-article-slug');
  const titleField = document.getElementById('kb-article-title');
  const summaryField = document.getElementById('kb-article-summary');
  const scopeField = document.getElementById('kb-article-scope');
  const publishedField = document.getElementById('kb-article-published');
  const idField = document.getElementById('kb-article-id');
  const userFieldWrapper = form.querySelector('[data-kb-user-select]');
  const companyFieldWrapper = form.querySelector('[data-kb-company-select]');
  const userSelect = document.getElementById('kb-article-users');
  const companySelect = document.getElementById('kb-article-companies');
  const scopeHelp = form.querySelector('[data-kb-scope-help]');
  const companyHelp = form.querySelector('[data-kb-company-help]');
  const deleteButton = form.querySelector('[data-kb-delete]');
  const resetButton = form.querySelector('[data-kb-reset]');
  const previewContainer = document.querySelector('[data-kb-preview]');
  const previewMeta = document.querySelector('[data-kb-preview-meta]');
  const sectionsContainer = document.querySelector('[data-kb-sections]');
  const addSectionButton = document.querySelector('[data-kb-add-section]');

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

  const scopeHelpMessages = {
    anonymous: 'Public articles are visible to anyone with the URL.',
    user: 'Only the selected users may view this article. Leaving the list empty revokes access.',
    company: 'Members of the selected companies can view the article. Leaving the list empty grants access to every company membership.',
    company_admin: 'Only company administrators in the selected companies can view the article. Leaving the list empty allows any company administrator.',
    super_admin: 'Only super administrators can read this article.',
  };

  function formatLocal(iso) {
    if (!iso) {
      return '';
    }
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return '';
    }
    return date.toLocaleString();
  }

  function setStatus(message, tone) {
    if (!statusElement) {
      return;
    }
    statusElement.textContent = message || '';
    statusElement.classList.remove('kb-admin__status--error', 'kb-admin__status--success');
    if (!message) {
      return;
    }
    if (tone === 'error') {
      statusElement.classList.add('kb-admin__status--error');
    } else if (tone === 'success') {
      statusElement.classList.add('kb-admin__status--success');
    }
  }

  function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value;
    return div.innerHTML;
  }

  function composeSectionsHtml(sections) {
    return sections
      .map((section, index) => {
        const heading = section.heading ? `<h2>${escapeHtml(section.heading)}</h2>` : '';
        return `<section class="kb-article__section" data-section-index="${index + 1}">${heading}${section.content}</section>`;
      })
      .join('');
  }

  function hasMeaningfulContent(html) {
    if (!html) {
      return false;
    }
    const temp = document.createElement('div');
    temp.innerHTML = html;
    const text = (temp.textContent || '').replace(/\u00a0/g, ' ').trim();
    if (text) {
      return true;
    }
    return Boolean(temp.querySelector('img, video, audio, iframe, table, code, pre, ul, ol, blockquote'));
  }

  function collectSectionsFromDom() {
    if (!sectionsContainer) {
      return [];
    }
    const sections = [];
    sectionsContainer.querySelectorAll('[data-kb-section]').forEach((section, index) => {
      const headingField = section.querySelector('[data-kb-section-heading]');
      const editor = section.querySelector('[data-kb-section-editor]');
      const heading = headingField ? headingField.value.trim() : '';
      const content = editor ? editor.innerHTML.trim() : '';
      if (!hasMeaningfulContent(content)) {
        return;
      }
      sections.push({
        heading: heading || null,
        content,
        position: index + 1,
      });
    });
    return sections;
  }

  function renderSections(sections) {
    if (!sectionsContainer) {
      return;
    }
    sectionsContainer.querySelectorAll('[data-kb-section-editor]').forEach((existingEditor) => {
      destroySectionEditor(existingEditor);
    });
    sectionsContainer.innerHTML = '';
    if (!sections || sections.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'kb-admin__sections-empty';
      empty.textContent = 'No sections added yet. Start by creating the introduction.';
      sectionsContainer.appendChild(empty);
      return;
    }
    sections.forEach((section) => {
      sectionsContainer.appendChild(createSectionElement(section));
    });
  }

  function createToolbarButton(command, label) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'kb-admin__toolbar-button';
    button.dataset.kbCommand = command;
    button.setAttribute('aria-label', label);
    button.innerHTML = label;
    return button;
  }

  function createSectionElement(section) {
    const wrapper = document.createElement('article');
    wrapper.className = 'kb-admin__section';
    wrapper.dataset.kbSection = 'true';

    const headingLabel = document.createElement('label');
    headingLabel.className = 'kb-admin__section-label';
    headingLabel.textContent = 'Section heading (optional)';

    const headingInput = document.createElement('input');
    headingInput.type = 'text';
    headingInput.className = 'form-input';
    headingInput.placeholder = 'Describe the section';
    headingInput.value = section && section.heading ? section.heading : '';
    headingInput.dataset.kbSectionHeading = 'true';

    const headingWrapper = document.createElement('div');
    headingWrapper.className = 'kb-admin__section-heading-group';
    headingWrapper.appendChild(headingLabel);
    headingWrapper.appendChild(headingInput);

    const toolbar = document.createElement('div');
    toolbar.className = 'kb-admin__toolbar';
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', 'Formatting');

    const commands = [
      { command: 'bold', label: '<strong>B</strong>' },
      { command: 'italic', label: '<em>I</em>' },
      { command: 'underline', label: '<span style="text-decoration: underline">U</span>' },
      { command: 'insertUnorderedList', label: '&bull; List' },
      { command: 'insertOrderedList', label: '1. List' },
      { command: 'formatBlock', value: 'blockquote', label: '&ldquo;Quote&rdquo;' },
      { command: 'createLink', label: 'Link' },
      { command: 'removeFormat', label: 'Clear' },
    ];

    commands.forEach((item) => {
      const button = createToolbarButton(item.command, item.label.replace(/<[^>]*>/g, ''));
      button.innerHTML = item.label;
      if (item.value) {
        button.dataset.kbCommandValue = item.value;
      }
      toolbar.appendChild(button);
    });

    const editor = document.createElement('div');
    editor.className = 'kb-admin__editor';
    editor.contentEditable = 'true';
    editor.dataset.kbSectionEditor = 'true';
    editor.innerHTML = section && section.content ? section.content : '<p><br></p>';
    initialiseSectionEditor(editor);

    const controls = document.createElement('div');
    controls.className = 'kb-admin__section-controls';

    const moveUp = document.createElement('button');
    moveUp.type = 'button';
    moveUp.className = 'button button--ghost';
    moveUp.dataset.kbSectionUp = 'true';
    moveUp.innerHTML = '&#8593; Move up';

    const moveDown = document.createElement('button');
    moveDown.type = 'button';
    moveDown.className = 'button button--ghost';
    moveDown.dataset.kbSectionDown = 'true';
    moveDown.innerHTML = '&#8595; Move down';

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'button button--danger button--ghost';
    remove.dataset.kbSectionDelete = 'true';
    remove.textContent = 'Remove';

    controls.appendChild(moveUp);
    controls.appendChild(moveDown);
    controls.appendChild(remove);

    wrapper.appendChild(headingWrapper);
    wrapper.appendChild(toolbar);
    wrapper.appendChild(editor);
    wrapper.appendChild(controls);
    return wrapper;
  }

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
      imageResize.overlay.style.left = '';
      imageResize.overlay.style.top = '';
      imageResize.overlay.style.width = '';
      imageResize.overlay.style.height = '';
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
    const overlay = ensureImageOverlay();
    overlay.style.width = `${rect.width}px`;
    overlay.style.height = `${rect.height}px`;
    overlay.style.left = `${Math.round(window.scrollX + rect.left)}px`;
    overlay.style.top = `${Math.round(window.scrollY + rect.top)}px`;
    overlay.hidden = false;
  }

  function showImageOverlay(editor, image) {
    if (!editor || !image) {
      return;
    }
    ensureImageOverlay();
    imageResize.activeImage = image;
    imageResize.activeEditor = editor;
    positionImageOverlay();
  }

  function endResizeSession(shouldUpdatePreview) {
    document.removeEventListener('pointermove', handleResizePointerMove);
    document.removeEventListener('pointerup', handleResizePointerUp);
    document.removeEventListener('pointercancel', handleResizePointerCancel);
    imageResize.pointerId = null;
    if (imageResize.activeImage) {
      positionImageOverlay();
    }
    if (shouldUpdatePreview) {
      ensurePreviewMatchesForm();
    }
  }

  function startResizeSession(event) {
    if (!imageResize.activeImage || !imageResize.activeEditor) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const image = imageResize.activeImage;
    const editor = imageResize.activeEditor;
    if (!editor.contains(image)) {
      return;
    }
    const imageRect = image.getBoundingClientRect();
    const editorRect = editor.getBoundingClientRect();
    imageResize.pointerId = event.pointerId;
    imageResize.startWidth = imageRect.width;
    imageResize.startX = event.clientX;
    const availableWidth = Math.max(32, editorRect.right - imageRect.left - 16);
    const naturalWidth = image.naturalWidth || Infinity;
    imageResize.maxWidth = Math.max(32, Math.min(availableWidth, naturalWidth));
    const proposedMin = Math.max(32, Math.min(48, imageRect.width));
    imageResize.minWidth = Math.min(proposedMin, imageResize.maxWidth);
    image.style.height = 'auto';
    document.addEventListener('pointermove', handleResizePointerMove);
    document.addEventListener('pointerup', handleResizePointerUp);
    document.addEventListener('pointercancel', handleResizePointerCancel);
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
    const deltaX = event.clientX - imageResize.startX;
    let width = imageResize.startWidth + deltaX;
    width = Math.max(imageResize.minWidth, Math.min(imageResize.maxWidth, width));
    image.style.width = `${Math.round(width)}px`;
    image.style.maxWidth = '100%';
    positionImageOverlay();
  }

  function handleResizePointerUp(event) {
    if (imageResize.pointerId != null && event.pointerId !== imageResize.pointerId) {
      return;
    }
    endResizeSession(true);
  }

  function handleResizePointerCancel(event) {
    if (imageResize.pointerId != null && event.pointerId !== imageResize.pointerId) {
      return;
    }
    endResizeSession(false);
  }

  function decorateEditorImages(editor) {
    if (!editor) {
      return;
    }
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
      if ('loading' in image && !image.loading) {
        image.loading = 'lazy';
      }
      image.addEventListener('load', () => {
        if (imageResize.activeImage === image) {
          positionImageOverlay();
        }
      });
    });
  }

  function initialiseSectionEditor(editor) {
    if (!editor || editor.dataset.kbEditorReady === 'true') {
      return;
    }
    editor.dataset.kbEditorReady = 'true';
    decorateEditorImages(editor);
    editor.addEventListener('click', (event) => {
      const targetImage = event.target.closest('img');
      if (targetImage && editor.contains(targetImage)) {
        showImageOverlay(editor, targetImage);
      } else if (imageResize.activeEditor === editor) {
        hideImageOverlay();
      }
    });
    editor.addEventListener('input', () => {
      decorateEditorImages(editor);
    });
    editor.addEventListener('paste', () => {
      window.requestAnimationFrame(() => {
        decorateEditorImages(editor);
      });
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

  function addSection(section) {
    if (!sectionsContainer) {
      return;
    }
    const existingEmpty = sectionsContainer.querySelector('.kb-admin__sections-empty');
    if (existingEmpty) {
      existingEmpty.remove();
    }
    const element = createSectionElement(section || { heading: '', content: '<p><br></p>' });
    sectionsContainer.appendChild(element);
    const headingInput = element.querySelector('[data-kb-section-heading]');
    if (headingInput) {
      headingInput.focus();
    }
  }

  function ensurePreviewMatchesForm() {
    if (!previewContainer) {
      return;
    }
    const sections = collectSectionsFromDom();
    if (sections.length === 0) {
      previewContainer.innerHTML = '<p class="text-muted">Add rich text sections to preview the article.</p>';
      return;
    }
    previewContainer.innerHTML = composeSectionsHtml(sections);
  }

  function moveSection(section, offset) {
    if (!sectionsContainer) {
      return;
    }
    const sections = Array.from(sectionsContainer.querySelectorAll('[data-kb-section]'));
    const index = sections.indexOf(section);
    if (index === -1) {
      return;
    }
    const target = index + offset;
    if (target < 0 || target >= sections.length) {
      return;
    }
    if (offset < 0) {
      sectionsContainer.insertBefore(section, sections[target]);
    } else {
      const reference = sections[target].nextSibling;
      sectionsContainer.insertBefore(section, reference);
    }
  }

  function getSelectedValues(selectElement) {
    return Array.from(selectElement.selectedOptions || [])
      .map((option) => {
        const value = parseInt(option.value, 10);
        return Number.isFinite(value) ? value : null;
      })
      .filter((value) => value !== null);
  }

  function renderUserOptions(selected) {
    const selectedSet = new Set((selected || []).map((value) => String(value)));
    userSelect.innerHTML = '';
    userOptions
      .slice()
      .sort((a, b) => (a.label || '').localeCompare(b.label || ''))
      .forEach((user) => {
        if (user.id == null) {
          return;
        }
        const option = document.createElement('option');
        option.value = String(user.id);
        option.textContent = user.label || `User ${user.id}`;
        if (selectedSet.has(String(user.id))) {
          option.selected = true;
        }
        userSelect.appendChild(option);
      });
  }

  function renderCompanyOptions(selected) {
    const selectedSet = new Set((selected || []).map((value) => String(value)));
    companySelect.innerHTML = '';
    companyOptions
      .slice()
      .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
      .forEach((company) => {
        if (company.id == null) {
          return;
        }
        const option = document.createElement('option');
        option.value = String(company.id);
        option.textContent = company.name || `Company ${company.id}`;
        if (selectedSet.has(String(company.id))) {
          option.selected = true;
        }
        companySelect.appendChild(option);
      });
  }

  function highlightRow(slug) {
    if (!table) {
      return;
    }
    table.querySelectorAll('tr[data-kb-row]').forEach((row) => {
      if (row.dataset.kbArticleSlug === slug) {
        row.classList.add('kb-admin__row--active');
      } else {
        row.classList.remove('kb-admin__row--active');
      }
    });
  }

  function updateScopeFields(scope) {
    const selectedScope = scope || scopeField.value || 'anonymous';
    if (scopeHelp) {
      scopeHelp.textContent = scopeHelpMessages[selectedScope] || '';
    }
    if (selectedScope === 'user') {
      userFieldWrapper.hidden = false;
      companyFieldWrapper.hidden = true;
      renderUserOptions(getSelectedValues(userSelect));
    } else if (selectedScope === 'company' || selectedScope === 'company_admin') {
      userFieldWrapper.hidden = true;
      companyFieldWrapper.hidden = false;
      renderCompanyOptions(getSelectedValues(companySelect));
      if (companyHelp) {
        companyHelp.textContent =
          selectedScope === 'company_admin'
            ? 'Limit visibility to administrators in the selected companies. Leave empty to allow any company administrator.'
            : 'Limit visibility to members of the selected companies. Leave empty to allow any company membership.';
      }
    } else {
      userFieldWrapper.hidden = true;
      companyFieldWrapper.hidden = true;
    }
  }

  const previewEmptyMessage =
    formMode === 'create'
      ? 'Compose sections to preview the article content here.'
      : 'Select an article to preview its rendered content. Newly created articles appear here after saving.';

  function resetPreview() {
    if (previewContainer) {
      previewContainer.innerHTML = `<p class="text-muted">${previewEmptyMessage}</p>`;
    }
    if (previewMeta) {
      previewMeta.innerHTML = '';
    }
  }

  function resetForm() {
    state.activeSlug = null;
    state.activeId = null;
    idField.value = '';
    slugField.value = '';
    titleField.value = '';
    summaryField.value = '';
    scopeField.value = 'anonymous';
    publishedField.checked = false;
    renderUserOptions([]);
    renderCompanyOptions([]);
    renderSections([]);
    addSection({ heading: '', content: '<p><br></p>' });
    updateScopeFields('anonymous');
    setStatus('');
    if (deleteButton) {
      deleteButton.hidden = true;
    }
    if (editorTitle) {
      editorTitle.textContent = 'Compose article';
    }
    highlightRow(null);
    resetPreview();
  }

  async function loadArticle(slug) {
    setStatus('Loading article…');
    try {
      const response = await fetch(`/api/knowledge-base/articles/${encodeURIComponent(slug)}?include_permissions=true`);
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error((detail && detail.detail) || `${response.status} ${response.statusText}`);
      }
      const article = await response.json();
      populateForm(article);
      setStatus('Article loaded. Ready to edit.', 'success');
    } catch (error) {
      console.error(error);
      setStatus(`Unable to load article: ${error.message}`, 'error');
    }
  }

  function populateForm(article) {
    if (!article) {
      return;
    }
    state.activeSlug = article.slug;
    state.activeId = article.id;
    idField.value = article.id != null ? String(article.id) : '';
    slugField.value = article.slug || '';
    titleField.value = article.title || '';
    summaryField.value = article.summary || '';
    if (Array.isArray(article.sections) && article.sections.length > 0) {
      const sortedSections = article.sections
        .slice()
        .sort((a, b) => (a.position || 0) - (b.position || 0));
      renderSections(sortedSections);
    } else {
      renderSections([
        {
          heading: article.title || '',
          content: article.content || '<p><br></p>',
        },
      ]);
    }
    scopeField.value = article.permission_scope || 'anonymous';
    publishedField.checked = Boolean(article.is_published);

    const selectedUsers = Array.isArray(article.allowed_user_ids) ? article.allowed_user_ids : [];
    const selectedCompanies = (() => {
      if (article.permission_scope === 'company_admin') {
        return Array.isArray(article.company_admin_ids) ? article.company_admin_ids : [];
      }
      return Array.isArray(article.allowed_company_ids) ? article.allowed_company_ids : [];
    })();

    renderUserOptions(selectedUsers);
    renderCompanyOptions(selectedCompanies);
    updateScopeFields(article.permission_scope);
    if (deleteButton) {
      deleteButton.hidden = false;
    }
    if (editorTitle) {
      editorTitle.textContent = `Edit “${article.title || article.slug}”`;
    }
    highlightRow(article.slug);
    updatePreview(article);
  }

  function updatePreview(article) {
    if (!previewContainer || !article) {
      return;
    }
    const tags = [];
    const scopeLabelText = (() => {
      switch (article.permission_scope) {
        case 'anonymous':
          return 'Public';
        case 'user':
          return 'Specific users';
        case 'company':
          return 'Company members';
        case 'company_admin':
          return 'Company admins';
        case 'super_admin':
          return 'Super admins';
        default:
          return article.permission_scope;
      }
    })();
    tags.push(`<span class="tag">${scopeLabelText}</span>`);
    if (!article.is_published) {
      tags.push('<span class="tag tag--warning">Draft</span>');
    }
    if (previewMeta) {
      const timestamps = [];
      if (article.updated_at) {
        timestamps.push(`<span class="kb-admin__timestamp">Updated ${formatLocal(article.updated_at)}</span>`);
      }
      if (article.published_at) {
        timestamps.push(`<span class="kb-admin__timestamp">Published ${formatLocal(article.published_at)}</span>`);
      }
      previewMeta.innerHTML = `
        <div class="kb-admin__tags">${tags.join(' ')}</div>
        ${timestamps.length ? `<div class="kb-admin__timestamps">${timestamps.join('')}</div>` : ''}
      `;
    }
    if (Array.isArray(article.sections) && article.sections.length > 0) {
      previewContainer.innerHTML = composeSectionsHtml(article.sections);
    } else {
      previewContainer.innerHTML =
        article.content || '<p class="text-muted">No content recorded for this article.</p>';
    }
  }

  function getPayloadFromForm() {
    const scope = scopeField.value;
    const sections = collectSectionsFromDom();
    if (sections.length === 0) {
      throw new Error('At least one section with content is required.');
    }
    const payload = {
      slug: slugField.value.trim(),
      title: titleField.value.trim(),
      summary: summaryField.value.trim() || null,
      content: composeSectionsHtml(sections),
      permission_scope: scope,
      is_published: Boolean(publishedField.checked),
      sections: sections.map((section) => ({ heading: section.heading, content: section.content })),
    };
    if (scope === 'user') {
      payload.allowed_user_ids = getSelectedValues(userSelect);
    } else if (scope === 'company' || scope === 'company_admin') {
      payload.allowed_company_ids = getSelectedValues(companySelect);
    }
    return payload;
  }

  async function submitForm() {
    const articleId = idField.value ? parseInt(idField.value, 10) : null;
    let payload;
    try {
      payload = getPayloadFromForm();
    } catch (error) {
      setStatus(error.message || 'Unable to read section content.', 'error');
      return;
    }
    if (!payload.slug || !payload.title || !payload.content) {
      setStatus('Slug, title, and content are required before saving.', 'error');
      return;
    }
    const url = articleId ? `/api/knowledge-base/articles/${articleId}` : '/api/knowledge-base/articles';
    const method = articleId ? 'PUT' : 'POST';
    const submitButtons = form.querySelectorAll('input, button, select, textarea');
    submitButtons.forEach((element) => {
      element.disabled = true;
    });
    setStatus('Saving article…');
    try {
      const response = await fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error((detail && detail.detail) || `${response.status} ${response.statusText}`);
      }
      setStatus('Article saved. Reloading…', 'success');
      window.setTimeout(() => {
        window.location.reload();
      }, 600);
    } catch (error) {
      console.error(error);
      setStatus(`Unable to save article: ${error.message}`, 'error');
    } finally {
      submitButtons.forEach((element) => {
        element.disabled = false;
      });
    }
  }

  async function deleteArticle() {
    const articleId = idField.value ? parseInt(idField.value, 10) : null;
    if (!articleId) {
      return;
    }
    if (!confirm('Delete this article? This action cannot be undone.')) {
      return;
    }
    setStatus('Deleting article…');
    try {
      const response = await fetch(`/api/knowledge-base/articles/${articleId}`, {
        method: 'DELETE',
        headers: {
          Accept: 'application/json',
        },
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error((detail && detail.detail) || `${response.status} ${response.statusText}`);
      }
      setStatus('Article deleted. Reloading…', 'success');
      window.setTimeout(() => {
        window.location.reload();
      }, 600);
    } catch (error) {
      console.error(error);
      setStatus(`Unable to delete article: ${error.message}`, 'error');
    }
  }

  if (scopeField) {
    scopeField.addEventListener('change', () => updateScopeFields(scopeField.value));
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    submitForm();
  });

  if (deleteButton) {
    deleteButton.addEventListener('click', () => {
      deleteArticle();
    });
  }

  if (resetButton) {
    resetButton.addEventListener('click', () => {
      resetForm();
    });
  }

  if (table) {
    table.addEventListener('click', (event) => {
      const trigger = event.target.closest('[data-kb-select]');
      if (!trigger) {
        return;
      }
      const row = trigger.closest('tr[data-kb-row]');
      if (!row) {
        return;
      }
      const slug = row.dataset.kbArticleSlug;
      if (!slug) {
        return;
      }
      loadArticle(slug);
    });
  }

  if (sectionsContainer) {
    sectionsContainer.addEventListener('click', (event) => {
      const commandButton = event.target.closest('[data-kb-command]');
      if (commandButton) {
        const section = commandButton.closest('[data-kb-section]');
        const editor = section ? section.querySelector('[data-kb-section-editor]') : null;
        if (editor) {
          editor.focus();
          const command = commandButton.dataset.kbCommand;
          const value = commandButton.dataset.kbCommandValue || null;
          if (command === 'createLink') {
            let url = window.prompt('Enter the link URL (https://example.com)');
            if (!url) {
              return;
            }
            if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(url)) {
              url = `https://${url}`;
            }
            if (!/^https?:/i.test(url) && !/^mailto:/i.test(url)) {
              window.alert('Only HTTP, HTTPS, or mailto links are allowed.');
              return;
            }
            document.execCommand('createLink', false, url);
            const selectorUrl = url.replace(/"/g, '\\"');
            editor.querySelectorAll(`a[href="${selectorUrl}"]`).forEach((anchor) => {
              anchor.target = '_blank';
              anchor.rel = 'noopener';
            });
          } else if (command === 'formatBlock') {
            document.execCommand('formatBlock', false, value || 'p');
          } else if (command === 'removeFormat') {
            document.execCommand('removeFormat', false, value);
          } else {
            document.execCommand(command, false, value);
          }
          ensurePreviewMatchesForm();
        }
        return;
      }

      const section = event.target.closest('[data-kb-section]');
      if (!section) {
        return;
      }
      if (event.target.closest('[data-kb-section-up]')) {
        moveSection(section, -1);
        ensurePreviewMatchesForm();
      } else if (event.target.closest('[data-kb-section-down]')) {
        moveSection(section, 1);
        ensurePreviewMatchesForm();
      } else if (event.target.closest('[data-kb-section-delete]')) {
        const editor = section.querySelector('[data-kb-section-editor]');
        destroySectionEditor(editor);
        section.remove();
        if (!sectionsContainer.querySelector('[data-kb-section]')) {
          renderSections([]);
        }
        ensurePreviewMatchesForm();
      }
    });

    sectionsContainer.addEventListener('input', (event) => {
      if (
        event.target.matches('[data-kb-section-heading]') ||
        event.target.closest('[data-kb-section-editor]')
      ) {
        ensurePreviewMatchesForm();
      }
    });
  }

  if (addSectionButton) {
    addSectionButton.addEventListener('click', () => {
      addSection({ heading: '', content: '<p><br></p>' });
      ensurePreviewMatchesForm();
    });
  }

  window.addEventListener('resize', () => {
    positionImageOverlay();
  });

  window.addEventListener(
    'scroll',
    () => {
      positionImageOverlay();
    },
    true,
  );

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
    const imageTarget = target.closest('img');
    if (imageTarget && imageResize.activeEditor && imageResize.activeEditor.contains(imageTarget)) {
      return;
    }
    const editorTarget = target.closest('[data-kb-section-editor]');
    if (editorTarget === imageResize.activeEditor) {
      return;
    }
    hideImageOverlay();
  });

  // Initialise select options for blank state.
  renderUserOptions([]);
  renderCompanyOptions([]);
  updateScopeFields(scopeField.value);

  if (initialArticle) {
    populateForm(initialArticle);
    setStatus('Article loaded. Ready to edit.', 'success');
  } else if (formMode === 'create') {
    resetForm();
    if (slugField) {
      slugField.focus();
    }
    setStatus('Ready to compose a new article.');
  } else if (!state.activeSlug && articles.length > 0) {
    // Attempt to preselect the first article for convenience when an editor page is not preloaded.
    const firstSlug = articles[0] && articles[0].slug;
    if (firstSlug) {
      loadArticle(firstSlug);
    }
  } else {
    resetPreview();
  }
})();
