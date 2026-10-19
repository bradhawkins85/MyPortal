(function () {
  'use strict';

  // Add or edit an onboarding step that installs Chocolatey packages.
  const modal = document.querySelector('[data-rmm-choco-step-modal]');
  if (!modal) return;
  const form = modal.querySelector('[data-rmm-choco-step-form]');
  const title = modal.querySelector('[data-rmm-choco-step-title]');
  const extra = modal.querySelector('[data-rmm-choco-step-extra]');
  const tagsField = modal.querySelector('[data-rmm-choco-step-tags-field]');
  const tags = modal.querySelector('[data-rmm-choco-step-tags]');
  const carryOn = modal.querySelector('[data-rmm-choco-step-continue]');
  const error = modal.querySelector('[data-rmm-choco-step-error]');
  const submit = modal.querySelector('[data-rmm-choco-step-submit]');
  const picker = window.MyPortalChocoPicker.create({
    search: modal.querySelector('[data-rmm-choco-step-search]'),
    button: modal.querySelector('[data-rmm-choco-step-search-button]'),
    status: modal.querySelector('[data-rmm-choco-step-status]'),
    results: modal.querySelector('[data-rmm-choco-step-results]'),
    multiple: true,
    name: 'rmm-choco-step-app',
  });
  let stepId = null;
  let opener = null;

  function target() { return modal.querySelector('[data-rmm-choco-step-target]:checked').value; }
  function setTarget(value) {
    modal.querySelectorAll('[data-rmm-choco-step-target]').forEach((radio) => { radio.checked = radio.value === value; });
    tagsField.hidden = value !== 'tags';
  }

  function open(item) {
    opener = document.activeElement;
    stepId = item ? item.id : null;
    title.textContent = item ? 'Edit Chocolatey packages' : 'Add Chocolatey packages';
    error.hidden = true;
    extra.value = '';
    picker.select(item ? item.package_ids || [] : []);
    setTarget(item && item.target_mode === 'tags' ? 'tags' : 'all');
    const chosenTags = new Set((item && item.tag_ids) || []);
    Array.from(tags.options).forEach((option) => { option.selected = chosenTags.has(Number(option.value)); });
    carryOn.checked = Boolean(item && item.continue_on_failure);
    modal.hidden = false;
    modal.classList.add('is-visible');
    modal.setAttribute('aria-hidden', 'false');
    modal.querySelector('[data-rmm-choco-step-search]').focus();
    picker.find();
  }

  function close() {
    modal.hidden = true;
    modal.classList.remove('is-visible');
    modal.setAttribute('aria-hidden', 'true');
    if (opener && opener.focus) opener.focus();
  }

  function fail(text) { error.textContent = text; error.hidden = false; }

  modal.querySelectorAll('[data-rmm-choco-step-target]').forEach((radio) => radio.addEventListener('change', () => setTarget(target())));
  modal.querySelectorAll('[data-rmm-choco-step-close]').forEach((button) => button.addEventListener('click', close));
  modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
  document.querySelectorAll('[data-rmm-choco-step-new]').forEach((button) => button.addEventListener('click', () => open(null)));

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const ids = Array.from(new Set(picker.selected().concat(
      extra.value.split(',').map((value) => value.trim().toLowerCase()).filter(Boolean))));
    const tagIds = Array.from(tags.selectedOptions).map((option) => Number(option.value));
    if (!ids.length) { fail('Choose at least one application.'); return; }
    if (target() === 'tags' && !tagIds.length) { fail('Choose at least one tag.'); return; }
    submit.disabled = true;
    try {
      const csrf = document.querySelector('meta[name="csrf-token"]');
      const response = await fetch('/api/rmm/onboarding/steps' + (stepId ? '/' + encodeURIComponent(stepId) : ''), {
        method: stepId ? 'PUT' : 'POST', credentials: 'same-origin',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': csrf ? csrf.content : '' },
        body: JSON.stringify({ kind: 'chocolatey', package_ids: ids, target_mode: target(),
          tag_ids: target() === 'tags' ? tagIds : [], continue_on_failure: carryOn.checked }),
      });
      const data = await response.json();
      if (!response.ok) {
        const errors = data.errors && typeof data.errors === 'object' ? Object.values(data.errors).join(' ') : '';
        throw new Error(errors || (typeof data.detail === 'string' ? data.detail : 'Could not save the step.'));
      }
      window.location.reload();
    } catch (err) {
      fail(err.message);
      submit.disabled = false;
    }
  });

  window.MyPortalOnboardingChoco = { open: open };
})();
