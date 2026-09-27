(() => {
  const createDialog = document.querySelector('#rack-create-dialog');
  const placeDialog = document.querySelector('#rack-place-dialog');
  const form = document.querySelector('[data-placement-form]');
  document.querySelector('[data-rack-create-open]')?.addEventListener('click', () => createDialog.showModal());
  document.querySelectorAll('[data-dialog-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); }));
  if (!form || !placeDialog) return;
  const preview = form.querySelector('[data-placement-preview]');
  const updatePreview = () => {
    const width = Number(form.elements.width_lanes.value); const lane = Number(form.elements.start_lane.value);
    const unit = Number(form.elements.start_unit.value); const height = Number(form.elements.unit_height.value);
    const rack = document.querySelector(`[data-rack-id="${CSS.escape(form.elements.rack_id.value)}"]`);
    const invalid = lane + width - 1 > 3 || unit < 1 || unit + height - 1 > Number(rack?.dataset.rackUnits || 0);
    const faces = form.elements.depth_mode.value === 'full' ? ['front', 'rear'] : [form.elements.face.value];
    const occupiedLabels = [...document.querySelectorAll(`[data-rack-id="${CSS.escape(form.elements.rack_id.value)}"] .rack__slot--occupied`)]
      .map(slot => slot.getAttribute('aria-label') || '');
    let occupied = false;
    for (let u = unit; u < unit + height; u += 1) {
      for (let l = lane; l < lane + width; l += 1) {
        occupied ||= faces.some(face => occupiedLabels.some(label => label.includes(`, ${face}, unit ${u}, lane ${l}`)));
      }
    }
    preview.classList.toggle('rack-preview--conflict', invalid || occupied);
    preview.textContent = invalid || occupied ? 'Conflict: this selection is outside the rack or overlaps occupied space.' : `Preview: U${unit}–${unit + height - 1}, lane ${lane}–${lane + width - 1}, ${form.elements.depth_mode.value === 'full' ? 'both faces' : form.elements.face.value + ' face'}.`;
  };
  document.querySelectorAll('[data-place-open]').forEach(button => button.addEventListener('click', () => {
    form.elements.rack_id.value = button.dataset.rack; form.elements.face.value = button.dataset.face;
    form.elements.start_unit.value = button.dataset.unit; form.elements.start_lane.value = button.dataset.lane;
    document.querySelector('#rack-place-title').textContent = `Place asset in ${button.dataset.rackName}`; updatePreview(); placeDialog.showModal();
  }));
  form.addEventListener('input', updatePreview);
})();
