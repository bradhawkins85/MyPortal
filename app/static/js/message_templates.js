(function () {
  'use strict';

  const LIST_URL = '/admin/message-templates';
  const API_URL = '/api/message-templates';
  const SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$/;
  const TOKEN_PATTERN = /\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}/g;
  const FRAME_HEAD = "<!doctype html><meta charset='utf-8'><style>"
    + 'body{margin:14px;font:14px/1.5 Arial,sans-serif;color:#1f2937;background:#fff}'
    + 'img{max-width:100%;height:auto}'
    + '.mt-token{background:#fef3c7;color:#92400e;border-radius:3px;padding:0 2px;font-family:ui-monospace,monospace;font-size:12px}'
    + '</style>';

  function getCookie(name) {
    const match = document.cookie.split('; ').find((part) => part.startsWith(`${name}=`));
    return match ? decodeURIComponent(match.split('=').slice(1).join('=')) : '';
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return (meta && meta.getAttribute('content')) || getCookie('myportal_session_csrf');
  }

  async function requestJson(url, options) {
    const headers = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
    };
    const token = csrfToken();
    if (token) {
      headers['X-CSRF-Token'] = token;
    }
    const response = await fetch(url, { credentials: 'same-origin', headers, ...options });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        if (data && data.detail) {
          detail = Array.isArray(data.detail)
            ? data.detail.map((entry) => entry.msg || entry).join(', ')
            : data.detail;
        }
      } catch (error) {
        /* keep the status text */
      }
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  }

  function toast(message, variant) {
    if (window.__portalToast) {
      window.__portalToast.show(message, { variant: variant || 'success' });
    }
  }

  function normalize(value) {
    return String(value == null ? '' : value).trim().toLowerCase();
  }

  function slugify(value) {
    return String(value || '')
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/[^a-z0-9._-]+/g, '-')
      .replace(/-{2,}/g, '-')
      .replace(/^[-._]+|[-._]+$/g, '')
      .slice(0, 120);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function tokensIn(content) {
    const found = [];
    String(content || '').replace(TOKEN_PATTERN, (match, path) => {
      if (!found.includes(path)) {
        found.push(path);
      }
      return match;
    });
    return found;
  }

  function copyText(button) {
    const value = button.dataset.mtCopy;
    const done = () => {
      toast(`Copied ${value}`);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(value).then(done, () => window.prompt('Copy this token', value));
    } else {
      window.prompt('Copy this token', value);
    }
  }

  // ----- delete confirmation ------------------------------------------------

  function createDeleteDialog() {
    const modal = document.getElementById('mt-delete-modal');
    if (!modal) {
      return null;
    }
    const nameNode = modal.querySelector('[data-mt-delete-name]');
    const systemNode = modal.querySelector('[data-mt-delete-system]');
    const errorNode = modal.querySelector('[data-mt-delete-error]');
    const confirmButton = modal.querySelector('[data-mt-delete-confirm]');
    let current = null;
    let returnFocus = null;

    function close() {
      modal.hidden = true;
      modal.classList.remove('is-visible');
      modal.setAttribute('aria-hidden', 'true');
      current = null;
      if (returnFocus) {
        returnFocus.focus();
      }
    }

    function open(target, trigger) {
      current = target;
      returnFocus = trigger || null;
      nameNode.textContent = target.name || 'This template';
      systemNode.textContent = target.system
        ? `MyPortal uses this template for the ${target.system.toLowerCase()}. After deleting it, that email goes out with MyPortal's built-in wording.`
        : '';
      systemNode.hidden = !target.system;
      errorNode.hidden = true;
      confirmButton.disabled = false;
      modal.hidden = false;
      modal.classList.add('is-visible');
      modal.setAttribute('aria-hidden', 'false');
      confirmButton.focus();
    }

    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', close);
    });
    modal.addEventListener('click', (event) => {
      if (event.target === modal) {
        close();
      }
    });
    confirmButton.addEventListener('click', async () => {
      if (!current) {
        return;
      }
      confirmButton.disabled = true;
      try {
        await requestJson(`${API_URL}/${encodeURIComponent(current.id)}`, { method: 'DELETE' });
        if (current.onDeleted) {
          current.onDeleted();
        }
        window.location.href = `${LIST_URL}?success=${encodeURIComponent(`Deleted ${current.name}.`)}`;
      } catch (error) {
        errorNode.textContent = `Couldn't delete the template: ${error.message}`;
        errorNode.hidden = false;
        confirmButton.disabled = false;
      }
    });

    return { open };
  }

  async function duplicate(templateId, button) {
    if (button) {
      button.disabled = true;
    }
    try {
      const cloned = await requestJson(`${API_URL}/${encodeURIComponent(templateId)}/clone`, { method: 'POST' });
      const message = `Duplicated as ${cloned.slug}. Rename it and save your changes.`;
      window.location.href = `${LIST_URL}/${encodeURIComponent(cloned.id)}/edit?success=${encodeURIComponent(message)}`;
    } catch (error) {
      toast(`Couldn't duplicate the template: ${error.message}`, 'error');
      if (button) {
        button.disabled = false;
      }
    }
  }

  // ----- list page ----------------------------------------------------------

  function initList(list) {
    const filterForm = document.querySelector('[data-mt-filter]');
    const search = document.querySelector('[data-mt-search]');
    const typeInputs = Array.from(document.querySelectorAll('[data-mt-type]'));
    const applyButton = document.querySelector('[data-mt-apply]');
    const noResults = document.querySelector('[data-mt-no-results]');
    const items = Array.from(list.querySelectorAll('[data-mt-item]'));
    const deleteDialog = createDeleteDialog();

    // Filter as you type; the Filter button stays for browsers without JavaScript.
    // When the server already narrowed the list, widening it needs a reload.
    const serverFiltered = Boolean(filterForm && filterForm.dataset.mtServerFiltered === 'true');
    if (filterForm) {
      applyButton.hidden = true;
      filterForm.addEventListener('submit', (event) => {
        if (!serverFiltered) {
          event.preventDefault();
        }
      });
    }

    let reloadTimer = null;
    function applyFilters(event) {
      if (serverFiltered) {
        window.clearTimeout(reloadTimer);
        reloadTimer = window.setTimeout(() => filterForm.requestSubmit(), event && event.type === 'input' ? 400 : 0);
        return;
      }
      const terms = normalize(search.value).split(/\s+/).filter(Boolean);
      const checked = typeInputs.find((input) => input.checked);
      const type = checked ? checked.value : '';
      let visible = 0;
      items.forEach((item) => {
        const text = normalize(item.dataset.mtSearchText);
        const match = terms.every((term) => text.includes(term)) && (!type || item.dataset.mtType === type);
        item.hidden = !match;
        visible += match ? 1 : 0;
      });
      if (noResults) {
        noResults.hidden = visible > 0;
      }
    }

    if (search) {
      search.addEventListener('input', applyFilters);
    }
    typeInputs.forEach((input) => input.addEventListener('change', applyFilters));

    list.addEventListener('click', (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) {
        return;
      }
      const item = target.closest('[data-mt-item]');
      if (!item) {
        return;
      }
      const copyButton = target.closest('[data-mt-copy]');
      if (copyButton) {
        copyText(copyButton);
        return;
      }
      const cloneButton = target.closest('[data-mt-clone]');
      if (cloneButton) {
        duplicate(item.dataset.templateId, cloneButton);
        return;
      }
      const deleteButton = target.closest('[data-mt-delete]');
      if (deleteButton && deleteDialog) {
        deleteDialog.open({
          id: item.dataset.templateId,
          name: item.dataset.templateName,
          system: item.dataset.templateSystem,
        }, deleteButton);
      }
    });
  }

  // ----- editor -------------------------------------------------------------

  function textToHtml(text) {
    return String(text || '')
      .split(/\n{2,}/)
      .map((block) => block.trim())
      .filter(Boolean)
      .map((block) => `<p>${escapeHtml(block).replace(/\n/g, '<br>')}</p>`)
      .join('');
  }

  function htmlToText(html) {
    const doc = new DOMParser().parseFromString(`<body>${html || ''}</body>`, 'text/html');
    doc.querySelectorAll('br').forEach((br) => br.replaceWith('\n'));
    doc.querySelectorAll('a[href]').forEach((link) => {
      const href = link.getAttribute('href');
      if (href && href !== link.textContent.trim()) {
        link.append(` (${href})`);
      }
    });
    doc.querySelectorAll('p, div, li, tr, h1, h2, h3, h4, h5, h6').forEach((block) => block.append('\n\n'));
    doc.querySelectorAll('td, th').forEach((cell) => cell.append('  '));
    return (doc.body.textContent || '')
      .split('\n')
      .map((line) => line.replace(/[ \t]+$/g, ''))
      .join('\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  // Wrap {{ tokens }} in text nodes so the preview shows where values go.
  function highlightHtml(html) {
    const doc = new DOMParser().parseFromString('<!doctype html><html><body></body></html>', 'text/html');
    doc.body.innerHTML = html || '';
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      if (/\{\{[^}]+\}\}/.test(walker.currentNode.nodeValue)) {
        nodes.push(walker.currentNode);
      }
    }
    nodes.forEach((node) => {
      const fragment = doc.createDocumentFragment();
      node.nodeValue.split(/(\{\{[^}]+\}\})/).forEach((part) => {
        if (!part) {
          return;
        }
        if (/^\{\{[^}]+\}\}$/.test(part)) {
          const span = doc.createElement('span');
          span.className = 'mt-token';
          span.textContent = part.replace(/^\{\{\s*|\s*\}\}$/g, '');
          fragment.append(span);
        } else {
          fragment.append(doc.createTextNode(part));
        }
      });
      node.replaceWith(fragment);
    });
    // Nothing in the frame can run: it is sandboxed without allow-scripts.
    return doc.body.innerHTML;
  }

  function renderTextPreview(container, text) {
    container.textContent = '';
    String(text || '').split(/(\{\{[^}]+\}\})/).forEach((part) => {
      if (!part) {
        return;
      }
      if (/^\{\{[^}]+\}\}$/.test(part)) {
        const span = document.createElement('span');
        span.className = 'sig-token-sample';
        span.textContent = part.replace(/^\{\{\s*|\s*\}\}$/g, '');
        container.append(span);
      } else {
        container.append(document.createTextNode(part));
      }
    });
  }

  function initEditor(form) {
    const data = JSON.parse(document.getElementById('mt-editor-data').textContent || '{}');
    const systemUses = data.systemUses || {};
    const commonVariables = data.commonVariables || [];
    const templateId = form.dataset.mtId;
    const savedSlug = form.dataset.mtSavedSlug || '';
    const isNew = !templateId;

    const tabsNav = form.querySelector('[data-mt-tabs]');
    const tabs = Array.from(form.querySelectorAll('[data-mt-tab]'));
    const panels = Array.from(form.querySelectorAll('[data-mt-panel]'));
    const title = form.querySelector('[data-mt-title]');
    const nameInput = form.querySelector('[data-mt-name]');
    const slugInput = form.querySelector('[data-mt-slug]');
    const descriptionInput = form.querySelector('#message-template-description');
    const formatInputs = Array.from(form.querySelectorAll('[data-mt-format]'));
    const textArea = form.querySelector('[data-mt-content]');
    const richHost = form.querySelector('[data-mt-rich]');
    const varsList = form.querySelector('[data-mt-vars-list]');
    const previewHtml = form.querySelector('[data-mt-preview-html]');
    const previewFrame = form.querySelector('[data-mt-preview-frame]');
    const previewText = form.querySelector('[data-mt-preview-text]');
    const usedList = form.querySelector('[data-mt-used]');
    const usedEmpty = form.querySelector('[data-mt-used-empty]');
    const usageToken = form.querySelector('[data-mt-usage-token]');
    const systemBanner = form.querySelector('[data-mt-system-banner]');
    const systemLabel = form.querySelector('[data-mt-system-label]');
    const slugWarning = form.querySelector('[data-mt-slug-warning]');
    const dirtyNote = form.querySelector('[data-mt-dirty]');
    const saveButton = form.querySelector('[data-mt-save]');
    const errors = {};
    form.querySelectorAll('[data-mt-error]').forEach((node) => {
      errors[node.dataset.mtError] = node;
    });

    let slugTouched = !isNew || Boolean(slugInput.value.trim());
    let sunEditor = null;
    let format = (formatInputs.find((input) => input.checked) || {}).value || 'text/plain';
    let initial = null;
    let submitting = false;
    let lastSavedSlug = savedSlug;

    // Tabs: both panels are visible without JavaScript.
    tabsNav.hidden = false;
    function showTab(name, focus) {
      tabs.forEach((tab) => {
        const active = tab.dataset.mtTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
        if (active && focus) {
          tab.focus();
        }
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.mtPanel !== name;
      });
    }
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => showTab(tab.dataset.mtTab));
      tab.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
          return;
        }
        event.preventDefault();
        const index = tabs.indexOf(tab);
        const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
        showTab(next.dataset.mtTab, true);
      });
    });
    showTab(isNew ? 'details' : 'content');

    function setError(name, message) {
      const node = errors[name];
      if (node) {
        node.textContent = message || '';
        node.hidden = !message;
      }
    }

    // ----- content editing --------------------------------------------------

    function getContent() {
      if (sunEditor) {
        const html = sunEditor.getContents(true);
        // An emptied editor leaves a lone empty paragraph behind.
        return /^<p>(<br>)?<\/p>$/.test(html.trim()) ? '' : html;
      }
      return textArea.value;
    }

    function startRichEditor(html) {
      if (typeof window.SUNEDITOR === 'undefined') {
        // Fall back to editing the HTML source directly.
        textArea.value = html;
        textArea.hidden = false;
        richHost.hidden = true;
        return;
      }
      richHost.hidden = false;
      textArea.hidden = true;
      richHost.innerHTML = '';
      const host = document.createElement('textarea');
      host.id = 'message-template-content-rich';
      host.setAttribute('aria-labelledby', 'message-template-content-label');
      richHost.append(host);
      sunEditor = window.SUNEDITOR.create(host, {
        width: '100%',
        height: '320',
        minHeight: '220',
        resizingBar: true,
        buttonList: [
          ['undo', 'redo'],
          ['formatBlock', 'bold', 'underline', 'italic', 'strike'],
          ['fontColor', 'hiliteColor'],
          ['align', 'list', 'outdent', 'indent', 'horizontalRule'],
          ['link', 'image', 'table'],
          ['removeFormat', 'codeView'],
        ],
      });
      sunEditor.setContents(html || '');
      sunEditor.onChange = refresh;
      sunEditor.onInput = refresh;
      sunEditor.onKeyUp = refresh;
    }

    function stopRichEditor() {
      if (sunEditor) {
        try {
          sunEditor.destroy();
        } catch (error) {
          /* already gone */
        }
        sunEditor = null;
      }
      richHost.innerHTML = '';
      richHost.hidden = true;
      textArea.hidden = false;
    }

    function setFormat(next, convert) {
      const current = getContent();
      if (next === 'text/html') {
        startRichEditor(convert ? textToHtml(current) : current);
      } else {
        stopRichEditor();
        textArea.value = convert ? htmlToText(current) : current;
      }
      format = next;
      formatInputs.forEach((input) => {
        input.checked = input.value === next;
        input.closest('.scf-type-card').classList.toggle('is-selected', input.checked);
      });
      previewHtml.hidden = next !== 'text/html';
      previewText.hidden = next === 'text/html';
      refresh();
    }

    formatInputs.forEach((input) => {
      input.addEventListener('change', () => {
        if (!input.checked || input.value === format) {
          return;
        }
        const content = getContent();
        if (input.value === 'text/plain' && /<[a-z][^>]*>/i.test(content)
          && !window.confirm('Switch to plain text? Formatting and images are removed; links are kept as addresses.')) {
          formatInputs.forEach((other) => { other.checked = other.value === format; });
          return;
        }
        setFormat(input.value, true);
      });
    });

    function insertVariable(token) {
      if (sunEditor) {
        sunEditor.insertHTML(escapeHtml(token), true);
        refresh();
        return;
      }
      const start = textArea.selectionStart;
      const end = textArea.selectionEnd;
      textArea.setRangeText(token, start, end, 'end');
      textArea.focus();
      refresh();
    }

    function renderVariables(slug) {
      const use = systemUses[slug];
      const variables = use ? use.variables : commonVariables;
      varsList.textContent = '';
      variables.forEach((variable) => {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'sig-var';
        button.dataset.mtVar = variable.token;
        button.title = `Insert ${variable.token}`;
        button.textContent = variable.label;
        item.append(button);
        varsList.append(item);
      });
    }
    varsList.addEventListener('mousedown', (event) => {
      if (event.target instanceof Element && event.target.closest('[data-mt-var]')) {
        event.preventDefault();
      }
    });
    varsList.addEventListener('click', (event) => {
      const button = event.target instanceof Element ? event.target.closest('[data-mt-var]') : null;
      if (button) {
        insertVariable(button.dataset.mtVar);
      }
    });

    // ----- live summary -----------------------------------------------------

    const snapshot = () => JSON.stringify([
      nameInput.value, slugInput.value, descriptionInput.value, format, getContent(),
    ]);
    const isDirty = () => initial !== null && snapshot() !== initial;

    let previewTimer = null;
    function renderPreview(content) {
      window.clearTimeout(previewTimer);
      previewTimer = window.setTimeout(() => {
        if (format === 'text/html') {
          previewFrame.srcdoc = FRAME_HEAD + highlightHtml(content);
        } else {
          renderTextPreview(previewText, content);
        }
      }, 120);
    }

    // The server rendered the chips for the saved key.
    let renderedVariablesFor = systemUses[savedSlug] ? savedSlug : '';
    function refresh() {
      const content = getContent();
      const slug = slugInput.value.trim();
      title.textContent = nameInput.value.trim() || (isNew ? 'New template' : 'Untitled template');
      if (!slugTouched) {
        slugInput.value = slugify(nameInput.value);
      }
      renderPreview(content);

      usedList.textContent = '';
      const used = tokensIn(content);
      used.forEach((path) => {
        const chip = document.createElement('li');
        chip.className = 'scf-chip';
        chip.textContent = path;
        usedList.append(chip);
      });
      usedEmpty.hidden = used.length > 0;

      const tokenText = `{{ template.${slugInput.value.trim() || 'your-key'} }}`;
      usageToken.textContent = tokenText;
      usageToken.dataset.mtCopy = tokenText;

      const use = systemUses[slugInput.value.trim()];
      systemBanner.hidden = !use;
      systemLabel.textContent = use ? use.label.toLowerCase() : '';
      const savedUse = systemUses[lastSavedSlug];
      if (savedUse && slugInput.value.trim() !== lastSavedSlug) {
        slugWarning.textContent = `MyPortal looks up the ${savedUse.label.toLowerCase()} by the key “${lastSavedSlug}”. With a different key it sends its built-in wording instead.`;
        slugWarning.hidden = false;
      } else {
        slugWarning.hidden = true;
      }
      const variablesFor = use ? slug : '';
      if (variablesFor !== renderedVariablesFor) {
        renderVariables(variablesFor);
        renderedVariablesFor = variablesFor;
      }

      dirtyNote.textContent = isDirty() ? 'Unsaved changes' : '';
    }

    nameInput.addEventListener('input', refresh);
    slugInput.addEventListener('input', () => {
      slugTouched = slugInput.value.trim() !== '';
      refresh();
    });
    form.addEventListener('input', refresh);
    usageToken.addEventListener('click', () => copyText(usageToken));

    // ----- save -------------------------------------------------------------

    function validate() {
      const problems = [];
      ['name', 'slug', 'content', 'form'].forEach((key) => setError(key, ''));
      [nameInput, slugInput].forEach((input) => input.classList.remove('is-invalid'));
      if (!nameInput.value.trim()) {
        setError('name', 'Give the template a name.');
        nameInput.classList.add('is-invalid');
        problems.push({ tab: 'details', focus: nameInput });
      }
      const slug = slugInput.value.trim();
      if (!slug || !SLUG_PATTERN.test(slug)) {
        setError('slug', 'Use lowercase letters, numbers, dots, hyphens or underscores, starting and ending with a letter or number.');
        slugInput.classList.add('is-invalid');
        problems.push({ tab: 'details', focus: slugInput });
      }
      if (!getContent().trim()) {
        setError('content', 'Write the message before saving.');
        problems.push({ tab: 'content', focus: sunEditor ? null : textArea });
      }
      return problems;
    }

    async function save() {
      if (submitting) {
        return;
      }
      const problems = validate();
      if (problems.length) {
        const first = problems[0];
        showTab(first.tab);
        if (first.focus) {
          first.focus.focus();
        } else if (sunEditor) {
          sunEditor.core.focus();
        }
        return;
      }
      const payload = {
        slug: slugInput.value.trim(),
        name: nameInput.value.trim(),
        description: descriptionInput.value.trim() || null,
        content_type: format,
        content: getContent(),
      };
      submitting = true;
      saveButton.disabled = true;
      saveButton.classList.add('button--processing');
      try {
        const record = await requestJson(
          isNew ? `${API_URL}/` : `${API_URL}/${encodeURIComponent(templateId)}`,
          { method: isNew ? 'POST' : 'PUT', body: JSON.stringify(payload) },
        );
        if (isNew) {
          window.location.href = `${LIST_URL}/${encodeURIComponent(record.id)}/edit?success=${encodeURIComponent('Template created.')}`;
          return;
        }
        // Saved in place: keep editing without a reload.
        lastSavedSlug = record.slug;
        form.dataset.mtSavedSlug = record.slug;
        initial = snapshot();
        refresh();
        toast('Template saved.');
        submitting = false;
      } catch (error) {
        submitting = false;
        if (/slug/i.test(error.message)) {
          setError('slug', /exists/i.test(error.message)
            ? 'Another template already uses this key. Choose a different one.'
            : error.message);
          slugInput.classList.add('is-invalid');
          showTab('details');
          slugInput.focus();
        } else {
          setError('form', `Couldn't save: ${error.message}`);
        }
      } finally {
        saveButton.disabled = false;
        saveButton.classList.remove('button--processing');
      }
    }

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      save();
    });
    document.addEventListener('keydown', (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        save();
      }
    });

    // ----- leaving, duplicating and deleting --------------------------------

    document.querySelectorAll('a[data-mt-leave]').forEach((link) => {
      link.addEventListener('click', (event) => {
        if (isDirty() && !window.confirm('Leave without saving your changes?')) {
          event.preventDefault();
          return;
        }
        submitting = true;
      });
    });
    window.addEventListener('beforeunload', (event) => {
      if (!submitting && isDirty()) {
        event.preventDefault();
        event.returnValue = '';
      }
    });

    document.querySelectorAll('[data-mt-duplicate]').forEach((button) => {
      button.addEventListener('click', () => {
        if (isDirty() && !window.confirm('Duplicate the last saved version? Your unsaved changes will be lost.')) {
          return;
        }
        submitting = true;
        duplicate(templateId, button);
      });
    });

    const deleteDialog = createDeleteDialog();
    const deleteButton = form.querySelector('[data-mt-delete]');
    if (deleteButton && deleteDialog) {
      deleteButton.addEventListener('click', () => {
        deleteDialog.open({
          id: templateId,
          name: nameInput.value.trim() || 'This template',
          system: (systemUses[lastSavedSlug] || {}).label || '',
          onDeleted: () => { submitting = true; },
        }, deleteButton);
      });
    }

    if (format === 'text/html') {
      startRichEditor(textArea.value);
    }
    initial = snapshot();
    refresh();
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('time[data-mt-date]').forEach((node) => {
      const date = new Date(node.getAttribute('datetime'));
      if (!Number.isNaN(date.getTime())) {
        node.textContent = date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
        node.title = date.toLocaleString();
      }
    });
    const list = document.querySelector('[data-mt-list]');
    if (list) {
      initList(list);
    } else {
      // Filters still work when the server returned nothing to filter.
      const applyButton = document.querySelector('[data-mt-apply]');
      const typeInputs = document.querySelectorAll('[data-mt-type]');
      typeInputs.forEach((input) => input.addEventListener('change', () => input.form.submit()));
      if (applyButton) {
        applyButton.hidden = false;
      }
    }
    const form = document.querySelector('[data-mt-editor]');
    if (form) {
      initEditor(form);
    }
  });
})();
