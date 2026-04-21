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

  function setMenuState(menu, open) {
    var toggle = menu.querySelector('[data-header-menu-toggle]');
    var panel = menu.querySelector('[data-header-menu-panel]');
    if (!toggle || !panel) {
      return;
    }
    if (open) {
      panel.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      menu.classList.add('header-menu--open');
      openMenu = menu;
    } else {
      panel.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      menu.classList.remove('header-menu--open');
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
    menu.__headerMenuBound = true;
    var toggle = menu.querySelector('[data-header-menu-toggle]');
    if (!toggle) {
      return;
    }
    toggle.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      var willOpen = !menu.classList.contains('header-menu--open');
      closeAllMenus(menu);
      setMenuState(menu, willOpen);
      if (willOpen) {
        var panel = menu.querySelector('[data-header-menu-panel]');
        var first = panel ? panel.querySelector('a, button, input') : null;
        if (first && typeof first.focus === 'function') {
          // Defer focus so the click doesn't immediately close the menu via outside-click handling.
          setTimeout(function () { first.focus(); }, 0);
        }
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
