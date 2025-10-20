(function () {
  const articlesScript = document.getElementById('kb-admin-articles');
  const form = document.getElementById('kb-article-form');
  if (!articlesScript || !form) {
    return;
  }

  function parseJson(script, fallback) {
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
  const contentField = document.getElementById('kb-article-content');
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
  const newButton = document.querySelector('[data-kb-new-article]');
  const previewContainer = document.querySelector('[data-kb-preview]');
  const previewMeta = document.querySelector('[data-kb-preview-meta]');

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

  function resetPreview() {
    if (previewContainer) {
      previewContainer.innerHTML = '<p class="text-muted">Select an article to preview its rendered content. Newly created articles appear here after saving.</p>';
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
    contentField.value = '';
    scopeField.value = 'anonymous';
    publishedField.checked = false;
    renderUserOptions([]);
    renderCompanyOptions([]);
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
    contentField.value = article.content || '';
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
    previewContainer.innerHTML = article.content || '<p class="text-muted">No content recorded for this article.</p>';
  }

  function getPayloadFromForm() {
    const scope = scopeField.value;
    const payload = {
      slug: slugField.value.trim(),
      title: titleField.value.trim(),
      summary: summaryField.value.trim() || null,
      content: contentField.value,
      permission_scope: scope,
      is_published: Boolean(publishedField.checked),
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
    const payload = getPayloadFromForm();
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

  if (newButton) {
    newButton.addEventListener('click', () => {
      resetForm();
      slugField.focus();
      setStatus('Ready to compose a new article.');
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

  // Initialise select options for blank state.
  renderUserOptions([]);
  renderCompanyOptions([]);
  updateScopeFields(scopeField.value);

  // Attempt to preselect the first article for convenience.
  if (!state.activeSlug && articles.length > 0) {
    const firstSlug = articles[0] && articles[0].slug;
    if (firstSlug) {
      loadArticle(firstSlug);
    }
  } else {
    resetPreview();
  }
})();
