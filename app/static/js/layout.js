(function () {
  // Server-rendered values for the shared layout (see base.html).
  const layoutData = (() => {
    try {
      return JSON.parse(document.getElementById('layout-data')?.textContent || '{}');
    } catch (_) {
      return {};
    }
  })();
  const tokenMeta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = tokenMeta ? tokenMeta.getAttribute('content') : null;

  if (csrfToken) {
    document.addEventListener('DOMContentLoaded', () => {
      document.querySelectorAll('form[method="post"]').forEach((form) => {
        if (!form.querySelector('input[name="_csrf"]')) {
          const input = document.createElement('input');
          input.type = 'hidden';
          input.name = '_csrf';
          input.value = csrfToken;
          form.insertBefore(input, form.firstChild);
        }
      });
    });

    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
      const nextInit = { ...init };
      if (typeof Headers !== 'undefined') {
        const headers =
          nextInit.headers instanceof Headers
            ? nextInit.headers
            : new Headers(nextInit.headers || {});
        if (!headers.has('X-CSRF-Token')) {
          headers.set('X-CSRF-Token', csrfToken);
        }
        nextInit.headers = headers;
      } else {
        const headerEntries = { ...(nextInit.headers || {}) };
        const hasHeader = Object.keys(headerEntries).some(
          (key) => key.toLowerCase() === 'x-csrf-token'
        );
        if (!hasHeader) {
          headerEntries['X-CSRF-Token'] = csrfToken;
        }
        nextInit.headers = headerEntries;
      }
      if (!nextInit.credentials) {
        nextInit.credentials = 'same-origin';
      }
      return originalFetch(input, nextInit);
    };
  }

  document.addEventListener(
    'submit',
    (event) => {
      if (!(event.target instanceof HTMLFormElement)) {
        return;
      }

      const formElement = event.target;
      if (!formElement.hasAttribute('data-confirm')) {
        return;
      }

      const message = formElement.getAttribute('data-confirm') || 'Are you sure?';
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true,
  );

  const form = document.querySelector('[data-company-switcher]');
  const select = form ? form.querySelector('.company-switcher__select') : null;
  if (form && select) {
    form.addEventListener('submit', function () {
      const selected = select.value;
      if (!selected) {
        return;
      }
      let hidden = form.querySelector('input[data-company-switcher-hidden="true"]');
      if (!hidden) {
        hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = 'companyId';
        hidden.setAttribute('data-company-switcher-hidden', 'true');
        form.appendChild(hidden);
      }
      hidden.value = selected;
    });

    select.addEventListener('change', function () {
      if (typeof form.requestSubmit === 'function') {
        form.requestSubmit();
      } else {
        form.submit();
      }
    });
  }

  const roleForm = document.querySelector('[data-role-switcher]');
  const roleSelect = roleForm ? roleForm.querySelector('select[name="roleId"]') : null;
  const submitRoleForm = () => {
    if (typeof roleForm.requestSubmit === 'function') {
      roleForm.requestSubmit();
    } else {
      roleForm.submit();
    }
  };
  if (roleForm && roleSelect) {
    roleSelect.addEventListener('change', submitRoleForm);

    // Compact mode: an icon button that pops down the role list.
    const roleToggle = roleForm.querySelector('[data-role-switcher-toggle]');
    const rolePopover = roleForm.querySelector('[data-role-switcher-popover]');
    if (roleToggle && rolePopover) {
      const roleOptions = () => Array.from(rolePopover.querySelectorAll('[data-role-value]'));
      const setRolePopoverOpen = (open, { focus = false } = {}) => {
        rolePopover.hidden = !open;
        roleToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open && focus) {
          const options = roleOptions();
          (options.find((option) => option.getAttribute('aria-checked') === 'true') || options[0])?.focus();
        }
      };
      roleToggle.addEventListener('click', () => setRolePopoverOpen(rolePopover.hidden, { focus: true }));
      rolePopover.addEventListener('click', (event) => {
        const option = event.target.closest('[data-role-value]');
        if (!option) {
          return;
        }
        roleSelect.value = option.dataset.roleValue;
        setRolePopoverOpen(false);
        submitRoleForm();
      });
      rolePopover.addEventListener('keydown', (event) => {
        const options = roleOptions();
        const index = options.indexOf(document.activeElement);
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          const step = event.key === 'ArrowDown' ? 1 : -1;
          options[(index + step + options.length) % options.length]?.focus();
        } else if (event.key === 'Home' || event.key === 'End') {
          event.preventDefault();
          options[event.key === 'Home' ? 0 : options.length - 1]?.focus();
        }
      });
      roleForm.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !rolePopover.hidden) {
          setRolePopoverOpen(false);
          roleToggle.focus();
        }
      });
      document.addEventListener('click', (event) => {
        if (!rolePopover.hidden && !roleForm.contains(event.target)) {
          setRolePopoverOpen(false);
        }
      });
    }
  }

  const layout = document.querySelector('[data-layout]');
  const sidebar = document.querySelector('[data-sidebar]');
  const toggleButton = document.querySelector('[data-mobile-nav-toggle]');
  const scrim = document.querySelector('[data-mobile-nav-scrim]');
  const mobileMedia = window.matchMedia('(max-width: 1024px)');
  let lastFocusedTrigger = null;

  const focusFirstSidebarItem = () => {
    if (!sidebar) {
      return;
    }
    const focusableSelector =
      'a[href], button:not([disabled]), textarea, input:not([type="hidden"]), select, [tabindex]:not([tabindex="-1"])';
    const firstFocusable = sidebar.querySelector(focusableSelector);
    if (firstFocusable instanceof HTMLElement) {
      firstFocusable.focus({ preventScroll: true });
    } else {
      sidebar.focus({ preventScroll: true });
    }
  };

  const syncSidebarAria = (isOpen) => {
    if (!sidebar) {
      return;
    }
    if (mobileMedia.matches) {
      sidebar.setAttribute('aria-hidden', String(!isOpen));
      if (!isOpen) {
        sidebar.setAttribute('inert', '');
      } else {
        sidebar.removeAttribute('inert');
      }
    } else {
      sidebar.removeAttribute('aria-hidden');
      sidebar.removeAttribute('inert');
    }
    if (scrim) {
      scrim.setAttribute('aria-hidden', String(!isOpen));
    }
  };

  const closeSidebar = () => {
    if (!layout || !toggleButton) {
      return;
    }
    layout.classList.remove('layout--sidebar-open');
    toggleButton.setAttribute('aria-expanded', 'false');
    syncSidebarAria(false);
    if (lastFocusedTrigger instanceof HTMLElement) {
      lastFocusedTrigger.focus({ preventScroll: true });
      lastFocusedTrigger = null;
    }
  };

  const openSidebar = () => {
    if (!layout || !toggleButton) {
      return;
    }
    layout.classList.add('layout--sidebar-open');
    toggleButton.setAttribute('aria-expanded', 'true');
    syncSidebarAria(true);
    lastFocusedTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    focusFirstSidebarItem();
  };

  const toggleSidebar = () => {
    if (!layout) {
      return;
    }
    const isOpen = layout.classList.contains('layout--sidebar-open');
    if (isOpen) {
      closeSidebar();
    } else {
      openSidebar();
    }
  };

  const handleMediaChange = () => {
    if (!layout || !toggleButton) {
      return;
    }
    if (!mobileMedia.matches) {
      layout.classList.remove('layout--sidebar-open');
      toggleButton.setAttribute('aria-expanded', 'false');
      syncSidebarAria(false);
      lastFocusedTrigger = null;
    } else {
      syncSidebarAria(layout.classList.contains('layout--sidebar-open'));
    }
  };

  if (layout && sidebar && toggleButton) {
    sidebar.setAttribute('tabindex', '-1');
    toggleButton.addEventListener('click', toggleSidebar);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && layout.classList.contains('layout--sidebar-open')) {
        event.preventDefault();
        closeSidebar();
      }
    });
    if (scrim) {
      scrim.addEventListener('click', closeSidebar);
    }
    sidebar.addEventListener('click', (event) => {
      if (
        mobileMedia.matches &&
        event.target instanceof Element &&
        event.target.closest('a[href]')
      ) {
        closeSidebar();
      }
    });
    mobileMedia.addEventListener('change', handleMediaChange);
    handleMediaChange();
  }

  const sidebarMenuRoot = document.querySelector('.layout__sidebar .menu');
  const hasAuthenticatedUser = layoutData.hasAuthenticatedUser === true;
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const SIDEBAR_DIVIDER_KEY_PREFIX = '__divider__:';
  const SIDEBAR_SPACER_KEY_PREFIX = '__spacer__:';
  const SIDEBAR_GROUP_KEY_PREFIX = '__group__:';
  const enabledModules = layoutData.enabledModules || {};
  const sidebarItemRegistry = new Map();
  let activeSidebarPreferences = { navigation_style: 'top', role_switcher_display: 'full', top_menu_labels: 'auto', order: [], hidden: [], groups: [] };
  const sidebarDefaultPreferences = layoutData.sidebarDefaultPreferences || { order: [], hidden: [], groups: [] };

  // Shared with the profile page editor so both render the same glyphs.
  const sidebarGroupIcons = {
    folder: '<path d="M3 5a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    support: '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 2c1.6 0 3.1.5 4.3 1.3l-2.2 2.2A4.9 4.9 0 0 0 12 7c-.8 0-1.5.2-2.1.5L7.7 5.3A8 8 0 0 1 12 4zM4 12c0-1.6.5-3.1 1.3-4.3l2.2 2.2c-.3.6-.5 1.3-.5 2.1s.2 1.5.5 2.1l-2.2 2.2A8 8 0 0 1 4 12zm8 8c-1.6 0-3.1-.5-4.3-1.3l2.2-2.2c.6.3 1.3.5 2.1.5s1.5-.2 2.1-.5l2.2 2.2A8 8 0 0 1 12 20zm0-5a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm6.7 1.3-2.2-2.2c.3-.6.5-1.3.5-2.1s-.2-1.5-.5-2.1l2.2-2.2A8 8 0 0 1 20 12c0 1.6-.5 3.1-1.3 4.3z"/>',
    shop: '<path d="M4 4h16l1 5a3 3 0 0 1-2 2.83V20H5v-8.17A3 3 0 0 1 3 9zm3 8v6h10v-6"/>',
    server: '<path d="M4 3h16a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm0 10h16a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1zm3-7.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm0 10a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"/>',
    briefcase: '<path d="M9 4V2h6v2h5a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm2 0h2V3h-2zM4 9v9h16V9z"/>',
    shield: '<path d="M12 2 4 5v6c0 5.1 3.4 9.9 8 11 4.6-1.1 8-5.9 8-11V5zm-1.2 13.6-3.5-3.5 1.4-1.4 2.1 2.1 4.6-4.6 1.4 1.4z"/>',
    chart: '<path d="M3 3h2v16h16v2H3zm4 9h3v5H7zm5-5h3v10h-3zm5 3h3v7h-3z"/>',
    settings: '<path d="M19.4 13a7.5 7.5 0 0 0 0-2l2.1-1.6-2-3.5-2.5 1a7.3 7.3 0 0 0-1.7-1L15 3.3h-4l-.4 2.6a7.3 7.3 0 0 0-1.7 1l-2.5-1-2 3.5L6.6 11a7.5 7.5 0 0 0 0 2l-2.1 1.6 2 3.5 2.5-1c.5.4 1.1.7 1.7 1l.4 2.6h4l.4-2.6c.6-.3 1.2-.6 1.7-1l2.5 1 2-3.5zM12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z"/>',
    automation: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    phone: '<path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1z"/>',
    history: '<path d="M13 3a9 9 0 0 0-9 9H1l4 4 4-4H6a7 7 0 1 1 2.05 4.95l-1.42 1.42A9 9 0 1 0 13 3zm-1 5v5l4.25 2.52.77-1.28-3.52-2.09V8z"/>',
    grid: '<path d="M3 3h8v8H3zm10 0h8v8h-8zM3 13h8v8H3zm10 0h8v8h-8z"/>',
    star: '<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8-6.2-3.2L5.8 21 7 14.2l-5-4.9 6.9-1z"/>',
    users: '<path d="M8 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm8-1a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM1 20v-2a6 6 0 0 1 12 0v2zm13 0v-2a7.9 7.9 0 0 0-1.1-4 5 5 0 0 1 8.1 4v2z"/>',
  };

  const SIDEBAR_OPEN_GROUPS_KEY = 'myportal_sidebar_open_groups';
  const loadOpenSidebarGroups = () => {
    try {
      const parsed = JSON.parse(localStorage.getItem(SIDEBAR_OPEN_GROUPS_KEY) || '[]');
      return new Set(Array.isArray(parsed) ? parsed : []);
    } catch (_) {
      return new Set();
    }
  };
  const openSidebarGroups = loadOpenSidebarGroups();
  const saveOpenSidebarGroups = () => {
    try {
      localStorage.setItem(SIDEBAR_OPEN_GROUPS_KEY, JSON.stringify(Array.from(openSidebarGroups)));
    } catch (_) {
      /* ignore storage errors */
    }
  };

  const createMenuKey = (menuItem) => {
    const existing = menuItem.dataset.menuKey || '';
    if (existing) {
      return existing;
    }
    const link = menuItem.querySelector(':scope > a[href]');
    if (!link) {
      return '';
    }
    const href = link.getAttribute('href') || '';
    if (href.startsWith('/')) {
      return href;
    }
    const label = (menuItem.querySelector('.menu__label')?.textContent || '').trim().toLowerCase();
    return label ? `label:${label}` : '';
  };

  // Badge counts live inside some labels; they must not leak into names.
  const readMenuLabel = (menuItem) => {
    const labelElement = menuItem.querySelector('.menu__label');
    if (!labelElement) {
      return '';
    }
    const copy = labelElement.cloneNode(true);
    copy.querySelectorAll('.menu__badge').forEach((badge) => badge.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim();
  };

  const buildCustomSeparator = (key) => {
    if (!sidebarMenuRoot || typeof key !== 'string') {
      return null;
    }
    let className = '';
    let label = '';
    if (key.startsWith(SIDEBAR_DIVIDER_KEY_PREFIX)) {
      className = 'menu__divider menu__divider--custom';
      label = 'Divider';
    } else if (key.startsWith(SIDEBAR_SPACER_KEY_PREFIX)) {
      className = 'menu__spacer menu__spacer--custom';
      label = 'Spacer';
    } else {
      return null;
    }
    const separator = document.createElement('li');
    separator.className = className;
    separator.dataset.menuKey = key;
    separator.dataset.menuLabel = label;
    separator.setAttribute('role', 'presentation');
    return separator;
  };

  const setMenuItemExpanded = (menuItem, expanded) => {
    const toggle = menuItem.querySelector(':scope > [data-menu-toggle]');
    const submenu = menuItem.querySelector(':scope > .menu__submenu');
    menuItem.classList.toggle('menu__item--expanded', expanded);
    submenu?.classList.toggle('menu__submenu--expanded', expanded);
    toggle?.setAttribute('aria-expanded', String(expanded));
  };

  // Returns null when none of the group's links are available to this
  // user, so role-specific default groups never render empty.
  const buildCustomGroup = (group, hidden) => {
    const children = (Array.isArray(group.items) ? group.items : [])
      .map((key) => [key, sidebarItemRegistry.get(key)])
      .filter(([, child]) => child);
    if (!children.length) {
      return null;
    }
    const wrapper = document.createElement('li');
    wrapper.className = 'menu__item menu__item--expandable menu__item--custom-group';
    wrapper.dataset.menuKey = group.id;
    wrapper.dataset.menuLabel = group.label;
    const button = document.createElement('button');
    button.className = 'menu__toggle';
    button.type = 'button';
    button.setAttribute('data-menu-toggle', '');
    button.setAttribute('aria-expanded', 'false');
    const icon = sidebarGroupIcons[group.icon] || sidebarGroupIcons.folder;
    button.innerHTML = `<span class="menu__icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false">${icon}</svg></span><span class="menu__label"></span><span class="menu__group-badge" hidden></span><span class="menu__expand-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M7 10l5 5 5-5z"/></svg></span>`;
    button.querySelector('.menu__label').textContent = group.label;
    const submenu = document.createElement('ul');
    submenu.className = 'menu__submenu menu__submenu--custom';
    let visibleChildren = 0;
    let badgeTotal = 0;
    children.forEach(([key, child]) => {
      child.classList.add('menu__custom-group-child');
      submenu.appendChild(child);
      const isCurrentPage = child.querySelector('a[aria-current="page"]');
      child.hidden = hidden.has(key) && !isCurrentPage;
      if (!child.hidden) {
        visibleChildren += 1;
        child.querySelectorAll('.menu__badge').forEach((badge) => {
          const count = Number.parseInt(badge.textContent, 10);
          if (Number.isFinite(count)) badgeTotal += count;
        });
      }
    });
    // Collapsed groups would otherwise hide unread counts entirely.
    const groupBadge = button.querySelector('.menu__group-badge');
    if (badgeTotal > 0) {
      groupBadge.textContent = String(badgeTotal);
      groupBadge.hidden = false;
    }
    wrapper.hidden = visibleChildren === 0;
    wrapper.append(button, submenu);
    const containsCurrentPage = Boolean(submenu.querySelector('a[aria-current="page"]'));
    setMenuItemExpanded(wrapper, containsCurrentPage || openSidebarGroups.has(group.id));
    return wrapper;
  };

  const listSidebarMenuItems = () => {
    if (!sidebarMenuRoot) {
      return [];
    }
    const rows = [];
    sidebarMenuRoot.querySelectorAll(':scope > li').forEach((menuItem) => {
      const moduleSlug = menuItem.dataset.moduleSlug;
      if (moduleSlug && enabledModules[moduleSlug] !== true) {
        menuItem.remove();
        return;
      }
      if (menuItem.classList.contains('menu__item--impersonation') || menuItem.classList.contains('menu__heading')) {
        return;
      }
      const key = createMenuKey(menuItem);
      const label = (menuItem.dataset.menuLabel || readMenuLabel(menuItem)).trim();
      if (!key || !label) {
        return;
      }
      menuItem.dataset.menuKey = key;
      menuItem.dataset.menuLabel = label;
      if (!key.startsWith(SIDEBAR_GROUP_KEY_PREFIX)) {
        sidebarItemRegistry.set(key, menuItem);
      }
      rows.push({ key, label, element: menuItem });
    });
    return rows;
  };

  const syncNavigationPresentation = () => {
    // Mobile always uses the labelled drawer; retain the saved desktop preference.
    const useTopNavigation = !mobileMedia.matches && activeSidebarPreferences.navigation_style === 'top';
    document.body.classList.toggle('navigation--top', useTopNavigation);
    // On standard resolution displays (1080p or lower) the labelled
    // top menu runs past the edge of the screen, so collapse it to icons
    // only with hover tooltips unless the user opted into always
    // showing the labels.
    const standardResolutionDisplay =
      window.screen && Number.isFinite(window.screen.height) && window.screen.height <= 1080;
    const compactTopMenu =
      useTopNavigation &&
      activeSidebarPreferences.top_menu_labels !== 'always' &&
      standardResolutionDisplay;
    document.body.classList.toggle('navigation--top-compact', compactTopMenu);
    return compactTopMenu;
  };
  mobileMedia.addEventListener('change', syncNavigationPresentation);

  const applySidebarPreferences = (preferences) => {
    if (!sidebarMenuRoot) {
      return;
    }
    activeSidebarPreferences = {
      navigation_style: preferences?.navigation_style === 'sidebar' ? 'sidebar' : 'top',
      role_switcher_display: ['full', 'icon', 'hidden'].includes(preferences?.role_switcher_display)
        ? preferences.role_switcher_display
        : 'full',
      top_menu_labels: ['auto', 'always'].includes(preferences?.top_menu_labels)
        ? preferences.top_menu_labels
        : 'auto',
      order: Array.isArray(preferences?.order) ? preferences.order : [],
      hidden: Array.isArray(preferences?.hidden) ? preferences.hidden : [],
      groups: Array.isArray(preferences?.groups) ? preferences.groups : [],
    };
    const compactTopMenu = syncNavigationPresentation();
    const roleSwitcherContainer = document.querySelector('[data-role-switcher-container]');
    if (roleSwitcherContainer) {
      // While viewing as another role a hidden switcher falls back to the
      // icon so the super admin can always switch back.
      let roleSwitcherDisplay = activeSidebarPreferences.role_switcher_display;
      if (roleSwitcherDisplay === 'hidden' && roleSwitcherContainer.hasAttribute('data-role-switcher-active')) {
        roleSwitcherDisplay = 'icon';
      }
      roleSwitcherContainer.hidden = roleSwitcherDisplay === 'hidden';
      roleSwitcherContainer.classList.toggle('role-switcher--icon', roleSwitcherDisplay === 'icon');
    }
    sidebarMenuRoot.querySelectorAll(':scope > .menu__item--custom-group').forEach((group) => group.remove());
    sidebarItemRegistry.forEach((element) => {
      element.classList.remove('menu__custom-group-child');
      sidebarMenuRoot.appendChild(element);
    });
    const order = activeSidebarPreferences.order;
    const hidden = new Set(activeSidebarPreferences.hidden);
    const rows = listSidebarMenuItems();
    // Server-rendered section headings cannot follow a rearranged menu.
    const isCustomLayout = order.length > 0 || activeSidebarPreferences.groups.length > 0;
    sidebarMenuRoot.querySelectorAll(':scope > .menu__heading').forEach((heading) => {
      heading.hidden = isCustomLayout;
    });
    const byKey = new Map(rows.map((row) => [row.key, row]));
    const groupedKeys = new Set();
    activeSidebarPreferences.groups.forEach((group) => {
      (Array.isArray(group.items) ? group.items : []).forEach((key) => groupedKeys.add(key));
    });
    activeSidebarPreferences.groups.forEach((group) => {
      const element = buildCustomGroup(group, hidden);
      if (element) {
        byKey.set(group.id, { key: group.id, label: group.label, element, isGroup: true });
      }
    });
    const ordered = [];

    order.forEach((key) => {
      let row = byKey.get(key);
      if (!row) {
        const separator = buildCustomSeparator(key);
        if (separator) {
          row = {
            key,
            label: separator.dataset.menuLabel || '',
            element: separator,
          };
        }
      }
      if (row && !groupedKeys.has(key)) {
        ordered.push(row);
        byKey.delete(key);
      }
    });

    byKey.forEach((row, key) => {
      if (!groupedKeys.has(key)) ordered.push(row);
    });

    ordered.forEach((row) => {
      sidebarMenuRoot.appendChild(row.element);
      if (row.isGroup) {
        return;
      }
      const isCurrentPage = row.element.querySelector('a[aria-current="page"]');
      row.element.hidden = hidden.has(row.key) && !isCurrentPage;
    });
    if (activeSidebarPreferences.navigation_style === 'top') {
      sidebarMenuRoot.querySelectorAll(':scope > .menu__item--expandable').forEach((item) => {
        // Top-navigation dropdowns are transient. The active link still
        // identifies the current section without opening its menu on
        // every page load or preferences refresh.
        setMenuItemExpanded(item, false);
      });
    }
    // In the compact top menu the labels are hidden by CSS, so each
    // top-level item needs a hover tooltip naming it (which doubles as
    // its accessible name) and any unread badge moves out of the hidden
    // label to stay visible next to the icon.
    sidebarMenuRoot.querySelectorAll(':scope > .menu__item, :scope > .menu__item--expandable').forEach((item) => {
      const target = item.querySelector(':scope > a, :scope > [data-menu-toggle]');
      const labelElement = target?.querySelector(':scope > .menu__label');
      if (!target || !labelElement) {
        return;
      }
      if (compactTopMenu) {
        const label = (item.dataset.menuLabel || readMenuLabel(item)).trim();
        if (label) {
          target.setAttribute('title', label);
        }
        const badge = labelElement.querySelector('.menu__badge');
        if (badge && badge.parentElement === labelElement) {
          target.insertBefore(badge, labelElement);
        }
      } else {
        target.removeAttribute('title');
        const badge = target.querySelector(':scope > .menu__badge');
        if (badge && badge.parentElement === target) {
          labelElement.appendChild(badge);
        }
      }
    });
    document.dispatchEvent(new CustomEvent('myportal:sidebar-updated'));
  };

  const requestSidebarPreferences = async (method = 'GET', payload = null) => {
    if (!hasAuthenticatedUser) {
      return { order: [], hidden: [], groups: [] };
    }
    const headers = {};
    if (csrfMeta?.content) {
      headers['X-CSRF-Token'] = csrfMeta.content;
    }
    if (payload !== null) {
      headers['Content-Type'] = 'application/json';
    }
    const response = await fetch('/api/users/me/sidebar-preferences', {
      method,
      credentials: 'same-origin',
      headers,
      body: payload !== null ? JSON.stringify(payload) : undefined,
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return await response.json();
  };

  // Versioned so browsers holding a pre-grouping cache do not flash it.
  const SIDEBAR_PREFS_CACHE_KEY = 'myportal_sidebar_prefs_v2';

  const loadCachedSidebarPreferences = () => {
    try {
      const raw = localStorage.getItem(SIDEBAR_PREFS_CACHE_KEY);
      if (raw) {
        return JSON.parse(raw);
      }
    } catch (_) {
      /* ignore parse errors */
    }
    return null;
  };

  const saveCachedSidebarPreferences = (prefs) => {
    try {
      localStorage.setItem(SIDEBAR_PREFS_CACHE_KEY, JSON.stringify(prefs));
    } catch (_) {
      /* ignore storage errors */
    }
  };

  // Capture every server-rendered entry before profile.js asks for the
  // customisation catalogue (including when this user has no cache yet).
  listSidebarMenuItems();

  window.MyPortalSidebarMenu = {
    listItems: () => {
      const groupByItem = new Map();
      activeSidebarPreferences.groups.forEach((group) =>
        (group.items || []).forEach((key) => groupByItem.set(key, group.id)),
      );
      return Array.from(sidebarItemRegistry.entries()).map(([key, element]) => ({
        key,
        label: element.dataset.menuLabel || '',
        icon: element.querySelector('.menu__icon svg')?.outerHTML || '',
        hidden: activeSidebarPreferences.hidden.includes(key),
        groupId: groupByItem.get(key) || '',
      }));
    },
    groupIcons: () => ({ ...sidebarGroupIcons }),
    getPreferences: () => JSON.parse(JSON.stringify(activeSidebarPreferences)),
    getDefaults: () => JSON.parse(JSON.stringify(sidebarDefaultPreferences)),
    apply: applySidebarPreferences,
    save: async (preferences) => {
      const saved = await requestSidebarPreferences('PUT', preferences);
      saveCachedSidebarPreferences(saved);
      applySidebarPreferences(saved);
      return saved;
    },
    reset: async () => {
      const defaults = await requestSidebarPreferences('DELETE');
      saveCachedSidebarPreferences(defaults);
      applySidebarPreferences(defaults);
      return defaults;
    },
  };

  if (hasAuthenticatedUser && sidebarMenuRoot) {
    applySidebarPreferences(loadCachedSidebarPreferences() || sidebarDefaultPreferences);
    requestSidebarPreferences()
      .then((prefs) => {
        saveCachedSidebarPreferences(prefs);
        applySidebarPreferences(prefs);
      })
      .catch(() => {
        /* ignore preferences load failures */
      });
  }

  // Delegated so groups rebuilt after preferences load keep working and
  // no toggle ever ends up with two handlers that cancel each other out.
  sidebarMenuRoot?.addEventListener('click', (event) => {
    const toggle = event.target.closest('[data-menu-toggle]');
    if (!toggle || !sidebarMenuRoot.contains(toggle)) {
      return;
    }
    const menuItem = toggle.closest('.menu__item--expandable');
    if (!menuItem) {
      return;
    }
    const expanded = !menuItem.classList.contains('menu__item--expanded');
    if (document.body.classList.contains('navigation--top') && expanded) {
      sidebarMenuRoot.querySelectorAll(':scope > .menu__item--expandable').forEach((item) => {
        // Never collapse the item an expanded sub-item lives inside, or
        // opening a nested submenu (Compliance Checks / Continuity) would
        // take the whole group down with it.
        if (item !== menuItem && !item.contains(menuItem)) setMenuItemExpanded(item, false);
      });
    }
    setMenuItemExpanded(menuItem, expanded);
    if (menuItem.classList.contains('menu__item--custom-group')) {
      const groupId = menuItem.dataset.menuKey;
      if (expanded) openSidebarGroups.add(groupId);
      else openSidebarGroups.delete(groupId);
      saveOpenSidebarGroups();
    }
  });

  // A top-navigation submenu is transient: choosing a destination or
  // clicking elsewhere closes it instead of leaving several panels open.
  document.addEventListener('click', (event) => {
    if (!document.body.classList.contains('navigation--top')) return;
    const selectedLink = event.target.closest('.menu__submenu a[href]');
    const clickedInsideMenu = event.target.closest('.layout__sidebar .menu');
    if (!selectedLink && clickedInsideMenu) return;
    sidebarMenuRoot?.querySelectorAll(':scope > .menu__item--expandable').forEach((item) => {
      setMenuItemExpanded(item, false);
    });
  });
})();
