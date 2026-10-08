(() => {
  const createDialog = document.querySelector('#rack-create-dialog');
  const placeDialog = document.querySelector('#rack-place-dialog');
  const reservationDialog = document.querySelector('#rack-reservation-dialog');
  const form = document.querySelector('[data-placement-form]');
  const reservationForm = document.querySelector('[data-reservation-form]');
  // Each dialog remembers its own trigger so one dialog handing off to another
  // (ports view -> edit) returns focus correctly.
  const dialogTriggers = new WeakMap();
  const openDialog = (dialog, trigger) => {
    if (!dialog) return;
    dialogTriggers.set(dialog, trigger);
    dialog.showModal();
  };
  document.querySelector('[data-rack-create-open]')?.addEventListener('click', event => openDialog(createDialog, event.currentTarget));
  document.querySelector('[data-rack-resize-open]')?.addEventListener('click', event => openDialog(document.querySelector('#rack-resize-dialog'), event.currentTarget));
  document.querySelectorAll('[data-dialog-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => {
      const trigger = dialogTriggers.get(dialog);
      dialogTriggers.delete(dialog);
      if (!document.querySelector('dialog[open]')) trigger?.focus();
    });
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
  const namesToggle = document.querySelector('[data-rack-names-toggle]');
  const setNames = (shown) => {
    workspace?.classList.toggle('hide-device-names', !shown);
    namesToggle?.setAttribute('aria-pressed', String(shown));
  };
  try { setNames(window.localStorage.getItem('racks.deviceNames') !== '0'); } catch { setNames(true); }
  namesToggle?.addEventListener('click', () => {
    const shown = workspace?.classList.contains('hide-device-names') ?? false;
    setNames(shown);
    try { window.localStorage.setItem('racks.deviceNames', shown ? '1' : '0'); } catch { /* storage unavailable */ }
  });
  // Remember whether "Item list and port links" is expanded, across racks and visits.
  document.querySelectorAll('details.rack__details').forEach(details => {
    try { if (window.localStorage.getItem('racks.itemListOpen') === '1') details.open = true; } catch { /* storage unavailable */ }
    details.addEventListener('toggle', () => {
      try { window.localStorage.setItem('racks.itemListOpen', details.open ? '1' : '0'); } catch { /* storage unavailable */ }
    });
  });
  // Saving a rack form reloads this page; come back to the same scroll
  // position instead of the top (or an anchor further down).
  const scrollKey = 'racks.scrollAfterSave';
  // Listen on the document so a cancelled "Remove …?" confirm is already known.
  document.addEventListener('submit', event => {
    if (event.defaultPrevented || event.target.method !== 'post') return;
    try {
      window.sessionStorage.setItem(scrollKey, JSON.stringify({ y: window.scrollY, at: Date.now() }));
    } catch { /* storage unavailable */ }
  });
  try {
    const saved = JSON.parse(window.sessionStorage.getItem(scrollKey) || 'null');
    window.sessionStorage.removeItem(scrollKey);
    if (saved && Date.now() - saved.at < 60000 && !window.location.hash) {
      window.requestAnimationFrame(() => window.scrollTo(0, saved.y));
    }
  } catch { /* storage unavailable */ }
  // Links from another rack's uplink list land on the far item in the item list.
  const placementHash = /^#(placement-\d+)$/.exec(window.location.hash);
  if (placementHash && document.getElementById(placementHash[1])) {
    window.requestAnimationFrame(() => focusListItem(placementHash[1]));
  }
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
    const swivelled = button.closest('[data-rack-faces]')?.classList.contains('is-swivelled');
    const side = swivelled && button.dataset.altView ? button.dataset.altView : button.dataset.view;
    const view = side === 'rear' ? ' (rear view)' : '';
    if (statusText && button.dataset.itemSummary) statusText.textContent = `${button.dataset.itemSummary} · ${face} elevation${view}`;
  };
  document.querySelectorAll('.rack__device').forEach(button => {
    button.addEventListener('focus', () => describe(button));
    button.addEventListener('mouseenter', () => describe(button));
  });
  document.querySelectorAll('[data-rack-faces]').forEach(faces => faces.addEventListener('mouseleave', () => {
    if (statusText && !faces.contains(document.activeElement)) statusText.textContent = defaultStatus;
  }));

  // Clicking an installed item shows its ports and what they connect to.
  const portsDialog = document.querySelector('#rack-ports-dialog');
  const portsEdit = portsDialog?.querySelector('[data-ports-edit]');
  let editItem = null;
  const showPorts = (button) => {
    const id = button.dataset.inspectPlacement;
    const source = document.querySelector(`template[data-port-table="${CSS.escape(id)}"]`);
    if (!portsDialog || !source) return;
    portsDialog.dataset.itemId = id;
    portsDialog.querySelector('[data-ports-title]').textContent = source.dataset.itemTitle || 'Rack item';
    portsDialog.querySelector('[data-ports-type]').textContent = `${source.dataset.itemTypeLabel || 'Rack item'} · ports and connections`;
    const face = button.closest('[data-face]')?.dataset.face;
    const start = Number(button.dataset.startUnit);
    const end = start + Number(button.dataset.unitHeight) - 1;
    portsDialog.querySelector('[data-ports-position]').textContent = [end > start ? `U${start}–${end}` : `U${start}`, face && `${face} elevation`].filter(Boolean).join(' · ');
    portsDialog.querySelector('[data-ports-body]').replaceChildren(source.content.cloneNode(true));
    if (portsEdit) portsEdit.hidden = !editItem;
    openDialog(portsDialog, button);
  };
  document.querySelectorAll('[data-inspect-placement]').forEach(button => button.addEventListener('click', () => showPorts(button)));
  portsEdit?.addEventListener('click', () => {
    const id = portsDialog.dataset.itemId;
    const trigger = dialogTriggers.get(portsDialog);
    dialogTriggers.delete(portsDialog);
    portsDialog.close();
    editItem?.(id, trigger);
  });
  if (!form || !placeDialog) return;

  // Add / edit rack item dialog. Editing reuses every field used when adding.
  let items = [];
  try { items = JSON.parse(document.getElementById('rack-items-data')?.textContent || '[]'); } catch { items = []; }
  const itemsById = new Map(items.map(item => [String(item.id), item]));
  // Every rack item's network ports and PSUs, for choosing the remote end of a link.
  let catalog = [];
  try { catalog = JSON.parse(document.getElementById('rack-port-catalog')?.textContent || '[]'); } catch { catalog = []; }
  const catalogPorts = new Map();
  catalog.forEach(entry => entry.ports.forEach(port => catalogPorts.set(String(port.id), { ...port, item: entry })));
  // Which remote connector a port links to: network port to network port, outlet to PSU.
  const peerConnector = { data: 'data', iec: 'psu', '3pin': 'psu' };
  // Remote ports reopen with their rack item chosen, so the picker shows which rack it is in.
  const deviceValueFor = (entry) => `item:${entry.id}`;
  const preview = form.querySelector('[data-placement-preview]');
  const map = form.querySelector('[data-placement-map]');
  const mapCaption = form.querySelector('[data-placement-map-caption]');
  const faceplate = form.querySelector('[data-faceplate]');
  const faceplateName = form.querySelector('[data-faceplate-name]');
  const title = placeDialog.querySelector('[data-dialog-title]');
  const submit = form.querySelector('[data-submit-label]');
  const removeButton = placeDialog.querySelector('[data-edit-remove]');
  const connections = form.querySelector('[data-connections]');
  const linksTable = form.querySelector('[data-port-links]');
  const linksSummary = form.querySelector('[data-port-links-summary]');
  const linksPanel = form.querySelector('[data-port-links-panel]');
  const assetOptions = form.querySelector('template[data-asset-options]');
  const outletOptions = form.querySelector('template[data-outlet-options]');
  const connectorLabels = { data: 'Port', iec: 'IEC', '3pin': '3-pin', psu: 'PSU', kvm: 'Device' };
  let editingId = null;
  let heightTouched = false;
  // Whether the user picked a type; until then a linked asset picks it.
  let typeTouched = false;
  let countsTouched = new Set();
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
        asset: row.querySelector('[data-link-target]')?.value || '',
        peer: row.querySelector('[data-link-peer]')?.value || '',
        label: row.querySelector('input').value,
        network: linkValues.get(row.dataset.portRow)?.network || '',
      });
    });
  };
  // Fill a remote-port picker with the matching ports on the chosen device.
  const fillPeers = (peerSelect, key, deviceValue, current, autoPick) => {
    const wanted = peerConnector[key];
    const candidates = catalog.filter(entry => String(entry.id) !== String(editingId) && deviceValue !== '' &&
      (deviceValue.startsWith('item:') ? `item:${entry.id}` === deviceValue : String(entry.asset_id) === deviceValue));
    peerSelect.replaceChildren(new Option(wanted === 'psu' ? 'Choose the PSU it feeds' : 'Choose the remote port', ''));
    const free = [];
    candidates.forEach(entry => entry.ports.filter(port => port.connector === wanted).forEach(port => {
      const taken = wanted === 'psu' ? port.source_port_id : (port.peer_port_id || port.network_peer);
      const inUse = taken && String(port.id) !== String(current);
      const option = new Option(`${candidates.length > 1 ? `${entry.name} · ` : ''}${port.label}${inUse ? ' (in use)' : ''}`, String(port.id));
      peerSelect.append(option);
      if (!inUse) free.push(String(port.id));
    }));
    const available = peerSelect.options.length > 1;
    peerSelect.hidden = !available;
    peerSelect.disabled = !available;
    peerSelect.value = current && [...peerSelect.options].some(option => option.value === String(current)) ? String(current) : '';
    if (!peerSelect.value && autoPick && free.length === 1) peerSelect.value = free[0];
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
        // A power supply is fed from another unit's outlet; other ports link to
        // an asset or rack item, and network ports and outlets then pick the
        // specific remote port (a network port, or the PSU the outlet feeds).
        const isPower = key === 'psu';
        const select = document.createElement('select');
        select.className = 'input input--small';
        select.dataset.linkTarget = '';
        select.name = `port-${id}-${isPower ? 'source' : 'asset'}`;
        select.setAttribute('aria-label', `${isPower ? 'Outlet feeding' : 'Device on'} ${name.textContent}`);
        select.append((isPower ? outletOptions : assetOptions).content.cloneNode(true));
        if (isPower) {
          select.querySelectorAll('option[data-equipment-id]').forEach(option => {
            if (editingId !== null && option.dataset.equipmentId === editingId) option.remove();
          });
        } else if (editingId !== null) {
          // An item cannot link to itself.
          select.querySelector(`option[value="item:${CSS.escape(editingId)}"]`)?.remove();
        }
        if (!isPower && !peerConnector[key]) {
          // KVM device ports link to an asset only.
          select.querySelectorAll('optgroup[data-rack-items]').forEach(group => group.remove());
        }
        select.value = saved.asset;
        if (select.value !== saved.asset) select.value = '';
        let peer = null;
        let networkLink = null;
        if (saved.network && key === 'data') {
          // Linked from an asset's interfaces on the network map; changed there.
          networkLink = document.createElement('span');
          networkLink.className = 'rack-port-link__network';
          networkLink.textContent = `↔ ${saved.network} (network map)`;
        } else if (peerConnector[key]) {
          peer = document.createElement('select');
          peer.className = 'input input--small';
          peer.dataset.linkPeer = '';
          peer.name = `port-${id}-peer`;
          peer.setAttribute('aria-label', `${peerConnector[key] === 'psu' ? 'PSU fed by' : 'Remote port for'} ${name.textContent}`);
          fillPeers(peer, key, select.value, saved.peer, false);
          select.addEventListener('change', () => fillPeers(peer, key, select.value, '', true));
          row.classList.add('rack-port-link--peer');
        }
        const label = document.createElement('input');
        label.className = 'input input--small';
        label.name = `port-${id}-label`;
        label.maxLength = 191;
        label.placeholder = isPower ? 'Label, e.g. Circuit B2' : key === 'kvm' ? 'Label, e.g. Web server' : 'Label, e.g. Printer';
        label.setAttribute('aria-label', `Label for ${name.textContent}`);
        label.value = saved.label;
        row.append(name, ...(networkLink ? [networkLink] : [select, ...(peer ? [peer] : [])]), label);
        linksTable.append(row);
        total += 1;
        if (saved.asset || saved.label || saved.peer || saved.network) linked += 1;
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
    faceplate.hidden = reservation;
    if (editingId) submit.textContent = 'Save changes';
    else submit.textContent = reservation ? 'Reserve space' : 'Place equipment';
    renderLinks();
  };
  const applyTypeDefaults = (input) => {
    // New items take the type's default counts unless the user typed one;
    // edits keep existing counts and only seed newly relevant connectors.
    typeConnectors(input).forEach(({ key, count }) => {
      const field = form.elements[`port_count_${key}`];
      if (!field) return;
      if (editingId === null ? !countsTouched.has(key) : field.disabled) field.value = String(count);
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
  const resetForm = () => {
    form.reset();
    linkValues = new Map();
    // Drop the previous item's rows so they are not remembered into this one.
    linksTable.replaceChildren();
    form.querySelectorAll('[data-connector-count] input').forEach(input => { input.disabled = true; });
  };
  form.elements.unit_height.addEventListener('input', () => { heightTouched = true; });
  form.querySelectorAll('input[name="item_type"]').forEach(input => input.addEventListener('change', () => {
    typeTouched = true;
    applyTypeDefaults(input);
    updateKind();
    updatePreview();
  }));
  form.elements.asset_id.addEventListener('change', () => {
    // A new item takes the linked asset's type, as on the asset register.
    const key = form.elements.asset_id.selectedOptions[0]?.dataset.itemType;
    const input = key && form.querySelector(`input[name="item_type"][value="${CSS.escape(key)}"]`);
    if (editingId !== null || typeTouched || !input || input.checked) return;
    input.checked = true;
    applyTypeDefaults(input);
    updateKind();
    updatePreview();
  });
  document.querySelectorAll('[data-place-open]').forEach(button => button.addEventListener('click', () => {
    resetForm();
    editingId = null;
    heightTouched = false;
    typeTouched = false;
    countsTouched = new Set();
    setMode('add');
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
    Object.entries(item.port_counts || {}).forEach(([key, count]) => {
      const field = form.elements[`port_count_${key}`];
      if (field) { field.value = String(count); field.disabled = false; }
    });
    (item.ports || []).forEach(port => {
      // A remote port link reopens with its device and port selected.
      const remoteId = port.connector === 'psu' ? null : (port.peer_port_id || port.fed_port_id);
      const remote = remoteId ? catalogPorts.get(String(remoteId)) : null;
      const target = port.connector === 'psu' ? port.source_port_id : (remote ? deviceValueFor(remote.item) : port.asset_id);
      linkValues.set(`${port.connector}-${port.ordinal}`, {
        asset: target ? String(target) : '', peer: remote ? String(remoteId) : '', label: port.label || '',
        network: port.network_peer || '',
      });
    });
    applyTypeDefaults(selectedType());
    form.querySelector('[data-selected-position]').textContent = positionText({
      unit: item.start_unit, height: item.unit_height, lane: item.start_lane,
      width: item.width_lanes, face: item.face, depthMode: item.depth_mode,
    });
    updateKind(); updatePreview();
    linksPanel.open = (item.ports || []).some(port => port.asset_id || port.label || port.network_peer);
    openDialog(placeDialog, trigger);
  };
  editItem = editEquipment;
  document.querySelectorAll('[data-edit-equipment]').forEach(button => button.addEventListener('click', () => editEquipment(button.dataset.editEquipment, button)));
  removeButton?.addEventListener('click', () => {
    const id = editingId;
    placeDialog.close();
    document.getElementById(`placement-${id}`)?.querySelector('[data-confirm-remove]')?.requestSubmit();
  });
  form.addEventListener('input', event => {
    if (event.target.name?.startsWith('port-')) {
      const rows = [...linksTable.querySelectorAll('[data-port-row]')];
      const linked = rows.filter(row => row.querySelector('.rack-port-link__network')
        || row.querySelector('select')?.value || row.querySelector('input').value).length;
      linksSummary.textContent = rows.length ? `Port links · ${linked} of ${rows.length} linked` : 'Port links';
      return;
    }
    if (event.target.name?.startsWith('port_count_')) countsTouched.add(event.target.name.slice('port_count_'.length));
    if (event.target.name?.startsWith('port_count_') || event.target.name === 'entry_kind') updateKind();
    updatePreview();
  });
  // Device / product image gallery. Images live in a per-type library; attaching one
  // links it to this rack item. The section is inside the equipment fields, so it is
  // hidden for reservations and inherits the dialog's auth + CSRF handling.
  const imagesRoot = form.querySelector('[data-rack-images]');
  if (imagesRoot) {
    const imagesStatus = imagesRoot.querySelector('[data-image-status]');
    const say = (message) => { imagesStatus.textContent = message || ''; imagesStatus.hidden = !message; };
    const currentType = () => selectedType()?.value || '';
    const kindOf = (node) => node.closest('[data-image-kind]')?.dataset.imageKind || '';
    const panelOf = (node) => node.closest('[data-image-kind]');
    const imageCard = (image, action) => {
      const card = document.createElement('div');
      card.className = `rack-image${action === 'detach' ? ' rack-image--attached' : ''}`;
      card.dataset.imageId = String(image.id);
      card.setAttribute('role', 'listitem');
      const img = document.createElement('img');
      img.src = image.thumb_url || image.url;
      img.alt = image.caption || 'Rack item image';
      img.loading = 'lazy';
      card.appendChild(img);
      if (image.caption) {
        const caption = document.createElement('span');
        caption.className = 'rack-image__caption';
        caption.textContent = image.caption;
        card.appendChild(caption);
      }
      if (action) {
        const actions = document.createElement('div');
        actions.className = 'rack-image__actions';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'button button--secondary';
        if (action === 'detach') button.dataset.imageDetach = String(image.id);
        else button.dataset.imageAttach = String(image.id);
        button.textContent = action === 'detach' ? 'Detach' : 'Attach';
        actions.appendChild(button);
        card.appendChild(actions);
      }
      return card;
    };
    const render = (kind, attached, library) => {
      const panel = imagesRoot.querySelector(`[data-image-kind="${kind}"]`);
      if (!panel) return;
      const editing = editingId !== null;
      const attGrid = panel.querySelector('[data-image-attached]');
      const libGrid = panel.querySelector('[data-image-library]');
      panel.querySelector('[data-image-attached-row]').hidden = !editing;
      attGrid.replaceChildren(attached.map((image) => imageCard(image, 'detach')));
      const attachedIds = new Set(attached.map((image) => image.id));
      libGrid.replaceChildren(library.filter((image) => !attachedIds.has(image.id))
        .map((image) => imageCard(image, editing ? 'attach' : null)));
    };
    const load = async () => {
      const type = currentType();
      if (!type || form.elements.entry_kind.value === 'reservation') return;
      say('');
      const libraryRequest = fetch(`/api/infrastructure/rack-item-images/library?item_type=${encodeURIComponent(type)}`);
      const attachedRequest = editingId !== null
        ? fetch(`/api/infrastructure/rack-equipment/${encodeURIComponent(editingId)}/images`)
        : Promise.resolve({ ok: true, json: () => Promise.resolve({ device: [], product: [] }) });
      const [libraryRes, attachedRes] = await Promise.all([libraryRequest, attachedRequest]);
      if (!libraryRes.ok) { say('Could not load images.'); return; }
      const library = await libraryRes.json().catch(() => ({ device: [], product: [] }));
      const attached = attachedRes.ok ? await attachedRes.json().catch(() => ({ device: [], product: [] })) : { device: [], product: [] };
      render('device', attached.device || [], library.device || []);
      render('product', attached.product || [], library.product || []);
    };
    const attachOrDetach = async (button, imageId, attach) => {
      if (!editingId || !imageId || !kindOf(button) || !currentType()) return;
      button.disabled = true;
      say(attach ? 'Attaching…' : 'Detaching…');
      try {
        const res = await fetch(`/api/infrastructure/rack-equipment/${encodeURIComponent(editingId)}/images/${encodeURIComponent(imageId)}/${attach ? 'attach' : 'detach'}`, { method: 'POST' });
        if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.detail || 'Action failed'); }
        say(attach ? 'Image attached.' : 'Image detached.');
        await load();
      } catch (error) { say(error.message); button.disabled = false; }
    };
    const linkAfterAdd = async (imageId) => {
      if (editingId !== null && imageId) {
        await fetch(`/api/infrastructure/rack-equipment/${encodeURIComponent(editingId)}/images/${encodeURIComponent(imageId)}/attach`, { method: 'POST' });
      }
    };
    imagesRoot.addEventListener('click', (event) => {
      const attach = event.target.closest('[data-image-attach]');
      const detach = event.target.closest('[data-image-detach]');
      if (attach) attachOrDetach(attach, attach.dataset.imageAttach, true);
      else if (detach) attachOrDetach(detach, detach.dataset.imageDetach, false);
    });
    imagesRoot.querySelectorAll('[data-image-upload]').forEach((input) => input.addEventListener('change', async () => {
      const file = input.files?.[0];
      const panel = panelOf(input);
      const kind = kindOf(input);
      const type = currentType();
      input.value = '';
      if (!file || !panel || !kind || !type) return;
      say('Uploading…');
      const body = new FormData();
      body.set('image', file, file.name);
      body.set('item_type', type);
      body.set('kind', kind);
      body.set('caption', panel.querySelector('[data-image-caption]')?.value.trim() || '');
      try {
        const res = await fetch('/api/infrastructure/rack-item-images', { method: 'POST', body });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Upload failed');
        await linkAfterAdd(data.id);
        say(data.duplicate ? 'Already in the library; linked to this item.' : 'Image uploaded.');
        panel.querySelector('[data-image-caption]').value = '';
        await load();
      } catch (error) { say(error.message); }
    }));
    // "Import from shop" picker: a searchable product dropdown (native datalist)
    // so a tech types a product name/SKU instead of an ID. Mirrors the
    // cross-sell/up-sell SKU typeahead; the chosen product's id is captured in
    // the hidden input that the import request reads.
    const productSearchInput = imagesRoot.querySelector('[data-image-import-product-search]');
    const productHiddenInput = imagesRoot.querySelector('[data-image-import-product]');
    const productDatalist = document.getElementById('rack-image-product-suggestions');
    if (productSearchInput && productHiddenInput && productDatalist) {
      let productResults = [];
      let latestProductQuery = '';
      let productSearchTimer = null;
      const resolveProductId = () => {
        const text = String(productSearchInput.value || '').trim();
        const lower = text.toLowerCase();
        const match = productResults.find((p) => (p.sku || '').toLowerCase() === lower)
          || productResults.find((p) => (p.name || '').toLowerCase() === lower)
          || productResults.find((p) => String(p.id) === text);
        productHiddenInput.value = match ? String(match.id) : '';
      };
      const runProductSearch = async (query) => {
        const trimmed = String(query || '').trim();
        latestProductQuery = trimmed;
        if (trimmed.length < 2) {
          productResults = [];
          productDatalist.innerHTML = '';
          productHiddenInput.value = '';
          return;
        }
        try {
          const url = new URL('/api/infrastructure/rack-item-images/products/search', window.location.origin);
          url.searchParams.set('q', trimmed);
          url.searchParams.set('limit', '8');
          const res = await fetch(url.toString(), { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
          if (!res.ok) throw new Error(`Product search failed (${res.status})`);
          const data = await res.json().catch(() => []);
          if (latestProductQuery !== trimmed) return; // ignore out-of-order responses
          productResults = Array.isArray(data) ? data : [];
          productDatalist.innerHTML = '';
          productResults.forEach((product) => {
            const option = document.createElement('option');
            option.value = product.sku || product.name || String(product.id);
            option.label = product.sku ? `${product.name || ''} (${product.sku})` : (product.name || String(product.id));
            productDatalist.appendChild(option);
          });
          resolveProductId();
        } catch (error) {
          console.error('Unable to search shop products', error);
        }
      };
      const scheduleProductSearch = (query) => {
        if (productSearchTimer) window.clearTimeout(productSearchTimer);
        productSearchTimer = window.setTimeout(() => runProductSearch(query), 200);
      };
      productSearchInput.addEventListener('input', () => {
        productHiddenInput.value = '';
        resolveProductId();
        scheduleProductSearch(productSearchInput.value);
      });
      productSearchInput.addEventListener('change', resolveProductId);
      productSearchInput.addEventListener('blur', () => { window.setTimeout(resolveProductId, 0); });
    }
    imagesRoot.querySelectorAll('[data-image-import]').forEach((button) => button.addEventListener('click', async () => {
      const panel = panelOf(button);
      const input = panel?.querySelector('[data-image-import-product]');
      const productId = Number(input?.value);
      const kind = kindOf(button);
      const type = currentType();
      if (!panel || !productId || !kind || !type) { say('Search and pick a product to import.'); return; }
      button.disabled = true;
      say('Importing…');
      try {
        const res = await fetch('/api/infrastructure/rack-item-images/import-shop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_id: productId, item_type: type, kind }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Import failed');
        await linkAfterAdd(data.id);
        say('Product image imported.');
        input.value = '';
        if (productSearchInput) productSearchInput.value = '';
        if (productDatalist) productDatalist.innerHTML = '';
        await load();
      } catch (error) { say(error.message); }
      finally { button.disabled = false; }
    }));
    placeDialog.addEventListener('open', load);
    form.querySelectorAll('input[name="item_type"]').forEach((input) => input.addEventListener('change', load));
  }
})();
