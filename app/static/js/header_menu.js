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
 */
(function () {
  'use strict';

  var openMenu = null;

  function positionPanel(menu, panel) {
    var toggle = menu.querySelector('[data-header-menu-toggle]');
    panel = panel || getMenuPanel(menu);
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
    if (panelHeight > spaceBelow && rect.top > spaceBelow) {
      panel.style.top = 'auto';
      panel.style.bottom = Math.max(8, window.innerHeight - rect.top + gap) + 'px';
    } else {
      panel.style.top = (rect.bottom + gap) + 'px';
      panel.style.bottom = 'auto';
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
    // Close the menu when a menu item (but not a checkbox toggle) is clicked.
    panel.addEventListener('click', function (event) {
      var item = event.target.closest('.header-menu__item, .header-title-menu__item');
      if (item && !item.closest('.header-menu__check')) {
        setMenuState(menu, false);
      }
    });
  }

  function init() {
    document.querySelectorAll('[data-header-menu]').forEach(attachMenu);
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
