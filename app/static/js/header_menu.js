/* Generic disclosure menu used by the page header actions ("Actions ▾"),
 * table column pickers, and table bulk-action menus.
 *
 * Markup contract (see templates/macros/header.html and macros/tables.html):
 *   <div class="header-menu" data-header-menu>
 *     <button data-header-menu-toggle aria-expanded="false" aria-controls="ID">…</button>
 *     <div id="ID" data-header-menu-panel hidden>…items…</div>
 *   </div>
 *
 * Behaviour: click toggles open/closed; Esc closes; click outside closes;
 * focus moves to the panel when opened.
 *
 * Submenus (render_submenu in macros/header.html) nest inside a panel:
 *   <div data-header-submenu>
 *     <button data-header-submenu-toggle aria-expanded="false" aria-controls="SUB">…</button>
 *     <div id="SUB" data-header-submenu-panel hidden>…items…</div>
 *   </div>
 * They open on hover (pointer devices), click, Enter/Space or ArrowRight, and
 * close on ArrowLeft or Esc (which returns focus to the submenu's toggle). The
 * flyout sits beside the menu, on whichever side has room, or expands inline
 * under its toggle when the screen is too narrow for either.
 *
 * Native <details> menus can opt into the same floating placement with
 *   <details data-floating-menu [data-floating-menu-placement="top"]>
 *     <summary>…</summary>
 *     <div data-floating-menu-panel>…items…</div>
 *   </details>
 * While open, the panel is position:fixed against its toggle so it pops over
 * cards, sections and short pages instead of being clipped by their overflow.
 */
(function () {
  'use strict';

  var openMenu = null;

  function positionPanel(menu, panel) {
    var toggle = menu.querySelector('[data-header-menu-toggle]');
    panel = panel || getMenuPanel(menu);
    placeFloating(toggle, panel, false);
  }

  // Fix `panel` to the viewport next to `toggle`, opening below unless there
  // is not enough room (or `preferTop` is set and there is room above).
  function placeFloating(toggle, panel, preferTop) {
    if (!toggle || !panel) {
      return;
    }
    var rect = toggle.getBoundingClientRect();
    var gap = 4;
    var right = Math.max(8, window.innerWidth - rect.right);
    panel.style.position = 'fixed';
    panel.style.left = 'auto';
    panel.style.right = right + 'px';
    panel.style.zIndex = '9999';

    // Measure after fixing the panel so row menus inside horizontally
    // scrollable table wrappers can escape the wrapper's clipping. If there
    // is not enough room below the button, open upward instead.
    var panelHeight = panel.offsetHeight || 0;
    var spaceBelow = window.innerHeight - rect.bottom - gap - 8;
    var spaceAbove = rect.top - gap - 8;
    var openUp = preferTop
      ? panelHeight <= spaceAbove || spaceAbove > spaceBelow
      : panelHeight > spaceBelow && spaceAbove > spaceBelow;
    if (openUp) {
      panel.style.top = 'auto';
      panel.style.bottom = Math.max(8, window.innerHeight - rect.top + gap) + 'px';
    } else {
      panel.style.top = (rect.bottom + gap) + 'px';
      panel.style.bottom = 'auto';
    }

    correctForContainingBlock(panel);
  }

  // An ancestor with backdrop-filter, filter, transform, contain or
  // will-change (e.g. the blurred .layout__header) becomes the containing
  // block for position:fixed descendants, so viewport coordinates land offset
  // by that ancestor's position. Measure where the panel actually rendered and
  // shift it by the difference so it sits directly against its toggle.
  function correctForContainingBlock(panel) {
    var placed = panel.getBoundingClientRect();
    var right = parseFloat(panel.style.right) || 0;
    var dx = (window.innerWidth - right) - placed.right;
    if (Math.abs(dx) > 0.5) {
      panel.style.right = (right - dx) + 'px';
    }
    if (panel.style.top !== 'auto') {
      var top = parseFloat(panel.style.top) || 0;
      var dy = placed.top - top;
      if (Math.abs(dy) > 0.5) {
        panel.style.top = (top - dy) + 'px';
      }
    } else {
      var bottom = parseFloat(panel.style.bottom) || 0;
      var dyUp = (window.innerHeight - bottom) - placed.bottom;
      if (Math.abs(dyUp) > 0.5) {
        panel.style.bottom = (bottom - dyUp) + 'px';
      }
    }
  }

  function getMenuPanel(menu) {
    return menu.querySelector('[data-header-menu-panel]') || menu.querySelector('.header-title-menu__list');
  }

  function resetPanelPosition(panel) {
    if (!panel) {
      return;
    }
    panel.style.position = '';
    panel.style.top = '';
    panel.style.left = '';
    panel.style.right = '';
    panel.style.bottom = '';
    panel.style.zIndex = '';
  }

  var HOVER_CLOSE_DELAY = 200;
  var HOVER_CLICK_GRACE = 600;

  function getSubmenuParts(sub) {
    return {
      toggle: sub.querySelector('[data-header-submenu-toggle]'),
      panel: sub.querySelector('[data-header-submenu-panel]'),
    };
  }

  function submenuItems(panel) {
    return Array.prototype.slice.call(panel.querySelectorAll('a, button')).filter(function (item) {
      return !item.disabled && !item.hasAttribute('disabled') && !item.closest('[hidden]');
    });
  }

  // Place the flyout beside the parent menu panel (its containing block), on
  // the left when there is room since Actions menus hug the right edge,
  // otherwise on the right, otherwise inline beneath the toggle.
  function placeSubmenu(sub) {
    var parts = getSubmenuParts(sub);
    var host = sub.closest('[data-header-menu-panel], [data-header-submenu-panel]');
    if (!parts.toggle || !parts.panel || !host) {
      return;
    }
    var panel = parts.panel;
    panel.classList.remove('header-menu__subpanel--inline');
    panel.style.top = '';
    panel.style.left = '';
    panel.style.right = '';
    var gap = 4;
    var margin = 8;
    var hostRect = host.getBoundingClientRect();
    var width = panel.offsetWidth;
    if (hostRect.left - width - gap >= margin) {
      panel.style.right = 'calc(100% + ' + gap + 'px)';
    } else if (hostRect.right + width + gap <= window.innerWidth - margin) {
      panel.style.left = 'calc(100% + ' + gap + 'px)';
    } else {
      panel.classList.add('header-menu__subpanel--inline');
      return;
    }
    var padTop = parseFloat(window.getComputedStyle(panel).paddingTop) || 0;
    var top = parts.toggle.offsetTop - padTop;
    panel.style.top = top + 'px';
    // Keep the flyout on screen when its toggle sits near the viewport edge.
    var rect = panel.getBoundingClientRect();
    var shift = 0;
    if (rect.bottom > window.innerHeight - margin) {
      shift = window.innerHeight - margin - rect.bottom;
    }
    if (rect.top + shift < margin) {
      shift = margin - rect.top;
    }
    if (shift) {
      panel.style.top = (top + shift) + 'px';
    }
  }

  function setSubmenuState(sub, open) {
    var parts = getSubmenuParts(sub);
    if (!parts.toggle || !parts.panel) {
      return;
    }
    if (sub.__submenuTimer) {
      clearTimeout(sub.__submenuTimer);
      sub.__submenuTimer = null;
    }
    if (open) {
      var host = sub.parentElement;
      if (host) {
        host.querySelectorAll(':scope > [data-header-submenu].header-menu__sub--open').forEach(function (other) {
          if (other !== sub) {
            setSubmenuState(other, false);
          }
        });
      }
      parts.panel.hidden = false;
      placeSubmenu(sub);
      parts.toggle.setAttribute('aria-expanded', 'true');
      sub.classList.add('header-menu__sub--open');
    } else {
      parts.panel.hidden = true;
      parts.panel.classList.remove('header-menu__subpanel--inline');
      parts.panel.style.top = '';
      parts.panel.style.left = '';
      parts.panel.style.right = '';
      parts.toggle.setAttribute('aria-expanded', 'false');
      sub.classList.remove('header-menu__sub--open');
    }
  }

  function closeSubmenus(root) {
    root.querySelectorAll('[data-header-submenu].header-menu__sub--open').forEach(function (sub) {
      setSubmenuState(sub, false);
    });
  }

  function focusItem(items, index) {
    if (!items.length) {
      return;
    }
    var target = items[(index + items.length) % items.length];
    if (typeof target.focus === 'function') {
      target.focus();
    }
  }

  function attachSubmenu(sub) {
    if (sub.__headerSubmenuBound) {
      return;
    }
    var parts = getSubmenuParts(sub);
    if (!parts.toggle || !parts.panel) {
      return;
    }
    sub.__headerSubmenuBound = true;
    var canHover = window.matchMedia ? window.matchMedia('(hover: hover) and (pointer: fine)') : null;

    parts.toggle.addEventListener('click', function (event) {
      event.preventDefault();
      // A mouse click lands just after hovering opened the submenu, so keep
      // it open then rather than toggling it straight back shut.
      var isOpen = sub.classList.contains('header-menu__sub--open');
      var justHovered = sub.__hoverOpenedAt && Date.now() - sub.__hoverOpenedAt < HOVER_CLICK_GRACE;
      setSubmenuState(sub, justHovered ? true : !isOpen);
    });

    sub.addEventListener('mouseenter', function () {
      if (!canHover || !canHover.matches) {
        return;
      }
      if (!sub.classList.contains('header-menu__sub--open')) {
        sub.__hoverOpenedAt = Date.now();
      }
      setSubmenuState(sub, true);
    });
    sub.addEventListener('mouseleave', function () {
      if (!canHover || !canHover.matches || parts.panel.classList.contains('header-menu__subpanel--inline')) {
        return;
      }
      if (sub.__submenuTimer) {
        clearTimeout(sub.__submenuTimer);
      }
      sub.__submenuTimer = setTimeout(function () {
        sub.__submenuTimer = null;
        if (!sub.contains(document.activeElement)) {
          setSubmenuState(sub, false);
        }
      }, HOVER_CLOSE_DELAY);
    });

    parts.toggle.addEventListener('keydown', function (event) {
      if (event.key === 'ArrowRight' || event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        setSubmenuState(sub, true);
        focusItem(submenuItems(parts.panel), 0);
      }
    });

    parts.panel.addEventListener('keydown', function (event) {
      var items = submenuItems(parts.panel);
      var index = items.indexOf(document.activeElement);
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        focusItem(items, index + 1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        focusItem(items, index - 1);
      } else if (event.key === 'ArrowLeft' || event.key === 'Escape') {
        // Close only this submenu, not the whole Actions menu.
        event.preventDefault();
        event.stopPropagation();
        setSubmenuState(sub, false);
        parts.toggle.focus();
      }
    });
  }

  function setMenuState(menu, open) {
    var toggle = menu.querySelector('[data-header-menu-toggle]');
    var panel = getMenuPanel(menu);
    if (!toggle || !panel) {
      return;
    }
    if (open) {
      if (menu.tagName === 'DETAILS') {
        menu.setAttribute('open', '');
      }
      panel.hidden = false;
      positionPanel(menu, panel);
      toggle.setAttribute('aria-expanded', 'true');
      menu.classList.add('header-menu--open');
      openMenu = menu;
    } else {
      closeSubmenus(menu);
      panel.hidden = true;
      resetPanelPosition(panel);
      toggle.setAttribute('aria-expanded', 'false');
      menu.classList.remove('header-menu--open');
      if (menu.tagName === 'DETAILS') {
        menu.removeAttribute('open');
      }
      if (openMenu === menu) {
        openMenu = null;
      }
    }
  }

  function closeAllMenus(except) {
    document.querySelectorAll('[data-header-menu].header-menu--open').forEach(function (menu) {
      if (menu !== except) {
        setMenuState(menu, false);
      }
    });
  }

  function attachMenu(menu) {
    if (menu.__headerMenuBound) {
      return;
    }
    var toggle = menu.querySelector('[data-header-menu-toggle]');
    var panel = getMenuPanel(menu);
    if (!toggle || !panel) {
      return;
    }
    menu.__headerMenuBound = true;
    toggle.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      var willOpen = !menu.classList.contains('header-menu--open');
      closeAllMenus(menu);
      setMenuState(menu, willOpen);
      if (willOpen) {
        var panel = getMenuPanel(menu);
        var first = panel ? panel.querySelector('a, button, input') : null;
        if (first && typeof first.focus === 'function') {
          // Defer focus so the click doesn't immediately close the menu via outside-click handling.
          setTimeout(function () { first.focus(); }, 0);
        }
      }
    });
    panel.querySelectorAll('[data-header-submenu]').forEach(attachSubmenu);
    // Close the menu when a menu item (but not a checkbox or submenu toggle) is clicked.
    panel.addEventListener('click', function (event) {
      var item = event.target.closest('.header-menu__item, .header-title-menu__item');
      if (item && !item.closest('.header-menu__check') && !item.hasAttribute('data-header-submenu-toggle')) {
        setMenuState(menu, false);
      }
    });
  }

  function getFloatingPanel(details) {
    return details.querySelector('[data-floating-menu-panel]');
  }

  function placeFloatingMenu(details) {
    var panel = getFloatingPanel(details);
    var toggle = details.querySelector('summary');
    if (!details.open) {
      resetPanelPosition(panel);
      return;
    }
    placeFloating(toggle, panel, details.getAttribute('data-floating-menu-placement') === 'top');
  }

  function attachFloatingMenu(details) {
    if (details.__floatingMenuBound || details.tagName !== 'DETAILS') {
      return;
    }
    details.__floatingMenuBound = true;
    details.addEventListener('toggle', function () {
      placeFloatingMenu(details);
    });
    if (details.open) {
      placeFloatingMenu(details);
    }
  }

  // Fixed panels don't follow their toggle when the page or a container
  // scrolls, so re-place any open ones.
  function repositionOpenMenus() {
    if (openMenu) {
      positionPanel(openMenu);
    }
    document.querySelectorAll('details[data-floating-menu][open]').forEach(placeFloatingMenu);
  }

  var repositionQueued = false;
  function queueReposition() {
    if (repositionQueued) {
      return;
    }
    repositionQueued = true;
    window.requestAnimationFrame(function () {
      repositionQueued = false;
      repositionOpenMenus();
    });
  }

  window.addEventListener('scroll', queueReposition, true);
  window.addEventListener('resize', queueReposition);

  function init() {
    document.querySelectorAll('[data-header-menu]').forEach(attachMenu);
    document.querySelectorAll('details[data-floating-menu]').forEach(attachFloatingMenu);
  }

  document.addEventListener('click', function (event) {
    if (!openMenu) {
      return;
    }
    if (!openMenu.contains(event.target)) {
      setMenuState(openMenu, false);
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && openMenu) {
      var toggle = openMenu.querySelector('[data-header-menu-toggle]');
      setMenuState(openMenu, false);
      if (toggle && typeof toggle.focus === 'function') {
        toggle.focus();
      }
    }
  });

  // Re-scan after dynamic content insertion (htmx swaps, etc.).
  document.addEventListener('htmx:afterSwap', init);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.MyPortalHeaderMenu = {
    init: init,
    close: function (menu) { setMenuState(menu, false); },
    closeAll: function () { closeAllMenus(null); },
  };
})();
