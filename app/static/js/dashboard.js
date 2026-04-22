/* MyPortal Dashboard customisation client.
 *
 * Implements layout edit mode: drag-to-move (HTML5 drag), resize via the
 * bottom-right handle, remove cards, and an "add card" catalogue modal.
 * Layout changes are debounced and persisted to ``/api/dashboard/layout``.
 *
 * No external dependencies — tries to be small and accessible:
 *   - All controls are <button>s with aria-labels.
 *   - When edit mode is enabled the Up/Down/Left/Right keys move the focused
 *     card by one grid cell; Shift+arrow keys resize.
 */
(function () {
  'use strict';

  const root = document.querySelector('[data-dashboard]');
  if (!root) return;

  const grid = root.querySelector('[data-dashboard-grid]');
  if (!grid) return;

  const layoutEndpoint = root.dataset.layoutEndpoint || '/api/dashboard/layout';
  const cardEndpoint = root.dataset.cardEndpoint || '/api/dashboard/cards';
  const resetEndpoint = root.dataset.resetEndpoint || '/api/dashboard/layout/reset';
  const gridColumns = parseInt(root.dataset.gridColumns || '24', 10) || 24;
  const maxGridRows = 60;

  const optionsDetails = root.querySelector('[data-dashboard-options]');
  const editToggle = root.querySelector('[data-dashboard-edit]');
  const addButton = root.querySelector('[data-dashboard-add]');
  const resetButton = root.querySelector('[data-dashboard-reset]');
  const modal = root.querySelector('[data-dashboard-add-modal]');
  const modalCloseButtons = modal ? modal.querySelectorAll('[data-dashboard-add-close]') : [];
  const search = modal ? modal.querySelector('[data-dashboard-add-search]') : null;
  const catalogueList = modal ? modal.querySelector('[data-dashboard-catalogue]') : null;

  let editMode = false;
  let saveTimer = null;

  // ------------------------------------------------------------------
  // Options dropdown helpers
  // ------------------------------------------------------------------

  function closeOptionsDropdown() {
    if (optionsDetails && optionsDetails.open) {
      optionsDetails.open = false;
    }
  }

  // Close dropdown when clicking outside.
  document.addEventListener('click', function (event) {
    if (optionsDetails && !optionsDetails.contains(event.target)) {
      closeOptionsDropdown();
    }
  });

  // Close dropdown on Escape; also close modal on Escape.
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      if (optionsDetails && optionsDetails.open) {
        closeOptionsDropdown();
      }
      if (modal && !modal.hidden) {
        closeModal();
      }
    }
  });

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function readLayoutFromDom() {
    const cards = grid.querySelectorAll('[data-dashboard-card]');
    const layout = [];
    cards.forEach((card) => {
      layout.push({
        id: card.dataset.dashboardCard,
        x: parseInt(card.dataset.cardX || '0', 10) || 0,
        y: parseInt(card.dataset.cardY || '0', 10) || 0,
        w: parseInt(card.dataset.cardW || '4', 10) || 4,
        h: parseInt(card.dataset.cardH || '2', 10) || 2,
      });
    });
    return layout;
  }

  function applyPosition(card) {
    const x = parseInt(card.dataset.cardX || '0', 10) || 0;
    const y = parseInt(card.dataset.cardY || '0', 10) || 0;
    const w = parseInt(card.dataset.cardW || '4', 10) || 4;
    const h = parseInt(card.dataset.cardH || '2', 10) || 2;
    card.style.gridColumn = (x + 1) + ' / span ' + w;
    card.style.gridRow = (y + 1) + ' / span ' + h;
  }

  function clamp(value, min, max) {
    if (value < min) return min;
    if (value > max) return max;
    return value;
  }

  function getCardRect(card) {
    return {
      x: parseInt(card.dataset.cardX || '0', 10) || 0,
      y: parseInt(card.dataset.cardY || '0', 10) || 0,
      w: parseInt(card.dataset.cardW || '4', 10) || 4,
      h: parseInt(card.dataset.cardH || '2', 10) || 2,
    };
  }

  function rectanglesOverlap(a, b) {
    return a.x < (b.x + b.w) && (a.x + a.w) > b.x && a.y < (b.y + b.h) && (a.y + a.h) > b.y;
  }

  function resolveCollisions(activeCard) {
    const cards = Array.from(grid.querySelectorAll('[data-dashboard-card]'));
    const activeRect = getCardRect(activeCard);
    let changed = true;
    let pass = 0;
    while (changed && pass < 80) {
      changed = false;
      pass += 1;
      cards.forEach((card) => {
        if (card === activeCard) return;
        const rect = getCardRect(card);
        if (!rectanglesOverlap(activeRect, rect)) return;
        const nextY = clamp(activeRect.y + activeRect.h, 0, maxGridRows);
        if (nextY !== rect.y) {
          card.dataset.cardY = String(nextY);
          applyPosition(card);
          changed = true;
        }
      });
    }
  }

  function setCardPosition(card, position) {
    const w = clamp(position.w, 1, gridColumns);
    const x = clamp(position.x, 0, gridColumns - w);
    const y = clamp(position.y, 0, maxGridRows);
    const h = clamp(position.h, 1, 12);
    card.dataset.cardX = String(x);
    card.dataset.cardY = String(y);
    card.dataset.cardW = String(w);
    card.dataset.cardH = String(h);
    applyPosition(card);
    resolveCollisions(card);
    schedulePersist();
  }

  function schedulePersist() {
    if (saveTimer) {
      window.clearTimeout(saveTimer);
    }
    saveTimer = window.setTimeout(persistLayout, 400);
  }

  async function persistLayout() {
    const layout = readLayoutFromDom();
    try {
      await fetch(layoutEndpoint, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': getCsrfToken(),
        },
        body: JSON.stringify({ cards: layout }),
      });
    } catch (err) {
      // Network errors are non-fatal: the layout will be re-attempted on the
      // next change.
      // eslint-disable-next-line no-console
      console.warn('Failed to persist dashboard layout', err);
    }
  }

  function setEditMode(active) {
    editMode = !!active;
    root.classList.toggle('dashboard--editing', editMode);
    if (editToggle) {
      editToggle.setAttribute('aria-pressed', editMode ? 'true' : 'false');
      const label = editToggle.querySelector('[data-dashboard-edit-label]');
      if (label) label.textContent = editMode ? 'Done editing' : 'Edit layout';
    }
    grid.querySelectorAll('[data-dashboard-card]').forEach((card) => {
      card.setAttribute('draggable', editMode ? 'true' : 'false');
      card.setAttribute('tabindex', editMode ? '0' : '-1');
      card.querySelectorAll('[data-dashboard-card-handle], [data-dashboard-card-remove], [data-dashboard-card-resize]').forEach((control) => {
        control.setAttribute('tabindex', editMode ? '0' : '-1');
        control.hidden = !editMode;
      });
    });
  }

  // ------------------------------------------------------------------
  // Grid cell calculation from pointer position
  // ------------------------------------------------------------------

  function getGridColWidth() {
    const gridRect = grid.getBoundingClientRect();
    const gap = parseFloat(getComputedStyle(grid).columnGap) || 0;
    return (gridRect.width - gap * (gridColumns - 1)) / gridColumns;
  }

  function getGridRowHeight() {
    const configured = parseFloat(getComputedStyle(grid).getPropertyValue('--dashboard-row-height'));
    if (configured > 0) return configured;
    return 36;
  }

  function getCellFromPointer(clientX, clientY) {
    const gridRect = grid.getBoundingClientRect();
    const colW = getGridColWidth();
    const rowH = getGridRowHeight();
    const gap = parseFloat(getComputedStyle(grid).columnGap) || 0;
    let col = Math.floor((clientX - gridRect.left) / (colW + gap));
    let row = Math.floor((clientY - gridRect.top + grid.scrollTop) / rowH);
    col = Math.max(0, Math.min(col, gridColumns - 1));
    row = Math.max(0, row);
    return { x: col, y: row };
  }

  // ------------------------------------------------------------------
  // Drag and drop — free placement (or swap when dropped on a card)
  // ------------------------------------------------------------------

  let draggedCard = null;
  let dropGhost = null;

  function createDropGhost(w, h) {
    removeDropGhost();
    const ghost = document.createElement('div');
    ghost.className = 'dashboard-drop-ghost';
    ghost.style.gridColumn = '1 / span ' + w;
    ghost.style.gridRow = '1 / span ' + h;
    ghost.setAttribute('aria-hidden', 'true');
    grid.appendChild(ghost);
    dropGhost = ghost;
    return ghost;
  }

  function removeDropGhost() {
    if (dropGhost && dropGhost.parentNode) {
      dropGhost.parentNode.removeChild(dropGhost);
    }
    dropGhost = null;
  }

  function updateDropGhost(x, y, w, h) {
    if (!dropGhost) return;
    dropGhost.style.gridColumn = (x + 1) + ' / span ' + w;
    dropGhost.style.gridRow = (y + 1) + ' / span ' + h;
  }

  function onDragStart(event) {
    if (!editMode) {
      event.preventDefault();
      return;
    }
    const card = event.target.closest('[data-dashboard-card]');
    if (!card) return;
    draggedCard = card;
    card.classList.add('dashboard-card--dragging');
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      try {
        event.dataTransfer.setData('text/plain', card.dataset.dashboardCard);
      } catch (err) {
        /* ignore */
      }
    }
    const w = parseInt(card.dataset.cardW, 10);
    const h = parseInt(card.dataset.cardH, 10);
    createDropGhost(w, h);
  }

  function onDragEnd() {
    if (draggedCard) {
      draggedCard.classList.remove('dashboard-card--dragging');
    }
    draggedCard = null;
    removeDropGhost();
  }

  function onDragOver(event) {
    if (!editMode || !draggedCard) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';

    // Update ghost to show where card would land.
    const w = parseInt(draggedCard.dataset.cardW, 10);
    const h = parseInt(draggedCard.dataset.cardH, 10);
    const cell = getCellFromPointer(event.clientX, event.clientY);
    const clampedX = clamp(cell.x, 0, gridColumns - w);
    updateDropGhost(clampedX, cell.y, w, h);
  }

  function onDrop(event) {
    if (!editMode || !draggedCard) return;
    event.preventDefault();
    const w = parseInt(draggedCard.dataset.cardW, 10);
    const h = parseInt(draggedCard.dataset.cardH, 10);
    const cell = getCellFromPointer(event.clientX, event.clientY);
    setCardPosition(draggedCard, { x: cell.x, y: cell.y, w: w, h: h });
    onDragEnd();
  }

  // ------------------------------------------------------------------
  // Resize handle
  // ------------------------------------------------------------------

  let resizingCard = null;
  let resizeStart = null;

  function onResizePointerDown(event) {
    if (!editMode) return;
    const handle = event.target.closest('[data-dashboard-card-resize]');
    if (!handle) return;
    const card = handle.closest('[data-dashboard-card]');
    if (!card) return;
    event.preventDefault();
    resizingCard = card;
    const colW = getGridColWidth();
    const rowH = getGridRowHeight();
    resizeStart = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      colWidth: colW,
      rowHeight: rowH,
      startW: parseInt(card.dataset.cardW, 10),
      startH: parseInt(card.dataset.cardH, 10),
    };
    window.addEventListener('pointermove', onResizePointerMove);
    window.addEventListener('pointerup', onResizePointerUp);
  }

  function onResizePointerMove(event) {
    if (!resizingCard || !resizeStart) return;
    const dx = event.clientX - resizeStart.pointerX;
    const dy = event.clientY - resizeStart.pointerY;
    const newW = Math.round(resizeStart.startW + dx / Math.max(resizeStart.colWidth, 1));
    const newH = Math.round(resizeStart.startH + dy / Math.max(resizeStart.rowHeight, 1));
    setCardPosition(resizingCard, {
      x: parseInt(resizingCard.dataset.cardX, 10),
      y: parseInt(resizingCard.dataset.cardY, 10),
      w: newW,
      h: newH,
    });
  }

  function onResizePointerUp() {
    resizingCard = null;
    resizeStart = null;
    window.removeEventListener('pointermove', onResizePointerMove);
    window.removeEventListener('pointerup', onResizePointerUp);
  }

  // ------------------------------------------------------------------
  // Keyboard a11y
  // ------------------------------------------------------------------

  function onKeyDown(event) {
    if (!editMode) return;
    const card = event.target.closest('[data-dashboard-card]');
    if (!card) return;
    const isArrow = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].indexOf(event.key) !== -1;
    if (!isArrow) return;
    event.preventDefault();
    const x = parseInt(card.dataset.cardX, 10);
    const y = parseInt(card.dataset.cardY, 10);
    const w = parseInt(card.dataset.cardW, 10);
    const h = parseInt(card.dataset.cardH, 10);
    let nextX = x;
    let nextY = y;
    let nextW = w;
    let nextH = h;
    const delta = event.shiftKey ? 'resize' : 'move';
    if (delta === 'move') {
      if (event.key === 'ArrowUp') nextY = y - 1;
      if (event.key === 'ArrowDown') nextY = y + 1;
      if (event.key === 'ArrowLeft') nextX = x - 1;
      if (event.key === 'ArrowRight') nextX = x + 1;
    } else {
      if (event.key === 'ArrowUp') nextH = h - 1;
      if (event.key === 'ArrowDown') nextH = h + 1;
      if (event.key === 'ArrowLeft') nextW = w - 1;
      if (event.key === 'ArrowRight') nextW = w + 1;
    }
    setCardPosition(card, { x: nextX, y: nextY, w: nextW, h: nextH });
  }

  // ------------------------------------------------------------------
  // Remove
  // ------------------------------------------------------------------

  function onRemove(event) {
    const button = event.target.closest('[data-dashboard-card-remove]');
    if (!button) return;
    if (!editMode) return;
    const card = button.closest('[data-dashboard-card]');
    if (!card) return;
    card.remove();
    schedulePersist();
  }

  // ------------------------------------------------------------------
  // Add card modal
  // ------------------------------------------------------------------

  function openModal() {
    if (!modal) return;
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    if (search) {
      search.value = '';
      filterCatalogue('');
      search.focus();
    }
  }

  function closeModal() {
    if (!modal) return;
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
  }

  function filterCatalogue(query) {
    if (!catalogueList) return;
    const q = (query || '').trim().toLowerCase();
    catalogueList.querySelectorAll('.dashboard-catalogue__item').forEach((item) => {
      const title = (item.dataset.catalogueTitle || '').toLowerCase();
      const category = (item.dataset.catalogueCategory || '').toLowerCase();
      const visible = !q || title.indexOf(q) !== -1 || category.indexOf(q) !== -1;
      item.style.display = visible ? '' : 'none';
    });
  }

  function findFreeSpot(width, height) {
    const layout = readLayoutFromDom();
    const occupied = {};
    layout.forEach((entry) => {
      for (let dx = 0; dx < entry.w; dx++) {
        for (let dy = 0; dy < entry.h; dy++) {
          occupied[(entry.x + dx) + ':' + (entry.y + dy)] = true;
        }
      }
    });
    for (let y = 0; y < 200; y++) {
      for (let x = 0; x <= gridColumns - width; x++) {
        let collision = false;
        for (let dx = 0; dx < width && !collision; dx++) {
          for (let dy = 0; dy < height && !collision; dy++) {
            if (occupied[(x + dx) + ':' + (y + dy)]) collision = true;
          }
        }
        if (!collision) return { x: x, y: y };
      }
    }
    return { x: 0, y: 0 };
  }

  async function addCard(cardId, descriptor) {
    let payload = {};
    let resolvedDescriptor = descriptor;
    try {
      const response = await fetch(cardEndpoint + '/' + encodeURIComponent(cardId), {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (response.ok) {
        const body = await response.json();
        payload = body.payload || {};
        resolvedDescriptor = body.descriptor || descriptor;
      }
    } catch (err) {
      // proceed with placeholder
    }
    if (!resolvedDescriptor) return;
    const width = resolvedDescriptor.default_width || 4;
    const height = resolvedDescriptor.default_height || 2;
    const spot = findFreeSpot(width, height);
    const card = document.createElement('article');
    card.className = 'card card--dashboard dashboard-card';
    card.setAttribute('role', 'listitem');
    card.dataset.dashboardCard = cardId;
    card.dataset.cardX = String(spot.x);
    card.dataset.cardY = String(spot.y);
    card.dataset.cardW = String(width);
    card.dataset.cardH = String(height);
    card.dataset.cardRefresh = String(resolvedDescriptor.refresh_interval_seconds || 0);
    card.innerHTML = (
      '<header class="dashboard-card__header">' +
        '<div class="dashboard-card__header-text">' +
          '<h2 class="dashboard-card__title"></h2>' +
          '<span class="dashboard-card__category"></span>' +
        '</div>' +
        '<div class="dashboard-card__controls" data-dashboard-card-controls>' +
          '<button type="button" class="dashboard-card__handle" data-dashboard-card-handle aria-label="Move card" hidden tabindex="-1">⠿</button>' +
          '<button type="button" class="dashboard-card__remove" data-dashboard-card-remove aria-label="Remove card" hidden tabindex="-1">×</button>' +
        '</div>' +
      '</header>' +
      '<div class="dashboard-card__body"><p class="dashboard-card__empty">Loaded — refresh the page to render.</p></div>' +
      '<button type="button" class="dashboard-card__resize" data-dashboard-card-resize aria-label="Resize card" hidden tabindex="-1">⇲</button>'
    );
    card.querySelector('.dashboard-card__title').textContent = resolvedDescriptor.title || cardId;
    card.querySelector('.dashboard-card__category').textContent = resolvedDescriptor.category || '';
    grid.appendChild(card);
    applyPosition(card);
    setEditMode(editMode);  // Refresh draggable/tabindex on the new card.
    schedulePersist();
    closeModal();
  }

  // ------------------------------------------------------------------
  // Wire up events
  // ------------------------------------------------------------------

  if (editToggle) {
    editToggle.addEventListener('click', () => {
      setEditMode(!editMode);
      closeOptionsDropdown();
    });
  }

  if (addButton) {
    addButton.addEventListener('click', () => {
      closeOptionsDropdown();
      openModal();
    });
  }

  if (resetButton) {
    resetButton.addEventListener('click', async () => {
      closeOptionsDropdown();
      if (!window.confirm('Reset your dashboard to the default layout?')) return;
      try {
        await fetch(resetEndpoint, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-CSRF-Token': getCsrfToken() },
        });
      } catch (err) {
        // ignore
      }
      window.location.reload();
    });
  }

  modalCloseButtons.forEach((button) => button.addEventListener('click', closeModal));
  if (modal) {
    modal.addEventListener('click', (event) => {
      if (event.target === modal) closeModal();
    });
  }
  if (search) {
    search.addEventListener('input', (event) => filterCatalogue(event.target.value));
  }
  if (catalogueList) {
    catalogueList.addEventListener('click', (event) => {
      const button = event.target.closest('[data-dashboard-add-card]');
      if (!button) return;
      const cardId = button.dataset.dashboardAddCard;
      const item = button.closest('.dashboard-catalogue__item');
      const descriptor = {
        title: item ? item.dataset.catalogueTitle : cardId,
        category: item ? item.dataset.catalogueCategory : '',
      };
      addCard(cardId, descriptor);
    });
  }

  grid.addEventListener('dragstart', onDragStart);
  grid.addEventListener('dragend', onDragEnd);
  grid.addEventListener('dragover', onDragOver);
  grid.addEventListener('drop', onDrop);
  grid.addEventListener('pointerdown', onResizePointerDown);
  grid.addEventListener('keydown', onKeyDown);
  grid.addEventListener('click', onRemove);

  // Set initial state.
  setEditMode(false);
})();
