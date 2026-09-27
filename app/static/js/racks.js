(() => {
  const createDialog = document.querySelector('#rack-create-dialog');
  const placeDialog = document.querySelector('#rack-place-dialog');
  const reservationDialog = document.querySelector('#rack-reservation-dialog');
  const form = document.querySelector('[data-placement-form]');
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

  // Shared rack geometry helpers used by the dialogs' mini-rack previews.
  const laneNames = ['left', 'centre', 'right'];
  const blocksOn = (rack, face, excludeId = null) => [...rack.querySelectorAll(`.rack-face[data-face="${face}"] .rack__device`)]
    .filter(slot => excludeId === null || slot.dataset.inspectPlacement !== String(excludeId))
    .map(slot => ({
      unit: Number(slot.dataset.startUnit),
      height: Number(slot.dataset.unitHeight),
      lane: Number(slot.dataset.startLane),
      width: Number(slot.dataset.widthLanes),
      reserved: slot.hasAttribute('data-inspect-reservation'),
    }));
  const overlaps = (a, b) => a.unit < b.unit + b.height && a.unit + a.height > b.unit &&
    a.lane < b.lane + b.width && a.lane + a.width > b.lane;
  const renderMap = (map, mapCaption, rack, candidate, faces, conflict = false, excludeId = null) => {
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
      blocksOn(rack, face, excludeId).forEach(block => place(block, block.reserved ? 'is-reserved' : 'is-used'));
      if (faces.includes(face) && candidate) place(candidate, conflict ? 'is-candidate is-conflict' : 'is-candidate');
      column.append(label, grid);
      map.append(column);
    });
    if (mapCaption) mapCaption.textContent = `${rack.dataset.rackName} · ${units}U · ${topDown ? 'top down' : 'bottom up'}`;
  };
  const rackFor = (rackId) => rackId ? document.querySelector(`[data-rack-id="${CSS.escape(String(rackId))}"]`) : null;
  const laneText = (lane, width) => width === 3 ? 'full width' : width === 2 ? `${laneNames[lane - 1]} + ${laneNames[lane]} lanes` : `${laneNames[lane - 1]} lane`;
  const positionText = ({ unit, height, lane, width, face, depthMode }) => {
    const units = height > 1 ? `U${unit}–${unit + height - 1}` : `U${unit}`;
    const where = depthMode === 'full' ? 'front and rear' : face;
    return `${units} · ${where} · ${laneText(lane, width)}`;
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
    const spot = {
      unit: Number(data.startUnit), height: Number(data.unitHeight), lane: Number(data.startLane),
      width: Number(data.widthLanes), face: data.face || 'front', depthMode: data.depthMode,
    };
    reservationDialog.querySelector('[data-reservation-position]').textContent = positionText(spot);
    renderMap(reservationDialog.querySelector('[data-reservation-map]'), reservationDialog.querySelector('[data-reservation-map-caption]'),
      rackFor(data.rackOwner), spot, spot.depthMode === 'full' ? ['front', 'rear'] : [spot.face]);
    openDialog(reservationDialog, trigger);
  };
  document.querySelectorAll('[data-inspect-reservation]').forEach(button => button.addEventListener('click', () => editReservation(button.dataset.inspectReservation, button)));
  document.querySelectorAll('[data-edit-reservation]').forEach(button => button.addEventListener('click', () => editReservation(button.dataset.editReservation, button)));
  document.querySelectorAll('[data-confirm-remove]').forEach(removeForm => removeForm.addEventListener('submit', event => {
    if (!window.confirm(`Remove ${removeForm.dataset.confirmRemove} from this rack?`)) event.preventDefault();
  }));
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
    const view = button.dataset.view === 'rear' ? ' (rear view)' : '';
    if (statusText && button.dataset.itemSummary) statusText.textContent = `${button.dataset.itemSummary} · ${face} elevation${view}`;
  };
  document.querySelectorAll('.rack__device').forEach(button => {
    button.addEventListener('focus', () => describe(button));
    button.addEventListener('mouseenter', () => describe(button));
  });
  document.querySelectorAll('[data-rack-faces]').forEach(faces => faces.addEventListener('mouseleave', () => {
    if (statusText && !faces.contains(document.activeElement)) statusText.textContent = defaultStatus;
  }));
  if (!form || !placeDialog) return;

  // Add / edit rack item dialog. Editing reuses every field used when adding.
  let items = [];
  try { items = JSON.parse(document.getElementById('rack-items-data')?.textContent || '[]'); } catch { items = []; }
  const itemsById = new Map(items.map(item => [String(item.id), item]));
  const preview = form.querySelector('[data-placement-preview]');
  const map = form.querySelector('[data-placement-map]');
  const mapCaption = form.querySelector('[data-placement-map-caption]');
  const faceplate = form.querySelector('[data-faceplate]');
  const faceplateName = form.querySelector('[data-faceplate-name]');
  const title = placeDialog.querySelector('[data-dialog-title]');
  const submit = form.querySelector('[data-submit-label]');
  const removeButton = placeDialog.querySelector('[data-edit-remove]');
  const connections = form.querySelector('[data-connections]');
  const powerSource = form.querySelector('[data-power-source]');
  const linksTable = form.querySelector('[data-port-links]');
  const linksSummary = form.querySelector('[data-port-links-summary]');
  const linksPanel = form.querySelector('[data-port-links-panel]');
  const assetOptions = form.querySelector('template[data-asset-options]');
  const connectorLabels = { data: 'Port', iec: 'IEC', '3pin': '3-pin' };
  let editingId = null;
  let heightTouched = false;
  // What the user has entered per port, kept while counts change.
  let linkValues = new Map();

  const selectedType = () => form.querySelector('input[name="item_type"]:checked');
  const typeConnectors = (input) => (input?.dataset.connectors || '').split(' ').filter(Boolean)
    .map(pair => { const [key, count] = pair.split(':'); return { key, count: Number(count || 0) }; });
  const clampLanes = (width) => {
    const lanes = form.querySelectorAll('input[name="start_lane"]');
    lanes.forEach(input => { input.disabled = Number(input.value) + width - 1 > 3; });
    if ([...lanes].find(input => input.checked)?.disabled) lanes[3 - width].checked = true;
  };
  const rememberLinks = () => {
    linksTable.querySelectorAll('[data-port-row]').forEach(row => {
      linkValues.set(row.dataset.portRow, {
        asset: row.querySelector('select').value,
        label: row.querySelector('input').value,
      });
    });
  };
  const renderLinks = () => {
    rememberLinks();
    linksTable.replaceChildren();
    let total = 0;
    let linked = 0;
    typeConnectors(selectedType()).forEach(({ key }) => {
      const count = Math.min(Number(form.elements[`port_count_${key}`]?.value || 0), 1000);
      for (let ordinal = 1; ordinal <= count; ordinal += 1) {
        const id = `${key}-${ordinal}`;
        const saved = linkValues.get(id) || { asset: '', label: '' };
        const row = document.createElement('div');
        row.className = `rack-port-link rack-port-link--${key}`;
        row.setAttribute('role', 'row');
        row.dataset.portRow = id;
        const name = document.createElement('span');
        name.className = 'rack-port-link__name';
        name.setAttribute('role', 'rowheader');
        name.textContent = `${connectorLabels[key] || 'Port'} ${ordinal}`;
        const select = document.createElement('select');
        select.className = 'input input--small';
        select.name = `port-${id}-asset`;
        select.setAttribute('aria-label', `Asset on ${name.textContent}`);
        select.append(assetOptions.content.cloneNode(true));
        select.value = saved.asset;
        const label = document.createElement('input');
        label.className = 'input input--small';
        label.name = `port-${id}-label`;
        label.maxLength = 191;
        label.placeholder = 'Label, e.g. Printer';
        label.setAttribute('aria-label', `Label for ${name.textContent}`);
        label.value = saved.label;
        row.append(name, select, label);
        linksTable.append(row);
        total += 1;
        if (saved.asset || saved.label) linked += 1;
      }
    });
    linksSummary.textContent = total ? `Port links · ${linked} of ${total} linked` : 'Port links';
    linksPanel.hidden = total === 0;
  };
  const updateFaceplate = () => {
    const type = selectedType();
    if (!faceplate || !type) return;
    const width = Number(form.elements.width_lanes.value || 3);
    const height = Math.min(Math.max(Number(form.elements.unit_height.value || 1), 1), 4);
    faceplate.style.setProperty('--faceplate-image', `url("${type.dataset[`imageW${width}`]}")`);
    faceplate.style.setProperty('--faceplate-units', String(height));
    faceplate.style.setProperty('--image-units', type.dataset.imageUnits || '1');
    faceplate.style.setProperty('--faceplate-width', `${(width / 3) * 100}%`);
    const assetOption = form.elements.asset_id.selectedOptions[0];
    const assetName = form.elements.asset_id.value ? assetOption?.textContent || '' : '';
    faceplateName.textContent = form.elements.name.value.trim() || assetName || type.dataset.typeLabel || '';
  };
  const updatePreview = () => {
    const width = Number(form.elements.width_lanes.value);
    clampLanes(width);
    const lane = Number(form.elements.start_lane.value);
    const unit = Number(form.elements.start_unit.value);
    const height = Number(form.elements.unit_height.value);
    const rack = rackFor(form.elements.rack_id.value);
    const units = Number(rack?.dataset.rackUnits || 0);
    form.elements.start_unit.max = String(units || '');
    form.elements.unit_height.max = String(Math.max(1, units - Math.max(unit, 1) + 1));
    const invalid = !rack || lane + width - 1 > 3 || unit < 1 || height < 1 || unit + height - 1 > units;
    const depthMode = form.elements.depth_mode.value;
    const face = form.elements.face.value;
    const faces = depthMode === 'full' ? ['front', 'rear'] : [face];
    const candidate = { unit, height, lane, width };
    const occupied = !invalid && faces.some(side => blocksOn(rack, side, editingId).some(block => overlaps(candidate, block)));
    const conflict = invalid || occupied;
    renderMap(map, mapCaption, rack, unit >= 1 && height >= 1 ? candidate : null, faces, conflict, editingId);
    preview.classList.toggle('rack-preview--conflict', conflict);
    const spot = positionText({ unit, height, lane, width, face, depthMode });
    if (invalid) preview.textContent = units ? `Choose a position between U1 and U${units}.` : 'Choose a rack position.';
    else if (occupied) preview.textContent = `${spot} overlaps installed or reserved space.`;
    else preview.textContent = `${spot} is free.`;
    submit.disabled = conflict;
    updateFaceplate();
  };
  const updateKind = () => {
    const reservation = form.elements.entry_kind.value === 'reservation';
    if (!editingId) form.action = reservation ? '/api/infrastructure/rack-reservations' : '/api/infrastructure/rack-equipment';
    form.querySelector('[data-equipment-fields]').hidden = reservation;
    form.querySelector('[data-reservation-fields]').hidden = !reservation;
    const type = selectedType();
    const keys = reservation ? [] : typeConnectors(type).map(({ key }) => key);
    form.querySelectorAll('[data-connector-count]').forEach(field => {
      const active = keys.includes(field.dataset.connectorCount);
      field.hidden = !active;
      field.querySelector('input').disabled = !active;
    });
    connections.hidden = keys.length === 0;
    const powered = !reservation && type?.dataset.powerInput === 'true';
    powerSource.hidden = !powered;
    powerSource.querySelectorAll('select, input').forEach(control => { control.disabled = !powered; });
    faceplate.hidden = reservation;
    if (editingId) submit.textContent = 'Save changes';
    else submit.textContent = reservation ? 'Reserve space' : 'Place equipment';
    renderLinks();
  };
  const applyTypeDefaults = (input) => {
    // Seed connector counts the first time a connector becomes relevant.
    typeConnectors(input).forEach(({ key, count }) => {
      const field = form.elements[`port_count_${key}`];
      if (field && field.disabled) field.value = String(count);
    });
    if (heightTouched) return;
    const rack = rackFor(form.elements.rack_id.value);
    const room = Number(rack?.dataset.rackUnits || 1) - Number(form.elements.start_unit.value || 1) + 1;
    form.elements.unit_height.value = String(Math.max(1, Math.min(Number(input.dataset.defaultHeight || 1), room)));
  };
  const setMode = (mode) => {
    placeDialog.dataset.mode = mode;
    form.querySelector('[data-kind-choice]').hidden = mode === 'edit';
    removeButton.hidden = mode !== 'edit';
    title.textContent = mode === 'edit' ? 'Edit rack item' : 'Add rack space';
  };
  const hideOwnOutlets = (equipmentId) => {
    form.elements.power_source_port_id.querySelectorAll('option[data-equipment-id]').forEach(option => {
      const own = equipmentId !== null && option.dataset.equipmentId === String(equipmentId);
      option.hidden = own;
      option.disabled = own;
    });
  };
  const resetForm = () => {
    form.reset();
    linkValues = new Map();
    form.querySelectorAll('[data-connector-count] input').forEach(input => { input.disabled = true; });
  };
  form.elements.unit_height.addEventListener('input', () => { heightTouched = true; });
  form.querySelectorAll('input[name="item_type"]').forEach(input => input.addEventListener('change', () => {
    applyTypeDefaults(input);
    updateKind();
    updatePreview();
  }));
  document.querySelectorAll('[data-place-open]').forEach(button => button.addEventListener('click', () => {
    resetForm();
    editingId = null;
    heightTouched = false;
    setMode('add');
    hideOwnOutlets(null);
    form.elements.rack_id.value = button.dataset.rack;
    form.elements.face.value = button.dataset.face;
    form.elements.start_unit.value = button.dataset.unit;
    form.elements.start_lane.value = button.dataset.lane;
    form.querySelector('[data-selected-position]').textContent = `${button.dataset.rackName} · ${button.dataset.face} · U${button.dataset.unit} · ${laneNames[Number(button.dataset.lane) - 1]} lane`;
    applyTypeDefaults(selectedType());
    updateKind(); updatePreview(); openDialog(placeDialog, button);
  }));
  const editEquipment = (id, trigger) => {
    const item = itemsById.get(String(id));
    const listItem = document.getElementById(`placement-${id}`);
    if (!item || !listItem) { focusListItem(`placement-${id}`); return; }
    resetForm();
    editingId = String(id);
    heightTouched = true;
    setMode('edit');
    hideOwnOutlets(item.id);
    form.action = `/api/infrastructure/rack-equipment/${encodeURIComponent(id)}/edit`;
    form.elements.entry_kind.value = 'equipment';
    form.elements.rack_id.value = listItem.dataset.rackOwner || '';
    form.elements.item_type.value = item.item_type;
    form.elements.name.value = item.name || '';
    form.elements.asset_id.value = item.asset_id ?? '';
    form.elements.power_draw_watts.value = item.power_draw_watts ?? '';
    form.elements.notes.value = item.notes || '';
    form.elements.face.value = item.face;
    form.elements.depth_mode.value = item.depth_mode;
    form.elements.start_unit.value = item.start_unit;
    form.elements.unit_height.value = item.unit_height;
    form.elements.width_lanes.value = item.width_lanes;
    form.elements.start_lane.value = item.start_lane;
    form.elements.power_source_port_id.value = item.power_source_port_id ?? '';
    form.elements.power_source_label.value = item.power_source_label || '';
    Object.entries(item.port_counts || {}).forEach(([key, count]) => {
      const field = form.elements[`port_count_${key}`];
      if (field) { field.value = String(count); field.disabled = false; }
    });
    (item.ports || []).forEach(port => {
      linkValues.set(`${port.connector}-${port.ordinal}`, { asset: port.asset_id ? String(port.asset_id) : '', label: port.label || '' });
    });
    applyTypeDefaults(selectedType());
    form.querySelector('[data-selected-position]').textContent = positionText({
      unit: item.start_unit, height: item.unit_height, lane: item.start_lane,
      width: item.width_lanes, face: item.face, depthMode: item.depth_mode,
    });
    updateKind(); updatePreview();
    linksPanel.open = (item.ports || []).some(port => port.asset_id || port.label);
    openDialog(placeDialog, trigger);
  };
  document.querySelectorAll('[data-inspect-placement]').forEach(button => button.addEventListener('click', () => editEquipment(button.dataset.inspectPlacement, button)));
  document.querySelectorAll('[data-edit-equipment]').forEach(button => button.addEventListener('click', () => editEquipment(button.dataset.editEquipment, button)));
  removeButton?.addEventListener('click', () => {
    const id = editingId;
    placeDialog.close();
    document.getElementById(`placement-${id}`)?.querySelector('[data-confirm-remove]')?.requestSubmit();
  });
  form.addEventListener('input', event => {
    if (event.target.name?.startsWith('port-')) {
      const rows = [...linksTable.querySelectorAll('[data-port-row]')];
      const linked = rows.filter(row => row.querySelector('select').value || row.querySelector('input').value).length;
      linksSummary.textContent = rows.length ? `Port links · ${linked} of ${rows.length} linked` : 'Port links';
      return;
    }
    if (event.target.name?.startsWith('port_count_') || event.target.name === 'entry_kind') updateKind();
    updatePreview();
  });
})();
