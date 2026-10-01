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

  // ── Modals ─────────────────────────────────────────────────────────────
  // Follows the gold-standard <div class="modal" role="dialog" hidden> pattern:
  // focus moves into the panel, Tab is trapped, and focus returns to the trigger.
  const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([type="hidden"]):not([disabled]):not([readonly]), select:not([disabled]), [tabindex="0"]';
  const modalTriggers = new WeakMap();
  const modalCloseHandlers = new WeakMap();

  function setModalError(modal, message) {
    const error = modal ? modal.querySelector('[data-modal-error]') : null;
    if (!error) {
      if (message) showMessage({ variant: 'error' }, message);
      return;
    }
    error.textContent = message || '';
    error.hidden = !message;
  }

  function openModal(modal, { trigger = null, focus = null, onClose = null } = {}) {
    if (!modal) {
      return;
    }
    modalTriggers.set(modal, trigger || document.activeElement);
    modalCloseHandlers.set(modal, onClose);
    setModalError(modal, '');
    modal.hidden = false;
    modal.classList.add('is-visible');
    document.body.classList.add('scf-modal-open');
    const body = modal.querySelector('.modal__body');
    if (body) {
      body.scrollTop = 0;
    }
    window.requestAnimationFrame(() => {
      const target = (focus && modal.querySelector(focus))
        || Array.from(modal.querySelectorAll(`.modal__body ${FOCUSABLE}`)).find((node) => node.offsetParent !== null)
        || modal.querySelector('.modal__close');
      if (target) target.focus();
    });
  }

  function closeModal(modal) {
    if (!modal || modal.hidden) {
      return;
    }
    modal.hidden = true;
    modal.classList.remove('is-visible');
    if (!document.querySelector('.modal.is-visible:not([hidden])')) {
      document.body.classList.remove('scf-modal-open');
    }
    const onClose = modalCloseHandlers.get(modal);
    modalCloseHandlers.delete(modal);
    if (typeof onClose === 'function') {
      onClose();
    }
    const trigger = modalTriggers.get(modal);
    if (trigger && typeof trigger.focus === 'function' && document.contains(trigger)) {
      trigger.focus();
    }
  }

  root.querySelectorAll('.modal').forEach((modal) => {
    modal.querySelectorAll('[data-modal-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        closeModal(modal);
      });
    });
    modal.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = Array.from(modal.querySelectorAll(FOCUSABLE)).filter((node) => node.offsetParent !== null);
      if (!focusable.length) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  });

  function setBusy(button, busy, busyLabel) {
    if (!button) {
      return;
    }
    if (busy) {
      button.dataset.idleLabel = button.textContent;
      button.disabled = true;
      button.classList.add('button--processing');
      if (busyLabel) button.textContent = busyLabel;
    } else {
      button.disabled = false;
      button.classList.remove('button--processing');
      if (button.dataset.idleLabel) button.textContent = button.dataset.idleLabel;
    }
  }

  // Password confirmation for removals, replacing window.confirm + prompt.
  const confirmModal = document.getElementById('profile-confirm-modal');
  const confirmForm = document.getElementById('profile-confirm-form');

  function confirmWithPassword({ title, message, submitLabel = 'Continue', danger = false, trigger = null, action }) {
    if (!confirmModal || !confirmForm) {
      return;
    }
    const titleNode = confirmModal.querySelector('[data-confirm-title]');
    const messageNode = confirmModal.querySelector('[data-confirm-message]');
    const submit = confirmModal.querySelector('[data-confirm-submit]');
    const input = confirmForm.querySelector('#profile-confirm-password');
    if (titleNode) titleNode.textContent = title;
    if (messageNode) messageNode.textContent = message;
    if (submit) {
      submit.textContent = submitLabel;
      submit.classList.toggle('button--danger', danger);
    }
    if (input) input.value = '';
    confirmForm.onsubmit = async (event) => {
      event.preventDefault();
      const password = input ? input.value : '';
      if (!password) {
        setModalError(confirmModal, 'Enter your current password to continue.');
        if (input) input.focus();
        return;
      }
      setModalError(confirmModal, '');
      setBusy(submit, true);
      try {
        await action(password);
        closeModal(confirmModal);
      } catch (error) {
        setModalError(confirmModal, error.message || 'Something went wrong. Try again.');
      } finally {
        setBusy(submit, false);
        if (submit) submit.textContent = submitLabel;
      }
    };
    openModal(confirmModal, { trigger, focus: '#profile-confirm-password' });
  }

  // ── Tabs ───────────────────────────────────────────────────────────────
  // Every panel is visible without JavaScript; the tab bar only appears once enhanced.
  const tabList = root.querySelector('[data-profile-tabs]');
  const tabs = Array.from(root.querySelectorAll('[data-profile-tab]'));
  const panels = Array.from(root.querySelectorAll('[data-profile-panel]'));

  function showTab(name, { updateHash = false, focusTab = false } = {}) {
    const tab = tabs.find((item) => item.dataset.profileTab === name) || tabs[0];
    if (!tab) {
      return;
    }
    const active = tab.dataset.profileTab;
    tabs.forEach((item) => {
      const selected = item === tab;
      item.classList.toggle('is-active', selected);
      item.setAttribute('aria-selected', selected ? 'true' : 'false');
      item.tabIndex = selected ? 0 : -1;
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.profilePanel !== active;
    });
    if (focusTab) tab.focus();
    if (updateHash && window.history && window.history.replaceState) {
      window.history.replaceState(null, '', `#${active}`);
    }
  }

  if (tabList && tabs.length) {
    tabList.hidden = false;
    root.classList.add('profile--tabbed');
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => showTab(tab.dataset.profileTab, { updateHash: true }));
      tab.addEventListener('keydown', (event) => {
        let next = null;
        if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
        if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
        if (event.key === 'Home') next = tabs[0];
        if (event.key === 'End') next = tabs[tabs.length - 1];
        if (next) {
          event.preventDefault();
          showTab(next.dataset.profileTab, { updateHash: true, focusTab: true });
        }
      });
    });
    showTab((window.location.hash || '').replace('#', '') || 'security');
    window.addEventListener('hashchange', () => showTab((window.location.hash || '').replace('#', '')));
  }

  // ── Saved data ─────────────────────────────────────────────────────────
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
      passkeys = parsed.map(normalisePasskey);
    }
  } catch (error) {
    passkeys = [];
  }

  function normalisePasskey(item) {
    return {
      id: item.id,
      name: item.name || item.display_name || 'Passkey',
      created_at: item.created_at || null,
      last_used_at: item.last_used_at || null,
      transports: Array.isArray(item.transports) ? item.transports : [],
      credential_device_type: item.credential_device_type || null,
      credential_backed_up: Boolean(item.credential_backed_up),
    };
  }

  function updateSecuritySummary() {
    const setStat = (key, value, variant) => {
      const tile = root.querySelector(`[data-profile-stat="${key}"]`);
      if (!tile) return;
      const valueNode = tile.querySelector('.stat-strip__stat-value');
      if (valueNode) valueNode.textContent = String(value);
      if (variant) {
        tile.className = tile.className.replace(/stat-strip__stat--\w+/, `stat-strip__stat--${variant}`);
      }
    };
    setStat('total', totpDevices.length + passkeys.length);
    setStat('totp', totpDevices.length, totpDevices.length ? 'success' : 'warning');
    setStat('passkeys', passkeys.length, passkeys.length ? 'success' : 'neutral');
    const notice = root.querySelector('[data-profile-no-second-factor]');
    if (notice) notice.hidden = Boolean(totpDevices.length || passkeys.length);
  }

  function flashStatus(node, text) {
    if (!node) return;
    node.textContent = text;
    window.clearTimeout(node._profileTimer);
    node._profileTimer = window.setTimeout(() => { node.textContent = ''; }, 4000);
  }

  // ── Click to call ──────────────────────────────────────────────────────
  const clickToCallForm = document.getElementById('click-to-call-form');
  if (clickToCallForm) {
    const enabledInput = clickToCallForm.querySelector('#click-to-call-enabled');
    const phoneIpInput = clickToCallForm.querySelector('#click-to-call-phone-ip');
    const usernameInput = clickToCallForm.querySelector('#click-to-call-username');
    const passwordInput = clickToCallForm.querySelector('#click-to-call-password');
    const submitButton = clickToCallForm.querySelector('button[type="submit"]');
    requestJson('/api/click-to-call/settings').then((settings) => {
      enabledInput.checked = Boolean(settings.enabled);
      phoneIpInput.value = settings.phone_ip || '';
      usernameInput.value = settings.login_username || '';
    }).catch(() => showMessage({ variant: 'error' }, 'Unable to load click-to-call settings.'));

    clickToCallForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      setBusy(submitButton, true, 'Saving…');
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
      } finally {
        setBusy(submitButton, false);
      }
    });
  }

  // ── Password ───────────────────────────────────────────────────────────
  const passwordModal = document.getElementById('profile-password-modal');
  const passwordForm = document.getElementById('password-form');

  document.querySelectorAll('[data-profile-open="profile-password-modal"]').forEach((button) => {
    button.addEventListener('click', () => {
      if (passwordForm) passwordForm.reset();
      openModal(passwordModal, { trigger: button, focus: '#current-password' });
    });
  });

  if (passwordForm) {
    passwordForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const current = passwordForm.querySelector('#current-password');
      const nextPassword = passwordForm.querySelector('#new-password');
      const confirmPassword = passwordForm.querySelector('#confirm-password');
      const submitButton = passwordForm.querySelector('button[type="submit"]');

      const currentValue = current ? current.value : '';
      const newValue = nextPassword ? nextPassword.value : '';
      const confirmValue = confirmPassword ? confirmPassword.value : '';

      let problem = '';
      let focusTarget = null;
      if (!currentValue) {
        problem = 'Enter your current password.';
        focusTarget = current;
      } else if (newValue.length < 12) {
        problem = 'Your new password needs at least 12 characters.';
        focusTarget = nextPassword;
      } else if (newValue === currentValue) {
        problem = 'Choose a new password that is different from your current one.';
        focusTarget = nextPassword;
      } else if (newValue !== confirmValue) {
        problem = 'The new passwords don’t match.';
        focusTarget = confirmPassword;
      }
      if (problem) {
        setModalError(passwordModal, problem);
        if (focusTarget) focusTarget.focus();
        return;
      }

      setModalError(passwordModal, '');
      setBusy(submitButton, true, 'Updating…');
      try {
        await requestJson('/auth/password/change', {
          method: 'POST',
          body: JSON.stringify({
            current_password: currentValue,
            new_password: newValue,
          }),
        });
        passwordForm.reset();
        closeModal(passwordModal);
        showMessage({ variant: 'success' }, 'Password updated.');
      } catch (error) {
        setModalError(passwordModal, error.message || 'Unable to update password.');
      } finally {
        setBusy(submitButton, false);
      }
    });
  }

  // ── Work details ───────────────────────────────────────────────────────
  const contactForm = document.getElementById('profile-contact-form');
  if (contactForm && userId) {
    const contactStatus = contactForm.querySelector('[data-profile-contact-status]');
    const bookingInput = contactForm.querySelector('#booking-link-url');
    const bookingTest = contactForm.querySelector('[data-booking-link-test]');
    const fields = {
      mobile_phone: contactForm.querySelector('#mobile-number'),
      booking_link_url: bookingInput,
      matrix_user_id: contactForm.querySelector('#matrix-user-id'),
    };

    const syncBookingTest = () => {
      if (!bookingTest || !bookingInput) return;
      const value = bookingInput.value.trim();
      const valid = /^https?:\/\/\S+$/i.test(value);
      bookingTest.hidden = !valid;
      bookingTest.href = valid ? value : '#';
    };
    if (bookingInput) {
      bookingInput.addEventListener('input', syncBookingTest);
    }
    contactForm.addEventListener('input', () => {
      if (contactStatus) contactStatus.textContent = 'Unsaved changes';
    });

    contactForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {};
      Object.entries(fields).forEach(([key, input]) => {
        if (input) payload[key] = input.value.trim() || null;
      });
      if (payload.booking_link_url && !/^https?:\/\/\S+$/i.test(payload.booking_link_url)) {
        showMessage({ variant: 'error' }, 'Booking links must start with https:// or http://.');
        bookingInput.focus();
        return;
      }
      if (payload.matrix_user_id && !/^@[^:\s]+:\S+$/.test(payload.matrix_user_id)) {
        showMessage({ variant: 'error' }, 'Matrix user IDs look like @username:server.com.');
        fields.matrix_user_id.focus();
        return;
      }
      const submitButton = contactForm.querySelector('button[type="submit"]');
      setBusy(submitButton, true, 'Saving…');
      try {
        await requestJson(`/api/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
        flashStatus(contactStatus, 'Saved');
        showMessage({ variant: 'success' }, 'Your details were saved.');
      } catch (error) {
        if (contactStatus) contactStatus.textContent = '';
        showMessage({ variant: 'error' }, error.message || 'Unable to save your details.');
      } finally {
        setBusy(submitButton, false);
      }
    });
  }

  const emailSignatureForm = document.getElementById('email-signature-form');
  if (emailSignatureForm && userId) {
    const signatureStatus = emailSignatureForm.querySelector('[data-profile-signature-status]');
    emailSignatureForm.addEventListener('input', () => {
      if (signatureStatus) signatureStatus.textContent = 'Unsaved changes';
    });
    emailSignatureForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const input = emailSignatureForm.querySelector('#email-signature-value');
      const value = input ? input.value.trim() : '';

      // Validate signature length (max 50KB)
      if (value.length > 51200) {
        showMessage({ variant: 'error' }, 'Email signature is too large. Please keep it under 50KB.');
        return;
      }

      const submitButton = emailSignatureForm.querySelector('button[type="submit"]');
      setBusy(submitButton, true, 'Saving…');
      try {
        await requestJson(`/api/users/${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ email_signature: value || null }),
        });
        flashStatus(signatureStatus, 'Saved');
        showMessage({ variant: 'success' }, 'Email signature saved.');
      } catch (error) {
        if (signatureStatus) signatureStatus.textContent = '';
        showMessage({ variant: 'error' }, error.message || 'Unable to save email signature.');
      } finally {
        setBusy(submitButton, false);
      }
    });
  }

  const sidebarSection = root.querySelector('[data-sidebar-customisation]');
  const sidebarList = root.querySelector('[data-sidebar-items]');
  const sidebarSaveButton = root.querySelector('[data-sidebar-save]');
  const navigationStyleInputs = root.querySelectorAll('input[name="navigation-style"]');
  let navigationStyle = 'top';
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
    const menuTab = root.querySelector('[data-profile-tab="menu"]');
    if (menuTab) {
      menuTab.classList.toggle('is-configured', dirty);
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
      navigation_style: navigationStyle,
      order: sidebarEntries.map((entry) => entry.key),
      hidden: Array.from(hidden),
      groups,
    };
  }

  if (sidebarSection && window.MyPortalSidebarMenu) {
    const initialPreferences = window.MyPortalSidebarMenu.getPreferences();
    navigationStyle = initialPreferences.navigation_style === 'sidebar' ? 'sidebar' : 'top';
    navigationStyleInputs.forEach((input) => { input.checked = input.value === navigationStyle; });
    buildSidebarState(initialPreferences);
    renderSidebarItems();

    navigationStyleInputs.forEach((input) => input.addEventListener('change', () => {
      if (!input.checked) return;
      navigationStyle = input.value === 'sidebar' ? 'sidebar' : 'top';
      setSidebarDirty(true);
    }));

    // The sidebar may first render from cache or the defaults; pick up the
    // server copy when it lands, unless the user has already started editing.
    document.addEventListener('myportal:sidebar-updated', () => {
      if (!sidebarDirty) {
        const preferences = window.MyPortalSidebarMenu.getPreferences();
        navigationStyle = preferences.navigation_style === 'sidebar' ? 'sidebar' : 'top';
        navigationStyleInputs.forEach((input) => { input.checked = input.value === navigationStyle; });
        buildSidebarState(preferences);
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
          showMessage(sidebarSuccess, 'Navigation menu saved.');
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
      sidebarResetButton.addEventListener('click', async () => {
        clearMessages([sidebarSuccess, sidebarError]);
        const confirmed = window.confirm(
          'Reset the navigation menu to the default layout? Your position, groups, order and hidden links will be reset.',
        );
        if (!confirmed) {
          return;
        }
        sidebarResetButton.disabled = true;
        try {
          const defaults = await window.MyPortalSidebarMenu.reset();
          navigationStyle = defaults.navigation_style === 'sidebar' ? 'sidebar' : 'top';
          navigationStyleInputs.forEach((input) => { input.checked = input.value === navigationStyle; });
          expandedGroupKeys.clear();
          buildSidebarState(defaults);
          renderSidebarItems();
          setSidebarDirty(false);
          if (sidebarStatus) sidebarStatus.textContent = 'Default layout applied';
          showMessage(sidebarSuccess, 'Navigation menu reset to the default layout.');
        } catch (error) {
          showMessage(sidebarError, error.message || 'Unable to reset the left menu.');
        } finally {
          sidebarResetButton.disabled = false;
        }
      });
    }

    window.addEventListener('beforeunload', (event) => {
      if (sidebarDirty) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
  }

  // ── Credential lists ───────────────────────────────────────────────────
  const ICONS = {
    phone: '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm0 3v13h10V5Zm5 14.25a1 1 0 1 0 0 1.5 1 1 0 0 0 0-1.5Z"/></svg>',
    key: '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M14 3a7 7 0 1 1-2.1 13.68L10 18.6V21H7v-2H5v-2l4.32-4.32A7 7 0 0 1 14 3Zm2 3.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z"/></svg>',
  };
  const TRANSPORT_LABELS = {
    internal: 'This device',
    hybrid: 'Phone',
    usb: 'USB key',
    nfc: 'NFC',
    ble: 'Bluetooth',
    'smart-card': 'Smart card',
  };

  function formatDate(value) {
    if (!value) {
      return null;
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return null;
    }
    return parsed;
  }

  function makeChip(text, extraClass = '') {
    const chip = document.createElement('li');
    chip.className = `scf-chip${extraClass ? ` ${extraClass}` : ''}`;
    chip.textContent = text;
    return chip;
  }

  function makeDate(prefix, value, fallback) {
    const wrapper = document.createElement('span');
    const parsed = formatDate(value);
    if (!parsed) {
      wrapper.textContent = fallback;
      return wrapper;
    }
    wrapper.append(`${prefix} `);
    const time = document.createElement('time');
    time.dateTime = parsed.toISOString();
    time.title = parsed.toLocaleString();
    time.textContent = parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    wrapper.appendChild(time);
    return wrapper;
  }

  function makeCredentialRow({ id, icon, name, chips = [], meta = [], actions = [] }) {
    const row = document.createElement('li');
    row.className = 'profile-credential';
    row.dataset.credentialId = String(id);

    const iconNode = document.createElement('span');
    iconNode.className = 'profile-credential__icon';
    iconNode.innerHTML = ICONS[icon];
    row.appendChild(iconNode);

    const main = document.createElement('div');
    main.className = 'profile-credential__main';
    const title = document.createElement('span');
    title.className = 'profile-credential__name';
    title.textContent = name;
    main.appendChild(title);
    if (chips.length) {
      const chipList = document.createElement('ul');
      chipList.className = 'scf-item__chips';
      chips.forEach((chip) => chipList.appendChild(chip));
      main.appendChild(chipList);
    }
    if (meta.length) {
      const metaLine = document.createElement('p');
      metaLine.className = 'profile-credential__meta';
      meta.forEach((part, index) => {
        if (index) metaLine.append(' · ');
        metaLine.appendChild(part);
      });
      main.appendChild(metaLine);
    }
    row.appendChild(main);

    const actionWrap = document.createElement('div');
    actionWrap.className = 'profile-credential__actions';
    actions.forEach(({ label, className, ariaLabel, onClick }) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `button button--small ${className}`;
      button.textContent = label;
      button.setAttribute('aria-label', ariaLabel);
      button.addEventListener('click', () => onClick(button));
      actionWrap.appendChild(button);
    });
    row.appendChild(actionWrap);
    return row;
  }

  // ── Authenticator apps ─────────────────────────────────────────────────
  const totpBody = root.querySelector('[data-totp-body]');
  const totpEmpty = root.querySelector('[data-totp-empty]');
  const totpModal = document.getElementById('profile-totp-modal');
  const verifyForm = document.getElementById('totp-verify-form');
  const secretInput = document.getElementById('totp-secret');
  const linkInput = document.getElementById('totp-link');
  const verifyName = document.getElementById('totp-name');
  const verifyCode = document.getElementById('totp-code');
  const verifyPassword = document.getElementById('totp-current-password');
  const verifyPasswordField = root.querySelector('[data-totp-password-field]');
  const verifySubmit = root.querySelector('[data-totp-submit]');
  const qrContainer = root.querySelector('[data-totp-qr-container]');
  const qrPlaceholder = root.querySelector('[data-totp-qr-placeholder]');
  const qrImage = root.querySelector('[data-totp-qr]');
  const manualToggle = root.querySelector('[data-totp-manual-toggle]');
  const manualSection = root.querySelector('[data-totp-manual]');
  let totpSetupRequest = 0;

  function renderTotpDevices() {
    if (!totpBody) {
      return;
    }
    totpDevices.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' }));
    totpBody.innerHTML = '';
    totpDevices.forEach((device) => {
      const name = device.name || 'Authenticator';
      totpBody.appendChild(makeCredentialRow({
        id: device.id,
        icon: 'phone',
        name,
        actions: [{
          label: 'Remove',
          className: 'button--danger',
          ariaLabel: `Remove authenticator ${name}`,
          onClick: (button) => handleRemoveTotp(device, button),
        }],
      }));
    });
    if (totpEmpty) totpEmpty.hidden = totpDevices.length > 0;
    if (verifyPasswordField) verifyPasswordField.hidden = totpDevices.length === 0;
    updateSecuritySummary();
  }

  function setQrLoading(loading) {
    if (qrPlaceholder) {
      qrPlaceholder.hidden = !loading;
      qrPlaceholder.textContent = 'Generating your QR code…';
    }
    if (qrImage && loading) {
      qrImage.hidden = true;
      qrImage.removeAttribute('src');
    }
    if (qrContainer) qrContainer.setAttribute('aria-busy', String(loading));
    if (verifySubmit) verifySubmit.disabled = loading;
  }

  function setManualOpen(open) {
    if (manualSection) manualSection.hidden = !open;
    if (manualToggle) manualToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function resetTotpSetup() {
    totpSetupRequest += 1;
    [secretInput, linkInput, verifyName, verifyCode, verifyPassword].forEach((input) => {
      if (input) input.value = '';
    });
    setManualOpen(false);
  }

  async function startTotpSetup(trigger) {
    resetTotpSetup();
    const requestId = totpSetupRequest;
    setQrLoading(true);
    openModal(totpModal, { trigger, focus: '#totp-code', onClose: resetTotpSetup });
    try {
      const response = await requestJson('/auth/totp/setup', { method: 'POST' });
      if (requestId !== totpSetupRequest) {
        return;
      }
      if (secretInput) secretInput.value = response.secret || '';
      if (linkInput) linkInput.value = response.otpauth_url || '';
      setQrLoading(false);
      if (qrImage && response.qr_code_data_uri) {
        qrImage.src = response.qr_code_data_uri;
        qrImage.hidden = false;
      } else {
        // No image: fall back to the secret and link.
        if (qrPlaceholder) {
          qrPlaceholder.hidden = false;
          qrPlaceholder.textContent = 'Use the secret instead.';
        }
        setManualOpen(true);
      }
      if (verifyCode) verifyCode.focus();
    } catch (error) {
      if (requestId !== totpSetupRequest) {
        return;
      }
      setQrLoading(false);
      if (verifySubmit) verifySubmit.disabled = true;
      if (qrPlaceholder) {
        qrPlaceholder.hidden = false;
        qrPlaceholder.textContent = 'Couldn’t create a code.';
      }
      setModalError(totpModal, `Unable to start authenticator setup: ${error.message}`);
    }
  }

  function handleRemoveTotp(device, trigger) {
    if (!device || !device.id) {
      return;
    }
    const lastMethod = totpDevices.length + passkeys.length === 1;
    confirmWithPassword({
      title: 'Remove authenticator app?',
      message: `“${device.name}” will stop working for sign-in.${lastMethod ? ' It’s your only sign-in method besides your password.' : ''} Enter your current password to confirm.`,
      submitLabel: 'Remove',
      danger: true,
      trigger,
      action: async (currentPassword) => {
        await requestJson(`/auth/totp/${device.id}`, {
          method: 'DELETE',
          body: JSON.stringify({ current_password: currentPassword }),
        });
        totpDevices = totpDevices.filter((entry) => entry.id !== device.id);
        renderTotpDevices();
        showMessage({ variant: 'success' }, 'Authenticator removed.');
      },
    });
  }

  document.querySelectorAll('[data-totp-add]').forEach((button) => {
    button.addEventListener('click', () => {
      showTab('security');
      startTotpSetup(button);
    });
  });

  if (manualToggle) {
    manualToggle.addEventListener('click', () => setManualOpen(manualSection ? manualSection.hidden : true));
  }

  if (verifyForm) {
    verifyForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const normalisedCode = (verifyCode ? verifyCode.value : '').replace(/\s+/g, '');
      let problem = '';
      if (!normalisedCode) {
        problem = 'Enter the 6-digit code from your authenticator app.';
      } else if (!/^\d{6}$/.test(normalisedCode)) {
        problem = 'Codes are 6 digits. Check the app and try again.';
      }
      if (problem) {
        setModalError(totpModal, problem);
        if (verifyCode) verifyCode.focus();
        return;
      }
      let currentPassword = null;
      if (totpDevices.length > 0) {
        currentPassword = verifyPassword ? verifyPassword.value : '';
        if (!currentPassword) {
          setModalError(totpModal, 'Enter your current password to add another authenticator.');
          if (verifyPassword) verifyPassword.focus();
          return;
        }
      }
      setModalError(totpModal, '');
      setBusy(verifySubmit, true, 'Verifying…');
      try {
        const response = await requestJson('/auth/totp/verify', {
          method: 'POST',
          body: JSON.stringify({
            code: normalisedCode,
            name: (verifyName ? verifyName.value.trim() : '') || null,
            current_password: currentPassword,
          }),
        });
        totpDevices.push({ id: response.id, name: response.name || 'Authenticator' });
        renderTotpDevices();
        closeModal(totpModal);
        showMessage({ variant: 'success' }, 'Authenticator app added.');
      } catch (error) {
        setModalError(totpModal, error.message || 'Unable to verify authenticator.');
      } finally {
        setBusy(verifySubmit, false);
      }
    });
  }

  // ── Passkeys ───────────────────────────────────────────────────────────
  const passkeyBody = root.querySelector('[data-passkey-body]');
  const passkeyEmpty = root.querySelector('[data-passkey-empty]');
  const passkeyModal = document.getElementById('profile-passkey-modal');
  const passkeyAddForm = document.getElementById('passkey-add-form');
  const passkeyNameInput = document.getElementById('passkey-name');
  const passkeyPasswordInput = document.getElementById('passkey-current-password');
  const passkeyUnsupported = root.querySelector('[data-passkey-unsupported]');
  const renameModal = document.getElementById('profile-rename-modal');
  const renameForm = document.getElementById('passkey-rename-form');
  const renameInput = document.getElementById('passkey-rename-input');
  const passkeyUtils = window.MyPortalPasskeyUtils;
  const passkeysSupported = Boolean(passkeyUtils && passkeyUtils.supportsPasskeys());

  function renderPasskeys() {
    if (!passkeyBody) {
      return;
    }
    passkeys.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' }));
    passkeyBody.innerHTML = '';
    passkeys.forEach((item) => {
      const name = item.name || 'Passkey';
      const chips = [];
      if (item.credential_backed_up) {
        chips.push(makeChip('Synced', 'sif-chip--optional'));
      } else if (item.credential_device_type === 'single_device') {
        chips.push(makeChip('This device only'));
      }
      item.transports.forEach((transport) => {
        chips.push(makeChip(TRANSPORT_LABELS[transport] || transport));
      });
      passkeyBody.appendChild(makeCredentialRow({
        id: item.id,
        icon: 'key',
        name,
        chips,
        meta: [
          makeDate('Added', item.created_at, 'Added date unknown'),
          makeDate('Last used', item.last_used_at, 'Not used yet'),
        ],
        actions: [
          {
            label: 'Rename',
            className: 'button--ghost',
            ariaLabel: `Rename passkey ${name}`,
            onClick: (button) => renamePasskey(item, button),
          },
          {
            label: 'Remove',
            className: 'button--danger',
            ariaLabel: `Remove passkey ${name}`,
            onClick: (button) => removePasskey(item, button),
          },
        ],
      }));
    });
    if (passkeyEmpty) passkeyEmpty.hidden = passkeys.length > 0;
    updateSecuritySummary();
  }

  function renamePasskey(item, trigger) {
    if (!renameModal || !renameForm || !renameInput) {
      return;
    }
    renameInput.value = item.name || 'Passkey';
    renameForm.onsubmit = async (event) => {
      event.preventDefault();
      const nextName = renameInput.value.trim();
      if (!nextName) {
        setModalError(renameModal, 'Enter a name for the passkey.');
        renameInput.focus();
        return;
      }
      const submit = renameForm.querySelector('button[type="submit"]');
      setBusy(submit, true);
      try {
        const updated = await requestJson(`/auth/passkeys/${item.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ name: nextName }),
        });
        passkeys = passkeys.map((entry) => (entry.id === item.id ? normalisePasskey(updated) : entry));
        renderPasskeys();
        closeModal(renameModal);
        showMessage({ variant: 'success' }, 'Passkey renamed.');
      } catch (error) {
        setModalError(renameModal, error.message || 'Unable to rename passkey.');
      } finally {
        setBusy(submit, false);
      }
    };
    openModal(renameModal, { trigger, focus: '#passkey-rename-input' });
    window.requestAnimationFrame(() => renameInput.select());
  }

  function removePasskey(item, trigger) {
    confirmWithPassword({
      title: 'Remove passkey?',
      message: `“${item.name}” will stop signing in to this portal. Enter your current password to confirm.`,
      submitLabel: 'Remove',
      danger: true,
      trigger,
      action: async (currentPassword) => {
        await requestJson(`/auth/passkeys/${item.id}`, {
          method: 'DELETE',
          body: JSON.stringify({ current_password: currentPassword }),
        });
        passkeys = passkeys.filter((entry) => entry.id !== item.id);
        renderPasskeys();
        showMessage({ variant: 'success' }, 'Passkey removed.');
      },
    });
  }

  if (!passkeysSupported) {
    if (passkeyUnsupported) passkeyUnsupported.hidden = false;
    document.querySelectorAll('[data-passkey-open]').forEach((button) => {
      button.disabled = true;
      button.title = 'Passkeys aren’t available in this browser or on this connection.';
    });
  }

  document.querySelectorAll('[data-passkey-open]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!passkeysSupported) {
        return;
      }
      showTab('security');
      if (passkeyAddForm) passkeyAddForm.reset();
      openModal(passkeyModal, { trigger: button, focus: '#passkey-name' });
    });
  });

  if (passkeyAddForm) {
    passkeyAddForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!passkeysSupported) {
        setModalError(passkeyModal, 'Passkeys aren’t available in this browser or on this connection.');
        return;
      }
      const passkeyName = passkeyNameInput ? passkeyNameInput.value.trim() : '';
      const currentPassword = passkeyPasswordInput ? passkeyPasswordInput.value : '';
      if (!passkeyName) {
        setModalError(passkeyModal, 'Enter a name for the passkey.');
        if (passkeyNameInput) passkeyNameInput.focus();
        return;
      }
      if (!currentPassword) {
        setModalError(passkeyModal, 'Enter your current password to continue.');
        if (passkeyPasswordInput) passkeyPasswordInput.focus();
        return;
      }
      setModalError(passkeyModal, '');
      const submitButton = passkeyAddForm.querySelector('[data-passkey-add]');
      setBusy(submitButton, true, 'Waiting for your device…');
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
        passkeys.push(normalisePasskey(created));
        renderPasskeys();
        passkeyAddForm.reset();
        closeModal(passkeyModal);
        showMessage({ variant: 'success' }, 'Passkey added.');
      } catch (error) {
        setModalError(
          passkeyModal,
          passkeyUtils.passkeyErrorMessage(error, error.message || 'Unable to register a passkey.'),
        );
      } finally {
        setBusy(submitButton, false);
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
        target.select();
        showMessage({ variant: 'error' }, 'Couldn’t copy automatically. The text is selected so you can copy it.');
      }
    });
  });

  renderTotpDevices();
  renderPasskeys();
})();
