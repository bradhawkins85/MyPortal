(function () {
  'use strict';

  const SLUG_PATTERN = /^[a-z0-9._-]+$/;
  const FRAME_HEAD = "<!doctype html><meta charset='utf-8'><style>"
    + 'body{margin:14px;font:13px/1.45 Arial,sans-serif;color:#1f2937;background:#fff}'
    + 'img{max-width:100%;height:auto}'
    + '.sig-token{background:#fef3c7;color:#92400e;border-radius:3px;padding:0 2px;font-family:ui-monospace,monospace;font-size:12px}'
    + '</style>';

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
      .replace(/^[-.]+|[-.]+$/g, '')
      .slice(0, 120);
  }

  function formatDate(iso) {
    const parts = String(iso || '').split('-').map(Number);
    if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) {
      return iso;
    }
    const date = new Date(parts[0], parts[1] - 1, parts[2]);
    return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  // Wrap {{ tokens }} in text nodes so the live design shows where values go.
  function highlightTokens(html) {
    const doc = new DOMParser().parseFromString(`<body>${html || ''}</body>`, 'text/html');
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
          span.className = 'sig-token';
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

  // ----- list page ----------------------------------------------------------

  function initList() {
    document.querySelectorAll('time[data-sig-date]').forEach((node) => {
      node.textContent = formatDate(node.getAttribute('datetime'));
    });
    const search = document.querySelector('[data-sig-search]');
    if (!search) {
      return;
    }
    const items = Array.from(document.querySelectorAll('[data-sig-item]'));
    const noResults = document.querySelector('[data-sig-no-results]');
    search.addEventListener('input', () => {
      const terms = normalize(search.value).split(/\s+/).filter(Boolean);
      let visible = 0;
      items.forEach((item) => {
        const match = terms.every((term) => normalize(item.dataset.sigSearchText).includes(term));
        item.hidden = !match;
        visible += match ? 1 : 0;
      });
      if (noResults) {
        noResults.hidden = visible > 0;
      }
    });
  }

  // ----- editor -------------------------------------------------------------

  function initEditor(form) {
    const tabsNav = form.querySelector('[data-sig-tabs]');
    const tabs = Array.from(form.querySelectorAll('[data-sig-tab]'));
    const panels = Array.from(form.querySelectorAll('[data-sig-panel]'));
    const title = form.querySelector('[data-sig-title]');
    const nameInput = form.querySelector('[data-sig-name]');
    const slugInput = form.querySelector('[data-sig-slug]');
    const htmlValue = form.querySelector('[data-rich-text-value]');
    const surface = form.querySelector('[data-rich-text-content]');
    const textArea = form.querySelector('[data-sig-text]');
    const liveFrame = form.querySelector('[data-sig-live]');
    const staffSelect = form.querySelector('[data-sig-staff]');
    const startInput = form.querySelector('[data-sig-start]');
    const endInput = form.querySelector('[data-sig-end]');
    const priorityInput = form.querySelector('[data-sig-priority]');
    const defaultInput = form.querySelector('[data-sig-default]');
    const scheduleSummary = form.querySelector('[data-sig-schedule-summary]');
    const dirtyNote = form.querySelector('[data-sig-dirty]');
    const generateText = form.querySelector('[data-sig-generate-text]');
    const serverPreview = form.querySelector('[data-sig-server-preview]');
    const staleNote = form.querySelector('[data-sig-preview-stale]');
    const errors = {};
    form.querySelectorAll('[data-sig-error]').forEach((node) => {
      errors[node.dataset.sigError] = node;
    });
    const isNew = form.dataset.sigNew === 'true';
    let slugTouched = !isNew || Boolean(slugInput.value.trim());
    let lastFocus = null;
    let savedRange = null;
    let submitting = false;

    // Tabs: panels are all visible without JavaScript.
    tabsNav.hidden = false;
    generateText.hidden = false;
    function showTab(name, focus) {
      tabs.forEach((tab) => {
        const active = tab.dataset.sigTab === name;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
        if (active && focus) {
          tab.focus();
        }
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.sigPanel !== name;
      });
    }
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => showTab(tab.dataset.sigTab));
      tab.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
          return;
        }
        event.preventDefault();
        const index = tabs.indexOf(tab);
        const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
        showTab(next.dataset.sigTab, true);
      });
    });
    showTab(isNew ? 'details' : 'design');

    // Snapshot for the unsaved-changes guard, taken once the editor has loaded.
    const snapshot = () => JSON.stringify(Array.from(new FormData(form).entries())
      .filter(([key]) => !['_csrf', 'preview_staff_id'].includes(key)));
    let initial = null;
    // After a server preview the form holds values that were never saved.
    const unsavedOnLoad = form.dataset.sigUnsaved === 'true';
    function hasChanged() {
      return initial !== null && snapshot() !== initial;
    }
    function isDirty() {
      return unsavedOnLoad || hasChanged();
    }

    function setError(name, message) {
      const node = errors[name];
      if (node) {
        node.textContent = message || '';
        node.hidden = !message;
      }
    }

    // Live design preview.
    let frameTimer = null;
    function renderLive() {
      window.clearTimeout(frameTimer);
      frameTimer = window.setTimeout(() => {
        liveFrame.srcdoc = FRAME_HEAD + highlightTokens(htmlValue.value);
      }, 120);
    }

    function describeSchedule() {
      const start = startInput.value;
      const end = endInput.value;
      const priority = Number.parseInt(priorityInput.value, 10) || 0;
      let when = 'Used every day once published';
      if (start && end) {
        when = `Used from ${formatDate(start)} to ${formatDate(end)} once published`;
      } else if (start) {
        when = `Used from ${formatDate(start)} once published`;
      } else if (end) {
        when = `Used until ${formatDate(end)} once published`;
      }
      const rank = defaultInput.checked
        ? `, as the default fallback (priority ${priority})`
        : `, with priority ${priority}`;
      scheduleSummary.textContent = `${when}${rank}.`;
      if (start && end && start > end) {
        setError('schedule', 'The end date must be on or after the start date.');
      } else {
        setError('schedule', '');
      }
    }

    function refresh() {
      title.textContent = nameInput.value.trim() || (isNew ? 'New signature' : 'Edit signature');
      if (!slugTouched) {
        slugInput.value = slugify(nameInput.value);
      }
      describeSchedule();
      renderLive();
      const dirty = isDirty();
      dirtyNote.textContent = dirty ? 'Unsaved changes' : '';
      if (serverPreview && staleNote) {
        staleNote.hidden = !hasChanged();
      }
    }

    nameInput.addEventListener('input', refresh);
    slugInput.addEventListener('input', () => {
      slugTouched = slugInput.value.trim() !== '';
      refresh();
    });
    form.addEventListener('input', refresh);
    form.addEventListener('change', refresh);
    // Toolbar commands update the hidden value on click.
    form.querySelector('[data-rich-text-editor]').addEventListener('click', () => window.setTimeout(refresh, 0));

    // Remember where to insert variables.
    document.addEventListener('selectionchange', () => {
      const selection = window.getSelection();
      if (selection && selection.rangeCount && surface.contains(selection.anchorNode)) {
        savedRange = selection.getRangeAt(0).cloneRange();
        lastFocus = 'design';
      }
    });
    textArea.addEventListener('focus', () => { lastFocus = 'text'; });

    form.querySelectorAll('[data-sig-var]').forEach((button) => {
      button.addEventListener('mousedown', (event) => event.preventDefault());
      button.addEventListener('click', () => {
        const token = button.dataset.sigVar;
        const activePanel = panels.find((panel) => !panel.hidden);
        if (lastFocus === 'text' && activePanel && activePanel.dataset.sigPanel === 'text') {
          const start = textArea.selectionStart;
          const end = textArea.selectionEnd;
          textArea.setRangeText(token, start, end, 'end');
          textArea.focus();
          textArea.dispatchEvent(new Event('input', { bubbles: true }));
          return;
        }
        surface.focus();
        const selection = window.getSelection();
        if (savedRange && surface.contains(savedRange.startContainer)) {
          selection.removeAllRanges();
          selection.addRange(savedRange);
        } else {
          const range = document.createRange();
          range.selectNodeContents(surface);
          range.collapse(false);
          selection.removeAllRanges();
          selection.addRange(range);
        }
        document.execCommand('insertText', false, token);
        surface.dispatchEvent(new Event('input', { bubbles: true }));
      });
    });

    generateText.addEventListener('click', () => {
      if (textArea.value.trim() && !window.confirm('Replace the plain-text version with the text from the design?')) {
        return;
      }
      const clone = surface.cloneNode(true);
      clone.querySelectorAll('br').forEach((br) => br.replaceWith('\n'));
      clone.querySelectorAll('p, div, li, tr, h1, h2, h3, h4').forEach((block) => block.append('\n'));
      clone.querySelectorAll('td').forEach((cell) => cell.append('  '));
      textArea.value = clone.textContent
        .replace(/​/g, '')
        .split('\n')
        .map((line) => line.replace(/[ \t]+$/g, ''))
        .join('\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
      textArea.dispatchEvent(new Event('input', { bubbles: true }));
      textArea.focus();
    });

    function validate(intent) {
      const problems = [];
      setError('name', '');
      setError('slug', '');
      setError('staff', '');
      if (intent === 'preview') {
        if (!staffSelect.value) {
          setError('staff', 'Choose a staff member to preview with.');
          problems.push({ tab: null, focus: staffSelect });
        }
        return problems;
      }
      if (!nameInput.value.trim()) {
        setError('name', 'Give the signature a name.');
        problems.push({ tab: 'details', focus: nameInput });
      }
      const slug = slugInput.value.trim().toLowerCase().replace(/ /g, '-');
      if (!slug || !SLUG_PATTERN.test(slug)) {
        setError('slug', 'Use only lowercase letters, numbers, dots, hyphens or underscores.');
        problems.push({ tab: 'details', focus: slugInput });
      }
      if (startInput.value && endInput.value && startInput.value > endInput.value) {
        problems.push({ tab: 'schedule', focus: endInput });
      }
      return problems;
    }

    form.addEventListener('submit', (event) => {
      const submitter = event.submitter;
      if (submitter && submitter.form && submitter.form !== form) {
        return;
      }
      const intent = submitter && submitter.value === 'preview' ? 'preview' : 'save';
      const problems = validate(intent);
      if (problems.length) {
        event.preventDefault();
        const first = problems[0];
        if (first.tab) {
          showTab(first.tab);
        }
        first.focus.focus();
        return;
      }
      submitting = true;
    });

    const deleteButton = form.querySelector('[data-sig-delete]');
    const deleteForm = document.getElementById('sig-delete-form');
    if (deleteButton && deleteForm) {
      deleteForm.addEventListener('submit', (event) => {
        const name = nameInput.value.trim() || 'this signature';
        if (!window.confirm(`Delete “${name}”? This can't be undone.`)) {
          event.preventDefault();
          return;
        }
        submitting = true;
      });
    }

    // Header actions (publish, disable, duplicate) act on the saved version.
    document.querySelectorAll('[data-sig-guard]').forEach((button) => {
      const actionForm = button.closest('form');
      if (!actionForm) {
        return;
      }
      actionForm.addEventListener('submit', (event) => {
        if (isDirty() && !window.confirm(button.dataset.sigGuard)) {
          event.preventDefault();
          return;
        }
        submitting = true;
      });
    });
    document.querySelectorAll('a[data-sig-leave]').forEach((link) => {
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

    // rich_text_editor.js fills the hidden value on DOMContentLoaded.
    window.setTimeout(() => {
      initial = snapshot();
      refresh();
    }, 0);
  }

  document.addEventListener('DOMContentLoaded', () => {
    initList();
    const form = document.querySelector('[data-sig-editor]');
    if (form) {
      initEditor(form);
    }
  });
})();
