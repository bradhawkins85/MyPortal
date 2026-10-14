(function () {
  'use strict';

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) {
      return meta.getAttribute('content');
    }
    const match = document.cookie.match(/(?:^|; )myportal_session_csrf=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  async function requestJson(url, options) {
    const headers = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
    };
    const csrfToken = getCsrfToken();
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
    const response = await fetch(url, { credentials: 'same-origin', headers, ...options });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        if (data && data.detail) {
          detail = Array.isArray(data.detail) ? data.detail.map((entry) => entry.msg || entry).join(', ') : data.detail;
        }
      } catch (error) {
        /* keep the status text */
      }
      throw new Error(detail);
    }
    return response.status !== 204 ? response.json() : null;
  }

  function reload() {
    window.location.reload();
  }

  function flashError(message) {
    window.alert(message);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function bindCreateForm() {
    const form = document.querySelector('[data-cf-create-form]');
    if (!form) return;
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const name = form.querySelector('[name="name"]').value.trim();
      const description = form.querySelector('[name="description"]').value.trim() || null;
      if (!name) return;
      try {
        await requestJson('/api/forms', {
          method: 'POST',
          body: JSON.stringify({ name, description }),
        });
        window.location.href = '/admin/custom-forms';
      } catch (error) {
        flashError(error.message);
      }
    });
  }

  function bindDeleteForm() {
    document.querySelectorAll('[data-cf-delete-form]').forEach((button) => {
      button.addEventListener('click', async () => {
        const formId = button.getAttribute('data-cf-delete-form');
        if (!window.confirm(button.getAttribute('data-cf-confirm') || 'Delete this form?')) return;
        try {
          await requestJson(`/api/forms/${formId}`, { method: 'DELETE' });
          reload();
        } catch (error) {
          flashError(error.message);
        }
      });
    });
  }

  function bindUpdateForm() {
    const form = document.querySelector('[data-cf-update-form]');
    if (!form) return;
    const formId = form.getAttribute('data-form-id');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const name = form.querySelector('[name="name"]').value.trim();
      const description = form.querySelector('[name="description"]').value.trim() || null;
      const active = form.querySelector('[name="active"]').checked;
      try {
        await requestJson(`/api/forms/${formId}`, {
          method: 'PATCH',
          body: JSON.stringify({ name, description, active }),
        });
        reload();
      } catch (error) {
        flashError(error.message);
      }
    });
  }

  const OPTION_TYPES = new Set(['select', 'radio', 'multiselect']);

  function splitLines(raw) {
    return String(raw == null ? '' : raw)
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function readFieldData(fieldId) {
    const tag = document.querySelector(`script[data-cf-field-data="${fieldId}"]`);
    if (!tag) return null;
    try {
      return JSON.parse(tag.textContent || 'null');
    } catch (error) {
      return null;
    }
  }

  function syncFieldEditorVisibility(editor) {
    const typeSelect = editor.querySelector('[data-cf-field-type]');
    const optionsWrap = editor.querySelector('[data-cf-options-wrap]');
    const type = typeSelect ? typeSelect.value : '';
    if (optionsWrap) optionsWrap.hidden = !OPTION_TYPES.has(type);
    editor.querySelectorAll('[data-cf-config-for]').forEach((section) => {
      section.hidden = section.getAttribute('data-cf-config-for') !== type;
    });
  }

  function readFieldEditorConfig(editor) {
    const typeSelect = editor.querySelector('[data-cf-field-type]');
    const type = typeSelect ? typeSelect.value : '';
    if (type === 'matrix') {
      const rows = editor.querySelector('[data-cf-matrix-rows]');
      const cols = editor.querySelector('[data-cf-matrix-cols]');
      return { rows: splitLines(rows && rows.value), columns: splitLines(cols && cols.value) };
    }
    if (type === 'rating') {
      const el = editor.querySelector('[data-cf-rating-max]');
      return { max: (el && parseInt(el.value, 10)) || 5 };
    }
    if (type === 'audio') {
      const el = editor.querySelector('[data-cf-audio-max]');
      return { max_clips: (el && parseInt(el.value, 10)) || 5 };
    }
    return {};
  }

  function resetFieldEditorConfig(editor) {
    const rows = editor.querySelector('[data-cf-matrix-rows]');
    const cols = editor.querySelector('[data-cf-matrix-cols]');
    const rating = editor.querySelector('[data-cf-rating-max]');
    const audio = editor.querySelector('[data-cf-audio-max]');
    if (rows) rows.value = '';
    if (cols) cols.value = '';
    if (rating) rating.value = '5';
    if (audio) audio.value = '5';
  }

  function populateFieldEditorConfig(editor, config, type) {
    resetFieldEditorConfig(editor);
    const cfg = config || {};
    const rows = editor.querySelector('[data-cf-matrix-rows]');
    const cols = editor.querySelector('[data-cf-matrix-cols]');
    const rating = editor.querySelector('[data-cf-rating-max]');
    const audio = editor.querySelector('[data-cf-audio-max]');
    if (type === 'matrix') {
      if (rows) rows.value = (cfg.rows || []).join('\n');
      if (cols) cols.value = (cfg.columns || []).join('\n');
    } else if (type === 'rating') {
      if (rating) rating.value = cfg.max || 5;
    } else if (type === 'audio') {
      if (audio) audio.value = cfg.max_clips || 5;
    }
  }

  function bindFieldEditor() {
    const editor = document.querySelector('[data-cf-field-editor]');
    if (!editor) return;
    const formId = editor.getAttribute('data-form-id');
    const typeSelect = editor.querySelector('[data-cf-field-type]');
    const submitBtn = editor.querySelector('[data-cf-field-submit]');

    typeSelect.addEventListener('change', () => syncFieldEditorVisibility(editor));
    editor.querySelector('[data-cf-field-cancel]').addEventListener('click', () => {
      editor.reset();
      editor.querySelector('[data-cf-field-id]').value = '';
      if (submitBtn) submitBtn.textContent = 'Add field';
      resetFieldEditorConfig(editor);
      syncFieldEditorVisibility(editor);
    });

    editor.addEventListener('submit', async (event) => {
      event.preventDefault();
      const fieldId = editor.querySelector('[data-cf-field-id]').value;
      const options = splitLines(editor.querySelector('[data-cf-options-input]').value);
      const payload = {
        label: editor.querySelector('[name="label"]').value.trim(),
        field_type: typeSelect.value,
        required: editor.querySelector('[data-cf-required]').checked,
        placeholder: editor.querySelector('[name="placeholder"]').value.trim() || null,
        help_text: editor.querySelector('[name="help_text"]').value.trim() || null,
        default_value: editor.querySelector('[name="default_value"]').value.trim() || null,
        options: OPTION_TYPES.has(typeSelect.value) ? options : [],
        config: readFieldEditorConfig(editor),
      };
      if (!payload.label) return;
      try {
        if (fieldId) {
          await requestJson(`/api/forms/${formId}/fields/${fieldId}`, {
            method: 'PATCH',
            body: JSON.stringify(payload),
          });
        } else {
          await requestJson(`/api/forms/${formId}/fields`, {
            method: 'POST',
            body: JSON.stringify(payload),
          });
        }
        reload();
      } catch (error) {
        flashError(error.message);
      }
    });

    syncFieldEditorVisibility(editor);
  }

  function currentAccess() {
    return Array.from(document.querySelectorAll('[data-cf-access] [data-user-id]')).map(
      (li) => parseInt(li.getAttribute('data-user-id'), 10),
    );
  }

  function bindFieldRows() {
    const editor = document.querySelector('[data-cf-field-editor]');
    if (!editor) return;
    const formId = editor.getAttribute('data-form-id');

    const currentOrder = () =>
      Array.from(document.querySelectorAll('[data-cf-fields] [data-cf-field]')).map(
        (row) => parseInt(row.getAttribute('data-field-id'), 10),
      );

    document.querySelectorAll('[data-cf-field-up], [data-cf-field-down]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const isUp = btn.hasAttribute('data-cf-field-up');
        const id = parseInt(isUp ? btn.getAttribute('data-cf-field-up') : btn.getAttribute('data-cf-field-down'), 10);
        const order = currentOrder();
        const idx = order.indexOf(id);
        const swap = isUp ? idx - 1 : idx + 1;
        if (idx < 0 || swap < 0 || swap >= order.length) return;
        [order[idx], order[swap]] = [order[swap], order[idx]];
        try {
          await requestJson(`/api/forms/${formId}/fields/reorder`, {
            method: 'PUT',
            body: JSON.stringify({ order }),
          });
          reload();
        } catch (error) {
          flashError(error.message);
        }
      });
    });

    document.querySelectorAll('[data-cf-field-edit]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const data = readFieldData(btn.getAttribute('data-cf-field-edit'));
        if (!data) return;
        const form = document.querySelector('[data-cf-field-editor]');
        form.querySelector('[data-cf-field-id]').value = data.id;
        form.querySelector('[name="label"]').value = data.label || '';
        form.querySelector('[data-cf-field-type]').value = data.field_type;
        form.querySelector('[data-cf-options-input]').value = (data.options || []).join('\n');
        form.querySelector('[name="placeholder"]').value = data.placeholder || '';
        form.querySelector('[name="help_text"]').value = data.help_text || '';
        form.querySelector('[name="default_value"]').value = data.default_value || '';
        form.querySelector('[data-cf-required]').checked = !!data.required;
        form.querySelector('[data-cf-field-submit]').textContent = 'Save field';
        populateFieldEditorConfig(form, data.config, data.field_type);
        syncFieldEditorVisibility(form);
        form.scrollIntoView({ behavior: 'smooth', block: 'center' });
        form.querySelector('[name="label"]').focus();
      });
    });

    document.querySelectorAll('[data-cf-field-delete]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const fieldId = btn.getAttribute('data-cf-field-delete');
        if (!window.confirm(btn.getAttribute('data-cf-confirm') || 'Remove this field?')) return;
        try {
          await requestJson(`/api/forms/${formId}/fields/${fieldId}`, { method: 'DELETE' });
          reload();
        } catch (error) {
          flashError(error.message);
        }
      });
    });
  }

  function bindAccess() {
    const removeBtn = document.querySelector('[data-cf-access-remove]');
    const addForm = document.querySelector('[data-cf-access-add]');
    if (!removeBtn && !addForm) return;
    const formId = (addForm || removeBtn).getAttribute('data-form-id');

    document.querySelectorAll('[data-cf-access-remove]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const uid = parseInt(btn.getAttribute('data-cf-access-remove'), 10);
        const user_ids = currentAccess().filter((id) => id !== uid);
        try {
          await requestJson(`/api/forms/${formId}/access`, {
            method: 'PUT',
            body: JSON.stringify({ user_ids }),
          });
          reload();
        } catch (error) {
          flashError(error.message);
        }
      });
    });

    if (addForm) {
      addForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const uid = parseInt(addForm.querySelector('[name="user_id"]').value, 10);
        if (!Number.isFinite(uid) || uid < 1) return;
        const user_ids = currentAccess();
        if (!user_ids.includes(uid)) user_ids.push(uid);
        try {
          await requestJson(`/api/forms/${formId}/access`, {
            method: 'PUT',
            body: JSON.stringify({ user_ids }),
          });
          reload();
        } catch (error) {
          flashError(error.message);
        }
      });
    }
  }

  function selectTab(name) {
    const tabs = document.querySelectorAll('[data-cf-tab]');
    const panels = document.querySelectorAll('[data-cf-panel]');
    tabs.forEach((tab) => {
      const active = tab.getAttribute('data-cf-tab') === name;
      if (tab.hasAttribute('disabled')) return;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      tab.tabIndex = active ? 0 : -1;
    });
    panels.forEach((panel) => {
      panel.hidden = panel.getAttribute('data-cf-panel') !== name;
    });
  }

  function bindTabs() {
    document.querySelectorAll('[data-cf-tab]').forEach((tab) => {
      tab.addEventListener('click', () => {
        if (tab.hasAttribute('disabled')) return;
        selectTab(tab.getAttribute('data-cf-tab'));
      });
    });
    document.querySelectorAll('[data-cf-goto]').forEach((link) => {
      link.addEventListener('click', () => {
        selectTab(link.getAttribute('data-cf-goto'));
      });
    });
  }

  function formatSubmissionData(data) {
    if (!data || typeof data !== 'object') return '<span class="text-muted">&mdash;</span>';
    const entries = Object.entries(data);
    if (!entries.length) return '<span class="text-muted">&mdash;</span>';
    const parts = entries.map(([key, value]) => {
      let display;
      if (Array.isArray(value)) {
        display = value.length ? value.map(String).join(', ') : '';
      } else if (value == null || value === '') {
        display = '';
      } else if (typeof value === 'string' && value.startsWith('data:image')) {
        display = '(image)';
      } else if (value && typeof value === 'object') {
        display = JSON.stringify(value);
      } else {
        display = String(value);
      }
      if (display.length > 60) display = `${display.slice(0, 60)}&hellip;`;
      return `<span class="cf-submission-kv"><strong>${escapeHtml(key)}</strong>: ${escapeHtml(display) || '&mdash;'}</span>`;
    });
    return parts.join(' ');
  }

  async function loadSubmissions() {
    const container = document.querySelector('[data-cf-submissions]');
    if (!container) return;
    const formId = container.getAttribute('data-form-id');
    container.innerHTML = '<p class="text-muted">Loading&hellip;</p>';
    try {
      const submissions = await requestJson(`/api/forms/${formId}/submissions`);
      const list = Array.isArray(submissions) ? submissions : [];
      if (!list.length) {
        container.innerHTML = '<p class="text-muted">No submissions yet.</p>';
        return;
      }
      const rows = list
        .map((submission) => {
          const ticketCell = submission.ticket_id
            ? `<a href="/admin/tickets?focus=${submission.ticket_id}">${submission.ticket_id}</a>`
            : '<span class="text-muted">&mdash;</span>';
          const date = submission.created_at
            ? new Date(submission.created_at).toLocaleString()
            : '&mdash;';
          return `<tr>
            <td>#${submission.id}</td>
            <td>${submission.submitted_by ? submission.submitted_by : '<span class="text-muted">&mdash;</span>'}</td>
            <td>${ticketCell}</td>
            <td class="cf-submission-date">${date}</td>
            <td class="cf-submission-data">${formatSubmissionData(submission.data)}</td>
          </tr>`;
        })
        .join('');
      container.innerHTML = `<div class="table-wrapper">
        <table class="table" data-cf-submissions-table>
          <thead><tr><th scope="col">#</th><th scope="col">Submitted by</th><th scope="col">Ticket</th><th scope="col">Received</th><th scope="col">Data</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
    } catch (error) {
      container.innerHTML = `<p class="text-muted">Could not load submissions: ${escapeHtml(error.message)}</p>`;
    }
  }

  function bindHeaderNewForm() {
    const btn = document.getElementById('cf-new-form');
    if (!btn) return;
    btn.addEventListener('click', () => {
      selectTab('forms');
      const panel = document.querySelector('[data-cf-create-panel]');
      const input = document.querySelector('[data-cf-create-form] [name="name"]');
      if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
      if (input) input.focus();
    });
  }

  function init() {
    bindTabs();
    bindCreateForm();
    bindDeleteForm();
    bindUpdateForm();
    bindFieldEditor();
    bindFieldRows();
    bindAccess();
    bindHeaderNewForm();
    loadSubmissions();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
