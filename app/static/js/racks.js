(() => {
  const createDialog = document.querySelector('#rack-create-dialog');
  const placeDialog = document.querySelector('#rack-place-dialog');
  const form = document.querySelector('[data-placement-form]');
  let dialogTrigger = null;
  const openDialog = (dialog, trigger) => { dialogTrigger = trigger; dialog.showModal(); };
  document.querySelector('[data-rack-create-open]')?.addEventListener('click', event => openDialog(createDialog, event.currentTarget));
  document.querySelectorAll('[data-dialog-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => { dialogTrigger?.focus(); dialogTrigger = null; });
  });
  document.querySelectorAll('[data-inspect-placement]').forEach(button => button.addEventListener('click', () => {
    const target = document.querySelector(`#placement-${CSS.escape(button.dataset.inspectPlacement)}`);
    target?.closest('details')?.setAttribute('open', ''); target?.scrollIntoView({ block: 'nearest' }); target?.querySelector('a,button')?.focus();
  }));
  document.querySelectorAll('[data-inspect-reservation]').forEach(button => button.addEventListener('click', () => {
    const target = document.querySelector(`#reservation-${CSS.escape(button.dataset.inspectReservation)}`);
    target?.closest('details')?.setAttribute('open', ''); target?.scrollIntoView({ block: 'nearest' }); target?.querySelector('button')?.focus();
  }));
  if (!form || !placeDialog) return;
  const preview = form.querySelector('[data-placement-preview]');
  const updatePreview = () => {
    const width = Number(form.elements.width_lanes.value); const lane = Number(form.elements.start_lane.value);
    const unit = Number(form.elements.start_unit.value); const height = Number(form.elements.unit_height.value);
    const rack = document.querySelector(`[data-rack-id="${CSS.escape(form.elements.rack_id.value)}"]`);
    const invalid = lane + width - 1 > 3 || unit < 1 || height < 1 || unit + height - 1 > Number(rack?.dataset.rackUnits || 0);
    const faces = form.elements.depth_mode.value === 'full' ? ['front', 'rear'] : [form.elements.face.value];
    let occupied = false;
    for (let u = unit; u < unit + height; u += 1) for (let l = lane; l < lane + width; l += 1) {
      occupied ||= faces.some(face => [...rack.querySelectorAll(`.rack-face:nth-of-type(${face === 'front' ? 1 : 2}) .rack__row`)]
        .some(row => row.querySelector('.rack__unit-label')?.textContent === `${u}U` && !row.querySelector(`.rack__slot:nth-child(${l})`)?.classList.contains('rack__slot--available')));
    }
    preview.classList.toggle('rack-preview--conflict', invalid || occupied);
    preview.textContent = invalid || occupied ? 'Conflict: this area is outside the rack or overlaps occupied or reserved space.' : `Available: U${unit}–${unit + height - 1}, lane ${lane}–${lane + width - 1}, ${form.elements.depth_mode.value === 'full' ? 'both faces' : form.elements.face.value + ' face'}.`;
  };
  const updateKind = () => {
    const reservation = form.elements.entry_kind.value === 'reservation';
    form.action = reservation ? '/api/infrastructure/rack-reservations' : '/api/infrastructure/rack-equipment';
    form.querySelector('[data-equipment-fields]').hidden = reservation;
    form.querySelector('[data-reservation-fields]').hidden = !reservation;
    form.elements.asset_id.required = !reservation;
    form.querySelector('[data-submit-label]').textContent = reservation ? 'Reserve space' : 'Place equipment';
  };
  document.querySelectorAll('[data-place-open]').forEach(button => button.addEventListener('click', () => {
    form.reset(); form.elements.rack_id.value = button.dataset.rack; form.elements.face.value = button.dataset.face;
    form.elements.start_unit.value = button.dataset.unit; form.elements.start_lane.value = button.dataset.lane;
    form.querySelector('[data-selected-position]').textContent = `${button.dataset.rackName} · ${button.dataset.face} · U${button.dataset.unit} · lane ${button.dataset.lane}`;
    updateKind(); updatePreview(); openDialog(placeDialog, button);
  }));
  form.addEventListener('input', () => { updateKind(); updatePreview(); });
})();
