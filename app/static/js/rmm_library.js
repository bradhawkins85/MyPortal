(function () {
  'use strict';

  // Script library (rmm/scripts.html): the left navigation lists folders and
  // scripts; choosing one swaps the right-hand panel without a full page load.
  // Without JavaScript every link is an ordinary page load.

  const library = document.querySelector('[data-rmm-library]');
  const nav = library && library.querySelector('[data-rmm-nav]');
  const detail = library && library.querySelector('[data-rmm-detail]');
  if (!library || !nav || !detail) {
    return;
  }

  const shared = window.MyPortalRmm || {};
  const attempted = new Set();
  let loadToken = 0;

  function formatDate(value) {
    if (typeof shared.formatDate === 'function') {
      return shared.formatDate(value);
    }
    return value;
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  // ------------------------------------------------------------------ //
  // Folder tree and search
  // ------------------------------------------------------------------ //

  const folders = Array.from(nav.querySelectorAll('[data-rmm-folder]'));
  const expandAll = nav.querySelector('[data-rmm-tree-expand]');
  const collapseAll = nav.querySelector('[data-rmm-tree-collapse]');
  const search = nav.querySelector('[data-rmm-script-search]');
  // The folders a technician had open before searching, restored when the search is cleared.
  let openBeforeSearch = null;

  function openAncestors(node) {
    let folder = node && node.closest('[data-rmm-folder]');
    while (folder) {
      folder.open = true;
      folder = folder.parentElement && folder.parentElement.closest('[data-rmm-folder]');
    }
  }

  if (folders.length) {
    [expandAll, collapseAll].forEach((button) => { if (button) { button.hidden = false; } });
    if (expandAll) {
      expandAll.addEventListener('click', () => folders.forEach((folder) => { if (!folder.hidden) { folder.open = true; } }));
    }
    if (collapseAll) {
      collapseAll.addEventListener('click', () => {
        folders.forEach((folder) => { folder.open = false; });
        // Keep the open script in view.
        openAncestors(nav.querySelector('[data-rmm-script-link][aria-current="true"]'));
      });
    }
  }

  function applySearch(term) {
    if (term && openBeforeSearch === null) {
      openBeforeSearch = new Set(folders.filter((folder) => folder.open));
    }
    let visible = 0;
    nav.querySelectorAll('[data-rmm-script-item]').forEach((item) => {
      const match = !term || (item.dataset.search || '').includes(term);
      item.hidden = !match;
      if (match) {
        visible += 1;
      }
    });
    // Deepest folders first, so a parent sees whether any child folder still shows.
    folders.slice().reverse().forEach((folder) => {
      const hasMatch = Boolean(folder.querySelector('[data-rmm-script-item]:not([hidden])'));
      const row = folder.parentElement;
      if (row) {
        row.hidden = Boolean(term) && !hasMatch;
      }
      if (term) {
        folder.open = hasMatch;
      }
    });
    if (!term && openBeforeSearch !== null) {
      folders.forEach((folder) => { folder.open = openBeforeSearch.has(folder); });
      openBeforeSearch = null;
    }
    const empty = nav.querySelector('[data-rmm-script-empty]');
    if (empty) {
      empty.hidden = visible > 0;
    }
  }

  if (search) {
    search.addEventListener('input', () => applySearch(search.value.trim().toLowerCase()));
    search.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && search.value) {
        search.value = '';
        applySearch('');
      }
    });
  }

  // ------------------------------------------------------------------ //
  // Pinned navigation
  // ------------------------------------------------------------------ //

  // With the top navigation the list is pinned below the sticky top bar,
  // whose height changes when its menu wraps.
  const topBar = document.querySelector('.layout__sidebar');
  function syncTopBarHeight() {
    const height = document.body.classList.contains('navigation--top') && topBar ? topBar.offsetHeight : 0;
    library.style.setProperty('--rmm-topbar-height', height + 'px');
  }
  syncTopBarHeight();
  if (topBar && typeof ResizeObserver === 'function') {
    new ResizeObserver(syncTopBarHeight).observe(topBar);
  }
  if (typeof MutationObserver === 'function') {
    new MutationObserver(syncTopBarHeight).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  }

  // Scroll the list itself (not the page) so the open script is visible.
  function revealInNav(link) {
    if (!link) {
      return;
    }
    const navBox = nav.getBoundingClientRect();
    const linkBox = link.getBoundingClientRect();
    if (linkBox.top < navBox.top || linkBox.bottom > navBox.bottom) {
      nav.scrollTop += linkBox.top - navBox.top - nav.clientHeight / 3;
    }
  }

  // ------------------------------------------------------------------ //
  // Panel navigation
  // ------------------------------------------------------------------ //

  // Only ever load this page: a script id, or '' for the overview.
  function scriptIdFrom(href) {
    const value = new URL(href, window.location.href).searchParams.get('script') || '';
    return /^[0-9]+$/.test(value) ? value : '';
  }

  function panelUrl(scriptId) {
    return '/rmm/scripts' + (scriptId ? '?script=' + encodeURIComponent(scriptId) : '');
  }

  function markCurrent(scriptId) {
    nav.querySelectorAll('[data-rmm-nav-link]').forEach((link) => {
      const current = (link.getAttribute('data-rmm-script-link') || '') === scriptId;
      if (current) {
        link.setAttribute('aria-current', 'true');
        openAncestors(link);
        revealInNav(link);
      } else {
        link.removeAttribute('aria-current');
      }
    });
  }

  async function loadPanel(scriptId, options) {
    const url = panelUrl(scriptId);
    const settings = Object.assign({ push: true, focus: true }, options || {});
    const token = ++loadToken;
    detail.setAttribute('aria-busy', 'true');
    let response;
    try {
      response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'text/html' } });
    } catch (error) {
      window.location.href = url;
      return;
    }
    if (token !== loadToken) {
      return;
    }
    const html = response.ok ? await response.text() : '';
    const doc = html ? new DOMParser().parseFromString(html, 'text/html') : null;
    const incoming = doc && doc.querySelector('[data-rmm-detail]');
    if (!incoming) {
      // Signed out, lost access or a server error: let the browser show it.
      window.location.href = url;
      return;
    }
    detail.replaceChildren(...Array.from(incoming.childNodes).map((node) => document.importNode(node, true)));
    detail.removeAttribute('aria-busy');
    if (doc.title) {
      document.title = doc.title;
    }
    if (settings.push) {
      window.history.pushState({ rmmLibrary: true }, '', url);
    }
    markCurrent(scriptId);
    initPanel();
    if (settings.focus) {
      const heading = detail.querySelector('h2');
      if (heading) {
        heading.setAttribute('tabindex', '-1');
        heading.focus({ preventScroll: true });
      }
      if (detail.getBoundingClientRect().top < 0 || window.matchMedia('(max-width: 1024px)').matches) {
        detail.scrollIntoView({ block: 'start' });
      }
    }
  }

  nav.addEventListener('click', (event) => {
    const link = event.target.closest('[data-rmm-nav-link]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    if (link.getAttribute('aria-current') === 'true') {
      return;
    }
    loadPanel(scriptIdFrom(link.href));
  });

  window.addEventListener('popstate', () => {
    loadPanel(scriptIdFrom(window.location.href), { push: false, focus: false });
  });

  // ------------------------------------------------------------------ //
  // AI summary and source
  // ------------------------------------------------------------------ //

  async function generateSummary(card) {
    const scriptId = card.getAttribute('data-rmm-ai');
    const button = card.querySelector('[data-rmm-ai-generate]');
    const busy = card.querySelector('[data-rmm-ai-busy]');
    const failure = card.querySelector('[data-rmm-ai-error]');
    const empty = card.querySelector('[data-rmm-ai-empty]');
    attempted.add(scriptId);
    if (button) {
      button.disabled = true;
    }
    if (busy) {
      busy.hidden = false;
    }
    if (empty) {
      empty.hidden = true;
    }
    if (failure) {
      failure.hidden = true;
    }
    try {
      const response = await fetch('/api/rmm/scripts/' + encodeURIComponent(scriptId) + '/summary', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json', 'X-CSRF-Token': csrfToken() },
      });
      let data = {};
      try {
        data = await response.json();
      } catch (error) {
        data = {};
      }
      if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'The summary could not be written (' + response.status + ').');
      }
      // Show the new summary only if the same script is still open.
      const open = detail.querySelector('[data-rmm-script-detail]');
      if (open && open.getAttribute('data-rmm-script-detail') === scriptId) {
        await loadPanel(scriptId, { push: false, focus: false });
      }
    } catch (error) {
      if (!card.isConnected) {
        return;
      }
      if (busy) {
        busy.hidden = true;
      }
      if (empty) {
        empty.hidden = false;
      }
      if (failure) {
        failure.textContent = error.message;
        failure.hidden = false;
      }
      if (button) {
        button.disabled = false;
      }
    }
  }

  function initFileEditor() {
    const editor = detail.querySelector('[data-rmm-file-editor]');
    if (!editor) return;
    const modal = editor.querySelector('[data-rmm-file-modal]');
    const form = editor.querySelector('form');
    const trigger = editor.querySelector('summary');
    const errorNode = editor.querySelector('[data-rmm-file-error]');
    const loading = editor.querySelector('[data-rmm-file-loading]');
    // Keep the text input as a no-JavaScript fallback, but show a picker as
    // soon as the modal opens rather than waiting for datalist suggestions.
    const sourceInput = form.elements.source_path;
    const source = document.createElement('select');
    ['id', 'name', 'class', 'required', 'aria-describedby'].forEach((attribute) => {
      source.setAttribute(attribute, sourceInput.getAttribute(attribute));
    });
    sourceInput.replaceWith(source);
    const save = form.querySelector('[type="submit"]');
    const destination = form.elements.destination;
    const index = form.elements.index;
    let lastTrigger = trigger;
    let snapshot = '';
    let loaded = false;
    let loadingFiles = false;
    let available = [];
    let saving = false;
    function renderSource(value, message) {
      const placeholder = document.createElement('option');
      placeholder.value = message ? value : '';
      placeholder.textContent = message || (available.length ? 'Choose a file…' : 'No files available');
      placeholder.disabled = true;
      const options = message ? [] : available.map((path) => {
        const option = document.createElement('option');
        option.value = path;
        option.textContent = path;
        return option;
      });
      // Preserve an existing association even if its source was removed from
      // Gitea; the server still validates the path when saving.
      if (!message && value && !available.includes(value)) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value + ' (unavailable)';
        options.push(option);
      }
      source.replaceChildren(placeholder, ...options);
      source.value = value;
      source.setAttribute('aria-busy', String(loadingFiles));
      save.disabled = saving || !loaded || !available.length;
    }
    modal.classList.add('modal');
    modal.hidden = true;
    form.classList.add('modal__panel', 'modal__panel--wide');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'rmm-file-editor-title');
    trigger.setAttribute('aria-haspopup', 'dialog');
    modal.id = 'rmm-file-modal';
    trigger.setAttribute('aria-controls', modal.id);
    editor.querySelectorAll('[data-rmm-file-close]').forEach((button) => { button.hidden = false; });
    const state = () => JSON.stringify([source.value, destination.value]);
    function close() {
      if (saving) return;
      if (state() !== snapshot && !window.confirm('Discard unsaved changes to this file?')) return;
      editor.open = false;
      modal.hidden = true;
      lastTrigger.focus();
    }
    async function open(button, rowIndex) {
      lastTrigger = button;
      index.value = rowIndex === undefined ? '' : String(rowIndex);
      renderSource(button.dataset.source || '', loaded ? '' : 'Loading…');
      destination.value = button.dataset.destination || '';
      snapshot = state();
      errorNode.hidden = true;
      editor.open = true;
      modal.hidden = false;
      source.focus();
      if (loaded || loadingFiles) return;
      loadingFiles = true;
      source.setAttribute('aria-busy', 'true');
      loading.hidden = false;
      try {
        const response = await fetch('/api/rmm/scripts/' + editor.dataset.rmmFileEditor + '/files', { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Could not load Private-Files files.');
        available = data.available;
        // Retry next time while Gitea could not create the Private-Files folders.
        loaded = !data.warning;
        if (data.warning) throw new Error(data.warning);
      } catch (error) {
        errorNode.textContent = error.message;
        errorNode.hidden = false;
      } finally {
        loadingFiles = false;
        loading.hidden = true;
        renderSource(source.value, loaded ? '' : 'Could not load files');
      }
    }
    trigger.addEventListener('click', (event) => { event.preventDefault(); open(trigger); });
    detail.querySelectorAll('[data-rmm-file-edit]').forEach((button) => {
      button.hidden = false;
      button.addEventListener('click', () => open(button, button.dataset.rmmFileEdit));
    });
    editor.querySelectorAll('[data-rmm-file-close]').forEach((button) => button.addEventListener('click', close));
    modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
    modal.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(modal.querySelectorAll('button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([hidden]):not([disabled])'));
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (saving || !loaded || !available.length || !form.reportValidity()) return;
      saving = true;
      save.disabled = true;
      save.textContent = 'Saving…';
      errorNode.hidden = true;
      try {
        const response = await fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: { Accept: 'application/json', 'X-CSRF-Token': csrfToken() } });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Could not save the file.');
        window.location.assign('/rmm/scripts?script=' + editor.dataset.rmmFileEditor);
      } catch (error) {
        errorNode.textContent = error.message;
        errorNode.hidden = false;
        saving = false;
        save.disabled = false;
        save.textContent = 'Save file';
        errorNode.setAttribute('tabindex', '-1');
        errorNode.focus();
      }
    });
  }

  function initPanel() {
    initFileEditor();
    detail.querySelectorAll('[data-utc]').forEach((node) => {
      node.textContent = formatDate(node.getAttribute('data-utc'));
    });
    const card = detail.querySelector('[data-rmm-ai]');
    if (card) {
      const button = card.querySelector('[data-rmm-ai-generate]');
      if (button) {
        button.addEventListener('click', () => generateSummary(card));
      }
      // Write a missing or out-of-date summary once per script per visit.
      if (card.hasAttribute('data-rmm-ai-auto') && !attempted.has(card.getAttribute('data-rmm-ai'))) {
        generateSummary(card);
      }
    }
    const copy = detail.querySelector('[data-rmm-copy-source]');
    const source = detail.querySelector('[data-rmm-source]');
    if (copy && source && navigator.clipboard) {
      copy.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(source.textContent.replace(/\n$/, ''));
          copy.textContent = 'Copied';
        } catch (error) {
          copy.textContent = 'Copy failed';
        }
        window.setTimeout(() => { copy.textContent = 'Copy'; }, 2000);
      });
    } else if (copy) {
      copy.hidden = true;
    }
  }

  window.history.replaceState({ rmmLibrary: true }, '');
  revealInNav(nav.querySelector('[data-rmm-script-link][aria-current="true"]'));
  initPanel();
})();
