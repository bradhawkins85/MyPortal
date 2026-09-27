(function () {
  const root = document.getElementById('profile-root');
  if (!root) {
    return;
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : null;
  }

  function formatErrorDetail(data, fallback) {
    let detail = fallback;
    if (data && data.detail) {
      detail = Array.isArray(data.detail)
        ? data.detail.map((entry) => entry.msg || entry).join(', ')
        : data.detail;
    }
    const reference = data && (data.error_reference || data.request_id);
    if (reference) {
      return `${detail} (Reference: ${reference})`;
    }
    return detail;
  }

  async function requestJson(url, options = {}) {
    const csrf = getCsrfToken();
    const headers = new Headers(options.headers || {});
    if (!headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    if (csrf && !headers.has('X-CSRF-Token')) {
      headers.set('X-CSRF-Token', csrf);
    }
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers,
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        detail = formatErrorDetail(data, detail);
      } catch (error) {
        /* ignore parse errors */
      }
      throw new Error(detail);
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  function showMessage(config, message) {
    if (!message) {
      return;
    }
    const variant = config && config.variant ? config.variant : 'info';
    if (window.__portalToast && typeof window.__portalToast.show === 'function') {
      window.__portalToast.show(message, { variant });
      return;
    }
    window.alert(message);
  }

  function clearMessages(_elements) {
    // Intentionally no-op: status messages are surfaced as toast notifications.
  }

  const userId = root.dataset.userId;
  const clickToCallForm = document.getElementById('click-to-call-form');
  if (clickToCallForm) {
    const enabledInput = clickToCallForm.querySelector('#click-to-call-enabled');
    const phoneIpInput = clickToCallForm.querySelector('#click-to-call-phone-ip');
    const usernameInput = clickToCallForm.querySelector('#click-to-call-username');
    const passwordInput = clickToCallForm.querySelector('#click-to-call-password');
    requestJson('/api/click-to-call/settings').then((settings) => {
      enabledInput.checked = Boolean(settings.enabled);
      phoneIpInput.value = settings.phone_ip || '';
      usernameInput.value = settings.login_username || '';
    }).catch(() => showMessage({ variant: 'error' }, 'Unable to load click-to-call settings.'));

    clickToCallForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        await requestJson('/api/click-to-call/settings', {
          method: 'PUT',
          body: JSON.stringify({
            enabled: enabledInput.checked,
            phone_ip: phoneIpInput.value.trim() || null,
            login_username: usernameInput.value.trim() || null,
            password: passwordInput.value || null,
          }),
        });
        passwordInput.value = '';
        showMessage({ variant: 'success' }, 'Click-to-call settings saved.');
      } catch (error) {
        showMessage({ variant: 'error' }, error.message || 'Unable to save click-to-call settings.');
      }
    });
  }
  let totpDevices = [];
  try {
    const parsed = JSON.parse(root.dataset.totpDevices || '[]');
    if (Array.isArray(parsed)) {
      totpDevices = parsed.map((item) => ({
        id: item.id,
        name: item.name || 'Authenticator',
      }));
    }
  } catch (error) {
    totpDevices = [];
  }
  let passkeys = [];
  try {
    const parsed = JSON.parse(root.dataset.passkeys || '[]');
    if (Array.isArray(parsed)) {
      passkeys = parsed.map((item) => ({
        id: item.id,
        name: item.name || 'Passkey',
        created_at: item.created_at || null,
        last_used_at: item.last_used_at || null,
        transports: Array.isArray(item.transports) ? item.transports : [],
        credential_device_type: item.credential_device_type || null,
        credential_backed_up: Boolean(item.credential_backed_up),
      }));
    }
  } catch (error) {
    passkeys = [];
  }

  const passwordForm = document.getElementById('password-form');
  const passwordSuccess = { variant: 'success' };
  const passwordError = { variant: 'error' };

  if (passwordForm) {
    passwordForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([passwordSuccess, passwordError]);

      const current = passwordForm.querySelector('#current-password');
      const nextPassword = passwordForm.querySelector('#new-password');
      const confirmPassword = passwordForm.querySelector('#confirm-password');

      const currentValue = current ? current.value : '';
      const newValue = nextPassword ? nextPassword.value : '';
      const confirmValue = confirmPassword ? confirmPassword.value : '';

      if (newValue !== confirmValue) {
        showMessage(passwordError, 'New passwords do not match.');
        return;
      }

      try {
        await requestJson('/auth/password/change', {
          method: 'POST',
          body: JSON.stringify({
            current_password: currentValue,
            new_password: newValue,
          }),
        });
        showMessage(passwordSuccess, 'Password updated successfully.');
        passwordForm.reset();
      } catch (error) {
        showMessage(passwordError, error.message || 'Unable to update password.');
      }
    });
  }

  const mobileForm = document.getElementById('mobile-form');
  const mobileSuccess = { variant: 'success' };
  const mobileError = { variant: 'error' };

  if (mobileForm && userId) {
    mobileForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([mobileSuccess, mobileError]);

      const input = mobileForm.querySelector('#mobile-number');
      const value = input ? input.value.trim() : '';

      try {
        await requestJson(`/api/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ mobile_phone: value || null }),
        });
        showMessage(mobileSuccess, 'Mobile number saved.');
      } catch (error) {
        showMessage(mobileError, error.message || 'Unable to save mobile number.');
      }
    });
  }

  const bookingLinkForm = document.getElementById('booking-link-form');
  const bookingSuccess = { variant: 'success' };
  const bookingError = { variant: 'error' };

  if (bookingLinkForm && userId) {
    bookingLinkForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([bookingSuccess, bookingError]);

      const input = bookingLinkForm.querySelector('#booking-link-url');
      const value = input ? input.value.trim() : '';

      try {
        await requestJson(`/api/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ booking_link_url: value || null }),
        });
        showMessage(bookingSuccess, 'Booking link saved.');
      } catch (error) {
        showMessage(bookingError, error.message || 'Unable to save booking link.');
      }
    });
  }

  const emailSignatureForm = document.getElementById('email-signature-form');
  const signatureSuccess = { variant: 'success' };
  const signatureError = { variant: 'error' };

  if (emailSignatureForm && userId) {
    emailSignatureForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([signatureSuccess, signatureError]);

      const input = emailSignatureForm.querySelector('#email-signature-value');
      const value = input ? input.value.trim() : '';

      // Validate signature length (max 50KB)
      if (value.length > 51200) {
        showMessage(signatureError, 'Email signature is too large. Please keep it under 50KB.');
        return;
      }

      try {
        await requestJson(`/api/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ email_signature: value || null }),
        });
        showMessage(signatureSuccess, 'Email signature saved.');
      } catch (error) {
        showMessage(signatureError, error.message || 'Unable to save email signature.');
      }
    });
  }

  const matrixUsernameForm = document.getElementById('matrix-username-form');
  const matrixUsernameSuccess = { variant: 'success' };
  const matrixUsernameError = { variant: 'error' };

  if (matrixUsernameForm && userId) {
    matrixUsernameForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([matrixUsernameSuccess, matrixUsernameError]);

      const input = matrixUsernameForm.querySelector('#matrix-user-id');
      const value = input ? input.value.trim() : '';

      try {
        await requestJson(`/api/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ matrix_user_id: value || null }),
        });
        showMessage(matrixUsernameSuccess, 'Matrix username saved.');
      } catch (error) {
        showMessage(matrixUsernameError, error.message || 'Unable to save Matrix username.');
      }
    });
  }

  const sidebarSection = root.querySelector('[data-sidebar-customisation]');
  const sidebarList = root.querySelector('[data-sidebar-items]');
  const sidebarSaveButton = root.querySelector('[data-sidebar-save]');
  const sidebarResetButton = root.querySelector('[data-sidebar-reset]');
  const sidebarAddDividerButton = root.querySelector('[data-sidebar-add-divider]');
  const sidebarAddSpacerButton = root.querySelector('[data-sidebar-add-spacer]');
  const sidebarAddGroupButton = root.querySelector('[data-sidebar-add-group]');
  const sidebarStatus = root.querySelector('[data-sidebar-status]');
  const sidebarExpandAllButton = root.querySelector('[data-sidebar-expand-all]');
  const sidebarSuccess = { variant: 'success' };
  const sidebarError = { variant: 'error' };
  const SIDEBAR_DIVIDER_KEY_PREFIX = '__divider__:';
  const SIDEBAR_SPACER_KEY_PREFIX = '__spacer__:';
  const SIDEBAR_GROUP_KEY_PREFIX = '__group__:';
  const SIDEBAR_GROUP_ICONS = [
    'folder', 'support', 'shop', 'server', 'briefcase', 'shield', 'chart',
    'settings', 'automation', 'phone', 'history', 'grid', 'star', 'users',
  ];
  const SIDEBAR_GROUP_ICON_NAMES = {
    folder: 'Folder',
    support: 'Support',
    shop: 'Shop',
    server: 'Server',
    briefcase: 'Briefcase',
    shield: 'Shield',
    chart: 'Chart',
    settings: 'Settings',
    automation: 'Automation',
    phone: 'Phone',
    history: 'History',
    grid: 'Grid',
    star: 'Star',
    users: 'People',
  };
  const SIDEBAR_PROTECTED_KEYS = new Set(['/admin/profile']);
  const SVG_NS_ATTRS = 'viewBox="0 0 24 24" aria-hidden="true" focusable="false"';
  const EDITOR_GLYPHS = {
    handle: '<circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/>',
    up: '<path d="m12 8-6 6 1.4 1.4 4.6-4.6 4.6 4.6L18 14z"/>',
    down: '<path d="m12 16 6-6-1.4-1.4-4.6 4.6-4.6-4.6L6 10z"/>',
    chevron: '<path d="M7 10l5 5 5-5z"/>',
    trash: '<path d="M9 3h6l1 2h4v2H4V5h4zm-3 6h12l-1 12H7zm4 2v8h2v-8zm4 0v8h2v-8z"/>',
    link: '<path d="M10.6 13.4a1 1 0 0 1 0-1.4l3.5-3.5a3 3 0 1 1 4.2 4.2l-2 2-1.4-1.4 2-2a1 1 0 1 0-1.4-1.4l-3.5 3.5a1 1 0 0 1-1.4 0zm2.8-2.8a1 1 0 0 1 0 1.4l-3.5 3.5a3 3 0 1 1-4.2-4.2l2-2 1.4 1.4-2 2a1 1 0 1 0 1.4 1.4l3.5-3.5a1 1 0 0 1 1.4 0z"/>',
  };
  const glyph = (name) => `<svg ${SVG_NS_ATTRS}>${EDITOR_GLYPHS[name]}</svg>`;

  // Editor state is a tree: top-level entries, some of which are groups
  // holding item entries. "ghost" entries and "dormant" groups are links or
  // default groups this user cannot currently see; they are never rendered
  // but are written back so a later permission change keeps its place.
  let sidebarEntries = [];
  let preservedHidden = [];
  let sidebarDirty = false;
  let dragged = null;
  // Groups start collapsed so the whole menu fits on screen; the ones a user
  // opens stay open across saves and re-renders.
  const expandedGroupKeys = new Set();

  function setGroupCollapsed(group, collapsed) {
    group.collapsed = collapsed;
    if (collapsed) expandedGroupKeys.delete(group.key);
    else expandedGroupKeys.add(group.key);
  }

  const isGroupKey = (key) => key.startsWith(SIDEBAR_GROUP_KEY_PREFIX);
  const isSeparatorKey = (key) =>
    key.startsWith(SIDEBAR_DIVIDER_KEY_PREFIX) || key.startsWith(SIDEBAR_SPACER_KEY_PREFIX);
  const isRendered = (entry) => entry.type !== 'ghost' && !entry.dormant;
  const canNest = (entry) => entry.type === 'item' && !SIDEBAR_PROTECTED_KEYS.has(entry.key);

  function setSidebarDirty(dirty) {
    sidebarDirty = dirty;
    if (sidebarStatus) {
      sidebarStatus.textContent = dirty ? 'Unsaved changes' : '';
      sidebarStatus.classList.toggle('menu-editor__status--dirty', dirty);
    }
  }

  function buildSidebarState(preferences, { keepHidden = true } = {}) {
    const menu = window.MyPortalSidebarMenu;
    const hiddenKeys = new Set(keepHidden ? preferences.hidden || [] : []);
    const available = new Map(
      menu.listItems().map((item) => [
        item.key,
        {
          type: 'item',
          key: item.key,
          label: item.label,
          icon: item.icon || '',
          hidden: !SIDEBAR_PROTECTED_KEYS.has(item.key) && hiddenKeys.has(item.key),
        },
      ]),
    );
    const knownKeys = new Set(available.keys());
    const defaultGroupIds = new Set((menu.getDefaults().groups || []).map((group) => group.id));
    const groups = new Map();
    (preferences.groups || []).forEach((group) => {
      const children = [];
      (group.items || []).forEach((key) => {
        if (available.has(key)) {
          children.push(available.get(key));
          available.delete(key);
        } else if (!knownKeys.has(key)) {
          // Keeps the link's slot so it reappears in place if access returns.
          children.push({ type: 'ghost', key });
        }
      });
      groups.set(group.id, {
        type: 'group',
        key: group.id,
        label: group.label,
        icon: group.icon || 'folder',
        children,
        collapsed: !expandedGroupKeys.has(group.id),
        // Role-specific default groups (e.g. Administration for a customer)
        // stay out of the editor when nothing in them applies to this user.
        dormant: !children.some(isRendered) && defaultGroupIds.has(group.id),
      });
    });
    const entries = [];
    (preferences.order || []).forEach((key) => {
      if (groups.has(key)) {
        entries.push(groups.get(key));
        groups.delete(key);
      } else if (available.has(key)) {
        entries.push(available.get(key));
        available.delete(key);
      } else if (isSeparatorKey(key)) {
        entries.push({
          type: 'separator',
          key,
          label: key.startsWith(SIDEBAR_DIVIDER_KEY_PREFIX) ? 'Divider' : 'Spacer',
        });
      } else if (!knownKeys.has(key) && !isGroupKey(key)) {
        entries.push({ type: 'ghost', key });
      }
    });
    groups.forEach((group) => entries.push(group));
    available.forEach((item) => entries.push(item));
    sidebarEntries = entries;
    preservedHidden = keepHidden
      ? (preferences.hidden || []).filter((key) => !knownKeys.has(key))
      : [];
  }

  function locateEntry(entry) {
    const topIndex = sidebarEntries.indexOf(entry);
    if (topIndex !== -1) {
      return { list: sidebarEntries, index: topIndex, parent: null };
    }
    for (const candidate of sidebarEntries) {
      if (candidate.type === 'group') {
        const index = candidate.children.indexOf(entry);
        if (index !== -1) {
          return { list: candidate.children, index, parent: candidate };
        }
      }
    }
    return null;
  }

  function detachEntry(entry) {
    const location = locateEntry(entry);
    if (location) {
      location.list.splice(location.index, 1);
    }
    return location;
  }

  // Arrow moves walk through the tree: an item stepping past a group enters
  // it, and one stepping off either end of a group leaves it.
  function moveEntry(entry, direction) {
    const location = locateEntry(entry);
    if (!location) {
      return;
    }
    const { list, index, parent } = location;
    if (parent) {
      let target = index + direction;
      while (target >= 0 && target < list.length && !isRendered(list[target])) {
        target += direction;
      }
      if (target >= 0 && target < list.length) {
        list.splice(index, 1);
        list.splice(target, 0, entry);
      } else {
        list.splice(index, 1);
        const parentIndex = sidebarEntries.indexOf(parent);
        sidebarEntries.splice(direction < 0 ? parentIndex : parentIndex + 1, 0, entry);
      }
    } else {
      let neighbourIndex = index + direction;
      while (neighbourIndex >= 0 && neighbourIndex < list.length && !isRendered(list[neighbourIndex])) {
        neighbourIndex += direction;
      }
      if (neighbourIndex < 0 || neighbourIndex >= list.length) {
        return;
      }
      const neighbour = list[neighbourIndex];
      list.splice(index, 1);
      if (neighbour.type === 'group' && canNest(entry)) {
        setGroupCollapsed(neighbour, false);
        if (direction < 0) neighbour.children.push(entry);
        else neighbour.children.unshift(entry);
      } else {
        const neighbourNow = list.indexOf(neighbour);
        list.splice(direction < 0 ? neighbourNow : neighbourNow + 1, 0, entry);
      }
    }
    setSidebarDirty(true);
    renderSidebarItems({ focusKey: entry.key, focusControl: direction < 0 ? 'up' : 'down' });
  }

  function moveEntryToGroup(entry, groupKey) {
    detachEntry(entry);
    const group = sidebarEntries.find((candidate) => candidate.key === groupKey);
    if (group) {
      setGroupCollapsed(group, false);
      group.children.push(entry);
    } else {
      sidebarEntries.push(entry);
    }
    setSidebarDirty(true);
    renderSidebarItems({ focusKey: entry.key, focusControl: 'move' });
  }

  function makeIconButton(glyphName, label, onClick, extraClass = '') {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `menu-editor__icon-button ${extraClass}`.trim();
    button.innerHTML = glyph(glyphName);
    button.setAttribute('aria-label', label);
    button.title = label;
    button.addEventListener('click', onClick);
    return button;
  }

  function makeHandle(entry, row, labelText) {
    const handle = document.createElement('span');
    handle.className = 'menu-editor__handle sidebar-drag-handle';
    handle.innerHTML = glyph('handle');
    handle.title = `Drag to move ${labelText}`;
    // Only the handle starts a drag so text inputs inside rows stay usable.
    handle.addEventListener('pointerdown', () => { row.draggable = true; });
    handle.addEventListener('pointerup', () => { row.draggable = false; });
    row.addEventListener('dragstart', (event) => {
      if (!row.draggable || event.target !== row) {
        return;
      }
      dragged = entry;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', entry.key);
      setTimeout(() => row.classList.add('menu-editor__row--dragging'), 0);
    });
    row.addEventListener('dragend', () => {
      row.draggable = false;
      dragged = null;
      clearDropIndicators();
      row.classList.remove('menu-editor__row--dragging');
    });
    return handle;
  }

  function makeMoveControls(entry) {
    const wrapper = document.createElement('div');
    wrapper.className = 'menu-editor__arrows';
    const up = makeIconButton('up', `Move ${entry.label} up`, () => moveEntry(entry, -1));
    up.dataset.control = 'up';
    const down = makeIconButton('down', `Move ${entry.label} down`, () => moveEntry(entry, 1));
    down.dataset.control = 'down';
    wrapper.append(up, down);
    return wrapper;
  }

  function clearDropIndicators() {
    sidebarList?.querySelectorAll('.menu-editor__drop-before, .menu-editor__drop-after, .menu-editor__drop-into')
      .forEach((element) => element.classList.remove(
        'menu-editor__drop-before', 'menu-editor__drop-after', 'menu-editor__drop-into',
      ));
  }

  // Works out where the dragged entry would land relative to a target row.
  function resolveDrop(target, element, clientY, { intoGroup = false } = {}) {
    if (!dragged || dragged === target) {
      return null;
    }
    const rect = element.getBoundingClientRect();
    const offset = clientY - rect.top;
    if (intoGroup && canNest(dragged)) {
      if (offset > rect.height * 0.25 && offset < rect.height * 0.75) {
        return { mode: 'into', target };
      }
    }
    const mode = offset < rect.height / 2 ? 'before' : 'after';
    const location = locateEntry(target);
    if (location?.parent && !canNest(dragged)) {
      // Groups, separators and My Profile cannot live inside a group.
      return location.parent === dragged ? null : { mode, target: location.parent };
    }
    return { mode, target };
  }

  function bindDropTarget(element, entry, options = {}) {
    element.addEventListener('dragover', (event) => {
      const drop = resolveDrop(entry, element, event.clientY, options);
      if (!drop) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = 'move';
      clearDropIndicators();
      const indicatorElement = drop.target === entry
        ? element
        : sidebarList.querySelector(`[data-entry-key="${CSS.escape(drop.target.key)}"]`);
      indicatorElement?.classList.add(`menu-editor__drop-${drop.mode}`);
    });
    element.addEventListener('drop', (event) => {
      const drop = resolveDrop(entry, element, event.clientY, options);
      clearDropIndicators();
      if (!drop) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      const moving = dragged;
      if (drop.target === moving || (moving.type === 'group' && locateEntry(drop.target)?.parent === moving)) {
        return;
      }
      detachEntry(moving);
      if (drop.mode === 'into') {
        setGroupCollapsed(drop.target, false);
        drop.target.children.push(moving);
      } else {
        const location = locateEntry(drop.target);
        location.list.splice(drop.mode === 'before' ? location.index : location.index + 1, 0, moving);
      }
      setSidebarDirty(true);
      renderSidebarItems();
    });
  }

  function renderItemRow(entry, parent) {
    const row = document.createElement('li');
    row.className = 'menu-editor__row menu-editor__row--item';
    row.dataset.entryKey = entry.key;
    const isProtected = SIDEBAR_PROTECTED_KEYS.has(entry.key);
    row.classList.toggle('menu-editor__row--hidden', Boolean(entry.hidden));

    const icon = document.createElement('span');
    icon.className = 'menu-editor__item-icon';
    icon.innerHTML = entry.icon || glyph('link');

    const label = document.createElement('span');
    label.className = 'menu-editor__label';
    label.textContent = entry.label;
    if (isProtected) {
      const tag = document.createElement('span');
      tag.className = 'menu-editor__tag';
      tag.textContent = 'Always shown';
      label.appendChild(tag);
    } else if (entry.hidden) {
      const tag = document.createElement('span');
      tag.className = 'menu-editor__tag menu-editor__tag--muted';
      tag.textContent = 'Hidden';
      label.appendChild(tag);
    }

    const controls = document.createElement('div');
    controls.className = 'menu-editor__controls';

    const moveSelect = document.createElement('select');
    moveSelect.className = 'menu-editor__move';
    moveSelect.dataset.control = 'move';
    moveSelect.setAttribute('aria-label', `Move ${entry.label} to`);
    moveSelect.title = 'Move to group';
    const topOption = new Option('Top level', '');
    moveSelect.appendChild(topOption);
    sidebarEntries.filter((candidate) => candidate.type === 'group' && !candidate.dormant).forEach((group) => {
      moveSelect.appendChild(new Option(group.label || 'Untitled group', group.key));
    });
    moveSelect.value = parent ? parent.key : '';
    moveSelect.disabled = isProtected;
    moveSelect.addEventListener('change', () => moveEntryToGroup(entry, moveSelect.value));

    const toggle = document.createElement('label');
    toggle.className = 'menu-editor__switch';
    toggle.title = isProtected ? 'My Profile is always shown' : 'Show in menu';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = !entry.hidden;
    checkbox.disabled = isProtected;
    checkbox.dataset.control = 'visible';
    checkbox.setAttribute('aria-label', `Show ${entry.label} in the menu`);
    checkbox.addEventListener('change', () => {
      entry.hidden = !checkbox.checked;
      setSidebarDirty(true);
      renderSidebarItems({ focusKey: entry.key, focusControl: 'visible' });
    });
    const track = document.createElement('span');
    track.className = 'menu-editor__switch-track';
    track.setAttribute('aria-hidden', 'true');
    toggle.append(checkbox, track);

    controls.append(moveSelect, makeMoveControls(entry), toggle);
    row.append(makeHandle(entry, row, entry.label), icon, label, controls);
    bindDropTarget(row, entry);
    return row;
  }

  function renderSeparatorRow(entry) {
    const row = document.createElement('li');
    row.className = 'menu-editor__row menu-editor__row--separator';
    row.dataset.entryKey = entry.key;
    const line = document.createElement('span');
    line.className = entry.key.startsWith(SIDEBAR_DIVIDER_KEY_PREFIX)
      ? 'menu-editor__separator menu-editor__separator--divider'
      : 'menu-editor__separator menu-editor__separator--spacer';
    const label = document.createElement('span');
    label.className = 'menu-editor__separator-label';
    label.textContent = entry.label;
    line.appendChild(label);
    const controls = document.createElement('div');
    controls.className = 'menu-editor__controls';
    controls.append(
      makeMoveControls(entry),
      makeIconButton('trash', `Remove ${entry.label.toLowerCase()}`, () => {
        detachEntry(entry);
        setSidebarDirty(true);
        renderSidebarItems();
      }, 'menu-editor__icon-button--danger'),
    );
    row.append(makeHandle(entry, row, entry.label.toLowerCase()), line, controls);
    bindDropTarget(row, entry);
    return row;
  }

  function renderGroupIconPicker(group) {
    const icons = window.MyPortalSidebarMenu.groupIcons();
    const picker = document.createElement('details');
    picker.className = 'menu-editor__icon-picker';
    const summary = document.createElement('summary');
    summary.className = 'menu-editor__group-icon';
    summary.setAttribute('aria-label', `Change icon for ${group.label || 'group'}`);
    summary.title = 'Change icon';
    summary.innerHTML = `<svg ${SVG_NS_ATTRS}>${icons[group.icon] || icons.folder}</svg>`;
    const grid = document.createElement('div');
    grid.className = 'menu-editor__icon-grid';
    grid.setAttribute('role', 'listbox');
    grid.setAttribute('aria-label', 'Group icon');
    SIDEBAR_GROUP_ICONS.forEach((name) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'menu-editor__icon-option';
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(name === group.icon));
      option.setAttribute('aria-label', SIDEBAR_GROUP_ICON_NAMES[name] || name);
      option.title = SIDEBAR_GROUP_ICON_NAMES[name] || name;
      option.innerHTML = `<svg ${SVG_NS_ATTRS}>${icons[name] || icons.folder}</svg>`;
      option.addEventListener('click', () => {
        group.icon = name;
        setSidebarDirty(true);
        renderSidebarItems({ focusKey: group.key, focusControl: 'icon' });
      });
      grid.appendChild(option);
    });
    picker.append(summary, grid);
    return picker;
  }

  function renderGroup(group) {
    const wrapper = document.createElement('li');
    wrapper.className = 'menu-editor__group';
    wrapper.dataset.entryKey = group.key;
    wrapper.classList.toggle('menu-editor__group--collapsed', group.collapsed);

    const header = document.createElement('div');
    header.className = 'menu-editor__row menu-editor__row--group';

    const collapse = makeIconButton(
      'chevron',
      `${group.collapsed ? 'Expand' : 'Collapse'} ${group.label || 'group'}`,
      () => {
        setGroupCollapsed(group, !group.collapsed);
        renderSidebarItems({ focusKey: group.key, focusControl: 'collapse' });
      },
      'menu-editor__collapse',
    );
    collapse.dataset.control = 'collapse';
    collapse.setAttribute('aria-expanded', String(!group.collapsed));

    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.className = 'menu-editor__group-name';
    nameInput.maxLength = 60;
    nameInput.value = group.label;
    nameInput.placeholder = 'Group name';
    nameInput.dataset.control = 'name';
    nameInput.setAttribute('aria-label', 'Group name');
    nameInput.addEventListener('input', () => {
      group.label = nameInput.value;
      nameInput.classList.toggle('menu-editor__group-name--invalid', !nameInput.value.trim());
      setSidebarDirty(true);
    });
    // Keep the "Move to" menus in step with renamed groups.
    nameInput.addEventListener('change', () => renderSidebarItems());

    const links = group.children.filter(isRendered);
    const hiddenCount = links.filter((child) => child.hidden).length;
    const count = document.createElement('span');
    count.className = 'menu-editor__count';
    count.textContent = links.length
      ? `${links.length} link${links.length === 1 ? '' : 's'}${hiddenCount ? ` · ${hiddenCount} hidden` : ''}`
      : 'Empty';

    const controls = document.createElement('div');
    controls.className = 'menu-editor__controls';
    controls.append(
      makeMoveControls(group),
      makeIconButton('trash', `Remove group ${group.label || ''}`.trim(), () => {
        // Removing a group keeps its links, lifting them to the top level.
        const location = locateEntry(group);
        sidebarEntries.splice(location.index, 1, ...group.children.filter(isRendered));
        setSidebarDirty(true);
        renderSidebarItems();
      }, 'menu-editor__icon-button--danger'),
    );

    header.append(
      makeHandle(group, wrapper, group.label || 'group'),
      collapse,
      renderGroupIconPicker(group),
      nameInput,
      count,
      controls,
    );
    bindDropTarget(header, group, { intoGroup: true });

    const children = document.createElement('ol');
    children.className = 'menu-editor__children';
    if (!group.collapsed) {
      links.forEach((child) => children.appendChild(renderItemRow(child, group)));
      if (!links.length) {
        const empty = document.createElement('li');
        empty.className = 'menu-editor__empty';
        empty.textContent = 'Drag links here, or use “Move to” on any link.';
        empty.addEventListener('dragover', (event) => {
          if (dragged && canNest(dragged)) {
            event.preventDefault();
            empty.classList.add('menu-editor__drop-into');
          }
        });
        empty.addEventListener('dragleave', () => empty.classList.remove('menu-editor__drop-into'));
        empty.addEventListener('drop', (event) => {
          if (!dragged || !canNest(dragged)) {
            return;
          }
          event.preventDefault();
          detachEntry(dragged);
          group.children.push(dragged);
          setSidebarDirty(true);
          renderSidebarItems();
        });
        children.appendChild(empty);
      }
    }
    wrapper.append(header, children);
    return { wrapper, nameInput };
  }

  function renderSidebarItems({ focusKey = null, focusControl = null } = {}) {
    if (!sidebarList) {
      return;
    }
    if (sidebarExpandAllButton) {
      const anyCollapsed = sidebarEntries.some((entry) => entry.type === 'group' && isRendered(entry) && entry.collapsed);
      sidebarExpandAllButton.textContent = anyCollapsed ? 'Expand all' : 'Collapse all';
      sidebarExpandAllButton.setAttribute('aria-pressed', String(!anyCollapsed));
    }
    const openPickerKey = sidebarList.querySelector('.menu-editor__icon-picker[open]')
      ?.closest('[data-entry-key]')?.dataset.entryKey;
    sidebarList.innerHTML = '';
    let inputToFocus = null;
    sidebarEntries.filter(isRendered).forEach((entry) => {
      if (entry.type === 'group') {
        const { wrapper, nameInput } = renderGroup(entry);
        if (entry.key === focusKey && focusControl === 'name') {
          inputToFocus = nameInput;
        }
        sidebarList.appendChild(wrapper);
      } else if (entry.type === 'separator') {
        sidebarList.appendChild(renderSeparatorRow(entry));
      } else {
        sidebarList.appendChild(renderItemRow(entry, null));
      }
    });
    if (openPickerKey && focusControl !== 'icon') {
      sidebarList.querySelector(`[data-entry-key="${CSS.escape(openPickerKey)}"] .menu-editor__icon-picker`)
        ?.setAttribute('open', '');
    }
    if (inputToFocus) {
      // A newly added group can land outside the visible area. Moving focus
      // to its name both reveals the new row and makes it ready to edit.
      inputToFocus.focus({ preventScroll: true });
      inputToFocus.scrollIntoView({ block: 'nearest' });
      inputToFocus.select();
      return;
    }
    if (focusKey && focusControl) {
      const selector = focusControl === 'icon' ? 'summary' : `[data-control="${focusControl}"]`;
      const row = sidebarList.querySelector(`[data-entry-key="${CSS.escape(focusKey)}"]`);
      const control = row?.querySelector(selector);
      if (control && !control.disabled) {
        control.focus({ preventScroll: true });
        control.scrollIntoView({ block: 'nearest' });
      }
    }
  }

  function buildSidebarPayload() {
    const hidden = new Set(preservedHidden);
    const groups = [];
    sidebarEntries.forEach((entry) => {
      if (entry.type === 'item' && entry.hidden) hidden.add(entry.key);
      if (entry.type === 'group') {
        entry.children.forEach((child) => { if (child.hidden) hidden.add(child.key); });
        groups.push({
          id: entry.key,
          label: (entry.label || '').trim(),
          icon: entry.icon,
          items: entry.children.map((child) => child.key),
        });
      }
    });
    SIDEBAR_PROTECTED_KEYS.forEach((key) => hidden.delete(key));
    return {
      order: sidebarEntries.map((entry) => entry.key),
      hidden: Array.from(hidden),
      groups,
    };
  }

  if (sidebarSection && window.MyPortalSidebarMenu) {
    buildSidebarState(window.MyPortalSidebarMenu.getPreferences());
    renderSidebarItems();

    // The sidebar may first render from cache or the defaults; pick up the
    // server copy when it lands, unless the user has already started editing.
    document.addEventListener('myportal:sidebar-updated', () => {
      if (!sidebarDirty) {
        buildSidebarState(window.MyPortalSidebarMenu.getPreferences());
        renderSidebarItems();
      }
    });

    if (sidebarSaveButton) {
      sidebarSaveButton.addEventListener('click', async () => {
        clearMessages([sidebarSuccess, sidebarError]);
        const payload = buildSidebarPayload();
        if (payload.groups.some((group) => !group.label)) {
          showMessage(sidebarError, 'Every group needs a name.');
          sidebarList?.querySelector('.menu-editor__group-name--invalid, .menu-editor__group-name:placeholder-shown')?.focus();
          return;
        }
        sidebarSaveButton.disabled = true;
        try {
          const saved = await window.MyPortalSidebarMenu.save(payload);
          buildSidebarState(saved);
          renderSidebarItems();
          setSidebarDirty(false);
          if (sidebarStatus) sidebarStatus.textContent = 'Saved';
          showMessage(sidebarSuccess, 'Left menu saved.');
        } catch (error) {
          showMessage(sidebarError, error.message || 'Unable to save left menu preferences.');
        } finally {
          sidebarSaveButton.disabled = false;
        }
      });
    }

    const addSeparator = (prefix, label) => {
      clearMessages([sidebarSuccess, sidebarError]);
      const key = `${prefix}${Date.now()}`;
      sidebarEntries.push({ type: 'separator', key, label });
      setSidebarDirty(true);
      renderSidebarItems({ focusKey: key, focusControl: 'up' });
    };

    sidebarExpandAllButton?.addEventListener('click', () => {
      const groups = sidebarEntries.filter((entry) => entry.type === 'group' && isRendered(entry));
      const expand = groups.some((group) => group.collapsed);
      groups.forEach((group) => setGroupCollapsed(group, !expand));
      renderSidebarItems();
    });

    sidebarAddDividerButton?.addEventListener('click', () => addSeparator(SIDEBAR_DIVIDER_KEY_PREFIX, 'Divider'));
    sidebarAddSpacerButton?.addEventListener('click', () => addSeparator(SIDEBAR_SPACER_KEY_PREFIX, 'Spacer'));

    if (sidebarAddGroupButton) {
      sidebarAddGroupButton.addEventListener('click', () => {
        clearMessages([sidebarSuccess, sidebarError]);
        const groupKey = `${SIDEBAR_GROUP_KEY_PREFIX}${Date.now()}`;
        sidebarEntries.unshift({
          type: 'group',
          key: groupKey,
          label: 'New group',
          icon: 'folder',
          children: [],
          collapsed: false,
          dormant: false,
        });
        expandedGroupKeys.add(groupKey);
        setSidebarDirty(true);
        renderSidebarItems({ focusKey: groupKey, focusControl: 'name' });
      });
    }

    if (sidebarResetButton) {
      sidebarResetButton.addEventListener('click', () => {
        clearMessages([sidebarSuccess, sidebarError]);
        buildSidebarState(window.MyPortalSidebarMenu.getDefaults(), { keepHidden: false });
        setSidebarDirty(true);
        renderSidebarItems();
        showMessage(sidebarSuccess, 'Default layout restored. Save to apply it to your menu.');
      });
    }

    window.addEventListener('beforeunload', (event) => {
      if (sidebarDirty) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
  }

  const totpTable = document.getElementById('totp-table');
  const totpBody = root.querySelector('[data-totp-body]');
  const totpEmptyRow = root.querySelector('[data-totp-empty]');
  const addButton = root.querySelector('[data-totp-add]');
  const setupSection = root.querySelector('[data-totp-setup]');
  const secretInput = document.getElementById('totp-secret');
  const linkInput = document.getElementById('totp-link');
  const verifyForm = document.getElementById('totp-verify-form');
  const verifyName = document.getElementById('totp-name');
  const verifyCode = document.getElementById('totp-code');
  const verifySuccess = { variant: 'success' };
  const verifyError = { variant: 'error' };
  const cancelButton = root.querySelector('[data-totp-cancel]');

  function renderTotpDevices() {
    if (!totpBody) {
      return;
    }
    totpDevices.sort((a, b) => {
      const nameA = (a.name || '').toLowerCase();
      const nameB = (b.name || '').toLowerCase();
      if (nameA < nameB) return -1;
      if (nameA > nameB) return 1;
      return 0;
    });
    totpBody.innerHTML = '';
    if (!totpDevices.length) {
      if (totpEmptyRow) {
        totpEmptyRow.hidden = false;
        totpBody.appendChild(totpEmptyRow);
      }
    } else {
      if (totpEmptyRow) {
        totpEmptyRow.hidden = true;
      }
      totpDevices.forEach((device) => {
        const row = document.createElement('tr');
        row.dataset.deviceId = String(device.id);

        const nameCell = document.createElement('td');
        nameCell.textContent = device.name || 'Authenticator';
        nameCell.setAttribute('data-label', 'Name');
        nameCell.setAttribute('data-value', (device.name || '').toLowerCase());
        row.appendChild(nameCell);

        const actionsCell = document.createElement('td');
        actionsCell.className = 'table__actions';
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'button button--danger button--small';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => handleRemoveTotp(device));
        actionsCell.appendChild(removeButton);
        row.appendChild(actionsCell);

        totpBody.appendChild(row);
      });
    }
    if (totpTable) {
      const event = new CustomEvent('table:rows-updated');
      totpTable.dispatchEvent(event);
    }
  }

  function resetTotpSetup() {
    if (setupSection) {
      setupSection.hidden = true;
    }
    if (secretInput) {
      secretInput.value = '';
    }
    if (linkInput) {
      linkInput.value = '';
    }
    if (verifyName) {
      verifyName.value = '';
    }
    if (verifyCode) {
      verifyCode.value = '';
    }
    clearMessages([verifySuccess, verifyError]);
  }

  async function startTotpSetup() {
    clearMessages([verifySuccess, verifyError]);
    resetTotpSetup();
    try {
      const response = await requestJson('/auth/totp/setup', { method: 'POST' });
      if (secretInput) {
        secretInput.value = response.secret || '';
      }
      if (linkInput) {
        linkInput.value = response.otpauth_url || '';
      }
      if (setupSection) {
        setupSection.hidden = false;
      }
      if (verifyCode) {
        verifyCode.focus();
      }
    } catch (error) {
      alert(`Unable to start authenticator setup: ${error.message}`);
    }
  }

  async function handleRemoveTotp(device) {
    if (!device || !device.id) {
      return;
    }
    const confirmRemoval = window.confirm(`Remove authenticator "${device.name}"?`);
    if (!confirmRemoval) {
      return;
    }
    try {
      await requestJson(`/auth/totp/${device.id}`, { method: 'DELETE' });
      totpDevices = totpDevices.filter((entry) => entry.id !== device.id);
      renderTotpDevices();
    } catch (error) {
      alert(`Unable to remove authenticator: ${error.message}`);
    }
  }

  if (addButton) {
    addButton.addEventListener('click', () => {
      startTotpSetup();
    });
  }

  if (cancelButton) {
    cancelButton.addEventListener('click', () => {
      resetTotpSetup();
    });
  }

  if (verifyForm) {
    verifyForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearMessages([verifySuccess, verifyError]);

      const codeRaw = verifyCode ? verifyCode.value.trim() : '';
      const normalisedCode = codeRaw.replace(/\s+/g, '');
      if (!normalisedCode) {
        showMessage(verifyError, 'Enter the authenticator code.');
        return;
      }
      if (!/^\d+$/.test(normalisedCode)) {
        showMessage(verifyError, 'Authenticator codes must contain digits only.');
        return;
      }

      const nameValue = verifyName ? verifyName.value.trim() : '';
      try {
        const response = await requestJson('/auth/totp/verify', {
          method: 'POST',
          body: JSON.stringify({
            code: normalisedCode,
            name: nameValue || null,
          }),
        });
        totpDevices.push({ id: response.id, name: response.name || 'Authenticator' });
        renderTotpDevices();
        showMessage(verifySuccess, 'Authenticator added successfully.');
        if (verifyCode) {
          verifyCode.value = '';
        }
        if (verifyName) {
          verifyName.value = '';
        }
        if (secretInput) {
          secretInput.value = '';
        }
        if (linkInput) {
          linkInput.value = '';
        }
        if (setupSection) {
          setupSection.hidden = true;
        }
      } catch (error) {
        showMessage(verifyError, error.message || 'Unable to verify authenticator.');
      }
    });
  }

  const passkeyTable = document.getElementById('passkey-table');
  const passkeyBody = root.querySelector('[data-passkey-body]');
  const passkeyEmptyRow = root.querySelector('[data-passkey-empty]');
  const passkeyAddForm = document.getElementById('passkey-add-form');
  const passkeyNameInput = document.getElementById('passkey-name');
  const passkeyPasswordInput = document.getElementById('passkey-current-password');
  const passkeySuccess = { variant: 'success' };
  const passkeyError = { variant: 'error' };
  const passkeyUtils = window.MyPortalPasskeyUtils;

  function formatDateTime(value) {
    if (!value) {
      return 'Never';
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleString();
  }

  function renderPasskeys() {
    if (!passkeyBody) {
      return;
    }
    passkeys.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' }));
    passkeyBody.innerHTML = '';
    if (!passkeys.length) {
      if (passkeyEmptyRow) {
        passkeyEmptyRow.hidden = false;
        passkeyBody.appendChild(passkeyEmptyRow);
      }
    } else {
      if (passkeyEmptyRow) {
        passkeyEmptyRow.hidden = true;
      }
      passkeys.forEach((item) => {
        const row = document.createElement('tr');

        const nameCell = document.createElement('td');
        const transportSuffix = item.transports.length ? ` (${item.transports.join(', ')})` : '';
        nameCell.textContent = `${item.name || 'Passkey'}${transportSuffix}`;
        row.appendChild(nameCell);

        const createdCell = document.createElement('td');
        createdCell.textContent = formatDateTime(item.created_at);
        row.appendChild(createdCell);

        const lastUsedCell = document.createElement('td');
        lastUsedCell.textContent = formatDateTime(item.last_used_at);
        row.appendChild(lastUsedCell);

        const actionsCell = document.createElement('td');
        actionsCell.className = 'table__actions';

        const renameButton = document.createElement('button');
        renameButton.type = 'button';
        renameButton.className = 'button button--ghost button--small';
        renameButton.textContent = 'Rename';
        renameButton.addEventListener('click', () => renamePasskey(item));
        actionsCell.appendChild(renameButton);

        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'button button--danger button--small';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => removePasskey(item));
        actionsCell.appendChild(removeButton);

        row.appendChild(actionsCell);
        passkeyBody.appendChild(row);
      });
    }
    if (passkeyTable) {
      passkeyTable.dispatchEvent(new CustomEvent('table:rows-updated'));
    }
  }

  async function renamePasskey(item) {
    const nextName = window.prompt('Rename passkey', item.name || 'Passkey');
    if (!nextName || !nextName.trim()) {
      return;
    }
    try {
      const updated = await requestJson(`/auth/passkeys/${item.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name: nextName.trim() }),
      });
      passkeys = passkeys.map((entry) => (entry.id === item.id ? updated : entry));
      renderPasskeys();
      showMessage(passkeySuccess, 'Passkey renamed.');
    } catch (error) {
      showMessage(passkeyError, error.message || 'Unable to rename passkey.');
    }
  }

  async function removePasskey(item) {
    const confirmed = window.confirm(`Remove passkey "${item.name}"?`);
    if (!confirmed) {
      return;
    }
    const currentPassword = await promptForPassword(`Enter your current password to remove "${item.name}"`);
    if (!currentPassword) {
      return;
    }
    try {
      await requestJson(`/auth/passkeys/${item.id}`, {
        method: 'DELETE',
        body: JSON.stringify({ current_password: currentPassword }),
      });
      passkeys = passkeys.filter((entry) => entry.id !== item.id);
      renderPasskeys();
      showMessage(passkeySuccess, 'Passkey removed.');
    } catch (error) {
      showMessage(passkeyError, error.message || 'Unable to remove passkey.');
    }
  }

  function promptForPassword(message) {
    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.style.position = 'fixed';
      overlay.style.inset = '0';
      overlay.style.background = 'rgba(15, 23, 42, 0.65)';
      overlay.style.display = 'flex';
      overlay.style.alignItems = 'center';
      overlay.style.justifyContent = 'center';
      overlay.style.padding = '1rem';
      overlay.style.zIndex = '1000';

      const dialog = document.createElement('div');
      dialog.className = 'card card--panel';
      dialog.style.maxWidth = '28rem';
      dialog.style.width = '100%';

      const form = document.createElement('form');
      form.className = 'form';
      const body = document.createElement('div');
      body.className = 'card__body card__body--stacked';
      const text = document.createElement('p');
      text.textContent = message;
      body.appendChild(text);
      const field = document.createElement('div');
      field.className = 'form-field';
      const label = document.createElement('label');
      label.className = 'form-label';
      label.htmlFor = 'passkey-remove-password';
      label.textContent = 'Current password';
      field.appendChild(label);
      const input = document.createElement('input');
      input.className = 'form-input';
      input.id = 'passkey-remove-password';
      input.type = 'password';
      input.autocomplete = 'current-password';
      input.required = true;
      field.appendChild(input);
      body.appendChild(field);
      const actions = document.createElement('div');
      actions.className = 'form-actions';
      const cancelButton = document.createElement('button');
      cancelButton.type = 'button';
      cancelButton.className = 'button button--ghost';
      cancelButton.setAttribute('data-passkey-cancel', 'true');
      cancelButton.textContent = 'Cancel';
      actions.appendChild(cancelButton);
      const submitButton = document.createElement('button');
      submitButton.type = 'submit';
      submitButton.className = 'button';
      submitButton.textContent = 'Continue';
      actions.appendChild(submitButton);
      body.appendChild(actions);
      form.appendChild(body);

      const cleanup = (value) => {
        overlay.remove();
        resolve(value);
      };

      form.addEventListener('submit', (event) => {
        event.preventDefault();
        const passwordInput = form.querySelector('#passkey-remove-password');
        cleanup(passwordInput ? passwordInput.value : '');
      });
      form.querySelector('[data-passkey-cancel]').addEventListener('click', () => cleanup(''));
      overlay.addEventListener('click', (event) => {
        if (event.target === overlay) {
          cleanup('');
        }
      });

      dialog.appendChild(form);
      overlay.appendChild(dialog);
      document.body.appendChild(overlay);
      const focusTarget = form.querySelector('#passkey-remove-password');
      if (focusTarget) {
        focusTarget.focus();
      }
    });
  }

  if (passkeyAddForm) {
    if (!passkeyUtils || !passkeyUtils.supportsPasskeys()) {
      const submitButton = passkeyAddForm.querySelector('[data-passkey-add]');
      if (submitButton) {
        submitButton.disabled = true;
      }
      showMessage(passkeyError, 'Passkeys are not supported in this browser or on this connection.');
    }
    passkeyAddForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!passkeyUtils || !passkeyUtils.supportsPasskeys()) {
        showMessage(passkeyError, 'Passkeys are not supported in this browser or on this connection.');
        return;
      }
      const passkeyName = passkeyNameInput ? passkeyNameInput.value.trim() : '';
      const currentPassword = passkeyPasswordInput ? passkeyPasswordInput.value : '';
      if (!passkeyName) {
        showMessage(passkeyError, 'Enter a name for the passkey.');
        return;
      }
      if (!currentPassword) {
        showMessage(passkeyError, 'Enter your current password to continue.');
        return;
      }
      try {
        const options = await requestJson('/auth/passkeys/register/options', {
          method: 'POST',
          body: JSON.stringify({ current_password: currentPassword }),
        });
        const credential = await navigator.credentials.create({
          publicKey: passkeyUtils.decodeCredentialOptions(options.public_key),
        });
        if (!credential) {
          throw new Error('No passkey was created.');
        }
        const created = await requestJson('/auth/passkeys/register/verify', {
          method: 'POST',
          body: JSON.stringify({
            challenge_id: options.challenge_id,
            name: passkeyName,
            credential: passkeyUtils.serializeCredential(credential),
          }),
        });
        passkeys.push(created);
        renderPasskeys();
        if (passkeyAddForm) {
          passkeyAddForm.reset();
        }
        showMessage(passkeySuccess, 'Passkey registered successfully.');
      } catch (error) {
        showMessage(
          passkeyError,
          passkeyUtils.passkeyErrorMessage(error, error.message || 'Unable to register a passkey.'),
        );
      }
    });
  }

  root.querySelectorAll('[data-copy-target]').forEach((button) => {
    button.addEventListener('click', async () => {
      const targetId = button.getAttribute('data-copy-target');
      if (!targetId) {
        return;
      }
      const target = document.getElementById(targetId);
      if (!target) {
        return;
      }
      try {
        await navigator.clipboard.writeText(target.value || '');
        button.textContent = 'Copied';
        setTimeout(() => {
          button.textContent = 'Copy';
        }, 2000);
      } catch (error) {
        alert('Unable to copy to clipboard.');
      }
    });
  });

  renderTotpDevices();
  renderPasskeys();
})();
