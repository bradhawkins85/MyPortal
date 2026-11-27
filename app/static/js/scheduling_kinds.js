// Booking kinds on /admin/schedule (super admins): the list and the editor,
// opened from the page's Actions menu. Without JavaScript the links load the
// page with ?kinds=1 or ?kind=new|<id> and the server opens the modal.
(function () {
  'use strict';

  const listModal = document.querySelector('[data-sch-kinds-modal]');
  const editModal = document.querySelector('[data-sch-kind-modal]');
  if (!listModal || !editModal) return;
  const form = editModal.querySelector('[data-sch-kind-form]');
  const deleteForm = document.getElementById('sch-kind-delete-form');
  const deleteButton = form.querySelector('[data-sch-kind-delete]');
  const labelInput = form.querySelector('[name="label"]');
  const defaultInput = form.querySelector('[data-sch-kind-default]');
  const defaultHelp = form.querySelector('[data-sch-kind-default-help]');
  const preview = form.querySelector('[data-sch-kind-preview]');
  const previewLabel = form.querySelector('[data-sch-kind-preview-label]');
  const summary = form.querySelector('[data-sch-kind-summary]');
  const labelError = form.querySelector('[data-sch-kind-error="label"]');

  let kinds = [];
  try {
    kinds = JSON.parse((document.getElementById('sch-kinds-data') || {}).textContent || '[]');
  } catch (error) {
    kinds = [];
  }

  let dirty = false;
  let lastTrigger = null;

  function tone() {
    return (form.querySelector('[name="tone"]:checked') || {}).value || 'accent';
  }

  function updatePreview() {
    preview.className = 'sch-block sch-block--tone-' + tone();
    previewLabel.textContent = labelInput.value.trim() || 'New booking kind';
  }

  function show(modal, trigger) {
    lastTrigger = trigger || lastTrigger;
    modal.hidden = false;
    const focusable = modal === editModal ? labelInput : modal.querySelector('[data-sch-kinds-search]');
    if (focusable) focusable.focus();
  }

  function hide(modal) {
    modal.hidden = true;
    if (/[?&](kinds|kind)=/.test(window.location.search)) {
      const params = new URLSearchParams(window.location.search);
      params.delete('kinds');
      params.delete('kind');
      window.history.replaceState(null, '', '/admin/schedule' + (params.toString() ? '?' + params.toString() : ''));
    }
  }

  function openEditor(kind, trigger) {
    const editing = Boolean(kind && kind.id);
    form.setAttribute('action', editing ? '/admin/schedule/kinds/' + kind.id : '/admin/schedule/kinds');
    form.querySelector('[data-sch-kind-title]').textContent = editing ? 'Edit booking kind' : 'Add booking kind';
    labelInput.value = editing ? kind.label : '';
    const radio = form.querySelector(`[name="tone"][value="${CSS.escape(editing ? kind.tone : 'accent')}"]`);
    if (radio) radio.checked = true;
    const isDefault = editing && kind.is_default;
    defaultInput.checked = isDefault;
    defaultInput.disabled = isDefault;
    defaultHelp.hidden = !isDefault;
    const count = editing ? kind.appointment_count : 0;
    summary.textContent = count
      ? `Used by ${count} appointment${count === 1 ? '' : 's'}. Renaming it changes their label too.`
      : 'Not used by any appointment yet.';
    deleteButton.hidden = !editing || isDefault;
    deleteButton.textContent = count ? 'Archive' : 'Delete';
    deleteForm.setAttribute('action', '/admin/schedule/kinds/' + (editing ? kind.id : 0) + '/delete');
    if (labelError) labelError.hidden = true;
    form.querySelectorAll('.alert').forEach((alert) => alert.remove());
    dirty = false;
    updatePreview();
    listModal.hidden = true;
    show(editModal, trigger);
  }

  document.querySelectorAll('[data-sch-kinds-open]').forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      event.preventDefault();
      show(listModal, trigger);
    });
  });

  document.querySelectorAll('[data-sch-kind-new]').forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      event.preventDefault();
      openEditor(null, trigger);
    });
  });

  document.querySelectorAll('[data-sch-kind-edit]').forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      const kind = kinds.find((item) => String(item.id) === trigger.getAttribute('data-sch-kind-edit'));
      if (!kind) return;
      event.preventDefault();
      openEditor(kind, trigger);
    });
  });

  form.addEventListener('input', () => { dirty = true; updatePreview(); });
  form.addEventListener('change', () => { dirty = true; updatePreview(); });

  form.addEventListener('submit', (event) => {
    if (event.submitter && event.submitter.getAttribute('form') === 'sch-kind-delete-form') return;
    if (!labelInput.value.trim()) {
      event.preventDefault();
      if (labelError) labelError.hidden = false;
      labelInput.focus();
      return;
    }
    dirty = false;
  });

  deleteForm.addEventListener('submit', (event) => {
    if (!window.confirm(deleteForm.getAttribute('data-confirm') || 'Are you sure?')) event.preventDefault();
  });

  listModal.querySelectorAll('[data-modal-close]').forEach((control) => {
    control.addEventListener('click', (event) => {
      event.preventDefault();
      hide(listModal);
      if (lastTrigger && lastTrigger.focus) lastTrigger.focus();
    });
  });

  editModal.querySelectorAll('[data-modal-close]').forEach((control) => {
    control.addEventListener('click', (event) => {
      event.preventDefault();
      if (dirty && !window.confirm('Discard your changes to this booking kind?')) return;
      dirty = false;
      hide(editModal);
      show(listModal);
    });
  });

  const search = listModal.querySelector('[data-sch-kinds-search]');
  const empty = listModal.querySelector('[data-sch-kinds-empty]');
  if (search) {
    search.addEventListener('input', () => {
      const query = search.value.trim().toLowerCase();
      let shown = 0;
      listModal.querySelectorAll('[data-sch-kind-item]').forEach((item) => {
        const match = !query || (item.getAttribute('data-search-text') || '').toLowerCase().includes(query);
        item.hidden = !match;
        if (match) shown += 1;
      });
      if (empty) empty.hidden = shown > 0;
    });
  }

  window.addEventListener('beforeunload', (event) => {
    if (dirty && !editModal.hidden) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  updatePreview();
  if (listModal.hasAttribute('data-sch-open-on-load')) show(listModal, null);
  if (editModal.hasAttribute('data-sch-open-on-load')) show(editModal, null);
})();
