(() => {
  const createDialog = document.querySelector('#rack-create-dialog');
  const placeDialog = document.querySelector('#rack-place-dialog');
  const editDialog = document.querySelector('#rack-edit-dialog');
  const reservationDialog = document.querySelector('#rack-reservation-dialog');
  const form = document.querySelector('[data-placement-form]');
  const editForm = document.querySelector('[data-edit-form]');
  const reservationForm = document.querySelector('[data-reservation-form]');
  let dialogTrigger = null;
  const openDialog = (dialog, trigger) => {
    if (!dialog) return;
    dialogTrigger = trigger;
    dialog.showModal();
  };
  document.querySelector('[data-rack-create-open]')?.addEventListener('click', event => openDialog(createDialog, event.currentTarget));
  document.querySelectorAll('[data-dialog-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => { dialogTrigger?.focus(); dialogTrigger = null; });
  });
  const focusListItem = (id) => {
    const target = document.getElementById(id);
    target?.closest('details')?.setAttribute('open', '');
    target?.scrollIntoView({ block: 'nearest' });
    target?.querySelector('button, a')?.focus();
  };
  document.querySelectorAll('[data-rack-list-open]').forEach(button => button.addEventListener('click', () => {
    const list = document.getElementById(button.dataset.rackListOpen);
    if (!list) return;
    list.open = true;
    list.scrollIntoView({ block: 'nearest' });
    list.querySelector('summary')?.focus();
  }));
  const editEquipment = (id, trigger) => {
    const item = document.getElementById(`placement-${id}`);
    if (!editForm || !item) { focusListItem(`placement-${id}`); return; }
    const data = item.dataset;
    editForm.action = `/api/infrastructure/rack-equipment/${encodeURIComponent(id)}/edit`;
    editForm.elements.name.value = data.itemName || '';
    editForm.elements.item_type.value = data.itemType || 'device';
    const hasPorts = Number(data.itemPortCount || 0) > 0;
    [...editForm.elements.item_type.options].forEach(option => {
      option.disabled = option.value !== 'device' && !hasPorts;
    });
    editForm.elements.asset_id.value = data.itemAsset || '';
    editForm.elements.power_draw_watts.value = data.itemPower || '';
    editForm.elements.notes.value = data.itemNotes || '';
    editDialog.dataset.itemId = id;
    editDialog.querySelector('[data-edit-ports]').hidden = !hasPorts;
    editDialog.querySelector('[data-edit-position]').textContent = data.itemPosition || '';
    openDialog(editDialog, trigger);
  };
  const editReservation = (id, trigger) => {
    const item = document.getElementById(`reservation-${id}`);
    if (!reservationForm || !item) { focusListItem(`reservation-${id}`); return; }
    const data = item.dataset;
    reservationForm.action = `/api/infrastructure/rack-reservations/${encodeURIComponent(id)}/edit`;
    reservationForm.elements.label.value = data.reservationLabel || '';
    reservationForm.elements.owner.value = data.reservationOwner || '';
    reservationForm.elements.notes.value = data.reservationNotes || '';
    reservationDialog.dataset.reservationId = id;
    reservationDialog.querySelector('[data-reservation-position]').textContent = data.reservationPosition || '';
    openDialog(reservationDialog, trigger);
  };
  document.querySelectorAll('[data-inspect-placement]').forEach(button => button.addEventListener('click', () => editEquipment(button.dataset.inspectPlacement, button)));
  document.querySelectorAll('[data-inspect-reservation]').forEach(button => button.addEventListener('click', () => editReservation(button.dataset.inspectReservation, button)));
  document.querySelectorAll('[data-edit-equipment]').forEach(button => button.addEventListener('click', () => editEquipment(button.dataset.editEquipment, button)));
  document.querySelectorAll('[data-edit-reservation]').forEach(button => button.addEventListener('click', () => editReservation(button.dataset.editReservation, button)));
  document.querySelectorAll('[data-confirm-remove]').forEach(removeForm => removeForm.addEventListener('submit', event => {
    if (!window.confirm(`Remove ${removeForm.dataset.confirmRemove} from this rack?`)) event.preventDefault();
  }));
  editDialog?.querySelector('[data-edit-remove]')?.addEventListener('click', () => {
    const id = editDialog.dataset.itemId;
    editDialog.close();
    document.getElementById(`placement-${id}`)?.querySelector('[data-confirm-remove]')?.requestSubmit();
  });
  editDialog?.querySelector('[data-edit-ports]')?.addEventListener('click', () => {
    const id = editDialog.dataset.itemId;
    editDialog.close();
    focusListItem(`placement-${id}`);
    document.getElementById(`placement-${id}`)?.querySelector('.rack-port select')?.focus();
  });
  reservationDialog?.querySelector('[data-reservation-remove]')?.addEventListener('click', () => {
    const id = reservationDialog.dataset.reservationId;
    reservationDialog.close();
    document.getElementById(`reservation-${id}`)?.querySelector('[data-confirm-remove]')?.requestSubmit();
  });
  document.querySelectorAll('[data-rack-add]').forEach(button => button.addEventListener('click', () => {
    const rack = document.getElementById(`rack-${button.dataset.rackAdd}`);
    rack?.querySelector('[data-place-open]')?.click();
  }));
  document.querySelectorAll('[data-rack-add]').forEach(button => {
    const rack = document.getElementById(`rack-${button.dataset.rackAdd}`);
    if (!rack?.querySelector('[data-place-open]')) {
      button.disabled = true;
      button.title = 'No free rack positions';
    }
  });
  if (!form || !placeDialog) return;
  const preview = form.querySelector('[data-placement-preview]');
  const updatePreview = () => {
    const width = Number(form.elements.width_lanes.value);
    const lane = Number(form.elements.start_lane.value);
    const unit = Number(form.elements.start_unit.value);
    const height = Number(form.elements.unit_height.value);
    const rack = document.querySelector(`[data-rack-id="${CSS.escape(form.elements.rack_id.value)}"]`);
    const invalid = !rack || lane + width - 1 > 3 || unit < 1 || height < 1 || unit + height - 1 > Number(rack?.dataset.rackUnits || 0);
    const faces = form.elements.depth_mode.value === 'full' ? ['front', 'rear'] : [form.elements.face.value];
    const occupied = !invalid && faces.some(face =>
      [...rack.querySelectorAll(`.rack-face[data-face="${face}"] .rack__device`)].some(slot => {
        const slotUnit = Number(slot.dataset.startUnit);
        const slotHeight = Number(slot.dataset.unitHeight);
        const slotLane = Number(slot.dataset.startLane);
        const slotWidth = Number(slot.dataset.widthLanes);
        return unit < slotUnit + slotHeight && unit + height > slotUnit &&
          lane < slotLane + slotWidth && lane + width > slotLane;
      })
    );
    const conflict = invalid || occupied;
    preview.classList.toggle('rack-preview--conflict', conflict);
    preview.textContent = conflict ? 'This position is outside the rack or overlaps occupied or reserved space.' : `Available: U${unit}–${unit + height - 1}, lane ${lane}–${lane + width - 1}, ${form.elements.depth_mode.value === 'full' ? 'both faces' : form.elements.face.value + ' face'}.`;
    form.querySelector('[data-submit-label]').disabled = conflict;
  };
  const updateKind = () => {
    const reservation = form.elements.entry_kind.value === 'reservation';
    form.action = reservation ? '/api/infrastructure/rack-reservations' : '/api/infrastructure/rack-equipment';
    form.querySelector('[data-equipment-fields]').hidden = reservation;
    form.querySelector('[data-reservation-fields]').hidden = !reservation;
    form.elements.name.required = !reservation;
    const portItem = !reservation && ['patch_panel', 'switch'].includes(form.elements.item_type.value);
    form.querySelector('[data-port-count]').hidden = !portItem;
    form.elements.port_count.required = portItem;
    if (!portItem) form.elements.port_count.value = '';
    form.querySelector('[data-submit-label]').textContent = reservation ? 'Reserve space' : 'Place equipment';
  };
  document.querySelectorAll('[data-place-open]').forEach(button => button.addEventListener('click', () => {
    form.reset();
    form.elements.rack_id.value = button.dataset.rack;
    form.elements.face.value = button.dataset.face;
    form.elements.start_unit.value = button.dataset.unit;
    form.elements.start_lane.value = button.dataset.lane;
    form.querySelector('[data-selected-position]').textContent = `${button.dataset.rackName} · ${button.dataset.face} · U${button.dataset.unit} · lane ${button.dataset.lane}`;
    updateKind(); updatePreview(); openDialog(placeDialog, button);
  }));
  form.addEventListener('input', () => { updateKind(); updatePreview(); });
})();
