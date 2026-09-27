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
  const workspace = document.querySelector('[data-rack-workspace]');
  const legacyRack = /^#rack-(\d+)$/.exec(window.location.hash);
  if (legacyRack && !document.getElementById(`rack-${legacyRack[1]}`) && workspace?.dataset.view !== 'overview') {
    window.location.replace(`/racks?rack=${legacyRack[1]}#rack-${legacyRack[1]}`);
    return;
  }
  const sidebar = document.querySelector('[data-rack-sidebar]');
  const sidebarToggle = sidebar?.querySelector('[data-rack-sidebar-toggle]');
  const setSidebar = (collapsed) => {
    workspace?.classList.toggle('is-sidebar-collapsed', collapsed);
    sidebarToggle?.setAttribute('aria-expanded', String(!collapsed));
    const label = collapsed ? 'Expand workspace menu' : 'Collapse workspace menu';
    sidebarToggle?.setAttribute('title', label);
    const text = sidebarToggle?.querySelector('.sr-only');
    if (text) text.textContent = label;
  };
  try { setSidebar(window.localStorage.getItem('racks.sidebarCollapsed') === '1'); } catch { setSidebar(false); }
  sidebarToggle?.addEventListener('click', () => {
    const collapsed = !workspace?.classList.contains('is-sidebar-collapsed');
    setSidebar(collapsed);
    try { window.localStorage.setItem('racks.sidebarCollapsed', collapsed ? '1' : '0'); } catch { /* storage unavailable */ }
  });
  const imagesToggle = document.querySelector('[data-rack-images-toggle]');
  const setImages = (enabled) => {
    workspace?.classList.toggle('has-device-images', enabled);
    imagesToggle?.setAttribute('aria-pressed', String(enabled));
  };
  try { setImages(window.localStorage.getItem('racks.deviceImages') !== '0'); } catch { setImages(true); }
  imagesToggle?.addEventListener('click', () => {
    const enabled = !workspace?.classList.contains('has-device-images');
    setImages(enabled);
    try { window.localStorage.setItem('racks.deviceImages', enabled ? '1' : '0'); } catch { /* storage unavailable */ }
  });
  document.querySelector('[data-rack-jump]')?.addEventListener('change', event => event.currentTarget.form?.requestSubmit());
  document.querySelectorAll('[data-rack-list-open]').forEach(button => button.addEventListener('click', event => {
    const list = document.getElementById(button.dataset.rackListOpen);
    if (!list) return;
    event.preventDefault();
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
    editForm.elements.item_type.value = data.itemType || 'server';
    const hasPorts = Number(data.itemPortCount || 0) > 0;
    // Port rows are created at placement, so a port-less item cannot become a port type.
    [...editForm.elements.item_type.options].forEach(option => {
      option.disabled = option.hasAttribute('data-has-ports') && !hasPorts && option.value !== data.itemType;
    });
    editForm.elements.asset_id.value = data.itemAsset || '';
    editForm.elements.power_draw_watts.value = data.itemPower || '';
    editForm.elements.notes.value = data.itemNotes || '';
    if (data.rackOwner) editForm.elements.rack_id.value = data.rackOwner;
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
    if (data.rackOwner) reservationForm.elements.rack_id.value = data.rackOwner;
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
  document.querySelectorAll('[data-rack-swivel]').forEach(button => button.addEventListener('click', () => {
    const faces = button.closest('[data-rack-faces]');
    const swivelled = faces?.classList.toggle('is-swivelled') ?? false;
    button.setAttribute('aria-pressed', String(swivelled));
  }));
  const statusText = document.querySelector('[data-rack-status-text]');
  const defaultStatus = statusText?.textContent || '';
  const describe = (button) => {
    const face = button.closest('[data-face]')?.dataset.face;
    if (statusText && button.dataset.itemSummary) statusText.textContent = `${button.dataset.itemSummary} · ${face} elevation`;
  };
  document.querySelectorAll('.rack__device').forEach(button => {
    button.addEventListener('focus', () => describe(button));
    button.addEventListener('mouseenter', () => describe(button));
  });
  document.querySelectorAll('[data-rack-faces]').forEach(faces => faces.addEventListener('mouseleave', () => {
    if (statusText && !faces.contains(document.activeElement)) statusText.textContent = defaultStatus;
  }));
  if (!form || !placeDialog) return;
  const preview = form.querySelector('[data-placement-preview]');
  const map = form.querySelector('[data-placement-map]');
  const mapCaption = form.querySelector('[data-placement-map-caption]');
  const laneNames = ['left', 'centre', 'right'];
  const blocksOn = (rack, face) => [...rack.querySelectorAll(`.rack-face[data-face="${face}"] .rack__device`)].map(slot => ({
    unit: Number(slot.dataset.startUnit),
    height: Number(slot.dataset.unitHeight),
    lane: Number(slot.dataset.startLane),
    width: Number(slot.dataset.widthLanes),
    reserved: slot.hasAttribute('data-inspect-reservation'),
  }));
  const overlaps = (a, b) => a.unit < b.unit + b.height && a.unit + a.height > b.unit &&
    a.lane < b.lane + b.width && a.lane + a.width > b.lane;
  const renderMap = (rack, candidate, faces, conflict) => {
    if (!map) return;
    map.replaceChildren();
    if (!rack) return;
    const units = Number(rack.dataset.rackUnits || 0);
    const topDown = rack.dataset.rackDirection === 'top-down';
    const rowOf = (unit, height) => topDown ? unit : units - unit - height + 2;
    map.style.setProperty('--mini-units', units);
    ['front', 'rear'].forEach(face => {
      const column = document.createElement('div');
      column.className = 'rack-mini__face';
      if (!faces.includes(face)) column.classList.add('is-inactive');
      const label = document.createElement('span');
      label.className = 'rack-mini__label';
      label.textContent = face === 'front' ? 'Front' : 'Rear';
      const grid = document.createElement('div');
      grid.className = 'rack-mini__grid';
      const place = (block, className) => {
        const top = Math.max(1, rowOf(block.unit, block.height));
        const bottom = Math.min(units + 1, rowOf(block.unit, block.height) + block.height);
        if (bottom <= top) return;
        const cell = document.createElement('i');
        cell.className = className;
        cell.style.gridRow = `${top} / ${bottom}`;
        cell.style.gridColumn = `${block.lane} / span ${Math.max(1, Math.min(block.width, 4 - block.lane))}`;
        grid.append(cell);
      };
      blocksOn(rack, face).forEach(block => place(block, block.reserved ? 'is-reserved' : 'is-used'));
      if (faces.includes(face) && candidate) place(candidate, conflict ? 'is-candidate is-conflict' : 'is-candidate');
      column.append(label, grid);
      map.append(column);
    });
    if (mapCaption) mapCaption.textContent = `${rack.dataset.rackName} · ${units}U · ${topDown ? 'top down' : 'bottom up'}`;
  };
  const clampLanes = (width) => {
    const lanes = form.querySelectorAll('input[name="start_lane"]');
    lanes.forEach(input => { input.disabled = Number(input.value) + width - 1 > 3; });
    if ([...lanes].find(input => input.checked)?.disabled) lanes[3 - width].checked = true;
  };
  const updatePreview = () => {
    const width = Number(form.elements.width_lanes.value);
    clampLanes(width);
    const lane = Number(form.elements.start_lane.value);
    const unit = Number(form.elements.start_unit.value);
    const height = Number(form.elements.unit_height.value);
    const rack = document.querySelector(`[data-rack-id="${CSS.escape(form.elements.rack_id.value)}"]`);
    const units = Number(rack?.dataset.rackUnits || 0);
    form.elements.start_unit.max = String(units || '');
    form.elements.unit_height.max = String(Math.max(1, units - Math.max(unit, 1) + 1));
    const invalid = !rack || lane + width - 1 > 3 || unit < 1 || height < 1 || unit + height - 1 > units;
    const faces = form.elements.depth_mode.value === 'full' ? ['front', 'rear'] : [form.elements.face.value];
    const candidate = { unit, height, lane, width };
    const occupied = !invalid && faces.some(face => blocksOn(rack, face).some(block => overlaps(candidate, block)));
    const conflict = invalid || occupied;
    renderMap(rack, unit >= 1 && height >= 1 ? candidate : null, faces, conflict);
    preview.classList.toggle('rack-preview--conflict', conflict);
    const span = height > 1 ? `U${unit}–${unit + height - 1}` : `U${unit}`;
    const lanes = width === 3 ? 'full width' : `${laneNames[lane - 1]}${width === 2 ? ` + ${laneNames[lane]}` : ''} lane${width === 2 ? 's' : ''}`;
    const where = form.elements.depth_mode.value === 'full' ? 'both faces' : `${form.elements.face.value} face`;
    if (invalid) preview.textContent = units ? `Choose a position between U1 and U${units}.` : 'Choose a rack position.';
    else if (occupied) preview.textContent = `${span} overlaps installed or reserved space on the ${where}.`;
    else preview.textContent = `${span} · ${lanes} · ${where} is free.`;
    form.querySelector('[data-submit-label]').disabled = conflict;
  };
  const updateKind = () => {
    const reservation = form.elements.entry_kind.value === 'reservation';
    form.action = reservation ? '/api/infrastructure/rack-reservations' : '/api/infrastructure/rack-equipment';
    form.querySelector('[data-equipment-fields]').hidden = reservation;
    form.querySelector('[data-reservation-fields]').hidden = !reservation;
    const type = form.querySelector('input[name="item_type"]:checked');
    const portsLabel = reservation ? '' : type?.dataset.portsLabel || '';
    form.querySelector('[data-port-count]').hidden = !portsLabel;
    if (portsLabel) form.querySelector('[data-ports-noun]').textContent = portsLabel;
    else form.elements.port_count.value = '';
    form.querySelector('[data-submit-label]').textContent = reservation ? 'Reserve space' : 'Place equipment';
  };
  let heightTouched = false;
  form.elements.unit_height.addEventListener('input', () => { heightTouched = true; });
  form.querySelectorAll('input[name="item_type"]').forEach(input => input.addEventListener('change', () => {
    if (heightTouched) return;
    const rack = document.querySelector(`[data-rack-id="${CSS.escape(form.elements.rack_id.value)}"]`);
    const room = Number(rack?.dataset.rackUnits || 1) - Number(form.elements.start_unit.value || 1) + 1;
    form.elements.unit_height.value = String(Math.max(1, Math.min(Number(input.dataset.defaultHeight || 1), room)));
    updatePreview();
  }));
  document.querySelectorAll('[data-place-open]').forEach(button => button.addEventListener('click', () => {
    form.reset();
    heightTouched = false;
    form.elements.rack_id.value = button.dataset.rack;
    form.elements.face.value = button.dataset.face;
    form.elements.start_unit.value = button.dataset.unit;
    form.elements.start_lane.value = button.dataset.lane;
    form.querySelector('[data-selected-position]').textContent = `${button.dataset.rackName} · ${button.dataset.face} · U${button.dataset.unit} · ${laneNames[Number(button.dataset.lane) - 1]} lane`;
    updateKind(); updatePreview(); openDialog(placeDialog, button);
  }));
  form.addEventListener('input', () => { updateKind(); updatePreview(); });
})();
