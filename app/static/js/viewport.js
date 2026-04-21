/* MyPortal viewport helper — see docs/MOBILE_UX_GUIDELINES.md.
 *
 * Exposes a tiny API for other scripts to react to mobile breakpoints
 * without each script duplicating its own matchMedia listener:
 *
 *   window.MyPortal.viewport.isMobile()    // ≤640px
 *   window.MyPortal.viewport.isTablet()    // ≤1024px
 *   window.MyPortal.viewport.current       // 'mobile' | 'tablet' | 'desktop'
 *
 * Dispatches a `viewport:change` CustomEvent on `window` with
 * { previous, current } in detail whenever the bucket changes.
 *
 * Also wires up the "More actions" overflow toggle used by the
 * header_actions_overflow Jinja macro — single delegated handler so we
 * don't ship per-page JS for a CSS-driven menu.
 */
(function () {
  'use strict';

  if (typeof window === 'undefined') {
    return;
  }

  var ns = window.MyPortal = window.MyPortal || {};

  var BREAKPOINTS = {
    mobile: 640,
    tablet: 1024
  };

  function compute() {
    var w = window.innerWidth || document.documentElement.clientWidth || 0;
    if (w <= BREAKPOINTS.mobile) {
      return 'mobile';
    }
    if (w <= BREAKPOINTS.tablet) {
      return 'tablet';
    }
    return 'desktop';
  }

  var current = compute();

  function publish() {
    var next = compute();
    if (next === current) {
      return;
    }
    var previous = current;
    current = next;
    try {
      window.dispatchEvent(new CustomEvent('viewport:change', {
        detail: { previous: previous, current: current }
      }));
    } catch (err) {
      /* IE-style fallback not needed — modern browsers only. */
    }
  }

  var resizeFrame = null;
  window.addEventListener('resize', function () {
    if (resizeFrame) {
      return;
    }
    resizeFrame = window.requestAnimationFrame(function () {
      resizeFrame = null;
      publish();
    });
  });

  ns.viewport = {
    breakpoints: BREAKPOINTS,
    get current() { return current; },
    isMobile: function () { return current === 'mobile'; },
    isTablet: function () { return current === 'mobile' || current === 'tablet'; },
    isDesktop: function () { return current === 'desktop'; }
  };

  /* ---- header_actions_overflow toggle (delegated) ------------------- */
  document.addEventListener('click', function (event) {
    var toggle = event.target.closest && event.target.closest('[data-header-overflow-toggle]');
    if (toggle) {
      var container = toggle.closest('[data-header-overflow]');
      if (!container) {
        return;
      }
      var isOpen = container.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
      event.stopPropagation();
      return;
    }
    /* Click outside any open overflow → close it. */
    var openMenus = document.querySelectorAll('[data-header-overflow].is-open');
    openMenus.forEach(function (menu) {
      if (!menu.contains(event.target)) {
        menu.classList.remove('is-open');
        var btn = menu.querySelector('[data-header-overflow-toggle]');
        if (btn) {
          btn.setAttribute('aria-expanded', 'false');
        }
      }
    });
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') {
      return;
    }
    document.querySelectorAll('[data-header-overflow].is-open').forEach(function (menu) {
      menu.classList.remove('is-open');
      var btn = menu.querySelector('[data-header-overflow-toggle]');
      if (btn) {
        btn.setAttribute('aria-expanded', 'false');
        btn.focus();
      }
    });
  });

  /* When viewport widens out of mobile, ensure overflow menus aren't left
   * stuck open in a now-irrelevant state. */
  window.addEventListener('viewport:change', function (event) {
    if (!event.detail || event.detail.current === 'mobile') {
      return;
    }
    document.querySelectorAll('[data-header-overflow].is-open').forEach(function (menu) {
      menu.classList.remove('is-open');
      var btn = menu.querySelector('[data-header-overflow-toggle]');
      if (btn) {
        btn.setAttribute('aria-expanded', 'false');
      }
    });
  });
})();
