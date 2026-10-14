(function () {
  'use strict';
  const OPTION_TYPES = new Set(['select', 'radio', 'multiselect']);
  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  let activeModal = null;
  let lastTrigger = null;
  let snapshot = '';
  let inertNodes = [];
  let options = [];
  let editor;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function fieldType() { return qs('[name="field_type"]:checked', editor).value; }
  function modalState() {
    if (!activeModal) return '';
    return JSON.stringify([qsa('input[name], select[name], textarea[name]', activeModal).filter((input) => !input.closest('[data-cf-preview]')).map((input) =>
      [input.name, input.value, input.checked]), activeModal.id === 'cf-field-modal' ? options : []]);
  }
  function isDirty() { return !!activeModal && snapshot !== modalState(); }
  function errorMessage(detail) {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((entry) => entry.msg || String(entry)).join('. ');
    if (detail && detail.errors) return Object.values(detail.errors).join('. ');
    return 'We could not save your changes. Please try again.';
  }
  function showError(message, form) {
    const target = (form && qs('[data-cf-error]', form)) || qs('[data-cf-status]');
    if (!target) return;
    target.textContent = message;
    target.hidden = false;
    target.setAttribute('role', 'alert');
    target.classList.add('cf-message--error');
  }
  async function requestJson(url, method, payload) {
    const headers = { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' };
    const csrf = qs('meta[name="csrf-token"]');
    if (csrf && csrf.content) headers['X-CSRF-Token'] = csrf.content;
    const response = await fetch(url, { method, headers, credentials: 'same-origin', ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) });
    if (response.redirected || response.headers.get('content-type')?.includes('text/html')) {
      throw new Error('Your session may have expired. Reload the page and sign in again.');
    }
    let data = null;
    try { data = await response.json(); } catch (_) { /* use a readable fallback */ }
    if (!response.ok) throw new Error(errorMessage(data && data.detail));
    return data;
  }
  async function saving(form, task) {
    if (form.dataset.saving) return;
    form.dataset.saving = 'true';
    const buttons = qsa('button', form);
    const disabled = buttons.map((button) => button.disabled);
    const submit = qs('button[type="submit"]', form);
    const originalLabel = submit?.textContent;
    buttons.forEach((button) => { button.disabled = true; });
    form.setAttribute('aria-busy', 'true');
    if (submit) submit.textContent = 'Saving…';
    const error = qs('[data-cf-error]', form);
    if (error) error.hidden = true;
    try { await task(); } catch (err) {
      showError(err.message || 'Connection lost. Your changes are still here; please try again.', form);
    } finally {
      delete form.dataset.saving;
      form.removeAttribute('aria-busy');
      buttons.forEach((button, index) => { button.disabled = disabled[index]; });
      if (submit) submit.textContent = originalLabel;
    }
  }
  function navigate(url, message = 'Changes saved.') {
    try { sessionStorage.setItem('cf-saved-message', message); } catch (_) { /* storage is optional */ }
    snapshot = modalState();
    window.location.href = url;
  }
  function openModal(modal, trigger) {
    activeModal = modal;
    lastTrigger = trigger;
    modal.hidden = false;
    modal.classList.add('is-visible');
    document.body.classList.add('cf-modal-open');
    inertNodes = [];
    let current = modal;
    while (current.parentElement && current.parentElement !== document.documentElement) {
      Array.from(current.parentElement.children).forEach((node) => {
        if (node !== current && !node.inert && !['SCRIPT', 'STYLE', 'LINK'].includes(node.tagName)) {
          node.inert = true;
          inertNodes.push(node);
        }
      });
      current = current.parentElement;
    }
    qsa('[data-cf-error]', modal).forEach((node) => { node.hidden = true; });
    snapshot = modalState();
    const first = qs('input:not([type="hidden"]), textarea, select', modal);
    if (first) first.focus();
  }
  function closeModal() {
    if (!activeModal || qs('[data-saving]', activeModal)) return;
    if (isDirty() && !window.confirm('Discard your unsaved changes?')) return;
    activeModal.hidden = true;
    activeModal.classList.remove('is-visible');
    activeModal = null;
    inertNodes.forEach((node) => { node.inert = false; });
    inertNodes = [];
    document.body.classList.remove('cf-modal-open');
    lastTrigger?.focus();
  }
  function focusable() {
    return qsa('button:not([disabled]), a[href], input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), summary', activeModal)
      .filter((node) => node.getClientRects().length && !node.closest('[hidden]'));
  }

  function renderOptions(focusIndex) {
    const list = qs('[data-cf-options]', editor);
    list.replaceChildren();
    options.forEach((value, index) => {
      const row = element('li', 'cf-option');
      const label = element('label', 'sr-only', `Choice ${index + 1}`);
      label.htmlFor = `cf-option-${index}`;
      const input = element('input', 'form-input');
      input.id = label.htmlFor;
      input.value = value;
      input.maxLength = 255;
      input.placeholder = `Choice ${index + 1}`;
      input.addEventListener('input', () => { options[index] = input.value; updatePreview(); });
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); addOption(index + 1); }
      });
      row.append(label, input);
      for (const [text, offset] of [['Move up', -1], ['Move down', 1], ['Remove', 0]]) {
        const button = element('button', 'button button--ghost cf-option__button', text);
        button.type = 'button';
        button.setAttribute('aria-label', `${text} choice ${index + 1}`);
        button.disabled = offset !== 0 && (index + offset < 0 || index + offset >= options.length);
        button.addEventListener('click', () => {
          if (offset) [options[index], options[index + offset]] = [options[index + offset], options[index]];
          else options.splice(index, 1);
          renderOptions(Math.max(0, Math.min(index + offset, options.length - 1)));
          updatePreview();
        });
        row.append(button);
      }
      list.append(row);
    });
    if (!options.length) list.append(element('li', 'form-help', 'Add the choices people can select.'));
    qs('[data-cf-option-add]', editor).disabled = options.length >= 100;
    if (focusIndex !== undefined) {
      const input = qs(`#cf-option-${focusIndex}`, list);
      if (input) input.focus(); else qs('[data-cf-option-add]', editor).focus();
    }
  }
  function addOption(index = options.length) {
    if (options.length >= 100) { showError('A field can have at most 100 choices.', editor); return; }
    options.splice(index, 0, '');
    renderOptions(index);
    updatePreview();
  }
  function updatePreview() {
    const type = fieldType();
    qs('[data-cf-matrix-config]', editor).hidden = type !== 'matrix';
    qs('[data-cf-rating-config]', editor).hidden = type !== 'rating';
    qs('[data-cf-audio-config]', editor).hidden = type !== 'audio';
    const label = qs('[name="label"]', editor).value.trim() || 'Your question';
    const help = qs('[name="help_text"]', editor).value.trim();
    const defaultValue = qs('[name="default_value"]', editor).value;
    const required = qs('[name="required"]', editor).checked && type !== 'hidden';
    const choices = options.map((value) => value.trim()).filter(Boolean);
    qs('[data-cf-options-wrap]', editor).hidden = !OPTION_TYPES.has(type);
    qs('[data-cf-placeholder-wrap]', editor).hidden = !['text', 'textarea', 'email', 'number', 'url', 'password'].includes(type);
    qs('[data-cf-default-wrap]', editor).hidden = ['file', 'audio', 'signature', 'matrix'].includes(type);
    qs('[data-cf-required-wrap]', editor).hidden = type === 'hidden';
    qs('[data-cf-default-help]', editor).textContent = type === 'hidden' ? 'This value is attached to every submission, without showing a question.' :
      type === 'checkbox' ? 'Use true or 1 to check the box by default.' : OPTION_TYPES.has(type) ? 'Use an exact choice above to preselect it.' : 'Prefill this answer. People can change it before submitting.';
    const preview = qs('[data-cf-preview]');
    preview.replaceChildren();
    if (type === 'hidden') {
      preview.append(element('strong', '', label), element('p', 'form-help', 'Hidden from users. The default value is included in the ticket.'), element('p', '', defaultValue || 'No default value'));
    } else {
      const title = element('label', 'form-label', label + (required ? ' (required)' : ''));
      title.htmlFor = 'cf-preview-input';
      preview.append(title);
      let control;
      if (type === 'radio' || type === 'multiselect') {
        title.removeAttribute('for');
        control = element('fieldset', 'cf-preview__choices');
        control.append(element('legend', 'sr-only', label));
        choices.forEach((choice) => {
          const item = element('label', 'cf-preview__choice');
          const input = element('input');
          input.type = type === 'radio' ? 'radio' : 'checkbox';
          input.name = 'cf-preview-choice';
          input.checked = choice === defaultValue;
          item.append(input, element('span', '', choice));
          control.append(item);
        });
        if (!choices.length) control.append(element('p', 'form-help', 'Your choices will appear here.'));
      } else if (type === 'matrix') {
        title.removeAttribute('for');
        control = element('div', 'cf-preview__choices');
        const columns = qs('[name="matrix_columns"]', editor).value.split('\n').map((v) => v.trim()).filter(Boolean);
        const rows = qs('[name="matrix_rows"]', editor).value.split('\n').map((v) => v.trim()).filter(Boolean);
        rows.forEach((row, index) => {
          const rowLabel = element('label', 'form-label', row); rowLabel.htmlFor = `cf-preview-row-${index}`;
          const select = element('select', 'form-input'); select.id = rowLabel.htmlFor;
          select.append(element('option', '', 'Choose an answer…'));
          columns.forEach((col) => select.append(element('option', '', col)));
          control.append(rowLabel, select);
        });
        if (!rows.length) control.append(element('p', 'form-help', 'Add rows and columns to preview the grid.'));
      } else if (type === 'signature') {
        control = element('p', 'form-help', 'People can draw their signature or upload an image.');
      } else if (type === 'rating') {
        control = element('input', 'form-input'); control.type = 'number'; control.min = '1'; control.max = qs('[name="rating_max"]', editor).value; control.placeholder = `1 to ${control.max}`;
      } else if (type === 'select') {
        control = element('select', 'form-input');
        const placeholder = element('option', '', 'Select an option…');
        placeholder.value = '';
        control.append(placeholder);
        choices.forEach((choice) => { const opt = element('option', '', choice); opt.value = choice; control.append(opt); });
        control.value = defaultValue;
      } else if (type === 'textarea') {
        control = element('textarea', 'form-input');
        control.rows = 4;
        control.value = defaultValue;
      } else {
        control = element('input', type === 'checkbox' ? '' : 'form-input');
        control.type = type === 'audio' ? 'file' : type;
        if (type === 'audio') { control.multiple = true; control.accept = 'audio/*'; }
        if (type === 'checkbox') control.checked = ['1', 'true', 'on', 'yes'].includes(defaultValue.toLowerCase());
        else if (!['file', 'audio'].includes(type)) control.value = defaultValue;
      }
      control.id = 'cf-preview-input';
      if (['text', 'textarea', 'email', 'number', 'url', 'password'].includes(type)) control.placeholder = qs('[name="placeholder"]', editor).value;
      preview.append(control);
      if (help) preview.append(element('p', 'form-help', help));
    }
    qs('[data-cf-preview-summary]').textContent = type === 'hidden' ? 'Included automatically in every submission.' : `${required ? 'An answer is required.' : 'People can leave this blank.'} ${OPTION_TYPES.has(type) ? `${choices.length} choices available.` : ''}`;
  }
  function openField(trigger, data) {
    editor.reset();
    qs('[name="field_id"]', editor).value = data?.id || '';
    for (const name of ['label', 'placeholder', 'help_text', 'default_value']) qs(`[name="${name}"]`, editor).value = data?.[name] || '';
    qs('[name="required"]', editor).checked = !!data?.required;
    qsa('[name="field_type"]', editor).forEach((input) => { input.checked = input.value === (data?.field_type || 'text'); });
    options = (data?.options || []).slice();
    qs('[name="matrix_rows"]', editor).value = (data?.config?.rows || []).join('\n');
    qs('[name="matrix_columns"]', editor).value = (data?.config?.columns || []).join('\n');
    qs('[name="rating_max"]', editor).value = data?.config?.max || 5;
    qs('[name="audio_max"]', editor).value = data?.config?.max_clips || 5;
    qs('[data-cf-bulk]', editor).hidden = true;
    qs('[data-cf-bulk-input]', editor).value = '';
    qs('[data-cf-bulk-toggle]', editor).setAttribute('aria-expanded', 'false');
    qs('[data-cf-field-delete]', editor).hidden = !data;
    qs('[data-cf-field-submit]', editor).textContent = data ? 'Save field' : 'Add field';
    qs('#cf-field-title').textContent = data ? 'Edit field' : 'Add field';
    renderOptions();
    updatePreview();
    openModal(qs('#cf-field-modal'), trigger);
  }
  function fieldPayload() {
    const type = fieldType();
    const choices = options.map((value) => value.trim()).filter(Boolean);
    if (OPTION_TYPES.has(type) && !choices.length) throw new Error('Add at least one choice for this field.');
    if (choices.some((value) => value.length > 255)) throw new Error('Each choice must be 255 characters or fewer.');
    const value = (name) => qs(`[name="${name}"]`, editor).value.trim() || null;
    let config = null;
    if (type === 'matrix') {
      config = { rows: qs('[name="matrix_rows"]', editor).value.split('\n').map((v) => v.trim()).filter(Boolean), columns: qs('[name="matrix_columns"]', editor).value.split('\n').map((v) => v.trim()).filter(Boolean) };
      if (!config.rows.length || !config.columns.length) throw new Error('Add at least one matrix row and column.');
    } else if (type === 'rating') config = { max: Number(qs('[name="rating_max"]', editor).value) };
    else if (type === 'audio') config = { max_clips: Number(qs('[name="audio_max"]', editor).value) };
    return {
      config, label: value('label'), field_type: type, required: type !== 'hidden' && qs('[name="required"]', editor).checked,
      placeholder: value('placeholder'), help_text: value('help_text'),
      default_value: ['file', 'audio', 'signature', 'matrix'].includes(type) ? null : value('default_value'), options: OPTION_TYPES.has(type) ? choices : [],
    };
  }
  async function saveAction(form) {
    const data = new FormData(form);
    const action = data.get('action');
    const formId = data.get('form_id');
    const base = `/api/forms/${formId}`;
    if (action === 'create_form' || action === 'update_form') {
      const payload = { name: data.get('name').trim(), description: data.get('description').trim() || null };
      if (action === 'update_form') payload.active = data.get('active') === '1';
      const result = await requestJson(action === 'create_form' ? '/api/forms' : base, action === 'create_form' ? 'POST' : 'PATCH', payload);
      navigate(`/admin/custom-forms?form_id=${result.id}`, action === 'create_form' ? 'Form created. Add your first field.' : 'Form details saved.');
    } else if (action === 'save_field') {
      const fieldId = data.get('field_id');
      await requestJson(`${base}/fields${fieldId ? `/${fieldId}` : ''}`, fieldId ? 'PATCH' : 'POST', fieldPayload());
      navigate(`/admin/custom-forms?form_id=${formId}`);
    } else if (action === 'delete_form') {
      await requestJson(base, 'DELETE');
      navigate('/admin/custom-forms');
    } else if (action === 'move_field') {
      const order = qsa('[data-cf-field]').map((row) => Number(row.dataset.fieldId));
      const index = order.indexOf(Number(data.get('field_id')));
      const next = index + (data.get('direction') === 'up' ? -1 : 1);
      if (index < 0 || next < 0 || next >= order.length) return;
      [order[index], order[next]] = [order[next], order[index]];
      await requestJson(`${base}/fields/reorder`, 'PUT', { field_ids: order });
      navigate(`/admin/custom-forms?form_id=${formId}`);
    } else if (action === 'add_access' || action === 'remove_access') {
      const ids = new Set(qsa('[data-cf-access] [data-user-id]').map((row) => Number(row.dataset.userId)));
      const id = Number(data.get('user_id'));
      if (!Number.isInteger(id) || id < 1) throw new Error('Choose a user to update access.');
      if (action === 'add_access') ids.add(id); else ids.delete(id);
      await requestJson(`${base}/access`, 'PUT', { user_ids: Array.from(ids) });
      navigate(`/admin/custom-forms?form_id=${formId}`);
    }
  }
  function init() {
    editor = qs('[data-cf-field-editor]');
    try {
      const message = sessionStorage.getItem('cf-saved-message');
      if (message) { const status = qs('[data-cf-status]'); status.textContent = message; status.hidden = false; sessionStorage.removeItem('cf-saved-message'); }
    } catch (_) { /* storage is optional */ }
    qsa('[data-cf-create-open]').forEach((trigger) => trigger.addEventListener('click', () => {
      qs('form', qs('#cf-create-modal')).reset(); openModal(qs('#cf-create-modal'), trigger);
    }));
    qsa('[data-cf-details-open]').forEach((trigger) => trigger.addEventListener('click', () => {
      qs('form', qs('#cf-details-modal')).reset(); openModal(qs('#cf-details-modal'), trigger);
    }));
    qsa('.cf-modal [data-modal-close]').forEach((button) => button.addEventListener('click', closeModal));
    document.addEventListener('keydown', (event) => {
      if (!activeModal) return;
      if (event.key === 'Escape') { event.preventDefault(); closeModal(); }
      if (event.key === 'Tab') {
        const items = focusable();
        if (!items.length) return;
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length - 1].focus(); }
        else if (!event.shiftKey && document.activeElement === items[items.length - 1]) { event.preventDefault(); items[0].focus(); }
      }
    });
    window.addEventListener('beforeunload', (event) => { if (isDirty()) { event.preventDefault(); event.returnValue = ''; } });
    qsa('[data-cf-action]').forEach((form) => form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (form.dataset.saving) return;
      if (form.dataset.cfConfirm && !window.confirm(form.dataset.cfConfirm)) return;
      saving(form, () => saveAction(form));
    }));
    qsa('[data-cf-search]').forEach((input) => input.addEventListener('input', () => {
      const kind = input.dataset.cfSearch;
      const rows = qsa(kind === 'forms' ? '[data-cf-form-card]' : '[data-cf-field]');
      const term = input.value.trim().toLocaleLowerCase();
      rows.forEach((row) => {
        const main = kind === 'forms' ? qs('a', row) : qs('.cf-field-row__main', row);
        row.hidden = !main.textContent.toLocaleLowerCase().includes(term);
      });
      qs(`[data-cf-no-results="${kind}"]`).hidden = rows.some((row) => !row.hidden);
    }));
    if (!editor) return;
    for (const eventName of ['input', 'change']) editor.addEventListener(eventName, (event) => { if (!event.target.closest('[data-cf-preview]')) updatePreview(); });
    qs('[data-cf-option-add]', editor).addEventListener('click', () => addOption());
    qs('[data-cf-bulk-toggle]', editor).addEventListener('click', (event) => {
      const bulk = qs('[data-cf-bulk]', editor);
      bulk.hidden = !bulk.hidden;
      event.currentTarget.setAttribute('aria-expanded', String(!bulk.hidden));
      if (!bulk.hidden) qs('[data-cf-bulk-input]', editor).focus();
    });
    qs('[data-cf-bulk-apply]', editor).addEventListener('click', () => {
      const input = qs('[data-cf-bulk-input]', editor);
      const choices = input.value.split('\n').map((line) => line.trim()).filter(Boolean);
      if (choices.length + options.length > 100 || choices.some((value) => value.length > 255)) {
        showError('Use at most 100 choices, with 255 characters or fewer in each.', editor); return;
      }
      options.push(...choices); input.value = ''; renderOptions(); updatePreview();
      qs('[data-cf-bulk]', editor).hidden = true;
      qs('[data-cf-bulk-toggle]', editor).setAttribute('aria-expanded', 'false');
      qs('[data-cf-option-add]', editor).focus();
    });
    qsa('[data-cf-field-add]').forEach((trigger) => trigger.addEventListener('click', () => openField(trigger)));
    qsa('[data-cf-field-edit]').forEach((trigger) => trigger.addEventListener('click', () => {
      try { openField(trigger, JSON.parse(qs(`script[data-cf-field-data="${trigger.dataset.cfFieldEdit}"]`).textContent)); }
      catch (_) { showError('This field could not be loaded. Reload the page and try again.'); }
    }));
    qs('[data-cf-field-delete]', editor).addEventListener('click', () => {
      if (!window.confirm('Delete this field? This cannot be undone.')) return;
      saving(editor, async () => {
        const data = new FormData(editor);
        await requestJson(`/api/forms/${data.get('form_id')}/fields/${data.get('field_id')}`, 'DELETE');
        navigate(`/admin/custom-forms?form_id=${data.get('form_id')}`);
      });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
