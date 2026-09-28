(() => {
  const RADIO_KINDS = new Set(['radio', 'wifi']);
  const NETWORK_CATEGORIES = new Set(['Internet edge', 'Security', 'Network', 'Wireless']);

  // ---- Forms shared by the map page and the asset page -------------------
  document.querySelectorAll('[data-interface-form]').forEach((form) => {
    const kind = form.querySelector('[data-interface-kind]');
    const radio = form.querySelector('[data-radio-fields]');
    if (!kind || !radio) return;
    const sync = () => {
      const isRadio = RADIO_KINDS.has(kind.value);
      radio.hidden = !isRadio;
      radio.disabled = !isRadio;
    };
    kind.addEventListener('change', sync);
    sync();
  });

  document.querySelectorAll('[data-link-form]').forEach((form) => {
    const medium = form.querySelector('[data-link-medium]');
    const ends = [form.querySelector('[name="a"]'), form.querySelector('[name="b"]')];
    const wireless = form.querySelectorAll('[data-wireless-field]');
    const isRadio = (select) => select?.selectedOptions[0]?.dataset.radio === '1';
    const sync = () => {
      const show = medium?.value === 'wireless';
      wireless.forEach((field) => {
        field.hidden = !show;
        field.querySelector('input').disabled = !show;
      });
    };
    ends.forEach((select) => select?.addEventListener('change', () => {
      // Two radios can only be joined wirelessly; suggest it.
      if (medium && ends.every(isRadio)) medium.value = 'wireless';
      else if (medium && medium.value === 'wireless' && ends.some((end) => end?.value && !isRadio(end))) medium.value = 'copper';
      sync();
    }));
    medium?.addEventListener('change', sync);
    sync();
  });

  // ---- Device type pickers --------------------------------------------------
  document.querySelectorAll('[data-nm-types]').forEach((button) => {
    button.addEventListener('click', () => {
      const scope = button.closest('form');
      const mode = button.dataset.nmTypes;
      scope.querySelectorAll('input[name="types"]').forEach((box) => {
        if (mode === 'all') box.checked = true;
        else if (mode === 'none') box.checked = false;
        else if (mode === 'present') box.checked = Number(box.dataset.count) > 0;
        else if (mode === 'network') box.checked = NETWORK_CATEGORIES.has(box.dataset.category);
      });
    });
  });

  // Build query parameters from a map options form. Selecting every type is
  // the same as no filter, so it is left out to keep links short.
  const optionParams = (form) => {
    const data = new FormData(form);
    const boxes = Array.from(form.querySelectorAll('input[name="types"]'));
    if (boxes.length && boxes.every((box) => box.checked)) data.delete('types');
    // Hiding unlinked devices is the default, so only "show" needs sending.
    const hideUnlinked = data.getAll('unlinked').includes('hide');
    data.delete('unlinked');
    if (!hideUnlinked && form.querySelector('input[name="unlinked"]')) data.append('unlinked', 'show');
    const params = new URLSearchParams();
    for (const [key, value] of data.entries()) {
      if (key === 'csrf_token' || key === 'format' || value === '') continue;
      params.append(key, value);
    }
    return params;
  };
  const noTypes = (form) => {
    const boxes = Array.from(form.querySelectorAll('input[name="types"]'));
    return boxes.length > 0 && !boxes.some((box) => box.checked);
  };

  const filters = document.querySelector('.nm-filters');
  filters?.addEventListener('submit', (event) => {
    event.preventDefault();
    if (noTypes(filters)) {
      window.alert('Choose at least one device type.');
      return;
    }
    const params = optionParams(filters);
    window.location.search = params.toString();
  });

  // ---- Modals ----------------------------------------------------------------
  let trigger = null;
  const closeModal = (modal) => {
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    trigger?.focus();
  };
  document.querySelectorAll('[data-nm-modal-open]').forEach((button) => {
    button.addEventListener('click', () => {
      const modal = document.getElementById(button.dataset.nmModalOpen);
      if (!modal) return;
      trigger = button;
      modal.hidden = false;
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
      modal.querySelector('input, select, button')?.focus();
    });
  });
  document.querySelectorAll('[data-nm-modal-close]').forEach((button) => {
    button.addEventListener('click', () => closeModal(button.closest('.modal')));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const open = document.querySelector('#export-modal:not([hidden])');
    if (open) closeModal(open);
  });

  // ---- Export ------------------------------------------------------------------
  const exportForm = document.querySelector('[data-nm-export]');
  const exportStatus = exportForm?.querySelector('[data-nm-export-status]');
  const paperField = exportForm?.querySelector('[data-nm-paper]');
  const selectedFormat = () => exportForm?.querySelector('input[name="format"]:checked')?.value || 'pdf';
  const syncPaper = () => { if (paperField) paperField.hidden = selectedFormat() !== 'pdf'; };
  exportForm?.querySelectorAll('input[name="format"]').forEach((input) => input.addEventListener('change', syncPaper));
  syncPaper();

  const download = (blob, filename) => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  // Rasterise the server-drawn SVG in the browser so PNG needs no image
  // libraries on the server. The SVG is self-contained (icons are inline
  // symbols), so the canvas is never tainted.
  const exportPng = async (params) => {
    const response = await fetch(`/network-map/export.svg?${params}`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`Export failed (${response.status})`);
    const text = await response.text();
    const match = text.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
    const width = match ? Number(match[1]) : 1600;
    const height = match ? Number(match[2]) : 1000;
    const scale = Math.max(1, Math.min(3, 12000 / Math.max(width, height)));
    const url = URL.createObjectURL(new Blob([text], { type: 'image/svg+xml' }));
    try {
      const image = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error('The map could not be rendered as an image'));
        img.src = url;
      });
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      const context = canvas.getContext('2d');
      context.fillStyle = '#ffffff';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('The map is too large for a PNG; try PDF or SVG');
      download(blob, `network-map-${new Date().toISOString().slice(0, 10)}.png`);
    } finally {
      URL.revokeObjectURL(url);
    }
  };

  exportForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (noTypes(exportForm)) {
      exportStatus.textContent = 'Choose at least one device type.';
      return;
    }
    const format = selectedFormat();
    const params = optionParams(exportForm);
    if (format !== 'pdf') params.delete('paper');
    if (format === 'pdf') {
      window.location.href = `/network-map/export.pdf?${params}`;
      exportStatus.textContent = 'Preparing PDF…';
      return;
    }
    if (format === 'svg') {
      params.set('download', '1');
      window.location.href = `/network-map/export.svg?${params}`;
      return;
    }
    exportStatus.textContent = 'Rendering PNG…';
    try {
      await exportPng(params);
      exportStatus.textContent = 'PNG downloaded.';
    } catch (error) {
      exportStatus.textContent = error.message;
    }
  });

  // ---- Diagram: pan, zoom, select ----------------------------------------------
  const viewport = document.querySelector('[data-nm-viewport]');
  const svg = viewport?.querySelector('svg');
  if (!svg) return;
  let payload = { nodes: {} };
  try {
    payload = JSON.parse(document.getElementById('nm-payload')?.textContent || '{}');
  } catch (error) {
    payload = { nodes: {} };
  }
  const [, , fullW, fullH] = svg.getAttribute('viewBox').split(/\s+/).map(Number);
  svg.removeAttribute('width');
  svg.removeAttribute('height');
  let view = { x: 0, y: 0, w: fullW, h: fullH };
  const apply = () => svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
  const fit = () => {
    const box = viewport.getBoundingClientRect();
    const ratio = box.width / Math.max(1, box.height);
    view = { x: 0, y: 0, w: fullW, h: fullH };
    if (fullW / fullH > ratio) view.h = fullW / ratio; else view.w = fullH * ratio;
    view.x = (fullW - view.w) / 2;
    view.y = (fullH - view.h) / 2;
    apply();
  };
  const zoom = (factor, cx, cy) => {
    const box = viewport.getBoundingClientRect();
    const px = cx === undefined ? 0.5 : (cx - box.left) / box.width;
    const py = cy === undefined ? 0.5 : (cy - box.top) / box.height;
    const w = Math.min(fullW * 4, Math.max(120, view.w * factor));
    const h = w * (view.h / view.w);
    view.x += (view.w - w) * px;
    view.y += (view.h - h) * py;
    view.w = w;
    view.h = h;
    apply();
  };
  fit();
  window.addEventListener('resize', fit);
  viewport.addEventListener('wheel', (event) => {
    event.preventDefault();
    zoom(event.deltaY > 0 ? 1.15 : 1 / 1.15, event.clientX, event.clientY);
  }, { passive: false });
  document.querySelectorAll('[data-nm-zoom]').forEach((button) => {
    button.addEventListener('click', () => {
      const mode = button.dataset.nmZoom;
      if (mode === 'fit') fit(); else zoom(mode === 'in' ? 1 / 1.3 : 1.3);
    });
  });
  viewport.addEventListener('keydown', (event) => {
    const step = view.w * 0.1;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (moves[event.key]) {
      event.preventDefault();
      view.x += moves[event.key][0];
      view.y += moves[event.key][1];
      apply();
    } else if (event.key === '+' || event.key === '=') zoom(1 / 1.3);
    else if (event.key === '-') zoom(1.3);
  });

  let drag = null;
  viewport.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    drag = { x: event.clientX, y: event.clientY, view: { ...view }, moved: false };
    viewport.setPointerCapture(event.pointerId);
  });
  viewport.addEventListener('pointermove', (event) => {
    if (!drag) return;
    const box = viewport.getBoundingClientRect();
    const dx = (event.clientX - drag.x) * (view.w / box.width);
    const dy = (event.clientY - drag.y) * (view.h / box.height);
    if (Math.abs(event.clientX - drag.x) + Math.abs(event.clientY - drag.y) > 4) drag.moved = true;
    view.x = drag.view.x - dx;
    view.y = drag.view.y - dy;
    apply();
  });
  const endDrag = (event) => {
    if (!drag) return;
    const wasDrag = drag.moved;
    drag = null;
    if (!wasDrag) {
      const node = document.elementFromPoint(event.clientX, event.clientY)?.closest('.nm-node');
      select(node ? node.dataset.nodeId : null);
    }
  };
  viewport.addEventListener('pointerup', endDrag);
  viewport.addEventListener('pointercancel', () => { drag = null; });

  const details = document.querySelector('[data-nm-details-body]');
  const detailsEmpty = document.querySelector('[data-nm-details-empty]');
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (className) node.className = className;
    return node;
  };

  const select = (nodeId) => {
    svg.querySelectorAll('.is-selected, .is-linked, .is-dimmed').forEach((item) =>
      item.classList.remove('is-selected', 'is-linked', 'is-dimmed'));
    const info = nodeId ? payload.nodes[nodeId] : null;
    if (!info) {
      details.hidden = true;
      detailsEmpty.hidden = false;
      return;
    }
    const neighbours = new Set([nodeId]);
    svg.querySelectorAll('.nm-edge').forEach((edge) => {
      const linked = edge.dataset.a === nodeId || edge.dataset.b === nodeId;
      edge.classList.add(linked ? 'is-linked' : 'is-dimmed');
      if (linked) { neighbours.add(edge.dataset.a); neighbours.add(edge.dataset.b); }
    });
    svg.querySelectorAll('.nm-node').forEach((node) => {
      if (node.dataset.nodeId === nodeId) node.classList.add('is-selected');
      else if (!neighbours.has(node.dataset.nodeId)) node.classList.add('is-dimmed');
    });

    details.replaceChildren();
    const header = element('div', null, 'nm-details__header');
    const icon = element('img');
    icon.src = info.icon;
    icon.alt = '';
    icon.width = 36;
    icon.height = 36;
    const heading = element('div');
    heading.append(element('h2', info.label), element('p', [info.type, info.rack, info.kind === 'asset' || info.kind === 'item' ? info.site : null].filter(Boolean).join(' · '), 'text-muted'));
    header.append(icon, heading);
    details.append(header);
    if (info.url) {
      const open = element('a', info.kind === 'asset' ? 'Open asset' : 'Open', 'button button--secondary button--small');
      open.href = info.url;
      details.append(open);
    }
    const section = (title, items) => {
      if (!items.length) return;
      details.append(element('h3', title));
      const list = element('ul', null, 'nm-details__list');
      items.forEach((item) => list.append(item));
      details.append(list);
    };
    section('Addresses', info.ips.map((ip) => element('li', ip, 'nm-mono')));
    section('Interfaces', info.interfaces.map((item) => {
      const li = element('li');
      li.append(element('strong', item.name));
      const extra = RADIO_KINDS.has(item.kind) ? item.radio : [item.ip, item.speed, item.vlan ? `VLAN ${item.vlan}` : null].filter(Boolean).join(' · ');
      if (extra) li.append(element('span', ` ${extra}`, RADIO_KINDS.has(item.kind) ? 'nm-radio-text' : 'text-muted'));
      return li;
    }));
    section('Links', info.links.map((link) => {
      const li = element('li');
      const peer = element('button', link.peer, 'nm-link-button');
      peer.type = 'button';
      peer.addEventListener('click', () => { select(link.peer_id); centreOn(link.peer_id); });
      if (link.port) li.append(element('span', `${link.port} → `));
      li.append(peer);
      const meta = [link.peer_port, link.medium, ...(link.details || []), link.label, link.count > 1 ? `×${link.count}` : null].filter(Boolean).join(' · ');
      if (meta) li.append(element('span', ` ${meta}`, 'text-muted'));
      return li;
    }));
    section('Details', (info.facts || []).map((fact) => element('li', `${fact.label}: ${fact.value}`)));
    details.hidden = false;
    detailsEmpty.hidden = true;
  };

  const centreOn = (nodeId) => {
    const node = svg.querySelector(`.nm-node[data-node-id="${CSS.escape(nodeId)}"] .nm-card`);
    if (!node) return;
    const x = Number(node.getAttribute('x')) + Number(node.getAttribute('width')) / 2;
    const y = Number(node.getAttribute('y')) + Number(node.getAttribute('height')) / 2;
    if (view.w > fullW * 0.6) {
      const w = Math.min(view.w, 900);
      view.h *= w / view.w;
      view.w = w;
    }
    view.x = x - view.w / 2;
    view.y = y - view.h / 2;
    apply();
  };

  const search = document.querySelector('[data-nm-search]');
  search?.addEventListener('change', () => {
    const term = search.value.trim().toLowerCase();
    if (!term) return;
    const match = Object.values(payload.nodes).find((node) => node.label.toLowerCase() === term)
      || Object.values(payload.nodes).find((node) => node.label.toLowerCase().includes(term)
        || (node.ips || []).some((ip) => ip.startsWith(term)));
    if (match) { select(match.id); centreOn(match.id); }
  });
})();
